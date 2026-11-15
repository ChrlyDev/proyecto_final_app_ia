"""Interfaz de chat de MultaClara (Streamlit) - Avance 3: agente con herramientas.

Ejecutar:  streamlit run app.py

- El chat usa el AGENTE: el modelo decide si consulta la base, calcula un valor/plazo o busca en la
  web. En cada respuesta se muestra QUE herramientas uso y con QUE argumentos (panel desplegable).
- Las fuentes se muestran SEPARADAS POR ORIGEN: fragmentos de la base (documento y seccion),
  resultados web (titulo y URL) y calculos (tabla oficial). Salen del registro de las tools, no de
  lo que el modelo "dice" haber citado.
- Si la informacion no esta ni en la base ni en los sitios permitidos, el agente responde con un
  mensaje fijo (RESPUESTA_SIN_INFORMACION) y la interfaz lo resalta.
- Historial real en `st.session_state`: las preguntas de seguimiento entienden los turnos previos.
- Secretos: GEMINI_API_KEY y TAVILY_API_KEY se leen de variables de entorno / `st.secrets`.
"""

import json
import os
import uuid

import streamlit as st

from src.agent import AgenteError, AgenteMultaClara, RespuestaAgente
from src.assistant import DISCLAIMER
from src.config import AppConfig, ConfigError
from src.prompts_agent import RESPUESTA_SIN_INFORMACION
from src.registro import RegistroUsoTools
from src.retriever import RetrievalError, Retriever
from src.tools import SITIOS_PERMITIDOS, CajaDeHerramientas

MAX_CARACTERES = 1500  # protege la cuota compartida de la API en un despliegue publico

EJEMPLOS = [
    "Hola, buenas tardes. ¿Cómo estás?",
    "¿Qué recursos se pueden presentar contra las decisiones del proceso de tránsito?",
    "Me pusieron un comparendo el 10 de abril de 2026. ¿Hasta qué fecha puedo pagar con descuento?",
    "¿Cuánto me toca pagar en pesos por no usar el cinturón de seguridad?",
    "¿Qué dispuso el Ministerio de Transporte en su resolución más reciente sobre patinetas eléctricas?",
]

ICONO_TOOL = {"busqueda": "📚", "tarea": "🧮", "web": "🌐"}
TIPO_TOOL = {"buscar_en_base": "busqueda", "calcular_valor_multa": "tarea",
             "calcular_plazo_descuento": "tarea", "buscar_en_web": "web"}

st.set_page_config(page_title="MultaClara", page_icon="🚦", layout="centered")


def _cargar_secrets() -> None:
    """Expone st.secrets (Streamlit Cloud) como variables de entorno."""
    try:
        for clave, valor in st.secrets.items():
            if isinstance(valor, (str, int, float, bool)):
                os.environ.setdefault(clave, str(valor))
    except Exception:  # no hay secrets.toml (ejecucion local con .env)
        pass


@st.cache_resource(show_spinner="Cargando la base normativa…")
def cargar_recursos() -> tuple[AppConfig, Retriever, RegistroUsoTools]:
    """Se abre UNA vez y se comparte entre sesiones (la base vectorial es solo lectura)."""
    config = AppConfig.from_env()
    return config, Retriever(config), RegistroUsoTools(config.agente_log)


def nuevo_agente() -> AgenteMultaClara:
    """Un agente (y una caja de tools) por sesion: la caja guarda estado de la pregunta en curso."""
    config, retriever, registro = cargar_recursos()
    return AgenteMultaClara(config, caja=CajaDeHerramientas(config, retriever=retriever), registro=registro)


# ---------------------------------------------------------------------------
# Presentacion de una respuesta
# ---------------------------------------------------------------------------
def mostrar_herramientas(r: RespuestaAgente) -> None:
    n = len(r.llamadas_tools)
    titulo = f"🛠️ Herramientas usadas ({n})" if n else "🛠️ Herramientas usadas (ninguna)"
    with st.expander(titulo):
        if not n:
            st.caption("El agente respondió sin consultar herramientas.")
        for i, c in enumerate(r.llamadas_tools, start=1):
            icono = ICONO_TOOL.get(TIPO_TOOL.get(c.nombre, ""), "🔧")
            st.markdown(f"**{i}. {icono} `{c.nombre}`**  ·  {c.segundos * 1000:.0f} ms")
            st.code(json.dumps(c.argumentos, ensure_ascii=False, indent=2), language="json")
            (st.warning if c.error else st.caption)(f"Resultado: {c.resumen}")
        st.caption(f"Llamadas al LLM: {r.llamadas_llm} · tokens: {r.tokens_total:,} · tiempo: {r.segundos:.1f} s".replace(",", "."))
        if r.limite_alcanzado:
            st.warning("Se alcanzó el límite de iteraciones: la respuesta se forzó sin más herramientas.")


def mostrar_fuentes(r: RespuestaAgente) -> None:
    """Fuentes SEPARADAS POR ORIGEN (los expanders no se anidan: van uno tras otro)."""
    if r.fuentes_base:
        with st.expander(f"📚 Fuentes de la base normativa ({len(r.fuentes_base)})"):
            st.caption("Fragmentos de la Ley 769 de 2002 que devolvió la herramienta, ordenados por similitud.")
            for i, f in enumerate(r.fuentes_base, start=1):
                st.markdown(f"**{i}. {f['documento']} — {f['seccion'].split(' | ')[0]}**  ·  similitud {f['similitud']:.2f}")
                ubicacion = " › ".join(x for x in f["seccion"].split(" | ")[1:] if x.strip())
                if ubicacion:
                    st.caption(ubicacion)
                st.markdown(f"> {f['texto'][:700].replace(chr(10), ' ')}{'…' if len(f['texto']) > 700 else ''}")
    calculos = [c for c in r.llamadas_tools if TIPO_TOOL.get(c.nombre) == "tarea" and not c.error]
    if calculos:
        with st.expander(f"🧮 Cálculos con la tabla oficial 2026 ({len(calculos)})"):
            for c in calculos:
                st.markdown(f"**{c.nombre}** — {c.resumen}")
                st.caption(c.resultado.get("fuente", ""))
    if r.fuentes_web:
        with st.expander(f"🌐 Fuentes web — sitios oficiales ({len(r.fuentes_web)})"):
            st.caption("Origen: web. Solo se consultan los sitios de la lista permitida.")
            for x in r.fuentes_web:
                st.markdown(f"**[{x['titulo'] or x['url']}]({x['url']})**  ·  `web`")
                st.caption(x["extracto"])


def mostrar_respuesta(r: RespuestaAgente) -> None:
    if RESPUESTA_SIN_INFORMACION[:40] in r.texto:
        st.info(r.texto, icon="🔎")  # no esta ni en la base ni en los sitios permitidos
    else:
        st.markdown(r.texto)
    mostrar_herramientas(r)
    mostrar_fuentes(r)
    if r.llamadas_tools:
        st.caption(DISCLAIMER)


def _turnos_previos() -> int:
    return sum(m["rol"] == "user" for m in st.session_state.mensajes)


def _memoria(max_turnos: int) -> int:
    return min(_turnos_previos(), max_turnos)


def mostrar_seguimiento(previos: int) -> None:
    if previos:
        st.caption(f"↩️ Seguimiento: esta respuesta tiene en cuenta {previos} mensaje"
                   f"{'s' if previos != 1 else ''} anterior{'es' if previos != 1 else ''} de la conversación.")


def _actualizar_memoria(ranura) -> None:
    n = _memoria(cargar_recursos()[0].max_turnos_historial)
    if n:
        ranura.caption(f"💬 Recuerdo {n} mensaje{'s' if n != 1 else ''} de esta conversación")


# ---------------------------------------------------------------------------
# Pagina
# ---------------------------------------------------------------------------
def main() -> None:
    _cargar_secrets()

    st.title("🚦 MultaClara")
    st.caption(
        "Agente que verifica comparendos de tránsito en Colombia: consulta la Ley 769 de 2002, "
        "calcula valores y plazos, y busca información oficial en la web cuando hace falta."
    )

    try:
        cargar_recursos()
    except ConfigError as e:
        st.error(f"Falta configuración: {e}")
        st.stop()
    except RetrievalError as e:
        st.error(f"No se pudo abrir la base normativa: {e}")
        st.stop()

    if "agente" not in st.session_state:
        st.session_state.agente = nuevo_agente()
        st.session_state.sesion = uuid.uuid4().hex[:8]
        st.session_state.mensajes = []

    with st.sidebar:
        st.header("Sobre MultaClara")
        st.write(
            "Cuéntame qué pasó con tu comparendo o hazme una pregunta de tránsito. "
            "Decido si consulto la base, hago un cálculo o busco en la web, y te muestro "
            "qué herramientas usé y de dónde sale cada dato."
        )
        if st.button("🗑️ Nueva conversación", use_container_width=True):
            st.session_state.agente.reiniciar()
            st.session_state.mensajes = []
            st.rerun()
        ranura_memoria = st.empty()
        st.divider()
        st.markdown("**Prueba con:**")
        for i, ejemplo in enumerate(EJEMPLOS):
            if st.button(ejemplo, key=f"ej{i}", use_container_width=True):
                st.session_state.pendiente = ejemplo
        st.divider()
        with st.expander("🌐 Sitios web permitidos"):
            st.caption("La búsqueda web solo consulta estos sitios oficiales (lista fija en el código):")
            for dominio, motivo in SITIOS_PERMITIDOS.items():
                st.markdown(f"- **{dominio}** — {motivo}")
        st.caption("Orientación informativa; no reemplaza la asesoría de un abogado.")

    chat()
    _actualizar_memoria(ranura_memoria)


def chat() -> None:
    if not st.session_state.mensajes:
        st.chat_message("assistant").markdown(
            "Hola 👋 Soy MultaClara. Cuéntame qué ocurrió con tu comparendo, pregúntame por una norma "
            "o pídeme calcular un valor o un plazo."
        )

    for m in st.session_state.mensajes:
        with st.chat_message(m["rol"]):
            if m["rol"] == "user":
                st.markdown(m["texto"])
            else:
                mostrar_respuesta(m["respuesta"])
                mostrar_seguimiento(m.get("previos", 0))

    prompt = st.chat_input("Escribe tu caso o tu pregunta…") or st.session_state.pop("pendiente", None)
    if not prompt:
        return

    if len(prompt) > MAX_CARACTERES:
        st.warning(f"Tu mensaje es muy largo ({len(prompt)} caracteres). Resúmelo a menos de {MAX_CARACTERES}.")
        return

    previos = _memoria(cargar_recursos()[0].max_turnos_historial)
    st.session_state.mensajes.append({"rol": "user", "texto": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Pensando y consultando herramientas…"):
                respuesta = st.session_state.agente.responder(prompt, sesion=st.session_state.sesion)
        except AgenteError as e:
            st.error(f"No pude responder en este momento: {e}")
            return
        mostrar_respuesta(respuesta)
        mostrar_seguimiento(previos)
    st.session_state.mensajes.append({"rol": "assistant", "respuesta": respuesta, "previos": previos})


main()
