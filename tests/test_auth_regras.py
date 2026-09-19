from pathlib import Path

from streamlit.testing.v1 import AppTest

from scripts import app_db, auth

APP = Path(__file__).resolve().parent.parent / "app.py"
ADMIN_PAGE = Path(__file__).resolve().parent.parent / "pages" / "90_Admin.py"
SCRIPT = "import streamlit as st\nfrom scripts.auth import require_login\nrequire_login()\nst.write('OK_PROTEGIDO')\n"


def _login(at, usuario, senha):
    at.text_input[0].set_value(usuario)
    at.text_input[1].set_value(senha)
    at.button[0].click().run()
    return at


def _protegido(at):
    return any("OK_PROTEGIDO" in m.value for m in at.markdown)


def test_bootstrap_aleatorio_exige_troca_e_apaga_arquivo(monkeypatch):
    monkeypatch.delenv("APP_ACCESS_KEY", raising=False)
    at = AppTest.from_string(SCRIPT, default_timeout=10).run()
    assert at.text_input[0].label == "Usuário"
    senha = auth._KEY_FILE.read_text().split("senha: ")[1].strip()
    assert auth._KEY_FILE.stat().st_mode & 0o077 == 0
    _login(at, "admin", senha)
    assert not _protegido(at)  # travado na troca obrigatória
    assert at.text_input[0].label == "Nova senha"
    at.text_input[0].set_value("nova-senha-forte-1")
    at.text_input[1].set_value("nova-senha-forte-1")
    at.button[0].click().run()
    assert _protegido(at)
    assert not auth._KEY_FILE.exists()


def test_usuario_desativado_perde_acesso_na_proxima_interacao(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    at = AppTest.from_string(SCRIPT, default_timeout=10).run()
    _login(at, "admin", "segredo-teste-123")
    assert _protegido(at)
    app_db.criar_usuario("outro", "senha-forte-123", role="admin")
    app_db.alterar_usuario(app_db.get_user("admin")["id"], active=False)
    at.run()
    assert not _protegido(at)


def test_lockout_apos_falhas_mesmo_com_senha_certa(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    at = AppTest.from_string(SCRIPT, default_timeout=10).run()
    for _ in range(5):
        _login(at, "admin", "errada")
    _login(at, "admin", "segredo-teste-123")
    assert not _protegido(at)
    assert any("Aguarde" in e.value for e in at.error)


def test_mensagem_igual_para_usuario_inexistente(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    at = AppTest.from_string(SCRIPT, default_timeout=10).run()
    _login(at, "admin", "errada")
    msg1 = at.error[0].value
    _login(at, "fantasma", "errada")
    assert at.error[0].value == msg1


def test_leitor_nao_acessa_pagina_admin(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    app_db.criar_usuario("admin", "segredo-teste-123", role="admin", must_change_password=False)
    app_db.criar_usuario("leitor1", "senha-forte-123", role="leitor", must_change_password=False)
    at = AppTest.from_file(str(ADMIN_PAGE), default_timeout=15).run()
    _login(at, "leitor1", "senha-forte-123")
    assert any("restrito a administradores" in e.value for e in at.error)
    assert not at.tabs


def test_admin_ve_pagina_admin_com_abas(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "segredo-teste-123")
    at = AppTest.from_file(str(ADMIN_PAGE), default_timeout=15).run()
    _login(at, "admin", "segredo-teste-123")
    assert not at.exception
    assert [t.label for t in at.tabs][:3] == ["Usuários", "Configurações", "Dados de negócio"]


def test_csv_exportado_neutraliza_formula():
    src = ADMIN_PAGE.read_text()
    assert '"="' in src and "_csv_seguro" in src
