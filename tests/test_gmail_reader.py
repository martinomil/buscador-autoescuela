from __future__ import annotations

import base64
import datetime as dt

from app.gmail_reader import (
    build_search_query,
    check_new_replies,
    html_to_text,
    parse_gmail_message,
)
from app.models import Autoescuela, EmailMessage


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _plain_message(msg_id, thread_id, sender, subject, body, internal_date="1700000000000"):
    return {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "usuario@ejemplo.com"},
                {"name": "Subject", "value": subject},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
        },
    }


def _html_message(msg_id, thread_id, sender, subject, html_body, internal_date="1700000000000"):
    return {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "usuario@ejemplo.com"},
                {"name": "Subject", "value": subject},
            ],
            "mimeType": "text/html",
            "body": {"data": _b64(html_body)},
        },
    }


class _FakeExecutable:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeMessages:
    def __init__(self, list_response, get_responses):
        self._list_response = list_response
        self._get_responses = get_responses
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _FakeExecutable(self._list_response)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _FakeExecutable(self._get_responses[kwargs["id"]])


class FakeGmailService:
    def __init__(self, message_ids: list[str], raw_messages: dict[str, dict]):
        self.messages_api = _FakeMessages(
            list_response={"messages": [{"id": mid} for mid in message_ids]},
            get_responses=raw_messages,
        )

    def users(self):
        return self

    def messages(self):
        return self.messages_api


def _make_autoescuela(session, **overrides) -> Autoescuela:
    defaults = dict(
        name="Autoescuela Test",
        city="A Coruña",
        email="autoescuela@example.com",
        status="email_sent",
        first_contact_date=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc),
    )
    defaults.update(overrides)
    a = Autoescuela(**defaults)
    session.add(a)
    session.commit()
    return a


def test_parse_gmail_message_plain_text():
    raw = _plain_message("m1", "t1", "Autoescuela X <x@example.com>", "Re: consulta", "Tenemos 3 semanas de espera.")
    parsed = parse_gmail_message(raw)

    assert parsed["gmail_message_id"] == "m1"
    assert parsed["gmail_thread_id"] == "t1"
    assert parsed["sender"] == "Autoescuela X <x@example.com>"
    assert parsed["subject"] == "Re: consulta"
    assert parsed["body_text"] == "Tenemos 3 semanas de espera."
    assert parsed["body_html"] is None
    assert parsed["timestamp"] == dt.datetime.fromtimestamp(1700000000, tz=dt.timezone.utc)


def test_parse_gmail_message_html_only_falls_back_to_text():
    html = "<html><body><p>Hola,</p><p>Tenemos <b>3 semanas</b> de espera.</p></body></html>"
    raw = _html_message("m2", "t2", "x@example.com", "Re: consulta", html)
    parsed = parse_gmail_message(raw)

    assert parsed["body_html"] == html
    assert "Hola," in parsed["body_text"]
    assert "3 semanas" in parsed["body_text"]
    assert "<b>" not in parsed["body_text"]


def test_html_to_text_strips_tags_and_blank_lines():
    html = "<div>Linea 1</div><div></div><div>Linea 2</div>"
    text = html_to_text(html)
    assert text == "Linea 1\nLinea 2"


def test_build_search_query_none_when_nobody_contacted(session):
    assert build_search_query(session) is None


def test_build_search_query_uses_earliest_contact_date(session):
    _make_autoescuela(session, email="a@example.com", first_contact_date=dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc))
    _make_autoescuela(session, email="b@example.com", first_contact_date=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc))

    query = build_search_query(session)
    assert query == "in:inbox -in:sent after:2026/08/31"


def test_check_new_replies_associates_by_thread(session):
    a = _make_autoescuela(session)
    session.add(EmailMessage(
        autoescuela_id=a.id, direction="outbound", kind="initial",
        gmail_thread_id="t1", gmail_message_id="sent1",
    ))
    session.commit()

    raw = _plain_message("m1", "t1", "otra_direccion@example.com", "Re: consulta", "3 semanas de espera.")
    service = FakeGmailService(["m1"], {"m1": raw})

    result = check_new_replies(session, service)

    assert len(result["new"]) == 1
    assert result["unmatched"] == []
    stored = result["new"][0]
    assert stored.autoescuela_id == a.id
    assert stored.direction == "inbound"
    assert stored.body_text == "3 semanas de espera."

    session.refresh(a)
    assert a.status == "replied"
    assert a.replies_count == 1
    assert a.last_reply_date is not None


def test_check_new_replies_falls_back_to_sender_email_match(session):
    a = _make_autoescuela(session, email="autoescuela@example.com")
    # Sin ningun thread_id conocido para esta autoescuela.
    raw = _plain_message("m1", "thread_desconocido", "Autoescuela Test <autoescuela@example.com>", "Info", "Cuerpo")
    service = FakeGmailService(["m1"], {"m1": raw})

    result = check_new_replies(session, service)

    assert len(result["new"]) == 1
    assert result["new"][0].autoescuela_id == a.id


def test_check_new_replies_keeps_unmatched_messages_without_losing_them(session):
    _make_autoescuela(session, email="autoescuela@example.com")
    raw = _plain_message("m1", "thread_desconocido", "desconocido@otralista.com", "Info", "Cuerpo importante")
    service = FakeGmailService(["m1"], {"m1": raw})

    result = check_new_replies(session, service)

    assert result["new"] == []
    assert len(result["unmatched"]) == 1
    stored = result["unmatched"][0]
    assert stored.autoescuela_id is None
    assert stored.body_text == "Cuerpo importante"

    # Sigue en la base de datos, no se ha perdido.
    assert session.query(EmailMessage).filter_by(gmail_message_id="m1").count() == 1


def test_check_new_replies_skips_already_known_messages(session):
    a = _make_autoescuela(session)
    session.add(EmailMessage(
        autoescuela_id=a.id, direction="inbound", gmail_message_id="m1", gmail_thread_id="t1",
    ))
    session.commit()

    raw = _plain_message("m1", "t1", "x@example.com", "Info", "Cuerpo")
    service = FakeGmailService(["m1"], {"m1": raw})

    result = check_new_replies(session, service)

    assert result["new"] == []
    assert result["unmatched"] == []
    assert len(service.messages_api.get_calls) == 0  # nunca se pidio el contenido completo


def test_check_new_replies_returns_early_when_nobody_contacted(session):
    service = FakeGmailService([], {})
    result = check_new_replies(session, service)
    assert result == {"new": [], "unmatched": [], "bounced": []}
    assert len(service.messages_api.list_calls) == 0
