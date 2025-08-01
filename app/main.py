from fastapi import FastAPI
from routes import assistant

app = FastAPI()

app.include_router(assistant.router)