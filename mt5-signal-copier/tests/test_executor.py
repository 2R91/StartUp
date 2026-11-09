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
    DEAL_ENTRY_OUT, DEAL_REASON_TP = 1, 5

    def __init__(self):
        self.sent, self.positions, self.deals, self.next_ticket = [], [], {}, 1000
        self.info = NS(name="XAUUSD", point=0.01, digits=2, volume_step=0.01,
                       volume_min=0.01, volume_max=100.0, filling_mode=2)
        self.ticks = {"XAUUSD": NS(bid=2649.8, ask=2650.0), "NAS100": NS(bid=26860.0, ask=26861.0)}

    def symbol_select(self, s, _): return s in self.ticks
    def symbol_info(self, s): return NS(**{**vars(self.info), "name": s})
    def symbol_info_tick(self, s): return self.ticks[s]
    def history_deals_get(self, position): return self.deals.get(position, [])
    def account_info(self): return NS(balance=10_000.0)
    # 1 lot de XAUUSD = 100 onces (1 $ de mouvement = 100 $) ; 1 lot de NAS100 = 1 $ par point
    def order_calc_profit(self, t, s, vol, a, b):
        return (b - a) * (1 if s == "NAS100" else 100) * vol * (1 if t == 0 else -1)
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


    def test_real_nasdaq_signal_half_tp1_and_quote_shift(self):
        self.ex.cfg.update(tp_split=[0.5, 0.25, 0.25], shift_levels_to_broker=True)
        text = ("🔴 SELL NASDAQ \n\n📉 PE  : 26850 \n\n🛑 SL : 26960 -110\n\n🎯 TP1 : 26750\n"
                "🎯 TP2 : 26530\n🎯 TP3 : 26230\n\nRisque maximum : 1 % du capital.\n"
                "☑️ À TP1 : Half + BE obligatoire.")
        self.ex.handle(parse_signal(text), "tg1_5")
        # broker à 26860 (+10 vs signal) : SL/TP décalés de +10.
        # Risque 1 % de 10 000 $ = 100 $ sur 110 points à 1 $/point -> 0.90 lot -> 50 % / 25 % / 25 %
        self.assertEqual([(r["volume"], r["tp"], r["sl"], r["type"]) for r in self.fake.sent],
                         [(0.45, 26760.0, 26970.0, 1), (0.22, 26540.0, 26970.0, 1), (0.22, 26240.0, 26970.0, 1)])

    def test_breakeven_after_tp1(self):
        self.ex.cfg["breakeven_after_tp1"] = True
        self.ex.trades["t"] = [11, 12, 13]
        leg = lambda t: NS(ticket=t, symbol="NAS100", volume=0.02, type=1, magic=self.ex.magic,  # noqa: E731
                           comment="t", price_open=26850.0, sl=26960.0, tp=26500.0)
        self.fake.positions = [leg(11), leg(12), leg(13)]
        self.ex.manage()
        self.assertEqual(self.fake.sent, [])  # TP1 pas encore touché
        self.fake.positions = [leg(12), leg(13)]
        self.fake.deals[11] = [NS(entry=1, reason=5)]
        self.ex.manage()
        self.assertEqual([(r["position"], r["sl"]) for r in self.fake.sent], [(12, 26850.0), (13, 26850.0)])

    def test_no_breakeven_when_first_leg_stopped_out(self):
        self.ex.cfg["breakeven_after_tp1"] = True
        self.ex.trades["t"] = [11, 12]
        self.fake.positions = [NS(ticket=12, symbol="NAS100", volume=0.02, type=1, magic=self.ex.magic,
                                  comment="t", price_open=26850.0, sl=26960.0, tp=26500.0)]
        self.fake.deals[11] = [NS(entry=1, reason=4)]  # clôturé au SL
        self.ex.manage()
        self.assertEqual(self.fake.sent, [])


if __name__ == "__main__":
    unittest.main()
