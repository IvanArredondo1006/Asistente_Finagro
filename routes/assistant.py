from fastapi import APIRouter
from app.classifier import clasificar_pregunta
from app.sql_agent import generar_sql, ejecutar_sql, generar_respuesta_sql
from app.assistant_agent import consultar_assistant
from models.payload import PreguntaPayload
import json

router = APIRouter()

@router.post("/asistente-finagro")
async def asistente_finagro(payload: PreguntaPayload):
    pregunta = payload.pregunta
    historial = payload.historial or []
    ultimo_sql = payload.ultimo_resultado_sql

    contexto_conversacional = ""
    for msg in historial[-10:]:
        contexto_conversacional += f"{msg['role']}: {msg['content']}\n"

    tipo = clasificar_pregunta(pregunta)

    if tipo == "sql":
        sql = generar_sql(pregunta, contexto="")
        if not sql.lower().startswith("select"):
            return {"error": f"Consulta no válida generada: {sql}"}

        datos = ejecutar_sql(sql)
        respuesta = generar_respuesta_sql(pregunta, datos)

        return {"respuesta": respuesta, "sql": sql, "resultados": datos}

    contexto_sql = f"Resultado anterior:\n{json.dumps(ultimo_sql, ensure_ascii=False)}\n" if ultimo_sql else ""
    prompt_con_historial = f"{contexto_conversacional}\n{contexto_sql}\nUsuario: {pregunta}"

    respuesta_manual = consultar_assistant(prompt_con_historial)

    return {"respuesta": respuesta_manual}
