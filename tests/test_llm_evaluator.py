from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from app.llm.evaluator import EvaluationError, build_user_message, evaluate


class FakeMessagesAPI:
    def __init__(self, response_input: dict):
        self.response_input = response_input
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", input=self.response_input)
        return SimpleNamespace(content=[block])


class FakeAnthropicClient:
    def __init__(self, response_input: dict):
        self.messages = FakeMessagesAPI(response_input)


SAMPLE_SCORE_RESULT = {
    "score": 91,
    "verdict": "highly_recommended",
    "breakdown": {
        "inicio": {"label": "Inicio de practicas", "points": 25, "max": 25, "status": "known"},
        "frecuencia": {"label": "Frecuencia de practicas", "points": 25, "max": 25, "status": "known"},
        "tiempo_examen": {"label": "Tiempo hasta examen", "points": 20, "max": 20, "status": "unknown"},
        "precio": {"label": "Precio", "points": 12, "max": 15, "status": "known"},
        "disponibilidad": {"label": "Confianza / disponibilidad", "points": 9, "max": 15, "status": "partial"},
    },
}

SAMPLE_FIELDS = {
    "waiting_time_to_start": {"value": 3, "unit": "days"},
    "practice_price": 28,
    "practices_per_week_min": 4,
    "practices_per_week_max": 5,
    "estimated_time_to_exam": None,
}


def test_build_user_message_includes_score_and_breakdown_and_skips_empty_fields():
    msg = build_user_message(SAMPLE_FIELDS, SAMPLE_SCORE_RESULT)
    assert "91/100" in msg
    assert "highly_recommended" in msg
    assert "Inicio de practicas" in msg
    assert "practice_price: 28" in msg
    # Un campo None no debe aparecer listado como si fuera un dato real.
    assert "estimated_time_to_exam" not in msg


def test_evaluate_returns_reasoning_pros_cons_risks():
    response_input = {
        "reasoning": "Buen inicio y frecuencia, aunque falta la fecha de examen.",
        "pros": ["Inicio rapido", "Alta frecuencia de practicas"],
        "cons": ["Precio algo por encima de la media"],
        "risks": ["No se conoce el tiempo hasta el examen"],
    }
    client = FakeAnthropicClient(response_input)

    result = evaluate(SAMPLE_FIELDS, SAMPLE_SCORE_RESULT, client=client, provider="anthropic")

    assert result["reasoning"].startswith("Buen inicio")
    assert result["pros"] == ["Inicio rapido", "Alta frecuencia de practicas"]
    assert result["cons"] == ["Precio algo por encima de la media"]
    assert result["risks"] == ["No se conoce el tiempo hasta el examen"]


def test_evaluate_defaults_missing_lists_to_empty():
    client = FakeAnthropicClient({"reasoning": "Ok."})
    result = evaluate(SAMPLE_FIELDS, SAMPLE_SCORE_RESULT, client=client, provider="anthropic")
    assert result["pros"] == []
    assert result["cons"] == []
    assert result["risks"] == []


def test_evaluate_raises_without_api_key(monkeypatch):
    monkeypatch.setattr("app.llm.evaluator.config.ANTHROPIC_API_KEY", "")
    with pytest.raises(EvaluationError, match="ANTHROPIC_API_KEY"):
        evaluate(SAMPLE_FIELDS, SAMPLE_SCORE_RESULT, provider="anthropic")


def test_evaluate_with_ollama_provider(monkeypatch):
    payload = {"message": {"tool_calls": [{"function": {"arguments": {
        "reasoning": "Buena opcion.", "pros": [], "cons": [], "risks": [],
    }}}]}}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr("app.llm.evaluator.requests.post", lambda url, json, timeout: FakeResponse())

    result = evaluate(SAMPLE_FIELDS, SAMPLE_SCORE_RESULT, provider="ollama")
    assert result["reasoning"] == "Buena opcion."


def test_evaluate_ollama_raises_on_connection_error(monkeypatch):
    def fake_post(url, json, timeout):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr("app.llm.evaluator.requests.post", fake_post)

    with pytest.raises(EvaluationError, match="Ollama"):
        evaluate(SAMPLE_FIELDS, SAMPLE_SCORE_RESULT, provider="ollama")


def test_evaluate_unknown_provider_raises():
    with pytest.raises(EvaluationError, match="LLM_PROVIDER"):
        evaluate(SAMPLE_FIELDS, SAMPLE_SCORE_RESULT, provider="chatgpt")
