"""Analyse des signaux de trading écrits en texte libre (Telegram, Discord...).

Formats reconnus (insensible à la casse, emojis ignorés) :

    BUY XAUUSD 2650 SL 2640 TP 2660 TP 2670
    🟢 GOLD BUY NOW @ 2650
       SL: 2640
       TP1: 2660
       TP2: 2670
    EURUSD SELL LIMIT 1.0850-1.0860 / Stop Loss 1.0890 / Take Profit 1.0800
    CLOSE XAUUSD          (ou "close" en réponse au message du signal)
    CLOSE ALL
    CLOSE HALF            (clôture partielle de 50 %)
    SL TO BE / BREAKEVEN  (stop ramené au prix d'entrée)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Surnoms courants -> symbole canonique. Le préfixe/suffixe du broker
# (ex. "XAUUSD.m") se règle dans la config, pas ici.
DEFAULT_ALIASES = {
    "GOLD": "XAUUSD", "XAU": "XAUUSD",
    "SILVER": "XAGUSD", "XAG": "XAGUSD",
    "BTC": "BTCUSD", "BITCOIN": "BTCUSD", "ETH": "ETHUSD",
    "NASDAQ": "NAS100", "NAS100": "NAS100", "US100": "NAS100", "USTEC": "NAS100",
    "DOW": "US30", "US30": "US30", "DJ30": "US30",
    "SP500": "US500", "SPX": "US500", "SPX500": "US500", "US500": "US500",
    "DAX": "GER40", "GER40": "GER40", "DE40": "GER40", "GER30": "GER40",
    "WTI": "USOIL", "USOIL": "USOIL", "OIL": "USOIL",
}

CURRENCIES = {
    "EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "XAU", "XAG",
    "BTC", "ETH", "SGD", "HKD", "NOK", "SEK", "MXN", "ZAR", "TRY", "PLN",
}

_NUM = r"(\d+(?:\.\d+)?)"
# Nombre « isolé » : pas collé à des lettres (évite le 30 de US30, le 1 de TP1).
_FREE_NUM = r"(?<![\w.])(\d+(?:\.\d+)?)(?!\w)"

_SIDE_RE = re.compile(r"\b(BUY|SELL)(?:_(LIMIT|STOP))?\b")
_SL_RE = re.compile(r"\bSL\s*[:=@\-]?\s*" + _NUM + r"(?!\w)")
# "TP 2660", "TP1: 2660", "TP2 2670", "TP - 2660" (le chiffre après TP est un index)
_TP_RE = re.compile(
    r"\bTP\s*(?:\d{1,2}\s*[:=@\-]\s*|\d{1,2}\s+(?=\d)|[:=@\-]\s*)?" + _NUM + r"(?!\w)(?!\s*[:=@])"
)
_RANGE_RE = re.compile(_FREE_NUM + r"\s*(?:-|/|TO|~)\s*" + _FREE_NUM)
_PAIR_RE = re.compile(r"\b([A-Z]{3})/?([A-Z]{3})(?:\.?[A-Z0-9]{0,4})?\b")
_CLOSE_RE = re.compile(r"\b(CLOSE|FERMER|FERMEZ|CLOTURER|CLÔTURER|CLOTUREZ|CLÔTUREZ|EXIT|SORTIR)\b")
_ALL_RE = re.compile(r"\b(ALL|TOUT|TOUTES|TOUS)\b")
_HALF_RE = re.compile(r"\b(HALF|PARTIAL|PARTIEL|PARTIELLE|MOITIE|MOITIÉ)\b|\b50\s*%")
_BE_RE = re.compile(
    r"BREAK\s*-?\s*EVEN"
    r"|\bSL\s*(?:TO|AT|ON|@|=|À|A|AU)?\s*(?:BE|ENTRY|ENTREE|ENTRÉE)\b"
    r"|\b(?:SET|MOVE\s+(?:SL\s+)?TO|PASSER\s+(?:EN|À|A|AU))\s+BE\b"
)


@dataclass
class Signal:
    action: str                       # "OPEN", "CLOSE" ou "BREAKEVEN"
    symbol: str | None = None         # symbole canonique (ex. "XAUUSD")
    side: str | None = None           # "BUY" ou "SELL"
    order_kind: str = "MARKET"        # "MARKET", "LIMIT" ou "STOP"
    entry: float | None = None        # prix d'entrée annoncé (milieu si fourchette)
    sl: float | None = None
    tps: list[float] = field(default_factory=list)
    close_all: bool = False
    fraction: float = 1.0             # part à clôturer (1.0 = tout)
    raw: str = ""

    def check_levels(self, price: float) -> list[str]:
        """Vérifie que SL et TP sont du bon côté du prix d'entrée."""
        errors = []
        if self.side == "BUY":
            if self.sl is not None and self.sl >= price:
                errors.append(f"SL {self.sl} au-dessus du prix {price} pour un BUY")
            errors += [f"TP {tp} sous le prix {price} pour un BUY" for tp in self.tps if tp <= price]
        elif self.side == "SELL":
            if self.sl is not None and self.sl <= price:
                errors.append(f"SL {self.sl} sous le prix {price} pour un SELL")
            errors += [f"TP {tp} au-dessus du prix {price} pour un SELL" for tp in self.tps if tp >= price]
        return errors

    def summary(self) -> str:
        if self.action == "OPEN":
            kind = "" if self.order_kind == "MARKET" else f" {self.order_kind}"
            entry = f" @ {self.entry}" if self.entry is not None else " au marché"
            tps = ", ".join(map(str, self.tps)) or "aucun"
            return f"OPEN {self.side}{kind} {self.symbol}{entry} | SL {self.sl} | TP {tps}"
        target = "TOUT" if self.close_all else (self.symbol or "signal d'origine")
        part = f" ({self.fraction:.0%})" if self.fraction < 1 else ""
        return f"{self.action} {target}{part}"


def _normalize(text: str) -> str:
    t = text.upper().replace("—", "-").replace("–", "-")
    t = re.sub(r"[^\w\s.:=@/\-%]", " ", t)  # retire emojis et ponctuation exotique
    for pattern, repl in (
        (r"\bS\s*/\s*L\b", "SL"),
        (r"\bT\s*/\s*P\b", "TP"),
        (r"\bSTOP\s*-?\s*LOSS\b", "SL"),
        (r"\bTAKE\s*-?\s*PROFITS?\b", "TP"),
        (r"\b(?:TARGETS?|OBJECTIFS?)\b", "TP"),
        (r"\b(?:LONG|ACHAT|ACHETER|ACHETEZ)\b", "BUY"),
        (r"\b(?:SHORT|VENTE|VENDRE|VENDEZ)\b", "SELL"),
        (r"\b(BUY|SELL)\s+(LIMIT|STOP)\b", r"\1_\2"),  # "BUY STOP" = type d'ordre
        (r"\bSTOP\b(?=\s*[:=@\-]?\s*\d)", "SL"),       # "STOP: 2640" = stop loss
    ):
        t = re.sub(pattern, repl, t)
    return t


def _find_symbol(t: str, aliases: dict[str, str], extra: set[str]) -> str | None:
    candidates: list[tuple[int, str]] = []
    for m in _PAIR_RE.finditer(t):
        if m.group(1) in CURRENCIES and m.group(2) in CURRENCIES and m.group(1) != m.group(2):
            candidates.append((m.start(), m.group(1) + m.group(2)))
    for m in re.finditer(r"\b[A-Z0-9]+\b", t):
        token = m.group(0)
        if token in extra:
            candidates.append((m.start(), token))
        elif token in aliases:
            candidates.append((m.start(), aliases[token]))
    return min(candidates)[1] if candidates else None


def _find_entry(t: str) -> float | None:
    rest = _SL_RE.sub(" ", t)
    rest = _TP_RE.sub(" ", rest)
    rest = re.sub(r"\d+(?:\.\d+)?\s*(?:%|LOTS?\b|PIPS?\b|POINTS?\b)", " ", rest)
    rest = re.sub(r"\b\d{1,2}:\d{2}\b", " ", rest)  # heures
    m = _RANGE_RE.search(rest)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and abs(a - b) / a < 0.05:  # vraie fourchette de prix, pas une date
            return round((a + b) / 2, 6)
    m = re.search(_FREE_NUM, rest)
    return float(m.group(1)) if m else None


def parse_signal(text: str, aliases: dict[str, str] | None = None,
                 extra_symbols: list[str] | tuple[str, ...] = ()) -> Signal | None:
    """Retourne un Signal, ou None si le message n'est pas un signal exploitable."""
    if not text or not text.strip():
        return None
    t = _normalize(text)
    all_aliases = {**DEFAULT_ALIASES, **{k.upper(): v.upper() for k, v in (aliases or {}).items()}}
    symbol = _find_symbol(t, all_aliases, {s.upper() for s in extra_symbols})

    side_m = _SIDE_RE.search(t)
    sl_m = _SL_RE.search(t)
    tps = list(dict.fromkeys(float(x) for x in _TP_RE.findall(t)))
    is_new_trade = side_m is not None and (sl_m is not None or bool(tps))

    if _CLOSE_RE.search(t) and not is_new_trade:
        return Signal("CLOSE", symbol=symbol, close_all=bool(_ALL_RE.search(t)),
                      fraction=0.5 if _HALF_RE.search(t) else 1.0, raw=text)
    if _BE_RE.search(t) and not is_new_trade:
        return Signal("BREAKEVEN", symbol=symbol, close_all=bool(_ALL_RE.search(t)), raw=text)
    if side_m is None or symbol is None:
        return None

    return Signal(
        "OPEN",
        symbol=symbol,
        side=side_m.group(1),
        order_kind=side_m.group(2) or "MARKET",
        entry=_find_entry(t),
        sl=float(sl_m.group(1)) if sl_m else None,
        tps=tps,
        raw=text,
    )
