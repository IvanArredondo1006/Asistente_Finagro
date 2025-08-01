import time
from openai import OpenAI
from app.config import OPENAI_API_KEY, ASSISTANT_ID

client = OpenAI(api_key=OPENAI_API_KEY)

def consultar_assistant(pregunta: str) -> str:
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role="user", content=pregunta)
    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=ASSISTANT_ID)

    while True:
        estado = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if estado.status == "completed":
            break
        elif estado.status in ["failed", "cancelled"]:
            raise Exception(f"Run falló con estado: {estado.status}")
        time.sleep(1)

    messages = client.beta.threads.messages.list(thread_id=thread.id)
    for m in reversed(messages.data):
        if m.role == "assistant":
            return m.content[0].text.value

    return "No se obtuvo respuesta del asistente."