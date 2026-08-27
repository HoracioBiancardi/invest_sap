"""Consulta o dicionário de dados do SAP (DDIC) direto no HANA/Datasphere.

Útil quando uma tabela/campo do SAP aparece numa investigação (ex.: "o que é VBUP.FKSTA?")
e a descrição não está documentada em docs/CONTEXTO_VENDAS_SAP.md.

Tabelas DDIC usadas:
    DD02T - Descrição de tabelas (nome curto -> texto)
    DD03L - Descrição de campos por tabela (inclui tipo de dado, tamanho, campo-chave)

Uso:
    uv run python scripts/ddic_lookup.py VBAK
    uv run python scripts/ddic_lookup.py VBAK --campo AUART
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

try:
    from scripts.db import read_hana_sql
except ImportError:
    from db import read_hana_sql


def descricao_tabela(tabela: str, idioma: str = "P") -> pd.DataFrame:
    """Retorna a descrição textual de uma tabela SAP (DD02T)."""
    query = """
        SELECT TABNAME, DDTEXT
        FROM DD02T
        WHERE TABNAME = ? AND DDLANGUAGE = ?
    """
    return read_hana_sql(query, params=(tabela.upper(), idioma))


def campos_tabela(tabela: str, idioma: str = "P") -> pd.DataFrame:
    """Lista os campos de uma tabela SAP com descrição, tipo e se é chave (DD03L)."""
    query = """
        SELECT
            L.FIELDNAME,
            T.DDTEXT AS DESCRICAO,
            L.KEYFLAG,
            L.DATATYPE,
            L.LENG,
            L.POSITION
        FROM DD03L AS L
        LEFT JOIN DD04T AS T
            ON L.ROLLNAME = T.ROLLNAME AND T.DDLANGUAGE = ?
        WHERE L.TABNAME = ?
        ORDER BY L.POSITION
    """
    return read_hana_sql(query, params=(idioma, tabela.upper()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Lookup de metadados SAP DDIC (DD02T/DD03L).")
    parser.add_argument("tabela", help="Nome da tabela SAP (ex.: VBAK, VBAP, VBRK)")
    parser.add_argument("--campo", help="Filtra a descrição de um campo específico (ex.: AUART)")
    parser.add_argument("--idioma", default="P", help="Idioma DDIC (default: P = português)")
    args = parser.parse_args()

    desc = descricao_tabela(args.tabela, args.idioma)
    if desc.empty:
        print(f"Tabela {args.tabela!r} não encontrada em DD02T (idioma={args.idioma}).")
    else:
        print(f"{args.tabela}: {desc.iloc[0]['DDTEXT']}\n")

    campos = campos_tabela(args.tabela, args.idioma)
    if args.campo:
        campos = campos[campos["FIELDNAME"].str.upper() == args.campo.upper()]

    if campos.empty:
        print("Nenhum campo encontrado.")
        return 1

    print(campos.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
