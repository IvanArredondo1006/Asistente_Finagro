from fastapi import APIRouter
from typing import Any, Dict, List, Optional, Tuple
import json
import re
import logging
import base64
import time
from io import BytesIO

from app.sql_agent import generar_sql, ejecutar_sql, generar_respuesta_sql
from app.assistant_agent import consultar_assistant
from models.payload import PreguntaPayload

try:
    import pandas as pd  # type: ignore
except ImportError:
    pd = None  # type: ignore
try:
    from openpyxl import Workbook  # type: ignore
except ImportError:
    Workbook = None  # type: ignore



router = APIRouter()

# Logger básico para evitar NameError
# LOGGER = logging.getLogger("asistente_finagro")
# if not LOGGER.handlers:
#     logging.basicConfig(level=logging.INFO)

STOPWORDS_BENEFICIARIO = {
    "de", "del", "la", "el", "los", "las", "para", "por", "con", "sin", "sobre",
    "en", "al", "lo", "una", "uno", "unas", "unos", "que", "cual", "cuales",
    "quien", "quienes", "beneficiario", "empresa", "nit", "existe", "exsito",
    "datos", "donde", "cuanto", "caul", "cliente", "opera",
}

### NUEVO BLOQUE ###

# --- Helpers SQL ---

def _escape_sql_literal(value: str) -> str:
    # Escapa comillas simples para SQL seguro
    return (value or "").replace("'", "''")

def _col_norm(col: str = '"BENEFICIARIO"') -> str:
    # Columna normalizada para búsquedas tolerantes a tildes y mayúsculas
    return f'unaccent(upper({col}))'

def _like_norm_expr(literal: str) -> str:
    # Construye un LIKE normalizado: %literal%
    lit = _escape_sql_literal(literal)
    # '% ' || unaccent(upper('texto')) || ' %' evita colisiones de funciones
    return f"{_col_norm()} LIKE ('%' || unaccent(upper('{lit}')) || '%')"

def _and_ilike_tokens(tokens: List[str]) -> str:
    # Combina varios términos con AND (todos deben aparecer)
    conds = [ _like_norm_expr(t) for t in tokens if t.strip() ]
    return " AND ".join(conds) if conds else "TRUE"

def _pg_trgm_similarity_expr(literal: str) -> Tuple[str, str]:
    # Devuelve (select_list, where_cond) usando similarity() de pg_trgm
    lit = _escape_sql_literal(literal)
    sel = f'{_col_norm()} as nombre_norm, similarity({_col_norm()}, unaccent(upper(\'{lit}\'))) AS sim'
    whr = f"similarity({_col_norm()}, unaccent(upper('{lit}'))) > 0.25"
    return sel, whr

def _levenshtein_order_expr(literal: str) -> str:
    # Ordena por distancia Levenshtein (requiere fuzzystrmatch)
    lit = _escape_sql_literal(literal)
    return f'levenshtein({_col_norm()}, unaccent(upper(\'{lit}\'))) ASC'


def _construir_sql_forzado(sql_base: Optional[str], beneficiario: str) -> Optional[str]:
    """
    Incrusta/reemplaza el filtro por beneficiario usando normalización con unaccent+upper.
    Si sql_base es None o no parece un SELECT, devuelve None.
    """
    if not sql_base or not sql_base.strip().lower().startswith("select"):
        return None

    b = _escape_sql_literal(beneficiario)
    cond_canon = f"{_col_norm()} LIKE ('%' || unaccent(upper('{b}')) || '%')"

    sql = sql_base

    # Patrones de filtro comunes a reemplazar
    patrones = [
        r'("BENEFICIARIO"\s*=\s*\'.*?\')',
        r'("BENEFICIARIO"\s+ILIKE\s+\'.*?\')',
        r'(LOWER\("BENEFICIARIO"\)\s+LIKE\s+\'.*?\')',
        r'(unaccent\(\s*upper\(\s*"BENEFICIARIO"\s*\)\s*\)\s+LIKE\s+\'.*?\')',
    ]

    reemplazado = False
    for pat in patrones:
        if re.search(pat, sql, flags=re.IGNORECASE | re.DOTALL):
            sql = re.sub(pat, cond_canon, sql, flags=re.IGNORECASE | re.DOTALL)
            reemplazado = True

    if not reemplazado:
        # Insertar WHERE/AND según corresponda
        if re.search(r'\bWHERE\b', sql, flags=re.IGNORECASE):
            sql = re.sub(r'\bWHERE\b', f'WHERE {cond_canon} AND ', sql, flags=re.IGNORECASE, count=1)
        else:
            # Insertar WHERE antes de ORDER BY / LIMIT / fin
            m = re.search(r'\b(ORDER\s+BY|LIMIT)\b', sql, flags=re.IGNORECASE)
            if m:
                idx = m.start()
                sql = sql[:idx] + f' WHERE {cond_canon} ' + sql[idx:]
            else:
                sql = sql.rstrip() + f' WHERE {cond_canon}'

    return sql


### FIN DEL NUEVO BLOQUE ###





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
    """
    Devuelve sugerencias robustas por:
      1) ILIKE normalizado (%token%)
      2) ILIKE combinado por tokens (AND)
      3) pg_trgm: similarity(normalizado, normalizado) > umbral
      4) levenshtein (si está disponible)
    """
    sugerencias: List[str] = []
    vistos: set[str] = set()

    if not candidatos:
        return sugerencias

    # 1) ILIKE por token individual (normalizado con unaccent+upper)
    for token in candidatos:
        if len(sugerencias) >= limite:
            break
        pat = token.strip()
        if not pat:
            continue
        sql = (
            'SELECT DISTINCT "BENEFICIARIO" '
            'FROM beneficiarios '
            f'WHERE {_like_norm_expr(pat)} '
            'ORDER BY "BENEFICIARIO" '
            f'LIMIT {max(1, limite - len(sugerencias))}'
        )
        try:
            filas = ejecutar_sql(sql)
            for fila in filas or []:
                nombre = fila.get("BENEFICIARIO") if isinstance(fila, dict) else None
                if nombre and nombre not in vistos:
                    vistos.add(nombre)
                    sugerencias.append(nombre)
                    if len(sugerencias) >= limite:
                        break
        except Exception as exc:
            print(f"[buscar_similares] ILIKE unaccent fallo para '{pat}': {exc}")

    if len(sugerencias) >= limite:
        return sugerencias

    # 2) ILIKE combinando varios tokens con AND (nombres largos)
    tokens_relev = [t for t in candidatos if len(t.strip()) >= 3][:3]  # hasta 3 tokens
    if tokens_relev:
        cond_and = _and_ilike_tokens(tokens_relev)
        sql_and = (
            'SELECT DISTINCT "BENEFICIARIO" '
            'FROM beneficiarios '
            f'WHERE {cond_and} '
            'ORDER BY "BENEFICIARIO" '
            f'LIMIT {max(1, limite - len(sugerencias))}'
        )
        try:
            filas = ejecutar_sql(sql_and)
            for fila in filas or []:
                nombre = fila.get("BENEFICIARIO") if isinstance(fila, dict) else None
                if nombre and nombre not in vistos:
                    vistos.add(nombre)
                    sugerencias.append(nombre)
                    if len(sugerencias) >= limite:
                        break
        except Exception as exc:
            print(f"[buscar_similares] ILIKE AND fallo: {exc}")

    if len(sugerencias) >= limite:
        return sugerencias

    # 3) pg_trgm similarity (si disponible)
    try:
        # usa el primer token más informativo como anzuelo
        mejor = max(tokens_relev or candidatos, key=len)
        sel, whr = _pg_trgm_similarity_expr(mejor)
        sql_trgm = (
            f'SELECT DISTINCT "BENEFICIARIO", {sel} '
            'FROM beneficiarios '
            f'WHERE {whr} '
            'ORDER BY sim DESC '
            f'LIMIT {max(1, limite - len(sugerencias))}'
        )
        filas = ejecutar_sql(sql_trgm)
        for fila in filas or []:
            nombre = fila.get("BENEFICIARIO") if isinstance(fila, dict) else None
            if nombre and nombre not in vistos:
                vistos.add(nombre)
                sugerencias.append(nombre)
                if len(sugerencias) >= limite:
                    break
    except Exception as exc:
        print(f"[buscar_similares] pg_trgm similarity no disponible/errores: {exc}")

    if len(sugerencias) >= limite:
        return sugerencias

    # 4) levenshtein como plan C
    try:
        mejor = max(tokens_relev or candidatos, key=len)
        order_lev = _levenshtein_order_expr(mejor)
        sql_lev = (
            'SELECT DISTINCT "BENEFICIARIO" '
            'FROM beneficiarios '
            'ORDER BY ' + order_lev + ' '
            f'LIMIT {max(1, limite - len(sugerencias))}'
        )
        filas = ejecutar_sql(sql_lev)
        for fila in filas or []:
            nombre = fila.get("BENEFICIARIO") if isinstance(fila, dict) else None
            if nombre and nombre not in vistos:
                vistos.add(nombre)
                sugerencias.append(nombre)
                if len(sugerencias) >= limite:
                    break
    except Exception as exc:
        print(f"[buscar_similares] levenshtein no disponible/errores: {exc}")

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
    if not filas:
        return None

    columnas = list(filas[0].keys())
    buffer = BytesIO()
    try:
        if pd is not None:
            df = pd.DataFrame(filas)  # type: ignore[arg-type]
            df.to_excel(buffer, index=False)
        elif Workbook is not None:
            workbook = Workbook()
            hoja = workbook.active
            if columnas:
                hoja.append(columnas)
            for fila in filas:
                hoja.append([fila.get(col) for col in columnas])
            workbook.save(buffer)
        else:
            return None
    except Exception:
        return None

    buffer.seek(0)
    contenido_b64 = base64.b64encode(buffer.read()).decode('utf-8')
    return {
        "filename": f"reporte_{int(time.time())}.xlsx",
        "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "base64": contenido_b64,
    }


def _resolver_sql_con_reintentos(pregunta: str, contexto: str) -> tuple[List[Dict[str, Any]], Optional[str], List[Dict[str, Any]], Optional[str], List[str]]:
    intentos: List[Dict[str, Any]] = []
    ultimo_sql: Optional[str] = None
    ultimo_error: Optional[str] = None
    resultados: List[Dict[str, Any]] = []

    candidatos = _extraer_candidatos_beneficiario(pregunta)
    sugerencias = _buscar_beneficiarios_similares(candidatos)
    contexto_enriquecido = (contexto or "").strip()
    if sugerencias:
        can = sugerencias[0]
        bloque_sugerencias = (
            "Sugerencias de beneficiario:\n" + "\n".join(f"- {nombre}" for nombre in sugerencias) +
            f"\nDirectiva_SQL: Si filtras por beneficiario, usa ILIKE con este literal canónico: '%{can}%'"
        )
        contexto_enriquecido = (contexto_enriquecido + "\n" + bloque_sugerencias).strip()

    # Intentos "normales" (hasta 3)
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

    # Fallback "forzado" con beneficiario canónico si hay sugerencias y teníamos un SQL base
    if not resultados and sugerencias and ultimo_sql:
        for suger in sugerencias:
            sql_forzado = _construir_sql_forzado(ultimo_sql, suger)
            if not sql_forzado:
                continue
            intento_fx: Dict[str, Any] = {
                "intento": "forzado",
                "beneficiario_forzado": suger,
                "sql_forzado": sql_forzado,
            }
            try:
                res_fx = ejecutar_sql(sql_forzado)
                intento_fx["filas"] = len(res_fx or [])
                intentos.append(intento_fx)
                if res_fx:
                    # Éxito en forzado: devolvemos estos resultados
                    return res_fx, ultimo_sql, intentos, None, sugerencias
            except Exception as exc:
                intento_fx["error"] = f"ejecucion_forzado: {exc}"
                intentos.append(intento_fx)
                continue

    return (resultados if resultados else []), ultimo_sql, intentos, ultimo_error, sugerencias




@router.post("/asistente-finagro")
async def asistente_finagro(payload: PreguntaPayload):
    pregunta = payload.pregunta
    historial = payload.historial or []
    ultimo_sql = payload.ultimo_resultado_sql

    contexto_conversacional = ""
    for msg in historial[-10:]:
        contexto_conversacional += f"{msg['role']}: {msg['content']}\n"

    contexto_sql = f"Resultado anterior:\n{json.dumps(ultimo_sql, ensure_ascii=False)}\n" if ultimo_sql else ""
    prompt_con_historial = f"{contexto_conversacional}\n{contexto_sql}\nUsuario: {pregunta}"

    sugerencias_beneficiario = _buscar_beneficiarios_similares(_extraer_candidatos_beneficiario(pregunta))
    respuesta_manual = consultar_assistant(prompt_con_historial)

    payload_manual: Dict[str, Any] = {"respuesta": respuesta_manual, "sugerencias_beneficiario": sugerencias_beneficiario}
    # LOGGER.info(json.dumps({
    #     "endpoint": "asistente_finagro",
    #     "tipo": "manual",
    #     "pregunta": pregunta,
    # }, ensure_ascii=False))
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
