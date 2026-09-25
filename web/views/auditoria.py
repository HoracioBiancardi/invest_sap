"""Auditoria do Fluxo — checagens genéricas de anomalia em todo o fluxo (porte de
`pages/2_Auditoria.py`); ver `docs/COMO_RODAR.md` §8. Cada checagem roda num bloco próprio,
em paralelo, e o resultado fica 10 min no cache."""

from __future__ import annotations

import pandas as pd

from scripts.audit_pendencia_flow import CHECKS as CHECKS_PENDENCIA
from scripts.query_vendas_sap import auditoria_linha_negocio_rh_vs_estrutura
from web.cache import cached
from web.ui import Ctx, Node, Pagina

CHECKS = {**CHECKS_PENDENCIA, "linha_negocio_rh_vs_estrutura": auditoria_linha_negocio_rh_vs_estrutura}

DESCRICOES = {
    "valor_sem_quantidade": "Linha com valor > 0 e quantidade = 0 (o padrão do bug KWMENG/ZMENG).",
    "pendencia_escondida": "'Concluido' com valor > 0 mas zero remessa e zero fatura — sintoma genérico.",
    "reconciliacao_contagem": "Contagem SAP cru (HANA) vs Gold, por tipo de pedido — detecta join quebrado.",
    "integridade_dimensoes": "% de linhas com join de dimensão falho (cliente/centro/produto sem nome).",
    "linha_negocio_rh_vs_estrutura": (
        "Cliente onde a Unidade de Negócio do vendedor (RH) discorda da Linha de Negócio manual (dim_estrutura) "
        "— candidato a rótulo desatualizado, não certeza de erro."
    ),
}


@cached(ttl=600, nome="auditoria: checagem")
def _rodar(nome: str):
    return CHECKS[nome]()


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Auditoria do Fluxo", "fact_check")
    p.caption("Varre o fluxo inteiro (não um pedido específico) procurando padrões de anomalia — ver `docs/COMO_RODAR.md` §8.")
    selecionadas = p.multiselect(
        "Checagens a rodar", list(CHECKS), "aud_checks", default=list(CHECKS),
        formatar=lambda nome: f"{nome} — {DESCRICOES.get(nome, '')}",
    )
    rodar = p.button("Rodar auditoria", "aud_rodar", primario=True, desabilitado=not selecionadas, icone_nome="play_arrow")
    if not rodar:
        p.caption('Selecione as checagens e clique em "Rodar auditoria".')
        return
    for nome in selecionadas:
        p.subheader(nome)
        p.caption(DESCRICOES.get(nome, ""))
        p.lazy("checagem", altura=140, nome=nome)
        p.divider()


def _bloco_checagem(p: Node, ctx: Ctx) -> None:
    nome = ctx.get("nome") or ""
    if nome not in CHECKS:
        p.error("Checagem desconhecida.")
        return
    resultado = _rodar(nome)
    secoes = resultado.items() if isinstance(resultado, dict) else [(nome, resultado)]
    for titulo, df in secoes:
        if isinstance(resultado, dict):
            p.markdown(f"**{titulo}**")
        if not isinstance(df, pd.DataFrame) or df.empty:
            p.info("Nenhuma anomalia encontrada.")
        else:
            p.card().table(df, nome_arquivo=f"auditoria_{nome}")


BLOCOS = {"checagem": _bloco_checagem}
