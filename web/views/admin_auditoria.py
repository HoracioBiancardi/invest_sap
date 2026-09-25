"""Administração → Auditoria: últimos eventos do log + estado do cache de consultas
(porte de `pages/95_Admin_Auditoria.py`)."""

from __future__ import annotations

import pandas as pd

from scripts import app_db
from web.cache import Cache
from web.ui import Ctx, Pagina


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Audit Log & Histórico", "history")
    if ctx.acao == "limpar_cache":
        Cache.limpar()
        app_db.audit(ctx.usuario["username"], "cache_limpo")
        p.toast("Cache de consultas esvaziado.")

    abas = p.tabs(["Audit log", "Cache de consultas"], "aba")
    if abas.ativa == 0:
        abas.corpo.caption("Últimos 200 eventos: logins, trocas de senha, cofre e alterações de configuração.")
        abas.corpo.table(pd.DataFrame(app_db.listar_audit()), nome_arquivo="audit_log")
    else:
        abas.corpo.caption(
            "Consultas ao DW guardadas em memória. As marcadas como pré-aquecidas são recalculadas "
            "em segundo plano antes de vencer, para ninguém esperar a consulta fria. Esvaziar força "
            "todo mundo a reconsultar o DW (use depois de uma carga corrigida no DW)."
        )
        abas.corpo.table(pd.DataFrame(Cache.resumo()), nome_arquivo="cache")
        with abas.corpo.acao("limpar_cache") as f:
            f.submit("Esvaziar cache", primario=False, icone_nome="delete_sweep", confirmar="Esvaziar o cache de todas as consultas?")
