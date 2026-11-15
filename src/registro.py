"""Registro de uso de tools: una linea JSON por pregunta (formato JSONL).

Guarda, por cada pregunta: que tools llamo el agente, con que argumentos, cuantas llamadas
hizo al LLM, los tokens y el tiempo. Sirve para auditar las decisiones del agente y para la
comparacion de costo contra el pipeline fijo del Avance 2.

El resultado COMPLETO de cada tool no se guarda (puede traer fragmentos largos o datos del
usuario): solo un resumen. La pregunta se guarda recortada.
"""

import json
from datetime import datetime
from pathlib import Path


class RegistroUsoTools:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)

    def registrar(self, respuesta, sesion: str = "") -> dict:
        """Agrega una linea al archivo y la devuelve (util para mostrarla en pantalla)."""
        entrada = {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "sesion": sesion,
            "pregunta": respuesta.pregunta[:200],
            "herramientas": [
                {"nombre": c.nombre, "argumentos": c.argumentos, "resumen": c.resumen,
                 "error": c.error, "segundos": round(c.segundos, 3)}
                for c in respuesta.llamadas_tools
            ],
            "llamadas_llm": respuesta.llamadas_llm,
            "tokens_entrada": respuesta.tokens_entrada,
            "tokens_salida": respuesta.tokens_salida,
            "tokens_total": respuesta.tokens_total,
            "segundos": round(respuesta.segundos, 3),
            "iteraciones": respuesta.iteraciones,
            "limite_alcanzado": respuesta.limite_alcanzado,
        }
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        return entrada
