"""Materialize split-adjusted OHLCV prices from raw NSE history."""

import sys
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from db import (
    get_active_symbols,
    get_connection,
    load_adjusted_market_caps,
    load_corporate_actions,
    load_raw_prices,
    load_share_history,
    get_symbol_instrument_types,
    replace_adjusted_prices,
    save_indicators,
    save_market_caps,
    setup_schema,
    upsert_adjusted_prices,
)
from logger import get_logger

log = get_logger(__name__)
MA_WINDOWS = [5, 10, 20, 50, 100, 200]


def truncate_to_2dp(value):
    if pd.isna(value):
        return value
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def build_split_adjusted(df, actions=None):
    if df.empty:
        return df
    work = df.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values("date")
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce").astype(float)
    work["stock_splits"] = 1.0

    if actions is not None and not actions.empty:
        actions = actions.copy()
        actions["date"] = pd.to_datetime(actions["date"])
        for row in actions.itertuples():
            matches = work[work["date"] >= row.date]
            if not matches.empty:
                target_idx = matches.index[0]
                work.loc[target_idx, "stock_splits"] *= row.value

    split_multiplier = work["stock_splits"].replace(0.0, 1.0)
    future_factor = split_multiplier.iloc[::-1].cumprod().iloc[::-1].shift(-1, fill_value=1.0)
    work["split_factor"] = future_factor.astype(float).apply(truncate_to_2dp)
    work["share_factor"] = (work["split_factor"] * split_multiplier).astype(float).apply(truncate_to_2dp)

    for price_col in ["open", "high", "low", "close"]:
        work[price_col] = (
            pd.to_numeric(work[price_col], errors="coerce") / work["split_factor"]
        ).apply(truncate_to_2dp)
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce") * work["split_factor"]
    work = work.dropna(subset=["close"]).copy()
    work = work[work["close"] > 0].copy()

    close_series = pd.to_numeric(work["close"], errors="coerce")
    for window in MA_WINDOWS:
        work[f"ma_{window}"] = close_series.rolling(window=window, min_periods=window).mean().apply(truncate_to_2dp)

    work["shares_outstanding"] = None
    work["market_cap_cr"] = None
    work["volume"] = work["volume"].round()
    return work[
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "split_factor",
            "share_factor",
            "shares_outstanding",
            "market_cap_cr",
            "ma_5",
            "ma_10",
            "ma_20",
            "ma_50",
            "ma_100",
            "ma_200",
        ]
    ]


def attach_market_cap(adjusted_df, share_df, instrument_type="STOCK"):
    if adjusted_df.empty:
        return adjusted_df
    work = adjusted_df.copy()
    work["date"] = pd.to_datetime(work["date"])
    if instrument_type != "STOCK":
        work["shares_outstanding"] = None
        work["market_cap_cr"] = None
        work["date"] = work["date"].dt.strftime("%Y-%m-%d")
        return work.where(pd.notnull(work), None)
    if share_df.empty:
        work["date"] = work["date"].dt.strftime("%Y-%m-%d")
        return work.where(pd.notnull(work), None)

    shares = share_df.copy()
    shares["date"] = pd.to_datetime(shares["date"])
    shares["shares_outstanding"] = pd.to_numeric(shares["shares_outstanding"], errors="coerce")
    shares = shares.dropna(subset=["shares_outstanding"]).sort_values("date")
    if shares.empty:
        work["date"] = work["date"].dt.strftime("%Y-%m-%d")
        return work.where(pd.notnull(work), None)

    work = work.sort_values("date")
    cols_to_drop = [col for col in ["shares_outstanding", "market_cap_cr"] if col in work.columns]
    work = work.drop(columns=cols_to_drop)
    merged = pd.merge_asof(
        work,
        shares[["date", "shares_outstanding"]],
        on="date",
        direction="backward",
    )
    merged["shares_outstanding"] = merged["shares_outstanding"].fillna(shares["shares_outstanding"].iloc[0])
    merged["shares_outstanding"] = (
        pd.to_numeric(merged["shares_outstanding"], errors="coerce") *
        pd.to_numeric(merged["share_factor"], errors="coerce")
    ).apply(truncate_to_2dp)
    merged["market_cap_cr"] = (
        pd.to_numeric(merged["close"], errors="coerce") *
        pd.to_numeric(merged["shares_outstanding"], errors="coerce") / 1e7
    ).apply(truncate_to_2dp)
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
    return merged.where(pd.notnull(merged), None)


def preserve_existing_market_cap(adjusted_df, existing_df):
    if adjusted_df.empty or existing_df.empty:
        return adjusted_df
    work = adjusted_df.copy()
    existing = existing_df.copy()
    existing["date"] = existing["date"].astype(str)
    existing["market_cap_cr"] = pd.to_numeric(existing["market_cap_cr"], errors="coerce")
    existing = existing.dropna(subset=["market_cap_cr"])
    if existing.empty:
        return work
    work = work.merge(
        existing[["date", "market_cap_cr"]],
        on="date",
        how="left",
        suffixes=("", "_existing"),
    )
    has_existing = work["market_cap_cr_existing"].notna()
    work.loc[has_existing, "market_cap_cr"] = work.loc[has_existing, "market_cap_cr_existing"]
    close_series = pd.to_numeric(work["close"], errors="coerce")
    market_cap_series = pd.to_numeric(work["market_cap_cr"], errors="coerce")
    valid = has_existing & close_series.notna() & (close_series != 0)
    work.loc[valid, "shares_outstanding"] = (market_cap_series[valid] * 1e7 / close_series[valid]).apply(truncate_to_2dp)
    work.drop(columns=["market_cap_cr_existing"], inplace=True)
    return work


def load_raw_price_range(conn, symbol, start_date, end_date):
    return pd.read_sql(
        """
        SELECT symbol, date, open, high, low, close, volume
        FROM raw_eod_prices
        WHERE symbol = ?
          AND date >= ?
          AND date <= ?
        ORDER BY date
        """,
        conn,
        params=[symbol, start_date, end_date],
    )


def load_prior_adjusted_closes(conn, symbol, before_date, limit=199):
    prior = pd.read_sql(
        """
        SELECT date, close
        FROM adjusted_eod_prices
        WHERE symbol = ?
          AND date < ?
        ORDER BY date DESC
        LIMIT ?
        """,
        conn,
        params=[symbol, before_date, limit],
    )
    if prior.empty:
        return prior
    prior["date"] = pd.to_datetime(prior["date"])
    prior["close"] = pd.to_numeric(prior["close"], errors="coerce")
    return prior.sort_values("date")


def build_identity_adjusted(raw_df):
    if raw_df.empty:
        return raw_df
    work = raw_df.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values("date")
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce").astype(float)
    for price_col in ["open", "high", "low", "close"]:
        work[price_col] = pd.to_numeric(work[price_col], errors="coerce").apply(truncate_to_2dp)
    work["split_factor"] = 1.0
    work["share_factor"] = 1.0
    work["shares_outstanding"] = None
    work["market_cap_cr"] = None
    work["volume"] = work["volume"].round()
    return work[
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "split_factor",
            "share_factor",
            "shares_outstanding",
            "market_cap_cr",
        ]
    ]


def attach_incremental_indicators(adjusted_subset, prior_closes):
    if adjusted_subset.empty:
        return adjusted_subset
    work = adjusted_subset.copy()
    work["date"] = pd.to_datetime(work["date"])
    close_frame = work[["date", "close"]].copy()
    close_frame["close"] = pd.to_numeric(close_frame["close"], errors="coerce")
    if prior_closes is not None and not prior_closes.empty:
        history = pd.concat([prior_closes[["date", "close"]], close_frame], ignore_index=True)
    else:
        history = close_frame.copy()
    history = history.sort_values("date")
    close_series = pd.to_numeric(history["close"], errors="coerce")
    for window in MA_WINDOWS:
        history[f"ma_{window}"] = close_series.rolling(window=window, min_periods=window).mean().apply(truncate_to_2dp)
    ma_cols = ["date"] + [f"ma_{window}" for window in MA_WINDOWS]
    work = work.merge(history[ma_cols], on="date", how="left")
    return work


def build_incremental_no_action_subset(conn, symbol, changed_dates, instrument_type="STOCK"):
    target_dates = sorted({pd.to_datetime(d).strftime("%Y-%m-%d") for d in changed_dates})
    if not target_dates:
        return pd.DataFrame()
    raw_subset = load_raw_price_range(conn, symbol, target_dates[0], target_dates[-1])
    if raw_subset.empty:
        return pd.DataFrame()
    adjusted_subset = build_identity_adjusted(raw_subset)
    prior_closes = load_prior_adjusted_closes(conn, symbol, target_dates[0], limit=max(MA_WINDOWS) - 1)
    adjusted_subset = attach_incremental_indicators(adjusted_subset, prior_closes)
    shares = load_share_history(conn, symbol) if instrument_type == "STOCK" else pd.DataFrame()
    adjusted_subset = attach_market_cap(adjusted_subset, shares, instrument_type=instrument_type)
    adjusted_subset["date"] = adjusted_subset["date"].astype(str)
    return adjusted_subset[adjusted_subset["date"].isin(target_dates)].copy()


def rebuild_symbols(symbols, preserve_market_cap=False):
    if not symbols:
        log.warning("No symbols to adjust.")
        return
    with get_connection() as conn:
        instrument_types = get_symbol_instrument_types(conn, symbols)
    for idx, symbol in enumerate(symbols, start=1):
        with get_connection() as conn:
            with conn:
                raw = load_raw_prices(conn, symbol)
                actions = load_corporate_actions(conn, symbol)
                adjusted = build_split_adjusted(raw, actions=actions)
                instrument_type = instrument_types.get(symbol, "STOCK")
                shares = load_share_history(conn, symbol) if instrument_type == "STOCK" else pd.DataFrame()
                adjusted = attach_market_cap(adjusted, shares, instrument_type=instrument_type)
                if preserve_market_cap and instrument_type == "STOCK":
                    adjusted = preserve_existing_market_cap(adjusted, load_adjusted_market_caps(conn, symbol))
                replace_adjusted_prices(conn, symbol, adjusted)
                save_indicators(conn, adjusted)
                if instrument_type == "STOCK":
                    save_market_caps(conn, adjusted)
        log.info(f"[{idx}/{len(symbols)}] rebuilt adjusted history for {symbol}")


def refresh_latest_rows(symbol_date_map):
    if not symbol_date_map:
        return
    items = list(symbol_date_map.items())
    with get_connection() as conn:
        instrument_types = get_symbol_instrument_types(conn, [symbol for symbol, _ in items])
    for idx, (symbol, changed_dates) in enumerate(items, start=1):
        with get_connection() as conn:
            actions = load_corporate_actions(conn, symbol)
            instrument_type = instrument_types.get(symbol, "STOCK")
            target_dates = {pd.to_datetime(d).strftime("%Y-%m-%d") for d in changed_dates}
            if actions.empty:
                subset = build_incremental_no_action_subset(
                    conn,
                    symbol,
                    target_dates,
                    instrument_type=instrument_type,
                )
            else:
                raw = load_raw_prices(conn, symbol)
                adjusted = build_split_adjusted(raw, actions=actions)
                shares = load_share_history(conn, symbol) if instrument_type == "STOCK" else pd.DataFrame()
                adjusted = attach_market_cap(adjusted, shares, instrument_type=instrument_type)
                adjusted["date"] = adjusted["date"].astype(str)
                subset = adjusted[adjusted["date"].isin(target_dates)].copy()
            upsert_adjusted_prices(conn, subset)
            save_indicators(conn, subset)
            if instrument_type == "STOCK":
                save_market_caps(conn, subset)
        log.info(f"[{idx}/{len(items)}] updated {len(target_dates):,} date(s) for {symbol}")


def main():
    requested = [arg.strip().upper() for arg in sys.argv[1:] if not arg.startswith("-")]
    with get_connection() as conn:
        setup_schema(conn)
        symbols = requested or get_active_symbols(conn)["symbol"].tolist()
    rebuild_symbols(symbols)


if __name__ == "__main__":
    main()
