"""Administração → Usuários: cadastro de contas, perfis (admin/leitor), ativação e reset de senha.

Só perfil `admin` (`scripts/auth.py::require_admin`). Usuários vivem no SQLite local do app
(`scripts/app_db.py`); senhas temporárias vêm do `PasswordService`.
"""

from __future__ import annotations

import html

import streamlit as st

st.set_page_config(page_title="Usuários — Invest SAP", page_icon="🛠️", layout="wide")

from scripts import app_db  # noqa: E402
from scripts.auth import require_admin  # noqa: E402
from scripts.ui_theme import badge, html_table  # noqa: E402

admin = require_admin()

st.title(":material/group: Gerenciamento de Usuários")
st.caption("Cadastro de contas, perfis de acesso e redefinição de senha.")

_SS_SENHA_NOVO = "admin_novo_senha"


def _gerar_senha_novo() -> None:
    """Callback: preenche a senha temporária do novo usuário com uma senha forte gerada."""
    st.session_state[_SS_SENHA_NOVO] = app_db.gerar_senha_temporaria()


def _sim_nao(valor: int, sim: str = "success", nao: str = "muted") -> str:
    """Selo Sim/Não para as colunas booleanas da tabela.

    Args:
        valor: 1 ou 0 vindo do banco.
        sim: Tipo de selo quando 1.
        nao: Tipo de selo quando 0.

    Returns:
        HTML do selo.
    """
    return badge("Sim", sim) if valor else badge("Não", nao)


usuarios = app_db.listar_usuarios()
html_table(
    ["Usuário", "Perfil", "Ativo", "Troca de senha pendente", "Criado em (UTC)"],
    [
        [
            f"<strong>{html.escape(u['username'])}</strong>",
            badge(u["role"], "primary" if u["role"] == "admin" else "muted"),
            _sim_nao(u["active"], "success", "error"),
            _sim_nao(u["must_change_password"], "warn", "muted"),
            html.escape(u["created_at"]),
        ]
        for u in usuarios
    ],
)
st.write("")

col_novo, col_gerir = st.columns(2, gap="large")
with col_novo, st.container(border=True):
    st.markdown("##### :material/person_add: Novo usuário")
    st.button("🎲 Gerar senha forte", on_click=_gerar_senha_novo, key="admin_gerar_senha")
    with st.form("novo_usuario", clear_on_submit=True, border=False):
        novo_nome = st.text_input("Usuário", placeholder="3 a 32 caracteres")
        novo_role = st.selectbox("Perfil", app_db.ROLES, index=1)
        nova_senha = st.text_input(
            "Senha temporária", type="password", key=_SS_SENHA_NOVO,
            help=f"Mínimo {app_db.MIN_SENHA} caracteres e força Média. Mostre/copie pelo ícone de olho.",
        )
        st.caption("O usuário será obrigado a trocar esta senha no primeiro acesso.")
        if st.form_submit_button("Criar usuário", type="primary", icon=":material/add:"):
            try:
                app_db.criar_usuario(novo_nome, nova_senha, role=novo_role)
            except app_db.AppDbError as exc:
                st.error(str(exc))
            else:
                app_db.audit(admin["username"], "usuario_criado", f"{novo_nome} ({novo_role})")
                st.toast(f"Usuário {novo_nome} criado.", icon=":material/check_circle:")
                st.rerun()

with col_gerir, st.container(border=True):
    st.markdown("##### :material/manage_accounts: Gerenciar usuário")
    alvo = st.selectbox(
        "Usuário", usuarios, format_func=lambda u: f"{u['username']} ({u['role']})",
        key="admin_alvo",
    )
    if alvo:
        eh_o_proprio = alvo["id"] == admin["id"]
        # Keys por usuário: trocar a seleção recarrega perfil/ativo do usuário escolhido.
        novo_perfil = st.selectbox(
            "Perfil", app_db.ROLES, index=app_db.ROLES.index(alvo["role"]),
            key=f"admin_perfil_{alvo['id']}",
        )
        ativo = st.toggle("Ativo", value=bool(alvo["active"]), key=f"admin_ativo_{alvo['id']}")
        c1, c2 = st.columns(2)
        if c1.button("Salvar", type="primary", icon=":material/save:", use_container_width=True,
                     help="Salva perfil e ativação do usuário selecionado."):
            if eh_o_proprio and not ativo:
                st.error("Você não pode desativar a si mesmo.")
            else:
                try:
                    app_db.alterar_usuario(alvo["id"], role=novo_perfil, active=ativo)
                except app_db.AppDbError as exc:
                    st.error(str(exc))
                else:
                    app_db.audit(
                        admin["username"], "usuario_alterado",
                        f"{alvo['username']}: perfil={novo_perfil} ativo={ativo}",
                    )
                    st.toast("Usuário atualizado.", icon=":material/check_circle:")
                    st.rerun()
        if c2.button("Redefinir senha", icon=":material/lock_reset:", use_container_width=True,
                     help="Gera uma senha temporária; o usuário troca no próximo login."):
            temporaria = app_db.gerar_senha_temporaria()
            app_db.definir_senha(alvo["id"], temporaria, must_change_password=True)
            app_db.audit(admin["username"], "senha_redefinida", alvo["username"])
            st.warning(f"Senha temporária de **{alvo['username']}** (mostrada só agora):")
            st.code(temporaria, language=None)
