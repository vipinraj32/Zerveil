"""Direct shielded Zcash payments: invoices to the recipient's own wallet, verified by a view-only scanner; receipts;
payment links; prepaid API credit with spending limits.

Rules this module keeps:
- Zerveil holds no funds. Every invoice address is a fresh address of the recipient's own wallet account, derived
  from the viewing key the recipient connected. Nothing is routed through, held by or released from Zerveil.
- A payment counts only from scanner data: notes decrypted with the recipient's viewing key, on the invoice's own
  address (or carrying its ZRV1 memo), with enough value and confirmations. Nothing a user types (a txid, a
  screenshot) is evidence, and an unconfirmed payment is never treated as confirmed.
- Each received note is bound to one invoice (`store.claim("zcash_note", txid:pool:index)`), so a payment is never
  counted twice.
- Zerveil cannot refund or forward anything. Late or excess payments are recorded so the parties can settle them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..models import new_id
from ..zcash.address import viewing_key_problem
from ..zcash.memo import memo_for, reference_in
from ..zcash.wallet import ReceivedNote, WalletError
from ..zcash.zip321 import payment_uri
from .models import (
    OPEN_INVOICE,
    PAID_INVOICE,
    CreditEntry,
    Invoice,
    InvoiceNote,
    InvoicePurpose,
    PaymentEvent,
    Receipt,
    ReceivingAccount,
    SpendingPolicy,
    SpendRequest,
)

if TYPE_CHECKING:
    from ..services.context import AppContext

log = logging.getLogger(__name__)

SPENDING_POLICY_KIND = "spending_policy"
API_SELLER = "api-seller"
LATE_WINDOW = timedelta(days=7)  # expired invoices keep being watched this long, so late payments are recorded
# Not paid in full yet and past expiry → expired. Seen/mined/confirming invoices were paid in time and never expire.
EXPIRABLE = frozenset({"awaiting_payment", "underpaid", "failed"})
AUTHORIZATION = (
    "I connect a viewing key for my own Zcash receiving account so that Zerveil can see payments sent to it and confirm "
    "invoices. I understand that a viewing key cannot spend my funds, that it lets Zerveil's scanner see this account's "
    "incoming and outgoing transactions, and that I can disconnect it at any time. Payments go directly to my wallet; "
    "Zerveil never holds them."
)

InvoiceHook = Callable[[Invoice], Awaitable[None]]


class PaymentError(Exception):
    pass


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.strip().encode()).hexdigest()[:16]


class PaymentService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._scan_lock = asyncio.Lock()
        self._spend_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        # purpose → called when an invoice of that purpose changes status (DealService registers "milestone")
        self.on_status: dict[str, InvoiceHook] = {}

    @property
    def scanner(self):  # noqa: ANN201
        return self.ctx.wallet

    async def event(self, event_kind: str, /, *, invoice_id: str | None = None, **detail: Any) -> None:
        await self.ctx.store.add_payment_event(PaymentEvent(at=self.ctx.clock.now(), invoice_id=invoice_id, kind=event_kind,
                                                            detail=json.loads(json.dumps(detail, default=str))))  # fmt: skip

    def _publish(self, payload: dict[str, Any], *users: str) -> None:
        self.ctx.bus.publish(payload, {u for u in users if u and u != API_SELLER})

    # ---- receiving accounts (viewing keys) -------------------------------------------------------------------------

    async def connect_viewing_key(self, owner_id: str, role: str, ufvk: str, birthday: int, *, consent: bool, label: str = "") -> ReceivingAccount:
        """The owner connects a viewing key of their own receiving account. The key goes to the scanner only; the
        database keeps a fingerprint. Replaces (revokes) the owner's previous account."""
        if not consent:
            raise PaymentError("confirm that you authorize Zerveil to watch this receiving account")
        ufvk = ufvk.strip()
        if problem := viewing_key_problem(ufvk, self.ctx.settings.zcash_network):  # type: ignore[arg-type]
            if not (self.scanner.simulated and ufvk.startswith("sim:")):
                raise PaymentError(problem)
        if birthday < 0:
            raise PaymentError("the birthday height cannot be negative")
        async with self._connect_lock:
            label = label or f"zerveil-{owner_id}-{key_fingerprint(ufvk)[:8]}"
            try:
                scanner_account = await self.scanner.import_viewing_key(label, ufvk, birthday)
            except WalletError as e:
                raise PaymentError(f"the scanner could not import this viewing key: {e}") from e
            now = self.ctx.clock.now()
            previous = await self.ctx.store.get_receiving_account(owner_id)
            if previous is not None and previous.scanner_account != scanner_account:
                previous.revoked_at = now
                await self.ctx.store.save_receiving_account(previous)
            account = ReceivingAccount(id=new_id("RA"), owner_id=owner_id, role=role, label=label, backend=self.scanner.name,  # type: ignore[arg-type]
                                       scanner_account=scanner_account, key_fingerprint=key_fingerprint(ufvk), birthday=birthday,
                                       network=self.ctx.settings.zcash_network, authorization=AUTHORIZATION, authorized_at=now,
                                       simulated=self.scanner.simulated)  # fmt: skip
            if previous is not None and previous.scanner_account == scanner_account and previous.active:
                account.id = previous.id
            await self.ctx.store.save_receiving_account(account)
        await self.event("viewing_key_connected", owner=owner_id, role=role, account=account.id, fingerprint=account.key_fingerprint,
                         birthday=birthday, simulated=account.simulated)  # fmt: skip
        return account

    async def receiving_account(self, owner_id: str) -> ReceivingAccount | None:
        account = await self.ctx.store.get_receiving_account(owner_id)
        return account if account is not None and account.active else None

    async def disconnect_viewing_key(self, owner_id: str) -> None:
        account = await self.receiving_account(owner_id)
        if account is None:
            return
        account.revoked_at = self.ctx.clock.now()
        await self.ctx.store.save_receiving_account(account)
        await self.event("viewing_key_disconnected", owner=owner_id, account=account.id)

    async def ensure_api_seller(self) -> ReceivingAccount | None:
        """The API seller's receiving account, from ZCASH_API_SELLER_UFVK (a simulated one in simulated mode)."""
        if (existing := await self.receiving_account(API_SELLER)) is not None:
            return existing
        s = self.ctx.settings
        if s.zcash_api_seller_ufvk:
            return await self.connect_viewing_key(API_SELLER, "api_seller", s.zcash_api_seller_ufvk, s.zcash_api_seller_birthday,
                                                  consent=True, label="zerveil-api-seller")  # fmt: skip
        if self.scanner.simulated:
            return await self.connect_viewing_key(API_SELLER, "api_seller", "sim:api-seller", 0, consent=True, label="zerveil-api-seller")
        return None

    # ---- invoices ----------------------------------------------------------------------------------------------

    async def create_invoice(self, purpose: InvoicePurpose, owner_id: str, recipient_id: str, title: str, amount: Decimal, *,
                             description: str = "", campaign_id: str | None = None, deal_ids: list[str] | None = None,
                             milestone_id: str | None = None, scope: str = "", ttl_minutes: int | None = None) -> Invoice:  # fmt: skip
        if amount <= 0:
            raise PaymentError("the amount must be positive")
        if amount != amount.quantize(Decimal("0.00000001")):
            raise PaymentError("ZEC amounts have at most 8 decimal places")
        account = await self.receiving_account(recipient_id)
        if account is None:
            who = "the API seller has" if recipient_id == API_SELLER else "the recipient has"
            raise PaymentError(f"{who} not connected a receiving account, so a payment could not be confirmed")
        invoice_id = new_id("PAY")
        now = self.ctx.clock.now()
        ttl = ttl_minutes if ttl_minutes is not None else self.ctx.settings.zcash_invoice_ttl_minutes
        try:
            address = await self.scanner.new_address(account.scanner_account)
        except WalletError as e:
            raise PaymentError(f"could not derive a receiving address: {e}") from e
        invoice = Invoice(id=invoice_id, purpose=purpose, owner_id=owner_id, recipient_id=recipient_id, receiving_account_id=account.id,
                          title=title[:120], description=description[:500], amount=amount, network=f"zcash-{self.ctx.settings.zcash_network}",
                          address=address, memo=memo_for(invoice_id), required_confirmations=self.ctx.settings.zcash_confirmation_threshold,
                          campaign_id=campaign_id, deal_ids=deal_ids or [], milestone_id=milestone_id, scope=scope,
                          simulated=self.scanner.simulated, created_at=now, expires_at=now + timedelta(minutes=ttl), updated_at=now)  # fmt: skip
        try:
            await self.ctx.store.save_invoice(invoice)
        except Exception as e:  # noqa: BLE001 - unique violation on the address
            raise PaymentError("the scanner returned an address already used by another invoice") from e
        await self.event("invoice_created", invoice_id=invoice.id, purpose=purpose, amount=amount, recipient=recipient_id, simulated=invoice.simulated)
        return invoice

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
            "direct": True,  # paid straight to the recipient's own wallet
            "zip321_uri": payment_uri(invoice.address, invoice.amount, invoice.memo, invoice.title),
            "pay_url": f"{self.ctx.settings.public_base_url.rstrip('/')}/pay/{invoice.id}",
        }

    async def cancel_invoice(self, invoice: Invoice, by: str) -> Invoice:
        if invoice.status != "awaiting_payment" or invoice.notes:
            raise PaymentError("only an invoice that has received nothing can be cancelled")
        invoice.status = "cancelled"
        invoice.updated_at = self.ctx.clock.now()
        await self.ctx.store.save_invoice(invoice)
        await self.event("invoice_cancelled", invoice_id=invoice.id, by=by)
        await self._notify(invoice)
        return invoice

    # ---- scanning: scanner data → invoice status -------------------------------------------------------------------

    async def scan(self) -> int:
        """Matches notes the watched accounts received to open (and recently expired) invoices. Returns status changes."""
        async with self._scan_lock:
            now = self.ctx.clock.now()
            invoices = await self.ctx.store.list_invoices(status=set(OPEN_INVOICE) | {"failed", "expired"})
            watched = [i for i in invoices if i.status != "expired" or now - i.expires_at < LATE_WINDOW]
            by_account: dict[str, list[Invoice]] = {}
            for i in watched:
                if i.paid_via != "agent_credit" and i.receiving_account_id:
                    by_account.setdefault(i.receiving_account_id, []).append(i)
            changed = 0
            for account_id, group in by_account.items():
                account = await self.ctx.store.get_receiving_account_by_id(account_id)
                if account is None:
                    continue
                try:
                    notes = await self.scanner.received(account.scanner_account, [i.address for i in group])
                except WalletError as e:
                    log.warning("payment scan of %s skipped: %s", account_id, e)
                    continue
                by_address: dict[str, list[ReceivedNote]] = {}
                by_memo: dict[str, list[ReceivedNote]] = {}
                for n in notes:
                    by_address.setdefault(n.address, []).append(n)
                    if ref := reference_in(n.memo):
                        by_memo.setdefault(ref, []).append(n)
                for invoice in group:
                    mine = {n.key: n for n in by_address.get(invoice.address, []) + by_memo.get(invoice.id, [])}
                    # Expiry is decided here too, only after a successful scan: a scanner outage never expires an
                    # invoice that may have been paid in time
                    if await self._apply(invoice, list(mine.values()), now):
                        changed += 1
            return changed

    @staticmethod
    def _status_for(invoice: Invoice) -> str:
        live = [n for n in invoice.notes if not n.expired]
        if invoice.received >= invoice.amount:
            confs = min((n.confirmations for n in live), default=0)
            if confs >= invoice.required_confirmations:
                return "overpaid" if invoice.received > invoice.amount else "confirmed"
            if confs == 0:
                return "seen"
            return "mined" if confs == 1 else "confirming"
        if invoice.received > 0:
            return "underpaid"
        if invoice.notes:  # every payment attempt expired unmined
            return "failed"
        return "awaiting_payment"

    async def _apply(self, invoice: Invoice, notes: list[ReceivedNote], now: datetime) -> bool:
        before = (invoice.status, invoice.received, invoice.confirmations)
        known = {n.key: n for n in invoice.notes}
        for note in notes:
            if note.key in known:
                known[note.key].confirmations = note.confirmations
                known[note.key].mined_height = note.mined_height
                known[note.key].expired = note.expired and note.confirmations == 0
                continue
            owner = await self.ctx.store.claim("zcash_note", note.key, invoice.id)
            if owner != invoice.id:  # already counted for another invoice: never twice
                await self.event("note_already_counted", invoice_id=invoice.id, note=note.key, counted_for=owner)
                continue
            matched = reference_in(note.memo) == invoice.id
            known[note.key] = InvoiceNote(key=note.key, txid=note.txid, pool=note.pool, output_index=note.output_index, amount=note.amount,
                                          confirmations=note.confirmations, mined_height=note.mined_height,
                                          expired=note.expired and note.confirmations == 0, memo_matched=matched, first_seen_at=now)  # fmt: skip
            await self.event("note_detected", invoice_id=invoice.id, amount=note.amount, pool=note.pool, confirmations=note.confirmations,
                             memo_matched=matched, txid=note.txid, simulated=invoice.simulated)  # fmt: skip
        invoice.notes = list(known.values())
        live = [n for n in invoice.notes if not n.expired]
        invoice.received = sum((n.amount for n in live), Decimal("0"))
        invoice.confirmations = min((n.confirmations for n in live), default=0)

        if invoice.status == "expired":
            if invoice.received != before[1] and invoice.received > 0:
                await self.event("late_payment", invoice_id=invoice.id, received=invoice.received,
                                 note="paid after expiry: the money is in the recipient's wallet; the parties settle it between them")  # fmt: skip
        elif invoice.status not in PAID_INVOICE | {"cancelled"}:
            invoice.status = self._status_for(invoice)  # type: ignore[assignment]
            if invoice.status in EXPIRABLE and now > invoice.expires_at:
                invoice.status = "expired"
            if invoice.status in PAID_INVOICE:
                invoice.paid_at, invoice.paid_via = now, "shielded"
        if (invoice.status, invoice.received, invoice.confirmations) == before:
            return False
        invoice.updated_at = now
        if invoice.status in PAID_INVOICE and invoice.receipt_id is None:
            invoice.receipt_id = (await self._issue_receipt(invoice)).id
        await self.ctx.store.save_invoice(invoice)
        if invoice.status != before[0]:
            await self.event("invoice_status", invoice_id=invoice.id, status=invoice.status, received=invoice.received,
                             confirmations=invoice.confirmations)  # fmt: skip
            if invoice.status == "overpaid":
                await self.event("overpaid", invoice_id=invoice.id, excess=invoice.received - invoice.amount)
            await self._notify(invoice)
        else:
            self._publish({"type": "invoice", "invoice_id": invoice.id, "status": invoice.status}, invoice.owner_id, invoice.recipient_id)
        return invoice.status != before[0]

    async def _notify(self, invoice: Invoice) -> None:
        self._publish({"type": "invoice", "invoice_id": invoice.id, "status": invoice.status}, invoice.owner_id, invoice.recipient_id)
        hook = self.on_status.get(invoice.purpose) or {"credit_topup": self._credit_topup}.get(invoice.purpose)
        if hook is not None:
            await hook(invoice)

    # ---- receipts -------------------------------------------------------------------------------------------------

    async def _issue_receipt(self, invoice: Invoice) -> Receipt:
        receipt_id = new_id("RCPT")
        owner = await self.ctx.store.claim("receipt", invoice.id, receipt_id)
        if owner != receipt_id and (existing := await self.ctx.store.get_receipt(owner)) is not None:
            return existing
        live = [n for n in invoice.notes if not n.expired]
        payer = "" if invoice.purpose == "payment_link" else invoice.owner_id
        receipt = Receipt(id=receipt_id, invoice_id=invoice.id, purpose=invoice.purpose, title=invoice.title, payer_id=payer,
                          recipient_id=invoice.recipient_id, amount=invoice.amount, received=invoice.received, network=invoice.network,
                          pools=sorted({n.pool for n in live}), txids=sorted({n.txid for n in live}), confirmations=invoice.confirmations,
                          mined_height=max((n.mined_height or 0 for n in live), default=0) or None, memo=invoice.memo,
                          campaign_id=invoice.campaign_id, deal_id=invoice.deal_ids[0] if invoice.deal_ids else None,
                          milestone_id=invoice.milestone_id, simulated=invoice.simulated, issued_at=self.ctx.clock.now())  # fmt: skip
        receipt.receipt_hash = receipt.compute_hash()
        await self.ctx.store.save_receipt(receipt)
        await self.event("receipt_issued", invoice_id=invoice.id, receipt=receipt.id, receipt_hash=receipt.receipt_hash)
        return receipt

    async def get_receipt(self, receipt_id: str) -> Receipt:
        receipt = await self.ctx.store.get_receipt(receipt_id)
        if receipt is None:
            raise PaymentError(f"unknown receipt {receipt_id}")
        return receipt

    # ---- simulated mode only -----------------------------------------------------------------------------------

    async def simulate_payment(self, invoice: Invoice, amount: Decimal | None = None, confirmations: int | None = None) -> Invoice:
        """Development helper: the simulated scanner "receives" a payment, then the normal scan verifies it."""
        from ..zcash.simulated import SimulatedScanner

        if not isinstance(self.scanner, SimulatedScanner):
            raise PaymentError("payments can only be simulated with PAYMENT_MODE=simulated")
        confs = confirmations if confirmations is not None else invoice.required_confirmations
        self.scanner.receive(invoice.address, amount if amount is not None else invoice.amount - invoice.received, invoice.memo, confs)
        await self.scan()
        return await self.get_invoice(invoice.id)

    # ---- payment links -------------------------------------------------------------------------------------------

    async def create_payment_link(self, creator_id: str, title: str, amount: Decimal, description: str = "", ttl_days: int = 14) -> Invoice:
        """A shareable request to pay the creator directly; anyone can pay it from their own wallet."""
        if await self.receiving_account(creator_id) is None:
            raise PaymentError("connect your receiving account's viewing key first: Zerveil confirms link payments with it")
        return await self.create_invoice("payment_link", creator_id, creator_id, title, amount, description=description,
                                         ttl_minutes=ttl_days * 24 * 60)  # fmt: skip

    # ---- prepaid API credit and spending limits -------------------------------------------------------------------

    async def spending_policy(self, user_id: str) -> SpendingPolicy:
        doc = await self.ctx.store.get_version(SPENDING_POLICY_KIND, user_id)
        return SpendingPolicy.model_validate(doc) if doc else SpendingPolicy()

    async def save_spending_policy(self, user_id: str, policy: SpendingPolicy) -> dict[str, Any]:
        return await self.ctx.store.save_version(SPENDING_POLICY_KIND, user_id, policy.model_dump(mode="json"))

    async def create_topup(self, user_id: str, amount: Decimal) -> Invoice:
        """Prepays API calls: paid directly to the API seller's wallet."""
        return await self.create_invoice("credit_topup", user_id, API_SELLER, "API credit top-up", amount,
                                         ttl_minutes=self.ctx.settings.api_invoice_ttl_minutes * 4)  # fmt: skip

    async def _credit_topup(self, invoice: Invoice) -> None:
        if not invoice.is_paid:
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
        """The advertiser's agent pays a 402 invoice from prepaid API credit, within the owner's spending policy.
        Returns {"status": "paid" | "pending_approval" | "refused", "reason": …}."""
        async with self._spend_lock:  # limit check and debit happen together
            if invoice.owner_id != user_id or invoice.purpose != "api_access":
                return {"status": "refused", "reason": "agents only pay their own API invoices"}
            if invoice.is_paid:
                return {"status": "paid", "reason": "already paid"}
            if invoice.status != "awaiting_payment" or self.ctx.clock.now() > invoice.expires_at:
                return {"status": "refused", "reason": f"invoice is {invoice.status}"}
            policy = await self.spending_policy(user_id)
            spent = await self.ctx.store.spent_since(user_id, self.ctx.clock.now() - timedelta(days=1))
            balance = await self.ctx.store.credit_balance(user_id)
            checks = [
                (policy.enabled, "agent payments are switched off"),
                (category in policy.allowed_categories, f"category {category} is not allowed"),
                (invoice.amount <= policy.max_per_request, f"{invoice.amount} is above the per-request limit {policy.max_per_request}"),
                (spent + invoice.amount <= policy.max_daily, f"daily limit {policy.max_daily} reached ({spent} spent in 24 h)"),
                (balance >= invoice.amount, f"API credit {balance} is less than {invoice.amount}: top up first"),
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
            return {"status": "paid", "reason": "paid from API credit"}

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
                return {"status": "refused", "reason": "not enough API credit"}
            if invoice.status == "awaiting_payment":
                await self._settle_with_credit(user_id, invoice, request.category)
        request.status = "paid"
        await self.ctx.store.save_spend_request(request)
        return {"status": "paid"}

    async def _settle_with_credit(self, user_id: str, invoice: Invoice, category: str) -> None:
        """Credit was already paid to the API seller through a confirmed shielded top-up; spending it moves no ZEC."""
        entry = CreditEntry(id=new_id("CR"), user_id=user_id, amount=-invoice.amount, kind="spend", ref=invoice.id, category=category,
                            simulated=invoice.simulated, at=self.ctx.clock.now())  # fmt: skip
        if await self.ctx.store.add_credit(entry):
            invoice.status, invoice.paid_via, invoice.paid_at = "confirmed", "agent_credit", self.ctx.clock.now()
            invoice.received = invoice.amount
            invoice.updated_at = self.ctx.clock.now()
            await self.ctx.store.save_invoice(invoice)
            await self.event("paid_from_api_credit", invoice_id=invoice.id, amount=invoice.amount, category=category)

    async def consume(self, invoice_id: str, user_id: str, scope: str) -> Invoice:
        """A paid API invoice unlocks its operation exactly once, for the user it was issued to."""
        invoice = await self.get_invoice(invoice_id)
        if invoice.owner_id != user_id or invoice.purpose != "api_access" or invoice.scope != scope:
            raise PaymentError("this payment is for another request")
        if not invoice.is_paid:
            raise PaymentError(f"payment is {invoice.status}")
        if await self.ctx.store.claim("api_invoice", invoice.id, f"consumed:{invoice.id}") != f"consumed:{invoice.id}" or invoice.consumed_at:
            raise PaymentError("this payment was already used")
        invoice.consumed_at = self.ctx.clock.now()
        await self.ctx.store.save_invoice(invoice)
        await self.event("api_access_unlocked", invoice_id=invoice.id, scope=scope)
        return invoice

    # ---- operator view ---------------------------------------------------------------------------------------------

    async def scanner_status(self) -> dict[str, Any]:
        """Scanner health and what it watches. Zerveil holds no funds, so there is no balance to reconcile."""
        info = await self.scanner.info()
        watching = await self.ctx.store.list_invoices(status=set(OPEN_INVOICE))
        return {
            "scanner": {
                "backend": info.backend,
                "network": info.network,
                "simulated": info.simulated,
                "reachable": info.reachable,
                "server": info.server,
                "chain_name": info.chain_name,
                "tip_height": info.tip_height,
                "nu7_activation_height": info.nu7_activation_height,
                "nu7_active": info.nu7_active,
                "detail": info.detail,
                "warnings": info.warnings,
            },
            "receiving_accounts": len([a for a in await self.ctx.store.list_receiving_accounts() if a.active]),
            "open_invoices": len(watching),
            "custody": "none: payments go directly to each recipient's own wallet",
        }
