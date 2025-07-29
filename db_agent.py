from fastapi import FastAPI, Query
import os
import json
import psycopg2
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
if not all([OPENAI_API_KEY, DB_NAME, DB_USER, DB_PASSWORD]):
    raise RuntimeError("Faltan variables de entorno para la base de datos o la API de OpenAI")


app = FastAPI()


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def obtener_esquema() -> str:
    """Devuelve una descripción simple del esquema de la base de datos."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
            filas = cur.fetchall()

    esquema = {}
    for tabla, columna in filas:
        esquema.setdefault(tabla, []).append(columna)
    partes = [f"{t}({', '.join(c)})" for t, c in esquema.items()]
    return "; ".join(partes)


SCHEMA_DESCRIPCION = obtener_esquema()


client = OpenAI(api_key=OPENAI_API_KEY)


def pregunta_a_sql(pregunta: str) -> str:
    """Genera una consulta SQL a partir de la pregunta."""
    system = (
        "Eres un generador de SQL especializado en analisis de datos y estadistica. "
        f"Utiliza la pregunta del usuario y el siguiente esquema: {SCHEMA_DESCRIPCION}. "
        "Devuelve unicamente la consulta SQL e incluye LIMIT 100 cuando sea necesario."
    )
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": pregunta}],
        max_tokens=150,
        temperature=0,
    )
    return res.choices[0].message.content.strip().strip(";")


def ejecutar_sql(sql: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            filas = cur.fetchall()
            columnas = [d[0] for d in cur.description]
    return [dict(zip(columnas, f)) for f in filas]


def generar_respuesta(pregunta: str, datos: list) -> str:
    """Genera la respuesta final utilizando los datos obtenidos."""
    mensajes = [
        {"role": "system", "content": "Responde a la pregunta usando solo los datos proporcionados."},
        {
            "role": "user",
            "content": f"Pregunta: {pregunta}\nDatos: {json.dumps(datos, ensure_ascii=False)}",
        },
    ]
    res = client.chat.completions.create(model="gpt-4o", messages=mensajes, max_tokens=300)
    return res.choices[0].message.content.strip()


@app.get("/preguntar-db")
def preguntar_db(pregunta: str = Query(...)):
    sql = pregunta_a_sql(pregunta)
    try:
        datos = ejecutar_sql(sql)
    except Exception as e:
        return {"error": f"Error al ejecutar la consulta: {e}", "sql": sql}
    respuesta = generar_respuesta(pregunta, datos)
    return {"sql": sql, "respuesta": respuesta}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
