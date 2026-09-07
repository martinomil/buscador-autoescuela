"""Orquesta la extraccion: coge respuestas sin procesar, llama al LLM, guarda.

Principios (ver README, Fase 4):
- Nunca se reanaliza un email ya procesado (EmailMessage.processed=True),
  salvo escalado explicito a un modelo mas potente.
- Una correccion manual del usuario (FieldValue.source="manual") nunca se
  pisa automaticamente con un nuevo resultado de IA.
- Se guarda siempre el resultado bruto del LLM (ExtractionResult) ademas del
  valor "actual" por campo (FieldValue), para poder auditar cualquier extraccion.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.llm.extractor import EXTRACTION_FIELDS, extract_reply
from app.models import Autoescuela, EmailMessage, ExtractionResult, FieldValue

logger = logging.getLogger(__name__)

# Campos "meta" que tambien se guardan como FieldValue (mismo mecanismo de
# origen ai/manual y de edicion que los campos de datos).
META_FIELD_NAMES = ("notes", "missing_info", "follow_up_needed")

STATUSES_NOT_OVERRIDDEN_BY_FOLLOWUP = {"interested", "rejected", "selected"}


class ProcessingError(Exception):
    pass


def _find_original_question_text(session: Session, autoescuela_id: int) -> str:
    stmt = (
        select(EmailMessage)
        .where(
            EmailMessage.autoescuela_id == autoescuela_id,
            EmailMessage.direction == "outbound",
            EmailMessage.kind == "initial",
        )
        .order_by(EmailMessage.created_at)
    )
    outbound = session.scalars(stmt).first()
    return outbound.body_text if outbound else ""


def _upsert_field_value(session: Session, autoescuela_id: int, field_name: str, value, extraction_result_id: int) -> None:
    stmt = select(FieldValue).where(
        FieldValue.autoescuela_id == autoescuela_id,
        FieldValue.field_name == field_name,
    )
    existing = session.scalars(stmt).first()

    if existing is not None and existing.source == "manual":
        logger.info(
            "Campo %r de autoescuela_id=%s tiene una correccion manual, no se sobrescribe",
            field_name,
            autoescuela_id,
        )
        return

    if existing is None:
        existing = FieldValue(autoescuela_id=autoescuela_id, field_name=field_name)
        session.add(existing)

    existing.value = value
    existing.source = "ai"
    existing.extraction_result_id = extraction_result_id


def process_email_message(session: Session, email_message: EmailMessage, model: str | None = None) -> ExtractionResult:
    """Analiza UNA respuesta entrante y guarda ExtractionResult + FieldValues."""
    if email_message.direction != "inbound":
        raise ProcessingError(f"EmailMessage {email_message.id} no es una respuesta entrante")
    if email_message.autoescuela_id is None:
        raise ProcessingError(
            f"EmailMessage {email_message.id} no esta asociado a ninguna autoescuela "
            "(usa 'assign-email' antes de procesarlo)"
        )

    original_question_text = _find_original_question_text(session, email_message.autoescuela_id)
    reply_text = email_message.body_text or ""

    used_model = model or config.LLM_MODEL_CHEAP
    result_dict = extract_reply(original_question_text, reply_text, model=used_model)

    extraction_result = ExtractionResult(
        email_message_id=email_message.id,
        autoescuela_id=email_message.autoescuela_id,
        raw_output=result_dict,
        model_used=used_model,
        follow_up_needed=result_dict["follow_up_needed"],
        follow_up_notes="; ".join(result_dict["missing_info"]) if result_dict["missing_info"] else None,
    )
    session.add(extraction_result)
    session.flush()  # necesitamos extraction_result.id para las FieldValue

    for field_name in EXTRACTION_FIELDS:
        value = result_dict.get(field_name)
        if value is not None:
            _upsert_field_value(session, email_message.autoescuela_id, field_name, value, extraction_result.id)

    _upsert_field_value(session, email_message.autoescuela_id, "notes", result_dict["notes"], extraction_result.id)
    _upsert_field_value(session, email_message.autoescuela_id, "missing_info", result_dict["missing_info"], extraction_result.id)
    _upsert_field_value(session, email_message.autoescuela_id, "follow_up_needed", result_dict["follow_up_needed"], extraction_result.id)

    email_message.processed = True

    autoescuela = session.get(Autoescuela, email_message.autoescuela_id)
    if result_dict["follow_up_needed"] and autoescuela.status not in STATUSES_NOT_OVERRIDDEN_BY_FOLLOWUP:
        autoescuela.status = "follow_up_needed"

    session.flush()
    logger.info(
        "Extraccion completada (modelo=%s) para autoescuela_id=%s, email_message_id=%s, follow_up_needed=%s",
        used_model,
        email_message.autoescuela_id,
        email_message.id,
        result_dict["follow_up_needed"],
    )
    return extraction_result


def _pending_replies_query(session: Session):
    return (
        select(EmailMessage)
        .where(
            EmailMessage.direction == "inbound",
            EmailMessage.processed.is_(False),
            EmailMessage.autoescuela_id.is_not(None),
        )
        .order_by(EmailMessage.created_at)
    )


def process_unprocessed_replies(session: Session, model: str | None = None, limit: int | None = None) -> dict:
    """Procesa todas las respuestas entrantes asociadas y aun no analizadas.

    Cada exito se confirma (commit) inmediatamente para no perder trabajo si
    algo falla a mitad de lote; los fallos se registran pero no detienen el resto.
    """
    stmt = _pending_replies_query(session)
    if limit:
        stmt = stmt.limit(limit)
    pending = list(session.scalars(stmt).all())
    logger.info("%s respuesta(s) pendiente(s) de analizar", len(pending))

    processed: list[ExtractionResult] = []
    errors: list[EmailMessage] = []

    for email_message in pending:
        try:
            result = process_email_message(session, email_message, model=model)
            session.commit()
            processed.append(result)
        except Exception:
            logger.exception("Error analizando email_message_id=%s", email_message.id)
            session.rollback()
            errors.append(email_message)

    return {"processed": processed, "errors": errors}


def find_ambiguous_email_messages(session: Session, escalated_model: str) -> list[EmailMessage]:
    """Respuestas ya procesadas cuyo ultimo analisis marco follow_up_needed,
    y que todavia no se han reanalizado con `escalated_model`."""
    stmt = (
        select(EmailMessage)
        .where(EmailMessage.direction == "inbound", EmailMessage.processed.is_(True))
        .order_by(EmailMessage.created_at)
    )
    candidates = []
    for email_message in session.scalars(stmt).all():
        results = sorted(email_message.extraction_results, key=lambda r: r.created_at)
        if not results:
            continue
        if any(r.model_used == escalated_model for r in results):
            continue
        if results[-1].follow_up_needed:
            candidates.append(email_message)
    return candidates


def escalate_ambiguous(session: Session, model: str | None = None) -> dict:
    """Reanaliza con un modelo mas potente las respuestas marcadas como
    ambiguas (follow_up_needed) por el modelo barato."""
    model = model or config.LLM_MODEL_SMART
    candidates = find_ambiguous_email_messages(session, model)
    logger.info("%s respuesta(s) ambigua(s) para reanalizar con %s", len(candidates), model)

    processed: list[ExtractionResult] = []
    errors: list[EmailMessage] = []

    for email_message in candidates:
        try:
            result = process_email_message(session, email_message, model=model)
            session.commit()
            processed.append(result)
        except Exception:
            logger.exception("Error reanalizando email_message_id=%s", email_message.id)
            session.rollback()
            errors.append(email_message)

    return {"processed": processed, "errors": errors}
