"""Tema visual compartilhado — CSS custom + logo da Blau no topo da sidebar.

**Exceção deliberada** à convenção do resto de `scripts/` (só consulta, sem `streamlit`) —
mesmo raciocínio de `scripts/ui_filtros_comercial.py` e `scripts/ui_charts_comercial.py`:
é visual compartilhado por todas as páginas, então mora num lugar só.

Chamado uma única vez em `app.py`, antes de `st.navigation(...).run()`. CSS injetado via
`st.markdown(unsafe_allow_html=True)` não fica escopado ao container que o gerou — é
global à página HTML inteira —, então uma chamada em `app.py` (que roda em toda
navegação, é o entrypoint) já cobre todas as páginas de `pages/`; não precisa repetir
por página. Os seletores usam `data-testid` (ex.: `stMetric`, `stAlertContainer`,
`stSidebarNavLink`) extraídos do bundle JS da versão instalada do Streamlit
(`.venv/.../streamlit/static/static/js/*.js`) — não são API pública, então podem quebrar
em upgrades de versão; se o visual "voltar ao padrão" depois de um `uv sync`, comece
conferindo se os testids mudaram.

O logo usa `st.logo()`, não um `st.markdown` dentro de `with st.sidebar:` (era a v1 desse
módulo) — o menu de `st.navigation` é renderizado pelo Streamlit num slot fixo que fica
SEMPRE acima de qualquer coisa escrita via `st.sidebar` no script, não importa a ordem no
código (confirmado no bundle JS: o container da sidebar monta cabeçalho → nav → conteúdo do
usuário, nessa ordem estrutural fixa). `st.logo()` é a única API pública que escreve num slot
anterior a esse (o cabeçalho da sidebar, onde fica também o botão de colapsar) — por isso é
o jeito certo de ter uma marca acima do menu, não um hack de CSS `order`.
"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

_CSS = """
<style>
:root {
    --accent: #26B4E9;
    --accent-soft: rgba(38, 180, 233, 0.12);
    --accent-2: #EDB50B;
    --surface: #262B33;
    --surface-border: #3A4149;
}

/* Título principal da página — friso lateral tipo painel de telemetria */
h1 {
    font-weight: 700 !important;
    letter-spacing: -0.02em;
    border-left: 4px solid var(--accent);
    padding-left: 0.7rem !important;
}

/* Cards de métrica (st.metric) */
div[data-testid="stMetric"] {
    background: var(--surface);
    border: 1px solid var(--surface-border);
    border-radius: 12px;
    padding: 0.9rem 1.1rem 0.8rem;
    border-top: 3px solid var(--accent);
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
}
div[data-testid="stMetricLabel"] {
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 0.72rem !important;
    opacity: 0.75;
}
div[data-testid="stMetricValue"] {
    font-size: 1.55rem !important;
    font-weight: 700 !important;
    font-variant-numeric: tabular-nums;
}

/* st.divider() mais discreto, em degradê saindo da cor de destaque */
hr {
    border: none !important;
    height: 1px !important;
    background: linear-gradient(
        90deg, var(--accent) 0%, var(--surface-border) 35%, transparent 100%
    ) !important;
    opacity: 0.6;
    margin: 1.4rem 0 !important;
}

/* Alertas (st.info/warning/success/error) com cantos arredondados */
div[data-testid="stAlertContainer"] {
    border-radius: 10px !important;
}

/* Botões */
div[data-testid="stButton"] > button {
    border-radius: 8px !important;
    font-weight: 600 !important;
    transition: filter 0.15s ease;
}
div[data-testid="stButton"] > button:hover {
    filter: brightness(1.15);
}

/* Tabelas/dataframes: só o arredondado — a borda quem dá é o card (`.st-key-bmt-card-*`)
   que normalmente envolve a tabela; ver função `card()` abaixo. */
div[data-testid="stDataFrame"] {
    border-radius: 8px;
    overflow: hidden;
}
div[data-testid="stDataFrame"] table {
    font-variant-numeric: tabular-nums;
}

/* Painel/card pra gráfico ou tabela (função `card()` abaixo) — cantos arredondados,
   friso de destaque no topo (2 cores, como uma faixa de carenagem de corrida) e um
   acento diagonal no canto (bandeirinha), no espírito telemetria/motorsport do
   blaumotorsport.com.br mas sutil o bastante pra não brigar com o dado. */
div[class*="st-key-bmt-card-"] {
    position: relative;
    overflow: hidden;
    border-radius: 10px !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
div[class*="st-key-bmt-card-"]:hover {
    border-color: var(--accent) !important;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
}
div[class*="st-key-bmt-card-"]::before {
    content: "";
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(
        90deg,
        var(--accent) 0%, var(--accent) 55%,
        var(--accent-2) 55%, var(--accent-2) 68%,
        transparent 68%
    );
    z-index: 2;
}
div[class*="st-key-bmt-card-"]::after {
    content: "";
    position: absolute;
    top: -22px;
    right: -22px;
    width: 44px;
    height: 44px;
    background: var(--accent);
    opacity: 0.14;
    transform: rotate(45deg);
    z-index: 1;
    pointer-events: none;
}

/* Rótulo de card opcional (helper `card_label()`) — texto tipo painel de instrumento */
.bmt-card-label {
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.72rem;
    font-weight: 700;
    opacity: 0.7;
    margin: -0.2rem 0 0.6rem;
}

/* Logo da Blau (st.logo): força maior que o teto do parâmetro size="large" do Streamlit
   (mapeia pra um token de tema pequeno demais pro peso visual que a marca precisa aqui). */
img[data-testid="stLogo"] {
    height: 3.4rem !important;
    max-height: none !important;
    width: auto !important;
}

/* Itens de navegação da sidebar (st.navigation): cantos arredondados no hover/seleção */
div[data-testid="stSidebarNavLink"] {
    border-radius: 8px !important;
}

/* Cabeçalho de seção da navegação (ex.: "📊 Dashboards") */
div[data-testid="stNavSectionHeader"] {
    font-weight: 700 !important;
    opacity: 0.55;
    letter-spacing: 0.03em;
}
</style>
"""


def apply_custom_theme() -> None:
    """Injeta o CSS custom + o logo da Blau no cabeçalho fixo da sidebar (acima do menu).

    Chamar uma vez só, em `app.py`, antes de `st.navigation(...).run()` — ver docstring
    do módulo pro porquê de bastar uma chamada só e por que é `st.logo()`, não
    `st.markdown` dentro de `st.sidebar`.
    """
    st.markdown(_CSS, unsafe_allow_html=True)
    st.logo(
        str(_ASSETS_DIR / "blau_logo.svg"),
        icon_image=str(_ASSETS_DIR / "blau_icon.svg"),
        size="large",
    )


_KEY_SANITIZE = re.compile(r"[^a-zA-Z0-9_-]+")


def card(key: str):
    """Container estilizado como painel pra envolver um gráfico ou uma tabela.

    Uso: `with card("pendencias-aging"): st.bar_chart(...)`. É um `st.container(border=True,
    key=...)` normal — o `key` só precisa ser único dentro do script da página (Streamlit
    não compartilha namespace de `key` entre páginas). O prefixo fixo `bmt-card-` é o que a
    CSS de `apply_custom_theme()` usa pra estilizar só estes containers (via
    `[class*="st-key-bmt-card-"]`, a classe `st-key-<key>` que o Streamlit gera sozinho pra
    todo container/widget com `key=` — não é hack, é o mecanismo documentado do Streamlit
    pra customizar CSS de um elemento específico) e não os demais `st.container`/`st.columns`
    do app.
    """
    slug = _KEY_SANITIZE.sub("-", key).strip("-")
    return st.container(border=True, key=f"bmt-card-{slug}")


def card_label(text: str) -> None:
    """Rótulo pequeno, em caixa alta, pro topo de um `card()` — uso opcional, quando o
    gráfico/tabela dentro do card não já tem um `st.caption`/`st.subheader` explicando o
    que é (evita rótulo duplicado nesses casos — só chamar quando faltar contexto)."""
    st.markdown(f'<p class="bmt-card-label">{text}</p>', unsafe_allow_html=True)
