"""Administração → Cofre de credenciais do DW (porte de `pages/94_Admin_Cofre.py`).

Criar a partir do .env, desbloquear após reinício, testar conexão, atualizar, trocar a senha
mestra e apagar. Acessível com o cofre bloqueado (é daqui que o admin o libera). Senhas nunca
voltam para a tela.
"""

from __future__ import annotations

import os

from markupsafe import Markup, escape

from scripts import app_db
from scripts.credential_vault import CHAVES, SENHAS, CofreError, CredentialVault
from scripts.db import DatabaseConnectionError, read_hana_sql, read_sql
from web.cache import Cache
from web.ui import Ctx, Node, Pagina

_ROTULOS = {
    "HANA_ADDRESS": "Host", "HANA_PORT": "Porta", "HANA_USER": "Usuário", "HANA_PASSWORD": "Senha",
    "DDIC_SCHEMA": "Schema padrão", "SQLSERVER_HOST": "Host", "SQLSERVER_PORT": "Porta",
    "SQLSERVER_USER": "Usuário", "SQLSERVER_PASSWORD": "Senha",
}


def _campos(f: Node, prefixo: str, padrao: dict[str, str], ajuda_senha: str) -> None:
    """Campos HANA / SQL Server lado a lado (senhas nunca pré-preenchidas)."""
    col_hana, col_sql = f.columns(2)
    for coluna, titulo, prefixo_chave in ((col_hana, "SAP HANA", "HANA_"), (col_sql, "SQL Server", "SQLSERVER_")):
        coluna.markdown(f"**{titulo}**")
        for chave in CHAVES:
            if not (chave.startswith(prefixo_chave) or (prefixo_chave == "HANA_" and chave == "DDIC_SCHEMA")):
                continue
            if chave in SENHAS:
                coluna.text_input(_ROTULOS[chave], f"{prefixo}_{chave}", tipo="password", autocomplete="off", help=ajuda_senha)
            else:
                coluna.text_input(_ROTULOS[chave], f"{prefixo}_{chave}", default=padrao.get(chave, ""))


def _valores(ctx: Ctx, prefixo: str) -> dict[str, str]:
    return {chave: ctx.form_get(f"{prefixo}_{chave}") for chave in CHAVES}


def _testar_conexoes(p: Node) -> None:
    partes = []
    for nome, teste in (
        ("SQL Server", lambda: read_sql("SELECT 1 AS ok")),
        ("SAP HANA", lambda: read_hana_sql("SELECT 1 AS OK FROM DUMMY")),
    ):
        try:
            teste()
        except (DatabaseConnectionError, RuntimeError) as exc:
            partes.append(p.badge(nome + ": falhou", "erro") + Markup(f' <span class="caption">{escape(str(exc)[:160])}</span>'))
        else:
            partes.append(p.badge(nome + ": ok", "ok"))
    p.html(Markup("<p>") + Markup("<br>").join(partes) + Markup("</p>"))


def _form_apagar(p: Node) -> None:
    corpo = p.expander("Perdi a senha mestra / apagar cofre")
    corpo.caption(
        "Apaga as credenciais cifradas. O app volta a ler o .env (se ainda tiver as variáveis) "
        "até você criar um cofre novo. Não dá para desfazer."
    )
    with corpo.acao("apagar") as f:
        f.submit("Apagar cofre", primario=False, icone_nome="delete",
                 confirmar="Apagar o cofre de credenciais? Não dá para desfazer.")


def _processar(p: Pagina, ctx: Ctx) -> None:
    usuario = ctx.usuario["username"]
    acao = ctx.acao
    try:
        if acao == "criar":
            valores = _valores(ctx, "criar")
            for chave in SENHAS:
                valores[chave] = valores[chave] or os.environ.get(chave, "")
            if ctx.form_get("mestra") != ctx.form_get("mestra_conf"):
                raise CofreError("As senhas mestras não conferem.")
            CredentialVault.criar(valores, ctx.form_get("mestra"))
            app_db.audit(usuario, "cofre_criado")
            p.toast("Cofre criado.")
        elif acao == "desbloquear":
            try:
                CredentialVault.desbloquear(ctx.form_get("senha"))
            except CofreError:
                app_db.audit(usuario, "cofre_desbloqueio_falhou")
                raise
            app_db.audit(usuario, "cofre_desbloqueado")
            p.toast("Cofre desbloqueado.")
        elif acao == "bloquear":
            CredentialVault.bloquear()
            app_db.audit(usuario, "cofre_bloqueado")
            p.toast("Cofre bloqueado.")
        elif acao == "atualizar":
            CredentialVault.atualizar(_valores(ctx, "atualizar"), ctx.form_get("mestra"))
            Cache.limpar()
            app_db.audit(usuario, "cofre_atualizado")
            p.success("Credenciais atualizadas.")
        elif acao == "trocar_mestra":
            if ctx.form_get("nova") != ctx.form_get("conf"):
                raise CofreError("As senhas não conferem.")
            CredentialVault.trocar_senha_mestra(ctx.form_get("atual"), ctx.form_get("nova"))
            app_db.audit(usuario, "cofre_senha_mestra_trocada")
            p.success("Senha mestra trocada.")
        elif acao == "apagar":
            CredentialVault.apagar()
            app_db.audit(usuario, "cofre_apagado")
            p.toast("Cofre apagado.")
    except CofreError as exc:
        p.error(str(exc))


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Cofre de Credenciais", "lock")
    p.caption("Conexões do HANA e do SQL Server cifradas em repouso com senha mestra.")
    if ctx.acao:
        _processar(p, ctx)

    estado = CredentialVault.estado()
    origem = (
        p.badge("Cofre desbloqueado", "ok") if estado["desbloqueado"]
        else p.badge("Cofre bloqueado", "erro") if estado["configurado"]
        else p.badge("Usando .env (texto puro)", "aviso")
    )
    col_status, col_teste = p.columns([3, 1])
    col_status.html(Markup('<p><span class="caption">Origem atual:</span> ') + origem + Markup("</p>"))
    testar = col_teste.button(
        "Testar conexão", "testar", icone_nome="network_check",
        desabilitado=estado["configurado"] and not estado["desbloqueado"],
    )
    if testar:
        _testar_conexoes(p)
    p.caption(
        "Cifradas no banco local do app (Fernet + PBKDF2, 600.000 iterações) com uma **senha mestra** "
        "que não é gravada em lugar nenhum. A cada reinício do app, um admin desbloqueia o cofre com ela."
    )

    if not estado["configurado"]:
        p.warning("Cofre não configurado: as credenciais estão vindo do **.env** em texto puro.", "lock_open")
        with p.card() as card, card.acao("criar") as f:
            f.markdown("Os campos vêm preenchidos com o .env atual; confira e defina a senha mestra.")
            _campos(f, "criar", {k: os.environ.get(k, "") for k in CHAVES if k not in SENHAS}, "Vazio = usar a senha atual do .env.")
            a, b = f.columns(2)
            a.text_input("Senha mestra", "mestra", tipo="password", autocomplete="new-password",
                         help="Mínimo 10 caracteres e força Forte. Guarde-a: sem ela, é preciso recadastrar tudo.")
            b.text_input("Confirmar senha mestra", "mestra_conf", tipo="password", autocomplete="new-password")
            f.submit("Criar cofre", icone_nome="lock")
        return

    if not estado["desbloqueado"]:
        p.error("Cofre bloqueado: o app não consegue consultar o DW.", "lock")
        col, _ = p.columns([1, 1])
        with col.card() as card, card.acao("desbloquear") as f:
            f.text_input("Senha mestra", "senha", tipo="password", autocomplete="off")
            f.submit("Desbloquear cofre", icone_nome="lock_open")
        _form_apagar(p)
        return

    ainda_no_env = [k for k in CHAVES if os.environ.get(k)]
    if ainda_no_env:
        p.warning(
            "O app já ignora estas variáveis, mas elas continuam em texto puro no **.env** — apague-as "
            "do arquivo e reinicie o app: " + ", ".join(f"`{k}`" for k in ainda_no_env),
        )
    with p.acao("bloquear") as f:
        f.submit("Bloquear agora", primario=False, icone_nome="lock")

    with p.expander("Atualizar credenciais").acao("atualizar") as f:
        _campos(f, "atualizar", CredentialVault.valores_visiveis(), "Vazio = manter a senha atual.")
        f.text_input("Senha mestra (confirmação)", "mestra", tipo="password", autocomplete="off")
        f.submit("Salvar credenciais", icone_nome="save")

    with p.expander("Trocar senha mestra").acao("trocar_mestra") as f:
        f.text_input("Senha mestra atual", "atual", tipo="password", autocomplete="off")
        f.text_input("Nova senha mestra", "nova", tipo="password", autocomplete="new-password", help="Mínimo 10 caracteres e força Forte.")
        f.text_input("Confirmar nova senha mestra", "conf", tipo="password", autocomplete="new-password")
        f.submit("Trocar senha mestra", icone_nome="key")

    _form_apagar(p)
