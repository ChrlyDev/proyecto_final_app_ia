"""Convierte la salida REAL de un comando (texto) en una imagen estilo terminal, para el PDF.

    python scripts/render_consola.py salida.txt docs/capturas_a3/saludo.png "python agente_cli.py \"Hola\""

La imagen no inventa nada: es exactamente el texto que imprimio el comando. Si prefieren una
captura de pantalla real de su terminal, pueden reemplazar el PNG con el mismo nombre.
"""

import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FUENTES = [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf", "DejaVuSansMono.ttf"]
FONDO, TEXTO, BARRA, COMANDO = (30, 30, 30), (212, 212, 212), (60, 60, 60), (120, 220, 140)


def _fuente(tam: int):
    for f in FUENTES:
        try:
            return ImageFont.truetype(f, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def renderizar(texto: str, destino: str | Path, comando: str = "", ancho_car: int = 105, tam: int = 16) -> None:
    fuente = _fuente(tam)
    lineas: list[str] = []
    n_cmd = 0
    if comando:
        cmd = textwrap.wrap("PS> " + comando, ancho_car, subsequent_indent="    ", break_long_words=True)
        lineas += cmd + [""]
        n_cmd = len(cmd)
    for linea in texto.replace("\t", "    ").splitlines():
        lineas += textwrap.wrap(linea, ancho_car, subsequent_indent="    ", break_long_words=True) or [""]
    caja = fuente.getbbox("M")
    ancho_c, alto_l = caja[2], int(tam * 1.35)
    margen, barra = 16, 30
    img = Image.new("RGB", (ancho_c * ancho_car + 2 * margen, barra + alto_l * len(lineas) + 2 * margen), FONDO)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, img.width, barra], fill=BARRA)
    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse([12 + i * 20, 9, 24 + i * 20, 21], fill=c)
    d.text((80, 6), "Terminal — MultaClara", font=_fuente(14), fill=(200, 200, 200))
    y = barra + margen
    for i, linea in enumerate(lineas):
        d.text((margen, y), linea, font=fuente, fill=COMANDO if i < n_cmd else TEXTO)
        y += alto_l
    Path(destino).parent.mkdir(parents=True, exist_ok=True)
    img.save(destino)


if __name__ == "__main__":
    entrada, salida = sys.argv[1], sys.argv[2]
    renderizar(Path(entrada).read_text(encoding="utf-8"), salida, sys.argv[3] if len(sys.argv) > 3 else "")
    print("guardado:", salida)
