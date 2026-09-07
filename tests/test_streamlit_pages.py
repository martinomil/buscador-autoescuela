"""Smoke tests de las paginas de Streamlit (Fase 6).

Verifican que cada pagina carga sin excepciones tanto con la base de datos
vacia como con una autoescuela ya evaluada (el caso que detecto un bug real
durante el desarrollo: una columna de tabla mezclando int y "—" rompia la
serializacion a Arrow).

Usan una base de datos SQLite temporal propia (nunca la real del proyecto).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from streamlit.testing.v1 import AppTest

from app.db import init_db
from app.models import Autoescuela, Evaluation, FieldValue

STREAMLIT_DIR = Path(__file__).parent.parent / "streamlit_app"

PAGES = [
    STREAMLIT_DIR / "Home.py",
    STREAMLIT_DIR / "pages" / "1_Autoescuelas.py",
    STREAMLIT_DIR / "pages" / "2_Autoescuela_Detalle.py",
    STREAMLIT_DIR / "pages" / "3_Ranking.py",
    STREAMLIT_DIR / "pages" / "4_Enviar_Emails.py",
]


@pytest.fixture()
def streamlit_db(tmp_path, monkeypatch):
    """Sustituye la BD real por una temporal para todas las paginas de esta prueba."""
    db_path = tmp_path / "streamlit_test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    init_db(engine)
    test_session_local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr("app.db.SessionLocal", test_session_local)
    return test_session_local


def _seed_scored_autoescuela(session_local) -> int:
    session = session_local()
    a = Autoescuela(name="Autoescuela Demo", email="demo@example.com", city="A Coruña", status="replied")
    session.add(a)
    session.commit()

    session.add(FieldValue(autoescuela_id=a.id, field_name="practice_price", value=32, source="ai"))
    session.add(
        FieldValue(
            autoescuela_id=a.id, field_name="waiting_time_to_start",
            value={"value": 3, "unit": "weeks"}, source="ai",
        )
    )
    session.commit()

    session.add(
        Evaluation(
            autoescuela_id=a.id,
            score=73,
            verdict="recommended",
            reasoning="Buena opcion segun tus prioridades.",
            pros=["Precio razonable"],
            cons=[],
            risks=[],
            breakdown={
                "inicio": {"label": "Inicio de practicas", "points": 15, "max": 25, "status": "known"},
                "frecuencia": {"label": "Frecuencia de practicas", "points": 15, "max": 25, "status": "unknown"},
                "tiempo_examen": {"label": "Tiempo hasta examen", "points": 12, "max": 20, "status": "unknown"},
                "precio": {"label": "Precio", "points": 9, "max": 15, "status": "known"},
                "disponibilidad": {"label": "Confianza / disponibilidad", "points": 9, "max": 15, "status": "partial"},
            },
        )
    )
    session.commit()
    autoescuela_id = a.id
    session.close()
    return autoescuela_id


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_loads_without_exceptions_on_empty_db(streamlit_db, page):
    at = AppTest.from_file(str(page), default_timeout=30)
    at.run()
    assert not at.exception, f"{page.name}: {at.exception}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_loads_without_exceptions_with_scored_autoescuela(streamlit_db, page):
    _seed_scored_autoescuela(streamlit_db)
    at = AppTest.from_file(str(page), default_timeout=30)
    at.run()
    assert not at.exception, f"{page.name}: {at.exception}"


def test_detalle_page_shows_selected_autoescuela_fields(streamlit_db):
    autoescuela_id = _seed_scored_autoescuela(streamlit_db)
    page = STREAMLIT_DIR / "pages" / "2_Autoescuela_Detalle.py"

    at = AppTest.from_file(str(page), default_timeout=30)
    at.run()
    select = at.selectbox[0]
    option = next(o for o in select.options if o.startswith(f"[{autoescuela_id}]"))
    at = select.set_value(option).run()

    assert not at.exception
    assert any("practice_price" in md.value for md in at.markdown)


def test_home_dashboard_counts_reflect_data(streamlit_db):
    _seed_scored_autoescuela(streamlit_db)
    at = AppTest.from_file(str(STREAMLIT_DIR / "Home.py"), default_timeout=30)
    at.run()
    assert not at.exception
    values = [m.value for m in at.metric]
    assert "1" in values  # total autoescuelas
