from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser

from app.gmail_client import build_raw_message


def _decode(raw_b64url: str):
    raw_bytes = base64.urlsafe_b64decode(raw_b64url)
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def test_build_raw_message_roundtrip_basic():
    payload = build_raw_message("destino@example.com", "Asunto de prueba", "Cuerpo simple.")

    assert set(payload.keys()) == {"raw"}
    msg = _decode(payload["raw"])

    assert msg["To"] == "destino@example.com"
    assert msg["Subject"] == "Asunto de prueba"
    assert msg.get_content().strip() == "Cuerpo simple."


def test_build_raw_message_preserves_accented_characters():
    subject = "Consulta prácticas permiso B"
    body = "Buenos días, Martín.\n\n¿Cuándo podríais empezar?\n\nUn saludo,\nMartín"

    payload = build_raw_message("destino@example.com", subject, body)
    msg = _decode(payload["raw"])

    assert msg["Subject"] == subject
    assert msg.get_content().strip() == body
