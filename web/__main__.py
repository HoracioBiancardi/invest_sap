"""`uv run python -m web` — sobe o app web num processo só (ver docstring de `web/main.py`).

Variáveis: `APP_HOST` (default 127.0.0.1), `APP_PORT` (default 8000). Atrás de proxy HTTPS,
`APP_FORWARDED_IPS` = IP do proxy (para o log ver o IP real do cliente).
"""

from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        "web.main:app",
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8000")),
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("APP_FORWARDED_IPS", "127.0.0.1"),
        server_header=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
