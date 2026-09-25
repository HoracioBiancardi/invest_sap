"""Relatório Analítico — detalhe linha a linha (1 linha = 1 item de fatura) com seletor de
colunas (porte de `pages/17_Relatorio_Analitico.py`). Consulta pesada (JOINs linha a linha):
primeira carga de um período/filtro novo pode levar até ~40s."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_faturamento_comercial import (
    COLUNAS_RELATORIO_ANALITICO,
    COLUNAS_RELATORIO_ANALITICO_PADRAO,
    DIMENSOES_FATURAMENTO,
    relatorio_analitico,
)
from web import fmt
from web.cache import cached
from web.ui import Ctx, Node, Pagina
from web.views._comum import filtros_comercial, tipo_cliente_param


@cached(ttl=900, nome="relatório analítico")
def _relatorio(inicio: datetime.date, fim: datetime.date, colunas: tuple[str, ...], tipo_cliente: Optional[str], filtros: dict, limite: int) -> pd.DataFrame:
    return relatorio_analitico(inicio, fim, colunas=list(colunas) or None, tipo_cliente=tipo_cliente, filtros=filtros, limit=limite)


def _filtros(p: Node) -> tuple:
    """Desenha (na página) ou só relê (no bloco, com uma página "sombra") os filtros da URL."""
    hoje = datetime.date.today()
    a, b, c = p.columns(3)
    tipo_cliente = tipo_cliente_param(a.selectbox("Tipo de cliente", ["Todos", "Governo", "Privado"], "flt_tipo_cliente", compartilhado=True))
    inicio = b.date_input("De", "p17_de", hoje.replace(day=1), maximo=hoje)
    fim = c.date_input("Até", "p17_ate", hoje, maximo=hoje)
    filtros = filtros_comercial(p, "p17", list(DIMENSOES_FATURAMENTO))
    x, y = p.columns([2, 1])
    colunas = x.multiselect("Colunas do relatório", list(COLUNAS_RELATORIO_ANALITICO), "p17_colunas", default=COLUNAS_RELATORIO_ANALITICO_PADRAO)
    limite = y.slider("Máximo de linhas", "p17_limite", 100, 10000, 500, 100)
    return tipo_cliente, inicio, fim, filtros, colunas, limite


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Relatório Analítico", "query_stats")
    p.caption(
        "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` — mesma fonte/caveat do **Painel Vendas** "
        "(`docs/CONTEXTO_VENDAS_SAP.md` §10). Detalhe linha a linha (1 linha = 1 item de fatura). Consulta "
        "mais pesada que as outras páginas — a primeira carga de um período/filtro novo pode levar até ~40s."
    )
    _, inicio, fim, _, colunas, _ = _filtros(p)
    if not colunas:
        p.info("Selecione ao menos 1 coluna.")
        return
    if inicio > fim:
        p.warning("'De' não pode ser depois de 'Até'.")
        return
    # A consulta vai num bloco à parte: a tela de filtros responde na hora e a tabela chega depois.
    p.lazy("relatorio", altura=320)


def _bloco_relatorio(p: Node, ctx: Ctx) -> None:
    tipo_cliente, inicio, fim, filtros, colunas, limite = _filtros(Pagina(ctx))
    df = _relatorio(inicio, fim, tuple(colunas), tipo_cliente, filtros, limite)
    if df.empty:
        p.info("Nada encontrado para esse período/filtro.")
        return
    if len(df) == limite:
        p.warning(
            f"Resultado truncado em {fmt.num(limite)} linhas — pode haver mais no período/filtro. Estreite o "
            "período, adicione um filtro de recorte ou aumente o 'Máximo de linhas'."
        )
    formato = {k: v for k, v in {"Valor Faturado": "brl2", "Preço Unitário": "brl2", "Qtd Faturada": "num0"}.items() if k in df.columns}
    p.card().table(df, formato, nome_arquivo="relatorio_analitico")


BLOCOS = {"relatorio": _bloco_relatorio}
