"""Página: Meta x Realizado, por mês x BU.

Reusa scripts/query_vendas_sap.py::meta_vs_realizado_mensal. Ver docstring da função pra
entender a fonte da meta (`vendas.fat_meta_equipe`, SharePoint — planejamento, não
transação, ver docs/CONTEXTO_VENDAS_SAP.md §8.3) e a cobertura do realizado (herda o
crosswalk cliente→setor de ~52%, o resto cai em BU 'NAO ALOCADO').
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import meta_vs_realizado_mensal  # noqa: E402
from scripts.ui_charts_comercial import grafico_meta_realizado  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Meta x Realizado — Vendas SAP", page_icon="🎯", layout="wide")
st.title(":material/track_changes: Meta x Realizado")
st.caption(
    "Meta vem de `vendas.fat_meta_equipe` — planejamento comercial (SharePoint, mês x "
    "setor x material), não existe (nem pode existir) fonte SAP/Salesforce equivalente: "
    "meta é decisão de orçamento, não uma transação observável. Realizado é atribuído ao "
    "mesmo `cod_setor` da meta via `vendas.dim_cliente_setor` — herda a cobertura ~52% "
    "desse crosswalk, então faturamento de cliente sem setor mapeado não desaparece, só "
    "cai em BU **'NAO ALOCADO'** (não misturar com falta de meta em si). `BU` aqui é o "
    "valor bruto de `fat_meta_equipe.bu` — nomenclatura própria, não é 1:1 com a Linha de "
    "Negócio da página de Faturamento por Org Vendas."
)

meses = st.slider("Período (meses)", min_value=3, max_value=24, value=8, step=1)
bu_opcao = st.selectbox(
    "BU", ["Todas", "ONCO-HEMATO", "FARMA", "BLAU AESTHETICS", "MS", "Botulift"], index=0
)
bu = None if bu_opcao == "Todas" else bu_opcao


@st.cache_data(ttl=900, show_spinner="Consultando meta x realizado...")
def _meta_cached(meses: int, bu: Optional[str]) -> pd.DataFrame:
    import datetime

    data_fim = datetime.date.today()
    data_inicio = (data_fim.replace(day=1) - pd.DateOffset(months=meses - 1)).date()
    return meta_vs_realizado_mensal(data_inicio=data_inicio, data_fim=data_fim, bu=bu)


df = _meta_cached(int(meses), bu)

if df.empty:
    st.info("Nada encontrado para esse período/BU.")
else:
    df["Atingimento"] = (df["Valor_Realizado"] / df["Meta_Valor"]).where(df["Meta_Valor"] > 0)

    total_meta = df["Meta_Valor"].sum()
    total_realizado = df["Valor_Realizado"].sum()
    atingimento_total = (total_realizado / total_meta) if total_meta else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Meta Total (R$)", f"{total_meta:,.0f}")
    c2.metric("Realizado Total (R$)", f"{total_realizado:,.0f}")
    c3.metric("Atingimento", f"{atingimento_total:.1%}")

    st.caption(
        "Atingimento acima **exclui** BU 'NAO ALOCADO' do numerador/denominador de meta "
        "(não tem meta associada) mas o realizado 'NAO ALOCADO' continua existindo à parte "
        "— ver tabela abaixo. Não é o faturamento total da empresa, só a fatia com meta."
    )

    st.divider()

    st.subheader("Meta x Realizado por mês (BUs com meta)")
    df_com_meta = df[df["BU"] != "NAO ALOCADO"]
    if not df_com_meta.empty:
        comparacao = (
            df_com_meta.groupby("Mes")[["Meta_Valor", "Valor_Realizado"]].sum().reset_index()
        )
        with card("metas-mensal"):
            st.altair_chart(grafico_meta_realizado(comparacao, "Mes"), width="stretch")

    st.divider()

    st.subheader("Atingimento por BU (R$, soma do período)")
    por_bu = df_com_meta.groupby("BU")[["Meta_Valor", "Valor_Realizado"]].sum().reset_index()
    por_bu = por_bu.sort_values("Valor_Realizado", ascending=False)
    with card("metas-por-bu"):
        col_bu_a, col_bu_b = st.columns([2, 1])
        with col_bu_a:
            st.altair_chart(grafico_meta_realizado(por_bu, "BU"), width="stretch")
        with col_bu_b:
            st.dataframe(
                por_bu.assign(
                    Atingimento=lambda d: (d["Valor_Realizado"] / d["Meta_Valor"]).where(
                        d["Meta_Valor"] > 0
                    )
                ).style.format(
                    {
                        "Meta_Valor": "R$ {:,.0f}",
                        "Valor_Realizado": "R$ {:,.0f}",
                        "Atingimento": "{:.1%}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )

    st.divider()

    st.subheader("Detalhe mensal")
    with card("metas-detalhe"):
        st.dataframe(
            df[
                [
                    "Mes",
                    "BU",
                    "Meta_Valor",
                    "Valor_Realizado",
                    "Atingimento",
                    "Meta_Unidades",
                    "Unidades_Realizado",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
