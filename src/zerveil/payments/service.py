"""Zcash payments: invoices verified from wallet scan data, a payout queue with operator approval, payment links,
prepaid agent credit with spending limits.

Rules this module keeps:
- An invoice becomes `paid` only from notes the wallet's scanner decrypted on the invoice's own one-time address,
  with enough value and confirmations. Nothing a user types (a txid, a screenshot) is evidence.
- Each received note is bound to one invoice (`store.claim("zcash_note", txid:pool:index)`), so a payment is never
  counted twice, even if two invoices were (wrongly) given the same address.
- A payout is handed to the wallet at most once: `approved → sending` is a compare-and-set, and the request is saved
  before the wallet is called. A send whose outcome is lost becomes `unknown` and is looked up by its memo in the
  wallet's outgoing transactions; it is never resent without an operator saying it was not sent.
- The escrow is a wallet account the platform operates (custodial). Nothing here is a smart contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..models import new_id
from ..zcash.address import unified_address_problem
from ..zcash.memo import memo_for, reference_in
from ..zcash.wallet import ReceivedNote, WalletError, WalletUnavailable
from ..zcash.zip321 import payment_uri
from .models import (
    OPEN_INVOICE,
    CreditEntry,
    Invoice,
    InvoiceNote,
    InvoicePurpose,
    PaymentEvent,
    PayoutApproval,
    PayoutKind,
    PayoutRequest,
    SpendingPolicy,
    SpendRequest,
    evidence_hash,
)

if TYPE_CHECKING:
    from ..services.context import AppContext

log = logging.getLogger(__name__)

SPENDING_POLICY_KIND = "spending_policy"
LATE_WINDOW = timedelta(days=7)  # expired invoices keep being watched this long, so late payments are noticed
PAYOUT_ACTIVE = {"pending_approval", "approved", "sending", "submitted", "sent", "unknown"}

InvoiceHook = Callable[[Invoice], Awaitable[None]]


class PaymentError(Exception):
    pass


class PaymentService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._scan_lock = asyncio.Lock()
        self._payout_lock = asyncio.Lock()
        self._spend_lock = asyncio.Lock()
        # purpose → called when an invoice of that purpose changes status (DealService registers campaign_funding)
        self.on_status: dict[str, InvoiceHook] = {}

    @property
    def wallet(self):  # noqa: ANN201
        return self.ctx.wallet

    async def event(self, event_kind: str, /, *, invoice_id: str | None = None, payout_id: str | None = None, **detail: Any) -> None:
        await self.ctx.store.add_payment_event(PaymentEvent(at=self.ctx.clock.now(), invoice_id=invoice_id, payout_id=payout_id, kind=event_kind,
                                                            detail=json.loads(json.dumps(detail, default=str))))  # fmt: skip

    def _publish(self, payload: dict[str, Any], *users: str) -> None:
        self.ctx.bus.publish(payload, {u for u in users if u})

    # ---- invoices ----------------------------------------------------------------------------------------------

    async def create_invoice(self, purpose: InvoicePurpose, owner_id: str, title: str, amount: Decimal, *, description: str = "",
                             campaign_id: str | None = None, deal_ids: list[str] | None = None, scope: str = "",
                             ttl_minutes: int | None = None) -> Invoice:  # fmt: skip
        if amount <= 0:
            raise PaymentError("the amount must be positive")
        if amount != amount.quantize(Decimal("0.00000001")):
            raise PaymentError("ZEC amounts have at most 8 decimal places")
        invoice_id = new_id("PAY")
        now = self.ctx.clock.now()
        ttl = ttl_minutes if ttl_minutes is not None else self.ctx.settings.zcash_invoice_ttl_minutes
        for attempt in range(3):  # a diversified address is fresh by construction; the unique address index is the backstop
            invoice = Invoice(id=invoice_id, purpose=purpose, owner_id=owner_id, title=title[:120], description=description[:500], amount=amount,
                              network=f"zcash-{self.ctx.settings.zcash_network}", address=await self.wallet.new_invoice_address(),
                              memo=memo_for(invoice_id), required_confirmations=self.ctx.settings.zcash_confirmation_threshold,
                              campaign_id=campaign_id, deal_ids=deal_ids or [], scope=scope, simulated=self.wallet.simulated, created_at=now,
                              expires_at=now + timedelta(minutes=ttl), updated_at=now)  # fmt: skip
            try:
                await self.ctx.store.save_invoice(invoice)
                break
            except Exception as e:  # noqa: BLE001 - unique violation on the address
                if attempt == 2 or "address" not in str(e):
                    raise PaymentError("the wallet returned an address already used by another invoice") from e
        await self.event("invoice_created", invoice_id=invoice.id, purpose=purpose, amount=amount, simulated=invoice.simulated)
        return invoice

    async def _recent(self) -> list[Invoice]:
        return await self.ctx.store.list_invoices(status=set(OPEN_INVOICE) | {"expired", "late_payment"})

    async def get_invoice(self, invoice_id: str) -> Invoice:
        invoice = await self.ctx.store.get_invoice(invoice_id)
        if invoice is None:
            raise PaymentError(f"unknown payment {invoice_id}")
        return invoice

    def payment_request(self, invoice: Invoice) -> dict[str, Any]:
        """Everything a payer (human or agent) needs: address, amount, memo, ZIP-321 URI, link, expiry. No txids."""
        return {
            **invoice.public(),
            "ticker": self.ctx.settings.ticker,
            "zip321_uri": payment_uri(invoice.address, invoice.amount, invoice.memo, invoice.title),
            "pay_url": f"{self.ctx.settings.public_base_url.rstrip('/')}/pay/{invoice.id}",
        }

    async def cancel_invoice(self, invoice: Invoice, by: str) -> Invoice:
        if invoice.status != "pending" or invoice.notes:
            raise PaymentError("only an invoice that has received nothing can be cancelled")
        invoice.status = "cancelled"
        invoice.updated_at = self.ctx.clock.now()
        await self.ctx.store.save_invoice(invoice)
        await self.event("invoice_cancelled", invoice_id=invoice.id, by=by)
        await self._notify(invoice)
        return invoice

    # ---- scanning: wallet data → invoice status -------------------------------------------------------------------

    async def scan(self) -> int:
        """Matches notes the wallet received to open (and recently expired) invoices. Returns status changes."""
        async with self._scan_lock:
            now = self.ctx.clock.now()
            watched = [i for i in await self._recent() if i.status in OPEN_INVOICE or now - i.expires_at < LATE_WINDOW]
            if not watched:
                return 0
            try:
                notes = await self.wallet.received([i.address for i in watched], 0)
            except WalletError as e:
                log.warning("payment scan skipped: %s", e)
                return 0
            by_address: dict[str, list[ReceivedNote]] = {}
            for n in notes:
                by_address.setdefault(n.address, []).append(n)
            changed = 0
            for invoice in watched:
                if await self._apply(invoice, by_address.get(invoice.address, []), now):
                    changed += 1
            return changed

    async def _apply(self, invoice: Invoice, notes: list[ReceivedNote], now: datetime) -> bool:
        before = (invoice.status, invoice.received, invoice.confirmations)
        known = {n.key: n for n in invoice.notes}
        for note in notes:
            if note.key in known:
                known[note.key].confirmations = note.confirmations
                continue
            owner = await self.ctx.store.claim("zcash_note", note.key, invoice.id)
            if owner != invoice.id:  # already counted for another invoice: never twice
                await self.event("note_already_counted", invoice_id=invoice.id, note=note.key, counted_for=owner)
                continue
            matched = reference_in(note.memo) == invoice.id
            known[note.key] = InvoiceNote(key=note.key, txid=note.txid, pool=note.pool, output_index=note.output_index, amount=note.amount,
                                          confirmations=note.confirmations, memo_matched=matched, first_seen_at=now)  # fmt: skip
            await self.event("note_detected", invoice_id=invoice.id, amount=note.amount, pool=note.pool, confirmations=note.confirmations,
                             memo_matched=matched, txid=note.txid, simulated=invoice.simulated)  # fmt: skip
        invoice.notes = list(known.values())
        invoice.received = sum((n.amount for n in invoice.notes), Decimal("0"))
        invoice.confirmations = min((n.confirmations for n in invoice.notes), default=0)

        new_notes = len(invoice.notes) > 0 and before[1] != invoice.received
        if invoice.status in ("expired", "cancelled", "late_payment") and new_notes:
            invoice.status = "late_payment"
            await self.event("late_payment", invoice_id=invoice.id, received=invoice.received)
            await self._refund_unusable(invoice, "payment arrived after the invoice expired")
        elif invoice.status in OPEN_INVOICE:
            if invoice.received >= invoice.amount and invoice.confirmations >= invoice.required_confirmations:
                invoice.status, invoice.paid_at, invoice.paid_via = "paid", now, "shielded"
            elif invoice.received >= invoice.amount:
                invoice.status = "confirming"  # paid in time; only confirmations are missing, so it never expires
            elif invoice.received > 0:
                invoice.status = "underpaid"
            if invoice.status in ("pending", "underpaid") and now > invoice.expires_at:
                invoice.status = "expired"
                if invoice.received > 0:
                    await self._refund_unusable(invoice, "the invoice expired before it was paid in full")
        if (invoice.status, invoice.received, invoice.confirmations) == before:
            return False
        invoice.updated_at = now
        await self.ctx.store.save_invoice(invoice)
        if invoice.status != before[0]:
            await self.event("invoice_status", invoice_id=invoice.id, status=invoice.status, received=invoice.received,
                             confirmations=invoice.confirmations)  # fmt: skip
            if invoice.status == "paid" and invoice.received > invoice.amount:
                await self.event("overpaid", invoice_id=invoice.id, excess=invoice.received - invoice.amount)
            await self._notify(invoice)
        else:
            self._publish({"type": "invoice", "invoice_id": invoice.id, "status": invoice.status}, invoice.owner_id)
        return invoice.status != before[0]

    async def _notify(self, invoice: Invoice) -> None:
        self._publish({"type": "invoice", "invoice_id": invoice.id, "status": invoice.status}, invoice.owner_id)
        hook = self.on_status.get(invoice.purpose) or {"credit_topup": self._credit_topup, "payment_link": self._forward_link}.get(invoice.purpose)
        if hook is not None:
            await hook(invoice)

    async def _refund_unusable(self, invoice: Invoice, reason: str) -> None:
        """Money that cannot be used for its purpose goes back to the payer's own refund address, after approval."""
        payer = await self.ctx.store.get_user(invoice.owner_id) if invoice.purpose != "payment_link" else None
        if payer is None or not payer.zcash_address:
            await self.event("refund_needs_operator", invoice_id=invoice.id, received=invoice.received, reason=reason)
            return
        # Each refund covers the money received up to that point: a later note is refunded on its own, never twice
        earlier = [p for p in await self.ctx.store.list_payouts() if p.invoice_id == invoice.id and p.kind == "refund" and p.status != "rejected"]
        covered = max((Decimal(p.evidence.get("received", "0")) for p in earlier), default=Decimal("0"))
        amount = invoice.received - covered - self.ctx.settings.zcash_fee_reserve_per_deal
        if amount <= 0:
            await self.event("refund_below_fee", invoice_id=invoice.id, received=invoice.received - covered)
            return
        await self.request_payout("refund", payer.zcash_address, amount, reason, beneficiary_id=payer.id, campaign_id=invoice.campaign_id,
                                  invoice_id=invoice.id, key=f"{invoice.id}:unusable:{invoice.received}",
                                  evidence={"invoice": invoice.id, "received": str(invoice.received), "covered_before": str(covered)},
                                  force_approval=True)  # fmt: skip

    # ---- simulated mode only -----------------------------------------------------------------------------------

    async def simulate_payment(self, invoice: Invoice, amount: Decimal | None = None, confirmations: int | None = None) -> Invoice:
        """Development helper: the simulated wallet "receives" a payment, then the normal scanner verifies it."""
        from ..zcash.simulated import SimulatedZcashWallet

        if not isinstance(self.wallet, SimulatedZcashWallet):
            raise PaymentError("payments can only be simulated with PAYMENT_MODE=simulated")
        confs = confirmations if confirmations is not None else invoice.required_confirmations
        self.wallet.receive(invoice.address, amount if amount is not None else invoice.amount - invoice.received, invoice.memo, confs)
        await self.scan()
        return await self.get_invoice(invoice.id)

    # ---- payouts ------------------------------------------------------------------------------------------------

    async def request_payout(self, kind: PayoutKind, to: str, amount: Decimal, reason: str, *, key: str, beneficiary_id: str = "",
                             campaign_id: str | None = None, deal_id: str | None = None, invoice_id: str | None = None,
                             evidence: dict[str, Any] | None = None, force_approval: bool = False) -> PayoutRequest | None:  # fmt: skip
        """Queues one payout, idempotently on `key` (e.g. "<deal>:fixed"). None when there is nothing to pay."""
        if amount <= 0:
            return None
        payout_id = new_id("PO")
        owner = await self.ctx.store.claim("payout", key, payout_id)
        if owner != payout_id:
            return await self.ctx.store.get_payout(owner)
        problem = unified_address_problem(to, self.ctx.settings.zcash_network)  # type: ignore[arg-type]
        now = self.ctx.clock.now()
        approval = force_approval or self.ctx.settings.payout_approval_required
        ev = evidence or {}
        payout = PayoutRequest(id=payout_id, kind=kind, to_address=to, amount=amount.quantize(Decimal("0.00000001")), memo=memo_for(payout_id),
                               beneficiary_id=beneficiary_id, campaign_id=campaign_id, deal_id=deal_id, invoice_id=invoice_id, reason=reason,
                               evidence=ev, evidence_hash=evidence_hash(ev), status="pending_approval" if approval or problem else "approved",
                               error=problem or "", simulated=self.wallet.simulated, created_at=now, updated_at=now)  # fmt: skip
        await self.ctx.store.save_payout(payout)
        await self.event("payout_requested", payout_id=payout.id, payout_kind=kind, amount=payout.amount, status=payout.status, reason=reason,
                         evidence_hash=payout.evidence_hash)  # fmt: skip
        self._publish({"type": "payout", "payout_id": payout.id, "status": payout.status}, beneficiary_id)
        return payout

    async def get_payout(self, payout_id: str) -> PayoutRequest:
        payout = await self.ctx.store.get_payout(payout_id)
        if payout is None:
            raise PaymentError(f"unknown payout {payout_id}")
        return payout

    async def decide_payout(self, payout_id: str, by: str, approved: bool, note: str = "") -> PayoutRequest:
        """An operator's decision. Approval lets the queue send it; rejection keeps the funds in the escrow account."""
        payout = await self.get_payout(payout_id)
        if payout.status != "pending_approval":
            raise PaymentError(f"payout is {payout.status}, not waiting for approval")
        if approved and (problem := unified_address_problem(payout.to_address, self.ctx.settings.zcash_network)):  # type: ignore[arg-type]
            raise PaymentError(f"cannot approve: {problem}")
        payout.approvals.append(PayoutApproval(by=by, approved=approved, note=note[:300], at=self.ctx.clock.now()))
        payout.status = "approved" if approved else "rejected"
        payout.updated_at = self.ctx.clock.now()
        if not await self.ctx.store.save_payout(payout, expect_status="pending_approval"):
            raise PaymentError("the payout changed meanwhile; reload")
        await self.event("payout_approved" if approved else "payout_rejected", payout_id=payout.id, by=by, note=note)
        self._publish({"type": "payout", "payout_id": payout.id, "status": payout.status}, payout.beneficiary_id)
        return payout

    async def resolve_unknown(self, payout_id: str, by: str, action: str, note: str = "") -> PayoutRequest:
        """For a payout whose send outcome was lost and is not in the wallet's history: an operator who checked the
        wallet says it was not sent (`resend`) or closes it (`cancel`)."""
        payout = await self.get_payout(payout_id)
        if payout.status not in ("unknown", "failed"):
            raise PaymentError(f"payout is {payout.status}")
        expect = payout.status
        payout.approvals.append(PayoutApproval(by=by, approved=action == "resend", note=f"{action}: {note}"[:300], at=self.ctx.clock.now()))
        payout.status = "approved" if action == "resend" else "rejected"
        payout.updated_at = self.ctx.clock.now()
        if not await self.ctx.store.save_payout(payout, expect_status=expect):
            raise PaymentError("the payout changed meanwhile; reload")
        await self.event(f"payout_{action}", payout_id=payout.id, by=by, note=note)
        return payout

    async def process_payouts(self) -> int:
        """Moves the payout queue forward: send approved ones, follow wallet operations, count confirmations."""
        async with self._payout_lock:
            actions = 0
            now = self.ctx.clock.now()
            for payout in await self.ctx.store.list_payouts(status={"approved", "sending", "submitted", "sent", "unknown"}):
                try:
                    if await self._advance(payout, now):
                        actions += 1
                except WalletUnavailable as e:
                    log.warning("payout %s: wallet unavailable (%s)", payout.id, e)
                    break
            return actions

    async def _advance(self, payout: PayoutRequest, now: datetime) -> bool:
        stale = payout.updated_at is not None and now - payout.updated_at > timedelta(minutes=self.ctx.settings.payout_reconcile_after_minutes)
        match payout.status:
            case "approved":
                return await self._send(payout)
            case "sending" if stale:  # the process died between saving `sending` and recording the wallet's answer
                return await self._set(payout, "unknown", "sending", error="the send's result was not recorded; checking the wallet")
            case "submitted":
                op = await self.wallet.operation(payout.operation_id)
                if op.status == "success" and op.txids:
                    return await self._set(payout, "sent", "submitted", txid=op.txids[0])
                if op.status in ("failed", "cancelled"):
                    return await self._set(payout, "failed", "submitted", error=op.error or op.status)
                if op.status == "unknown" and stale:
                    return await self._set(payout, "unknown", "submitted", error="the wallet no longer knows this operation")
            case "sent":
                tx = await self.wallet.transaction(payout.txid)
                if tx is not None and tx.confirmations != payout.confirmations:
                    status = "confirmed" if tx.confirmations >= self.ctx.settings.zcash_confirmation_threshold else "sent"
                    return await self._set(payout, status, "sent", confirmations=tx.confirmations)
            case "unknown":
                for tx in await self.wallet.recent_outgoing():
                    if payout.memo in tx.outgoing_memos:
                        return await self._set(payout, "sent", "unknown", txid=tx.txid, confirmations=max(tx.confirmations, 0))
        return False

    async def _send(self, payout: PayoutRequest) -> bool:
        payout.status, payout.attempts, payout.updated_at = "sending", payout.attempts + 1, self.ctx.clock.now()
        if not await self.ctx.store.save_payout(payout, expect_status="approved"):
            return False  # another worker took it
        await self.event("payout_sending", payout_id=payout.id, attempt=payout.attempts)
        try:
            result = await self.wallet.send(payout.to_address, payout.amount, payout.memo)
        except WalletUnavailable as e:
            await self._set(payout, "unknown", "sending", error=str(e)[:300])
            return True
        except WalletError as e:  # refused before anything was broadcast
            await self._set(payout, "failed", "sending", error=str(e)[:300])
            return True
        if result.txids:
            return await self._set(payout, "sent", "sending", txid=result.txids[0], operation_id=result.operation_id)
        return await self._set(payout, "submitted", "sending", operation_id=result.operation_id)

    async def _set(self, payout: PayoutRequest, status: str, expect: str, **fields: Any) -> bool:
        for k, v in fields.items():
            setattr(payout, k, v)
        payout.status = status  # type: ignore[assignment]
        payout.updated_at = self.ctx.clock.now()
        if not await self.ctx.store.save_payout(payout, expect_status=expect):
            return False
        await self.event(f"payout_{status}", payout_id=payout.id, **{k: v for k, v in fields.items() if k != "evidence"})
        self._publish({"type": "payout", "payout_id": payout.id, "status": status}, payout.beneficiary_id)
        return True

    # ---- payment links ---------------------------------------------------------------------------------------

    async def create_payment_link(self, creator_id: str, title: str, amount: Decimal, description: str = "", ttl_days: int = 14) -> Invoice:
        creator = await self.ctx.store.get_user(creator_id)
        if creator is None or not creator.zcash_address:
            raise PaymentError("add your shielded payout address first: link payments are forwarded to it")
        if amount <= self.ctx.settings.zcash_fee_reserve_per_deal:
            raise PaymentError("the amount must be larger than the network fee reserve")
        return await self.create_invoice("payment_link", creator_id, title, amount, description=description, ttl_minutes=ttl_days * 24 * 60)

    async def _forward_link(self, invoice: Invoice) -> None:
        if invoice.status != "paid":
            return
        creator = await self.ctx.store.get_user(invoice.owner_id)
        if creator is None or not creator.zcash_address:
            await self.event("refund_needs_operator", invoice_id=invoice.id, reason="creator has no payout address")
            return
        await self.request_payout("link_forward", creator.zcash_address, invoice.received - self.ctx.settings.zcash_fee_reserve_per_deal,
                                  f"payment link {invoice.id}", key=f"{invoice.id}:forward", beneficiary_id=creator.id, invoice_id=invoice.id,
                                  evidence={"invoice": invoice.id, "received": str(invoice.received), "notes": [n.key for n in invoice.notes]})  # fmt: skip

    # ---- agent credit and spending limits ------------------------------------------------------------------------

    async def spending_policy(self, user_id: str) -> SpendingPolicy:
        doc = await self.ctx.store.get_version(SPENDING_POLICY_KIND, user_id)
        return SpendingPolicy.model_validate(doc) if doc else SpendingPolicy()

    async def save_spending_policy(self, user_id: str, policy: SpendingPolicy) -> dict[str, Any]:
        return await self.ctx.store.save_version(SPENDING_POLICY_KIND, user_id, policy.model_dump(mode="json"))

    async def create_topup(self, user_id: str, amount: Decimal) -> Invoice:
        return await self.create_invoice("credit_topup", user_id, "Agent credit top-up", amount)

    async def _credit_topup(self, invoice: Invoice) -> None:
        if invoice.status != "paid":
            return
        entry = CreditEntry(id=new_id("CR"), user_id=invoice.owner_id, amount=invoice.received, kind="topup", ref=invoice.id,
                            simulated=invoice.simulated, at=self.ctx.clock.now())  # fmt: skip
        if await self.ctx.store.add_credit(entry):
            await self.event("credit_topped_up", invoice_id=invoice.id, amount=invoice.received)

    async def credit_summary(self, user_id: str) -> dict[str, Any]:
        day_start = self.ctx.clock.now() - timedelta(days=1)
        return {
            "balance": str(await self.ctx.store.credit_balance(user_id)),
            "spent_24h": str(await self.ctx.store.spent_since(user_id, day_start)),
            "entries": [e.model_dump(mode="json") for e in (await self.ctx.store.list_credit(user_id))[:50]],
            "pending_requests": [r.model_dump(mode="json") for r in await self.ctx.store.list_spend_requests(user_id, "pending")],
        }

    async def agent_pay(self, user_id: str, invoice: Invoice, category: str) -> dict[str, Any]:
        """The advertiser's agent pays a 402 invoice from prepaid credit, within the owner's spending policy.
        Returns {"status": "paid" | "pending_approval" | "refused", "reason": …}."""
        async with self._spend_lock:  # limit check and debit happen together
            if invoice.owner_id != user_id or invoice.purpose != "api_access":
                return {"status": "refused", "reason": "agents only pay their own API invoices"}
            if invoice.status == "paid":
                return {"status": "paid", "reason": "already paid"}
            if invoice.status not in OPEN_INVOICE or self.ctx.clock.now() > invoice.expires_at:
                return {"status": "refused", "reason": f"invoice is {invoice.status}"}
            policy = await self.spending_policy(user_id)
            spent = await self.ctx.store.spent_since(user_id, self.ctx.clock.now() - timedelta(days=1))
            balance = await self.ctx.store.credit_balance(user_id)
            checks = [
                (policy.enabled, "agent payments are switched off"),
                (category in policy.allowed_categories, f"category {category} is not allowed"),
                (invoice.amount <= policy.max_per_request, f"{invoice.amount} is above the per-request limit {policy.max_per_request}"),
                (spent + invoice.amount <= policy.max_daily, f"daily limit {policy.max_daily} reached ({spent} spent in 24 h)"),
                (balance >= invoice.amount, f"agent credit {balance} is less than {invoice.amount}: top up first"),
            ]
            problems = [msg for ok, msg in checks if not ok]
            if problems:
                await self.event("agent_payment_refused", invoice_id=invoice.id, problems=problems)
                return {"status": "refused", "reason": "; ".join(problems)}
            if invoice.amount > policy.approval_above:
                existing = next((r for r in await self.ctx.store.list_spend_requests(user_id, "pending") if r.invoice_id == invoice.id), None)
                request = existing or SpendRequest(id=new_id("SR"), user_id=user_id, invoice_id=invoice.id, category=category, amount=invoice.amount,
                                                   reason=f"above the auto-approve limit {policy.approval_above}", created_at=self.ctx.clock.now())  # fmt: skip
                await self.ctx.store.save_spend_request(request)
                await self.event("agent_payment_needs_approval", invoice_id=invoice.id, request=request.id)
                return {"status": "pending_approval", "reason": request.reason, "request_id": request.id}
            await self._settle_with_credit(user_id, invoice, category)
            return {"status": "paid", "reason": "paid from agent credit"}

    async def decide_spend_request(self, user_id: str, request_id: str, approved: bool) -> dict[str, Any]:
        request = await self.ctx.store.get_spend_request(request_id)
        if request is None or request.user_id != user_id:
            raise PaymentError("unknown request")
        if request.status != "pending":
            raise PaymentError(f"request is {request.status}")
        request.status, request.decided_at = ("approved" if approved else "rejected"), self.ctx.clock.now()
        await self.ctx.store.save_spend_request(request)
        if not approved:
            return {"status": "rejected"}
        invoice = await self.get_invoice(request.invoice_id)
        async with self._spend_lock:
            balance = await self.ctx.store.credit_balance(user_id)
            if balance < invoice.amount:
                return {"status": "refused", "reason": "not enough agent credit"}
            if invoice.status in OPEN_INVOICE:
                await self._settle_with_credit(user_id, invoice, request.category)
        request.status = "paid"
        await self.ctx.store.save_spend_request(request)
        return {"status": "paid"}

    async def _settle_with_credit(self, user_id: str, invoice: Invoice, category: str) -> None:
        """Credit is ZEC the user already paid in through a verified shielded top-up; spending it moves no ZEC."""
        entry = CreditEntry(id=new_id("CR"), user_id=user_id, amount=-invoice.amount, kind="spend", ref=invoice.id, category=category,
                            simulated=invoice.simulated, at=self.ctx.clock.now())  # fmt: skip
        if await self.ctx.store.add_credit(entry):
            invoice.status, invoice.paid_via, invoice.paid_at = "paid", "agent_credit", self.ctx.clock.now()
            invoice.received = invoice.amount
            invoice.updated_at = self.ctx.clock.now()
            await self.ctx.store.save_invoice(invoice)
            await self.event("paid_from_agent_credit", invoice_id=invoice.id, amount=invoice.amount, category=category)

    async def consume(self, invoice_id: str, user_id: str, scope: str) -> Invoice:
        """A paid API invoice unlocks its operation exactly once, for the user it was issued to."""
        invoice = await self.get_invoice(invoice_id)
        if invoice.owner_id != user_id or invoice.purpose != "api_access" or invoice.scope != scope:
            raise PaymentError("this payment is for another request")
        if invoice.status != "paid":
            raise PaymentError(f"payment is {invoice.status}")
        if await self.ctx.store.claim("api_invoice", invoice.id, f"consumed:{invoice.id}") != f"consumed:{invoice.id}" or invoice.consumed_at:
            raise PaymentError("this payment was already used")
        invoice.consumed_at = self.ctx.clock.now()
        await self.ctx.store.save_invoice(invoice)
        await self.event("api_access_unlocked", invoice_id=invoice.id, scope=scope)
        return invoice

    # ---- custody view ------------------------------------------------------------------------------------------

    async def custody(self) -> dict[str, Any]:
        """What the escrow account should hold, next to what the wallet says it holds."""
        info = await self.wallet.info()
        paid = [i for i in await self.ctx.store.list_invoices(status={"paid", "late_payment", "expired"}) if i.paid_via != "agent_credit"]
        received = sum((i.received for i in paid), Decimal("0"))
        payouts = await self.ctx.store.list_payouts()
        sent = sum((p.amount for p in payouts if p.status in ("sent", "confirmed")), Decimal("0"))
        queued = sum((p.amount for p in payouts if p.status in PAYOUT_ACTIVE - {"sent"}), Decimal("0"))
        return {
            "wallet": {
                "backend": info.backend,
                "network": info.network,
                "simulated": info.simulated,
                "reachable": info.reachable,
                "escrow_balance": str(info.escrow_balance) if info.escrow_balance is not None else None,
                "detail": info.detail,
                "warnings": info.warnings,
            },  # fmt: skip
            "received_total": str(received),
            "paid_out_total": str(sent),
            "queued_payouts_total": str(queued),
            "expected_balance_before_fees": str(received - sent),
            "note": "The escrow is a wallet account the Zerveil operator controls. Network fees (ZIP-317) come out of the fee reserve "
            "added to each funding invoice, so the wallet balance is expected to be slightly below the expected balance.",
        }
