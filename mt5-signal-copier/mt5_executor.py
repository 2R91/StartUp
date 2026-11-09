"""Passage des ordres sur MetaTrader 5 via le package Python officiel `MetaTrader5`.

Le package ne fonctionne que sous Windows, avec le terminal MT5 installé et
« Algo Trading » activé. Ailleurs, le copieur tourne en mode « analyse seule ».
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from signal_parser import Signal

try:
    import MetaTrader5 as mt5
except ImportError:  # macOS / Linux : pas de package officiel
    mt5 = None

log = logging.getLogger("copier")

RETCODES_OK = {10008, 10009, 10010}  # PLACED, DONE, DONE_PARTIAL


class MT5Executor:
    def __init__(self, mt5_cfg: dict, trading_cfg: dict, symbol_map: dict[str, str]):
        self.mt5_cfg = mt5_cfg
        self.cfg = trading_cfg
        self.symbol_map = {k.upper(): v for k, v in symbol_map.items()}
        self.dry_run = bool(trading_cfg.get("dry_run", True))
        self.magic = int(trading_cfg.get("magic", 20261109))
        self.connected = False
        self.state_path = Path(trading_cfg.get("state_file", "trades.json"))
        self.trades: dict[str, list[int]] = self._load_state()

    # ------------------------------------------------------------------ connexion
    def connect(self) -> bool:
        if mt5 is None:
            log.warning("Package MetaTrader5 indisponible (Windows uniquement) : "
                        "mode ANALYSE SEULE, aucun ordre ne sera envoyé.")
            return False
        kwargs = {}
        if self.mt5_cfg.get("login"):
            kwargs = {"login": int(self.mt5_cfg["login"]),
                      "password": str(self.mt5_cfg.get("password", "")),
                      "server": str(self.mt5_cfg.get("server", ""))}
        path = self.mt5_cfg.get("path")
        ok = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
        if not ok:
            raise RuntimeError(f"Connexion à MT5 impossible : {mt5.last_error()}")
        acc = mt5.account_info()
        log.info("Connecté à MT5 : compte %s sur %s, solde %.2f %s",
                 acc.login, acc.server, acc.balance, acc.currency)
        if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL:
            log.warning("ATTENTION : compte RÉEL.")
        if not mt5.terminal_info().trade_allowed:
            log.warning("« Algo Trading » est désactivé dans MT5 : les ordres seront refusés.")
        log.info("Mode %s", "DRY-RUN (simulation, rien n'est envoyé)" if self.dry_run else "LIVE")
        self.connected = True
        return True

    def shutdown(self) -> None:
        if self.connected:
            mt5.shutdown()

    # ------------------------------------------------------------------ point d'entrée
    def handle(self, sig: Signal, tag: str, reply_tag: str | None = None) -> None:
        log.info("Signal [%s] : %s", tag, sig.summary())
        if not self.connected:
            return
        if sig.action == "OPEN":
            self._open(sig, tag)
        elif sig.action == "CLOSE":
            self._close(sig, reply_tag)
        elif sig.action == "BREAKEVEN":
            self._breakeven(sig, reply_tag)

    # ------------------------------------------------------------------ ouverture
    def _open(self, sig: Signal, tag: str) -> None:
        cfg = self.cfg
        allowed = {s.upper() for s in cfg.get("allowed_symbols", [])}
        if allowed and sig.symbol not in allowed:
            log.warning("Ignoré : %s n'est pas dans allowed_symbols", sig.symbol)
            return
        if cfg.get("require_sl", True) and sig.sl is None:
            log.warning("Ignoré : signal sans stop loss (require_sl = true)")
            return
        max_pos = int(cfg.get("max_open_positions", 0))
        if max_pos and len(self._positions()) >= max_pos:
            log.warning("Ignoré : %d positions déjà ouvertes (max_open_positions)", max_pos)
            return
        symbol = self._broker_symbol(sig.symbol)
        if not symbol:
            return

        info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
        buy = sig.side == "BUY"
        market = tick.ask if buy else tick.bid
        pending = sig.order_kind != "MARKET" and sig.entry is not None
        price = sig.entry if pending else market

        max_dist = float(cfg.get("max_entry_distance_points", 0))
        if not pending and sig.entry is not None and max_dist > 0:
            dist = abs(market - sig.entry) / info.point
            if dist > max_dist:
                log.warning("Ignoré : le prix (%s) est à %.0f points de l'entrée annoncée (%s)",
                            market, dist, sig.entry)
                return
        if not pending and sig.entry is not None and cfg.get("shift_levels_to_broker", False):
            # Décalage de cotation : on garde les mêmes distances SL/TP que le signal,
            # mais recalées sur le prix réel du broker.
            offset = market - sig.entry
            if offset:
                sig.sl = sig.sl + offset if sig.sl is not None else None
                sig.tps = [tp + offset for tp in sig.tps]
                log.info("Niveaux décalés de %+.*f pour coller au prix du broker", info.digits, offset)
        errors = sig.check_levels(price)
        if errors:
            log.warning("Ignoré : niveaux incohérents : %s", "; ".join(errors))
            return

        total = self._lot_size(sig, info, buy, price)
        if not total:
            return

        if pending:
            order_type = {("BUY", "LIMIT"): mt5.ORDER_TYPE_BUY_LIMIT,
                          ("BUY", "STOP"): mt5.ORDER_TYPE_BUY_STOP,
                          ("SELL", "LIMIT"): mt5.ORDER_TYPE_SELL_LIMIT,
                          ("SELL", "STOP"): mt5.ORDER_TYPE_SELL_STOP}[(sig.side, sig.order_kind)]
        else:
            order_type = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL

        tickets = []
        for volume, tp in self._legs(total, sig.tps, info):
            request = {
                "action": mt5.TRADE_ACTION_PENDING if pending else mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "price": round(price, info.digits),
                "sl": round(sig.sl, info.digits) if sig.sl else 0.0,
                "tp": round(tp, info.digits) if tp else 0.0,
                "deviation": int(cfg.get("deviation_points", 20)),
                "magic": self.magic,
                "comment": tag[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN if pending else self._filling(info),
            }
            ticket = self._send(request, f"{sig.side} {volume} {symbol} TP {tp or '-'}")
            if ticket:
                tickets.append(ticket)
        if tickets:
            self.trades[tag] = tickets
            self._save_state()

    def _lot_size(self, sig: Signal, info, buy: bool, price: float) -> float:
        cfg = self.cfg
        if cfg.get("lot_mode", "fixed") == "risk" and sig.sl is not None:
            order_type = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
            loss_per_lot = mt5.order_calc_profit(order_type, info.name, 1.0, price, sig.sl)
            if not loss_per_lot:
                log.error("Impossible de calculer le risque : %s", mt5.last_error())
                return 0.0
            risk_money = mt5.account_info().balance * float(cfg.get("risk_percent", 1.0)) / 100
            lots = risk_money / abs(loss_per_lot)
        else:
            lots = float(cfg.get("fixed_lot", 0.01))
        if cfg.get("max_lot"):
            lots = min(lots, float(cfg["max_lot"]))
        volume = self._round_volume(lots, info)
        if not volume:
            log.warning("Ignoré : lot calculé (%.4f) inférieur au minimum du broker (%s)",
                        lots, info.volume_min)
        return volume

    def _legs(self, total: float, tps: list[float], info) -> list[tuple[float, float]]:
        """Répartit le lot sur plusieurs positions (une par TP) si multi_tp = "split"."""
        if not tps:
            return [(total, 0.0)]
        mode = self.cfg.get("multi_tp", "split")
        if mode == "last":
            return [(total, tps[-1])]
        if mode == "split":
            weights = [float(w) for w in self.cfg.get("tp_split", [])] or [1.0] * len(tps)
            weights += [weights[-1]] * (len(tps) - len(weights))
            for n in range(len(tps), 1, -1):  # moins de TP si le lot est trop petit
                w = weights[:n]
                volumes = [self._round_volume(total * x / sum(w), info) for x in w]
                if all(volumes):
                    return list(zip(volumes, tps[:n]))
        return [(total, tps[0])]

    # ------------------------------------------------------------------ suivi automatique
    def manage(self) -> None:
        """À appeler régulièrement : SL au prix d'entrée dès que TP1 est touché."""
        if not self.connected:
            return
        open_positions = {p.ticket: p for p in self._positions()}
        open_orders = {o.ticket for o in mt5.orders_get() or () if o.magic == self.magic}
        changed = False
        for tag, tickets in list(self.trades.items()):
            alive = [t for t in tickets if t in open_positions or t in open_orders]
            if not alive:
                del self.trades[tag]  # trade entièrement terminé
                changed = True
                continue
            first = tickets[0]
            if (not self.cfg.get("breakeven_after_tp1", False) or len(tickets) < 2
                    or first in alive or not self._closed_at_tp(first)):
                continue
            for t in alive:
                if t in open_positions:
                    self._move_to_breakeven(open_positions[t], f"TP1 touché [{tag}]")
        if changed:
            self._save_state()

    def _closed_at_tp(self, ticket: int) -> bool:
        deals = mt5.history_deals_get(position=ticket) or ()
        return any(d.entry == mt5.DEAL_ENTRY_OUT and d.reason == mt5.DEAL_REASON_TP for d in deals)

    def _move_to_breakeven(self, p, why: str) -> None:
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        if p.sl and (p.sl >= p.price_open if is_buy else p.sl <= p.price_open):
            return  # déjà au BE ou mieux
        self._send({"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol,
                    "position": p.ticket, "sl": p.price_open, "tp": p.tp},
                   f"SL au prix d'entrée {p.symbol} #{p.ticket} ({why})")

    # ------------------------------------------------------------------ gestion
    def _close(self, sig: Signal, reply_tag: str | None) -> None:
        targets = self._targets(sig, reply_tag)
        if targets is None:
            return
        positions, orders = targets
        if not positions and not orders:
            log.info("Aucune position à clôturer")
        for p in positions:
            info, tick = mt5.symbol_info(p.symbol), mt5.symbol_info_tick(p.symbol)
            volume = p.volume
            if sig.fraction < 1:
                volume = self._round_volume(p.volume * sig.fraction, info) or p.volume
            is_buy = p.type == mt5.POSITION_TYPE_BUY
            self._send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": p.symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "position": p.ticket,
                "price": tick.bid if is_buy else tick.ask,
                "deviation": int(self.cfg.get("deviation_points", 20)),
                "magic": self.magic,
                "comment": "copier close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }, f"Clôture {volume} {p.symbol} #{p.ticket}")
        if sig.fraction >= 1:
            for o in orders:
                self._send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket},
                           f"Annulation ordre en attente #{o.ticket}")

    def _breakeven(self, sig: Signal, reply_tag: str | None) -> None:
        targets = self._targets(sig, reply_tag)
        if targets is None:
            return
        for p in targets[0]:
            self._move_to_breakeven(p, "demandé par le canal")

    def _targets(self, sig: Signal, reply_tag: str | None):
        """Positions/ordres visés : ceux du signal d'origine (réponse), d'un symbole, ou tous."""
        symbol = self._broker_symbol(sig.symbol) if sig.symbol else None
        if reply_tag:
            tickets = set(self.trades.get(reply_tag, []))
            match = lambda x: x.ticket in tickets or x.comment == reply_tag[:31]  # noqa: E731
        elif symbol or sig.close_all:
            match = lambda x: True  # noqa: E731
        else:
            log.warning("%s ignoré : ni symbole, ni réponse à un signal, ni « ALL »", sig.action)
            return None
        positions = [p for p in self._positions(symbol) if match(p)]
        orders = [o for o in (mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()) or ()
                  if o.magic == self.magic and match(o)]
        return positions, orders

    # ------------------------------------------------------------------ utilitaires
    def _positions(self, symbol: str | None = None):
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        return [p for p in positions or () if p.magic == self.magic]

    def _broker_symbol(self, canonical: str) -> str | None:
        symbol = self.symbol_map.get(canonical.upper(), canonical + self.cfg.get("symbol_suffix", ""))
        if not mt5.symbol_select(symbol, True):
            log.error("Symbole %s introuvable chez le broker (voir [symbol_map] / symbol_suffix)", symbol)
            return None
        return symbol

    @staticmethod
    def _round_volume(volume: float, info) -> float:
        step = info.volume_step
        decimals = max(0, -int(math.floor(math.log10(step))))
        rounded = round(math.floor(volume / step + 1e-9) * step, decimals)
        rounded = min(rounded, info.volume_max)
        return rounded if rounded >= info.volume_min else 0.0

    @staticmethod
    def _filling(info) -> int:
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict, what: str) -> int | None:
        if self.dry_run:
            if request["action"] not in (mt5.TRADE_ACTION_DEAL, mt5.TRADE_ACTION_PENDING):
                log.info("[DRY-RUN] %s (non envoyé)", what)
                return None
            check = mt5.order_check(request)
            verdict = "accepté" if check and check.retcode == 0 else \
                f"REFUSÉ ({check.comment if check else mt5.last_error()})"
            log.info("[DRY-RUN] %s -> %s par le broker", what, verdict)
            return None
        result = mt5.order_send(request)
        if result is None:
            log.error("%s : échec d'envoi %s", what, mt5.last_error())
            return None
        if result.retcode not in RETCODES_OK:
            log.error("%s : refusé (%s %s)", what, result.retcode, result.comment)
            return None
        log.info("%s : OK, ticket #%s", what, result.order)
        return result.order

    def _load_state(self) -> dict[str, list[int]]:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.trades, indent=1))
