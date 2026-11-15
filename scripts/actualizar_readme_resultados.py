"""Regenera en el README las tablas de resultados del Avance 3 a partir de eval/runs/.

    python scripts/actualizar_readme_resultados.py

Reemplaza el contenido entre los marcadores <!-- X:INICIO --> y <!-- X:FIN --> (RAGAS, CASOS, COSTO),
de modo que las cifras del README salgan siempre de los archivos de resultados.
"""

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import ragas_eval as R  # noqa: E402

RUNS = RAIZ / "eval" / "runs"
README = RAIZ / "README.md"


def cargar(n):
    p = RUNS / n
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def tabla_ragas() -> str:
    a2 = R.calcular_resumen(list(cargar("cabecera_respuestas.json").values()), cargar("cabecera_ragas.json"))
    a3 = R.calcular_resumen(list(cargar("agente_respuestas.json").values()), cargar("agente_ragas.json"))
    filas = ["| Métrica | Avance 2 (pipeline fijo) | Avance 3 (agente) | Diferencia |", "|---|---|---|---|"]
    for m in R.METRICAS:
        filas.append(f"| {m} | {a2[m]:.3f} | {a3[m]:.3f} | {a3[m] - a2[m]:+.3f} |")
    filas.append(f"| hit_articulo (sin LLM) | {a2['hit_articulo']:.3f} | {a3['hit_articulo']:.3f} | {a3['hit_articulo'] - a2['hit_articulo']:+.3f} |")
    filas.append(f"| rechazo_correcto (4 de control) | {a2['rechazo_correcto']:.2f} | {a3['rechazo_correcto']:.2f} | {a3['rechazo_correcto'] - a2['rechazo_correcto']:+.2f} |")
    return "\n".join(filas)


def tabla_casos() -> str:
    casos = cargar("casos_resultados.json")
    filas = ["| # | Tipo | Esperado | Tools usadas | Llam. LLM | ¿Cumple? |", "|---|---|---|---|---|---|"]
    for c in casos.values():
        tools = ", ".join(h["nombre"] for h in c["herramientas"]) or "ninguna"
        filas.append(f"| {c['id']} | {c['tipo']} | {c['esperado']} | {tools} | {c['llamadas_llm']} | {'Sí' if c['cumple'] else 'No'} |")
    filas.append(f"\nCasos que cumplen lo esperado: **{sum(c['cumple'] for c in casos.values())}/{len(casos)}**.")
    return "\n".join(filas)


def tabla_costo() -> str:
    pipe, ag = cargar("pipeline_costo.json"), cargar("agente_respuestas.json")
    ids = [k for k in pipe if k in ag]
    pm = lambda m: sum(pipe[k][m] for k in ids) / len(ids)
    am = lambda m: sum(ag[k][m] for k in ids) / len(ids)
    tk = lambda x: f"{x:,.0f}".replace(",", ".")
    return "\n".join([
        "| Medida (promedio por pregunta) | Pipeline fijo (Avance 2) | Agente (Avance 3) |", "|---|---|---|",
        f"| Llamadas al LLM | {pm('llamadas_llm'):.2f} | {am('llamadas_llm'):.2f} |",
        f"| Tokens (entrada + salida + razonamiento) | {tk(pm('tokens_total'))} | {tk(am('tokens_total'))} |",
        f"| Tiempo de respuesta (s) | {pm('segundos'):.1f} | {am('segundos'):.1f} |",
        f"\nMedido sobre las mismas {len(ids)} preguntas. Llamadas a tools por pregunta (agente): "
        f"{sum(len(ag[k]['herramientas']) for k in ids) / len(ids):.2f}; preguntas respondidas sin tools: "
        f"{sum(not ag[k]['herramientas'] for k in ids)}/{len(ids)}.",
    ])


if __name__ == "__main__":
    texto = README.read_text(encoding="utf-8")
    for clave, bloque in (("RAGAS", tabla_ragas()), ("CASOS", tabla_casos()), ("COSTO", tabla_costo())):
        patron = re.compile(rf"(<!-- {clave}:INICIO -->)(.*?)(<!-- {clave}:FIN -->)", re.S)
        if not patron.search(texto):
            sys.exit(f"Falta el marcador {clave} en el README")
        texto = patron.sub(lambda m: f"{m.group(1)}\n{bloque}\n{m.group(3)}", texto)
    README.write_text(texto, encoding="utf-8", newline="\n")
    print("README actualizado con las tablas de eval/runs/")
