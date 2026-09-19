"""Administração — usuários, configurações do app e solicitações de ajuste de dados manuais.

Só perfil `admin` (ver `scripts/auth.py::require_admin`). Usuários/configurações/ajustes vivem
no SQLite local do app (`scripts/app_db.py`) — o DW (SQL Server/HANA) continua só leitura:
`vendas.dim_cliente_setor`/`dim_estrutura`/`fat_meta_equipe` são alimentadas pelo pipeline
(SharePoint) e a conta do app não tem INSERT nesse schema, então aqui o admin *consulta* essas
tabelas e registra *solicitações de ajuste* exportáveis em CSV pra quem mantém a fonte.
"""

from __future__ import annotations

import csv
import io

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Administração — Invest SAP", page_icon="🛠️", layout="wide")

from scripts import app_db  # noqa: E402
from scripts.auth import require_admin  # noqa: E402
from scripts.db import DatabaseConnectionError, read_sql  # noqa: E402
from scripts.ui_theme import THEMES  # noqa: E402

admin = require_admin()

st.title(":material/admin_panel_settings: Administração")
st.caption(f"Logado como **{admin['username']}** (admin).")

tab_usuarios, tab_config, tab_dados = st.tabs(["Usuários", "Configurações", "Dados de negócio"])


# ── Usuários ─────────────────────────────────────────────────────────────────
with tab_usuarios:
    usuarios = app_db.listar_usuarios()
    tabela = pd.DataFrame(usuarios)
    for coluna in ("active", "must_change_password"):
        tabela[coluna] = tabela[coluna].map({1: "Sim", 0: "Não"})
    st.dataframe(
        tabela.rename(
            columns={
                "username": "Usuário", "role": "Perfil", "active": "Ativo",
                "must_change_password": "Troca de senha pendente", "created_at": "Criado em (UTC)",
            }
        ).drop(columns=["id"]),
        use_container_width=True, hide_index=True,
    )

    col_novo, col_gerir = st.columns(2)
    with col_novo:
        st.subheader("Novo usuário")
        with st.form("novo_usuario", clear_on_submit=True):
            novo_nome = st.text_input("Usuário")
            novo_role = st.selectbox("Perfil", app_db.ROLES, index=1)
            nova_senha = st.text_input(
                "Senha temporária", type="password", help=f"Mínimo {app_db.MIN_SENHA} caracteres."
            )
            if st.form_submit_button("Criar", type="primary"):
                try:
                    app_db.criar_usuario(novo_nome, nova_senha, role=novo_role)
                except app_db.AppDbError as exc:
                    st.error(str(exc))
                else:
                    app_db.audit(admin["username"], "usuario_criado", f"{novo_nome} ({novo_role})")
                    st.success("Usuário criado — ele deverá trocar a senha no primeiro login.")
                    st.rerun()

    with col_gerir:
        st.subheader("Gerenciar")
        alvo = st.selectbox(
            "Usuário", usuarios, format_func=lambda u: f"{u['username']} ({u['role']})",
            key="admin_alvo",
        )
        if alvo:
            eh_o_proprio = alvo["id"] == admin["id"]
            novo_perfil = st.selectbox(
                "Perfil", app_db.ROLES, index=app_db.ROLES.index(alvo["role"]), key="admin_perfil"
            )
            ativo = st.toggle("Ativo", value=bool(alvo["active"]), key="admin_ativo")
            if st.button("Salvar perfil / ativação"):
                if eh_o_proprio and not ativo:
                    st.error("Você não pode desativar a si mesmo.")
                else:
                    try:
                        app_db.alterar_usuario(alvo["id"], role=novo_perfil, active=ativo)
                    except app_db.AppDbError as exc:
                        st.error(str(exc))
                    else:
                        app_db.audit(
                            admin["username"], "usuario_alterado",
                            f"{alvo['username']}: perfil={novo_perfil} ativo={ativo}",
                        )
                        st.success("Atualizado.")
                        st.rerun()
            if st.button("Redefinir senha (gera temporária)"):
                temporaria = app_db.gerar_senha_temporaria()
                app_db.definir_senha(alvo["id"], temporaria, must_change_password=True)
                app_db.audit(admin["username"], "senha_redefinida", alvo["username"])
                st.warning(f"Senha temporária de **{alvo['username']}** (mostrada só agora):")
                st.code(temporaria)

    with st.expander("Auditoria (últimos 200 eventos)"):
        st.dataframe(pd.DataFrame(app_db.listar_audit()), use_container_width=True, hide_index=True)


# ── Configurações ────────────────────────────────────────────────────────────
with tab_config:
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


# ── Dados de negócio ─────────────────────────────────────────────────────────
# Consultas fixas (sem interpolação) — o único valor de fora entra como bind param.
_FONTES = {
    "Cliente → Setor (vendas.dim_cliente_setor)": (
        "Código do cliente",
        "SELECT TOP 500 * FROM vendas.dim_cliente_setor WHERE cod_cliente = :v ORDER BY periodo DESC",
    ),
    "Estrutura (vendas.dim_estrutura)": (None, "SELECT * FROM vendas.dim_estrutura ORDER BY cod_setor"),
    "Meta (vendas.fat_meta_equipe)": (
        "Código do setor",
        "SELECT TOP 500 * FROM vendas.fat_meta_equipe WHERE cod_setor = :v ORDER BY data_meta DESC",
    ),
}


@st.cache_data(ttl=300, show_spinner="Consultando…")
def _consultar(query: str, valor: str | None) -> pd.DataFrame:
    return read_sql(query, database="GOLD", params={"v": valor} if valor is not None else None)


def _csv_seguro(linhas: list[dict]) -> str:
    """CSV pro Excel: neutraliza células que começam com = + - @ (injeção de fórmula)."""
    def limpa(v):
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    saida = io.StringIO()
    if linhas:
        w = csv.DictWriter(saida, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        for linha in linhas:
            w.writerow({k: limpa(v) for k, v in linha.items()})
    return saida.getvalue()


with tab_dados:
    st.info(
        "Estas tabelas são alimentadas pelo pipeline (SharePoint) e o app só tem acesso de "
        "leitura. Consulte abaixo e registre as correções como **solicitações de ajuste** — "
        "exporte o CSV e envie a quem mantém a fonte.",
        icon=":material/info:",
    )
    st.subheader("Consultar fonte")
    fonte = st.selectbox("Tabela", list(_FONTES))
    rotulo, query = _FONTES[fonte]
    valor = st.text_input(rotulo).strip() if rotulo else None
    if rotulo and not valor:
        st.caption("Informe o valor para consultar.")
    else:
        try:
            st.dataframe(_consultar(query, valor), use_container_width=True, hide_index=True)
        except DatabaseConnectionError as exc:
            st.error(str(exc), icon="🔌")

    st.divider()
    st.subheader("Solicitações de ajuste")
    with st.form("novo_ajuste", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            tipo = st.selectbox("Tipo", app_db.TIPOS_AJUSTE)
            chave = st.text_input("Chave (ex.: código do cliente/setor)")
        with c2:
            v_atual = st.text_input("Valor atual (opcional)")
            v_prop = st.text_input("Valor proposto")
        motivo = st.text_area("Motivo / justificativa", height=80)
        if st.form_submit_button("Registrar solicitação", type="primary"):
            try:
                app_db.criar_ajuste(tipo, chave, v_atual, v_prop, motivo, admin["username"])
            except app_db.AppDbError as exc:
                st.error(str(exc))
            else:
                app_db.audit(admin["username"], "ajuste_criado", f"{tipo}: {chave}")
                st.success("Solicitação registrada.")
                st.rerun()

    filtro = st.radio("Status", ["todos", *app_db.STATUS_AJUSTE], horizontal=True)
    ajustes = app_db.listar_ajustes(None if filtro == "todos" else filtro)
    if ajustes:
        st.dataframe(pd.DataFrame(ajustes), use_container_width=True, hide_index=True)
        col_a, col_b, col_c = st.columns([1, 1, 1])
        with col_a:
            ajuste_id = st.selectbox("Solicitação", [a["id"] for a in ajustes])
        with col_b:
            novo_status = st.selectbox("Novo status", app_db.STATUS_AJUSTE)
        with col_c:
            st.write("")
            if st.button("Atualizar status"):
                app_db.resolver_ajuste(ajuste_id, novo_status)
                app_db.audit(admin["username"], "ajuste_status", f"#{ajuste_id} → {novo_status}")
                st.rerun()
        st.download_button(
            "Exportar CSV", _csv_seguro(ajustes).encode("utf-8-sig"),
            file_name="solicitacoes_ajuste.csv", mime="text/csv",
        )
    else:
        st.caption("Nenhuma solicitação neste filtro.")
