"""Página: Home — visão executiva (KPIs gerais, evolução do ano, pontos de atenção).

Tela principal do app (`default=True` em `app.py`, primeira da navegação). Resume 2 seções
lado a lado, sem misturar: **Backlog e Operação** (`vendas_sap`, total bruto — mesmas
funções de `scripts/query_vendas_sap.py` usadas em Pendências/Estoque/Crédito/Análise
Histórica) e **Faturamento Comercial** (também `vendas_sap`, mas via
`scripts/query_faturamento_comercial.py` — hierarquia comercial pelo crosswalk cliente→setor,
~52% de cobertura, ver `docs/CONTEXTO_VENDAS_SAP.md` §10). Mesma tabela fonte, escopo/consulta
diferente — por isso os totais não somam entre as duas seções. Pensada como um resumo "pra
diretoria": números agregados rápidos de cada área, sem entrar no detalhe operacional — quem
quiser o detalhe navega pra página específica.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_faturamento_comercial import (  # noqa: E402
    faturamento_por_dimensao,
    faturamento_serie,
    meta_vs_realizado_por_dimensao,
)
from scripts.query_vendas_sap import (  # noqa: E402
    aging_pendencias,
    credito_disponivel_clientes,
    devolucoes_credito_motivo,
    estoque_totais,
    estoque_validade_resumo,
    faturamento_mensal,
    pendencia_status_estoque,
)
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Visão Executiva — Vendas", page_icon="🔎", layout="wide")

st.title(":material/dashboard: Visão Executiva")
st.caption(
    "Resumo geral pra decisão rápida — números ao vivo de `GOLD.vendas_sap` nas 2 seções "
    "abaixo, mas com escopo diferente: **Backlog e Operação** soma tudo sem recorte "
    "comercial; **Faturamento Comercial** passa pelo crosswalk cliente→setor (~52% de "
    "cobertura) pra poder quebrar por Divisional/Regional/Distrital/Setor/Canal — ver "
    "`docs/CONTEXTO_VENDAS_SAP.md` §10. **As duas seções não são comparáveis 1:1** — cada "
    "uma soma o que já é comparável dentro dela mesma. Pra investigar o detalhe de qualquer "
    "número aqui, use as páginas do menu."
)
st.caption(
    '"Valor em Estoque" (aqui e no ponto de atenção de vencido abaixo) já filtra só centro '
    "em R$ (Brasil) — Uruguai/Colômbia usam moeda local e ficam de fora dessa soma. Ver "
    "**Estoque** para detalhe."
)


@st.cache_data(ttl=300, show_spinner="Consultando visão executiva...")
def _dados_executivos() -> dict[str, pd.DataFrame]:
    return {
        "aging": aging_pendencias(),
        "estoque_status": pendencia_status_estoque(),
        "estoque_totais": estoque_totais(),
        "estoque_validade": estoque_validade_resumo(),
        "credito_bloqueado": credito_disponivel_clientes(apenas_bloqueados=True),
        "devolucoes": devolucoes_credito_motivo(
            data_inicio=datetime.date.today() - datetime.timedelta(days=30),
            data_fim=datetime.date.today(),
            limit=5000,
        ),
        "faturamento_mensal": faturamento_mensal(meses=12),
    }


dados = _dados_executivos()
df_aging = dados["aging"]
df_estoque_status = dados["estoque_status"]
df_estoque_totais = dados["estoque_totais"]
df_estoque_validade = dados["estoque_validade"]
df_credito_bloqueado = dados["credito_bloqueado"]
df_devolucoes = dados["devolucoes"]
df_fat_mensal = dados["faturamento_mensal"]

valor_pendente_total = df_aging["Valor_Pendente_Total"].sum() if not df_aging.empty else 0.0
valor_pendente_60mais = (
    df_aging.loc[df_aging["Faixa_Aging"] == "60+ dias", "Valor_Pendente_Total"].sum()
    if not df_aging.empty
    else 0.0
)
pct_60mais = (valor_pendente_60mais / valor_pendente_total) if valor_pendente_total else 0.0

valor_sem_estoque = (
    df_estoque_status.loc[
        df_estoque_status["Status_Pendencia_Estoque"] == "Pendente sem Estoque",
        "Valor_Pendente_Total",
    ].sum()
    if not df_estoque_status.empty
    else 0.0
)
pct_sem_estoque = (valor_sem_estoque / valor_pendente_total) if valor_pendente_total else 0.0

valor_faturado_mes_atual = (
    df_fat_mensal["Valor_Faturado"].iloc[-1] if not df_fat_mensal.empty else 0.0
)

valor_estoque_total = (
    df_estoque_totais["Valor_Financeiro_Estoque"].iloc[0] if not df_estoque_totais.empty else 0.0
)
valor_estoque_restrito = (
    df_estoque_totais["Qtd_Restrito"].iloc[0] if not df_estoque_totais.empty else 0.0
)
valor_estoque_disponivel = (
    df_estoque_totais["Qtd_Disponivel_Venda"].iloc[0] if not df_estoque_totais.empty else 0.0
)
qtd_fisico_total = (
    df_estoque_totais["Qtd_Fisico_Total"].iloc[0] if not df_estoque_totais.empty else 0.0
)
pct_estoque_restrito = (valor_estoque_restrito / qtd_fisico_total) if qtd_fisico_total else 0.0

qtd_clientes_bloqueados = len(df_credito_bloqueado)
valor_exposicao_bloqueada = (
    df_credito_bloqueado["Valor_Exposicao_Total_SAP"].sum()
    if not df_credito_bloqueado.empty
    else 0.0
)

valor_devolucoes_30d = df_devolucoes["Montante"].sum() if not df_devolucoes.empty else 0.0

valor_estoque_vencido = (
    df_estoque_validade.loc[
        df_estoque_validade["Faixa_Validade"] == "Vencido", "Valor_Financeiro_Estoque"
    ].sum()
    if not df_estoque_validade.empty
    else 0.0
)
pct_estoque_vencido = (valor_estoque_vencido / valor_estoque_total) if valor_estoque_total else 0.0

st.subheader("Backlog e Operação (`vendas_sap`)")
st.caption("Situação do backlog aberto hoje.")
with st.container(border=True):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Valor Pendente (Backlog)", f"R$ {valor_pendente_total:,.0f}")
    c2.metric("Faturado (mês corrente)", f"R$ {valor_faturado_mes_atual:,.0f}")
    c3.metric("Valor em Estoque", f"R$ {valor_estoque_total:,.0f}")
    c4.metric(
        "Backlog com 60+ dias",
        f"{pct_60mais:.0%}",
        help="Fatia do valor pendente total que está aberta há mais de 60 dias.",
    )

st.divider()

st.subheader("Evolução do faturamento (últimos 12 meses)")
st.caption(
    "`vendas_sap.fct_faturamento_itens_sap`, total bruto (todas Org Vendas, incl. filial "
    "estrangeira/documento intercompany) — mesmo total da seção Faturamento Comercial "
    "abaixo, só sem quebra por dimensão."
)
if df_fat_mensal.empty:
    st.info("Sem dado de faturamento no período.")
else:
    with card("home-faturamento-mensal"):
        st.bar_chart(df_fat_mensal.set_index("Mes")["Valor_Faturado"])

st.divider()

st.subheader("Faturamento Comercial (quebra por Canal/Divisional/Regional/Setor)")
st.caption(
    "Mesmo total de `vendas_sap.fct_faturamento_itens_sap` acima, quebrado por dimensão "
    "comercial via o crosswalk cliente→setor (`vendas.dim_cliente_setor` → "
    "`vendas.dim_estrutura`, ~52% de cobertura — ver `docs/CONTEXTO_VENDAS_SAP.md` §10)."
)

hoje = datetime.date.today()
_inicio_mes = hoje.replace(day=1)
_inicio_ano = hoje.replace(month=1, day=1)


@st.cache_data(ttl=300, show_spinner="Consultando faturamento comercial...")
def _dados_comerciais() -> dict[str, pd.DataFrame]:
    return {
        "serie_mes": faturamento_serie(_inicio_ano, hoje, granularidade="mes"),
        "meta_canal": meta_vs_realizado_por_dimensao(_inicio_ano, hoje, "Canal"),
        "canal_ytd": faturamento_por_dimensao(_inicio_ano, hoje, "Canal"),
    }


dados_com = _dados_comerciais()
df_com_serie_mes = dados_com["serie_mes"]
df_com_meta_canal = dados_com["meta_canal"]
df_com_canal_ytd = dados_com["canal_ytd"]

faturado_mtd_com = (
    df_com_serie_mes.loc[df_com_serie_mes["Mes"] == hoje.strftime("%Y-%m"), "Valor_Faturado"].sum()
    if not df_com_serie_mes.empty
    else 0.0
)
faturado_ytd_com = df_com_serie_mes["Valor_Faturado"].sum() if not df_com_serie_mes.empty else 0.0
meta_mtd_com = (
    df_com_meta_canal.loc[df_com_meta_canal["Mes"] == hoje.strftime("%Y-%m"), "Meta_Valor"].sum()
    if not df_com_meta_canal.empty
    else 0.0
)
meta_ytd_com = df_com_meta_canal["Meta_Valor"].sum() if not df_com_meta_canal.empty else 0.0
cob_meta_mtd_com = (faturado_mtd_com / meta_mtd_com) if meta_mtd_com else None
cob_meta_ytd_com = (faturado_ytd_com / meta_ytd_com) if meta_ytd_com else None

with st.container(border=True):
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Faturado MTD (comercial)", f"R$ {faturado_mtd_com:,.0f}")
    c6.metric("Faturado YTD (comercial)", f"R$ {faturado_ytd_com:,.0f}")
    c7.metric(
        "Cob. Meta MTD",
        f"{cob_meta_mtd_com:.1%}" if cob_meta_mtd_com is not None else "—",
        help=(
            "Pode passar de 100% com folga: o numerador é o faturamento total (todas Org "
            "Vendas, incl. filial estrangeira/intercompany), a meta só cobre a fatia com "
            "crosswalk cliente→setor mapeado (~52%) — não é atingimento real de meta comercial."
        ),
    )
    c8.metric(
        "Cob. Meta YTD",
        f"{cob_meta_ytd_com:.1%}" if cob_meta_ytd_com is not None else "—",
        help=(
            "Mesma ressalva do Cob. Meta MTD. Canal 'MS' também não tem meta própria — ver "
            "**Painel Vendas** pro detalhe."
        ),
    )

if not df_com_canal_ytd.empty:
    with card("home-canal-ytd"):
        col_com_a, col_com_b = st.columns([1, 2])
        with col_com_a:
            st.caption("Faturado YTD por Canal")
            st.dataframe(
                df_com_canal_ytd[["Dimensao", "Valor_Faturado"]]
                .rename(columns={"Dimensao": "Canal", "Valor_Faturado": "Faturado YTD"})
                .style.format({"Faturado YTD": "R$ {:,.0f}"}),
                width="stretch",
                hide_index=True,
            )
        with col_com_b:
            st.bar_chart(df_com_canal_ytd.set_index("Dimensao")["Valor_Faturado"])

st.divider()

st.subheader("Pontos de atenção")
st.caption("Calculados ao vivo a partir do backlog, estoque, crédito e devoluções de hoje.")

if valor_pendente_total > 0:
    if pct_60mais >= 0.5:
        st.warning(
            f"**{pct_60mais:.0%} do valor pendente (R$ {valor_pendente_60mais:,.0f} de "
            f"R$ {valor_pendente_total:,.0f}) está em backlog há mais de 60 dias.** "
            "Aging concentrado assim geralmente indica pedidos travados, não giro normal — "
            "ver **Pendências** para detalhar por cliente/produto."
        )
    else:
        st.info(f"{pct_60mais:.0%} do valor pendente está em backlog há mais de 60 dias.")

    if pct_sem_estoque >= 0.3:
        st.warning(
            f"**{pct_sem_estoque:.0%} do valor pendente (R$ {valor_sem_estoque:,.0f}) está "
            "sem cobertura de estoque** — não é gargalo logístico/faturamento, é falta de "
            "produto pra atender. Ver **Estoque** para saber quais materiais faltam."
        )
    else:
        st.info(f"{pct_sem_estoque:.0%} do valor pendente está sem cobertura de estoque.")

if qtd_clientes_bloqueados > 0:
    st.info(
        f"**{qtd_clientes_bloqueados:,} clientes bloqueados por crédito**, R$ "
        f"{valor_exposicao_bloqueada:,.0f} de exposição total — ver **Crédito e Devoluções** "
        "para saber quanto disso está represando pedidos no backlog."
    )

if valor_devolucoes_30d > 0:
    st.info(
        f"**R$ {valor_devolucoes_30d:,.0f} em devoluções/abatimentos de negócio** nos "
        "últimos 30 dias (excluindo transferência de faturamento de rotina) — ver "
        "**Crédito e Devoluções** para os motivos mais frequentes."
    )

if qtd_fisico_total > 0:
    st.info(
        f'**{pct_estoque_restrito:.0%}** da quantidade física em estoque está "restrita" '
        "— soma de 2 coisas que existem fisicamente mas não podem ser vendidas agora: "
        "**em qualidade** (lote recebido, aguardando laudo/liberação do controle de "
        "qualidade — etapa normal do processo, não é problema) + **bloqueado** (travado "
        "manualmente por algum motivo específico, ex.: suspeita de desvio, quarentena). "
        'Ver aba "Restrito x Disponível" em **Estoque** pra ver os dois separados.'
    )

if valor_estoque_vencido > 0:
    if pct_estoque_vencido >= 0.1:
        st.warning(
            f"**{pct_estoque_vencido:.0%} do valor em estoque (R$ {valor_estoque_vencido:,.0f}) já está "
            "vencido** — ver aba Validade dos lotes em **Estoque** para saber quais materiais/lotes."
        )
    else:
        st.info(
            f"R$ {valor_estoque_vencido:,.0f} em estoque vencido ({pct_estoque_vencido:.0%} do valor total)."
        )

if cob_meta_ytd_com is not None:
    if cob_meta_ytd_com < 0.8:
        st.warning(
            f"**Cob. Meta YTD comercial em {cob_meta_ytd_com:.0%}** (R$ {faturado_ytd_com:,.0f} "
            f"de R$ {meta_ytd_com:,.0f}) — ver **Painel Vendas** pra detalhar por "
            "Divisional/Regional/Distrital/Setor onde está o maior desvio."
        )
    else:
        st.info(
            f"Cob. Meta YTD comercial em {cob_meta_ytd_com:.0%} — normal passar de 100% "
            "com folga aqui (ver ajuda no metric acima, o numerador não tem o mesmo "
            "recorte da meta)."
        )

st.divider()

st.markdown(
    """
    ### Páginas

    **Dashboards** (fonte `vendas_sap` — backlog/operação; ao vivo, sem botão):

    - **Pendências** — aging do backlog, cobertura de estoque, top clientes, backlog por
      tipo de ordem de venda.
    - **Jornada do Pedido** — Oportunidade (Salesforce) → Pedido → Pendência → Fatura numa
      linha só, com filtro Governo x Privado, funil de conversão, divergência de valor
      e impacto de crédito bloqueado no backlog.
    - **Estoque** — restrito (qualidade/bloqueado) x disponível pra venda, produto acabado
      x não acabado, e validade dos lotes (vencido / a vencer), por material/centro.
    - **Crédito e Devoluções** — limite/exposição de crédito por cliente + devoluções e
      abatimentos com motivo em texto livre.
    - **Faturamento por Org Vendas** — faturamento cruzando Organização de Vendas (SAP) x
      Linha de Negócio (Estética/Farma/Onco-Hemato/Não Alocado).
    - **Análise Histórica** — faturamento, pedidos entrando no funil e devoluções por mês
      (as únicas 3 séries com histórico real nessa fonte).
    - **Meta x Realizado (SAP)** — meta por mês x BU, atribuída via crosswalk cliente→setor.
    - **Relatório de Pedidos** — quantidade/valor médio de pedido por mês + ranking por
      cliente.
    - **Visão do Vendedor** — ranking de faturamento por vendedor (Salesforce, ~82% de
      cobertura) + drill-down individual (tendência mensal, top clientes).

    **Faturamento (Painel Vendas)** (inspirada no Painel Vendas de referência, mas sobre
    `vendas_sap` + crosswalk cliente→setor — ver `docs/CONTEXTO_VENDAS_SAP.md` §10):

    - **Painel Vendas** — 3 abas: MTD/YTD/Trimestral (gauges + Meta x Realizado por
      Canal/Divisional/Regional/Distrital/Setor/Família), Diário (drill do dia/MTD + Estado
      UF) e Anual/YoY (comparativo ano corrente x ano anterior + top clientes).
    - **Produto / Cliente** — preço médio, SKUs vendidos, ranking mensal por cliente/família/
      produto.
    - **Relatório Analítico** — detalhe linha a linha com seletor de colunas.

    Todas as 3 acima têm um filtro de recorte próprio (Canal, Divisional, Cliente, Produto
    etc.) além do "Quebrar por" de cada gráfico/tabela.

    **Técnico** (ferramentas de investigação pontual, com botão/input):

    - **Auditoria do Fluxo** — 4 checagens genéricas de anomalia.
    - **Rastrear Pedido** — SAP cru (HANA) → Gold → Salesforce, lado a lado, pra um
      pedido específico.
    - **DDIC Lookup** — o que é uma tabela/campo SAP, direto do dicionário de dados.
    - **Conectividade** — testa a conexão com SQL Server e SAP HANA.

    Use o menu à esquerda pra navegar.
    """
)
