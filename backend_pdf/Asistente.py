from fastapi import FastAPI, Query
from typing import List, Optional
import os
import fitz  # PyMuPDF
from openai import OpenAI
import difflib
from pdf2image import convert_from_path
import base64
import tempfile
from unidecode import unidecode
import re
from dotenv import load_dotenv

# Cargar variables de entorno desde un archivo .env
load_dotenv()

# Configuración inicial
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BASE_NAS_DIR = os.getenv("BASE_NAS_DIR", r"M:\\Bancos")  # Ruta base general de la NAS
os.environ["PATH"] += os.pathsep + os.getenv("POPPLER_PATH", r"C:\\Program Files (x86)\\poppler-24.08.0\\Library\\bin")

# Diccionario de rutas por banco
RUTAS_PROYECTOS = {
    "Banco Av Villas": [os.path.join(BASE_NAS_DIR, "Banco Av Villas", "Proyectos", "FINAGRO", "PLANIFICACIONES")],
    "Banco BBVA": [os.path.join(BASE_NAS_DIR, "Banco BBVA", "Proyectos")],
    "Banco de Bogota": [os.path.join(BASE_NAS_DIR, "Banco de Bogota", "Proyectos")],
    "Banco de Occidente": [os.path.join(BASE_NAS_DIR, "Banco de Occidente", "Proyectos")],
    "Banco Finandina": [os.path.join(BASE_NAS_DIR, "Banco Finandina", "Proyectos")],
    "Banco Itau": [os.path.join(BASE_NAS_DIR, "Banco Itau", "Proyectos")],
    "Banco Popular": [os.path.join(BASE_NAS_DIR, "Banco Popular", "Proyectos")],
    "Banco Santander": [os.path.join(BASE_NAS_DIR, "Banco Santander", "Proyectos")],
    "Bancolombia": [os.path.join(BASE_NAS_DIR, "Bancolombia", "Proyectos")],
    "Leasing Bancolombia": [os.path.join(BASE_NAS_DIR, "Leasing Bancolombia", "Proyectos")],
    "PYME Banco Bogota": [os.path.join(BASE_NAS_DIR, "PYME Banco Bogota", "Proyectos")],
    "Renthabilidad": [os.path.join(BASE_NAS_DIR, "Renthabilidad", "Proyectos")],
    "Scotiabank": [os.path.join(BASE_NAS_DIR, "Scotiabank", "Proyectos")],
}

# Alias conocidos para los bancos
ALIAS_BANCOS = {
    "av villas": "Banco Av Villas",
    "bbva": "Banco BBVA",
    "bogota": "Banco de Bogota",
    "banco de bogota": "Banco de Bogota",
    "occidente": "Banco de Occidente",
    "finandina": "Banco Finandina",
    "itau": "Banco Itau",
    "popular": "Banco Popular",
    "santander": "Banco Santander",
    "bancolombia": "Bancolombia",
    "leasing bancolombia": "Leasing Bancolombia",
    "pyme bogota": "PYME Banco Bogota",
    "pyme banco bogota": "PYME Banco Bogota",
    "renthabilidad": "Renthabilidad",
    "scotiabank": "Scotiabank",
}

app = FastAPI()


def limpiar_texto(texto: str) -> str:
    texto = texto.lower()
    texto = unidecode(texto)
    texto = re.sub(r"(s\\.a\\.s?\\.?|ltda\\.?|s\\.a\\.?|\\.|,)", "", texto)
    texto = re.sub(r"[^a-z0-9 ]", "", texto)
    texto = re.sub(r"\\s+", " ", texto)
    return texto.strip()


def normalizar_banco(nombre_banco_usuario: str) -> Optional[str]:
    clave = nombre_banco_usuario.lower().strip()
    return ALIAS_BANCOS.get(clave, nombre_banco_usuario)


def buscar_pdf_en_banco(nombre_banco: str, nombre_proyecto: str) -> Optional[str]:
    nombre_banco = normalizar_banco(nombre_banco)
    rutas = RUTAS_PROYECTOS.get(nombre_banco)
    if not rutas:
        return None

    posibles_archivos = []
    for ruta in rutas:
        if not os.path.exists(ruta):
            continue
        for root, dirs, files in os.walk(ruta):
            for archivo in files:
                if archivo.lower().endswith(".pdf") and ("paf" in archivo.lower() or "flujo" in archivo.lower()):
                    posibles_archivos.append(os.path.join(root, archivo))

    if not posibles_archivos:
        return None

    nombre_proyecto_limpio = limpiar_texto(nombre_proyecto)
    archivos_limpios = [(limpiar_texto(os.path.basename(p)), p) for p in posibles_archivos]
    coincidencias = [(a, p) for a, p in archivos_limpios if nombre_proyecto_limpio in a]

    if not coincidencias:
        nombres_limpios = [a for a, _ in archivos_limpios]
        candidatos = difflib.get_close_matches(nombre_proyecto_limpio, nombres_limpios, n=3, cutoff=0.6)
        coincidencias = [(a, p) for a, p in archivos_limpios if a in candidatos]

    if not coincidencias:
        return None

    coincidencias.sort(key=lambda x: os.path.getmtime(x[1]), reverse=True)
    return coincidencias[0][1]


def buscar_pdf_en_todos_los_bancos(nombre_proyecto: str) -> Optional[tuple]:
    for banco in RUTAS_PROYECTOS:
        ruta = buscar_pdf_en_banco(banco, nombre_proyecto)
        if ruta:
            return banco, ruta
    return None


def convertir_pdf_a_imagenes(path: str, max_pages: int = 3, guardar_en_disc=True) -> List[str]:
    imagenes = convert_from_path(path, first_page=1, last_page=max_pages, dpi=300)
    imagenes_base64 = []

    for i, imagen in enumerate(imagenes):
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_page_{i}.png")
        imagen.save(temp_path, format="PNG")

        if guardar_en_disc:
            print(f"📸 Imagen guardada: {temp_path}")

        with open(temp_path, "rb") as image_file:
            imagen_b64 = base64.b64encode(image_file.read()).decode("utf-8")
            imagenes_base64.append(imagen_b64)

    return imagenes_base64


def responder_pregunta_con_vision(banco: str, proyecto: str, pregunta: str) -> str:
    if banco:
        path_pdf = buscar_pdf_en_banco(banco, proyecto)
    else:
        resultado = buscar_pdf_en_todos_los_bancos(proyecto)
        if not resultado:
            return f"No se encontró el proyecto '{proyecto}' en ningún banco."
        banco, path_pdf = resultado

    if not path_pdf:
        return f"No se encontró el proyecto '{proyecto}' en el banco '{banco}'."

    print(f"📄 Documento usado: {path_pdf}")

    imagenes_b64 = convertir_pdf_a_imagenes(path_pdf)

    client = OpenAI(api_key=OPENAI_API_KEY)
    mensajes = [
        {"role": "system", "content": "Eres un asistente experto en interpretar proyectos financieros escaneados. Analiza la imagen y responde con precisión basándote solo en lo que ves."},
        {"role": "user", "content": [
            {"type": "text", "text": pregunta},
            *[{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}} for img in imagenes_b64]
        ]}
    ]

    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=mensajes,
        max_tokens=600
    )

    return respuesta.choices[0].message.content.strip()


def extraer_banco_y_proyecto(pregunta: str) -> Optional[dict]:
    system_prompt = (
        "Eres un extractor de datos que identifica con exactitud el nombre del banco (si se menciona) y el nombre del proyecto. "
        "Tu tarea es devolver solo dos cosas: el nombre del banco (si se menciona en la pregunta) y el nombre del proyecto (que generalmente es un nombre propio o una empresa). "
        "Si no se menciona banco, deja ese campo vacío. "
        "No corrijas, no completes, no asumas. Devuelve solo un JSON así: {\"banco\": \"\", \"proyecto\": \"MILTON DIAZ OTERO\"}"
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    respuesta = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": pregunta}
        ],
        max_tokens=100
    )

    try:
        import json
        return json.loads(respuesta.choices[0].message.content.strip())
    except:
        return None


@app.get("/preguntar-libre-vision")
def preguntar_libre_vision(pregunta: str = Query(...)):
    extraido = extraer_banco_y_proyecto(pregunta)
    if not extraido:
        return {"error": "No se pudo identificar el banco o el proyecto en la pregunta."}

    banco = extraido.get("banco", "").strip()
    proyecto = extraido.get("proyecto", "").strip()

    if not proyecto:
        return {"error": "No se pudo identificar el nombre del proyecto."}

    respuesta = responder_pregunta_con_vision(banco, proyecto, pregunta)
    print("EXTRAIDO:", extraido)
    return {"banco": banco or "(detectado automáticamente)", "proyecto": proyecto, "respuesta": respuesta}
