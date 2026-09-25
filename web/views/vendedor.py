"""Visão do Vendedor — ranking de faturamento por vendedor + detalhe individual (porte de
`pages/18_Visao_Vendedor.py`). Vendedor só existe quando a origem é Salesforce (join VBPA
quebrado na origem SAP): o resto cai em 'Sem Vendedor Identificado' — não usar ranking pra
decisão de performance/comissão sem olhar essa fatia."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import faturamento_por_vendedor, faturamento_vendedor_mensal, top_clientes_por_vendedor
from web import fmt
from web.cache import cached
from web.ui import Ctx, Pagina
from web.views._comum import filtro_periodo_tipo_cliente, tipo_cliente_param


@cached(ttl=300, nome="vendedor: faturamento por vendedor")
def _por_vendedor(inicio: datetime.date, fim: datetime.date, tipo_cliente: Optional[str]) -> pd.DataFrame:
    return faturamento_por_vendedor(inicio, fim, tipo_cliente=tipo_cliente)


@cached(ttl=300, nome="vendedor: tendência mensal")
def _tendencia(codigo: str, meses: int) -> pd.DataFrame:
    return faturamento_vendedor_mensal(codigo, meses=meses)


@cached(ttl=300, nome="vendedor: top clientes")
def _top_clientes(codigo: str, inicio: datetime.date, fim: datetime.date) -> pd.DataFrame:
    return top_clientes_por_vendedor(codigo, inicio, fim, n=15)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Visão do Vendedor", "badge")
    p.caption(
        "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap.Codigo_Vendedor`, nome via `dim_vendedor_sf`. **Só "
        "existe vendedor identificado quando a origem é Salesforce** — a origem SAP está sempre vazia (join VBPA "
        "quebrado). Item sem vendedor cai em **'Sem Vendedor Identificado'** — **não confie em rankings aqui pra "
        "decisão de performance/comissão individual sem checar essa fatia primeiro**."
    )
    inicio, fim, tipo_opcao = filtro_periodo_tipo_cliente(p)
    df = _por_vendedor(inicio, fim, tipo_cliente_param(tipo_opcao))
    if df.empty:
        p.info("Nada encontrado para esse período/filtro.")
        return
    # 1 linha por Vendedor+Moeda — nunca somar entre moedas; ranking restrito a BRL.
    p.caption("Faturado no período, por moeda:")
    p.valor_por_moeda(df, "Valor_Faturado")
    brl = df[df["Moeda"] == "BRL"]
    total = brl["Valor_Faturado"].sum()
    sem = brl.loc[brl["Codigo_Vendedor"] == "SEM_VENDEDOR", "Valor_Faturado"].sum()
    pct_sem = sem / total if total else 0.0
    identificados = brl[brl["Codigo_Vendedor"] != "SEM_VENDEDOR"].sort_values("Valor_Faturado", ascending=False)
    topo = identificados.iloc[0] if not identificados.empty else None
    p.metrics(
        [
            ("Faturado no período (BRL)", fmt.brl(total)),
            ("Vendedores identificados", fmt.num(len(identificados))),
            ("Top vendedor", topo["Nome_Vendedor"] if topo is not None else "—", None, fmt.brl(topo["Valor_Faturado"]) if topo is not None else None),
            ("Sem vendedor identificado", fmt.pct(pct_sem, 0), None,
             f"{fmt.brl(sem)} de {fmt.brl(total)} sem Codigo_Vendedor resolvido nesse período."),
        ]
    )
    if pct_sem >= 0.3:
        p.warning(
            f"**{fmt.pct(pct_sem, 0)} do faturamento do período ({fmt.brl(sem)}) está sem vendedor identificado** — "
            "fatia grande demais pra ignorar ao ler os rankings, principalmente pra comparar pessoas."
        )
    p.divider()
    p.subheader("Ranking de vendedores (BRL)")
    a, _ = p.columns([1, 2])
    top_n = a.slider("Quantos vendedores mostrar", "vend_top", 5, 50, 20, 5)
    top = identificados.head(top_n)
    card = p.card()
    x, y = card.columns([2, 1])
    x.bar_chart(top.set_index("Nome_Vendedor")["Valor_Faturado"].rename("Faturado (R$)"), horizontal=True, fmt_valor="brl0",
                altura=max(260, 24 * len(top) + 40))
    y.table(top[["Nome_Vendedor", "Valor_Faturado", "Qtd_Clientes"]], {"Valor_Faturado": "brl0", "Qtd_Clientes": "num0"})
    p.divider()

    p.subheader("Detalhe de 1 vendedor")
    if identificados.empty:
        p.info("Nenhum vendedor identificado nesse período/filtro.")
        return
    opcoes = dict(zip(identificados["Nome_Vendedor"], identificados["Codigo_Vendedor"]))
    b, c = p.columns(2)
    nome = b.selectbox("Vendedor", list(opcoes), "vend_nome")
    meses = c.slider("Janela da tendência mensal (meses)", "vend_meses", 3, 24, 12)
    codigo = opcoes[nome]
    tendencia = _tendencia(codigo, meses)
    if tendencia.empty:
        p.info("Sem faturamento nesse vendedor na janela selecionada.")
    else:
        p.card().bar_chart(tendencia.pivot_table(index="Mes", columns="Moeda", values="Valor_Faturado", aggfunc="sum", fill_value=0))
    p.caption(f"Top clientes de **{nome}** no período do filtro — coluna `Moeda` ao lado (não somar entre moedas).")
    clientes = _top_clientes(codigo, inicio, fim)
    if clientes.empty:
        p.info("Sem faturamento desse vendedor no período do filtro.")
    else:
        p.card().table(clientes, {"Valor_Faturado": "num2", "Qtd_Faturada": "num0"}, nome_arquivo="top_clientes_vendedor")
