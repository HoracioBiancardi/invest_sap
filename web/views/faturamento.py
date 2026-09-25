"""Faturamento — Org Vendas x Linha de Negócio e tendência mensal com devoluções (porte de
`pages/22_Faturamento.py`). Só a aba aberta consulta o DW."""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import devolucoes_mensal, faturamento_mensal, faturamento_por_org_vendas_linha_negocio
from web import fmt
from web.cache import cached, em_paralelo
from web.ui import Ctx, Node, Pagina
from web.views._comum import (
    config,
    filtro_periodo_tipo_cliente,
    nota_config,
    para_brl,
    tipo_cliente_param,
    valor_convertido_brl,
)


@cached(ttl=300, nome="faturamento: org vendas x linha de negócio")
def _org(inicio: datetime.date, fim: datetime.date, tipo_cliente: Optional[str], excl_interco: bool, excl_org: bool) -> pd.DataFrame:
    return faturamento_por_org_vendas_linha_negocio(
        data_inicio=inicio, data_fim=fim, tipo_cliente=tipo_cliente,
        excluir_intercompany=excl_interco, excluir_org_vendas_internacional=excl_org,
    )


@cached(ttl=1800, nome="faturamento: histórico mensal")
def _historico(meses: int, excl_interco: bool) -> dict[str, pd.DataFrame]:
    return em_paralelo(
        faturamento=lambda: faturamento_mensal(meses=meses, excluir_intercompany=excl_interco),
        devolucoes=lambda: devolucoes_mensal(meses=meses, excluir_intercompany=excl_interco),
    )


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Faturamento", "payments")
    inicio, fim, tipo_opcao = filtro_periodo_tipo_cliente(p)
    p.caption(
        f"Filtro: período de **{fmt.data(inicio)}** a **{fmt.data(fim)}**, tipo de cliente **{tipo_opcao}**. A "
        "tendência mensal ignora esse período de propósito (mostra o prazo mais longo)."
        + nota_config(intercompany=True, org_internacional=True)
    )
    abas = p.tabs(["Org Vendas x Linha de Negócio", "Tendência mensal"], "aba")
    if abas.ativa == 0:
        _aba_org(abas.corpo, inicio, fim, tipo_cliente_param(tipo_opcao))
    else:
        _aba_tendencia(abas.corpo)


def _aba_org(p: Node, inicio: datetime.date, fim: datetime.date, tipo_cliente: Optional[str]) -> None:
    p.caption(
        "Organização de Vendas é o campo SAP (VKORG). Linha de Negócio vem do cruzamento Cliente → "
        "`dim_cliente_setor`/`dim_estrutura` (~52%) com fallback por heurística de produto dominante (~87%) — "
        "`Origem_Linha_Negocio` no detalhe indica qual camada resolveu. São 2 dimensões independentes."
    )
    df = _org(inicio, fim, tipo_cliente, config("excluir_intercompany"), config("excluir_org_vendas_internacional"))
    if df.empty:
        p.info("Nada encontrado para esse período.")
        return
    # A consulta agrega o período sem data por linha: converte com a taxa do meio do período.
    df = df.assign(_Data_Ref=inicio + (fim - inicio) / 2)
    a, b = p.columns(2)
    valor_convertido_brl(a, df, "Valor_Faturado", "_Data_Ref", "Valor faturado")
    b.metric("Qtd Faturada Total (todas as moedas)", fmt.num(df["Qtd_Faturada"].sum()))
    df = df.assign(Valor_BRL=para_brl(df, "Valor_Faturado", "_Data_Ref"))
    sem_taxa = int(df["Valor_BRL"].isna().sum())
    if sem_taxa:
        p.caption(f"{fmt.num(sem_taxa)} linha(s) sem taxa de câmbio disponível ficam de fora dos gráficos/matriz.")
    p.divider()
    card = p.card()
    x, y = card.columns(2)
    x.subheader("Por Organização de Vendas")
    org = df.groupby("Descricao_Org_Vendas")["Valor_BRL"].sum().sort_values(ascending=False)
    x.bar_chart(org.rename("Faturado (R$)"), horizontal=True, fmt_valor="brl0", altura=max(220, 30 * len(org) + 40))
    y.subheader("Por Linha de Negócio")
    linha = df.groupby("Linha_Negocio")["Valor_BRL"].sum().sort_values(ascending=False)
    y.bar_chart(linha.rename("Faturado (R$)"), horizontal=True, fmt_valor="brl0", altura=max(220, 30 * len(linha) + 40))
    p.divider()
    p.subheader("Matriz Org Vendas x Linha de Negócio (R$, convertido)")
    matriz = df.pivot_table(index="Descricao_Org_Vendas", columns="Linha_Negocio", values="Valor_BRL", aggfunc="sum", fill_value=0)
    p.card().table(matriz, formato_todas="brl2", indice=True, nome_arquivo="matriz_org_linha")
    p.divider()
    p.subheader("Detalhe (todas as moedas)")
    p.card().table(
        df[["Codigo_Org_Vendas", "Descricao_Org_Vendas", "Linha_Negocio", "Origem_Linha_Negocio", "Moeda", "Valor_Faturado", "Qtd_Faturada", "Qtd_Itens"]],
        {"Valor_Faturado": "num2"}, nome_arquivo="faturamento_org_detalhe",
    )


def _janelas_12_meses(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(últimos 12 meses, 12 anteriores) por Mes distinto — 1 linha por Mes+moeda, `.tail(12)` erraria."""
    if df.empty:
        return df, df
    meses = sorted(df["Mes"].unique())
    ultimos = set(meses[-12:])
    anteriores = set(meses[max(0, len(meses) - 24): max(0, len(meses) - 12)])
    return df[df["Mes"].isin(ultimos)], df[df["Mes"].isin(anteriores)]


def _aba_tendencia(p: Node) -> None:
    p.caption(
        "Faturamento e devoluções/abatimentos mês a mês — as únicas 2 séries com data real de transação "
        "acumulada nesta base (backlog/estoque só guardam o estado de hoje)."
    )
    a, _ = p.columns([1, 2])
    meses = a.slider("Período (meses)", "faturamento_meses", 6, 60, 24, 6)
    dados = _historico(meses, config("excluir_intercompany"))
    fat, dev = dados["faturamento"], dados["devolucoes"]
    p.subheader("Faturamento")
    p.caption("Convertido pra BRL via taxa de câmbio real do SAP (`TCURR`) — ver por moeda original nos expanders.")
    if fat.empty:
        p.info("Sem dado de faturamento no período.")
    else:
        atual, anterior = _janelas_12_meses(fat)
        atual_brl = para_brl(atual, "Valor_Faturado", "Mes").sum()
        anterior_brl = para_brl(anterior, "Valor_Faturado", "Mes").sum()
        p.metric("Variação (últimos 12 meses vs. 12 anteriores)", fmt.pct((atual_brl - anterior_brl) / anterior_brl if anterior_brl else 0.0, 1, sinal=True))
        x, y = p.columns(2)
        valor_convertido_brl(x, atual, "Valor_Faturado", "Mes", "Últimos 12 meses")
        valor_convertido_brl(y, anterior, "Valor_Faturado", "Mes", "12 meses anteriores")
        p.card().bar_chart(fat.pivot_table(index="Mes", columns="Moeda", values="Valor_Faturado", aggfunc="sum", fill_value=0))
    p.divider()
    p.subheader("Devoluções / abatimentos de negócio")
    p.caption(
        "Exclui `Tipo_Documento_Contabil = 'RV'` (faturamento de rotina). Quebrado por `Empresa_Codigo` (5 "
        "empresas SAP), não por moeda — a tabela não tem moeda e o mapeamento empresa→moeda não está documentado; "
        "trate cada empresa como potencialmente uma moeda diferente, não some entre elas."
    )
    if dev.empty:
        p.info("Sem dado de devolução no período.")
        return
    atual, anterior = _janelas_12_meses(dev)
    x, y = p.columns(2)
    x.caption("Últimos 12 meses, por empresa")
    x.valor_por_moeda(atual, "Valor", moeda_col="Empresa_Codigo")
    y.caption("12 meses anteriores, por empresa")
    y.valor_por_moeda(anterior, "Valor", moeda_col="Empresa_Codigo")
    p.card().bar_chart(dev.pivot_table(index="Mes", columns="Empresa_Codigo", values="Valor", aggfunc="sum", fill_value=0))
