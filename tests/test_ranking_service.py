from __future__ import annotations

import datetime as dt

import pytest

from app.models import Autoescuela, Evaluation, FieldValue
from app.ranking_service import (
    RankingError,
    build_ranking_rows,
    evaluate_all,
    evaluate_autoescuela,
    needs_reevaluation,
    sort_ranking_rows,
)

FAKE_NARRATIVE = {
    "reasoning": "Buena opcion segun tus prioridades.",
    "pros": ["Inicio rapido"],
    "cons": [],
    "risks": [],
}


@pytest.fixture()
def fake_llm_evaluate(monkeypatch):
    calls = []

    def _fake(fields, score_result, model=None):
        calls.append({"fields": fields, "score_result": score_result, "model": model})
        return dict(FAKE_NARRATIVE)

    monkeypatch.setattr("app.ranking_service.llm_evaluate", _fake)
    return calls


def _make_autoescuela(session, **overrides) -> Autoescuela:
    defaults = dict(name="Autoescuela Test", email="autoescuela@example.com", city="A Coruña")
    defaults.update(overrides)
    a = Autoescuela(**defaults)
    session.add(a)
    session.commit()
    return a


def _set_field(session, autoescuela_id, field_name, value):
    fv = FieldValue(autoescuela_id=autoescuela_id, field_name=field_name, value=value, source="ai")
    session.add(fv)
    session.commit()
    return fv


def test_evaluate_autoescuela_requires_existing_autoescuela(session, fake_llm_evaluate):
    with pytest.raises(RankingError, match="No existe"):
        evaluate_autoescuela(session, 999)


def test_evaluate_autoescuela_requires_field_data(session, fake_llm_evaluate):
    a = _make_autoescuela(session)
    with pytest.raises(RankingError, match="no tiene datos"):
        evaluate_autoescuela(session, a.id)


def test_evaluate_autoescuela_computes_deterministic_score_and_llm_narrative(session, fake_llm_evaluate):
    a = _make_autoescuela(session)
    _set_field(session, a.id, "practice_price", 22)
    _set_field(session, a.id, "practices_per_week_min", 5)
    _set_field(session, a.id, "practices_per_week_max", 5)
    _set_field(session, a.id, "waiting_time_to_start", {"value": 3, "unit": "days"})

    evaluation = evaluate_autoescuela(session, a.id)
    session.commit()

    assert evaluation.score is not None
    assert evaluation.verdict is not None
    assert evaluation.reasoning == FAKE_NARRATIVE["reasoning"]
    assert evaluation.pros == ["Inicio rapido"]
    assert "inicio" in evaluation.breakdown

    assert len(fake_llm_evaluate) == 1
    assert fake_llm_evaluate[0]["fields"]["practice_price"] == 22
    assert fake_llm_evaluate[0]["score_result"]["score"] == evaluation.score


def test_needs_reevaluation_true_when_never_evaluated(session):
    a = _make_autoescuela(session)
    _set_field(session, a.id, "practice_price", 30)
    assert needs_reevaluation(session, a.id) is True


def test_needs_reevaluation_false_when_no_changes_since_last_eval(session, fake_llm_evaluate):
    a = _make_autoescuela(session)
    _set_field(session, a.id, "practice_price", 30)
    evaluate_autoescuela(session, a.id)
    session.commit()

    assert needs_reevaluation(session, a.id) is False


def test_needs_reevaluation_true_after_field_updated(session, fake_llm_evaluate):
    a = _make_autoescuela(session)
    fv = _set_field(session, a.id, "practice_price", 30)
    evaluate_autoescuela(session, a.id)
    session.commit()

    # Simula una correccion manual posterior.
    fv.updated_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)
    fv.value = 25
    session.commit()

    assert needs_reevaluation(session, a.id) is True


def test_evaluate_all_skips_autoescuelas_without_data_and_unchanged(session, fake_llm_evaluate):
    with_data = _make_autoescuela(session, email="a1@example.com")
    _set_field(session, with_data.id, "practice_price", 30)

    without_data = _make_autoescuela(session, email="a2@example.com")

    result = evaluate_all(session)
    assert with_data in result["evaluated"]
    assert without_data in result["no_data"]
    assert len(fake_llm_evaluate) == 1

    # Segunda pasada sin cambios: no debe volver a llamar al LLM.
    result2 = evaluate_all(session)
    assert with_data in result2["skipped"]
    assert len(fake_llm_evaluate) == 1

    # Con force=True, se reevalua igualmente.
    evaluate_all(session, force=True)
    assert len(fake_llm_evaluate) == 2


def test_build_ranking_rows_includes_score_and_raw_fields(session, fake_llm_evaluate):
    a = _make_autoescuela(session, city="Santiago de Compostela")
    _set_field(session, a.id, "practice_price", 25)
    _set_field(session, a.id, "practices_per_week_min", 3)
    _set_field(session, a.id, "practices_per_week_max", 4)
    evaluate_autoescuela(session, a.id)
    session.commit()

    rows = build_ranking_rows(session)
    row = next(r for r in rows if r["id"] == a.id)

    assert row["score"] is not None
    assert row["practice_price"] == 25
    assert row["practices_per_week_min"] == 3
    assert row["city"] == "Santiago de Compostela"


def test_build_ranking_rows_unscored_autoescuela_has_none_score(session):
    a = _make_autoescuela(session, email="sinscore@example.com")
    rows = build_ranking_rows(session)
    row = next(r for r in rows if r["id"] == a.id)
    assert row["score"] is None
    assert row["verdict"] is None


def test_sort_ranking_rows_by_score_descending_puts_unknown_last():
    rows = [
        {"score": 50, "_sort_inicio_days": None, "practice_price": None, "_sort_frecuencia": None, "_sort_examen_days": None, "city": "B", "name": "Y"},
        {"score": None, "_sort_inicio_days": None, "practice_price": None, "_sort_frecuencia": None, "_sort_examen_days": None, "city": "A", "name": "X"},
        {"score": 90, "_sort_inicio_days": None, "practice_price": None, "_sort_frecuencia": None, "_sort_examen_days": None, "city": "C", "name": "Z"},
    ]
    sorted_rows = sort_ranking_rows(rows, "score")
    assert [r["score"] for r in sorted_rows] == [90, 50, None]


def test_sort_ranking_rows_by_precio_puts_unknown_last():
    rows = [
        {"score": None, "practice_price": 30, "_sort_inicio_days": None, "_sort_frecuencia": None, "_sort_examen_days": None, "city": "", "name": ""},
        {"score": None, "practice_price": None, "_sort_inicio_days": None, "_sort_frecuencia": None, "_sort_examen_days": None, "city": "", "name": ""},
        {"score": None, "practice_price": 20, "_sort_inicio_days": None, "_sort_frecuencia": None, "_sort_examen_days": None, "city": "", "name": ""},
    ]
    sorted_rows = sort_ranking_rows(rows, "precio")
    assert [r["practice_price"] for r in sorted_rows] == [20, 30, None]


def test_sort_ranking_rows_rejects_unknown_criterion():
    with pytest.raises(RankingError):
        sort_ranking_rows([], "no_existe")
