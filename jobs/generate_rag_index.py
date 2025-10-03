import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from psycopg2.extras import DictCursor
from unidecode import unidecode

from app.config import (
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    RAG_COLLECTION_NAME,
    RAG_INDEX_PATH,
)
from app.db import get_db_connection
from utils.diccionario import column_synonyms


logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SOURCE_TAG = "beneficiarios_v1"


@dataclass
class Fact:
    id: str
    text: str
    metadata: Dict[str, object]


def format_cop(value: Decimal | float | int | None) -> str:
    if value is None:
        return "COP 0"
    amount = Decimal(value)
    formatted = f"{amount:,.0f}"
    return f"COP {formatted}".replace(",", ".")


def format_percentage(value: Decimal | float | None) -> str:
    if value is None:
        return "0%"
    pct = float(value) * 100
    return f"{pct:.1f}%"


def slugify(value: str | None) -> str:
    if not value:
        return "sin-dato"
    ascii_value = unidecode(value).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_value)
    cleaned = cleaned.strip("-")
    return cleaned or "sin-dato"


def clean_label(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\n", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


COLUMN_DESCRIPTION_OVERRIDES: Dict[str, str] = {
    "INTERMEDIARIO FINANCIERO": "Entidad financiera que canaliza el credito, por ejemplo bancos o cooperativas.",
    "NIT BENEFICIARIO": "Numero de identificacion tributaria del beneficiario sin digito de verificacion.",
    "BENEFICIARIO": "Nombre o razon social del productor o empresa que recibe el desembolso.",
    "FECHA DESEMBOLSO": "Fecha en la que FINAGRO desembolso los recursos al beneficiario.",
    "VALOR DESEMBOLSO": "Monto desembolsado en pesos colombianos para la operacion.",
    "VALOR DEL PROYECTO": "Valor total del proyecto financiado con recursos de FINAGRO.",
    "RUBRO": "Categoria de inversion o destino del credito desembolsado.",
    "DEPARTAMENTO BENEFICIARIO": "Departamento colombiano donde se ubica el beneficiario.",
    "MUNICIPIO BENEFICIARIO": "Municipio asociado al beneficiario.",
    "NIVEL ACTIVOS (PEQ-MED-GRA)": "Clasificacion del beneficiario segun el nivel de activos (pequeno, mediano, grande).",
}


def collect_monthly_intermediary_facts(conn) -> List[Fact]:
    logger.info("Consultando KPIs mensuales por intermediario...")
    sql = """
        WITH datos AS (
            SELECT
                CASE
                    WHEN "FECHA DESEMBOLSO" IS NULL THEN NULL
                    WHEN "FECHA DESEMBOLSO"::text ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING("FECHA DESEMBOLSO"::text FROM 1 FOR 10)::date
                    WHEN "FECHA DESEMBOLSO"::text ~ '^\d{2}/\d{2}/\d{4}' THEN TO_DATE(SUBSTRING("FECHA DESEMBOLSO"::text FROM 1 FOR 10), 'DD/MM/YYYY')
                    ELSE NULL
                END AS fecha,
                "INTERMEDIARIO FINANCIERO" AS intermediario,
                CASE
                    WHEN "VALOR DESEMBOLSO" IS NULL THEN NULL
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(\.\d+)?$' THEN "VALOR DESEMBOLSO"::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(,\d+)?$' THEN REPLACE("VALOR DESEMBOLSO"::text, ',', '.')::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?[0-9\.,]+$' THEN REPLACE(REPLACE("VALOR DESEMBOLSO"::text, '.', ''), ',', '.')::numeric
                    ELSE NULL
                END AS valor
            FROM beneficiarios
        ),
        mensual AS (
            SELECT DATE_TRUNC('month', fecha) AS mes,
                   intermediario,
                   SUM(valor) AS total_mes_intermediario,
                   COUNT(*) AS operaciones
            FROM datos
            WHERE fecha IS NOT NULL
              AND valor IS NOT NULL
              AND intermediario IS NOT NULL
            GROUP BY 1, 2
        ),
        totales AS (
            SELECT mes, SUM(total_mes_intermediario) AS total_mes
            FROM mensual
            GROUP BY 1
        )
        SELECT TO_CHAR(m.mes, 'YYYY-MM') AS mes_label,
               DATE_TRUNC('month', m.mes)::date AS inicio_mes,
               (DATE_TRUNC('month', m.mes) + INTERVAL '1 month' - INTERVAL '1 day')::date AS fin_mes,
               m.intermediario,
               m.total_mes_intermediario,
               m.operaciones,
               t.total_mes,
               CASE WHEN t.total_mes = 0 THEN NULL ELSE m.total_mes_intermediario / t.total_mes END AS participacion
        FROM mensual m
        JOIN totales t ON t.mes = m.mes
        ORDER BY m.mes DESC, m.total_mes_intermediario DESC
        LIMIT 120
    """
    facts: List[Fact] = []
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            total = row["total_mes_intermediario"]
            share = row["participacion"]
            if not total or (share is not None and share < 0.05):
                continue
            intermediario = clean_label(row["intermediario"])
            mes_label = row["mes_label"]
            fin_mes: date = row["fin_mes"]
            texto = (
                f"En {mes_label}, {intermediario} desembolso {format_cop(total)} en {row['operaciones']} operaciones, "
                f"equivalente al {format_percentage(share)} del total mensual (tabla beneficiarios, corte {fin_mes.isoformat()})."
            )
            fact_id = f"monthly_intermediary::{mes_label}::{slugify(intermediario)}"
            metadata = {
                "type": "monthly_intermediary_share",
                "month": mes_label,
                "intermediario": intermediario,
                "operaciones": int(row["operaciones"]),
                "share": float(share) if share is not None else None,
                "source": SOURCE_TAG,
            }
            facts.append(Fact(id=fact_id, text=texto, metadata=metadata))
    logger.info("Hechos mensuales generados: %s", len(facts))
    return facts



def collect_top_beneficiario_facts(conn) -> List[Fact]:
    logger.info("Consultando top beneficiarios...")
    sql = """
        WITH base AS (
            SELECT
                "BENEFICIARIO" AS beneficiario,
                "NIT BENEFICIARIO" AS nit,
                CASE
                    WHEN "VALOR DESEMBOLSO" IS NULL THEN NULL
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(\.\d+)?$' THEN "VALOR DESEMBOLSO"::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(,\d+)?$' THEN REPLACE("VALOR DESEMBOLSO"::text, ',', '.')::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?[0-9\.,]+$' THEN REPLACE(REPLACE("VALOR DESEMBOLSO"::text, '.', ''), ',', '.')::numeric
                    ELSE NULL
                END AS valor
            FROM beneficiarios
        )
        SELECT beneficiario,
               nit,
               SUM(valor) AS total_valor,
               COUNT(*) AS operaciones,
               MAX(valor) AS max_operacion
        FROM base
        WHERE beneficiario IS NOT NULL
          AND valor IS NOT NULL
        GROUP BY beneficiario, nit
        ORDER BY total_valor DESC NULLS LAST
        LIMIT 50
    """
    facts: List[Fact] = []
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            beneficiario = clean_label(row["beneficiario"])
            if not beneficiario:
                continue
            total = row["total_valor"]
            if not total:
                continue
            nit = clean_label(row["nit"])
            texto = f"El beneficiario {beneficiario} acumula {format_cop(total)} en {int(row['operaciones'])} desembolsos registrados."
            if nit:
                texto += f" NIT {nit}."
            max_operacion = row["max_operacion"]
            if max_operacion:
                texto += f" La mayor operacion individual fue de {format_cop(max_operacion)}."
            texto += " (tabla beneficiarios)."
            fact_id = f"top_beneficiario::{slugify(beneficiario)}"
            metadata = {
                "type": "top_beneficiario",
                "beneficiario": beneficiario,
                "nit": nit,
                "operaciones": int(row["operaciones"]),
                "source": SOURCE_TAG,
            }
            facts.append(Fact(id=fact_id, text=texto, metadata=metadata))
    logger.info("Hechos por beneficiario: %s", len(facts))
    return facts


def collect_top_rubro_facts(conn) -> List[Fact]:
    logger.info("Consultando top rubros...")
    sql = """
        WITH base AS (
            SELECT
                "RUBRO" AS rubro,
                CASE
                    WHEN "VALOR DESEMBOLSO" IS NULL THEN NULL
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(\.\d+)?$' THEN "VALOR DESEMBOLSO"::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(,\d+)?$' THEN REPLACE("VALOR DESEMBOLSO"::text, ',', '.')::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?[0-9\.,]+$' THEN REPLACE(REPLACE("VALOR DESEMBOLSO"::text, '.', ''), ',', '.')::numeric
                    ELSE NULL
                END AS valor
            FROM beneficiarios
        )
        SELECT rubro,
               SUM(valor) AS total_valor,
               COUNT(*) AS operaciones,
               AVG(valor) AS ticket_promedio
        FROM base
        WHERE rubro IS NOT NULL
          AND valor IS NOT NULL
        GROUP BY rubro
        ORDER BY total_valor DESC NULLS LAST
        LIMIT 60
    """
    facts: List[Fact] = []
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            rubro = clean_label(row["rubro"])
            if not rubro:
                continue
            total = row["total_valor"]
            if not total:
                continue
            avg_ticket = row["ticket_promedio"]
            texto = (
                f"El rubro {rubro} acumula {format_cop(total)} en {row['operaciones']} desembolsos, "
                f"con un ticket promedio de {format_cop(avg_ticket)} (tabla beneficiarios)."
            )
            fact_id = f"top_rubro::{slugify(rubro)}"
            metadata = {
                "type": "top_rubro",
                "rubro": rubro,
                "operaciones": int(row["operaciones"]),
                "source": SOURCE_TAG,
            }
            facts.append(Fact(id=fact_id, text=texto, metadata=metadata))
    logger.info("Hechos por rubro: %s", len(facts))
    return facts


def collect_top_departamento_facts(conn) -> List[Fact]:
    logger.info("Consultando top departamentos...")
    sql = """
        WITH base AS (
            SELECT
                "DEPARTAMENTO BENEFICIARIO" AS departamento,
                CASE
                    WHEN "VALOR DESEMBOLSO" IS NULL THEN NULL
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(\.\d+)?$' THEN "VALOR DESEMBOLSO"::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(,\d+)?$' THEN REPLACE("VALOR DESEMBOLSO"::text, ',', '.')::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?[0-9\.,]+$' THEN REPLACE(REPLACE("VALOR DESEMBOLSO"::text, '.', ''), ',', '.')::numeric
                    ELSE NULL
                END AS valor
            FROM beneficiarios
        )
        SELECT departamento,
               SUM(valor) AS total_valor,
               COUNT(*) AS operaciones
        FROM base
        WHERE departamento IS NOT NULL
          AND valor IS NOT NULL
        GROUP BY departamento
        ORDER BY total_valor DESC NULLS LAST
        LIMIT 50
    """
    facts: List[Fact] = []
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            departamento = clean_label(row["departamento"])
            if not departamento:
                continue
            total = row["total_valor"]
            if not total:
                continue
            texto = (
                f"El departamento {departamento} concentra {format_cop(total)} en {row['operaciones']} desembolsos registrados (tabla beneficiarios)."
            )
            fact_id = f"top_departamento::{slugify(departamento)}"
            metadata = {
                "type": "top_departamento",
                "departamento": departamento,
                "operaciones": int(row["operaciones"]),
                "source": SOURCE_TAG,
            }
            facts.append(Fact(id=fact_id, text=texto, metadata=metadata))
    logger.info("Hechos por departamento: %s", len(facts))
    return facts


def collect_recent_examples(conn) -> List[Fact]:
    logger.info("Consultando ejemplos recientes...")
    sql = """
        WITH base AS (
            SELECT
                CASE
                    WHEN "FECHA DESEMBOLSO" IS NULL THEN NULL
                    WHEN "FECHA DESEMBOLSO"::text ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING("FECHA DESEMBOLSO"::text FROM 1 FOR 10)::date
                    WHEN "FECHA DESEMBOLSO"::text ~ '^\d{2}/\d{2}/\d{4}' THEN TO_DATE(SUBSTRING("FECHA DESEMBOLSO"::text FROM 1 FOR 10), 'DD/MM/YYYY')
                    ELSE NULL
                END AS fecha,
                "INTERMEDIARIO FINANCIERO" AS intermediario,
                "BENEFICIARIO" AS beneficiario,
                "NIT BENEFICIARIO" AS nit,
                CASE
                    WHEN "VALOR DESEMBOLSO" IS NULL THEN NULL
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(\.\d+)?$' THEN "VALOR DESEMBOLSO"::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?\d+(,\d+)?$' THEN REPLACE("VALOR DESEMBOLSO"::text, ',', '.')::numeric
                    WHEN "VALOR DESEMBOLSO"::text ~ '^-?[0-9\.,]+$' THEN REPLACE(REPLACE("VALOR DESEMBOLSO"::text, '.', ''), ',', '.')::numeric
                    ELSE NULL
                END AS valor,
                "RUBRO" AS rubro,
                "DEPARTAMENTO BENEFICIARIO" AS departamento
            FROM beneficiarios
        )
        SELECT fecha, intermediario, beneficiario, nit, valor, rubro, departamento
        FROM base
        WHERE fecha IS NOT NULL
          AND valor IS NOT NULL
        ORDER BY fecha DESC
        LIMIT 80
    """
    facts: List[Fact] = []
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(sql)
        for idx, row in enumerate(cur.fetchall(), start=1):
            fecha: date = row["fecha"]
            valor = row["valor"]
            if not fecha or not valor:
                continue
            intermediario = clean_label(row["intermediario"])
            beneficiario = clean_label(row["beneficiario"])
            departamento = clean_label(row["departamento"])
            rubro = clean_label(row["rubro"])
            nit = clean_label(row["nit"])
            partes = [
                f"El {fecha.isoformat()}",
                f"{intermediario or 'un intermediario'} desembolso {format_cop(valor)}",
            ]
            if beneficiario:
                partes.append(f"al beneficiario {beneficiario}")
            if nit:
                partes.append(f"(NIT {nit})")
            if rubro:
                partes.append(f"para el rubro {rubro}")
            if departamento:
                partes.append(f"en {departamento}")
            texto = ", ".join(partes) + " (tabla beneficiarios)."
            fact_id = f"operacion_reciente::{fecha.isoformat()}::{idx}"
            metadata = {
                "type": "operacion_reciente",
                "fecha": fecha.isoformat(),
                "intermediario": intermediario,
                "beneficiario": beneficiario,
                "rubro": rubro,
                "departamento": departamento,
                "source": SOURCE_TAG,
            }
            facts.append(Fact(id=fact_id, text=texto, metadata=metadata))
    logger.info("Ejemplos recientes generados: %s", len(facts))
    return facts


def collect_column_definition_facts() -> List[Fact]:
    logger.info("Generando definiciones de columnas...")
    facts: List[Fact] = []
    for canonical, synonyms in column_synonyms.items():
        label = clean_label(canonical)
        if not label:
            continue
        description = COLUMN_DESCRIPTION_OVERRIDES.get(label)
        if description:
            base_text = description
        else:
            synonym_list = sorted({clean_label(unidecode(s)) for s in (synonyms or []) if s})
            if synonym_list:
                base_text = f"Campo de la tabla beneficiarios. Sinonimos frecuentes: {', '.join(synonym_list)}."
            else:
                base_text = "Campo de la tabla beneficiarios sin sinonimos registrados."
        texto = f"{label}: {base_text}"
        fact_id = f"columna::{slugify(label)}"
        metadata = {
            "type": "definicion_columna",
            "columna": label,
            "source": SOURCE_TAG,
        }
        facts.append(Fact(id=fact_id, text=texto, metadata=metadata))
    logger.info("Definiciones generadas: %s", len(facts))
    return facts


def ensure_collection():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY no esta configurada")
    embedding_fn = OpenAIEmbeddingFunction(api_key=OPENAI_API_KEY, model_name=OPENAI_EMBEDDING_MODEL)
    target_path = Path(RAG_INDEX_PATH).expanduser()
    target_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(target_path))
    collection = client.get_or_create_collection(name=RAG_COLLECTION_NAME, embedding_function=embedding_fn)
    return collection


def upsert_facts(collection, facts: Iterable[Fact]) -> None:
    facts_list = list(facts)
    if not facts_list:
        logger.warning("No se recibieron hechos para indexar.")
        return

    deduped: list[Fact] = []
    seen_ids = set()
    for fact in facts_list:
        if fact.id in seen_ids:
            continue
        seen_ids.add(fact.id)
        deduped.append(fact)

    if len(deduped) < len(facts_list):
        logger.warning("Se ignoraron %s hechos por ID duplicado.", len(facts_list) - len(deduped))

    collection.delete(where={"source": SOURCE_TAG})
    collection.upsert(
        ids=[f.id for f in deduped],
        documents=[f.text for f in deduped],
        metadatas=[f.metadata for f in deduped],
    )
    logger.info("Se indexaron %s hechos en la coleccion %s", len(deduped), RAG_COLLECTION_NAME)


def main() -> None:
    logger.info("Generando indice semantico desde la base de datos...")
    with get_db_connection() as conn:
        facts: List[Fact] = []
        facts.extend(collect_monthly_intermediary_facts(conn))
        facts.extend(collect_top_beneficiario_facts(conn))
        facts.extend(collect_top_rubro_facts(conn))
        facts.extend(collect_top_departamento_facts(conn))
        facts.extend(collect_recent_examples(conn))
    facts.extend(collect_column_definition_facts())

    logger.info("Total de hechos a indexar: %s", len(facts))
    collection = ensure_collection()
    upsert_facts(collection, facts)
    logger.info("Indice semantico actualizado correctamente.")


if __name__ == "__main__":
    main()
