import streamlit as st
import requests
import json
from PIL import Image

st.set_page_config(page_title="Asistente de Proyectos Finagro", page_icon="🤖")
st.title("🤖 Asistente de Proyectos Finagro")

# Sidebar con Logo, modo y endpoints
with st.sidebar:
    try:
        logo = Image.open("chatbot/logo Megag.png")
        st.image(logo, width=90)
    except Exception:
        st.write("(Logo no disponible)")

    st.header("ℹ️ Instrucciones de Consulta")

    st.subheader("📘 Normativa FINAGRO")
    st.markdown(
        """
        - Pregunta de manera general como:
          - **¿Se puede financiar un tractor?**
          - **¿Qué requisitos existen para pequeños productores?**
          - **¿Qué líneas aplican para compra de maquinaria agrícola?**
        """
    )

    st.subheader("🗄️ Datos MEGAG (SQL)")
    st.markdown(
        """
        - Utiliza datos precisos como NIT, montos, rubros, etc.
        - Para consultar por una empresa en concreto usa el NIT sin DV.
        - Columnas clave:
          - **NIT BENEFICIARIO**
          - **FECHA DESEMBOLSO**
          - **RUBRO**
          - **VALOR DESEMBOLSADO**
        - Ejemplos:
          - **¿Cuántos desembolsos se han hecho bajo el NIT 800009632?**
          - **¿Bajo qué rubro se desembolsó a la empresa con NIT 800009632?**
        """
    )

    st.subheader("⚙️ Modo de consulta")
    mode = st.selectbox(
        "Selecciona el modo",
        ["SQL (MEGAG)", "Visión PDF"],
        index=0,
    )

    # Endpoints (ajústalos si cambiaste puertos)
    DEFAULT_SQL_API_URL = "http://127.0.0.1:8000/asistente-sql"
    DEFAULT_VISION_API_URL = "http://127.0.0.1:8000/asistente-finagro"

    sql_api_url = st.text_input("Endpoint SQL", value=DEFAULT_SQL_API_URL)
    vision_api_url = st.text_input("Endpoint Visión PDF", value=DEFAULT_VISION_API_URL)

# Estado de sesión
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "ultimo_resultado_sql" not in st.session_state:
    st.session_state.ultimo_resultado_sql = None

# Mostrar historial
for mensaje in st.session_state.chat_history:
    with st.chat_message(mensaje["role"]):
        st.markdown(mensaje["content"])

# Entrada
if prompt := st.chat_input("Haz tu pregunta: normativa, SQL o PDF"):
    st.chat_message("user").markdown(prompt)
    st.session_state.chat_history.append({"role": "user", "content": prompt})

    with st.spinner("Consultando..."):
        try:
            historial_limitado = st.session_state.chat_history[-10:]
            ultimo_sql = st.session_state.ultimo_resultado_sql or None

            if mode == "SQL (MEGAG)":
                headers = {"Content-Type": "application/json"}
                payload = {
                    "pregunta": prompt,
                    "historial": historial_limitado,
                    "ultimo_resultado_sql": ultimo_sql,
                }
                resp = requests.post(sql_api_url, headers=headers, data=json.dumps(payload))
                data = resp.json()

                if "respuesta" in data:
                    respuesta = data["respuesta"]
                else:
                    respuesta = f"❗ Error: {data.get('error', 'No se pudo procesar la pregunta.')}"

                # Mostrar SQL si viene en la respuesta
                if "sql" in data and "resultados" in data:
                    st.session_state.ultimo_resultado_sql = data["resultados"]
                    st.code(data["sql"], language="sql")
                else:
                    st.session_state.ultimo_resultado_sql = None

            else:  # Asistente Finagro (manual o clasificado)
                resp = requests.post(vision_api_url, headers=headers, data=json.dumps(payload))
                data = resp.json()

                if "respuesta" in data:
                    respuesta = data["respuesta"]
                else:
                    respuesta = f"❗ Error: {data.get('error', 'No se pudo procesar la pregunta.')}"

                # No conservar resultados SQL en modo asistente
                st.session_state.ultimo_resultado_sql = None

        except Exception as e:
            respuesta = f"🚫 Error al conectar con el backend: {e}"

    st.chat_message("assistant").markdown(respuesta)
    st.session_state.chat_history.append({"role": "assistant", "content": respuesta})

# Reset conversación
if st.button("🔁 Reiniciar Conversación"):
    st.session_state.chat_history = []
    st.session_state.ultimo_resultado_sql = None
    st.rerun()
