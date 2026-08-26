"""Router do dashboard (Streamlit multipage) — monta a navegação agrupada + filtro global.

Uso:
    uv run streamlit run app.py

Conteúdo de cada página está em pages/*.py; este arquivo define a estrutura do menu lateral
(Home solta no topo, sem seção — `st.navigation` trata `""` como cabeçalho vazio, exibido
antes das seções colapsáveis —, seguida de 3 seções: Dashboards, Faturamento (Painel Vendas)
e Técnico — ver docs/COMO_RODAR.md §9; a separação entre Dashboards e Faturamento (Painel
Vendas) existe porque são *consultas* diferentes sobre a mesma fonte — Dashboards soma total
bruto de `vendas_sap`, Faturamento (Painel Vendas) passa pelo crosswalk cliente→setor
(`scripts/query_faturamento_comercial.py`, ~52% de cobertura) — ver
docs/CONTEXTO_VENDAS_SAP.md §10 — não é só organização visual)
e o filtro global (período de
datas + Governo x Privado) que os Dashboards leem via `st.session_state["flt_data_inicio"]`
/ `st.session_state["flt_data_fim"]` / `st.session_state["flt_tipo_cliente"]` — ver
docs/COMO_RODAR.md §9.1. Reusa os módulos em
scripts/ (mesma lógica de conexão e consultas dos CLIs) — não duplica SQL, só troca
print()/tabela de texto por uma tela. Ver docs/CONTEXTO_VENDAS_SAP.md para o significado
das tabelas.
"""

from __future__ import annotations

import datetime

import streamlit as st

with st.sidebar:
    st.markdown("#### 🔍 Filtro global")
    st.caption("Vale para Pendências, Jornada do Pedido, Crédito/Devoluções e Faturamento.")
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
            st.Page("pages/0_Home.py", title="Home", icon="🔎", default=True),
        ],
        "📊 Dashboards": [
            st.Page("pages/1_Pendencias.py", title="Pendências", icon="📦"),
            st.Page("pages/5_Jornada_Pedido.py", title="Jornada do Pedido", icon="🔗"),
            st.Page("pages/6_Estoque.py", title="Estoque", icon="📦"),
            st.Page("pages/7_Credito_Devolucoes.py", title="Crédito e Devoluções", icon="💳"),
            st.Page(
                "pages/8_Faturamento_Org_Vendas.py", title="Faturamento por Org Vendas", icon="🏢"
            ),
            st.Page("pages/10_Analise_Historica.py", title="Análise Histórica", icon="📈"),
            st.Page("pages/11_Metas.py", title="Meta x Realizado (SAP)", icon="🎯"),
            st.Page("pages/16_Relatorio_Pedidos.py", title="Relatório de Pedidos", icon="🧾"),
        ],
        "💰 Faturamento (Painel Vendas)": [
            st.Page("pages/12_Faturamento_vs_Meta.py", title="Faturamento vs Meta", icon="🎯"),
            st.Page("pages/13_Faturamento_Diario.py", title="Faturamento Diário", icon="📅"),
            st.Page("pages/14_Faturamento_Anual.py", title="Faturamento Anual (YoY)", icon="📆"),
            st.Page("pages/15_Produto_Cliente.py", title="Produto / Cliente", icon="🧪"),
            st.Page("pages/17_Relatorio_Analitico.py", title="Relatório Analítico", icon="🔬"),
        ],
        "🛠️ Técnico": [
            st.Page("pages/2_Auditoria.py", title="Auditoria do Fluxo", icon="🩺"),
            st.Page("pages/3_Rastrear_Pedido.py", title="Rastrear Pedido", icon="🧭"),
            st.Page("pages/4_DDIC_Lookup.py", title="DDIC Lookup", icon="📖"),
            st.Page("pages/9_Conectividade.py", title="Conectividade", icon="🔌"),
        ],
    }
)
pg.run()
