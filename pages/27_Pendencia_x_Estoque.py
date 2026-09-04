"""Página: Pendência x Estoque — visão global, todos os materiais/pedidos de uma vez.

Classifica cada item de backlog aberto num `Motivo_Principal` real (Falso Positivo /
Sem Estoque / Estoque Parcial / Financeiro-Crédito / Fiscal-Faturamento /
Logístico-Remessa), quebra por Organização de Vendas, e permite clicar em 1 item no
detalhe pra ver o contexto completo — incluindo o estoque REAL na data do pedido (via
`IB_SAPECC.MCHBH`, fechamento de período do próprio SAP, não estimativa), pra responder
"na hora que foi aprovado, tinha estoque ou não?" sem sair da tela.

Achado GRAVE de auditoria (2026-09-03, reportado pelo usuário com pedidos reais
0000134668/0060008372/0060009216/0060011929 — todos devolução ZREB/ZROB): 52% dos itens
"pendentes" (84,5% da quantidade) já estão `Flag_Totalmente_Faturado=1` — não são
backlog real, é `Qtd_Remetida` que nunca é populada nesses tipos de pedido. Ver
docstring de `pendencia_x_estoque_global` no código-fonte pro mecanismo completo;
classificados aqui como "Falso Positivo (já faturado)", primeiro na ordem de
prioridade — nenhum outro motivo é avaliado pra esses itens.

Achado de auditoria (2026-09-03): bloqueio comercial explícito do SAP (VBAK.LIFSK/FAKSK)
tem só 82 pedidos preenchidos em TODO o histórico da base — não vira categoria aqui por
falta de volume; ver docstring de `pendencia_x_estoque_global` no código-fonte.

Reusa scripts/query_vendas_sap.py::pendencia_x_estoque_global +
scripts/trace_lote.py::estoque_historico_material_centro. Pra investigar 1 material ou
1 cliente específico com todo o contexto (Oportunidade, Remessa), usar Visão
360/Material/Cliente 360 — esta página é o panorama + drill-down de 1 item por vez.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    correlacao_oportunidade_pedido_pendencia_fatura,
    credito_disponivel_clientes,
    estoque_restrito_disponivel,
    movimento_estoque_resumo_material_centro,
    organizacoes_vendas_texto,
    pendencia_x_estoque_global,
    remessas,
)
from scripts.trace_lote import estoque_historico_material_centro  # noqa: E402
from scripts.ui_theme import card  # noqa: E402

st.set_page_config(page_title="Pendência x Estoque — Vendas SAP", page_icon="🧩", layout="wide")
st.title(":material/fact_check: Pendência x Estoque: visão global")
st.caption(
    "Todo o backlog aberto (`Flag_Pendencia = 1`), classificado por `Motivo_Principal` "
    "— cruza `Status_Pendencia_Estoque` (item vs. estoque do Material+Centro), crédito "
    "do cliente (`fct_limite_credito_sap`, pior caso entre áreas) e `Status_Pendencia` "
    "(documento de remessa/fatura pendente mesmo com estoque). Clique numa linha do "
    "detalhe pra abrir o contexto completo do item."
)


@st.cache_data(ttl=300, show_spinner="Consultando pendência x estoque (todos os materiais)...")
def _dados_cached() -> pd.DataFrame:
    return pendencia_x_estoque_global()


@st.cache_data(ttl=1800, show_spinner="Consultando nomes de Organização de Vendas...")
def _org_vendas_cached() -> pd.DataFrame:
    return organizacoes_vendas_texto()


@st.cache_data(ttl=300, show_spinner="Consultando estoque restrito x disponível (todos os materiais)...")
def _estoque_cached() -> pd.DataFrame:
    # limit alto o bastante pra trazer todo Material+Centro com estoque físico > 0, não só
    # os top N por valor financeiro (default da função é 500, pensado pra tela de Estoque).
    return estoque_restrito_disponivel(limit=100_000)


@st.cache_data(ttl=1800, show_spinner="Consultando histórico de movimento (MSEG/MKPF, pode demorar)...")
def _movimento_cached() -> pd.DataFrame:
    return movimento_estoque_resumo_material_centro(meses=24)


@st.cache_data(ttl=3600, show_spinner="Consultando estoque real na data do pedido (IB_SAPECC.MCHBH, ~5-10s)...")
def _estoque_historico_cached(codigo_material: str, codigo_centro: str, data_corte: str) -> dict:
    return estoque_historico_material_centro(codigo_material, codigo_centro, data_corte)


@st.cache_data(ttl=300, show_spinner="Consultando crédito do cliente...")
def _credito_cliente_cached(codigo_cliente: str) -> pd.DataFrame:
    return credito_disponivel_clientes(codigo_cliente=codigo_cliente)


@st.cache_data(ttl=300, show_spinner="Consultando Oportunidade (Salesforce)...")
def _oportunidade_pedido_cached(numero_pedido: str) -> pd.DataFrame:
    # numero_pedido busca exata, ignora período — funciona pra pedido de qualquer idade
    # (ver docstring de correlacao_oportunidade_pedido_pendencia_fatura).
    return correlacao_oportunidade_pedido_pendencia_fatura(numero_pedido=numero_pedido, apenas_pendentes=False)


@st.cache_data(ttl=300, show_spinner="Consultando remessas (entregas)...")
def _remessas_pedido_cached(numero_pedido: str) -> pd.DataFrame:
    return remessas(numero_pedido=numero_pedido)


def _classificar_motivo_principal(row: pd.Series) -> str:
    """Motivo real da pendência, nessa ordem de prioridade (ver docstring do módulo):

    0. Falso Positivo (já faturado): `Flag_Totalmente_Faturado=1` mas `Flag_Pendencia=1`
       mesmo assim — achado GRAVE de auditoria (2026-09-03, ver docstring de
       `pendencia_x_estoque_global` no código-fonte): 23.981 dos 46.132 itens pendentes
       (52%, 84,5% de toda a quantidade pendente) são pedidos JÁ 100% faturados —
       principalmente devolução (`ZREB`/`ZROB`/`ZRSG`/`ZRES`/...) cujo `Qtd_Remetida`
       nunca é populado, então `Status_Pendencia_Estoque` fica preso em "sem estoque"
       pra sempre. Checado ANTES de tudo — nenhum desses itens é backlog real.
    1. Financeiro: cliente bloqueado OU sem limite de crédito (pior caso entre áreas) —
       trava o pedido mesmo que o produto exista em estoque.
    2. Sem Estoque / Estoque Parcial: `Status_Pendencia_Estoque` do próprio item.
    3. Fiscal/Logístico: tem estoque, mas travado num documento (remessa ainda não
       criada, ou fatura ainda não emitida sobre remessa já feita).
    """
    if row.get("Flag_Totalmente_Faturado") == 1:
        return "Falso Positivo (já faturado)"
    if row.get("Cliente_Bloqueado") == 1 or (pd.notna(row.get("Valor_Credito_Disponivel")) and row["Valor_Credito_Disponivel"] < 0):
        return "Financeiro (crédito)"
    status_estoque = row.get("Status_Pendencia_Estoque")
    if status_estoque == "Pendente sem Estoque":
        return "Sem Estoque"
    if status_estoque == "Pendente com Estoque Parcial":
        return "Estoque Parcial"
    status_pendencia = row.get("Status_Pendencia")
    if status_pendencia == "Pendente Fiscal (Faturamento)":
        return "Fiscal (Faturamento)"
    if status_pendencia == "Pendente Logistico (Remessa)":
        return "Logístico (Remessa)"
    if status_pendencia == "Pendente Logistico e Fiscal":
        return "Logístico e Fiscal"
    return "Outro"


def _classificar_causa_estoque_material(row: pd.Series) -> str:
    """Motivo provável de falta de estoque no Material+Centro (grão diferente do item —
    cruza snapshot atual de `fct_estoque_lote_sap` com histórico MSEG de 24 meses, ver
    `movimento_estoque_resumo_material_centro`). Usado só no ranking por material, não
    no detalhe por item (esse usa `Motivo_Principal`, que é por cliente+item).
    """
    if row.get("Qtd_Qualidade", 0) > 0:
        return "Preso em qualidade agora"
    if row.get("Qtd_Bloqueado", 0) > 0:
        return "Bloqueado agora (não é etapa normal)"
    if pd.isna(row.get("Data_Ultima_Entrada")):
        return "Sem entrada registrada (24 meses) — nunca produzido/recebido nessa janela"
    if row.get("Qtd_Disponivel_Venda", 0) > 0:
        return "Tem estoque livre — não alocado a este pedido na fila FIFO"
    if pd.notna(row.get("Data_Ultima_Saida")):
        return "Já vendido/consumido — produção não repôs desde a última saída"
    return "Sem dado suficiente pra classificar"


df = _dados_cached()

if df.empty:
    st.info("Nenhum backlog aberto encontrado.")
else:
    df = df.merge(_org_vendas_cached(), on="Codigo_Org_Vendas", how="left")
    df["Nome_Org_Vendas"] = df["Nome_Org_Vendas"].fillna(df["Codigo_Org_Vendas"])
    df["Motivo_Principal"] = df.apply(_classificar_motivo_principal, axis=1)

    n_falso_positivo = int((df["Motivo_Principal"] == "Falso Positivo (já faturado)").sum())
    qtd_falso_positivo = df.loc[df["Motivo_Principal"] == "Falso Positivo (já faturado)", "Qtd_Pendente_Operacional"].sum()
    if n_falso_positivo:
        st.warning(
            f"**Achado grave**: {n_falso_positivo:,} dos {len(df):,} itens marcados 'pendente' "
            f"({qtd_falso_positivo:,.0f} unidades) já estão `Flag_Totalmente_Faturado=1` — "
            "principalmente devolução (`ZREB`/`ZROB`/`ZRSG`/...) cujo `Qtd_Remetida` nunca é "
            "populado no SAP, então ficam presos em 'sem estoque' pra sempre mesmo já "
            "concluídos. Não entram nas métricas/gráficos abaixo por padrão (categoria "
            "'Falso Positivo (já faturado)') — marque a caixa abaixo pra incluir mesmo assim."
        )
    incluir_falso_positivo = st.checkbox(
        "Incluir 'Falso Positivo (já faturado)' nas métricas abaixo",
        value=False,
        key="pxe_incluir_falso_positivo",
        help="Deixe desmarcado pra ver só backlog real — pedidos já 100% faturados não deveriam contar como pendência.",
    )
    df_base = df if incluir_falso_positivo else df[df["Motivo_Principal"] != "Falso Positivo (já faturado)"]

    f1, f2, f3, f4, f5 = st.columns([1.3, 1.3, 0.8, 0.8, 0.8])
    with f1:
        filtro_org = st.multiselect(
            "Organização de Vendas",
            options=sorted(df["Nome_Org_Vendas"].dropna().unique()),
            default=[],
            key="pxe_org",
            help="Vazio = todas.",
        )
    with f2:
        filtro_motivo = st.multiselect(
            "Motivo_Principal",
            options=sorted(df["Motivo_Principal"].dropna().unique()),
            default=[],
            key="pxe_motivo",
            help="Vazio = todos.",
        )
    with f3:
        filtro_centro = st.text_input("Centro (opcional)", key="pxe_centro").strip()
    with f4:
        filtro_material = st.text_input("Material (opcional)", key="pxe_material").strip().upper()
    with f5:
        filtro_cliente = st.text_input("Cliente (nome, opcional)", key="pxe_cliente").strip().upper()

    def _limpar_filtro_material_centro() -> None:
        st.session_state["pxe_material"] = ""
        st.session_state["pxe_centro"] = ""

    if filtro_material or filtro_centro:
        st.button(
            "Limpar filtro de Material/Centro",
            on_click=_limpar_filtro_material_centro,
            key="pxe_limpar_material_centro",
        )

    df_filtrado = df_base
    if filtro_org:
        df_filtrado = df_filtrado[df_filtrado["Nome_Org_Vendas"].isin(filtro_org)]
    if filtro_motivo:
        df_filtrado = df_filtrado[df_filtrado["Motivo_Principal"].isin(filtro_motivo)]
    if filtro_centro:
        df_filtrado = df_filtrado[df_filtrado["Codigo_Centro"] == filtro_centro]
    if filtro_material:
        df_filtrado = df_filtrado[df_filtrado["Codigo_Produto"].str.upper() == filtro_material]
    if filtro_cliente:
        df_filtrado = df_filtrado[df_filtrado["Nome_Cliente"].str.upper().str.contains(filtro_cliente, na=False)]

    valor_total = df_filtrado["Valor_Pendente_Faturamento"].sum()
    qtd_total = df_filtrado["Qtd_Pendente_Operacional"].sum()
    mask_sem_estoque = df_filtrado["Motivo_Principal"].isin(["Sem Estoque", "Estoque Parcial"])
    valor_sem_estoque = df_filtrado.loc[mask_sem_estoque, "Valor_Pendente_Faturamento"].sum()
    qtd_sem_estoque = df_filtrado.loc[mask_sem_estoque, "Qtd_Pendente_Operacional"].sum()
    mask_financeiro = df_filtrado["Motivo_Principal"] == "Financeiro (crédito)"
    valor_financeiro = df_filtrado.loc[mask_financeiro, "Valor_Pendente_Faturamento"].sum()
    qtd_financeiro = df_filtrado.loc[mask_financeiro, "Qtd_Pendente_Operacional"].sum()

    with card("pxe-kpi"):
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Itens de pedido (filtro atual)", f"{len(df_filtrado):,}")
        k2.metric("Valor pendente total", f"R$ {valor_total:,.0f}", f"{qtd_total:,.0f} un pendentes")
        k3.metric(
            "Sem estoque + parcial",
            f"R$ {valor_sem_estoque:,.0f}",
            f"{qtd_sem_estoque:,.0f} un ({(valor_sem_estoque / valor_total * 100 if valor_total else 0):.0f}% do valor)",
        )
        k4.metric(
            "Financeiro (crédito)",
            f"R$ {valor_financeiro:,.0f}",
            f"{qtd_financeiro:,.0f} un ({(valor_financeiro / valor_total * 100 if valor_total else 0):.0f}% do valor)",
        )

    st.markdown("**Valor e quantidade pendente por Motivo_Principal**")
    with card("pxe-motivo-chart"):
        col_a, col_b = st.columns([1, 1])
        df_motivo = (
            df_filtrado.groupby("Motivo_Principal")
            .agg(Valor_Pendente_Faturamento=("Valor_Pendente_Faturamento", "sum"), Qtd_Pendente_Operacional=("Qtd_Pendente_Operacional", "sum"))
            .sort_values("Valor_Pendente_Faturamento", ascending=False)
        )
        with col_a:
            st.dataframe(
                df_motivo.reset_index().style.format(
                    {"Valor_Pendente_Faturamento": "R$ {:,.2f}", "Qtd_Pendente_Operacional": "{:,.0f}"}
                ),
                width="stretch",
                hide_index=True,
            )
        with col_b:
            st.caption("% do total — Valor x Quantidade (escalas bem diferentes, comparar em R$/un direto não faz sentido)")
            df_motivo_pct = pd.DataFrame(
                {
                    "% do Valor": df_motivo["Valor_Pendente_Faturamento"] / df_motivo["Valor_Pendente_Faturamento"].sum() * 100,
                    "% da Quantidade": df_motivo["Qtd_Pendente_Operacional"] / df_motivo["Qtd_Pendente_Operacional"].sum() * 100,
                }
            )
            st.bar_chart(df_motivo_pct, horizontal=True, stack=False)

    st.markdown("**Motivo_Principal x Organização de Vendas**")
    tab_valor_pivot, tab_qtd_pivot = st.tabs(["Valor pendente (R$)", "Quantidade pendente (un)"])
    with tab_valor_pivot:
        with card("pxe-motivo-org-pivot-valor"):
            pivot_valor = df_filtrado.pivot_table(
                index="Motivo_Principal",
                columns="Nome_Org_Vendas",
                values="Valor_Pendente_Faturamento",
                aggfunc="sum",
                fill_value=0,
            )
            st.dataframe(pivot_valor.style.format("R$ {:,.0f}"), width="stretch")
    with tab_qtd_pivot:
        with card("pxe-motivo-org-pivot-qtd"):
            pivot_qtd = df_filtrado.pivot_table(
                index="Motivo_Principal",
                columns="Nome_Org_Vendas",
                values="Qtd_Pendente_Operacional",
                aggfunc="sum",
                fill_value=0,
            )
            st.dataframe(pivot_qtd.style.format("{:,.0f}"), width="stretch")

    st.divider()

    df_sem_cobertura = df_filtrado[df_filtrado["Motivo_Principal"].isin(["Sem Estoque", "Estoque Parcial"])]

    if df_sem_cobertura.empty:
        st.caption(
            ":material/inventory_2: Sem itens \"Sem Estoque\"/\"Estoque Parcial\" no filtro atual — a visão por "
            "material (suprimento) não se aplica aqui. Veja o motivo real no **Detalhe item a item** abaixo."
        )
    else:
        st.markdown("## :material/inventory_2: Visão por material — suprimento")
        st.caption(
            "Daqui pra baixo a pergunta muda: não é mais \"por que ESTE pedido está pendente\" "
            "(`Motivo_Principal`, acima, por pedido+cliente), e sim \"por que ESTE MATERIAL não "
            "tem estoque, somando TODOS os pedidos/clientes que esperam por ele\" "
            "(`Causa_Estoque_Material`, abaixo, por Material+Centro). Útil pra priorizar compra/"
            "produção — um material pode ter cliente A com crédito ok e cliente B bloqueado ao "
            "mesmo tempo, então o motivo por pedido varia mesmo dentro do mesmo material."
        )
        st.markdown("**Ranking de materiais sem cobertura de estoque (priorizar compra/produção)**")
        st.caption(
            "`Causa_Estoque_Material` cruza o snapshot atual de estoque com o histórico de "
            "movimento dos últimos 24 meses (`SILVER.dataspherev2.mseg`/`mkpf` — ver aba "
            "**Rastreio de Lote** na página **Estoque** pra abrir 1 lote específico)."
        )
        df_ranking = (
            df_sem_cobertura.groupby(["Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro"])
            .agg(
                Itens_Total=("Numero_Pedido", "count"),
                Qtd_Sem_Estoque=("Qtd_Pendente_Operacional", "sum"),
                Valor_Sem_Estoque=("Valor_Pendente_Faturamento", "sum"),
            )
            .reset_index()
        )
        df_ranking = df_ranking[df_ranking["Valor_Sem_Estoque"] > 0].sort_values("Valor_Sem_Estoque", ascending=False)

        df_estoque_mc = _estoque_cached().rename(columns={"Codigo_Material": "Codigo_Produto"})[
            ["Codigo_Produto", "Codigo_Centro", "Qtd_Qualidade", "Qtd_Bloqueado", "Qtd_Disponivel_Venda"]
        ]
        df_ranking = df_ranking.merge(df_estoque_mc, on=["Codigo_Produto", "Codigo_Centro"], how="left").merge(
            _movimento_cached(), on=["Codigo_Produto", "Codigo_Centro"], how="left"
        )
        df_ranking["Causa_Estoque_Material"] = df_ranking.apply(_classificar_causa_estoque_material, axis=1)
        for col in ("Data_Ultima_Entrada", "Data_Ultima_Liberacao_Qualidade", "Data_Ultima_Saida", "Data_Ultimo_Movimento"):
            df_ranking[col] = pd.to_datetime(df_ranking[col])
        df_ranking["Dias_Desde_Ultimo_Movimento"] = (pd.Timestamp(date.today()) - df_ranking["Data_Ultimo_Movimento"]).dt.days

        filtro_motivo_estoque = st.multiselect(
            "Causa_Estoque_Material (filtra o ranking abaixo)",
            options=sorted(df_ranking["Causa_Estoque_Material"].dropna().unique()),
            default=[],
            key="pxe_motivo_estoque_material",
            help="Vazio = todos.",
        )
        if filtro_motivo_estoque:
            df_ranking = df_ranking[df_ranking["Causa_Estoque_Material"].isin(filtro_motivo_estoque)]

        st.markdown("**Valor e quantidade sem estoque por Causa_Estoque_Material**")
        with card("pxe-motivo-estoque-material-chart"):
            col_c, col_d = st.columns([1, 1])
            df_motivo_estoque = (
                df_ranking.groupby("Causa_Estoque_Material")
                .agg(Valor_Sem_Estoque=("Valor_Sem_Estoque", "sum"), Qtd_Sem_Estoque=("Qtd_Sem_Estoque", "sum"))
                .sort_values("Valor_Sem_Estoque", ascending=False)
            )
            with col_c:
                st.dataframe(
                    df_motivo_estoque.reset_index().style.format({"Valor_Sem_Estoque": "R$ {:,.2f}", "Qtd_Sem_Estoque": "{:,.0f}"}),
                    width="stretch",
                    hide_index=True,
                )
            with col_d:
                st.caption("% do total — Valor x Quantidade (escalas bem diferentes, comparar em R$/un direto não faz sentido)")
                df_motivo_estoque_pct = pd.DataFrame(
                    {
                        "% do Valor": df_motivo_estoque["Valor_Sem_Estoque"] / df_motivo_estoque["Valor_Sem_Estoque"].sum() * 100,
                        "% da Quantidade": df_motivo_estoque["Qtd_Sem_Estoque"] / df_motivo_estoque["Qtd_Sem_Estoque"].sum() * 100,
                    }
                )
                st.bar_chart(df_motivo_estoque_pct, horizontal=True, stack=False)

        st.caption("Clique numa linha pra filtrar a página inteira nesse Material+Centro (preenche os filtros de Centro/Material acima).")
        colunas_ranking = [
            "Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro",
            "Itens_Total", "Qtd_Sem_Estoque", "Valor_Sem_Estoque",
            "Causa_Estoque_Material", "Data_Ultima_Entrada", "Data_Ultima_Saida", "Dias_Desde_Ultimo_Movimento",
        ]
        df_ranking_show = df_ranking[colunas_ranking].reset_index(drop=True)

        def _ao_selecionar_ranking() -> None:
            # Callback (roda ANTES do corpo do script na próxima execução) — só assim dá
            # pra escrever em st.session_state dos widgets de filtro (pxe_material/
            # pxe_centro), que já foram instanciados mais acima no mesmo script; fazer
            # isso direto no corpo do script (depois de já ter passado pelos widgets)
            # dispara StreamlitAPIException ("cannot be modified after the widget ... is
            # instantiated").
            selecao = st.session_state["pxe_ranking_tabela"]["selection"]["rows"]
            if selecao:
                linha_r = df_ranking_show.iloc[selecao[0]]
                st.session_state["pxe_material"] = linha_r["Codigo_Produto"]
                st.session_state["pxe_centro"] = linha_r["Codigo_Centro"]

        with card("pxe-ranking"):
            st.dataframe(
                df_ranking_show.style.format(
                    {
                        "Qtd_Sem_Estoque": "{:,.0f}",
                        "Valor_Sem_Estoque": "R$ {:,.2f}",
                        "Dias_Desde_Ultimo_Movimento": "{:,.0f}",
                    },
                    na_rep="—",
                ),
                width="stretch",
                hide_index=True,
                on_select=_ao_selecionar_ranking,
                selection_mode="single-row",
                key="pxe_ranking_tabela",
            )

    st.divider()

    st.markdown("## :material/list_alt: Detalhe por pedido")
    st.caption(
        "1 linha por pedido (não por item) — clique num pedido pra ver os itens dele, "
        "depois clique num item pra abrir o contexto completo."
    )
    colunas_detalhe = [
        "Numero_Pedido", "Item_Pedido", "Tipo_Ordem_Venda", "Data_Inclusao_Pedido", "Dias_Desde_Inclusao_Pedido",
        "Codigo_Produto", "Descricao_Produto", "Codigo_Centro", "Nome_Centro",
        "Nome_Org_Vendas", "Codigo_Cliente", "Nome_Cliente", "Qtd_Pendente_Operacional", "Valor_Pendente_Faturamento",
        "Motivo_Principal", "Status_Pendencia", "Status_Pendencia_Estoque", "Flag_Totalmente_Faturado",
        "Valor_Credito_Disponivel", "Cliente_Bloqueado",
        "Qtd_Pedida", "Qtd_Remetida", "Qtd_Faturada",
        "Primeira_Data_Remessa", "Ultima_Data_Remessa", "Primeira_Data_Faturamento", "Ultima_Data_Faturamento",
    ]

    def _resumo_motivo(serie: pd.Series) -> str:
        valores = serie.unique()
        return valores[0] if len(valores) == 1 else f"Vários ({len(valores)})"

    df_pedido_agregado = (
        df_filtrado.groupby("Numero_Pedido")
        .agg(
            Data_Inclusao_Pedido=("Data_Inclusao_Pedido", "min"),
            Tipo_Ordem_Venda=("Tipo_Ordem_Venda", "first"),
            Nome_Cliente=("Nome_Cliente", "first"),
            Nome_Org_Vendas=("Nome_Org_Vendas", "first"),
            Itens_Total=("Item_Pedido", "count"),
            Qtd_Pendente_Total=("Qtd_Pendente_Operacional", "sum"),
            Valor_Pendente_Total=("Valor_Pendente_Faturamento", "sum"),
            Motivo_Resumo=("Motivo_Principal", _resumo_motivo),
        )
        .reset_index()
        .sort_values("Data_Inclusao_Pedido", ascending=False)
    )

    n_detalhe = st.slider("Quantos pedidos mostrar", min_value=20, max_value=2000, value=200, step=20, key="pxe_detalhe_n")
    df_pedido_show = df_pedido_agregado.head(n_detalhe).reset_index(drop=True)

    with card("pxe-detalhe-pedido"):
        evento_pedido = st.dataframe(
            df_pedido_show.style.format(
                {"Valor_Pendente_Total": "R$ {:,.2f}", "Qtd_Pendente_Total": "{:,.0f}"}, na_rep="—"
            ),
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="pxe_pedido_tabela",
        )

    linhas_pedido_sel = evento_pedido.selection.rows if evento_pedido and evento_pedido.selection else []
    if not linhas_pedido_sel:
        st.caption("Clique num pedido acima pra ver os itens dele.")
        df_detalhe_show = pd.DataFrame(columns=colunas_detalhe)
        linhas_selecionadas: list[int] = []
    else:
        numero_pedido_sel = df_pedido_show.iloc[linhas_pedido_sel[0]]["Numero_Pedido"]
        st.markdown(f"**Itens do pedido {numero_pedido_sel}**")
        df_detalhe_show = (
            df_filtrado[df_filtrado["Numero_Pedido"] == numero_pedido_sel][colunas_detalhe]
            .sort_values("Item_Pedido")
            .reset_index(drop=True)
        )
        with card("pxe-detalhe-item"):
            evento = st.dataframe(
                df_detalhe_show.style.format(
                    {"Valor_Pendente_Faturamento": "R$ {:,.2f}", "Valor_Credito_Disponivel": "R$ {:,.2f}"}, na_rep="—"
                ),
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                # key inclui o pedido selecionado: trocar de pedido tem que "esquecer" a
                # linha selecionada antes (senão o índice antigo aponta pra outro item,
                # de outro pedido, depois da troca).
                key=f"pxe_item_tabela_{numero_pedido_sel}",
            )
        linhas_selecionadas = evento.selection.rows if evento and evento.selection else []

    if linhas_selecionadas:
        linha = df_detalhe_show.iloc[linhas_selecionadas[0]]
        st.markdown(
            f"### Pedido {linha['Numero_Pedido']} / item {linha['Item_Pedido']} — "
            f"{linha['Codigo_Produto']} ({linha['Descricao_Produto']})"
        )
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Cliente", linha["Nome_Cliente"])
        d2.metric("Motivo_Principal", linha["Motivo_Principal"])
        d3.metric("Data do pedido", str(linha["Data_Inclusao_Pedido"])[:10])
        d4.metric("Valor pendente", f"R$ {linha['Valor_Pendente_Faturamento']:,.2f}")
        st.caption(
            f"Centro {linha['Codigo_Centro']} ({linha['Nome_Centro']}) — Org. Vendas "
            f"{linha['Nome_Org_Vendas']}. `Status_Pendencia`: {linha['Status_Pendencia']}. "
            f"`Status_Pendencia_Estoque`: {linha['Status_Pendencia_Estoque']}."
        )

        with st.expander("Oportunidade (Salesforce)"):
            df_opp = _oportunidade_pedido_cached(str(linha["Numero_Pedido"]))
            df_opp_item = df_opp[df_opp["Item_Pedido"] == linha["Item_Pedido"]] if not df_opp.empty else df_opp
            df_opp_match = df_opp_item[df_opp_item["Nome_Oportunidade"].notna()] if not df_opp_item.empty else df_opp_item
            if df_opp_match.empty:
                st.info("Nenhuma Oportunidade vinculada a este item (73% de cobertura medida — ver docs).")
            else:
                o1, o2, o3 = st.columns(3)
                o1.metric("Oportunidade", df_opp_match.iloc[0]["Nome_Oportunidade"])
                o2.metric("Estágio", df_opp_match.iloc[0]["Estagio_Oportunidade"])
                o3.metric("Valor do item (Salesforce)", f"R$ {df_opp_match.iloc[0]['Valor_Item_Oportunidade']:,.2f}")
                st.caption(
                    f"Ganha: {'sim' if df_opp_match.iloc[0]['Oportunidade_Ganha'] else 'não'} · "
                    f"Valor total da Oportunidade (todos os itens): R$ {df_opp_match.iloc[0]['Valor_Oportunidade']:,.2f} · "
                    f"Criada em {str(df_opp_match.iloc[0]['Data_Criacao_Oportunidade'])[:10]}."
                )

        with st.expander("Remessas (entregas) deste item"):
            df_rem = _remessas_pedido_cached(str(linha["Numero_Pedido"]))
            df_rem_item = df_rem[df_rem["Item_Pedido_Origem"] == linha["Item_Pedido"]] if not df_rem.empty else df_rem
            if df_rem_item.empty:
                st.info("Nenhuma remessa registrada pra este item ainda.")
            else:
                colunas_remessa = [
                    "Numero_Entrega", "Item_Entrega", "Data_Remessa", "Tipo_Remessa",
                    "Codigo_Centro", "Charg_Numero_Do_Lote", "Qtd_Remetida", "Peso_Liquido",
                ]
                colunas_remessa = [c for c in colunas_remessa if c in df_rem_item.columns]
                st.dataframe(df_rem_item[colunas_remessa], width="stretch", hide_index=True)

        if linha["Motivo_Principal"] == "Financeiro (crédito)":
            st.markdown("**Por que está bloqueado financeiramente?**")
            df_credito = _credito_cliente_cached(str(linha["Codigo_Cliente"]))
            if df_credito.empty:
                st.caption("Cliente sem linha em `fct_limite_credito_sap` — não achei detalhe de crédito pra ele.")
            else:
                colunas_credito = [
                    "Area_Controle_Credito", "Classe_Risco_Cliente", "Flag_Cliente_Bloqueado",
                    "Valor_Limite_Credito_Concedido", "Valor_Exposicao_Total_SAP",
                    "Valor_Saldo_A_Vencer", "Valor_Saldo_Vencido", "Valor_Saldo_Aberto_Total",
                    "Valor_Credito_Disponivel",
                ]
                st.caption(
                    f"{len(df_credito)} área(s) de controle de crédito pra este cliente — o pior caso entre elas "
                    f"é o que trava o pedido (R$ {linha['Valor_Credito_Disponivel']:,.2f} disponível, "
                    f"{'BLOQUEADO' if linha['Cliente_Bloqueado'] == 1 else 'não bloqueado pela flag'})."
                )
                st.dataframe(
                    df_credito[colunas_credito].style.format(
                        {
                            "Valor_Limite_Credito_Concedido": "R$ {:,.2f}",
                            "Valor_Exposicao_Total_SAP": "R$ {:,.2f}",
                            "Valor_Saldo_A_Vencer": "R$ {:,.2f}",
                            "Valor_Saldo_Vencido": "R$ {:,.2f}",
                            "Valor_Saldo_Aberto_Total": "R$ {:,.2f}",
                            "Valor_Credito_Disponivel": "R$ {:,.2f}",
                        },
                        na_rep="—",
                    ),
                    width="stretch",
                    hide_index=True,
                )
                if (df_credito["Valor_Saldo_Vencido"] > 0).any():
                    st.caption(
                        ":material/warning: Tem saldo VENCIDO em pelo menos 1 área de crédito — é o motivo mais "
                        "provável do bloqueio, não só limite estourado por pedidos em aberto."
                    )

        if linha["Motivo_Principal"] in ("Logístico e Fiscal", "Logístico (Remessa)", "Fiscal (Faturamento)"):
            st.markdown("**Onde está travado: remessa ou fatura?**")
            qtd_pedida_total = linha["Qtd_Pedida"]
            qtd_remetida = linha["Qtd_Remetida"]
            qtd_faturada = linha["Qtd_Faturada"]
            falta_remeter = max(qtd_pedida_total - qtd_remetida, 0)
            falta_faturar = max(qtd_remetida - qtd_faturada, 0)

            l1, l2, l3 = st.columns(3)
            l1.metric("Pedida", f"{qtd_pedida_total:,.0f}")
            l2.metric("Remetida", f"{qtd_remetida:,.0f}", f"falta {falta_remeter:,.0f}" if falta_remeter else "completa")
            l3.metric("Faturada", f"{qtd_faturada:,.0f}", f"falta {falta_faturar:,.0f} (do já remetido)" if falta_faturar else "em dia")

            if falta_remeter > 0 and falta_faturar > 0:
                explicacao = (
                    f"Falta **remeter {falta_remeter:,.0f} un.** (dos {qtd_pedida_total:,.0f} pedidos) "
                    f"E falta **faturar {falta_faturar:,.0f} un.** do que já foi remetido — travado nos dois "
                    "documentos ao mesmo tempo."
                )
            elif falta_remeter > 0:
                explicacao = f"Falta **remeter {falta_remeter:,.0f} un.** — nada a faturar ainda porque não saiu do depósito."
            elif falta_faturar > 0:
                explicacao = f"Já foi **tudo remetido** — falta só **faturar {falta_faturar:,.0f} un.** que já saíram do depósito."
            else:
                explicacao = "Remessa e faturamento já batem com o pedido — se ainda aparece pendente, vale conferir `Status_Pendencia` direto."
            st.caption(explicacao)

            datas_txt = []
            if pd.notna(linha["Primeira_Data_Remessa"]):
                datas_txt.append(f"1ª remessa: {str(linha['Primeira_Data_Remessa'])[:10]}")
            if pd.notna(linha["Ultima_Data_Remessa"]):
                datas_txt.append(f"última remessa: {str(linha['Ultima_Data_Remessa'])[:10]}")
            if pd.notna(linha["Primeira_Data_Faturamento"]):
                datas_txt.append(f"1ª fatura: {str(linha['Primeira_Data_Faturamento'])[:10]}")
            if pd.notna(linha["Ultima_Data_Faturamento"]):
                datas_txt.append(f"última fatura: {str(linha['Ultima_Data_Faturamento'])[:10]}")
            if datas_txt:
                st.caption(" · ".join(datas_txt))
            else:
                st.caption("Nunca teve remessa nem fatura registrada pra este item.")

        st.markdown("**Tinha estoque na data do pedido? (dado real do SAP)**")
        st.caption(
            "Fonte: `IB_SAPECC.MCHBH` — fechamento de estoque por período, calculado pelo "
            "próprio SAP (não é estimativa nossa). Granularidade de MÊS fechado, não do dia "
            "exato — usa o fechamento do mês do pedido, ou o mais recente ANTES dele se o mês "
            "ainda não fechou (comum pra pedidos muito recentes). Carrega automaticamente ao "
            "selecionar a linha, ~5-10s (cacheado por 1h)."
        )
        with st.spinner("Consultando IB_SAPECC.MCHBH (HANA)..."):
            resultado = _estoque_historico_cached(
                str(linha["Codigo_Produto"]), str(linha["Codigo_Centro"]), str(linha["Data_Inclusao_Pedido"])[:10]
            )
        qtd_pedida = linha["Qtd_Pendente_Operacional"]
        livre_periodo = resultado["Qtd_Livre_Periodo"]
        total_periodo = livre_periodo + resultado["Qtd_Qualidade_Periodo"] + resultado["Qtd_Bloqueado_Periodo"]
        livre_atual = resultado["Qtd_Livre_Atual_Real"]
        total_atual = livre_atual + resultado["Qtd_Qualidade_Atual_Real"] + resultado["Qtd_Bloqueado_Atual_Real"]

        # Se o mês do pedido ainda não fechou, isso só acontece porque o pedido é do mês
        # CORRENTE (MCHBH só tem linha em mês fechado) — ou seja, "hoje real" nunca está
        # mais que ~1 mês incompleto de distância do pedido, enquanto o fechamento
        # disponível é de 1+ mês ANTES disso, podendo estar bem mais desatualizado.
        # Achado real, 2026-09-03: pedido 0000139216 feito HOJE tinha fechamento de
        # 08/2026 com 104.161 un. livres, mas o real de HOJE é 0 — a maior parte já
        # girou entre o fechamento e agora. Usar o fechamento antigo como se fosse "a
        # situação do pedido" nesse caso teria dado um veredito enganoso (o SAP já diz
        # `Status_Pendencia_Estoque='Pendente sem Estoque'`, batendo com o real de hoje,
        # não com o fechamento antigo).
        usar_hoje = resultado["Cobertura_Suficiente"] and not resultado["Periodo_E_Exato"]
        livre_base = livre_atual if usar_hoje else livre_periodo
        total_base = total_atual if usar_hoje else total_periodo
        fonte_label = "estoque de HOJE (real)" if usar_hoje else f"fechamento de {resultado['Periodo_Mes']}/{resultado['Periodo_Ano']}"

        if not resultado["Cobertura_Suficiente"]:
            st.warning("Nenhum fechamento de período encontrado em MCHBH pra este Material+Centro — sem dado real disponível.")
        elif usar_hoje:
            st.caption(
                f":material/info: Mês do pedido ainda não fechado no SAP — o fechamento mais recente disponível "
                f"é de **{resultado['Periodo_Mes']}/{resultado['Periodo_Ano']}**, mas como o pedido é do mês "
                f"corrente, o **estoque de hoje (real)** é uma referência mais atual, e é o que o veredito usa "
                f"abaixo (fechamento mostrado só como contexto)."
            )

        # Só "Livre" é vendável de verdade (mesma distinção da página Estoque, aba
        # "Restrito x Disponível") — Qualidade/Bloqueado existem fisicamente mas não
        # podiam ser alocados a este pedido. Checar Livre isolado primeiro, e só depois
        # o total físico, evita contar estoque preso em qualidade como se desse pra
        # vender (achado real, 2026-09-03: pedido 0000138952/PA8116 tinha 5.422 "total",
        # mas 5.387 presos em Qualidade e 0 em Livre — não estava disponível de fato).
        if not resultado["Cobertura_Suficiente"]:
            veredito = "Não dá pra confirmar — sem fechamento de período disponível pra esse Material+Centro."
        elif livre_base >= qtd_pedida:
            veredito = (
                f":material/check_circle: **TINHA estoque disponível pra venda** (base: {fonte_label} — "
                f"{livre_base:,.0f} un. livres, pedido pedia {qtd_pedida:,.0f})."
            )
        elif total_base >= qtd_pedida:
            veredito = (
                f":material/warning: Existia estoque fisicamente ({total_base:,.0f} un., base: {fonte_label}), mas "
                f"a maior parte presa em Qualidade/Bloqueado — só {livre_base:,.0f} un. estavam **livres pra "
                f"venda** (pedido pedia {qtd_pedida:,.0f}). Provavelmente **NÃO estava disponível** pra este pedido."
            )
        else:
            veredito = (
                f":material/cancel: **NÃO tinha estoque**, nem contando o restrito (base: {fonte_label} — "
                f"{total_base:,.0f} un. no total, pedido pedia {qtd_pedida:,.0f})."
            )

        v1, v2 = st.columns([2, 1])
        v1.markdown(f"#### {veredito}")
        v2.metric(f"Livre pra venda — {fonte_label}", f"{livre_base:,.0f}", f"pedido pedia {qtd_pedida:,.0f}")
        v2.caption(f"Total físico (Livre+Qualidade+Bloqueado): {total_base:,.0f}")

        with st.expander("Detalhe por tipo de estoque (Livre/Qualidade/Bloqueado)"):
            h1, h2, h3 = st.columns(3)
            h1.metric(
                "Livre — fechamento do período",
                f"{resultado['Qtd_Livre_Periodo']:,.0f}",
                f"hoje real: {resultado['Qtd_Livre_Atual_Real']:,.0f}",
            )
            h2.metric(
                "Qualidade — fechamento do período",
                f"{resultado['Qtd_Qualidade_Periodo']:,.0f}",
                f"hoje real: {resultado['Qtd_Qualidade_Atual_Real']:,.0f}",
            )
            h3.metric(
                "Bloqueado — fechamento do período",
                f"{resultado['Qtd_Bloqueado_Periodo']:,.0f}",
                f"hoje real: {resultado['Qtd_Bloqueado_Atual_Real']:,.0f}",
            )
    elif linhas_pedido_sel:
        st.caption("Clique numa linha da tabela de itens acima pra abrir o contexto completo do item.")
