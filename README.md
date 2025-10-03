# Asistente Finagro

Este repositorio contiene distintos asistentes basados en GPT. El archivo `Asistente.py` ofrece un servicio que analiza documentos PDF mediante GPT-4o y `chatbot.py` muestra una interfaz sencilla con Streamlit.

## Nuevo agente basado en PostgreSQL

El script `db_agent.py` implementa un nuevo servicio en FastAPI que permite responder preguntas basÃ¡ndose en la informaciÃ³n almacenada en una base de datos PostgreSQL.

### Uso

1. Defina las variables de entorno necesarias para conectarse a la base de datos y a la API de OpenAI:

```bash
export OPENAI_API_KEY=<su_clave>
export DB_HOST=<host>
export DB_PORT=<puerto>
export DB_NAME=<base>
export DB_USER=<usuario>
export DB_PASSWORD=<contraseÃ±a>
```

2. Ejecute el servicio:

```bash
python db_agent.py
```

El servidor escucharÃ¡ en `http://localhost:8001/preguntar-db`. En la consulta se envÃ­a un parÃ¡metro `pregunta` con la duda en lenguaje natural.

El agente genera la consulta SQL correspondiente con GPT, ejecuta la consulta en la base de datos y luego crea una respuesta basada en los datos obtenidos.
### InstalaciÃ³n

Instale las dependencias principales:

```bash
pip install fastapi uvicorn[standard] psycopg2-binary openai python-dotenv
```

Este servicio es Ãºtil para responder consultas analÃ­ticas o estadÃ­sticas de los datos almacenados, generando la consulta SQL y devolviendo una respuesta en lenguaje natural.

## Indice semantico (RAG)

El asistente ahora aprovecha un indice vectorial con hechos curados para enriquecer las respuestas abiertas.

### Dependencias adicionales

```bash
pip install chromadb
```

### Variables de entorno relevantes

- `RAG_INDEX_PATH`: ruta donde se persiste el indice (por defecto `rag_index`).
- `RAG_COLLECTION_NAME`: nombre de la coleccion en Chroma (por defecto `finagro_facts`).
- `OPENAI_EMBEDDING_MODEL`: modelo de embeddings de OpenAI (por defecto `text-embedding-3-large`).
- `RAG_TOP_K`: numero maximo de hechos recuperados por pregunta (por defecto `5`).

### Generar o refrescar el indice

```bash
python jobs/generate_rag_index.py
```

El script extrae KPIs mensuales, top beneficiarios, top rubros/departamentos, ejemplos recientes y definiciones de columnas desde la tabla `beneficiarios`. Luego embebe los textos y los guarda en Chroma para que el asistente manual pueda consultarlos.

Para automatizarlo, programe el comando anterior como tarea programada o cron segun su sistema operativo.

## Consultas en lenguaje natural

- El backend limpia formatos numericos comunes (por ejemplo `99,999,999,999` -> `99999999999`) antes de generar SQL.
- Cuando una consulta SQL no devuelve filas, el asistente combina la respuesta con los hechos del indice RAG para ofrecer contexto util.
- Las respuestas estan redactadas para personas sin conocimientos tecnicos, mencionando cifras clave y definiendo conceptos cuando es necesario.

## Asistente integrado

El endpoint `/asistente-finagro` ahora combina la ejecución de consultas SQL y el razonamiento normativo en una sola respuesta. El flujo:

1. Normaliza la pregunta, detecta posibles beneficiarios y aplica una regla para que los filtros sobre `"BENEFICIARIO"` usen `ILIKE '%texto%'`.
2. Ejecuta hasta tres reintentos de generación/ejecución SQL, registrando los intentos y devolviendo coincidencias aproximadas de nombres cuando es necesario.
3. Con los resultados tabulares y los hechos del índice RAG construye un prompt y genera la respuesta final con el asistente de FINAGRO.

El endpoint `/asistente-sql` reutiliza la misma orquestación, por lo que siempre obtendrás la respuesta final, los datos estructurados, los intentos SQL y las sugerencias de beneficiarios en un único payload.
- Durante la generación de SQL, todas las constantes se envían en mayúsculas para coincidir con la base y se aplica la regla explícita en el contexto para que los filtros usen `ILIKE` con comodines.
