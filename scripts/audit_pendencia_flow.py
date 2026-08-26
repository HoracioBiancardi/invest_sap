"""Auditoria genérica do fluxo de vendas/pendência SAP — varre GOLD.vendas_sap (e o
GOLD.vendas legado) procurando os mesmos *padrões* de anomalia que achamos investigando o
pedido 137490 (ver docs/CONTEXTO_VENDAS_SAP.md §6.9), sem depender de já saber qual pedido
está quebrado.

4 checagens, cada uma pensada pra pegar uma classe diferente de problema:

1. `valor_sem_quantidade`   — linha tem valor > 0 mas quantidade = 0 (o padrão exato do
                               bug KWMENG/ZMENG). Roda em fct_vendas_itens_sap,
                               fct_vendas_canceladas_sap, fct_faturamento_itens_sap e no
                               dim_pendencia legado.
2. `pendencia_escondida`    — sintoma direto em fct_pendencia_sap: pedido com valor > 0,
                               nunca remetido nem faturado, mas classificado como
                               'Concluido'. Não depende de entender a causa raiz —
                               pega qualquer variação futura do mesmo tipo de bug.
3. `reconciliacao_contagem` — compara contagem de itens no SAP cru (HANA, VBAP não
                               rejeitado) vs GOLD.fct_vendas_itens_sap, por tipo de
                               pedido. Diferença grande indica join quebrado/perda de
                               linhas na camada Gold (não é o caso do bug 137490, mas
                               pegaria uma classe de problema diferente: pedidos que
                               somem inteiros, não só com quantidade zerada).
4. `integridade_dimensoes`  — % de linhas em fct_pendencia_sap onde o join com
                               dim_cliente_sap/dim_centro_sap/dim_material_sap falhou
                               (campo descritivo NULL) — indica dimensão desatualizada
                               ou chave de join divergente.

Uso:
    uv run python scripts/audit_pendencia_flow.py            # roda tudo, imprime relatório
    uv run python scripts/audit_pendencia_flow.py --checks valor_sem_quantidade,pendencia_escondida
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict

import pandas as pd

try:
    from scripts.db import read_hana_sql, read_sql
except ImportError:
    from db import read_hana_sql, read_sql

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)


def _print_df(titulo: str, df: pd.DataFrame) -> None:
    print(f"\n--- {titulo} ---")
    if df.empty:
        print("(nenhuma anomalia encontrada)")
    else:
        print(df.to_string(index=False))


def audit_valor_sem_quantidade() -> Dict[str, pd.DataFrame]:
    """Checagem 1: valor > 0 com quantidade = 0, tabela por tabela.

    Retorna um dict {titulo: DataFrame} — mais de uma tabela por checagem.
    """
    alvos = [
        ("vendas_sap.fct_vendas_itens_sap", "Qtd_Pedida_Original", "Valor_Liquido_Pedido", "Tipo_Ordem_Venda"),
        ("vendas_sap.fct_vendas_canceladas_sap", "Qtd_Cancelada", "Valor_Liquido_Cancelado", "Tipo_Ordem_Venda"),
        ("vendas_sap.fct_faturamento_itens_sap", "Qtd_Faturada", "Valor_Liquido_Faturamento", "Tipo_Documento_Faturamento"),
    ]
    resultado: Dict[str, pd.DataFrame] = {}
    for tabela, qty_col, val_col, group_col in alvos:
        q = f"""
            SELECT
                {group_col} AS Grupo,
                COUNT(*) AS Total_Itens,
                SUM(CASE WHEN {qty_col} = 0 AND {val_col} > 0 THEN 1 ELSE 0 END) AS Itens_Anomalos,
                SUM(CASE WHEN {qty_col} = 0 AND {val_col} > 0 THEN {val_col} ELSE 0 END) AS Valor_Anomalo
            FROM {tabela}
            GROUP BY {group_col}
            HAVING SUM(CASE WHEN {qty_col} = 0 AND {val_col} > 0 THEN 1 ELSE 0 END) > 0
            ORDER BY Valor_Anomalo DESC
        """  # nosec B608
        titulo = f"Valor sem quantidade — GOLD.{tabela} ({qty_col}=0 e {val_col}>0)"
        resultado[titulo] = read_sql(q, database="GOLD")

    # dim_pendencia legado: nomes de coluna diferentes (snake_case, sem alias PascalCase)
    q_legado = """
        SELECT
            COUNT(*) AS Total_Itens,
            SUM(CASE WHEN kwmeng_quantidade_da_ordem_acumulada_em_unidade_de_venda = 0
                     AND netwr_valor_liquido_do_item_da_ordem_na_moeda_do_documento > 0
                THEN 1 ELSE 0 END) AS Itens_Anomalos
        FROM vendas.dim_pendencia
    """  # nosec B608
    resultado["Valor sem quantidade — GOLD.vendas.dim_pendencia (legado)"] = read_sql(q_legado, database="GOLD")
    return resultado


def audit_pendencia_escondida() -> pd.DataFrame:
    """Checagem 2: sintoma direto em fct_pendencia_sap — 'Concluido' sem nunca ter sido
    remetido/faturado, apesar de ter valor. Pega qualquer causa raiz, não só KWMENG/ZMENG.
    """
    q = """
        SELECT
            Tipo_Ordem_Venda,
            COUNT(*) AS Itens_Suspeitos,
            SUM(Valor_Liquido_Pedido) AS Valor_Suspeito
        FROM vendas_sap.fct_pendencia_sap
        WHERE Status_Pendencia = 'Concluido'
          AND Qtd_Remetida = 0
          AND Qtd_Faturada = 0
          AND Valor_Liquido_Pedido > 0
        GROUP BY Tipo_Ordem_Venda
        ORDER BY Valor_Suspeito DESC
    """  # nosec B608
    return read_sql(q, database="GOLD")


def audit_reconciliacao_contagem() -> pd.DataFrame:
    """Checagem 3: contagem SAP cru (HANA) vs GOLD, por tipo de pedido — detecta perda de
    linhas inteiras na camada Gold (join quebrado), não só quantidade zerada.
    """
    # HANA dobra identificadores sem aspas para maiúsculo (mesmo o alias) — usar aspas
    # duplas no alias pra preservar o nome exato que o pandas vai usar como coluna.
    q_hana = """
        SELECT VBAK.AUART AS "Tipo_Ordem_Venda", COUNT(*) AS "Itens_SAP_Cru"
        FROM VBAP
        INNER JOIN VBAK ON VBAP.MANDT = VBAK.MANDT AND VBAP.VBELN = VBAK.VBELN
        WHERE COALESCE(TRIM(VBAP.ABGRU), '') = ''
        GROUP BY VBAK.AUART
    """  # nosec B608
    df_hana = read_hana_sql(q_hana)

    q_gold = """
        SELECT Tipo_Ordem_Venda, COUNT(*) AS Itens_Gold
        FROM vendas_sap.fct_vendas_itens_sap
        GROUP BY Tipo_Ordem_Venda
    """  # nosec B608
    df_gold = read_sql(q_gold, database="GOLD")

    df_hana["Tipo_Ordem_Venda"] = df_hana["Tipo_Ordem_Venda"].str.strip()
    merged = df_hana.merge(df_gold, on="Tipo_Ordem_Venda", how="outer").fillna(0)
    merged["Itens_SAP_Cru"] = merged["Itens_SAP_Cru"].astype(int)
    merged["Itens_Gold"] = merged["Itens_Gold"].astype(int)
    merged["Diferenca"] = merged["Itens_SAP_Cru"] - merged["Itens_Gold"]
    merged["Diferenca_Pct"] = (
        (merged["Diferenca"] / merged["Itens_SAP_Cru"].replace(0, pd.NA) * 100).round(1)
    )
    # Só reporta tipos com diferença relevante (>1% e >10 itens) — algum ruído residual
    # (pedidos processados entre a extração HANA e a última carga Gold) é esperado.
    suspeitos = merged[(merged["Diferenca"].abs() > 10) & (merged["Diferenca_Pct"].abs() > 1)]
    return suspeitos.sort_values("Diferenca", key=abs, ascending=False)


def audit_integridade_dimensoes() -> pd.DataFrame:
    """Checagem 4: % de linhas em fct_pendencia_sap com join de dimensão falho (NULL)."""
    q = """
        SELECT
            COUNT(*) AS Total_Itens,
            SUM(CASE WHEN Nome_Cliente IS NULL THEN 1 ELSE 0 END) AS Sem_Nome_Cliente,
            SUM(CASE WHEN Nome_Centro IS NULL THEN 1 ELSE 0 END) AS Sem_Nome_Centro,
            SUM(CASE WHEN Descricao_Produto IS NULL THEN 1 ELSE 0 END) AS Sem_Descricao_Produto
        FROM vendas_sap.fct_pendencia_sap
    """  # nosec B608
    df = read_sql(q, database="GOLD")
    total = int(df.iloc[0]["Total_Itens"])
    for col in ("Sem_Nome_Cliente", "Sem_Nome_Centro", "Sem_Descricao_Produto"):
        df[f"{col}_Pct"] = (df[col] / total * 100).round(2)
    return df


CHECKS = {
    "valor_sem_quantidade": audit_valor_sem_quantidade,
    "pendencia_escondida": audit_pendencia_escondida,
    "reconciliacao_contagem": audit_reconciliacao_contagem,
    "integridade_dimensoes": audit_integridade_dimensoes,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Auditoria do fluxo de vendas/pendência SAP.")
    parser.add_argument(
        "--checks",
        default=",".join(CHECKS),
        help=f"Lista separada por vírgula das checagens a rodar. Disponíveis: {', '.join(CHECKS)}",
    )
    args = parser.parse_args()

    selecionadas = [c.strip() for c in args.checks.split(",") if c.strip()]
    for nome in selecionadas:
        if nome not in CHECKS:
            print(f"Checagem desconhecida: {nome!r}. Disponíveis: {', '.join(CHECKS)}")
            return 1

    print(f"Rodando {len(selecionadas)} checagem(ns): {', '.join(selecionadas)}")
    for nome in selecionadas:
        resultado = CHECKS[nome]()
        if isinstance(resultado, dict):
            for titulo, df in resultado.items():
                _print_df(titulo, df)
        else:
            _print_df(nome, resultado)

    return 0


if __name__ == "__main__":
    sys.exit(main())
