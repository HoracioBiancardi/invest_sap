"""Testes do serviço de senhas (porte do crypto_vault_service do app_template) e da regra de força."""

import string

import pytest

from scripts import app_db
from scripts.password_service import PasswordService



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
