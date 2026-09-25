# App web do Invest SAP (FastAPI + HTMX) — mesmo padrão do input_arquivos na VM.
# bookworm fixo: é a versão de Debian que o repositório da Microsoft (ODBC 18) atende.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ODBC Driver 18 (pyodbc → SQL Server) + tzdata (as páginas usam "hoje" — MTD, faturamento do dia).
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates unixodbc tzdata \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y curl gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" TZ=America/Sao_Paulo

# Dependências primeiro (melhor cache de camadas)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY scripts ./scripts
COPY web ./web
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 app && mkdir -p /app/data && chown -R app /app/data
USER app

# Um processo só (cofre, lockout e cache em memória) — ver web/__main__.py.
ENV APP_HOST=0.0.0.0 APP_PORT=8005 APP_DB_PATH=/app/data/app.db APP_ACCESS_KEY_FILE=/app/data/.access_key
EXPOSE 8005
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8005/saude', timeout=4)"
CMD ["python", "-m", "web"]
