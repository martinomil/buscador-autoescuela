"""Importacion de autoescuelas desde CSV.

Formato esperado (cabecera obligatoria: name, email; el resto opcional):

    name,city,email,phone,website,address,notes
    Autoescuela X,A Coruna,example@gmail.com,981123456,https://...,,
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Autoescuela

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"name", "email"}
OPTIONAL_COLUMNS = ("city", "phone", "website", "address", "notes")


@dataclass
class ImportSummary:
    created: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    total_rows: int = 0


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def import_csv(session: Session, csv_path: str | Path) -> ImportSummary:
    csv_path = Path(csv_path)
    summary = ImportSummary()

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"El CSV {csv_path} no tiene cabecera")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Faltan columnas obligatorias en el CSV: {sorted(missing)}")

        existing_emails = {
            e.lower() for (e,) in session.query(Autoescuela.email).all()
        }

        for row in reader:
            summary.total_rows += 1
            name = _clean(row.get("name"))
            email = _clean(row.get("email"))

            if not name or not email:
                logger.warning("Fila %s ignorada: falta name o email", summary.total_rows)
                summary.skipped_invalid += 1
                continue

            if email.lower() in existing_emails:
                logger.info("Autoescuela con email %s ya existe, se omite", email)
                summary.skipped_duplicate += 1
                continue

            autoescuela = Autoescuela(
                name=name,
                email=email,
                city=_clean(row.get("city")),
                phone=_clean(row.get("phone")),
                website=_clean(row.get("website")),
                address=_clean(row.get("address")),
                notes=_clean(row.get("notes")),
                status="not_contacted",
            )
            session.add(autoescuela)
            existing_emails.add(email.lower())
            summary.created += 1

    session.flush()
    logger.info(
        "Importacion completada: %s creadas, %s duplicadas, %s invalidas (de %s filas)",
        summary.created,
        summary.skipped_duplicate,
        summary.skipped_invalid,
        summary.total_rows,
    )
    return summary
