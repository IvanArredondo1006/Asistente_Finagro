import streamlit as st
import requests
import json
from PIL import Image
import re
import base64

st.set_page_config(page_title="Asistente de Proyectos Finagro", page_icon="🤖")
st.title("🤖 Asistente de Proyectos Finagro")

# Sidebar con Logo e Instrucciones
with st.sidebar:
    try:
        logo = Image.open("chatbot/logo Megag.png")
        st.image(logo, width=90)
    except Exception:
        st.write("(Logo no disponible)")

    st.header("Instrucciones de Consulta")

    st.subheader("Consultas Normativa FINAGRO")
    st.markdown(
        """
        - Pregunta de manera general como:
            - **¿Se puede financiar un tractor?**
            - **¿Qué requisitos existen para pequeños productores?**
            - **¿Qué líneas aplican para compra de maquinaria agrícola?**
        """
    )

    st.subheader("Consultas Datos MEGAG (SQL)")
    st.markdown(
        """
        - Utiliza datos precisos como NIT, montos , rubros etc
        - Para consultar por una empresa en concreto use el NIT sin DV
        - Las columnas más importantes son:
            - **NIT BENEFICIARIO**
            - **FECHA DESEMBOLSO**
            - **RUBRO**
            - **VALOR DESEMBOLSADO**
        - Ejemplos de preguntas:
            - **¿Cuántos desembolsos se han hecho bajo el NIT 800009632?**
            - **¿Bajo qué rubro se desembolsó a la empresa con NIT 800009632?**
        """
    )

    # Selector de modo y endpoints (ambos en el mismo backend FastAPI)
    st.subheader("Modo de consulta")
    mode = st.selectbox(
        "Selecciona el modo",
        ["SQL (MEGAG)", "Asistente Finagro"],
        index=0,
    )

    DEFAULT_ASSISTANT_API_URL = "http://127.0.0.1:8004/asistente-finagro"
    DEFAULT_SQL_API_URL = "http://127.0.0.1:8004/asistente-sql"

    # Endpoints fijos (no visibles en el front)
    assistant_api_url = DEFAULT_ASSISTANT_API_URL
    sql_api_url = DEFAULT_SQL_API_URL

# Estado de sesión
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "ultimo_resultado_sql" not in st.session_state:
    st.session_state.ultimo_resultado_sql = None

# Mostrar historial en pantalla
for mensaje in st.session_state.chat_history:
    with st.chat_message(mensaje["role"]):
        st.markdown(mensaje["content"])

# Entrada del usuario
if prompt := st.chat_input("Haz tu pregunta (normativa o SQL)"):
    st.chat_message("user").markdown(prompt)
    st.session_state.chat_history.append({"role": "user", "content": prompt})

    with st.spinner("Consultando..."):
        excel_bytes = None
        excel_filename = None
        excel_mime = None
        try:
            historial_limitado = st.session_state.chat_history[-10:]
            ultimo_sql = st.session_state.ultimo_resultado_sql or None

            headers = {"Content-Type": "application/json"}
            payload = {
                "pregunta": prompt,
                "historial": historial_limitado,
                "ultimo_resultado_sql": ultimo_sql,
            }

            # Llamada al endpoint según el modo
            if mode == "SQL (MEGAG)":
                resp = requests.post(sql_api_url, headers=headers, data=json.dumps(payload))
            else:
                resp = requests.post(assistant_api_url, headers=headers, data=json.dumps(payload))

            # --- NUEVO: detectar si la respuesta es un Excel ---
            content_type = resp.headers.get("Content-Type", "")
            if "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in content_type:
                # Intentar extraer el nombre del archivo del header Content-Disposition
                disp = resp.headers.get("Content-Disposition", "")
                filename = "resultado.xlsx"
                m = re.search(r'filename="?([^"]+)"?', disp)
                if m:
                    filename = m.group(1)

                # Mensaje del asistente + botón de descarga dentro del chat
                with st.chat_message("assistant"):
                    st.markdown(f"Tu archivo **{filename}** está listo para descargar.")
                    st.download_button(
                        "⬇️ Descargar Excel",
                        data=resp.content,
                        file_name=filename,
                        mime=content_type,
                        key=f"dl_{filename}_{len(st.session_state.chat_history)}"
                    )

                # Opcional: agrega una marca mínima al historial (texto)
                st.session_state.chat_history.append(
                    {"role": "assistant", "content": f"[Excel generado: {filename}]"}
                )

                # No procesar JSON si ya entregamos Excel
                st.stop()

            # --- Respuesta JSON como antes ---
            data = resp.json()

            excel_info = data.get("excel")
            if excel_info:
                base64_content = excel_info.get("base64") or excel_info.get("content")
                if base64_content:
                    try:
                        excel_bytes = base64.b64decode(base64_content)
                    except Exception:  # noqa: BLE001
                        excel_bytes = None
                excel_filename = excel_info.get("filename", "resultados.xlsx")
                excel_mime = excel_info.get("mime", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            if "respuesta" in data:
                respuesta = data["respuesta"]
            else:
                respuesta = f"Error: {data.get('error', 'No se pudo procesar la pregunta.')}"

            # No mostrar la consulta SQL en el front
            if "resultados" in data:
                st.session_state.ultimo_resultado_sql = data["resultados"]
            else:
                st.session_state.ultimo_resultado_sql = None

        except Exception as e:
            respuesta = f"Error al conectar con el backend: {e}"

    # Mensaje normal del asistente (texto)
    history_content = respuesta or ""
    with st.chat_message("assistant"):
        st.markdown(respuesta)
        if excel_bytes:
            label = "Descargar Excel" if (excel_filename or "").lower().endswith(".xlsx") else "Descargar archivo"
            st.download_button(
                label,
                data=excel_bytes,
                file_name=excel_filename or "resultados.xlsx",
                mime=excel_mime or "application/octet-stream",
                key=f"excel_{len(st.session_state.chat_history)}"
            )
            history_content = f"{history_content}\n\n[Archivo generado: {excel_filename or 'resultados.xlsx'}]"
    st.session_state.chat_history.append({"role": "assistant", "content": history_content.strip()})

# Botón para resetear la memoria
if st.button("Reiniciar Conversación"):
    st.session_state.chat_history = []
    st.session_state.ultimo_resultado_sql = None
    st.rerun()
