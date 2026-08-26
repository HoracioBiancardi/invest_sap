"""Gráficos compartilhados pelas páginas de Meta x Realizado —
`pages/11_Metas.py` e `pages/12_Faturamento_vs_Meta.py`.

**Exceção deliberada** à convenção do resto de `scripts/` (só consulta, sem `streamlit`/
`altair`) — mesmo raciocínio de `scripts/ui_filtros_comercial.py`: 2 páginas precisavam do
mesmo gráfico combinado barra+linha, e duplicar o código arriscava as duas divergirem
silenciosamente num ajuste futuro (cor, formato do rótulo, etc.).
"""

from __future__ import annotations

import altair as alt
import pandas as pd


def grafico_meta_realizado(df: pd.DataFrame, categoria: str) -> alt.LayerChart:
    """Barras de Meta x Venda + linha de % Atingimento em eixo secundário (com rótulo),
    no mesmo padrão "Meta / Venda / Atingimento" do Painel Vendas (Power BI) de referência.

    Args:
        df: precisa ter as colunas `categoria`, `Meta_Valor` e `Valor_Realizado` (não como
            índice — `reset_index()` antes de chamar se vieram de um `groupby`).
        categoria: nome da coluna categórica do eixo X (ordem preservada como veio no `df`).
    """
    ordem = df[categoria].tolist()
    df = df.assign(
        Atingimento=lambda d: (d["Valor_Realizado"] / d["Meta_Valor"]).where(d["Meta_Valor"] > 0)
    )

    df_barras = df.melt(
        id_vars=[categoria],
        value_vars=["Meta_Valor", "Valor_Realizado"],
        var_name="Tipo",
        value_name="Valor",
    )
    df_barras["Tipo"] = df_barras["Tipo"].map({"Meta_Valor": "Meta", "Valor_Realizado": "Venda"})

    barras = (
        alt.Chart(df_barras)
        .mark_bar()
        .encode(
            x=alt.X(f"{categoria}:N", title=None, sort=ordem, axis=alt.Axis(labelAngle=-40)),
            xOffset=alt.XOffset("Tipo:N", sort=["Meta", "Venda"]),
            y=alt.Y("Valor:Q", title="R$"),
            color=alt.Color(
                "Tipo:N",
                sort=["Meta", "Venda"],
                scale=alt.Scale(domain=["Meta", "Venda"], range=["#94a3b8", "#2563eb"]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[categoria, "Tipo", alt.Tooltip("Valor:Q", format=",.0f")],
        )
    )

    df_ating = df.dropna(subset=["Atingimento"])
    teto_eixo = max(1.2, float(df_ating["Atingimento"].max() * 1.2)) if not df_ating.empty else 1.2
    linha_base = alt.Chart(df_ating).encode(
        x=alt.X(f"{categoria}:N", sort=ordem),
        y=alt.Y(
            "Atingimento:Q",
            axis=alt.Axis(title="Atingimento", format="%"),
            scale=alt.Scale(domain=[0, teto_eixo]),
        ),
    )
    linha = linha_base.mark_line(color="#f97316", point=True)
    rotulo = linha_base.mark_text(dy=-12, color="#f97316", fontWeight="bold").encode(
        text=alt.Text("Atingimento:Q", format=".0%")
    )

    return alt.layer(barras, linha, rotulo).resolve_scale(y="independent")
