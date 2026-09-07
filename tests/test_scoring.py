from __future__ import annotations

from app.scoring import (
    compute_score,
    score_disponibilidad,
    score_frecuencia,
    score_inicio,
    score_precio,
    score_tiempo_examen,
    verdict_for_score,
)


def test_score_inicio_fast_start_gets_max_points():
    points, status = score_inicio({"value": 3, "unit": "days"})
    assert points == 25
    assert status == "known"


def test_score_inicio_converts_weeks_and_months():
    points_weeks, _ = score_inicio({"value": 2, "unit": "weeks"})  # 14 dias
    assert points_weeks == 20
    points_months, _ = score_inicio({"value": 2, "unit": "months"})  # 60 dias
    assert points_months == 8


def test_score_inicio_unknown_is_neutral_not_zero():
    points, status = score_inicio(None)
    assert status == "unknown"
    assert 0 < points < 25  # ni maximo ni cero


def test_score_frecuencia_averages_range():
    points, status = score_frecuencia(3, 5)  # media 4
    assert points == 20
    assert status == "known"


def test_score_frecuencia_uses_single_value_if_only_one_present():
    points, status = score_frecuencia(None, 5)
    assert points == 25
    assert status == "known"


def test_score_frecuencia_unknown_when_neither_present():
    points, status = score_frecuencia(None, None)
    assert status == "unknown"


def test_score_tiempo_examen_thresholds():
    fast, _ = score_tiempo_examen({"value": 20, "unit": "days"})
    slow, _ = score_tiempo_examen({"value": 4, "unit": "months"})
    assert fast == 20
    assert slow == 5


def test_score_precio_cheaper_scores_higher():
    cheap, _ = score_precio(20)
    expensive, _ = score_precio(45)
    assert cheap == 15
    assert expensive == 3
    assert cheap > expensive


def test_score_disponibilidad_penalizes_unknowns_and_uncertainty():
    full, status_full = score_disponibilidad(["known", "known", "known", "known"], False, False)
    assert full == 15
    assert status_full == "known"

    partial, status_partial = score_disponibilidad(["known", "unknown", "known", "known"], False, False)
    assert partial == 12
    assert status_partial == "partial"

    uncertain, _ = score_disponibilidad(["known", "known", "known", "known"], True, False)
    assert uncertain == 12

    never_negative, _ = score_disponibilidad(["unknown"] * 4, True, True)
    assert never_negative >= 0


def test_verdict_thresholds():
    assert verdict_for_score(95) == "highly_recommended"
    assert verdict_for_score(85) == "highly_recommended"
    assert verdict_for_score(70) == "recommended"
    assert verdict_for_score(50) == "possible"
    assert verdict_for_score(10) == "not_recommended"


def test_compute_score_all_data_known_scores_high():
    fields = {
        "waiting_time_to_start": {"value": 3, "unit": "days"},
        "practices_per_week_min": 5,
        "practices_per_week_max": 5,
        "estimated_time_to_exam": {"value": 3, "unit": "weeks"},
        "practice_price": 22,
        "information_is_uncertain": False,
        "follow_up_needed": False,
    }
    result = compute_score(fields)
    assert result["score"] == 100
    assert result["verdict"] == "highly_recommended"
    assert result["breakdown"]["inicio"]["status"] == "known"
    assert result["breakdown"]["disponibilidad"]["points"] == 15


def test_compute_score_all_unknown_is_not_automatically_bad():
    result = compute_score({})
    # Ningun dato conocido no debe interpretarse como "muy malo": queda en
    # zona intermedia, marcado como incierto, no como reprobado.
    assert 40 <= result["score"] <= 65
    for component in result["breakdown"].values():
        if component["label"] != "Confianza / disponibilidad":
            assert component["status"] == "unknown"


def test_compute_score_partial_data_matches_readme_example_shape():
    # Ejemplo similar al del README: buen inicio/frecuencia/examen, precio
    # razonable pero no minimo, algo de incertidumbre en disponibilidad.
    fields = {
        "waiting_time_to_start": {"value": 1, "unit": "weeks"},
        "practices_per_week_min": 5,
        "practices_per_week_max": 5,
        "estimated_time_to_exam": {"value": 3, "unit": "weeks"},
        "practice_price": 28,
        "information_is_uncertain": True,
    }
    result = compute_score(fields)
    breakdown = result["breakdown"]
    assert breakdown["inicio"]["points"] == 25
    assert breakdown["frecuencia"]["points"] == 25
    assert breakdown["tiempo_examen"]["points"] == 20
    assert breakdown["precio"]["points"] == 12
    assert breakdown["disponibilidad"]["points"] == 12
    assert result["score"] == 94
    assert result["verdict"] == "highly_recommended"


def test_compute_score_never_exceeds_100_or_goes_negative():
    great = compute_score({
        "waiting_time_to_start": {"value": 1, "unit": "days"},
        "practices_per_week_min": 6,
        "practices_per_week_max": 7,
        "estimated_time_to_exam": {"value": 1, "unit": "weeks"},
        "practice_price": 10,
    })
    assert 0 <= great["score"] <= 100

    terrible = compute_score({
        "waiting_time_to_start": {"value": 6, "unit": "months"},
        "practices_per_week_min": 1,
        "practices_per_week_max": 1,
        "estimated_time_to_exam": {"value": 6, "unit": "months"},
        "practice_price": 60,
        "information_is_uncertain": True,
        "follow_up_needed": True,
    })
    assert 0 <= terrible["score"] <= 100
