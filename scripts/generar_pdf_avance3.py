"""Rellena la plantilla oficial del Avance 3 (docs/Avance3_plantilla_de_entrega.docx) con los datos
REALES de eval/runs/ y exporta el PDF de entrega con Microsoft Word.

    python scripts/generar_pdf_avance3.py            # genera docs/Avance3_Entrega.docx y .pdf

- Cifras, tablas, casos y costos se leen de eval/runs/*.json (no estan escritos a mano).
- Las capturas se toman de docs/capturas_a3/ (las de consola las genera scripts/render_consola.py a
  partir de la salida real; las de la interfaz desplegada las toma el equipo). Si falta una, la celda
  queda marcada en rojo como [CAPTURA PENDIENTE: nombre.png].
- Requiere Microsoft Word (se usa solo para convertir .docx -> .pdf).
"""

import copy
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.shared import Cm, Pt, RGBColor

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import ragas_eval as R  # noqa: E402
from src.prompts_agent import construir_system_prompt  # noqa: E402
from src.tools import (MAX_CARACTERES_CONSULTA_WEB, MAX_CARACTERES_EXTRACTO_WEB, MAX_CARACTERES_FRAGMENTO,  # noqa: E402
                       MAX_FRAGMENTOS, MAX_RESULTADOS_WEB, SITIOS_PERMITIDOS, CajaDeHerramientas)
from src.config import AppConfig  # noqa: E402

PLANTILLA = RAIZ / "docs" / "Avance3_plantilla_de_entrega.docx"
SALIDA_DOCX = RAIZ / "docs" / "Avance3_Entrega.docx"
SALIDA_PDF = RAIZ / "docs" / "Avance3_Entrega.pdf"
CAPT = RAIZ / "docs" / "capturas_a3"
RUNS = RAIZ / "eval" / "runs"

URL_APP = "https://multaclara.streamlit.app/"
URL_REPO = "https://github.com/samsRPA/proyecto_final_app_ia"
EQUIPO = "Samuel Alejandro Monsalve Sarmiento, Carlos Alberto Franco Hernandez"
ROJO = RGBColor(0xC0, 0x39, 0x2B)


# ---------------------------------------------------------------------------
# Utilidades sobre la plantilla
# ---------------------------------------------------------------------------
def vaciar(celda) -> None:
    for p in celda.paragraphs[1:]:
        p._element.getparent().remove(p._element)
    for r in list(celda.paragraphs[0].runs):
        r._element.getparent().remove(r._element)


def escribir(celda, texto: str, tam: float = 9, negrita: bool = False, mono: bool = False, color=None) -> None:
    """Reemplaza el contenido de una celda. Los saltos de linea crean parrafos nuevos."""
    vaciar(celda)
    lineas = str(texto).split("\n")
    for i, linea in enumerate(lineas):
        p = celda.paragraphs[0] if i == 0 else celda.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(linea)
        r.font.size = Pt(tam)
        r.bold = negrita
        if mono:
            r.font.name = "Consolas"
        if color:
            r.font.color.rgb = color


def imagen(celda, nombre: str, ancho_cm: float = 11.0, ruta: Path | None = None) -> None:
    """Inserta una imagen de docs/capturas_a3 (o ruta) en la celda; si no existe, deja un aviso en rojo."""
    destino = ruta or (CAPT / nombre)
    vaciar(celda)
    p = celda.paragraphs[0]
    if destino.exists():
        p.add_run().add_picture(str(destino), width=Cm(ancho_cm))
    else:
        r = p.add_run(f"[CAPTURA PENDIENTE: {nombre}]")
        r.font.size, r.bold, r.font.color.rgb = Pt(9), True, ROJO


def imagen_ancha(fila, nombre, ancho_cm: float = 15.8, ruta: Path | None = None) -> None:
    """Combina las dos celdas de la fila para usar todo el ancho: etiqueta arriba e imagen(es) debajo.
    `nombre` puede ser un archivo o una lista (p. ej. fuentes de la base y fuentes web)."""
    etiqueta = fila.cells[0].text.strip()
    celda = fila.cells[0].merge(fila.cells[1])
    escribir(celda, etiqueta, tam=9, negrita=True)
    for n in (nombre if isinstance(nombre, list) else [nombre]):
        p = celda.add_paragraph()
        destino = ruta or (CAPT / n)
        if destino.exists():
            p.add_run().add_picture(str(destino), width=Cm(ancho_cm))
        else:
            r = p.add_run(f"[CAPTURA PENDIENTE: {n}]")
            r.font.size, r.bold, r.font.color.rgb = Pt(9), True, ROJO


def permitir_corte_de_filas(tabla) -> None:
    """Quita 'no dividir la fila entre paginas': una fila muy larga (el prompt) saltaba entera y dejaba una pagina casi vacia."""
    for fila in tabla.rows:
        trPr = fila._tr.trPr
        if trPr is not None:
            for e in trPr.findall("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}cantSplit"):
                trPr.remove(e)


def preparar_plantilla(d) -> None:
    """Titulo de entrega y retiro de los textos de instrucciones (cursiva) de la plantilla."""
    for sec in d.sections:  # el pie de pagina de la plantilla dice "Plantilla de entrega"
        for p in sec.footer.paragraphs:
            for r in p.runs:
                if "Plantilla de entrega" in r.text:
                    r.text = r.text.replace("Plantilla de entrega", "Entrega del agente MultaClara")
    for p in list(d.paragraphs):
        if p.style.name == "Title":
            for r in p.runs[1:]:
                r._element.getparent().remove(r._element)
            p.runs[0].text = "Avance 3: Entrega del agente MultaClara"
        elif p.runs and all(r.italic for r in p.runs if r.text.strip()) and p.text.strip():
            p._element.getparent().remove(p._element)


def asegurar_filas(tabla, n_filas: int) -> None:
    """Agrega filas (copiando el formato de la ultima) hasta tener `n_filas` en total."""
    while len(tabla.rows) < n_filas:
        nueva = copy.deepcopy(tabla.rows[-1]._tr)
        tabla._tbl.append(nueva)
        for c in tabla.rows[-1].cells:
            vaciar(c)


def cargar(nombre: str, defecto=None):
    p = RUNS / nombre
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else defecto


def lineas_jsonl(nombre: str) -> list[dict]:
    p = RUNS / nombre
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def fmt(x) -> str:
    return "n/d" if x is None else f"{x:.3f}"


def args_texto(herramientas: list[dict]) -> str:
    if not herramientas:
        return "(ninguna: respondió sin herramientas)"
    return "\n".join(f"{i}. {h['nombre']}({json.dumps(h['argumentos'], ensure_ascii=False)})  →  {h['resumen']}"
                     for i, h in enumerate(herramientas, 1))


# ---------------------------------------------------------------------------
# Datos de las evaluaciones
# ---------------------------------------------------------------------------
def resumen_ragas() -> tuple[dict, dict, int]:
    a2_r, a2_g = cargar("cabecera_respuestas.json", {}), cargar("cabecera_ragas.json", {})
    a3_r, a3_g = cargar("agente_respuestas.json", {}), cargar("agente_ragas.json", {})
    return (R.calcular_resumen(list(a2_r.values()), a2_g), R.calcular_resumen(list(a3_r.values()), a3_g), len(a3_r))


def promedio(xs):
    return sum(xs) / len(xs) if xs else 0.0


# ---------------------------------------------------------------------------
# Construccion
# ---------------------------------------------------------------------------
def construir(textos: dict) -> None:
    d = Document(str(PLANTILLA))
    preparar_plantilla(d)
    T = d.tables
    class _R:  # retriever de mentira: solo para leer las descripciones de las tools
        pass
    caja = CajaDeHerramientas(AppConfig(api_key="x", model="x", temperature=0, max_output_tokens=1), retriever=_R())
    H = caja.herramientas

    # --- Datos del equipo
    escribir(T[0].rows[0].cells[1], EQUIPO)
    escribir(T[0].rows[1].cells[1], URL_REPO)
    escribir(T[0].rows[2].cells[1], URL_APP)

    # --- 1.1 diagrama
    imagen_ancha(T[1].rows[0], "07_arquitectura_agente.png", 16.0, RAIZ / "docs" / "images" / "07_arquitectura_agente.png")

    # --- 1.2 tools
    filas = [
        ("buscar_en_base", "Búsqueda",
         "Busca en la Ley 769 de 2002 (índice vectorial local) los fragmentos más relevantes. Se usa para toda pregunta sobre normas, "
         "comparendos, sanciones, plazos o recursos; una llamada por tema. No se usa en saludos.",
         "consulta (texto)",
         f"Máx. {MAX_FRAGMENTOS} fragmentos por llamada; cada uno ≤ {MAX_CARACTERES_FRAGMENTO} caracteres; consulta ≤ 300 caracteres. "
         "Cada fragmento trae documento y sección (artículo, título, capítulo)."),
        ("calcular_valor_multa", "Tarea",
         "Valor en pesos (2026) de una infracción y descuento por pago oportuno, con la tabla oficial de autoliquidación. "
         "Con fecha del comparendo y de pago cuenta días hábiles (festivos de Colombia) y aplica 50 % (días 1-5) o 75 % (días 6-20).",
         "infraccion (texto o código), fecha_comparendo y fecha_pago (AAAA-MM-DD, opcionales)",
         "Devuelve máx. 3 candidatas; la tabla es un JSON local (124 infracciones); no calcula de memoria; fechas validadas."),
        ("calcular_plazo_descuento", "Tarea",
         "Fechas límite (en días hábiles colombianos) para pagar con 50 % (día hábil 5) y 75 % (día hábil 20), y si el plazo sigue vigente.",
         "fecha_comparendo (AAAA-MM-DD), fecha_consulta (opcional)",
         "Calendario de festivos de Colombia (librería holidays); entrada inválida devuelve error, no excepción."),
        ("buscar_en_web", "Web",
         "Busca información pública o actual SOLO en 8 sitios oficiales y SOLO si buscar_en_base ya se consultó y no tiene la respuesta.",
         "consulta (texto corto, sin datos personales)",
         f"Máx. {MAX_RESULTADOS_WEB} resultados; extracto ≤ {MAX_CARACTERES_EXTRACTO_WEB} car.; consulta enviada ≤ {MAX_CARACTERES_CONSULTA_WEB} car. "
         "y sin cédula/placa/correo; lista de sitios fija en el código; exige buscar antes en la base."),
    ]
    asegurar_filas(T[2], 5)
    for i, f in enumerate(filas, start=1):
        for j, txt in enumerate(f):
            escribir(T[2].rows[i].cells[j], txt, tam=7 if j == 0 else 8, negrita=(j == 0))
    imagen_ancha(T[3].rows[0], "prueba_invoke.png", 16.0)

    # --- 1.3 bucle y registro
    ejemplo = lineas_jsonl("agente_uso_tools.jsonl")
    escribir(T[4].rows[0].cells[1],
             "Bucle manual con el SDK google-genai (sin framework), en src/agent.py. El LLM solo PIDE una tool; el programa la ejecuta "
             "(aplicando los límites) y le devuelve el resultado como function_response. El modelo soporta tool calling "
             "(gemini-3.5-flash-lite); la ejecución automática del SDK está desactivada para controlar el bucle.", tam=8.5)
    escribir(T[4].rows[1].cells[1],
             "5 rondas de uso de tools por pregunta (AGENT_MAX_ITER). Al alcanzarlo, una última llamada fuerza la respuesta en texto "
             "(tool_config mode NONE). Además, tope absoluto de 8 llamadas a tools por pregunta.", tam=8.5)
    escribir(T[4].rows[2].cells[1],
             "Archivo JSONL (una línea por pregunta): logs/uso_tools.jsonl en la app; eval/runs/*_uso_tools.jsonl en las evaluaciones. "
             "Guarda fecha, pregunta recortada, cada tool con argumentos y resumen del resultado, llamadas al LLM, tokens, tiempo e iteraciones.", tam=8.5)
    if len(T[4].rows) < 4:
        asegurar_filas(T[4], 4)
    escribir(T[4].rows[3].cells[0], "Ejemplo del registro (extracto)", tam=9, negrita=True)
    if ejemplo:
        e = next((x for x in ejemplo if x["herramientas"]), ejemplo[0])
        resumen = {k: e[k] for k in ("fecha", "pregunta", "herramientas", "llamadas_llm", "tokens_total", "segundos", "iteraciones")}
        for h in resumen["herramientas"]:
            h.pop("segundos", None)
        escribir(T[4].rows[3].cells[1], json.dumps(resumen, ensure_ascii=False, indent=1)[:1400], tam=7, mono=True)
    else:
        escribir(T[4].rows[3].cells[1], "[PENDIENTE: ejecutar las evaluaciones]", color=ROJO)

    # --- 2. prompt
    permitir_corte_de_filas(T[5])
    escribir(T[5].rows[0].cells[1], construir_system_prompt(date(2026, 11, 14)), tam=6.5, mono=True)
    escribir(T[5].rows[1].cells[1], textos["cambios_prompt"], tam=8.5)

    # --- 3. busqueda web
    escribir(T[6].rows[0].cells[1], "Tavily (API de búsqueda), consultada con httpx desde src/tools.py. La clave TAVILY_API_KEY vive en variables de entorno / secrets, nunca en el repositorio.", tam=8.5)
    escribir(T[6].rows[1].cells[1],
             "Política «primero la base»: solo si buscar_en_base ya se llamó y NO tiene la respuesta, y la pregunta trata de información "
             "pública o actual (norma o resolución vigente o reciente, trámite, definición general). Nunca para saludos, casos personales ni lo que la base ya responde. "
             "La regla está en el docstring de la tool y en el System Prompt, y el código además rechaza la llamada si no hubo búsqueda previa en la base.", tam=8.5)
    escribir(T[6].rows[2].cells[1],
             f"Máximo {MAX_RESULTADOS_WEB} resultados. Cada uno devuelve solo: título, URL, extracto (≤ {MAX_CARACTERES_EXTRACTO_WEB} caracteres) y origen = \"web\".", tam=8.5)
    if len(T[6].rows) < 4:
        asegurar_filas(T[6], 4)
    escribir(T[6].rows[3].cells[0], "Cómo se marca el origen web en la respuesta", tam=9, negrita=True)
    escribir(T[6].rows[3].cells[1],
             "En el texto: [web: URL] junto a cada dato (y subtítulos «Desde la base normativa» / «Desde la web (sitios oficiales)» si se usan ambas). "
             "En la interfaz: panel «Fuentes web» aparte del panel «Fuentes de la base normativa». La base se cita como (Fuente: Ley 769 de 2002, Art. N).", tam=8.5)
    asegurar_filas(T[7], 1 + len(SITIOS_PERMITIDOS))
    for i, (dom, motivo) in enumerate(SITIOS_PERMITIDOS.items(), start=1):
        escribir(T[7].rows[i].cells[0], dom, tam=8.5, negrita=True)
        escribir(T[7].rows[i].cells[1], motivo, tam=8.5)

    # --- 4. ejecuciones
    for tabla, clave, nombre_img in ((T[8], "ejec_saludo", "ejec_saludo.png"), (T[9], "ejec_base", "ejec_base.png"),
                                     (T[10], "ejec_tarea", "ejec_tarea.png"), (T[11], "ejec_web", "ejec_web.png")):
        e = textos[clave]
        escribir(tabla.rows[0].cells[1], e["pregunta"], tam=8.5)
        escribir(tabla.rows[1].cells[1], e["tools"], tam=8, mono=True)
        imagen_ancha(tabla.rows[2], nombre_img, 15.8)
        escribir(tabla.rows[3].cells[1], e["analisis"], tam=8.5)

    # --- 5. Ragas
    a2, a3, n = resumen_ragas()
    for i, m in enumerate(R.METRICAS, start=1):
        escribir(T[12].rows[i].cells[1], fmt(a2[m]), tam=9)
        escribir(T[12].rows[i].cells[2], fmt(a3[m]), tam=9)
        escribir(T[12].rows[i].cells[3], f"{a3[m] - a2[m]:+.3f}" if None not in (a2[m], a3[m]) else "n/d", tam=9, negrita=True)
    escribir(T[13].rows[0].cells[1], f"{n} (las {a3['n_dentro']} del corpus se promedian en la tabla; {a3['n_control']} de control se juzgan por su comportamiento)", tam=8.5)
    escribir(T[13].rows[1].cells[1], textos["analisis_ragas"], tam=8.3)

    # --- 6. casos de comportamiento
    casos = cargar("casos_resultados.json", {})
    asegurar_filas(T[14], 1 + max(7, len(casos)))
    for i, c in enumerate(casos.values(), start=1):
        fila = T[14].rows[i]
        escribir(fila.cells[0], str(c["id"]), tam=7.5)
        escribir(fila.cells[1], c["tipo"], tam=7)
        escribir(fila.cells[2], c["pregunta"], tam=6.5)
        escribir(fila.cells[3], c["esperado"], tam=6.3)
        escribir(fila.cells[4], textos["obtenido"].get(str(c["id"]), "[completar]"), tam=6.3)
        escribir(fila.cells[5], ", ".join(h["nombre"] for h in c["herramientas"]) or "ninguna", tam=6.5)
        escribir(fila.cells[6], str(c["llamadas_llm"]), tam=8)
        escribir(fila.cells[7], "Sí" if c["cumple"] else "No", tam=8, negrita=True)

    # --- 7. costo
    pipe = cargar("pipeline_costo.json", {})
    ag = cargar("agente_respuestas.json", {})
    ids = [k for k in pipe if k in ag]
    pm = lambda m: promedio([pipe[k][m] for k in ids])
    am = lambda m: promedio([ag[k][m] for k in ids])
    for i, (m, f) in enumerate((("llamadas_llm", "{:.2f}"), ("tokens_total", "{:,.0f}"), ("segundos", "{:.1f}")), start=1):
        escribir(T[15].rows[i].cells[1], f.format(pm(m)).replace(",", "."), tam=9)
        escribir(T[15].rows[i].cells[2], f.format(am(m)).replace(",", "."), tam=9)
    escribir(T[16].rows[0].cells[1],
             f"Las mismas {len(ids)} preguntas de eval/dataset_eval.json (16 del corpus, 2 sin respuesta en la base y 2 fuera de alcance), "
             f"ejecutadas por el pipeline fijo del Avance 2 y por el agente. Tokens = entrada + salida + razonamiento interno del modelo. "
             f"Llamadas a tools por pregunta (agente): {promedio([len(ag[k]['herramientas']) for k in ids]):.2f}.", tam=8.5)

    # --- 8. interfaz
    escribir(T[17].rows[0].cells[1], URL_APP)
    imagen_ancha(T[17].rows[1], "ui_tools.png", 15.8)
    imagen_ancha(T[17].rows[2], ["ui_fuentes_base.png", "ui_fuentes_web.png"], 15.8)
    if len(T[17].rows) >= 4:
        imagen_ancha(T[17].rows[3], "ui_fuera_alcance.png", 15.8)

    # --- 9. reflexion
    escribir(T[18].rows[0].cells[1], textos["reflexion"], tam=9)

    d.save(str(SALIDA_DOCX))
    print("docx guardado:", SALIDA_DOCX)


def exportar_pdf() -> None:
    """Convierte con Microsoft Word (COM) a traves de PowerShell."""
    ps = (f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
          f"$doc = $w.Documents.Open('{SALIDA_DOCX}'); $doc.SaveAs([ref]'{SALIDA_PDF}', [ref]17); "
          f"$doc.Close(); $w.Quit()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=180)
    print("pdf guardado:", SALIDA_PDF)


if __name__ == "__main__":
    sys.path.insert(0, str(RAIZ / "docs"))
    from textos_avance3 import TEXTOS  # noqa: E402  (redactado con los resultados reales)
    construir(TEXTOS)
    exportar_pdf()
