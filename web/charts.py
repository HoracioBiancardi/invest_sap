"""Especificação de gráficos (ECharts) a partir de Series/DataFrame — substitui `st.bar_chart`,
`st.line_chart` e o Altair de `scripts/ui_charts_comercial.py`.

O Python só monta uma especificação compacta (categorias + séries + formato); o estilo fica
todo em `static/app.js` (`montarOpcaoGrafico`), num lugar só: paleta categórica validada
(ordem fixa, CVD-safe nos 4 temas escuros), barras ≤ 24px com ponta arredondada, linha 2px,
grade discreta, legenda só com 2+ séries, tooltip por eixo.

Regra do ecossistema de visualização seguida aqui: **nunca eixo Y duplo**. O antigo gráfico
Meta x Realizado (barras + linha de % no eixo secundário) virou barras Meta/Venda com o %
de atingimento como rótulo direto sobre a barra de Venda.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

import pandas as pd


def _categoria(v: Any) -> str:
    if isinstance(v, (pd.Timestamp, _dt.date)):
        return v.strftime("%d/%m/%Y")
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)


def _valor(v: Any) -> Optional[float]:
    try:
        return None if pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def spec(
    dados: pd.Series | pd.DataFrame,
    tipo: str = "bar",
    horizontal: bool = False,
    stack: bool = False,
    fmt: str = "num0",
    altura: int = 300,
) -> dict[str, Any]:
    """Especificação de gráfico de barras/linhas.

    Mesma convenção do `st.bar_chart`: o índice vira o eixo de categorias e cada coluna do
    DataFrame (ou a Series sozinha) vira uma série.

    Args:
        dados: Series (1 série) ou DataFrame (1 série por coluna).
        tipo: "bar" ou "line".
        horizontal: Barras horizontais (categorias no eixo Y).
        stack: Empilhar as séries.
        fmt: Código de formato dos valores (ver `web/fmt.py`).
        altura: Altura em px.
    """
    df = dados.to_frame() if isinstance(dados, pd.Series) else dados
    categorias = [_categoria(c) for c in df.index]
    series = [
        {"name": str(col if col is not None else "Valor"), "data": [_valor(v) for v in df[col].tolist()]}
        for col in df.columns
    ]
    return {
        "kind": tipo,
        "categories": categorias,
        "series": series,
        "horizontal": horizontal,
        "stack": stack,
        "fmt": fmt,
        "height": altura,
    }


def meta_realizado(df: pd.DataFrame, categoria: str, altura: int = 320) -> dict[str, Any]:
    """Barras Meta x Venda por categoria, com % de atingimento rotulado sobre a Venda.

    Args:
        df: Colunas `categoria`, `Meta_Valor`, `Valor_Realizado` (ordem preservada).
        categoria: Coluna categórica do eixo X.
    """
    base = df[[categoria, "Meta_Valor", "Valor_Realizado"]].copy()
    ating = (base["Valor_Realizado"] / base["Meta_Valor"]).where(base["Meta_Valor"] > 0)
    rotulos = [None if pd.isna(a) else f"{a * 100:.0f}%".replace(".", ",") for a in ating]
    resultado = spec(
        base.set_index(categoria).rename(columns={"Meta_Valor": "Meta", "Valor_Realizado": "Venda"}),
        fmt="brl0",
        altura=altura,
    )
    resultado["series"][1]["labels"] = rotulos
    return resultado
