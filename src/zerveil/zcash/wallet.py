"""The wallet interface Zerveil depends on: a **view-only payment scanner**.

Zerveil never holds funds and never sends ZEC. Advertisers pay creators (and API buyers pay the API seller) straight
from their own wallets to the recipient's own shielded Unified Address. To confirm such a payment without custody,
the recipient connects a viewing key for a receiving account of their wallet; the scanner imports it watch-only,
derives fresh receiving addresses of that account for invoices, and reports the notes that arrive on them. A viewing
key cannot spend.

Two implementations:
- `devtool.DevtoolScanner`: a real light-client scanner (zcash-devtool against a lightwalletd server, testnet).
- `simulated.SimulatedScanner`: an in-process stand-in for development and tests. Everything it reports carries
  `simulated=True`, and the UI labels it as such. It never presents a fake transaction as a real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


class WalletError(Exception):
    """The scanner refused the request (bad viewing key, unknown account, invalid parameters)."""


class WalletUnavailable(WalletError):
    """The scanner, or the lightwalletd server behind it, could not be reached."""


@dataclass(frozen=True)
class ReceivedNote:
    """One shielded output received by a watched account, as the scanner decrypted it."""

    txid: str
    pool: str  # ironwood | orchard | sapling (transparent outputs are ignored)
    output_index: int
    address: str  # the receiving address when the scanner knows it ("" otherwise; the memo reference then matches)
    amount: Decimal
    confirmations: int  # 0 = seen in the mempool, not mined yet
    memo: str = ""  # decrypted memo text ("" when empty or not text)
    mined_height: int | None = None
    expired: bool = False  # the transaction expired unmined: it will never confirm

    @property
    def key(self) -> str:
        return f"{self.txid}:{self.pool}:{self.output_index}"


@dataclass
class WalletInfo:
    backend: str
    network: str
    simulated: bool
    reachable: bool
    server: str = ""  # lightwalletd host:port (no credentials)
    chain_name: str = ""
    tip_height: int | None = None
    nu7_activation_height: int | None = None
    nu7_active: bool | None = None  # tip ≥ NU7 activation height on this network
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


class PaymentScanner(Protocol):
    name: str
    network: str
    simulated: bool

    async def import_viewing_key(self, label: str, ufvk: str, birthday: int) -> str:
        """Imports a Unified Full Viewing Key watch-only and returns the scanner's account reference."""
        ...

    async def new_address(self, account: str) -> str:
        """A fresh shielded Unified Address of the watched account (funds sent to it reach the key owner's wallet)."""
        ...

    async def received(self, account: str, addresses: list[str]) -> list[ReceivedNote]:
        """Shielded notes the watched account received on any of `addresses`, or carrying a ZRV1 memo."""
        ...

    async def info(self) -> WalletInfo: ...
