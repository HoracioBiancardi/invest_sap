"""Persistência local do app (SQLite, modo WAL) — usuários, configurações, solicitações de ajuste.

Mesmo padrão do `db_service.py` do app_template: arquivo local, WAL, pragmas de performance.
É um banco *do app*, separado do DW (SQL Server/HANA, que o app só lê). Fica em
`data/app.db` (ignorado pelo git; pasta 0700, arquivo 0600) ou em `APP_DB_PATH`.

Toda query usa bind params (`?`) — nunca f-string com valor vindo de fora.
Senhas: PBKDF2-HMAC-SHA256 com salt aleatório (600.000 iterações, como o crypto_vault_service
do template); comparação em tempo constante.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

ROLES = ("admin", "leitor")
STATUS_AJUSTE = ("pendente", "aplicado", "recusado")
TIPOS_AJUSTE = ("Cliente → Setor", "Meta", "Estrutura", "Outro")

_ITERACOES = 600_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_SENHA = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'leitor')),
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ajustes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,
    chave TEXT NOT NULL,
    valor_atual TEXT,
    valor_proposto TEXT NOT NULL,
    motivo TEXT,
    status TEXT NOT NULL DEFAULT 'pendente',
    criado_por TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    resolvido_em TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    usuario TEXT,
    acao TEXT NOT NULL,
    detalhe TEXT
);
"""


class AppDbError(ValueError):
    """Erro de validação/regra de negócio, com mensagem pronta pra exibir na tela."""


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def db_path() -> Path:
    env = os.environ.get("APP_DB_PATH", "").strip()
    return Path(env) if env else Path(__file__).resolve().parent.parent / "data" / "app.db"


@contextlib.contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    novo = not path.exists()
    conn = sqlite3.connect(path, timeout=10)
    if novo:
        os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(_SCHEMA)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# ── senhas ───────────────────────────────────────────────────────────────────


def hash_password(senha: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, _ITERACOES)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERACOES, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(senha: str, armazenado: str) -> bool:
    try:
        _, iteracoes, salt_b64, dk_b64 = armazenado.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", senha.encode(), base64.b64decode(salt_b64), int(iteracoes)
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def validar_senha(senha: str) -> None:
    if len(senha) < MIN_SENHA:
        raise AppDbError(f"A senha precisa ter ao menos {MIN_SENHA} caracteres.")


def gerar_senha_temporaria() -> str:
    return secrets.token_urlsafe(12)


# ── usuários ─────────────────────────────────────────────────────────────────


def _user(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row else None


def contar_usuarios() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(username: str) -> Optional[dict[str, Any]]:
    with connect() as c:
        return _user(c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())


def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    with connect() as c:
        return _user(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def listar_usuarios() -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, username, role, active, must_change_password, created_at "
            "FROM users ORDER BY username"
        ).fetchall()
    return [dict(r) for r in rows]


def criar_usuario(
    username: str, senha: str, role: str = "leitor", must_change_password: bool = True
) -> int:
    username = username.strip()
    if not _USERNAME_RE.match(username):
        raise AppDbError("Usuário: 3 a 32 caracteres (letras, números, ponto, hífen, sublinhado).")
    if role not in ROLES:
        raise AppDbError("Perfil inválido.")
    validar_senha(senha)
    try:
        with connect() as c:
            cur = c.execute(
                "INSERT INTO users (username, password_hash, role, must_change_password, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, hash_password(senha), role, int(must_change_password), _agora()),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise AppDbError("Já existe um usuário com esse nome.") from exc


def _admins_ativos_exceto(c: sqlite3.Connection, user_id: int) -> int:
    return c.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id <> ?", (user_id,)
    ).fetchone()[0]


def alterar_usuario(
    user_id: int, *, role: Optional[str] = None, active: Optional[bool] = None
) -> None:
    """Muda perfil/ativação. Nunca deixa o sistema sem ao menos 1 admin ativo."""
    if role is not None and role not in ROLES:
        raise AppDbError("Perfil inválido.")
    with connect() as c:
        alvo = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if alvo is None:
            raise AppDbError("Usuário não encontrado.")
        novo_role = role if role is not None else alvo["role"]
        novo_ativo = int(active) if active is not None else alvo["active"]
        era_admin_ativo = alvo["role"] == "admin" and alvo["active"] == 1
        segue_admin_ativo = novo_role == "admin" and novo_ativo == 1
        if era_admin_ativo and not segue_admin_ativo and _admins_ativos_exceto(c, user_id) == 0:
            raise AppDbError("Não é possível remover o último administrador ativo.")
        c.execute("UPDATE users SET role = ?, active = ? WHERE id = ?", (novo_role, novo_ativo, user_id))


def definir_senha(user_id: int, senha: str, *, must_change_password: bool) -> None:
    validar_senha(senha)
    with connect() as c:
        cur = c.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?",
            (hash_password(senha), int(must_change_password), user_id),
        )
        if cur.rowcount == 0:
            raise AppDbError("Usuário não encontrado.")


# ── configurações ────────────────────────────────────────────────────────────

DEFAULTS_CONFIG: dict[str, Any] = {
    "default_theme": "corporate",
    "idle_minutes": 30,
    "limiar_zumbi_dias": 365,
    "excluir_estoque_internacional": False,
    "excluir_intercompany": True,
    "excluir_org_vendas_internacional": True,
    "excluir_possivel_zumbi": True,
}


def get_setting(chave: str, default: Any = None) -> Any:
    fallback = DEFAULTS_CONFIG.get(chave) if default is None else default
    try:
        with connect() as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (chave,)).fetchone()
    except sqlite3.Error:
        return fallback
    return json.loads(row["value"]) if row else fallback


def set_setting(chave: str, valor: Any) -> None:
    if chave not in DEFAULTS_CONFIG:
        raise AppDbError(f"Configuração desconhecida: {chave}")
    with connect() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (chave, json.dumps(valor)),
        )


# ── solicitações de ajuste ───────────────────────────────────────────────────


def criar_ajuste(
    tipo: str, chave: str, valor_atual: str, valor_proposto: str, motivo: str, criado_por: str
) -> int:
    if tipo not in TIPOS_AJUSTE:
        raise AppDbError("Tipo inválido.")
    if not chave.strip() or not valor_proposto.strip():
        raise AppDbError("Informe a chave (ex.: código do cliente) e o valor proposto.")
    with connect() as c:
        cur = c.execute(
            "INSERT INTO ajustes (tipo, chave, valor_atual, valor_proposto, motivo, criado_por, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tipo, chave.strip(), valor_atual.strip(), valor_proposto.strip(), motivo.strip(),
             criado_por, _agora()),
        )
        return int(cur.lastrowid)


def listar_ajustes(status: Optional[str] = None) -> list[dict[str, Any]]:
    with connect() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM ajustes WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM ajustes ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def resolver_ajuste(ajuste_id: int, status: str) -> None:
    if status not in STATUS_AJUSTE:
        raise AppDbError("Status inválido.")
    with connect() as c:
        c.execute(
            "UPDATE ajustes SET status = ?, resolvido_em = ? WHERE id = ?",
            (status, None if status == "pendente" else _agora(), ajuste_id),
        )


# ── auditoria ────────────────────────────────────────────────────────────────


def audit(usuario: Optional[str], acao: str, detalhe: str = "") -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO audit_log (ts, usuario, acao, detalhe) VALUES (?, ?, ?, ?)",
            (_agora(), usuario, acao, detalhe),
        )


def listar_audit(limite: int = 200) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute(
            "SELECT ts, usuario, acao, detalhe FROM audit_log ORDER BY id DESC LIMIT ?",
            (int(limite),),
        ).fetchall()
    return [dict(r) for r in rows]
