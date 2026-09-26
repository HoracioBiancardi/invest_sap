/* Invest SAP — comportamento do lado do navegador. Cópia do app.js do htmx_kit do app_template
   (padrão do ecossistema), com as rotas/cookies daqui e a medição de força de senha.
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
          return document.createTextNode(formatar(v, c.fmt));  // nó de texto: o Tabulator poria string como HTML
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
  // todo campo de senha ganha o SHOW/HIDE (= ligarMostrarSenha da SPA)
  function ligarMostrarSenha(raiz) {
    if (!raiz.querySelectorAll) return;
    raiz.querySelectorAll('input[type="password"]').forEach((campo) => {
      if (campo.parentElement.querySelector("[data-mostra-senha]")) return;
      const caixa = document.createElement("span");
      caixa.className = "senha-caixa";
      campo.replaceWith(caixa);
      const botao = document.createElement("button");
      botao.type = "button"; botao.className = "senha-mostra"; botao.dataset.mostraSenha = ""; botao.textContent = "SHOW";
      caixa.append(campo, botao);
    });
  }

  function iniciar(raiz) {
    ligarMostrarSenha(raiz);
    if (raiz.querySelector && raiz.querySelector("[data-filtra-lateral]")) filtrarLateral();
    raiz.querySelectorAll(".tabela").forEach((el) => aoVisivel.observe(el));
    raiz.querySelectorAll(".grafico").forEach((el) => aoVisivel.observe(el));
    raiz.querySelectorAll("select[data-dim]").forEach((s) => { carregarDim(s).catch(() => {}); });
    // consoles (logs, progresso) mostram sempre a última linha; htmx.onLoad também entrega o próprio .terminal (oob)
    [raiz, ...raiz.querySelectorAll(".terminal")].forEach((t) => { if (t.classList && t.classList.contains("terminal")) t.scrollTop = t.scrollHeight; });
    raiz.querySelectorAll("template.toast-dados").forEach((t) => {
      toast(t.content.textContent, t.dataset.tipo);
      t.remove();
    });
  }

  function toast(texto, tipo) {
    const caixa = document.getElementById("toasts");
    if (!caixa) return;
    const el = document.createElement("div");
    el.className = "toast" + ({ erro: " toast--erro", aviso: " toast--aviso", info: " toast--info" }[tipo] || "");
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
      try { localStorage.setItem("app-theme", t.value); } catch (_) { /* mesmo tema na SPA (prefs.js) */ }
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
    // Aba de servidor: o conteúdo da aba vira esqueleto na hora (o submit segue normalmente).
    const aba = e.target.closest('button.aba[type="submit"]');
    if (aba && !aba.classList.contains("aba--on")) {
      const corpo = aba.closest(".abas") && aba.closest(".abas").nextElementSibling;
      if (corpo && corpo.classList.contains("abas-corpo")) {
        aba.parentElement.querySelectorAll(".aba").forEach((b) => b.classList.toggle("aba--on", b === aba));
        corpo.innerHTML = ESQUELETO_ABA;  // HTML fixo, sem dado externo
      }
    }
    const usar = e.target.closest("[data-usar-senha]");
    if (usar) {
      const caixa = usar.closest("form, .acao");
      ["nova", "conf", "novo_senha"].forEach((n) => { const i = caixa && caixa.querySelector('input[name="' + n + '"]'); if (i) i.value = usar.dataset.usarSenha; });
      const nova = caixa && caixa.querySelector('input[name="nova"]');
      if (nova) medirForca(nova);
    }
    if (e.target.closest("[data-alterna-lateral]")) alternarLateral();

  });

  // ── senha: SHOW/HIDE (todos os campos de senha) ────────────────────────────────────
  document.addEventListener("click", (e) => {
    const botao = e.target.closest("[data-mostra-senha]");
    if (!botao) return;
    const campo = botao.parentElement.querySelector("input");
    const mostrar = campo.type === "password";
    campo.type = mostrar ? "text" : "password";
    botao.textContent = mostrar ? "HIDE" : "SHOW";
  });

  // ── sair / auto-lock (mesma preferência da SPA: localStorage "app-autolock-minutes") ──
  function sair(porInatividade) {
    if (!document.querySelector(".shell")) return;  // tela de acesso: nada a bloquear
    const corpo = new URLSearchParams({ motivo: porInatividade ? "inatividade" : "" });
    fetch("/logout", { method: "POST", headers: { "X-CSRF-Token": csrf() }, body: corpo, credentials: "same-origin", redirect: "follow" })
      .then((r) => { location.href = r.redirected ? r.url : "/login?motivo=" + (porInatividade ? "inatividade" : "1"); })
      .catch(() => { location.href = "/login?motivo=" + (porInatividade ? "inatividade" : "1"); });
  }
  function minutosAutoLock() {
    let v = null;
    try { v = localStorage.getItem("app-autolock-minutes"); } catch (_) { /* sem storage: padrão */ }
    return v === null ? 5 : Number(v);
  }
  let timerLock = null;
  function reiniciarLock() {
    clearTimeout(timerLock);
    const minutos = minutosAutoLock();
    if (minutos > 0 && document.querySelector(".shell")) timerLock = setTimeout(() => sair(true), minutos * 60 * 1000);
  }
  ["mousemove", "keydown", "mousedown", "scroll", "touchstart"].forEach((evt) => window.addEventListener(evt, reiniciarLock, { passive: true }));
  document.addEventListener("click", (e) => { if (e.target.closest("[data-sair]")) { e.preventDefault(); sair(false); } });

  // ── copiar (COPY dos campos e cartões da galeria) ─────────────────────────
  document.addEventListener("click", (e) => {
    const alvo = e.target.closest("[data-copiar], [data-copiar-texto]");
    if (!alvo) return;
    const texto = alvo.dataset.copiarTexto ?? (document.querySelector(alvo.dataset.copiar) || {}).value ?? "";
    if (!texto) { toast("Nada para copiar ainda.", "erro"); return; }
    navigator.clipboard.writeText(texto).then(() => toast("Copiado!"), () => toast("Não foi possível copiar.", "erro"));
  });

  // ── filtro do menu lateral (como o "Filtrar módulos" da SPA) ─────────────
  // O termo sobrevive à troca de página (a lateral é redesenhada), como o filtro da SPA.
  let termoLateral = "";
  function filtrarLateral() {
    const campo = document.querySelector("[data-filtra-lateral]");
    if (campo && campo.value !== termoLateral) campo.value = termoLateral;
    const termo = termoLateral.trim().toLowerCase();
    document.querySelectorAll(".lateral-item").forEach((a) => { a.hidden = termo !== "" && !a.textContent.toLowerCase().includes(termo); });
  }
  document.addEventListener("input", (e) => {
    if (!e.target.matches("[data-filtra-lateral]")) return;
    termoLateral = e.target.value;
    filtrarLateral();
  });

  // ── largura da lateral (arrastar a alça), mesma preferência da SPA (js/prefs.js) ──────
  const LARGURA_CHAVE = "app-sidebar-width", LARGURA_PADRAO = 240, LARGURA_MIN = 160, LARGURA_MAX = 550;
  function aplicarLargura(px, salvar) {
    const v = Math.min(LARGURA_MAX, Math.max(LARGURA_MIN, Math.round(px)));
    document.documentElement.style.setProperty("--lateral-w", v + "px");
    if (salvar) { try { localStorage.setItem(LARGURA_CHAVE, String(v)); } catch (_) { /* sem storage */ } }
  }
  try {
    const salva = parseInt(localStorage.getItem(LARGURA_CHAVE), 10);
    if (Number.isFinite(salva)) aplicarLargura(salva, false);
  } catch (_) { /* sem storage: padrão */ }
  let arrastandoLateral = false;
  function moverAlca(x) {
    const lateral = document.querySelector(".lateral");
    if (arrastandoLateral && lateral) aplicarLargura(x - lateral.getBoundingClientRect().left, true);
  }
  function fimAlca() {
    if (!arrastandoLateral) return;
    arrastandoLateral = false;
    document.querySelector(".lateral")?.classList.remove("redimensionando");
    document.querySelector("[data-alca-lateral]")?.classList.remove("arrastando");
    document.body.style.cursor = ""; document.body.style.userSelect = "";
  }
  function inicioAlca(e) {
    const alca = e.target.closest && e.target.closest("[data-alca-lateral]");
    if (!alca) return;
    arrastandoLateral = true;
    document.querySelector(".lateral")?.classList.add("redimensionando");
    alca.classList.add("arrastando");
    document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none";
    e.preventDefault();
  }
  document.addEventListener("mousedown", inicioAlca);
  document.addEventListener("touchstart", inicioAlca, { passive: false });
  document.addEventListener("mousemove", (e) => moverAlca(e.clientX));
  document.addEventListener("touchmove", (e) => { if (arrastandoLateral) { moverAlca(e.touches[0].clientX); e.preventDefault(); } }, { passive: false });
  document.addEventListener("mouseup", fimAlca);
  document.addEventListener("touchend", fimAlca);
  document.addEventListener("dblclick", (e) => { if (e.target.closest("[data-alca-lateral]")) aplicarLargura(LARGURA_PADRAO, true); });

  // ── configurações (tema + auto-lock), como o modal da SPA ─────────────────
  document.addEventListener("click", (e) => {
    const dialogo = document.getElementById("ajustes");
    if (!dialogo) return;
    if (e.target.closest("[data-abre-ajustes]")) {
      dialogo.querySelector("[data-autolock]").value = String(minutosAutoLock());
      dialogo.showModal();
    } else if (e.target.closest("[data-fecha-ajustes]") || e.target === dialogo) {
      dialogo.close();
    }
  });
  document.addEventListener("change", (e) => {
    if (!e.target.matches("select[data-autolock]")) return;
    try { localStorage.setItem("app-autolock-minutes", e.target.value); } catch (_) { /* sem storage */ }
    reiniciarLock();
  });

  // Mesmo comportamento da SPA (switchTab): clicar na seção já aberta (ícone ou aba da topbar)
  // recolhe/mostra a lateral; ir para outra seção com a lateral recolhida reabre a lateral.
  // Fase de captura: roda antes do hx-boost, que já mandaria a requisição com o cookie antigo.
  document.addEventListener("click", (e) => {
    // Ícone de seção com várias páginas: só troca a lista da lateral. Tem de ser na captura: o
    // hx-boost escuta no próprio link e, na fase de bolha, a navegação já teria saído.
    const secao = e.target.closest("[data-abre-secao]");
    if (secao && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
      e.preventDefault(); e.stopPropagation(); abrirSecao(secao); return;
    }
    const navAtivo = e.target.closest(".rail-item--on:not([data-abre-secao]):not([data-abre-ajustes])");
    if (navAtivo) { e.preventDefault(); e.stopPropagation(); alternarLateral(); return; }
    const navOutro = e.target.closest(".rail-item:not(.rail-item--on):not([data-abre-ajustes])");
    if (navOutro && !lateralVisivel()) mostrarLateral(true);
  }, true);

  // ── menu lateral ─────────────────────────────────────────────────────────
  const ehTelaEstreita = () => window.matchMedia("(max-width: 760px)").matches;  // = @media da lateral em app.css
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


  // Força da senha nova (o servidor mede e devolve o HTML já escapado).
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
  // ── troca de página: esqueleto imediato ──────────────────────────────────
  // Link do menu (navegação boost) apaga o conteúdo atual na hora e mostra um esqueleto até a
  // página nova chegar; o conteúdo antigo volta se a requisição falhar. Filtros não passam por
  // aqui (é a mesma tela, só esmaece).
  const ESQUELETO =
    '<div class="esq" aria-busy="true" aria-label="Carregando página">' +
    '<div class="esq-bloco esq-titulo"></div><div class="esq-bloco esq-linha"></div><div class="esq-bloco esq-linha esq-curta"></div>' +
    '<div class="esq-metricas"><div class="esq-bloco"></div><div class="esq-bloco"></div><div class="esq-bloco"></div><div class="esq-bloco"></div></div>' +
    '<div class="esq-bloco esq-grafico"></div><div class="esq-bloco esq-tabela"></div></div>';
  const ESQUELETO_ABA =
    '<div class="esq" aria-busy="true" aria-label="Carregando aba">' +
    '<div class="esq-metricas"><div class="esq-bloco"></div><div class="esq-bloco"></div><div class="esq-bloco"></div><div class="esq-bloco"></div></div>' +
    '<div class="esq-bloco esq-grafico"></div><div class="esq-bloco esq-tabela"></div></div>';
  let mainAnterior = null;

  function ehNavegacao(elt) {
    return elt && elt.tagName === "A" && !elt.hasAttribute("hx-get") && !elt.hasAttribute("download");
  }

  document.addEventListener("htmx:beforeRequest", (e) => {
    const lateral = document.querySelector(".lateral.aberta");
    if (lateral) lateral.classList.remove("aberta");
    const elt = e.detail.elt;
    if (!ehNavegacao(elt)) return;
    const main = document.getElementById("main");
    if (!main) return;
    // Destaca já no menu a página clicada.
    if (elt.classList.contains("lateral-item")) {
      document.querySelectorAll(".lateral-item--on").forEach((a) => a.classList.remove("lateral-item--on"));
      elt.classList.add("lateral-item--on");
    }
    mainAnterior = main.cloneNode(true);
    main.innerHTML = ESQUELETO;  // HTML fixo, sem dado externo
    window.scrollTo(0, 0);
  });
  function restaurarMain() {
    const main = document.getElementById("main");
    if (mainAnterior && main && main.querySelector(".esq")) {
      main.replaceWith(mainAnterior);
      htmx.process(mainAnterior);
    }
    mainAnterior = null;
  }
  document.addEventListener("htmx:afterSwap", () => { mainAnterior = null; });
  document.addEventListener("htmx:responseError", () => { restaurarMain(); toast("Falha ao carregar — tente de novo.", "erro"); });
  document.addEventListener("htmx:sendError", () => { restaurarMain(); toast("Sem conexão com o servidor.", "erro"); });

  function aoCarregar() {
    reiniciarLock();
    if (window.htmx) htmx.onLoad(iniciar);
    else iniciar(document.body);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", aoCarregar);
  else aoCarregar();
})();
