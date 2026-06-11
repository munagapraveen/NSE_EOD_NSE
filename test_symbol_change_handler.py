import sys
import types
import unittest

sys.modules.setdefault("requests", types.SimpleNamespace())

import symbol_change_handler


class SymbolChangeHandlerTests(unittest.TestCase):
    def test_filter_valid_change_records_skips_self_mappings_and_duplicates(self):
        records = [
            {"old_symbol": "AAA", "new_symbol": "AAA", "effective_date": "2025-01-01", "source": "nse"},
            {"old_symbol": "AAA", "new_symbol": "BBB", "effective_date": "2025-01-01", "source": "nse"},
            {"old_symbol": "aaa", "new_symbol": "bbb", "effective_date": "2025-01-01", "source": "nse"},
            {"old_symbol": "", "new_symbol": "CCC", "effective_date": "2025-01-01", "source": "nse"},
        ]
        filtered = symbol_change_handler.filter_valid_change_records(records)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["old_symbol"], "AAA")
        self.assertEqual(filtered[0]["new_symbol"], "BBB")


if __name__ == "__main__":
    unittest.main()
