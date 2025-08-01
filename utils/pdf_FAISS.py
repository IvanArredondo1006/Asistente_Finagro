import os
from dotenv import load_dotenv
from langchain.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

# Cargar variables de entorno
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Validar la clave
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY no encontrada. Asegúrate de tener un archivo .env con la clave.")

# Cargar el PDF y dividirlo en chunks
loader = PyPDFLoader("data/25.24 (29-07-2025).pdf")
pages = loader.load()

splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
docs = splitter.split_documents(pages)

# Crear el índice con la clave explícita
embedding = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
vectorstore = FAISS.from_documents(docs, embedding)
vectorstore.save_local("data/faiss_index")
