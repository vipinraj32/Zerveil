"""ZEC amounts. 1 ZEC (TAZ on testnet) = 100,000,000 zatoshi. Amounts are Decimal in the app and integers on the wire."""

from __future__ import annotations

import os
from decimal import Decimal

ZATOSHI = Decimal("0.00000001")
MAX_MONEY_ZAT = 21_000_000 * 100_000_000


def to_zatoshi(amount: Decimal) -> int:
    """Exact conversion; refuses amounts finer than one zatoshi or outside the money range."""
    zat = amount * 100_000_000
    if zat != zat.to_integral_value():
        raise ValueError(f"{amount} has more than 8 decimal places")
    value = int(zat)
    if not 0 <= value <= MAX_MONEY_ZAT:
        raise ValueError(f"{amount} is outside the valid ZEC range")
    return value


def zec_from_zatoshi(zat: int) -> Decimal:
    return (Decimal(zat) / 100_000_000).quantize(ZATOSHI)


def format_zec(amount: Decimal) -> str:
    """Plain decimal string without trailing zeros (ZIP-321 amounts must not use exponent notation)."""
    q = amount.quantize(ZATOSHI)
    text = format(q, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def ticker() -> str:
    """TAZ on testnet (test coins with no value), ZEC on mainnet."""
    return "ZEC" if os.environ.get("ZCASH_NETWORK", "testnet").lower() == "mainnet" else "TAZ"
