"""Rastreia um pedido de venda pelas 3 camadas: Salesforce (Opportunity) -> SAP cru (HANA)
-> Gold vendas_sap (SQL Server).

Fluxo de negócio: a Opportunity é criada no Salesforce, vira OpportunityLineItem(s) por
produto, e quando aprovada é transmitida ao SAP, que cria o pedido de venda (VBAK/VBAP).
O campo OpportunityLineItem.Ordem_de_Venda_Sap__c (+ ItemNumero__c) é o elo de volta: guarda
o VBELN/POSNR do pedido criado no SAP. O cabeçalho Opportunity também guarda o retorno dessa
integração em numero_pedido/retorno_numero_pedido/retorno_motivo_status/situacao_pedido_del.

Uso:
    uv run python scripts/trace_pedido.py 137490
    uv run python scripts/trace_pedido.py 137490 --item 10
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional

import pandas as pd

try:
    from scripts.db import read_hana_sql, read_sql
except ImportError:
    from db import read_hana_sql, read_sql

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)


def _print_df(titulo: str, df: pd.DataFrame) -> None:
    print(f"\n--- {titulo} ({len(df)} linha(s)) ---")
    if df.empty:
        print("(nada encontrado)")
    else:
        print(df.to_string(index=False))


def trace_pedido(numero_pedido: str, item: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """Busca um pedido nas 3 camadas (SAP cru, Gold, Salesforce).

    Retorna um dict {titulo: DataFrame} na ordem em que as seções devem aparecer.
    """
    pedido_raw = numero_pedido.strip()
    pedido_pad = pedido_raw.zfill(10)
    resultado: Dict[str, pd.DataFrame] = {}

    # 1) SAP cru via HANA (VBAK/VBAP) — fonte primária, antes de qualquer transformação.
    vbak = read_hana_sql(f"SELECT MANDT, VBELN, ERDAT, AUART, VKORG, VTWEG, SPART, KUNNR FROM VBAK WHERE VBELN = '{pedido_pad}'")  # nosec B608
    resultado["SAP HANA — VBAK (cabeçalho)"] = vbak

    vbap_query = f"SELECT MANDT, VBELN, POSNR, MATNR, WERKS, ABGRU, KWMENG, ZMENG, VRKME, NETWR, NETPR FROM VBAP WHERE VBELN = '{pedido_pad}'"  # nosec B608
    if item:
        vbap_query += f" AND POSNR = '{item.zfill(6)}'"
    resultado["SAP HANA — VBAP (itens)"] = read_hana_sql(vbap_query)

    # 2) Gold vendas_sap — o que o DW mostra hoje para esse pedido.
    fvi_query = f"SELECT * FROM vendas_sap.fct_vendas_itens_sap WHERE Numero_Pedido = '{pedido_pad}'"  # nosec B608
    if item:
        fvi_query += f" AND Item_Pedido = '{item.zfill(6)}'"
    resultado["GOLD.vendas_sap.fct_vendas_itens_sap"] = read_sql(fvi_query, database="GOLD")

    fp_query = f"SELECT * FROM vendas_sap.fct_pendencia_sap WHERE Numero_Pedido = '{pedido_pad}'"  # nosec B608
    if item:
        fp_query += f" AND Item_Pedido = '{item.zfill(6)}'"
    resultado["GOLD.vendas_sap.fct_pendencia_sap"] = read_sql(fp_query, database="GOLD")

    fvc_query = f"SELECT * FROM vendas_sap.fct_vendas_canceladas_sap WHERE Numero_Pedido = '{pedido_pad}'"  # nosec B608
    resultado["GOLD.vendas_sap.fct_vendas_canceladas_sap"] = read_sql(fvc_query, database="GOLD")

    # 3) Salesforce — origem comercial da ordem (Opportunity -> OpportunityLineItem).
    oli_query = f"""
        SELECT Id, OpportunityId, Ordem_de_Venda_Sap__c, ItemNumero__c, ProductCode,
               Codigo_Material__c, Quantity, Qtde_Ordem__c, Qtde_Pendente__c, Pendencia__c,
               TotalPrice, Total_Venda__c, Status_Faturamento__c, Vendedor__c, IsDeleted,
               CreatedDate
        FROM salesforce.OpportunityLineItem
        WHERE RTRIM(LTRIM(Ordem_de_Venda_Sap__c)) IN ('{pedido_raw}', '{pedido_pad}')
    """  # nosec B608
    oli = read_sql(oli_query, database="SILVER")
    resultado["SILVER.salesforce.OpportunityLineItem"] = oli

    opp_ids = [i for i in oli.get("OpportunityId", pd.Series(dtype=str)).dropna().unique()]
    if opp_ids:
        placeholders = ",".join(f"'{i}'" for i in opp_ids)
        opp_query = f"""
            SELECT id, name, stage_name, is_closed, is_won, amount, close_date,
                   numero_pedido, retorno_numero_pedido, retorno_motivo_status,
                   situacao_pedido_del, sistema_origem_pedido, origem_pedido,
                   organizacao_venda, centro, codigo_cliente, status_faturamento,
                   pendencia_pedido, qtde_faturada, created_date
            FROM salesforce.Opportunity
            WHERE id IN ({placeholders})
        """  # nosec B608
        resultado["SILVER.salesforce.Opportunity"] = read_sql(opp_query, database="SILVER")
    else:
        # Fallback: procura direto por numero_pedido no cabeçalho, caso o item não tenha
        # sido encontrado em OpportunityLineItem (ex.: pedido criado direto no SAP).
        opp_query = f"""
            SELECT id, name, stage_name, is_closed, is_won, amount, close_date,
                   numero_pedido, retorno_numero_pedido, retorno_motivo_status,
                   situacao_pedido_del, sistema_origem_pedido, origem_pedido,
                   organizacao_venda, centro, codigo_cliente, status_faturamento,
                   pendencia_pedido, qtde_faturada, created_date
            FROM salesforce.Opportunity
            WHERE numero_pedido IN ('{pedido_raw}', '{pedido_pad}')
        """  # nosec B608
        resultado["SILVER.salesforce.Opportunity (via numero_pedido, sem OLI)"] = read_sql(opp_query, database="SILVER")

    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description="Rastreia um pedido: Salesforce -> SAP cru -> Gold vendas_sap.")
    parser.add_argument("numero_pedido", help="Número do pedido de venda (com ou sem zeros à esquerda)")
    parser.add_argument("--item", help="Filtra um item/posição específico (ex.: 10)")
    args = parser.parse_args()

    print(f"Rastreando pedido {args.numero_pedido.strip()}" + (f", item {args.item}" if args.item else ""))
    for titulo, df in trace_pedido(args.numero_pedido, args.item).items():
        _print_df(titulo, df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
