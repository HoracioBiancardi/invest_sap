"""Material — ficha de cadastro (`dim_material_sap`) e catálogo de produto acabado (porte de
`pages/23_Material.py`). Clique numa linha do catálogo abre a ficha."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from scripts.query_vendas_sap import ficha_material, materiais_catalogo
from web import fmt
from web.cache import cached
from web.ui import Ctx, Pagina


@cached(ttl=600, nome="material: catálogo")
def _catalogo(filtro_nome: Optional[str]) -> pd.DataFrame:
    return materiais_catalogo(somente_acabado=True, filtro_nome=filtro_nome)


@cached(ttl=600, nome="material: ficha")
def _ficha(codigo: str) -> pd.DataFrame:
    return ficha_material(codigo)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Material: ficha de cadastro", "inventory")
    p.caption(
        "Dado cadastral do material (`dim_material_sap`) — descrição, tipo, status, unidade de medida, peso. Pra "
        "pedido/estoque/fatura ou fila FIFO por material, veja **Pendência x Estoque** (filtre por Material)."
    )
    # Clique no catálogo preenche o código (equivalente ao callback on_select do Streamlit).
    escolhido = ctx.get("material_sel") or ""
    if escolhido:
        ctx.params["material_codigo"] = [escolhido]
    a, b = p.columns([1, 2])
    codigo = a.text_input("Código do material (opcional)", "material_codigo", placeholder="ex.: PA5522").strip().upper() or None
    nome = b.text_input("Ou filtre o catálogo pelo nome (parcial)", "material_filtro_nome", placeholder="ex.: HEPAMAX").strip() or None
    if codigo:
        p.link("Voltar ao catálogo", ctx.caminho, "arrow_back")
        ficha = _ficha(codigo)
        if ficha.empty:
            p.info("Nenhum material encontrado com esse código.")
            return
        if len(ficha) > 1:
            p.caption(f"{len(ficha)} linhas pra esse código (mais de 1 Mandante) — mostrando todas.")
        for _, linha in ficha.iterrows():
            p.card(f"Mandante {linha.get('Mandante', '')}").table(linha.to_frame(name="Valor").astype(str), indice=True, nome_arquivo=f"ficha_{codigo}")
        return
    p.caption(
        "Catálogo de produto acabado (~1.812 materiais) — clique numa linha pra abrir a ficha completa, ou digite "
        "um código/nome acima."
    )
    catalogo = _catalogo(nome)
    if catalogo.empty:
        p.info("Nenhum material encontrado com esse filtro.")
        return
    p.caption(f"{fmt.num(len(catalogo))} material(is)")
    p.card().table(catalogo, selecao="material_sel", coluna_selecao="Codigo_Produto", nome_arquivo="catalogo_materiais")
