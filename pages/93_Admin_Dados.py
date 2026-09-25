"""Administração → Dados de negócio: consulta das tabelas manuais e solicitações de ajuste.

`vendas.dim_cliente_setor`/`dim_estrutura`/`fat_meta_equipe` são alimentadas pelo pipeline
(SharePoint) e a conta do app não tem INSERT nesse schema, então aqui o admin *consulta* essas
tabelas e registra *solicitações de ajuste* exportáveis em CSV pra quem mantém a fonte.
"""

from __future__ import annotations

import csv
import io

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Dados de negócio — Invest SAP", page_icon="🛠️", layout="wide")

from scripts import app_db  # noqa: E402
from scripts.auth import require_admin  # noqa: E402
from scripts.db import DatabaseConnectionError, read_sql  # noqa: E402

admin = require_admin()

st.title(":material/database: Dados de Negócio")
st.caption("Tabelas manuais do DW (só leitura) e solicitações de ajuste para quem mantém a fonte.")

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
