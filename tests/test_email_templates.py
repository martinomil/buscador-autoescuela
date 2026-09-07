from __future__ import annotations

from pathlib import Path

import pytest

from app.email_templates import load_template, render_template

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def test_load_real_initial_template():
    template = load_template(TEMPLATES_DIR / "email_inicial.txt")

    assert "permiso B" in template.subject
    assert "Buenos días" in template.body
    assert "Un saludo" in template.body
    assert "Martín" in template.body
    # La plantilla actual no requiere placeholders para renderizarse.
    rendered = render_template(template, {})
    assert rendered.body == template.body


def test_load_template_requires_subject_line(tmp_path):
    bad_template = tmp_path / "sin_subject.txt"
    bad_template.write_text("Hola,\n\ncuerpo\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Subject:"):
        load_template(bad_template)


def test_load_template_missing_file():
    with pytest.raises(FileNotFoundError):
        load_template("no_existe.txt")


def test_render_template_substitutes_placeholders():
    template = load_template(FIXTURES_DIR / "template_with_placeholder.txt")

    rendered = render_template(template, {"name": "Autoescuela X", "city": "A Coruña"})

    assert rendered.subject == "Hola Autoescuela X"
    assert "Autoescuela X" in rendered.body
    assert "A Coruña" in rendered.body


def test_render_template_raises_on_missing_variables():
    template = load_template(FIXTURES_DIR / "template_with_placeholder.txt")

    with pytest.raises(ValueError, match="Faltan valores"):
        render_template(template, {"name": "Autoescuela X"})
