from __future__ import annotations

from pathlib import Path

import pytest

from app.importer import import_csv
from app.models import Autoescuela

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_import_csv_creates_autoescuelas(session):
    summary = import_csv(session, FIXTURES_DIR / "autoescuelas_sample.csv")

    assert summary.created == 3
    assert summary.skipped_duplicate == 0
    assert summary.skipped_invalid == 0

    rows = session.query(Autoescuela).order_by(Autoescuela.name).all()
    assert [r.name for r in rows] == [
        "Autoescuela Coruna Centro",
        "Autoescuela Milladoiro",
        "Autoescuela Santiago Norte",
    ]
    coruna = next(r for r in rows if "Coruna Centro" in r.name)
    assert coruna.city == "A Coruna"
    assert coruna.email == "coruna.centro@example.com"
    assert coruna.status == "not_contacted"
    assert coruna.website.startswith("https://")


def test_import_csv_is_idempotent_by_email(session):
    import_csv(session, FIXTURES_DIR / "autoescuelas_sample.csv")
    summary_second_run = import_csv(session, FIXTURES_DIR / "autoescuelas_sample.csv")

    assert summary_second_run.created == 0
    assert summary_second_run.skipped_duplicate == 3

    total = session.query(Autoescuela).count()
    assert total == 3


def test_import_csv_skips_rows_missing_required_fields(session, tmp_path):
    csv_content = (
        "name,city,email\n"
        "Autoescuela Valida,A Coruna,valida@example.com\n"
        ",A Coruna,sin_nombre@example.com\n"
        "Autoescuela Sin Email,A Coruna,\n"
    )
    csv_path = tmp_path / "invalid_rows.csv"
    csv_path.write_text(csv_content, encoding="utf-8")

    summary = import_csv(session, csv_path)

    assert summary.created == 1
    assert summary.skipped_invalid == 2
    assert session.query(Autoescuela).count() == 1


def test_import_csv_requires_mandatory_columns(session, tmp_path):
    csv_path = tmp_path / "missing_columns.csv"
    csv_path.write_text("city,phone\nA Coruna,981000000\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Faltan columnas obligatorias"):
        import_csv(session, csv_path)
