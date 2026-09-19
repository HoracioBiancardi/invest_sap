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

/* Spinner de carregamento (st.spinner()/show_spinner="...") com a cor de destaque do app
   em vez do ícone cinza padrão do Streamlit. Não troca a estrutura/texto — só esconde o
   ícone original (`stSpinnerIcon`, mantendo a caixa/alinhamento) e desenha um anel
   giratório próprio no lugar dele via `::before`. */
div[data-testid="stSpinnerIcon"] {
    visibility: hidden;
    position: relative;
}
div[data-testid="stSpinnerIcon"]::before {
    content: "";
    visibility: visible;
    position: absolute;
    inset: 0;
    margin: auto;
    width: 1rem;
    height: 1rem;
    border-radius: 50%;
    border: 2px solid var(--surface-border);
    border-top-color: var(--accent);
    animation: bmt-spin 0.7s linear infinite;
}
@keyframes bmt-spin {
    to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
    div[data-testid="stSpinnerIcon"]::before {
        animation: none;
    }
}

/* Tabelas/dataframes: só o arredondado — a borda quem dá é o card (`.st-key-bmt-card-*`)
   que normalmente envolve a tabela; ver função `card()` abaixo. `overflow: hidden` vai no
   filho `stDataFrameResizable` (o grid em si), não no `stDataFrame` — esse é o pai direto
   do toolbar de hover (`stElementToolbar`, confirmado no bundle JS do Streamlit instalado:
   `DataFrame.*.js` renderiza toolbar e grid como irmãos dentro do mesmo `stDataFrame`), e
   `overflow: hidden` ali cortava o toolbar inteiro (ele "sumia" no hover em vez de só ficar
   atrás de algo, diferente do problema de z-index abaixo, que é outro). */
div[data-testid="stDataFrame"] {
    border-radius: 8px;
}
div[data-testid="stDataFrameResizable"] {
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
    transition: opacity 0.15s ease;
}
/* O menu/toolbar de gráfico ou tabela (nativo do Streamlit ou do vega-embed, ver os 2
   blocos abaixo) só aparece no hover, bem no canto onde mora o acento diagonal acima —
   mesmo com z-index acima dele, os ícones ficam visualmente sujos por cima da bandeirinha
   colorida. Mais simples que reposicionar/encolher o acento: sumir com ele durante o hover
   (mesmo momento em que o toolbar aparece), então nunca competem visualmente. */
div[class*="st-key-bmt-card-"]:hover::after {
    opacity: 0;
}
/* stElementToolbar (ícones de fullscreen/download/"show data" que aparecem no hover de
   gráfico/tabela) não tem z-index próprio — sem isso, fica por baixo do friso/acento
   diagonal acima (z-index 1-2) e alguns ícones somem ou ficam inclicáveis. */
div[class*="st-key-bmt-card-"] [data-testid="stElementToolbar"] {
    z-index: 3 !important;
}
/* Mesmo problema, só que no menu "..." nativo do Altair/Vega (`st.altair_chart`) — esse
   não é o `stElementToolbar` acima, é o próprio botão do vega-embed (`summary`/
   `.vega-actions`, `z-index: 1000` no CSS dele), que fica preso num stacking context por
   baixo do acento diagonal (`::after`) quando o gráfico é o primeiro elemento do card (sem
   `st.subheader`/`card_label()` empurrando ele pra baixo primeiro). */
div[class*="st-key-bmt-card-"] .vega-embed summary,
div[class*="st-key-bmt-card-"] .vega-embed .vega-actions {
    z-index: 3 !important;
}
/* Mesmo cenário "sem heading empurrando pra baixo" acima, mas o sintoma aqui é outro: o
   toolbar (`stElementToolbar`) flutua ACIMA do próprio elemento (offset negativo, padrão do
   Streamlit) pra não ocupar espaço no layout — quando o elemento é o primeiro filho do seu
   bloco (direto no card, ou dentro de 1 coluna de `st.columns()` dentro do card, como
   tabela+gráfico lado a lado), esse offset negativo estoura o teto do card e o
   `overflow: hidden` dele (necessário pro acento diagonal acima ficar só com a ponta visível,
   não um losango inteiro flutuando) corta o toolbar de vez — ele some, em vez de só ficar
   sujo atrás de algo (esse já era resolvido pelo z-index acima). Fix: reservar espaço no
   topo só quando o 1º elemento do bloco tiver toolbar (`:has()`), sem mexer no espaçamento
   de cards que já têm heading/`card_label()` antes (nesses o elemento não é `:first-child`
   do bloco, a regra não bate). `:first-child` aqui é relativo ao bloco imediato (o
   `stVerticalBlock` do card OU de cada coluna dentro dele), não ao card inteiro — por isso
   funciona igual pro caso "tabela+gráfico em 2 colunas" e pro caso "1 elemento só". */
div[class*="st-key-bmt-card-"] div[data-testid="stElementContainer"]:first-child:has([data-testid="stElementToolbar"]) {
    margin-top: 2rem;
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
        "bg": "#12141a", "surface": "#1a1d24", "surface_alt": "#21252e",
        "border": "#2d323d", "primary": "#5b8def", "primary_soft": "rgba(91, 141, 239, 0.12)",
        "text": "#e4e6eb", "font": "Inter",
    },
    "green-neutral": {
        "label": "Verde Neutro",
        "bg": "#0d0d0d", "surface": "#161616", "surface_alt": "#202020",
        "border": "#303030", "primary": "#1aff80", "primary_soft": "rgba(26, 255, 128, 0.12)",
        "text": "#1aff80", "font": "Share Tech Mono",
    },
    "cyber-dark": {
        "label": "Cyber Dark",
        "bg": "#080914", "surface": "#0f1123", "surface_alt": "#171936",
        "border": "#282c5e", "primary": "#8b5cf6", "primary_soft": "rgba(139, 92, 246, 0.15)",
        "text": "#f3f4f6", "font": "Inter",
    },
    "blau": {
        "label": "Blau (marca)",
        "bg": "#1C1F26", "surface": "#262B33", "surface_alt": "#2F343C",
        "border": "#3A4149", "primary": "#26B4E9", "primary_soft": "rgba(38, 180, 233, 0.12)",
        "text": "#F1F3F5", "font": "Roboto",
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
}}
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
.stApp button[kind="primary"] {{ background: {t["primary"]}; border-color: {t["primary"]}; color: {t["bg"]}; }}
div[data-baseweb="input"], div[data-baseweb="select"] > div {{ background: {t["surface_alt"]}; }}
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
    display: flex; align-items: center; justify-content: center; font-weight: 700;
    box-shadow: 0 0 0 1px {t["primary_soft"]}, 0 2px 8px {t["primary_soft"]};
}}
.bmt-brand-text {{ display: flex; flex-direction: column; line-height: 1; }}
.bmt-brand-name {{ font-size: .875rem; font-weight: 700; letter-spacing: .02em; }}
.bmt-brand-tag {{ font-size: .6rem; letter-spacing: .07em; text-transform: uppercase; opacity: .55; margin-top: .2rem; }}
.bmt-brand-by {{
    margin-left: .5rem; padding: .15rem .6rem; border-radius: 4px; font-size: .68rem; font-weight: 700;
    letter-spacing: .04em; border: 1px solid {t["border"]}; background: {t["surface_alt"]};
}}
/* Header transparente ACIMA da topbar: é nele que mora o botão de reabrir a sidebar. */
[data-testid="stHeader"] {{ height: 52px; background: transparent; z-index: 999995; }}
/* Botão de reabrir a sidebar: canto esquerdo da Topbar, acima da Activity Bar (sem isso ele
   caía em cima do ícone da marca). */
[data-testid="stExpandSidebarButton"] {{
    position: fixed; left: 14px; top: 12px; z-index: 999996; color: {t["text"]};
}}
[data-testid="stSidebar"] > div:first-child {{ padding-top: 52px; }}
.block-container {{ padding-top: 4.5rem !important; }}
/* Botão Sair na Topbar (canto direito, à esquerda do indicador "Running/Stop" e do menu de 3 pontos do Streamlit). */
[class*="st-key-bmt-logout"] {{
    position: fixed; top: 9px; right: 12rem; z-index: 999996; width: auto !important;
}}
[class*="st-key-bmt-logout"] button {{
    min-height: 2.1rem; padding: 0 .75rem; background: {t["surface_alt"]};
    border: 1px solid {t["border"]}; color: {t["text"]};
}}
[class*="st-key-bmt-logout"] button:hover {{ border-color: {t["primary"]}; color: {t["primary"]}; }}
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
  <div class="bmt-brand-icon">⚡</div>
  <div class="bmt-brand-text">
    <span class="bmt-brand-name">Invest SAP</span>
    <span class="bmt-brand-tag">Vendas &amp; Pendências</span>
  </div>
  <span class="bmt-brand-by">SwordPower</span>
</div>
"""


def apply_custom_theme() -> None:
    """Injeta o CSS custom + o logo da Blau + o tema escolhido + a Topbar SwordPower.

    Chamar uma vez só, em `app.py`, antes de `st.navigation(...).run()` — ver docstring
    do módulo pro porquê de bastar uma chamada só e por que é `st.logo()`, não
    `st.markdown` dentro de `st.sidebar`. O tema (Corporativo/Verde Neutro/Cyber Dark/Blau)
    é o mesmo conjunto do app_template e fica em `st.session_state["app_theme"]`; o seletor
    é renderizado por `render_theme_selector()` (chamada depois da navegação, no rodapé
    da sidebar) — aqui só se lê o valor já escolhido.
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


def render_theme_selector() -> None:
    """Seletor de tema na sidebar (mesmos 3 temas do app_template + Blau)."""
    with st.sidebar:
        st.selectbox(
            "Tema",
            list(THEMES),
            format_func=lambda k: THEMES[k]["label"],
            key=_SS_THEME,
        )


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
