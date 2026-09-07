from __future__ import annotations

from app.models import Autoescuela, FieldValue


def test_autoescuela_default_status(session):
    a = Autoescuela(name="Autoescuela Test", email="test@example.com")
    session.add(a)
    session.commit()

    assert a.status == "not_contacted"
    assert a.emails_sent_count == 0
    assert a.replies_count == 0
    assert a.id is not None


def test_field_value_tracks_manual_override(session):
    a = Autoescuela(name="Autoescuela Test", email="test2@example.com")
    session.add(a)
    session.commit()

    fv = FieldValue(
        autoescuela_id=a.id,
        field_name="practice_price",
        value=32,
        source="ai",
    )
    session.add(fv)
    session.commit()

    # Simula una correccion manual del usuario
    fv.ai_original_value = fv.value
    fv.value = 35
    fv.source = "manual"
    fv.updated_by = "martin"
    session.commit()

    reloaded = session.get(FieldValue, fv.id)
    assert reloaded.value == 35
    assert reloaded.ai_original_value == 32
    assert reloaded.source == "manual"
    assert reloaded.updated_by == "martin"
