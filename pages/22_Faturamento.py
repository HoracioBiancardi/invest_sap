"""Página: Faturamento — visão executiva resumida (total bruto de `vendas_sap`).

Funde 2 páginas antigas: Faturamento por Org Vendas x Linha de Negócio
(scripts/query_vendas_sap.py::faturamento_por_org_vendas_linha_negocio) e a parte de
faturamento/devoluções de Análise Histórica (faturamento_mensal/devolucoes_mensal) — a
parte de "pedidos entrando no funil" de Análise Histórica virou parte da página **Pedidos**.

Diferente de **Faturamento (Painel Vendas)** (scripts/query_faturamento_comercial.py, que
passa pelo crosswalk cliente→setor comercial, ~52% cobertura) — aqui é o total bruto de
`fct_faturamento_itens_sap`, sem esse crosswalk. Ver docs/CONTEXTO_VENDAS_SAP.md §10.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    converter_para_brl,
    devolucoes_mensal,
    faturamento_mensal,
    faturamento_por_org_vendas_linha_negocio,
    taxas_cambio_brl,
)
from scripts.ui_theme import (  # noqa: E402
    card,
    render_filtro_periodo_tipo_cliente,
    render_valor_convertido_brl,
    render_valor_por_moeda,
)

st.set_page_config(page_title="Faturamento — Vendas SAP", page_icon="💰", layout="wide")
st.title(":material/payments: Faturamento")

render_filtro_periodo_tipo_cliente()
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
data_inicio = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
st.caption(
    f"Filtro: período de **{data_inicio:%d/%m/%Y}** a **{data_fim:%d/%m/%Y}**, "
    f"tipo de cliente **{tipo_cliente_opcao}**. A série mensal abaixo ignora esse período "
    "de propósito (mostra tendência de mais longo prazo)."
)

tab_resumo, tab_tendencia = st.tabs(["Org Vendas x Linha de Negócio", "Tendência mensal"])


@st.cache_data(ttl=300, show_spinner="Consultando faturamento por Org Vendas x Linha de Negócio...")
def _faturamento_org_cached(data_inicio: datetime.date, data_fim: datetime.date, tipo_cliente: Optional[str]) -> pd.DataFrame:
    return faturamento_por_org_vendas_linha_negocio(data_inicio=data_inicio, data_fim=data_fim, tipo_cliente=tipo_cliente)


with tab_resumo:
    st.caption(
        "Organização de Vendas é o campo SAP (VKORG). Linha de Negócio vem do cruzamento "
        "Cliente → `dim_cliente_setor`/`dim_estrutura` (~52%) com fallback por heurística de "
        "produto dominante (eleva pra ~87%) — coluna `Origem_Linha_Negocio` no detalhe indica "
        "qual camada resolveu cada linha. São 2 dimensões independentes, não uma hierarquia."
    )
    df_org = _faturamento_org_cached(data_inicio, data_fim, tipo_cliente)
    if df_org.empty:
        st.info("Nada encontrado para esse período.")
    else:
        # Consulta agrega por período (sem data por linha) — converte com a taxa do meio
        # do período como referência (achado 2026-09-04: precisão diária não dá pra
        # aplicar aqui sem reestruturar a consulta; pra período curto/médio a diferença é
        # pequena — ver taxas_cambio_brl/converter_para_brl).
        data_meio_periodo = data_inicio + (data_fim - data_inicio) / 2
        df_org = df_org.assign(_Data_Ref=data_meio_periodo)
        render_valor_convertido_brl(df_org, "Valor_Faturado", data_col="_Data_Ref", label="Valor faturado")
        st.metric("Qtd Faturada Total (todas as moedas)", f"{df_org['Qtd_Faturada'].sum():,.0f}")

        _taxas_org = taxas_cambio_brl()
        df_org["Valor_BRL"] = converter_para_brl(df_org, "Valor_Faturado", data_col="_Data_Ref", taxas=_taxas_org)
        n_org_sem_taxa = int(df_org["Valor_BRL"].isna().sum())
        if n_org_sem_taxa:
            st.caption(f"{n_org_sem_taxa:,} linha(s) sem taxa de câmbio disponível ficam de fora dos gráficos/matriz abaixo.")

        st.divider()
        with card("faturamento-org-dimensao"):
            col_a, col_b = st.columns([1, 1])
            with col_a:
                st.subheader("Por Organização de Vendas")
                st.bar_chart(df_org.groupby("Descricao_Org_Vendas")["Valor_BRL"].sum())
            with col_b:
                st.subheader("Por Linha de Negócio")
                st.bar_chart(df_org.groupby("Linha_Negocio")["Valor_BRL"].sum())

        st.divider()
        st.subheader("Matriz Org Vendas x Linha de Negócio (R$, convertido)")
        matriz = df_org.pivot_table(
            index="Descricao_Org_Vendas", columns="Linha_Negocio", values="Valor_BRL", aggfunc="sum", fill_value=0
        )
        with card("faturamento-org-matriz"):
            st.dataframe(matriz.style.format("R$ {:,.2f}"), width="stretch")

        st.divider()
        st.subheader("Detalhe (todas as moedas)")
        with card("faturamento-org-detalhe"):
            st.dataframe(
                df_org[
                    [
                        "Codigo_Org_Vendas", "Descricao_Org_Vendas", "Linha_Negocio",
                        "Origem_Linha_Negocio", "Moeda", "Valor_Faturado", "Qtd_Faturada", "Qtd_Itens",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

with tab_tendencia:
    st.caption(
        "Faturamento e devoluções/abatimentos mês a mês — as únicas 2 séries com data real "
        "de transação acumulada nesta base (backlog/estoque só guardam o estado de hoje)."
    )
    meses = st.slider("Período (meses)", min_value=6, max_value=60, value=24, step=6, key="faturamento_meses")

    @st.cache_data(ttl=1800, show_spinner="Consultando histórico...")
    def _historico_cached(meses: int) -> dict[str, pd.DataFrame]:
        return {"faturamento": faturamento_mensal(meses=meses), "devolucoes": devolucoes_mensal(meses=meses)}

    dados = _historico_cached(int(meses))
    df_fat = dados["faturamento"]
    df_dev = dados["devolucoes"]

    def _janelas_12_meses(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Retorna (últimos 12 meses, 12 meses anteriores) — por Mes distinto, não por
        linha (1 linha por Mes+moeda agora, `.tail(12)` contaria linhas erradas)."""
        if df.empty:
            return df, df
        meses_ordenados = sorted(df["Mes"].unique())
        ultimos = set(meses_ordenados[-12:])
        anteriores = set(meses_ordenados[max(0, len(meses_ordenados) - 24) : max(0, len(meses_ordenados) - 12)])
        return df[df["Mes"].isin(ultimos)], df[df["Mes"].isin(anteriores)]

    st.subheader("Faturamento")
    st.caption("Convertido pra BRL via taxa de câmbio real do SAP (`TCURR`) — ver por moeda original nos expanders abaixo.")
    if df_fat.empty:
        st.info("Sem dado de faturamento no período.")
    else:
        df_fat_atual, df_fat_anterior = _janelas_12_meses(df_fat)
        _taxas = taxas_cambio_brl()
        atual_brl = converter_para_brl(df_fat_atual, "Valor_Faturado", data_col="Mes", taxas=_taxas).sum()
        anterior_brl = converter_para_brl(df_fat_anterior, "Valor_Faturado", data_col="Mes", taxas=_taxas).sum()
        variacao_brl = ((atual_brl - anterior_brl) / anterior_brl) if anterior_brl else 0.0
        st.metric("Variação (últimos 12 meses vs. 12 anteriores)", f"{variacao_brl:+.1%}")
        col_a, col_b = st.columns(2)
        with col_a:
            render_valor_convertido_brl(df_fat_atual, "Valor_Faturado", data_col="Mes", label="Últimos 12 meses", taxas=_taxas)
        with col_b:
            render_valor_convertido_brl(df_fat_anterior, "Valor_Faturado", data_col="Mes", label="12 meses anteriores", taxas=_taxas)
        with card("faturamento-tendencia"):
            pivot_fat = df_fat.pivot_table(index="Mes", columns="Moeda", values="Valor_Faturado", aggfunc="sum", fill_value=0)
            st.bar_chart(pivot_fat)

    st.divider()

    st.subheader("Devoluções / abatimentos de negócio")
    st.caption(
        "Exclui `Tipo_Documento_Contabil = 'RV'` (transferência de faturamento de rotina). "
        "Quebrado por `Empresa_Codigo` (5 empresas SAP distintas nesta base), não por moeda "
        "— esta tabela não tem coluna de moeda, e o mapeamento empresa→moeda não está "
        "documentado (achado 2026-09-04); trate cada empresa como potencialmente uma moeda "
        "diferente, não some entre elas."
    )
    if df_dev.empty:
        st.info("Sem dado de devolução no período.")
    else:
        df_dev_atual, df_dev_anterior = _janelas_12_meses(df_dev)
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Últimos 12 meses, por empresa")
            render_valor_por_moeda(df_dev_atual, "Valor", moeda_col="Empresa_Codigo")
        with col_b:
            st.caption("12 meses anteriores, por empresa")
            render_valor_por_moeda(df_dev_anterior, "Valor", moeda_col="Empresa_Codigo")
        with card("faturamento-devolucoes"):
            pivot_dev = df_dev.pivot_table(index="Mes", columns="Empresa_Codigo", values="Valor", aggfunc="sum", fill_value=0)
            st.bar_chart(pivot_dev)
