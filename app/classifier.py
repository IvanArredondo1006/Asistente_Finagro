from openai import OpenAI
from app.config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

def clasificar_pregunta(pregunta: str) -> str:
    system_prompt = (
        "Eres un clasificador de preguntas para el Asistente FINAGRO.\n"
        "Responde únicamente con 'sql' o 'manual'.\n\n"
        "Ejemplos:\n"
        "- ¿Cuánto se desembolsó por el Banco Agrario en 2023? → sql\n"
        "- ¿Puedo financiar maquinaria con FINAGRO? → manual"
    )

    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": pregunta}
        ],
        temperature=0,
        max_tokens=5
    )

    return completion.choices[0].message.content.strip().lower()