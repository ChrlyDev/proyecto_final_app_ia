"""Pruebas de las tools y del bucle del agente, SIN llamar a ningun servicio externo.

    python -m unittest discover -s tests -v

Se usan dobles de prueba: un retriever falso (fragmentos fijos), un cliente HTTP falso
(respuestas de Tavily simuladas) y un cliente LLM falso (que pide tools en secuencia). Asi se
comprueban los LIMITES escritos en el codigo y la logica del bucle sin gastar cuota.
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import tools  # noqa: E402
from src.agent import AgenteMultaClara  # noqa: E402
from src.config import AppConfig  # noqa: E402
from src.registro import RegistroUsoTools  # noqa: E402
from src.retriever import Fragmento  # noqa: E402


def config(**extra) -> AppConfig:
    return AppConfig(api_key="x", model="m", temperature=0.0, max_output_tokens=100, tavily_api_key="tvly-falsa", **extra)


class RetrieverFalso:
    def __init__(self, n=8, largo=3000):
        self.n, self.largo = n, largo
        self.top_k_pedido = None

    def buscar(self, consulta, top_k=None):
        self.top_k_pedido = top_k
        return [Fragmento("A" * self.largo, "Ley 769 de 2002", str(i), f"Ley 769 de 2002, Art. {i}", "TÍTULO I", "CAP I", 0.9)
                for i in range(1, self.n + 1)]  # devuelve MAS de lo permitido a proposito


class HttpFalso:
    def __init__(self, resultados):
        self.resultados, self.ultimo_json = resultados, None

    def post(self, url, json=None, headers=None, timeout=None):
        self.ultimo_json = json
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"results": self.resultados})


def caja(retriever=None, http=None, **cfg):
    return tools.CajaDeHerramientas(config(**cfg), retriever=retriever or RetrieverFalso(), cliente_http=http)


class PruebasBuscarEnBase(unittest.TestCase):
    def test_limite_de_fragmentos_y_longitud(self):
        r = caja().herramientas["buscar_en_base"].invoke({"consulta": "caducidad"})
        self.assertEqual(len(r["fragmentos"]), tools.MAX_FRAGMENTOS)
        for f in r["fragmentos"]:
            self.assertLessEqual(len(f["texto"]), tools.MAX_CARACTERES_FRAGMENTO)
            self.assertTrue(f["truncado"])

    def test_pide_al_retriever_solo_el_maximo(self):
        rf = RetrieverFalso()
        caja(retriever=rf).herramientas["buscar_en_base"].invoke({"consulta": "x"})
        self.assertEqual(rf.top_k_pedido, tools.MAX_FRAGMENTOS)

    def test_cada_fragmento_trae_documento_y_seccion_para_citar(self):
        f = caja().herramientas["buscar_en_base"].invoke({"consulta": "x"})["fragmentos"][0]
        self.assertEqual(f["documento"], "Ley 769 de 2002")
        self.assertIn("Art. 1", f["seccion"])
        self.assertEqual(f["cita"], "Ley 769 de 2002, Art. 1")

    def test_consulta_vacia_o_faltante(self):
        self.assertIn("error", caja().herramientas["buscar_en_base"].invoke({"consulta": "  "}))
        self.assertIn("error", caja().herramientas["buscar_en_base"].invoke({}))


class PruebasTareasDelDominio(unittest.TestCase):
    def test_valor_con_descuento_50(self):  # 4.o dia habil
        r = caja().herramientas["calcular_valor_multa"].invoke(
            {"infraccion": "no usar el cinturón de seguridad", "fecha_comparendo": "2026-03-02", "fecha_pago": "2026-03-06"})
        self.assertEqual(r["infraccion"]["codigo"], "C06")
        self.assertEqual(r["calculo"]["dias_habiles_transcurridos"], 4)
        self.assertEqual(r["calculo"]["valor_a_pagar_pesos"], 316600)

    def test_valor_con_descuento_75_y_sin_descuento(self):
        h = caja().herramientas["calcular_valor_multa"]
        r75 = h.invoke({"infraccion": "C06", "fecha_comparendo": "2026-04-10", "fecha_pago": "2026-04-20"})
        self.assertEqual(r75["calculo"]["valor_a_pagar_pesos"], 474900)
        r100 = h.invoke({"infraccion": "C06", "fecha_comparendo": "2026-04-10", "fecha_pago": "2026-06-01"})
        self.assertEqual(r100["calculo"]["valor_a_pagar_pesos"], 633200)

    def test_plazo_descuenta_festivo_del_1_de_mayo(self):
        r = caja().herramientas["calcular_plazo_descuento"].invoke({"fecha_comparendo": "2026-04-10", "fecha_consulta": "2026-04-20"})
        self.assertEqual(r["descuento_50_hasta"]["fecha"], "2026-04-17")
        self.assertEqual(r["descuento_75_hasta"]["fecha"], "2026-05-11")
        self.assertTrue(any("2026-05-01" in f for f in r["festivos_en_el_rango"]))

    def test_fecha_con_formato_invalido_no_tumba_la_tool(self):
        r = caja().herramientas["calcular_plazo_descuento"].invoke({"fecha_comparendo": "mañana"})
        self.assertIn("error", r)

    def test_infraccion_inexistente(self):
        self.assertIn("error", caja().herramientas["calcular_valor_multa"].invoke({"infraccion": "volar en escoba"}))


RES_WEB = [
    {"title": "Resolución", "url": "https://www.mintransporte.gov.co/res", "content": "x" * 1000},
    {"title": "Blog", "url": "https://blog-no-oficial.com/nota", "content": "no permitido"},
    {"title": "SIMIT", "url": "https://www.simit.org.co/consulta", "content": "ok"},
    {"title": "Otro", "url": "https://sub.runt.com.co/a", "content": "ok"},
    {"title": "Cuarto", "url": "https://www.ansv.gov.co/b", "content": "ok"},
]


class PruebasBuscarEnWeb(unittest.TestCase):
    def preparar(self, **kw):
        http = HttpFalso(RES_WEB)
        c = caja(http=http, **kw)
        c.nueva_pregunta()
        c.herramientas["buscar_en_base"].invoke({"consulta": "algo"})  # politica: primero la base
        return c, http

    def test_politica_primero_la_base(self):
        c = caja(http=HttpFalso(RES_WEB))
        c.nueva_pregunta()
        self.assertIn("error", c.herramientas["buscar_en_web"].invoke({"consulta": "resolución"}))

    def test_maximo_3_resultados_extracto_corto_y_solo_sitios_permitidos(self):
        c, http = self.preparar()
        r = c.herramientas["buscar_en_web"].invoke({"consulta": "resolución micromovilidad"})
        self.assertEqual(len(r["resultados"]), tools.MAX_RESULTADOS_WEB)
        self.assertNotIn("blog-no-oficial.com", " ".join(x["url"] for x in r["resultados"]))
        for x in r["resultados"]:
            self.assertEqual(x["origen"], "web")
            self.assertLessEqual(len(x["extracto"]), tools.MAX_CARACTERES_EXTRACTO_WEB)

    def test_la_lista_de_sitios_la_fija_el_codigo_no_el_llm(self):
        c, http = self.preparar()
        c.herramientas["buscar_en_web"].invoke({"consulta": "x", "include_domains": ["malo.com"]})  # argumento ajeno: se ignora
        self.assertEqual(http.ultimo_json["include_domains"], list(tools.SITIOS_PERMITIDOS))
        self.assertEqual(http.ultimo_json["max_results"], tools.MAX_RESULTADOS_WEB)

    def test_datos_personales_no_salen_hacia_el_buscador(self):
        c, http = self.preparar()
        c.herramientas["buscar_en_web"].invoke({"consulta": "multas de la cédula 1032456789 placa ABC123 correo a@b.co"})
        enviada = http.ultimo_json["query"]
        for dato in ("1032456789", "ABC123", "a@b.co"):
            self.assertNotIn(dato, enviada)

    def test_sin_clave_responde_con_error_claro(self):
        c = tools.CajaDeHerramientas(AppConfig(api_key="x", model="m", temperature=0, max_output_tokens=1),
                                     retriever=RetrieverFalso(), cliente_http=HttpFalso([]))
        c.herramientas["buscar_en_base"].invoke({"consulta": "x"})
        self.assertIn("TAVILY_API_KEY", c.herramientas["buscar_en_web"].invoke({"consulta": "x"})["error"])


# ---------- bucle del agente con un LLM falso ----------
def respuesta_llm(texto=None, llamadas=()):
    fcs = [SimpleNamespace(name=n, args=a) for n, a in llamadas]
    contenido = SimpleNamespace(role="model", parts=[])
    return SimpleNamespace(function_calls=fcs, text=texto, candidates=[SimpleNamespace(content=contenido)],
                           usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15))


class LlmFalso:
    """Devuelve las respuestas de `guion` una tras otra y recuerda cuantas veces lo llamaron."""

    def __init__(self, guion):
        self.guion, self.llamadas = list(guion), 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.llamadas += 1
        return self.guion.pop(0) if len(self.guion) > 1 else self.guion[0]


class PruebasBucleDelAgente(unittest.TestCase):
    def agente(self, guion, **cfg):
        self.log = Path(tempfile.mkdtemp()) / "uso.jsonl"
        llm = LlmFalso(guion)
        ag = AgenteMultaClara(config(**cfg), caja=caja(), registro=RegistroUsoTools(self.log), cliente=llm)
        return ag, llm

    def test_saludo_sin_tools_una_sola_llamada_al_llm(self):
        ag, llm = self.agente([respuesta_llm(texto="¡Hola!")])
        r = ag.responder("Hola")
        self.assertEqual((r.llamadas_tools, r.llamadas_llm, r.texto), ([], 1, "¡Hola!"))

    def test_busca_dos_veces_y_luego_responde(self):
        ag, llm = self.agente([
            respuesta_llm(llamadas=[("buscar_en_base", {"consulta": "velocidad"}), ("buscar_en_base", {"consulta": "caducidad"})]),
            respuesta_llm(texto="Respuesta con dos temas"),
        ])
        r = ag.responder("dos temas")
        self.assertEqual(r.herramientas_usadas, ["buscar_en_base", "buscar_en_base"])
        self.assertEqual(r.llamadas_llm, 2)

    def test_limite_de_iteraciones_fuerza_respuesta_final(self):
        pide_siempre = respuesta_llm(llamadas=[("buscar_en_base", {"consulta": "x"})])
        ag, llm = self.agente([pide_siempre, respuesta_llm(texto="final forzado")], agente_max_iteraciones=2)
        # el LLM falso solo "obedece" la orden de no usar tools cuando se le pasa mode NONE
        llm.generate_content = lambda model, contents, config: (
            respuesta_llm(texto="final forzado") if config.tool_config else pide_siempre)
        r = ag.responder("pregunta sin fin")
        self.assertEqual(r.iteraciones, 2)
        self.assertEqual(r.llamadas_llm, 3)  # 2 rondas con tools + 1 forzada sin tools
        self.assertEqual(r.texto, "final forzado")

    def test_tool_inexistente_no_rompe_el_bucle(self):
        ag, _ = self.agente([respuesta_llm(llamadas=[("herramienta_fantasma", {})]), respuesta_llm(texto="ok")])
        r = ag.responder("x")
        self.assertIsNotNone(r.llamadas_tools[0].error)

    def test_el_registro_guarda_tools_argumentos_y_llamadas_llm(self):
        import json
        ag, _ = self.agente([respuesta_llm(llamadas=[("buscar_en_base", {"consulta": "recursos"})]), respuesta_llm(texto="ok")])
        ag.responder("¿recursos?")
        linea = json.loads(self.log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(linea["herramientas"][0]["nombre"], "buscar_en_base")
        self.assertEqual(linea["herramientas"][0]["argumentos"], {"consulta": "recursos"})
        self.assertEqual(linea["llamadas_llm"], 2)


class PruebasUtilidades(unittest.TestCase):
    def test_dias_habiles(self):
        self.assertEqual(tools.sumar_dias_habiles(date(2026, 3, 2), 4), date(2026, 3, 6))
        # 23 de marzo de 2026 es festivo (San José): 20-mar viernes + 1 habil = martes 24
        self.assertEqual(tools.sumar_dias_habiles(date(2026, 3, 20), 1), date(2026, 3, 24))

    def test_redaccion(self):
        self.assertEqual(tools._redactar("ley 769 de 2002 artículo 131"), "ley 769 de 2002 artículo 131")


if __name__ == "__main__":
    unittest.main()
