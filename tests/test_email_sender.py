from __future__ import annotations

import itertools

import pytest

from app.email_sender import (
    DuplicateEmailError,
    has_sent_kind,
    preview_initial_email,
    send_batch,
    send_initial_email,
)
from app.models import Autoescuela, EmailMessage


class FakeGmailService:
    """Sustituye a la Gmail API real: no hace ninguna llamada de red."""


@pytest.fixture()
def fake_send_message(monkeypatch):
    counter = itertools.count(1)
    calls = []

    def _fake(service, to, subject, body_text):
        msg_id = f"msg{next(counter)}"
        calls.append({"to": to, "subject": subject, "body_text": body_text})
        return {"id": msg_id, "threadId": f"thread-{msg_id}"}

    monkeypatch.setattr("app.email_sender.send_message", _fake)
    return calls


def _make_autoescuela(session, **overrides) -> Autoescuela:
    defaults = dict(name="Autoescuela Test", city="A Coruña", email="autoescuela@example.com")
    defaults.update(overrides)
    a = Autoescuela(**defaults)
    session.add(a)
    session.commit()
    return a


def test_preview_does_not_touch_db_or_network(session, fake_send_message):
    a = _make_autoescuela(session)

    preview = preview_initial_email(a)

    assert preview["to"] == "autoescuela@example.com"
    assert "permiso B" in preview["subject"]
    assert fake_send_message == []  # no se ha llamado a Gmail
    assert session.query(EmailMessage).count() == 0


def test_send_initial_email_registers_message_and_updates_autoescuela(session, fake_send_message):
    a = _make_autoescuela(session)
    service = FakeGmailService()

    email_message = send_initial_email(session, service, a)
    session.commit()

    assert len(fake_send_message) == 1
    assert fake_send_message[0]["to"] == "autoescuela@example.com"

    assert email_message.autoescuela_id == a.id
    assert email_message.direction == "outbound"
    assert email_message.kind == "initial"
    assert email_message.gmail_message_id == "msg1"
    assert email_message.recipient == "autoescuela@example.com"

    assert a.status == "email_sent"
    assert a.emails_sent_count == 1
    assert a.first_contact_date is not None


def test_send_initial_email_blocks_accidental_duplicate(session, fake_send_message):
    a = _make_autoescuela(session)
    service = FakeGmailService()

    send_initial_email(session, service, a)
    session.commit()

    with pytest.raises(DuplicateEmailError):
        send_initial_email(session, service, a)

    # No se ha realizado una segunda llamada real de envio.
    assert len(fake_send_message) == 1
    assert session.query(EmailMessage).count() == 1


def test_send_initial_email_force_allows_resend(session, fake_send_message):
    a = _make_autoescuela(session)
    service = FakeGmailService()

    send_initial_email(session, service, a)
    session.commit()
    send_initial_email(session, service, a, force=True)
    session.commit()

    assert len(fake_send_message) == 2
    assert session.query(EmailMessage).count() == 2
    assert a.emails_sent_count == 2


def test_send_initial_email_override_to_redirects_delivery_but_keeps_association(session, fake_send_message):
    a = _make_autoescuela(session, email="autoescuela_real@example.com")
    service = FakeGmailService()

    email_message = send_initial_email(session, service, a, override_to="martin@example.com")
    session.commit()

    assert fake_send_message[0]["to"] == "martin@example.com"
    assert email_message.autoescuela_id == a.id
    assert email_message.recipient == "martin@example.com"


def test_has_sent_kind(session, fake_send_message):
    a = _make_autoescuela(session)
    service = FakeGmailService()

    assert has_sent_kind(session, a.id, "initial") is False
    send_initial_email(session, service, a)
    session.commit()
    assert has_sent_kind(session, a.id, "initial") is True


def test_send_batch_respects_max_per_run_and_skips_duplicates(session, fake_send_message):
    autoescuelas = [
        _make_autoescuela(session, name=f"Autoescuela {i}", email=f"autoescuela{i}@example.com")
        for i in range(3)
    ]
    service = FakeGmailService()

    # Una de ellas ya fue contactada antes.
    send_initial_email(session, service, autoescuelas[0])
    session.commit()

    result = send_batch(
        session, service, autoescuelas, delay_seconds=0, max_per_run=1,
    )

    assert autoescuelas[0] in result["skipped"]
    # max_per_run=1 limita a un solo envio nuevo aunque haya 2 pendientes
    assert len(result["sent"]) == 1
    assert result["errors"] == []


def test_send_batch_sends_all_pending_when_under_limit(session, fake_send_message):
    autoescuelas = [
        _make_autoescuela(session, name=f"Autoescuela {i}", email=f"autoescuela{i}@example.com")
        for i in range(3)
    ]
    service = FakeGmailService()

    result = send_batch(session, service, autoescuelas, delay_seconds=0, max_per_run=10)

    assert len(result["sent"]) == 3
    assert result["skipped"] == []
    assert result["errors"] == []
    assert session.query(EmailMessage).count() == 3
