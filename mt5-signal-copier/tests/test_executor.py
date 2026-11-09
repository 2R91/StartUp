"""Tests de l'exécuteur avec un faux module MetaTrader5 (aucun terminal requis)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mt5_executor  # noqa: E402
from signal_parser import parse_signal  # noqa: E402


class FakeMT5:
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE = 1, 5, 6, 8
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT, ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 2, 3, 4, 5
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0

    def __init__(self):
        self.sent, self.positions, self.next_ticket = [], [], 1000
        self.info = NS(name="XAUUSD", point=0.01, digits=2, volume_step=0.01,
                       volume_min=0.01, volume_max=100.0, filling_mode=2)

    def symbol_select(self, s, _): return s == "XAUUSD"
    def symbol_info(self, s): return self.info
    def symbol_info_tick(self, s): return NS(bid=2649.8, ask=2650.0)
    def account_info(self): return NS(balance=10_000.0)
    # 1 lot de XAUUSD = 100 onces : 1 $ de mouvement = 100 $
    def order_calc_profit(self, t, s, vol, a, b): return (b - a) * 100 * vol * (1 if t == 0 else -1)
    def positions_get(self, symbol=None): return [p for p in self.positions if not symbol or p.symbol == symbol]
    def orders_get(self, symbol=None): return []
    def last_error(self): return (0, "ok")

    def order_send(self, req):
        self.sent.append(req)
        self.next_ticket += 1
        return NS(retcode=10009, order=self.next_ticket, comment="done")


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMT5()
        mt5_executor.mt5 = self.fake
        self.tmp = tempfile.TemporaryDirectory()
        cfg = {"dry_run": False, "lot_mode": "risk", "risk_percent": 1.0, "multi_tp": "split",
               "state_file": f"{self.tmp.name}/trades.json"}
        self.ex = mt5_executor.MT5Executor({}, cfg, {})
        self.ex.connected = True

    def tearDown(self):
        self.tmp.cleanup()

    def test_risk_sizing_and_split(self):
        # risque 1 % de 10 000 $ = 100 $ ; SL à 10 $ -> 1 000 $/lot -> 0.10 lot, réparti sur 2 TP
        self.ex.handle(parse_signal("BUY XAUUSD SL 2640 TP 2660 TP 2670"), "tg1_1")
        self.assertEqual([(r["volume"], r["tp"]) for r in self.fake.sent], [(0.05, 2660.0), (0.05, 2670.0)])
        self.assertEqual(self.fake.sent[0]["type_filling"], FakeMT5.ORDER_FILLING_IOC)
        self.assertEqual(self.ex.trades["tg1_1"], [1001, 1002])

    def test_rejects_inconsistent_or_missing_sl(self):
        self.ex.handle(parse_signal("BUY XAUUSD SL 2660 TP 2670"), "a")
        self.ex.handle(parse_signal("BUY XAUUSD TP 2670"), "b")
        self.ex.handle(parse_signal("BUY EURUSD SL 1.07 TP 1.09"), "c")  # inconnu chez le broker
        self.assertEqual(self.fake.sent, [])

    def test_close_by_reply_only_targets_that_signal(self):
        self.fake.positions = [
            NS(ticket=1001, symbol="XAUUSD", volume=0.05, type=0, magic=self.ex.magic, comment="tg1_1"),
            NS(ticket=2001, symbol="XAUUSD", volume=0.05, type=0, magic=self.ex.magic, comment="tg1_9"),
            NS(ticket=3001, symbol="XAUUSD", volume=1.00, type=0, magic=0, comment="manuel"),
        ]
        self.ex.trades["tg1_1"] = [1001]
        self.ex.handle(parse_signal("close half"), "tg1_2", reply_tag="tg1_1")
        self.assertEqual([(r["position"], r["volume"], r["type"]) for r in self.fake.sent], [(1001, 0.02, 1)])

    def test_close_symbol_never_touches_manual_trades(self):
        self.fake.positions = [
            NS(ticket=1, symbol="XAUUSD", volume=0.1, type=1, magic=self.ex.magic, comment="x"),
            NS(ticket=2, symbol="XAUUSD", volume=1.0, type=0, magic=0, comment="manuel"),
        ]
        self.ex.handle(parse_signal("CLOSE GOLD"), "t")
        self.assertEqual([(r["position"], r["type"], r["price"]) for r in self.fake.sent], [(1, 0, 2650.0)])

    def test_ambiguous_close_is_ignored(self):
        self.fake.positions = [NS(ticket=1, symbol="XAUUSD", volume=0.1, type=0, magic=self.ex.magic, comment="x")]
        self.ex.handle(parse_signal("close now"), "t")
        self.assertEqual(self.fake.sent, [])


if __name__ == "__main__":
    unittest.main()
