"""Página: Visão 360 — Oportunidade + Pedido/Pendência/Fatura + Remessa + Estoque, por
material e/ou cliente.

Reusa scripts/query_vendas_sap.py::visao_360_material_cliente. Diferente de Material
(pedido+estoque corrigido+FIFO, só material) e Cliente 360 (pedido+crédito+devolução, só
cliente) — esta cruza os 2 filtros ao mesmo tempo e adiciona Oportunidade e Remessa, que
nenhuma das outras duas página traz.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import visao_360_material_cliente  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Visão 360 — Vendas SAP", page_icon="🔎", layout="wide")
st.title(":material/hub: Visão 360: Material e/ou Cliente")
st.caption(
    "Informe material e/ou cliente pra ver Oportunidade, Pedido/Pendência/Fatura, Remessa "
    "e Estoque juntos. Só material → veja também a página **Material** (tem a correção de "
    "estoque por lote e a simulação FIFO). Só cliente → veja também **Cliente 360** (tem "
    "crédito e devoluções)."
)

col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
with col1:
    codigo_produto = st.text_input("Código do material (opcional)", placeholder="ex.: PA5522", key="v360_material").strip().upper() or None
with col2:
    codigo_cliente = st.text_input("Código do cliente (opcional)", placeholder="ex.: 0001004873", key="v360_cliente").strip() or None
with col3:
    tipo_ordem_venda = st.text_input(
        "Tipo de ordem — AUART (opcional)", placeholder="ex.: ZPRI", key="v360_tipo_ordem",
        help="Código SAP do tipo de ordem de venda. Veja os códigos e volume em Pedidos → \"Backlog por Tipo de Ordem de Venda\".",
    ).strip().upper() or None
with col4:
    somente_pendente = st.checkbox("Só backlog aberto", value=False, key="v360_somente_pendente")

if not codigo_produto and not codigo_cliente:
    st.caption("Informe pelo menos 1 dos 2 filtros acima (material e/ou cliente).")
else:

    @st.cache_data(ttl=300, show_spinner="Consultando Oportunidade + Pedido + Remessa + Estoque...")
    def _v360_cached(
        codigo_produto: str | None, codigo_cliente: str | None, tipo_ordem_venda: str | None, somente_pendente: bool
    ) -> dict[str, pd.DataFrame]:
        return visao_360_material_cliente(
            codigo_produto=codigo_produto,
            codigo_cliente=codigo_cliente,
            tipo_ordem_venda=tipo_ordem_venda,
            somente_pendente=somente_pendente,
        )

    resultado = _v360_cached(codigo_produto, codigo_cliente, tipo_ordem_venda, somente_pendente)
    df_pedidos = resultado["Pedido / Pendência / Fatura"]
    df_oportunidade = resultado["Oportunidade"]
    df_remessas = resultado["Remessas"]

    if df_pedidos.empty and df_oportunidade.empty and df_remessas.empty:
        st.info("Nada encontrado para esse filtro.")
    else:
        df_opp_match_kpi = df_oportunidade[df_oportunidade["Nome_Oportunidade"].notna()] if not df_oportunidade.empty else df_oportunidade
        valor_oportunidade_kpi = (
            df_opp_match_kpi.drop_duplicates(subset=["Nome_Oportunidade", "Data_Criacao_Oportunidade"])[
                "Valor_Oportunidade"
            ].sum()
            if not df_opp_match_kpi.empty
            else 0.0
        )

        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        r1c1.metric("Oportunidade (R$, dedup.)", f"R$ {valor_oportunidade_kpi:,.2f}")
        r1c2.metric("Em pedido (R$)", f"R$ {df_pedidos['Valor_Liquido_Pedido'].sum():,.2f}" if not df_pedidos.empty else "R$ 0,00")
        r1c3.metric("Faturado (R$)", f"R$ {df_pedidos['Valor_Liquido_Faturado'].sum():,.2f}" if not df_pedidos.empty else "R$ 0,00")
        r1c4.metric("Pendente de faturamento (R$)", f"R$ {df_pedidos['Valor_Pendente_Faturamento'].sum():,.2f}" if not df_pedidos.empty else "R$ 0,00")

        r2c1, r2c2, r2c3 = st.columns(3)
        r2c1.metric("Qtd remetida (24 meses)", f"{df_remessas['Qtd_Remetida'].sum():,.0f}" if not df_remessas.empty else "0")
        r2c2.metric("Qtd pendente de remessa", f"{df_pedidos['Qtd_Pendente_Remessa'].sum():,.0f}" if not df_pedidos.empty else "0")
        if "Estoque" in resultado and not resultado["Estoque"].empty:
            r2c3.metric("Estoque disponível p/ venda", f"{resultado['Estoque']['Qtd_Disponivel_Venda'].sum():,.0f}")
        else:
            r2c3.metric("Estoque disponível p/ venda", "—", help="Informe um material pra ver estoque.")

        st.divider()

        abas = ["Oportunidade", "Pedido / Pendência / Fatura", "Remessas"]
        if "Estoque" in resultado:
            abas.append("Estoque")
        tabs = st.tabs(abas)

        with tabs[0]:
            with card("v360-oportunidade"):
                df_opp_match = df_oportunidade[df_oportunidade["Nome_Oportunidade"].notna()]
                if df_opp_match.empty:
                    st.info("Nenhuma Oportunidade vinculada nesse filtro (últimos 24 meses).")
                else:
                    valor_oportunidade = df_opp_match.drop_duplicates(
                        subset=["Nome_Oportunidade", "Data_Criacao_Oportunidade"]
                    )["Valor_Oportunidade"].sum()
                    st.metric("Valor de Oportunidade (deduplicado)", f"R$ {valor_oportunidade:,.2f}")
                    colunas_opp = [
                        "Numero_Pedido", "Item_Pedido", "Tipo_Ordem_Venda", "Nome_Cliente", "Codigo_Produto",
                        "Descricao_Produto", "Nome_Oportunidade", "Estagio_Oportunidade",
                        "Oportunidade_Ganha", "Valor_Oportunidade", "Valor_Item_Oportunidade",
                        "Valor_Liquido_Pedido", "Status_Pendencia",
                    ]
                    st.dataframe(df_opp_match[colunas_opp], width="stretch", hide_index=True)

        with tabs[1]:
            with card("v360-pedido"):
                if df_pedidos.empty:
                    st.info("Nenhum pedido encontrado para esse filtro.")
                else:
                    if not tipo_ordem_venda and df_pedidos["Tipo_Ordem_Venda"].nunique() > 1:
                        st.markdown("**Valor pendente por Tipo de Ordem de Venda (AUART)**")
                        st.caption(
                            "Sem tradução de código pra texto disponível nesta base. Use o filtro "
                            "\"Tipo de ordem\" acima pra restringir a 1 tipo específico."
                        )
                        resumo_tipo_ordem = df_pedidos.groupby("Tipo_Ordem_Venda")["Valor_Pendente_Faturamento"].sum().sort_values(
                            ascending=False
                        )
                        st.bar_chart(resumo_tipo_ordem)
                    colunas_pedido = [
                        "Numero_Pedido", "Item_Pedido", "Tipo_Ordem_Venda", "Data_Inclusao_Pedido", "Codigo_Produto",
                        "Descricao_Produto", "Nome_Cliente", "Nome_Centro",
                        "Qtd_Pedida", "Qtd_Remetida", "Qtd_Pendente_Remessa",
                        "Qtd_Faturada", "Qtd_Pendente_Operacional",
                        "Valor_Liquido_Pedido", "Valor_Liquido_Faturado", "Valor_Pendente_Faturamento",
                        "Status_Pendencia", "Status_Faturamento",
                    ]
                    st.caption(
                        "`Qtd_Remetida` = quanto já foi entregue (remessa criada); "
                        "`Qtd_Pendente_Remessa` = quanto de `Qtd_Pedida` ainda não foi remetido "
                        "— a mesma remessa, do lado inverso. Ver aba **Remessas** pro detalhe "
                        "item a item de cada entrega."
                    )
                    st.dataframe(df_pedidos[colunas_pedido], width="stretch", hide_index=True)

        with tabs[2]:
            with card("v360-remessas"):
                if df_remessas.empty:
                    st.info("Nenhuma remessa encontrada para esse filtro (últimos 24 meses).")
                else:
                    colunas_remessa = [
                        "Numero_Entrega", "Item_Entrega", "Numero_Pedido_Origem", "Data_Remessa",
                        "Tipo_Remessa", "Codigo_Produto", "Codigo_Centro", "Codigo_Cliente", "Nome_Cliente",
                        "Charg_Numero_Do_Lote", "Qtd_Remetida", "Peso_Liquido",
                    ]
                    st.dataframe(df_remessas[colunas_remessa], width="stretch", hide_index=True)

        if "Estoque" in resultado:
            with tabs[3]:
                df_estoque = resultado["Estoque"]
                with card("v360-estoque"):
                    if df_estoque.empty:
                        st.info("Nenhum estoque encontrado para esse material.")
                    else:
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Livre", f"{df_estoque['Qtd_Livre'].sum():,.0f}")
                        c2.metric("Bloqueado", f"{df_estoque['Qtd_Bloqueado'].sum():,.0f}")
                        c3.metric(
                            "Já reservado p/ outros pedidos",
                            f"{df_estoque['Qtd_Reservada'].sum():,.0f}",
                            help="Qtd_Reservada — parte do Livre já comprometida com pedidos pendentes deste Material+Centro.",
                        )
                        c4.metric(
                            "Disponível p/ Venda (original, pode estar zerado — ver Material)",
                            f"{df_estoque['Qtd_Disponivel_Venda'].sum():,.0f}",
                        )
                        st.caption(
                            "`Livre` = `Já reservado` + `Disponível p/ Venda` — o reservado não "
                            "\"sumiu\", só já está comprometido com pedidos em aberto desse "
                            "Material+Centro. `Qtd_Disponivel_Venda` aqui vem direto de "
                            "`fct_estoque_lote_sap` — veja a página **Material** para o valor "
                            "corrigido (`Estoque_Disponivel_Corrigido`) e a simulação FIFO por pedido."
                        )
                        colunas_estoque = [
                            "Codigo_Centro", "Nome_Centro", "Status_Material",
                            "Qtd_Livre", "Qtd_Qualidade", "Qtd_Bloqueado", "Qtd_Reservada",
                            "Qtd_Disponivel_Venda", "Qtd_Fisico_Total", "Valor_Financeiro_Estoque",
                        ]
                        st.dataframe(df_estoque[colunas_estoque], width="stretch", hide_index=True)

                        if (df_estoque["Qtd_Reservada"] > 0).any():
                            st.markdown("**Quem está usando a reserva, por centro**")
                            st.caption(
                                "`Qtd_Reservada` (VBBE) é 1 número por Material+Centro — o SAP não "
                                "grava lá qual pedido específico reservou. Abaixo, o backlog aberto "
                                "(`Qtd_Pendente_Remessa > 0`) desse material nesse mesmo centro, que "
                                "normalmente soma bem próximo do reservado — é a explicação mais "
                                "provável de quem está segurando essa quantidade."
                            )
                            limiar_zumbi_dias = st.number_input(
                                "Considerar backlog \"recente\" até quantos dias (o resto entra como possível pedido zumbi)",
                                min_value=30, max_value=3650, value=365, step=30, key="v360_limiar_zumbi",
                            )
                            df_backlog_centro = (
                                df_pedidos[df_pedidos["Qtd_Pendente_Remessa"] > 0]
                                if not df_pedidos.empty
                                else df_pedidos
                            )
                            for _, linha_centro in df_estoque[df_estoque["Qtd_Reservada"] > 0].iterrows():
                                centro = linha_centro["Codigo_Centro"]
                                reservado_centro = linha_centro["Qtd_Reservada"]
                                df_backlog_este_centro = df_backlog_centro[
                                    df_backlog_centro["Codigo_Centro"] == centro
                                ]
                                soma_backlog = df_backlog_este_centro["Qtd_Pendente_Remessa"].sum()
                                bate = "✅ bate" if abs(soma_backlog - reservado_centro) < 1 else "⚠️ não bate"
                                with st.expander(
                                    f"Centro {centro} — {linha_centro['Nome_Centro']}: reservado "
                                    f"{reservado_centro:,.0f} · backlog aberto soma {soma_backlog:,.0f} ({bate})"
                                ):
                                    if df_backlog_este_centro.empty:
                                        st.info(
                                            "Nenhum pedido em backlog aberto encontrado pra esse "
                                            "centro com o filtro atual — o reservado pode vir de "
                                            "pedido fora do filtro de tipo de ordem, ou de outra "
                                            "origem de reserva (ex.: STO, produção)."
                                        )
                                    else:
                                        df_recente = df_backlog_este_centro[
                                            df_backlog_este_centro["Dias_Desde_Inclusao_Pedido"] <= limiar_zumbi_dias
                                        ].sort_values("Dias_Desde_Inclusao_Pedido")
                                        df_zumbi = df_backlog_este_centro[
                                            df_backlog_este_centro["Dias_Desde_Inclusao_Pedido"] > limiar_zumbi_dias
                                        ].sort_values("Dias_Desde_Inclusao_Pedido", ascending=False)

                                        colunas_backlog = [
                                            "Numero_Pedido", "Item_Pedido", "Nome_Cliente",
                                            "Dias_Desde_Inclusao_Pedido", "Qtd_Pendente_Remessa",
                                            "Status_Pendencia",
                                        ]

                                        if not df_zumbi.empty:
                                            bate_recente = (
                                                "✅ bate melhor com o recente"
                                                if abs(df_recente["Qtd_Pendente_Remessa"].sum() - reservado_centro) < abs(soma_backlog - reservado_centro)
                                                else None
                                            )
                                            st.warning(
                                                f"🧟 **{len(df_zumbi)} item(ns) de pedido com mais de "
                                                f"{limiar_zumbi_dias:,} dias** (até "
                                                f"{int(df_zumbi['Dias_Desde_Inclusao_Pedido'].max()):,} dias — "
                                                f"{df_zumbi['Dias_Desde_Inclusao_Pedido'].max() / 365:.1f} anos), "
                                                f"somando {df_zumbi['Qtd_Pendente_Remessa'].sum():,.0f} unidades — "
                                                "possível **pedido zumbi** (nunca baixado/cancelado no SAP, sem "
                                                "relação real com a reserva atual). "
                                                + (bate_recente or "")
                                                + " Não tomar como demanda real sem confirmar com o time de "
                                                "vendas/SAP antes de decidir compra ou produção."
                                            )
                                            st.caption(
                                                f"Backlog recente (≤ {limiar_zumbi_dias:,} dias): "
                                                f"{len(df_recente):,} item(ns), "
                                                f"{df_recente['Qtd_Pendente_Remessa'].sum():,.0f} unidades — "
                                                "candidato mais provável a explicar a reserva viva."
                                            )
                                            if not df_recente.empty:
                                                st.dataframe(df_recente[colunas_backlog], width="stretch", hide_index=True)
                                            st.markdown("**Backlog antigo — possível pedido zumbi**")
                                            st.dataframe(df_zumbi[colunas_backlog], width="stretch", hide_index=True)
                                        else:
                                            st.dataframe(df_recente[colunas_backlog], width="stretch", hide_index=True)
