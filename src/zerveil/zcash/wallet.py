"""The wallet interface Zerveil depends on. Two implementations:

- `rpc.ZalletRpcWallet`: a real Zcash wallet (Zallet, or zcashd-compatible wallet RPC) on testnet. The wallet holds
  the keys, builds and proves transactions, scans the chain and broadcasts. Zerveil never sees a spending key.
- `simulated.SimulatedZcashWallet`: an in-process stand-in for development and tests. Everything it reports carries
  `simulated=True` and the UI labels it as such; it never presents a fake transaction as a real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol


class WalletError(Exception):
    """The wallet refused the request (bad address, insufficient funds, invalid params)."""


class WalletUnavailable(WalletError):
    """The wallet could not be reached or did not answer: the outcome of a send is unknown."""


@dataclass(frozen=True)
class ReceivedNote:
    """One shielded output received by the wallet, as its scanner decrypted it."""

    txid: str
    pool: str  # orchard | sapling (transparent outputs are ignored for invoices)
    output_index: int
    address: str  # the receiving address (the invoice's one-time Unified Address)
    amount: Decimal
    confirmations: int
    memo: str = ""  # decrypted memo text ("" when empty or not text)

    @property
    def key(self) -> str:
        return f"{self.txid}:{self.pool}:{self.output_index}"


@dataclass(frozen=True)
class SendResult:
    """What the wallet returned for a send: an async operation id, and the txid once known."""

    operation_id: str = ""
    txids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationStatus:
    status: Literal["queued", "executing", "success", "failed", "cancelled", "unknown"]
    txids: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class TxStatus:
    txid: str
    confirmations: int  # 0 = in the mempool; -1 = conflicted / not found
    outgoing_memos: tuple[str, ...] = ()


@dataclass
class WalletInfo:
    backend: str
    network: str
    simulated: bool
    reachable: bool
    escrow_balance: Decimal | None = None  # spendable balance of the escrow account, when the wallet reports it
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


class ZcashWallet(Protocol):
    name: str
    network: str
    simulated: bool

    async def new_invoice_address(self) -> str:
        """A fresh shielded Unified Address of the escrow account, used by exactly one invoice."""
        ...

    async def received(self, addresses: list[str], min_confirmations: int = 0) -> list[ReceivedNote]:
        """Shielded notes received on any of `addresses`."""
        ...

    async def send(self, to: str, amount: Decimal, memo: str) -> SendResult:
        """Starts a shielded payment from the escrow account. Raises WalletError when refused before sending,
        WalletUnavailable when the outcome is unknown (never retry those blindly)."""
        ...

    async def operation(self, operation_id: str) -> OperationStatus: ...

    async def transaction(self, txid: str) -> TxStatus | None: ...

    async def recent_outgoing(self, limit: int = 200) -> list[TxStatus]:
        """Recent transactions sent by the escrow account with their outgoing memos (payout reconciliation)."""
        ...

    async def validate_address(self, address: str) -> str | None:
        """Why the wallet would not pay `address` (None when it is a usable shielded Unified Address)."""
        ...

    async def info(self) -> WalletInfo: ...
