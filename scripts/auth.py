"""Portão de acesso do dashboard — login com usuário e senha, perfis admin/leitor.

Equivalente Streamlit do "Portal de Acesso" do app_template. Usuários vivem no SQLite local
(`scripts/app_db.py`). Segue o checklist de segurança do ecossistema
(~/.claude/security-review-checklist.md):

- **Fail-closed / sem senha default**: na primeira execução (banco sem usuários) cria o
  admin `admin` com a senha de `APP_ACCESS_KEY` ou, se ausente, uma senha aleatória gravada
  em `.access_key` (0600, ignorado pelo git) e avisada uma vez no log; nesse segundo caso a
  troca de senha é obrigatória no primeiro login (e o arquivo é apagado após a troca).
- **Sessão revalidada no banco a cada interação**: usuário desativado/rebaixado perde acesso
  na interação seguinte, sem esperar a sessão expirar.
- Comparação em tempo constante; hash do usuário inexistente também é verificado (não
  entrega, pelo tempo de resposta, quais usuários existem); mesma mensagem de erro sempre.
- Lockout em memória do processo, por usuário e global (`session_state` sozinho não serve: o
  atacante abre outra aba e zera o contador).
- Sessão só em `st.session_state`, com auto-lock por inatividade (config `idle_minutes`).

Uso: `require_login()` em `app.py`; `require_login(show_logout=False)` no topo de cada página
(defesa em profundidade contra abertura direta por URL); `require_admin()` nas páginas admin.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from scripts import app_db

logger = logging.getLogger(__name__)

_KEY_FILE = Path(__file__).resolve().parent.parent / ".access_key"
_ADMIN_INICIAL = "admin"
_MAX_FALHAS_USUARIO = 5
_MAX_FALHAS_GLOBAL = 30
_LOCKOUT_SEG = 60
_SS_UID = "_auth_uid"
_SS_ULTIMA = "_auth_ultima_atividade"
_HASH_FALSO = app_db.hash_password("senha-falsa-para-tempo-constante")

_CSS_ESCONDE_SIDEBAR = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"], [data-testid="stSidebarNav"] { display: none !important; }
</style>
"""


def _idle_seconds() -> int:
    minutos = app_db.get_setting("idle_minutes")
    if not isinstance(minutos, int) or minutos < 1:
        minutos = 30
    return minutos * 60


def _garantir_admin_inicial() -> None:
    if app_db.contar_usuarios() > 0:
        return
    senha = os.environ.get("APP_ACCESS_KEY", "").strip()
    definida_no_env = len(senha) >= app_db.MIN_SENHA
    if not definida_no_env:
        senha = secrets.token_urlsafe(18)
        fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"usuario: {_ADMIN_INICIAL}\nsenha: {senha}\n")
        logger.warning(
            "Banco de usuários vazio — admin inicial criado com senha aleatória em %s (0600). "
            "A troca de senha é obrigatória no primeiro login.",
            _KEY_FILE,
        )
    app_db.criar_usuario(_ADMIN_INICIAL, senha, role="admin", must_change_password=not definida_no_env)
    app_db.audit(None, "bootstrap_admin", "admin inicial criado")


@st.cache_resource
def _lockouts() -> dict[str, dict[str, float]]:
    """Estado compartilhado entre sessões: {chave: {falhas, ate}} — chave = usuário ou '*'."""
    return {}


def _segundos_bloqueado(*chaves: str) -> int:
    agora = time.time()
    return max((int(_lockouts().get(c, {}).get("ate", 0) - agora) + 1 for c in chaves), default=0)


def _registrar_falha(usuario: str) -> None:
    for chave, limite in ((usuario.lower(), _MAX_FALHAS_USUARIO), ("*", _MAX_FALHAS_GLOBAL)):
        st_ = _lockouts().setdefault(chave, {"falhas": 0, "ate": 0.0})
        st_["falhas"] += 1
        if st_["falhas"] >= limite:
            st_["falhas"] = 0
            st_["ate"] = time.time() + _LOCKOUT_SEG


def _limpar_falhas(usuario: str) -> None:
    _lockouts().pop(usuario.lower(), None)


def autenticar(usuario: str, senha: str) -> Optional[dict[str, Any]]:
    """Confere credenciais; devolve o usuário (dict) ou None. Não faz lockout."""
    user = app_db.get_user(usuario.strip())
    ok = app_db.verify_password(senha, user["password_hash"] if user else _HASH_FALSO)
    if user and ok and user["active"]:
        return user
    return None


def logout() -> None:
    uid = st.session_state.pop(_SS_UID, None)
    st.session_state.pop(_SS_ULTIMA, None)
    if uid:
        user = app_db.get_user_by_id(uid)
        app_db.audit(user["username"] if user else None, "logout")


def current_user() -> Optional[dict[str, Any]]:
    """Usuário da sessão, revalidado no banco (None se não logado/desativado)."""
    uid = st.session_state.get(_SS_UID)
    if not uid:
        return None
    user = app_db.get_user_by_id(uid)
    if not user or not user["active"]:
        st.session_state.pop(_SS_UID, None)
        return None
    return user


def _render_login() -> None:
    st.markdown(_CSS_ESCONDE_SIDEBAR, unsafe_allow_html=True)
    _, centro, _ = st.columns([1, 1.6, 1])
    with centro:
        st.markdown("## 🔐 Portal de Acesso")
        st.caption("SwordPower · sessão apenas em memória, com bloqueio automático por inatividade.")
        with st.form("login", border=True):
            usuario = st.text_input("Usuário", autocomplete="username")
            senha = st.text_input("Senha", type="password", autocomplete="current-password")
            enviar = st.form_submit_button("Entrar", type="primary", use_container_width=True)
        if not enviar:
            return
        restante = _segundos_bloqueado(usuario.lower(), "*")
        if restante > 0:
            st.error(f"Muitas tentativas. Aguarde {restante}s.")
            return
        user = autenticar(usuario, senha)
        if user:
            _limpar_falhas(usuario)
            st.session_state[_SS_UID] = user["id"]
            st.session_state[_SS_ULTIMA] = time.time()
            app_db.audit(user["username"], "login")
            st.rerun()
        _registrar_falha(usuario)
        app_db.audit(None, "login_falhou", usuario[:32])
        st.error("Usuário ou senha inválidos.")


def _render_troca_senha(user: dict[str, Any]) -> None:
    st.markdown(_CSS_ESCONDE_SIDEBAR, unsafe_allow_html=True)
    _, centro, _ = st.columns([1, 1.6, 1])
    with centro:
        st.markdown("## 🔑 Defina uma nova senha")
        st.caption(f"Olá, {user['username']}. Sua senha atual é temporária — troque para continuar.")
        with st.form("troca_senha", border=True):
            nova = st.text_input("Nova senha", type="password", autocomplete="new-password")
            conf = st.text_input("Confirmar nova senha", type="password", autocomplete="new-password")
            enviar = st.form_submit_button("Salvar", type="primary", use_container_width=True)
        if enviar:
            if nova != conf:
                st.error("As senhas não conferem.")
            elif app_db.verify_password(nova, user["password_hash"]):
                st.error("A nova senha deve ser diferente da atual.")
            else:
                try:
                    app_db.definir_senha(user["id"], nova, must_change_password=False)
                except app_db.AppDbError as exc:
                    st.error(str(exc))
                else:
                    app_db.audit(user["username"], "senha_alterada")
                    if user["username"] == _ADMIN_INICIAL and _KEY_FILE.exists():
                        _KEY_FILE.unlink()
                    st.rerun()


def require_login(show_logout: bool = True) -> dict[str, Any]:
    """Bloqueia a execução do script (st.stop) até o usuário se autenticar. Devolve o usuário."""
    _garantir_admin_inicial()
    agora = time.time()
    user = current_user()
    if user:
        if agora - st.session_state.get(_SS_ULTIMA, 0) > _idle_seconds():
            logout()
            st.warning("Sessão bloqueada por inatividade.")
        elif user["must_change_password"]:
            _render_troca_senha(user)
            st.stop()
        else:
            st.session_state[_SS_ULTIMA] = agora
            if show_logout:
                # Posicionado na Topbar (canto direito) via CSS de ui_theme (`st-key-bmt-logout`).
                with st.container(key="bmt-logout"):
                    st.button(
                        "Sair", icon=":material/lock:", on_click=logout,
                        help=f"{user['username']} ({user['role']}) — sair / bloquear a sessão",
                    )
            return user
    _render_login()
    st.stop()


def require_admin() -> dict[str, Any]:
    """Exige login + perfil admin (páginas de administração)."""
    user = require_login(show_logout=False)
    if user["role"] != "admin":
        st.error("Acesso restrito a administradores.", icon=":material/block:")
        st.stop()
    return user
