"""Toda página aberta direto (sem passar pelo app.py) também precisa exigir login."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PAGES = sorted((Path(__file__).resolve().parent.parent / "pages").glob("*.py"))


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_pagina_direta_exige_login(page, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    at = AppTest.from_file(str(page), default_timeout=15).run()
    assert not at.exception
    assert at.text_input and at.text_input[0].label == "Usuário"
