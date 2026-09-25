"""Formatação numérica pt-BR (1.234.567,89) para métricas e textos das páginas.

As tabelas formatam no navegador (`static/app.js`, `Intl.NumberFormat("pt-BR")`) a partir do
mesmo código de formato usado aqui, para ordenar pelo valor cru e exibir formatado:

- `num0`/`num1`/`num2`: número com N casas.
- `brl0`/`brl2`: "R$ " + número.
- `cur2:UYU`: código da moeda + número (moeda estrangeira não leva "R$").
- `pct0`/`pct1`: fração → percentual (0.123 → "12,3%").
- `spct1`: fração com sinal (+12,3%).
- `pctv1`: valor já em percentual (12.3 → "12,3%").
- `date`/`datetime`/`text`/`bool`.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pandas as pd

VAZIO = "—"


def _eh_vazio(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):  # listas/arrays: não são "vazio" escalar
        return False


def num(v: Any, dec: int = 0) -> str:
    """Número pt-BR com `dec` casas (1234.5 → "1.234,5")."""
    if _eh_vazio(v):
        return VAZIO
    texto = f"{float(v):,.{dec}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def brl(v: Any, dec: int = 0) -> str:
    """Valor em reais ("R$ 1.234")."""
    return VAZIO if _eh_vazio(v) else f"R$ {num(v, dec)}"


def moeda(v: Any, codigo: str, dec: int = 2) -> str:
    """Valor numa moeda: BRL vira "R$", as demais levam o código ("UYU 1.234,00")."""
    if _eh_vazio(v):
        return VAZIO
    return brl(v, dec) if codigo == "BRL" else f"{codigo} {num(v, dec)}"


def pct(v: Any, dec: int = 1, sinal: bool = False) -> str:
    """Fração como percentual (0.123 → "12,3%"; `sinal=True` → "+12,3%")."""
    if _eh_vazio(v):
        return VAZIO
    valor = float(v) * 100
    texto = num(abs(valor) if sinal else valor, dec) + "%"
    if sinal:
        texto = ("+" if valor >= 0 else "−") + texto
    return texto


def data(v: Any) -> str:
    """Data dd/mm/aaaa (aceita date, datetime, Timestamp ou string ISO)."""
    if _eh_vazio(v):
        return VAZIO
    if isinstance(v, str):
        try:
            v = _dt.date.fromisoformat(v[:10])
        except ValueError:
            return v
    return v.strftime("%d/%m/%Y")


def formatar(v: Any, codigo: str) -> str:
    """Aplica um código de formato (ver docstring do módulo) no servidor."""
    if _eh_vazio(v):
        return VAZIO
    if codigo.startswith("cur"):
        dec, _, cod = codigo[3:].partition(":")
        return moeda(v, cod or "BRL", int(dec or 2))
    base, dec = codigo.rstrip("0123456789"), codigo[len(codigo.rstrip("0123456789")):]
    casas = int(dec) if dec else 0
    if base == "num":
        return num(v, casas)
    if base == "brl":
        return brl(v, casas)
    if base == "pct":
        return pct(v, casas)
    if base == "spct":
        return pct(v, casas, sinal=True)
    if base == "pctv":
        return num(v, casas) + "%"
    if base in ("date", "datetime"):
        return data(v)
    if base == "bool":
        return "Sim" if v else "Não"
    return str(v)
