"""Estoque — restrito x disponível, validade dos lotes e rastreio de lote (porte de
`pages/6_Estoque.py`). Fonte `GOLD.vendas_sap.fct_estoque_lote_sap` (foto de agora, sem
dimensão de cliente/data) + `SILVER.dataspherev2.mseg`/`mkpf` no rastreio.

Diferença do Streamlit: só a aba aberta consulta o DW (antes as 3 rodavam a cada interação).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from markupsafe import Markup

from scripts.query_vendas_sap import estoque_restrito_disponivel, estoque_validade, estoque_validade_resumo
from scripts.trace_lote import trace_lote
from web import fmt
from web.cache import cached
from web.ui import Ctx, Node, Pagina
from web.views._comum import config

PAISES = {"Brasil": "BR", "Uruguai": "UY", "Colômbia": "CO", "Alemanha": "DE"}
MOEDA_POR_PAIS = {"BR": "BRL", "UY": "UYU", "CO": "COP", "DE": "EUR"}
FAIXAS_VALIDADE = ["Vencido", "0-30 dias", "31-90 dias", "91-180 dias", "180+ dias"]


@cached(ttl=300, nome="estoque: restrito x disponível")
def _estoque(centro: Optional[str], material: Optional[str], acabado: Optional[bool], pais: Optional[str], excl_int: bool) -> pd.DataFrame:
    return estoque_restrito_disponivel(
        codigo_centro=centro, codigo_material=material, produto_acabado=acabado, pais_centro=pais,
        limit=2000, excluir_paises_internacionais=excl_int,
    )


@cached(ttl=300, nome="estoque: validade (resumo)")
def _validade_resumo(centro: Optional[str], acabado: Optional[bool], pais: Optional[str], excl_int: bool) -> pd.DataFrame:
    return estoque_validade_resumo(codigo_centro=centro, produto_acabado=acabado, pais_centro=pais, excluir_paises_internacionais=excl_int)


@cached(ttl=300, nome="estoque: validade (lotes)")
def _validade_detalhe(centro: Optional[str], acabado: Optional[bool], pais: Optional[str], excl_int: bool) -> pd.DataFrame:
    return estoque_validade(codigo_centro=centro, produto_acabado=acabado, pais_centro=pais, limit=2000, excluir_paises_internacionais=excl_int)


@cached(ttl=600, nome="estoque: rastreio de lote (MSEG)")
def _rastreio(material: str, lote: str) -> pd.DataFrame:
    return trace_lote(material, lote)


def render(p: Pagina, ctx: Ctx) -> None:
    p.title("Estoque", "inventory_2")
    p.caption("Fonte: `GOLD.vendas_sap.fct_estoque_lote_sap` — foto de agora, sem dimensão de cliente/data.")
    c1, c2, c3, c4 = p.columns(4)
    pais_opcao = c1.selectbox(
        "País", ["Todos"] + list(PAISES), "est_pais", default="Brasil",
        help=(
            "Default Brasil (BRL, o grosso do estoque). Uruguai/Colômbia valoram em moeda local (UYU/COP), "
            "sem conversão. 'Todos' mistura os países nas quantidades, mas o valor financeiro nos totais "
            "fica restrito a BRL pra não somar moeda errado."
        ),
    )
    centro = c2.text_input("Centro específico (opcional)", "est_centro", placeholder="ex.: 1100").strip() or None
    material = c3.text_input("Material específico (opcional)", "est_material", placeholder="ex.: PA5522").strip().upper() or None
    acabado_opcao = c4.selectbox(
        "Produto", ["Todos", "Acabado", "Não Acabado"], "est_acabado", default="Acabado",
        help="'Acabado' = ZFER (Produto Terminado) e ZPFA (Prod. Terminado IFA Biotech); 'Não Acabado' = o resto.",
    )
    acabado = {"Todos": None, "Acabado": True, "Não Acabado": False}[acabado_opcao]
    pais = PAISES.get(pais_opcao)
    excl_int = config("excluir_estoque_internacional")
    if excl_int and not pais:
        p.caption("Config do Admin: estoque de Uruguai/Colômbia excluído das quantidades (selecione o País pra ver eles).")

    abas = p.tabs(["Restrito x Disponível", "Validade dos lotes", "Rastreio de Lote"], "aba")
    if abas.ativa == 0:
        _aba_restrito(abas.corpo, centro, material, acabado, pais, excl_int)
    elif abas.ativa == 1:
        _aba_validade(abas.corpo, centro, acabado, pais, excl_int)
    else:
        _aba_rastreio(abas.corpo)


def _aba_restrito(p: Node, centro, material, acabado, pais, excl_int) -> None:
    df = _estoque(centro, material, acabado, pais, excl_int)
    if df.empty:
        p.info("Nada encontrado para esse filtro.")
        return
    if pais:
        df_valor, moeda = df, df["Moeda"].iloc[0]  # 1 país = 1 moeda
    else:
        df_valor, moeda = df[df["Moeda"] == "BRL"], "BRL"  # "Todos": só BRL nos totais financeiros
    valor_fora = df.loc[df["Moeda"] != moeda, "Valor_Financeiro_Estoque"].sum() if not pais else 0
    p.metrics(
        [
            ("Qtd Física Total", fmt.num(df["Qtd_Fisico_Total"].sum()), None,
             "Tudo que existe fisicamente no depósito: Disponível + Qualidade + Bloqueado + Transferência."),
            ("Qtd Disponível p/ Venda", fmt.num(df["Qtd_Disponivel_Venda"].sum()), None,
             "Já passou por todas as checagens — pode ser alocado num pedido agora."),
            ("Qtd em Qualidade", fmt.num(df["Qtd_Qualidade"].sum()), None,
             "Lote aguardando laudo/liberação do controle de qualidade — etapa normal do processo."),
            ("Qtd Bloqueada", fmt.num(df["Qtd_Bloqueado"].sum()), None,
             "Travado manualmente por um motivo específico (ex.: suspeita de desvio, quarentena)."),
            (f"Valor Financeiro Total ({moeda})", fmt.moeda(df_valor["Valor_Financeiro_Estoque"].sum(), moeda)),
        ]
    )
    if valor_fora:
        p.caption(
            f"+ {fmt.num(valor_fora, 2)} em moeda(s) local(is) de outros países (fora de {moeda}), não somado "
            "acima — ver coluna `Moeda` no detalhe, ou filtre por País."
        )
    p.divider()
    card = p.card()
    a, b = card.columns(2)
    a.subheader("Disponível x Qualidade x Bloqueado, por Centro")
    a.bar_chart(df.groupby("Nome_Centro")[["Qtd_Disponivel_Venda", "Qtd_Qualidade", "Qtd_Bloqueado"]].sum(), stack=True, horizontal=True,
                altura=max(240, 34 * df["Nome_Centro"].nunique() + 70))
    b.subheader(f"Top materiais por valor financeiro em estoque ({moeda})")
    top = df_valor.nlargest(15, "Valor_Financeiro_Estoque").set_index("Descricao_Material")["Valor_Financeiro_Estoque"]
    b.bar_chart(top.rename(f"Valor ({moeda})"), horizontal=True, fmt_valor="brl0" if moeda == "BRL" else "num0", altura=max(240, 26 * len(top) + 40))

    p.subheader("Acabado x Não Acabado")
    card = p.card()
    e, f = card.columns(2)
    e.bar_chart(df.groupby("Produto_Acabado")[["Qtd_Disponivel_Venda", "Qtd_Qualidade", "Qtd_Bloqueado"]].sum())
    f.table(df_valor.groupby("Produto_Acabado")["Valor_Financeiro_Estoque"].sum().to_frame(),
            {"Valor_Financeiro_Estoque": f"cur2:{moeda}"}, indice=True)

    p.subheader("Status do Material")
    p.caption(
        "`ATIVO` x `MARCADO PARA EXCLUSAO` (material marcado pra sair de linha no SAP) — estoque parado de "
        "material já sinalizado pra descontinuar provavelmente não vai girar."
    )
    resumo_status = df.groupby("Status_Material")[["Qtd_Fisico_Total"]].sum()
    card = p.card()
    i, j = card.columns(2)
    i.bar_chart(resumo_status)
    j.table(df_valor.groupby("Status_Material")["Valor_Financeiro_Estoque"].sum().to_frame(),
            {"Valor_Financeiro_Estoque": f"cur2:{moeda}"}, indice=True)
    if "MARCADO PARA EXCLUSAO" in resumo_status.index:
        qtd = resumo_status.loc["MARCADO PARA EXCLUSAO", "Qtd_Fisico_Total"]
        p.warning(
            f"**{fmt.num(qtd)} unidades em estoque de material já marcado pra exclusão** no cadastro SAP — "
            "candidato a write-off/descarte, vale investigar com o time de materiais/qualidade."
        )
    exp = p.expander("Bloqueio de material (Descricao_Status_Global_Material)")
    exp.caption(
        "Diferente de Qtd_Bloqueado (lote específico): é o material inteiro bloqueado no cadastro "
        "(ex.: pra suprimento/depósito ou roteiro)."
    )
    exp.table(df.groupby("Descricao_Status_Global_Material")["Qtd_Fisico_Total"].sum().sort_values(ascending=False).to_frame(), indice=True)

    p.divider()
    p.subheader(f"Detalhe ({fmt.num(len(df))} linhas)")
    colunas = [
        "Codigo_Material", "Descricao_Material", "Produto_Acabado", "Descricao_Tipo_Material",
        "Status_Material", "Descricao_Status_Global_Material", "Codigo_Centro", "Nome_Centro", "Pais_Centro", "Moeda",
        "Qtd_Disponivel_Venda", "Qtd_Qualidade", "Qtd_Bloqueado", "Qtd_Transferencia", "Qtd_Reservada",
        "Qtd_Fisico_Total", "Valor_Financeiro_Estoque",
    ]
    p.card().table(df[colunas], {"Valor_Financeiro_Estoque": "num2"}, nome_arquivo="estoque_detalhe")


def _aba_validade(p: Node, centro, acabado, pais, excl_int) -> None:
    p.caption(
        "Lotes sem `Data_Validade` (comum em embalagem/material de manutenção) e o sentinela SAP "
        "`2999-12-31` (\"sem vencimento definido\") ficam de fora."
    )
    resumo = _validade_resumo(centro, acabado, pais, excl_int)
    if resumo.empty:
        p.info("Nenhum lote com validade cadastrada nesse filtro.")
        return
    moeda = MOEDA_POR_PAIS.get(pais, "BRL") if pais else "BRL"
    resumo = resumo.set_index("Faixa_Validade").reindex(FAIXAS_VALIDADE).fillna(0)
    vencido = resumo.loc["Vencido", "Valor_Financeiro_Estoque"]
    total = resumo["Valor_Financeiro_Estoque"].sum()
    pct = vencido / total if total else 0
    p.metrics(
        [
            (f"Valor Vencido ({moeda})", fmt.moeda(vencido, moeda), None, "Lotes com Data_Validade no passado."),
            ("% do valor total vencido", fmt.pct(pct, 1)),
            ("Lotes vencidos", fmt.num(int(resumo.loc["Vencido", "Qtd_Lotes"]))),
        ]
    )
    if pct >= 0.1:
        p.warning(f"**{fmt.pct(pct, 1)} do valor em estoque nesse filtro está vencido** ({fmt.moeda(vencido, moeda)}).")
    p.divider()
    card = p.card()
    g, h = card.columns(2)
    g.subheader("Valor por faixa de validade")
    g.bar_chart(resumo["Valor_Financeiro_Estoque"].rename(f"Valor ({moeda})"), fmt_valor="brl0" if moeda == "BRL" else "num0")
    h.table(resumo[["Qtd_Lotes", "Qtd_Fisico_Total", "Valor_Financeiro_Estoque"]],
            {"Valor_Financeiro_Estoque": f"cur2:{moeda}", "Qtd_Lotes": "num0", "Qtd_Fisico_Total": "num0"}, indice=True)
    p.divider()
    p.subheader("Lotes mais urgentes (vencidos ou vencendo antes)")
    p.caption("Coluna `Moeda`: filtre por País acima pra garantir que o valor exibido é 1 moeda só.")
    detalhe = _validade_detalhe(centro, acabado, pais, excl_int)
    colunas = [
        "Codigo_Material", "Descricao_Material", "Status_Material", "Numero_Lote", "Codigo_Centro", "Nome_Centro", "Moeda",
        "Data_Producao", "Data_Validade", "Dias_Para_Vencer", "Faixa_Validade", "Qtd_Estoque_Fisico_Total", "Valor_Financeiro_Estoque",
    ]
    p.card().table(detalhe[colunas], {"Valor_Financeiro_Estoque": "num2", "Data_Producao": "date", "Data_Validade": "date"}, nome_arquivo="lotes_validade")


def _aba_rastreio(p: Node) -> None:
    p.caption(
        "Fonte: `SILVER.dataspherev2.mseg`/`mkpf` (documento de material do SAP) — histórico de movimentos "
        "por lote (produção → transferência → liberação de qualidade → venda), que `fct_estoque_lote_sap` não guarda."
    )
    a, b, c = p.columns([2, 2, 1])
    material = a.text_input("Código do material", "rastreio_material", placeholder="ex.: PA5522").strip().upper()
    lote = b.text_input("Número do lote", "rastreio_lote", placeholder="ex.: 24101103").strip()
    c.html(Markup('<div class="campo-rotulo">&nbsp;</div>'))
    rastrear = c.button("Rastrear", "rastrear_lote", primario=True, desabilitado=not (material and lote), icone_nome="search")
    if not (rastrear and material and lote):
        p.caption('Digite código do material e número do lote, e clique em "Rastrear".')
        return
    movimentos = _rastreio(material, lote)
    if movimentos.empty:
        p.info("Nenhum movimento encontrado pra esse material+lote em MSEG/MKPF.")
        return
    p.caption(
        f"{fmt.num(len(movimentos))} movimento(s) — do mais antigo ao mais recente. `Direcao` vem de "
        "`Debito_Credito` (S=Entrada, H=Saída); `Descricao_Estoque` é o tipo de estoque daquela linha."
    )
    colunas = [
        "Data_Lancamento", "Descricao_Movimento", "Direcao", "Quantidade", "Unidade", "Codigo_Centro", "Codigo_Deposito",
        "Descricao_Estoque", "Centro_Destino", "Deposito_Destino", "Lote_Destino", "Pedido_Venda", "Usuario", "Numero_Documento",
    ]
    p.card().table(movimentos[colunas], nome_arquivo=f"rastreio_{material}_{lote}")
