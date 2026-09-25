"""Testes do serviço de senhas (porte do crypto_vault_service do app_template) e da regra de força."""

import string

import pytest
from streamlit.testing.v1 import AppTest

from scripts import app_db, auth
from scripts.password_service import PasswordService

SCRIPT = "import streamlit as st\nfrom scripts.auth import require_login\nrequire_login()\nst.write('OK_PROTEGIDO')\n"


def test_hash_e_verificacao():
    h = PasswordService.hash("Senha-Forte-123")
    assert h.startswith("pbkdf2_sha256$")
    assert "Senha-Forte-123" not in h
    assert PasswordService.verify("Senha-Forte-123", h)
    assert not PasswordService.verify("outra", h)
    assert not PasswordService.verify("x", "lixo")


def test_hash_antigo_com_outra_contagem_continua_valido(monkeypatch):
    monkeypatch.setattr(PasswordService, "ITERACOES", 2000)
    h = PasswordService.hash("Senha-Forte-123")
    monkeypatch.setattr(PasswordService, "ITERACOES", 3000)
    assert PasswordService.verify("Senha-Forte-123", h)


@pytest.mark.parametrize("_", range(20))
def test_gerada_tem_todas_as_classes_e_e_aceitavel(_):
    senha = PasswordService.generate(16)
    assert len(senha) == 16
    assert any(c in string.ascii_uppercase for c in senha)
    assert any(c in string.ascii_lowercase for c in senha)
    assert any(c in string.digits for c in senha)
    assert any(c in PasswordService.SIMBOLOS for c in senha)
    assert PasswordService.aceitavel(senha)


def test_gerada_so_digitos():
    senha = PasswordService.generate(8, use_uppercase=False, use_lowercase=False, use_symbols=False)
    assert senha.isdigit() and len(senha) == 8


@pytest.mark.parametrize(
    ("senha", "score", "nivel"),
    [
        ("", 0, "Muito Fraca"),
        ("abc", 15, "Muito Fraca"),
        ("abcdefghij", 30, "Fraca"),
        ("abcdefgh12", 45, "Média"),
        ("Abcdefgh1234", 70, "Forte"),
        ("Abcdefgh1234567!", 95, "Excelente"),
    ],
)
def test_avaliacao_segue_regras_do_template(senha, score, nivel):
    forca = PasswordService.evaluate(senha)
    assert (forca.score, forca.nivel) == (score, nivel)


def test_senha_fraca_recusada_ao_criar_usuario():
    with pytest.raises(app_db.AppDbError, match="mínimo é força Média"):
        app_db.criar_usuario("ana", "abcdefghij")
    with pytest.raises(app_db.AppDbError, match="ao menos 10"):
        app_db.criar_usuario("ana", "Ab1!")
    app_db.criar_usuario("ana", "abcdefgh12")


def test_senha_temporaria_passa_na_validacao():
    app_db.validar_senha(app_db.gerar_senha_temporaria())


def test_app_access_key_fraca_e_ignorada(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "abcdefghijkl")
    AppTest.from_string(SCRIPT, default_timeout=10).run()
    admin = app_db.get_user("admin")
    assert admin["must_change_password"] == 1
    assert not app_db.verify_password("abcdefghijkl", admin["password_hash"])
    assert auth._KEY_FILE.exists()


def _login_troca(monkeypatch):
    monkeypatch.delenv("APP_ACCESS_KEY", raising=False)
    at = AppTest.from_string(SCRIPT, default_timeout=10).run()
    senha = auth._KEY_FILE.read_text().split("senha: ")[1].strip()
    at.text_input[0].set_value("admin")
    at.text_input[1].set_value(senha)
    at.button[0].click().run()
    assert at.text_input[0].label == "Nova senha"
    return at


def test_troca_de_senha_recusa_fraca_e_mostra_medidor(monkeypatch):
    at = _login_troca(monkeypatch)
    at.text_input[0].set_value("abcdefghij").run()
    assert any("bmt-forca" in m.value and "Fraca" in m.value for m in at.markdown)
    at.text_input[1].set_value("abcdefghij")
    at.button[0].click().run()
    assert at.error and "mínimo é força Média" in at.error[0].value
    assert app_db.get_user("admin")["must_change_password"] == 1


def test_sugerir_senha_preenche_e_permite_salvar(monkeypatch):
    at = _login_troca(monkeypatch)
    at.button[1].click().run()
    sugerida = at.code[0].value
    assert at.text_input[0].value == sugerida == at.text_input[1].value
    at.button[0].click().run()
    assert any("OK_PROTEGIDO" in m.value for m in at.markdown)
    assert app_db.verify_password(sugerida, app_db.get_user("admin")["password_hash"])
