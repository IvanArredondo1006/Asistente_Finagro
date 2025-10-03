import os
import sys
import psycopg2
from dotenv import load_dotenv


def mask(s: str | None) -> str:
    if not s:
        return "(vacío)"
    if len(s) <= 2:
        return "*" * len(s)
    return s[0] + "*" * (len(s) - 2) + s[-1]


def main() -> int:
    load_dotenv()

    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    pwd = os.getenv("DB_PASSWORD")

    print("Verificando conexión a PostgreSQL con las siguientes variables:")
    print(f"  DB_HOST = {host}")
    print(f"  DB_PORT = {port}")
    print(f"  DB_NAME = {name or '(vacío)'}")
    print(f"  DB_USER = {user or '(vacío)'}")
    print(f"  DB_PASSWORD = {mask(pwd)}")

    if not all([name, user, pwd]):
        print("Faltan variables obligatorias: DB_NAME, DB_USER y/o DB_PASSWORD.")
        return 2

    table = None
    if len(sys.argv) > 1:
        table = sys.argv[1]

    try:
        # connect_timeout en segundos para evitar cuelgues
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=name,
            user=user,
            password=pwd,
            connect_timeout=8,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                one = cur.fetchone()
                print(f"Conexión OK. SELECT 1 => {one}")

                if table:
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {table}")
                        cnt = cur.fetchone()[0]
                        print(f"COUNT(*) de {table}: {cnt}")
                    except Exception as te:
                        print(f"Conexión OK, pero error contando tabla '{table}': {te}")
        return 0
    except Exception as e:
        print("ERROR de conexión:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

