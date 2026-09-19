import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_streamlit_escuta_so_em_loopback():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["server"]["address"] == "127.0.0.1"
    assert cfg["server"]["enableXsrfProtection"] is True


def test_env_example_sem_valores_reais():
    for linha in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.startswith("#"):
            chave, valor = linha.split("=", 1)
            if any(t in chave for t in ("PASSWORD", "USER", "HOST", "ADDRESS")):
                assert valor == "", chave
