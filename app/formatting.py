"""Helpers de formato para mostrar datos en CLI/interfaz (Fase 5/6).

Regla comun: un dato desconocido se muestra como "—", nunca como "0" o
vacio, para no confundir "no lo sabemos" con "es cero/malo".
"""
from __future__ import annotations

UNKNOWN = "—"

_UNIT_LABELS = {"days": "dias", "weeks": "semanas", "months": "meses"}


def format_duration(duration: dict | None) -> str:
    if not isinstance(duration, dict):
        return UNKNOWN
    value, unit = duration.get("value"), duration.get("unit")
    if value is None or unit is None:
        return UNKNOWN
    unit_label = _UNIT_LABELS.get(unit, unit)
    return f"{value:g} {unit_label}"


def format_price(price: float | None) -> str:
    if price is None:
        return UNKNOWN
    return f"{price:g}€"


def format_range(min_value: float | None, max_value: float | None) -> str:
    if min_value is None and max_value is None:
        return UNKNOWN
    if min_value is None:
        return f"~{max_value:g}"
    if max_value is None:
        return f"~{min_value:g}"
    if min_value == max_value:
        return f"{min_value:g}"
    return f"{min_value:g}-{max_value:g}"


def format_value(value) -> str:
    if value is None or value == "":
        return UNKNOWN
    return str(value)
