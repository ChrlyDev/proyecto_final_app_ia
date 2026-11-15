"""Agente MultaClara: el LLM decide cuando consultar la base y que tool usar.

Bucle (implementado a mano con el SDK de Gemini, sin framework):

    pregunta -> LLM --(pide tools)--> el PROGRAMA ejecuta cada tool --> resultado -> LLM -> ...
                     \\--(responde texto)--> respuesta final

Lo importante:
  * El LLM solo PIDE llamar a una tool; quien la ejecuta es este codigo, que aplica los limites.
  * Hay un limite de iteraciones por pregunta (`config.agente_max_iteraciones`) y un tope de
    llamadas a tools (`MAX_LLAMADAS_TOOLS`). Al alcanzarlo se fuerza una respuesta final sin tools.
  * Cada pregunta se registra (tools, argumentos, llamadas al LLM, tokens, tiempo) en un JSONL.
  * Un saludo se responde sin tools porque el prompt le dice al modelo cuando NO usarlas.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from google import genai
from google.genai import types

from .config import AppConfig
from .prompts import construir_turno_usuario
from .prompts_agent import construir_system_prompt
from .registro import RegistroUsoTools
from .tools import CajaDeHerramientas

MAX_LLAMADAS_TOOLS = 8  # tope absoluto de llamadas a tools por pregunta
MAX_TOKENS_SALIDA = 4096  # holgura para el razonamiento interno del modelo + la respuesta
REINTENTOS_API = 5
MENSAJE_SIN_TEXTO = "No pude generar una respuesta en este momento. Intenta reformular tu pregunta."


class AgenteError(RuntimeError):
    """Fallo al invocar el modelo."""


@dataclass
class LlamadaTool:
    nombre: str
    argumentos: dict[str, Any]
    resultado: dict[str, Any]
    segundos: float
    error: str | None = None

    @property
    def resumen(self) -> str:
        r = self.resultado
        if self.error:
            return f"error: {self.error}"
        if self.nombre == "buscar_en_base":
            citas = [f["cita"] for f in r.get("fragmentos", [])]
            return f"{len(citas)} fragmentos: " + "; ".join(citas) if citas else "sin fragmentos"
        if self.nombre == "buscar_en_web":
            return f"{len(r.get('resultados', []))} resultados web"
        if "calculo" in r:
            return f"valor a pagar ${r['calculo']['valor_a_pagar_pesos']:,}".replace(",", ".")
        if "descuento_50_hasta" in r:
            return f"50 % hasta {r['descuento_50_hasta']['fecha']}; 75 % hasta {r['descuento_75_hasta']['fecha']}"
        if "infraccion" in r:
            return f"infracción {r['infraccion']['codigo']}"
        return "ok"


@dataclass
class RespuestaAgente:
    pregunta: str
    texto: str
    llamadas_tools: list[LlamadaTool] = field(default_factory=list)
    llamadas_llm: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_total: int = 0
    segundos: float = 0.0
    iteraciones: int = 0
    limite_alcanzado: bool = False

    @property
    def herramientas_usadas(self) -> list[str]:
        return [c.nombre for c in self.llamadas_tools]

    @property
    def fuentes_base(self) -> list[dict]:
        """Fragmentos de la base devueltos por las tools (sin repetir)."""
        vistos, salida = set(), []
        for c in self.llamadas_tools:
            for f in c.resultado.get("fragmentos", []) if c.nombre == "buscar_en_base" else []:
                clave = (f["cita"], f["texto"][:80])
                if clave not in vistos:
                    vistos.add(clave)
                    salida.append(f)
        return salida

    @property
    def fuentes_web(self) -> list[dict]:
        vistos, salida = set(), []
        for c in self.llamadas_tools:
            for r in c.resultado.get("resultados", []) if c.nombre == "buscar_en_web" else []:
                if r["url"] not in vistos:
                    vistos.add(r["url"])
                    salida.append(r)
        return salida

    @property
    def contextos(self) -> list[str]:
        """Lo que devolvieron las tools (base, web y calculos): son los `retrieved_contexts` para Ragas.

        Los calculos cuentan como contexto porque son la evidencia de la que sale la respuesta
        (un valor en pesos no esta en ningun fragmento de la ley, solo en la tabla de la tool)."""
        calculos = []
        for c in self.llamadas_tools:
            if c.nombre in ("calcular_valor_multa", "calcular_plazo_descuento") and not c.error:
                util = {k: v for k, v in c.resultado.items() if k not in ("nota", "origen", "alternativas")}
                calculos.append(f"[cálculo: {c.resultado.get('fuente', 'tabla de autoliquidación 2026')}] "
                                + json.dumps(util, ensure_ascii=False)[:1500])
        return ([f"[{f['cita']}] {f['texto']}" for f in self.fuentes_base]
                + [f"[web: {r['url']}] {r['titulo']}. {r['extracto']}" for r in self.fuentes_web] + calculos)


class AgenteMultaClara:
    """Agente con tools. Una instancia por conversacion (guarda el historial para seguimientos)."""

    def __init__(self, config: AppConfig, caja: CajaDeHerramientas | None = None,
                 registro: RegistroUsoTools | None = None, cliente: Any = None, registrar: bool = True):
        self._config = config
        self._client = cliente or genai.Client(api_key=config.api_key)
        self._caja = caja or CajaDeHerramientas(config)
        self._registro = (registro or RegistroUsoTools(config.agente_log)) if registrar else None
        self._turnos: list[tuple[str, str]] = []  # (pregunta, respuesta) para los seguimientos

    @property
    def caja(self) -> CajaDeHerramientas:
        return self._caja

    def reiniciar(self) -> None:
        self._turnos.clear()

    def _historial(self) -> list[types.Content]:
        h: list[types.Content] = []
        for pregunta, respuesta in self._turnos[-self._config.max_turnos_historial:]:
            h.append(types.Content(role="user", parts=[types.Part(text=construir_turno_usuario(pregunta))]))
            h.append(types.Content(role="model", parts=[types.Part(text=respuesta)]))
        return h

    def _generar(self, contents: list[types.Content], config: types.GenerateContentConfig):
        """Una llamada al LLM, con reintentos si la API pide esperar (429/503)."""
        for intento in range(REINTENTOS_API):
            try:
                return self._client.models.generate_content(model=self._config.model, contents=contents, config=config)
            except Exception as exc:
                temporal = re.search(r"429|503|RESOURCE_EXHAUSTED|UNAVAILABLE|overloaded", str(exc), re.I)
                if intento == REINTENTOS_API - 1 or not temporal:
                    raise AgenteError(f"No fue posible contactar al modelo: {exc}") from exc
                m = re.search(r"retry in ([\d.]+)s", str(exc), re.I)
                time.sleep(float(m.group(1)) + 2 if m else min(2 ** (intento + 1), 30))
        raise AgenteError("No fue posible contactar al modelo.")  # inalcanzable

    def _ejecutar(self, fc: types.FunctionCall) -> LlamadaTool:
        nombre, args = fc.name, dict(fc.args or {})
        inicio = time.perf_counter()
        herramienta = self._caja.herramientas.get(nombre)
        if herramienta is None:
            resultado = {"error": f"La herramienta '{nombre}' no existe."}
        else:
            resultado = herramienta.invoke(args)
        return LlamadaTool(nombre=nombre, argumentos=args, resultado=resultado,
                           segundos=time.perf_counter() - inicio, error=resultado.get("error"))

    def responder(self, pregunta: str, sesion: str = "") -> RespuestaAgente:
        pregunta = (pregunta or "").strip()
        if not pregunta:
            raise AgenteError("La pregunta no puede estar vacía.")
        inicio = time.perf_counter()
        self._caja.nueva_pregunta()

        base = dict(system_instruction=construir_system_prompt(date.today()),
                    temperature=self._config.temperature, max_output_tokens=MAX_TOKENS_SALIDA,
                    tools=self._caja.declaraciones())
        # con tools: el modelo decide; el programa ejecuta (se desactiva la ejecucion automatica del SDK)
        con_tools = types.GenerateContentConfig(
            **base, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        # tras alcanzar el limite: se obliga a responder en texto
        sin_tools = types.GenerateContentConfig(
            **base, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="NONE")))

        contents = self._historial() + [
            types.Content(role="user", parts=[types.Part(text=construir_turno_usuario(pregunta))])]
        r = RespuestaAgente(pregunta=pregunta, texto="")
        max_iter = self._config.agente_max_iteraciones

        for ronda in range(max_iter + 1):
            ultimo_intento = ronda == max_iter  # ya se uso el maximo de rondas con tools
            resp = self._generar(contents, sin_tools if ultimo_intento else con_tools)
            r.llamadas_llm += 1
            uso = resp.usage_metadata
            if uso:
                r.tokens_entrada += uso.prompt_token_count or 0
                r.tokens_salida += uso.candidates_token_count or 0
                r.tokens_total += uso.total_token_count or 0

            pedidas = resp.function_calls or []
            if not pedidas or ultimo_intento:
                r.limite_alcanzado = bool(pedidas)
                r.texto = (resp.text or "").strip() or MENSAJE_SIN_TEXTO
                break

            # el modelo pidio tools: se conserva su turno tal cual (incluye su razonamiento) y
            # se le devuelve el resultado de cada una
            contents.append(resp.candidates[0].content)
            partes = []
            for fc in pedidas:
                if len(r.llamadas_tools) >= MAX_LLAMADAS_TOOLS:
                    llamada = LlamadaTool(fc.name, dict(fc.args or {}), {"error": "Límite de llamadas a herramientas alcanzado."},
                                          0.0, "Límite de llamadas a herramientas alcanzado.")
                else:
                    llamada = self._ejecutar(fc)
                r.llamadas_tools.append(llamada)
                partes.append(types.Part.from_function_response(name=fc.name, response=llamada.resultado))
            contents.append(types.Content(role="user", parts=partes))
            r.iteraciones += 1

        r.segundos = time.perf_counter() - inicio
        self._turnos.append((pregunta, r.texto))
        if self._registro:
            self._registro.registrar(r, sesion=sesion)
        return r
