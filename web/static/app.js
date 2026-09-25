/* Invest SAP — comportamento do lado do navegador.
 *
 * - Filtros: todo input com [data-w] pertence ao <form id="pf">; mudar o valor dispara o GET
 *   do HTMX (a página devolve só o <main>). Multi-seleção só dispara ao fechar a lista.
 * - Tabelas (Tabulator) e gráficos (ECharts) são montados só quando ficam visíveis
 *   (IntersectionObserver) — página longa não paga o custo do que está fora da tela, e o que
 *   está em aba/expander fechado nasce com o tamanho certo quando aparece.
 * - Listas grandes de filtro ([data-dim]) vêm do IndexedDB, rebaixadas só quando a versão muda.
 * Sem eval/inline script (CSP script-src 'self').
 */
(function () {
  "use strict";

  // ── formatação pt-BR (mesmos códigos de web/fmt.py) ──────────────────────
  const nfCache = {};
  function nf(dec) {
    if (!nfCache[dec]) nfCache[dec] = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    return nfCache[dec];
  }
  const nfCompacto = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });

  function formatar(v, codigo) {
    if (v === null || v === undefined || v === "") return "—";
    codigo = codigo || "text";
    if (codigo.startsWith("cur")) {
      const [dec, moeda] = codigo.slice(3).split(":");
      const n = nf(parseInt(dec || "2", 10)).format(v);
      return (moeda || "BRL") === "BRL" ? "R$ " + n : moeda + " " + n;
    }
    const m = codigo.match(/^([a-z]+)(\d*)$/);
    const base = m ? m[1] : codigo;
    const dec = m && m[2] ? parseInt(m[2], 10) : 0;
    switch (base) {
      case "num": return nf(dec).format(v);
      case "brl": return "R$ " + nf(dec).format(v);
      case "pct": return nf(dec).format(v * 100) + "%";
      case "spct": return (v >= 0 ? "+" : "−") + nf(dec).format(Math.abs(v * 100)) + "%";
      case "pctv": return nf(dec).format(v) + "%";
      case "bool": return v ? "Sim" : "Não";
      case "date": case "datetime": {
        const s = String(v);
        const d = s.match(/^(\d{4})-(\d{2})-(\d{2})(.*)$/);
        return d ? d[3] + "/" + d[2] + "/" + d[1] + (base === "datetime" ? d[4] : "") : s;
      }
      default: return String(v);
    }
  }
  function ehNumerico(codigo) { return /^(num|brl|pct|spct|pctv|cur)/.test(codigo || ""); }
  function compacto(v, codigo) {
    if (v === null || v === undefined) return "";
    const base = (codigo || "").replace(/\d+$/, "");
    if (base === "pct") return nf(0).format(v * 100) + "%";
    if (base === "pctv") return nf(0).format(v) + "%";
    const n = Math.abs(v) >= 10000 ? nfCompacto.format(v) : nf(0).format(v);
    return base === "brl" ? "R$ " + n : n;
  }

  function csrf() {
    try { return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || ""; }
    catch (e) { return ""; }
  }
  function css(nome) { return getComputedStyle(document.documentElement).getPropertyValue(nome).trim(); }
  function enviarFiltros() {
    const pf = document.getElementById("pf");
    if (pf) pf.requestSubmit();
  }

  // ── montagem preguiçosa (quando visível) ─────────────────────────────────
  const aoVisivel = new IntersectionObserver((entradas) => {
    for (const e of entradas) {
      if (!e.isIntersecting) continue;
      aoVisivel.unobserve(e.target);
      if (e.target.classList.contains("tabela")) montarTabela(e.target);
      else if (e.target.classList.contains("grafico")) montarGrafico(e.target);
    }
  }, { rootMargin: "200px" });

  // ── tabelas ───────────────────────────────────────────────────────────────
  function paraCsv(codigo) {
    return function (valor) {
      if (valor === null || valor === undefined) return "";
      if (ehNumerico(codigo) && typeof valor === "number") return String(valor).replace(".", ",");
      const s = String(valor);
      // Injeção de fórmula no Excel: célula de texto começando com = + - @ vira texto.
      return /^[=+\-@\t\r]/.test(s) ? "'" + s : s;
    };
  }

  function montarTabela(el) {
    if (el._tabela || typeof Tabulator === "undefined") return;
    const spec = JSON.parse(el.querySelector(".tabela-dados").textContent);
    const alvo = el.querySelector(".tabela-alvo");
    const muitas = spec.rows.length > 15;
    const colunas = spec.columns.map((c) => {
      const numerico = ehNumerico(c.fmt);
      return {
        title: c.title, field: c.field,
        hozAlign: numerico ? "right" : "left", headerHozAlign: numerico ? "right" : "left",
        sorter: numerico || c.fmt === "bool" ? "number" : "alphanum",
        formatter: (cell) => {
          const v = cell.getValue();
          if (v === null || v === undefined || v === "") { cell.getElement().classList.add("celula-vazia"); return "—"; }
          return formatar(v, c.fmt);
        },
        headerFilter: muitas && !numerico ? "input" : false,
        headerFilterPlaceholder: "filtrar…",
        accessorDownload: paraCsv(c.fmt),
        minWidth: 70, maxInitialWidth: 420, tooltip: !numerico,
      };
    });
    const opcoes = {
      data: spec.rows, columns: colunas, layout: "fitDataStretch",
      placeholder: "Sem dados", movableColumns: true, reactiveData: false,
      height: spec.rows.length > 14 ? (spec.height || 440) : false,
      columnDefaults: { resizable: true },
    };
    const sel = spec.select;
    if (sel) {
      opcoes.selectableRows = false;
      opcoes.rowFormatter = (row) => {
        row.getElement().classList.add("tabulator-selectable");
        if (sel.current !== null && sel.current !== undefined && String(row.getData()[sel.field]) === String(sel.current)) {
          row.getElement().classList.add("linha-selecionada");
        }
      };
    }
    const barra = document.createElement("div");
    barra.className = "tabela-barra";
    const qtd = spec.rows.length;
    barra.innerHTML = "<span></span><button type=\"button\"><span class=\"ms\">download</span>CSV</button>";
    barra.firstChild.textContent = nf(0).format(qtd) + (qtd === 1 ? " linha" : " linhas") + (sel ? " · clique numa linha para selecionar" : "");
    el.insertBefore(barra, alvo);
    const tabela = new Tabulator(alvo, opcoes);
    el._tabela = tabela;
    barra.querySelector("button").addEventListener("click", () => {
      tabela.download("csv", (spec.file || "dados") + ".csv", { delimiter: ";", bom: true });
    });
    if (sel) {
      tabela.on("rowClick", (evento, row) => {
        const input = el.parentElement.querySelector('input[data-selecao][name="' + CSS.escape(sel.param) + '"]');
        if (!input) return;
        const valor = row.getData()[sel.field];
        input.value = valor === null || valor === undefined ? "" : String(valor);
        enviarFiltros();
      });
    }
  }

  // ── gráficos ──────────────────────────────────────────────────────────────
  function montarOpcaoGrafico(spec) {
    const cores = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => css("--serie-" + i));
    const texto = css("--text"), textoMudo = css("--text-muted"), superficie = css("--surface");
    const fonte = css("--font");
    const varias = spec.series.length > 1;
    const eixoCat = {
      type: "category", data: spec.categories, inverse: spec.horizontal,
      axisLine: { lineStyle: { color: css("--eixo") } }, axisTick: { show: false },
      axisLabel: { color: textoMudo, hideOverlap: true, fontSize: 11,
        rotate: !spec.horizontal && spec.categories.length > 8 && spec.categories.some((c) => c.length > 10) ? 35 : 0,
        formatter: (v) => (v.length > 28 ? v.slice(0, 27) + "…" : v) },
    };
    const eixoVal = {
      type: "value", splitLine: { lineStyle: { color: css("--grade") } },
      axisLabel: { color: textoMudo, fontSize: 11, formatter: (v) => compacto(v, spec.fmt) },
    };
    const series = spec.series.map((s, i) => {
      const base = { name: s.name, data: s.data, emphasis: { focus: "series" } };
      if (spec.kind === "line") {
        return Object.assign(base, {
          type: "line", symbol: "circle", symbolSize: 8, showSymbol: s.data.length <= 40,
          lineStyle: { width: 2, cap: "round", join: "round" },
          itemStyle: { borderColor: superficie, borderWidth: 2 },
        });
      }
      const serie = Object.assign(base, {
        type: "bar", barMaxWidth: 24, stack: spec.stack ? "total" : undefined,
        itemStyle: {
          borderRadius: spec.stack ? 0 : (spec.horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]),
          borderColor: spec.stack ? superficie : undefined, borderWidth: spec.stack ? 1 : 0,
        },
      });
      if (s.labels) {
        serie.label = {
          show: true, position: spec.horizontal ? "right" : "top", color: texto, fontSize: 11, fontWeight: 600,
          formatter: (p) => s.labels[p.dataIndex] || "",
        };
      }
      return serie;
    });
    return {
      animationDuration: 250, color: cores,
      textStyle: { fontFamily: fonte, color: textoMudo },
      grid: { left: 8, right: 24, top: varias ? 36 : 14, bottom: 8, containLabel: true },
      legend: varias ? { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 8, textStyle: { color: texto } } : undefined,
      tooltip: {
        trigger: "axis", confine: true,
        axisPointer: { type: spec.kind === "line" ? "line" : "shadow" },
        backgroundColor: css("--surface-alt"), borderColor: css("--border-bright"), textStyle: { color: texto },
        valueFormatter: (v) => formatar(v, spec.fmt),
      },
      dataZoom: !spec.horizontal && spec.categories.length > 40 ? [{ type: "inside" }] : undefined,
      xAxis: spec.horizontal ? eixoVal : eixoCat,
      yAxis: spec.horizontal ? eixoCat : eixoVal,
      series: series,
    };
  }

  const redimensionar = new ResizeObserver((entradas) => {
    for (const e of entradas) if (e.target._grafico) e.target._grafico.resize();
  });

  function montarGrafico(el) {
    if (el._grafico || typeof echarts === "undefined") return;
    const spec = JSON.parse(el.querySelector(".grafico-dados").textContent);
    el._spec = spec;
    el._grafico = echarts.init(el, null, { renderer: "canvas" });
    el._grafico.setOption(montarOpcaoGrafico(spec));
    redimensionar.observe(el);
  }

  function repintarGraficos() {
    document.querySelectorAll(".grafico").forEach((el) => {
      if (el._grafico && el._spec) el._grafico.setOption(montarOpcaoGrafico(el._spec), true);
    });
  }

  // ── listas grandes (IndexedDB) ────────────────────────────────────────────
  let bancoPromessa = null;
  function banco() {
    if (!bancoPromessa) {
      bancoPromessa = new Promise((ok, falha) => {
        const req = indexedDB.open("invest-sap", 1);
        req.onupgradeneeded = () => req.result.createObjectStore("dims", { keyPath: "nome" });
        req.onsuccess = () => ok(req.result);
        req.onerror = () => falha(req.error);
      });
    }
    return bancoPromessa;
  }
  async function idb(modo, operacao) {
    const db = await banco();
    return new Promise((ok, falha) => {
      const req = operacao(db.transaction("dims", modo).objectStore("dims"));
      req.onsuccess = () => ok(req.result);
      req.onerror = () => falha(req.error);
    });
  }
  async function carregarDim(select) {
    const nome = select.dataset.dim, versao = select.dataset.dimVer;
    let registro = null;
    try { registro = await idb("readonly", (s) => s.get(nome)); } catch (e) { registro = null; }
    if (!registro || registro.versao !== versao) {
      const resp = await fetch("/api/dim/" + encodeURIComponent(nome), { credentials: "same-origin" });
      if (!resp.ok) return;
      const json = await resp.json();
      registro = { nome: nome, versao: json.versao, itens: json.itens };
      try { await idb("readwrite", (s) => s.put(registro)); } catch (e) { /* sem IndexedDB: só não guarda */ }
    }
    const atual = select.value;
    const frag = document.createDocumentFragment();
    for (const item of registro.itens) {
      const op = document.createElement("option");
      op.value = item; op.textContent = item;
      if (item === atual) op.selected = true;
      frag.appendChild(op);
    }
    select.replaceChildren(frag);
    select.value = atual;
  }

  // ── inicialização de um trecho de DOM (página inteira ou parcial HTMX) ────
  function iniciar(raiz) {
    raiz.querySelectorAll(".tabela").forEach((el) => aoVisivel.observe(el));
    raiz.querySelectorAll(".grafico").forEach((el) => aoVisivel.observe(el));
    raiz.querySelectorAll("select[data-dim]").forEach((s) => { carregarDim(s).catch(() => {}); });
    raiz.querySelectorAll("template.toast-dados").forEach((t) => {
      toast(t.content.textContent, t.dataset.tipo);
      t.remove();
    });
  }

  function toast(texto, tipo) {
    const caixa = document.getElementById("toasts");
    if (!caixa) return;
    const el = document.createElement("div");
    el.className = "toast" + (tipo === "erro" ? " toast--erro" : "");
    el.setAttribute("role", "status");
    el.textContent = texto;
    caixa.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  // ── eventos globais ──────────────────────────────────────────────────────
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.matches("select[data-tema]")) {
      document.documentElement.dataset.theme = t.value;
      document.cookie = "invest_tema=" + encodeURIComponent(t.value) + "; path=/; max-age=31536000; samesite=lax" + (location.protocol === "https:" ? "; secure" : "");
      repintarGraficos();
      return;
    }
    if (!t.matches("[data-w]")) return;
    const multi = t.closest(".multi");
    if (multi) { multi.dataset.sujo = "1"; return; }
    enviarFiltros();
  });

  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.matches('.slider input[type="range"]')) t.nextElementSibling.textContent = nf(0).format(t.value);
    if (t.matches('input[name="nova"]')) medirForca(t);
  });

  // Multi-seleção: aplica ao fechar (clicar no resumo ou fora da lista).
  document.addEventListener("toggle", (e) => {
    const d = e.target;
    if (d.classList && d.classList.contains("multi") && !d.open && d.dataset.sujo) {
      delete d.dataset.sujo;
      enviarFiltros();
    }
  }, true);
  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.multi[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
    const abaLocal = e.target.closest("[data-aba-local]");
    if (abaLocal) {
      const grupo = abaLocal.closest(".abas-locais");
      grupo.querySelectorAll("[data-aba-local]").forEach((b) => {
        const on = b === abaLocal;
        b.classList.toggle("aba--on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      grupo.querySelectorAll(":scope > .aba-painel").forEach((p) => { p.hidden = p.dataset.painel !== abaLocal.dataset.abaLocal; });
    }
    const usar = e.target.closest("[data-usar-senha]");
    if (usar) {
      const caixa = usar.closest("form, .acao");
      ["nova", "conf", "novo_senha"].forEach((n) => { const i = caixa && caixa.querySelector('input[name="' + n + '"]'); if (i) i.value = usar.dataset.usarSenha; });
      const nova = caixa && caixa.querySelector('input[name="nova"]');
      if (nova) medirForca(nova);
    }
    if (e.target.closest("[data-alterna-lateral]")) alternarLateral();
    const secao = e.target.closest("[data-abre-secao]");
    if (secao && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
      e.preventDefault();
      abrirSecao(secao);
    }
  });

  // ── menu lateral ─────────────────────────────────────────────────────────
  const ehTelaEstreita = () => window.matchMedia("(max-width: 1100px)").matches;
  function lateralVisivel() {
    const lateral = document.querySelector(".lateral");
    return ehTelaEstreita() ? lateral.classList.contains("aberta") : !document.querySelector(".shell").classList.contains("shell--recolhida");
  }
  function mostrarLateral(visivel) {
    if (ehTelaEstreita()) {
      document.querySelector(".lateral").classList.toggle("aberta", visivel);
      return;
    }
    document.querySelector(".shell").classList.toggle("shell--recolhida", !visivel);
    // Cookie (não localStorage): o servidor já desenha a página no estado certo, sem piscar.
    document.cookie = "invest_lateral=" + (visivel ? "1" : "0") + "; path=/; max-age=31536000; samesite=lax" + (location.protocol === "https:" ? "; secure" : "");
  }
  function alternarLateral() { mostrarLateral(!lateralVisivel()); }

  // Ícone da seção: troca a lista de páginas na hora (sem ir ao servidor); clicar de novo na
  // seção já aberta recolhe o menu, como a activity bar do VSCode.
  function abrirSecao(item) {
    const id = item.dataset.secao;
    const grupoAtual = document.querySelector(".lateral-grupo:not([hidden])");
    if (grupoAtual && grupoAtual.dataset.secao === id && lateralVisivel()) {
      mostrarLateral(false);
      return;
    }
    document.querySelectorAll(".lateral-grupo").forEach((g) => { g.hidden = g.dataset.secao !== id; });
    document.querySelectorAll(".rail-item").forEach((r) => r.classList.toggle("rail-item--on", r === item));
    mostrarLateral(true);
  }

  let temporizadorForca = null;
  function medirForca(input) {
    const caixa = input.closest("form, .acao");
    const alvo = caixa && caixa.querySelector('.forca-alvo[data-forca-de="nova"]');
    if (!alvo) return;
    clearTimeout(temporizadorForca);
    temporizadorForca = setTimeout(async () => {
      const token = csrf() || (caixa.querySelector('input[name="_csrf"]') || {}).value || "";
      const resp = await fetch("/senha/forca", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": token },
        body: "nova=" + encodeURIComponent(input.value),
      });
      if (resp.ok) alvo.innerHTML = await resp.text();  // HTML montado e escapado pelo servidor
    }, 250);
  }

  // Tabela/gráfico removidos numa troca do HTMX: libera memória.
  document.addEventListener("htmx:beforeCleanupElement", (e) => {
    const el = e.target;
    if (el._grafico) { redimensionar.unobserve(el); el._grafico.dispose(); el._grafico = null; }
    if (el._tabela) { el._tabela.destroy(); el._tabela = null; }
  });
  document.addEventListener("htmx:beforeRequest", () => {
    const lateral = document.querySelector(".lateral.aberta");
    if (lateral) lateral.classList.remove("aberta");
  });
  document.addEventListener("htmx:responseError", () => toast("Falha ao carregar — tente de novo.", "erro"));
  document.addEventListener("htmx:sendError", () => toast("Sem conexão com o servidor.", "erro"));

  function aoCarregar() {
    if (window.htmx) htmx.onLoad(iniciar);
    else iniciar(document.body);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", aoCarregar);
  else aoCarregar();
})();
