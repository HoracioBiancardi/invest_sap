"""Testes do cofre de credenciais do DW (scripts/credential_vault.py) e da integração com db/app."""

import pytest
from cryptography.fernet import InvalidToken

from scripts import app_db, db
from scripts.credential_vault import CofreError, CredentialVault, CryptoVault

MESTRA = "Mestra-Forte-2026!"
CREDS = {
    "HANA_ADDRESS": "hana.exemplo", "HANA_PORT": "443", "HANA_USER": "u_hana",
    "HANA_PASSWORD": "segredo-hana", "DDIC_SCHEMA": "IB_SAPECC",
    "SQLSERVER_HOST": "sql.exemplo", "SQLSERVER_PORT": "1433",
    "SQLSERVER_USER": "u_sql", "SQLSERVER_PASSWORD": "segredo-sql",
}


def _blob_bruto() -> bytes:
    with app_db.connect() as c:
        return bytes(c.execute("SELECT blob FROM vault").fetchone()[0])


def test_cifra_e_decifra_com_salt_novo():
    a = CryptoVault.cifrar(b"dado", MESTRA)
    b = CryptoVault.cifrar(b"dado", MESTRA)
    assert a != b
    assert CryptoVault.decifrar(a, MESTRA) == b"dado"
    with pytest.raises(InvalidToken):
        CryptoVault.decifrar(a, "outra-senha")


def test_criar_grava_so_cifrado_e_desbloqueia():
    CredentialVault.criar(CREDS, MESTRA)
    blob = _blob_bruto()
    assert b"segredo-sql" not in blob and b"sql.exemplo" not in blob
    assert CredentialVault.desbloqueado()
    assert CredentialVault.valor("SQLSERVER_PASSWORD") == "segredo-sql"


def test_senha_mestra_fraca_recusada():
    with pytest.raises(CofreError, match="força Forte"):
        CredentialVault.criar(CREDS, "abcdefgh12")
    assert not CredentialVault.configurado()


def test_campo_obrigatorio_e_porta():
    with pytest.raises(CofreError, match="SQLSERVER_PASSWORD"):
        CredentialVault.criar({**CREDS, "SQLSERVER_PASSWORD": ""}, MESTRA)
    with pytest.raises(CofreError, match="numérica"):
        CredentialVault.criar({**CREDS, "HANA_PORT": "abc"}, MESTRA)


def test_bloqueado_nao_entrega_credencial_e_env_e_ignorado(monkeypatch):
    monkeypatch.setenv("SQLSERVER_PASSWORD", "do-env")
    CredentialVault.criar(CREDS, MESTRA)
    CredentialVault.bloquear()
    with pytest.raises(CofreError, match="bloqueado"):
        CredentialVault.valor("SQLSERVER_PASSWORD")
    with pytest.raises(db.DatabaseConnectionError, match="bloqueado"):
        db._require_env("SQLSERVER_PASSWORD")
    CredentialVault.desbloquear(MESTRA)
    assert db._require_env("SQLSERVER_PASSWORD") == "segredo-sql"


def test_sem_cofre_usa_env(monkeypatch):
    monkeypatch.setenv("SQLSERVER_HOST", "host-do-env")
    assert db._require_env("SQLSERVER_HOST") == "host-do-env"


def test_mestra_errada_e_lockout():
    CredentialVault.criar(CREDS, MESTRA)
    CredentialVault.bloquear()
    for _ in range(CredentialVault.MAX_FALHAS):
        with pytest.raises(CofreError, match="incorreta"):
            CredentialVault.desbloquear("errada")
    with pytest.raises(CofreError, match="Aguarde"):
        CredentialVault.desbloquear(MESTRA)
    assert not CredentialVault.desbloqueado()


def test_atualizar_mantem_senha_vazia_e_trocar_mestra():
    CredentialVault.criar(CREDS, MESTRA)
    CredentialVault.atualizar({"SQLSERVER_HOST": "novo.host", "SQLSERVER_PASSWORD": ""}, MESTRA)
    assert CredentialVault.valor("SQLSERVER_HOST") == "novo.host"
    assert CredentialVault.valor("SQLSERVER_PASSWORD") == "segredo-sql"
    assert "SQLSERVER_PASSWORD" not in CredentialVault.valores_visiveis()
    nova = "Outra-Mestra-2026#"
    CredentialVault.trocar_senha_mestra(MESTRA, nova)
    CredentialVault.bloquear()
    with pytest.raises(CofreError):
        CredentialVault.desbloquear(MESTRA)
    CredentialVault.desbloquear(nova)
    assert CredentialVault.valor("SQLSERVER_HOST") == "novo.host"


def test_apagar_volta_para_env(monkeypatch):
    monkeypatch.setenv("SQLSERVER_HOST", "host-do-env")
    CredentialVault.criar(CREDS, MESTRA)
    CredentialVault.apagar()
    assert not CredentialVault.configurado()
    assert db._require_env("SQLSERVER_HOST") == "host-do-env"


def test_trocar_credencial_descarta_engine_cacheada():
    CredentialVault.criar(CREDS, MESTRA)
    e1 = db.get_sqlserver_engine("GOLD")
    CredentialVault.atualizar({"SQLSERVER_HOST": "novo.host"}, MESTRA)
    e2 = db.get_sqlserver_engine("GOLD")
    assert e1 is not e2
    assert "novo.host" in str(e2.url.query)
