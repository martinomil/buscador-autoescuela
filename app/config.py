"""Carga de configuracion desde variables de entorno / .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


DATABASE_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "data/autoescuelas.db")

GMAIL_CREDENTIALS_FILE = BASE_DIR / os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
GMAIL_TOKEN_FILE = BASE_DIR / os.getenv("GMAIL_TOKEN_FILE", "token.json")
GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL_CHEAP = os.getenv("LLM_MODEL_CHEAP", "claude-haiku-4-5-20251001")
LLM_MODEL_SMART = os.getenv("LLM_MODEL_SMART", "claude-sonnet-5")

EMAIL_SEND_DELAY_SECONDS = _get_int("EMAIL_SEND_DELAY_SECONDS", 5)
EMAIL_MAX_PER_RUN = _get_int("EMAIL_MAX_PER_RUN", 30)

TEMPLATES_DIR = BASE_DIR / "templates"
