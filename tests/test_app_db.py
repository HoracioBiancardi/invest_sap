import pytest

from scripts import app_db


def test_senha_hash_e_verificacao():
    h = app_db.hash_password("senha-forte-123")
    assert "senha-forte-123" not in h
    assert app_db.verify_password("senha-forte-123", h)
    assert not app_db.verify_password("outra", h)
    assert not app_db.verify_password("x", "lixo")


def test_criar_usuario_valida_entrada():
    with pytest.raises(app_db.AppDbError):
        app_db.criar_usuario("a b", "senha-forte-123")
    with pytest.raises(app_db.AppDbError):
        app_db.criar_usuario("ana", "curta")
    with pytest.raises(app_db.AppDbError):
        app_db.criar_usuario("ana", "senha-forte-123", role="root")
    app_db.criar_usuario("ana", "senha-forte-123")
    with pytest.raises(app_db.AppDbError):  # duplicado, case-insensitive
        app_db.criar_usuario("ANA", "senha-forte-123")


def test_sql_injection_no_username_e_inofensivo():
    app_db.criar_usuario("admin", "senha-forte-123", role="admin")
    assert app_db.get_user("admin' OR '1'='1") is None


def test_nunca_remove_ultimo_admin():
    uid = app_db.criar_usuario("admin", "senha-forte-123", role="admin")
    with pytest.raises(app_db.AppDbError):
        app_db.alterar_usuario(uid, active=False)
    with pytest.raises(app_db.AppDbError):
        app_db.alterar_usuario(uid, role="leitor")
    outro = app_db.criar_usuario("bia", "senha-forte-123", role="admin")
    app_db.alterar_usuario(uid, active=False)  # agora há outro admin ativo
    with pytest.raises(app_db.AppDbError):
        app_db.alterar_usuario(outro, role="leitor")


def test_settings_default_e_persistencia():
    assert app_db.get_setting("idle_minutes") == 30
    app_db.set_setting("idle_minutes", 5)
    assert app_db.get_setting("idle_minutes") == 5
    with pytest.raises(app_db.AppDbError):
        app_db.set_setting("chave_inventada", 1)


def test_ajustes_fluxo():
    aid = app_db.criar_ajuste("Meta", "301010104", "10", "20", "erro", "admin")
    assert app_db.listar_ajustes("pendente")[0]["id"] == aid
    app_db.resolver_ajuste(aid, "aplicado")
    assert app_db.listar_ajustes("pendente") == []
    with pytest.raises(app_db.AppDbError):
        app_db.criar_ajuste("Meta", " ", "", "x", "", "admin")


def test_arquivo_do_banco_so_do_dono():
    app_db.criar_usuario("admin", "senha-forte-123", role="admin")
    assert app_db.db_path().stat().st_mode & 0o077 == 0
