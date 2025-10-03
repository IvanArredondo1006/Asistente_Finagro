from fastapi import APIRouter
from typing import Any, Dict, List, Optional

from app.classifier import clasificar_pregunta
from app.sql_agent import generar_sql, ejecutar_sql, generar_respuesta_sql
from app.assistant_agent import consultar_assistant
from models.payload import PreguntaPayload
import json
import re

router = APIRouter()



STOPWORDS_BENEFICIARIO = {
    "de", "del", "la", "el", "los", "las", "para", "por", "con", "sin", "sobre",
    "en", "al", "lo", "una", "uno", "unas", "unos", "que", "cual", "cuales",
    "quien", "quienes", "beneficiario", "empresa", "nit", "existe", "exsito",
    "datos", "donde", "cuanto", "cuales", "caul", "cliente", "opera",
}


def _extraer_candidatos_beneficiario(texto: str) -> List[str]:
    if not texto:
        return []
    tokens = re.findall(r"[\wÁÉÍÓÚÜÑáéíóúüñ]+", texto)
    candidatos: List[str] = []
    vistos: set[str] = set()
    for token in tokens:
        normalizado = token.lower()
        if len(normalizado) < 3:
            continue
        if normalizado in STOPWORDS_BENEFICIARIO:
            continue
        if normalizado.isdigit():
            continue
        if normalizado not in vistos:
            vistos.add(normalizado)
            candidatos.append(token)
    return candidatos


def _buscar_beneficiarios_similares(candidatos: List[str], limite: int = 5) -> List[str]:
    if not candidatos:
        return []
    sugerencias: List[str] = []
    vistos: set[str] = set()
    for token in candidatos:
        if len(sugerencias) >= limite:
            break
        patron = token.strip().upper()
        if not patron:
            continue
        patron = patron.replace("'", "''")
        sql = (
            "SELECT DISTINCT \"BENEFICIARIO\" "
            "FROM beneficiarios "
            f"WHERE \"BENEFICIARIO\" ILIKE '%{patron}%' "
            "ORDER BY \"BENEFICIARIO\" "
            f"LIMIT {max(1, limite - len(sugerencias))}"
        )
        try:
            filas = ejecutar_sql(sql)
        except Exception as exc:  # noqa: BLE001
            print(f"[assistant_route] Falla sugiriendo beneficiarios para '{token}': {exc}")
            continue
        for fila in filas:
            nombre = fila.get("BENEFICIARIO") if isinstance(fila, dict) else None
            if not nombre:
                continue
            if nombre in vistos:
                continue
            vistos.add(nombre)
            sugerencias.append(nombre)
            if len(sugerencias) >= limite:
                break
    return sugerencias

def _resolver_sql_con_reintentos(pregunta: str, contexto: str) -> tuple[List[Dict[str, Any]], Optional[str], List[Dict[str, Any]], Optional[str], List[str]]:
    intentos: List[Dict[str, Any]] = []
    ultimo_sql: Optional[str] = None
    ultimo_error: Optional[str] = None
    resultados: List[Dict[str, Any]] = []

    candidatos = _extraer_candidatos_beneficiario(pregunta)
    sugerencias = _buscar_beneficiarios_similares(candidatos)
    contexto_enriquecido = contexto or ""
    if sugerencias:
        bloque_sugerencias = "Sugerencias de beneficiario:\n" + "\n".join(f"- {nombre}" for nombre in sugerencias)
        contexto_enriquecido = (contexto_enriquecido + "\n" + bloque_sugerencias).strip()
    else:
        contexto_enriquecido = contexto_enriquecido.strip()


    for intento in range(1, 4):
        intento_info: Dict[str, Any] = {"intento": intento}
        try:
            sql = generar_sql(pregunta, contexto=contexto_enriquecido)
            ultimo_sql = sql
            intento_info["sql"] = sql
        except Exception as exc:  # noqa: BLE001
            ultimo_error = str(exc)
            intento_info["error"] = f"generacion: {ultimo_error}"
            intentos.append(intento_info)
            continue

        if not sql.lower().startswith("select"):
            ultimo_error = f"Consulta no valida generada: {sql}"
            intento_info["error"] = ultimo_error
            intentos.append(intento_info)
            continue

        try:
            resultados = ejecutar_sql(sql)
            intento_info["filas"] = len(resultados)
            intentos.append(intento_info)
        except Exception as exc:  # noqa: BLE001
            ultimo_error = str(exc)
            intento_info["error"] = f"ejecucion: {ultimo_error}"
            intentos.append(intento_info)
            continue

        if resultados:
            return resultados, ultimo_sql, intentos, None, sugerencias
        intento_info["error"] = "sin_resultados"

        ultimo_error = "Consulta sin resultados"

    return (resultados if resultados else []), ultimo_sql, intentos, ultimo_error, sugerencias

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
        datos, sql_generado, intentos_sql, error_sql, sugerencias_beneficiario = _resolver_sql_con_reintentos(pregunta, contexto_conversacional)
        respuesta, hechos = generar_respuesta_sql(pregunta, datos)

        payload_resp = {"respuesta": respuesta, "resultados": datos, "intentos_sql": intentos_sql, "sugerencias_beneficiario": sugerencias_beneficiario}
        if sql_generado:
            payload_resp["sql"] = sql_generado
        if hechos:
            payload_resp["hechos"] = hechos
        if error_sql:
            payload_resp["error_sql"] = error_sql

        return payload_resp

    contexto_sql = f"Resultado anterior:\n{json.dumps(ultimo_sql, ensure_ascii=False)}\n" if ultimo_sql else ""
    prompt_con_historial = f"{contexto_conversacional}\n{contexto_sql}\nUsuario: {pregunta}"

    sugerencias_beneficiario = _buscar_beneficiarios_similares(_extraer_candidatos_beneficiario(pregunta))
    respuesta_manual = consultar_assistant(prompt_con_historial)

    return {"respuesta": respuesta_manual, "sugerencias_beneficiario": sugerencias_beneficiario}



@router.post("/asistente-sql")
async def asistente_sql(payload: PreguntaPayload):
    pregunta = payload.pregunta
    historial = payload.historial or []

    contexto_conversacional = ''
    for msg in historial[-10:]:
        contexto_conversacional += f"{msg['role']}: {msg['content']}\n"

    datos, sql_generado, intentos_sql, error_sql, sugerencias_beneficiario = _resolver_sql_con_reintentos(pregunta, contexto_conversacional)
    respuesta, hechos = generar_respuesta_sql(pregunta, datos)

    payload_resp = {
        "respuesta": respuesta,
        "resultados": datos,
        "intentos_sql": intentos_sql,
        "sugerencias_beneficiario": sugerencias_beneficiario,
    }
    if sql_generado:
        payload_resp["sql"] = sql_generado
    if hechos:
        payload_resp["hechos"] = hechos
    if error_sql:
        payload_resp["error_sql"] = error_sql

    return payload_resp
