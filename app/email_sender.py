"""Orquestacion del envio de emails: plantillas + Gmail + registro en BD.

Este modulo no llama nunca a la Gmail API "a lo loco": toda funcion que
envia de verdad requiere un `gmail_service` explicito (obtenido con
`app.gmail_client.get_gmail_service`), y comprueba antes si ya se envio un
email del mismo tipo a esa autoescuela para evitar duplicados accidentales.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.email_templates import EmailTemplate, load_template, render_template
from app.gmail_client import send_message
from app.models import Autoescuela, EmailMessage

logger = logging.getLogger(__name__)

DEFAULT_INITIAL_TEMPLATE = config.TEMPLATES_DIR / "email_inicial.txt"


class DuplicateEmailError(Exception):
    """Se intento reenviar un email del mismo tipo a la misma autoescuela."""


def autoescuela_context(autoescuela: Autoescuela) -> dict:
    """Variables disponibles para las plantillas ({{name}}, {{city}}...)."""
    return {
        "name": autoescuela.name,
        "city": autoescuela.city or "",
        "email": autoescuela.email,
    }


def has_sent_kind(session: Session, autoescuela_id: int, kind: str) -> bool:
    stmt = select(EmailMessage.id).where(
        EmailMessage.autoescuela_id == autoescuela_id,
        EmailMessage.direction == "outbound",
        EmailMessage.kind == kind,
    )
    return session.scalars(stmt).first() is not None


def render_for_autoescuela(template: EmailTemplate, autoescuela: Autoescuela) -> EmailTemplate:
    return render_template(template, autoescuela_context(autoescuela))


def preview_initial_email(autoescuela: Autoescuela, template_path=None) -> dict:
    """Renderiza el email sin enviarlo ni tocar la base de datos (modo dry-run)."""
    template = load_template(template_path or DEFAULT_INITIAL_TEMPLATE)
    rendered = render_for_autoescuela(template, autoescuela)
    return {"to": autoescuela.email, "subject": rendered.subject, "body": rendered.body}


def send_initial_email(
    session: Session,
    gmail_service,
    autoescuela: Autoescuela,
    template_path=None,
    override_to: str | None = None,
    force: bool = False,
) -> EmailMessage:
    """Envia (de verdad) el email inicial a una autoescuela y lo registra.

    Si `override_to` se indica, se trata de un envio de PRUEBA: el correo se
    manda a esa direccion (util para enviarte a ti mismo el contenido real
    que recibiria la autoescuela), se registra igualmente asociado a
    `autoescuela` (kind="test") para poder auditarlo, pero NO cuenta como
    contacto real: no bloquea futuros envios ni actualiza el estado/contador
    de la autoescuela.
    """
    is_test = override_to is not None
    kind = "test" if is_test else "initial"

    if not is_test and not force and has_sent_kind(session, autoescuela.id, "initial"):
        raise DuplicateEmailError(
            f"Ya se envio un email inicial a {autoescuela.name!r} (id={autoescuela.id}). "
            "Usa force=True si realmente quieres reenviarlo."
        )

    template = load_template(template_path or DEFAULT_INITIAL_TEMPLATE)
    rendered = render_for_autoescuela(template, autoescuela)
    to = override_to or autoescuela.email

    sent = send_message(gmail_service, to=to, subject=rendered.subject, body_text=rendered.body)

    now = dt.datetime.now(dt.timezone.utc)
    email_message = EmailMessage(
        autoescuela_id=autoescuela.id,
        direction="outbound",
        kind=kind,
        gmail_message_id=sent.get("id"),
        gmail_thread_id=sent.get("threadId"),
        sender=config.GMAIL_USER_EMAIL or None,
        recipient=to,
        subject=rendered.subject,
        body_text=rendered.body,
        timestamp=now,
    )
    session.add(email_message)

    if not is_test:
        if autoescuela.status == "not_contacted":
            autoescuela.status = "email_sent"
        if autoescuela.first_contact_date is None:
            autoescuela.first_contact_date = now
        autoescuela.emails_sent_count += 1

    session.flush()
    logger.info(
        "EmailMessage id=%s registrado para autoescuela %s (to=%s)",
        email_message.id,
        autoescuela.name,
        to,
    )
    return email_message


def send_batch(
    session: Session,
    gmail_service,
    autoescuelas: list[Autoescuela],
    template_path=None,
    delay_seconds: int | None = None,
    max_per_run: int | None = None,
) -> dict:
    """Envia el email inicial a varias autoescuelas, con pausa y limite.

    Autoescuelas ya contactadas (email inicial ya enviado) se omiten
    automaticamente en vez de fallar. Cada envio exitoso se confirma (commit)
    inmediatamente para no perder el registro si algo falla a mitad de lote.
    """
    delay_seconds = config.EMAIL_SEND_DELAY_SECONDS if delay_seconds is None else delay_seconds
    max_per_run = config.EMAIL_MAX_PER_RUN if max_per_run is None else max_per_run

    sent: list[Autoescuela] = []
    skipped: list[Autoescuela] = []
    errors: list[Autoescuela] = []

    for autoescuela in autoescuelas:
        if len(sent) >= max_per_run:
            logger.info("Limite EMAIL_MAX_PER_RUN=%s alcanzado, deteniendo el envio", max_per_run)
            break

        try:
            send_initial_email(session, gmail_service, autoescuela, template_path=template_path)
            session.commit()
            sent.append(autoescuela)
        except DuplicateEmailError as exc:
            logger.warning(str(exc))
            skipped.append(autoescuela)
            continue
        except Exception:
            logger.exception("Error enviando email a %s", autoescuela.name)
            session.rollback()
            errors.append(autoescuela)
            continue

        if delay_seconds > 0 and (len(sent) < max_per_run):
            time.sleep(delay_seconds)

    return {"sent": sent, "skipped": skipped, "errors": errors}
