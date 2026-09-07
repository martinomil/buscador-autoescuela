from __future__ import annotations

import datetime as dt

import pytest

from app.llm.pipeline import (
    ProcessingError,
    escalate_ambiguous,
    process_email_message,
    process_unprocessed_replies,
)
from app.models import Autoescuela, EmailMessage, ExtractionResult, FieldValue
from app.repository import set_field_value_manual

FULL_EXTRACTION = {
    "waiting_time_to_start": None,
    "earliest_start_date": None,
    "practice_price": 32,
    "practice_duration_minutes": 45,
    "practices_per_week_min": 4,
    "practices_per_week_max": 4,
    "estimated_time_to_exam": None,
    "earliest_exam_date": None,
    "enrollment_fee": None,
    "exam_fee": None,
    "other_fees": None,
    "availability": None,
    "requirements": None,
    "relevant_conditions": None,
    "accepts_already_passed_theory": None,
    "information_is_uncertain": False,
    "follow_up_needed": False,
    "missing_info": [],
    "notes": "",
}


def _make_contacted_autoescuela(session, status="replied") -> Autoescuela:
    a = Autoescuela(name="Autoescuela Test", email="autoescuela@example.com", status=status)
    session.add(a)
    session.commit()

    session.add(EmailMessage(
        autoescuela_id=a.id, direction="outbound", kind="initial",
        body_text="¿Cuanto es la lista de espera? ¿Precio y duracion de cada practica?",
    ))
    session.commit()
    return a


def _make_inbound_reply(session, autoescuela_id, body="Cobramos 32€ la practica de 45 min, hasta 4 por semana.", processed=False) -> EmailMessage:
    m = EmailMessage(
        autoescuela_id=autoescuela_id, direction="inbound", body_text=body,
        gmail_message_id=f"m-{autoescuela_id}-{body[:5]}", processed=processed,
    )
    session.add(m)
    session.commit()
    return m


@pytest.fixture()
def fake_extract(monkeypatch):
    calls = []

    def _fake(question_text, reply_text, model=None):
        calls.append({"question_text": question_text, "reply_text": reply_text, "model": model})
        return dict(FULL_EXTRACTION)

    monkeypatch.setattr("app.llm.pipeline.extract_reply", _fake)
    return calls


def test_process_email_message_stores_result_and_field_values(session, fake_extract):
    a = _make_contacted_autoescuela(session)
    reply = _make_inbound_reply(session, a.id)

    result = process_email_message(session, reply, model="fake-model")
    session.commit()

    assert isinstance(result, ExtractionResult)
    assert result.model_used == "fake-model"
    assert result.follow_up_needed is False

    field_values = {fv.field_name: fv.value for fv in session.query(FieldValue).filter_by(autoescuela_id=a.id).all()}
    assert field_values["practice_price"] == 32
    assert field_values["practice_duration_minutes"] == 45
    assert field_values["practices_per_week_min"] == 4
    assert field_values["notes"] == ""
    assert field_values["follow_up_needed"] is False
    # Los campos que el LLM no menciono (None) no generan FieldValue.
    assert "waiting_time_to_start" not in field_values

    assert reply.processed is True
    # Se paso el texto de la pregunta original al extractor.
    assert "lista de espera" in fake_extract[0]["question_text"]


def test_process_email_message_never_overwrites_manual_correction(session, fake_extract):
    a = _make_contacted_autoescuela(session)
    set_field_value_manual(session, a.id, "practice_price", 999, updated_by="martin")
    session.commit()

    reply = _make_inbound_reply(session, a.id)
    process_email_message(session, reply, model="fake-model")
    session.commit()

    fv = session.query(FieldValue).filter_by(autoescuela_id=a.id, field_name="practice_price").one()
    assert fv.value == 999
    assert fv.source == "manual"


def test_process_email_message_sets_follow_up_needed_status(session, monkeypatch):
    a = _make_contacted_autoescuela(session, status="replied")
    reply = _make_inbound_reply(session, a.id)

    ambiguous_extraction = dict(FULL_EXTRACTION)
    ambiguous_extraction["follow_up_needed"] = True
    ambiguous_extraction["missing_info"] = ["precio de la matricula"]
    monkeypatch.setattr("app.llm.pipeline.extract_reply", lambda q, r, model=None: ambiguous_extraction)

    process_email_message(session, reply)
    session.commit()

    assert a.status == "follow_up_needed"


def test_process_email_message_does_not_override_advanced_status(session, monkeypatch):
    a = _make_contacted_autoescuela(session, status="interested")
    reply = _make_inbound_reply(session, a.id)

    ambiguous_extraction = dict(FULL_EXTRACTION)
    ambiguous_extraction["follow_up_needed"] = True
    monkeypatch.setattr("app.llm.pipeline.extract_reply", lambda q, r, model=None: ambiguous_extraction)

    process_email_message(session, reply)
    session.commit()

    assert a.status == "interested"


def test_process_email_message_raises_for_outbound_message(session):
    a = _make_contacted_autoescuela(session)
    outbound = session.query(EmailMessage).filter_by(autoescuela_id=a.id, direction="outbound").one()

    with pytest.raises(ProcessingError):
        process_email_message(session, outbound)


def test_process_email_message_raises_for_unassociated_message(session, fake_extract):
    unmatched = EmailMessage(autoescuela_id=None, direction="inbound", body_text="hola")
    session.add(unmatched)
    session.commit()

    with pytest.raises(ProcessingError):
        process_email_message(session, unmatched)


def test_process_unprocessed_replies_skips_processed_and_unmatched(session, fake_extract):
    a = _make_contacted_autoescuela(session)
    pending1 = _make_inbound_reply(session, a.id, body="Respuesta 1")
    pending2 = _make_inbound_reply(session, a.id, body="Respuesta 2")
    already_done = _make_inbound_reply(session, a.id, body="Ya procesado", processed=True)
    unmatched = EmailMessage(autoescuela_id=None, direction="inbound", body_text="sin asociar")
    session.add(unmatched)
    session.commit()

    result = process_unprocessed_replies(session)

    assert len(result["processed"]) == 2
    assert result["errors"] == []
    assert pending1.processed is True
    assert pending2.processed is True
    assert already_done.processed is True  # ya lo estaba, no se toca
    assert unmatched.processed is False  # nunca se proceso


def test_escalate_ambiguous_reprocesses_only_flagged_replies(session, monkeypatch):
    a = _make_contacted_autoescuela(session)
    reply = _make_inbound_reply(session, a.id, processed=True)

    cheap_result = ExtractionResult(
        email_message_id=reply.id, autoescuela_id=a.id, raw_output=FULL_EXTRACTION,
        model_used="cheap-model", follow_up_needed=True,
    )
    session.add(cheap_result)
    session.commit()

    smart_extraction = dict(FULL_EXTRACTION)
    smart_extraction["follow_up_needed"] = False
    smart_extraction["practice_price"] = 35
    monkeypatch.setattr("app.llm.pipeline.extract_reply", lambda q, r, model=None: smart_extraction)

    result = escalate_ambiguous(session, model="smart-model")

    assert len(result["processed"]) == 1
    assert result["processed"][0].model_used == "smart-model"
    assert result["processed"][0].follow_up_needed is False

    fv = session.query(FieldValue).filter_by(autoescuela_id=a.id, field_name="practice_price").one()
    assert fv.value == 35

    # Segunda ejecucion: ya no deberia haber candidatos (ya se escalo con ese modelo).
    result2 = escalate_ambiguous(session, model="smart-model")
    assert result2["processed"] == []
