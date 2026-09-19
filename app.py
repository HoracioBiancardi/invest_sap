"""Router do dashboard (Streamlit multipage) — monta a navegação agrupada + filtro global.

Uso:
    uv run streamlit run app.py

Conteúdo de cada página está em pages/*.py; este arquivo define a estrutura do menu lateral
(Home solta no topo, sem seção — `st.navigation` trata `""` como cabeçalho vazio, exibido
antes das seções colapsáveis —, seguida de 3 seções: Executivo, Faturamento (Painel Vendas)
e Técnico — ver docs/COMO_RODAR.md §9).

As 12 visões de portfólio sobre o total bruto de `vendas_sap` (antes só "Executivo", 1 seção
só, ordem sem critério) foram divididas em 3 seções seguindo o fluxo real de venda —
reordenar/reagrupar por pedido do usuário (2026-09-05), sem mudar nenhuma página em si:
**Funil de Vendas** segue a ordem cronológica do pedido (Oportunidade → Pedido → Pendência/
Estoque → Remessa → Faturamento → Crédito e Devoluções, esse último pós-fatura); **Metas e
Performance** é acompanhamento (Faturamento x Meta, Vendedor, Vendedor x Meta x Faturamento);
**Cadastros** é consulta pontual de dimensão (Cliente 360, Material). **Faturamento (Painel Vendas)**
passa pelas mesmas tabelas fonte só que via o crosswalk comercial cliente→setor
(`scripts/query_faturamento_comercial.py`, ~52% de cobertura) — ver
docs/CONTEXTO_VENDAS_SAP.md §10 — não é só organização visual, são *consultas* diferentes
sobre a mesma fonte. **Técnico** é ferramenta de investigação pontual (Auditoria do Fluxo),
não uso recorrente.

Filtro de Período + Tipo de cliente (Governo x Privado): até 2026-09-04 vivia sozinho na
sidebar ("filtro global"), afetando 9 páginas sem ficar visível em nenhuma delas. Movido
pra dentro de cada página que usa (`scripts/ui_theme.py::render_filtro_periodo_tipo_cliente`/
`render_filtro_tipo_cliente`, chamado no topo do corpo de cada uma) — mesmas chaves de
`st.session_state` (`flt_data_inicio`/`flt_data_fim`/`flt_tipo_cliente`), então o valor
continua compartilhado entre as páginas que chamam uma das duas funções, só não mora mais
neste arquivo. Ver docs/COMO_RODAR.md §9.1. Reusa os módulos em scripts/ (mesma lógica de
conexão e consultas dos CLIs) — não duplica SQL, só troca print()/tabela de texto por uma
tela. Ver docs/CONTEXTO_VENDAS_SAP.md para o significado das tabelas.
"""

from __future__ import annotations

import streamlit as st

from scripts.db import DatabaseConnectionError
from scripts.auth import require_login
from scripts.ui_theme import apply_custom_theme, render_theme_selector

st.set_page_config(
    page_title="SwordPower — Invest SAP", page_icon=":material/dashboard:", layout="wide"
)
apply_custom_theme()
usuario = require_login()  # fail-closed: nada abaixo roda sem autenticar

SECTIONS = {
    "": [
        st.Page("pages/0_Home.py", title="Home", icon=":material/dashboard:", default=True),
    ],
    "Funil de Vendas": [
        st.Page("pages/19_Oportunidade.py", title="Oportunidade", icon=":material/target:"),
        st.Page("pages/20_Pedidos.py", title="Pedidos", icon=":material/receipt_long:"),
        st.Page(
            "pages/27_Pendencia_x_Estoque.py",
            title="Pendência x Estoque",
            icon=":material/pending_actions:",
        ),
        st.Page("pages/6_Estoque.py", title="Estoque", icon=":material/inventory_2:"),
        st.Page("pages/21_Remessas.py", title="Remessas", icon=":material/local_shipping:"),
        st.Page("pages/22_Faturamento.py", title="Faturamento", icon=":material/payments:"),
        st.Page(
            "pages/7_Credito_Devolucoes.py",
            title="Crédito e Devoluções",
            icon=":material/credit_card:",
        ),
    ],
    "Metas e Performance": [
        st.Page(
            "pages/11_Metas.py",
            title="Faturamento x Meta",
            icon=":material/track_changes:",
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
    "Cadastros": [
        st.Page("pages/25_Cliente_360.py", title="Cliente 360", icon=":material/account_circle:"),
        st.Page("pages/23_Material.py", title="Material", icon=":material/inventory:"),
    ],
    "Faturamento (Painel Vendas)": [
        st.Page(
            "pages/12_Painel_Vendas.py",
            title="Painel Vendas",
            icon=":material/speed:",
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
        st.Page("pages/2_Auditoria.py", title="Auditoria do Fluxo", icon=":material/fact_check:"),
    ],
}

if usuario["role"] == "admin":
    SECTIONS["Administração"] = [
        st.Page("pages/90_Admin.py", title="Admin", icon=":material/admin_panel_settings:")
    ]

# nome da seção -> (ícone, título completo, rótulo curto sob o ícone)
_SECTION_ICONS = {
    "": (":material/dashboard:", "Início", "Início"),
    "Funil de Vendas": (":material/filter_alt:", "Funil de Vendas", "Funil"),
    "Metas e Performance": (":material/track_changes:", "Metas e Performance", "Metas"),
    "Cadastros": (":material/contacts:", "Cadastros", "Cadastro"),
    "Faturamento (Painel Vendas)": (":material/payments:", "Faturamento (Painel Vendas)", "Painel"),
    "Técnico": (":material/build:", "Técnico", "Técnico"),
    "Administração": (":material/admin_panel_settings:", "Administração", "Admin"),
}

pg = st.navigation([p for pages in SECTIONS.values() for p in pages], position="hidden")

# Activity Bar vertical (estilo VSCode, fixa à esquerda via CSS `st-key-bmt-rail`) + painel de
# páginas da seção atual na sidebar — equivalente Streamlit da Activity Bar + Sidebar do
# app_template. A navegação nativa fica oculta. A barra é renderizada no corpo principal (não
# dentro de `st.sidebar`) porque a sidebar tem transform CSS e quebraria o `position: fixed`.
secao_atual = next((nome for nome, pages in SECTIONS.items() if pg in pages), "")
with st.container(key="bmt-rail"):
    for i, (nome, pages) in enumerate(SECTIONS.items()):
        icone, rotulo, curto = _SECTION_ICONS[nome]
        estado = "on" if nome == secao_atual else "off"
        with st.container(key=f"bmt-activity-{estado}-{i}"):
            st.page_link(pages[0], label=curto, icon=icone, help=rotulo)
with st.sidebar:
    st.caption(_SECTION_ICONS[secao_atual][1].upper())
    for pagina in SECTIONS[secao_atual]:
        st.page_link(pagina)
    st.divider()
render_theme_selector()

try:
    pg.run()
except DatabaseConnectionError as exc:
    st.error(str(exc), icon="🔌")
