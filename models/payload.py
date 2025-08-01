from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class PreguntaPayload(BaseModel):
    pregunta: str
    historial: Optional[List[Dict[str, str]]] = None
    ultimo_resultado_sql: Optional[List[Dict[str, Any]]] = None  # <-- Cambia str por Any
