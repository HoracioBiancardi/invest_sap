"""Router do dashboard (Streamlit multipage) — monta a navegação agrupada + filtro global.

Uso:
    uv run streamlit run app.py

Conteúdo de cada página está em pages/*.py; este arquivo define a estrutura do menu lateral
(Home solta no topo, sem seção — `st.navigation` trata `""` como cabeçalho vazio, exibido
antes das seções colapsáveis —, seguida de 3 seções: Executivo, Faturamento (Painel Vendas)
e Técnico — ver docs/COMO_RODAR.md §9).

**Executivo** reúne as 13 visões de portfólio (Visão 360, Oportunidade, Pedidos, Pendência
x Estoque, Remessas, Faturamento, Faturamento x Meta, Estoque, Material, Cliente 360,
Crédito e Devoluções, Vendedor, Vendedor x Meta x Faturamento) — todas sobre o total bruto
de `vendas_sap`. **Faturamento (Painel Vendas)**
passa pelas mesmas tabelas fonte só que via o crosswalk comercial cliente→setor
(`scripts/query_faturamento_comercial.py`, ~52% de cobertura) — ver
docs/CONTEXTO_VENDAS_SAP.md §10 — não é só organização visual, são *consultas* diferentes
sobre a mesma fonte. **Técnico** é ferramenta de investigação pontual (Auditoria do Fluxo),
não uso recorrente.

Filtro global (período de datas + Governo x Privado) que as páginas leem via
`st.session_state["flt_data_inicio"]`/`st.session_state["flt_data_fim"]`/
`st.session_state["flt_tipo_cliente"]` — ver docs/COMO_RODAR.md §9.1. Reusa os módulos em
scripts/ (mesma lógica de conexão e consultas dos CLIs) — não duplica SQL, só troca
print()/tabela de texto por uma tela. Ver docs/CONTEXTO_VENDAS_SAP.md para o significado
das tabelas.
"""

from __future__ import annotations

import datetime

import streamlit as st

from scripts.ui_theme import apply_custom_theme

apply_custom_theme()

with st.sidebar:
    st.markdown("#### :material/filter_alt: Filtro global")
    st.caption("Vale para Oportunidade, Pedidos, Crédito/Devoluções, Faturamento e Vendedor.")
    _hoje = datetime.date.today()
    _periodo = st.date_input(
        "Período",
        value=(_hoje - datetime.timedelta(days=30), _hoje),
        max_value=_hoje,
        key="flt_periodo",
    )
    # date_input com range retorna tupla de 1 elemento enquanto o usuário só escolheu a
    # data inicial (segunda ponta ainda não selecionada) — só atualiza o filtro global
    # quando o range vier completo; até lá, mantém o valor anterior (ou o default).
    if isinstance(_periodo, tuple) and len(_periodo) == 2:
        st.session_state["flt_data_inicio"], st.session_state["flt_data_fim"] = _periodo
    st.selectbox("Tipo de cliente", ["Todos", "Governo", "Privado"], key="flt_tipo_cliente")
    st.divider()

pg = st.navigation(
    {
        "": [
            st.Page("pages/0_Home.py", title="Home", icon=":material/dashboard:", default=True),
        ],
        "Executivo": [
            st.Page(
                "pages/27_Pendencia_x_Estoque.py",
                title="Pendência x Estoque",
                icon=":material/pending_actions:",
            ),
            st.Page(
                "pages/26_Visao_360.py", title="Visão 360", icon=":material/hub:"
            ),
            st.Page(
                "pages/19_Oportunidade.py", title="Oportunidade", icon=":material/target:"
            ),
            st.Page(
                "pages/20_Pedidos.py", title="Pedidos", icon=":material/receipt_long:"
            ),
            st.Page(
                "pages/21_Remessas.py", title="Remessas", icon=":material/local_shipping:"
            ),
            st.Page(
                "pages/22_Faturamento.py", title="Faturamento", icon=":material/payments:"
            ),
            st.Page(
                "pages/11_Metas.py",
                title="Faturamento x Meta",
                icon=":material/track_changes:",
            ),
            st.Page("pages/6_Estoque.py", title="Estoque", icon=":material/inventory_2:"),
            st.Page(
                "pages/23_Material.py", title="Material", icon=":material/inventory:"
            ),
            st.Page(
                "pages/25_Cliente_360.py", title="Cliente 360", icon=":material/account_circle:"
            ),
            st.Page(
                "pages/7_Credito_Devolucoes.py",
                title="Crédito e Devoluções",
                icon=":material/credit_card:",
            ),
            st.Page(
                "pages/18_Visao_Vendedor.py",
                title="Vendedor",
                icon=":material/badge:",
            ),
            st.Page(
                "pages/24_Vendedor_x_Meta.py",
                title="Vendedor x Meta x Faturamento",
                icon=":material/leaderboard:",
            ),
        ],
        "Faturamento (Painel Vendas)": [
            st.Page(
                "pages/12_Faturamento_vs_Meta.py",
                title="Faturamento vs Meta",
                icon=":material/speed:",
            ),
            st.Page(
                "pages/13_Faturamento_Diario.py",
                title="Faturamento Diário",
                icon=":material/calendar_today:",
            ),
            st.Page(
                "pages/14_Faturamento_Anual.py",
                title="Faturamento Anual (YoY)",
                icon=":material/calendar_month:",
            ),
            st.Page(
                "pages/15_Produto_Cliente.py", title="Produto / Cliente", icon=":material/category:"
            ),
            st.Page(
                "pages/17_Relatorio_Analitico.py",
                title="Relatório Analítico",
                icon=":material/query_stats:",
            ),
        ],
        "Técnico": [
            st.Page(
                "pages/2_Auditoria.py", title="Auditoria do Fluxo", icon=":material/fact_check:"
            ),
        ],
    }
)
pg.run()
