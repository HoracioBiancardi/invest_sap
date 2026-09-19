import pytest

from scripts import app_db
from scripts import auth


@pytest.fixture(autouse=True)
def banco_isolado(tmp_path, monkeypatch):
    """Cada teste usa um SQLite próprio, hash rápido e sem arquivo .access_key real."""
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(app_db, "_ITERACOES", 1000)
    monkeypatch.setattr(auth, "_KEY_FILE", tmp_path / ".access_key")
    monkeypatch.setattr(auth, "_HASH_FALSO", app_db.hash_password("x"))
    auth._lockouts.clear()
    yield
    auth._lockouts.clear()
