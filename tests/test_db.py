import pytest

from scripts import db


def test_database_invalido_e_rejeitado():
    with pytest.raises(ValueError):
        db.get_sqlserver_engine("MASTER; DROP TABLE x")


def test_require_env_sem_valor(monkeypatch):
    monkeypatch.delenv("SQLSERVER_HOST", raising=False)
    with pytest.raises(RuntimeError, match="SQLSERVER_HOST"):
        db._require_env("SQLSERVER_HOST")
