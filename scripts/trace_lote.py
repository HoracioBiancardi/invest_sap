"""Rastreia a movimentação de um lote no tempo: produção -> qualidade -> liberação -> saída.

Fonte: `SILVER.dataspherev2.mseg`/`mkpf` (MSEG/MKPF do SAP — documento de material), grão de
*evento* (1 linha por movimento), diferente de `GOLD.vendas_sap.fct_estoque_lote_sap`, que é
uma foto do estoque *agora* (1 linha por Material+Centro+Depósito+Lote, sem histórico — ver
docs/CONTEXTO_VENDAS_SAP.md §6.4). Não existe hoje uma tabela GOLD equivalente pra isso; esta
consulta lê direto de SILVER.

`Descricao_Movimento` traduz `Bwart` (tipo de movimento) usando os textos-padrão do SAP
(T156T não está replicado neste DW — ver `scripts/ddic_lookup.py` — então o mapeamento abaixo
é fixo, cobrindo os `Bwart` mais comuns encontrados nesta base; código sem mapa aparece cru).

`estoque_historico_material_centro()` usa uma fonte DIFERENTE: `IB_SAPECC.MCHBH` (view HANA
com dado real de fechamento de período, não replicada no DW) — ver docstring da função.

Uso:
    uv run python scripts/trace_lote.py PA5522 24101103
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import pandas as pd

try:
    from scripts.db import read_hana_sql, read_sql
except ImportError:
    from db import read_hana_sql, read_sql

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)

# Textos-padrão SAP por Bwart (tipo de movimento) — cobre os códigos mais frequentes em
# dataspherev2.mseg nesta base (levantado por COUNT(*) GROUP BY bwart). Estorno = mesma
# operação revertida (SAP não faz UPDATE, lança o inverso com doc próprio).
DESCRICAO_BWART = {
    "101": "Entrada de mercadoria (produção/pedido)",
    "102": "Estorno de entrada de mercadoria (101)",
    "201": "Saída p/ centro de custo",
    "202": "Estorno de saída p/ centro de custo",
    "261": "Saída de mercadoria p/ ordem (consumo em produção)",
    "262": "Estorno de consumo em produção (261)",
    "311": "Transferência entre depósitos",
    "312": "Estorno de transferência entre depósitos",
    "321": "Liberação: Qualidade → Livre utilização",
    "322": "Estorno de liberação Qualidade → Livre (321)",
    "325": "Transferência: Qualidade → Bloqueado",
    "326": "Estorno de Qualidade → Bloqueado (325)",
    "331": "Transferência: Bloqueado → Livre utilização",
    "332": "Estorno de Bloqueado → Livre (331)",
    "341": "Transferência: Livre utilização → Qualidade",
    "342": "Estorno de Livre → Qualidade (341)",
    "343": "Transferência: Livre utilização → Bloqueado",
    "344": "Estorno de Livre → Bloqueado (343)",
    "411": "Transferência de estoque especial → próprio",
    "412": "Estorno de transferência especial → próprio (411)",
    "561": "Lançamento de saldo inicial de estoque",
    "562": "Estorno de saldo inicial (561)",
    "601": "Saída de mercadoria p/ remessa (venda)",
    "602": "Estorno de saída p/ remessa (601)",
    "631": "Transferência entre centros (em trânsito)",
    "632": "Estorno de transferência entre centros (631)",
    "711": "Ajuste de inventário: entrada",
    "712": "Estorno de ajuste de inventário (711)",
    "713": "Ajuste de inventário: saída",
    "714": "Estorno de ajuste de inventário (713)",
    "861": "Entrada por transferência entre centros (pedido de transferência)",
    "862": "Estorno de entrada por transferência entre centros (861)",
}

# Indicador de tipo de estoque (MSEG.INSMK) — padrão SAP.
DESCRICAO_INSMK = {
    "": "Livre utilização",
    " ": "Livre utilização",
    "X": "Qualidade",
    "S": "Bloqueado",
}


def trace_lote(codigo_material: str, numero_lote: str, codigo_centro: Optional[str] = None) -> pd.DataFrame:
    """Busca todos os movimentos (MSEG+MKPF) de um lote, ordenados por data de lançamento.

    Args:
        codigo_material: código do material tal como aparece em `Codigo_Material` do GOLD
            (mesmo formato cru do SAP, ex.: '000000000007009555' ou 'PA5522' — sem
            padding adicional, o valor já vem consistente entre MSEG e o GOLD).
        numero_lote: número do lote (MSEG.CHARG).
        codigo_centro: filtra um centro específico (opcional — um lote pode transitar
            por mais de um centro via transferência).
    """
    filtros = ["s.matnr = :material", "s.charg = :lote"]
    params: dict[str, str] = {"material": codigo_material.strip(), "lote": numero_lote.strip()}
    if codigo_centro:
        filtros.append("s.werks = :centro")
        params["centro"] = codigo_centro.strip()
    where = " AND ".join(filtros)

    query = f"""
        SELECT
            k.budat AS Data_Lancamento,
            s.bwart AS Bwart,
            s.werks AS Codigo_Centro,
            s.lgort AS Codigo_Deposito,
            s.insmk AS Indicador_Estoque,
            s.shkzg AS Debito_Credito,
            s.menge AS Quantidade,
            s.meins AS Unidade,
            s.umwrk AS Centro_Destino,
            s.umlgo AS Deposito_Destino,
            s.umcha AS Lote_Destino,
            s.kdauf AS Pedido_Venda,
            s.kdpos AS Item_Pedido,
            k.usnam AS Usuario,
            s.mblnr AS Numero_Documento,
            s.mjahr AS Ano_Documento
        FROM dataspherev2.mseg s
        JOIN dataspherev2.mkpf k
            ON s.mandt = k.mandt AND s.mblnr = k.mblnr AND s.mjahr = k.mjahr
        WHERE {where}
        ORDER BY k.budat, s.mblnr, s.zeile
    """  # nosec B608
    df = read_sql(query, database="SILVER", params=params)
    if df.empty:
        return df

    df["Descricao_Movimento"] = df["Bwart"].map(DESCRICAO_BWART).fillna("Bwart " + df["Bwart"].astype(str) + " (não mapeado)")
    df["Descricao_Estoque"] = df["Indicador_Estoque"].fillna("").map(DESCRICAO_INSMK).fillna(df["Indicador_Estoque"])
    df["Direcao"] = df["Debito_Credito"].map({"S": "Entrada", "H": "Saída"}).fillna(df["Debito_Credito"])
    return df


def estoque_historico_material_centro(codigo_material: str, codigo_centro: str, data_corte) -> dict:
    """Estoque REAL de um Material+Centro num fechamento de período passado (mês/ano).

    Achado de auditoria (2026-09-03): `IB_SAPECC.MCHBH` ("Estoques de lotes -
    histórico") existe e tem dado real no HANA/Datasphere deste projeto (798 mil
    linhas, testado ao vivo) — snapshot de FECHAMENTO DE PERÍODO por lote, calculado
    pelo próprio SAP (não uma reconstrução nossa). Isso substitui a versão anterior
    desta função, que reconstruía o saldo somando `MSEG`/`MKPF` e calibrava contra o
    estoque de hoje — uma estimativa que dava número errado quando o material já tinha
    estoque antes do início da réplica de `MSEG` (~2024). Validado o número novo contra
    o antigo: PA8116/centro 1100, fechamento de 06/2026 real = Livre 1 + Qualidade 27 +
    Bloqueado 0 (total 28) — a estimativa antiga tinha dado total 36, perto, mas a
    divisão por tipo (Livre 7.965 / Qualidade -7.964) era pura calibração quebrada.

    Limitação: granularidade de MÊS FECHADO, não do dia exato — usa o fechamento do
    mês da `data_corte` se já existir, senão o fechamento mais recente ANTES dele
    (`Periodo_E_Exato=False` nesse caso). O mês corrente (ainda não fechado pelo SAP)
    nunca tem linha em `MCHBH` — por isso pedidos muito recentes (últimos dias/semanas)
    sempre caem no fechamento do mês anterior.

    `MCHBH` não está replicado no nosso Data Warehouse (BRONZE/SILVER/GOLD) — só existe
    como view no HANA/Datasphere (`IB_SAPECC.MCHBH`), então esta função consulta direto
    lá (mesma conexão de `scripts/ddic_lookup.py`/`trace_pedido.py`). Considerar pedir
    ingestão pro repo `data-platform` se o uso deste drill-down crescer.

    Args:
        codigo_material: código do material (mesmo formato de `Codigo_Material` no GOLD).
        codigo_centro: código do centro.
        data_corte: data (str 'YYYY-MM-DD' ou `date`/`datetime`) — normalmente
            `Data_Inclusao_Pedido` do pedido que se quer entender.

    Returns:
        dict com `Qtd_{Livre,Qualidade,Bloqueado}_Periodo` (real, do período
        encontrado), `Qtd_{Livre,Qualidade,Bloqueado}_Atual_Real` (hoje, de
        `fct_estoque_lote_sap`, pra comparação), `Periodo_Ano`/`Periodo_Mes` (período
        efetivamente usado), `Periodo_E_Exato` (bool) e `Cobertura_Suficiente` (bool:
        False se não achou NENHUM período em `MCHBH` pra esse Material+Centro).
    """
    material = codigo_material.strip()
    centro = codigo_centro.strip()
    data_corte_ts = pd.Timestamp(data_corte)
    periodo_alvo = int(data_corte_ts.strftime("%Y%m"))

    periodos_query = """
        SELECT LFGJA, LFMON, SUM(CLABS) AS Livre, SUM(CINSM) AS Qualidade, SUM(CSPEM) AS Bloqueado
        FROM IB_SAPECC.MCHBH
        WHERE MATNR = ? AND WERKS = ?
        GROUP BY LFGJA, LFMON
    """  # nosec B608
    periodos = read_hana_sql(periodos_query, params=(material, centro))
    # HANA dobra identificador sem aspas pra maiúsculo (LFGJA/LIVRE/...), mesmo com
    # "AS Livre" na query — normaliza aqui pra não depender de como o driver devolve.
    periodos.columns = periodos.columns.str.upper()

    resultado: dict = {
        "Qtd_Livre_Periodo": 0.0,
        "Qtd_Qualidade_Periodo": 0.0,
        "Qtd_Bloqueado_Periodo": 0.0,
        "Periodo_Ano": None,
        "Periodo_Mes": None,
        "Periodo_E_Exato": False,
        "Cobertura_Suficiente": False,
    }
    if not periodos.empty:
        periodos["Periodo_Num"] = periodos["LFGJA"].astype(int) * 100 + periodos["LFMON"].astype(int)
        candidatos = periodos[periodos["Periodo_Num"] <= periodo_alvo].sort_values("Periodo_Num")
        if not candidatos.empty:
            linha_periodo = candidatos.iloc[-1]
            resultado["Qtd_Livre_Periodo"] = float(linha_periodo["LIVRE"] or 0.0)
            resultado["Qtd_Qualidade_Periodo"] = float(linha_periodo["QUALIDADE"] or 0.0)
            resultado["Qtd_Bloqueado_Periodo"] = float(linha_periodo["BLOQUEADO"] or 0.0)
            resultado["Periodo_Ano"] = str(linha_periodo["LFGJA"])
            resultado["Periodo_Mes"] = str(linha_periodo["LFMON"])
            resultado["Periodo_E_Exato"] = int(linha_periodo["Periodo_Num"]) == periodo_alvo
            resultado["Cobertura_Suficiente"] = True

    atual_query = """
        SELECT
            SUM(Qtd_Estoque_Livre) AS Livre_Atual_Real,
            SUM(Qtd_Estoque_Qualidade) AS Qualidade_Atual_Real,
            SUM(Qtd_Estoque_Bloqueado) AS Bloqueado_Atual_Real
        FROM vendas_sap.fct_estoque_lote_sap
        WHERE Codigo_Material = :material AND Codigo_Centro = :centro
    """  # nosec B608
    atual = read_sql(atual_query, database="GOLD", params={"material": material, "centro": centro}).iloc[0].fillna(0.0)
    resultado["Qtd_Livre_Atual_Real"] = float(atual["Livre_Atual_Real"])
    resultado["Qtd_Qualidade_Atual_Real"] = float(atual["Qualidade_Atual_Real"])
    resultado["Qtd_Bloqueado_Atual_Real"] = float(atual["Bloqueado_Atual_Real"])
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description="Rastreia a movimentação de um lote (MSEG/MKPF).")
    parser.add_argument("codigo_material", help="Código do material (ex.: PA5522)")
    parser.add_argument("numero_lote", help="Número do lote (ex.: 24101103)")
    parser.add_argument("--centro", help="Filtra um centro específico (opcional)")
    args = parser.parse_args()

    df = trace_lote(args.codigo_material, args.numero_lote, args.centro)
    print(f"\n--- Movimentos do lote {args.numero_lote} (material {args.codigo_material}) ({len(df)} linha(s)) ---")
    if df.empty:
        print("(nada encontrado)")
    else:
        print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
