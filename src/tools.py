"""Herramientas (tools) del agente MultaClara.

El LLM decide si llama una tool; el programa la ejecuta y le devuelve el resultado.
Hay cuatro:

  * buscar_en_base          (busqueda) -> recuperacion del Avance 2 convertida en tool.
  * calcular_valor_multa    (tarea)    -> valor en pesos de una infraccion + descuento por dias habiles.
  * calcular_plazo_descuento(tarea)    -> fechas limite de pago con descuento (dias habiles colombianos).
  * buscar_en_web           (web)      -> buscador restringido a sitios oficiales, solo si la base no basta.

Los LIMITES viven aqui, en el codigo (constantes de abajo), no en el prompt: el modelo no
puede saltarselos aunque se le pida. Cada tool devuelve un dict con "origen" ("base",
"calculo" o "web") para que la respuesta pueda separar las fuentes por su procedencia.
"""

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import holidays
import httpx
from google.genai import types

from .config import AppConfig
from .retriever import Fragmento, Retriever

# ---------------------------------------------------------------------------
# LIMITES (escritos en el codigo)
# ---------------------------------------------------------------------------
MAX_FRAGMENTOS = 5  # fragmentos de la base por llamada a buscar_en_base
MAX_CARACTERES_FRAGMENTO = 1500  # longitud maxima de cada fragmento devuelto
MAX_CARACTERES_CONSULTA = 300  # longitud maxima de la consulta a la base
MAX_CANDIDATOS_MULTA = 3  # infracciones candidatas que devuelve calcular_valor_multa
MAX_RESULTADOS_WEB = 3  # resultados por busqueda web
MAX_CARACTERES_EXTRACTO_WEB = 300  # extracto corto por resultado web
MAX_CARACTERES_CONSULTA_WEB = 200  # lo unico que sale hacia el buscador
TIMEOUT_WEB_S = 15

# Politica de descuento por pago anticipado (tabla de autoliquidacion 2026):
DIAS_HABILES_PAGO_50 = 5  # dias 1 a 5 habiles  -> se paga el 50 %
DIAS_HABILES_PAGO_75 = 20  # dias 6 a 20 habiles -> se paga el 75 %

TABLA_MULTAS = Path(__file__).resolve().parent.parent / "data" / "tabla_multas_2026.json"

# Sitios permitidos para la busqueda web. Fijados aqui: el LLM no puede cambiarlos.
SITIOS_PERMITIDOS: dict[str, str] = {
    "mintransporte.gov.co": "Ministerio de Transporte: autoridad suprema de tránsito; publica las resoluciones vigentes.",
    "ansv.gov.co": "Agencia Nacional de Seguridad Vial: normativa y lineamientos oficiales de seguridad vial.",
    "runt.com.co": "RUNT, Registro Único Nacional de Tránsito: licencias, vehículos y trámites oficiales.",
    "simit.org.co": "SIMIT: sistema oficial para consultar y pagar multas por infracciones de tránsito.",
    "funcionpublica.gov.co": "Función Pública (Gestor Normativo EVA): texto vigente y concordancias de las normas.",
    "suin-juriscol.gov.co": "SUIN-Juriscol (Ministerio de Justicia): sistema oficial de información normativa.",
    "secretariasenado.gov.co": "Secretaría del Senado: texto oficial de las leyes de la República.",
    "movilidadbogota.gov.co": "Secretaría Distrital de Movilidad de Bogotá: fuente de la tabla de valores 2026.",
}

URL_TAVILY = "https://api.tavily.com/search"
MENSAJE_SIN_RESULTADO_BASE = "No se encontraron fragmentos relevantes en la base normativa."

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------------------
# Infraestructura: una Herramienta tiene nombre, docstring, parametros y .invoke()
# ---------------------------------------------------------------------------
@dataclass
class Herramienta:
    nombre: str
    tipo: str  # "busqueda" | "tarea" | "web"
    descripcion: str  # el docstring: que hace y cuando usarla (lo lee el LLM)
    parametros: dict[str, dict]  # nombre -> {"tipo", "descripcion", "requerido"}
    funcion: Callable[..., dict]

    def invoke(self, argumentos: dict[str, Any]) -> dict:
        """Ejecuta la tool con un dict de argumentos (sin LLM). Valida lo basico."""
        faltan = [n for n, p in self.parametros.items() if p.get("requerido") and argumentos.get(n) in (None, "")]
        if faltan:
            return {"error": f"Faltan argumentos obligatorios: {', '.join(faltan)}."}
        validos = {k: v for k, v in argumentos.items() if k in self.parametros}
        try:
            return self.funcion(**validos)
        except Exception as exc:  # la tool nunca debe tumbar el bucle del agente
            return {"error": f"Fallo al ejecutar {self.nombre}: {exc}"}

    def declaracion(self) -> types.FunctionDeclaration:
        """Esquema que se le entrega al LLM para que sepa llamarla."""
        return types.FunctionDeclaration(
            name=self.nombre,
            description=self.descripcion,
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    n: types.Schema(type=p["tipo"].upper(), description=p["descripcion"])
                    for n, p in self.parametros.items()
                },
                required=[n for n, p in self.parametros.items() if p.get("requerido")],
            ),
        )


def _herramienta(tipo: str, parametros: dict[str, dict]):
    """Marca un metodo como tool; su docstring pasa a ser la descripcion para el LLM."""

    def deco(f):
        f._meta = (tipo, parametros)
        return f

    return deco


# ---------------------------------------------------------------------------
# Utilidades de texto y fechas
# ---------------------------------------------------------------------------
def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in t if not unicodedata.combining(c)))


_STOP = {"de", "la", "el", "los", "las", "un", "una", "y", "o", "por", "en", "con", "que", "se", "a", "al", "del",
         "para", "es", "su", "sus", "multa", "infraccion", "comparendo", "pesos", "valor", "cuanto", "cuesta",
         "pagar", "pago", "sancion", "debo", "hay", "lo", "le", "mi"}


def _raices(t: str) -> set[str]:
    """Palabras significativas reducidas a sus 5 primeras letras (conducir ~ conduccion)."""
    return {w[:5] for w in _norm(t).split() if w not in _STOP and len(w) > 1}


def _fecha(texto: str, nombre: str) -> date:
    t = str(texto).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"{nombre} debe tener formato AAAA-MM-DD (recibido: {texto!r}).")


def _bonita(d: date) -> str:
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} de {d.year}"


def _festivos(anios: set[int]) -> holidays.HolidayBase:
    return holidays.country_holidays("CO", years=sorted(anios))


def _es_habil(d: date, fest: holidays.HolidayBase) -> bool:
    return d.weekday() < 5 and d not in fest


def sumar_dias_habiles(inicio: date, n: int) -> date:
    """n-esimo dia habil colombiano despues de `inicio` (el dia 0 es el propio `inicio`)."""
    fest = _festivos({inicio.year, inicio.year + 1})
    d, contados = inicio, 0
    while contados < n:
        d += timedelta(days=1)
        if _es_habil(d, fest):
            contados += 1
    return d


def dias_habiles_entre(inicio: date, fin: date) -> int:
    """Dias habiles transcurridos en (inicio, fin]: el dia 1 es el primer habil despues de `inicio`."""
    fest = _festivos({inicio.year, fin.year})
    d, n = inicio, 0
    while d < fin:
        d += timedelta(days=1)
        n += _es_habil(d, fest)
    return n


def _redactar(consulta: str) -> str:
    """Quita datos personales antes de que la consulta salga hacia el buscador."""
    c = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[correo]", consulta)  # correos
    c = re.sub(r"\b[A-Z]{3}[- ]?\d{2}[\dA-Z]\b", "[placa]", c)  # placas (autos y motos)
    c = re.sub(r"\b\d{1,3}(?:\.\d{3}){2,}\b", "[numero]", c)  # 1.032.456.789
    c = re.sub(r"\b\d{6,}\b", "[numero]", c)  # cedulas, telefonos, NIT
    return re.sub(r"\s+", " ", c).strip()


def _seccion(f: Fragmento) -> str:
    partes = [f"Art. {f.articulo}"] if f.articulo and f.articulo != "N/A" else []
    partes += [x for x in (f.titulo, f.capitulo) if x]
    return " | ".join(partes) or "Documento"


def _recortar(texto: str, maximo: int) -> tuple[str, bool]:
    return (texto, False) if len(texto) <= maximo else (texto[: maximo - 1].rstrip() + "…", True)


# ---------------------------------------------------------------------------
# La caja de herramientas
# ---------------------------------------------------------------------------
class CajaDeHerramientas:
    """Agrupa las tools y el estado que comparten (retriever, tabla de multas, clave web)."""

    def __init__(self, config: AppConfig, retriever: Retriever | None = None,
                 cliente_http: Any = None, ruta_tabla: Path = TABLA_MULTAS):
        self._retriever = retriever or Retriever(config)
        self._tavily_key = config.tavily_api_key
        self._http = cliente_http or httpx
        self._tabla = json.loads(Path(ruta_tabla).read_text(encoding="utf-8"))
        self._busquedas_base = 0  # politica "primero la base": se reinicia en cada pregunta
        self.herramientas: dict[str, Herramienta] = {}
        for nombre in ("buscar_en_base", "calcular_valor_multa", "calcular_plazo_descuento", "buscar_en_web"):
            metodo = getattr(self, nombre)
            tipo, params = metodo._meta
            self.herramientas[nombre] = Herramienta(
                nombre=nombre, tipo=tipo, descripcion=_limpiar_doc(metodo.__doc__),
                parametros=params, funcion=metodo,
            )

    def nueva_pregunta(self) -> None:
        self._busquedas_base = 0

    def declaraciones(self) -> list[types.Tool]:
        return [types.Tool(function_declarations=[h.declaracion() for h in self.herramientas.values()])]

    # ----- 1. Busqueda en la base (Avance 2 convertido en tool) -----
    @_herramienta("busqueda", {
        "consulta": {"tipo": "string", "requerido": True,
                     "descripcion": "Consulta en español con los términos de la norma (p. ej. 'plazo de caducidad de la acción por contravención')."},
    })
    def buscar_en_base(self, consulta: str) -> dict:
        """Busca en la base de conocimientos (Ley 769 de 2002, Código Nacional de Tránsito de Colombia) los fragmentos más relevantes para una consulta.

        Úsala para responder cualquier pregunta sobre normas de tránsito, el procedimiento de un comparendo, infracciones, sanciones, plazos o recursos. Formula la consulta con tus propias palabras y términos de la norma. Si la pregunta trata de dos temas distintos, llámala una vez por tema. NO la uses para saludos, agradecimientos ni conversación casual.

        Devuelve hasta 5 fragmentos; cada uno trae documento y sección (artículo, título, capítulo) para poder citar.
        """
        consulta = (consulta or "").strip()[:MAX_CARACTERES_CONSULTA]
        if not consulta:
            return {"error": "La consulta está vacía."}
        self._busquedas_base += 1
        fragmentos = self._retriever.buscar(consulta, top_k=MAX_FRAGMENTOS)[:MAX_FRAGMENTOS]
        salida = []
        for f in fragmentos:
            texto, truncado = _recortar(f.texto, MAX_CARACTERES_FRAGMENTO)
            salida.append({"documento": f.documento, "seccion": _seccion(f), "cita": f.cita,
                           "texto": texto, "truncado": truncado, "similitud": round(f.score, 3)})
        resultado: dict[str, Any] = {"origen": "base", "consulta": consulta, "fragmentos": salida}
        if not salida:
            resultado["nota"] = MENSAJE_SIN_RESULTADO_BASE
        return resultado

    # ----- 2. Tarea del dominio: valor de la multa -----
    @_herramienta("tarea", {
        "infraccion": {"tipo": "string", "requerido": True,
                       "descripcion": "Descripción breve de la infracción tal como la dice la persona (p. ej. 'no usar el cinturón de seguridad', 'conducir sin licencia') o su código de la tabla (p. ej. 'C06')."},
        "fecha_comparendo": {"tipo": "string", "requerido": False,
                             "descripcion": "Fecha del comparendo en formato AAAA-MM-DD, solo si la persona la menciona."},
        "fecha_pago": {"tipo": "string", "requerido": False,
                       "descripcion": "Fecha en que se pagó o se pagará, formato AAAA-MM-DD, solo si la persona la menciona."},
    })
    def calcular_valor_multa(self, infraccion: str, fecha_comparendo: str | None = None,
                             fecha_pago: str | None = None) -> dict:
        """Calcula el valor en PESOS (año 2026) de la multa de una infracción de tránsito y el descuento por pago oportuno, a partir de la tabla oficial de autoliquidación. Ejecuta un cálculo y una consulta exacta que NO debes hacer de memoria.

        Úsala cuando pidan el valor en PESOS o cuánto se paga por una infracción, o cuánto se paga si se paga en cierta fecha. Si solo preguntan con cuántos salarios mínimos (SMLDV) se sanciona, eso lo dice la ley: usa buscar_en_base. Si dan la fecha del comparendo y la fecha de pago, calcula los días hábiles transcurridos (con festivos de Colombia) y aplica el 50 % (días hábiles 1 a 5) o el 75 % (días 6 a 20). Si no dan fechas, devuelve los tres valores posibles.
        """
        texto = (infraccion or "").strip()
        if re.search(r"embriaguez|alcohol|alcoholemia", _norm(texto)):
            return {"origen": "calculo", "fuente": self._tabla["fuente"], "tipo": "embriaguez",
                    "valor_unidad_uvb_2026": self._tabla["valor_unidad_uvb_2026"],
                    "tabla_embriaguez": self._tabla["embriaguez"],
                    "nota": "Para embriaguez la tabla no ofrece descuento por pago oportuno."}
        candidatos = self._buscar_infraccion(texto)
        if not candidatos:
            return {"error": "No se encontró una infracción parecida en la tabla de autoliquidación 2026.",
                    "origen": "calculo"}
        mejor, puntaje = candidatos[0]
        resultado: dict[str, Any] = {
            "origen": "calculo",
            "fuente": self._tabla["fuente"],
            "infraccion": self._ficha(mejor),
            "confianza": "alta" if puntaje >= 0.99 or (puntaje >= 0.6 and (len(candidatos) == 1 or puntaje - candidatos[1][1] >= 0.2)) else "baja",
            "alternativas": [self._ficha(c) for c, _ in candidatos[1:]],
            "nota": "Valores de la tabla de la Secretaría Distrital de Movilidad de Bogotá; los descuentos exigen "
                    "curso pedagógico previo. Confirma con el organismo de tránsito donde se impuso el comparendo.",
        }
        if fecha_comparendo and fecha_pago:
            fc, fp = _fecha(fecha_comparendo, "fecha_comparendo"), _fecha(fecha_pago, "fecha_pago")
            if fp <= fc:
                return {"error": "La fecha de pago debe ser posterior a la fecha del comparendo.", "origen": "calculo"}
            dias = dias_habiles_entre(fc, fp)
            if dias <= DIAS_HABILES_PAGO_50:
                ventana, pagar = "días hábiles 1 a 5 (se paga el 50 %)", mejor["pagar_dia_1_a_5_habil"]
            elif dias <= DIAS_HABILES_PAGO_75:
                ventana, pagar = "días hábiles 6 a 20 (se paga el 75 %)", mejor["pagar_dia_6_a_20_habil"]
            else:
                ventana, pagar = "después del día hábil 20 (sin descuento: se paga el 100 %)", mejor["valor_aprox_centenas"]
            resultado["calculo"] = {
                "fecha_comparendo": fc.isoformat(), "fecha_pago": fp.isoformat(),
                "dias_habiles_transcurridos": dias, "ventana": ventana, "valor_a_pagar_pesos": pagar,
                "regla": "Se cuentan días hábiles (lunes a viernes sin festivos de Colombia) desde el día hábil siguiente al comparendo.",
            }
        elif fecha_comparendo:
            resultado["plazos"] = self._plazos(_fecha(fecha_comparendo, "fecha_comparendo"), None)
        return resultado

    # ----- 3. Tarea del dominio: plazos en dias habiles -----
    @_herramienta("tarea", {
        "fecha_comparendo": {"tipo": "string", "requerido": True,
                             "descripcion": "Fecha en que se impuso el comparendo, formato AAAA-MM-DD."},
        "fecha_consulta": {"tipo": "string", "requerido": False,
                           "descripcion": "Fecha 'de hoy' para saber si el plazo sigue vigente, formato AAAA-MM-DD (por defecto, la fecha de hoy)."},
    })
    def calcular_plazo_descuento(self, fecha_comparendo: str, fecha_consulta: str | None = None) -> dict:
        """Calcula, en DÍAS HÁBILES colombianos, hasta qué fecha se puede pagar un comparendo con descuento (50 % hasta el día hábil 5; 75 % hasta el día hábil 20). Es un cálculo con calendario y festivos que NO debes hacer de memoria.

        Úsala cuando pregunten hasta cuándo hay descuento, cuántos días hábiles quedan o si un plazo ya venció, a partir de la fecha del comparendo.
        """
        fc = _fecha(fecha_comparendo, "fecha_comparendo")
        hoy = _fecha(fecha_consulta, "fecha_consulta") if fecha_consulta else date.today()
        return {"origen": "calculo", "fuente": self._tabla["fuente"], **self._plazos(fc, hoy),
                "nota": "Los descuentos exigen curso pedagógico previo. Confirma las fechas con el organismo de tránsito."}

    # ----- 4. Busqueda web controlada -----
    @_herramienta("web", {
        "consulta": {"tipo": "string", "requerido": True,
                     "descripcion": "Consulta corta y general en español, SIN datos personales (nada de cédula, placa, nombres ni correos)."},
    })
    def buscar_en_web(self, consulta: str) -> dict:
        """Busca en la web, SOLO dentro de una lista fija de sitios oficiales de tránsito de Colombia (Ministerio de Transporte, ANSV, RUNT, SIMIT, Función Pública, SUIN-Juriscol, Secretaría del Senado y Movilidad de Bogotá), información pública o actual.

        Úsala ÚNICAMENTE si buscar_en_base ya se consultó y NO tiene la respuesta, y la pregunta trata de información pública o actual (una norma o resolución vigente o reciente, un trámite, una definición general). No la uses para saludos, para casos personales ni para lo que la base ya responde. No incluyas datos personales en la consulta.

        Devuelve máximo 3 resultados con título, URL y un extracto corto, todos marcados con origen "web".
        """
        if self._busquedas_base == 0:
            return {"error": "Política 'primero la base': antes de buscar en la web debes llamar a buscar_en_base."}
        enviada = _redactar((consulta or "").strip())[:MAX_CARACTERES_CONSULTA_WEB]
        if not enviada:
            return {"error": "La consulta está vacía."}
        if not self._tavily_key:
            return {"error": "La búsqueda web no está configurada (falta TAVILY_API_KEY).", "origen": "web"}
        try:
            r = self._http.post(
                URL_TAVILY,
                json={"query": enviada, "include_domains": list(SITIOS_PERMITIDOS),
                      "max_results": MAX_RESULTADOS_WEB, "search_depth": "basic",
                      "include_answer": False, "include_raw_content": False},
                headers={"Authorization": f"Bearer {self._tavily_key}"},
                timeout=TIMEOUT_WEB_S,
            )
            r.raise_for_status()
            crudos = r.json().get("results", [])
        except Exception as exc:
            return {"error": f"No fue posible consultar el buscador web: {type(exc).__name__}", "origen": "web",
                    "consulta_enviada": enviada}
        resultados = []
        for x in crudos:
            host = (urlparse(x.get("url", "")).hostname or "").lower()
            # defensa adicional: solo se aceptan URL de los sitios permitidos
            if not any(host == d or host.endswith("." + d) for d in SITIOS_PERMITIDOS):
                continue
            extracto, _ = _recortar(" ".join(str(x.get("content", "")).split()), MAX_CARACTERES_EXTRACTO_WEB)
            resultados.append({"origen": "web", "titulo": str(x.get("title", ""))[:160], "url": x["url"],
                               "extracto": extracto})
            if len(resultados) >= MAX_RESULTADOS_WEB:
                break
        salida: dict[str, Any] = {"origen": "web", "consulta_enviada": enviada, "resultados": resultados}
        if not resultados:
            salida["nota"] = "No hubo resultados en los sitios permitidos."
        return salida

    # ----- apoyos internos de las tools de tarea -----
    def _ficha(self, i: dict) -> dict:
        return {"codigo": i["codigo"], "descripcion": i["descripcion"], "grupo": i["grupo"], "uvb": i["uvb"],
                "valor_unidad_uvb_2026": self._tabla["valor_unidad_uvb_2026"],
                "valor_completo_pesos": i["valor_aprox_centenas"],
                "pagar_dia_1_a_5_habil_pesos": i["pagar_dia_1_a_5_habil"],
                "pagar_dia_6_a_20_habil_pesos": i["pagar_dia_6_a_20_habil"],
                "inmoviliza_vehiculo": i["inmoviliza_vehiculo"]}

    def _buscar_infraccion(self, texto: str) -> list[tuple[dict, float]]:
        infracciones = self._tabla["infracciones"]
        m = re.fullmatch(r"\s*([A-Ia-i]\d{2})\s*", texto)
        if m:
            exacta = [i for i in infracciones if i["codigo"] == m.group(1).upper()]
            return [(exacta[0], 1.0)] if exacta else []
        q = _raices(texto)
        if not q:
            return []
        puntuadas = []
        for i in infracciones:
            d = _raices(i["descripcion"])
            solape = len(q & d) / len(q)
            if solape == 0:
                continue
            fino = difflib.SequenceMatcher(None, _norm(texto), _norm(i["descripcion"])).ratio()
            puntuadas.append((i, solape, solape + 0.25 * fino))
        puntuadas.sort(key=lambda p: -p[2])  # orden: solape de palabras + parecido de texto
        # el puntaje que se devuelve es el solape puro (0-1): la confianza se mide con el
        return [(i, round(solape, 3)) for i, solape, _ in puntuadas[:MAX_CANDIDATOS_MULTA]]

    def _plazos(self, fc: date, hoy: date | None) -> dict:
        d5 = sumar_dias_habiles(fc, DIAS_HABILES_PAGO_50)
        d20 = sumar_dias_habiles(fc, DIAS_HABILES_PAGO_75)
        fest = _festivos({fc.year, d20.year})
        considerados = sorted(f for f in fest if fc < f <= d20 and f.weekday() < 5)
        salida: dict[str, Any] = {
            "fecha_comparendo": fc.isoformat(),
            "descuento_50_hasta": {"fecha": d5.isoformat(), "texto": _bonita(d5), "dia_habil": DIAS_HABILES_PAGO_50,
                                   "paga": "50 % del valor"},
            "descuento_75_hasta": {"fecha": d20.isoformat(), "texto": _bonita(d20), "dia_habil": DIAS_HABILES_PAGO_75,
                                   "paga": "75 % del valor"},
            "festivos_en_el_rango": [f"{f.isoformat()} ({fest.get(f)})" for f in considerados],
            "regla": "Días hábiles = lunes a viernes sin festivos de Colombia; el día 1 es el primer día hábil después del comparendo.",
        }
        if hoy:
            transcurridos = dias_habiles_entre(fc, hoy) if hoy > fc else 0
            estado = ("aún aplica el 50 %" if transcurridos <= DIAS_HABILES_PAGO_50
                      else "aún aplica el 75 %" if transcurridos <= DIAS_HABILES_PAGO_75
                      else "los plazos de descuento ya vencieron")
            salida["a_la_fecha"] = {"fecha": hoy.isoformat(), "dias_habiles_transcurridos": transcurridos, "estado": estado}
        return salida


def _limpiar_doc(doc: str | None) -> str:
    """Docstring -> descripcion de una linea por parrafo (sin sangrias)."""
    return "\n\n".join(" ".join(p.split()) for p in (doc or "").strip().split("\n\n"))
