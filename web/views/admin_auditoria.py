"""Administração → Auditoria: últimos eventos do log + estado do cache de consultas
(porte de `pages/95_Admin_Auditoria.py`)."""

from __future__ import annotations

import pandas as pd

from scripts import app_db
from web.cache import Cache
from web.ui import Ctx, Pagina

# Código gravado no audit_log → texto da tela.
_ACOES = {
    "login": "Login", "logout": "Saída", "login_falhou": "Login falhou",
    "senha_alterada": "Senha alterada", "senha_troca_falhou": "Troca de senha falhou",
    "senha_redefinida": "Senha redefinida (admin)", "senha_redefinida_cli": "Senha redefinida (linha de comando)",
    "usuario_criado": "Usuário criado", "usuario_alterado": "Usuário alterado", "bootstrap_admin": "Admin inicial criado",
    "cofre_criado": "Cofre criado", "cofre_desbloqueado": "Cofre desbloqueado", "cofre_bloqueado": "Cofre bloqueado",
    "cofre_desbloqueio_falhou": "Desbloqueio do cofre falhou", "cofre_atualizado": "Credenciais atualizadas",
    "cofre_senha_mestra_trocada": "Senha mestra trocada", "cofre_apagado": "Cofre apagado",
    "cache_limpo": "Cache esvaziado", "ajuste_criado": "Solicitação de ajuste", "ajuste_status": "Status de ajuste",
    "config_alterada": "Configuração alterada",
}
_FALHAS = {"login_falhou", "senha_troca_falhou", "cofre_desbloqueio_falhou"}


def _eventos() -> pd.DataFrame:
    """Log com nomes legíveis: quando (dd/mm/aaaa hh:mm:ss), usuário, ação e detalhe."""
    df = pd.DataFrame(app_db.listar_audit())
    if df.empty:
        return pd.DataFrame(columns=["Quando", "Usuário", "Ação", "Detalhe"])
    quando = pd.to_datetime(df["ts"], errors="coerce")
    return pd.DataFrame({
        "Quando": quando.dt.strftime("%d/%m/%Y %H:%M:%S").fillna(df["ts"]),
        "Usuário": df["usuario"],
        "Ação": df["acao"].map(lambda a: _ACOES.get(a, a)),
        "Detalhe": df["detalhe"],
    })


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Audit Log & Histórico", "history")
    if ctx.acao == "limpar_cache":
        Cache.limpar()
        app_db.audit(ctx.usuario["username"], "cache_limpo")
        p.toast("Cache de consultas esvaziado.")

    abas = p.tabs(["Audit log", "Cache de consultas"], "aba")
    if abas.ativa == 0:
        eventos = _eventos()
        codigos = pd.Series([e["acao"] for e in app_db.listar_audit()], dtype="object")
        abas.corpo.metrics([
            ("Eventos", len(eventos), None, "Últimos 200 registros do log."),
            ("Logins", int((codigos == "login").sum())),
            ("Falhas", int(codigos.isin(_FALHAS).sum()), None, "Login, troca de senha ou desbloqueio do cofre que falharam."),
            ("Usuários distintos", int(eventos["Usuário"].dropna().nunique())),
        ])
        abas.corpo.table(eventos, nome_arquivo="audit_log", altura="tela")
    else:
        abas.corpo.caption(
            "Consultas ao DW guardadas em memória. As marcadas como pré-aquecidas são recalculadas "
            "em segundo plano antes de vencer, para ninguém esperar a consulta fria. Esvaziar força "
            "todo mundo a reconsultar o DW (use depois de uma carga corrigida no DW)."
        )
        abas.corpo.table(pd.DataFrame(Cache.resumo()), nome_arquivo="cache", altura="tela")
        with abas.corpo.acao("limpar_cache") as f:
            f.submit("Esvaziar cache", primario=False, icone_nome="delete_sweep", confirmar="Esvaziar o cache de todas as consultas?")
