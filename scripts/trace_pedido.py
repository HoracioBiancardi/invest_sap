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
    item_pad = item.zfill(6) if item else None
    resultado: Dict[str, pd.DataFrame] = {}

    # 1) SAP cru via HANA (VBAK/VBAP) — fonte primária, antes de qualquer transformação.
    vbak = read_hana_sql(
        "SELECT MANDT, VBELN, ERDAT, AUART, VKORG, VTWEG, SPART, KUNNR FROM VBAK WHERE VBELN = ?",
        params=(pedido_pad,),
    )
    resultado["SAP HANA — VBAK (cabeçalho)"] = vbak

    vbap_query = (
        "SELECT MANDT, VBELN, POSNR, MATNR, WERKS, ABGRU, KWMENG, ZMENG, VRKME, NETWR, NETPR "
        "FROM VBAP WHERE VBELN = ?"
    )
    vbap_params: list[str] = [pedido_pad]
    if item_pad:
        vbap_query += " AND POSNR = ?"
        vbap_params.append(item_pad)
    resultado["SAP HANA — VBAP (itens)"] = read_hana_sql(vbap_query, params=tuple(vbap_params))

    # 2) Gold vendas_sap — o que o DW mostra hoje para esse pedido.
    fvi_query = "SELECT * FROM vendas_sap.fct_vendas_itens_sap WHERE Numero_Pedido = :pedido"
    fvi_params: dict[str, str] = {"pedido": pedido_pad}
    if item_pad:
        fvi_query += " AND Item_Pedido = :item"
        fvi_params["item"] = item_pad
    resultado["GOLD.vendas_sap.fct_vendas_itens_sap"] = read_sql(
        fvi_query, database="GOLD", params=fvi_params
    )

    fp_query = "SELECT * FROM vendas_sap.fct_pendencia_sap WHERE Numero_Pedido = :pedido"
    fp_params: dict[str, str] = {"pedido": pedido_pad}
    if item_pad:
        fp_query += " AND Item_Pedido = :item"
        fp_params["item"] = item_pad
    resultado["GOLD.vendas_sap.fct_pendencia_sap"] = read_sql(
        fp_query, database="GOLD", params=fp_params
    )

    fvc_query = "SELECT * FROM vendas_sap.fct_vendas_canceladas_sap WHERE Numero_Pedido = :pedido"
    resultado["GOLD.vendas_sap.fct_vendas_canceladas_sap"] = read_sql(
        fvc_query, database="GOLD", params={"pedido": pedido_pad}
    )

    # 3) Salesforce — origem comercial da ordem (Opportunity -> OpportunityLineItem).
    oli_query = """
        SELECT Id, OpportunityId, Ordem_de_Venda_Sap__c, ItemNumero__c, ProductCode,
               Codigo_Material__c, Quantity, Qtde_Ordem__c, Qtde_Pendente__c, Pendencia__c,
               TotalPrice, Total_Venda__c, Status_Faturamento__c, Vendedor__c, IsDeleted,
               CreatedDate
        FROM salesforce.OpportunityLineItem
        WHERE RTRIM(LTRIM(Ordem_de_Venda_Sap__c)) IN (:pedido_raw, :pedido_pad)
    """
    oli = read_sql(
        oli_query, database="SILVER", params={"pedido_raw": pedido_raw, "pedido_pad": pedido_pad}
    )
    resultado["SILVER.salesforce.OpportunityLineItem"] = oli

    opp_ids = [i for i in oli.get("OpportunityId", pd.Series(dtype=str)).dropna().unique()]
    if opp_ids:
        # Um `read_sql` por id (em vez de IN (...) com placeholders expandidos): bind param
        # nomeado não expande bem uma lista via SQLAlchemy text() (mesma limitação notada
        # em query_vendas_sap.py); com poucos ids por pedido, o custo é desprezível.
        opp_query = """
            SELECT id, name, stage_name, is_closed, is_won, amount, close_date,
                   numero_pedido, retorno_numero_pedido, retorno_motivo_status,
                   situacao_pedido_del, sistema_origem_pedido, origem_pedido,
                   organizacao_venda, centro, codigo_cliente, status_faturamento,
                   pendencia_pedido, qtde_faturada, created_date
            FROM salesforce.Opportunity
            WHERE id = :opp_id
        """
        resultado["SILVER.salesforce.Opportunity"] = pd.concat(
            [read_sql(opp_query, database="SILVER", params={"opp_id": oid}) for oid in opp_ids],
            ignore_index=True,
        )
    else:
        # Fallback: procura direto por numero_pedido no cabeçalho, caso o item não tenha
        # sido encontrado em OpportunityLineItem (ex.: pedido criado direto no SAP).
        opp_query = """
            SELECT id, name, stage_name, is_closed, is_won, amount, close_date,
                   numero_pedido, retorno_numero_pedido, retorno_motivo_status,
                   situacao_pedido_del, sistema_origem_pedido, origem_pedido,
                   organizacao_venda, centro, codigo_cliente, status_faturamento,
                   pendencia_pedido, qtde_faturada, created_date
            FROM salesforce.Opportunity
            WHERE numero_pedido IN (:pedido_raw, :pedido_pad)
        """
        resultado["SILVER.salesforce.Opportunity (via numero_pedido, sem OLI)"] = read_sql(
            opp_query,
            database="SILVER",
            params={"pedido_raw": pedido_raw, "pedido_pad": pedido_pad},
        )

    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rastreia um pedido: Salesforce -> SAP cru -> Gold vendas_sap."
    )
    parser.add_argument(
        "numero_pedido", help="Número do pedido de venda (com ou sem zeros à esquerda)"
    )
    parser.add_argument("--item", help="Filtra um item/posição específico (ex.: 10)")
    args = parser.parse_args()

    print(
        f"Rastreando pedido {args.numero_pedido.strip()}"
        + (f", item {args.item}" if args.item else "")
    )
    for titulo, df in trace_pedido(args.numero_pedido, args.item).items():
        _print_df(titulo, df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
