"""Testes do app web (FastAPI + HTMX) — sem DW: autenticação, CSRF, sessão, headers,
páginas de Admin, kit de UI, formatação e cache."""

from __future__ import annotations

import re
import threading
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from scripts import app_db
from web import auth as web_auth
from web import fmt
from web.cache import Aquecedor, Cache, cached
from web.ui import Ctx, Pagina, md

SENHA_ADMIN = "Teste#Forte-2026!x"


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", SENHA_ADMIN)
    monkeypatch.setattr(web_auth, "KEY_FILE", web_auth.KEY_FILE.parent / "data" / ".access_key_teste")
    monkeypatch.setattr(web_auth, "_HASH_FALSO", app_db.hash_password("x"))
    monkeypatch.setattr(Aquecedor, "iniciar", classmethod(lambda cls, pode: None))  # nada de DW nos testes
    web_auth._lockouts.clear()
    Cache.limpar()
    from web import main

    monkeypatch.setattr(main, "COOKIE_SEGURO", False)  # TestClient fala http://testserver
    with TestClient(main.app) as c:
        yield c
    web_auth._lockouts.clear()


def _login(c: TestClient, usuario: str = "admin", senha: str = SENHA_ADMIN, proximo: str = "/p/admin-usuarios"):
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login").text).group(1)
    return c.post("/login", data={"_csrf": tok, "usuario": usuario, "senha": senha, "next": proximo}, follow_redirects=False)


def _csrf(c: TestClient, url: str = "/p/admin-usuarios") -> str:
    return re.search(r'X-CSRF-Token": "([^"]+)"', c.get(url).text).group(1)


# ── autenticação e sessão ────────────────────────────────────────────────────


def test_pagina_exige_login(cliente):
    r = cliente.get("/p/home", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")


def test_htmx_sem_login_recebe_hx_redirect(cliente):
    r = cliente.get("/p/home", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.headers.get("HX-Redirect", "").startswith("/login")


def test_login_ok_cria_cookie_httponly_samesite(cliente):
    r = _login(cliente)
    assert r.status_code == 303 and r.headers["location"] == "/p/admin-usuarios"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_login_sem_csrf_de_login_e_recusado(cliente):
    r = cliente.post("/login", data={"usuario": "admin", "senha": SENHA_ADMIN}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "invest_sid" not in r.headers.get("set-cookie", "")


def test_senha_errada_mesma_mensagem_e_lockout(cliente):
    for _ in range(web_auth.MAX_FALHAS_USUARIO):
        r = _login(cliente, senha="errada-errada")
        assert r.status_code == 401 and "Usuário ou senha inválidos." in r.text
    r = _login(cliente)  # senha certa, mas bloqueado
    assert r.status_code == 401 and "Muitas tentativas" in r.text


def test_open_redirect_bloqueado(cliente):
    r = _login(cliente, proximo="//evil.example/x")
    assert r.headers["location"] == "/p/home"


def test_sessao_expira_por_inatividade(cliente, monkeypatch):
    _login(cliente)
    monkeypatch.setattr(web_auth, "idle_segundos", lambda: -1)
    assert cliente.get("/p/admin-usuarios", follow_redirects=False).status_code == 303


def test_usuario_desativado_perde_sessao_na_hora(cliente):
    app_db.criar_usuario("leitor1", "Outra#Senha-99z", role="leitor", must_change_password=False)
    _login(cliente, "leitor1", "Outra#Senha-99z", "/p/home")
    user = app_db.get_user("leitor1")
    app_db.alterar_usuario(user["id"], role="leitor", active=False)
    assert cliente.get("/p/conta", follow_redirects=False).status_code in (303, 404)
    assert cliente.get("/conta/senha", follow_redirects=False).status_code == 303


def test_leitor_nao_acessa_admin(cliente):
    app_db.criar_usuario("leitor1", "Outra#Senha-99z", role="leitor", must_change_password=False)
    _login(cliente, "leitor1", "Outra#Senha-99z", "/conta/senha")
    assert cliente.get("/p/admin-usuarios").status_code == 403
    assert cliente.get("/p/admin-cofre/_bloco/x").status_code == 404


def test_troca_obrigatoria_de_senha(cliente):
    app_db.criar_usuario("novo1", "Temporaria#123x", role="leitor", must_change_password=True)
    _login(cliente, "novo1", "Temporaria#123x", "/p/home")
    r = cliente.get("/p/home", follow_redirects=False)
    assert r.headers["location"] == "/senha/nova"
    csrf = re.search(r'name="_csrf" value="([^"]+)"', cliente.get("/senha/nova").text).group(1)
    r = cliente.post("/senha/nova", data={"_csrf": csrf, "nova": "Nova#Senha-2026z", "conf": "Nova#Senha-2026z"}, follow_redirects=False)
    assert r.status_code == 303 and not app_db.get_user("novo1")["must_change_password"]


def test_logout_apaga_sessao_e_storage(cliente):
    _login(cliente)
    csrf = _csrf(cliente)
    r = cliente.post("/logout", data={"_csrf": csrf}, follow_redirects=False)
    assert r.headers.get("Clear-Site-Data") == '"storage"'
    assert cliente.get("/p/admin-usuarios", follow_redirects=False).status_code == 303


# ── CSRF, headers, ações ─────────────────────────────────────────────────────


def test_post_sem_csrf_recusado(cliente):
    _login(cliente)
    r = cliente.post("/p/admin-usuarios", data={"_action": "criar_usuario", "novo_nome": "x1234"})
    assert r.status_code == 403
    assert app_db.get_user("x1234") is None


def test_criar_usuario_via_acao(cliente):
    _login(cliente)
    csrf = _csrf(cliente)
    r = cliente.post(
        "/p/admin-usuarios",
        data={"_action": "criar_usuario", "novo_nome": "leitor2", "novo_role": "leitor", "novo_senha": "Outra#Senha-99z"},
        headers={"X-CSRF-Token": csrf, "HX-Request": "true"},
    )
    assert r.status_code == 200 and r.text.lstrip().startswith('<main id="main"')
    assert app_db.get_user("leitor2")["must_change_password"]


def test_redefinir_senha_derruba_sessoes_do_alvo(cliente):
    app_db.criar_usuario("leitor3", "Outra#Senha-99z", role="leitor", must_change_password=False)
    alvo = app_db.get_user("leitor3")
    app_db.criar_sessao(alvo["id"])
    _login(cliente)
    csrf = _csrf(cliente)
    cliente.post("/p/admin-usuarios", data={"_action": "redefinir_senha", "alvo": str(alvo["id"])}, headers={"X-CSRF-Token": csrf})
    with app_db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (alvo["id"],)).fetchone()[0] == 0


def test_headers_de_seguranca(cliente):
    _login(cliente)
    r = cliente.get("/p/admin-usuarios")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    assert "server" not in {k.lower() for k in r.headers if k.lower() == "server" and "uvicorn" in r.headers[k]}


def test_paginas_admin_renderizam_sem_erro(cliente):
    _login(cliente)
    for slug in ("admin", "admin-usuarios", "admin-configuracoes", "admin-cofre", "admin-auditoria"):
        r = cliente.get(f"/p/{slug}")
        assert r.status_code == 200 and "Erro inesperado" not in r.text, slug


def test_configuracoes_salvam(cliente):
    _login(cliente)
    csrf = _csrf(cliente)
    cliente.post(
        "/p/admin-configuracoes",
        data={"_action": "salvar_config", "tema": "blau", "idle": "45", "zumbi": "400", "excluir_intercompany": "0"},
        headers={"X-CSRF-Token": csrf},
    )
    assert app_db.get_setting("default_theme") == "blau"
    assert app_db.get_setting("idle_minutes") == 45
    assert app_db.get_setting("excluir_intercompany") is False


def test_parcial_htmx_devolve_so_main(cliente):
    _login(cliente)
    r = cliente.get("/p/admin-auditoria?aba=1", headers={"HX-Request": "true"})
    assert r.text.lstrip().startswith('<main id="main"') and "<html" not in r.text


def test_todas_as_views_importam():
    from web.views import PAGINAS

    for pagina in PAGINAS:
        assert callable(pagina.carregar().render), pagina.slug


# ── kit de UI ────────────────────────────────────────────────────────────────


def _pagina(**params) -> Pagina:
    ctx = Ctx(caminho="/p/x", params={k: v if isinstance(v, list) else [v] for k, v in params.items()}, usuario={"role": "admin"}, estado={}, slug="x")
    return Pagina(ctx)


def test_md_escapa_html_antes_de_formatar():
    html = str(md("**<script>alert(1)</script>** `<b>` :material/info:"))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<strong>" in html and '<span class="ms' in html


def test_tabela_nao_quebra_script_json():
    p = _pagina()
    p.table(pd.DataFrame({"a": ["</script><script>alert(1)</script>"]}))
    html = str(p.render())
    assert html.count("</script>") == 1  # só o fechamento do próprio bloco JSON


def test_widgets_leem_query_string_e_defaults():
    p = _pagina(sel="B", chk=["0", "1"], txt="abc", rng="999", multi=["", "y"])
    assert p.selectbox("S", ["A", "B"], "sel") == "B"
    assert p.checkbox("C", "chk") is True
    assert p.text_input("T", "txt") == "abc"
    assert p.slider("R", "rng", 1, 50, 10) == 50  # limitado ao máximo
    assert p.multiselect("M", ["x", "y"], "multi") == ["y"]
    vazio = _pagina()
    assert vazio.checkbox("C", "chk", default=True) is True
    assert vazio.selectbox("S", ["A", "B"], "sel", default="B") == "B"


def test_selectbox_rejeita_valor_fora_das_opcoes():
    assert _pagina(sel="<x>").selectbox("S", ["A", "B"], "sel") == "A"


def test_filtro_compartilhado_entre_paginas():
    estado: dict = {}
    ctx1 = Ctx(caminho="/p/a", params={"tipo": ["Governo"]}, usuario={}, estado=estado, slug="a")
    assert Pagina(ctx1).selectbox("T", ["Todos", "Governo"], "tipo", compartilhado=True) == "Governo"
    ctx2 = Ctx(caminho="/p/b", params={}, usuario={}, estado=estado, slug="b")
    assert Pagina(ctx2).selectbox("T", ["Todos", "Governo"], "tipo", compartilhado=True) == "Governo"


def test_abas_so_calculam_a_ativa():
    abas = _pagina(aba="1").tabs(["A", "B"], "aba")
    assert abas.ativa == 1
    assert _pagina(aba="9").tabs(["A", "B"], "aba").ativa == 0


# ── formatação e cache ───────────────────────────────────────────────────────


def test_formatacao_pt_br():
    assert fmt.brl(1234567.891) == "R$ 1.234.568"
    assert fmt.brl(1234.5, 2) == "R$ 1.234,50"
    assert fmt.pct(0.1234, 1) == "12,3%"
    assert fmt.pct(-0.05, 1, sinal=True) == "−5,0%"
    assert fmt.moeda(10, "UYU") == "UYU 10,00"
    assert fmt.num(float("nan")) == "—"
    assert fmt.data("2026-09-25") == "25/09/2026"


def test_cache_single_flight():
    chamadas = []

    @cached(ttl=60, nome="teste: single flight")
    def lento(x):
        chamadas.append(x)
        time.sleep(0.2)
        return x * 2

    resultados = []
    threads = [threading.Thread(target=lambda: resultados.append(lento(3))) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert resultados == [6] * 5 and chamadas == [3]


def test_cache_expira():
    chamadas = []

    @cached(ttl=0.05, nome="teste: expira")
    def f():
        chamadas.append(1)
        return len(chamadas)

    assert f() == 1 and f() == 1
    time.sleep(0.06)
    assert f() == 2


# ── regras portadas dos testes do Streamlit ──────────────────────────────────


def test_bootstrap_com_access_key_fraca_gera_senha_aleatoria(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ACCESS_KEY", "abcdefghijkl")
    monkeypatch.setattr(web_auth, "KEY_FILE", tmp_path / ".access_key")
    web_auth.garantir_admin_inicial()
    admin = app_db.get_user("admin")
    assert admin["must_change_password"] == 1
    assert not app_db.verify_password("abcdefghijkl", admin["password_hash"])
    assert (tmp_path / ".access_key").stat().st_mode & 0o077 == 0


def test_troca_obrigatoria_do_admin_inicial_apaga_arquivo(monkeypatch, tmp_path):
    arquivo = tmp_path / ".access_key"
    monkeypatch.delenv("APP_ACCESS_KEY", raising=False)
    monkeypatch.setattr(web_auth, "KEY_FILE", arquivo)
    web_auth.garantir_admin_inicial()
    senha = arquivo.read_text().split("senha: ")[1].strip()
    admin = web_auth.autenticar("admin", senha)
    assert web_auth.trocar_senha_obrigatoria(admin, "abcdefghij", "abcdefghij")  # fraca: recusa
    assert web_auth.trocar_senha_obrigatoria(admin, "Nova#Senha-2026z", "Nova#Senha-2026z") is None
    assert not arquivo.exists()


def test_mensagem_igual_para_usuario_inexistente(cliente):
    r1 = _login(cliente, "naoexiste", "qualquer-coisa")
    r2 = _login(cliente, "admin", "senha-errada-x")
    assert r1.status_code == r2.status_code == 401
    assert "Usuário ou senha inválidos." in r1.text and "Usuário ou senha inválidos." in r2.text


def _cofre_bloqueado():
    from scripts.credential_vault import CredentialVault

    creds = {
        "HANA_ADDRESS": "h", "HANA_PORT": "443", "HANA_USER": "u", "HANA_PASSWORD": "p", "DDIC_SCHEMA": "IB_SAPECC",
        "SQLSERVER_HOST": "s", "SQLSERVER_PORT": "1433", "SQLSERVER_USER": "u", "SQLSERVER_PASSWORD": "p",
    }
    CredentialVault.criar(creds, "Mestra-Forte-2026!")
    CredentialVault.bloquear()
    return CredentialVault


def test_cofre_bloqueado_trava_paginas_e_admin_desbloqueia(cliente):
    cofre = _cofre_bloqueado()
    _login(cliente)
    r = cliente.get("/p/home")
    assert "Cofre de credenciais bloqueado" in r.text and "senha_mestra" in r.text
    csrf = re.search(r'X-CSRF-Token": "([^"]+)"', r.text).group(1)
    r = cliente.post("/p/home", data={"_action": "desbloquear_cofre", "senha_mestra": "Mestra-Forte-2026!"},
                     headers={"X-CSRF-Token": csrf, "HX-Request": "true"})
    assert cofre.desbloqueado() and r.headers.get("HX-Redirect") == "/p/home"


def test_leitor_ve_aviso_com_cofre_bloqueado(cliente):
    _cofre_bloqueado()
    app_db.criar_usuario("leitor1", "Outra#Senha-99z", role="leitor", must_change_password=False)
    _login(cliente, "leitor1", "Outra#Senha-99z", "/p/home")
    r = cliente.get("/p/home")
    assert "Peça a um administrador" in r.text and "senha_mestra" not in r.text


def test_csv_de_ajustes_neutraliza_formula():
    from web.views.admin_dados import _csv_seguro

    assert "'=HYPERLINK" in _csv_seguro([{"a": "=HYPERLINK(1)"}])


def test_sair_mostra_o_motivo_no_login(cliente):
    _login(cliente)
    tok = _csrf(cliente)
    r = cliente.post("/logout", data={"_csrf": tok, "motivo": "inatividade"}, follow_redirects=False)
    assert r.headers["location"] == "/login?motivo=inatividade"
    assert "Sessão bloqueada por inatividade" in cliente.get(r.headers["location"]).text
    _login(cliente)
    r = cliente.post("/logout", data={"_csrf": _csrf(cliente)}, follow_redirects=False)
    assert "Você saiu." in cliente.get(r.headers["location"]).text


def test_post_de_outra_origem_recusado_mesmo_com_token(cliente):
    # Outra porta/subdomínio do mesmo domínio é o mesmo "site": o SameSite não barra.
    _login(cliente)
    tok = _csrf(cliente)
    r = cliente.post("/conta/senha", data={"_csrf": tok, "_acao": "minha_senha"}, headers={"Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403


def test_casca_do_kit_com_ajustes_e_sem_seletor_de_tema_no_topo(cliente):
    _login(cliente)
    html = cliente.get("/p/admin-usuarios").text
    assert 'id="ajustes"' in html and "data-abre-ajustes" in html and "data-filtra-lateral" in html
    assert 'class="tema-sel"' not in html and "data-sair" in html


def test_painel_admin_saiu_e_link_antigo_vai_para_usuarios(cliente):
    _login(cliente)
    r = cliente.get("/p/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/admin-usuarios"
    assert 'href="/p/admin"' not in cliente.get("/p/admin-usuarios").text


def test_rajada_de_logins_confere_no_maximo_o_limite(monkeypatch):
    """Antes, o bloqueio era checado antes do hash e a falha contada depois: a rajada passava toda."""
    from concurrent.futures import ThreadPoolExecutor

    app_db.criar_usuario("alvo", "Senha-Certa-2026!", role="leitor", must_change_password=False)
    conferidas = []
    original = app_db.verify_password

    def espiao(senha, hash_):
        conferidas.append(senha)
        return original(senha, hash_)

    monkeypatch.setattr(app_db, "verify_password", espiao)
    with ThreadPoolExecutor(20) as pool:
        list(pool.map(lambda i: web_auth.login("alvo", f"errada-{i}"), range(40)))
    assert len(conferidas) <= web_auth.MAX_FALHAS_USUARIO


def test_login_certo_nao_gasta_o_limite_global():
    """A reserva conta no global, mas o login certo devolve: usuários legítimos não bloqueiam todos."""
    app_db.criar_usuario("alvo", "Senha-Certa-2026!", role="leitor", must_change_password=False)
    for _ in range(web_auth.MAX_FALHAS_GLOBAL + 5):
        token, erro = web_auth.login("alvo", "Senha-Certa-2026!")
        assert token and not erro
