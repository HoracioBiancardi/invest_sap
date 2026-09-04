"""Página: limite de crédito + devoluções/abatimentos com motivo.

Reusa scripts/query_vendas_sap.py::credito_disponivel_clientes (limite) e
::devolucoes_credito_motivo (devoluções — fonte GOLD.vendas.dim_credito_devolucoes, a única
com o campo Texto/motivo preenchido; ver docstring da função pra detalhe).
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import credito_disponivel_clientes, devolucoes_credito_motivo  # noqa: E402
from scripts.ui_theme import card, render_filtro_periodo_tipo_cliente  # noqa: E402

st.set_page_config(page_title="Crédito e Devoluções — Vendas SAP", page_icon="💳", layout="wide")
st.title(":material/credit_card: Crédito e Devoluções")

render_filtro_periodo_tipo_cliente()
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao
data_inicio_global = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim_global = st.session_state.get("flt_data_fim", datetime.date.today())
st.caption(f"Filtro: tipo de cliente **{tipo_cliente_opcao}**.")

tab_limite, tab_devolucao = st.tabs(["Limite de crédito", "Devoluções / abatimentos (com motivo)"])


@st.cache_data(ttl=300, show_spinner="Consultando limite de crédito...")
def _credito_cached(apenas_bloqueados: bool, tipo_cliente: Optional[str], limite: int) -> pd.DataFrame:
    return credito_disponivel_clientes(apenas_bloqueados, tipo_cliente=tipo_cliente, limit=limite)


@st.cache_data(ttl=300, show_spinner="Consultando devoluções/abatimentos...")
def _devolucoes_cached(
    data_inicio: datetime.date,
    data_fim: datetime.date,
    excluir_rv: bool,
    nome_cliente: Optional[str],
    tipo_cliente: Optional[str],
) -> pd.DataFrame:
    return devolucoes_credito_motivo(
        data_inicio=data_inicio, data_fim=data_fim, excluir_faturamento_rotina=excluir_rv,
        nome_cliente=nome_cliente, tipo_cliente=tipo_cliente, limit=5000,
    )


with tab_limite:
    st.caption("Fonte: `GOLD.vendas_sap.fct_limite_credito_sap` — limite/exposição por cliente.")
    apenas_bloqueados = st.checkbox("Só clientes bloqueados", value=False)
    limite_credito = st.slider("Máximo de linhas", min_value=100, max_value=20000, value=5000, step=100)

    df_credito = _credito_cached(apenas_bloqueados, tipo_cliente, limite_credito)
    if df_credito.empty:
        st.info("Nada encontrado.")
    else:
        if len(df_credito) == limite_credito:
            st.warning(
                f"Resultado truncado em {limite_credito:,} linhas — pode haver mais clientes "
                "além desse teto. Ajuste o filtro ou aumente o 'Máximo de linhas' acima."
            )
        c1, c2, c3 = st.columns(3)
        c1.metric("Clientes", f"{len(df_credito):,}")
        c2.metric("Limite Concedido Total", f"R$ {df_credito['Valor_Limite_Credito_Concedido'].sum():,.2f}")
        c3.metric("Saldo Vencido Total", f"R$ {df_credito['Valor_Saldo_Vencido'].sum():,.2f}")
        with card("credito-limite"):
            st.dataframe(
                df_credito[
                    [
                        "Codigo_Cliente", "Nome_Cliente", "Classe_Risco_Cliente", "Flag_Cliente_Bloqueado",
                        "Valor_Limite_Credito_Concedido", "Valor_Exposicao_Total_SAP",
                        "Valor_Saldo_A_Vencer", "Valor_Saldo_Vencido", "Valor_Credito_Disponivel",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

with tab_devolucao:
    st.caption(
        "Fonte: `GOLD.vendas.dim_credito_devolucoes` — lançamentos de crédito/devolução/abatimento "
        "de cliente, com o texto de motivo (livre) que o time financeiro registrou no lançamento. "
        "Por padrão exclui `Tp_doc = 'RV'` (transferência de documento de faturamento de rotina, "
        ">95% das linhas, não é devolução/abatimento de negócio de fato)."
    )
    col1, col2 = st.columns([1, 1])
    with col1:
        nome_cliente = st.text_input("Cliente contém (opcional)")
    with col2:
        incluir_rv = st.checkbox("Incluir faturamento de rotina (RV)", value=False)
    st.caption(f"Período: **{data_inicio_global:%d/%m/%Y}** a **{data_fim_global:%d/%m/%Y}** — vem do filtro global no sidebar.")

    df_dev = _devolucoes_cached(data_inicio_global, data_fim_global, not incluir_rv, nome_cliente or None, tipo_cliente)
    if df_dev.empty:
        st.info("Nada encontrado para esse filtro.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Qtd Lançamentos", f"{len(df_dev):,}")
        c2.metric("Valor Total", f"R$ {df_dev['Montante'].sum():,.2f}")

        st.subheader("Por tipo de documento (código SAP)")
        st.caption(
            "Sem tradução oficial disponível pra esses códigos nesta base (T003T não replicada "
            "no HANA) — use o texto de motivo na tabela abaixo, que é bem mais informativo."
        )
        with card("credito-devolucao-tipo-doc"):
            st.bar_chart(df_dev.groupby("Tp_doc")["Montante"].sum())

        st.subheader("Motivos mais frequentes")
        top_motivos = (
            df_dev.groupby("Texto")["Montante"]
            .agg(Valor_Total="sum", Qtd="count")
            .sort_values("Valor_Total", ascending=False)
            .head(30)
        )
        with card("credito-devolucao-motivos"):
            st.dataframe(top_motivos.style.format({"Valor_Total": "R$ {:,.2f}"}), width="stretch")

        st.divider()
        st.subheader(f"Detalhe ({len(df_dev)} linhas)")
        with card("credito-devolucao-detalhe"):
            st.dataframe(
                df_dev[
                    ["N_documento", "Codigo_Cliente", "Nome_Cliente", "Data_documento", "Tp_doc", "Montante", "Texto"]
                ],
                width="stretch",
                hide_index=True,
            )
