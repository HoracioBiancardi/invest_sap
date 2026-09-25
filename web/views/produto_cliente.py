"""Produto | Cliente — preço médio, SKUs vendidos e ranking mensal (porte de
`pages/15_Produto_Cliente.py`). Mesma fonte/caveat do Painel Vendas; **Família** vem de
`vendas.dim_produto` (~316 materiais, instável — sem match cai em 'NAO INFORMADO')."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_faturamento_comercial import (
    DIMENSOES_FATURAMENTO,
    faturamento_por_dimensao,
    faturamento_serie,
    skus_ativos_periodo,
)
from web import fmt
from web.cache import cached
from web.ui import Ctx, Pagina
from web.views._comum import filtros_comercial, tipo_cliente_param


@cached(ttl=900, nome="produto/cliente: série mensal")
def _serie_mes(inicio: datetime.date, fim: datetime.date, filtros: dict) -> pd.DataFrame:
    return faturamento_serie(inicio, fim, granularidade="mes", filtros=filtros)


@cached(ttl=900, nome="produto/cliente: SKUs ativos")
def _skus(inicio: datetime.date, fim: datetime.date, filtros: dict) -> pd.DataFrame:
    return skus_ativos_periodo(inicio, fim, filtros=filtros)


@cached(ttl=900, nome="produto/cliente: faturamento por dimensão (mês)")
def _dimensao_mes(dimensao: str, inicio: datetime.date, fim: datetime.date, tipo_cliente: Optional[str], filtros: dict) -> pd.DataFrame:
    return faturamento_por_dimensao(inicio, fim, dimensao, granularidade="mes", tipo_cliente=tipo_cliente, filtros=filtros)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Visão Produto | Cliente", "category")
    p.caption(
        "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` — mesma fonte/caveat do **Painel Vendas** "
        "(`docs/CONTEXTO_VENDAS_SAP.md` §10). **Família** vem de `vendas.dim_produto` (mapeamento SharePoint, "
        "~316 materiais — produto sem match cai em 'NAO INFORMADO')."
    )
    a, b = p.columns([2, 1])
    meses = a.slider("Janela (meses)", "p15_meses", 3, 24, 12)
    hoje = datetime.date.today()
    inicio = (hoje.replace(day=1) - pd.DateOffset(months=meses - 1)).date()
    tipo_cliente = tipo_cliente_param(b.selectbox("Tipo de cliente", ["Todos", "Governo", "Privado"], "flt_tipo_cliente", compartilhado=True))
    filtros = filtros_comercial(p, "p15", list(DIMENSOES_FATURAMENTO))

    mes = _serie_mes(inicio, hoje, filtros)
    skus = _skus(inicio, hoje, filtros)
    # Valor_Faturado vem por Moeda — preço médio/rankings só fazem sentido em 1 moeda: BRL.
    mes_brl = mes[mes["Moeda"] == "BRL"] if not mes.empty else mes
    if not mes.empty and (mes["Moeda"] != "BRL").any():
        p.caption("Faturamento no período, por moeda (métricas abaixo usam só BRL):")
        p.valor_por_moeda(mes, "Valor_Faturado")
    p.metrics(
        [
            ("Média Vendas/mês (BRL)", fmt.brl(mes_brl["Valor_Faturado"].mean() if not mes_brl.empty else 0.0)),
            ("Média SKUs vendidos/mês", fmt.num(skus["Qtd_SKUs_Vendidos"].mean() if not skus.empty else 0.0)),
            ("Média clientes atendidos/mês", fmt.num(skus["Qtd_Clientes_Atendidos"].mean() if not skus.empty else 0.0)),
        ]
    )
    p.divider()
    card = p.card()
    x, y = card.columns([2, 1])
    x.subheader("Faturamento Bruto e Preço Médio por mês (BRL)")
    if mes_brl.empty:
        x.info("Sem dado no período.")
    else:
        preco = mes_brl.assign(Preco_Medio=mes_brl["Valor_Faturado"] / mes_brl["Qtd_Faturada"].replace(0, pd.NA))
        # Duas medidas de escala diferente = dois gráficos (nunca eixo duplo).
        x.bar_chart(preco.set_index("Mes")["Valor_Faturado"].rename("Faturado (R$)"), fmt_valor="brl0", altura=240)
        x.line_chart(preco.set_index("Mes")["Preco_Medio"].astype(float).rename("Preço médio (R$)"), fmt_valor="brl2", altura=200)
    y.subheader("SKUs vendidos por mês")
    if skus.empty:
        y.info("Sem dado no período.")
    else:
        y.bar_chart(skus.set_index("Mes")["Qtd_SKUs_Vendidos"].rename("SKUs"), altura=440)
    p.divider()

    p.subheader("Ranking mensal (BRL)")
    p.caption("Restrito a `Moeda='BRL'` — pivot/média por dimensão não mistura moeda.")
    c, d = p.columns([1, 1])
    dimensao = c.radio("Quebrar por", ["Cliente", "Família", "Produto"], "p15_quebra")
    todas = _dimensao_mes(dimensao, inicio, hoje, tipo_cliente, filtros)
    dim_mes = todas[todas["Moeda"] == "BRL"] if not todas.empty else todas
    if dim_mes.empty:
        p.info("Nada encontrado para essa combinação de filtro.")
        return
    total = dim_mes.groupby("Dimensao")["Valor_Faturado"].sum().sort_values(ascending=False)
    top_n = d.slider(f"Top N {dimensao.lower()}s (por faturamento total no período)", "p15_top", 5, 50, 15, 5)
    top = total.head(top_n).index
    p.markdown(f"**Faturamento mensal — Top {top_n} {dimensao.lower()}s**")
    pivot = dim_mes[dim_mes["Dimensao"].isin(top)].pivot_table(index="Dimensao", columns="Mes", values="Valor_Faturado", aggfunc="sum", fill_value=0).reindex(top)
    p.card().table(pivot, formato_todas="brl0", indice=True, nome_arquivo=f"ranking_mensal_{dimensao}")
    p.markdown("**Média dos últimos 6 meses (dentro da janela selecionada)**")
    ultimos_6 = sorted(dim_mes["Mes"].unique())[-6:]
    media = (
        dim_mes[dim_mes["Mes"].isin(ultimos_6)]
        .groupby("Dimensao")
        .agg(Qtde_Meses=("Mes", "nunique"), Fatur_Bruto_Medio=("Valor_Faturado", lambda s: s.sum() / len(ultimos_6)))
        .sort_values("Fatur_Bruto_Medio", ascending=False)
        .head(top_n)
    )
    p.card().table(media, {"Fatur_Bruto_Medio": "brl2", "Qtde_Meses": "num0"}, indice=True, nome_arquivo=f"media_6m_{dimensao}")
