import pytest

from scripts import app_db
from scripts.credential_vault import CredentialVault, CryptoVault
from scripts.password_service import PasswordService
from web import auth as web_auth


@pytest.fixture(autouse=True)
def banco_isolado(tmp_path, monkeypatch):
    """Cada teste usa um SQLite próprio, hash rápido e sem arquivo .access_key real."""
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(PasswordService, "ITERACOES", 1000)
    monkeypatch.setattr(CryptoVault, "ITERACOES", 1000)
    CredentialVault.bloquear()
    CredentialVault._falhas.clear()
    monkeypatch.setattr(web_auth, "KEY_FILE", tmp_path / ".access_key")
    monkeypatch.setattr(web_auth, "_HASH_FALSO", app_db.hash_password("x"))
    web_auth._lockouts.clear()
    yield
    web_auth._lockouts.clear()
    CredentialVault.bloquear()
    CredentialVault._falhas.clear()
