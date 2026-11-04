"""Zcash payment endpoints.

GET  /api/pay/{id}                          public payment page data (no transaction ids, no campaign terms)
GET  /api/payments/invoices[/{id}]          the caller's invoices; one with its detected notes and audit trail
POST /api/payments/invoices/{id}/cancel     an invoice that received nothing
POST /api/payments/links                    creator payment link (paid in → forwarded to the creator's address)
GET  /api/payments/payouts                  payouts to the caller
GET  /api/wallet/status                     network, backend, reachability (balance for admins only)
GET|PUT /api/agent/spending-policy          the advertiser agent's spending limits
GET  /api/agent/credit                      prepaid agent credit, ledger and pending approvals
POST /api/agent/credit/topup                a shielded top-up invoice
POST /api/agent/spend-requests/{id}         approve or reject an agent payment above the auto-approve limit
GET  /api/influencers/premium-search        HTTP 402: pay a shielded invoice, retry with X-Payment-Id
POST /api/agent/premium-search              the advertiser's agent pays the 402 from credit, within its limits
/api/admin/...                              payout approval queue, unknown-outcome resolution, custody view
POST /api/dev/payments/{id}/simulate        PAYMENT_MODE=simulated only: the in-process wallet "receives" a payment
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..models import Campaign, DealState, User
from ..payments.models import Invoice, SpendingPolicy
from ..payments.service import PaymentError

if TYPE_CHECKING:
    from .app import State

PREMIUM_CATEGORY = "premium_search"


class LinkIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    amount: Decimal = Field(gt=0, le=10_000, decimal_places=8)
    description: str = Field(default="", max_length=500)


class TopupIn(BaseModel):
    amount: Decimal = Field(gt=0, le=1_000, decimal_places=8)


class PayoutDecisionIn(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=300)


class ResolveIn(BaseModel):
    action: Literal["resend", "cancel"]
    note: str = Field(min_length=3, max_length=300)  # what the operator checked in the wallet


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
            raise HTTPException(403, "payout operators only (ADMIN_EMAILS)")
        return user

    AdminUser = Annotated[User, Depends(admin)]

    def owner_view(invoice: Invoice) -> dict[str, Any]:
        """The payer's (or link owner's) own view: adds the notes the wallet detected, with transaction ids."""
        return {**payments().payment_request(invoice), "notes": [n.model_dump(mode="json") for n in invoice.notes], "campaign_id": invoice.campaign_id,
                "deal_ids": invoice.deal_ids, "scope": invoice.scope, "paid_via": invoice.paid_via, "consumed_at": invoice.consumed_at}  # fmt: skip

    async def own_invoice(invoice_id: str, user: User) -> Invoice:
        invoice = await payments().get_invoice(invoice_id)
        if invoice.owner_id != user.id and not is_admin(user):
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
        if invoice.purpose == "payment_link" and (creator := await state.ctx.store.get_user(invoice.owner_id)):
            out["payee_name"] = creator.channel.title if creator.channel else creator.name
        return out

    # ---- invoices and payouts of the signed-in user -----------------------------------------------------------

    @app.get("/api/payments/invoices")
    async def my_invoices(user: CurrentUser) -> list[dict[str, Any]]:
        return [owner_view(i) for i in await state.ctx.store.list_invoices(owner_id=user.id)]

    @app.get("/api/payments/invoices/{invoice_id}")
    async def my_invoice(invoice_id: str, user: CurrentUser) -> dict[str, Any]:
        invoice = await own_invoice(invoice_id, user)
        events = await state.ctx.store.list_payment_events(invoice_id=invoice.id)
        return {**owner_view(invoice), "events": [e.model_dump(mode="json") for e in events]}

    @app.post("/api/payments/invoices/{invoice_id}/cancel")
    async def cancel_invoice(invoice_id: str, user: CurrentUser) -> dict[str, Any]:
        invoice = await own_invoice(invoice_id, user)
        if invoice.purpose == "campaign_funding":
            raise HTTPException(409, "funding invoices expire on their own; their deals are then closed")
        return owner_view(await payments().cancel_invoice(invoice, user.id))

    @app.post("/api/payments/links", status_code=201)
    async def create_link(body: LinkIn, user: CurrentUser) -> dict[str, Any]:
        if user.role != "influencer":
            raise HTTPException(403, "payment links are for creators")
        return owner_view(await payments().create_payment_link(user.id, body.title, body.amount, body.description))

    @app.get("/api/payments/payouts")
    async def my_payouts(user: CurrentUser) -> list[dict[str, Any]]:
        return [p.model_dump(mode="json", exclude={"evidence"}) for p in await state.ctx.store.list_payouts(beneficiary_id=user.id)]

    @app.get("/api/campaigns/{campaign_id}/payouts")
    async def campaign_payouts(campaign_id: str, user: AdvertiserUser) -> list[dict[str, Any]]:
        await readable_campaign(campaign_id, user)
        out = []
        for p in await state.ctx.store.list_payouts(campaign_id=campaign_id):
            row = p.model_dump(mode="json", exclude={"evidence"})
            if p.kind != "refund":
                row["to_address"] = "shielded address (private to the creator)"
            out.append(row)
        return out

    @app.get("/api/wallet/status")
    async def wallet_status(user: CurrentUser) -> dict[str, Any]:
        info = await state.ctx.wallet.info()
        out = {"backend": info.backend, "network": info.network, "simulated": info.simulated, "reachable": info.reachable,
               "detail": info.detail if is_admin(user) else "", "your_address": user.zcash_address}  # fmt: skip
        if is_admin(user):
            out["escrow_balance"] = str(info.escrow_balance) if info.escrow_balance is not None else None
        return out

    # ---- agent credit and spending limits -------------------------------------------------------------------

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
        return owner_view(await payments().create_topup(user.id, body.amount))

    @app.post("/api/agent/spend-requests/{request_id}")
    async def decide_spend(request_id: str, body: SpendDecisionIn, user: AdvertiserUser) -> dict[str, Any]:
        return await payments().decide_spend_request(user.id, request_id, body.approved)

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
        body = {
            "error": "payment_required",
            "detail": "Pay this shielded Zcash invoice, then repeat the request with the header X-Payment-Id. "
            "Access unlocks only after the wallet sees the payment confirmed; a transaction id is not accepted as proof.",
            "payment": {
                k: req[k]
                for k in (
                    "id",
                    "network",
                    "ticker",
                    "amount",
                    "address",
                    "memo",
                    "zip321_uri",
                    "pay_url",
                    "expires_at",
                    "status",
                    "confirmations",
                    "required_confirmations",
                    "simulated",
                )
            },  # fmt: skip
            "retry_with_header": {"X-Payment-Id": invoice.id},
        }
        return JSONResponse(body, status_code=402, headers={"X-Payment-Id": invoice.id})

    async def premium_invoice(user: User, scope: str) -> Invoice:
        return await payments().create_invoice("api_access", user.id, "Zerveil premium creator search", state.settings.premium_search_price,
                                               scope=scope, ttl_minutes=state.settings.api_invoice_ttl_minutes)  # fmt: skip

    @app.get("/api/influencers/premium-search", responses={402: {"description": "Payment required: a shielded Zcash invoice"}})
    async def premium_search(user: AdvertiserUser, niche: str = Query(min_length=2, max_length=40),
                             x_payment_id: Annotated[str | None, Header()] = None) -> Any:  # fmt: skip
        scope = f"{PREMIUM_CATEGORY}:{niche.lower()}"
        if x_payment_id:
            invoice = await state.ctx.store.get_invoice(x_payment_id)
            if invoice is None or invoice.owner_id != user.id or invoice.scope != scope:
                raise HTTPException(400, "X-Payment-Id does not belong to this request")
            if invoice.status != "paid":
                if invoice.status in ("pending", "underpaid", "confirming"):
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
        """The advertiser's agent runs the 402 flow itself: gets the invoice, pays it from prepaid agent credit if the
        spending policy allows (otherwise asks its owner), then unlocks the results."""
        scope = f"{PREMIUM_CATEGORY}:{body.niche.lower()}"
        invoice = await premium_invoice(user, scope)
        decision = await payments().agent_pay(user.id, invoice, PREMIUM_CATEGORY)
        out: dict[str, Any] = {"invoice": owner_view(await payments().get_invoice(invoice.id)), "payment": decision}
        if decision["status"] == "paid":
            await payments().consume(invoice.id, user.id, scope)
            out["results"] = await premium_results(body.niche)
        return out

    # ---- operators: payout approval and custody -------------------------------------------------------------

    @app.get("/api/admin/payouts")
    async def admin_payouts(_: AdminUser, status: str | None = None) -> list[dict[str, Any]]:
        statuses = set(status.split(",")) if status else None
        return [p.model_dump(mode="json") for p in await state.ctx.store.list_payouts(status=statuses)]

    @app.post("/api/admin/payouts/{payout_id}/decision")
    async def admin_decide(payout_id: str, body: PayoutDecisionIn, user: AdminUser) -> dict[str, Any]:
        payout = await payments().decide_payout(payout_id, user.email, body.approved, body.note)
        await payments().process_payouts()  # send now rather than at the next tick
        return (await payments().get_payout(payout.id)).model_dump(mode="json")

    @app.post("/api/admin/payouts/{payout_id}/resolve")
    async def admin_resolve(payout_id: str, body: ResolveIn, user: AdminUser) -> dict[str, Any]:
        return (await payments().resolve_unknown(payout_id, user.email, body.action, body.note)).model_dump(mode="json")

    @app.get("/api/admin/custody")
    async def admin_custody(_: AdminUser) -> dict[str, Any]:
        return await payments().custody()

    @app.get("/api/admin/payment-events")
    async def admin_events(_: AdminUser, invoice_id: str | None = None, payout_id: str | None = None) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in await state.ctx.store.list_payment_events(invoice_id=invoice_id, payout_id=payout_id)]

    @app.get("/api/admin/invoices")
    async def admin_invoices(_: AdminUser) -> list[dict[str, Any]]:
        return [owner_view(i) for i in await state.ctx.store.list_invoices()]

    # ---- development: simulated payments ---------------------------------------------------------------------

    @app.post("/api/dev/payments/{invoice_id}/simulate")
    async def simulate(invoice_id: str, body: SimulateIn, user: CurrentUser) -> dict[str, Any]:
        """PAYMENT_MODE=simulated only. The stand-in wallet receives the payment; the normal scanner then verifies it
        exactly as it would real wallet data. The result is labeled simulated everywhere."""
        if not state.ctx.wallet.simulated:
            raise HTTPException(400, "only available with PAYMENT_MODE=simulated: pay with a real Zcash wallet instead")
        invoice = await payments().get_invoice(invoice_id)
        if invoice.owner_id != user.id and not is_admin(user) and invoice.purpose != "payment_link":
            raise HTTPException(404, "payment not found")
        invoice = await payments().simulate_payment(invoice, body.amount, body.confirmations)
        await state.deals.tick()
        return payments().payment_request(await payments().get_invoice(invoice.id))
