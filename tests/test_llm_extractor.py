from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from app.llm.extractor import (
    EXTRACTION_FIELDS,
    ExtractionError,
    _coerce_field,
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

    result = extract_reply("preguntas...", "Cobramos 32€ la práctica de 45 min, hasta 4 por semana.", model="fake-model", client=client, provider="anthropic")

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

    result = extract_reply("preguntas...", "Unas 3 semanas mas o menos.", client=client, provider="anthropic")

    assert result["waiting_time_to_start"] == {"value": 3, "unit": "weeks"}
    assert result["information_is_uncertain"] is True
    assert result["follow_up_needed"] is True
    assert result["missing_info"] == ["precio de la matricula"]
    assert "aproximada" in result["notes"]


def test_extract_reply_sends_expected_model_and_tool_choice():
    client = FakeAnthropicClient({"follow_up_needed": False, "missing_info": [], "notes": ""})

    extract_reply("preguntas", "respuesta", model="claude-haiku-test", client=client, provider="anthropic")

    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-test"
    assert call["tool_choice"] == {"type": "tool", "name": "extract_autoescuela_reply"}
    assert call["tools"][0]["name"] == "extract_autoescuela_reply"


def test_extract_reply_raises_if_no_tool_use_block_returned():
    with pytest.raises(ExtractionError):
        extract_reply("preguntas", "respuesta", client=FakeAnthropicClientNoToolUse(), provider="anthropic")


def test_extract_reply_raises_without_api_key_and_no_client(monkeypatch):
    monkeypatch.setattr("app.llm.extractor.config.ANTHROPIC_API_KEY", "")
    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        extract_reply("preguntas", "respuesta", provider="anthropic")


# --- Proveedor Ollama (gratis, local) ---


class FakeOllamaResponse:
    def __init__(self, payload: dict, status_ok: bool = True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError("400 Bad Request")

    def json(self):
        return self._payload


def _ollama_payload(arguments) -> dict:
    return {"message": {"tool_calls": [{"function": {"name": "extract_autoescuela_reply", "arguments": arguments}}]}}


def test_extract_reply_with_ollama_provider(monkeypatch):
    arguments = {"practice_price": 32, "follow_up_needed": False, "missing_info": [], "notes": ""}

    def fake_post(url, json, timeout):
        assert "localhost:11434" in url
        assert json["model"] == "llama3.1"
        return FakeOllamaResponse(_ollama_payload(arguments))

    monkeypatch.setattr("app.llm.extractor.requests.post", fake_post)

    result = extract_reply("preguntas", "respuesta", provider="ollama", model="llama3.1")

    assert result["practice_price"] == 32
    assert result["follow_up_needed"] is False


def test_extract_reply_with_ollama_parses_string_arguments(monkeypatch):
    import json as json_module

    arguments_str = json_module.dumps({"follow_up_needed": True, "missing_info": ["precio"], "notes": ""})

    monkeypatch.setattr(
        "app.llm.extractor.requests.post",
        lambda url, json, timeout: FakeOllamaResponse(_ollama_payload(arguments_str)),
    )

    result = extract_reply("preguntas", "respuesta", provider="ollama")
    assert result["follow_up_needed"] is True
    assert result["missing_info"] == ["precio"]


def test_extract_reply_with_ollama_raises_when_no_tool_calls(monkeypatch):
    monkeypatch.setattr(
        "app.llm.extractor.requests.post",
        lambda url, json, timeout: FakeOllamaResponse({"message": {"content": "no tool call"}}),
    )
    with pytest.raises(ExtractionError, match="tool_calls"):
        extract_reply("preguntas", "respuesta", provider="ollama")


def test_extract_reply_with_ollama_raises_on_connection_error(monkeypatch):
    def fake_post(url, json, timeout):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("app.llm.extractor.requests.post", fake_post)

    with pytest.raises(ExtractionError, match="Ollama"):
        extract_reply("preguntas", "respuesta", provider="ollama")


def test_extract_reply_unknown_provider_raises():
    with pytest.raises(ExtractionError, match="LLM_PROVIDER"):
        extract_reply("preguntas", "respuesta", provider="chatgpt")


# --- Validacion defensiva de tipos (_coerce_field) ---


def test_coerce_field_accepts_valid_duration():
    assert _coerce_field("waiting_time_to_start", {"value": 3, "unit": "weeks"}) == {"value": 3, "unit": "weeks"}


def test_coerce_field_rejects_duration_missing_unit():
    assert _coerce_field("waiting_time_to_start", {"value": 3}) is None


def test_coerce_field_rejects_duration_with_invalid_unit():
    assert _coerce_field("waiting_time_to_start", {"value": 3, "unit": "lunas"}) is None


def test_coerce_field_unwraps_number_mistakenly_wrapped_in_object():
    # Un modelo local a veces envuelve un numero simple como {"value": 32}.
    assert _coerce_field("practice_price", {"value": 32}) == 32.0


def test_coerce_field_rejects_non_numeric_price():
    assert _coerce_field("practice_price", "treinta euros") is None


def test_coerce_field_rejects_dict_for_string_field():
    assert _coerce_field("availability", {"unexpected": "shape"}) is None


def test_coerce_field_rejects_non_boolean_for_boolean_field():
    assert _coerce_field("information_is_uncertain", "si") is None


def test_coerce_field_passes_through_none():
    assert _coerce_field("practice_price", None) is None
