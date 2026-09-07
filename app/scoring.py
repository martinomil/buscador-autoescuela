"""Puntuacion deterministica y explicable de una autoescuela (Fase 5).

Reglas puras, sin LLM, para las 4 metricas cuantitativas segun las
prioridades del usuario (ver README):
    1. Empezar las practicas cuanto antes.
    2. Hacer muchas practicas por semana.
    3. Poco tiempo entre empezar y el examen.
    4. Precio razonable (NO es la prioridad principal).

Un dato desconocido NUNCA se trata como "malo": se puntua con un valor
neutro (ni alto ni bajo) en su propio componente. La incertidumbre se
refleja aparte, en el componente "disponibilidad/confianza", que resta
puntos por cada dato que falta o es explicitamente incierto. Así, una
autoescuela con datos excelentes pero incompletos no queda penalizada dos
veces por lo mismo.
"""
from __future__ import annotations

import math

COMPONENT_MAX = {
    "inicio": 25,
    "frecuencia": 25,
    "tiempo_examen": 20,
    "precio": 15,
    "disponibilidad": 15,
}

COMPONENT_LABELS = {
    "inicio": "Inicio de practicas",
    "frecuencia": "Frecuencia de practicas",
    "tiempo_examen": "Tiempo hasta examen",
    "precio": "Precio",
    "disponibilidad": "Confianza / disponibilidad",
}

# Puntuacion que recibe un componente cuando el dato es desconocido: un
# valor neutro (ni bueno ni malo), no un cero.
_NEUTRAL_RATIO = 0.6

_UNKNOWN_PENALTY_PER_COMPONENT = 3
_UNCERTAIN_PENALTY = 3
_FOLLOW_UP_PENALTY = 2

VERDICT_THRESHOLDS = (
    (85, "highly_recommended"),
    (65, "recommended"),
    (45, "possible"),
    (0, "not_recommended"),
)


def duration_to_days(duration: dict | None) -> float | None:
    if not isinstance(duration, dict):
        return None
    value, unit = duration.get("value"), duration.get("unit")
    multiplier = {"days": 1, "weeks": 7, "months": 30}.get(unit)
    if value is None or multiplier is None:
        return None
    try:
        return float(value) * multiplier
    except (TypeError, ValueError):
        return None


def _score_lower_is_better(value: float | None, thresholds: list[tuple[float, int]], max_points: int) -> tuple[int, str]:
    """thresholds: lista ASCENDENTE de (limite_superior, puntos)."""
    if value is None:
        return round(max_points * _NEUTRAL_RATIO), "unknown"
    for limit, points in thresholds:
        if value <= limit:
            return points, "known"
    return thresholds[-1][1], "known"


def _score_higher_is_better(value: float | None, thresholds: list[tuple[float, int]], max_points: int) -> tuple[int, str]:
    """thresholds: lista DESCENDENTE de (limite_inferior, puntos)."""
    if value is None:
        return round(max_points * _NEUTRAL_RATIO), "unknown"
    for limit, points in thresholds:
        if value >= limit:
            return points, "known"
    return thresholds[-1][1], "known"


def score_inicio(waiting_time_to_start: dict | None) -> tuple[int, str]:
    days = duration_to_days(waiting_time_to_start)
    return _score_lower_is_better(
        days, [(7, 25), (14, 20), (30, 15), (60, 8), (math.inf, 3)], COMPONENT_MAX["inicio"]
    )


def score_frecuencia(practices_per_week_min: float | None, practices_per_week_max: float | None) -> tuple[int, str]:
    values = [v for v in (practices_per_week_min, practices_per_week_max) if v is not None]
    freq = sum(values) / len(values) if values else None
    return _score_higher_is_better(
        freq, [(5, 25), (4, 20), (3, 15), (2, 8), (0, 3)], COMPONENT_MAX["frecuencia"]
    )


def score_tiempo_examen(estimated_time_to_exam: dict | None) -> tuple[int, str]:
    days = duration_to_days(estimated_time_to_exam)
    return _score_lower_is_better(
        days, [(30, 20), (60, 15), (90, 10), (math.inf, 5)], COMPONENT_MAX["tiempo_examen"]
    )


def score_precio(practice_price: float | None) -> tuple[int, str]:
    return _score_lower_is_better(
        practice_price, [(25, 15), (30, 12), (35, 9), (40, 6), (math.inf, 3)], COMPONENT_MAX["precio"]
    )


def score_disponibilidad(
    component_statuses: list[str],
    information_is_uncertain: bool | None,
    follow_up_needed: bool | None,
) -> tuple[int, str]:
    max_points = COMPONENT_MAX["disponibilidad"]
    points = max_points
    unknown_count = component_statuses.count("unknown")
    points -= unknown_count * _UNKNOWN_PENALTY_PER_COMPONENT
    if information_is_uncertain:
        points -= _UNCERTAIN_PENALTY
    if follow_up_needed:
        points -= _FOLLOW_UP_PENALTY
    points = max(0, points)
    status = "known" if unknown_count == 0 and not information_is_uncertain else "partial"
    return points, status


def verdict_for_score(score: int) -> str:
    for limit, verdict in VERDICT_THRESHOLDS:
        if score >= limit:
            return verdict
    return "not_recommended"


def compute_score(fields: dict) -> dict:
    """fields: dict field_name -> valor, tal como se guardan en FieldValue.

    Devuelve {"score": int, "verdict": str, "breakdown": {componente: {...}}}.
    """
    inicio_pts, inicio_status = score_inicio(fields.get("waiting_time_to_start"))
    frecuencia_pts, frecuencia_status = score_frecuencia(
        fields.get("practices_per_week_min"), fields.get("practices_per_week_max")
    )
    tiempo_pts, tiempo_status = score_tiempo_examen(fields.get("estimated_time_to_exam"))
    precio_pts, precio_status = score_precio(fields.get("practice_price"))
    disponibilidad_pts, disponibilidad_status = score_disponibilidad(
        [inicio_status, frecuencia_status, tiempo_status, precio_status],
        fields.get("information_is_uncertain"),
        fields.get("follow_up_needed"),
    )

    components = {
        "inicio": (inicio_pts, inicio_status),
        "frecuencia": (frecuencia_pts, frecuencia_status),
        "tiempo_examen": (tiempo_pts, tiempo_status),
        "precio": (precio_pts, precio_status),
        "disponibilidad": (disponibilidad_pts, disponibilidad_status),
    }

    breakdown = {
        key: {
            "label": COMPONENT_LABELS[key],
            "points": points,
            "max": COMPONENT_MAX[key],
            "status": status,
        }
        for key, (points, status) in components.items()
    }

    total = max(0, min(100, sum(points for points, _ in components.values())))

    return {"score": total, "verdict": verdict_for_score(total), "breakdown": breakdown}
