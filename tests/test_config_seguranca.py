from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_app_web_escuta_so_em_loopback_por_padrao():
    src = (ROOT / "web" / "__main__.py").read_text(encoding="utf-8")
    assert 'os.environ.get("APP_HOST", "127.0.0.1")' in src
    assert "workers=1" in src


def test_env_example_sem_valores_reais():
    for linha in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.startswith("#"):
            chave, valor = linha.split("=", 1)
            if any(t in chave for t in ("PASSWORD", "USER", "HOST", "ADDRESS")):
                assert valor == "", chave
