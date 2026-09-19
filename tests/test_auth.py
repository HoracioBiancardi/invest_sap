from streamlit.testing.v1 import AppTest

SCRIPT = """
import streamlit as st
from scripts.auth import require_login
require_login()
st.write("CONTEUDO_PROTEGIDO")
"""


def _app(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    return AppTest.from_string(SCRIPT, default_timeout=10)


def _entrar(at, usuario, senha):
    at.text_input[0].set_value(usuario)
    at.text_input[1].set_value(senha)
    at.button[0].click().run()


def test_sem_login_bloqueia_conteudo(monkeypatch):
    at = _app(monkeypatch).run()
    assert not any("CONTEUDO_PROTEGIDO" in m.value for m in at.markdown)
    assert at.text_input


def test_chave_errada_nao_libera(monkeypatch):
    at = _app(monkeypatch).run()
    _entrar(at, "admin", "errada")
    assert not any("CONTEUDO_PROTEGIDO" in m.value for m in at.markdown)
    assert at.error


def test_chave_certa_libera(monkeypatch):
    at = _app(monkeypatch).run()
    _entrar(at, "admin", "segredo-teste-123")
    assert any("CONTEUDO_PROTEGIDO" in m.value for m in at.markdown)


def test_login_esconde_sidebar(monkeypatch):
    at = _app(monkeypatch).run()
    assert any('[data-testid="stSidebar"]' in m.value and "display: none" in m.value for m in at.markdown)
