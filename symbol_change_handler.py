"""Detect and apply symbol changes using NSE rename files and ISIN continuity."""

import sys

from db import (
    apply_symbol_rename,
    get_connection,
    load_active_symbol_map,
    setup_schema,
    upsert_symbol_aliases,
)
from logger import get_logger
from nse import fetch_securities_master, fetch_symbol_changes

log = get_logger(__name__)


def build_isin_suggestions(db_df, latest_df):
    latest_by_isin = latest_df[latest_df["isin"] != ""].copy()
    if latest_by_isin.empty:
        return []
    latest_lookup = latest_by_isin.groupby("isin")["symbol"].apply(list).to_dict()
    suggestions = []
    for row in db_df.itertuples(index=False):
        if not row.isin:
            continue
        candidates = [sym for sym in latest_lookup.get(row.isin, []) if sym != row.symbol]
        if len(candidates) == 1:
            suggestions.append({
                "old_symbol": row.symbol,
                "new_symbol": candidates[0],
                "effective_date": None,
                "source": "nse-isin-match",
                "note": f"ISIN continuity match for {row.isin}",
            })
    return suggestions


def filter_valid_change_records(records):
    filtered = []
    seen_pairs = set()
    for record in records:
        old_symbol = (record.get("old_symbol") or "").strip().upper()
        new_symbol = (record.get("new_symbol") or "").strip().upper()
        if not old_symbol or not new_symbol:
            continue
        if old_symbol == new_symbol:
            continue
        pair = (old_symbol, new_symbol)
        if pair in seen_pairs:
            continue
        normalized = dict(record)
        normalized["old_symbol"] = old_symbol
        normalized["new_symbol"] = new_symbol
        filtered.append(normalized)
        seen_pairs.add(pair)
    return filtered


def run_symbol_change_sync(apply_changes=False):
    with get_connection() as conn:
        setup_schema(conn)
        db_df = load_active_symbol_map(conn)

    latest = fetch_securities_master()
    direct = fetch_symbol_changes()
    records = [
        {
            "old_symbol": row.old_symbol,
            "new_symbol": row.new_symbol,
            "effective_date": row.effective_date or None,
            "source": "nse-symbol-changes",
            "note": "Direct NSE symbol change file",
        }
        for row in direct.itertuples(index=False)
    ]
    existing_pairs = {
        (record["old_symbol"], record["new_symbol"])
        for record in filter_valid_change_records(records)
    }
    for suggestion in build_isin_suggestions(db_df, latest):
        pair = (suggestion["old_symbol"], suggestion["new_symbol"])
        if pair not in existing_pairs:
            records.append(suggestion)
    records = filter_valid_change_records(records)

    if not records:
        log.info("No symbol changes detected.")
        return {"detected": 0, "applied": 0}

    with get_connection() as conn:
        upsert_symbol_aliases(conn, records)
        if apply_changes:
            for record in records:
                apply_symbol_rename(
                    conn,
                    record["old_symbol"],
                    record["new_symbol"],
                    effective_date=record["effective_date"],
                    source=record["source"],
                    note=record["note"],
                )
            log.info("Applied symbol changes to DB tables.")
            return {"detected": len(records), "applied": len(records)}
        else:
            log.info("Dry run only. Re-run with --apply to rename stored history.")
            return {"detected": len(records), "applied": 0}


def main():
    apply_changes = "--apply" in sys.argv[1:]
    run_symbol_change_sync(apply_changes=apply_changes)


if __name__ == "__main__":
    main()
