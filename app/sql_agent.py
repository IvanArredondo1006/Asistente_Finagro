from openai import OpenAI
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import re
import time
import unicodedata

from app.db import get_db_connection
from app.config import OPENAI_API_KEY
from app.rag import retrieve_facts
from utils.diccionario import column_synonyms

CLIENT_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "30"))
client = OpenAI(api_key=OPENAI_API_KEY)


def obtener_esquema() -> tuple[str, Dict[str, List[str]]]:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
            filas = cur.fetchall()

    esquema: Dict[str, List[tuple[str, str]]] = {}
    for tabla, columna, tipo in filas:
        esquema.setdefault(tabla, []).append((columna, tipo))

    descripcion = "\n".join(
        f"{tabla}: " + ", ".join(f"{col} ({tipo})" for col, tipo in columnas)
        for tabla, columnas in esquema.items()
    )
    columnas_por_tabla = {
        tabla: [col for col, _ in columnas] for tabla, columnas in esquema.items()
    }
    return descripcion, columnas_por_tabla


SCHEMA_DESCRIPCION, SCHEMA_COLUMN_INDEX = obtener_esquema()
COLUMN_TABLE_INDEX: Dict[str, List[str]] = {}
for tabla, columnas in SCHEMA_COLUMN_INDEX.items():
    for columna in columnas:
        COLUMN_TABLE_INDEX.setdefault(columna, []).append(tabla)

ALL_COLUMN_NAMES = {
    col
    for columnas in SCHEMA_COLUMN_INDEX.values()
    for col in columnas
}
ALL_COLUMN_NAMES.update(column_synonyms.keys())
ALL_COLUMN_NAMES = sorted(ALL_COLUMN_NAMES)

COLUMN_SYNONYMS_GLOSSARY = "\n".join(


    f"{col}: {', '.join(sinonimos)}" for col, sinonimos in column_synonyms.items()
)

RAW_BANCO_SYNONYMS = {
    "BANCO AV VILLAS": ["AV VILLAS", "AVVILLAS"],
    "BANCO DE BOGOTÁ": ["BANCO DE BOGOTA", "BANCO DE BOGOTÁ"],
    "BANCO DE OCCIDENTE": ["BANCO DE OCCIDENTE"],
    "BANCO FINANDINA": ["FINANDINA", "BANCO FINANDINA"],
    "BANCO SANTANDER": ["SANTANDER", "BANCO SANTANDER"],
    "BANCO SCOTIABANK COLPATRIA S.A.": [
        "SCOTIABANK", "COLPATRIA", "SCOTIABANK COLPATRIA",
        "BANCO SCOTIABANK COLPATRIA", "BANCO SCOTIABANK COLPATRIA S.A.",
    ],
    "BANCOLOMBIA": ["BANCOLOMBIA", "BANCOLOMBIA S.A."],
    "BBVA COLOMBIA": ["BBVA", "BBVA COLOMBIA"],
    "IRIS CF - COMPAÑIA DE FINANCIAMIENTO S.A.": [
        "IRIS", "IRIS CF", "IRIS CF - COMPAÑIA DE FINANCIAMIENTO S.A.",
    ],
    "ITAÚ CORPBANCA COLOMBIA S.A.": [
        "ITAÚ", "ITAU", "ITAÚ CORPBANCA", "ITAÚ CORPBANCA COLOMBIA S.A.",
    ],
}



def _normalizar(texto: str) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()

def _normalizar_clave_banco(texto: str) -> str:
    return _normalizar(texto).replace(" ", "") if texto else ""

BANCO_SYNONYMS: Dict[str, str] = {}
for canonical, variantes in RAW_BANCO_SYNONYMS.items():
    clave_canonica = _normalizar_clave_banco(canonical)
    if clave_canonica:
        BANCO_SYNONYMS[clave_canonica] = canonical
    for variante in variantes:
        clave_variante = _normalizar_clave_banco(variante)
        if clave_variante:
            BANCO_SYNONYMS.setdefault(clave_variante, canonical)




def _extraer_bancos_canonicos(*textos: str) -> List[str]:
    encontrados: List[str] = []
    vistos: set[str] = set()
    for texto in textos:
        if not texto:
            continue
        for token in re.findall(r"[\wÁÉÍÓÚÜÑáéíóúüñ]+", texto):
            clave = _normalizar_clave_banco(token)
            if not clave:
                continue
            banco = BANCO_SYNONYMS.get(clave)
            if banco and banco not in vistos:
                vistos.add(banco)
                encontrados.append(banco)
    return encontrados

def _detectar_mapeos(*textos: str) -> Dict[str, List[str]]:
    combinado = " ".join(t for t in textos if t)
    normalizado = _normalizar(combinado)
    coincidencias: Dict[str, List[str]] = {}

    for columna in ALL_COLUMN_NAMES:
        posibles_terminos = [columna]
        sinonimos = column_synonyms.get(columna, [])
        posibles_terminos.extend(sinonimos)

        for termino in posibles_terminos:
            termino_normalizado = _normalizar(termino)
            if termino_normalizado and termino_normalizado in normalizado:
                coincidencias.setdefault(columna, [])
                if termino not in coincidencias[columna]:
                    coincidencias[columna].append(termino)
    return coincidencias


def _formatear_pistas(mapeos: Dict[str, List[str]]) -> str:
    if not mapeos:
        return (
            "No se detectaron coincidencias directas. Revisa el glosario y el esquema para elegir la columna correcta."
        )

    lineas = [
        f'- {", ".join(terminos)} -> "{columna}"' for columna, terminos in mapeos.items()
    ]
    pistas = "\n".join(lineas)
    return (
        "Equivalencias detectadas entre términos de la conversación y columnas del esquema:\n"
        f"{pistas}\nUsa exactamente esos nombres de columna entre comillas dobles."
    )


def _extraer_anios(*textos: str) -> List[int]:
    anios: List[int] = []
    patron = re.compile(r"\b(19|20)\d{2}\b")
    for texto in textos:
        if not texto:
            continue
        for match in patron.finditer(texto):
            valor = int(match.group())
            if 1900 <= valor <= 2100:
                anios.append(valor)
    return anios


_MAX_KEYWORDS = (
    "mas grande",
    "mayor",
    "maximo",
    "mas alto",
    "maxima",
    "mayores",
)


def _quiere_maximo(texto: str) -> bool:
    normalizado = _normalizar(texto)
    return any(palabra in normalizado for palabra in _MAX_KEYWORDS)


def _inferir_tabla_principal(columnas: List[str]) -> Optional[str]:
    mejor_tabla: Optional[str] = None
    mejor_puntaje = 0
    for tabla, columnas_tabla in SCHEMA_COLUMN_INDEX.items():
        puntaje = sum(1 for col in columnas if col in columnas_tabla)
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_tabla = tabla
    return mejor_tabla




def _construir_sql_por_banca(pregunta: str, contexto: str, mapeos: Dict[str, List[str]]) -> Optional[str]:
    if "BANCA" not in mapeos:
        return None

    columnas_base = ["BANCA", "VALOR DESEMBOLSO", "INTERMEDIARIO FINANCIERO"]
    tabla = _inferir_tabla_principal(columnas_base)
    if not tabla:
        return None

    columnas_tabla = SCHEMA_COLUMN_INDEX.get(tabla, [])
    if "VALOR DESEMBOLSO" not in columnas_tabla or "INTERMEDIARIO FINANCIERO" not in columnas_tabla:
        return None

    include_linea = "LINEA ESPECIAL" in columnas_tabla and "LINEA ESPECIAL" in mapeos

    filtros: List[str] = []
    anios = _extraer_anios(pregunta, contexto)
    if anios:
        fecha_columna = None
        for candidata in ("FECHA DESEMBOLSO", "FECHA ELABORACION", "FECHA"):
            if candidata in columnas_tabla:
                fecha_columna = candidata
                break
        if fecha_columna:
            filtros.append(f"""DATE_PART('year', "{fecha_columna}") = {anios[-1]}""")

    tokens_banco = mapeos.get("INTERMEDIARIO FINANCIERO", [])
    if not tokens_banco:
        tokens_banco = _extraer_bancos_canonicos(pregunta, contexto)
    filtros_banco: List[str] = []
    for token in tokens_banco:
        token_limpio = token.strip()
        if not token_limpio:
            continue
        clave_banco = _normalizar_clave_banco(token_limpio)
        candidato = BANCO_SYNONYMS.get(clave_banco, token_limpio)
        candidato_sql = candidato.replace("'", "''")
        filtros_banco.append("\"INTERMEDIARIO FINANCIERO\" ILIKE '%{}%'".format(candidato_sql))
    if filtros_banco:
        filtros.append("( " + " OR ".join(filtros_banco) + " )")

    filtros.append('"VALOR DESEMBOLSO" IS NOT NULL')

    select_cols = ['"INTERMEDIARIO FINANCIERO"', '"BANCA"']
    group_cols = ['"INTERMEDIARIO FINANCIERO"', '"BANCA"']
    if include_linea:
        select_cols.append('"LINEA ESPECIAL"')
        group_cols.append('"LINEA ESPECIAL"')

    select_cols.append('SUM("VALOR DESEMBOLSO") AS total_desembolso')
    porcentaje_expr = (
        'CASE WHEN SUM("VALOR DESEMBOLSO") = 0 THEN 0 '
        'ELSE SUM("VALOR DESEMBOLSO") / '
        'NULLIF(SUM(SUM("VALOR DESEMBOLSO")) OVER (PARTITION BY "INTERMEDIARIO FINANCIERO"), 0) END AS porcentaje_banca'
    )
    select_cols.append(porcentaje_expr)

    where_clause = ''
    if filtros:
        where_clause = ' WHERE ' + ' AND '.join(filtros)

    group_clause = ' GROUP BY ' + ', '.join(group_cols)
    order_clause = ' ORDER BY porcentaje_banca DESC'

    sql = f'SELECT {", ".join(select_cols)} FROM "{tabla}"{where_clause}{group_clause}{order_clause}'
    return sql

def _construir_sql_heuristico(pregunta: str, contexto: str, mapeos: Dict[str, List[str]]) -> Optional[str]:
    sql_banca = _construir_sql_por_banca(pregunta, contexto, mapeos)
    if sql_banca:
        return sql_banca

    combinado = " ".join(filter(None, [pregunta, contexto]))
    if not combinado:
        return None

    if not _quiere_maximo(combinado):
        return None

    columnas_referenciadas = list(mapeos.keys())
    if not columnas_referenciadas:
        return None

    tabla = _inferir_tabla_principal(columnas_referenciadas)
    if not tabla:
        return None

    columnas_tabla = SCHEMA_COLUMN_INDEX.get(tabla, [])

    medida = None
    for candidata in (
        "VALOR DESEMBOLSO",
        "VALOR DEL PROYECTO",
        "MONTO ACTIVOS BENEFICIARIO",
    ):
        if candidata in columnas_tabla:
            medida = candidata
            break
    if not medida:
        return None

    dimension = None
    for candidata in (
        "BENEFICIARIO",
        "NIT BENEFICIARIO",
        "INTERMEDIARIO FINANCIERO",
    ):
        if candidata in columnas_tabla:
            dimension = candidata
            break

    columnas_select = [medida] if not dimension else [dimension, medida]

    fecha_columna = None
    for candidata in (
        "FECHA DESEMBOLSO",
        "FECHA ELABORACION",
        "FECHA",
    ):
        if candidata in columnas_tabla:
            fecha_columna = candidata
            break

    filtros: List[str] = []
    anios = _extraer_anios(pregunta, contexto)
    if anios and fecha_columna:
        filtros.append(f"DATE_PART('year', \"{fecha_columna}\") = {anios[-1]}")

    filtros.append(f'"{medida}" IS NOT NULL')

    where_clause = ''
    if filtros:
        where_clause = ' WHERE ' + ' AND '.join(filtros)

    order_clause = f' ORDER BY "{medida}" DESC'
    limit_clause = ' LIMIT 1'
    select_clause = ', '.join(f'"{col}"' for col in columnas_select)

    sql = f'SELECT {select_clause} FROM "{tabla}"{where_clause}{order_clause}{limit_clause}'
    return sql_banca

    combinado = " ".join(filter(None, [pregunta, contexto]))
    if not combinado:
        return None

    if not _quiere_maximo(combinado):
        return None

    columnas_referenciadas = list(mapeos.keys())
    if not columnas_referenciadas:
        return None

    tabla = _inferir_tabla_principal(columnas_referenciadas)
    if not tabla:
        return None

    columnas_tabla = SCHEMA_COLUMN_INDEX.get(tabla, [])

    medida = None
    for candidata in (
        "VALOR DESEMBOLSO",
        "VALOR DEL PROYECTO",
        "MONTO ACTIVOS BENEFICIARIO",
    ):
        if candidata in columnas_tabla:
            medida = candidata
            break
    if not medida:
        return None

    dimension = None
    for candidata in (
        "BENEFICIARIO",
        "NIT BENEFICIARIO",
        "INTERMEDIARIO FINANCIERO",
    ):
        if candidata in columnas_tabla:
            dimension = candidata
            break

    columnas_select = [medida] if not dimension else [dimension, medida]

    fecha_columna = None
    for candidata in (
        "FECHA DESEMBOLSO",
        "FECHA ELABORACION",
        "FECHA",
    ):
        if candidata in columnas_tabla:
            fecha_columna = candidata
            break

    filtros: List[str] = []
    anios = _extraer_anios(pregunta, contexto)
    if anios and fecha_columna:
        filtros.append(f"DATE_PART('year', \"{fecha_columna}\") = {anios[-1]}")

    filtros.append(f'"{medida}" IS NOT NULL')

    where_clause = ""
    if filtros:
        where_clause = " WHERE " + " AND ".join(filtros)

    order_clause = f' ORDER BY "{medida}" DESC'
    limit_clause = " LIMIT 1"
    select_clause = ", ".join(f'"{col}"' for col in columnas_select)

    sql = f'SELECT {select_clause} FROM "{tabla}"{where_clause}{order_clause}{limit_clause}'
    return sql




def generar_sql(pregunta: str, contexto: str, sugerencias_beneficiario: Optional[List[str]] = None) -> str:
    t0 = time.time()
    sugerencias_limpias = [nombre for nombre in (sugerencias_beneficiario or []) if nombre]
    print(f"[sql_agent] Generando SQL para: {pregunta[:120]}")
    mapeos = _detectar_mapeos(pregunta, contexto)
    pistas_texto = _formatear_pistas(mapeos)
    bancos_detectados = _extraer_bancos_canonicos(pregunta, contexto)
    if bancos_detectados:
        pistas_texto += "\nBancos detectados: " + ", ".join(bancos_detectados)
    if sugerencias_limpias:
        pistas_texto += (
            "\nCoincidencias de beneficiario detectadas:\n"
            + "\n".join(f"- {nombre}" for nombre in sugerencias_limpias[:5])
        )
    filtra_beneficiario = "BENEFICIARIO" in mapeos or bool(sugerencias_limpias)
    terminos_beneficiario = mapeos.get("BENEFICIARIO") or []
    if not terminos_beneficiario and sugerencias_limpias:
        terminos_beneficiario = sugerencias_limpias
    ejemplo_beneficiario = ""
    if filtra_beneficiario:
        candidato = terminos_beneficiario[0] if terminos_beneficiario else ""
        for termino in terminos_beneficiario:
            if _normalizar(termino) != _normalizar("BENEFICIARIO"):
                candidato = termino
                break
        candidato = candidato.upper()
        ejemplo_beneficiario = candidato.replace(chr(34), "").replace("%", "").strip()
    reglas = [
        "- Siempre encierra los nombres de columnas entre comillas dobles ("").",
        "- No asumas filtros adicionales a menos que la pregunta los mencione explicitamente.",
        "- Si el usuario no menciona filtros clave (p. ej., NIT o ID), reutiliza los presentes en el contexto si existen.",
        "- Si la pregunta es ambigua, devuelve la mejor consulta que responda con los datos disponibles, prefiriendo conteos o listados generales.",
        "- Convierte los literales de texto a MAYUSCULAS y, cuando compares texto, aplica UPPER() o ILIKE segun corresponda.",
        "- Respeta los nombres de columnas exactamente como aparecen en el esquema (incluyendo tildes y caracteres especiales).",
    ]
    if "BANCA" in mapeos:
        reglas.append("- Cuando la consulta pida resultados por BANCA, agrupa por \"BANCA\" y calcula el porcentaje sobre el total del banco. Si necesitas PARTITION BY, incluye \"INTERMEDIARIO FINANCIERO\" en el SELECT y en el GROUP BY; si ya filtras a un solo banco, usa la ventana sin PARTITION BY.")
    if "LINEA ESPECIAL" in mapeos:
        reglas.append("- Si la pregunta menciona LINEA ESPECIAL, incluye \"LINEA ESPECIAL\" en el SELECT y en el GROUP BY para mostrar ese detalle junto con la BANCA o el intermediario.")
    if sugerencias_limpias:
        coincidencias_texto = ", ".join(sugerencias_limpias[:5])
        reglas.append(
            "- Valida los filtros sobre \"BENEFICIARIO\" utilizando literalmente uno de los siguientes nombres: "
            f"{coincidencias_texto}."
        )
    if filtra_beneficiario:
        ejemplo_regla = (ejemplo_beneficiario or "texto").upper()
        if sugerencias_limpias:
            regla_beneficiario = (
                "- Para filtros sobre \"BENEFICIARIO\" usa ILIKE con comodines empleando el nombre validado, "
                f"por ejemplo \"BENEFICIARIO\" ILIKE '%{ejemplo_regla}%'."
            )
        else:
            regla_beneficiario = (
                "- Para filtros sobre \"BENEFICIARIO\" usa ILIKE con comodines empleando el texto del usuario en MAYUSCULAS, "
                f"por ejemplo \"BENEFICIARIO\" ILIKE '%{ejemplo_regla}%'."
            )
        reglas.append(regla_beneficiario)
    reglas_texto = "\n".join(reglas)
    prompt = (
        "Eres un generador de consultas SQL para PostgreSQL. "
        "Tu unica salida debe ser una consulta que comience por SELECT, sin ningun texto adicional. "
        "Reglas importantes:\n"
        f"{reglas_texto}\n"
        "\nContexto de conversacion y resultados anteriores:\n"
        f"{contexto}\n\n"
        "Glosario de columnas y terminos asociados:\n"
        f"{COLUMN_SYNONYMS_GLOSSARY or 'Sin glosario disponible'}\n\n"
        "Esquema de la base de datos:\n"
        f"{SCHEMA_DESCRIPCION}"
    )
    mensaje_usuario = (
        f"Pregunta del usuario: {pregunta}\n\n"
        f"Pistas de mapeo detectadas:\n{pistas_texto}"
    )
    if sugerencias_limpias:
        mensaje_usuario += (
            "\n\nBeneficiarios validados:\n"
            + "\n".join(f"- {nombre}" for nombre in sugerencias_limpias[:5])
            + "\nUsa exactamente uno de estos nombres en la consulta."
        )
    try:
        respuesta = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": mensaje_usuario}
            ],
            max_tokens=400,
            temperature=0,
            timeout=CLIENT_TIMEOUT
        )
        contenido = respuesta.choices[0].message.content.strip()
        if "```" in contenido:
            contenido = contenido.split("```")[-1].strip()
        if not contenido.lower().startswith("select"):
            raise ValueError(f"La respuesta no es una consulta valida: {contenido}")
        sql = contenido.rstrip(";")
        print(f"[sql_agent] SQL generado en {time.time()-t0:.2f}s: {sql}")
        return sql
    except Exception as exc:  # noqa: BLE001
        print(f"[sql_agent] Fallback heuristico por error: {exc}")
        fallback_sql = _construir_sql_heuristico(pregunta, contexto, mapeos)
        if fallback_sql:
            print(f"[sql_agent] SQL heuristico: {fallback_sql}")
            return fallback_sql
        raise



def ejecutar_sql(sql: str):
    t0 = time.time()
    print(f"[sql_agent] Ejecutando SQL: {sql}")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            filas = cur.fetchall()
            columnas = [desc[0] for desc in cur.description]
    resultados = [
        {col: float(val) if isinstance(val, Decimal) else val for col, val in zip(columnas, fila)}
        for fila in filas
    ]
    print(f"[sql_agent] Ejecutado en {time.time()-t0:.2f}s, filas: {len(resultados)}")
    return resultados




def generar_respuesta_sql(
    pregunta: str,
    datos: List[Dict[str, Any]],
    hechos: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    usados = list(hechos or [])
    if not datos and not usados:
        usados = retrieve_facts(pregunta)

    if not datos and not usados:
        mensaje = (
            "No encontre informacion directa en la base de datos ni en el indice contextual. "
            "Intenta reformular con mas filtros como ano, NIT o intermediario."
        )
        return mensaje, []

    t0 = time.time()
    print(
        f"[sql_agent] Generando respuesta NL, filas={len(datos)} hechos={len(usados)}"
    )

    piezas_usuario = [f"Pregunta: {pregunta}"]
    if datos:
        piezas_usuario.append(
            "Datos tabulares (JSON):\n" + json.dumps(datos, ensure_ascii=False)
        )
    if usados:
        piezas_usuario.append(
            "Hechos de referencia:\n" + json.dumps(usados, ensure_ascii=False)
        )

    mensajes = [
        {
            "role": "system",
            "content": (
                "Eres analista de datos para FINAGRO. Explica los hallazgos de forma clara para personas sin conocimiento tecnico. "
                "Si hay datos tabulares, prioriza responder con cifras concretas y comparaciones sencillas. "
                "Si solo cuentas con hechos de referencia, resume la informacion tal cual aparece sin inventar datos nuevos."
            ),
        },
        {"role": "user", "content": "\n\n".join(piezas_usuario)},
    ]

    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=mensajes,
        max_tokens=700,
        temperature=0.2,
        timeout=CLIENT_TIMEOUT,
    )
    texto = respuesta.choices[0].message.content.strip()
    print(f"[sql_agent] Respuesta generada en {time.time()-t0:.2f}s")
    return texto, usados
