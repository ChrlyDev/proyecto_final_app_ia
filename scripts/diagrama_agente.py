"""Genera docs/images/07_arquitectura_agente.png: flujo pregunta -> LLM -> tool -> resultado -> respuesta,
marcando que se queda en el sistema (verde) y que sale hacia un servicio externo (naranja).

    python scripts/diagrama_agente.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

SALIDA = Path(__file__).resolve().parent.parent / "docs" / "images" / "07_arquitectura_agente.png"
VERDE, NARANJA, AZUL, GRIS = "#cfe8d5", "#fdd9b5", "#cfe0f5", "#e6e8ec"
BORDE, TEXTO = "#33415c", "#0b132b"

fig, ax = plt.subplots(figsize=(14, 9.2))
ax.set_xlim(0, 14)
ax.set_ylim(0, 9.2)
ax.axis("off")


def caja(x, y, w, h, txt, color, size=9.2, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05", fc=color, ec=BORDE, lw=1.2))
    ax.text(x + w / 2, y + h / 2, txt, ha="center", va="center", fontsize=size, color=TEXTO,
            fontweight="bold" if bold else "normal", linespacing=1.25)


def flecha(x1, y1, x2, y2, color=BORDE, ls="-", doble=False):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="<|-|>" if doble else "-|>", color=color, lw=1.5, ls=ls, shrinkA=0, shrinkB=0))


def paso(x, y, n):
    ax.text(x, y, str(n), ha="center", va="center", fontsize=9.5, fontweight="bold", color=TEXTO,
            bbox=dict(boxstyle="circle,pad=0.25", fc="white", ec=BORDE, lw=1.3), zorder=5)


ax.text(0.1, 8.95, "ARQUITECTURA DEL AGENTE MULTACLARA", fontsize=13, weight="bold", color="#1c2541")
ax.text(0.1, 8.62, "flujo: pregunta → LLM → tool → resultado → respuesta", fontsize=10, color="#3a506b")

# --- flujo principal (izquierda)
caja(0.2, 4.9, 1.6, 1.1, "Usuario", GRIS, bold=True)
caja(2.2, 4.9, 2.0, 1.1, "Interfaz\nStreamlit\n(historial)", AZUL)
caja(4.7, 4.5, 2.5, 1.9, "AGENTE (bucle)\n• límite: 5 iteraciones\n• ejecuta las tools\n• registro JSONL de uso", AZUL, bold=True)
caja(4.7, 7.0, 2.5, 1.35, "LLM Gemini\n(decide si llama\nuna tool y cuál)", NARANJA)

flecha(1.8, 5.45, 2.2, 5.45)
flecha(4.2, 5.45, 4.7, 5.45)
flecha(5.4, 6.4, 5.4, 7.0, "#c1121f")
paso(5.05, 6.7, 1)
flecha(6.5, 7.0, 6.5, 6.4, "#c1121f")
paso(6.85, 6.7, 2)
flecha(4.7, 4.85, 4.2, 4.85, BORDE, "--")
paso(4.45, 4.55, 5)

# --- herramientas (centro)
caja(8.2, 7.25, 3.1, 1.1, "buscar_en_base\n≤ 5 fragmentos · ≤ 1500 caracteres", VERDE, size=8.6)
caja(8.2, 5.85, 3.1, 1.0, "calcular_valor_multa\n(argumentos que extrae el LLM)", VERDE, size=8.6)
caja(8.2, 4.65, 3.1, 0.95, "calcular_plazo_descuento\n(días hábiles + festivos)", VERDE, size=8.6)
caja(8.2, 2.3, 3.1, 1.3, "buscar_en_web\n≤ 3 resultados · 8 sitios fijos\nsolo si la base no responde", NARANJA, size=8.6)

for y in (7.8, 6.35, 5.12, 2.95):
    flecha(7.2, 5.45, 8.2, y, "#5c677d", doble=True)
paso(7.75, 6.05, "3·4")

# --- recursos (derecha)
caja(11.9, 7.25, 1.9, 1.1, "ChromaDB\n(índice local\n315 vectores)", VERDE, size=8.4)
caja(11.9, 4.95, 1.9, 1.5, "Tabla de multas\n2026 (JSON) +\nfestivos de\nColombia", VERDE, size=8.4)
caja(11.9, 2.3, 1.9, 1.3, "Tavily\n→ sitios\noficiales", NARANJA, size=8.4)
flecha(11.3, 7.8, 11.9, 7.8, "#2d6a4f")
flecha(11.3, 6.35, 11.9, 5.95, "#2d6a4f")
flecha(11.3, 5.12, 11.9, 5.45, "#2d6a4f")
flecha(11.3, 2.95, 11.9, 2.95, "#c1121f")
caja(11.9, 8.5, 1.9, 0.5, "API de embeddings\nGemini", NARANJA, size=7.4)
flecha(12.85, 8.35, 12.85, 8.5, "#c1121f")

# --- notas de privacidad (abajo izquierda)
ax.add_patch(FancyBboxPatch((0.2, 1.75), 7.6, 2.35, boxstyle="round,pad=0.05", fc="#f8f9fb", ec="#b8c0cc", lw=1))
ax.text(0.4, 3.95, "¿Qué sale del sistema y hacia dónde?", fontsize=9.2, weight="bold", va="top", color="#1c2541")
ax.text(0.4, 3.6,
        "• Hacia Gemini (LLM): instrucciones + pregunta + historial + lo que\n"
        "   devolvieron las tools (fragmentos recuperados, nunca el corpus completo).\n"
        "• Hacia la API de embeddings de Gemini: solo el texto de la consulta.\n"
        "• Hacia Tavily: solo la consulta redactada (≤ 200 caracteres, sin cédula,\n"
        "   placa ni correo), y únicamente después de buscar en la base.\n"
        "• La base, el índice vectorial y la tabla de multas no se envían\n"
        "   completos a ningún servicio.",
        fontsize=8.3, va="top", color="#1c2541", linespacing=1.5)

# --- pasos y leyenda (abajo)
ax.text(0.2, 1.45,
        "1  el agente envía al LLM la pregunta, el historial y las instrucciones     2  el LLM pide una tool (o responde en texto)\n"
        "3·4  el programa ejecuta la tool (con sus límites) y devuelve el resultado al LLM (origen: base, cálculo o web)\n"
        "5  respuesta final al usuario, con las fuentes separadas por origen",
        fontsize=8.5, va="top", color=TEXTO, linespacing=1.6)
ax.add_patch(FancyBboxPatch((0.2, 0.2), 0.35, 0.28, boxstyle="round,pad=0.02", fc=VERDE, ec=BORDE))
ax.text(0.7, 0.26, "se queda en el sistema (nada sale)", fontsize=8.8)
ax.add_patch(FancyBboxPatch((4.6, 0.2), 0.35, 0.28, boxstyle="round,pad=0.02", fc=NARANJA, ec=BORDE))
ax.text(5.1, 0.26, "sale hacia un servicio externo", fontsize=8.8)

SALIDA.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(SALIDA, dpi=170, bbox_inches="tight")
print("guardado:", SALIDA)
