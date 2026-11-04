"""Direct Zcash payment endpoints. Zerveil holds no funds: every invoice is paid straight to the recipient's own wallet
and confirmed by the view-only scanner. Amounts users enter (links, top-ups) and every business total are USD; each
invoice also carries the asset amount to pay (shielded ZEC, TAZ on testnet) and the rate it was converted at.

GET  /api/pay/{id}                          public payment page data (no transaction ids, no campaign terms)
GET  /api/payments/invoices[/{id}]          invoices the caller pays or receives; one with its notes and audit trail
POST /api/payments/invoices/{id}/cancel     an invoice that received nothing
POST /api/payments/links                    creator payment link (paid directly to the creator's own wallet)
GET|PUT|DELETE /api/receiving-account       the creator's viewing-key connection (watch-only, cannot spend)
GET  /api/receipts[/{id}]                   receipts of confirmed payments the caller made or received
GET  /api/campaigns/{id}/payments           a campaign's milestone invoices (+ one ZIP-321 request for the open ones)
GET  /api/wallet/status                     network, scanner, chain tip, NU7 status, faucet link; never credentials
GET|PUT /api/agent/spending-policy          the advertiser agent's spending limits
GET  /api/agent/credit                      prepaid API credit, ledger and pending approvals
POST /api/agent/credit/topup                a top-up invoice, paid directly to the API seller's wallet
POST /api/agent/spend-requests/{id}         approve or reject an agent payment above the auto-approve limit
GET  /api/influencers/premium-search        HTTP 402: pay the API seller's invoice, retry with X-Payment-Id
POST /api/agent/premium-search              the advertiser's agent pays the 402 from credit, within its limits
/api/admin/...                              payment events, invoices, scanner status (operators cannot move funds)
POST /api/dev/payments/{id}/simulate        PAYMENT_MODE=simulated only: the stand-in scanner "receives" a payment
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..models import Campaign, DealState, User
from ..payments.models import OPEN_INVOICE, Invoice, Receipt, SpendingPolicy
from ..payments.service import API_SELLER, AUTHORIZATION, PaymentError
from ..zcash.zip321 import multi_payment_uri

if TYPE_CHECKING:
    from .app import State

PREMIUM_CATEGORY = "premium_search"


class LinkIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    amount: Decimal = Field(gt=0, le=10_000, decimal_places=2)  # USD
    description: str = Field(default="", max_length=500)


class TopupIn(BaseModel):
    amount: Decimal = Field(gt=0, le=1_000, decimal_places=2)  # USD


class ViewingKeyIn(BaseModel):
    ufvk: str = Field(min_length=10, max_length=2000)  # Unified Full Viewing Key of the creator's receiving account
    birthday: int = Field(ge=0, le=100_000_000)  # block height the account was created at (scanning starts there)
    consent: bool  # the creator agrees to the authorization statement


class SpendDecisionIn(BaseModel):
    approved: bool


class SimulateIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, decimal_places=8)
    confirmations: int | None = Field(default=None, ge=0, le=100)


class AgentSearchIn(BaseModel):
    niche: str = Field(min_length=2, max_length=40)


def register_payment_routes(
    app: FastAPI,
    state: "State",
    *,
    principal: Callable[..., Awaitable[User]],
    advertiser: Callable[..., Awaitable[User]],
    is_admin: Callable[[User], bool],
    readable_campaign: Callable[[str, User | str], Awaitable[Campaign]],
) -> None:
    CurrentUser = Annotated[User, Depends(principal)]
    AdvertiserUser = Annotated[User, Depends(advertiser)]

    def payments():  # noqa: ANN202
        return state.deals.payments

    async def admin(user: CurrentUser) -> User:
        if not is_admin(user):
            raise HTTPException(403, "operators only (ADMIN_EMAILS)")
        return user

    AdminUser = Annotated[User, Depends(admin)]

    def party_view(invoice: Invoice) -> dict[str, Any]:
        """The payer's and recipient's view: adds the notes the scanner detected, with transaction ids."""
        return {**payments().payment_request(invoice), "notes": [n.model_dump(mode="json") for n in invoice.notes], "campaign_id": invoice.campaign_id,
                "deal_ids": invoice.deal_ids, "milestone_id": invoice.milestone_id, "recipient_id": invoice.recipient_id, "scope": invoice.scope,
                "paid_via": invoice.paid_via, "consumed_at": invoice.consumed_at, "receipt_id": invoice.receipt_id}  # fmt: skip

    def is_party(invoice: Invoice, user: User) -> bool:
        return user.id in (invoice.owner_id, invoice.recipient_id) or is_admin(user)

    async def party_invoice(invoice_id: str, user: User) -> Invoice:
        try:
            invoice = await payments().get_invoice(invoice_id)
        except PaymentError as e:
            raise HTTPException(404, "payment not found") from e
        if not is_party(invoice, user):
            raise HTTPException(404, "payment not found")
        return invoice

    # ---- public payment page ---------------------------------------------------------------------------------

    @app.get("/api/pay/{invoice_id}")
    async def public_payment(invoice_id: str) -> dict[str, Any]:
        """Anyone with the link sees what to pay and whether it arrived, never transaction ids or campaign details."""
        try:
            invoice = await payments().get_invoice(invoice_id)
        except PaymentError as e:
            raise HTTPException(404, "payment not found") from e
        out = payments().payment_request(invoice)
        if invoice.purpose == "payment_link" and (creator := await state.ctx.store.get_user(invoice.recipient_id or invoice.owner_id)):
            out["payee_name"] = creator.channel.title if creator.channel else creator.name
        return out

    # ---- invoices of the signed-in user ------------------------------------------------------------------------

    @app.get("/api/payments/invoices")
    async def my_invoices(user: CurrentUser) -> list[dict[str, Any]]:
        mine = {i.id: i for i in await state.ctx.store.list_invoices(owner_id=user.id)}
        for i in await state.ctx.store.list_invoices():
            if i.recipient_id == user.id:
                mine[i.id] = i
        return [party_view(i) for i in sorted(mine.values(), key=lambda i: i.created_at, reverse=True)]

    @app.get("/api/payments/invoices/{invoice_id}")
    async def my_invoice(invoice_id: str, user: CurrentUser) -> dict[str, Any]:
        invoice = await party_invoice(invoice_id, user)
        events = await state.ctx.store.list_payment_events(invoice_id=invoice.id)
        return {**party_view(invoice), "events": [e.model_dump(mode="json") for e in events]}

    @app.post("/api/payments/invoices/{invoice_id}/cancel")
    async def cancel_invoice(invoice_id: str, user: CurrentUser) -> dict[str, Any]:
        invoice = await party_invoice(invoice_id, user)
        if invoice.purpose == "milestone":
            raise HTTPException(409, "milestone invoices expire on their own; their deals are then closed")
        if invoice.owner_id != user.id:
            raise HTTPException(403, "only the invoice's owner can cancel it")
        try:
            return party_view(await payments().cancel_invoice(invoice, user.id))
        except PaymentError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/payments/links", status_code=201)
    async def create_link(body: LinkIn, user: CurrentUser) -> dict[str, Any]:
        if user.role != "influencer":
            raise HTTPException(403, "payment links are for creators")
        try:
            return party_view(await payments().create_payment_link(user.id, body.title, body.amount, body.description))
        except PaymentError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/campaigns/{campaign_id}/payments")
    async def campaign_payments(campaign_id: str, user: AdvertiserUser) -> dict[str, Any]:
        """The campaign's milestone invoices. Each pays one creator's own wallet; `pay_all_uri` is one ZIP-321 request
        covering every open one (wallets that support multi-payment requests pay them in one transaction)."""
        await readable_campaign(campaign_id, user)
        invoices = await state.ctx.store.list_invoices(campaign_id=campaign_id, purpose="milestone")
        open_ = [i for i in invoices if i.status == "awaiting_payment" and not i.notes]
        uri = multi_payment_uri([(i.address, i.amount, i.memo, i.title) for i in open_]) if open_ else None
        assets = sorted({i.asset for i in open_ if i.asset})
        return {"invoices": [party_view(i) for i in invoices], "pay_all_uri": uri, "currency": "USD",
                "open_total_usd": str(sum((i.usd_amount or Decimal("0") for i in open_), Decimal("0"))),
                "open_total": str(sum((i.amount for i in open_), Decimal("0"))),  # asset amount to pay for all open invoices
                "open_asset": assets[0] if len(assets) == 1 else None,
                "summary": await state.deals.payment_summary(campaign_id)}  # fmt: skip

    # ---- receiving account (viewing key) -----------------------------------------------------------------------

    def account_view(user: User, account: Any) -> dict[str, Any]:
        return {
            "connected": account is not None,
            "authorization": AUTHORIZATION,
            "your_address": user.zcash_address,
            "account": None if account is None else account.model_dump(mode="json", include={"id", "backend", "key_fingerprint", "birthday", "network",
                                                                                               "authorized_at", "simulated"}),
        }  # fmt: skip

    @app.get("/api/receiving-account")
    async def get_receiving_account(user: CurrentUser) -> dict[str, Any]:
        return account_view(user, await payments().receiving_account(user.id))

    @app.put("/api/receiving-account")
    async def connect_receiving_account(body: ViewingKeyIn, user: CurrentUser) -> dict[str, Any]:
        """The creator connects a viewing key of their own receiving account. Never a seed phrase or spending key:
        those are refused. The key is handed to the scanner; the database keeps a fingerprint."""
        if user.role != "influencer":
            raise HTTPException(403, "receiving accounts are for creators: advertisers pay from their own wallet")
        try:
            account = await payments().connect_viewing_key(user.id, "creator", body.ufvk, body.birthday, consent=body.consent)
        except PaymentError as e:
            raise HTTPException(400, str(e)) from e
        user.receiving_account_id = account.id
        await state.ctx.store.save_user(user)
        return account_view(user, account)

    @app.delete("/api/receiving-account")
    async def disconnect_receiving_account(user: CurrentUser) -> dict[str, Any]:
        await payments().disconnect_viewing_key(user.id)
        user.receiving_account_id = ""
        await state.ctx.store.save_user(user)
        return account_view(user, None)

    # ---- receipts ------------------------------------------------------------------------------------------------

    def receipt_view(receipt: Receipt) -> dict[str, Any]:
        out = receipt.model_dump(mode="json")
        out["explorer_urls"] = [state.settings.zcash_explorer_tx_url.format(txid=t) for t in receipt.txids] if state.settings.zcash_explorer_tx_url else []
        return out

    @app.get("/api/receipts")
    async def my_receipts(user: CurrentUser) -> list[dict[str, Any]]:
        return [receipt_view(r) for r in await state.ctx.store.list_receipts(user.id)]

    @app.get("/api/receipts/{receipt_id}")
    async def get_receipt(receipt_id: str, user: CurrentUser) -> dict[str, Any]:
        try:
            receipt = await payments().get_receipt(receipt_id)
        except PaymentError as e:
            raise HTTPException(404, "receipt not found") from e
        if user.id not in (receipt.payer_id, receipt.recipient_id) and not is_admin(user):
            raise HTTPException(404, "receipt not found")
        return receipt_view(receipt)

    # ---- network and scanner status ----------------------------------------------------------------------------

    @app.get("/api/wallet/status")
    async def wallet_status(user: CurrentUser) -> dict[str, Any]:
        info = await state.ctx.wallet.info()
        account = await payments().receiving_account(user.id)
        mine = [i for i in await state.ctx.store.list_invoices() if i.recipient_id == user.id and i.is_paid and i.paid_via == "shielded"]
        received = sum((i.received for i in mine), Decimal("0"))
        received_usd = sum((i.usd_amount for i in mine if i.usd_amount is not None), Decimal("0"))
        return {
            "backend": info.backend,
            "network": info.network,
            "simulated": info.simulated,
            "reachable": info.reachable,
            "tip_height": info.tip_height,
            "nu7_activation_height": info.nu7_activation_height,
            "nu7_active": info.nu7_active,
            "warnings": info.warnings,
            "detail": info.detail if is_admin(user) else "",
            "faucet_url": state.settings.zcash_faucet_url if state.settings.zcash_network == "testnet" else None,
            "your_address": user.zcash_address,
            "receiving_account_connected": account is not None,
            "confirmed_received_via_zerveil": str(received),  # asset amount the scanner confirmed in your wallet (not a wallet balance)
            "confirmed_received_usd": str(received_usd),  # the USD value of those invoices (USD-priced ones only)
            "asset": state.settings.ticker,
            "custody": "none: Zerveil never holds funds",
        }

    # ---- prepaid API credit and spending limits -----------------------------------------------------------------

    @app.get("/api/agent/spending-policy")
    async def get_spending_policy(user: AdvertiserUser) -> dict[str, Any]:
        return (await payments().spending_policy(user.id)).model_dump(mode="json")

    @app.put("/api/agent/spending-policy")
    async def put_spending_policy(body: SpendingPolicy, user: AdvertiserUser) -> dict[str, Any]:
        return await payments().save_spending_policy(user.id, body)

    @app.get("/api/agent/credit")
    async def credit(user: AdvertiserUser) -> dict[str, Any]:
        return await payments().credit_summary(user.id)

    @app.post("/api/agent/credit/topup", status_code=201)
    async def topup(body: TopupIn, user: AdvertiserUser) -> dict[str, Any]:
        try:
            return party_view(await payments().create_topup(user.id, body.amount))
        except PaymentError as e:
            raise HTTPException(503, str(e)) from e

    @app.post("/api/agent/spend-requests/{request_id}")
    async def decide_spend(request_id: str, body: SpendDecisionIn, user: AdvertiserUser) -> dict[str, Any]:
        try:
            return await payments().decide_spend_request(user.id, request_id, body.approved)
        except PaymentError as e:
            raise HTTPException(409, str(e)) from e

    # ---- HTTP 402: premium creator search ----------------------------------------------------------------------

    async def premium_results(niche: str) -> list[dict[str, Any]]:
        """Listed creators in a niche with their verified channel statistics and Zerveil delivery record."""
        rows = []
        all_deals = await state.ctx.store.list_deals()
        for reg in await state.ctx.registry.all():
            p = reg.profile
            if niche.lower() not in p.all_niches:
                continue
            user = await state.ctx.store.get_user(p.id)
            ch = user.channel if user else None
            mine = [d for d in all_deals if d.influencer_id == p.id]
            completed = [d for d in mine if d.state == DealState.COMPLETED]
            first_try = [d for d in completed if d.proof and d.proof.attempt == 1]
            rows.append({
                "id": p.id, "name": p.name, "channel_id": p.channel_id, "niches": p.all_niches, "subscribers": p.subscribers,
                "channel_views": ch.view_count if ch else None, "videos": ch.video_count if ch else None,
                "stats_verified_at": ch.verified_at.isoformat() if ch else None,
                "zerveil_deals_completed": len(completed), "proofs_accepted_first_try": len(first_try), "agent_url": reg.agent_url,
            })  # fmt: skip
        return sorted(rows, key=lambda r: (-r["zerveil_deals_completed"], -r["subscribers"]))

    def payment_required(invoice: Invoice) -> JSONResponse:
        req = payments().payment_request(invoice)
        keys = ("id", "currency", "usd_amount", "asset", "usd_rate", "rate_source", "test_rate", "network", "ticker", "amount", "address",
                "memo", "zip321_uri", "pay_url", "expires_at", "status", "confirmations", "required_confirmations", "simulated", "direct")  # fmt: skip
        body = {
            "error": "payment_required",
            "detail": "Pay this shielded Zcash invoice (it pays the API seller's own wallet directly), then repeat the request with the header "
            "X-Payment-Id. Access unlocks only after the scanner sees the payment confirmed; a transaction id is not accepted as proof.",
            "payment": {k: req[k] for k in keys},
            "retry_with_header": {"X-Payment-Id": invoice.id},
        }
        return JSONResponse(body, status_code=402, headers={"X-Payment-Id": invoice.id})

    async def premium_invoice(user: User, scope: str) -> Invoice:
        try:
            return await payments().create_invoice("api_access", user.id, API_SELLER, "Zerveil premium creator search",
                                                   state.settings.premium_search_price, scope=scope,
                                                   ttl_minutes=state.settings.api_invoice_ttl_minutes)  # fmt: skip
        except PaymentError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/api/influencers/premium-search", responses={402: {"description": "Payment required: a shielded Zcash invoice"}})
    async def premium_search(user: AdvertiserUser, niche: str = Query(min_length=2, max_length=40),
                             x_payment_id: Annotated[str | None, Header()] = None) -> Any:  # fmt: skip
        scope = f"{PREMIUM_CATEGORY}:{niche.lower()}"
        if x_payment_id:
            invoice = await state.ctx.store.get_invoice(x_payment_id)
            if invoice is None or invoice.owner_id != user.id or invoice.scope != scope:
                raise HTTPException(400, "X-Payment-Id does not belong to this request")
            if not invoice.is_paid:
                if invoice.status in OPEN_INVOICE:
                    return payment_required(invoice)  # not paid (or not confirmed) yet: same invoice again
                raise HTTPException(402, f"payment {invoice.id} is {invoice.status}: request a new one without X-Payment-Id")
            try:
                await payments().consume(invoice.id, user.id, scope)
            except PaymentError as e:
                raise HTTPException(409, str(e)) from e
            return {"niche": niche.lower(), "paid_with": invoice.id, "results": await premium_results(niche)}
        return payment_required(await premium_invoice(user, scope))

    @app.post("/api/agent/premium-search")
    async def agent_premium_search(body: AgentSearchIn, user: AdvertiserUser) -> dict[str, Any]:
        """The advertiser's agent runs the 402 flow itself: gets the invoice, pays it from prepaid API credit if the
        spending policy allows (otherwise asks its owner), then unlocks the results."""
        scope = f"{PREMIUM_CATEGORY}:{body.niche.lower()}"
        invoice = await premium_invoice(user, scope)
        decision = await payments().agent_pay(user.id, invoice, PREMIUM_CATEGORY)
        out: dict[str, Any] = {"invoice": party_view(await payments().get_invoice(invoice.id)), "payment": decision}
        if decision["status"] == "paid":
            await payments().consume(invoice.id, user.id, scope)
            out["results"] = await premium_results(body.niche)
        return out

    # ---- operators: read-only payment views -------------------------------------------------------------------

    @app.get("/api/admin/scanner")
    async def admin_scanner(_: AdminUser) -> dict[str, Any]:
        return await payments().scanner_status()

    @app.get("/api/admin/payment-events")
    async def admin_events(_: AdminUser, invoice_id: str | None = None) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in await state.ctx.store.list_payment_events(invoice_id=invoice_id)]

    @app.get("/api/admin/invoices")
    async def admin_invoices(_: AdminUser) -> list[dict[str, Any]]:
        return [party_view(i) for i in await state.ctx.store.list_invoices()]

    # ---- development: simulated payments ---------------------------------------------------------------------

    @app.post("/api/dev/payments/{invoice_id}/simulate")
    async def simulate(invoice_id: str, body: SimulateIn, user: CurrentUser) -> dict[str, Any]:
        """PAYMENT_MODE=simulated only. The stand-in scanner receives the payment; the normal scan then verifies it
        exactly as it would real scanner data. The result is labeled simulated everywhere."""
        if not state.ctx.wallet.simulated:
            raise HTTPException(400, "only available with PAYMENT_MODE=simulated: pay with a real Zcash wallet instead")
        invoice = await payments().get_invoice(invoice_id)
        if invoice.owner_id != user.id and not is_admin(user) and invoice.purpose != "payment_link":
            raise HTTPException(404, "payment not found")
        invoice = await payments().simulate_payment(invoice, body.amount, body.confirmations)
        await state.deals.tick()
        return payments().payment_request(await payments().get_invoice(invoice.id))
