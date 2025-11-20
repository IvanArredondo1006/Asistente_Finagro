from __future__ import annotations

import logging
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_LOG_FILE = _LOG_DIR / "assistant.log"
_LOG_TXT_FILE = _LOG_DIR / "assistant.txt"

LOGGER = logging.getLogger("assistant")
if not LOGGER.handlers:
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    # .log handler
    handler_log = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    handler_log.setFormatter(formatter)
    LOGGER.addHandler(handler_log)

    # .txt handler (for users asking a plain text log)
    handler_txt = logging.FileHandler(_LOG_TXT_FILE, encoding="utf-8")
    handler_txt.setFormatter(formatter)
    LOGGER.addHandler(handler_txt)
    LOGGER.setLevel(logging.INFO)
