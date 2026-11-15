"""Extrae la tabla de autoliquidacion de infracciones 2026 (PDF de la Secretaria Distrital
de Movilidad) a un JSON estructurado que usa la tool `calcular_valor_multa`.

Uso (una sola vez; el JSON resultante se versiona en data/tabla_multas_2026.json):
    pip install pdfplumber
    python scripts/extraer_tabla_multas.py "ruta/a/tabla_autoliquidacion_2026.pdf"

Como el PDF es una tabla grafica (las columnas no vienen alineadas como texto), cada fila
se reconstruye con las coordenadas de las palabras: el codigo (A01, C06...) ancla la fila,
las columnas numericas de la derecha se leen a su altura, y las lineas de descripcion se
asignan al codigo vertical mas cercano (descartando los encabezados de cada grupo).
"""

import json
import re
import sys
from pathlib import Path

import pdfplumber

COLUMNAS = {"uvb": (340, 385), "valor": (385, 435), "aprox": (435, 485), "p50": (485, 535), "p75": (535, 600)}
COD = re.compile(r"^[A-I]\d{2}$")
NUM = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d+)?$")  # 13,95 | 168.935 | 1.266.101


def a_numero(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def _filas_de_pagina(pagina) -> list[dict]:
    palabras = pagina.extract_words(use_text_flow=False)
    anclas = sorted((w for w in palabras if w["x0"] < 30 and COD.match(w["text"])), key=lambda w: w["top"])
    if not anclas:
        return []
    ys = [a["top"] for a in anclas]
    limites = [-1e9] + [(ys[i] + ys[i + 1]) / 2 for i in range(len(ys) - 1)] + [1e9]
    # el encabezado de cada grupo termina en la linea "centenas de pesos" y empieza ~20 pt
    # antes de la palabra "Código" (columna izquierda)
    fin_encabezado = sorted(w["top"] for w in palabras if w["text"] == "centenas")
    ini_encabezado = sorted(w["top"] - 22 for w in palabras if w["text"] == "Código" and w["x0"] < 20)

    filas = []
    for i, ancla in enumerate(anclas):
        lo, hi = limites[i], limites[i + 1]
        previos = [t for t in fin_encabezado if t < ancla["top"]]
        if previos:
            lo = max(lo, previos[-1] + 6)
        siguientes = [t for t in ini_encabezado if t > ancla["top"]]
        if siguientes:
            hi = min(hi, siguientes[0])
        desc = [w for w in palabras if 40 <= w["x0"] < 338 and lo <= w["top"] < hi]
        desc.sort(key=lambda w: (round(w["top"]), w["x0"]))
        # columnas numericas por posicion horizontal: UVB, valor, aprox, 50 %, 75 %
        columnas = {}
        for w in palabras:
            if w["x0"] >= 340 and abs(w["top"] - ancla["top"]) <= 7 and NUM.match(w["text"]):
                for nombre, (x1, x2) in COLUMNAS.items():
                    if x1 <= w["x0"] < x2 and nombre not in columnas:
                        columnas[nombre] = w["text"]
        filas.append({
            "codigo": ancla["text"],
            "descripcion": re.sub(r"\s+", " ", " ".join(w["text"] for w in desc)).strip(),
            "_cols": columnas,
        })
    return filas


def _embriaguez(pagina) -> list[dict]:
    """Tabla F (embriaguez): grado y reincidencia (1/2/3 vez), con UVB y valor, sin descuentos."""
    palabras = pagina.extract_words(use_text_flow=False)
    filas_y: dict[int, list] = {}
    for w in palabras:
        filas_y.setdefault(round(w["top"] / 4), []).append(w)
    regs = []
    for k in sorted(filas_y):
        ws = sorted(filas_y[k], key=lambda w: w["x0"])
        textos = [w["text"] for w in ws]
        if "vez" in textos:
            i = textos.index("vez")
            nums = [t for t in textos[i + 1:] if NUM.match(t)]
            if len(nums) >= 3:
                regs.append({"vez": int(textos[i - 1]), "uvb": a_numero(nums[0]), "valor_2026": int(a_numero(nums[1]))})
        elif "NEGARSE" in textos:
            nums = [t for t in textos if NUM.match(t)]
            if len(nums) >= 2:
                regs.append({"vez": None, "uvb": a_numero(nums[0]), "valor_2026": int(a_numero(nums[1]))})
    nombres = ["Grado 0 (positivo)", "Grado I", "Grado II", "Grado III"]
    salida = []
    for g, nombre in enumerate(nombres):
        for r in regs[g * 3: g * 3 + 3]:
            salida.append({"grado": nombre, "reincidencia": f"{r['vez']}.ª vez", **{k: r[k] for k in ("uvb", "valor_2026")}})
    if len(regs) > 12:
        salida.append({"grado": "Negarse a realizar la prueba", "reincidencia": "única", "uvb": regs[12]["uvb"], "valor_2026": regs[12]["valor_2026"]})
    return salida


def extraer(ruta_pdf: str) -> dict:
    filas: list[dict] = []
    uvb_valor = None
    grupos: dict[str, str] = {}
    embriaguez: list[dict] = []

    with pdfplumber.open(ruta_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            m = re.search(r"VALOR UNIDAD UVB\s*\d{4}\s*([\d.]+)", texto)
            if m and uvb_valor is None:
                uvb_valor = int(m.group(1).replace(".", ""))
            for m in re.finditer(r"C[oó]digo\s+([A-I])\.\s+(.+?)\s+Cantidad UVB", texto):
                grupos.setdefault(m.group(1), m.group(2).strip())
            filas.extend(_filas_de_pagina(pagina))
            if "embriaguez" in texto and "GRADO" in texto:
                embriaguez = _embriaguez(pagina)

    salida = []
    for f in filas:
        c = f["_cols"]
        if not all(k in c for k in ("uvb", "valor", "aprox")):
            continue
        desc = f["descripcion"]
        # el titulo del grupo H queda pegado a la primera fila de esa tabla
        desc = re.sub(r"^\(5\) salarios m[ií]nimos legales diarios vigentes \(SMLDV\) convertidas en UVB\s*", "", desc)
        salida.append({
            "codigo": f["codigo"],
            "grupo": f["codigo"][0],
            "descripcion": desc.lstrip('*"').strip(),
            "inmoviliza_vehiculo": desc.startswith("*") or "inmoviliz" in desc.lower(),
            "uvb": a_numero(c["uvb"]),
            "valor_2026": int(a_numero(c["valor"])),
            "valor_aprox_centenas": int(a_numero(c["aprox"])),
            "pagar_dia_1_a_5_habil": int(a_numero(c["p50"])) if "p50" in c and f["codigo"][0] not in "GH" else None,
            "pagar_dia_6_a_20_habil": int(a_numero(c["p75"])) if "p75" in c and f["codigo"][0] not in "GH" else None,
        })

    # G01: el texto se solapa con la nota de la celda y se extrae desordenado; se normaliza a mano
    for i in salida:
        if i["codigo"] == "G01":
            i["descripcion"] = ("El pasajero que sea sorprendido fumando en un vehículo de servicio público debe asistir a un "
                                "curso de seguridad vial (la infracción G se convierte en multa pecuniaria si al día 30 no se ha efectuado el curso).")

    # titulos de grupo (el texto del PDF viene entrecortado; se fijan los que son legibles)
    grupos.update({
        "A": "Conductores de vehículo no automotor o de tracción animal",
        "B": "Conductor de un vehículo automotor",
        "C": "Conductor de un vehículo automotor",
        "D": "Conductor de un vehículo automotor",
        "E": "Conductor de vehículo automotor",
        "F": "Infracciones en que incurren los peatones",
        "G": "Infracciones sancionadas con comparendo educativo",
        "H": "Infracciones sancionadas con amonestación (curso de educación vial) o multa de 5 SMLDV",
        "I": "Infracciones que dan lugar a sanciones pecuniarias de diferente monto",
    })

    return {
        "fuente": "Tabla de autoliquidación de infracciones 2026 - Secretaría Distrital de Movilidad (Bogotá)",
        "marco_normativo": "Ley 769 de 2002 mod. Ley 1383 de 2010 y Ley 1696 de 2013; Resolución 3488 de 2025",
        "valor_unidad_uvb_2026": uvb_valor,
        "descuentos": {
            "dia_1_a_5_habil": "se paga el 50 % del valor (con curso pedagógico previo)",
            "dia_6_a_20_habil": "se paga el 75 % del valor (con curso pedagógico previo)",
        },
        "grupos": grupos,
        "infracciones": salida,
        "embriaguez": embriaguez,
    }


if __name__ == "__main__":
    datos = extraer(sys.argv[1])
    destino = Path(__file__).resolve().parent.parent / "data" / "tabla_multas_2026.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    inf = datos["infracciones"]
    print(f"{len(inf)} infracciones | embriaguez: {len(datos['embriaguez'])} filas | UVB 2026 = {datos['valor_unidad_uvb_2026']}")
    print(f"guardado en {destino}")
