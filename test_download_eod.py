import sys
import types
import unittest
from unittest import mock

import pandas as pd

sys.modules.setdefault("requests", types.SimpleNamespace())
sys.modules.setdefault("yfinance", types.SimpleNamespace())

import download_eod


class DownloadEODTests(unittest.TestCase):
    def test_parse_args(self):
        options = download_eod.parse_args(
            ["--bootstrap", "--daily-pipeline", "--symbols", "AAA,BBB", "--start", "2024-01-01", "--end", "2024-01-31", "--sleep", "0.1"]
        )
        self.assertTrue(options["bootstrap"])
        self.assertTrue(options["daily_pipeline"])
        self.assertEqual(options["symbols"], ["AAA", "BBB"])
        self.assertEqual(options["start"], "2024-01-01")
        self.assertEqual(options["end"], "2024-01-31")
        self.assertEqual(options["sleep_secs"], 0.1)

    def test_collect_touched_dates(self):
        history = pd.DataFrame([
            {"symbol": "AAA", "date": "2025-01-01"},
            {"symbol": "AAA", "date": "2025-01-02"},
            {"symbol": "BBB", "date": "2025-01-01"},
        ])
        touched_symbols, touched_dates = download_eod.collect_touched_dates(history)
        self.assertEqual(touched_symbols, ["AAA", "BBB"])
        self.assertEqual(touched_dates["AAA"], ["2025-01-01", "2025-01-02"])

    def test_filter_to_symbols_keeps_eq_be_and_indices(self):
        history = pd.DataFrame([
            {"symbol": "AAA", "date": "2025-01-01", "source": "nse-security-bhavcopy", "series": "EQ"},
            {"symbol": "BBB", "date": "2025-01-01", "source": "nse-security-bhavcopy", "series": "BE"},
            {"symbol": "CCC", "date": "2025-01-01", "source": "nse-security-bhavcopy", "series": "BZ"},
            {"symbol": "NIFTY 50", "date": "2025-01-01", "source": "nse-index-close"},
        ])
        filtered = download_eod._filter_to_symbols(history)
        self.assertEqual(filtered["symbol"].tolist(), ["AAA", "BBB", "NIFTY 50"])

    def test_filter_to_symbols_applies_optional_symbol_filter(self):
        history = pd.DataFrame([
            {"symbol": "AAA", "date": "2025-01-01", "source": "nse-security-bhavcopy", "series": "EQ"},
            {"symbol": "BBB", "date": "2025-01-01", "source": "nse-security-bhavcopy", "series": "BE"},
            {"symbol": "NIFTY 50", "date": "2025-01-01", "source": "nse-index-close"},
        ])
        filtered = download_eod._filter_to_symbols(history, selected_symbols=["BBB", "NIFTY 50"])
        self.assertEqual(filtered["symbol"].tolist(), ["BBB", "NIFTY 50"])

    def test_build_observed_symbol_records_creates_placeholder_records(self):
        history = pd.DataFrame([
            {"symbol": "AAA", "date": "2025-01-01", "source": "nse-security-bhavcopy", "series": "EQ"},
            {"symbol": "NIFTY 50", "date": "2025-01-01", "source": "nse-index-close"},
        ])
        records = download_eod.build_observed_symbol_records(history)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["instrument_type"], "STOCK")
        self.assertEqual(records[0]["series"], "EQ")
        self.assertEqual(records[0]["active"], 0)
        self.assertEqual(records[0]["status"], "observed")
        self.assertEqual(records[1]["instrument_type"], "INDEX")
        self.assertEqual(records[1]["series"], "INDEX")

    def test_bootstrap_main_runs_without_sync_symbols(self):
        symbols = pd.DataFrame([
            {"symbol": "AAA", "yahoo_symbol": "AAA.NS", "instrument_type": "STOCK"},
            {"symbol": "BBB", "yahoo_symbol": "BBB.NS", "instrument_type": "STOCK"},
        ])
        with mock.patch.object(download_eod, "load_target_symbols", return_value=(symbols, {}, ["AAA"])) as fake_load:
            with mock.patch.object(download_eod, "run_eod_download", return_value={"total_rows": 10, "failures": [], "touched_dates": {}}) as fake_download:
                with mock.patch.object(download_eod, "run_sync") as fake_sync:
                    with mock.patch.object(download_eod, "rebuild_symbols") as fake_rebuild:
                        with mock.patch.object(download_eod, "load_bootstrap_share_scope", return_value=symbols) as fake_share_scope:
                            fake_share_module = types.SimpleNamespace(run_share_download=mock.Mock())
                            fake_corp_module = types.SimpleNamespace(run_corporate_sync=mock.Mock())
                            fake_symbol_module = types.SimpleNamespace(run_symbol_change_sync=mock.Mock())
                            with mock.patch.dict(sys.modules, {
                                "sync_share_counts": fake_share_module,
                                "sync_corporate_actions": fake_corp_module,
                                    "symbol_change_handler": fake_symbol_module,
                                }):
                                with mock.patch.object(sys, "argv", ["download_eod.py", "--bootstrap", "--symbols", "AAA"]):
                                    download_eod.main()

        fake_sync.assert_not_called()
        fake_load.assert_called_once()
        fake_download.assert_called_once()
        fake_corp_module.run_corporate_sync.assert_called_once_with(rebuild=True, symbols=["AAA"])
        fake_symbol_module.run_symbol_change_sync.assert_called_once_with(apply_changes=True)
        fake_share_scope.assert_called_once_with(limit=None, only_symbols=["AAA"])
        fake_share_module.run_share_download.assert_called_once_with(symbols)
        fake_rebuild.assert_called_once_with(symbols["symbol"].tolist())

    def test_run_daily_refresh_pipeline_runs_sync_chain(self):
        known_symbols = pd.DataFrame([
            {"symbol": "AAA", "yahoo_symbol": "AAA.NS", "instrument_type": "STOCK"},
            {"symbol": "ETF1", "yahoo_symbol": "ETF1.NS", "instrument_type": "ETF"},
        ])
        summary = {"total_rows": 5, "failures": [], "touched_dates": {"AAA": ["2026-06-03"]}}
        fake_share_module = types.SimpleNamespace(run_share_download=mock.Mock())
        fake_corp_module = types.SimpleNamespace(run_corporate_sync=mock.Mock())
        with mock.patch.object(download_eod, "run_eod_download", return_value=summary) as fake_download:
            with mock.patch.object(download_eod, "refresh_latest_rows") as fake_refresh:
                with mock.patch.dict(sys.modules, {
                    "sync_share_counts": fake_share_module,
                    "sync_corporate_actions": fake_corp_module,
                }):
                    result = download_eod.run_daily_refresh_pipeline(
                        known_symbols,
                        {"AAA": "2026-06-02"},
                        selected_symbols=["AAA"],
                        end_date="2026-06-03",
                    )

        self.assertEqual(result, summary)
        fake_corp_module.run_corporate_sync.assert_called_once_with(rebuild=True, symbols=["AAA"])
        fake_download.assert_called_once()
        share_df = fake_share_module.run_share_download.call_args.args[0]
        self.assertEqual(share_df["symbol"].tolist(), ["AAA"])
        fake_refresh.assert_called_once_with({"AAA": ["2026-06-03"]})

    def test_daily_pipeline_main_uses_new_mode(self):
        symbols = pd.DataFrame([
            {"symbol": "AAA", "yahoo_symbol": "AAA.NS", "instrument_type": "STOCK"},
        ])
        with mock.patch.object(download_eod, "load_target_symbols", return_value=(symbols, {}, ["AAA"])) as fake_load:
            with mock.patch.object(download_eod, "run_daily_refresh_pipeline", return_value={"total_rows": 0, "failures": [], "touched_dates": {}}) as fake_pipeline:
                with mock.patch.object(download_eod, "run_sync") as fake_sync:
                    with mock.patch.object(sys, "argv", ["download_eod.py", "--daily-pipeline", "--symbols", "AAA"]):
                        download_eod.main()

        fake_sync.assert_called_once()
        fake_load.assert_called_once()
        fake_pipeline.assert_called_once()


if __name__ == "__main__":
    unittest.main()
