import sys
import types
import unittest

import pandas as pd

sys.modules.setdefault("requests", types.SimpleNamespace())

import sharpe_screener


class SharpeScreenerTests(unittest.TestCase):
    def test_compute_annual_roc_filter_uses_next_available_date(self):
        prices = pd.DataFrame([
            {"symbol": "AAA", "date": "2024-01-10", "close": 100, "volume": 1000},
            {"symbol": "AAA", "date": "2025-01-09", "close": 112, "volume": 1000},
            {"symbol": "BBB", "date": "2024-02-05", "close": 100, "volume": 1000},
            {"symbol": "BBB", "date": "2025-01-09", "close": 110, "volume": 1000},
        ])
        prices["date"] = pd.to_datetime(prices["date"])

        result = sharpe_screener.compute_annual_roc_filter(prices, "2025-01-09", 6.5)

        self.assertEqual(result["symbol"].tolist(), ["AAA"])
        self.assertEqual(result.loc[0, "ROC_annual"], 12.0)

    def test_compute_turnover_filter_uses_median_turnover(self):
        rows = []
        for i in range(120):
            rows.append({"symbol": "AAA", "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i), "close": 100, "volume": 200000})
            rows.append({"symbol": "BBB", "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i), "close": 10, "volume": 1000})
        prices = pd.DataFrame(rows)

        result = sharpe_screener.compute_turnover_filter(prices, ["AAA", "BBB"], 1.0)

        self.assertEqual(result["symbol"].tolist(), ["AAA"])
        self.assertEqual(result.loc[0, "median_turnover_cr"], 2.0)

    def test_compute_sharpe_metrics_returns_expected_columns(self):
        rows = []
        base = 100.0
        for day in range(190):
            rows.append({
                "symbol": "AAA",
                "date": pd.Timestamp("2024-07-01") + pd.Timedelta(days=day),
                "close": base + day * 0.5 + (day % 3) * 0.2,
                "volume": 1000,
            })
        prices = pd.DataFrame(rows)

        result = sharpe_screener.compute_sharpe_metrics(prices, ["AAA"], "2024-12-31", long_months=3, short_months=1)

        self.assertEqual(result["symbol"].tolist(), ["AAA"])
        self.assertIn("sharpe_6", result.columns)
        self.assertIn("sharpe_3", result.columns)
        self.assertIn("ROC_6", result.columns)
        self.assertIn("ROC_3", result.columns)
        self.assertIsNotNone(result.loc[0, "sharpe_6"])
        self.assertIsNotNone(result.loc[0, "sharpe_3"])

    def test_rank_results_uses_raw_sharpe_for_ordering(self):
        df = pd.DataFrame([
            {"symbol": "AAA", "sharpe_long_raw": 0.1234, "sharpe_short_raw": 0.2234, "sharpe_6": 0.12, "sharpe_3": 0.22},
            {"symbol": "BBB", "sharpe_long_raw": 0.1534, "sharpe_short_raw": 0.2334, "sharpe_6": 0.15, "sharpe_3": 0.23},
        ])

        ranked = sharpe_screener.rank_results(df)

        self.assertEqual(ranked.iloc[0]["symbol"], "BBB")
        self.assertEqual(ranked.iloc[0]["Avg_sharpe_6_3_Rank"], 2)


if __name__ == "__main__":
    unittest.main()
