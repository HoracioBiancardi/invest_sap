"""Conexões reutilizáveis para o DW da Blau (SQL Server BRONZE/SILVER/GOLD e SAP HANA/Datasphere).

Lê credenciais do .env na raiz do projeto (mesmas variáveis usadas em data-platform).
Nunca commitar valores de .env; nunca logar senha.

Uso típico:
    from scripts.db import get_sqlserver_engine, get_hana_connection, read_sql

    df = read_sql("SELECT TOP 10 * FROM vendas_sap.fct_pendencia_sap", database="GOLD")

    with get_hana_connection(schema="IB_SAPECC") as conn:
        df2 = pd.read_sql("SELECT * FROM VBAK LIMIT 10", conn)
"""

from __future__ import annotations

import os
import urllib.parse
from functools import lru_cache
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()

# Databases disponíveis na mesma instância SQL Server (ver dbt_project.yml / profiles.yml
# do data-platform): BRONZE (dados brutos), SILVER (limpos/tipados), GOLD (modelos analíticos).
VALID_DATABASES = {"BRONZE", "SILVER", "GOLD"}

# Schema GOLD onde vivem os modelos de vendas via SAP (ver CONTEXTO_VENDAS_SAP.md).
GOLD_VENDAS_SAP_SCHEMA = "vendas_sap"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Variável de ambiente {name} não definida. Verifique o arquivo .env na raiz do projeto."
        )
    return value


@lru_cache(maxsize=None)
def get_sqlserver_engine(database: str = "GOLD") -> Engine:
    """Cria (e cacheia) uma engine SQLAlchemy para o SQL Server de produção.

    Args:
        database: "BRONZE", "SILVER" ou "GOLD" (mesma instância, bancos separados).

    Returns:
        Engine SQLAlchemy conectada via pyodbc (ODBC Driver 18 for SQL Server).
    """
    database = database.upper()
    if database not in VALID_DATABASES:
        raise ValueError(f"database deve ser um de {VALID_DATABASES}, recebido: {database!r}")

    host = _require_env("SQLSERVER_HOST")
    port = os.environ.get("SQLSERVER_PORT", "1433")
    user = _require_env("SQLSERVER_USER")
    password = _require_env("SQLSERVER_PASSWORD")

    params = urllib.parse.quote_plus(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={host},{port};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        "TrustServerCertificate=yes;"
        "Encrypt=yes;"
    )
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}", pool_pre_ping=True)


def read_sql(
    query: str, database: str = "GOLD", params: Optional[dict[str, Any]] = None
) -> pd.DataFrame:
    """Executa uma query no SQL Server (BRONZE/SILVER/GOLD) e retorna um DataFrame.

    Args:
        query: SQL a executar (T-SQL). Use parâmetros nomeados (:nome) em vez de f-string
            sempre que o valor vier de fora do código, para evitar SQL injection.
        database: "BRONZE", "SILVER" ou "GOLD".
        params: dict de parâmetros nomeados para bind (opcional).
    """
    engine = get_sqlserver_engine(database)
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def get_hana_config(schema: Optional[str] = None) -> dict[str, Any]:
    """Monta os parâmetros de conexão do SAP HANA/Datasphere a partir do .env."""
    return {
        "address": _require_env("HANA_ADDRESS"),
        "port": int(os.environ.get("HANA_PORT", 443)),
        "user": _require_env("HANA_USER"),
        "password": _require_env("HANA_PASSWORD"),
        "encrypt": True,
        "sslValidateCertificate": True,
        "currentSchema": schema or os.environ.get("DDIC_SCHEMA", "IB_SAPECC"),
    }


def get_hana_connection(schema: Optional[str] = None):
    """Abre uma conexão direta com o SAP HANA/Datasphere via hdbcli.

    Args:
        schema: Schema padrão da conexão. Default: DDIC_SCHEMA do .env (IB_SAPECC),
            que é o mesmo schema onde ficam as tabelas replicadas do SAP ECC (VBAK, VBAP, ...)
            e as tabelas de dicionário de dados (DD02T, DD03L, ...).

    Returns:
        Conexão hdbcli.dbapi. Não suporta `with` (hdbcli não implementa context manager) —
        feche explicitamente com `.close()`.

    Note:
        Esta é uma conexão *direta* ao HANA/Datasphere, útil para investigação pontual
        (DDIC, contagens, amostras). A ingestão oficial para o DW (Bronze) é feita pelo
        pipeline dataspherev3 do projeto data-platform, não por este script.
    """
    from hdbcli import dbapi

    config = get_hana_config(schema)
    return dbapi.connect(**config)


def read_hana_sql(
    query: str, schema: Optional[str] = None, params: Optional[tuple[Any, ...]] = None
) -> pd.DataFrame:
    """Executa uma query no SAP HANA/Datasphere e retorna um DataFrame.

    Args:
        query: SQL a executar. Use marcadores posicionais `?` (paramstyle "qmark" do
            hdbcli) em vez de f-string sempre que o valor vier de fora do código, para
            evitar SQL injection.
        schema: Schema HANA a usar (ver `get_hana_connection`).
        params: Sequência de valores para bind posicional dos `?` da query (opcional).

    hdbcli não é uma conexão SQLAlchemy/DBAPI2 totalmente padrão, então pandas emite um
    UserWarning inofensivo ao usá-la em pd.read_sql; suprimimos apenas esse aviso pontual.
    """
    import warnings

    conn = get_hana_connection(schema)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module="pandas")
            return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()
