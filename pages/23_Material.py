"""Página: Material — pedido + estoque + fatura + simulação FIFO, por 1 material.

Extraída de pages/1_Pendencias.py ("Detalhe por material"). Reusa
scripts/query_vendas_sap.py::pedido_estoque_fatura_material/alocacao_virtual_fifo. Página
"dona" da busca por material — outras páginas (ex.: Pedidos) não duplicam essa busca, só
referenciam esta.

Sem informar material, mostra um ranking global (todos os Material+Centro com estoque),
ordenado por maior descoberto (backlog > disponível) — reusa
scripts/query_vendas_sap.py::estoque_restrito_disponivel/pendencias_abertas, sem consulta
nova própria pra esse modo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.query_vendas_sap import (  # noqa: E402
    alocacao_virtual_fifo,
    estoque_restrito_disponivel,
    pedido_estoque_fatura_material,
    pendencias_abertas,
)
from scripts.ui_theme import card  # noqa: E402

# Cores de fundo por Status_Pendencia (fct_pendencia_sap) — vermelho pra travado nos dois
# lados (logístico + fiscal), amarelo pra travado só de 1 lado, verde pra concluído.
_CORES_STATUS_PENDENCIA = {
    "Concluido": "background-color: rgba(34, 197, 94, 0.18)",
    "Pendente Logistico e Fiscal": "background-color: rgba(239, 68, 68, 0.18)",
    "Pendente Logistico (Remessa)": "background-color: rgba(245, 158, 11, 0.18)",
    "Pendente Fiscal (Faturamento)": "background-color: rgba(245, 158, 11, 0.18)",
}


def _destacar_status_pendencia(valor: str) -> str:
    return _CORES_STATUS_PENDENCIA.get(valor, "")


def _destacar_credito_negativo(valor: float) -> str:
    if pd.isna(valor) or valor >= 0:
        return ""
    return "background-color: rgba(239, 68, 68, 0.18); font-weight: 600"


st.set_page_config(page_title="Material — Vendas SAP", page_icon="🧪", layout="wide")
st.title(":material/inventory: Material: pedido, estoque e fatura")
st.caption(
    "Informe um código de material pra ver pedido, estoque e faturamento juntos, item a "
    "item — sem precisar cruzar as páginas de Pedidos, Estoque e Faturamento na mão."
)

_cache = st.cache_data(ttl=300)
_fifo_cached = _cache(alocacao_virtual_fifo)
_detalhe_material_cached = _cache(pedido_estoque_fatura_material)
_estoque_geral_cached = _cache(estoque_restrito_disponivel)
_backlog_geral_cached = _cache(pendencias_abertas)

codigo_produto_detalhe = (
    st.text_input("Código do material", placeholder="ex.: PA5522", key="material_codigo").strip().upper() or None
)

if codigo_produto_detalhe:
    tab_pedido, tab_fifo = st.tabs(["Pedido + Estoque + Fatura", "Simulação FIFO"])

    with tab_pedido:
        somente_pendente = st.checkbox(
            "Somente pendências em aberto", value=True, key="material_somente_pendente"
        )
        df_detalhe = _detalhe_material_cached(
            codigo_produto_detalhe, somente_pendente=somente_pendente
        )
        with card("material-detalhe"):
            if df_detalhe.empty:
                st.info("Nenhum pedido encontrado para esse material com esse filtro.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Itens de pedido", f"{len(df_detalhe):,}")
                c2.metric("Qtd pendente total", f"{df_detalhe['Qtd_Pendente_Operacional'].sum():,.0f}")
                c3.metric(
                    "Estoque disponível corrigido (soma por centro)",
                    f"{df_detalhe.drop_duplicates('Codigo_Centro')['Estoque_Disponivel_Corrigido'].sum():,.0f}",
                    help=(
                        "Somado 1x por centro (não por pedido) — cada linha de pedido "
                        "repete o mesmo estoque do seu centro."
                    ),
                )
                st.caption(
                    "`Qtd_Estoque_Disponivel_Venda_Original` vem de `fct_pendencia_sap` e pode "
                    "estar zerada por um bug de cálculo em `fct_estoque_lote_sap` (reservado da "
                    "planta subtraído do livre de cada lote, não do total) — use "
                    "`Estoque_Disponivel_Corrigido` para decidir o que dá pra faturar."
                )

                st.markdown("**Situação do pedido (`Status_Pendencia`)**")
                st.caption(
                    "`Pendente Logistico e Fiscal` = falta remessa **e** falta fatura — normal "
                    "em pedido recém-incluído (SAP ainda não rodou a criação de remessa); vira "
                    "sinal de alerta se o pedido já é antigo (ver `Dias_Desde_Inclusao_Pedido`) "
                    "e mesmo assim continua nesse status com estoque disponível."
                )
                resumo_status = (
                    df_detalhe.groupby("Status_Pendencia")
                    .agg(
                        Itens_Pedido=("Numero_Pedido", "count"),
                        Qtd_Pendente_Total=("Qtd_Pendente_Operacional", "sum"),
                        Valor_Pendente_Total=("Valor_Pendente_Faturamento", "sum"),
                    )
                    .sort_values("Valor_Pendente_Total", ascending=False)
                )
                st.dataframe(resumo_status, width="stretch")

                if (df_detalhe["Cliente_Bloqueado"] == 1).any() or (df_detalhe["Valor_Credito_Disponivel"] < 0).any():
                    st.warning(
                        "Há pedido(s) nesse material de cliente **sem limite de crédito "
                        "disponível** (ou marcado bloqueado) — linhas destacadas em vermelho "
                        "na coluna `Valor_Credito_Disponivel` abaixo. Pode estar segurando a "
                        "liberação da remessa mesmo com estoque disponível; confirmar bloqueio "
                        "de crédito ativo no SAP (VKM3/VKM4)."
                    )

                colunas_moeda = [
                    "Valor_Liquido_Pedido",
                    "Valor_Liquido_Faturado",
                    "Valor_Pendente_Faturamento",
                    "Valor_Credito_Disponivel",
                ]
                styled_detalhe = df_detalhe.style.map(
                    _destacar_status_pendencia, subset=["Status_Pendencia"]
                ).map(_destacar_credito_negativo, subset=["Valor_Credito_Disponivel"]).format(
                    {c: "R$ {:,.2f}".format for c in colunas_moeda}
                )
                st.dataframe(styled_detalhe, width="stretch", hide_index=True)

    with tab_fifo:
        codigo_centro_fifo = (
            st.text_input("Centro (opcional)", placeholder="ex.: 1100", key="material_fifo_centro").strip() or None
        )
        df_fifo = _fifo_cached(codigo_centro=codigo_centro_fifo, codigo_produto=codigo_produto_detalhe)
        with card("material-fifo"):
            if df_fifo.empty:
                st.info("Nenhum item pendente encontrado para esse filtro.")
            else:
                qtd_sem_estoque = df_fifo.loc[
                    df_fifo["Status_Alocacao_Virtual"] == "SEM ESTOQUE", "Qtd_Pendente_Operacional"
                ].sum()
                c1, c2, c3 = st.columns(3)
                c1.metric("Itens de pedido na fila", f"{len(df_fifo):,}")
                c2.metric("Qtd pendente total", f"{df_fifo['Qtd_Pendente_Operacional'].sum():,.0f}")
                c3.metric("Qtd sem cobertura (SEM ESTOQUE)", f"{qtd_sem_estoque:,.0f}")
                st.dataframe(
                    df_fifo.sort_values(["Codigo_Centro", "Posicao_Fila_Prioridade"]),
                    width="stretch",
                    hide_index=True,
                )
else:
    st.caption(
        "Sem informar 1 material, mostra o panorama de todos: estoque disponível "
        "(`Qtd_Disponivel_Venda`, `fct_estoque_lote_sap`) cruzado com o backlog aberto "
        "(`fct_pendencia_sap`), por Material+Centro — ordenado pelo maior descoberto "
        "(backlog maior que o disponível). Digite um código acima pra abrir o detalhe "
        "de 1 material, com pedido item a item e simulação FIFO."
    )

    df_estoque_geral = _estoque_geral_cached(limit=10000)
    df_backlog_geral = _backlog_geral_cached()

    if df_estoque_geral.empty:
        st.info("Nenhum estoque encontrado.")
    else:
        if df_backlog_geral.empty:
            df_backlog_mat_centro = pd.DataFrame(
                columns=["Codigo_Material", "Codigo_Centro", "Qtd_Pendente", "Valor_Pendente"]
            )
        else:
            df_backlog_mat_centro = (
                df_backlog_geral.groupby(["Codigo_Produto", "Codigo_Centro"])
                .agg(
                    Qtd_Pendente=("Qtd_Pendente_Operacional", "sum"),
                    Valor_Pendente=("Valor_Pendente_Faturamento", "sum"),
                )
                .reset_index()
                .rename(columns={"Codigo_Produto": "Codigo_Material"})
            )

        df_geral = df_estoque_geral.merge(
            df_backlog_mat_centro, on=["Codigo_Material", "Codigo_Centro"], how="left"
        )
        df_geral[["Qtd_Pendente", "Valor_Pendente"]] = df_geral[["Qtd_Pendente", "Valor_Pendente"]].fillna(0)
        df_geral["Descoberto"] = df_geral["Qtd_Pendente"] - df_geral["Qtd_Disponivel_Venda"]

        materiais_com_descoberto = int((df_geral["Descoberto"] > 0).sum())
        with card("material-geral-kpi"):
            k1, k2, k3 = st.columns(3)
            k1.metric("Combinações Material+Centro com estoque", f"{len(df_geral):,}")
            k2.metric("Com descoberto (backlog > disponível)", f"{materiais_com_descoberto:,}")
            k3.metric(
                "Valor pendente nesses casos",
                f"R$ {df_geral.loc[df_geral['Descoberto'] > 0, 'Valor_Pendente'].sum():,.0f}",
            )

        st.markdown("**Ranking por maior descoberto (backlog - disponível)**")
        st.caption(
            "`Qtd_Disponivel_Venda` reflete o fix aplicado em `fct_estoque_lote_sap` em "
            "2026-09-03 (antes zerava por bug de cálculo por lote) — mas ainda não está "
            "mergeado em `main` do data-platform, então pode voltar a zerar no próximo "
            "run agendado do Airflow até a PR ser mergeada."
        )
        df_ranking_geral = df_geral[df_geral["Descoberto"] > 0].sort_values("Descoberto", ascending=False)
        n_ranking_geral = st.slider(
            "Quantos materiais mostrar", min_value=5, max_value=200, value=30, step=5, key="material_geral_top_n"
        )
        colunas_ranking_geral = [
            "Codigo_Material", "Descricao_Material", "Codigo_Centro", "Nome_Centro",
            "Qtd_Disponivel_Venda", "Qtd_Pendente", "Descoberto", "Valor_Pendente",
            "Status_Material",
        ]
        with card("material-geral-ranking"):
            st.dataframe(
                df_ranking_geral.head(n_ranking_geral)[colunas_ranking_geral].style.format(
                    {
                        "Qtd_Disponivel_Venda": "{:,.0f}",
                        "Qtd_Pendente": "{:,.0f}",
                        "Descoberto": "{:,.0f}",
                        "Valor_Pendente": "R$ {:,.2f}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
        st.caption(
            "Pra abrir o detalhe pedido a pedido de qualquer material da lista, copie o "
            "`Codigo_Material` e cole no campo acima."
        )
