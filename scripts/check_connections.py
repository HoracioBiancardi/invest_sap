"""Verifica conectividade com o SQL Server (BRONZE/SILVER/GOLD) e o SAP HANA/Datasphere.

Uso:
    uv run python scripts/check_connections.py
"""

from __future__ import annotations

import sys

try:
    from scripts.db import get_hana_connection, get_sqlserver_engine
except ImportError:
    from db import get_hana_connection, get_sqlserver_engine


def check_sqlserver(database: str) -> bool:
    try:
        engine = get_sqlserver_engine(database)
        with engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT @@VERSION AS version, DB_NAME() AS db").fetchone()
        print(f"[OK] SQL Server {database}: conectado. DB atual = {row.db}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[FALHA] SQL Server {database}: {exc}")
        return False


def check_hana() -> bool:
    conn = None
    try:
        conn = get_hana_connection()
        cur = conn.cursor()
        cur.execute("SELECT CURRENT_SCHEMA, CURRENT_USER FROM DUMMY")
        schema, user = cur.fetchone()
        print(f"[OK] SAP HANA/Datasphere: conectado. schema={schema} user={user}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[FALHA] SAP HANA/Datasphere: {exc}")
        return False
    finally:
        if conn is not None:
            conn.close()


def check_vendas_sap_tables() -> bool:
    """Confere se as tabelas gold.vendas_sap esperadas existem e têm linhas."""
    try:
        from scripts.db import read_sql
    except ImportError:
        from db import read_sql

    tabelas = [
        "dim_centro_sap",
        "dim_cliente_sap",
        "dim_material_sap",
        "fct_vendas_itens_sap",
        "fct_remessa_itens_sap",
        "fct_faturamento_itens_sap",
        "fct_pendencia_sap",
        "fct_pendencia_status_sap",
    ]
    ok = True
    for tabela in tabelas:
        try:
            df = read_sql(f"SELECT COUNT(*) AS n FROM vendas_sap.{tabela}", database="GOLD")  # nosec B608
            print(f"  [OK] GOLD.vendas_sap.{tabela}: {df.iloc[0]['n']:,} linhas")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FALHA] GOLD.vendas_sap.{tabela}: {exc}")
            ok = False
    return ok


def main() -> int:
    print("== Conectividade SQL Server ==")
    results = [check_sqlserver(db) for db in ("BRONZE", "SILVER", "GOLD")]

    print("\n== Conectividade SAP HANA/Datasphere ==")
    results.append(check_hana())

    print("\n== Tabelas GOLD.vendas_sap ==")
    results.append(check_vendas_sap_tables())

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
