"""Business currency vs payment asset.

Zerveil keeps its books in **USD**: campaign budgets, agent floors and ceilings, signed deal terms, milestones, API
prices, credit and analytics are USD amounts (Decimal, 2 places). What actually moves is a **payment asset** on a
**payment network**: shielded ZEC on Zcash (TAZ on testnet). An invoice converts its USD value into an asset amount
once, at creation, with a `RateQuote` stored on the invoice; the scanner then checks the asset amount.

There is no USD stablecoin on Zcash (Zcash Shielded Assets are not on the Zcash testnet or mainnet), so nothing here
is a stablecoin and TAZ is never presented as USD. Testnet TAZ has no market price: its rate is a configured *test
rate* (ZCASH_TEST_USD_RATE), labeled as such everywhere it appears. Mainnet would need a live ZEC/USD price source,
which this build does not implement, so mainnet pricing is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from .zcash.amounts import ZATOSHI

if TYPE_CHECKING:
    from .config import Settings

USD = "USD"
CENT = Decimal("0.01")
TEST_RATE_NOTICE = "test rate — TAZ has no market price; the TAZ amount has no real value"


class RateUnavailable(Exception):
    """No trustworthy USD rate for the payment asset: refuse to price an invoice rather than guess."""


def usd(value: Any) -> Decimal:
    """A USD amount rounded to cents (half up). Never built from a float."""
    if isinstance(value, float):
        raise TypeError("USD amounts must not be floats")
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as e:
        raise ValueError(f"not a USD amount: {value!r}") from e


def exact_usd(value: Decimal) -> Decimal:
    """A USD amount that must already be whole cents (user and policy input)."""
    if value != value.quantize(CENT):
        raise ValueError("USD amounts have at most 2 decimal places")
    return value.quantize(CENT)


def format_usd(value: Decimal, *, code: bool = False) -> str:
    """'$1,234.50' (with code=True: '$1,234.50 USD')."""
    text = f"${usd(value):,.2f}"
    return f"{text} USD" if code else text


@dataclass(frozen=True)
class RateQuote:
    asset: str  # what is paid: TAZ (testnet) or ZEC
    network: str  # zcash-testnet | zcash-mainnet
    usd_per_unit: Decimal  # USD value of 1 asset unit
    source: str
    is_test_rate: bool
    quoted_at: datetime

    def to_asset(self, amount_usd: Decimal) -> Decimal:
        """Asset amount for a USD value, rounded UP to the zatoshi so the recipient never gets less than agreed."""
        if self.usd_per_unit <= 0:
            raise RateUnavailable("the rate must be positive")
        return (usd(amount_usd) / self.usd_per_unit).quantize(ZATOSHI, rounding=ROUND_UP)

    def to_usd(self, amount: Decimal) -> Decimal:
        """USD equivalent of an asset amount at this quote (rounded down to the cent)."""
        return (amount * self.usd_per_unit).quantize(CENT, rounding=ROUND_DOWN)


def quote(settings: Settings, now: datetime) -> RateQuote:
    """The rate an invoice is priced at. Testnet: the configured test rate. Mainnet: refused (no live source)."""
    network = settings.zcash_network
    if network == "mainnet":
        raise RateUnavailable("mainnet pricing needs a live ZEC/USD price source, which this build does not implement")
    rate = settings.zcash_test_usd_rate
    if rate <= 0:
        raise RateUnavailable("ZCASH_TEST_USD_RATE must be positive")
    return RateQuote(asset=settings.ticker, network=f"zcash-{network}", usd_per_unit=rate,
                     source=f"ZCASH_TEST_USD_RATE ({TEST_RATE_NOTICE})", is_test_rate=True, quoted_at=now)  # fmt: skip
