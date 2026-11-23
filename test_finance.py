import math
import unittest
from finance import SYMBOLS, indicators, rebalance, validate_settings
from kb import checked_body, parse_account, protect


def response(body):
    return {"dataHeader": {"resultCode": "200", "processFlag": "A"}, "dataBody": body}


class FinanceTests(unittest.TestCase):
    def test_drawdown_is_path_dependent(self):
        result = indicators([100, 120, 60, 110])
        self.assertEqual(result["mdd"], -50)
        self.assertAlmostEqual(result["drawdown"], -100 / 12)

    def test_rsi_wilder_reference_and_edges(self):
        prices = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00]
        self.assertAlmostEqual(indicators(prices[:15])["rsi"], 70.464135, places=5)
        self.assertAlmostEqual(indicators(prices)["rsi"], 66.249619, places=5)
        self.assertEqual(indicators(list(range(1, 21)))["rsi"], 100)
        self.assertEqual(indicators(list(range(21, 1, -1)))["rsi"], 0)
        self.assertEqual(indicators([5]*20)["rsi"], 50)
        self.assertIsNone(indicators([5]*10)["rsi"])

    def test_allocation_conserves_money_with_contribution(self):
        settings = {"quantities": {s: 1 for s in SYMBOLS}, "targets": {s: 25 for s in SYMBOLS}, "cash": 100, "contribution": 200}
        quotes = {s: {"price": p} for s, p in zip(SYMBOLS, [100,200,50,50])}
        result = rebalance(settings, quotes)
        self.assertTrue(result["ready"])
        self.assertEqual(result["total"], 500)
        self.assertAlmostEqual(sum(r["delta"] for r in result["rows"]), 300)
        self.assertEqual(result["rows"][1]["delta"], -25)

    def test_unset_or_invalid_targets_disable_calculation(self):
        quotes = {s: {"price": 100} for s in SYMBOLS}
        self.assertFalse(rebalance({}, quotes)["ready"])
        result = rebalance({"targets": {s: 30 for s in SYMBOLS}, "cash": 100}, quotes)
        self.assertTrue(all(r["delta"] is None for r in result["rows"]))
        self.assertFalse(rebalance({}, {})["ready"])
        for invalid in [-1, math.inf, math.nan, True]:
            with self.assertRaises(ValueError):
                validate_settings({"cash": invalid})

    def test_kb_decimal_holdings_and_cash(self):
        rows = [{"is_cd": "QQQ ", "crncy_clsf_nm": "USD ", "frgn_hld_q_p6": "000001.125000"},
                {"is_cd": "QQQ", "crncy_clsf_nm": "USD", "frgn_hld_q_p6": "000000.125000"},
                {"is_cd": "NVDA", "crncy_clsf_nm": "USD", "frgn_hld_q_p6": "1"}]
        b = response({"Record2": rows, "nxt_key": " "})
        c = response({"Record1": [{"crncy_cd": "USD", "fcrncy_tfnd": "0000123.45"}]})
        result = parse_account(b, c)
        self.assertEqual(result["quantities"]["QQQ"], 1.25)
        self.assertEqual(result["cash"], 123.45)
        self.assertEqual(result["excluded"], 1)
        b["dataBody"]["nxt_key"] = "more"
        with self.assertRaises(ValueError):
            parse_account(b, c)

    def test_dpapi_round_trip(self):
        original = b"test-only-fake-credential"
        encrypted = protect(original)
        self.assertNotEqual(encrypted, original)
        self.assertEqual(protect(encrypted, decrypt=True), original)

    def test_http_200_is_not_business_success(self):
        with self.assertRaises(ValueError):
            checked_body({"dataHeader": {"resultCode": "200", "processFlag": "E"}, "dataBody": {}})


if __name__ == "__main__":
    unittest.main()
