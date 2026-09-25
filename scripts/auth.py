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

import html
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from scripts import app_db
from scripts.credential_vault import CofreError, CredentialVault
from scripts.password_service import ForcaSenha, PasswordService

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

# Portal de Acesso no visual do app_template (`.login-modal`, `.login-brand`, `.aurora`): cores
# vêm dos tokens do tema ativo (`--primary`, `--surface`...), definidos em ui_theme._theme_css.
_CSS_PORTAL = """
<style>
@keyframes bmtModalPop {
    from { opacity: 0; transform: scale(.94) translateY(14px); }
    to { opacity: 1; transform: scale(1) translateY(0); }
}
@keyframes bmtBrandGlow {
    0%, 100% { box-shadow: 0 0 0 1px var(--primary-dim), 0 2px 8px var(--primary-glow); }
    50% { box-shadow: 0 0 0 4px var(--primary-dim), 0 4px 20px var(--primary-glow); }
}
[class*="st-key-bmt-portal"] {
    margin-top: 8vh; padding: 2rem; background: var(--surface);
    border: 1px solid var(--border); border-top: 2px solid var(--primary);
    border-radius: var(--r-lg); box-shadow: var(--shadow-lg);
    animation: bmtModalPop .22s var(--ease-spring) both;
}
.bmt-login-brand { display: flex; align-items: center; gap: 1rem; margin-bottom: .5rem; }
.bmt-login-icon {
    width: 46px; height: 46px; border-radius: 12px; flex-shrink: 0;
    background: var(--primary-dim); border: 1px solid var(--border-bright); color: var(--primary);
    display: flex; align-items: center; justify-content: center;
    animation: bmtBrandGlow 3s ease-in-out infinite;
}
.bmt-login-title { font-size: 1.25rem; font-weight: 800; letter-spacing: .02em; color: var(--text); }
.bmt-login-sub {
    font-size: .75rem; color: var(--text-subtle); letter-spacing: .04em;
    text-transform: uppercase; margin-top: .15rem;
}
[class*="st-key-bmt-portal"] label p {
    font-size: .72rem !important; font-weight: 700; letter-spacing: .07em;
    text-transform: uppercase; color: var(--text-muted) !important;
}
[class*="st-key-bmt-portal"] div[data-testid="stForm"] {
    background: none; border: 0 !important; box-shadow: none; padding: 0;
}
@media (prefers-reduced-motion: reduce) {
    .bmt-login-icon, [class*="st-key-bmt-portal"] { animation: none; }
}
</style>
"""

# Fundo aurora só nas telas sem sidebar (login/troca de senha): o `z-index` no
# `.block-container` cria um contexto de empilhamento que, com a sidebar aberta (trava do
# cofre), a deixaria por cima da Topbar.
_CSS_AURORA = """
<style>
.block-container { position: relative; z-index: 0; }
.bmt-aurora { position: fixed; inset: 0; z-index: -1; pointer-events: none; overflow: hidden; }
.bmt-orb {
    position: absolute; border-radius: 50%; filter: blur(90px); opacity: .25;
    animation: bmtOrbFloat 28s ease-in-out infinite;
}
.bmt-orb-1 { width: 420px; height: 420px; top: -12%; left: -8%; background: radial-gradient(circle, var(--primary), transparent 70%); }
.bmt-orb-2 { width: 380px; height: 380px; top: 50%; right: -12%; background: radial-gradient(circle, #38bdf8, transparent 70%); animation-delay: -7s; animation-duration: 34s; }
.bmt-orb-3 { width: 320px; height: 320px; bottom: -14%; left: 22%; background: radial-gradient(circle, #818cf8, transparent 70%); animation-delay: -15s; animation-duration: 31s; }
.bmt-orb-4 { width: 260px; height: 260px; top: 12%; right: 28%; background: radial-gradient(circle, var(--success), transparent 70%); animation-delay: -21s; animation-duration: 25s; }
@keyframes bmtOrbFloat {
    0%, 100% { transform: translate(0, 0) scale(1); }
    33% { transform: translate(4%, 5%) scale(1.08); }
    66% { transform: translate(-3%, -4%) scale(.95); }
}
@media (prefers-reduced-motion: reduce) { .bmt-orb { animation: none; } }
</style>
<div class="bmt-aurora">
  <div class="bmt-orb bmt-orb-1"></div><div class="bmt-orb bmt-orb-2"></div>
  <div class="bmt-orb bmt-orb-3"></div><div class="bmt-orb bmt-orb-4"></div>
</div>
"""

_ICONE_CADEADO = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M12 15v2m-6 4h12a2 2 '
    "0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z\" "
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

_COR_NIVEL = {
    "Muito Fraca": "var(--danger)", "Fraca": "var(--danger)", "Média": "var(--warn)",
    "Forte": "var(--success)", "Excelente": "var(--success)",
}
_SS_NOVA = "_auth_nova_senha"
_SS_CONF = "_auth_conf_senha"
_SS_SUGERIDA = "_auth_senha_sugerida"


def _idle_seconds() -> int:
    minutos = app_db.get_setting("idle_minutes")
    if not isinstance(minutos, int) or minutos < 1:
        minutos = 30
    return minutos * 60


def _garantir_admin_inicial() -> None:
    if app_db.contar_usuarios() > 0:
        return
    senha = os.environ.get("APP_ACCESS_KEY", "").strip()
    definida_no_env = PasswordService.aceitavel(senha)
    if senha and not definida_no_env:
        logger.warning("APP_ACCESS_KEY ignorada: senha curta ou fraca (mínimo força Média).")
    if not definida_no_env:
        senha = PasswordService.generate(20)
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


def _abrir_portal() -> None:
    """Esconde a sidebar e desenha o fundo aurora do Portal de Acesso.

    Chamar FORA do container do modal: a animação de entrada dele usa `transform`, que
    viraria o bloco de contenção do `position: fixed` da aurora.
    """
    st.markdown(_CSS_ESCONDE_SIDEBAR + _CSS_PORTAL + _CSS_AURORA, unsafe_allow_html=True)


def _marca_portal(subtitulo: str) -> None:
    """Cabeçalho do modal: ícone de cadeado + "SwordPower" + subtítulo.

    Args:
        subtitulo: Texto sob "SwordPower" (ex.: "Portal de Acesso").
    """
    st.markdown(
        f'''<div class="bmt-login-brand"><div class="bmt-login-icon">{_ICONE_CADEADO}</div>
<div><div class="bmt-login-title">SwordPower</div>
<div class="bmt-login-sub">{html.escape(subtitulo)}</div></div></div>''',
        unsafe_allow_html=True,
    )


def _render_forca(senha: str) -> None:
    """Medidor de força da senha (barra + nível + dicas), como o avaliador do app_template.

    Args:
        senha: Senha digitada; nada é desenhado se vazia.
    """
    if not senha:
        return
    forca: ForcaSenha = PasswordService.evaluate(senha)
    cor = _COR_NIVEL[forca.nivel]
    dicas = " · ".join(html.escape(d) for d in forca.feedback)
    st.markdown(
        f'''<div class="bmt-forca"><div class="bmt-forca-track">
<div class="bmt-forca-fill" style="width:{forca.score}%;background:{cor}"></div></div>
<div class="bmt-forca-info"><span class="bmt-badge" style="color:{cor}">{html.escape(forca.nivel)}</span>
<span>{dicas}</span></div></div>''',
        unsafe_allow_html=True,
    )


def _sugerir_senha() -> None:
    """Callback: preenche nova senha + confirmação com uma senha forte gerada."""
    sugerida = PasswordService.generate(16)
    st.session_state[_SS_NOVA] = sugerida
    st.session_state[_SS_CONF] = sugerida
    st.session_state[_SS_SUGERIDA] = sugerida


def _render_login() -> None:
    """Tela de login (Portal de Acesso); autentica, aplica lockout e registra auditoria."""
    _abrir_portal()
    _, centro, _ = st.columns([1, 1.3, 1])
    with centro, st.container(key="bmt-portal-login"):
        _marca_portal("Portal de Acesso · Invest SAP")
        with st.form("login", border=False):
            usuario = st.text_input("Usuário", autocomplete="username", placeholder="seu usuário")
            senha = st.text_input(
                "Senha", type="password", autocomplete="current-password",
                placeholder="Digite sua senha…",
            )
            st.caption("Sessão apenas em memória, com bloqueio automático por inatividade.")
            enviar = st.form_submit_button("Entrar / Desbloquear", type="primary", use_container_width=True)
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
    """Troca obrigatória de senha, com medidor de força e sugestão de senha gerada.

    Sem `st.form` de propósito: o medidor precisa reagir ao valor digitado (atualiza ao sair
    do campo ou apertar Enter).

    Args:
        user: Usuário logado com `must_change_password` ligado.
    """
    _abrir_portal()
    _, centro, _ = st.columns([1, 1.3, 1])
    with centro, st.container(key="bmt-portal-senha"):
        _marca_portal("Defina uma nova senha")
        st.caption(
            f"Olá, {user['username']}. Sua senha atual é temporária — troque para continuar "
            f"(mínimo {PasswordService.MIN_TAMANHO} caracteres, força Média)."
        )
        nova = st.text_input(
            "Nova senha", type="password", autocomplete="new-password", key=_SS_NOVA,
            help="A força é avaliada ao apertar Enter ou sair do campo.",
        )
        _render_forca(nova)
        conf = st.text_input(
            "Confirmar nova senha", type="password", autocomplete="new-password", key=_SS_CONF
        )
        salvar = st.button("Salvar", type="primary", use_container_width=True)
        st.button("Sugerir senha forte", icon=":material/key:", on_click=_sugerir_senha, use_container_width=True)
        sugerida = st.session_state.get(_SS_SUGERIDA)
        if sugerida:
            st.caption("Senha sugerida (já preenchida acima) — guarde antes de salvar:")
            st.code(sugerida, language=None)
        if not salvar:
            return
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
                for chave in (_SS_NOVA, _SS_CONF, _SS_SUGERIDA):
                    st.session_state.pop(chave, None)
                st.rerun()


_MS_ATUAL = "_ms_atual"
_MS_NOVA = "_ms_nova"
_MS_CONF = "_ms_conf"
_MS_SUGERIDA = "_ms_sugerida"


def _sugerir_minha_senha() -> None:
    """Callback do diálogo "Minha senha": preenche nova senha + confirmação com uma gerada."""
    sugerida = PasswordService.generate(16)
    st.session_state[_MS_NOVA] = sugerida
    st.session_state[_MS_CONF] = sugerida
    st.session_state[_MS_SUGERIDA] = sugerida


@st.dialog("Trocar minha senha")
def dialog_minha_senha(user: dict[str, Any]) -> None:
    """Troca voluntária da própria senha (botão "Minha senha" da Topbar).

    Exige a senha atual (sessão aberta num computador desbloqueado não basta para trocar),
    mede a força ao vivo e oferece senha gerada, como no input_arquivos.

    Args:
        user: Usuário logado.
    """
    st.text_input("Senha atual", type="password", autocomplete="current-password", key=_MS_ATUAL)
    nova = st.text_input(
        "Nova senha", type="password", autocomplete="new-password", key=_MS_NOVA,
        help="A força é avaliada ao apertar Enter ou sair do campo.",
    )
    _render_forca(nova)
    st.text_input("Confirmar nova senha", type="password", autocomplete="new-password", key=_MS_CONF)
    st.button("🎲 Gerar senha forte", on_click=_sugerir_minha_senha)
    if st.session_state.get(_MS_SUGERIDA):
        st.caption("Senha gerada (já preenchida acima) — guarde antes de salvar:")
        st.code(st.session_state[_MS_SUGERIDA], language=None)
    st.caption(f"Mínimo {PasswordService.MIN_TAMANHO} caracteres e força Média.")
    if not st.button("Trocar senha", type="primary", use_container_width=True):
        return
    atual = st.session_state.get(_MS_ATUAL, "")
    conf = st.session_state.get(_MS_CONF, "")
    fresco = app_db.get_user_by_id(user["id"])
    if not fresco or not app_db.verify_password(atual, fresco["password_hash"]):
        app_db.audit(user["username"], "senha_troca_falhou")
        st.error("Senha atual incorreta.")
    elif nova != conf:
        st.error("As senhas não conferem.")
    elif nova == atual:
        st.error("A nova senha deve ser diferente da atual.")
    else:
        try:
            app_db.definir_senha(user["id"], nova, must_change_password=False)
        except app_db.AppDbError as exc:
            st.error(str(exc))
        else:
            app_db.audit(user["username"], "senha_alterada")
            for chave in (_MS_ATUAL, _MS_NOVA, _MS_CONF, _MS_SUGERIDA):
                st.session_state.pop(chave, None)
            st.toast("Senha trocada.", icon=":material/check_circle:")
            st.rerun()


def render_acoes_topbar(user: dict[str, Any], pagina_admin: Any = None) -> None:
    """Ações do usuário no canto direito da Topbar (`.topbar-right` do input_arquivos).

    Selo de conexão (cofre de credenciais liberado ou não), saudação, atalho do Admin (se
    `pagina_admin` e perfil admin), "Minha senha", "Configurações" e "Sair".

    Args:
        user: Usuário logado.
        pagina_admin: `st.Page` do painel admin; só pode ser passado depois de
            `st.navigation` (o `st.page_link` exige a página registrada).
    """
    from scripts.ui_theme import dialog_configuracoes  # import tardio: ui_theme não importa auth

    liberado = not CredentialVault.configurado() or CredentialVault.desbloqueado()
    selo = (
        '<span class="bmt-conn">online</span>' if liberado
        else '<span class="bmt-conn bmt-conn--off">cofre bloqueado</span>'
    )
    with st.container(key="bmt-acoes", horizontal=True):
        st.markdown(
            f'{selo} <span class="bmt-hello">Olá, {html.escape(user["username"])}</span>',
            unsafe_allow_html=True,
        )
        if pagina_admin is not None and user["role"] == "admin":
            st.page_link(pagina_admin, label="Admin", icon=":material/admin_panel_settings:", help="Área administrativa")
        if st.button("Minha senha", icon=":material/key:", key="bmt_btn_minha_senha", help="Trocar minha senha"):
            dialog_minha_senha(user)
        if st.button("Configurações", icon=":material/settings:", key="bmt_btn_config", help="Configurações de aparência"):
            dialog_configuracoes()
        st.button(
            "Sair", icon=":material/logout:", key="bmt_btn_sair", on_click=logout,
            help=f"{user['username']} ({user['role']}) — sair / bloquear a sessão",
        )


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
                render_acoes_topbar(user)
            return user
    _render_login()
    st.stop()


def require_cofre(user: dict[str, Any]) -> None:
    """Trava o app enquanto o cofre de credenciais estiver configurado e bloqueado.

    Depois de um reinício do processo as credenciais do DW não estão na memória: o admin vê
    o desbloqueio (senha mestra); o leitor vê um aviso para chamar um admin. Sem cofre
    configurado (credenciais no .env) ou já desbloqueado, não faz nada.

    Args:
        user: Usuário logado.
    """
    if not CredentialVault.configurado() or CredentialVault.desbloqueado():
        return
    st.markdown(_CSS_PORTAL, unsafe_allow_html=True)
    _, centro, _ = st.columns([1, 1.3, 1])
    with centro, st.container(key="bmt-portal-cofre"):
        _marca_portal("Cofre de credenciais bloqueado")
        if user["role"] != "admin":
            st.info(
                "O app foi reiniciado e as credenciais do banco estão trancadas. "
                "Peça a um administrador para desbloquear o cofre.",
                icon=":material/lock:",
            )
            st.stop()
        st.caption("O app foi reiniciado. Digite a senha mestra para liberar as consultas ao DW.")
        with st.form("cofre_desbloquear", border=False):
            senha = st.text_input("Senha mestra", type="password", autocomplete="off")
            enviar = st.form_submit_button("Desbloquear cofre", type="primary", use_container_width=True)
        if enviar:
            try:
                CredentialVault.desbloquear(senha)
            except CofreError as exc:
                app_db.audit(user["username"], "cofre_desbloqueio_falhou")
                st.error(str(exc))
            else:
                app_db.audit(user["username"], "cofre_desbloqueado")
                st.rerun()
        st.caption("Perdeu a senha mestra? Admin → Cofre → Apagar cofre, e recadastre as credenciais.")
    st.stop()


def require_admin() -> dict[str, Any]:
    """Exige login + perfil admin (páginas de administração)."""
    user = require_login(show_logout=False)
    if user["role"] != "admin":
        st.error("Acesso restrito a administradores.", icon=":material/block:")
        st.stop()
    return user
