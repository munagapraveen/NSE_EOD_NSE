"""Download NSE EOD history from NSE archives into the standalone database."""

import csv
from datetime import datetime, timedelta
import sys
import time

import pandas as pd

from adjust_splits import rebuild_symbols, refresh_latest_rows
from config import DEFAULT_HISTORY_START, DEFAULT_SHARE_REFRESH_DAYS, FAILED_EOD_FILE
from db import (
    get_active_symbols,
    get_connection,
    get_price_backfill_symbols,
    get_symbol_last_dates,
    insert_raw_prices,
    setup_schema,
    upsert_observed_symbols,
)
from logger import get_logger
from nse import (
    create_session,
    fetch_index_close,
    fetch_security_bhavcopy,
    iter_calendar_dates,
    normalize_index_close,
    normalize_security_bhavcopy,
)
from sync_symbols import run_sync

log = get_logger(__name__)


def parse_args(args):
    options = {
        "bootstrap": "--bootstrap" in args,
        "daily_pipeline": "--daily-pipeline" in args,
        "sync": "--sync" in args,
        "symbols": None,
        "limit": None,
        "start": None,
        "end": None,
        "sleep_secs": 0.0,
    }
    for i, arg in enumerate(args):
        if arg == "--symbols" and i + 1 < len(args):
            options["symbols"] = [part.strip().upper() for part in args[i + 1].split(",") if part.strip()]
        if arg == "--limit" and i + 1 < len(args):
            options["limit"] = int(args[i + 1])
        if arg == "--start" and i + 1 < len(args):
            options["start"] = args[i + 1].strip()
        if arg == "--end" and i + 1 < len(args):
            options["end"] = args[i + 1].strip()
        if arg == "--sleep" and i + 1 < len(args):
            options["sleep_secs"] = max(0.0, float(args[i + 1]))
    return options


def load_target_symbols(limit=None, only_symbols=None):
    with get_connection() as conn:
        setup_schema(conn)
        known_symbols = get_active_symbols(conn)
        selected_symbols = None
        if only_symbols:
            selected_symbols = [symbol.strip().upper() for symbol in only_symbols if symbol.strip()]
            if not known_symbols.empty:
                known_symbols = known_symbols[
                    known_symbols["symbol"].astype(str).str.upper().isin(set(selected_symbols))
                ].copy()
        if limit and selected_symbols:
            selected_symbols = selected_symbols[:limit]
            if not known_symbols.empty:
                known_symbols = known_symbols.head(limit).copy()
        last_dates = get_symbol_last_dates(conn, selected_symbols)
    return known_symbols, last_dates, selected_symbols


def collect_touched_dates(history):
    touched_symbols = history["symbol"].dropna().astype(str).str.upper().unique().tolist()
    touched_dates = (
        history[["symbol", "date"]]
        .dropna()
        .assign(symbol=lambda df: df["symbol"].astype(str).str.upper())
        .groupby("symbol")["date"]
        .apply(lambda series: sorted(series.astype(str).unique().tolist()))
        .to_dict()
    )
    return touched_symbols, touched_dates


def save_failure_report(failures):
    if not failures:
        if FAILED_EOD_FILE.exists():
            FAILED_EOD_FILE.unlink()
        return
    FAILED_EOD_FILE.parent.mkdir(exist_ok=True)
    with FAILED_EOD_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "stage", "error"])
        writer.writeheader()
        writer.writerows(failures)


def _filter_to_symbols(history, selected_symbols=None):
    if history.empty:
        return history
    stock_like = history[history["source"] == "nse-security-bhavcopy"].copy()
    if not stock_like.empty and "series" in stock_like.columns:
        stock_like = stock_like[stock_like["series"].astype(str).str.upper().isin({"EQ", "BE"})].copy()
    index_like = history[history["source"] == "nse-index-close"].copy()
    filtered = pd.concat([stock_like, index_like], ignore_index=True)
    if selected_symbols:
        wanted = {symbol.strip().upper() for symbol in selected_symbols if symbol.strip()}
        filtered = filtered[filtered["symbol"].astype(str).str.upper().isin(wanted)].copy()
    return filtered


def build_observed_symbol_records(history):
    if history.empty:
        return []
    records = []
    seen = set()
    for row in history.itertuples(index=False):
        symbol = str(row.symbol).strip().upper()
        if not symbol or symbol in seen:
            continue
        source = getattr(row, "source", "") or ""
        instrument_type = "INDEX" if source == "nse-index-close" else "STOCK"
        series = getattr(row, "series", "")
        if pd.isna(series) or series is None:
            series = "INDEX" if instrument_type == "INDEX" else ""
        date_value = str(getattr(row, "date", "") or "")
        records.append({
            "symbol": symbol,
            "yahoo_symbol": f"{symbol}.NS" if instrument_type == "STOCK" else None,
            "company_name": symbol,
            "isin": "",
            "series": str(series).strip().upper(),
            "instrument_type": instrument_type,
            "active": 0,
            "status": "observed",
            "last_seen_date": date_value,
            "source": "nse-observed-from-eod",
            "last_synced_at": date_value,
        })
        seen.add(symbol)
    return records


def _fetch_day_frames(report_date, session, selected_symbols=None):
    stock_df = normalize_security_bhavcopy(fetch_security_bhavcopy(report_date, session=session))
    index_df = normalize_index_close(fetch_index_close(report_date, session=session))
    history = pd.concat([stock_df, index_df], ignore_index=True) if not stock_df.empty or not index_df.empty else pd.DataFrame()
    if history.empty:
        return history
    history = _filter_to_symbols(history, selected_symbols)
    if "series" in history.columns:
        history = history.drop(columns=["series"])
    return history


def run_eod_download(
    known_symbols,
    last_dates,
    bootstrap=False,
    start_date=None,
    end_date=None,
    sleep_secs=0.0,
    selected_symbols=None,
    postprocess=True,
):
    if bootstrap:
        start = datetime.strptime(start_date or DEFAULT_HISTORY_START, "%Y-%m-%d").date()
    else:
        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
        else:
            max_last = max((value for value in last_dates.values() if value), default=None)
            start = (
                datetime.strptime(max_last, "%Y-%m-%d").date()
                if max_last
                else datetime.strptime(DEFAULT_HISTORY_START, "%Y-%m-%d").date()
            )
            if max_last:
                start = start + pd.Timedelta(days=1)
                start = start.date() if hasattr(start, "date") else start

    end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else datetime.today().date()
    if start > end:
        log.info("Nothing to download. Database is already up to date for requested range.")
        return {"total_rows": 0, "failures": [], "touched_dates": {}}

    session = create_session()
    all_touched = {}
    total_rows = 0
    failures = []

    for report_date in iter_calendar_dates(start, end):
        try:
            history = _fetch_day_frames(report_date, session, selected_symbols=selected_symbols)
        except Exception as exc:
            failures.append({"date": report_date.isoformat(), "stage": "download", "error": str(exc)})
            continue
        if history.empty:
            continue
        with get_connection() as conn:
            observed_records = build_observed_symbol_records(history)
            upsert_observed_symbols(conn, observed_records)
            insert_raw_prices(conn, history)
        touched_symbols, touched_dates = collect_touched_dates(history)
        total_rows += len(history)
        for symbol in touched_symbols:
            existing = set(all_touched.get(symbol, []))
            existing.update(touched_dates.get(symbol, []))
            all_touched[symbol] = sorted(existing)
        if sleep_secs:
            time.sleep(sleep_secs)

    save_failure_report(failures)
    if postprocess:
        if bootstrap:
            rebuild_symbols(sorted(all_touched.keys()))
        else:
            refresh_latest_rows(all_touched)
    return {"total_rows": total_rows, "failures": failures, "touched_dates": all_touched}


def _load_share_symbols_from_known(known_symbols, selected_symbols=None):
    if known_symbols.empty:
        return known_symbols
    share_symbols = known_symbols[known_symbols["instrument_type"] == "STOCK"].copy()
    if selected_symbols:
        wanted = {symbol.strip().upper() for symbol in selected_symbols if symbol.strip()}
        share_symbols = share_symbols[share_symbols["symbol"].astype(str).str.upper().isin(wanted)].copy()
    return share_symbols


def load_bootstrap_share_scope(limit=None, only_symbols=None):
    with get_connection() as conn:
        setup_schema(conn)
        scope = get_price_backfill_symbols(
            conn,
            instrument_type="STOCK",
            only_symbols=only_symbols,
            exclude_statuses=["renamed"],
        )
        if limit:
            scope = scope.head(limit).copy()
    return scope


def run_daily_refresh_pipeline(known_symbols, last_dates, selected_symbols=None, start_date=None, end_date=None, sleep_secs=0.0):
    from sync_corporate_actions import run_corporate_sync
    from sync_share_counts import run_share_download

    run_corporate_sync(rebuild=True, symbols=selected_symbols)
    summary = run_eod_download(
        known_symbols,
        last_dates,
        bootstrap=False,
        start_date=start_date,
        end_date=end_date,
        sleep_secs=sleep_secs,
        selected_symbols=selected_symbols,
    )
    share_symbols = _load_share_symbols_from_known(known_symbols, selected_symbols=selected_symbols)
    recent_start = (datetime.today() - timedelta(days=DEFAULT_SHARE_REFRESH_DAYS)).strftime("%Y-%m-%d")
    run_share_download(share_symbols, start=recent_start)
    if summary["touched_dates"]:
        refresh_latest_rows(summary["touched_dates"])
    return summary


def main():
    options = parse_args(sys.argv[1:])
    if options["sync"] or options["daily_pipeline"]:
        run_sync()
    symbols, last_dates, selected_symbols = load_target_symbols(limit=options["limit"], only_symbols=options["symbols"])
    if options["daily_pipeline"]:
        summary = run_daily_refresh_pipeline(
            symbols,
            last_dates,
            selected_symbols=selected_symbols,
            start_date=options["start"],
            end_date=options["end"],
            sleep_secs=options["sleep_secs"],
        )
    else:
        summary = run_eod_download(
            symbols,
            last_dates,
            bootstrap=options["bootstrap"],
            start_date=options["start"],
            end_date=options["end"],
            sleep_secs=options["sleep_secs"],
            selected_symbols=selected_symbols,
            postprocess=not options["bootstrap"],
        )
    if options["bootstrap"]:
        from sync_share_counts import run_share_download
        from sync_corporate_actions import run_corporate_sync
        from symbol_change_handler import run_symbol_change_sync

        run_corporate_sync(rebuild=True, symbols=selected_symbols)
        run_symbol_change_sync(apply_changes=True)
        share_symbols = load_bootstrap_share_scope(limit=options["limit"], only_symbols=options["symbols"])
        run_share_download(share_symbols)
        rebuild_symbols(share_symbols["symbol"].tolist())
    log.info(f"Download complete: {summary['total_rows']:,} rows.")


if __name__ == "__main__":
    main()
