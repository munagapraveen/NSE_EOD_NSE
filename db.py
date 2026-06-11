"""SQLite database layer for the standalone NSE EOD project.

Design note:
    `share_history` is a source/staging table for shares outstanding fetched
    from Yahoo. Once a trading date is materialized into the analytics layer,
    the authoritative historical market-cap series is:

        marketcap.market_cap_cr

    For brand-new dates, market cap is derived from `share_history`.
    For historical corporate-action rebuilds, previously stored
    `market_cap_cr` is preserved by date and `shares_outstanding` inside
    `marketcap` is adjusted to remain consistent with the refreshed adjusted
    close.
"""

from contextlib import contextmanager
import sqlite3

import pandas as pd

from config import DB_FILE
from logger import get_logger

log = get_logger(__name__)

MA_WINDOWS = [5, 10, 20, 50, 100, 200]


@contextmanager
def get_connection(db_file=DB_FILE):
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def setup_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbols (
            symbol            TEXT PRIMARY KEY,
            yahoo_symbol      TEXT,
            company_name      TEXT,
            isin              TEXT,
            series            TEXT,
            instrument_type   TEXT NOT NULL DEFAULT 'STOCK',
            active            INTEGER NOT NULL DEFAULT 1,
            status            TEXT NOT NULL DEFAULT 'active',
            last_seen_date    TEXT,
            source            TEXT,
            last_synced_at    TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_isin ON symbols(isin)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_active ON symbols(active)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_aliases (
            old_symbol        TEXT,
            new_symbol        TEXT,
            effective_date    TEXT,
            source            TEXT,
            note              TEXT,
            detected_at       TEXT DEFAULT CURRENT_DATE,
            PRIMARY KEY (old_symbol, new_symbol)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_eod_prices (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            open              REAL,
            high              REAL,
            low               REAL,
            close             REAL,
            volume            INTEGER,
            source            TEXT NOT NULL DEFAULT 'nse',
            source_file       TEXT,
            downloaded_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_eod_symbol_date ON raw_eod_prices(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_eod_date ON raw_eod_prices(date)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS adjusted_eod_prices (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            open              REAL,
            high              REAL,
            low               REAL,
            close             REAL,
            volume            INTEGER,
            split_factor      REAL NOT NULL,
            adjusted_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_adj_eod_symbol_date ON adjusted_eod_prices(symbol, date)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS marketcap (
            symbol             TEXT NOT NULL,
            date               TEXT NOT NULL,
            market_cap_cr      REAL,
            shares_outstanding REAL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_marketcap_symbol_date ON marketcap(symbol, date)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS share_history (
            symbol             TEXT NOT NULL,
            date               TEXT NOT NULL,
            shares_outstanding REAL,
            source             TEXT NOT NULL DEFAULT 'yahoo',
            fetched_at         TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_share_history_symbol_date ON share_history(symbol, date)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS corporate_actions (
            symbol            TEXT NOT NULL,
            ex_date           TEXT NOT NULL,
            action_type       TEXT NOT NULL,
            value             REAL,
            source            TEXT NOT NULL,
            note              TEXT,
            PRIMARY KEY (symbol, ex_date, action_type, source)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_symbol_date ON corporate_actions(symbol, ex_date)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS indicators (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            ma_5              REAL,
            ma_10             REAL,
            ma_20             REAL,
            ma_50             REAL,
            ma_100            REAL,
            ma_200            REAL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_indicators_symbol_date ON indicators(symbol, date)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS download_runs (
            run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name          TEXT NOT NULL,
            started_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at       TEXT,
            status            TEXT,
            details           TEXT
        )
        """
    )

    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(adjusted_eod_prices)")
    }
    for col in [
        "ma_5",
        "ma_10",
        "ma_20",
        "ma_50",
        "ma_100",
        "ma_200",
        "shares_outstanding",
        "market_cap_cr",
    ]:
        if col in existing_columns:
            try:
                conn.execute(f"ALTER TABLE adjusted_eod_prices DROP COLUMN {col}")
            except Exception as exc:
                log.warning(f"Could not drop column {col}: {exc}")

    # Enforce stock-only market-cap/share semantics.
    conn.execute(
        """
        DELETE FROM marketcap
        WHERE symbol IN (
            SELECT symbol
            FROM symbols
            WHERE instrument_type != 'STOCK'
        )
        """
    )
    conn.execute(
        """
        DELETE FROM share_history
        WHERE symbol IN (
            SELECT symbol
            FROM symbols
            WHERE instrument_type != 'STOCK'
        )
        """
    )

    conn.commit()


def upsert_symbols(conn, records):
    conn.executemany(
        """
        INSERT INTO symbols (
            symbol, yahoo_symbol, company_name, isin, series, instrument_type,
            active, status, last_seen_date, source, last_synced_at
        ) VALUES (
            :symbol, :yahoo_symbol, :company_name, :isin, :series, :instrument_type,
            :active, :status, :last_seen_date, :source, :last_synced_at
        )
        ON CONFLICT(symbol) DO UPDATE SET
            yahoo_symbol=excluded.yahoo_symbol,
            company_name=excluded.company_name,
            isin=excluded.isin,
            series=excluded.series,
            instrument_type=excluded.instrument_type,
            active=excluded.active,
            status=excluded.status,
            last_seen_date=excluded.last_seen_date,
            source=excluded.source,
            last_synced_at=excluded.last_synced_at
        """,
        records,
    )
    conn.commit()


def upsert_observed_symbols(conn, records):
    if not records:
        return
    conn.executemany(
        """
        INSERT INTO symbols (
            symbol, yahoo_symbol, company_name, isin, series, instrument_type,
            active, status, last_seen_date, source, last_synced_at
        ) VALUES (
            :symbol, :yahoo_symbol, :company_name, :isin, :series, :instrument_type,
            :active, :status, :last_seen_date, :source, :last_synced_at
        )
        ON CONFLICT(symbol) DO UPDATE SET
            series=COALESCE(NULLIF(symbols.series, ''), excluded.series),
            instrument_type=CASE
                WHEN symbols.instrument_type IS NULL OR symbols.instrument_type = ''
                THEN excluded.instrument_type
                ELSE symbols.instrument_type
            END,
            active=CASE
                WHEN symbols.active = 1 THEN symbols.active
                ELSE excluded.active
            END,
            last_seen_date=CASE
                WHEN symbols.last_seen_date IS NULL OR symbols.last_seen_date < excluded.last_seen_date
                THEN excluded.last_seen_date
                ELSE symbols.last_seen_date
            END,
            source=CASE
                WHEN symbols.source IS NULL OR symbols.source = ''
                THEN excluded.source
                ELSE symbols.source
            END,
            last_synced_at=CASE
                WHEN symbols.last_synced_at IS NULL OR symbols.last_synced_at < excluded.last_synced_at
                THEN excluded.last_synced_at
                ELSE symbols.last_synced_at
            END
        """,
        records,
    )
    conn.commit()


def mark_missing_symbols_inactive(conn, active_symbols):
    if not active_symbols:
        conn.execute("UPDATE symbols SET active = 0, status = 'inactive'")
        conn.commit()
        return

    conn.execute("CREATE TEMP TABLE temp_active_symbols (symbol TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO temp_active_symbols (symbol) VALUES (?)",
        [(symbol,) for symbol in active_symbols],
    )
    conn.execute(
        """
        UPDATE symbols
        SET active = 0,
            status = CASE
                WHEN status = 'renamed' THEN status
                ELSE 'inactive'
            END
        WHERE symbol NOT IN (SELECT symbol FROM temp_active_symbols)
        """
    )
    conn.execute("DROP TABLE temp_active_symbols")
    conn.commit()


def get_active_symbols(conn):
    return pd.read_sql(
        """
        SELECT symbol, yahoo_symbol, company_name, isin, series, instrument_type
        FROM symbols
        WHERE active = 1
        ORDER BY symbol
        """,
        conn,
    )


def get_price_backfill_symbols(conn, instrument_type=None, only_symbols=None, exclude_statuses=None):
    query = """
        SELECT DISTINCT s.symbol, s.yahoo_symbol, s.company_name, s.isin, s.series, s.instrument_type, s.status, s.active
        FROM symbols s
        INNER JOIN raw_eod_prices r
            ON r.symbol = s.symbol
    """
    clauses = []
    params = []
    if instrument_type:
        clauses.append("s.instrument_type = ?")
        params.append(instrument_type)
    if only_symbols:
        placeholders = ",".join("?" for _ in only_symbols)
        clauses.append(f"UPPER(s.symbol) IN ({placeholders})")
        params.extend([symbol.strip().upper() for symbol in only_symbols if symbol.strip()])
    if exclude_statuses:
        placeholders = ",".join("?" for _ in exclude_statuses)
        clauses.append(f"COALESCE(s.status, '') NOT IN ({placeholders})")
        params.extend(exclude_statuses)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY s.symbol"
    return pd.read_sql(query, conn, params=params)


def get_symbol_last_dates(conn, symbols=None):
    query = """
        SELECT symbol, MAX(date) AS last_date
        FROM raw_eod_prices
    """
    params = []
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        query += f" WHERE symbol IN ({placeholders})"
        params.extend(symbols)
    query += " GROUP BY symbol"
    rows = conn.execute(query, params).fetchall()
    return {symbol: last_date for symbol, last_date in rows}


def get_symbol_instrument_types(conn, symbols=None):
    query = "SELECT symbol, instrument_type FROM symbols"
    params = []
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        query += f" WHERE symbol IN ({placeholders})"
        params.extend(symbols)
    rows = conn.execute(query, params).fetchall()
    return {symbol: instrument_type for symbol, instrument_type in rows}


def insert_raw_prices(conn, df):
    if df.empty:
        return

    cols = [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
        "source_file",
    ]
    clean_df = df[cols].where(pd.notnull(df[cols]), None)
    conn.executemany(
        """
        INSERT INTO raw_eod_prices (
            symbol, date, open, high, low, close, volume, source, source_file
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            source=excluded.source,
            source_file=excluded.source_file,
            downloaded_at=CURRENT_TIMESTAMP
        """,
        clean_df.itertuples(index=False, name=None),
    )
    conn.commit()


def upsert_corporate_actions(conn, records):
    conn.executemany(
        """
        INSERT INTO corporate_actions (
            symbol, ex_date, action_type, value, source, note
        ) VALUES (
            :symbol, :ex_date, :action_type, :value, :source, :note
        )
        ON CONFLICT(symbol, ex_date, action_type, source) DO UPDATE SET
            value=excluded.value,
            note=excluded.note
        """,
        records,
    )
    conn.commit()


def upsert_adjusted_prices(conn, df):
    if df.empty:
        return
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "split_factor"]
    data = [
        tuple(None if pd.isna(value) else value for value in row)
        for row in df[cols].itertuples(index=False, name=None)
    ]
    conn.executemany(
        """
        INSERT INTO adjusted_eod_prices (
            symbol, date, open, high, low, close, volume, split_factor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            split_factor=excluded.split_factor,
            adjusted_at=CURRENT_TIMESTAMP
        """,
        data,
    )
    conn.commit()


def replace_adjusted_prices(conn, symbol, df):
    conn.execute("DELETE FROM adjusted_eod_prices WHERE symbol = ?", (symbol,))
    upsert_adjusted_prices(conn, df)
    conn.commit()


def save_market_caps(conn, df):
    if df.empty:
        return
    cols = ["symbol", "date", "market_cap_cr", "shares_outstanding"]
    if not all(col in df.columns for col in cols):
        return
    data = [
        tuple(None if pd.isna(value) else value for value in row)
        for row in df[cols].itertuples(index=False, name=None)
    ]
    conn.executemany(
        """
        INSERT INTO marketcap (symbol, date, market_cap_cr, shares_outstanding)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            market_cap_cr=excluded.market_cap_cr,
            shares_outstanding=excluded.shares_outstanding
        """,
        data,
    )
    conn.commit()


def save_indicators(conn, df):
    if df.empty:
        return
    indicator_cols = [col for col in df.columns if col.startswith("ma_")]
    cols = ["symbol", "date"] + indicator_cols
    data = [
        tuple(None if pd.isna(value) else value for value in row)
        for row in df[cols].itertuples(index=False, name=None)
    ]
    placeholders = ",".join(["?"] * len(cols))
    col_names = ",".join(cols)
    update_clause = ",".join(f"{col}=excluded.{col}" for col in indicator_cols)
    conn.executemany(
        f"""
        INSERT INTO indicators ({col_names})
        VALUES ({placeholders})
        ON CONFLICT(symbol, date) DO UPDATE SET
            {update_clause}
        """,
        data,
    )
    conn.commit()


def upsert_share_history(conn, records):
    conn.executemany(
        """
        INSERT INTO share_history (symbol, date, shares_outstanding, source)
        VALUES (:symbol, :date, :shares_outstanding, :source)
        ON CONFLICT(symbol, date) DO UPDATE SET
            shares_outstanding=excluded.shares_outstanding,
            source=excluded.source,
            fetched_at=CURRENT_TIMESTAMP
        """,
        records,
    )
    conn.commit()


def load_raw_prices(conn, symbol):
    return pd.read_sql(
        """
        SELECT symbol, date, open, high, low, close, volume
        FROM raw_eod_prices
        WHERE symbol = ?
        ORDER BY date
        """,
        conn,
        params=[symbol],
    )


def load_share_history(conn, symbol):
    return pd.read_sql(
        """
        SELECT symbol, date, shares_outstanding
        FROM share_history
        WHERE symbol = ?
        ORDER BY date
        """,
        conn,
        params=[symbol],
    )


def load_adjusted_market_caps(conn, symbol):
    return pd.read_sql(
        """
        SELECT symbol, date, market_cap_cr, shares_outstanding
        FROM marketcap
        WHERE symbol = ?
        ORDER BY date
        """,
        conn,
        params=[symbol],
    )


def load_corporate_actions(conn, symbol):
    return pd.read_sql(
        """
        SELECT ex_date AS date, action_type, value
        FROM corporate_actions
        WHERE symbol = ?
          AND action_type IN ('split', 'bonus')
        ORDER BY ex_date
        """,
        conn,
        params=[symbol],
    )


def load_active_symbol_map(conn):
    return pd.read_sql(
        """
        SELECT symbol, isin, company_name, yahoo_symbol, status, active, instrument_type
        FROM symbols
        ORDER BY symbol
        """,
        conn,
    )


def upsert_symbol_aliases(conn, records):
    conn.executemany(
        """
        INSERT INTO symbol_aliases (
            old_symbol, new_symbol, effective_date, source, note
        ) VALUES (
            :old_symbol, :new_symbol, :effective_date, :source, :note
        )
        ON CONFLICT(old_symbol, new_symbol) DO UPDATE SET
            effective_date=excluded.effective_date,
            source=excluded.source,
            note=excluded.note
        """,
        records,
    )
    conn.commit()


def apply_symbol_rename(conn, old_symbol, new_symbol, effective_date=None, source="nse", note=""):
    if not old_symbol or not new_symbol:
        return
    if str(old_symbol).strip().upper() == str(new_symbol).strip().upper():
        log.warning(f"Skipping self-mapping symbol rename for {old_symbol}")
        return

    cutoff_date = effective_date or None

    def should_overwrite_existing(row_date):
        return cutoff_date is not None and str(row_date) < str(cutoff_date)

    for table in [
        "raw_eod_prices",
        "adjusted_eod_prices",
        "share_history",
        "marketcap",
        "indicators",
    ]:
        existing_dates = {
            row[0] for row in conn.execute(
                f"SELECT date FROM {table} WHERE symbol = ?",
                (new_symbol,),
            )
        }
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE symbol = ? ORDER BY date",
            (old_symbol,),
        ).fetchall()
        if rows:
            cols = [col[1] for col in conn.execute(f"PRAGMA table_info({table})")]
            symbol_idx = cols.index("symbol")
            date_idx = cols.index("date")
            placeholders = ",".join("?" for _ in cols)
            for row in rows:
                mutable = list(row)
                mutable[symbol_idx] = new_symbol
                if row[date_idx] in existing_dates:
                    if should_overwrite_existing(row[date_idx]):
                        assignments = ",".join(
                            f"{col}=excluded.{col}"
                            for col in cols
                            if col not in {"symbol", "date"}
                        )
                        conn.execute(
                            f"""
                            INSERT INTO {table} VALUES ({placeholders})
                            ON CONFLICT(symbol, date) DO UPDATE SET
                                {assignments}
                            """,
                            mutable,
                        )
                    continue
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})",
                    mutable,
                )
            conn.execute(f"DELETE FROM {table} WHERE symbol = ?", (old_symbol,))

    action_rows = conn.execute(
        """
        SELECT ex_date, action_type, value, source, note
        FROM corporate_actions
        WHERE symbol = ?
        """,
        (old_symbol,),
    ).fetchall()
    for ex_date, action_type, value, src, row_note in action_rows:
        if should_overwrite_existing(ex_date):
            conn.execute(
                """
                INSERT INTO corporate_actions (
                    symbol, ex_date, action_type, value, source, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, ex_date, action_type, source) DO UPDATE SET
                    value=excluded.value,
                    note=excluded.note
                """,
                (new_symbol, ex_date, action_type, value, src, row_note),
            )
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO corporate_actions (
                    symbol, ex_date, action_type, value, source, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_symbol, ex_date, action_type, value, src, row_note),
            )
    conn.execute("DELETE FROM corporate_actions WHERE symbol = ?", (old_symbol,))
    conn.execute(
        """
        UPDATE symbols
        SET active = 0, status = 'renamed'
        WHERE symbol = ?
        """,
        (old_symbol,),
    )
    upsert_symbol_aliases(
        conn,
        [{
            "old_symbol": old_symbol,
            "new_symbol": new_symbol,
            "effective_date": effective_date,
            "source": source,
            "note": note,
        }],
    )
    conn.commit()
