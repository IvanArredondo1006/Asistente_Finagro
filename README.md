# Asistente Finagro

Este repositorio contiene distintos asistentes basados en GPT. El archivo `Asistente.py` ofrece un servicio que analiza documentos PDF mediante GPT-4o y `chatbot.py` muestra una interfaz sencilla con Streamlit.

## Nuevo agente basado en PostgreSQL

El script `db_agent.py` implementa un nuevo servicio en FastAPI que permite responder preguntas basándose en la información almacenada en una base de datos PostgreSQL.

### Uso

1. Defina las variables de entorno necesarias para conectarse a la base de datos y a la API de OpenAI:

```bash
export OPENAI_API_KEY=<su_clave>
export DB_HOST=<host>
export DB_PORT=<puerto>
export DB_NAME=<base>
export DB_USER=<usuario>
export DB_PASSWORD=<contraseña>
```

2. Ejecute el servicio:

```bash
python db_agent.py
```

El servidor escuchará en `http://localhost:8001/preguntar-db`. En la consulta se envía un parámetro `pregunta` con la duda en lenguaje natural.

El agente genera la consulta SQL correspondiente con GPT, ejecuta la consulta en la base de datos y luego crea una respuesta basada en los datos obtenidos.
### Instalación

Instale las dependencias principales:

```bash
pip install fastapi uvicorn[standard] psycopg2-binary openai python-dotenv
```

Este servicio es útil para responder consultas analíticas o estadísticas de los datos almacenados, generando la consulta SQL y devolviendo una respuesta en lenguaje natural.
