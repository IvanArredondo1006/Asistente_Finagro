from fastapi import APIRouter
from typing import Any, Dict, List, Optional, Tuple
import json
import re
import logging
import base64
import io
import csv

from app.classifier import clasificar_pregunta
from app.sql_agent import generar_sql, ejecutar_sql, generar_respuesta_sql
from app.assistant_agent import consultar_assistant
from models.payload import PreguntaPayload

router = APIRouter()

# Logger básico para evitar NameError
LOGGER = logging.getLogger("asistente_finagro")
if not LOGGER.handlers:
    logging.basicConfig(level=logging.INFO)

STOPWORDS_BENEFICIARIO = {
    "de", "del", "la", "el", "los", "las", "para", "por", "con", "sin", "sobre",
    "en", "al", "lo", "una", "uno", "unas", "unos", "que", "cual", "cuales",
    "quien", "quienes", "beneficiario", "empresa", "nit", "existe", "exsito",
    "datos", "donde", "cuanto", "caul", "cliente", "opera",
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
            'SELECT DISTINCT "BENEFICIARIO" '
            'FROM beneficiarios '
            f'WHERE "BENEFICIARIO" ILIKE \'%{patron}%\' '
            'ORDER BY "BENEFICIARIO" '
            f'LIMIT {max(1, limite - len(sugerencias))}'
        )
        try:
            filas = ejecutar_sql(sql)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[assistant_route] Falla sugiriendo beneficiarios para '%s': %s", token, exc)
            continue
        for fila in filas or []:
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


def _ajustar_sql_con_sugerencias(sql: str, sugerencias: List[str]) -> Tuple[str, bool]:
    if not sql or not sugerencias:
        return sql, False
    candidatos = [nombre for nombre in sugerencias if nombre]
    if not candidatos:
        return sql, False
    sql_mayus = sql.upper()
    for nombre in candidatos:
        if nombre.upper() in sql_mayus:
            return sql, False

    canonico = candidatos[0]
    canonico_like = canonico.replace("'", "''")

    # Patrones corregidos (comillas bien balanceadas)
    patrones = [
        re.compile(r"(\"BENEFICIARIO\"\s+(?:ILIKE|LIKE)\s*)'%?([^']*?)%?'", re.IGNORECASE),
        re.compile(r"(UPPER\(\"BENEFICIARIO\"\)\s+(?:LIKE|ILIKE)\s*)'%?([^']*?)%?'", re.IGNORECASE),
        re.compile(r"(\"BENEFICIARIO\"\s*=\s*)'([^']*)'", re.IGNORECASE),
    ]

    for patron in patrones:
        def _reemplazo(match: re.Match) -> str:
            prefijo = match.group(1)
            if "UPPER" in prefijo.upper():
                valor = canonico.upper().replace("'", "''")
            else:
                valor = canonico_like
            if "LIKE" in prefijo.upper():
                return f"{prefijo}'%{valor}%'"
            return f"{prefijo}'{valor}'"

        nuevo_sql, conteo = patron.subn(_reemplazo, sql, count=1)
        if conteo:
            return nuevo_sql, True
    return sql, False


def _mensaje_sin_resultados_con_sugerencias(sugerencias: List[str]) -> str:
    if not sugerencias:
        return ""
    coincidencias = ", ".join(sugerencias[:5])
    return (
        "No se recuperaron filas para los filtros aplicados. "
        f"Coincidencias de beneficiario detectadas: {coincidencias}. "
        "Intenta consultar usando uno de esos nombres exactos o solicita el Excel completo para validar la información."
    )


def _solicita_excel(pregunta: str, historial: List[Dict[str, Any]]) -> bool:
    """
    Heurística simple: si el usuario pide 'excel', 'xlsx', 'archivo', 'descargar', generamos Excel.
    """
    texto = (pregunta or "").lower()
    if any(pal in texto for pal in ["excel", "xlsx", "archivo", "descargar", "hoja de cálculo", "generar informe"]):
        return True
    # revisar también el último mensaje del usuario en historial
    for msg in reversed(historial[-5:]):
        if (msg.get("role") == "user") and isinstance(msg.get("content"), str):
            t = msg["content"].lower()
            if any(pal in t for pal in ["excel", "xlsx", "archivo", "descargar", "hoja de cálculo", "generar informe"]):
                return True
    return False


def _generar_excel_desde_resultados(filas: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Genera un CSV en base64 (compatible con Excel) para evitar dependencias.
    Retorna payload con nombre y contenido.
    """
    if not filas:
        return None
    # columnas en orden determinista
    columnas: List[str] = sorted({k for fila in filas for k in fila.keys()})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columnas, extrasaction="ignore")
    writer.writeheader()
    for fila in filas:
        writer.writerow({k: fila.get(k, "") for k in columnas})
    contenido_csv = buffer.getvalue().encode("utf-8-sig")  # BOM para Excel
    contenido_b64 = base64.b64encode(contenido_csv).decode("ascii")
    return {
        "filename": "resultados.csv",
        "mime": "text/csv",
        "base64": contenido_b64,
        "rows": len(filas),
        "columns": columnas,
    }


def _resolver_sql_con_reintentos(
    pregunta: str,
    contexto: str
) -> Tuple[List[Dict[str, Any]], Optional[str], List[Dict[str, Any]], Optional[str], List[str]]:
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

    pregunta_para_sql = pregunta
    if sugerencias:
        nombres_sugeridos = ", ".join(sugerencias[:5])
        instruccion = (
            "Si aplicas filtros por beneficiario, usa exactamente alguno de estos nombres: "
            f"{nombres_sugeridos}."
        )
        pregunta_para_sql = f"{pregunta}. {instruccion}"

    for intento in range(1, 4):
        intento_info: Dict[str, Any] = {"intento": intento}
        try:
            sql_generado = generar_sql(
                pregunta_para_sql,
                contexto=contexto_enriquecido,
                sugerencias_beneficiario=sugerencias,
            )
            intento_info["sql_generado"] = sql_generado
            sql_ejecutar = sql_generado
            if sugerencias:
                sql_ejecutar, ajustado = _ajustar_sql_con_sugerencias(sql_ejecutar, sugerencias)
                if ajustado:
                    intento_info["sql_ajustado"] = sql_ejecutar
                    intento_info["ajuste_beneficiario"] = sugerencias[0]
            intento_info["sql"] = sql_ejecutar
            ultimo_sql = sql_ejecutar
        except Exception as exc:  # noqa: BLE001
            ultimo_error = str(exc)
            intento_info["error"] = f"generacion: {ultimo_error}"
            intentos.append(intento_info)
            continue

        if not sql_ejecutar.lower().startswith("select"):
            ultimo_error = f"Consulta no válida generada: {sql_ejecutar}"
            intento_info["error"] = ultimo_error
            intentos.append(intento_info)
            continue

        try:
            resultados = ejecutar_sql(sql_ejecutar) or []
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
    solicitar_excel = _solicita_excel(pregunta, historial)
    if solicitar_excel:
        tipo = "sql"

    if tipo == "sql":
        datos, sql_generado, intentos_sql, error_sql, sugerencias_beneficiario = _resolver_sql_con_reintentos(
            pregunta, contexto_conversacional
        )

        excel_payload = _generar_excel_desde_resultados(datos) if solicitar_excel else None
        advertencia_sin_datos = _mensaje_sin_resultados_con_sugerencias(sugerencias_beneficiario) if not datos else ""
        if solicitar_excel:
            if datos:
                respuesta = "Genero un archivo Excel con los datos solicitados."
            else:
                respuesta = "La consulta no produjo datos, por lo que no se generó el archivo Excel."
            hechos: List[Dict[str, Any]] = []
            if advertencia_sin_datos:
                respuesta = f"{respuesta} {advertencia_sin_datos}".strip()
        else:
            respuesta, hechos = generar_respuesta_sql(pregunta, datos)
            if advertencia_sin_datos:
                respuesta = f"{respuesta}\n\n{advertencia_sin_datos}" if respuesta else advertencia_sin_datos

        payload_resp: Dict[str, Any] = {
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
        if excel_payload:
            payload_resp["excel"] = excel_payload

        LOGGER.info(json.dumps({
            "endpoint": "asistente_finagro",
            "tipo": "sql",
            "pregunta": pregunta,
            "filas": len(datos),
            "sql": sql_generado,
            "solicitar_excel": solicitar_excel,
            "excel_generado": bool(excel_payload),
            "error_sql": error_sql,
            "intentos_sql": intentos_sql,
        }, ensure_ascii=False))

        return payload_resp

    contexto_sql = f"Resultado anterior:\n{json.dumps(ultimo_sql, ensure_ascii=False)}\n" if ultimo_sql else ""
    prompt_con_historial = f"{contexto_conversacional}\n{contexto_sql}\nUsuario: {pregunta}"

    sugerencias_beneficiario = _buscar_beneficiarios_similares(_extraer_candidatos_beneficiario(pregunta))
    respuesta_manual = consultar_assistant(prompt_con_historial)

    payload_manual: Dict[str, Any] = {"respuesta": respuesta_manual, "sugerencias_beneficiario": sugerencias_beneficiario}
    LOGGER.info(json.dumps({
        "endpoint": "asistente_finagro",
        "tipo": "manual",
        "pregunta": pregunta,
        "solicitar_excel": solicitar_excel,
    }, ensure_ascii=False))
    return payload_manual


@router.post("/asistente-sql")
async def asistente_sql(payload: PreguntaPayload):
    pregunta = payload.pregunta
    historial = payload.historial or []
    solicitar_excel = _solicita_excel(pregunta, historial)

    contexto_conversacional = ""
    for msg in historial[-10:]:
        contexto_conversacional += f"{msg['role']}: {msg['content']}\n"

    datos, sql_generado, intentos_sql, error_sql, sugerencias_beneficiario = _resolver_sql_con_reintentos(
        pregunta, contexto_conversacional
    )

    excel_payload = _generar_excel_desde_resultados(datos) if solicitar_excel else None
    advertencia_sin_datos = _mensaje_sin_resultados_con_sugerencias(sugerencias_beneficiario) if not datos else ""
    if solicitar_excel:
        if datos:
            respuesta = "Genero un archivo Excel con los datos solicitados."
        else:
            respuesta = "La consulta no produjo datos, por lo que no se generó el archivo Excel."
        hechos: List[Dict[str, Any]] = []
        if advertencia_sin_datos:
            respuesta = f"{respuesta} {advertencia_sin_datos}".strip()
    else:
        respuesta, hechos = generar_respuesta_sql(pregunta, datos)
        if advertencia_sin_datos:
            respuesta = f"{respuesta}\n\n{advertencia_sin_datos}" if respuesta else advertencia_sin_datos

    payload_resp: Dict[str, Any] = {
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
    if excel_payload:
        payload_resp["excel"] = excel_payload

    # LOGGER.info(json.dumps({
    #     "endpoint": "asistente_sql",
    #     "tipo": "sql",
    #     "pregunta": pregunta,
    #     "filas": len(datos),
    #     "sql": sql_generado,
    #     "solicitar_excel": solicitar_excel,
    #     "excel_generado": bool(excel_payload),
    #     "error_sql": error_sql,
    #     "intentos_sql": intentos_sql,
    # }, ensure_ascii=False))

    return payload_resp
