"""Página: análise histórica (faturamento, pedidos entrando no funil, devoluções por mês).

Reusa scripts/query_vendas_sap.py::faturamento_mensal/pedidos_mensal/devolucoes_mensal.

Por que só essas 3 séries: fct_pendencia_sap e fct_estoque_lote_sap (backlog/estoque) só
guardam o estado de HOJE — são recarregadas por completo a cada rodada, sem histórico
preservado (confirmado ao vivo 2026-08-25: 1 única data em Data_Processamento_DW). Não dá
pra reconstruir "como estava o backlog há 6 meses". Faturamento/Pedido/Devolução, por outro
lado, têm data real de transação acumulada desde 2011-2014 — são séries de verdade.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import devolucoes_mensal, faturamento_mensal, pedidos_mensal  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Análise Histórica — Vendas SAP", page_icon="📈", layout="wide")
st.title(":material/trending_up: Análise Histórica")
st.caption(
    "Faturamento, pedidos entrando no funil e devoluções/abatimentos, mês a mês — as 3 "
    "únicas séries com data real de transação nesta base. Backlog e estoque **não têm "
    "histórico salvo** (só o estado de hoje) — não dá pra montar um gráfico de tendência "
    "pra eles."
)

meses = st.slider("Período (meses)", min_value=6, max_value=60, value=24, step=6)


@st.cache_data(ttl=1800, show_spinner="Consultando histórico...")
def _historico_cached(meses: int) -> dict[str, pd.DataFrame]:
    return {
        "faturamento": faturamento_mensal(meses=meses),
        "pedidos": pedidos_mensal(meses=meses),
        "devolucoes": devolucoes_mensal(meses=meses),
    }


dados = _historico_cached(int(meses))
df_fat = dados["faturamento"]
df_ped = dados["pedidos"]
df_dev = dados["devolucoes"]


def _resumo_ano_atual_vs_anterior(df: pd.DataFrame, col_valor: str) -> tuple[float, float]:
    """Soma os últimos 12 meses da série vs os 12 meses anteriores a esses — pra comparação."""
    if df.empty or len(df) < 2:
        return 0.0, 0.0
    ultimos_12 = df.tail(12)[col_valor].sum()
    anteriores_12 = df.iloc[max(0, len(df) - 24) : max(0, len(df) - 12)][col_valor].sum()
    return ultimos_12, anteriores_12


st.subheader("Faturamento")
if df_fat.empty:
    st.info("Sem dado de faturamento no período.")
else:
    atual, anterior = _resumo_ano_atual_vs_anterior(df_fat, "Valor_Faturado")
    variacao = ((atual - anterior) / anterior) if anterior else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("Últimos 12 meses", f"R$ {atual:,.0f}")
    c2.metric("12 meses anteriores", f"R$ {anterior:,.0f}")
    c3.metric("Variação", f"{variacao:+.1%}")
    with card("historico-faturamento"):
        st.bar_chart(df_fat.set_index("Mes")["Valor_Faturado"])

st.divider()

st.subheader("Pedidos entrando no funil")
st.caption("`Valor_Liquido_Pedido` por `Data_Inclusao_Pedido` — quanto entrou de pedido novo por mês, não é backlog.")
if df_ped.empty:
    st.info("Sem dado de pedido no período.")
else:
    atual, anterior = _resumo_ano_atual_vs_anterior(df_ped, "Valor_Pedido")
    variacao = ((atual - anterior) / anterior) if anterior else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("Últimos 12 meses", f"R$ {atual:,.0f}")
    c2.metric("12 meses anteriores", f"R$ {anterior:,.0f}")
    c3.metric("Variação", f"{variacao:+.1%}")
    with card("historico-pedidos"):
        st.bar_chart(df_ped.set_index("Mes")["Valor_Pedido"])

st.divider()

st.subheader("Faturamento x Pedido entrado")
st.caption(
    "Comparação de tendência (não são o mesmo pedido de um mês pro outro — um pedido de "
    "julho pode ser faturado em agosto). Se \"Pedido\" cresce consistentemente mais que "
    "\"Faturado\" por vários meses seguidos, é um sinal indireto de que o funil pode estar "
    "acumulando mais do que escoando."
)
if not df_fat.empty and not df_ped.empty:
    comparacao = pd.merge(
        df_fat[["Mes", "Valor_Faturado"]], df_ped[["Mes", "Valor_Pedido"]], on="Mes", how="outer"
    ).fillna(0).set_index("Mes").sort_index()
    with card("historico-comparacao"):
        st.bar_chart(comparacao)

st.divider()

st.subheader("Devoluções / abatimentos de negócio")
st.caption("Exclui `Tipo_Documento_Contabil = 'RV'` (transferência de faturamento de rotina).")
if df_dev.empty:
    st.info("Sem dado de devolução no período.")
else:
    atual, anterior = _resumo_ano_atual_vs_anterior(df_dev, "Valor")
    variacao = ((atual - anterior) / anterior) if anterior else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("Últimos 12 meses", f"R$ {atual:,.0f}")
    c2.metric("12 meses anteriores", f"R$ {anterior:,.0f}")
    c3.metric("Variação", f"{variacao:+.1%}")
    with card("historico-devolucoes"):
        st.bar_chart(df_dev.set_index("Mes")["Valor"])
