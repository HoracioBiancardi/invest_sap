"""Vendedor x Meta x Faturamento (porte de `pages/24_Vendedor_x_Meta.py`).

Não existe meta por vendedor: `Meta_Valor_BU`/`Valor_Realizado_BU`/`Atingimento_BU` são do grupo
(BU) inteiro, repetidos em cada vendedor da BU; BU vem de `dim_vendedor_sf.Unidade_Negocio`
(~25% preenchido). Não usar para comissão individual."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import faturamento_vendedor_com_meta_bu
from web import fmt
from web.cache import cached
from web.ui import Ctx, Pagina
from web.views._comum import filtro_periodo_tipo_cliente, tipo_cliente_param


@cached(ttl=300, nome="vendedor x meta")
def _dados(inicio: datetime.date, fim: datetime.date, tipo_cliente: Optional[str]) -> pd.DataFrame:
    return faturamento_vendedor_com_meta_bu(data_inicio=inicio, data_fim=fim, tipo_cliente=tipo_cliente)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Vendedor x Meta x Faturamento", "track_changes")
    p.warning(
        "**Não existe meta oficial por vendedor** nesta base — meta é planejamento por Setor/BU "
        "(`vendas.fat_meta_equipe`), sem coluna de vendedor. `Meta_Valor_BU`/`Valor_Realizado_BU`/`Atingimento_BU` "
        "são do **grupo (BU) inteiro**, repetidos em cada vendedor da BU. A BU vem de `dim_vendedor_sf.Unidade_Negocio` "
        "(só ~25% preenchido) — a maioria cai em **'NAO ALOCADO'**. Não use esta página pra comissão individual."
    )
    inicio, fim, tipo_opcao = filtro_periodo_tipo_cliente(p)
    p.caption(f"Filtro: período de **{fmt.data(inicio)}** a **{fmt.data(fim)}**, tipo de cliente **{tipo_opcao}**.")
    df = _dados(inicio, fim, tipo_cliente_param(tipo_opcao))
    if df.empty:
        p.info("Nada encontrado para esse período/filtro.")
        return
    # Meta é sempre BRL — comparação só em BRL.
    p.caption("Faturado no período, por moeda:")
    p.valor_por_moeda(df, "Valor_Faturado")
    brl = df[df["Moeda"] == "BRL"]
    identificados = brl[brl["Codigo_Vendedor"] != "SEM_VENDEDOR"].sort_values("Valor_Faturado", ascending=False)
    com_bu = identificados[identificados["BU"] != "NAO ALOCADO"]
    p.metrics(
        [
            ("Vendedores identificados (BRL)", fmt.num(len(identificados))),
            ("Faturado no período (BRL)", fmt.brl(identificados["Valor_Faturado"].sum())),
            ("% de vendedores com BU cadastrada", fmt.pct(len(com_bu) / len(identificados) if len(identificados) else 0.0, 0)),
        ]
    )
    p.divider()
    p.subheader("Por BU: faturamento de vendedor x meta do grupo")
    if com_bu.empty:
        p.info("Nenhum vendedor com BU cadastrada nesse filtro — sem comparação possível.")
    else:
        resumo = com_bu.groupby("BU").agg(
            Qtd_Vendedores=("Codigo_Vendedor", "nunique"),
            Valor_Faturado_Vendedores=("Valor_Faturado", "sum"),
            Meta_Valor_BU=("Meta_Valor_BU", "first"),
            Valor_Realizado_BU=("Valor_Realizado_BU", "first"),
            Atingimento_BU=("Atingimento_BU", "first"),
        ).sort_values("Valor_Faturado_Vendedores", ascending=False)
        card = p.card()
        x, y = card.columns([2, 1])
        x.bar_chart(
            resumo[["Valor_Faturado_Vendedores", "Meta_Valor_BU"]].rename(columns={"Valor_Faturado_Vendedores": "Faturado vendedores", "Meta_Valor_BU": "Meta da BU"}),
            fmt_valor="brl0",
        )
        y.table(resumo, {"Valor_Faturado_Vendedores": "brl0", "Meta_Valor_BU": "brl0", "Valor_Realizado_BU": "brl0", "Atingimento_BU": "pct1"}, indice=True)
    p.divider()
    p.subheader("Detalhe por vendedor")
    a, _ = p.columns([1, 2])
    top_n = a.slider("Quantos vendedores mostrar", "vmeta_top", 5, 50, 20, 5)
    p.card().table(
        identificados.head(top_n)[["Nome_Vendedor", "BU", "Valor_Faturado", "Qtd_Clientes", "Meta_Valor_BU", "Valor_Realizado_BU", "Atingimento_BU"]],
        {"Valor_Faturado": "brl0", "Meta_Valor_BU": "brl0", "Valor_Realizado_BU": "brl0", "Atingimento_BU": "pct1", "Qtd_Clientes": "num0"},
        nome_arquivo="vendedor_meta",
    )
