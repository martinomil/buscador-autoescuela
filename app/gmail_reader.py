"""Deteccion, parseo y almacenamiento de respuestas nuevas via Gmail API.

Estrategia de asociacion respuesta -> autoescuela (en este orden):

1. Gmail thread: si el mensaje pertenece al mismo hilo que un email que
   enviamos nosotros, se asocia a esa autoescuela (la señal mas fiable).
2. Direccion del remitente: si el hilo no coincide (p. ej. la autoescuela
   rompio el hilo, reenvio el correo, o respondio desde otra cuenta), se
   compara la direccion del remitente con el email registrado de cada
   autoescuela.
3. Si ninguna de las dos coincide, el mensaje se guarda igualmente
   (autoescuela_id=None) para no perder nunca la respuesta original, y se
   deja constancia en el log para revision manual.

Para no procesar todo el historial de la bandeja de entrada, la busqueda en
Gmail se acota con "after:" a partir de la fecha de primer contacto mas
antigua, y los mensajes ya guardados (por gmail_message_id) se descartan
antes de pedir su contenido completo a la API.
"""
from __future__ import annotations

import base64
import datetime as dt
import logging
from email.utils import parseaddr

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Autoescuela, EmailMessage

logger = logging.getLogger(__name__)

# Estados desde los que una respuesta nueva SI actualiza el estado a
# "replied". Estados mas avanzados (interested/rejected/selected) no se
# pisan automaticamente: se asumen decisiones ya tomadas por el usuario.
STATUSES_UPDATED_ON_REPLY = {"not_contacted", "email_sent", "follow_up_needed", "replied"}


def _known_message_ids(session: Session) -> set[str]:
    stmt = select(EmailMessage.gmail_message_id).where(EmailMessage.gmail_message_id.is_not(None))
    return {mid for (mid,) in session.execute(stmt).all()}


def _thread_to_autoescuela(session: Session) -> dict[str, int]:
    stmt = select(EmailMessage.gmail_thread_id, EmailMessage.autoescuela_id).where(
        EmailMessage.gmail_thread_id.is_not(None),
        EmailMessage.autoescuela_id.is_not(None),
    )
    mapping: dict[str, int] = {}
    for thread_id, autoescuela_id in session.execute(stmt).all():
        mapping.setdefault(thread_id, autoescuela_id)
    return mapping


def _email_to_autoescuela(session: Session) -> dict[str, int]:
    stmt = select(Autoescuela.email, Autoescuela.id)
    return {email.lower(): autoescuela_id for email, autoescuela_id in session.execute(stmt).all()}


def _earliest_contact_date(session: Session) -> dt.datetime | None:
    stmt = select(Autoescuela.first_contact_date).where(Autoescuela.first_contact_date.is_not(None))
    dates = [d for (d,) in session.execute(stmt).all()]
    return min(dates) if dates else None


def build_search_query(session: Session) -> str | None:
    """Consulta de Gmail acotada a partir del primer contacto conocido.

    Devuelve None si todavia no se ha contactado a ninguna autoescuela (no
    hay nada relevante que buscar).
    """
    earliest = _earliest_contact_date(session)
    if earliest is None:
        return None
    since = (earliest - dt.timedelta(days=1)).strftime("%Y/%m/%d")
    return f"in:inbox -in:sent after:{since}"


def _list_message_ids(gmail_service, query: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        response = (
            gmail_service.users()
            .messages()
            .list(userId="me", q=query, maxResults=100, pageToken=page_token)
            .execute()
        )
        ids.extend(m["id"] for m in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return ids


def _get_header(headers: list[dict], name: str) -> str | None:
    name_lower = name.lower()
    for header in headers:
        if header.get("name", "").lower() == name_lower:
            return header.get("value")
    return None


def _decode_part_data(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_bodies(payload: dict) -> tuple[str | None, str | None]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            decoded = _decode_part_data(data)
            if mime_type == "text/plain":
                text_parts.append(decoded)
            elif mime_type == "text/html":
                html_parts.append(decoded)
        for sub_part in part.get("parts") or []:
            walk(sub_part)

    walk(payload)
    text = "\n".join(text_parts).strip() or None
    html = "\n".join(html_parts).strip() or None
    return text, html


def html_to_text(html: str) -> str:
    """Texto legible aproximado a partir de HTML (para respuestas sin parte texto/plano)."""
    soup = BeautifulSoup(html, "html.parser")
    raw_text = soup.get_text(separator="\n")
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return "\n".join(lines)


def parse_gmail_message(raw_message: dict) -> dict:
    """Convierte un mensaje crudo de la Gmail API en los campos que guardamos.

    Si el mensaje solo trae HTML (sin parte text/plain), se deriva un texto
    legible con html_to_text en vez de guardar el HTML crudo como cuerpo
    principal.
    """
    payload = raw_message.get("payload", {})
    headers = payload.get("headers", [])
    text, html = _extract_bodies(payload)
    if not text and html:
        text = html_to_text(html)

    timestamp = None
    internal_date = raw_message.get("internalDate")
    if internal_date:
        timestamp = dt.datetime.fromtimestamp(int(internal_date) / 1000, tz=dt.timezone.utc)

    return {
        "gmail_message_id": raw_message.get("id"),
        "gmail_thread_id": raw_message.get("threadId"),
        "sender": _get_header(headers, "From"),
        "recipient": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "body_text": text,
        "body_html": html,
        "timestamp": timestamp,
    }


def _extract_email_address(header_value: str | None) -> str | None:
    if not header_value:
        return None
    _, addr = parseaddr(header_value)
    return addr.lower() or None


def apply_reply_side_effects(autoescuela: Autoescuela, timestamp: dt.datetime | None) -> None:
    """Actualiza contador/fecha/estado de una autoescuela al recibir una respuesta.

    Compartido entre la asociacion automatica (check_new_replies) y la
    asignacion manual de un mensaje sin asociar (ver app.repository).
    """
    autoescuela.replies_count += 1
    if timestamp and (autoescuela.last_reply_date is None or timestamp > autoescuela.last_reply_date):
        autoescuela.last_reply_date = timestamp
    if autoescuela.status in STATUSES_UPDATED_ON_REPLY:
        autoescuela.status = "replied"


def check_new_replies(session: Session, gmail_service) -> dict:
    """Busca respuestas nuevas en Gmail y las guarda, asociadas cuando es posible.

    Devuelve {"new": [EmailMessage asociados], "unmatched": [EmailMessage sin asociar]}.
    No vuelve a descargar ni procesar un gmail_message_id ya guardado.
    """
    query = build_search_query(session)
    if query is None:
        logger.info("Todavia no se ha contactado a ninguna autoescuela; no hay nada que comprobar")
        return {"new": [], "unmatched": []}

    logger.info("Buscando respuestas nuevas en Gmail (query=%r)", query)
    known_ids = _known_message_ids(session)
    thread_map = _thread_to_autoescuela(session)
    email_map = _email_to_autoescuela(session)

    all_ids = _list_message_ids(gmail_service, query)
    candidate_ids = [mid for mid in all_ids if mid not in known_ids]
    logger.info("%s mensajes encontrados, %s nuevos (no procesados antes)", len(all_ids), len(candidate_ids))

    new_messages: list[EmailMessage] = []
    unmatched: list[EmailMessage] = []

    for message_id in candidate_ids:
        raw = gmail_service.users().messages().get(userId="me", id=message_id, format="full").execute()
        parsed = parse_gmail_message(raw)

        autoescuela_id = thread_map.get(parsed["gmail_thread_id"])
        if autoescuela_id is None:
            sender_addr = _extract_email_address(parsed["sender"])
            if sender_addr:
                autoescuela_id = email_map.get(sender_addr)

        email_message = EmailMessage(
            autoescuela_id=autoescuela_id,
            direction="inbound",
            processed=False,
            **parsed,
        )
        session.add(email_message)

        if autoescuela_id is not None:
            autoescuela = session.get(Autoescuela, autoescuela_id)
            apply_reply_side_effects(autoescuela, parsed["timestamp"])
            new_messages.append(email_message)
            logger.info(
                "Respuesta asociada a %s (autoescuela_id=%s, message_id=%s)",
                autoescuela.name,
                autoescuela_id,
                message_id,
            )
        else:
            unmatched.append(email_message)
            logger.warning(
                "No se pudo asociar automaticamente el mensaje %s (de %s, asunto %r). "
                "Guardado sin asociar para revision manual.",
                message_id,
                parsed["sender"],
                parsed["subject"],
            )

    session.flush()
    return {"new": new_messages, "unmatched": unmatched}
