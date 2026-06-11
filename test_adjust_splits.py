import unittest

import pandas as pd
import sqlite3

import adjust_splits


class AdjustSplitsTests(unittest.TestCase):
    def test_attach_market_cap_skips_non_stock_instruments(self):
        adjusted = pd.DataFrame([
            {
                "symbol": "NIFTY 50",
                "date": "2024-01-01",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": None,
                "split_factor": 1.0,
                "share_factor": 1.0,
                "shares_outstanding": None,
                "market_cap_cr": None,
                "ma_5": None,
                "ma_10": None,
                "ma_20": None,
                "ma_50": None,
                "ma_100": None,
                "ma_200": None,
            }
        ])
        shares = pd.DataFrame([
            {"symbol": "NIFTY 50", "date": "2024-01-01", "shares_outstanding": 12345}
        ])

        result = adjust_splits.attach_market_cap(adjusted, shares, instrument_type="INDEX")

        self.assertIsNone(result.loc[0, "shares_outstanding"])
        self.assertIsNone(result.loc[0, "market_cap_cr"])

    def test_build_incremental_no_action_subset_uses_prior_closes_for_moving_averages(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE raw_eod_prices (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE adjusted_eod_prices (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, split_factor REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE share_history (
                symbol TEXT, date TEXT, shares_outstanding REAL
            )
            """
        )

        prior_rows = [
            ("ABC", "2024-01-01", 100.0),
            ("ABC", "2024-01-02", 101.0),
            ("ABC", "2024-01-03", 102.0),
            ("ABC", "2024-01-04", 103.0),
        ]
        conn.executemany(
            "INSERT INTO adjusted_eod_prices (symbol, date, open, high, low, close, volume, split_factor) VALUES (?, ?, 0, 0, 0, ?, 0, 1.0)",
            prior_rows,
        )
        raw_rows = [
            ("ABC", "2024-01-05", 104.0, 105.0, 103.0, 104.0, 1000),
            ("ABC", "2024-01-06", 105.0, 106.0, 104.0, 105.0, 1000),
        ]
        conn.executemany(
            "INSERT INTO raw_eod_prices (symbol, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            raw_rows,
        )
        conn.execute(
            "INSERT INTO share_history (symbol, date, shares_outstanding) VALUES ('ABC', '2024-01-01', 10000000)"
        )

        result = adjust_splits.build_incremental_no_action_subset(
            conn,
            "ABC",
            ["2024-01-05", "2024-01-06"],
            instrument_type="STOCK",
        ).sort_values("date").reset_index(drop=True)

        self.assertEqual(result.loc[0, "date"], "2024-01-05")
        self.assertAlmostEqual(result.loc[0, "ma_5"], 102.0)
        self.assertAlmostEqual(result.loc[1, "ma_5"], 103.0)
        self.assertEqual(result.loc[0, "split_factor"], 1.0)
        self.assertIsNotNone(result.loc[0, "market_cap_cr"])

    def test_build_incremental_no_action_subset_skips_market_cap_for_etf(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE raw_eod_prices (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE adjusted_eod_prices (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, split_factor REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE share_history (
                symbol TEXT, date TEXT, shares_outstanding REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO raw_eod_prices (symbol, date, open, high, low, close, volume) VALUES ('ETF1', '2024-01-05', 10, 11, 9, 10.5, 100)"
        )

        result = adjust_splits.build_incremental_no_action_subset(
            conn,
            "ETF1",
            ["2024-01-05"],
            instrument_type="ETF",
        )

        self.assertEqual(len(result), 1)
        self.assertIsNone(result.loc[0, "shares_outstanding"])
        self.assertIsNone(result.loc[0, "market_cap_cr"])

    def test_build_split_adjusted_and_market_cap_round_to_two_decimals(self):
        raw = pd.DataFrame([
            {
                "symbol": "ABC",
                "date": "2024-01-01",
                "open": 100.126,
                "high": 101.236,
                "low": 99.876,
                "close": 100.555,
                "volume": 1000,
            },
            {
                "symbol": "ABC",
                "date": "2024-01-02",
                "open": 102.126,
                "high": 103.236,
                "low": 101.876,
                "close": 102.555,
                "volume": 1000,
            },
            {
                "symbol": "ABC",
                "date": "2024-01-03",
                "open": 104.126,
                "high": 105.236,
                "low": 103.876,
                "close": 104.555,
                "volume": 1000,
            },
            {
                "symbol": "ABC",
                "date": "2024-01-04",
                "open": 106.126,
                "high": 107.236,
                "low": 105.876,
                "close": 106.555,
                "volume": 1000,
            },
            {
                "symbol": "ABC",
                "date": "2024-01-05",
                "open": 108.126,
                "high": 109.236,
                "low": 107.876,
                "close": 108.555,
                "volume": 1000,
            },
        ])
        adjusted = adjust_splits.build_split_adjusted(raw)
        shares = pd.DataFrame([
            {"symbol": "ABC", "date": "2024-01-01", "shares_outstanding": 12345678.987}
        ])
        result = adjust_splits.attach_market_cap(adjusted, shares, instrument_type="STOCK").reset_index(drop=True)

        self.assertEqual(result.loc[0, "open"], 100.13)
        self.assertEqual(result.loc[0, "close"], 100.56)
        self.assertEqual(result.loc[4, "ma_5"], 104.56)
        self.assertEqual(result.loc[0, "shares_outstanding"], 12345678.99)
        self.assertEqual(result.loc[0, "market_cap_cr"], 124.15)


if __name__ == "__main__":
    unittest.main()
