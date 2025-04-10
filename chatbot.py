import streamlit as st
import requests

API_URL = "http://localhost:8000/preguntar-libre-vision"

st.set_page_config(page_title="Asistente de Proyectos con Visión", page_icon="🧠")
st.title("🧠 Asistente de Proyectos Finagro (visión PDF)")

# Inicializar historial de conversación
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# Mostrar historial estilo chat
for mensaje in st.session_state.chat_history:
    with st.chat_message(mensaje["role"]):
        st.markdown(mensaje["content"])

# Entrada del usuario
if prompt := st.chat_input("Haz tu pregunta sobre un proyecto (incluye banco y nombre del proyecto)"):
    # Mostrar pregunta en el historial
    st.chat_message("user").markdown(prompt)
    st.session_state.chat_history.append({"role": "user", "content": prompt})

    # Enviar la pregunta al backend de visión
    with st.spinner("Analizando el proyecto con visión..."):
        try:
            response = requests.get(API_URL, params={"pregunta": prompt})
            data = response.json()

            if "respuesta" in data:
                respuesta = data["respuesta"]
            else:
                respuesta = f"⚠️ Error: {data.get('error', 'No se pudo procesar la pregunta.')}"
        except Exception as e:
            respuesta = f"❌ Error al conectar con el backend: {e}"

    # Mostrar respuesta en el historial
    st.chat_message("assistant").markdown(respuesta)
    st.session_state.chat_history.append({"role": "assistant", "content": respuesta})
