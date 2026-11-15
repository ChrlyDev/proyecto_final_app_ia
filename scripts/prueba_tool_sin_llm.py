"""Prueba de las tools SIN el LLM (se llaman directamente con .invoke()).

    python scripts/prueba_tool_sin_llm.py

Muestra el resultado de buscar_en_base y de las dos tools de calculo: fragmentos con documento y
seccion, limites aplicados y cifras exactas. No se llama a ningun modelo de lenguaje generativo
(solo se vectoriza la consulta, como en cualquier busqueda en la base).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import AppConfig  # noqa: E402
from src.tools import CajaDeHerramientas  # noqa: E402

caja = CajaDeHerramientas(AppConfig.from_env())
tools = caja.herramientas

print(">>> tools['buscar_en_base'].invoke({'consulta': 'plazo de caducidad de la acción por una contravención'})")
r = tools["buscar_en_base"].invoke({"consulta": "plazo de caducidad de la acción por una contravención"})
print(f"origen = {r['origen']!r} | fragmentos devueltos = {len(r['fragmentos'])} (límite de código: 5)")
for f in r["fragmentos"]:
    print(f"  - documento={f['documento']!r} | sección={f['seccion'].split(' | ')[0]!r} | similitud={f['similitud']} "
          f"| {len(f['texto'])} car. (límite 1500) | {f['texto'][:60]!r}...")

print("\n>>> tools['calcular_valor_multa'].invoke({'infraccion': 'no usar el cinturón de seguridad', "
      "'fecha_comparendo': '2026-03-02', 'fecha_pago': '2026-03-06'})")
r = tools["calcular_valor_multa"].invoke({"infraccion": "no usar el cinturón de seguridad",
                                          "fecha_comparendo": "2026-03-02", "fecha_pago": "2026-03-06"})
print(json.dumps({"infraccion": {k: r["infraccion"][k] for k in ("codigo", "uvb", "valor_completo_pesos")},
                  "calculo": r["calculo"]}, ensure_ascii=False, indent=2))

print("\n>>> tools['calcular_plazo_descuento'].invoke({'fecha_comparendo': '2026-04-10', 'fecha_consulta': '2026-04-20'})")
r = tools["calcular_plazo_descuento"].invoke({"fecha_comparendo": "2026-04-10", "fecha_consulta": "2026-04-20"})
print(json.dumps({k: r[k] for k in ("descuento_50_hasta", "descuento_75_hasta", "festivos_en_el_rango", "a_la_fecha")},
                 ensure_ascii=False, indent=2))
