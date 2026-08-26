"""Página: estoque restrito x disponível + validade dos lotes.

Reusa scripts/query_vendas_sap.py::estoque_restrito_disponivel/estoque_validade_resumo/
estoque_validade.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    estoque_restrito_disponivel,
    estoque_validade,
    estoque_validade_resumo,
)
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Estoque — Vendas SAP", page_icon="📦", layout="wide")
st.title(":material/inventory_2: Estoque")
st.caption(
    "Fonte: `GOLD.vendas_sap.fct_estoque_lote_sap`. O filtro global do sidebar não se "
    "aplica aqui — estoque é uma foto de agora, sem dimensão de cliente/data."
)
PAISES = {"Brasil": "BR", "Uruguai": "UY", "Colômbia": "CO", "Alemanha": "DE"}

col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
with col1:
    pais_opcao = st.selectbox(
        "País",
        options=["Todos"] + list(PAISES),
        index=1,
        help=(
            "Default Brasil (moeda BRL, o grosso do estoque). Os outros países valoram em "
            "moeda local (Uruguai=UYU, Colômbia=COP) — selecionar um deles mostra o valor "
            "financeiro naquela moeda, não convertido pra R$ (sem tabela de câmbio "
            "disponível nesta base). 'Todos' mistura os dois nas quantidades, mas o valor "
            "financeiro nos totais/gráficos fica restrito a BRL pra não somar moeda errado."
        ),
    )
with col2:
    codigo_centro = st.text_input("Centro específico (opcional)", placeholder="ex.: 1100")
with col3:
    produto_acabado_opcao = st.selectbox(
        "Produto",
        options=["Todos", "Acabado", "Não Acabado"],
        index=1,
        help=(
            "Vem do Tipo_Material em dim_material_sap: 'Acabado' = ZFER (Produto "
            "Terminado) e ZPFA (Prod. Terminado IFA Biotech); 'Não Acabado' = tudo o "
            "resto (matéria-prima, embalagem, granel, consumíveis, etc.)."
        ),
    )
with col4:
    linhas = st.slider("Linhas exibidas", min_value=20, max_value=500, value=100, step=20)

produto_acabado = {"Todos": None, "Acabado": True, "Não Acabado": False}[produto_acabado_opcao]
pais_centro = PAISES.get(pais_opcao)

tab_restrito, tab_validade = st.tabs(["Restrito x Disponível", "Validade dos lotes"])


@st.cache_data(ttl=300, show_spinner="Consultando estoque...")
def _estoque_cached(codigo_centro: Optional[str], produto_acabado: Optional[bool], pais_centro: Optional[str]) -> pd.DataFrame:
    return estoque_restrito_disponivel(
        codigo_centro=codigo_centro, produto_acabado=produto_acabado, pais_centro=pais_centro, limit=2000
    )


@st.cache_data(ttl=300, show_spinner="Consultando validade dos lotes...")
def _validade_resumo_cached(codigo_centro: Optional[str], produto_acabado: Optional[bool], pais_centro: Optional[str]) -> pd.DataFrame:
    return estoque_validade_resumo(codigo_centro=codigo_centro, produto_acabado=produto_acabado, pais_centro=pais_centro)


@st.cache_data(ttl=300, show_spinner="Consultando lotes mais urgentes...")
def _validade_detalhe_cached(codigo_centro: Optional[str], produto_acabado: Optional[bool], pais_centro: Optional[str]) -> pd.DataFrame:
    return estoque_validade(codigo_centro=codigo_centro, produto_acabado=produto_acabado, pais_centro=pais_centro, limit=2000)


with tab_restrito:
    df = _estoque_cached(codigo_centro or None, produto_acabado, pais_centro)

    if df.empty:
        st.info("Nada encontrado para esse filtro.")
    else:
        if pais_centro:
            # 1 país selecionado = 1 moeda só, sem risco de misturar.
            df_valor = df
            moeda_label = df["Moeda"].iloc[0]
        else:
            # "Todos": só BRL entra nos totais financeiros, pra não somar moeda diferente.
            df_valor = df[df["Moeda"] == "BRL"]
            moeda_label = "BRL"
        valor_fora = df.loc[df["Moeda"] != moeda_label, "Valor_Financeiro_Estoque"].sum() if not pais_centro else 0

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(
            "Qtd Física Total",
            f"{df['Qtd_Fisico_Total'].sum():,.0f}",
            help="Tudo que existe fisicamente no depósito: Disponível + Qualidade + Bloqueado + Transferência.",
        )
        c2.metric(
            "Qtd Disponível p/ Venda",
            f"{df['Qtd_Disponivel_Venda'].sum():,.0f}",
            help="Já passou por todas as checagens — pode ser alocado num pedido agora.",
        )
        c3.metric(
            "Qtd em Qualidade",
            f"{df['Qtd_Qualidade'].sum():,.0f}",
            help=(
                "Lote recebido, mas ainda aguardando laudo/liberação do controle de "
                "qualidade — etapa normal do processo (todo lote passa por isso antes de "
                "poder ser vendido), não é sinal de problema por si só."
            ),
        )
        c4.metric(
            "Qtd Bloqueada",
            f"{df['Qtd_Bloqueado'].sum():,.0f}",
            help=(
                "Travado manualmente por um motivo específico (ex.: suspeita de desvio, "
                "quarentena, decisão comercial) — diferente de 'Qualidade', que é etapa "
                "normal do processo."
            ),
        )
        c5.metric(f"Valor Financeiro Total ({moeda_label})", f"{moeda_label} {df_valor['Valor_Financeiro_Estoque'].sum():,.2f}")
        if valor_fora:
            st.caption(
                f"+ {valor_fora:,.2f} em moeda(s) local(is) de outros países (fora de {moeda_label}), "
                "não somado acima — ver coluna `Moeda` no detalhe, ou filtre por País pra ver certo."
            )

        st.divider()

        with card("estoque-centro"):
            col_a, col_b = st.columns([1, 1])
            with col_a:
                st.subheader("Disponível x Qualidade x Bloqueado, por Centro")
                resumo_centro = df.groupby("Nome_Centro")[
                    ["Qtd_Disponivel_Venda", "Qtd_Qualidade", "Qtd_Bloqueado"]
                ].sum()
                st.bar_chart(resumo_centro)
            with col_b:
                st.subheader(f"Top materiais por valor financeiro em estoque ({moeda_label})")
                top_material = df_valor.nlargest(15, "Valor_Financeiro_Estoque").set_index(
                    "Descricao_Material"
                )["Valor_Financeiro_Estoque"]
                st.bar_chart(top_material)

        st.subheader("Acabado x Não Acabado")
        resumo_acabado = df.groupby("Produto_Acabado")[["Qtd_Disponivel_Venda", "Qtd_Qualidade", "Qtd_Bloqueado"]].sum()
        resumo_acabado_valor = df_valor.groupby("Produto_Acabado")["Valor_Financeiro_Estoque"].sum()
        with card("estoque-acabado"):
            col_e, col_f = st.columns([1, 1])
            with col_e:
                st.bar_chart(resumo_acabado)
            with col_f:
                st.dataframe(
                    resumo_acabado_valor.to_frame().style.format(
                        {"Valor_Financeiro_Estoque": f"{moeda_label} {{:,.2f}}"}
                    ),
                    width="stretch",
                )

        st.subheader("Status do Material")
        st.caption(
            "`ATIVO` x `MARCADO PARA EXCLUSAO` (cadastro do material marcado pra sair de linha "
            "no SAP) — estoque parado de material já sinalizado pra descontinuar é um sinal "
            "de que provavelmente não vai girar, diferente de bloqueio físico de lote."
        )
        resumo_status = df.groupby("Status_Material")[["Qtd_Fisico_Total"]].sum()
        resumo_status_valor = df_valor.groupby("Status_Material")["Valor_Financeiro_Estoque"].sum()
        with card("estoque-status"):
            col_i, col_j = st.columns([1, 1])
            with col_i:
                st.bar_chart(resumo_status)
            with col_j:
                st.dataframe(
                    resumo_status_valor.to_frame().style.format(
                        {"Valor_Financeiro_Estoque": f"{moeda_label} {{:,.2f}}"}
                    ),
                    width="stretch",
                )
        if "MARCADO PARA EXCLUSAO" in resumo_status.index:
            qtd_exclusao = resumo_status.loc["MARCADO PARA EXCLUSAO", "Qtd_Fisico_Total"]
            st.warning(
                f"**{qtd_exclusao:,.0f} unidades em estoque de material já marcado pra exclusão** "
                "no cadastro SAP — candidato a write-off/descarte, vale investigar com o time de "
                "materiais/qualidade."
            )

        with st.expander("Bloqueio de material (Descricao_Status_Global_Material)"):
            st.caption(
                "Diferente de Qtd_Bloqueado (lote específico bloqueado): isso é o material "
                "inteiro bloqueado no cadastro (ex.: pra suprimento/depósito ou roteiro)."
            )
            with card("estoque-bloqueio-material"):
                st.dataframe(
                    df.groupby("Descricao_Status_Global_Material")["Qtd_Fisico_Total"]
                    .sum()
                    .sort_values(ascending=False),
                    width="stretch",
                )

        st.divider()

        st.subheader(f"Detalhe ({min(linhas, len(df))} de {len(df)} linhas)")
        colunas_exibir = [
            "Codigo_Material", "Descricao_Material", "Produto_Acabado", "Descricao_Tipo_Material",
            "Status_Material", "Descricao_Status_Global_Material",
            "Codigo_Centro", "Nome_Centro", "Pais_Centro", "Moeda",
            "Qtd_Disponivel_Venda", "Qtd_Qualidade", "Qtd_Bloqueado",
            "Qtd_Transferencia", "Qtd_Reservada", "Qtd_Fisico_Total", "Valor_Financeiro_Estoque",
        ]
        with card("estoque-detalhe"):
            st.dataframe(df[colunas_exibir].head(linhas), width="stretch", hide_index=True)

with tab_validade:
    st.caption(
        "Lotes sem `Data_Validade` cadastrada (comum em embalagem/material de manutenção, "
        "onde validade não se aplica) e o sentinela SAP `2999-12-31` (\"sem vencimento "
        "definido\") ficam de fora dessa análise."
    )
    FAIXAS_VALIDADE = ["Vencido", "0-30 dias", "31-90 dias", "91-180 dias", "180+ dias"]
    MOEDA_POR_PAIS = {"BR": "BRL", "UY": "UYU", "CO": "COP", "DE": "EUR"}

    df_resumo = _validade_resumo_cached(codigo_centro or None, produto_acabado, pais_centro)
    if df_resumo.empty:
        st.info("Nenhum lote com validade cadastrada nesse filtro.")
    else:
        # Moeda do resumo: se um país foi escolhido, é a moeda daquele país; senão, BRL
        # (mesma regra da aba Restrito x Disponível).
        moeda_resumo = MOEDA_POR_PAIS.get(pais_centro, "BRL") if pais_centro else "BRL"

        df_resumo = df_resumo.set_index("Faixa_Validade").reindex(FAIXAS_VALIDADE).fillna(0)
        valor_vencido = df_resumo.loc["Vencido", "Valor_Financeiro_Estoque"]
        valor_total = df_resumo["Valor_Financeiro_Estoque"].sum()
        pct_vencido = (valor_vencido / valor_total) if valor_total else 0

        c1, c2, c3 = st.columns(3)
        c1.metric(f"Valor Vencido ({moeda_resumo})", f"{moeda_resumo} {valor_vencido:,.2f}", help="Lotes com Data_Validade no passado.")
        c2.metric("% do valor total vencido", f"{pct_vencido:.1%}")
        c3.metric("Lotes vencidos", f"{int(df_resumo.loc['Vencido', 'Qtd_Lotes']):,}")

        if pct_vencido >= 0.1:
            st.warning(f"**{pct_vencido:.1%} do valor em estoque nesse filtro está vencido** ({moeda_resumo} {valor_vencido:,.2f}).")

        st.divider()

        with card("estoque-validade-faixa"):
            col_g, col_h = st.columns([1, 1])
            with col_g:
                st.subheader("Valor por faixa de validade")
                st.bar_chart(df_resumo["Valor_Financeiro_Estoque"])
            with col_h:
                st.dataframe(
                    df_resumo[["Qtd_Lotes", "Qtd_Fisico_Total", "Valor_Financeiro_Estoque"]].style.format(
                        {
                            "Valor_Financeiro_Estoque": f"{moeda_resumo} {{:,.2f}}",
                            "Qtd_Lotes": "{:,.0f}",
                            "Qtd_Fisico_Total": "{:,.0f}",
                        }
                    ),
                    width="stretch",
                )

        st.divider()

        st.subheader("Lotes mais urgentes (vencidos ou vencendo antes)")
        st.caption("Coluna `Moeda`: filtre por País acima pra garantir que o valor exibido é 1 moeda só.")
        df_detalhe = _validade_detalhe_cached(codigo_centro or None, produto_acabado, pais_centro)
        colunas_validade = [
            "Codigo_Material", "Descricao_Material", "Status_Material", "Numero_Lote",
            "Codigo_Centro", "Nome_Centro", "Moeda",
            "Data_Producao", "Data_Validade", "Dias_Para_Vencer", "Faixa_Validade",
            "Qtd_Estoque_Fisico_Total", "Valor_Financeiro_Estoque",
        ]
        with card("estoque-validade-detalhe"):
            st.dataframe(df_detalhe[colunas_validade].head(linhas), width="stretch", hide_index=True)
