import pandas as pd
from sqlalchemy import create_engine

# Leer el archivo Excel limpio
df = pd.read_excel("base_limpia_postgresql.xlsx")  # o el nombre del archivo que descargaste

# Parámetros de conexión
usuario = 'postgres'
contrasena = 'Administrador'
host = 'localhost'
puerto = '5432'
nombre_bd = 'gpt_database'
nombre_tabla = 'beneficiarios'

# Crear engine de SQLAlchemy
conexion = create_engine(f'postgresql+psycopg2://{usuario}:{contrasena}@{host}:{puerto}/{nombre_bd}')

# Subir a PostgreSQL (reemplaza si existe, puedes usar append para añadir sin borrar)
df.to_sql(nombre_tabla, con=conexion, index=False, if_exists='replace')

print("✅ Datos cargados exitosamente.")
