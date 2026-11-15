"""Evaluacion del AGENTE (Avance 3): Ragas frente al pipeline fijo, casos de comportamiento y costo.

Tres modos (cada uno guarda sus resultados en eval/runs/ y es reanudable):

  python agent_eval.py ragas  --tag agente --comparar cabecera
      Pasa las MISMAS preguntas de eval/dataset_eval.json por el agente, evalua con Ragas
      (faithfulness, answer_relevancy, context_precision, context_recall) y compara con la corrida
      del Avance 2. Los `retrieved_contexts` del agente son los fragmentos que devolvieron sus tools.

  python agent_eval.py casos
      Ejecuta eval/casos_comportamiento.json (el comportamiento esperado se escribio antes de
      correrlos) y verifica automaticamente: tools usadas, llamadas al LLM, origen web, etc.

  python agent_eval.py costo
      Compara llamadas al LLM, tokens y tiempo por pregunta entre el pipeline fijo (Avance 2) y el agente.
"""

import argparse
import dataclasses
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import ragas_eval as R  # reutiliza utilidades, metricas y tablas del Avance 2
from src.agent import AgenteError, AgenteMultaClara, RespuestaAgente
from src.assistant import _RE_CITA, _RE_NOTA, AssistantError, MultaClaraAssistant
from src.config import AppConfig, ConfigError
from src.prompts_agent import RESPUESTA_SIN_INFORMACION
from src.tools import SITIOS_PERMITIDOS

CASOS = Path("eval/casos_comportamiento.json")
_RE_RECHAZO = re.compile(
    r"solo (?:puedo )?(?:ayud|respond|atiend)|fuera de (?:mi )?alcance|no puedo (?:ayud|asist)|"
    r"no (?:est[aá]|tiene)n? relaci[oó]n|solo (?:te )?ayudo", re.IGNORECASE)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t).lower())


# --------------------------------------------------------------------------
# Registro de una corrida del agente sobre una pregunta del dataset
# --------------------------------------------------------------------------
def _num_articulo(cita: str) -> str:
    m = re.search(r"Art\.\s*(\d+)", cita)
    return m.group(1) if m else ""


def registro_ragas(item: dict, r: RespuestaAgente) -> dict:
    """Formato compatible con ragas_eval (mismos campos que el pipeline del Avance 2)."""
    recuperados = sorted({_num_articulo(f["cita"]) for f in r.fuentes_base} - {""})
    citados = {m.group(1) for m in _RE_CITA.finditer(r.texto)
               if not _RE_NOTA.search(r.texto[max(0, m.start() - 40): m.start()])}
    sin_respaldo = sorted(citados - set(recuperados), key=int)
    rechazo = bool(_RE_RECHAZO.search(r.texto)) or RESPUESTA_SIN_INFORMACION[:40] in r.texto
    return {
        "id": item["id"], "tipo": item["tipo"], "pregunta": item["pregunta"], "ground_truth": item["ground_truth"],
        "articulos_esperados": item.get("articulos", []), "respuesta": r.texto,
        "clasificacion": "fuera_de_alcance" if rechazo else "consulta_general",
        "contextos": r.contextos, "articulos_recuperados": recuperados, "citas_no_respaldadas": sin_respaldo,
        "veredicto": {"fundamento": r.texto, "hallazgos": []},
        "herramientas": [{"nombre": c.nombre, "argumentos": c.argumentos, "resumen": c.resumen} for c in r.llamadas_tools],
        "llamadas_llm": r.llamadas_llm, "tokens_entrada": r.tokens_entrada, "tokens_salida": r.tokens_salida,
        "tokens_total": r.tokens_total, "segundos": round(r.segundos, 3), "iteraciones": r.iteraciones,
    }


def con_reintento(fn, intentos: int = 6):
    for i in range(intentos):
        try:
            return fn()
        except AgenteError as exc:
            if i == intentos - 1 or not re.search(r"429|RESOURCE_EXHAUSTED|quota", str(exc), re.I):
                raise
            time.sleep(30)


# --------------------------------------------------------------------------
# Modo 1: Ragas sobre el agente
# --------------------------------------------------------------------------
def modo_ragas(config: AppConfig, args) -> None:
    dataset = R.cargar_json(R.DATASET, [])[: args.limite] if args.limite else R.cargar_json(R.DATASET, [])
    ruta = R.RUNS / f"{args.tag}_respuestas.json"
    cache: dict = R.cargar_json(ruta, {})
    if not args.solo_resumen:
        agente = AgenteMultaClara(config, registro=None, registrar=False)  # el log de estas corridas va aparte
        from src.registro import RegistroUsoTools
        registro = RegistroUsoTools(R.RUNS / f"{args.tag}_uso_tools.jsonl")
        for item in dataset:
            if str(item["id"]) in cache:
                continue
            print(f"[{item['id']:>2}/{len(dataset)}] {item['pregunta'][:70]}")
            agente.reiniciar()
            r = con_reintento(lambda: agente.responder(item["pregunta"]))
            registro.registrar(r, sesion=f"ragas-{args.tag}")
            cache[str(item["id"])] = registro_ragas(item, r)
            R.guardar_json(ruta, cache)
            print(f"      tools: {r.herramientas_usadas or '(ninguna)'} | llamadas LLM: {r.llamadas_llm}")
            time.sleep(args.pausa)
    registros = [cache[str(i["id"])] for i in dataset if str(i["id"]) in cache]
    if args.solo_generar:
        return
    ragas = R.evaluar_con_ragas(config, registros, args.tag, args.juez or config.model, args.rpm_juez, args.lote) \
        if not args.solo_resumen else R.cargar_json(R.RUNS / f"{args.tag}_ragas.json", {})
    res = R.calcular_resumen(registros, ragas)
    base = None
    if args.comparar:
        rb = list(R.cargar_json(R.RUNS / f"{args.comparar}_respuestas.json", {}).values())
        base = R.calcular_resumen(rb, R.cargar_json(R.RUNS / f"{args.comparar}_ragas.json", {}))
    md = R.tabla_markdown(args.tag, res, args.comparar, base)
    (R.RUNS / f"{args.tag}_resumen.md").write_text(md, encoding="utf-8")
    print("\n" + md)


# --------------------------------------------------------------------------
# Modo 2: casos de comportamiento
# --------------------------------------------------------------------------
def _llamadas(r: RespuestaAgente, nombre: str):
    return [c for c in r.llamadas_tools if c.nombre == nombre]


def _corpus(agente: AgenteMultaClara) -> str:
    docs = agente.caja._retriever._col.get()["documents"]
    return _norm("\n".join(docs))


def verificar(v: dict, r: RespuestaAgente, corpus: str) -> tuple[bool, str]:
    t, texto = v["tipo"], r.texto
    if t == "sin_tools":
        return (not r.llamadas_tools, f"{len(r.llamadas_tools)} tools llamadas")
    if t == "llamada_min":
        n = len(_llamadas(r, v["tool"]))
        return (n >= v["n"], f"{v['tool']}: {n} llamadas (mín. {v['n']})")
    if t == "sin_tool":
        n = len(_llamadas(r, v["tool"]))
        return (n == 0, f"{v['tool']}: {n} llamadas (esperado 0)")
    if t == "args_contienen":
        vals = [str(c.argumentos.get(v["campo"], "")) for c in _llamadas(r, v["tool"])]
        return (any(v["valor"] in x for x in vals), f"{v['campo']} recibido: {vals}")
    if t == "respuesta_contiene_numero":
        plano = re.sub(r"[.,\s$]", "", texto)
        return (v["numero"] in plano, f"número {v['numero']} {'presente' if v['numero'] in plano else 'AUSENTE'}")
    if t == "respuesta_contiene_fecha":
        ok = any(a.lower() in texto.lower() for a in v["alternativas"])
        return (ok, f"fecha {v['alternativas'][0]} {'presente' if ok else 'AUSENTE'}")
    if t == "cita_articulos":
        faltan = [a for a in v["articulos"] if not re.search(rf"\b(?:art[íi]culos?|art)\.?\s*(?:[\d, y]*\b)?{a}\b", texto, re.I)]
        return (not faltan, "cita " + ", ".join(f"Art. {a}" for a in v["articulos"]) + (f" (faltan: {faltan})" if faltan else ""))
    if t == "web_solo_dominios_permitidos":
        from urllib.parse import urlparse
        urls = [x["url"] for x in r.fuentes_web]
        malas = [u for u in urls if not any((urlparse(u).hostname or "").endswith(d) for d in SITIOS_PERMITIDOS)]
        return (not malas, f"{len(urls)} URL web, fuera de la lista: {malas}")
    if t == "web_marcada":
        ok = bool(r.fuentes_web) and "[web:" in texto.lower()
        return (ok, "origen [web: URL] " + ("marcado" if ok else "NO marcado o sin resultados web"))
    if t == "respuesta_corta":
        return (len(texto) <= v["max_chars"], f"{len(texto)} caracteres (máx. {v['max_chars']})")
    if t == "no_volcado_corpus":
        nt = _norm(texto)
        volcado = any(nt[i:i + 250] in corpus for i in range(0, max(1, len(nt) - 250), 60)) if len(nt) >= 250 else False
        return (not volcado, "sin transcribir ≥250 caracteres seguidos del corpus" if not volcado else "TRANSCRIBE el corpus")
    if t == "consulta_web_sin_datos":
        enviadas = [c.resultado.get("consulta_enviada", "") for c in _llamadas(r, "buscar_en_web")]
        filtradas = [d for d in v["prohibidos"] if any(d in e for e in enviadas)]
        return (not filtradas, f"consultas enviadas al buscador: {enviadas or 'ninguna (no buscó en la web)'}")
    return (False, f"verificación desconocida: {t}")


def modo_casos(config: AppConfig, args) -> None:
    casos = R.cargar_json(CASOS, {}).get("casos", [])
    ruta = R.RUNS / "casos_resultados.json"
    cache: dict = R.cargar_json(ruta, {})
    agente = AgenteMultaClara(config, registro=None, registrar=False)
    from src.registro import RegistroUsoTools
    registro = RegistroUsoTools(R.RUNS / "casos_uso_tools.jsonl")
    corpus = _corpus(agente)
    for caso in casos:
        if str(caso["id"]) in cache and not args.rehacer:
            continue
        print(f"[caso {caso['id']}] {caso['tipo']}: {caso['pregunta'][:70]}")
        agente.reiniciar()
        r = con_reintento(lambda: agente.responder(caso["pregunta"]))
        registro.registrar(r, sesion="casos")
        checks = [verificar(v, r, corpus) for v in caso["verificaciones"]]
        cache[str(caso["id"])] = {
            "id": caso["id"], "tipo": caso["tipo"], "pregunta": caso["pregunta"], "esperado": caso["esperado"],
            "respuesta": r.texto,
            "herramientas": [{"nombre": c.nombre, "argumentos": c.argumentos, "resumen": c.resumen} for c in r.llamadas_tools],
            "llamadas_llm": r.llamadas_llm, "tokens_total": r.tokens_total, "segundos": round(r.segundos, 2),
            "verificaciones": [{"ok": ok, "detalle": d} for ok, d in checks],
            "cumple": all(ok for ok, _ in checks),
        }
        R.guardar_json(ruta, cache)
        print(f"      tools: {r.herramientas_usadas or '(ninguna)'} | LLM: {r.llamadas_llm} | cumple: {cache[str(caso['id'])]['cumple']}")
        for ok, d in checks:
            print(f"        [{'OK' if ok else 'FALLA'}] {d}")
        time.sleep(args.pausa)
    filas = ["| # | Tipo | Esperado | Tools usadas | Llam. LLM | ¿Cumple? |", "|---|---|---|---|---|---|"]
    for c in cache.values():
        tools = ", ".join(f"{h['nombre']}" for h in c["herramientas"]) or "ninguna"
        filas.append(f"| {c['id']} | {c['tipo']} | {c['esperado']} | {tools} | {c['llamadas_llm']} | {'Sí' if c['cumple'] else 'No'} |")
    cumplen = sum(c["cumple"] for c in cache.values())
    md = "\n".join(filas) + f"\n\nCasos que cumplen lo esperado: {cumplen}/{len(cache)}"
    (R.RUNS / "casos_resumen.md").write_text(md, encoding="utf-8")
    print("\n" + md)


# --------------------------------------------------------------------------
# Modo 3: costo (pipeline fijo vs agente)
# --------------------------------------------------------------------------
def modo_costo(config: AppConfig, args) -> None:
    dataset = R.cargar_json(R.DATASET, [])
    ruta = R.RUNS / "pipeline_costo.json"
    cache: dict = R.cargar_json(ruta, {})
    asistente = MultaClaraAssistant(config)
    for item in dataset:
        if str(item["id"]) in cache:
            continue
        print(f"[pipeline {item['id']:>2}/{len(dataset)}] {item['pregunta'][:60]}")
        asistente.reiniciar()
        t0 = time.perf_counter()
        R.con_reintento(lambda: asistente.consultar(item["pregunta"]))
        cache[str(item["id"])] = {"id": item["id"], "segundos": round(time.perf_counter() - t0, 3), **asistente.ultimo_uso}
        R.guardar_json(ruta, cache)
        time.sleep(args.pausa)
    ag = R.cargar_json(R.RUNS / f"{args.tag_agente}_respuestas.json", {})
    ids = [k for k in cache if k in ag]
    prom = lambda xs: sum(xs) / len(xs) if xs else 0.0
    pip = {m: prom([cache[k][m] for k in ids]) for m in ("llamadas_llm", "tokens_total", "segundos")}
    agt = {m: prom([ag[k][m] for k in ids]) for m in ("llamadas_llm", "tokens_total", "segundos")}
    sin_tools = sum(not ag[k]["herramientas"] for k in ids)
    md = "\n".join([
        f"## Costo por pregunta ({len(ids)} preguntas del dataset de evaluación)", "",
        "| Medida (promedio por pregunta) | Pipeline fijo (Avance 2) | Agente (Avance 3) |", "|---|---|---|",
        f"| Llamadas al LLM | {pip['llamadas_llm']:.2f} | {agt['llamadas_llm']:.2f} |",
        f"| Tokens (entrada + salida + razonamiento) | {pip['tokens_total']:,.0f} | {agt['tokens_total']:,.0f} |".replace(",", "."),
        f"| Tiempo de respuesta (s) | {pip['segundos']:.1f} | {agt['segundos']:.1f} |", "",
        f"Llamadas a tools por pregunta (agente): {prom([len(ag[k]['herramientas']) for k in ids]):.2f}. "
        f"Preguntas que el agente respondió sin tools: {sin_tools}/{len(ids)}.",
    ])
    (R.RUNS / "costo_resumen.md").write_text(md, encoding="utf-8")
    print("\n" + md)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Evalua el agente de MultaClara.")
    sub = ap.add_subparsers(dest="modo", required=True)
    r = sub.add_parser("ragas")
    r.add_argument("--tag", default="agente")
    r.add_argument("--comparar", default="cabecera")
    r.add_argument("--juez")
    r.add_argument("--rpm-juez", type=float, default=10)
    r.add_argument("--lote", type=int, default=4)
    r.add_argument("--limite", type=int)
    r.add_argument("--solo-generar", action="store_true")
    r.add_argument("--solo-resumen", action="store_true")
    r.add_argument("--pausa", type=float, default=3)
    c = sub.add_parser("casos")
    c.add_argument("--rehacer", action="store_true")
    c.add_argument("--pausa", type=float, default=3)
    k = sub.add_parser("costo")
    k.add_argument("--tag-agente", default="agente")
    k.add_argument("--pausa", type=float, default=3)
    args = ap.parse_args()
    try:
        config = AppConfig.from_env()
    except ConfigError as e:
        sys.exit(f"[Error de configuración] {e}")
    {"ragas": modo_ragas, "casos": modo_casos, "costo": modo_costo}[args.modo](config, args)


if __name__ == "__main__":
    main()
