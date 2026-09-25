import pytest

from scripts import app_db
from scripts import auth
from scripts.credential_vault import CredentialVault, CryptoVault
from scripts.password_service import PasswordService


@pytest.fixture(autouse=True)
def banco_isolado(tmp_path, monkeypatch):
    """Cada teste usa um SQLite próprio, hash rápido e sem arquivo .access_key real."""
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(PasswordService, "ITERACOES", 1000)
    monkeypatch.setattr(CryptoVault, "ITERACOES", 1000)
    CredentialVault.bloquear()
    CredentialVault._falhas.clear()
    monkeypatch.setattr(auth, "_KEY_FILE", tmp_path / ".access_key")
    monkeypatch.setattr(auth, "_HASH_FALSO", app_db.hash_password("x"))
    auth._lockouts.clear()
    yield
    auth._lockouts.clear()
    CredentialVault.bloquear()
    CredentialVault._falhas.clear()
