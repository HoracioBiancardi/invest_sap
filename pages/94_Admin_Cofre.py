"""Administração → Cofre de credenciais do DW (`scripts/credential_vault.py`).

Criar a partir do .env, desbloquear após reinício, testar conexão, atualizar, trocar a senha
mestra e apagar. Acessível mesmo com o cofre bloqueado (é daqui que o admin o libera).
"""

from __future__ import annotations

import html
import os

import streamlit as st

st.set_page_config(page_title="Cofre — Invest SAP", page_icon="🛠️", layout="wide")

from scripts import app_db  # noqa: E402
from scripts.auth import require_admin  # noqa: E402
from scripts.credential_vault import CHAVES, SENHAS, CofreError, CredentialVault  # noqa: E402
from scripts.db import DatabaseConnectionError, read_hana_sql, read_sql  # noqa: E402
from scripts.ui_theme import badge, section_header  # noqa: E402

admin = require_admin()

st.title(":material/lock: Cofre de Credenciais")
st.caption("Conexões do HANA e do SQL Server cifradas em repouso com senha mestra.")

_ROTULOS_COFRE = {
    "HANA_ADDRESS": "Host", "HANA_PORT": "Porta", "HANA_USER": "Usuário", "HANA_PASSWORD": "Senha",
    "DDIC_SCHEMA": "Schema padrão", "SQLSERVER_HOST": "Host", "SQLSERVER_PORT": "Porta",
    "SQLSERVER_USER": "Usuário", "SQLSERVER_PASSWORD": "Senha",
}


def _campos_credenciais(prefixo: str, padrao: dict[str, str], ajuda_senha: str) -> dict[str, str]:
    """Campos de conexão HANA / SQL Server lado a lado (senhas nunca pré-preenchidas).

    Args:
        prefixo: Prefixo das keys dos widgets (um por formulário).
        padrao: Valores iniciais dos campos que não são senha.
        ajuda_senha: Texto de ajuda dos campos de senha (o que acontece se ficar vazio).

    Returns:
        Valor digitado por chave de `CHAVES`.
    """
    valores: dict[str, str] = {}
    col_hana, col_sql = st.columns(2)
    for coluna, titulo, prefixo_chave in ((col_hana, "SAP HANA", "HANA_"), (col_sql, "SQL Server", "SQLSERVER_")):
        with coluna:
            st.markdown(f"**{titulo}**")
            for chave in CHAVES:
                if not (chave.startswith(prefixo_chave) or (prefixo_chave == "HANA_" and chave == "DDIC_SCHEMA")):
                    continue
                if chave in SENHAS:
                    valores[chave] = st.text_input(
                        _ROTULOS_COFRE[chave], type="password", autocomplete="off",
                        key=f"{prefixo}_{chave}", help=ajuda_senha,
                    )
                else:
                    valores[chave] = st.text_input(
                        _ROTULOS_COFRE[chave], value=padrao.get(chave, ""), key=f"{prefixo}_{chave}"
                    )
    return valores


def _form_apagar_cofre() -> None:
    """Apagar o cofre (recuperação de senha mestra perdida), com confirmação explícita."""
    with st.expander("Perdi a senha mestra / apagar cofre"):
        st.caption(
            "Apaga as credenciais cifradas. O app volta a ler o .env (se ainda tiver as "
            "variáveis) até você criar um cofre novo. Não dá para desfazer."
        )
        confirmar = st.checkbox("Entendo que as credenciais do cofre serão apagadas", key="cofre_apagar_ok")
        if st.button("Apagar cofre", disabled=not confirmar, icon=":material/delete:"):
            CredentialVault.apagar()
            app_db.audit(admin["username"], "cofre_apagado")
            st.rerun()


def _testar_conexoes() -> None:
    """Testa SQL Server e HANA com uma consulta trivial e mostra o resultado em selos."""
    resultados: list[str] = []
    for nome, teste in (
        ("SQL Server", lambda: read_sql("SELECT 1 AS ok")),
        ("SAP HANA", lambda: read_hana_sql("SELECT 1 AS OK FROM DUMMY")),
    ):
        try:
            teste()
        except (DatabaseConnectionError, RuntimeError) as exc:
            resultados.append(f"{badge(nome + ': falhou', 'error')} <span class='bmt-sec-sub'>{html.escape(str(exc)[:160])}</span>")
        else:
            resultados.append(badge(nome + ": ok", "success"))
    st.markdown("<br>".join(resultados), unsafe_allow_html=True)


estado = CredentialVault.estado()
origem = (
    badge("Cofre desbloqueado", "success") if estado["desbloqueado"]
    else badge("Cofre bloqueado", "error") if estado["configurado"]
    else badge("Usando .env (texto puro)", "warn")
)
col_status, col_teste = st.columns([3, 1], vertical_alignment="center")
col_status.markdown(
    f"<span class='bmt-sec-sub'>Origem atual:</span> {origem}", unsafe_allow_html=True
)
if col_teste.button(
    "Testar conexão", icon=":material/network_check:", use_container_width=True,
    disabled=estado["configurado"] and not estado["desbloqueado"],
    help="Roda SELECT 1 no SQL Server e no HANA com as credenciais em uso.",
):
    with st.spinner("Testando conexões…"):
        _testar_conexoes()
st.caption(
    "Cifradas no banco local do app (Fernet + PBKDF2, 600.000 iterações) com uma **senha "
    "mestra** que não é gravada em lugar nenhum. A cada reinício do app, um admin desbloqueia "
    "o cofre com ela."
)
if not estado["configurado"]:
    st.warning(
        "Cofre não configurado: as credenciais estão vindo do **.env** em texto puro.",
        icon=":material/lock_open:",
    )
    with st.form("cofre_criar"):
        st.markdown("Os campos vêm preenchidos com o .env atual; confira e defina a senha mestra.")
        valores = _campos_credenciais(
            "criar", {k: os.environ.get(k, "") for k in CHAVES if k not in SENHAS},
            "Vazio = usar a senha atual do .env.",
        )
        mestra = st.text_input(
            "Senha mestra", type="password", autocomplete="new-password",
            help="Mínimo 10 caracteres e força Forte. Guarde-a: sem ela, é preciso recadastrar tudo.",
        )
        mestra_conf = st.text_input("Confirmar senha mestra", type="password", autocomplete="new-password")
        if st.form_submit_button("Criar cofre", type="primary"):
            for chave in SENHAS:
                valores[chave] = valores[chave] or os.environ.get(chave, "")
            if mestra != mestra_conf:
                st.error("As senhas mestras não conferem.")
            else:
                try:
                    CredentialVault.criar(valores, mestra)
                except CofreError as exc:
                    st.error(str(exc))
                else:
                    app_db.audit(admin["username"], "cofre_criado")
                    st.rerun()
elif not estado["desbloqueado"]:
    st.error("Cofre bloqueado: o app não consegue consultar o DW.", icon=":material/lock:")
    with st.form("cofre_admin_desbloquear"):
        senha = st.text_input("Senha mestra", type="password", autocomplete="off")
        if st.form_submit_button("Desbloquear cofre", type="primary"):
            try:
                CredentialVault.desbloquear(senha)
            except CofreError as exc:
                app_db.audit(admin["username"], "cofre_desbloqueio_falhou")
                st.error(str(exc))
            else:
                app_db.audit(admin["username"], "cofre_desbloqueado")
                st.rerun()
    _form_apagar_cofre()
else:
    ainda_no_env = [k for k in CHAVES if os.environ.get(k)]
    if ainda_no_env:
        st.warning(
            "O app já ignora estas variáveis, mas elas continuam em texto puro no **.env** — "
            "apague-as do arquivo e reinicie o app: " + ", ".join(f"`{k}`" for k in ainda_no_env),
            icon=":material/warning:",
        )
    col_bloq, _ = st.columns([1, 3])
    if col_bloq.button("Bloquear agora", icon=":material/lock:"):
        CredentialVault.bloquear()
        app_db.audit(admin["username"], "cofre_bloqueado")
        st.rerun()

    with st.expander("Atualizar credenciais"):
        with st.form("cofre_atualizar"):
            valores = _campos_credenciais(
                "atualizar", CredentialVault.valores_visiveis(), "Vazio = manter a senha atual."
            )
            mestra = st.text_input("Senha mestra (confirmação)", type="password", autocomplete="off")
            if st.form_submit_button("Salvar credenciais", type="primary"):
                try:
                    CredentialVault.atualizar(valores, mestra)
                except CofreError as exc:
                    st.error(str(exc))
                else:
                    app_db.audit(admin["username"], "cofre_atualizado")
                    st.success("Credenciais atualizadas.")

    with st.expander("Trocar senha mestra"):
        with st.form("cofre_trocar_mestra", clear_on_submit=True):
            atual = st.text_input("Senha mestra atual", type="password", autocomplete="off")
            nova = st.text_input(
                "Nova senha mestra", type="password", autocomplete="new-password",
                help="Mínimo 10 caracteres e força Forte.",
            )
            conf = st.text_input("Confirmar nova senha mestra", type="password", autocomplete="new-password")
            if st.form_submit_button("Trocar senha mestra", type="primary"):
                if nova != conf:
                    st.error("As senhas não conferem.")
                else:
                    try:
                        CredentialVault.trocar_senha_mestra(atual, nova)
                    except CofreError as exc:
                        st.error(str(exc))
                    else:
                        app_db.audit(admin["username"], "cofre_senha_mestra_trocada")
                        st.success("Senha mestra trocada.")

    _form_apagar_cofre()
