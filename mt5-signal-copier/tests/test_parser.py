import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signal_parser import parse_signal  # noqa: E402


class OpenSignals(unittest.TestCase):
    def test_one_line(self):
        s = parse_signal("BUY XAUUSD 2650 SL 2640 TP 2660 TP 2670")
        self.assertEqual((s.action, s.side, s.symbol, s.order_kind), ("OPEN", "BUY", "XAUUSD", "MARKET"))
        self.assertEqual((s.entry, s.sl, s.tps), (2650.0, 2640.0, [2660.0, 2670.0]))

    def test_multiline_with_emojis_and_alias(self):
        s = parse_signal("🟢 GOLD BUY NOW @ 2650.5\n🔴 SL: 2640\n✅ TP1: 2660\n✅ TP2: 2670\n✅ TP3: open")
        self.assertEqual((s.side, s.symbol, s.entry, s.sl), ("BUY", "XAUUSD", 2650.5, 2640.0))
        self.assertEqual(s.tps, [2660.0, 2670.0])

    def test_sell_limit_range_and_long_words(self):
        s = parse_signal("EUR/USD Sell Limit 1.0850-1.0860\nStop Loss 1.0890\nTake Profit 1.0800")
        self.assertEqual((s.side, s.order_kind, s.symbol), ("SELL", "LIMIT", "EURUSD"))
        self.assertAlmostEqual(s.entry, 1.0855)
        self.assertEqual((s.sl, s.tps), (1.0890, [1.0800]))

    def test_buy_stop_and_stop_as_sl(self):
        s = parse_signal("GBPJPY buy stop 191.20\nstop: 190.50\ntarget 192.40")
        self.assertEqual((s.order_kind, s.entry, s.sl, s.tps), ("STOP", 191.2, 190.5, [192.4]))

    def test_french(self):
        s = parse_signal("ACHAT US30 maintenant 42000\nSL 41900\nObjectif 1 : 42150")
        self.assertEqual((s.side, s.symbol, s.entry, s.sl, s.tps), ("BUY", "US30", 42000.0, 41900.0, [42150.0]))

    def test_market_without_price(self):
        s = parse_signal("SELL XAUUSD now\nSL 2660\nTP 2630")
        self.assertIsNone(s.entry)
        self.assertEqual((s.sl, s.tps), (2660.0, [2630.0]))

    def test_tp_index_without_colon(self):
        s = parse_signal("BUY EURUSD 1.0800 SL 1.0770 TP1 1.0830 TP2 1.0860")
        self.assertEqual(s.tps, [1.0830, 1.0860])

    def test_custom_symbol_and_alias(self):
        s = parse_signal("BUY BOOM1000 SL 9500 TP 9700", extra_symbols=["BOOM1000"])
        self.assertEqual(s.symbol, "BOOM1000")
        s = parse_signal("SELL CABLE SL 1.30 TP 1.28", aliases={"CABLE": "GBPUSD"})
        self.assertEqual(s.symbol, "GBPUSD")

    def test_level_validation(self):
        s = parse_signal("BUY XAUUSD SL 2660 TP 2640")
        self.assertTrue(s.check_levels(2650))
        s = parse_signal("BUY XAUUSD SL 2640 TP 2660")
        self.assertEqual(s.check_levels(2650), [])


class ManagementSignals(unittest.TestCase):
    def test_close_symbol(self):
        s = parse_signal("Close XAUUSD now ❌")
        self.assertEqual((s.action, s.symbol, s.close_all, s.fraction), ("CLOSE", "XAUUSD", False, 1.0))

    def test_close_all_and_half(self):
        self.assertTrue(parse_signal("CLOSE ALL TRADES").close_all)
        self.assertEqual(parse_signal("close half, secure profits").fraction, 0.5)
        self.assertEqual(parse_signal("Fermez 50% de la position").fraction, 0.5)

    def test_close_reply_without_symbol(self):
        s = parse_signal("close now")
        self.assertEqual((s.action, s.symbol), ("CLOSE", None))

    def test_breakeven(self):
        for text in ("Move SL to entry", "SL to BE 🔒", "breakeven now", "SL à BE"):
            self.assertEqual(parse_signal(text).action, "BREAKEVEN", text)


class Noise(unittest.TestCase):
    def test_ignored_messages(self):
        for text in ("TP1 hit +50 pips 🔥", "SL hit, next one", "Good morning traders!",
                     "Who bought gold today?", "", "BUY now"):
            self.assertIsNone(parse_signal(text), text)


if __name__ == "__main__":
    unittest.main()
