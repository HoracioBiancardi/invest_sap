"""Painel Vendas — MTD/YTD/Trimestral vs Meta, Diário e Anual (YoY) (porte de
`pages/12_Painel_Vendas.py`). Fonte `GOLD.vendas_sap.fct_faturamento_itens_sap` (+ meta de
`vendas.fat_meta_equipe`) com a hierarquia comercial do crosswalk cliente→setor (~52% de
cobertura, o resto cai em 'NAO ALOCADO'). Ver `docs/CONTEXTO_VENDAS_SAP.md` §10.

Diferença do Streamlit: só a aba aberta consulta o DW; o `@st.fragment` da quebra por dimensão
não é mais necessário (toda troca de filtro já atualiza só o conteúdo, sem recarregar a página).
"""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from scripts.query_faturamento_comercial import (
    DIMENSOES_FATURAMENTO,
    DIMENSOES_META,
    faturamento_anual_comparativo,
    faturamento_por_dimensao,
    faturamento_serie,
    meta_vs_realizado_por_dimensao,
    top_clientes_periodo,
)
from web import charts, fmt
from web.cache import cached, em_paralelo
from web.ui import Ctx, Node, Pagina
from web.views._comum import filtro_tipo_cliente, filtros_comercial, para_brl, tipo_cliente_param


@cached(ttl=300, nome="painel: série de faturamento")
def _serie(inicio: datetime.date, fim: datetime.date, gran: str, tipo_cliente: Optional[str], filtros: dict) -> pd.DataFrame:
    return faturamento_serie(inicio, fim, granularidade=gran, tipo_cliente=tipo_cliente, filtros=filtros)


@cached(ttl=900, nome="painel: meta x realizado por dimensão")
def _meta_dimensao(inicio: datetime.date, fim: datetime.date, dimensao: str, tipo_cliente: Optional[str], filtros: dict) -> pd.DataFrame:
    return meta_vs_realizado_por_dimensao(inicio, fim, dimensao, tipo_cliente=tipo_cliente, filtros=filtros)


@cached(ttl=180, nome="painel: faturamento por dimensão")
def _por_dimensao(dimensao: str, inicio: datetime.date, fim: datetime.date, tipo_cliente: Optional[str], filtros: dict) -> pd.DataFrame:
    return faturamento_por_dimensao(inicio, fim, dimensao, granularidade="total", tipo_cliente=tipo_cliente, filtros=filtros)


@cached(ttl=1800, nome="painel: comparativo anual")
def _comparativo(dimensao: str, filtros: dict) -> pd.DataFrame:
    return faturamento_anual_comparativo(dimensao, filtros=filtros)


@cached(ttl=1800, nome="painel: top clientes YTD")
def _top_clientes(inicio: datetime.date, fim: datetime.date, n: int, filtros: dict) -> pd.DataFrame:
    return top_clientes_periodo(inicio, fim, n=n, filtros=filtros)


def _trimestre(meses: pd.Series) -> pd.Series:
    return "Tri " + (pd.to_datetime(meses).dt.month.sub(1) // 3 + 1).astype(str)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Painel Vendas", "speed")
    p.caption(
        "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` (+ `GOLD.vendas.fat_meta_equipe` na aba Meta) com "
        "a hierarquia comercial via crosswalk cliente→setor (~52% de cobertura — cliente sem match cai em "
        "'NAO ALOCADO', um balde grande e esperado). Ver `docs/CONTEXTO_VENDAS_SAP.md` §10."
    )
    tipo_cliente = tipo_cliente_param(filtro_tipo_cliente(p))
    abas = p.tabs(["MTD/YTD/Trimestral (vs Meta)", "Diário", "Anual (YoY)"], "aba")
    if abas.ativa == 0:
        _aba_meta(abas.corpo, tipo_cliente)
    elif abas.ativa == 1:
        _aba_diario(abas.corpo, tipo_cliente)
    else:
        _aba_anual(abas.corpo)


def _aba_meta(p: Node, tipo_cliente: Optional[str]) -> None:
    hoje = datetime.date.today()
    inicio_mes, inicio_ano = hoje.replace(day=1), hoje.replace(month=1, day=1)
    filtros = filtros_comercial(p, "p12", sorted(DIMENSOES_META))
    # As 4 consultas da aba saem juntas (tempo = a mais lenta); o resto do código lê do cache.
    dimensao_pedida = p.ctx.get("p12_dim") if p.ctx.get("p12_dim") in DIMENSOES_META else "Divisional"
    inicio_dim = inicio_mes if p.ctx.get("p12_periodo") == "Mês corrente" else inicio_ano
    pre = em_paralelo(
        dia=lambda: _serie(inicio_mes, hoje, "dia", tipo_cliente, filtros),
        mes=lambda: _serie(inicio_ano, hoje, "mes", tipo_cliente, filtros),
        meta_canal=lambda: _meta_dimensao(inicio_ano, hoje, "Canal", tipo_cliente, filtros),
        dim=lambda: _meta_dimensao(inicio_dim, hoje, dimensao_pedida, tipo_cliente, filtros),
    )
    dia, mes, meta_canal = pre["dia"], pre["mes"], pre["meta_canal"]
    # Valor_Faturado vem por Moeda — convertido pra BRL (TCURR) antes de comparar com a meta.
    dia_brl = dia.assign(Valor_BRL=para_brl(dia, "Valor_Faturado", "Dia")) if not dia.empty else dia
    mes_brl = mes.assign(Valor_BRL=para_brl(mes, "Valor_Faturado", "Mes")) if not mes.empty else mes
    fat_mtd = dia_brl["Valor_BRL"].sum() if not dia_brl.empty else 0.0
    fat_ytd = mes_brl["Valor_BRL"].sum() if not mes_brl.empty else 0.0
    meta_mtd = meta_canal.loc[meta_canal["Mes"] == hoje.strftime("%Y-%m"), "Meta_Valor"].sum() if not meta_canal.empty else 0.0
    meta_ytd = meta_canal["Meta_Valor"].sum() if not meta_canal.empty else 0.0
    p.metrics(
        [
            ("Faturado MTD", fmt.brl(fat_mtd)),
            ("Meta do Mês", fmt.brl(meta_mtd), None, "Meta pode não cobrir Canal 'MS' — ver caveat na quebra por dimensão."),
            ("Cob. Meta MTD", fmt.pct(fat_mtd / meta_mtd, 1) if meta_mtd else "—"),
            ("Cob. Meta YTD", fmt.pct(fat_ytd / meta_ytd, 1) if meta_ytd else "—", None,
             f"Faturado YTD: {fmt.brl(fat_ytd)} / Meta YTD: {fmt.brl(meta_ytd)}"),
        ]
    )
    if not dia.empty and (dia["Moeda"] != "BRL").any():
        p.expander("Faturado MTD, por moeda original (sem conversão)").valor_por_moeda(dia, "Valor_Faturado")
    p.divider()
    card = p.card()
    a, b = card.columns([3, 2])
    a.subheader("Faturamento diário — mês corrente")
    if dia_brl.empty:
        a.info("Sem faturamento no mês corrente ainda.")
    else:
        a.line_chart(dia_brl.groupby("Dia")["Valor_BRL"].sum().rename("Faturado (R$)"), fmt_valor="brl0")
    b.subheader("Evolução mensal — ano corrente")
    if mes_brl.empty:
        b.info("Sem faturamento no ano corrente ainda.")
    else:
        b.bar_chart(mes_brl.groupby("Mes")["Valor_BRL"].sum().rename("Faturado (R$)"), fmt_valor="brl0")
    p.divider()
    p.subheader("Meta x Realizado — trimestral")
    if mes_brl.empty:
        p.info("Sem dado suficiente pro trimestral.")
    else:
        comparacao = (
            pd.DataFrame(
                {
                    "Valor_Realizado": mes_brl.assign(T=_trimestre(mes_brl["Mes"])).groupby("T")["Valor_BRL"].sum(),
                    "Meta_Valor": meta_canal.assign(T=_trimestre(meta_canal["Mes"])).groupby("T")["Meta_Valor"].sum(),
                }
            )
            .fillna(0.0)
            .rename_axis("Trimestre")
            .reset_index()
        )
        p.card().chart(charts.meta_realizado(comparacao, "Trimestre"))
    p.divider()

    p.subheader("Meta x Realizado por dimensão comercial")
    p.caption(
        "Divisional/Regional/Distrital/Setor vêm de `vendas.dim_estrutura` — organograma SharePoint por nome "
        "de gerente, desigual entre linhas de negócio (ONCO/HEMATO é o mais completo). Canal='MS' isola o "
        "Ministério da Saúde; a Meta dele aparece vazia de propósito (a meta não separa esse cliente do 'Publico')."
    )
    x, y = p.columns([1, 2])
    opcoes = sorted(DIMENSOES_META)
    dimensao = x.selectbox("Quebrar por", opcoes, "p12_dim", default="Divisional")
    periodo = y.radio("Período", ["Ano corrente (YTD)", "Mês corrente"], "p12_periodo")
    inicio = inicio_ano if periodo == "Ano corrente (YTD)" else inicio_mes
    df_dim = _meta_dimensao(inicio, hoje, dimensao, tipo_cliente, filtros)
    if df_dim.empty:
        p.info("Nada encontrado para essa combinação de filtro.")
        return
    resumo = df_dim.groupby("Dimensao")[["Meta_Valor", "Valor_Realizado"]].sum()
    resumo["Cob_Meta"] = (resumo["Valor_Realizado"] / resumo["Meta_Valor"]).where(resumo["Meta_Valor"] > 0)
    resumo = resumo.sort_values("Valor_Realizado", ascending=False)
    card = p.card()
    if len(resumo) > 15:
        card.caption(f"Gráfico mostra as 15 maiores de {len(resumo)} — a tabela tem todas.")
    card.chart(charts.meta_realizado(resumo.head(15).reset_index(), "Dimensao"))
    card.table(resumo, {"Meta_Valor": "brl0", "Valor_Realizado": "brl0", "Cob_Meta": "pct1"}, indice=True, nome_arquivo=f"meta_por_{dimensao}")
    p.expander("Detalhe mês a mês").table(
        df_dim.assign(Cob_Meta=(df_dim["Valor_Realizado"] / df_dim["Meta_Valor"]).where(df_dim["Meta_Valor"] > 0))[
            ["Mes", "Dimensao", "Meta_Valor", "Valor_Realizado", "Cob_Meta", "Meta_Unidades", "Unidades_Realizado"]
        ],
        {"Meta_Valor": "brl0", "Valor_Realizado": "brl0", "Cob_Meta": "pct1", "Meta_Unidades": "num0", "Unidades_Realizado": "num0"},
        nome_arquivo="meta_dimensao_mensal",
    )


def _tabela_quebra(p: Node, df: pd.DataFrame, rotulo: str) -> None:
    p.caption("Coluna `Moeda` = original antes da conversão.")
    p.table(
        df[["Dimensao", "Moeda", "Valor_Faturado", "Valor_BRL", "Qtd_Faturada"]].rename(columns={"Dimensao": rotulo}),
        {"Valor_Faturado": "num2", "Valor_BRL": "brl2", "Qtd_Faturada": "num0"},
    )


def _aba_diario(p: Node, tipo_cliente: Optional[str]) -> None:
    hoje = datetime.date.today()
    inicio_mes = hoje.replace(day=1)
    p.caption("Sempre olha o mês corrente.")
    filtros = filtros_comercial(p, "p13", list(DIMENSOES_FATURAMENTO))
    dim_pedida = p.ctx.get("p13_dim") if p.ctx.get("p13_dim") in DIMENSOES_FATURAMENTO else list(DIMENSOES_FATURAMENTO)[0]
    hoje_ou_mes = (hoje, hoje) if p.ctx.get("p13_janela", "Hoje") == "Hoje" else (inicio_mes, hoje)
    em_paralelo(
        dia=lambda: _serie(inicio_mes, hoje, "dia", tipo_cliente, filtros),
        quebra=lambda: _por_dimensao(dim_pedida, *hoje_ou_mes, tipo_cliente, filtros),
        uf=lambda: _por_dimensao("Estado (UF)", inicio_mes, hoje, tipo_cliente, filtros),
    )
    dia = _serie(inicio_mes, hoje, "dia", tipo_cliente, filtros)
    dia_brl = dia.assign(Valor_BRL=para_brl(dia, "Valor_Faturado", "Dia")) if not dia.empty else dia
    fat_hoje = dia_brl.loc[pd.to_datetime(dia_brl["Dia"]).dt.date == hoje, "Valor_BRL"].sum() if not dia_brl.empty else 0.0
    fat_mes = dia_brl["Valor_BRL"].sum() if not dia_brl.empty else 0.0
    p.metrics([("Faturamento do Dia", fmt.brl(fat_hoje)), ("Faturamento do Mês (MTD)", fmt.brl(fat_mes))])
    if not dia.empty and (dia["Moeda"] != "BRL").any():
        p.expander("MTD por moeda original (sem conversão)").valor_por_moeda(dia, "Valor_Faturado")
    p.divider()
    p.subheader("Evolução diária")
    if dia_brl.empty:
        p.info("Sem faturamento no mês corrente ainda.")
    else:
        p.card().line_chart(dia_brl.groupby("Dia")["Valor_BRL"].sum().rename("Faturado (R$)"), fmt_valor="brl0")
    p.divider()
    p.subheader("Quebra por dimensão comercial")
    a, b = p.columns(2)
    dimensao = a.selectbox("Quebrar por", list(DIMENSOES_FATURAMENTO), "p13_dim")
    janela = b.radio("Janela", ["Hoje", "Mês (MTD)"], "p13_janela")
    if janela == "Hoje":
        data_ref, quebra = hoje, _por_dimensao(dimensao, hoje, hoje, tipo_cliente, filtros)
        if quebra.empty:
            p.info(
                "Sem faturamento hoje ainda para esse recorte — comum de manhã, antes do primeiro lote de "
                "faturas do dia ser processado."
            )
    else:
        data_ref, quebra = inicio_mes + (hoje - inicio_mes) / 2, _por_dimensao(dimensao, inicio_mes, hoje, tipo_cliente, filtros)
        if quebra.empty:
            p.info("Nada encontrado para esse recorte no mês.")
    if not quebra.empty:
        quebra = quebra.assign(_Data_Ref=data_ref)
        quebra = quebra.assign(Valor_BRL=para_brl(quebra, "Valor_Faturado", "_Data_Ref"))
        card = p.card()
        g, h = card.columns([2, 1])
        top = quebra.groupby("Dimensao")["Valor_BRL"].sum().sort_values(ascending=False).head(20)
        g.bar_chart(top.rename("Faturado (R$)"), horizontal=True, fmt_valor="brl0", altura=max(220, 26 * len(top) + 40))
        _tabela_quebra(h, quebra, dimensao)
    p.divider()
    p.subheader("Faturamento por Estado (UF) — mês corrente")
    uf = _por_dimensao("Estado (UF)", inicio_mes, hoje, tipo_cliente, filtros)
    if uf.empty:
        p.info("Sem faturamento no mês corrente ainda.")
        return
    uf = uf.assign(_Data_Ref=inicio_mes + (hoje - inicio_mes) / 2)
    uf = uf.assign(Valor_BRL=para_brl(uf, "Valor_Faturado", "_Data_Ref"))
    card = p.card()
    i, j = card.columns(2)
    serie_uf = uf.groupby("Dimensao")["Valor_BRL"].sum().sort_values(ascending=False)
    i.bar_chart(serie_uf.rename("Faturado (R$)"), horizontal=True, fmt_valor="brl0", altura=max(220, 22 * len(serie_uf) + 40))
    _tabela_quebra(j, uf, "Estado")


def _aba_anual(p: Node) -> None:
    hoje = datetime.date.today()
    filtros = filtros_comercial(p, "p14", list(DIMENSOES_FATURAMENTO))
    a, _ = p.columns([1, 2])
    dimensao = a.selectbox("Quebrar por", list(DIMENSOES_FATURAMENTO), "p14_dim")
    n_pedido = max(5, min(50, int(p.ctx.get("p14_n") or 15)))
    em_paralelo(
        comparativo=lambda: _comparativo(dimensao, filtros),
        clientes=lambda: _top_clientes(hoje.replace(month=1, day=1), hoje, n_pedido, filtros),
    )
    df = _comparativo(dimensao, filtros)
    if df.empty:
        p.info("Nada encontrado para essa dimensão.")
    else:
        col_ano_ant, col_ytd_ant, col_ytd_atual = df.columns[1], df.columns[2], df.columns[3]
        total_ant, total_atual = df[col_ytd_ant].sum(), df[col_ytd_atual].sum()
        variacao = (total_atual - total_ant) / total_ant if total_ant else 0.0
        p.metrics([(col_ytd_ant, fmt.brl(total_ant)), (col_ytd_atual, fmt.brl(total_atual)), ("Evolução YTD", fmt.pct(variacao, 1, sinal=True))])
        p.divider()
        card = p.card()
        x, y = card.columns([2, 1])
        x.subheader(f"{dimensao}: YTD ano anterior x YTD ano corrente")
        top = df.set_index("Dimensao")[[col_ytd_ant, col_ytd_atual]].head(20)
        x.bar_chart(top, horizontal=True, fmt_valor="brl0", altura=max(260, 40 * len(top) + 60))
        y.subheader("Maiores altas/quedas (YTD)")
        evolucao = df.dropna(subset=["Evolucao_YTD_Pct"]).sort_values("Evolucao_YTD_Pct", ascending=False)
        y.table(evolucao[["Dimensao", "Evolucao_YTD_Pct"]], {"Evolucao_YTD_Pct": "spct1"})
        p.divider()
        p.subheader("Detalhe")
        p.card().table(
            df, {col_ano_ant: "brl2", col_ytd_ant: "brl2", col_ytd_atual: "brl2", "Evolucao_YTD_Pct": "spct1"},
            nome_arquivo=f"comparativo_anual_{dimensao}",
        )
    p.divider()
    p.subheader("Top clientes — ano corrente (YTD)")
    b, _ = p.columns([1, 2])
    n = b.slider("Quantos clientes mostrar", "p14_n", 5, 50, 15, 5)
    clientes = _top_clientes(hoje.replace(month=1, day=1), hoje, n, filtros)
    if clientes.empty:
        p.info("Nada encontrado.")
        return
    p.caption("Valor faturado convertido pra BRL (taxa real do SAP, `TCURR`) antes de rankear.")
    p.card().table(clientes, {"Valor_Faturado": "brl2", "Qtd_Faturada": "num0", "Preco_Medio": "brl2"}, nome_arquivo="top_clientes_ytd")
