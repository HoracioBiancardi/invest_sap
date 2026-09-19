from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app.py"


def _logado(monkeypatch):
    from scripts import db

    def _sem_banco(*args, **kwargs):
        raise db.DatabaseConnectionError("banco desligado nos testes")

    monkeypatch.setattr(db, "read_sql", _sem_banco)
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.text_input[0].set_value("admin")
    at.text_input[1].set_value("segredo-teste-123")
    at.button[0].click().run()
    return at


def test_app_bloqueado_sem_login(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert any("Portal de Acesso" in m.value for m in at.markdown)


def test_app_logado_renderiza_navegacao_e_tema(monkeypatch):
    at = _logado(monkeypatch)
    assert not at.exception
    assert "Tema" in [s.label for s in at.selectbox]
    assert any("bmt-topbar" in m.value for m in at.markdown)
