"""Autenticação do app web — regras portadas de `scripts/auth.py`, sem Streamlit.

Mesmo checklist de segurança do ecossistema (~/.claude/security-review-checklist.md):

- **Fail-closed / sem senha default**: banco sem usuários → cria `admin` com `APP_ACCESS_KEY`
  (se forte) ou senha aleatória em `.access_key` (0600) com troca obrigatória no 1º login.
- **Sessão server-side** (`app_db.sessions`): cookie `HttpOnly; SameSite=Strict` (`Secure`
  atrás do HTTPS) com token aleatório; o banco guarda só o SHA-256. Usuário revalidado no
  banco a cada requisição (desativado/rebaixado perde acesso na hora) e expiração por
  inatividade (config `idle_minutes`).
- **CSRF**: token por sessão, exigido no header `X-CSRF-Token` (HTMX) ou campo `_csrf` em
  todo POST.
- Comparação em tempo constante; hash de usuário inexistente também é verificado; mesma
  mensagem de erro sempre.
- **Lockout** em memória do processo, por usuário e global (o app roda num processo só —
  ver `web/main.py`).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from scripts import app_db
from scripts.password_service import PasswordService

logger = logging.getLogger(__name__)

# No Docker, aponte para a pasta persistida (APP_ACCESS_KEY_FILE=/app/data/.access_key).
KEY_FILE = Path(os.environ.get("APP_ACCESS_KEY_FILE") or Path(__file__).resolve().parent.parent / ".access_key")
ADMIN_INICIAL = "admin"
MAX_FALHAS_USUARIO = 5
MAX_FALHAS_GLOBAL = 30
LOCKOUT_SEG = 60
COOKIE = "invest_sid"

_HASH_FALSO = app_db.hash_password("senha-falsa-para-tempo-constante")
_lockouts: dict[str, dict[str, float]] = {}
_lock = threading.Lock()


def idle_segundos() -> int:
    minutos = app_db.get_setting("idle_minutes")
    if not isinstance(minutos, int) or minutos < 1:
        minutos = 30
    return minutos * 60


def garantir_admin_inicial() -> None:
    """Cria o admin inicial se o banco não tem usuários (idempotente)."""
    if app_db.contar_usuarios() > 0:
        return
    senha = os.environ.get("APP_ACCESS_KEY", "").strip()
    definida_no_env = PasswordService.aceitavel(senha)
    if senha and not definida_no_env:
        logger.warning("APP_ACCESS_KEY ignorada: senha curta ou fraca (mínimo força Média).")
    if not definida_no_env:
        senha = PasswordService.generate(20)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"usuario: {ADMIN_INICIAL}\nsenha: {senha}\n")
        logger.warning(
            "Banco de usuários vazio — admin inicial criado com senha aleatória em %s (0600). "
            "A troca de senha é obrigatória no primeiro login.",
            KEY_FILE,
        )
    app_db.criar_usuario(ADMIN_INICIAL, senha, role="admin", must_change_password=not definida_no_env)
    app_db.audit(None, "bootstrap_admin", "admin inicial criado")


def segundos_bloqueado(usuario: str) -> int:
    agora = time.time()
    with _lock:
        return max(
            (int(_lockouts.get(c, {}).get("ate", 0) - agora) + 1 for c in (usuario.lower(), "*")),
            default=0,
        )


def registrar_falha(usuario: str) -> None:
    with _lock:
        for chave, limite in ((usuario.lower(), MAX_FALHAS_USUARIO), ("*", MAX_FALHAS_GLOBAL)):
            estado = _lockouts.setdefault(chave, {"falhas": 0, "ate": 0.0})
            estado["falhas"] += 1
            if estado["falhas"] >= limite:
                estado["falhas"] = 0
                estado["ate"] = time.time() + LOCKOUT_SEG


def limpar_falhas(usuario: str) -> None:
    with _lock:
        _lockouts.pop(usuario.lower(), None)


def autenticar(usuario: str, senha: str) -> Optional[dict[str, Any]]:
    """Confere credenciais; devolve o usuário ou None. Não faz lockout."""
    user = app_db.get_user(usuario.strip())
    ok = app_db.verify_password(senha, user["password_hash"] if user else _HASH_FALSO)
    if user and ok and user["active"]:
        return user
    return None


def login(usuario: str, senha: str) -> tuple[Optional[str], Optional[str]]:
    """Fluxo completo de login com lockout e auditoria.

    Returns:
        (token de sessão, None) em caso de sucesso; (None, mensagem de erro) senão.
    """
    usuario = usuario[:64]
    restante = segundos_bloqueado(usuario)
    if restante > 0:
        return None, f"Muitas tentativas. Aguarde {restante}s."
    user = autenticar(usuario, senha)
    if not user:
        registrar_falha(usuario)
        app_db.audit(None, "login_falhou", usuario[:32])
        return None, "Usuário ou senha inválidos."
    limpar_falhas(usuario)
    token, _ = app_db.criar_sessao(user["id"])
    app_db.audit(user["username"], "login")
    return token, None


def sessao_atual(token: Optional[str]) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Resolve (sessão, usuário) do cookie, aplicando inatividade e revalidação no banco."""
    if not token:
        return None, None
    sessao = app_db.ler_sessao(token)
    if not sessao:
        return None, None
    if time.time() - sessao["last_seen"] > idle_segundos():
        app_db.apagar_sessao(sessao["token_hash"])
        return None, None
    user = app_db.get_user_by_id(sessao["user_id"])
    if not user or not user["active"]:
        app_db.apagar_sessao(sessao["token_hash"])
        return None, None
    app_db.tocar_sessao(sessao["token_hash"])
    return sessao, user


def logout(sessao: dict[str, Any], user: Optional[dict[str, Any]]) -> None:
    app_db.apagar_sessao(sessao["token_hash"])
    app_db.audit(user["username"] if user else None, "logout")


def trocar_senha_obrigatoria(user: dict[str, Any], nova: str, conf: str) -> Optional[str]:
    """Troca de senha do 1º acesso. Devolve mensagem de erro ou None."""
    if nova != conf:
        return "As senhas não conferem."
    if app_db.verify_password(nova, user["password_hash"]):
        return "A nova senha deve ser diferente da atual."
    try:
        app_db.definir_senha(user["id"], nova, must_change_password=False)
    except app_db.AppDbError as exc:
        return str(exc)
    app_db.audit(user["username"], "senha_alterada")
    if user["username"] == ADMIN_INICIAL and KEY_FILE.exists():
        KEY_FILE.unlink()
    return None


def trocar_minha_senha(user: dict[str, Any], atual: str, nova: str, conf: str) -> Optional[str]:
    """Troca voluntária (exige a senha atual). Devolve mensagem de erro ou None."""
    fresco = app_db.get_user_by_id(user["id"])
    if not fresco or not app_db.verify_password(atual, fresco["password_hash"]):
        app_db.audit(user["username"], "senha_troca_falhou")
        return "Senha atual incorreta."
    if nova != conf:
        return "As senhas não conferem."
    if nova == atual:
        return "A nova senha deve ser diferente da atual."
    try:
        app_db.definir_senha(user["id"], nova, must_change_password=False)
    except app_db.AppDbError as exc:
        return str(exc)
    app_db.audit(user["username"], "senha_alterada")
    return None
