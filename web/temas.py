"""Temas da interface — os mesmos 4 do app_template/Streamlit (`scripts/ui_theme.THEMES`).

As cores vivem em `static/app.css` (`:root[data-theme="..."]`); aqui só a lista e o padrão.
O tema escolhido fica num cookie do navegador (preferência por pessoa); o padrão de quem
nunca escolheu vem de Admin → Configurações (`default_theme`).
"""

from __future__ import annotations

from scripts import app_db

TEMAS: dict[str, str] = {
    "corporate": "Corporativo",
    "green-neutral": "Verde Neutro",
    "cyber-dark": "Cyber Dark",
    "blau": "Blau (marca)",
}
PADRAO = "corporate"


def tema_padrao() -> str:
    escolhido = app_db.get_setting("default_theme", PADRAO)
    return escolhido if escolhido in TEMAS else PADRAO
