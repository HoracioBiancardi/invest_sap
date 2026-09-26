"""Administração → Dados de negócio: consulta das tabelas manuais e solicitações de ajuste
(porte de `pages/93_Admin_Dados.py`).

`vendas.dim_cliente_setor`/`dim_estrutura`/`fat_meta_equipe` são alimentadas pelo pipeline
(SharePoint) e a conta do app não tem INSERT nesse schema: aqui o admin *consulta* e registra
*solicitações de ajuste* exportáveis em CSV para quem mantém a fonte.
"""

from __future__ import annotations

import csv
import io
from typing import Optional

import pandas as pd

from scripts import app_db
from scripts.db import read_sql
from web.cache import cached
from web.ui import Ctx, Pagina

# Consultas fixas (sem interpolação) — o único valor de fora entra como bind param.
_FONTES = {
    "Cliente → Setor (vendas.dim_cliente_setor)": (
        "Código do cliente",
        "SELECT TOP 500 * FROM vendas.dim_cliente_setor WHERE cod_cliente = :v ORDER BY periodo DESC",
    ),
    "Estrutura (vendas.dim_estrutura)": (None, "SELECT * FROM vendas.dim_estrutura ORDER BY cod_setor"),
    "Meta (vendas.fat_meta_equipe)": (
        "Código do setor",
        "SELECT TOP 500 * FROM vendas.fat_meta_equipe WHERE cod_setor = :v ORDER BY data_meta DESC",
    ),
}


@cached(ttl=300, nome="admin: tabela manual")
def _consultar(query: str, valor: Optional[str]) -> pd.DataFrame:
    return read_sql(query, database="GOLD", params={"v": valor} if valor is not None else None)


def _csv_seguro(linhas: list[dict]) -> str:
    """CSV pro Excel: neutraliza células que começam com = + - @ (injeção de fórmula)."""

    def limpa(v: object) -> str:
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    saida = io.StringIO()
    if linhas:
        w = csv.DictWriter(saida, fieldnames=list(linhas[0].keys()), delimiter=";")
        w.writeheader()
        for linha in linhas:
            w.writerow({k: limpa(v) for k, v in linha.items()})
    return saida.getvalue()


def _baixar_ajustes(ctx: Ctx) -> tuple[bytes, str]:
    status = ctx.get("ajuste_status", "todos")
    ajustes = app_db.listar_ajustes(None if status not in app_db.STATUS_AJUSTE else status)
    return _csv_seguro(ajustes).encode("utf-8-sig"), "text/csv; charset=utf-8"


DOWNLOADS = {"solicitacoes_ajuste.csv": _baixar_ajustes}


def render(p: Pagina, ctx: Ctx) -> None:
    admin = ctx.usuario
    p.title("Dados de Negócio", "database")
    p.caption("Tabelas manuais do DW (só leitura) e solicitações de ajuste para quem mantém a fonte.")

    falhou = False
    if ctx.acao == "novo_ajuste":
        try:
            app_db.criar_ajuste(
                ctx.form_get("tipo"), ctx.form_get("chave"), ctx.form_get("v_atual"),
                ctx.form_get("v_prop"), ctx.form_get("motivo"), admin["username"],
            )
        except app_db.AppDbError as exc:
            p.error(str(exc))
            falhou = True
        else:
            app_db.audit(admin["username"], "ajuste_criado", f"{ctx.form_get('tipo')}: {ctx.form_get('chave')}")
            p.toast("Solicitação registrada.")
    elif ctx.acao == "status_ajuste":
        try:
            ajuste_id = int(ctx.form_get("ajuste_id"))
            app_db.resolver_ajuste(ajuste_id, ctx.form_get("novo_status"))
        except (ValueError, app_db.AppDbError) as exc:
            p.error(str(exc) or "Solicitação inválida.")
        else:
            app_db.audit(admin["username"], "ajuste_status", f"#{ajuste_id} → {ctx.form_get('novo_status')}")
            p.toast("Status atualizado.")

    p.info(
        "Estas tabelas são alimentadas pelo pipeline (SharePoint) e o app só tem acesso de "
        "leitura. Consulte abaixo e registre as correções como **solicitações de ajuste** — "
        "exporte o CSV e envie a quem mantém a fonte.",
    )
    p.subheader("Consultar fonte")
    c1, c2 = p.columns([1, 1])
    fonte = c1.selectbox("Tabela", list(_FONTES), "fonte")
    rotulo, query = _FONTES[fonte]
    valor = c2.text_input(rotulo, "valor").strip() if rotulo else None
    if rotulo and not valor:
        p.caption("Informe o valor para consultar.")
    else:
        with p.card() as card:
            card.table(_consultar(query, valor), nome_arquivo="fonte")

    p.subheader("Solicitações de ajuste")
    with p.card() as card, card.acao("novo_ajuste", manter_valores=falhou) as f:
        # Os 4 campos curtos numa linha e o motivo com 2 linhas: a lista de solicitações cabe na tela.
        a, b, c, d = f.columns(4)
        a.selectbox("Tipo", list(app_db.TIPOS_AJUSTE), "tipo")
        b.text_input("Chave (ex.: código do cliente/setor)", "chave")
        c.text_input("Valor atual (opcional)", "v_atual")
        d.text_input("Valor proposto", "v_prop")
        f.text_area("Motivo / justificativa", "motivo", linhas=2)
        f.submit("Registrar solicitação", icone_nome="add")

    filtro = p.radio("Status", ["todos", *app_db.STATUS_AJUSTE], "ajuste_status")
    ajustes = app_db.listar_ajustes(None if filtro == "todos" else filtro)
    if not ajustes:
        p.caption("Nenhuma solicitação neste filtro.")
        return
    p.table(pd.DataFrame(ajustes), nome_arquivo="solicitacoes_ajuste")
    with p.acao("status_ajuste") as f:
        a, b, c = f.columns([1, 1, 1])
        a.selectbox("Solicitação", [str(x["id"]) for x in ajustes], "ajuste_id", formatar=lambda i: f"#{i}")
        b.selectbox("Novo status", list(app_db.STATUS_AJUSTE), "novo_status")
        c.submit("Atualizar status", icone_nome="sync")
    p.link("Exportar CSV (sanitizado pro Excel)", f"{ctx.caminho}/_download/solicitacoes_ajuste.csv?ajuste_status={filtro}", "download")
