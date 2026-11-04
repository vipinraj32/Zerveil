"""In-process stand-in for the payment scanner, for development and tests only.

Nothing here touches a blockchain:
- Addresses are well-formed testnet Unified Address *strings* derived from random bytes, not real keys. Never use
  them for real testing.
- Transaction ids are prefixed `sim`.
- Every record the app builds from this scanner carries `simulated=True`, which the UI shows as
  "Simulated — no ZEC moved".

`PAYMENT_MODE=zcash` never uses it.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from decimal import Decimal

from .address import UA_HRP, bech32m_encode, convert_bits
from .wallet import ReceivedNote, WalletError, WalletInfo


def fake_unified_address(network: str = "testnet", seed: bytes | None = None) -> str:
    """A string with Unified Address encoding and checksum, made from random bytes. Not spendable by anyone."""
    return bech32m_encode(UA_HRP[network], convert_bits(seed or secrets.token_bytes(64), 8, 5))


@dataclass
class SimulatedScanner:
    network: str = "testnet"
    name: str = "simulated"
    simulated: bool = True
    tip_height: int = 4_500_000
    notes: dict[str, list[ReceivedNote]] = field(default_factory=dict)  # account → notes
    _accounts: dict[str, str] = field(default_factory=dict)  # account → key fingerprint
    _addresses: dict[str, str] = field(default_factory=dict)  # address → account

    async def import_viewing_key(self, label: str, ufvk: str, birthday: int) -> str:
        fingerprint = hashlib.sha256(ufvk.encode()).hexdigest()
        for account, fp in self._accounts.items():
            if fp == fingerprint:
                return account
        account = "sim-acct-" + fingerprint[:16]
        self._accounts[account] = fingerprint
        self.notes.setdefault(account, [])
        return account

    async def new_address(self, account: str) -> str:
        if account not in self._accounts:
            raise WalletError("unknown watched account")
        address = fake_unified_address(self.network)
        self._addresses[address] = account
        return address

    def receive(self, address: str, amount: Decimal, memo: str = "", confirmations: int = 1, txid: str | None = None) -> ReceivedNote:
        """Simulates an incoming shielded payment to a watched address (dev endpoint / tests)."""
        account = self._addresses.get(address)
        if account is None:
            raise WalletError("not an address of a watched account")
        notes = self.notes[account]
        height = self.tip_height - confirmations + 1 if confirmations > 0 else None
        note = ReceivedNote(txid=txid or "sim" + secrets.token_hex(30), pool="ironwood", output_index=len(notes), address=address,
                            amount=amount, confirmations=confirmations, memo=memo, mined_height=height)  # fmt: skip
        notes.append(note)
        return note

    def confirm(self, blocks: int = 1) -> None:
        """Mines `blocks` simulated blocks: every note gains that many confirmations."""
        self.tip_height += blocks
        for account, notes in self.notes.items():
            self.notes[account] = [
                ReceivedNote(
                    **{**n.__dict__, "confirmations": n.confirmations + blocks, "mined_height": n.mined_height or self.tip_height - blocks + 1}
                )  # fmt: skip
                for n in notes
            ]

    def expire(self, txid: str) -> None:
        """Marks an unmined simulated transaction as expired (it will never confirm)."""
        for account, notes in self.notes.items():
            self.notes[account] = [ReceivedNote(**{**n.__dict__, "expired": True}) if n.txid == txid and n.confirmations == 0 else n
                                   for n in notes]  # fmt: skip

    async def received(self, account: str, addresses: list[str]) -> list[ReceivedNote]:
        wanted = set(addresses)
        return [n for n in self.notes.get(account, []) if n.address in wanted or n.memo.startswith("ZRV1:")]

    async def info(self) -> WalletInfo:
        return WalletInfo(backend=self.name, network=self.network, simulated=True, reachable=True, tip_height=self.tip_height,
                          detail="Simulated scanner: no Zcash node or wallet is connected and no ZEC moves.")  # fmt: skip
