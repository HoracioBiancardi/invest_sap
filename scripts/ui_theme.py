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

import datetime as _dt
import html
import re
from pathlib import Path

import streamlit as st

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

_CSS = """
<style>
/* Tokens (--primary, --surface, --border...) vêm de `_theme_css()` — este bloco só usa
   variáveis, então acompanha o tema escolhido. Padrão visual: app_template / input_arquivos. */

/* ── Tipografia de página ─────────────────────────────────────────────────
   Título de página compacto (text-xl bold) + legenda discreta, como no input_arquivos. */
.stApp h1 {
    font-size: 1.55rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em;
    padding: 0 0 .15rem !important;
}
.stApp h1 [data-testid="stIconMaterial"] { color: var(--primary); font-size: 1.45rem !important; }
.stApp h2 { font-size: 1.2rem !important; font-weight: 700 !important; padding-top: .9rem !important; }
.stApp h3 { font-size: 1.02rem !important; font-weight: 600 !important; }
.stApp h2 code, .stApp h3 code { font-size: .8em; }
/* Rótulos de campo no estilo .form-label (maiúsculo, pequeno, espaçado). Só os <label> —
   o rótulo de checkbox/toggle é um <div> e continua em caixa normal, como no template. */
label[data-testid="stWidgetLabel"] p {
    font-size: .72rem !important; font-weight: 700; letter-spacing: .07em;
    text-transform: uppercase; color: var(--text-muted) !important;
}

/* ── Métricas (st.metric) — card .surface-card com hover-lift ──────────── */
div[data-testid="stMetric"] {
    background: var(--surface-alt);
    border: 1px solid var(--border);
    border-radius: var(--r-lg);
    padding: .85rem 1rem .8rem;
    box-shadow: var(--shadow-sm);
    transition: transform .15s ease, border-color .15s ease;
}
div[data-testid="stMetric"]:hover { transform: translateY(-2px); border-color: var(--border-bright); }
div[data-testid="stMetricLabel"] p {
    font-size: .7rem !important; font-weight: 700; letter-spacing: .06em;
    text-transform: uppercase; color: var(--text-muted) !important;
}
div[data-testid="stMetricValue"] {
    font-size: 1.3rem !important;
    font-weight: 700 !important;
    font-variant-numeric: tabular-nums;
}
/* Valor/rótulo longo quebra linha em vez de virar "R$ 1,868,166,7..." */
div[data-testid="stMetricValue"], div[data-testid="stMetricValue"] *,
div[data-testid="stMetricLabel"], div[data-testid="stMetricLabel"] * {
    white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
    overflow-wrap: anywhere;
}

/* ── Divisor ─────────────────────────────────────────────────────────── */
hr {
    border: none !important;
    height: 1px !important;
    background: linear-gradient(90deg, var(--border-bright) 0%, var(--border) 40%, transparent 100%) !important;
    margin: 1.4rem 0 !important;
}

/* ── Alertas — .page-alert do input_arquivos: fundo escuro + borda/texto coloridos ── */
div[data-testid="stAlertContainer"] {
    border-radius: var(--r-md) !important;
    border: 1px solid transparent !important;
    font-size: .875rem;
}
div[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) {
    background: #0c1f3f !important; border-color: rgba(56, 139, 253, .28) !important; color: #79b8ff !important;
}
div[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {
    background: #291e0b !important; border-color: rgba(245, 158, 11, .28) !important; color: #e3b341 !important;
}
div[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) {
    background: #0d2a1a !important; border-color: rgba(34, 197, 94, .28) !important; color: #4ade80 !important;
}
div[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {
    background: #2d0b0b !important; border-color: rgba(239, 68, 68, .28) !important; color: #f87171 !important;
}
div[data-testid="stAlertContainer"] p, div[data-testid="stAlertContainer"] strong { color: inherit !important; }

/* ── Indicador "Running/Stop" do Streamlit: sai da topbar (lá ficam as ações do usuário)
   e vira um selo flutuante no canto inferior direito, como o toast do template. ── */
[data-testid="stStatusWidget"] {
    position: fixed !important; bottom: 1rem; right: 1rem; top: auto !important; z-index: 999999;
    background: var(--surface-alt); border: 1px solid var(--border); border-radius: var(--r-md);
    padding: .2rem .6rem; box-shadow: var(--shadow-md);
}

/* ── Spinner com a cor do tema (anel próprio no lugar do ícone cinza padrão) ── */
div[data-testid="stSpinnerIcon"] { visibility: hidden; position: relative; }
div[data-testid="stSpinnerIcon"]::before {
    content: ""; visibility: visible; position: absolute; inset: 0; margin: auto;
    width: 1rem; height: 1rem; border-radius: 50%;
    border: 2px solid var(--border); border-top-color: var(--primary);
    animation: bmt-spin .7s linear infinite;
}
@keyframes bmt-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { div[data-testid="stSpinnerIcon"]::before { animation: none; } }

/* ── Tabelas/dataframes ─────────────────────────────────────────────────
   `overflow: hidden` vai no grid (`stDataFrameResizable`), não no `stDataFrame`: esse é pai
   do toolbar de hover (`stElementToolbar`) e cortaria o toolbar inteiro. */
div[data-testid="stDataFrame"] { border-radius: var(--r-md); }
div[data-testid="stDataFrameResizable"] { border-radius: var(--r-md); overflow: hidden; }
div[data-testid="stDataFrame"] table { font-variant-numeric: tabular-nums; }

/* ── Card de gráfico/tabela (`card()`) — .surface-card do input_arquivos ── */
div[class*="st-key-bmt-card-"] {
    background: var(--surface);
    border: 1px solid var(--border) !important;
    border-radius: var(--r-lg) !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, .28);
    transition: border-color .15s ease;
}
div[class*="st-key-bmt-card-"]:hover { border-color: var(--border-bright) !important; }
div[class*="st-key-bmt-card-"] [data-testid="stElementToolbar"],
div[class*="st-key-bmt-card-"] .vega-embed summary,
div[class*="st-key-bmt-card-"] .vega-embed .vega-actions { z-index: 3 !important; }
/* Toolbar de hover flutua acima do elemento; se ele é o 1º do card, reserva espaço. */
div[class*="st-key-bmt-card-"] div[data-testid="stElementContainer"]:first-child:has([data-testid="stElementToolbar"]) {
    margin-top: 1.6rem;
}
/* Container com borda (st.container(border=True)) no mesmo padrão de card */
div[data-testid="stVerticalBlockBorderWrapper"]:not([class*="st-key-bmt-"]) {
    border-color: var(--border) !important; border-radius: var(--r-lg) !important;
}

/* ── Cabeçalho de seção (`section_header()`) — .icon-badge + título + subtítulo ── */
.bmt-sec { display: flex; align-items: center; gap: .75rem; margin: .4rem 0 .9rem; }
.bmt-sec-icon {
    width: 2.25rem; height: 2.25rem; border-radius: var(--r-md); flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 1.05rem;
    background: var(--primary-dim); color: var(--primary); border: 1px solid var(--border-bright);
}
.bmt-sec-title { font-size: 1rem; font-weight: 700; color: var(--text); line-height: 1.2; }
.bmt-sec-sub { font-size: .75rem; color: var(--text-muted); margin-top: .15rem; }

/* ── Medidor de força de senha (`scripts/auth.py::_render_forca`) ── */
.bmt-forca { margin: -.25rem 0 .75rem; }
.bmt-forca-track {
    width: 100%; height: 8px; background: var(--surface-alt);
    border: 1px solid var(--border-muted); border-radius: 4px; overflow: hidden;
}
.bmt-forca-fill { height: 100%; border-radius: 4px; transition: width .3s ease; }
.bmt-forca-info { display: flex; align-items: center; gap: .5rem; margin-top: .4rem; font-size: .72rem; color: var(--text-subtle); }
.bmt-badge {
    display: inline-flex; padding: .1rem .45rem; border-radius: var(--r-sm); font-size: .68rem;
    font-weight: 600; text-transform: uppercase; letter-spacing: .02em; border: 1px solid currentColor;
}

/* ── Selos (`badge()`) — .status-badge do input_arquivos ── */
.bmt-pill {
    display: inline-flex; align-items: center; gap: .3rem; border-radius: 9999px;
    padding: .125rem .625rem; font-size: .75rem; font-weight: 500; line-height: 1.4; white-space: nowrap;
}
.bmt-pill--success { background: rgba(34, 197, 94, .15); color: var(--success); }
.bmt-pill--error { background: rgba(239, 68, 68, .15); color: var(--danger); }
.bmt-pill--warn { background: rgba(245, 158, 11, .15); color: var(--warn); }
.bmt-pill--primary { background: var(--primary-dim); color: var(--primary); }
.bmt-pill--muted { background: var(--surface-alt); color: var(--text-muted); }

/* ── Tabela HTML (`html_table()`) — tabela do input_arquivos ── */
.bmt-table-wrap { border: 1px solid var(--border); border-radius: var(--r-lg); overflow: hidden; background: var(--surface); }
.bmt-table { width: 100%; border-collapse: collapse; font-size: .8125rem; }
.bmt-table th {
    background: var(--surface-alt); color: var(--text-muted); font-weight: 600; text-align: left;
    padding: .65rem 1rem; border-bottom: 1px solid var(--border);
}
.bmt-table td { padding: .6rem 1rem; border-bottom: 1px solid var(--border-muted); color: var(--text); }
.bmt-table tr:last-child td { border-bottom: 0; }
.bmt-table tr:hover td { background: var(--surface-hover); }

/* ── Cards de navegação (`nav_card()`) — painel de administração do input_arquivos ── */
div[class*="st-key-bmt-navcard-"] {
    background: var(--surface); border: 1px solid var(--border) !important;
    border-radius: var(--r-lg) !important; box-shadow: 0 8px 24px rgba(0, 0, 0, .28);
    padding: 1.25rem 1.35rem .9rem !important; transition: transform .15s ease, border-color .15s ease;
    min-height: 12.5rem; justify-content: space-between;
}
div[class*="st-key-bmt-navcard-"] [data-testid="stPageLink"] { margin-top: .6rem; }
div[class*="st-key-bmt-navcard-"]:hover { transform: translateY(-2px); border-color: var(--border-bright) !important; }
.bmt-navcard-icon {
    width: 40px; height: 40px; border-radius: var(--r-md); background: var(--primary);
    display: flex; align-items: center; justify-content: center; font-size: 1.1rem;
    box-shadow: 0 0 0 1px var(--primary-dim), 0 2px 10px var(--primary-glow); margin-bottom: .75rem;
}
.bmt-navcard-title { font-size: 1rem; font-weight: 700; color: var(--text); margin-bottom: .3rem; }
.bmt-navcard-desc { font-size: .78rem; color: var(--text-muted); line-height: 1.55; }
div[class*="st-key-bmt-navcard-"] [data-testid="stPageLink"] a { padding-left: 0; }
div[class*="st-key-bmt-navcard-"] [data-testid="stPageLink"] a p {
    color: var(--primary) !important; font-size: .78rem !important; font-weight: 600;
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
   (mapeia pra um token de tema pequeno demais pro peso visual que a marca precisa aqui) e
   centraliza no cabeçalho da sidebar (default do Streamlit é alinhado à esquerda). */
img[data-testid="stLogo"] {
    height: 4.4rem !important;
    max-height: none !important;
    max-width: 90%;
    width: auto !important;
    object-fit: contain;
}
div[data-testid="stSidebarHeader"] {
    justify-content: center !important;
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


# Paleta dos 3 temas do app_template (frontend/css/theme.css), mapeada pros elementos do
# Streamlit. `corporate` é o padrão do ecossistema; `blau` é o tema de marca original.
THEMES: dict[str, dict[str, str]] = {
    "corporate": {
        "label": "Corporativo",
        "bg": "#12141a", "surface": "#1a1d24", "surface_alt": "#21252e", "surface_hover": "#2a2f3a",
        "border": "#2d323d", "border_muted": "#21252e", "border_bright": "#3d4452",
        "primary": "#5b8def", "primary_hover": "#6f9bff", "primary_soft": "rgba(91, 141, 239, 0.12)",
        "primary_glow": "rgba(91, 141, 239, 0.18)",
        "text": "#e4e6eb", "text_muted": "#9aa0ac", "text_subtle": "#6b7280",
        "success": "#22c55e", "warn": "#f59e0b", "danger": "#ef4444", "danger_dim": "rgba(239, 68, 68, 0.1)",
        "font": "Inter", "radius": "8px",
    },
    "green-neutral": {
        "label": "Verde Neutro",
        "bg": "#0d0d0d", "surface": "#161616", "surface_alt": "#202020", "surface_hover": "#2b2b2b",
        "border": "#303030", "border_muted": "#202020", "border_bright": "#454545",
        "primary": "#1aff80", "primary_hover": "#26ff8c", "primary_soft": "rgba(26, 255, 128, 0.12)",
        "primary_glow": "rgba(26, 255, 128, 0.24)",
        "text": "#1aff80", "text_muted": "#11b857", "text_subtle": "#075927",
        "success": "#1aff80", "warn": "#ff9900", "danger": "#ff3333", "danger_dim": "rgba(255, 51, 51, 0.12)",
        "font": "Share Tech Mono", "radius": "3px",
    },
    "cyber-dark": {
        "label": "Cyber Dark",
        "bg": "#080914", "surface": "#0f1123", "surface_alt": "#171936", "surface_hover": "#21244d",
        "border": "#282c5e", "border_muted": "#171936", "border_bright": "#404694",
        "primary": "#8b5cf6", "primary_hover": "#a78bfa", "primary_soft": "rgba(139, 92, 246, 0.15)",
        "primary_glow": "rgba(139, 92, 246, 0.25)",
        "text": "#f3f4f6", "text_muted": "#9ca3af", "text_subtle": "#6b7280",
        "success": "#06b6d4", "warn": "#f59e0b", "danger": "#ec4899", "danger_dim": "rgba(236, 72, 153, 0.12)",
        "font": "Inter", "radius": "8px",
    },
    "blau": {
        "label": "Blau (marca)",
        "bg": "#1C1F26", "surface": "#262B33", "surface_alt": "#2F343C", "surface_hover": "#363C45",
        "border": "#3A4149", "border_muted": "#2F343C", "border_bright": "#4A525C",
        "primary": "#26B4E9", "primary_hover": "#4CC3EF", "primary_soft": "rgba(38, 180, 233, 0.12)",
        "primary_glow": "rgba(38, 180, 233, 0.22)",
        "text": "#F1F3F5", "text_muted": "#ADB5BD", "text_subtle": "#7A838C",
        "success": "#5CB85C", "warn": "#EDB50B", "danger": "#D9534F", "danger_dim": "rgba(217, 83, 79, 0.12)",
        "font": "Roboto", "radius": "8px",
    },
}
DEFAULT_THEME = "corporate"
_SS_THEME = "app_theme"


def _theme_css(name: str) -> str:
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Share+Tech+Mono&family=Roboto:wght@400;700&display=swap');
:root {{
    --accent: {t["primary"]};
    --accent-soft: {t["primary_soft"]};
    --surface: {t["surface"]};
    --surface-border: {t["border"]};
    /* Tokens do app_template (frontend/css/style.css + theme.css), usados pelos componentes
       abaixo e pela tela de login (scripts/auth.py). */
    --bg: {t["bg"]}; --surface-alt: {t["surface_alt"]}; --surface-hover: {t["surface_hover"]};
    --border: {t["border"]}; --border-muted: {t["border_muted"]}; --border-bright: {t["border_bright"]};
    --primary: {t["primary"]}; --primary-hover: {t["primary_hover"]};
    --primary-dim: {t["primary_soft"]}; --primary-glow: {t["primary_glow"]};
    --glow-ring: 0 0 0 3px {t["primary_glow"]};
    --text: {t["text"]}; --text-muted: {t["text_muted"]}; --text-subtle: {t["text_subtle"]};
    --success: {t["success"]}; --warn: {t["warn"]}; --danger: {t["danger"]}; --danger-dim: {t["danger_dim"]};
    --shadow-sm: 0 1px 3px rgba(0,0,0,.5); --shadow-md: 0 4px 16px rgba(0,0,0,.55);
    --shadow-lg: 0 8px 32px rgba(0,0,0,.65);
    --ease-spring: cubic-bezier(.16,1,.3,1);
    --r-sm: calc({t["radius"]} - 2px); --r-md: {t["radius"]}; --r-lg: calc({t["radius"]} + 4px);
}}
/* ── Componentes no padrão do app_template ───────────────────────────────── */
::selection {{ background: var(--primary-dim); color: var(--primary); }}
* {{ scrollbar-width: thin; scrollbar-color: var(--border) transparent; }}
*::-webkit-scrollbar {{ width: 6px; height: 6px; }}
*::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
*::-webkit-scrollbar-track {{ background: transparent; }}
/* Botões: .btn-primary (glow no hover) e .btn-ghost (secundário) */
.stApp button[kind^="primary"] {{
    background: var(--primary) !important; border: 1px solid var(--primary) !important;
    color: var(--bg) !important; font-weight: 600; border-radius: var(--r-md) !important;
    box-shadow: 0 1px 3px rgba(0,0,0,.2); transition: background .15s, box-shadow .15s, transform .08s;
}}
.stApp button[kind^="primary"] p {{ color: var(--bg) !important; }}
.stApp button[kind^="primary"]:hover {{
    background: var(--primary-hover) !important; box-shadow: 0 3px 12px var(--primary-glow); filter: none;
}}
.stApp button[kind^="secondary"] {{
    background: var(--surface-alt); border: 1px solid var(--border); color: var(--text-muted);
    border-radius: var(--r-md) !important; transition: background .15s, border-color .15s, transform .08s;
}}
.stApp button[kind^="secondary"]:hover {{
    background: var(--surface-hover); border-color: var(--border-bright); color: var(--text); filter: none;
}}
.stApp button:active {{ transform: scale(.97); }}
.stApp button:focus-visible {{ outline: 2px solid var(--primary); outline-offset: 2px; }}
/* Inputs: .form-input (fundo surface-alt, anel de foco). Streamlit 1.62 usa react-aria (sem
   `data-baseweb`): o contorno visível mora no div pai do <input>/<textarea>. */
[data-testid="stTextInput"] div:has(> input), [data-testid="stTextArea"] div:has(> textarea),
[data-testid="stDateInput"] div:has(> input), [data-testid="stNumberInputContainer"],
.stApp .react-aria-ComboBox > [role="group"] {{
    background: var(--surface-alt) !important; border-color: var(--border) !important;
    border-radius: var(--r-md) !important; transition: border-color .15s, box-shadow .15s;
}}
[data-testid="stTextInput"] div:has(> input):focus-within, [data-testid="stTextArea"] div:has(> textarea):focus-within,
[data-testid="stDateInput"] div:has(> input):focus-within, [data-testid="stNumberInputContainer"]:focus-within,
.stApp .react-aria-ComboBox > [role="group"]:focus-within {{
    border-color: var(--primary) !important; box-shadow: var(--glow-ring);
}}
.stApp input::placeholder, .stApp textarea::placeholder {{ color: var(--text-subtle) !important; }}
/* Checkbox e toggle marcados na cor do tema (o `primaryColor` do config.toml é fixo). */
[data-testid="stCheckbox"] label[data-selected="true"] > div:not([data-testid]) {{
    background: var(--primary) !important; border-color: var(--primary) !important;
}}
/* Formulários e containers com borda: .card */
div[data-testid="stForm"] {{ border-color: var(--border) !important; border-radius: var(--r-lg) !important; }}
div[data-testid="stExpander"] details {{
    background: var(--surface); border: 1px solid var(--border) !important;
    border-radius: var(--r-lg) !important; box-shadow: var(--shadow-sm);
}}
div[data-testid="stExpander"] summary:hover {{ color: var(--primary); }}
/* Abas: .nav-btn.active */
.stApp [data-testid="stTab"] p {{ color: var(--text-muted) !important; }}
.stApp [data-testid="stTab"]:hover p {{ color: var(--text) !important; }}
.stApp [data-testid="stTab"][aria-selected="true"] p {{ color: var(--primary) !important; font-weight: 600; }}
/* Barra de progresso na cor do tema */
div[data-testid="stProgress"] div[role="progressbar"] > div > div {{
    background: linear-gradient(90deg, var(--primary), var(--primary-hover)) !important;
}}
/* Toast (st.toast): .toast */
div[data-testid="stToast"] {{
    background: var(--surface-alt) !important; border: 1px solid var(--border);
    border-radius: var(--r-md) !important; box-shadow: var(--shadow-md);
}}
/* Código e tabelas */
.stApp code {{ font-family: 'Share Tech Mono', ui-monospace, monospace !important; color: var(--primary); }}
div[data-testid="stCode"] pre {{ background: var(--surface-alt) !important; border: 1px solid var(--border); }}
div[data-testid="stTable"] th {{ background: var(--surface-alt); color: var(--text-muted); }}
div[data-testid="stTable"] td {{ border-color: var(--border-muted); }}
.stApp [data-testid="stCaptionContainer"] p {{ color: var(--text-muted) !important; }}
.stApp, [data-testid="stHeader"] {{ background: {t["bg"]}; color: {t["text"]}; }}
[data-testid="stSidebar"] {{ background: {t["surface"]}; border-right: 1px solid {t["border"]}; }}
.stApp, .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp li,
.stApp span:not([data-testid="stIconMaterial"]):not([translate="no"]), .stApp input, .stApp button, .stApp textarea {{
    font-family: '{t["font"]}', ui-sans-serif, system-ui, sans-serif !important;
    color: {t["text"]};
}}
.stApp [data-testid="stIconMaterial"], .stApp span[translate="no"] {{ font-family: "Material Symbols Rounded" !important; }}
/* Logo Blau já fica no topo da sidebar; no cabeçalho ele colidia com a marca da Topbar. */
[data-testid="stHeader"] [data-testid="stHeaderLogo"], [data-testid="stHeader"] [data-testid="stLogoLink"] {{ display: none !important; }}
.stApp a {{ color: {t["primary"]}; }}
.bmt-topbar {{
    position: fixed; top: 0; left: 0; right: 0; height: 52px; z-index: 999992;
    background: {t["surface"]}; border-bottom: 1px solid {t["border"]};
    display: flex; align-items: center; gap: .625rem; padding: 0 1rem 0 5rem;
    pointer-events: none;
}}
.bmt-topbar::after {{
    content: ''; position: absolute; bottom: -1px; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent 0%, {t["border"]} 30%, {t["primary"]} 50%,
        {t["border"]} 70%, transparent 100%);
    background-size: 200% auto; animation: bmtShimmer 4s linear infinite;
}}
@keyframes bmtShimmer {{ from {{ background-position: 0% 0; }} to {{ background-position: 200% 0; }} }}
.bmt-brand-icon {{
    width: 30px; height: 30px; border-radius: 8px; background: {t["primary"]}; color: {t["bg"]};
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 0 0 1px {t["primary_soft"]}, 0 2px 8px {t["primary_glow"]};
    animation: bmtBrandGlow 3s ease-in-out infinite;
}}
@keyframes bmtBrandGlow {{
    0%, 100% {{ box-shadow: 0 0 0 1px {t["primary_soft"]}, 0 2px 8px {t["primary_glow"]}; }}
    50% {{ box-shadow: 0 0 0 4px {t["primary_soft"]}, 0 4px 20px {t["primary_glow"]}; }}
}}
.bmt-brand-text {{ display: flex; flex-direction: column; line-height: 1; }}
.bmt-brand-name {{ font-size: .875rem; font-weight: 700; letter-spacing: .02em; color: {t["text"]}; }}
.bmt-brand-tag {{ font-size: .6rem; letter-spacing: .07em; text-transform: uppercase; color: {t["text_subtle"]}; margin-top: .2rem; }}
.bmt-brand-divider {{ width: 1px; height: 22px; background: {t["border"]}; margin: 0 .25rem; }}
/* Selo "SwordPower" em forma de espada (mesmo SVG do input_arquivos) */
.bmt-brand-by {{ position: relative; width: 140px; height: 26px; display: flex; align-items: center; opacity: .9; }}
.bmt-brand-by svg {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
.bmt-brand-by span {{
    position: relative; z-index: 1; margin-left: 30px; font-size: .68rem; font-weight: 800;
    letter-spacing: .05em; color: #f0fdf4;
    text-shadow: 0 1px 2px rgba(0,0,0,.85), 0 0 1px rgba(0,0,0,.9);
}}
@media (max-width: 900px) {{ .bmt-brand-divider, .bmt-brand-by, .bmt-brand-tag {{ display: none; }} }}
@media (prefers-reduced-motion: reduce) {{ .bmt-brand-icon {{ animation: none; }} }}
/* Header transparente ACIMA da topbar: é nele que mora o botão de reabrir a sidebar. */
[data-testid="stHeader"] {{ height: 52px; background: transparent; z-index: 999995; }}
/* Botão de reabrir a sidebar: canto esquerdo da Topbar, acima da Activity Bar (sem isso ele
   caía em cima do ícone da marca). */
[data-testid="stExpandSidebarButton"] {{
    position: fixed; left: 14px; top: 12px; z-index: 999996; color: {t["text"]};
}}
[data-testid="stSidebar"] > div:first-child {{ padding-top: 52px; }}
.block-container {{ padding-top: 4.5rem !important; }}
/* Ações do usuário na Topbar (canto direito): selo online, saudação, Admin, Minha senha,
   Configurações, Sair — como o `.topbar-right` do input_arquivos. Widgets reais do Streamlit
   (container `bmt-acoes`, montado em scripts/auth.py) posicionados por cima da Topbar. */
[class*="st-key-bmt-acoes"] {{
    position: fixed; top: 9px; right: 3.4rem; z-index: 999996; width: auto !important;
    flex-wrap: nowrap !important; align-items: center; gap: .45rem !important;
}}
[class*="st-key-bmt-acoes"] > div {{ width: auto !important; flex: 0 0 auto !important; }}
[class*="st-key-bmt-acoes"] button, [class*="st-key-bmt-acoes"] [data-testid="stPageLink"] a {{
    min-height: 2rem; height: 2rem; padding: 0 .75rem; background: {t["surface_alt"]} !important;
    border: 1px solid {t["border"]} !important; border-radius: {t["radius"]} !important;
    color: {t["text_muted"]}; font-size: .8125rem; margin: 0;
}}
[class*="st-key-bmt-acoes"] button p, [class*="st-key-bmt-acoes"] [data-testid="stPageLink"] a p {{
    font-size: .8125rem !important; color: {t["text_muted"]} !important; font-weight: 500;
}}
[class*="st-key-bmt-acoes"] button:hover, [class*="st-key-bmt-acoes"] [data-testid="stPageLink"] a:hover {{
    background: {t["surface_hover"]} !important; border-color: {t["border_bright"]} !important;
}}
[class*="st-key-bmt-acoes"] button:hover p, [class*="st-key-bmt-acoes"] a:hover p {{ color: {t["text"]} !important; }}
.bmt-conn {{
    display: inline-flex; align-items: center; gap: .35rem; padding: .15rem .55rem; border-radius: 6px;
    font-size: .72rem; font-weight: 600; color: {t["success"]}; background: rgba(34,197,94,.1);
    border: 1px solid rgba(34,197,94,.3); white-space: nowrap;
}}
.bmt-conn::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
.bmt-conn--off {{ color: {t["warn"]}; background: rgba(245,158,11,.1); border-color: rgba(245,158,11,.3); }}
.bmt-hello {{ font-size: .75rem; color: {t["text_muted"]}; white-space: nowrap; }}
/* Em telas menores que um notebook fica só o ícone (o `help` do botão explica a ação). */
@media (max-width: 1180px) {{
    [class*="st-key-bmt-acoes"] button p, [class*="st-key-bmt-acoes"] [data-testid="stPageLink"] a p,
    .bmt-hello {{ display: none; }}
}}
/* Activity Bar vertical (estilo VSCode): coluna fixa de 56px à esquerda, abaixo da Topbar.
   O `padding-left` empurra sidebar + conteúdo pra direita (só existe se a barra existe —
   `:has`, então a tela de login não ganha a faixa vazia). */
[data-testid="stAppViewContainer"]:has([class*="st-key-bmt-rail"]) {{ padding-left: 56px; }}
[class*="st-key-bmt-rail"] {{
    position: fixed; top: 52px; left: 0; bottom: 0; width: 56px; z-index: 999990;
    background: {t["surface"]}; border-right: 1px solid {t["border"]};
    padding: .5rem .25rem; gap: .25rem !important; overflow-y: auto;
}}
/* Os wrappers do page_link (container, tooltip do `help=`) encolhem ao conteúdo; sem isso a
   célula não ocupa a largura da barra e o ícone/rótulo ficam deslocados. */
[class*="st-key-bmt-rail"] :is([data-testid="stElementContainer"], [data-testid="stPageLink"],
    [data-testid="stTooltipHoverTarget"], [data-testid="stTooltipIcon"]),
[class*="st-key-bmt-rail"] [data-testid="stTooltipIcon"] > div {{ width: 100% !important; display: block; }}
[class*="st-key-bmt-activity"] {{ width: 100%; }}
[class*="st-key-bmt-activity"] a [data-testid="stIconMaterial"] {{ font-size: 1.4rem; }}
[class*="st-key-bmt-activity"] a {{
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: .2rem; width: 100%; min-height: 3.4rem; padding: .4rem 0 !important;
    border-radius: 8px; border: 1px solid transparent; color: {t["text"]}; opacity: .65;
}}
[class*="st-key-bmt-activity"] a p {{
    font-size: .6rem !important; letter-spacing: .01em; margin: 0; white-space: nowrap;
    text-align: center; line-height: 1;
}}
[class*="st-key-bmt-activity"] a:hover {{ background: {t["surface_alt"]}; opacity: 1; }}
[class*="st-key-bmt-activity-on"] a {{
    background: {t["primary_soft"]}; color: {t["primary"]}; opacity: 1;
    border-color: {t["primary"]}; font-weight: 700;
}}
[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"][aria-current="page"] {{
    background: {t["primary_soft"]}; border-left: 3px solid {t["primary"]};
}}
</style>
<div class="bmt-topbar">
  <div class="bmt-brand-icon">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
  </div>
  <div class="bmt-brand-text">
    <span class="bmt-brand-name">Invest SAP</span>
    <span class="bmt-brand-tag">Vendas &amp; Pendências · SwordPower Web</span>
  </div>
  <div class="bmt-brand-divider"></div>
  <div class="bmt-brand-by" title="SwordPower">
    <svg viewBox="0 0 140 26" preserveAspectRatio="none" fill="none">
      <circle cx="7" cy="13" r="3.5" fill="#d29922"/>
      <rect x="11.5" y="10.5" width="7" height="5" rx="1.2" fill="#d29922"/>
      <rect x="19.5" y="6" width="2.6" height="14" rx="1.2" fill="#e6edf3"/>
      <path d="M22 11.5 L133 9.5 L139 13 L133 16.5 L22 14.5 Z" fill="#10b981" opacity=".9"/>
      <path d="M22 12.2 L131 10.6 L131 15.4 L22 13.8 Z" fill="#5eead4" opacity=".4"/>
    </svg>
    <span>SwordPower</span>
  </div>
</div>
"""


def apply_custom_theme() -> None:
    """Injeta o CSS custom + o logo da Blau + o tema escolhido + a Topbar SwordPower.

    Chamar uma vez só, em `app.py`, antes de `st.navigation(...).run()` — ver docstring
    do módulo pro porquê de bastar uma chamada só e por que é `st.logo()`, não
    `st.markdown` dentro de `st.sidebar`. O tema (Corporativo/Verde Neutro/Cyber Dark/Blau)
    é o mesmo conjunto do app_template e fica em `st.session_state["app_theme"]`; o seletor
    fica no diálogo "Configurações" da Topbar (`dialog_configuracoes()`) — aqui só se lê o
    valor já escolhido.
    """
    st.markdown(_CSS, unsafe_allow_html=True)
    if _SS_THEME not in st.session_state:
        from scripts import app_db  # import tardio: evita ciclo e só toca o SQLite se preciso

        padrao = app_db.get_setting("default_theme", DEFAULT_THEME)
        st.session_state[_SS_THEME] = padrao if padrao in THEMES else DEFAULT_THEME
    escolhido = st.session_state[_SS_THEME]
    st.markdown(_theme_css(escolhido), unsafe_allow_html=True)
    st.logo(
        str(_ASSETS_DIR / "blau_logo.png"),
        icon_image=str(_ASSETS_DIR / "blau_icon.png"),
        size="large",
    )


_SS_THEME_WIDGET = "_app_theme_sel"


def _aplicar_tema_escolhido() -> None:
    """Callback do seletor: copia o valor do widget para a chave persistente do tema.

    O widget vive num diálogo; quando o diálogo fecha, o Streamlit descarta a chave do
    widget — por isso o tema fica numa chave própria (`app_theme`), não na do widget.
    """
    st.session_state[_SS_THEME] = st.session_state[_SS_THEME_WIDGET]


@st.dialog("Configurações de Aparência")
def dialog_configuracoes() -> None:
    """Diálogo "Configurações" da Topbar (mesmo modal do app_template/input_arquivos)."""
    st.session_state[_SS_THEME_WIDGET] = st.session_state.get(_SS_THEME, DEFAULT_THEME)
    st.selectbox(
        "Tema da interface",
        list(THEMES),
        format_func=lambda k: THEMES[k]["label"] + (" (padrão)" if k == DEFAULT_THEME else ""),
        key=_SS_THEME_WIDGET,
        on_change=_aplicar_tema_escolhido,
    )
    st.caption(
        "Vale para esta sessão. O tema padrão de novas sessões e o bloqueio por inatividade "
        "são definidos pelo admin em Administração → Configurações."
    )
    if st.button("Fechar", type="primary", use_container_width=True):
        st.rerun()


def section_header(icone: str, titulo: str, subtitulo: str = "") -> None:
    """Cabeçalho de seção: ícone em quadrado + título + subtítulo (padrão input_arquivos).

    Args:
        icone: Emoji ou caractere curto exibido no quadrado.
        titulo: Título da seção.
        subtitulo: Linha de descrição opcional.
    """
    sub = f'<div class="bmt-sec-sub">{html.escape(subtitulo)}</div>' if subtitulo else ""
    st.markdown(
        f'''<div class="bmt-sec"><div class="bmt-sec-icon">{html.escape(icone)}</div>
<div><div class="bmt-sec-title">{html.escape(titulo)}</div>{sub}</div></div>''',
        unsafe_allow_html=True,
    )


def badge(texto: str, tipo: str = "muted") -> str:
    """HTML de um selo arredondado (`.status-badge` do input_arquivos), já escapado.

    Args:
        texto: Texto do selo.
        tipo: "success", "error", "warn", "primary" ou "muted".

    Returns:
        String HTML para compor em `st.markdown(..., unsafe_allow_html=True)`.
    """
    tipo = tipo if tipo in ("success", "error", "warn", "primary", "muted") else "muted"
    return f'<span class="bmt-pill bmt-pill--{tipo}">{html.escape(texto)}</span>'


def html_table(colunas: list[str], linhas: list[list[str]]) -> None:
    """Tabela HTML no estilo do input_arquivos (para listas curtas com selos).

    Args:
        colunas: Cabeçalhos (texto puro, escapado aqui).
        linhas: Células já em HTML seguro — texto vindo de dado precisa passar por
            `html.escape` (ou `badge()`, que já escapa) antes de chegar aqui.
    """
    cab = "".join(f"<th>{html.escape(c)}</th>" for c in colunas)
    corpo = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in linha) + "</tr>" for linha in linhas)
    st.markdown(
        f'<div class="bmt-table-wrap"><table class="bmt-table"><thead><tr>{cab}</tr></thead>'
        f"<tbody>{corpo}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def nav_card(key: str, icone: str, titulo: str, descricao: str, pagina: str, rotulo_link: str) -> None:
    """Card de navegação (painel de administração do input_arquivos).

    Args:
        key: Sufixo único da key do container.
        icone: Emoji do quadrado.
        titulo: Título do card.
        descricao: Descrição curta.
        pagina: Caminho do arquivo da página de destino (ex.: "pages/91_Admin_Usuarios.py").
        rotulo_link: Texto do link (ex.: "Gerenciar Usuários →").
    """
    with st.container(key=f"bmt-navcard-{key}", border=True):
        st.markdown(
            f'''<div class="bmt-navcard-icon">{html.escape(icone)}</div>
<div class="bmt-navcard-title">{html.escape(titulo)}</div>
<div class="bmt-navcard-desc">{html.escape(descricao)}</div>''',
            unsafe_allow_html=True,
        )
        st.page_link(pagina, label=rotulo_link)


def render_filtro_periodo_tipo_cliente(key_prefix: str = "flt") -> None:
    """Filtro de Período + Tipo de cliente (Governo/Privado), pra usar DENTRO de 1 página.

    Até 2026-09-04 isso vivia sozinho na sidebar de `app.py` ("filtro global"), afetando
    9 páginas sem ficar visível em nenhuma delas — confuso (setava o filtro num lugar, via
    o efeito em outro). Movido pra dentro de cada página que usa, chamando esta função (as
    que usam só Tipo de cliente, sem Período, chamam `render_filtro_tipo_cliente` em vez
    desta) no topo do corpo da página — mesmas chaves de `st.session_state`
    (`flt_data_inicio`/`flt_data_fim`/`flt_tipo_cliente`), então o valor ainda é
    compartilhado entre as páginas que chamarem uma das duas (é o mesmo widget/estado, só
    renderizado em lugares diferentes) — não é 1 filtro isolado por página.

    Usam esta versão (período + tipo): Oportunidade, Faturamento, Vendedor, Crédito e
    Devoluções, Vendedor x Meta x Faturamento. Usam só `render_filtro_tipo_cliente`:
    Pedidos, Painel Vendas, Produto/Cliente, Relatório Analítico.

    Não precisa de `with st.sidebar:` nem nada — a página que chama decide onde
    (normalmente logo abaixo do título/caption, antes de qualquer consulta).
    """
    hoje = _dt.date.today()
    col_periodo, col_tipo = st.columns([2, 1])
    with col_periodo:
        periodo = st.date_input(
            "Período",
            value=(hoje - _dt.timedelta(days=30), hoje),
            max_value=hoje,
            key=f"{key_prefix}_periodo",
        )
    # date_input com range retorna tupla de 1 elemento enquanto o usuário só escolheu a
    # data inicial (segunda ponta ainda não selecionada) — só atualiza o filtro quando o
    # range vier completo; até lá, mantém o valor anterior (ou o default).
    if isinstance(periodo, tuple) and len(periodo) == 2:
        st.session_state[f"{key_prefix}_data_inicio"], st.session_state[f"{key_prefix}_data_fim"] = periodo
    with col_tipo:
        st.selectbox("Tipo de cliente", ["Todos", "Governo", "Privado"], key=f"{key_prefix}_tipo_cliente")


def render_filtro_tipo_cliente(key_prefix: str = "flt") -> None:
    """Só o filtro de Tipo de cliente (Governo/Privado) — ver `render_filtro_periodo_tipo_cliente`
    pra contexto completo e pra quando usar cada uma. Mesma chave de `st.session_state`
    (`flt_tipo_cliente`), então o valor é compartilhado com quem usa a outra função também.
    """
    st.selectbox("Tipo de cliente", ["Todos", "Governo", "Privado"], key=f"{key_prefix}_tipo_cliente")


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
    st.markdown(f'<p class="bmt-card-label">{html.escape(text)}</p>', unsafe_allow_html=True)


def render_valor_convertido_brl(
    df,
    valor_col: str,
    moeda_col: str = "Moeda",
    data_col: str = "Data",
    label: str = "Total",
    taxas=None,
) -> None:
    """1 `st.metric` com o total já convertido pra BRL (via `TCURR`, taxa de câmbio real do
    SAP — ver `scripts/query_vendas_sap.py::converter_para_brl`), com um `st.expander`
    fechado por padrão pra ver a quebra bruta por moeda sem conversão, se alguém quiser
    conferir (decisão do usuário 2026-09-04: total convertido é o principal, a quebra por
    moeda fica "atrás de um botão", não escondida de vez).

    Args:
        df: precisa ter `moeda_col`, `data_col` e `valor_col`.
        label: nome do total (ex.: "Faturado no período" -> métrica "Faturado no período
            (convertido p/ BRL)").
        taxas: `taxas_cambio_brl()` já carregada, opcional (evita reconsultar o HANA toda
            vez que esta função é chamada na mesma página).
    """
    from scripts.query_vendas_sap import MOEDAS_CAMBIO_DISPONIVEL, converter_para_brl

    if df.empty:
        st.metric(f"{label} (BRL)", "R$ 0")
        return

    valores_brl = converter_para_brl(df, valor_col, moeda_col, data_col, taxas=taxas)
    total_convertido = valores_brl.sum()
    st.metric(f"{label} (convertido p/ BRL)", f"R$ {total_convertido:,.0f}")

    mask_sem_taxa = valores_brl.isna() & (df[moeda_col] != "BRL")
    if mask_sem_taxa.any():
        moedas_sem_taxa = sorted(df.loc[mask_sem_taxa, moeda_col].unique())
        st.caption(
            f":material/info: {int(mask_sem_taxa.sum()):,} linha(s) em moeda sem taxa de "
            f"câmbio disponível ({', '.join(moedas_sem_taxa)}) não entraram no total acima "
            f"(só {', '.join(MOEDAS_CAMBIO_DISPONIVEL)} têm taxa real nesta base)."
        )

    if (df[moeda_col] != "BRL").any():
        with st.expander("Ver por moeda, sem conversão (valor original de cada uma)"):
            render_valor_por_moeda(df, valor_col, moeda_col)


def render_valor_por_moeda(df, valor_col: str, moeda_col: str = "Moeda", label_prefix: str = "") -> None:
    """1 `st.metric` por moeda presente em `df`, em vez de somar tudo junto como se fosse R$.

    Achado 2026-09-04: `Valor_*` de `fct_vendas_itens_sap`/`fct_faturamento_itens_sap`/
    `fct_pendencia_sap` (e `Opportunity.amount`/`OpportunityLineItem.TotalPrice` do
    Salesforce) vêm na moeda do documento original (BRL/USD/UYU/COP/EUR/...), sem conversão
    — não existe taxa de câmbio pronta pra virar 1 número em R$ hoje (ver `TCURR`/HANA,
    achado real mas ainda não ligado a nenhuma consulta deste projeto). Decisão do usuário
    (2026-09-04): em vez de esconder/descartar moeda estrangeira, mostrar cada uma separada
    — BRL primeiro (moeda principal do negócio), resto em ordem decrescente de valor.

    Args:
        df: DataFrame com 1 linha por (o que for) + moeda já no grão certo pra somar
            (`valor_col` ainda não deve ter sido agregado ignorando `moeda_col`).
        valor_col: coluna a somar por moeda.
        moeda_col: coluna com o código da moeda (default `"Moeda"`).
        label_prefix: texto opcional antes do código da moeda no rótulo do metric
            (ex.: `"Faturado"` -> rótulo "Faturado BRL").
    """
    if df.empty or moeda_col not in df.columns:
        st.caption("Sem dado de moeda disponível.")
        return
    resumo = df.groupby(moeda_col)[valor_col].sum().sort_values(ascending=False)
    resumo = resumo[resumo != 0]
    if resumo.empty:
        st.caption("Sem valor a mostrar.")
        return
    ordem = ([m for m in ("BRL",) if m in resumo.index]) + [m for m in resumo.index if m != "BRL"]
    cols = st.columns(len(ordem))
    for col, moeda in zip(cols, ordem):
        prefixo = "R$" if moeda == "BRL" else f"{moeda} "
        rotulo = f"{label_prefix} {moeda}".strip()
        col.metric(rotulo, f"{prefixo}{resumo[moeda]:,.0f}")
