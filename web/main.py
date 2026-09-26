"""App web do Invest SAP — FastAPI + Jinja + HTMX (substitui o Streamlit).

Uso:
    uv run python -m web                     # 127.0.0.1:8000, 1 processo
    APP_HOST=0.0.0.0 uv run python -m web    # só atrás do proxy HTTPS (ver docs/COMO_RODAR.md)

**Um processo só, de propósito.** O cofre de credenciais desbloqueado, o lockout de login e o
cache de consultas vivem na memória do processo: com vários workers, cada um pediria a senha
mestra de novo e o lockout seria contornável. As rotas de página são `def` (não `async`), então
o FastAPI roda cada requisição no threadpool — várias consultas ao DW em paralelo, que é onde
está o tempo de verdade.

Ciclo de uma página (`/p/<slug>`): autentica → exige troca de senha pendente → confere perfil
→ trava se o cofre estiver bloqueado → roda `render()` da view → devolve a página inteira
(navegação normal/boost) ou só o `<main>` (mudança de filtro via HTMX).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import traceback
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.exc import DBAPIError
from starlette.middleware.gzip import GZipMiddleware

from scripts import app_db
from scripts.credential_vault import CofreError, CredentialVault
from scripts.db import DatabaseConnectionError
from scripts.password_service import PasswordService
from web import auth, dims
from web.cache import Aquecedor
from web.temas import TEMAS, tema_padrao
from web.ui import Ctx, Pagina, icone
from web.views import POR_SLUG, SECOES, InfoPagina, menu, sementes_de_aquecimento

logger = logging.getLogger("web")

RAIZ = Path(__file__).resolve().parent
COOKIE_SEGURO = os.environ.get("APP_COOKIE_SECURE", "1") != "0"
COOKIE_CSRF_LOGIN = "invest_csrf_login"
COOKIE_TEMA = "invest_tema"

CredentialVault.interativo = False

@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
    auth.garantir_admin_inicial()
    app_db.limpar_sessoes_inativas(auth.idle_segundos())
    Aquecedor.sementes = sementes_de_aquecimento()
    Aquecedor.iniciar(lambda: not CredentialVault.configurado() or CredentialVault.desbloqueado())
    yield
    Aquecedor.parar()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, title="DataPlataform", lifespan=_ciclo_de_vida)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

templates = Jinja2Templates(directory=str(RAIZ / "templates"))


def _versao_estaticos() -> str:
    """Hash dos arquivos estáticos — vai na URL (`?v=`) para cache longo sem ficar velho."""
    h = hashlib.blake2b(digest_size=6)
    for arquivo in sorted((RAIZ / "static").rglob("*")):
        if arquivo.is_file():
            h.update(arquivo.read_bytes())
    return h.hexdigest()


VERSAO_ESTATICOS = _versao_estaticos()
templates.env.globals.update(v=VERSAO_ESTATICOS, icone=icone, TEMAS=TEMAS, SECOES=SECOES)


class EstaticosImutaveis(StaticFiles):
    """Arquivos estáticos com cache longo (a URL muda quando o conteúdo muda)."""

    async def get_response(self, path: str, scope: dict) -> Response:
        resposta = await super().get_response(path, scope)
        if resposta.status_code == 200:
            resposta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resposta


app.mount("/static", EstaticosImutaveis(directory=str(RAIZ / "static")), name="static")

# Estado de sessão em memória (filtros compartilhados entre páginas — o `st.session_state`
# das chaves `flt_*`). Perder no reinício é aceitável: só volta aos defaults.
_ESTADOS: dict[str, dict[str, Any]] = {}

_CSP = (
    "default-src 'self'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next: Any) -> Response:
    resposta: Response = await call_next(request)
    resposta.headers.setdefault("Content-Security-Policy", _CSP)
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("X-Frame-Options", "DENY")
    resposta.headers.setdefault("Referrer-Policy", "same-origin")
    resposta.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resposta.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if COOKIE_SEGURO:
        resposta.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if not request.url.path.startswith("/static/"):
        # Tela com dado comercial: nada de cache no navegador/proxy.
        resposta.headers.setdefault("Cache-Control", "no-store")
    return resposta


# ── helpers de resposta ──────────────────────────────────────────────────────


def _eh_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _eh_parcial(request: Request) -> bool:
    """Requisição HTMX que troca só o <main> (filtro/ação), não navegação boost."""
    return _eh_htmx(request) and request.headers.get("HX-Boosted") != "true"


def _redirecionar(request: Request, destino: str) -> Response:
    if _eh_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": destino})
    return RedirectResponse(destino, status_code=303)


def _cookie(resposta: Response, nome: str, valor: str, *, max_age: Optional[int] = None, httponly: bool = True, samesite: str = "strict") -> None:
    resposta.set_cookie(
        nome, valor, max_age=max_age, httponly=httponly, secure=COOKIE_SEGURO, samesite=samesite, path="/"
    )


def _tema(request: Request) -> str:
    escolhido = request.cookies.get(COOKIE_TEMA, "")
    return escolhido if escolhido in TEMAS else tema_padrao()


def _sessao(request: Request) -> tuple[Optional[dict], Optional[dict]]:
    return auth.sessao_atual(request.cookies.get(auth.COOKIE))


def _csrf_ok(request: Request, sessao: dict, form: Optional[dict[str, list[str]]] = None) -> bool:
    # Reforço (= htmx_kit do app_template): o navegador marca pedidos de outra origem; outra porta
    # ou subdomínio do mesmo domínio é o mesmo "site" e passa pelo SameSite=Strict do cookie.
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        return False
    enviado = request.headers.get("X-CSRF-Token") or ((form or {}).get("_csrf") or [""])[-1]
    return bool(enviado) and secrets.compare_digest(enviado, sessao["csrf"])


async def _form(request: Request) -> dict[str, list[str]]:
    dados = await request.form()
    resultado: dict[str, list[str]] = {}
    for chave, valor in dados.multi_items():
        if isinstance(valor, str):
            resultado.setdefault(chave, []).append(valor)
    return resultado


def _params(request: Request) -> dict[str, list[str]]:
    resultado: dict[str, list[str]] = {}
    for chave, valor in request.query_params.multi_items():
        resultado.setdefault(chave, []).append(valor)
    return resultado


def _estado_cofre() -> dict[str, Any]:
    return CredentialVault.estado()


def _contexto_shell(request: Request, usuario: dict, sessao: dict, pagina: Optional[InfoPagina]) -> dict[str, Any]:
    estado = _estado_cofre()
    return {
        "request": request,
        "usuario": usuario,
        "csrf": sessao["csrf"],
        "tema": _tema(request),
        "menu": menu(usuario["role"]),
        "pagina_atual": pagina,
        "secao_atual": pagina.secao if pagina else None,
        "cofre_liberado": not estado["configurado"] or estado["desbloqueado"],
        "lateral_recolhida": request.cookies.get("invest_lateral") == "0",
    }


def _responder_pagina(
    request: Request, usuario: dict, sessao: dict, pagina: Optional[InfoPagina], p: Pagina, status: int = 200
) -> Response:
    contexto = _contexto_shell(request, usuario, sessao, pagina)
    contexto.update(conteudo=p.render(), titulo=p.titulo or (pagina.titulo if pagina else ""), toasts=p.toasts, caminho=p.ctx.caminho)
    nome = "_main.html" if _eh_parcial(request) else "pagina.html"
    return templates.TemplateResponse(request, nome, contexto, status_code=status)


def _exigir_login(request: Request) -> tuple[Optional[dict], Optional[dict], Optional[Response]]:
    sessao, usuario = _sessao(request)
    if not sessao or not usuario:
        proximo = urllib.parse.quote(request.url.path + (f"?{request.url.query}" if request.url.query else ""), safe="")
        return None, None, _redirecionar(request, f"/login?next={proximo}")
    if usuario["must_change_password"] and request.url.path != "/senha/nova":
        return sessao, usuario, _redirecionar(request, "/senha/nova")
    return sessao, usuario, None


def _executar_view(
    request: Request, info: InfoPagina, usuario: dict, sessao: dict, form: Optional[dict[str, list[str]]]
) -> Pagina:
    ctx = Ctx(
        caminho=info.url,
        params=_params(request),
        form=form,
        usuario=usuario,
        estado=_ESTADOS.setdefault(sessao["token_hash"], {}),
        slug=info.slug,
        csrf=sessao["csrf"],
    )
    p = Pagina(ctx)
    _proteger(p, info.slug, lambda: info.carregar().render(p, ctx))
    return p


def _proteger(p: Pagina, onde: str, desenhar: Any) -> None:
    """Roda a view; erro vira alerta na tela (sem traceback) e traceback no log."""
    try:
        desenhar()
    except DatabaseConnectionError as exc:
        p.error(str(exc), "cloud_off")
    except DBAPIError:
        logger.error("Falha de consulta ao DW em %s:\n%s", onde, traceback.format_exc())
        p.error(
            "A consulta ao DW falhou (instabilidade ou tempo esgotado no SQL Server/HANA). Tente de novo em "
            "instantes — o detalhe foi registrado no log do servidor.",
            "cloud_off",
        )
    except Exception:  # noqa: BLE001 — nunca mostrar traceback na tela
        logger.error("Erro em %s:\n%s", onde, traceback.format_exc())
        p.error("Erro inesperado ao montar esta tela. O detalhe foi registrado no log do servidor.")


def _pagina_cofre_bloqueado(ctx: Ctx) -> Pagina:
    p = Pagina(ctx)
    p.title("Cofre de credenciais bloqueado", "lock")
    if ctx.usuario["role"] != "admin":
        p.info(
            "O app foi reiniciado e as credenciais do banco estão trancadas. Peça a um "
            "administrador para desbloquear o cofre.",
            "lock",
        )
        return p
    p.caption("O app foi reiniciado. Digite a senha mestra para liberar as consultas ao DW.")
    if ctx.acao == "desbloquear_cofre":
        try:
            CredentialVault.desbloquear(ctx.form_get("senha_mestra"))
        except CofreError as exc:
            app_db.audit(ctx.usuario["username"], "cofre_desbloqueio_falhou")
            p.error(str(exc))
        else:
            app_db.audit(ctx.usuario["username"], "cofre_desbloqueado")
            p.toast("Cofre desbloqueado.")
            return p
    col, _ = p.columns([1, 1])
    with col.acao("desbloquear_cofre") as f:
        f.text_input("Senha mestra", "senha_mestra", tipo="password")
        f.submit("Desbloquear cofre", icone_nome="lock_open")
    p.caption("Perdeu a senha mestra? Admin → Cofre → Apagar cofre, e recadastre as credenciais.")
    return p


# ── páginas ──────────────────────────────────────────────────────────────────


@app.get("/")
def raiz() -> Response:
    return RedirectResponse("/p/home", status_code=303)


def _pagina(request: Request, slug: str, form: Optional[dict[str, list[str]]]) -> Response:
    info = POR_SLUG.get(slug)
    sessao, usuario, desvio = _exigir_login(request)
    if desvio:
        return desvio
    assert sessao and usuario
    if not info:
        return HTMLResponse("Página não encontrada.", status_code=404)
    if info.admin and usuario["role"] != "admin":
        ctx = Ctx(caminho=info.url, params={}, usuario=usuario, estado={}, slug=slug)
        p = Pagina(ctx)
        p.error("Acesso restrito a administradores.", "block")
        return _responder_pagina(request, usuario, sessao, info, p, status=403)
    estado = _estado_cofre()
    if not info.sem_cofre and estado["configurado"] and not estado["desbloqueado"]:
        ctx = Ctx(caminho=info.url, params=_params(request), form=form, usuario=usuario, estado={}, slug=slug)
        p = _pagina_cofre_bloqueado(ctx)
        if CredentialVault.desbloqueado():
            return _redirecionar(request, info.url)
        return _responder_pagina(request, usuario, sessao, info, p)
    return _responder_pagina(request, usuario, sessao, info, _executar_view(request, info, usuario, sessao, form))


@app.get("/p/{slug}")
def pagina_get(request: Request, slug: str) -> Response:
    return _pagina(request, slug, None)


@app.post("/p/{slug}")
async def pagina_post(request: Request, slug: str) -> Response:
    form = await _form(request)
    sessao, _ = _sessao(request)
    if not sessao or not _csrf_ok(request, sessao, form):
        return HTMLResponse("Requisição inválida (CSRF).", status_code=403)
    # A view roda no threadpool (consulta bloqueante), não no event loop.
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(_pagina, request, slug, form)


@app.get("/p/{slug}/_bloco/{nome}")
def bloco(request: Request, slug: str, nome: str) -> Response:
    info = POR_SLUG.get(slug)
    sessao, usuario, desvio = _exigir_login(request)
    if desvio:
        return desvio
    assert sessao and usuario
    if not info or (info.admin and usuario["role"] != "admin"):
        return HTMLResponse("", status_code=404)
    blocos = getattr(info.carregar(), "BLOCOS", {})
    if nome not in blocos:
        return HTMLResponse("", status_code=404)
    ctx = Ctx(
        caminho=info.url, params=_params(request), usuario=usuario,
        estado=_ESTADOS.setdefault(sessao["token_hash"], {}), slug=slug, csrf=sessao["csrf"],
    )
    p = Pagina(ctx)
    _proteger(p, f"{slug}/_bloco/{nome}", lambda: blocos[nome](p, ctx))
    return HTMLResponse(Markup('<div class="bloco">') + p.render() + Markup("</div>"))


@app.get("/p/{slug}/_download/{nome}")
def download(request: Request, slug: str, nome: str) -> Response:
    info = POR_SLUG.get(slug)
    sessao, usuario, desvio = _exigir_login(request)
    if desvio:
        return desvio
    assert sessao and usuario
    if not info or (info.admin and usuario["role"] != "admin"):
        return HTMLResponse("", status_code=404)
    downloads = getattr(info.carregar(), "DOWNLOADS", {})
    if nome not in downloads:
        return HTMLResponse("", status_code=404)
    ctx = Ctx(caminho=info.url, params=_params(request), usuario=usuario, estado={}, slug=slug)
    conteudo, mime = downloads[nome](ctx)
    return Response(conteudo, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.get("/api/dim/{nome}")
def api_dim(request: Request, nome: str) -> Response:
    sessao, usuario = _sessao(request)
    if not sessao or not usuario:
        return JSONResponse({"erro": "não autenticado"}, status_code=401)
    try:
        versao, itens = dims.carregar(nome)
    except KeyError:
        return JSONResponse({"erro": "lista desconhecida"}, status_code=404)
    except DatabaseConnectionError as exc:
        return JSONResponse({"erro": str(exc)}, status_code=503)
    return JSONResponse({"versao": versao, "itens": itens}, headers={"Cache-Control": "private, no-store"})


# ── login / sessão / senha ───────────────────────────────────────────────────


def _portal(request: Request, nome: str, contexto: dict[str, Any], status: int = 200) -> Response:
    contexto = {"request": request, "tema": _tema(request), **contexto}
    return templates.TemplateResponse(request, nome, contexto, status_code=status)


def _proximo_seguro(proximo: str) -> str:
    """Só redireciona para caminho interno (evita open redirect)."""
    return proximo if proximo.startswith("/") and not proximo.startswith("//") and "\\" not in proximo else "/p/home"


_AVISOS_LOGIN = {"saiu": "Você saiu.", "inatividade": "Sessão bloqueada por inatividade. Entre novamente."}


@app.get("/login")
def login_form(request: Request, next: str = "/p/home", motivo: str = "") -> Response:
    sessao, usuario = _sessao(request)
    if sessao and usuario:
        return RedirectResponse(_proximo_seguro(next), status_code=303)
    token = secrets.token_urlsafe(24)
    resposta = _portal(
        request, "login.html",
        {"csrf_login": token, "proximo": _proximo_seguro(next), "erro": None, "aviso": _AVISOS_LOGIN.get(motivo)},
    )
    _cookie(resposta, COOKIE_CSRF_LOGIN, token, max_age=3600)
    return resposta


@app.post("/login")
async def login_post(request: Request) -> Response:
    form = await _form(request)
    enviado = (form.get("_csrf") or [""])[-1]
    esperado = request.cookies.get(COOKIE_CSRF_LOGIN, "")
    proximo = _proximo_seguro((form.get("next") or ["/p/home"])[-1])
    if not enviado or not esperado or not secrets.compare_digest(enviado, esperado):
        return RedirectResponse("/login", status_code=303)
    from starlette.concurrency import run_in_threadpool

    token, erro = await run_in_threadpool(
        auth.login, (form.get("usuario") or [""])[-1], (form.get("senha") or [""])[-1]
    )
    if erro:
        return _portal(
            request, "login.html",
            {"csrf_login": enviado, "proximo": proximo, "erro": erro, "usuario": (form.get("usuario") or [""])[-1][:64]},
            status=401,
        )
    resposta = RedirectResponse(proximo, status_code=303)
    _cookie(resposta, auth.COOKIE, token or "")
    resposta.delete_cookie(COOKIE_CSRF_LOGIN, path="/")
    return resposta


@app.post("/logout")
async def logout(request: Request) -> Response:
    form = await _form(request)
    sessao, usuario = _sessao(request)
    if sessao and _csrf_ok(request, sessao, form):
        auth.logout(sessao, usuario)
        _ESTADOS.pop(sessao["token_hash"], None)
    # Botão Sair ou bloqueio por inatividade (app.js): a tela de login mostra o motivo.
    motivo = "inatividade" if (form.get("motivo") or [""])[-1] == "inatividade" else "saiu"
    resposta = _redirecionar(request, f"/login?motivo={motivo}")
    resposta.delete_cookie(auth.COOKIE, path="/")
    # Apaga IndexedDB/localStorage (listas de filtro) do navegador.
    resposta.headers["Clear-Site-Data"] = '"storage"'
    return resposta


@app.get("/senha/nova")
def senha_nova_form(request: Request) -> Response:
    sessao, usuario, desvio = _exigir_login(request)
    if desvio:
        return desvio
    assert sessao and usuario
    if not usuario["must_change_password"]:
        return RedirectResponse("/p/home", status_code=303)
    return _portal(request, "senha_nova.html", {"usuario": usuario, "csrf": sessao["csrf"], "erro": None, "min": PasswordService.MIN_TAMANHO})


@app.post("/senha/nova")
async def senha_nova_post(request: Request) -> Response:
    form = await _form(request)
    sessao, usuario = _sessao(request)
    if not sessao or not usuario or not _csrf_ok(request, sessao, form):
        return RedirectResponse("/login", status_code=303)
    erro = auth.trocar_senha_obrigatoria(usuario, (form.get("nova") or [""])[-1], (form.get("conf") or [""])[-1])
    if erro:
        return _portal(
            request, "senha_nova.html",
            {"usuario": usuario, "csrf": sessao["csrf"], "erro": erro, "min": PasswordService.MIN_TAMANHO}, status=400,
        )
    app_db.apagar_sessoes_usuario(usuario["id"], exceto=sessao["token_hash"])
    return RedirectResponse("/p/home", status_code=303)


@app.post("/senha/forca")
async def senha_forca(request: Request) -> Response:
    """Medidor de força ao vivo (parcial HTMX)."""
    form = await _form(request)
    sessao, _ = _sessao(request)
    if not sessao or not _csrf_ok(request, sessao, form):
        return HTMLResponse("", status_code=403)
    senha = (form.get("nova") or [""])[-1]
    if not senha:
        return HTMLResponse("")
    forca = PasswordService.evaluate(senha)
    return templates.TemplateResponse(request, "_forca.html", {"forca": forca})


@app.get("/senha/sugerir")
def senha_sugerir(request: Request) -> Response:
    sessao, _ = _sessao(request)
    if not sessao:
        return HTMLResponse("", status_code=401)
    return templates.TemplateResponse(request, "_sugestao.html", {"senha": PasswordService.generate(16)})


@app.get("/conta/senha")
def minha_senha_form(request: Request) -> Response:
    sessao, usuario, desvio = _exigir_login(request)
    if desvio:
        return desvio
    assert sessao and usuario
    ctx = Ctx(caminho="/conta/senha", params={}, usuario=usuario, estado={}, slug="conta")
    return _responder_pagina(request, usuario, sessao, None, _pagina_minha_senha(ctx))


@app.post("/conta/senha")
async def minha_senha_post(request: Request) -> Response:
    form = await _form(request)
    sessao, usuario, desvio = _exigir_login(request)
    if desvio:
        return desvio
    assert sessao and usuario
    if not _csrf_ok(request, sessao, form):
        return HTMLResponse("Requisição inválida (CSRF).", status_code=403)
    ctx = Ctx(caminho="/conta/senha", params={}, form=form, usuario=usuario, estado={}, slug="conta", csrf=sessao["csrf"])
    return _responder_pagina(request, usuario, sessao, None, _pagina_minha_senha(ctx, sessao))


def _pagina_minha_senha(ctx: Ctx, sessao: Optional[dict] = None) -> Pagina:
    p = Pagina(ctx)
    p.title("Trocar minha senha", "key")
    if ctx.acao == "minha_senha" and sessao:
        erro = auth.trocar_minha_senha(ctx.usuario, ctx.form_get("atual"), ctx.form_get("nova"), ctx.form_get("conf"))
        if erro:
            p.error(erro)
        else:
            app_db.apagar_sessoes_usuario(ctx.usuario["id"], exceto=sessao["token_hash"])
            p.success("Senha trocada. As outras sessões abertas com este usuário foram encerradas.")
    col, _ = p.columns([1, 1])
    with col.card() as card, card.acao("minha_senha") as f:
        f.text_input("Senha atual", "atual", tipo="password", autocomplete="current-password")
        f.text_input("Nova senha", "nova", tipo="password", autocomplete="new-password")
        f.html(Markup('<div class="forca-alvo" data-forca-de="nova"></div>'))
        f.text_input("Confirmar nova senha", "conf", tipo="password", autocomplete="new-password")
        f.html(
            Markup(
                '<button type="button" class="botao" hx-get="/senha/sugerir" hx-target="next .sugestao" '
                'hx-swap="innerHTML">' + str(icone("casino")) + "Gerar senha forte</button><div class=\"sugestao\"></div>"
            )
        )
        f.caption(f"Mínimo {PasswordService.MIN_TAMANHO} caracteres e força Média.")
        f.submit("Trocar senha", icone_nome="lock_reset")
    return p


@app.get("/saude")
def saude() -> dict[str, str]:
    """Health check do proxy/monitoramento (sem dado nenhum)."""
    return {"status": "ok"}
