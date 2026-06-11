import unittest
import sys
import types

import pandas as pd

sys.modules.setdefault("requests", types.SimpleNamespace())

import nse


class NseNormalizationTests(unittest.TestCase):
    def test_normalize_security_bhavcopy_rounds_prices_to_two_decimals(self):
        df = pd.DataFrame([
            {
                "SYMBOL": "AAA",
                "SERIES": "EQ",
                "DATE1": "01-Jan-2024",
                "OPEN_PRICE": 100.126,
                "HIGH_PRICE": 101.236,
                "LOW_PRICE": 99.876,
                "CLOSE_PRICE": 100.555,
                "TTL_TRD_QNTY": 12345,
                "SOURCE_FILE": "x",
            }
        ])
        out = nse.normalize_security_bhavcopy(df)
        self.assertEqual(out.loc[0, "open"], 100.13)
        self.assertEqual(out.loc[0, "high"], 101.24)
        self.assertEqual(out.loc[0, "low"], 99.88)
        self.assertEqual(out.loc[0, "close"], 100.56)

    def test_normalize_index_close_rounds_prices_to_two_decimals(self):
        df = pd.DataFrame([
            {
                "INDEX NAME": "NIFTY 50",
                "INDEX DATE": "01-Jan-2024",
                "OPEN INDEX VALUE": 22000.126,
                "HIGH INDEX VALUE": 22100.236,
                "LOW INDEX VALUE": 21900.876,
                "CLOSING INDEX VALUE": 22050.555,
                "SOURCE_FILE": "x",
            }
        ])
        out = nse.normalize_index_close(df)
        self.assertEqual(out.loc[0, "open"], 22000.13)
        self.assertEqual(out.loc[0, "high"], 22100.24)
        self.assertEqual(out.loc[0, "low"], 21900.88)
        self.assertEqual(out.loc[0, "close"], 22050.56)


if __name__ == "__main__":
    unittest.main()
