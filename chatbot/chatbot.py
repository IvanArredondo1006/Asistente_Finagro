import streamlit as st
import requests
import json

st.set_page_config(page_title="Asistente de Proyectos Finagro", page_icon="🧠")
st.title("🧠 Asistente de Proyectos Finagro")

#API_URL = "http://localhost:8000/asistente-finagro"
API_URL = "https://day-nurse-lopez-criterion.trycloudflare.com/asistente-finagro"

#https://day-nurse-lopez-criterion.trycloudflare.com
# Inicializar variables en sesión si no existen
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "ultimo_resultado_sql" not in st.session_state:
    st.session_state.ultimo_resultado_sql = None

# Mostrar historial en pantalla
for mensaje in st.session_state.chat_history:
    with st.chat_message(mensaje["role"]):
        st.markdown(mensaje["content"])

# Entrada del usuario
if prompt := st.chat_input("Haz tu pregunta sobre normativa o desembolsos"):
    st.chat_message("user").markdown(prompt)
    st.session_state.chat_history.append({"role": "user", "content": prompt})

    with st.spinner("Consultando..."):
        try:
            historial_limitado = st.session_state.chat_history[-10:]

            # Asegurar que ultimo_resultado_sql siempre sea lista o None, nunca null
            ultimo_sql = st.session_state.ultimo_resultado_sql
            if not ultimo_sql:
                ultimo_sql = None

            headers = {"Content-Type": "application/json"}
            payload = {
                "pregunta": prompt,
                "historial": historial_limitado,
                "ultimo_resultado_sql": ultimo_sql
            }

            response = requests.post(API_URL, headers=headers, data=json.dumps(payload))
            data = response.json()

            if "respuesta" in data:
                respuesta = data["respuesta"]
            else:
                respuesta = f"⚠️ Error: {data.get('error', 'No se pudo procesar la pregunta.')}"

            # Si la respuesta trae resultados SQL, guardarlos para siguiente pregunta
            if "sql" in data and "resultados" in data:
                st.session_state.ultimo_resultado_sql = data["resultados"]
            else:
                st.session_state.ultimo_resultado_sql = None

        except Exception as e:
            respuesta = f"❌ Error al conectar con el backend: {e}"

    st.chat_message("assistant").markdown(respuesta)
    st.session_state.chat_history.append({"role": "assistant", "content": respuesta})

# Botón para resetear la memoria
if st.button("🔄 Reiniciar Conversación"):
    st.session_state.chat_history = []
    st.session_state.ultimo_resultado_sql = None
    st.rerun()
