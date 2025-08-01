from openai import OpenAI
from decimal import Decimal
import json
from langchain.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from app.db import get_db_connection
from app.config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)
embedding = OpenAIEmbeddings()
#vectorstore = FAISS.load_local("faiss_index", embedding, allow_dangerous_deserialization=True)

# Obtener el esquema automáticamente desde la base de datos
def obtener_esquema():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
            """)
            filas = cur.fetchall()

    esquema = {}
    for tabla, columna, tipo in filas:
        esquema.setdefault(tabla, []).append(f"{columna} ({tipo})")
    return "\n".join([f"{tabla}: {', '.join(campos)}" for tabla, campos in esquema.items()])

SCHEMA_DESCRIPCION = obtener_esquema()

def generar_sql(pregunta: str, contexto: str) -> str:
    prompt = (
        "Eres un generador de consultas SQL para PostgreSQL. "
        "Tu única salida debe ser una consulta que comience por SELECT, sin ningún texto adicional. "
        "Este es el esquema de la base de datos:\n"
        f"{SCHEMA_DESCRIPCION}\n\n"
        f"Este es el contexto del manual de FINAGRO:\n{contexto}"
    )
    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": pregunta}
        ],
        max_tokens=400,
        temperature=0
    )
    contenido = respuesta.choices[0].message.content.strip()
    if "```" in contenido:
        contenido = contenido.split("```")[-1].strip()
    if not contenido.lower().startswith("select"):
        raise ValueError(f"La respuesta no es una consulta válida: {contenido}")
    return contenido.rstrip(";")

def ejecutar_sql(sql: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            filas = cur.fetchall()
            columnas = [desc[0] for desc in cur.description]
    return [
        {col: float(val) if isinstance(val, Decimal) else val for col, val in zip(columnas, fila)}
        for fila in filas
    ]

def generar_respuesta_sql(pregunta: str, datos: list) -> str:
    if not datos:
        return "No se encontraron resultados."
    mensajes = [
        {"role": "system", "content": "Responde como analista de datos, solo con base en los datos."},
        {"role": "user", "content": f"Pregunta: {pregunta}\n\nDatos:\n{json.dumps(datos, ensure_ascii=False)}"}
    ]
    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=mensajes,
        max_tokens=600
    )
    return respuesta.choices[0].message.content.strip()
