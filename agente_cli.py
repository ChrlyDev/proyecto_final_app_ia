"""CLI del agente MultaClara (pruebas y capturas de consola).

Uso:
    python agente_cli.py "¿Cuál es la velocidad máxima en vías urbanas?"   # una pregunta
    python agente_cli.py --demo                                           # casos de ejemplo
    python agente_cli.py                                                  # chat interactivo
Muestra, por cada pregunta: las tools que llamó el agente (con argumentos), la respuesta, y las
cifras de costo (llamadas al LLM, tokens y tiempo). El registro completo queda en logs/uso_tools.jsonl.
"""

import json
import sys

from src.agent import AgenteError, AgenteMultaClara, RespuestaAgente
from src.config import AppConfig, ConfigError

DEMO = [
    "Hola, buenas tardes. ¿Cómo estás?",
    "¿Qué recursos se pueden presentar contra las decisiones del proceso de tránsito?",
    "Me pusieron un comparendo el 2 de marzo de 2026 por no usar el cinturón de seguridad y pagué el 6 de marzo de 2026. ¿Cuánto me tocó pagar en pesos?",
    "¿Qué ha dispuesto el Ministerio de Transporte en su resolución más reciente sobre los vehículos de micromovilidad, como las patinetas eléctricas?",
]


def mostrar(r: RespuestaAgente) -> None:
    print(f"\nPregunta: {r.pregunta}")
    print("Herramientas usadas:" + (" (ninguna)" if not r.llamadas_tools else ""))
    for i, c in enumerate(r.llamadas_tools, 1):
        args = json.dumps(c.argumentos, ensure_ascii=False)
        print(f"  {i}. {c.nombre}({args})  ->  {c.resumen}")
    print(f"\nRespuesta:\n{r.texto}")
    print(f"\n[llamadas al LLM: {r.llamadas_llm} | tokens: {r.tokens_total} | tiempo: {r.segundos:.1f} s"
          f"{' | LÍMITE DE ITERACIONES' if r.limite_alcanzado else ''}]")


def main() -> None:
    try:
        agente = AgenteMultaClara(AppConfig.from_env())
    except ConfigError as e:
        sys.exit(f"[Error de configuración] {e}")
    args = sys.argv[1:]
    try:
        if "--demo" in args:
            for p in DEMO:
                agente.reiniciar()
                mostrar(agente.responder(p))
        elif args:
            mostrar(agente.responder(" ".join(args)))
        else:
            print("MultaClara (agente). Escribe 'salir' para terminar.\n")
            while (p := input("Tú: ").strip().lower() != "salir"):
                if p:
                    mostrar(agente.responder(p))
    except AgenteError as e:
        sys.exit(f"[Error] {e}")


if __name__ == "__main__":
    main()
