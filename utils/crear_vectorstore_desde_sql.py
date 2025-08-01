import os
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings.openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
import psycopg2

load_dotenv()

# Conexión a base de datos
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )

# Cargar y formatear los datos
def cargar_datos_tabla():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM beneficiarios")
            columnas = [desc[0] for desc in cur.description]
            filas = cur.fetchall()

    documentos = []
    for fila in filas:
        texto = "\n".join(f"{col}: {val}" for col, val in zip(columnas, fila) if val is not None)
        documentos.append(texto)
    return documentos

# Dividir en chunks para evitar el error de tokens
def dividir_en_chunks(documentos, chunk_size=1000, chunk_overlap=200):
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = []
    for doc in documentos:
        chunks.extend(splitter.split_text(doc))
    return chunks

# Embeddings y FAISS
documentos = cargar_datos_tabla()
chunks = dividir_en_chunks(documentos)

embedding_model = OpenAIEmbeddings()
vectorstore = FAISS.from_texts(chunks, embedding_model)
vectorstore.save_local("faiss_index")

print("✅ Vectorstore generado y guardado correctamente.")
