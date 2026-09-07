"""Parseo flexible de valores introducidos a mano (CLI y Streamlit).

Permite escribir numeros/booleanos/objetos JSON, o texto plano si no es
JSON valido, sin tener que escapar comillas para el caso mas comun (texto).
"""
from __future__ import annotations

import json


def parse_flexible_value(raw_value: str):
    """Ejemplos: "32" -> 32 (int); "true" -> True; "alta demanda" -> "alta
    demanda" (texto tal cual); '{"value": 2, "unit": "weeks"}' -> dict."""
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value
