"""Payment records. Amounts are Decimal ZEC (TAZ on testnet), serialized as strings."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

InvoicePurpose = Literal["campaign_funding", "api_access", "credit_topup", "payment_link"]
InvoiceStatus = Literal["pending", "underpaid", "confirming", "paid", "expired", "late_payment", "cancelled"]
OPEN_INVOICE: frozenset[str] = frozenset({"pending", "underpaid", "confirming"})


class InvoiceNote(BaseModel):
    """A shielded output the wallet's scanner found on the invoice's address. Each note is bound to one invoice."""

    key: str  # txid:pool:output_index
    txid: str
    pool: str
    output_index: int
    amount: Decimal
    confirmations: int
    memo_matched: bool  # the decrypted memo carried this invoice's reference
    first_seen_at: datetime


class Invoice(BaseModel):
    """A request to pay `amount` to a one-time shielded Unified Address of the escrow wallet.

    Paid only from wallet scan data: enough value received on `address` with `required_confirmations`.
    A transaction id typed by a user is never evidence."""

    id: str  # PAY-…
    purpose: InvoicePurpose
    owner_id: str  # campaign_funding / api_access / credit_topup: who pays; payment_link: the creator who is paid
    title: str  # shown on the public payment page: no campaign terms
    description: str = ""
    amount: Decimal
    network: str
    address: str
    memo: str  # ZRV1:<id>, the only thing Zerveil asks the payer to put in the encrypted memo
    status: InvoiceStatus = "pending"
    received: Decimal = Decimal("0")
    confirmations: int = 0  # of the least-confirmed note counted
    required_confirmations: int = 3
    notes: list[InvoiceNote] = Field(default_factory=list)
    campaign_id: str | None = None
    deal_ids: list[str] = Field(default_factory=list)
    scope: str = ""  # api_access: the operation it unlocks, e.g. "premium_search:fitness"
    consumed_at: datetime | None = None  # api_access: used once to unlock the operation
    paid_via: Literal["", "shielded", "agent_credit"] = ""
    simulated: bool = False
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None = None
    updated_at: datetime | None = None

    def public(self) -> dict[str, Any]:
        """What anyone with the payment link sees: no transaction ids, no campaign or deal references."""
        return self.model_dump(mode="json", include={"id", "purpose", "title", "description", "amount", "network", "address", "memo", "status",
                                                     "received", "confirmations", "required_confirmations", "simulated", "created_at",
                                                     "expires_at", "paid_at"})  # fmt: skip


PayoutKind = Literal["fixed", "performance", "refund", "link_forward"]
PayoutStatus = Literal["pending_approval", "approved", "sending", "submitted", "sent", "confirmed", "failed", "rejected", "unknown"]


class PayoutApproval(BaseModel):
    by: str
    approved: bool
    note: str = ""
    at: datetime


class PayoutRequest(BaseModel):
    """A shielded payment out of the escrow account, decided by deterministic code and (by default) approved by an
    operator before the wallet sends it. Status moves forward only; `unknown` means a send may or may not have
    happened and is reconciled from the wallet's outgoing memos, never resent automatically."""

    id: str  # PO-…
    kind: PayoutKind
    to_address: str
    amount: Decimal
    memo: str  # ZRV1:<id>
    beneficiary_id: str = ""  # the user being paid
    campaign_id: str | None = None
    deal_id: str | None = None
    invoice_id: str | None = None
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_hash: str = ""
    status: PayoutStatus = "pending_approval"
    approvals: list[PayoutApproval] = Field(default_factory=list)
    operation_id: str = ""
    txid: str = ""
    confirmations: int = 0
    error: str = ""
    attempts: int = 0
    simulated: bool = False
    created_at: datetime
    updated_at: datetime | None = None


def evidence_hash(evidence: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()


class SpendingPolicy(BaseModel):
    """Limits on what the advertiser's AI agent may pay on its own, from the advertiser's prepaid agent credit."""

    enabled: bool = True
    max_per_request: Decimal = Field(default=Decimal("0.1"), ge=0)
    max_daily: Decimal = Field(default=Decimal("1"), ge=0)
    max_campaign_budget: Decimal = Field(default=Decimal("100"), ge=0)  # no campaign the agent runs may exceed this budget
    allowed_categories: list[str] = Field(default_factory=lambda: ["premium_search"])
    approval_above: Decimal = Field(default=Decimal("0.05"), ge=0)  # payments above this wait for the owner's approval


class CreditEntry(BaseModel):
    """Prepaid agent credit. Top-ups come only from paid credit_topup invoices; spends only through the spending policy."""

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
    """Payment verification audit trail (scanner findings, status changes, payout steps)."""

    at: datetime
    invoice_id: str | None = None
    payout_id: str | None = None
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)
