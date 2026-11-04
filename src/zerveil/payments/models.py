"""Payment records. Amounts are Decimal ZEC (TAZ on testnet), serialized as strings.

Zerveil holds no funds. An invoice asks a payer to send ZEC straight to a fresh address of the recipient's own
wallet account; the view-only scanner reports what arrived there, and that is the only evidence of payment.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

InvoicePurpose = Literal["milestone", "api_access", "credit_topup", "payment_link"]
InvoiceStatus = Literal["awaiting_payment", "seen", "mined", "confirming", "confirmed", "underpaid", "overpaid", "expired", "failed",
                        "cancelled"]  # fmt: skip
# Still watched by the scanner and payable
OPEN_INVOICE: frozenset[str] = frozenset({"awaiting_payment", "seen", "mined", "confirming", "underpaid"})
# Paid in full with enough confirmations
PAID_INVOICE: frozenset[str] = frozenset({"confirmed", "overpaid"})
# Escrow-era statuses as stored before migration 3
_LEGACY_STATUS = {"pending": "awaiting_payment", "paid": "confirmed", "late_payment": "expired"}


class InvoiceNote(BaseModel):
    """A shielded output the scanner found for this invoice. Each note is bound to one invoice."""

    key: str  # txid:pool:output_index
    txid: str
    pool: str
    output_index: int
    amount: Decimal
    confirmations: int
    mined_height: int | None = None
    expired: bool = False
    memo_matched: bool  # the decrypted memo carried this invoice's reference
    first_seen_at: datetime


class Invoice(BaseModel):
    """A request to pay `amount` directly to `address`, a one-time shielded Unified Address of the recipient's own
    wallet account. Confirmed only from scanner data: enough value received with `required_confirmations`. A
    transaction id typed by a user is never evidence."""

    id: str  # PAY-…
    purpose: InvoicePurpose
    owner_id: str  # milestone / api_access / credit_topup: who pays; payment_link: the creator who is paid
    recipient_id: str = ""  # whose wallet receives the money (a creator's user id, or "api-seller")
    receiving_account_id: str = ""  # the ReceivingAccount the address belongs to
    title: str  # shown on the public payment page: no campaign terms
    description: str = ""
    amount: Decimal
    network: str
    address: str
    memo: str  # ZRV1:<id>, the only thing Zerveil asks the payer to put in the encrypted memo
    status: InvoiceStatus = "awaiting_payment"
    received: Decimal = Decimal("0")
    confirmations: int = 0  # of the least-confirmed note counted
    required_confirmations: int = 3
    notes: list[InvoiceNote] = Field(default_factory=list)
    campaign_id: str | None = None
    deal_ids: list[str] = Field(default_factory=list)
    milestone_id: str | None = None
    scope: str = ""  # api_access: the operation it unlocks, e.g. "premium_search:fitness"
    consumed_at: datetime | None = None  # api_access: used once to unlock the operation
    paid_via: Literal["", "shielded", "agent_credit"] = ""
    receipt_id: str | None = None
    simulated: bool = False
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_status(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("status") in _LEGACY_STATUS:
                data = {**data, "status": _LEGACY_STATUS[data["status"]]}
            if data.get("purpose") == "campaign_funding":  # escrow funding invoices: history only
                data = {**data, "purpose": "milestone"}
        return data

    @property
    def is_paid(self) -> bool:
        return self.status in PAID_INVOICE

    def public(self) -> dict[str, Any]:
        """What anyone with the payment link sees: no transaction ids, no campaign or deal references."""
        return self.model_dump(mode="json", include={"id", "purpose", "title", "description", "amount", "network", "address", "memo", "status",
                                                     "received", "confirmations", "required_confirmations", "simulated", "created_at",
                                                     "expires_at", "paid_at"})  # fmt: skip


class ReceivingAccount(BaseModel):
    """A wallet account whose incoming payments Zerveil may watch, connected by its owner with a viewing key.

    The viewing key itself is not stored here: only the scanner's account reference and a fingerprint. A viewing
    key cannot spend; it does let the scanner see this account's transactions (see PRIVACY.md)."""

    id: str  # RA-…
    owner_id: str  # user id, or "api-seller"
    role: Literal["creator", "api_seller"]
    label: str = ""
    backend: str
    scanner_account: str  # the scanner's account reference (zcash-devtool account UUID)
    key_fingerprint: str  # sha256 of the viewing key, first 16 hex characters
    birthday: int  # block height the scanner starts from
    network: str
    authorization: str  # the statement the owner agreed to when connecting
    authorized_at: datetime
    revoked_at: datetime | None = None
    simulated: bool = False

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class Receipt(BaseModel):
    """Proof, for the payer and the recipient, that an invoice was paid and confirmed on chain. Holds no addresses;
    `receipt_hash` lets either party show a third party exactly what they were given."""

    id: str  # RCPT-…
    invoice_id: str
    purpose: InvoicePurpose
    title: str
    payer_id: str
    recipient_id: str
    amount: Decimal
    received: Decimal
    network: str
    pools: list[str]
    txids: list[str]
    confirmations: int
    mined_height: int | None = None
    memo: str
    campaign_id: str | None = None
    deal_id: str | None = None
    milestone_id: str | None = None
    simulated: bool = False
    issued_at: datetime
    receipt_hash: str = ""

    def compute_hash(self) -> str:
        body = self.model_dump(mode="json", exclude={"receipt_hash", "confirmations"})
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class SpendingPolicy(BaseModel):
    """Limits on what the advertiser's AI agent may pay on its own, from the advertiser's prepaid API credit."""

    enabled: bool = True
    max_per_request: Decimal = Field(default=Decimal("0.1"), ge=0)
    max_daily: Decimal = Field(default=Decimal("1"), ge=0)
    max_campaign_budget: Decimal = Field(default=Decimal("100"), ge=0)  # no campaign the agent runs may exceed this budget
    allowed_categories: list[str] = Field(default_factory=lambda: ["premium_search"])
    approval_above: Decimal = Field(default=Decimal("0.05"), ge=0)  # payments above this wait for the owner's approval


class CreditEntry(BaseModel):
    """Prepaid API credit with the API seller. Top-ups come only from confirmed credit_topup invoices paid to the
    seller's wallet; spends only through the spending policy. It is a prepayment for API calls, not a deposit."""

    id: str
    user_id: str
    amount: Decimal  # + top-up, − spend
    kind: Literal["topup", "spend"]
    ref: str  # invoice id: one entry per invoice and kind
    category: str = ""
    simulated: bool = False
    at: datetime


class SpendRequest(BaseModel):
    """An agent payment above the auto-approval limit, waiting for its owner."""

    id: str
    user_id: str
    invoice_id: str
    category: str
    amount: Decimal
    status: Literal["pending", "approved", "rejected", "paid"] = "pending"
    reason: str = ""
    created_at: datetime
    decided_at: datetime | None = None


class PaymentEvent(BaseModel):
    """Payment verification audit trail (scanner findings, status changes, receipts)."""

    at: datetime
    invoice_id: str | None = None
    payout_id: str | None = None  # escrow-era history only
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)
