"""Administração → Configurações do app: tema padrão, bloqueio por inatividade e filtros padrão.

Só perfil `admin`. Valores gravados na tabela `settings` do SQLite local (`scripts/app_db.py`).
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Configurações — Invest SAP", page_icon="🛠️", layout="wide")

from scripts import app_db  # noqa: E402
from scripts.auth import require_admin  # noqa: E402
from scripts.ui_theme import THEMES  # noqa: E402

admin = require_admin()

st.title(":material/tune: Configurações do Sistema")
st.caption("Aparência padrão, sessão e filtros aplicados por padrão nas páginas.")

st.subheader("Configurações do app")
with st.form("config"):
    tema = st.selectbox(
        "Tema padrão (novas sessões)", list(THEMES),
        index=list(THEMES).index(app_db.get_setting("default_theme")),
        format_func=lambda k: THEMES[k]["label"],
    )
    idle = st.number_input(
        "Bloqueio por inatividade (minutos)", min_value=1, max_value=480,
        value=int(app_db.get_setting("idle_minutes")),
    )
    zumbi = st.number_input(
        "Pedidos: backlog \"recente\" até quantos dias (padrão do limiar de pedido zumbi)",
        min_value=30, max_value=3650, step=30,
        value=int(app_db.get_setting("limiar_zumbi_dias")),
    )
    excluir_internacional = st.checkbox(
        "Excluir estoque internacional (Uruguai/Colômbia) das métricas por padrão",
        value=bool(app_db.get_setting("excluir_estoque_internacional")),
        help=(
            "Aplica-se às páginas Home e Estoque: tira os centros de Uruguai/Colômbia "
            "(`Pais_Centro` UY/CO) das quantidades agregadas — hoje elas somam junto "
            "mesmo quando o valor financeiro já fica restrito a BRL. Alemanha (DE) "
            "não entra nessa exclusão. Não afeta filtro manual de País na página "
            "Estoque, que continua deixando ver qualquer país específico."
        ),
    )
    excluir_intercompany = st.checkbox(
        "Excluir clientes intercompany (filiais \"BLAU*\") das métricas por padrão",
        value=bool(app_db.get_setting("excluir_intercompany")),
        help=(
            "Aplica-se a Pedidos, Faturamento, Cliente 360, Remessas, Crédito/Devoluções, "
            "Oportunidade e Pendência x Estoque: tira clientes com `Nome_Cliente` contendo "
            "\"BLAU\" (ex.: BLAU FARMACEUTICA COLOMBIA/URUGUAY/CHILE/FILIAL SP) — são "
            "transferência entre filiais do próprio grupo, não cliente comercial, e "
            "concentram boa parte do volume quando entram na conta (achado original: "
            "infla 'NAO ALOCADO' de Linha de Negócio pra ~98% se não excluído)."
        ),
    )
    excluir_org_vendas_internacional = st.checkbox(
        "Excluir Organização de Vendas Colômbia/Uruguai das métricas por padrão",
        value=bool(app_db.get_setting("excluir_org_vendas_internacional")),
        help=(
            "Aplica-se a Faturamento e Pendência x Estoque: tira pendência/faturamento "
            "atribuído às Organizações de Vendas \"Blau Farma Colombia\"/\"Blau Farma "
            "Uruguay\" (`Nome_Org_Vendas`/`Descricao_Org_Vendas`) — diferente de "
            "'Excluir clientes intercompany' acima (esse é por cliente): aqui sai mesmo "
            "quando o cliente final não é filial do grupo, porque a venda foi processada "
            "pela unidade comercial estrangeira, não pela brasileira."
        ),
    )
    excluir_possivel_zumbi = st.checkbox(
        "Excluir possíveis pedidos zumbi do ranking por material por padrão",
        value=bool(app_db.get_setting("excluir_possivel_zumbi")),
        help=(
            "Aplica-se ao ranking \"Materiais sem cobertura de estoque\" em Pendência x "
            "Estoque: tira Material+Centro onde o pedido mais antigo passa o limiar de "
            "backlog \"recente\" acima **e** a reserva SAP (`Qtd_Reservada`) é menor que "
            "a quantidade sem estoque (`Possivel_Zumbi`) — sinal de pedido nunca "
            "baixado/cancelado no SAP, não falta de estoque real. Ver também \"Radar de "
            "pedido zumbi\" na página Pedidos (visão global, não só material sem estoque)."
        ),
    )
    if st.form_submit_button("Salvar", type="primary"):
        app_db.set_setting("default_theme", tema)
        app_db.set_setting("idle_minutes", int(idle))
        app_db.set_setting("limiar_zumbi_dias", int(zumbi))
        app_db.set_setting("excluir_estoque_internacional", bool(excluir_internacional))
        app_db.set_setting("excluir_intercompany", bool(excluir_intercompany))
        app_db.set_setting(
            "excluir_org_vendas_internacional", bool(excluir_org_vendas_internacional)
        )
        app_db.set_setting("excluir_possivel_zumbi", bool(excluir_possivel_zumbi))
        app_db.audit(
            admin["username"], "config_alterada",
            f"{tema}/{idle}/{zumbi}/excluir_internacional={excluir_internacional}"
            f"/excluir_intercompany={excluir_intercompany}"
            f"/excluir_org_vendas_internacional={excluir_org_vendas_internacional}"
            f"/excluir_possivel_zumbi={excluir_possivel_zumbi}",
        )
        st.success("Configurações salvas.")
