"""Kit de UI server-side — o "Streamlit" do app FastAPI + HTMX.

Cada página (`web/views/*.py`) recebe um `Pagina` e um `Ctx` e descreve a tela com a mesma
semântica que tinha no Streamlit (`caption`, `columns`, `metric`, `table`, `tabs`,
`selectbox`...). A diferença está no ciclo de vida:

- Os widgets de filtro são inputs HTML de um único `<form id="pf">` (o conteúdo principal
  da página). Mudar um valor dispara um GET do mesmo endereço com todos os valores na query
  string (`hx-push-url` — o link da tela filtrada pode ser copiado e compartilhado) e o
  servidor devolve só o `<main>` para o HTMX trocar. Nada de WebSocket nem reexecução da
  página no navegador.
- `tabs()` só calcula a aba ativa (as outras nem consultam o DW); `tabs_locais()` troca no
  navegador quando o conteúdo já está calculado e é barato.
- `lazy()` carrega um bloco numa requisição à parte (`hx-trigger="load"`/`"revealed"`), para a
  página aparecer já com o esqueleto enquanto a parte pesada ainda consulta.
- Ações que gravam (páginas Admin) são blocos `acao()` enviados por POST com token CSRF.

Todo texto é escapado; `md()` aceita um markdown mínimo (**negrito**, `código`, *itálico*,
:material/icone:, listas e títulos) aplicado DEPOIS do escape, então dado vindo do DW nunca
vira HTML.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import itertools
import json
import re
import urllib.parse
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

import numpy as np
import pandas as pd
from markupsafe import Markup, escape

from web import charts, fmt

# ── markdown mínimo ──────────────────────────────────────────────────────────

_RE_ICONE = re.compile(r":material/([a-z0-9_]+):")
_RE_NEGRITO = re.compile(r"\*\*(.+?)\*\*", re.S)
_RE_ITALICO = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_RE_CODIGO = re.compile(r"`([^`\n]+)`")


def icone(nome: str, classe: str = "") -> Markup:
    """Ícone Material Symbols (`nome` só [a-z0-9_])."""
    nome = re.sub(r"[^a-z0-9_]", "", nome)
    return Markup(f'<span class="ms {escape(classe)}" aria-hidden="true">{nome}</span>')


def _inline(texto: str) -> str:
    texto = str(escape(texto))
    codigos: list[str] = []

    def _guarda_codigo(m: re.Match) -> str:
        codigos.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codigos) - 1}\x00"

    texto = _RE_CODIGO.sub(_guarda_codigo, texto)
    texto = _RE_NEGRITO.sub(r"<strong>\1</strong>", texto)
    texto = _RE_ITALICO.sub(r"<em>\1</em>", texto)
    texto = _RE_ICONE.sub(lambda m: str(icone(m.group(1))), texto)
    return re.sub(r"\x00(\d+)\x00", lambda m: codigos[int(m.group(1))], texto)


def md(texto: str) -> Markup:
    """Markdown mínimo e seguro (escapa antes de formatar)."""
    blocos: list[str] = []
    lista: list[str] = []
    paragrafo: list[str] = []

    def _fecha() -> None:
        if paragrafo:
            blocos.append("<p>" + "<br>".join(paragrafo) + "</p>")
            paragrafo.clear()
        if lista:
            blocos.append("<ul>" + "".join(f"<li>{i}</li>" for i in lista) + "</ul>")
            lista.clear()

    for linha in str(texto).strip("\n").splitlines():
        crua = linha.strip()
        if not crua:
            _fecha()
            continue
        titulo = re.match(r"^(#{2,5})\s+(.*)$", crua)
        if titulo:
            _fecha()
            nivel = min(len(titulo.group(1)) + 1, 6)
            blocos.append(f"<h{nivel}>{_inline(titulo.group(2))}</h{nivel}>")
        elif re.match(r"^[-*]\s+", crua):
            if paragrafo:
                _fecha()
            lista.append(_inline(re.sub(r"^[-*]\s+", "", crua)))
        elif lista and linha.startswith("  "):
            lista[-1] += " " + _inline(crua)
        else:
            if lista:
                _fecha()
            paragrafo.append(_inline(crua))
    _fecha()
    return Markup("".join(blocos))


def md_inline(texto: str) -> Markup:
    """Markdown mínimo sem parágrafo (rótulos, métricas)."""
    return Markup(_inline(str(texto)))


# ── contexto da requisição ───────────────────────────────────────────────────


class Ctx:
    """Tudo que uma página precisa da requisição.

    Attributes:
        caminho: Path da página (ex.: "/p/estoque").
        params: Query string (GET) como dict de listas.
        form: Corpo do POST (ações do Admin) como dict de listas, ou vazio.
        usuario: Usuário logado (dict do `app_db`).
        estado: Estado da sessão (filtros compartilhados entre páginas).
        slug: Slug da página.
    """

    def __init__(
        self,
        *,
        caminho: str,
        params: dict[str, list[str]],
        form: Optional[dict[str, list[str]]] = None,
        usuario: dict[str, Any],
        estado: dict[str, Any],
        slug: str,
        csrf: str = "",
    ) -> None:
        self.caminho = caminho
        self.params = params
        self.form = form or {}
        self.usuario = usuario
        self.estado = estado
        self.slug = slug
        self.csrf = csrf

    def get(self, chave: str, default: Optional[str] = None) -> Optional[str]:
        """Último valor do parâmetro (o último vence: botão de aba/submit sobrepõe o hidden)."""
        valores = self.params.get(chave)
        return valores[-1] if valores else default

    def getlist(self, chave: str) -> list[str]:
        return list(self.params.get(chave, []))

    def tem(self, chave: str) -> bool:
        return chave in self.params

    @property
    def acao(self) -> Optional[str]:
        valores = self.form.get("_action")
        return valores[-1] if valores else None

    def form_get(self, chave: str, default: str = "") -> str:
        valores = self.form.get(chave)
        return valores[-1] if valores else default

    def query(self, **extra: Any) -> str:
        """Query string atual (sem controles internos) com `extra` sobreposto."""
        base = {k: v for k, v in self.params.items() if not k.startswith("_")}
        for k, v in extra.items():
            base[k] = [str(v)] if not isinstance(v, (list, tuple)) else [str(i) for i in v]
        return urllib.parse.urlencode([(k, i) for k, vs in base.items() for i in vs])


# ── conversão de DataFrame pro navegador ─────────────────────────────────────


def _json_valor(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) or np.isinf(v) else float(v)
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (pd.Timestamp, _dt.datetime)):
        if pd.isna(v):
            return None
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v if isinstance(v, (int, str)) else str(v)


def _fmt_padrao(serie: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(serie):
        return "bool"
    if pd.api.types.is_integer_dtype(serie):
        return "num0"
    if pd.api.types.is_float_dtype(serie):
        return "num2"
    if pd.api.types.is_datetime64_any_dtype(serie):
        return "date"
    return "text"


def json_script(dados: Any) -> Markup:
    """JSON seguro para `<script type="application/json">` (não fecha a tag, não executa)."""
    texto = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    return Markup(texto.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _atrs(**atrs: Any) -> Markup:
    partes = []
    for k, v in atrs.items():
        if v is None or v is False:
            continue
        nome = k.rstrip("_").replace("_", "-")
        partes.append(nome if v is True else f'{nome}="{escape(v)}"')
    return Markup(" ".join(partes))


_ids = itertools.count(1)


def _novo_id(prefixo: str) -> str:
    return f"{prefixo}{next(_ids)}"


# ── árvore de componentes ────────────────────────────────────────────────────


class Node:
    """Contêiner de componentes; os métodos espelham a API do Streamlit."""

    def __init__(self, pagina: "Pagina", tag: str = "div", classe: str = "", atributos: Optional[dict] = None) -> None:
        self.pagina = pagina
        self.tag = tag
        self.classe = classe
        self.atributos = atributos or {}
        self.filhos: list[Any] = []
        self.acao_nome: Optional[str] = None
        self.acao_manter = False

    # ── infraestrutura ───────────────────────────────────────────────────

    @property
    def ctx(self) -> Ctx:
        return self.pagina.ctx

    def _add(self, html: Any) -> None:
        self.filhos.append(html)

    def _filho(self, tag: str = "div", classe: str = "", **atributos: Any) -> "Node":
        filho = Node(self.pagina, tag, classe, atributos)
        filho.acao_nome, filho.acao_manter = self.acao_nome, self.acao_manter
        self.filhos.append(filho)
        return filho

    def __enter__(self) -> "Node":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def render(self) -> Markup:
        corpo = Markup("").join(f.render() if isinstance(f, Node) else Markup(f) for f in self.filhos)
        if not self.tag:
            return corpo
        return Markup(f"<{self.tag} {_atrs(class_=self.classe or None, **self.atributos)}>{corpo}</{self.tag}>")

    # ── texto ────────────────────────────────────────────────────────────

    def title(self, texto: str, icone_nome: Optional[str] = None) -> None:
        self.pagina.titulo = texto
        ic = icone(icone_nome, "titulo-icone") if icone_nome else ""
        self._add(Markup(f'<h1 class="titulo">{ic}{escape(texto)}</h1>'))

    def header(self, texto: str) -> None:
        self._add(Markup(f"<h2>{md_inline(texto)}</h2>"))

    def subheader(self, texto: str) -> None:
        self._add(Markup(f"<h3>{md_inline(texto)}</h3>"))

    def markdown(self, texto: str) -> None:
        self._add(Markup(f'<div class="md">{md(texto)}</div>'))

    def caption(self, texto: str) -> None:
        self._add(Markup(f'<div class="caption">{md(texto)}</div>'))

    def text(self, texto: str) -> None:
        self._add(Markup(f"<p>{escape(texto)}</p>"))

    def code(self, texto: str) -> None:
        self._add(Markup(f'<pre class="codigo"><code>{escape(texto)}</code></pre>'))

    def divider(self) -> None:
        self._add(Markup('<hr class="divisor">'))

    def html(self, trecho: Markup) -> None:
        """HTML já seguro (use só com `Markup` montado com `escape`)."""
        self._add(trecho)

    def _alerta(self, tipo: str, texto: str, icone_nome: str) -> None:
        self._add(
            Markup(
                f'<div class="alerta alerta--{tipo}" role="{"alert" if tipo in ("erro", "aviso") else "status"}">'
                f"{icone(icone_nome)}<div>{md(texto)}</div></div>"
            )
        )

    def info(self, texto: str, icone_nome: str = "info") -> None:
        self._alerta("info", texto, icone_nome)

    def warning(self, texto: str, icone_nome: str = "warning") -> None:
        self._alerta("aviso", texto, icone_nome)

    def error(self, texto: str, icone_nome: str = "error") -> None:
        self._alerta("erro", texto, icone_nome)

    def success(self, texto: str, icone_nome: str = "check_circle") -> None:
        self._alerta("ok", texto, icone_nome)

    def badge(self, texto: str, tipo: str = "muted") -> Markup:
        tipo = tipo if tipo in ("ok", "erro", "aviso", "primario", "muted") else "muted"
        return Markup(f'<span class="pill pill--{tipo}">{escape(texto)}</span>')

    # ── layout ───────────────────────────────────────────────────────────

    def columns(self, spec: int | Sequence[float], gap: str = "md") -> list["Node"]:
        pesos = [1.0] * spec if isinstance(spec, int) else [float(p) for p in spec]
        grade = self._filho(
            "div", f"colunas colunas--{gap}", style="grid-template-columns:" + " ".join(f"minmax(0,{p:g}fr)" for p in pesos)
        )
        return [grade._filho("div", "coluna") for _ in pesos]

    def container(self, classe: str = "") -> "Node":
        return self._filho("div", classe)

    def card(self, rotulo: Optional[str] = None) -> "Node":
        card = self._filho("section", "card")
        if rotulo:
            card._add(Markup(f'<p class="card-rotulo">{escape(rotulo)}</p>'))
        return card

    def expander(self, rotulo: str, aberto: bool = False) -> "Node":
        det = self._filho("details", "expander", open=aberto or None)
        det._add(Markup(f"<summary>{icone('expand_more', 'expander-seta')}{md_inline(rotulo)}</summary>"))
        return det._filho("div", "expander-corpo")

    def tabs(self, rotulos: Sequence[str], chave: str) -> "Abas":
        """Abas server-side: só a ativa é calculada (`abas.ativa`, `abas.corpo`)."""
        ativa = self.ctx.get(chave)
        indice = int(ativa) if ativa and ativa.isdigit() and int(ativa) < len(rotulos) else 0
        barra = self._filho("div", "abas", role="tablist")
        barra._add(Markup(f'<input type="hidden" name="{escape(chave)}" value="{indice}" form="pf">'))
        for i, rotulo in enumerate(rotulos):
            barra._add(
                Markup(
                    f'<button type="submit" form="pf" name="{escape(chave)}" value="{i}" role="tab" '
                    f'aria-selected="{"true" if i == indice else "false"}" class="aba{" aba--on" if i == indice else ""}">'
                    f"{escape(rotulo)}</button>"
                )
            )
        return Abas(indice, self._filho("div", "abas-corpo", role="tabpanel"))

    def tabs_locais(self, rotulos: Sequence[str]) -> list["Node"]:
        """Abas trocadas no navegador (conteúdo de todas já calculado — use quando é barato)."""
        grupo = self._filho("div", "abas-locais")
        barra = grupo._filho("div", "abas", role="tablist")
        paineis = []
        for i, rotulo in enumerate(rotulos):
            barra._add(
                Markup(
                    f'<button type="button" role="tab" data-aba-local="{i}" '
                    f'aria-selected="{"true" if i == 0 else "false"}" class="aba{" aba--on" if i == 0 else ""}">'
                    f"{escape(rotulo)}</button>"
                )
            )
        for i in range(len(rotulos)):
            paineis.append(grupo._filho("div", "aba-painel", role="tabpanel", hidden=(i != 0) or None, data_painel=str(i)))
        return paineis

    # ── métricas ─────────────────────────────────────────────────────────

    def metric(
        self, rotulo: str, valor: Any, delta: Optional[str] = None, help: Optional[str] = None, tipo_delta: str = "neutro"
    ) -> None:
        ajuda = (
            Markup(f' <span class="ajuda" tabindex="0" data-tip="{escape(help)}">{icone("help")}</span>') if help else ""
        )
        d = Markup(f'<div class="metrica-delta metrica-delta--{escape(tipo_delta)}">{escape(delta)}</div>') if delta else ""
        self._add(
            Markup(
                f'<div class="metrica"><div class="metrica-rotulo">{md_inline(rotulo)}{ajuda}</div>'
                f'<div class="metrica-valor{" metrica-valor--texto" if len(str(valor)) > 18 else ""}">{escape(valor)}</div>{d}</div>'
            )
        )

    def metrics(self, itens: Iterable[tuple]) -> None:
        """Linha de métricas: cada item = (rótulo, valor[, delta[, help]])."""
        itens = list(itens)
        for coluna, item in zip(self.columns(len(itens)), itens):
            coluna.metric(*item)

    def valor_por_moeda(self, df: pd.DataFrame, valor_col: str, moeda_col: str = "Moeda", prefixo: str = "") -> None:
        """1 métrica por moeda, BRL primeiro (porte de `ui_theme.render_valor_por_moeda`)."""
        if df.empty or moeda_col not in df.columns:
            self.caption("Sem dado de moeda disponível.")
            return
        resumo = df.groupby(moeda_col)[valor_col].sum().sort_values(ascending=False)
        resumo = resumo[resumo != 0]
        if resumo.empty:
            self.caption("Sem valor a mostrar.")
            return
        ordem = [m for m in ("BRL",) if m in resumo.index] + [m for m in resumo.index if m != "BRL"]
        self.metrics((f"{prefixo} {m}".strip(), fmt.moeda(resumo[m], str(m), 0)) for m in ordem)

    # ── dados ────────────────────────────────────────────────────────────

    def table(
        self,
        df: pd.DataFrame,
        formatos: Optional[dict[str, str]] = None,
        formato_todas: Optional[str] = None,
        indice: bool = False,
        selecao: Optional[str] = None,
        coluna_selecao: Optional[str] = None,
        selecionado: Optional[str] = None,
        altura: Optional[int] = None,
        nome_arquivo: str = "dados",
    ) -> None:
        """Tabela interativa (ordenar, filtrar, redimensionar, baixar CSV).

        Args:
            df: Dados.
            formatos: Coluna → código de formato (`web/fmt.py`); o resto é inferido pelo dtype.
            formato_todas: Formato aplicado a todas as colunas numéricas (pivots).
            indice: Mostrar o índice como primeira(s) coluna(s) (pivot, groupby).
            selecao: Nome do parâmetro que recebe o valor da linha clicada (e recarrega a
                página) — equivalente ao `on_select` do `st.dataframe`.
            coluna_selecao: Coluna cujo valor vai para o parâmetro `selecao`.
            selecionado: Valor atualmente selecionado (destaca a linha).
            altura: Altura máxima em px (default: cresce até ~440px e rola).
        """
        if indice:
            df = df.reset_index()
        df = df.loc[:, ~df.columns.duplicated()]
        formatos = dict(formatos or {})
        colunas = []
        for i, nome in enumerate(df.columns):
            serie = df[nome]
            codigo = formatos.get(str(nome))
            if codigo is None:
                codigo = formato_todas if formato_todas and pd.api.types.is_numeric_dtype(serie) and not pd.api.types.is_bool_dtype(serie) else _fmt_padrao(serie)
            colunas.append({"field": f"c{i}", "title": str(nome), "fmt": codigo})
        linhas = [
            {f"c{i}": _json_valor(v) for i, v in enumerate(registro)}
            for registro in df.itertuples(index=False, name=None)
        ]
        spec: dict[str, Any] = {"columns": colunas, "rows": linhas, "file": nome_arquivo}
        if selecao and coluna_selecao is not None and coluna_selecao in df.columns:
            spec["select"] = {
                "param": selecao,
                "field": f"c{list(df.columns).index(coluna_selecao)}",
                "current": selecionado,
            }
        if altura:
            spec["height"] = altura
        id_ = _novo_id("tb")
        self._add(
            Markup(
                f'<div class="tabela" id="{id_}"><div class="tabela-alvo"></div>'
                f'<script type="application/json" class="tabela-dados">{json_script(spec)}</script></div>'
            )
        )
        if selecao:
            self._add(
                Markup(f'<input type="hidden" name="{escape(selecao)}" value="{escape(selecionado or "")}" form="pf" data-selecao>')
            )

    def html_table(self, colunas: Sequence[str], linhas: Sequence[Sequence[Any]]) -> None:
        """Tabela HTML simples (listas curtas com selos). Células: `Markup` ou texto (escapado)."""
        cab = Markup("").join(Markup(f"<th>{escape(c)}</th>") for c in colunas)
        corpo = Markup("").join(
            Markup("<tr>") + Markup("").join(Markup(f"<td>{escape(c)}</td>") for c in linha) + Markup("</tr>")
            for linha in linhas
        )
        self._add(Markup(f'<div class="tabela-simples"><table><thead><tr>{cab}</tr></thead><tbody>{corpo}</tbody></table></div>'))

    def chart(self, especificacao: dict[str, Any]) -> None:
        self._add(
            Markup(
                f'<div class="grafico" style="height:{int(especificacao.get("height", 300))}px">'
                f'<script type="application/json" class="grafico-dados">{json_script(especificacao)}</script></div>'
            )
        )

    def bar_chart(self, dados: pd.Series | pd.DataFrame, horizontal: bool = False, stack: bool = False, fmt_valor: str = "num0", altura: int = 300) -> None:
        if dados is None or len(dados) == 0:
            return
        self.chart(charts.spec(dados, "bar", horizontal=horizontal, stack=stack, fmt=fmt_valor, altura=altura))

    def line_chart(self, dados: pd.Series | pd.DataFrame, fmt_valor: str = "num0", altura: int = 300) -> None:
        if dados is None or len(dados) == 0:
            return
        self.chart(charts.spec(dados, "line", fmt=fmt_valor, altura=altura))

    def lazy(self, bloco: str, altura: int = 160, gatilho: str = "load", **extra: Any) -> None:
        """Bloco carregado numa requisição separada (`BLOCOS[bloco]` da view)."""
        url = f"{self.ctx.caminho}/_bloco/{urllib.parse.quote(bloco)}?{self.ctx.query(**extra)}"
        self._add(
            Markup(
                f'<div class="lazy" hx-get="{escape(url)}" hx-trigger="{escape(gatilho)}" hx-swap="outerHTML">'
                f'<div class="esqueleto" style="min-height:{int(altura)}px"><span class="spinner"></span>'
                f'<span>Carregando…</span></div></div>'
            )
        )

    # ── widgets de filtro (GET) ──────────────────────────────────────────

    def _valor_widget(self, chave: str, compartilhado: bool) -> Optional[list[str]]:
        """Valores enviados para o widget; None = primeira carga (usar default)."""
        if self.acao_nome:
            if self.ctx.acao == self.acao_nome and self.acao_manter and chave in self.ctx.form:
                return list(self.ctx.form[chave])
            return None
        if self.ctx.tem(chave):
            valores = self.ctx.getlist(chave)
            if compartilhado:
                self.ctx.estado[chave] = valores
            return valores
        if compartilhado and chave in self.ctx.estado:
            return list(self.ctx.estado[chave])
        return None

    def _campo(self, rotulo: str, controle: Markup, help: Optional[str] = None, grupo: bool = False) -> None:
        # `grupo=True` (várias opções clicáveis): <div>, não <label> — um <label> em volta
        # ativaria o 1º checkbox/radio ao clicar no rótulo.
        ajuda = Markup(f'<span class="ajuda" tabindex="0" data-tip="{escape(help)}">{icone("help")}</span>') if help else ""
        tag = "div" if grupo else "label"
        self._add(Markup(f'<{tag} class="campo"><span class="campo-rotulo">{md_inline(rotulo)}{ajuda}</span>{controle}</{tag}>'))

    def _form_attr(self) -> Markup:
        # Dentro de uma ação (POST), o input não pertence ao form de filtros (não vai pra URL).
        return Markup('form="_acao"') if self.acao_nome else Markup('form="pf" data-w')

    def selectbox(
        self,
        rotulo: str,
        opcoes: Sequence[Any],
        chave: str,
        default: Any = None,
        formatar: Callable[[Any], str] = str,
        valor_de: Callable[[Any], str] = str,
        help: Optional[str] = None,
        compartilhado: bool = False,
        dim: Optional[str] = None,
        placeholder: Optional[str] = None,
    ) -> Any:
        """Select de 1 valor. `dim` = lista grande carregada do IndexedDB (ver `/api/dim`)."""
        opcoes = list(opcoes)
        enviados = self._valor_widget(chave, compartilhado)
        atual = default if default is not None else (opcoes[0] if opcoes and not placeholder else None)
        if enviados:
            escolhido = next((o for o in opcoes if valor_de(o) == enviados[-1]), None)
            if escolhido is not None or not opcoes:
                atual = escolhido if escolhido is not None else enviados[-1]
            elif placeholder and enviados[-1] == "":
                atual = None
        partes = []
        if placeholder:
            partes.append(Markup(f'<option value="">{escape(placeholder)}</option>'))
        # Com `dim`, só a 1ª opção ("Todos") e a escolhida vão no HTML; o resto vem do IndexedDB.
        lista = opcoes if not dim else [o for i, o in enumerate(opcoes) if i == 0 or (atual is not None and valor_de(o) == valor_de(atual))]
        for o in lista:
            sel = " selected" if atual is not None and valor_de(o) == valor_de(atual) else ""
            partes.append(Markup(f'<option value="{escape(valor_de(o))}"{sel}>{escape(formatar(o))}</option>'))
        dim_attr = ""
        if dim:
            from web.dims import versao  # import tardio: dims importa as views

            dim_attr = Markup(f' data-dim="{escape(dim)}" data-dim-ver="{versao([valor_de(o) for o in opcoes])}"')
        self._campo(
            rotulo,
            Markup(f'<select name="{escape(chave)}" {self._form_attr()}{dim_attr}>{Markup("").join(partes)}</select>'),
            help,
        )
        return atual

    def multiselect(
        self,
        rotulo: str,
        opcoes: Sequence[Any],
        chave: str,
        default: Sequence[Any] = (),
        formatar: Callable[[Any], str] = str,
        help: Optional[str] = None,
    ) -> list[Any]:
        opcoes = list(opcoes)
        enviados = self._valor_widget(chave, False)
        if enviados is None:
            atuais = [str(o) for o in default]
        else:
            atuais = [v for v in enviados if v]
        escolhidos = [o for o in opcoes if str(o) in atuais]
        itens = Markup("").join(
            Markup(
                f'<label class="multi-item"><input type="checkbox" name="{escape(chave)}" value="{escape(str(o))}" '
                f'{self._form_attr()}{" checked" if str(o) in atuais else ""}><span>{escape(formatar(o))}</span></label>'
            )
            for o in opcoes
        )
        resumo = f"{len(escolhidos)} selecionado(s)" if escolhidos else "Todos"
        controle = Markup(
            f'<details class="multi"><summary>{escape(resumo)}</summary>'
            f'<input type="hidden" name="{escape(chave)}" value="" {self._form_attr()}>'
            f'<div class="multi-lista">{itens}</div></details>'
        )
        self._campo(rotulo, controle, help, grupo=True)
        return escolhidos

    def text_input(
        self,
        rotulo: str,
        chave: str,
        default: str = "",
        placeholder: str = "",
        help: Optional[str] = None,
        tipo: str = "text",
        autocomplete: str = "off",
        compartilhado: bool = False,
    ) -> str:
        enviados = self._valor_widget(chave, compartilhado)
        valor = enviados[-1] if enviados else default
        mostrar = "" if tipo == "password" else valor
        self._campo(
            rotulo,
            Markup(
                f'<input type="{escape(tipo)}" name="{escape(chave)}" value="{escape(mostrar)}" '
                f'placeholder="{escape(placeholder)}" autocomplete="{escape(autocomplete)}" {self._form_attr()}>'
            ),
            help,
        )
        return valor

    def text_area(self, rotulo: str, chave: str, default: str = "", help: Optional[str] = None) -> str:
        enviados = self._valor_widget(chave, False)
        valor = enviados[-1] if enviados else default
        self._campo(rotulo, Markup(f'<textarea name="{escape(chave)}" rows="3" {self._form_attr()}>{escape(valor)}</textarea>'), help)
        return valor

    def number_input(self, rotulo: str, chave: str, minimo: int, maximo: int, default: int, passo: int = 1, help: Optional[str] = None) -> int:
        enviados = self._valor_widget(chave, False)
        valor = _int_limitado(enviados[-1] if enviados else None, default, minimo, maximo)
        self._campo(
            rotulo,
            Markup(
                f'<input type="number" name="{escape(chave)}" value="{valor}" min="{minimo}" max="{maximo}" '
                f'step="{passo}" {self._form_attr()}>'
            ),
            help,
        )
        return valor

    def slider(self, rotulo: str, chave: str, minimo: int, maximo: int, default: int, passo: int = 1, help: Optional[str] = None) -> int:
        enviados = self._valor_widget(chave, False)
        valor = _int_limitado(enviados[-1] if enviados else None, default, minimo, maximo)
        self._campo(
            rotulo,
            Markup(
                f'<span class="slider"><input type="range" name="{escape(chave)}" value="{valor}" min="{minimo}" '
                f'max="{maximo}" step="{passo}" {self._form_attr()}><output>{valor}</output></span>'
            ),
            help,
        )
        return valor

    def checkbox(self, rotulo: str, chave: str, default: bool = False, help: Optional[str] = None) -> bool:
        enviados = self._valor_widget(chave, False)
        valor = default if enviados is None else enviados[-1] == "1"
        ajuda = Markup(f'<span class="ajuda" tabindex="0" data-tip="{escape(help)}">{icone("help")}</span>') if help else ""
        self._add(
            Markup(
                f'<label class="check"><input type="hidden" name="{escape(chave)}" value="0" {self._form_attr()}>'
                f'<input type="checkbox" name="{escape(chave)}" value="1" {self._form_attr()}{" checked" if valor else ""}>'
                f"<span>{md_inline(rotulo)}</span>{ajuda}</label>"
            )
        )
        return valor

    toggle = checkbox

    def radio(self, rotulo: str, opcoes: Sequence[str], chave: str, default: Optional[str] = None, compartilhado: bool = False) -> str:
        enviados = self._valor_widget(chave, compartilhado)
        atual = default if default in opcoes else opcoes[0]
        if enviados and enviados[-1] in opcoes:
            atual = enviados[-1]
        itens = Markup("").join(
            Markup(
                f'<label class="radio-item"><input type="radio" name="{escape(chave)}" value="{escape(o)}" '
                f'{self._form_attr()}{" checked" if o == atual else ""}><span>{escape(o)}</span></label>'
            )
            for o in opcoes
        )
        self._campo(rotulo, Markup(f'<span class="radio">{itens}</span>'), grupo=True)
        return atual

    def date_input(
        self,
        rotulo: str,
        chave: str,
        default: _dt.date,
        maximo: Optional[_dt.date] = None,
        compartilhado: bool = False,
    ) -> _dt.date:
        enviados = self._valor_widget(chave, compartilhado)
        valor = default
        if enviados and enviados[-1]:
            try:
                valor = _dt.date.fromisoformat(enviados[-1])
            except ValueError:
                pass
        if maximo and valor > maximo:
            valor = maximo
        limite = f' max="{maximo.isoformat()}"' if maximo else ""
        self._campo(
            rotulo,
            Markup(f'<input type="date" name="{escape(chave)}" value="{valor.isoformat()}"{limite} {self._form_attr()}>'),
        )
        return valor

    def button(self, rotulo: str, chave: str, primario: bool = False, desabilitado: bool = False, icone_nome: Optional[str] = None) -> bool:
        """Botão de GET (ex.: "Rastrear"): True na requisição em que foi clicado."""
        ic = icone(icone_nome) if icone_nome else ""
        self._add(
            Markup(
                f'<button type="submit" form="pf" name="_btn" value="{escape(chave)}" '
                f'class="botao{" botao--primario" if primario else ""}"{" disabled" if desabilitado else ""}>'
                f"{ic}{escape(rotulo)}</button>"
            )
        )
        return self.ctx.get("_btn") == chave and not desabilitado

    def hidden(self, chave: str, valor: str) -> None:
        self._add(Markup(f'<input type="hidden" name="{escape(chave)}" value="{escape(valor)}" form="pf">'))

    def link(self, rotulo: str, href: str, icone_nome: Optional[str] = None, classe: str = "link") -> None:
        ic = icone(icone_nome) if icone_nome else ""
        # Download não pode passar pelo hx-boost (ele tentaria trocar a página pelo arquivo).
        extra = Markup(' hx-boost="false" download') if "/_download/" in href else ""
        self._add(Markup(f'<a class="{escape(classe)}" href="{escape(href)}"{extra}>{ic}{escape(rotulo)}</a>'))

    # ── ações (POST, páginas Admin) ──────────────────────────────────────

    @contextmanager
    def acao(self, nome: str, manter_valores: bool = False) -> Iterator["Node"]:
        """Bloco de formulário de gravação. Os campos dentro não vão para a URL.

        Args:
            nome: Identificador da ação (chega em `ctx.acao`).
            manter_valores: Re-renderizar com o que foi enviado (ex.: ação falhou).
        """
        bloco = self._filho("div", "acao", data_acao=nome)
        bloco.acao_nome, bloco.acao_manter = nome, manter_valores
        yield bloco

    def submit(self, rotulo: str, valor: Optional[str] = None, primario: bool = True, icone_nome: Optional[str] = None, confirmar: Optional[str] = None, desabilitado: bool = False) -> None:
        """Botão que envia o bloco `acao()` mais próximo por POST."""
        acao = valor or self.acao_nome or ""
        ic = icone(icone_nome) if icone_nome else ""
        conf = Markup(f' hx-confirm="{escape(confirmar)}"') if confirmar else ""
        self._add(
            Markup(
                f'<button type="button" class="botao{" botao--primario" if primario else ""}" '
                f'hx-post="{escape(self.ctx.caminho)}" hx-include="closest [data-acao]" '
                f'hx-target="#main" hx-swap="outerHTML" name="_action" value="{escape(acao)}"{conf}'
                f'{" disabled" if desabilitado else ""}>{ic}{escape(rotulo)}</button>'
            )
        )


class Abas:
    """Resultado de `Node.tabs`: índice da aba ativa + contêiner do conteúdo dela."""

    def __init__(self, ativa: int, corpo: Node) -> None:
        self.ativa = ativa
        self.corpo = corpo


def _int_limitado(texto: Optional[str], default: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(float(texto)) if texto not in (None, "") else default
    except ValueError:
        valor = default
    return max(minimo, min(maximo, valor))


class Pagina(Node):
    """Raiz de uma página: guarda título, toasts e o contexto."""

    def __init__(self, ctx: Ctx) -> None:
        self._ctx = ctx
        super().__init__(self, tag="", classe="")
        self.titulo = ""
        self.toasts: list[tuple[str, str]] = []

    @property
    def ctx(self) -> Ctx:
        return self._ctx

    def toast(self, texto: str, tipo: str = "ok") -> None:
        self.toasts.append((tipo, texto))
