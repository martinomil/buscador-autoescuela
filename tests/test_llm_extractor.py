from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.extractor import (
    EXTRACTION_FIELDS,
    ExtractionError,
    build_user_message,
    extract_reply,
)


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


class FakeAnthropicClientNoToolUse:
    class _Messages:
        def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="no deberia pasar esto")])

    def __init__(self):
        self.messages = self._Messages()


def test_build_user_message_includes_question_and_reply():
    msg = build_user_message("¿Cuanto es la lista de espera?", "Unas 3 semanas.")
    assert "¿Cuanto es la lista de espera?" in msg
    assert "Unas 3 semanas." in msg


def test_extract_reply_only_returns_mentioned_fields_never_invents():
    response_input = {
        "practice_price": 32,
        "practice_duration_minutes": 45,
        "practices_per_week_min": 4,
        "practices_per_week_max": 4,
        "follow_up_needed": False,
        "missing_info": [],
        "notes": "",
    }
    client = FakeAnthropicClient(response_input)

    result = extract_reply("preguntas...", "Cobramos 32€ la práctica de 45 min, hasta 4 por semana.", model="fake-model", client=client)

    assert result["practice_price"] == 32
    assert result["practice_duration_minutes"] == 45
    assert result["practices_per_week_min"] == 4
    assert result["practices_per_week_max"] == 4
    # Campos no mencionados por el modelo deben venir como None, nunca inventados.
    for field in EXTRACTION_FIELDS:
        if field not in response_input:
            assert result[field] is None, f"{field} deberia ser None"
    assert result["follow_up_needed"] is False
    assert result["missing_info"] == []
    assert result["notes"] == ""


def test_extract_reply_preserves_uncertainty_flags_and_notes():
    response_input = {
        "waiting_time_to_start": {"value": 3, "unit": "weeks"},
        "information_is_uncertain": True,
        "follow_up_needed": True,
        "missing_info": ["precio de la matricula"],
        "notes": "Indica que la espera es aproximada.",
    }
    client = FakeAnthropicClient(response_input)

    result = extract_reply("preguntas...", "Unas 3 semanas mas o menos.", client=client)

    assert result["waiting_time_to_start"] == {"value": 3, "unit": "weeks"}
    assert result["information_is_uncertain"] is True
    assert result["follow_up_needed"] is True
    assert result["missing_info"] == ["precio de la matricula"]
    assert "aproximada" in result["notes"]


def test_extract_reply_sends_expected_model_and_tool_choice():
    client = FakeAnthropicClient({"follow_up_needed": False, "missing_info": [], "notes": ""})

    extract_reply("preguntas", "respuesta", model="claude-haiku-test", client=client)

    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-test"
    assert call["tool_choice"] == {"type": "tool", "name": "extract_autoescuela_reply"}
    assert call["tools"][0]["name"] == "extract_autoescuela_reply"


def test_extract_reply_raises_if_no_tool_use_block_returned():
    with pytest.raises(ExtractionError):
        extract_reply("preguntas", "respuesta", client=FakeAnthropicClientNoToolUse())


def test_extract_reply_raises_without_api_key_and_no_client(monkeypatch):
    monkeypatch.setattr("app.llm.extractor.config.ANTHROPIC_API_KEY", "")
    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        extract_reply("preguntas", "respuesta")
