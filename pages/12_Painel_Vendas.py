"""Página: Painel Vendas — Faturamento vs Meta (MTD/YTD/Trimestral) + Diário + Anual (YoY).

Funde 3 páginas antigas (2026-09-04): `12_Faturamento_vs_Meta.py`, `13_Faturamento_Diario.py`
e `14_Faturamento_Anual.py` — mesma fonte/crosswalk nas 3, só granularidade de tempo
diferente (trimestral/mensal, diário, anual). Viraram abas de 1 página em vez de 3
separadas no menu, sem perder nenhuma visão.

Inspirada nas páginas "Visão Faturamento", "Faturamento vs Meta - Acompanhamento Mensal",
"Faturamento Diário - Mês Atual" e "Faturamento Anual" do Painel Vendas (Power BI) enviado
pelo usuário — não é mais réplica numérica exata dele (ver docs/CONTEXTO_VENDAS_SAP.md §10:
a fonte que batia com esse painel, `vendas.fat_faturamento`, foi descartada por decisão do
usuário — schema `vendas` legado só segue em uso pra `fat_meta_equipe`). Reusa
scripts/query_faturamento_comercial.py — fonte `GOLD.vendas_sap.fct_faturamento_itens_sap`,
com a hierarquia comercial via o mesmo crosswalk (~52% de cobertura) já usado em
`pages/22_Faturamento.py`/`pages/11_Metas.py`.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_faturamento_comercial import (  # noqa: E402
    DIMENSOES_FATURAMENTO,
    DIMENSOES_META,
    faturamento_anual_comparativo,
    faturamento_por_dimensao,
    faturamento_serie,
    meta_vs_realizado_por_dimensao,
    top_clientes_periodo,
)
from scripts.ui_charts_comercial import grafico_meta_realizado  # noqa: E402
from scripts.ui_filtros_comercial import render_filtros_comercial  # noqa: E402
from scripts.ui_theme import card, render_filtro_tipo_cliente  # noqa: E402

st.set_page_config(page_title="Painel Vendas — Vendas Comercial", page_icon="🎯", layout="wide")
st.title(":material/speed: Painel Vendas")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_faturamento_itens_sap` (+ `GOLD.vendas.fat_meta_equipe` na "
    "aba Meta) com a hierarquia comercial via o mesmo crosswalk cliente→setor de "
    "`pages/22_Faturamento.py`/`pages/11_Metas.py` (~52% de cobertura — cliente sem match "
    "cai em 'NAO ALOCADO', um balde grande e esperado, não um erro). Ver "
    "`docs/CONTEXTO_VENDAS_SAP.md` §10 pra fórmula completa e histórico."
)

render_filtro_tipo_cliente()
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao

hoje = datetime.date.today()
ontem = hoje - datetime.timedelta(days=1)
inicio_mes = hoje.replace(day=1)
inicio_ano = hoje.replace(month=1, day=1)

tab_meta, tab_diario, tab_anual = st.tabs(["MTD/YTD/Trimestral (vs Meta)", "Diário", "Anual (YoY)"])

with tab_meta:
    filtros = render_filtros_comercial("p12", sorted(DIMENSOES_META))
    if filtros:
        st.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros.items()))

    @st.cache_data(ttl=300, show_spinner="Consultando faturamento MTD/YTD...")
    def _serie_dia_cached(
        data_inicio: datetime.date,
        data_fim: datetime.date,
        tipo_cliente: Optional[str],
        filtros: dict[str, str],
    ) -> pd.DataFrame:
        return faturamento_serie(
            data_inicio, data_fim, granularidade="dia", tipo_cliente=tipo_cliente, filtros=filtros
        )

    @st.cache_data(ttl=900, show_spinner="Consultando evolução mensal...")
    def _serie_mes_cached(
        data_inicio: datetime.date,
        data_fim: datetime.date,
        tipo_cliente: Optional[str],
        filtros: dict[str, str],
    ) -> pd.DataFrame:
        return faturamento_serie(
            data_inicio, data_fim, granularidade="mes", tipo_cliente=tipo_cliente, filtros=filtros
        )

    @st.cache_data(ttl=900, show_spinner="Consultando Meta x Realizado...")
    def _meta_dimensao_cached(
        data_inicio: datetime.date,
        data_fim: datetime.date,
        dimensao: str,
        tipo_cliente: Optional[str],
        filtros: dict[str, str],
    ) -> pd.DataFrame:
        return meta_vs_realizado_por_dimensao(
            data_inicio, data_fim, dimensao, tipo_cliente=tipo_cliente, filtros=filtros
        )

    df_dia_mtd = _serie_dia_cached(inicio_mes, hoje, tipo_cliente, filtros)
    df_mes_ytd = _serie_mes_cached(inicio_ano, hoje, tipo_cliente, filtros)
    df_meta_ytd_canal = _meta_dimensao_cached(inicio_ano, hoje, "Canal", tipo_cliente, filtros)

    faturado_mtd = df_dia_mtd["Valor_Faturado"].sum() if not df_dia_mtd.empty else 0.0
    faturado_ytd = df_mes_ytd["Valor_Faturado"].sum() if not df_mes_ytd.empty else 0.0
    meta_mtd = df_meta_ytd_canal.loc[
        df_meta_ytd_canal["Mes"] == hoje.strftime("%Y-%m"), "Meta_Valor"
    ].sum()
    meta_ytd = df_meta_ytd_canal["Meta_Valor"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Faturado MTD", f"R$ {faturado_mtd:,.0f}")
    c2.metric(
        "Meta do Mês",
        f"R$ {meta_mtd:,.0f}",
        help="Meta pode não cobrir Canal 'MS' — ver caveat no rodapé.",
    )
    c3.metric("Cob. Meta MTD", f"{(faturado_mtd / meta_mtd):.1%}" if meta_mtd else "—")
    c4.metric(
        "Cob. Meta YTD",
        f"{(faturado_ytd / meta_ytd):.1%}" if meta_ytd else "—",
        help=f"Faturado YTD: R$ {faturado_ytd:,.0f} / Meta YTD: R$ {meta_ytd:,.0f}",
    )

    st.divider()

    with card("fatmeta-evolucao"):
        col_a, col_b = st.columns([3, 2])
        with col_a:
            st.subheader("Faturamento diário — mês corrente")
            if df_dia_mtd.empty:
                st.info("Sem faturamento no mês corrente ainda.")
            else:
                st.bar_chart(df_dia_mtd.set_index("Dia")["Valor_Faturado"])
        with col_b:
            st.subheader("Evolução mensal — ano corrente")
            if df_mes_ytd.empty:
                st.info("Sem faturamento no ano corrente ainda.")
            else:
                st.bar_chart(df_mes_ytd.set_index("Mes")["Valor_Faturado"])

    st.divider()

    st.subheader("Meta x Realizado — trimestral")
    if df_mes_ytd.empty:
        st.info("Sem dado suficiente pro trimestral.")
    else:
        df_tri = df_mes_ytd.copy()
        df_tri["Trimestre"] = "Tri " + (pd.to_datetime(df_tri["Mes"]).dt.month.sub(1) // 3 + 1).astype(
            str
        )
        meta_tri = df_meta_ytd_canal.copy()
        meta_tri["Trimestre"] = "Tri " + (
            pd.to_datetime(meta_tri["Mes"]).dt.month.sub(1) // 3 + 1
        ).astype(str)
        comparacao_tri = (
            pd.DataFrame(
                {
                    "Valor_Realizado": df_tri.groupby("Trimestre")["Valor_Faturado"].sum(),
                    "Meta_Valor": meta_tri.groupby("Trimestre")["Meta_Valor"].sum(),
                }
            )
            .fillna(0.0)
            .reset_index()
        )
        with card("fatmeta-trimestral"):
            st.altair_chart(grafico_meta_realizado(comparacao_tri, "Trimestre"), width="stretch")

    st.divider()

    st.subheader("Meta x Realizado por dimensão comercial")
    st.caption(
        "Divisional/Regional/Distrital/Setor vêm de `vendas.dim_estrutura` — organograma "
        "SharePoint por nome de gerente, desigual entre linhas de negócio (ONCO/HEMATO é o "
        "mais completo). Canal='MS' isola o cliente Ministério da Saúde (ver §10.1 do "
        "contexto); a coluna Meta dele aparece vazia de propósito — a meta orçamentária não "
        "separa esse cliente do resto do 'Publico'."
    )

    col_dim, col_periodo = st.columns([1, 2])
    with col_dim:
        dimensao_meta = st.selectbox(
            "Quebrar por",
            options=sorted(DIMENSOES_META),
            index=sorted(DIMENSOES_META).index("Divisional"),
            key="dim_meta",
        )
    with col_periodo:
        periodo_opcao = st.radio(
            "Período", options=["Ano corrente (YTD)", "Mês corrente"], horizontal=True
        )

    data_inicio_dim = inicio_ano if periodo_opcao == "Ano corrente (YTD)" else inicio_mes
    df_dim = _meta_dimensao_cached(data_inicio_dim, hoje, dimensao_meta, tipo_cliente, filtros)

    if df_dim.empty:
        st.info("Nada encontrado para essa combinação de filtro.")
    else:
        resumo = df_dim.groupby("Dimensao")[["Meta_Valor", "Valor_Realizado"]].sum()
        resumo["Cob_Meta"] = (resumo["Valor_Realizado"] / resumo["Meta_Valor"]).where(
            resumo["Meta_Valor"] > 0
        )
        resumo = resumo.sort_values("Valor_Realizado", ascending=False)

        with card("fatmeta-dimensao"):
            col_e, col_f = st.columns([2, 1])
            with col_e:
                top_grafico = resumo.head(15).reset_index()
                if len(resumo) > 15:
                    st.caption(
                        f"Gráfico mostra as 15 maiores de {len(resumo)} — tabela ao lado tem todas."
                    )
                st.altair_chart(grafico_meta_realizado(top_grafico, "Dimensao"), width="stretch")
            with col_f:
                st.dataframe(
                    resumo.style.format(
                        {
                            "Meta_Valor": "R$ {:,.0f}",
                            "Valor_Realizado": "R$ {:,.0f}",
                            "Cob_Meta": "{:.1%}",
                        }
                    ),
                    width="stretch",
                )

        with st.expander("Detalhe mês a mês"):
            with card("fatmeta-dimensao-detalhe"):
                st.dataframe(
                    df_dim.assign(
                        Cob_Meta=lambda d: (d["Valor_Realizado"] / d["Meta_Valor"]).where(
                            d["Meta_Valor"] > 0
                        )
                    )[
                        [
                            "Mes",
                            "Dimensao",
                            "Meta_Valor",
                            "Valor_Realizado",
                            "Cob_Meta",
                            "Meta_Unidades",
                            "Unidades_Realizado",
                        ]
                    ],
                    width="stretch",
                    hide_index=True,
                )

with tab_diario:
    st.caption(
        "Sempre olha o mês corrente (não usa o período do filtro global do sidebar, que "
        "costuma ser mais amplo)."
    )
    filtros_diario = render_filtros_comercial("p13", list(DIMENSOES_FATURAMENTO))
    if filtros_diario:
        st.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros_diario.items()))

    @st.cache_data(ttl=180, show_spinner="Consultando faturamento diário...")
    def _dia_cached(
        data_inicio: datetime.date,
        data_fim: datetime.date,
        tipo_cliente: Optional[str],
        filtros: dict[str, str],
    ) -> pd.DataFrame:
        return faturamento_serie(
            data_inicio, data_fim, granularidade="dia", tipo_cliente=tipo_cliente, filtros=filtros
        )

    @st.cache_data(ttl=180, show_spinner="Consultando quebra por dimensão...")
    def _dimensao_dia_cached(
        dimensao: str, data_ref: datetime.date, tipo_cliente: Optional[str], filtros: dict[str, str]
    ) -> pd.DataFrame:
        return faturamento_por_dimensao(
            data_ref,
            data_ref,
            dimensao,
            granularidade="total",
            tipo_cliente=tipo_cliente,
            filtros=filtros,
        )

    @st.cache_data(ttl=180, show_spinner="Consultando quebra por dimensão (mês)...")
    def _dimensao_mes_cached(
        dimensao: str,
        data_inicio: datetime.date,
        data_fim: datetime.date,
        tipo_cliente: Optional[str],
        filtros: dict[str, str],
    ) -> pd.DataFrame:
        return faturamento_por_dimensao(
            data_inicio,
            data_fim,
            dimensao,
            granularidade="total",
            tipo_cliente=tipo_cliente,
            filtros=filtros,
        )

    df_dia = _dia_cached(inicio_mes, hoje, tipo_cliente, filtros_diario)
    faturado_hoje = (
        df_dia.loc[df_dia["Dia"] == hoje, "Valor_Faturado"].sum() if not df_dia.empty else 0.0
    )
    faturado_mes = df_dia["Valor_Faturado"].sum() if not df_dia.empty else 0.0

    c1, c2 = st.columns(2)
    c1.metric("Faturamento do Dia", f"R$ {faturado_hoje:,.0f}")
    c2.metric("Faturamento do Mês (MTD)", f"R$ {faturado_mes:,.0f}")

    st.divider()

    st.subheader("Evolução diária")
    if df_dia.empty:
        st.info("Sem faturamento no mês corrente ainda.")
    else:
        st.bar_chart(df_dia.set_index("Dia")["Valor_Faturado"])

    st.divider()

    st.subheader("Quebra por dimensão comercial")
    col_dim, col_janela = st.columns([1, 1])
    with col_dim:
        dimensao_diario = st.selectbox(
            "Quebrar por", options=list(DIMENSOES_FATURAMENTO), index=0, key="dim_diario"
        )
    with col_janela:
        janela_opcao = st.radio("Janela", options=["Hoje", "Mês (MTD)"], horizontal=True)

    if janela_opcao == "Hoje":
        df_quebra = _dimensao_dia_cached(dimensao_diario, hoje, tipo_cliente, filtros_diario)
        if df_quebra.empty:
            st.info(
                "Sem faturamento hoje ainda para esse recorte — comum se a consulta for feita "
                "de manhã, antes do primeiro lote de faturas do dia ser processado."
            )
    else:
        df_quebra = _dimensao_mes_cached(dimensao_diario, inicio_mes, hoje, tipo_cliente, filtros_diario)
        if df_quebra.empty:
            st.info("Nada encontrado para esse recorte no mês.")

    if not df_quebra.empty:
        col_g, col_h = st.columns([2, 1])
        with col_g:
            st.bar_chart(df_quebra.set_index("Dimensao")["Valor_Faturado"].head(20))
        with col_h:
            st.dataframe(
                df_quebra[["Dimensao", "Valor_Faturado", "Qtd_Faturada"]].style.format(
                    {"Valor_Faturado": "R$ {:,.2f}", "Qtd_Faturada": "{:,.0f}"}
                ),
                width="stretch",
                hide_index=True,
            )

    st.divider()

    st.subheader("Faturamento por Estado (UF) — mês corrente")
    df_uf = _dimensao_mes_cached("Estado (UF)", inicio_mes, hoje, tipo_cliente, filtros_diario)
    if df_uf.empty:
        st.info("Sem faturamento no mês corrente ainda.")
    else:
        col_i, col_j = st.columns([1, 1])
        with col_i:
            st.bar_chart(df_uf.set_index("Dimensao")["Valor_Faturado"])
        with col_j:
            st.dataframe(
                df_uf[["Dimensao", "Valor_Faturado", "Qtd_Faturada"]]
                .rename(columns={"Dimensao": "Estado"})
                .style.format({"Valor_Faturado": "R$ {:,.2f}", "Qtd_Faturada": "{:,.0f}"}),
                width="stretch",
                hide_index=True,
            )

with tab_anual:
    filtros_anual = render_filtros_comercial("p14", list(DIMENSOES_FATURAMENTO))
    if filtros_anual:
        st.caption("Filtro ativo: " + ", ".join(f"**{k}** = {v}" for k, v in filtros_anual.items()))

    dimensao_anual = st.selectbox(
        "Quebrar por", options=list(DIMENSOES_FATURAMENTO), index=0, key="dim_anual"
    )

    @st.cache_data(ttl=1800, show_spinner="Consultando comparativo anual...")
    def _comparativo_cached(dimensao: str, filtros: dict[str, str]) -> pd.DataFrame:
        return faturamento_anual_comparativo(dimensao, filtros=filtros)

    df = _comparativo_cached(dimensao_anual, filtros_anual)

    if df.empty:
        st.info("Nada encontrado para essa dimensão.")
    else:
        col_ano_anterior, col_ytd_anterior, col_ytd_atual = df.columns[1], df.columns[2], df.columns[3]

        total_ytd_anterior = df[col_ytd_anterior].sum()
        total_ytd_atual = df[col_ytd_atual].sum()
        variacao_total = (
            ((total_ytd_atual - total_ytd_anterior) / total_ytd_anterior) if total_ytd_anterior else 0.0
        )

        c1, c2, c3 = st.columns(3)
        c1.metric(col_ytd_anterior, f"R$ {total_ytd_anterior:,.0f}")
        c2.metric(col_ytd_atual, f"R$ {total_ytd_atual:,.0f}")
        c3.metric("Evolução YTD", f"{variacao_total:+.1%}")

        st.divider()

        with card("fatanual-comparativo"):
            col_a, col_b = st.columns([2, 1])
            with col_a:
                st.subheader(f"{dimensao_anual}: YTD ano anterior x YTD ano corrente")
                st.bar_chart(df.set_index("Dimensao")[[col_ytd_anterior, col_ytd_atual]].head(20))
            with col_b:
                st.subheader("Maiores altas/quedas (YTD)")
                top_evolucao = df.dropna(subset=["Evolucao_YTD_Pct"]).sort_values(
                    "Evolucao_YTD_Pct", ascending=False
                )
                st.dataframe(
                    top_evolucao[["Dimensao", "Evolucao_YTD_Pct"]].style.format(
                        {"Evolucao_YTD_Pct": "{:+.1%}"}
                    ),
                    width="stretch",
                    hide_index=True,
                )

        st.divider()

        st.subheader("Detalhe")
        with card("fatanual-detalhe"):
            st.dataframe(
                df.style.format(
                    {col: "R$ {:,.2f}" for col in [df.columns[1], col_ytd_anterior, col_ytd_atual]}
                    | {"Evolucao_YTD_Pct": "{:+.1%}"}
                ),
                width="stretch",
                hide_index=True,
            )

    st.divider()

    st.subheader("Top clientes — ano corrente (YTD)")

    @st.cache_data(ttl=1800, show_spinner="Consultando top clientes...")
    def _top_clientes_cached(n: int, filtros: dict[str, str]) -> pd.DataFrame:
        return top_clientes_periodo(hoje.replace(month=1, day=1), hoje, n=n, filtros=filtros)

    n_clientes = st.slider("Quantos clientes mostrar", min_value=5, max_value=50, value=15, step=5)
    df_clientes = _top_clientes_cached(n_clientes, filtros_anual)
    if df_clientes.empty:
        st.info("Nada encontrado.")
    else:
        with card("fatanual-top-clientes"):
            st.dataframe(
                df_clientes.style.format(
                    {
                        "Valor_Faturado": "R$ {:,.2f}",
                        "Qtd_Faturada": "{:,.0f}",
                        "Preco_Medio": "R$ {:,.2f}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
