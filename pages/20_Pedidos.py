"""Página: Pedidos — visão geral do backlog/volume + busca de 1 pedido específico.

Funde o conteúdo de 3 páginas antigas:
- Pendências (aging, cobertura de estoque, top clientes, tipo de ordem) —
  scripts/query_vendas_sap.py::aging_pendencias/pendencia_status_estoque/
  pendencia_por_tipo_ordem_venda/top_clientes_pendentes.
- Relatório de Pedidos (volume/valor médio mensal, ranking por cliente) —
  scripts/query_vendas_sap.py::pedidos_mensal/pedidos_por_cliente.
- Rastrear Pedido (SAP cru + Gold + Salesforce de 1 pedido específico) —
  scripts/trace_pedido.py::trace_pedido (já traz as 3 camadas juntas, então a aba "Buscar
  pedido" não precisa de consulta adicional).

Detalhe por material (pedido+estoque+fatura por material, simulação FIFO) virou página
própria — ver **Material** no menu.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    aging_pendencias,
    estoque_reservado_por_material_centro,
    pedidos_mensal,
    pedidos_por_cliente,
    pendencia_por_tipo_ordem_venda,
    pendencia_status_estoque,
    pendencias_abertas,
    top_clientes_pendentes,
)
from scripts.trace_pedido import trace_pedido  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Pedidos — Vendas SAP", page_icon="📦", layout="wide")
st.title(":material/receipt_long: Pedidos")

tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")
tipo_cliente = None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao

tab_geral, tab_buscar = st.tabs(["Visão geral", "Buscar pedido"])

with tab_geral:
    st.caption(
        f"Usando filtro global de tipo de cliente: **{tipo_cliente_opcao}** (ajuste no "
        "sidebar). O período (dias) do filtro global **não** se aplica ao backlog aberto de "
        "propósito — ele existe pra mostrar tudo em aberto, inclusive o que é antigo."
    )

    _cache = st.cache_data(ttl=300)
    _aging_cached = _cache(aging_pendencias)
    _estoque_cached = _cache(pendencia_status_estoque)
    _top_clientes_cached = _cache(top_clientes_pendentes)
    _tipo_ordem_cached = _cache(pendencia_por_tipo_ordem_venda)
    _pedidos_mensal_cached = _cache(pedidos_mensal)
    _pedidos_cliente_cached = _cache(pedidos_por_cliente)
    _pendencias_abertas_cached = _cache(pendencias_abertas)
    _reservado_cached = _cache(estoque_reservado_por_material_centro)

    df_aging = _aging_cached(tipo_cliente=tipo_cliente)
    st.subheader("Aging do backlog aberto")
    with card("pedidos-aging"):
        col1, col2 = st.columns([1, 1])
        with col1:
            st.dataframe(df_aging, width="stretch", hide_index=True)
        with col2:
            if not df_aging.empty:
                st.bar_chart(df_aging.set_index("Faixa_Aging")["Valor_Pendente_Total"])
        st.caption(
            "O balde \"60+ dias\" acima junta de 61 dias até pedido de mais de 1 década — "
            "ver \"Radar de pedido zumbi\" abaixo pra abrir esse balde."
        )

    st.divider()

    st.subheader("🧟 Radar de pedido zumbi (visão global, todos os materiais)")
    st.caption(
        "Cruza o backlog aberto (`fct_pendencia_sap`, `Qtd_Pendente_Remessa > 0`) com o "
        "estoque reservado no SAP (`Qtd_Estoque_Reservada`, VBBE) por Material+Centro — "
        "sem filtro de material/cliente, pra achar em toda a base onde o backlog é muito "
        "maior que a reserva viva, sinal de pedido antigo nunca baixado/cancelado no SAP "
        "(mesmo padrão achado manualmente no material PA5522, ver página **Visão 360**). "
        "Filtro de tipo de cliente do sidebar não se aplica aqui (mesmo motivo do aging acima: "
        "propósito é mostrar todo backlog aberto, inclusive o antigo)."
    )
    df_backlog_raw = _pendencias_abertas_cached()
    df_reservado = _reservado_cached()

    if df_backlog_raw.empty:
        st.info("Nenhum backlog aberto encontrado.")
    else:
        df_backlog = df_backlog_raw[df_backlog_raw["Qtd_Pendente_Remessa"] > 0].copy()
        limiar_zumbi_dias = st.number_input(
            "Considerar backlog \"recente\" até quantos dias (o resto entra como possível pedido zumbi)",
            min_value=30, max_value=3650, value=365, step=30, key="pedidos_limiar_zumbi",
        )
        df_backlog["Backlog_Antigo"] = df_backlog["Dias_Desde_Inclusao_Pedido"] > limiar_zumbi_dias

        valor_total = df_backlog["Valor_Pendente_Faturamento"].sum()
        valor_antigo = df_backlog.loc[df_backlog["Backlog_Antigo"], "Valor_Pendente_Faturamento"].sum()
        qtd_antigo = df_backlog.loc[df_backlog["Backlog_Antigo"], "Qtd_Pendente_Remessa"].sum()
        materiais_com_antigo = df_backlog.loc[df_backlog["Backlog_Antigo"], "Codigo_Produto"].nunique()

        with card("pedidos-zumbi-kpi"):
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Backlog aberto total (R$)", f"R$ {valor_total:,.0f}")
            k2.metric(
                f"Possível zumbi, > {limiar_zumbi_dias:,} dias (R$)",
                f"R$ {valor_antigo:,.0f}",
                f"{(valor_antigo / valor_total * 100 if valor_total else 0):.0f}% do total",
            )
            k3.metric("Qtd possível zumbi (unid.)", f"{qtd_antigo:,.0f}")
            k4.metric("Materiais afetados", f"{materiais_com_antigo:,}")

        bins = [-1, 90, 365, 1095, float("inf")]
        labels = ["0-90 dias", "91-365 dias", "1-3 anos", "3+ anos"]
        df_backlog["Faixa_Idade"] = pd.cut(
            df_backlog["Dias_Desde_Inclusao_Pedido"], bins=bins, labels=labels
        )
        df_faixa = (
            df_backlog.groupby("Faixa_Idade", observed=True)["Valor_Pendente_Faturamento"]
            .sum()
            .reindex(labels)
        )
        with card("pedidos-zumbi-faixa"):
            st.bar_chart(df_faixa)

        st.markdown("**Ranking de materiais por valor de backlog antigo (candidato a limpeza)**")
        df_ranking = (
            df_backlog.groupby(["Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro"])
            .apply(
                lambda g: pd.Series(
                    {
                        "Itens_Total": len(g),
                        "Qtd_Recente": g.loc[~g["Backlog_Antigo"], "Qtd_Pendente_Remessa"].sum(),
                        "Qtd_Antigo": g.loc[g["Backlog_Antigo"], "Qtd_Pendente_Remessa"].sum(),
                        "Valor_Antigo": g.loc[g["Backlog_Antigo"], "Valor_Pendente_Faturamento"].sum(),
                        "Dias_Max": g["Dias_Desde_Inclusao_Pedido"].max(),
                    }
                ),
                include_groups=False,
            )
            .reset_index()
        )
        df_ranking = df_ranking[df_ranking["Valor_Antigo"] > 0].merge(
            df_reservado, on=["Codigo_Produto", "Codigo_Centro"], how="left"
        )
        df_ranking["Qtd_Reservada"] = df_ranking["Qtd_Reservada"].fillna(0)
        df_ranking = df_ranking.sort_values("Valor_Antigo", ascending=False)

        n_ranking = st.slider(
            "Quantos materiais mostrar", min_value=5, max_value=100, value=20, step=5, key="pedidos_zumbi_top_n"
        )
        with card("pedidos-zumbi-ranking"):
            st.dataframe(
                df_ranking.head(n_ranking).style.format(
                    {
                        "Qtd_Recente": "{:,.0f}",
                        "Qtd_Antigo": "{:,.0f}",
                        "Valor_Antigo": "R$ {:,.2f}",
                        "Qtd_Reservada": "{:,.0f}",
                        "Dias_Max": "{:,.0f}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
        st.caption(
            "`Qtd_Reservada` bem menor que `Qtd_Antigo` reforça a suspeita de pedido zumbi "
            "(reserva viva no SAP não cobre nem perto do backlog antigo). Não tratar como "
            "sinal de compra/produção sem confirmar com vendas/SAP — pra investigar 1 "
            "material específico com o detalhe pedido a pedido, ver página **Visão 360**."
        )

    st.divider()

    df_estoque = _estoque_cached(tipo_cliente=tipo_cliente)
    st.subheader("Backlog por cobertura de estoque")
    st.caption(
        "`Status_Pendencia_Estoque` compara quantidade pendente com "
        "`Qtd_Estoque_Disponivel_Venda` de `fct_pendencia_sap` — esse campo pode subestimar "
        "o estoque real disponível pra um material específico (ver bug documentado na "
        "página **Material**); pra decidir se dá pra faturar um material específico, use "
        "a página Material em vez de confiar só nesse resumo agregado."
    )
    with card("pedidos-estoque"):
        col1, col2 = st.columns([1, 1])
        with col1:
            st.dataframe(df_estoque, width="stretch", hide_index=True)
        with col2:
            if not df_estoque.empty:
                st.bar_chart(df_estoque.set_index("Status_Pendencia_Estoque")["Valor_Pendente_Total"])

    st.divider()

    st.subheader("Top clientes por valor pendente")
    n = st.slider("Quantos clientes mostrar", min_value=5, max_value=50, value=20, step=5, key="pedidos_top_n")
    df_clientes_pendentes = _top_clientes_cached(n, tipo_cliente=tipo_cliente)
    with card("pedidos-top-clientes-pendentes"):
        st.dataframe(df_clientes_pendentes, width="stretch", hide_index=True)

    st.divider()

    st.subheader("Backlog por Tipo de Ordem de Venda")
    st.caption(
        "Tipo_Ordem_Venda é o código SAP (AUART) do pedido — sem tradução pra texto "
        "disponível nesta base."
    )
    df_tipo_ordem = _tipo_ordem_cached(tipo_cliente=tipo_cliente)
    with card("pedidos-tipo-ordem"):
        col3, col4 = st.columns([1, 1])
        with col3:
            st.dataframe(df_tipo_ordem, width="stretch", hide_index=True)
        with col4:
            if not df_tipo_ordem.empty:
                st.bar_chart(df_tipo_ordem.set_index("Tipo_Ordem_Venda")["Valor_Pendente_Total"].head(15))

    st.divider()

    st.subheader("Volume de pedidos entrando no funil")
    st.caption(
        "Fonte: `fct_vendas_itens_sap.Data_Inclusao_Pedido` — pedido novo entrando, não é o "
        "mesmo conceito de backlog (que não tem data de \"quando entrou\" fixa, é o que "
        "ainda está em aberto hoje)."
    )
    meses = st.slider("Janela (meses)", min_value=6, max_value=36, value=12, step=1, key="pedidos_meses")
    hoje = datetime.date.today()
    data_inicio_mensal = (hoje.replace(day=1) - pd.DateOffset(months=meses - 1)).date()
    df_mensal = _pedidos_mensal_cached(int(meses))
    if df_mensal.empty:
        st.info("Sem pedidos no período.")
    else:
        df_mensal = df_mensal.assign(
            Valor_Medio_Pedido=lambda d: d["Valor_Pedido"] / d["Qtd_Pedidos"].replace(0, pd.NA)
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Pedidos no período", f"{df_mensal['Qtd_Pedidos'].sum():,.0f}")
        c2.metric("Valor total pedido", f"R$ {df_mensal['Valor_Pedido'].sum():,.0f}")
        c3.metric("Valor médio de pedido (média mensal)", f"R$ {df_mensal['Valor_Medio_Pedido'].mean():,.0f}")
        with card("pedidos-mensal"):
            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Quantidade mensal de pedidos")
                st.bar_chart(df_mensal.set_index("Mes")["Qtd_Pedidos"])
            with col_b:
                st.subheader("Valor médio de pedido — mensal")
                st.bar_chart(df_mensal.set_index("Mes")["Valor_Medio_Pedido"])

    st.divider()

    st.subheader("Ranking de pedidos por cliente")
    n_clientes = st.slider("Quantos clientes mostrar", min_value=5, max_value=50, value=20, step=5, key="pedidos_ranking_n")
    df_ranking_clientes = _pedidos_cliente_cached(data_inicio_mensal, hoje, n_clientes, tipo_cliente)
    if df_ranking_clientes.empty:
        st.info("Nada encontrado para esse período/filtro.")
    else:
        with card("pedidos-ranking-clientes"):
            st.dataframe(
                df_ranking_clientes.style.format(
                    {
                        "Qtd_Pedidos": "{:,.0f}",
                        "Qtd_Itens_Total": "{:,.0f}",
                        "Media_Itens_Pedido": "{:,.2f}",
                        "Valor_Medio_Pedido": "R$ {:,.2f}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )

with tab_buscar:
    st.caption("SAP cru (HANA) → Gold `vendas_sap` → Salesforce (Opportunity/OpportunityLineItem), lado a lado.")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        numero_pedido_busca = st.text_input("Número do pedido", placeholder="ex.: 137490", key="pedidos_busca_numero")
    with col2:
        item_busca = st.text_input("Item (opcional)", placeholder="ex.: 10", key="pedidos_busca_item")
    with col3:
        st.write("")
        st.write("")
        buscar = st.button("Rastrear", type="primary", disabled=not numero_pedido_busca, key="pedidos_busca_botao")

    if buscar and numero_pedido_busca:
        with st.spinner(f"Rastreando pedido {numero_pedido_busca}..."):
            resultado = trace_pedido(numero_pedido_busca, item_busca or None)

        for titulo, df in resultado.items():
            with st.expander(f"{titulo} ({len(df)} linha{'s' if len(df) != 1 else ''})", expanded=not df.empty):
                if isinstance(df, pd.DataFrame) and not df.empty:
                    with card(f"pedidos-busca-{titulo}"):
                        st.dataframe(df, width="stretch", hide_index=True)
                else:
                    st.info("Nada encontrado.")
    elif not numero_pedido_busca:
        st.caption("Digite um número de pedido (com ou sem zeros à esquerda) e clique em \"Rastrear\".")
        st.caption("Pra funil/conversão Oportunidade → Pedido em nível de portfólio (não 1 pedido), veja a página **Oportunidade**.")
