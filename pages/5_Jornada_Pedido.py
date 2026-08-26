"""Página: Jornada do Pedido — Oportunidade -> Pedido -> Pendência -> Fatura.

Reusa scripts/query_vendas_sap.py::correlacao_oportunidade_pedido_pendencia_fatura, que
preenche a lacuna citada em docs/CONTEXTO_VENDAS_SAP.md §9 (não existe hoje um model Gold
que concilie Salesforce com vendas_sap). Ver docstring da função para a nota de performance
sobre por que o lado Salesforce é buscado por filtro de data, não por IN (<pedidos>).

Período e Tipo de cliente vêm do filtro global no sidebar (app.py, st.session_state)
— só o pedido/cliente/backlog aberto são filtros locais desta página.
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

st.set_page_config(page_title="Jornada do Pedido — Vendas SAP", page_icon="🔗", layout="wide")
st.title("🔗 Jornada do Pedido: Oportunidade → Pedido → Pendência → Fatura")
st.caption(
    "Uma linha por Pedido+Item: valor da Oportunidade de origem (Salesforce), valor do "
    "pedido (SAP), quanto já foi faturado e quanto ainda está pendente. Cobertura de "
    "Oportunidade não é 100% (~73% medido em 2026-08-13) — pedidos sem match aparecem com "
    "as colunas de Oportunidade em branco. `Valor_Oportunidade` é o total do negócio inteiro "
    "no Salesforce (repetido em toda linha da mesma Oportunidade); `Valor_Item_Oportunidade` "
    "é o valor só daquele item, comparável 1:1 com o valor do pedido no SAP."
)

data_inicio = st.session_state.get("flt_data_inicio", datetime.date.today() - datetime.timedelta(days=30))
data_fim = st.session_state.get("flt_data_fim", datetime.date.today())
tipo_cliente_opcao = st.session_state.get("flt_tipo_cliente", "Todos")

col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    numero_pedido = st.text_input("Pedido específico (opcional)", placeholder="ex.: 138524")
with col2:
    nome_cliente = st.text_input("Cliente contém (opcional)", placeholder="ex.: BIOHOSP")
with col3:
    apenas_pendentes = st.checkbox("Só backlog aberto", value=True, disabled=bool(numero_pedido))

st.caption(
    f"Usando filtro global: período de **{data_inicio:%d/%m/%Y}** a **{data_fim:%d/%m/%Y}**, "
    f"tipo de cliente **{tipo_cliente_opcao}** — ajuste no sidebar. Buscar por um pedido "
    "específico ignora o período."
)

linhas_tabela = st.slider(
    "Linhas exibidas na tabela",
    min_value=50,
    max_value=2000,
    value=300,
    step=50,
    help=(
        "Só controla quantas linhas aparecem na tabela de detalhe (as mais recentes primeiro). "
        "Os totais acima (valor/quantidade em pedido, pendente, faturado, % com Oportunidade) "
        "são sempre calculados em cima do período inteiro, não só das linhas mostradas na tabela."
    ),
)

SAFETY_LIMIT = 20000
FAIXAS_AGING = ["0-7 dias", "8-15 dias", "16-30 dias", "31-60 dias", "60+ dias"]
BINS_AGING = [-1, 7, 15, 30, 60, 10**6]


@st.cache_data(ttl=300, show_spinner="Montando a jornada Oportunidade → Pedido → Pendência → Fatura (pode levar ~10-20s)...")
def _jornada_cached(
    data_inicio: datetime.date,
    data_fim: datetime.date,
    apenas_pendentes: bool,
    numero_pedido: Optional[str],
    nome_cliente: Optional[str],
    tipo_cliente: Optional[str],
) -> pd.DataFrame:
    return correlacao_oportunidade_pedido_pendencia_fatura(
        data_inicio=data_inicio,
        data_fim=data_fim,
        apenas_pendentes=apenas_pendentes,
        numero_pedido=numero_pedido,
        nome_cliente=nome_cliente,
        tipo_cliente=tipo_cliente,
        limit=SAFETY_LIMIT,
    )


df = _jornada_cached(
    data_inicio,
    data_fim,
    apenas_pendentes,
    numero_pedido or None,
    nome_cliente or None,
    None if tipo_cliente_opcao == "Todos" else tipo_cliente_opcao,
)

if df.empty:
    st.info("Nada encontrado para esse filtro.")
else:
    if len(df) >= SAFETY_LIMIT:
        st.warning(
            f"Atingiu o teto de segurança de {SAFETY_LIMIT:,} linhas — pra esse período/filtro "
            "existem mais itens do que isso, então os totais abaixo estão incompletos (não são "
            "o real do período). Reduza o período no filtro global ou marque \"Só backlog "
            "aberto\" pra trazer tudo."
        )
    tem_oportunidade = df["Nome_Oportunidade"].notna()

    st.markdown("**Valores (R$)**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Em Pedido", f"R$ {df['Valor_Liquido_Pedido'].sum():,.2f}")
    c2.metric("Pendente", f"R$ {df['Valor_Pendente_Faturamento'].sum():,.2f}")
    c3.metric("Faturado", f"R$ {df['Valor_Liquido_Faturado'].sum():,.2f}")
    c4.metric("Com Oportunidade", f"{tem_oportunidade.mean():.0%}")

    st.markdown("**Quantidades**")
    q1, q2, q3 = st.columns(3)
    q1.metric("Qtd Pedida", f"{df['Qtd_Pedida'].sum():,.0f}")
    q2.metric("Qtd Pendente", f"{df['Qtd_Pendente_Operacional'].sum():,.0f}")
    q3.metric("Qtd Faturada", f"{df['Qtd_Faturada'].sum():,.0f}")

    diferenca = df["Valor_Liquido_Pedido"].sum() - df["Valor_Liquido_Faturado"].sum() - df["Valor_Pendente_Faturamento"].sum()
    with st.expander(f"Pedido ≠ Faturado + Pendente? Diferença: R$ {diferenca:,.2f}"):
        st.caption(
            "`Valor_Pendente_Faturamento` é calculado como `MAX(Valor_Pedido - Valor_Faturado, 0)` "
            "(mesma regra documentada para as quantidades em `docs/CONTEXTO_VENDAS_SAP.md` §4). "
            "Isso significa que, item a item, Faturado + Pendente só bate com Pedido quando o "
            "faturado não passa do valor pedido. Quando um item é 'sobre-faturado' em relação ao "
            "valor de pedido atual (ex.: pedido revisado para baixo depois da fatura emitida, "
            "devolução/estorno que reduziu o valor do pedido sem reduzir o faturado, fatura "
            "complementar), o pendente daquele item vira zero mas o faturado continua contando o "
            "valor cheio — somando muitos itens assim, Faturado + Pendente passa a soma do Pedido. "
            "Não é um erro da página, é uma característica do dado — veja a página **Auditoria do "
            "Fluxo** (checagem `valor_sem_quantidade`) para investigar quais itens específicos "
            "estão causando essa diferença."
        )

    st.divider()

    tab_geral, tab_governo, tab_funil, tab_divergencia, tab_aging, tab_credito = st.tabs(
        [
            "Status", "Governo x Privado", "Funil Oportunidade→Fatura",
            "Divergência Oportunidade x Pedido", "Aging (Governo x Privado)", "Crédito bloqueado",
        ]
    )

    with tab_geral:
        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.subheader("Por status de pendência")
            st.bar_chart(df.groupby("Status_Pendencia")["Valor_Liquido_Pedido"].sum())
        with col_b:
            st.subheader("Por status de faturamento")
            st.bar_chart(df.groupby("Status_Faturamento")["Valor_Liquido_Pedido"].sum())

    with tab_governo:
        resumo_valor = df.groupby("Tipo_Cliente")[
            ["Valor_Liquido_Pedido", "Valor_Pendente_Faturamento", "Valor_Liquido_Faturado"]
        ].sum()
        resumo_qtd = df.groupby("Tipo_Cliente")[
            ["Qtd_Pedida", "Qtd_Pendente_Operacional", "Qtd_Faturada"]
        ].sum()
        col_c, col_d = st.columns([1, 1])
        with col_c:
            st.markdown("**Valores (R$)**")
            st.bar_chart(resumo_valor)
            st.dataframe(resumo_valor.style.format("R$ {:,.2f}"), width="stretch")
        with col_d:
            st.markdown("**Quantidades**")
            st.bar_chart(resumo_qtd)
            st.dataframe(resumo_qtd.style.format("{:,.0f}"), width="stretch")

    with tab_funil:
        st.caption(
            "Contagem de linhas (Pedido+Item) em cada etapa do funil, dentro do filtro atual — "
            "não é necessariamente sequencial por linha individual (uma Oportunidade pode ter "
            "sido criada fora da janela de busca, ver nota de performance na função)."
        )
        com_faturamento = df["Qtd_Faturada"] > 0
        totalmente_faturado = df["Status_Faturamento"] == "Totalmente Faturado"
        funil = pd.DataFrame(
            {
                "Etapa": ["Com Oportunidade", "Pedido (todos)", "Com faturamento", "Totalmente faturado"],
                "Qtd_Linhas": [
                    int(tem_oportunidade.sum()),
                    len(df),
                    int(com_faturamento.sum()),
                    int(totalmente_faturado.sum()),
                ],
            }
        ).set_index("Etapa")
        col_e, col_f = st.columns([1, 1])
        with col_e:
            st.bar_chart(funil)
        with col_f:
            st.dataframe(funil, width="stretch")

        dt_criacao_opp = pd.to_datetime(df["Data_Criacao_Oportunidade"], utc=True, errors="coerce").dt.tz_localize(None)
        dt_pedido = pd.to_datetime(df["Data_Inclusao_Pedido"], errors="coerce")
        dt_fatura = pd.to_datetime(df["Primeira_Data_Faturamento"], errors="coerce")

        dias_opp_pedido = (dt_pedido - dt_criacao_opp).dt.days
        dias_opp_pedido = dias_opp_pedido[dias_opp_pedido >= 0]
        dias_pedido_fatura = (dt_fatura - dt_pedido).dt.days
        dias_pedido_fatura = dias_pedido_fatura[dias_pedido_fatura >= 0]

        col_g, col_h = st.columns([1, 1])
        col_g.metric(
            "Mediana dias: Oportunidade → Pedido",
            f"{dias_opp_pedido.median():.0f}" if not dias_opp_pedido.empty else "sem dado",
        )
        col_h.metric(
            "Mediana dias: Pedido → 1ª Fatura",
            f"{dias_pedido_fatura.median():.0f}" if not dias_pedido_fatura.empty else "sem dado",
        )

    with tab_divergencia:
        st.caption(
            "Compara `Valor_Item_Oportunidade` (Salesforce `OpportunityLineItem.TotalPrice`, "
            "valor do item negociado) com `Valor_Liquido_Pedido` (SAP, valor que de fato virou "
            "pedido) pro mesmo Pedido+Item — grão item a item, não confundir com "
            "`Valor_Oportunidade`, que é o total do negócio inteiro (repetido em toda linha "
            "daquela Oportunidade) e por isso não é comparável 1:1 aqui."
        )
        mask_div = tem_oportunidade & df["Valor_Item_Oportunidade"].notna() & (df["Valor_Liquido_Pedido"] != 0)
        df_div = df.loc[mask_div].copy()
        if df_div.empty:
            st.info("Nenhuma linha com Oportunidade e Pedido pra comparar nesse filtro.")
        else:
            df_div["Diferenca"] = df_div["Valor_Item_Oportunidade"] - df_div["Valor_Liquido_Pedido"]
            df_div["Diferenca_Pct"] = df_div["Diferenca"] / df_div["Valor_Liquido_Pedido"] * 100
            limite_pct = st.slider("Mostrar divergências acima de (%)", min_value=0, max_value=100, value=5, step=5)
            df_div_filtrado = df_div[df_div["Diferenca_Pct"].abs() >= limite_pct].sort_values(
                "Diferenca_Pct", key=lambda s: s.abs(), ascending=False
            )
            st.metric(
                f"Linhas com divergência ≥ {limite_pct}%",
                f"{len(df_div_filtrado)} de {len(df_div)}",
            )
            colunas_div = [
                "Numero_Pedido", "Item_Pedido", "Nome_Cliente", "Nome_Oportunidade",
                "Valor_Item_Oportunidade", "Valor_Liquido_Pedido", "Diferenca", "Diferenca_Pct",
            ]
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

    df_pend = df[df["Flag_Pendencia"] == 1]

    with tab_aging:
        st.caption(
            "Só considera linhas com `Flag_Pendencia = 1` (backlog aberto), mesmo se \"Só backlog "
            "aberto\" estiver desmarcado acima — aging de item já concluído não faz sentido."
        )
        if df_pend.empty:
            st.info("Nenhum item em backlog aberto nesse filtro.")
        else:
            df_pend_aging = df_pend.copy()
            df_pend_aging["Faixa_Aging"] = pd.cut(
                df_pend_aging["Dias_Desde_Inclusao_Pedido"], bins=BINS_AGING, labels=FAIXAS_AGING
            )
            pivot_aging = (
                df_pend_aging.groupby(["Faixa_Aging", "Tipo_Cliente"], observed=True)["Valor_Pendente_Faturamento"]
                .sum()
                .unstack(fill_value=0)
                .reindex(FAIXAS_AGING)
            )
            col_i, col_j = st.columns([1, 1])
            with col_i:
                st.bar_chart(pivot_aging)
            with col_j:
                st.dataframe(pivot_aging.style.format("R$ {:,.2f}"), width="stretch")

    with tab_credito:
        st.caption(
            "Cruza o backlog pendente (`Flag_Pendencia = 1`) com o bloqueio de crédito **atual** "
            "do cliente (`fct_limite_credito_sap` é uma foto de hoje, não histórico) — mostra "
            "quanto do backlog pertence a cliente hoje bloqueado, então plausivelmente travado por "
            "crédito, não só por estoque/logística."
        )
        if df_pend.empty:
            st.info("Nenhum item em backlog aberto nesse filtro.")
        else:
            resumo_credito = df_pend.groupby("Cliente_Bloqueado")["Valor_Pendente_Faturamento"].agg(
                Valor_Pendente="sum", Qtd_Itens="count"
            )
            resumo_credito.index = resumo_credito.index.map({True: "Bloqueado", False: "Não bloqueado"})
            pct_bloqueado = df_pend.loc[df_pend["Cliente_Bloqueado"], "Valor_Pendente_Faturamento"].sum() / df_pend[
                "Valor_Pendente_Faturamento"
            ].sum()
            st.metric("% do backlog pendente em cliente bloqueado", f"{pct_bloqueado:.1%}")
            col_k, col_l = st.columns([1, 1])
            with col_k:
                st.bar_chart(resumo_credito["Valor_Pendente"])
            with col_l:
                st.dataframe(
                    resumo_credito.style.format({"Valor_Pendente": "R$ {:,.2f}", "Qtd_Itens": "{:,.0f}"}),
                    width="stretch",
                )

    st.divider()

    df_tabela = df.head(linhas_tabela)
    total_texto = f"de {len(df)} " if len(df) > len(df_tabela) else ""
    st.subheader(f"Detalhe ({len(df_tabela)} {total_texto}linha{'s' if len(df) != 1 else ''} — mais recentes primeiro)")
    colunas_exibir = [
        "Numero_Pedido", "Item_Pedido", "Data_Inclusao_Pedido", "Nome_Cliente",
        "Tipo_Cliente", "Descricao_Canal_Distribuicao", "Cliente_Bloqueado",
        "Descricao_Produto", "Nome_Oportunidade", "Estagio_Oportunidade",
        "Valor_Oportunidade", "Valor_Item_Oportunidade",
        "Qtd_Pedida", "Qtd_Faturada", "Qtd_Pendente_Operacional",
        "Valor_Liquido_Pedido", "Valor_Liquido_Faturado", "Valor_Pendente_Faturamento",
        "Status_Faturamento", "Status_Pendencia",
    ]
    st.dataframe(df_tabela[colunas_exibir], width="stretch", hide_index=True)
