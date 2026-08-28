"""OCC option symbol parsing (e.g. AAPL250117C00150000).

Format: <underlying><YYMMDD><C|P><strike*1000, 8 digits zero-padded>. Used
by bot/risk.py to check an option proposal's days-to-expiration, and later
by the snapshot/execute steps to interpret Alpaca's own position/order
symbols the same way.
"""

import re
from dataclasses import dataclass
from datetime import date

_OCC_RE = re.compile(r"^(?P<underlying>[A-Z]+)(?P<date>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


@dataclass
class OCCSymbol:
    underlying: str
    expiration: date
    option_type: str  # "call" | "put"
    strike: float


def parse_occ_symbol(symbol: str) -> OCCSymbol:
    match = _OCC_RE.match(symbol)
    if not match:
        raise ValueError(f"not a valid OCC option symbol: {symbol!r}")
    yy, mm, dd = match["date"][:2], match["date"][2:4], match["date"][4:6]
    expiration = date(2000 + int(yy), int(mm), int(dd))
    option_type = "call" if match["cp"] == "C" else "put"
    strike = int(match["strike"]) / 1000
    return OCCSymbol(
        underlying=match["underlying"],
        expiration=expiration,
        option_type=option_type,
        strike=strike,
    )
