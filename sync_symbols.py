"""Sync active NSE symbols into the standalone NSE EOD database."""

from datetime import datetime

import pandas as pd

from db import get_connection, mark_missing_symbols_inactive, setup_schema, upsert_symbols
from logger import get_logger
from nse import fetch_etf_master, fetch_indices_master, fetch_securities_master

log = get_logger(__name__)


def run_sync():
    master = fetch_securities_master()
    etf_master = pd.DataFrame()
    indices_master = pd.DataFrame()
    try:
        etf_master = fetch_etf_master()
    except Exception as exc:
        log.warning(f"Could not fetch ETF master: {exc}")
    try:
        indices_master = fetch_indices_master()
    except Exception as exc:
        log.warning(f"Could not fetch indices master: {exc}")

    today = datetime.today().strftime("%Y-%m-%d")
    records = []

    for row in master.to_dict("records"):
        if row["series"] not in {"EQ", "BE"}:
            continue
        records.append({
            "symbol": row["symbol"],
            "yahoo_symbol": f"{row['symbol']}.NS",
            "company_name": row["company_name"],
            "isin": row["isin"],
            "series": row["series"],
            "instrument_type": "STOCK",
            "active": 1,
            "status": "active",
            "last_seen_date": today,
            "source": "nse-securities-master",
            "last_synced_at": today,
        })

    for row in etf_master.to_dict("records"):
        records.append({
            "symbol": row["symbol"],
            "yahoo_symbol": f"{row['symbol']}.NS",
            "company_name": row["company_name"] or row["symbol"],
            "isin": row["isin"],
            "series": row["series"],
            "instrument_type": "ETF",
            "active": 1,
            "status": "active",
            "last_seen_date": today,
            "source": "nse-etf-master",
            "last_synced_at": today,
        })

    for row in indices_master.to_dict("records"):
        symbol = row["symbol"]
        records.append({
            "symbol": symbol,
            "yahoo_symbol": None,
            "company_name": symbol,
            "isin": f"IDX_{symbol.replace(' ', '_')}",
            "series": "INDEX",
            "instrument_type": "INDEX",
            "active": 1,
            "status": "active",
            "last_seen_date": today,
            "source": "nse-indices-master",
            "last_synced_at": today,
        })

    unique_records = []
    seen = set()
    for record in records:
        if record["symbol"] in seen:
            continue
        unique_records.append(record)
        seen.add(record["symbol"])

    with get_connection() as conn:
        setup_schema(conn)
        upsert_symbols(conn, unique_records)
        mark_missing_symbols_inactive(conn, [record["symbol"] for record in unique_records])

    log.info(f"NSE symbol sync complete: {len(unique_records):,} symbols.")


def main():
    run_sync()


if __name__ == "__main__":
    main()
