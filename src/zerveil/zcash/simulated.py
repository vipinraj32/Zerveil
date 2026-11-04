"""In-process stand-in for a Zcash wallet, for development and tests only.

Nothing here touches a blockchain. Addresses are well-formed testnet Unified Address *strings* derived from random
bytes (not real keys), transaction ids are prefixed `sim`, and every record the app builds from this wallet carries
`simulated=True`, which the UI shows as "Simulated — no ZEC moved". `PAYMENT_MODE=zcash` never uses it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from decimal import Decimal

from .address import UA_HRP, bech32m_encode, convert_bits
from .wallet import OperationStatus, ReceivedNote, SendResult, TxStatus, WalletError, WalletInfo


def fake_unified_address(network: str = "testnet", seed: bytes | None = None) -> str:
    """A string with Unified Address encoding and checksum, made from random bytes. Not spendable by anyone."""
    return bech32m_encode(UA_HRP[network], convert_bits(seed or secrets.token_bytes(64), 8, 5))


@dataclass
class _Sent:
    txid: str
    to: str
    amount: Decimal
    memo: str


@dataclass
class SimulatedZcashWallet:
    network: str = "testnet"
    name: str = "simulated"
    simulated: bool = True
    balance: Decimal = Decimal("0")
    notes: list[ReceivedNote] = field(default_factory=list)
    sent: list[_Sent] = field(default_factory=list)
    confirmations: dict[str, int] = field(default_factory=dict)
    fail_next_send: str = ""  # tests: make the next send fail with this message
    _addresses: set[str] = field(default_factory=set)
    _ops: dict[str, OperationStatus] = field(default_factory=dict)

    async def new_invoice_address(self) -> str:
        address = fake_unified_address(self.network)
        self._addresses.add(address)
        return address

    def receive(self, address: str, amount: Decimal, memo: str = "", confirmations: int = 1, txid: str | None = None) -> ReceivedNote:
        """Simulates an incoming shielded payment (dev endpoint / tests)."""
        if address not in self._addresses:
            raise WalletError("not an address of this wallet")
        note = ReceivedNote(txid=txid or "sim" + secrets.token_hex(30), pool="orchard", output_index=len(self.notes), address=address,
                            amount=amount, confirmations=confirmations, memo=memo)  # fmt: skip
        self.notes.append(note)
        self.balance += amount
        return note

    def confirm(self, blocks: int = 1) -> None:
        """Mines `blocks` simulated blocks."""
        self.notes = [ReceivedNote(**{**n.__dict__, "confirmations": n.confirmations + blocks}) for n in self.notes]
        self.confirmations = {k: v + blocks for k, v in self.confirmations.items()}

    async def received(self, addresses: list[str], min_confirmations: int = 0) -> list[ReceivedNote]:
        wanted = set(addresses)
        return [n for n in self.notes if n.address in wanted and n.confirmations >= min_confirmations]

    async def send(self, to: str, amount: Decimal, memo: str) -> SendResult:
        if self.fail_next_send:
            message, self.fail_next_send = self.fail_next_send, ""
            raise WalletError(message)
        if amount > self.balance:
            raise WalletError(f"insufficient funds: {self.balance} available")
        txid = "sim" + secrets.token_hex(30)
        self.balance -= amount
        self.sent.append(_Sent(txid, to, amount, memo))
        self.confirmations[txid] = 0
        op = "opid-sim-" + secrets.token_hex(8)
        self._ops[op] = OperationStatus("success", (txid,))
        return SendResult(operation_id=op)

    async def operation(self, operation_id: str) -> OperationStatus:
        return self._ops.get(operation_id, OperationStatus("unknown"))

    async def transaction(self, txid: str) -> TxStatus | None:
        sent = next((s for s in self.sent if s.txid == txid), None)
        if sent is None:
            return None
        return TxStatus(txid=txid, confirmations=self.confirmations.get(txid, 0), outgoing_memos=(sent.memo,))

    async def recent_outgoing(self, limit: int = 200) -> list[TxStatus]:
        return [TxStatus(s.txid, self.confirmations.get(s.txid, 0), (s.memo,)) for s in self.sent[-limit:]]

    async def validate_address(self, address: str) -> str | None:
        return None

    async def info(self) -> WalletInfo:
        return WalletInfo(backend=self.name, network=self.network, simulated=True, reachable=True, escrow_balance=self.balance,
                          detail="Simulated wallet: no Zcash node or wallet is connected and no ZEC moves.")  # fmt: skip
