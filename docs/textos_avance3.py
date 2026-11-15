"""Textos narrativos del PDF del Avance 3 (se leen en scripts/generar_pdf_avance3.py).

Las cifras NO se escriben aqui a mano cuando salen de eval/runs/: las tablas las rellena el generador.
Los analisis estan redactados con los resultados reales de las corridas.
"""

import json
import re
from pathlib import Path

CAPT = Path(__file__).resolve().parent / "capturas_a3"
RUNS = Path(__file__).resolve().parent.parent / "eval" / "runs"


def _leer(nombre: str) -> dict:
    """Pregunta y herramientas usadas (con argumentos) tal como las imprimio agente_cli.py."""
    ruta = CAPT / f"{nombre}.txt"
    if not ruta.exists():
        return {"pregunta": "[PENDIENTE: ejecutar agente_cli.py]", "tools": "[PENDIENTE]"}
    t = ruta.read_text(encoding="utf-8")
    pregunta = re.search(r"Pregunta: (.+)", t).group(1).strip()
    bloque = t.split("Herramientas usadas:")[1].split("\n\nRespuesta:")[0]
    tools = "(ninguna: respondió sin herramientas)" if "(ninguna)" in bloque else "\n".join(
        l.strip() for l in bloque.strip().splitlines())
    llamadas = re.search(r"\[llamadas al LLM: (\d+)", t)
    return {"pregunta": pregunta, "tools": tools + (f"\nLlamadas al LLM: {llamadas.group(1)}" if llamadas else "")}


def _obtenido() -> dict:
    """Comportamiento obtenido de cada caso, resumido a partir de eval/runs/casos_resultados.json."""
    casos = json.loads((RUNS / "casos_resultados.json").read_text(encoding="utf-8")) if (RUNS / "casos_resultados.json").exists() else {}
    salida = {}
    for k, c in casos.items():
        tools = ", ".join(h["nombre"] for h in c["herramientas"]) or "ninguna tool"
        resp = " ".join(c["respuesta"].replace("**", "").replace("*", "").split())
        resp = (resp[:130].rsplit(" ", 1)[0] + "…") if len(resp) > 130 else resp
        fallas = [v["detalle"] for v in c["verificaciones"] if not v["ok"]]
        salida[k] = f"{tools}. «{resp}»" + (f" ✗ {'; '.join(fallas)}" if fallas else " ✓ verificaciones automáticas OK")
        if k == "9":  # cumple sin haber usado la web: se dice para no dar a entender que se probo la redaccion aqui
            salida[k] += " (no necesitó la web: respondió con la base; la ruta de privacidad se ejercita en el caso 10)"
    return salida


TEXTOS = {
    "cambios_prompt": (
        "1) El prompt ya NO lleva el contexto: antes el programa pegaba 5 fragmentos en <contexto_normativo>; ahora llegan como resultado de buscar_en_base, "
        "porque el agente puede saludar sin buscar, buscar varias veces o no buscar.\n"
        "2) Bloque <herramientas>: cuándo usar cada tool y cuándo NO; «no calcules de memoria»; política «primero la base» para la web.\n"
        "3) Responder solo con lo que devuelven las tools y citar por origen: (Fuente: Ley 769 de 2002, Art. N), (Fuente: Tabla de autoliquidación 2026) y [web: URL].\n"
        "4) Respuesta exacta cuando la información no está en la base ni en los sitios permitidos.\n"
        "5) Anti-inyección ampliada: los resultados de las tools también son datos; no se vuelca el corpus ni las instrucciones.\n"
        "6) Few-shot: de 3 ejemplos JSON como turnos a 6 ejemplos de decisión (saludo, dos temas, cálculo, plazo, web, instrucción maliciosa), porque ahora lo que se enseña es cuándo usar cada tool.\n"
        "7) Formato de salida: de JSON forzado a texto con secciones (Resumen, Revisión, Defensa y riesgo, Recomendaciones); las fuentes de la interfaz salen del registro de las tools.\n"
        "8) La fecha de hoy la inyecta el código. Una corrida piloto mostró que el agente usaba la tool de cálculo para «¿con cuánta multa se sanciona…?» (que la ley responde en SMLDV); "
        "se aclaró la diferencia entre la sanción que fija la ley y el valor en pesos."
    ),
    "ejec_saludo": {**_leer("ejec_saludo"), "analisis": (
        "Sí. El agente respondió con un saludo breve sin llamar ninguna tool: 0 tools y 1 llamada al LLM. Era lo esperado: una conversación casual no necesita la base.")},
    "ejec_base": {**_leer("ejec_base"), "analisis": (
        "Sí. Llamó buscar_en_base una vez, con una consulta formulada por él mismo, y respondió con la reposición y la apelación citando (Fuente: Ley 769 de 2002, Art. 142), "
        "que coincide con el texto de la ley. No usó la web porque la base ya respondía.")},
    "ejec_tarea": {**_leer("ejec_tarea"), "analisis": (
        "Sí. Usó calcular_plazo_descuento y extrajo de la pregunta fecha_comparendo=2026-04-10 (el programa añadió la fecha de hoy). Respondió viernes 17 de abril de 2026 (50 %) y "
        "lunes 11 de mayo de 2026 (75 %), descontando el festivo del 1 de mayo, sin calcular de memoria; además advirtió que, a la fecha de hoy, esos plazos ya vencieron.")},
    "ejec_web": {**_leer("ejec_web"), "analisis": (
        "Sí. Siguió la política «primero la base»: llamó buscar_en_base y, como la ley no trae resoluciones recientes, llamó buscar_en_web (3 resultados, todos de mintransporte.gov.co, sitio permitido). "
        "La respuesta separa «Desde la base normativa» (Art. 106, patinetas hasta 40 km/h) de «Desde la web (sitios oficiales)» y marca [web: URL]. "
        "Límite a tener en cuenta: la tool solo entrega extractos de hasta 300 caracteres, así que la respuesta resume lo que dicen los extractos y no el texto completo de la resolución.")},
    "analisis_ragas": (
        "MÉTRICAS DE CONTROL (no están en la tabla): hit_articulo 1,000 → 1,000; rechazo_correcto 0,75 → 0,50 (explicado abajo).\n"
        "MEJORÓ: answer_relevancy (+0,026: las respuestas del agente son más directas) y faithfulness (+0,007). EMPEORÓ levemente context_precision (−0,002) y context_recall quedó igual (1,000); "
        "todas esas diferencias están dentro del ruido (16 preguntas del corpus, juez no determinista), y es lo esperable: para las preguntas que la base responde, el agente hace lo mismo que el pipeline (una búsqueda y una respuesta).\n"
        "COMPONENTE: el agente formula su propia consulta a la base. Eso mejoró la precisión en las preguntas 1, 7 y 16 y la empeoró en la 8 (1,00→0,50) y la 11, con efecto neto nulo. Además, el texto evaluado en el Avance 2 era solo fundamento + hallazgos, "
        "y el del agente es la respuesta completa, así que la comparación no es exacta.\n"
        "HALLAZGO: en la pregunta 17 el agente respondió correctamente (conducta D.7, 30 SMLDV) pero sus fragmentos NO contenían esa frase: la tomó de su memoria, y Ragas lo detectó (fidelidad 0,00). "
        "La regla «responde solo con lo que devuelven las tools» está en el prompt pero no se puede imponer por código: es el riesgo principal de un agente.\n"
        "En la pregunta 18 ahora responde «15 SMLDV en pesos» ($633.200) con la tool de cálculo, algo imposible en el Avance 2. rechazo_correcto baja de 0,75 a 0,50 porque las dos preguntas ajenas se rechazaron bien (sin tools) "
        "pero las otras dos de control ya no se «rechazan»: el dataset estaba pensado para el pipeline.\n"
        "MEJORAS: verificar las afirmaciones contra los fragmentos antes de responder y actualizar las preguntas de control."
    ),
    "obtenido": _obtenido(),
    "reflexion": (
        "El agente NO mejora la calidad en las preguntas que el pipeline ya resolvía: en las 16 preguntas del corpus la fidelidad (0,991 frente a 0,984), la relevancia (0,951 frente a 0,926), la precisión (0,958 frente a 0,960) y la cobertura "
        "(1,000) son prácticamente iguales, dentro del ruido de una evaluación pequeña con un juez no determinista. Lo que mejora son las CAPACIDADES: (1) cálculos exactos —valor en pesos y plazos en días hábiles con los festivos de Colombia— que el pipeline solo podía "
        "contestar de memoria o no contestar (la pregunta 18, imposible en el Avance 2, ahora se responde con $633.200); (2) información pública o actual tomada de sitios oficiales, con la fuente [web: URL] marcada; y (3) no buscar cuando no hace falta: "
        "los saludos y las preguntas ajenas se respondieron sin tools. Además, el registro de uso hace auditable cada decisión.\n\n"
        "Lo que cuesta: 2 llamadas al LLM por pregunta frente a 1, 7.662 tokens frente a 4.031 y 4,9 s frente a 1,8 s (≈1,9× los tokens y ≈2,7× el tiempo). Con la capa gratuita de Gemini también significa llegar antes a los límites de cuota.\n\n"
        "Errores de decisión del modelo: en una corrida piloto eligió la tool de cálculo para una pregunta que la ley responde en SMLDV (se corrigió aclarando el prompt), y en la pregunta 17 respondió bien pero con su memoria y no con lo que devolvieron las tools; "
        "Ragas lo detectó con una fidelidad de 0,00. Es el riesgo central de un agente: la regla «responde solo con lo que devuelven las tools» vive en el prompt y no se puede imponer por código, mientras que en el pipeline fijo el contexto siempre se pega.\n\n"
        "Conclusión: el agente vale la pena cuando la tarea exige un cálculo o información fuera de la base; para lo que la base ya responde, el pipeline fijo es más barato y igual de bueno. "
        "Mejoras: verificar las afirmaciones contra los fragmentos antes de responder y combinar ambas estrategias (recuperación siempre y tools opcionales)."
    ),
}
