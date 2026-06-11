import sqlite3
import sys
import types
import unittest
from unittest import mock

import pandas as pd

sys.modules.setdefault("requests", types.SimpleNamespace())

import db
import sync_corporate_actions


class SyncCorporateActionsTests(unittest.TestCase):
    def test_run_corporate_sync_rebuilds_only_changed_symbols(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            db.upsert_corporate_actions(conn, [{
                "symbol": "AAA",
                "ex_date": "2025-01-01",
                "action_type": "split",
                "value": 2.0,
                "source": "nse",
                "note": "old note",
            }])
            fetched = pd.DataFrame([
                {"symbol": "AAA", "ex_date": "2025-01-01", "action_type": "split", "value": 2.0, "source": "nse", "note": "updated note"},
                {"symbol": "BBB", "ex_date": "2025-02-01", "action_type": "bonus", "value": 1.5, "source": "nse", "note": "new action"},
            ])
            rebuilt = []

            def fake_rebuild(symbols, preserve_market_cap=False):
                rebuilt.append((symbols, preserve_market_cap))

            with mock.patch.object(sync_corporate_actions, "fetch_nse_corporate_actions", return_value=fetched):
                with mock.patch.object(sync_corporate_actions, "get_connection") as fake_get_connection:
                    fake_get_connection.return_value.__enter__.return_value = conn
                    summary = sync_corporate_actions.run_corporate_sync(rebuild=True, rebuild_func=fake_rebuild)

            self.assertEqual(summary["synced"], 2)
            self.assertEqual(summary["rebuilt_symbols"], ["AAA", "BBB"])
            self.assertEqual(rebuilt, [(["AAA", "BBB"], True)])
        finally:
            conn.close()

    def test_run_corporate_sync_respects_symbol_filter(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            fetched = pd.DataFrame([
                {"symbol": "AAA", "ex_date": "2025-01-01", "action_type": "split", "value": 2.0, "source": "nse", "note": "AAA action"},
                {"symbol": "BBB", "ex_date": "2025-02-01", "action_type": "bonus", "value": 1.5, "source": "nse", "note": "BBB action"},
            ])
            rebuilt = []

            def fake_rebuild(symbols, preserve_market_cap=False):
                rebuilt.append((symbols, preserve_market_cap))

            with mock.patch.object(sync_corporate_actions, "fetch_nse_corporate_actions", return_value=fetched):
                with mock.patch.object(sync_corporate_actions, "get_connection") as fake_get_connection:
                    fake_get_connection.return_value.__enter__.return_value = conn
                    summary = sync_corporate_actions.run_corporate_sync(
                        rebuild=True,
                        rebuild_func=fake_rebuild,
                        symbols=["BBB"],
                    )

            self.assertEqual(summary["synced"], 1)
            self.assertEqual(summary["rebuilt_symbols"], ["BBB"])
            self.assertEqual(rebuilt, [(["BBB"], True)])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
