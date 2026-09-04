"""Página: Oportunidade — funil Oportunidade (Salesforce) → Pedido → Pendência → Fatura.

Reusa scripts/query_vendas_sap.py::correlacao_oportunidade_pedido_pendencia_fatura (mesma
função de pages/20_Pedidos.py, aba "Buscar pedido"), mas agregada em pandas pra visão de
portfólio (funil por estágio, conversão, aging) em vez de detalhe de 1 pedido — essa página
não tem consulta nova própria.

Uma linha da consulta = Pedido+Item; como uma Oportunidade pode ter mais de 1 item de pedido,
as métricas de "quantas Oportunidades" abaixo deduplicam por (Nome_Oportunidade,
Data_Criacao_Oportunidade) — aproximação razoável na ausência do OpportunityId na consulta
(não exposto pela função hoje), documentada onde usada.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import correlacao_oportunidade_pedido_pendencia_fatura  # noqa: E402
from scripts.ui_filtros_executivo import render_filtros_executivo  # noqa: E402
from scripts.ui_theme import card, render_filtro_periodo_tipo_cliente  # noqa: E402

st.set_page_config(page_title="Oportunidade — Vendas SAP", page_icon="🎯", layout="wide")
st.title(":material/target: Oportunidade")
st.caption(
    "Funil da Oportunidade (Salesforce): estágio, ganha x não ganha, aging de oportunidade "
    "aberta e divergência de valor vs. o pedido SAP correspondente. Cobertura de Oportunidade "
    "não é 100% (~73% medido em 2026-08-13) — pedido sem match no Salesforce não entra nas "
    "métricas de funil abaixo (que são só sobre linhas COM Oportunidade), mas ainda é contado "
    "no denominador de 'conversão'."
)

render_filtro_periodo_tipo_cliente()
data_inicio = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
st.caption(
    f"Filtro: período de **{data_inicio:%d/%m/%Y}** a **{data_fim:%d/%m/%Y}**, "
    f"tipo de cliente **{tipo_cliente_opcao}**."
)

filtros = render_filtros_executivo("oportunidade", mostrar_pedido=True)
numero_pedido = filtros.get("numero_pedido")

SAFETY_LIMIT = 20000


@st.cache_data(ttl=300, show_spinner="Consultando Oportunidade → Pedido → Pendência → Fatura...")
def _dados_cached(
    data_inicio: datetime.date, data_fim: datetime.date, numero_pedido: Optional[str], tipo_cliente: Optional[str]
) -> pd.DataFrame:
    return correlacao_oportunidade_pedido_pendencia_fatura(
        data_inicio=data_inicio,
        data_fim=data_fim,
        apenas_pendentes=False,
        numero_pedido=numero_pedido,
        tipo_cliente=tipo_cliente,
        limit=SAFETY_LIMIT,
    )


df = _dados_cached(data_inicio, data_fim, numero_pedido, None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao)

if df.empty:
    st.info("Nada encontrado para esse filtro.")
else:
    if len(df) >= SAFETY_LIMIT:
        st.warning(
            f"Atingiu o teto de segurança de {SAFETY_LIMIT:,} linhas — reduza o período no "
            "filtro global pra garantir que os totais abaixo são exatos."
        )

    df_opp = df[df["Nome_Oportunidade"].notna()].copy()
    opp_dedup = df_opp.drop_duplicates(subset=["Nome_Oportunidade", "Data_Criacao_Oportunidade"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pedidos+Item no período", f"{len(df):,}")
    c2.metric("% com Oportunidade vinculada", f"{len(df_opp) / len(df):.0%}")
    c3.metric("Oportunidades distintas (aprox.)", f"{len(opp_dedup):,}")
    c4.metric(
        "Oportunidades ganhas",
        f"{opp_dedup['Oportunidade_Ganha'].mean():.0%}" if not opp_dedup.empty else "—",
    )

    st.divider()

    if df_opp.empty:
        st.info("Nenhuma linha com Oportunidade vinculada nesse filtro — sem funil pra mostrar.")
    else:
        tab_funil, tab_ganha, tab_aging, tab_divergencia, tab_detalhe = st.tabs(
            [
                "Funil por Estágio", "Ganha x Não Ganha", "Aging de Oportunidade Aberta",
                "Divergência Oportunidade x Pedido", "Detalhe",
            ]
        )

        with tab_funil:
            funil_estagio = opp_dedup.groupby("Estagio_Oportunidade", dropna=False).agg(
                Qtd_Oportunidades=("Nome_Oportunidade", "count"),
                Valor_Oportunidade=("Valor_Oportunidade", "sum"),
            ).sort_values("Valor_Oportunidade", ascending=False)
            with card("oportunidade-funil-estagio"):
                col_a, col_b = st.columns([1, 1])
                with col_a:
                    st.bar_chart(funil_estagio["Qtd_Oportunidades"])
                with col_b:
                    st.dataframe(
                        funil_estagio.style.format({"Valor_Oportunidade": "R$ {:,.2f}"}),
                        width="stretch",
                    )

            dt_criacao_opp = pd.to_datetime(
                df_opp["Data_Criacao_Oportunidade"], utc=True, errors="coerce"
            ).dt.tz_localize(None)
            dt_pedido = pd.to_datetime(df_opp["Data_Inclusao_Pedido"], errors="coerce")
            dt_fatura = pd.to_datetime(df_opp["Primeira_Data_Faturamento"], errors="coerce")
            dias_opp_pedido = (dt_pedido - dt_criacao_opp).dt.days
            dias_opp_pedido = dias_opp_pedido[dias_opp_pedido >= 0]
            dias_pedido_fatura = (dt_fatura - dt_pedido).dt.days
            dias_pedido_fatura = dias_pedido_fatura[dias_pedido_fatura >= 0]

            col_c, col_d = st.columns([1, 1])
            col_c.metric(
                "Mediana dias: Oportunidade → Pedido",
                f"{dias_opp_pedido.median():.0f}" if not dias_opp_pedido.empty else "sem dado",
            )
            col_d.metric(
                "Mediana dias: Pedido → 1ª Fatura",
                f"{dias_pedido_fatura.median():.0f}" if not dias_pedido_fatura.empty else "sem dado",
            )

        with tab_ganha:
            resumo_ganha = opp_dedup.groupby("Oportunidade_Ganha", dropna=False).agg(
                Qtd_Oportunidades=("Nome_Oportunidade", "count"),
                Valor_Oportunidade=("Valor_Oportunidade", "sum"),
            )
            resumo_ganha.index = resumo_ganha.index.map({True: "Ganha", False: "Não ganha / em aberto"})
            with card("oportunidade-ganha"):
                col_e, col_f = st.columns([1, 1])
                with col_e:
                    st.bar_chart(resumo_ganha["Valor_Oportunidade"])
                with col_f:
                    st.dataframe(
                        resumo_ganha.style.format({"Valor_Oportunidade": "R$ {:,.2f}"}),
                        width="stretch",
                    )

        with tab_aging:
            st.caption(
                "Oportunidade sem `Data_Fechamento_Oportunidade` — ainda em aberto no "
                "Salesforce. Dias contados a partir de `Data_Criacao_Oportunidade`."
            )
            abertas = opp_dedup[opp_dedup["Data_Fechamento_Oportunidade"].isna()].copy()
            if abertas.empty:
                st.info("Nenhuma Oportunidade em aberto nesse filtro.")
            else:
                abertas["Dias_Aberta"] = (
                    pd.Timestamp.today().normalize()
                    - pd.to_datetime(abertas["Data_Criacao_Oportunidade"], utc=True, errors="coerce").dt.tz_localize(None)
                ).dt.days
                faixas = ["0-15 dias", "16-30 dias", "31-60 dias", "61-90 dias", "90+ dias"]
                bins = [-1, 15, 30, 60, 90, 10**6]
                abertas["Faixa_Aging"] = pd.cut(abertas["Dias_Aberta"], bins=bins, labels=faixas)
                pivot = (
                    abertas.groupby("Faixa_Aging", observed=True)["Valor_Oportunidade"].sum().reindex(faixas)
                )
                with card("oportunidade-aging"):
                    col_g, col_h = st.columns([1, 1])
                    with col_g:
                        st.bar_chart(pivot)
                    with col_h:
                        st.dataframe(pivot.to_frame().style.format({"Valor_Oportunidade": "R$ {:,.2f}"}), width="stretch")

        with tab_divergencia:
            st.caption(
                "Compara `Valor_Item_Oportunidade` (Salesforce `OpportunityLineItem.TotalPrice`, "
                "valor do item negociado) com `Valor_Liquido_Pedido` (SAP, valor que de fato virou "
                "pedido) pro mesmo Pedido+Item — não confundir com `Valor_Oportunidade`, total do "
                "negócio inteiro (repetido em toda linha da mesma Oportunidade)."
            )
            mask_div = df_opp["Valor_Item_Oportunidade"].notna() & (df_opp["Valor_Liquido_Pedido"] != 0)
            df_div = df_opp.loc[mask_div].copy()
            if df_div.empty:
                st.info("Nenhuma linha com Oportunidade e Pedido pra comparar nesse filtro.")
            else:
                df_div["Diferenca"] = df_div["Valor_Item_Oportunidade"] - df_div["Valor_Liquido_Pedido"]
                df_div["Diferenca_Pct"] = df_div["Diferenca"] / df_div["Valor_Liquido_Pedido"] * 100
                limite_pct = st.slider("Mostrar divergências acima de (%)", min_value=0, max_value=100, value=5, step=5)
                df_div_filtrado = df_div[df_div["Diferenca_Pct"].abs() >= limite_pct].sort_values(
                    "Diferenca_Pct", key=lambda s: s.abs(), ascending=False
                )
                st.metric(f"Linhas com divergência ≥ {limite_pct}%", f"{len(df_div_filtrado)} de {len(df_div)}")
                colunas_div = [
                    "Numero_Pedido", "Item_Pedido", "Nome_Cliente", "Nome_Oportunidade",
                    "Valor_Item_Oportunidade", "Valor_Liquido_Pedido", "Diferenca", "Diferenca_Pct",
                ]
                with card("oportunidade-divergencia"):
                    st.dataframe(
                        df_div_filtrado[colunas_div].head(200).style.format(
                            {
                                "Valor_Item_Oportunidade": "R$ {:,.2f}",
                                "Valor_Liquido_Pedido": "R$ {:,.2f}",
                                "Diferenca": "R$ {:,.2f}",
                                "Diferenca_Pct": "{:,.1f}%",
                            }
                        ),
                        width="stretch",
                        hide_index=True,
                    )

        with tab_detalhe:
            colunas_exibir = [
                "Numero_Pedido", "Item_Pedido", "Nome_Cliente", "Nome_Oportunidade",
                "Estagio_Oportunidade", "Oportunidade_Ganha", "Valor_Oportunidade",
                "Valor_Item_Oportunidade", "Valor_Liquido_Pedido", "Status_Pendencia",
                "Status_Faturamento", "Data_Criacao_Oportunidade", "Data_Fechamento_Oportunidade",
            ]
            with card("oportunidade-detalhe"):
                st.dataframe(df_opp[colunas_exibir].head(500), width="stretch", hide_index=True)
