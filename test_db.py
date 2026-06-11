import sqlite3
import unittest

import pandas as pd

import db


class ApplySymbolRenameTests(unittest.TestCase):
    def test_apply_symbol_rename_moves_marketcap_and_indicators(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            price_row = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 103.0,
                    "volume": 12345.0,
                    "split_factor": 1.0,
                }
            ])
            indicator_row = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "ma_5": 101.0,
                    "ma_10": 100.5,
                    "ma_20": None,
                    "ma_50": None,
                    "ma_100": None,
                    "ma_200": None,
                }
            ])
            marketcap_row = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "market_cap_cr": 999.9,
                    "shares_outstanding": 9707766.99,
                }
            ])
            db.upsert_symbols(conn, [{
                "symbol": "OLDSYM",
                "yahoo_symbol": "OLDSYM.NS",
                "company_name": "Old Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }, {
                "symbol": "NEWSYM",
                "yahoo_symbol": "NEWSYM.NS",
                "company_name": "New Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }])
            db.upsert_adjusted_prices(conn, price_row)
            db.save_indicators(conn, indicator_row)
            db.save_market_caps(conn, marketcap_row)

            db.apply_symbol_rename(conn, "OLDSYM", "NEWSYM", source="test")

            new_indicator = conn.execute(
                "SELECT ma_5 FROM indicators WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()
            new_marketcap = conn.execute(
                "SELECT market_cap_cr FROM marketcap WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()
            self.assertEqual(new_indicator[0], 101.0)
            self.assertEqual(new_marketcap[0], 999.9)
        finally:
            conn.close()

    def test_apply_symbol_rename_uses_effective_date_for_overlap_resolution(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            db.upsert_symbols(conn, [{
                "symbol": "OLDSYM",
                "yahoo_symbol": "OLDSYM.NS",
                "company_name": "Old Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }, {
                "symbol": "NEWSYM",
                "yahoo_symbol": "NEWSYM.NS",
                "company_name": "New Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }])
            old_rows = pd.DataFrame([
                {"symbol": "OLDSYM", "date": "2025-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0, "split_factor": 1.0},
                {"symbol": "OLDSYM", "date": "2025-01-03", "open": 103.0, "high": 104.0, "low": 102.0, "close": 103.5, "volume": 1200.0, "split_factor": 1.0},
            ])
            new_rows = pd.DataFrame([
                {"symbol": "NEWSYM", "date": "2025-01-01", "open": 200.0, "high": 201.0, "low": 199.0, "close": 200.5, "volume": 2000.0, "split_factor": 1.0},
                {"symbol": "NEWSYM", "date": "2025-01-03", "open": 300.0, "high": 301.0, "low": 299.0, "close": 300.5, "volume": 3000.0, "split_factor": 1.0},
            ])
            db.upsert_adjusted_prices(conn, old_rows)
            db.upsert_adjusted_prices(conn, new_rows)
            db.apply_symbol_rename(conn, "OLDSYM", "NEWSYM", effective_date="2025-01-02", source="test")
            before_cutoff = conn.execute(
                "SELECT close FROM adjusted_eod_prices WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()[0]
            after_cutoff = conn.execute(
                "SELECT close FROM adjusted_eod_prices WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-03"),
            ).fetchone()[0]
            self.assertEqual(before_cutoff, 100.5)
            self.assertEqual(after_cutoff, 300.5)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
