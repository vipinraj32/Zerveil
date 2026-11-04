"""Zcash payment rules with the simulated wallet: invoice scanning, duplicate notes, expiry and late payments, the
payout queue (approval, compare-and-set, failures, lost outcomes), deal funding, agent credit and spending limits.

Every test here is MOCKED: SimulatedZcashWallet stands in for Zallet. What is real is the code under test — the
same scanner, invoice rules and payout queue run against the real wallet in PAYMENT_MODE=zcash."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from tests.conftest import AGENT_KEY, ua
from zerveil.config import Settings
from zerveil.models import Campaign, Deal, DealState, DealTerms, Offer, User, transition_allowed
from zerveil.payments.models import SpendingPolicy
from zerveil.payments.service import PaymentError
from zerveil.policy import sign_terms
from zerveil.services.context import build_context
from zerveil.services.deals import DealError, DealService
from zerveil.verifier.youtube import FixtureVideoSource, VideoInfo
from zerveil.zcash.memo import reference_in
from zerveil.zcash.wallet import WalletUnavailable

FEE = Decimal("0.0003")


def settings(**over) -> Settings:
    return Settings(_env_file=None, database_url="", anthropic_api_key="", youtube_api_key="", session_secret="s", **over)


@pytest.fixture
async def svc(campaign, influencer):
    ctx = await build_context(settings())
    service = DealService(ctx)
    service.profile = AsyncMock(return_value=influencer)  # registry stand-in: the influencer's verified profile
    now = ctx.clock.now()
    await ctx.store.save_user(User(id="USR-adv", email="adv@example.com", name="Adv", role="advertiser", created_at=now, zcash_address=ua("advertiser")))
    await ctx.store.save_user(User(id="USR-ava", email="ava@example.com", name="Ava", role="influencer", created_at=now, zcash_address=ua("ava")))
    await service.save_campaign(Campaign(spec=campaign.model_copy(update={"owner_id": "USR-adv"}), created_at=now))
    return service


@pytest.fixture
def payments(svc):
    return svc.payments


@pytest.fixture
def wallet(svc):
    return svc.ctx.wallet


async def new_deal(svc: DealService, influencer, n: int, *, gate: bool = True) -> Deal:
    """A deal with signed terms, through the policy gate: waiting for the advertiser's approval."""
    ctx = svc.ctx
    spec = (await svc.campaign("CMP-test")).spec
    did = f"DEAL-{n}"
    offer = Offer(deal_id=did, campaign_id=spec.id, round=1, fixed_fee=Decimal("20"), cpm=Decimal("2.5"), cap=spec.cap_per_creator,
                  deliverable="youtube_short", publish_within_days=7, required_tags=["#ad"])  # fmt: skip
    terms = sign_terms(AGENT_KEY, DealTerms(deal_id=did, campaign_id=spec.id, advertiser=spec.advertiser, influencer_id=influencer.id,
                                            channel_id=influencer.channel_id, payee=influencer.payout_address, fixed_fee=offer.fixed_fee,
                                            cpm=offer.cpm, cap=offer.cap, deliverable=offer.deliverable,
                                            publish_by=ctx.clock.now() + timedelta(days=7), required_tags=["#ad"]))  # fmt: skip
    deal = await svc.save(Deal(id=did, campaign_id=spec.id, influencer_id=influencer.id, terms=terms, agreed_at=ctx.clock.now()), DealState.AGREED)
    if gate:
        await svc.gate(deal, spec, offer)
    return await svc.get(did)


async def funded(svc, influencer, n: int = 1) -> Deal:
    deal = await new_deal(svc, influencer, n)
    invoice = await svc.request_funding("CMP-test", [deal.id], "USR-adv")
    svc.ctx.wallet.receive(invoice.address, invoice.amount, invoice.memo, confirmations=3)
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.FUNDED
    return deal


async def approve_queue(svc) -> None:
    for p in await svc.ctx.store.list_payouts(status={"pending_approval"}):
        await svc.payments.decide_payout(p.id, "ops@example.com", True, "checked")
    await svc.payments.process_payouts()


# --- invoices and scanning -------------------------------------------------------------------------------------------


async def test_invoice_gets_a_fresh_shielded_address_and_memo(payments):
    a = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"))
    b = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"))
    assert a.address != b.address and a.address.startswith("utest1")
    assert reference_in(a.memo) == a.id and a.simulated and a.status == "pending"
    req = payments.payment_request(a)
    assert req["ticker"] == "TAZ" and req["pay_url"].endswith(f"/pay/{a.id}") and req["zip321_uri"].startswith(f"zcash:{a.address}?amount=1")
    assert "notes" not in req and "deal_ids" not in req
    for bad in (Decimal("0"), Decimal("0.000000001")):
        with pytest.raises(PaymentError):
            await payments.create_invoice("credit_topup", "USR-adv", "Top-up", bad)


async def test_underpaid_then_confirming_then_paid(payments, wallet):
    inv = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"))
    wallet.receive(inv.address, Decimal("0.4"), inv.memo, confirmations=5)
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "underpaid"
    wallet.receive(inv.address, Decimal("0.6"), "", confirmations=0)  # no memo: the address alone identifies the invoice
    await payments.scan()
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "confirming" and inv.received == Decimal("1") and inv.confirmations == 0
    assert [n.memo_matched for n in inv.notes] == [True, False]
    payments.ctx.clock.advance(timedelta(days=1))  # paid in time: waiting for blocks never expires it
    wallet.confirm(3)
    await payments.scan()
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "paid" and inv.paid_via == "shielded" and inv.paid_at is not None


async def test_a_note_counts_for_one_invoice_only(payments, wallet):
    a = await payments.create_invoice("credit_topup", "USR-adv", "A", Decimal("1"))
    b = await payments.create_invoice("credit_topup", "USR-adv", "B", Decimal("1"))
    note = wallet.receive(a.address, Decimal("1"), a.memo, confirmations=3)
    wallet.notes.append(type(note)(**{**note.__dict__, "address": b.address}))  # a wallet bug reporting the same note twice
    await payments.scan()
    await payments.scan()  # rescans never double count
    a, b = await payments.get_invoice(a.id), await payments.get_invoice(b.id)
    assert a.received + b.received == Decimal("1") and len(a.notes) + len(b.notes) == 1
    loser = b if a.notes else a
    assert loser.status == "pending"
    assert "note_already_counted" in [e.kind for e in await payments.ctx.store.list_payment_events(invoice_id=loser.id)]


async def test_a_typed_txid_is_not_evidence(payments, wallet):
    """Nothing but wallet scan data marks an invoice paid: there is no endpoint or method that accepts a txid."""
    inv = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"))
    other = await wallet.new_invoice_address()
    wallet.receive(other, Decimal("1"), inv.memo, confirmations=10)  # right memo, wrong address (not this invoice's)
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "pending"


async def test_expiry_partial_refund_and_late_payment_refunded_once_each(payments, wallet):
    inv = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"), ttl_minutes=30)
    wallet.receive(inv.address, Decimal("0.5"), inv.memo, confirmations=3)
    await payments.scan()
    payments.ctx.clock.advance(timedelta(minutes=31))
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "expired"
    refunds = await payments.ctx.store.list_payouts()
    assert [(p.kind, p.amount, p.status, p.to_address) for p in refunds] == [("refund", Decimal("0.5") - FEE, "pending_approval", ua("advertiser"))]

    wallet.receive(inv.address, Decimal("0.7"), inv.memo, confirmations=1)  # arrives after expiry
    await payments.scan()
    await payments.scan()
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "late_payment" and inv.received == Decimal("1.2")
    amounts = sorted(p.amount for p in await payments.ctx.store.list_payouts())
    assert amounts == [Decimal("0.5") - FEE, Decimal("0.7") - FEE]  # the second refund covers only the late note
    wallet.receive(inv.address, Decimal("0.2"), inv.memo, confirmations=1)  # and another after that
    await payments.scan()
    assert len(await payments.ctx.store.list_payouts()) == 3
    assert all(p.status == "pending_approval" for p in await payments.ctx.store.list_payouts())  # refunds always need an operator


async def test_cancel_only_an_untouched_invoice(payments, wallet):
    inv = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"))
    assert (await payments.cancel_invoice(inv, "USR-adv")).status == "cancelled"
    other = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"))
    wallet.receive(other.address, Decimal("0.1"), other.memo, confirmations=0)
    await payments.scan()
    with pytest.raises(PaymentError):
        await payments.cancel_invoice(await payments.get_invoice(other.id), "USR-adv")


async def test_scan_survives_an_unreachable_wallet(payments, wallet, monkeypatch):
    inv = await payments.create_invoice("credit_topup", "USR-adv", "Top-up", Decimal("1"))
    monkeypatch.setattr(wallet, "received", AsyncMock(side_effect=WalletUnavailable("down")))
    assert await payments.scan() == 0
    assert (await payments.get_invoice(inv.id)).status == "pending"


# --- payout queue ----------------------------------------------------------------------------------------------------


async def test_payout_needs_approval_and_is_idempotent(payments, wallet):
    wallet.balance = Decimal("10")
    a = await payments.request_payout("fixed", ua("ava"), Decimal("2"), "fixed fee", key="DEAL-1:fixed", beneficiary_id="USR-ava")
    b = await payments.request_payout("fixed", ua("ava"), Decimal("2"), "fixed fee", key="DEAL-1:fixed", beneficiary_id="USR-ava")
    assert a.id == b.id and a.status == "pending_approval" and reference_in(a.memo) == a.id
    assert await payments.process_payouts() == 0 and wallet.sent == []  # not approved: nothing is sent
    await payments.decide_payout(a.id, "ops@example.com", True, "evidence ok")
    with pytest.raises(PaymentError):
        await payments.decide_payout(a.id, "ops@example.com", True)  # decided once
    await payments.process_payouts()
    await payments.process_payouts()
    p = await payments.get_payout(a.id)
    assert p.status == "sent" and p.txid == wallet.sent[0].txid and len(wallet.sent) == 1
    assert wallet.sent[0].memo == a.memo
    wallet.confirm(3)
    await payments.process_payouts()
    assert (await payments.get_payout(a.id)).status == "confirmed"


async def test_payout_without_approval_requirement(svc):
    svc.ctx.settings.payout_approval_required = False
    svc.ctx.wallet.balance = Decimal("5")
    p = await svc.payments.request_payout("fixed", ua("ava"), Decimal("1"), "fee", key="k1")
    assert p.status == "approved"
    # but a refund of unusable money and a non-shielded payee always wait for an operator
    r = await svc.payments.request_payout("refund", ua("advertiser"), Decimal("1"), "late", key="k2", force_approval=True)
    bad = await svc.payments.request_payout("fixed", "tmBsTi2xWTjUdEXnuTceL7fecEQKeWaPDJd", Decimal("1"), "fee", key="k3")
    assert r.status == "pending_approval" and bad.status == "pending_approval" and "transparent" in bad.error
    with pytest.raises(PaymentError, match="cannot approve"):
        await svc.payments.decide_payout(bad.id, "ops", True)


async def test_rejected_payout_keeps_the_funds(payments, wallet):
    wallet.balance = Decimal("5")
    p = await payments.request_payout("fixed", ua("ava"), Decimal("1"), "fee", key="k")
    await payments.decide_payout(p.id, "ops", False, "proof looks staged")
    await payments.process_payouts()
    assert (await payments.get_payout(p.id)).status == "rejected" and wallet.sent == [] and wallet.balance == 5


async def test_concurrent_queue_workers_send_once(payments, wallet):
    wallet.balance = Decimal("5")
    p = await payments.request_payout("fixed", ua("ava"), Decimal("1"), "fee", key="k")
    await payments.decide_payout(p.id, "ops", True)
    payout = await payments.get_payout(p.id)
    results = await asyncio.gather(*(payments._send(payout.model_copy(deep=True)) for _ in range(5)))
    assert sum(results) == 1 and len(wallet.sent) == 1


async def test_refused_send_fails_and_can_be_resent_by_an_operator(payments, wallet):
    wallet.balance = Decimal("5")
    p = await payments.request_payout("fixed", ua("ava"), Decimal("1"), "fee", key="k")
    await payments.decide_payout(p.id, "ops", True)
    wallet.fail_next_send = "insufficient funds"
    await payments.process_payouts()
    failed = await payments.get_payout(p.id)
    assert failed.status == "failed" and "insufficient" in failed.error
    await payments.resolve_unknown(p.id, "ops", "resend", "balance topped up")
    await payments.process_payouts()
    assert (await payments.get_payout(p.id)).status in ("submitted", "sent") and len(wallet.sent) == 1


async def test_lost_send_outcome_is_reconciled_from_outgoing_memos_not_resent(payments, wallet, monkeypatch):
    wallet.balance = Decimal("5")
    p = await payments.request_payout("fixed", ua("ava"), Decimal("1"), "fee", key="k")
    await payments.decide_payout(p.id, "ops", True)
    real_send = wallet.send

    async def broadcast_then_drop(to, amount, memo):  # the wallet sent it, but the answer never reached us
        await real_send(to, amount, memo)
        raise WalletUnavailable("connection reset")

    monkeypatch.setattr(wallet, "send", broadcast_then_drop)
    await payments.process_payouts()
    assert (await payments.get_payout(p.id)).status == "unknown"
    monkeypatch.setattr(wallet, "send", AsyncMock(side_effect=AssertionError("an unknown payout must never be resent automatically")))
    await payments.process_payouts()
    p = await payments.get_payout(p.id)
    assert p.status == "sent" and p.txid == wallet.sent[0].txid and len(wallet.sent) == 1


async def test_unknown_outcome_not_in_wallet_waits_for_an_operator(payments, wallet, monkeypatch):
    wallet.balance = Decimal("5")
    p = await payments.request_payout("fixed", ua("ava"), Decimal("1"), "fee", key="k")
    await payments.decide_payout(p.id, "ops", True)
    monkeypatch.setattr(wallet, "send", AsyncMock(side_effect=WalletUnavailable("timeout")))
    await payments.process_payouts()
    for _ in range(3):
        await payments.process_payouts()
    assert (await payments.get_payout(p.id)).status == "unknown"  # stays unknown, never resent on its own
    with pytest.raises(PaymentError):
        await payments.decide_payout(p.id, "ops", True)
    assert (await payments.resolve_unknown(p.id, "ops", "cancel", "checked the wallet: never sent")).status == "rejected"


async def test_stale_sending_becomes_unknown(payments, wallet):
    wallet.balance = Decimal("5")
    p = await payments.request_payout("fixed", ua("ava"), Decimal("1"), "fee", key="k")
    await payments.decide_payout(p.id, "ops", True)
    payout = await payments.get_payout(p.id)
    payout.status = "sending"  # the process died between saving `sending` and the wallet's answer
    await payments.ctx.store.save_payout(payout)
    payments.ctx.clock.advance(timedelta(minutes=11))
    await payments.process_payouts()
    assert (await payments.get_payout(p.id)).status == "unknown" and wallet.sent == []


# --- deals funded by a shielded invoice ------------------------------------------------------------------------------


async def test_funding_invoice_covers_caps_plus_fee_reserve(svc, influencer, wallet):
    deals = [await new_deal(svc, influencer, n) for n in range(2)]
    invoice = await svc.request_funding("CMP-test", [d.id for d in deals], "USR-adv")
    assert invoice.amount == Decimal("80") + 2 * FEE and invoice.purpose == "campaign_funding" and invoice.deal_ids == [d.id for d in deals]
    assert [(await svc.get(d.id)).state for d in deals] == [DealState.AWAITING_FUNDING] * 2
    with pytest.raises(DealError):  # funding is requested once
        await svc.request_funding("CMP-test", [deals[0].id], "USR-adv")
    wallet.receive(invoice.address, invoice.amount, invoice.memo, confirmations=1)
    await svc.tick()
    assert [(await svc.get(d.id)).state for d in deals] == [DealState.FUNDING] * 2
    wallet.confirm(2)
    await svc.tick()
    for d in deals:
        d = await svc.get(d.id)
        assert d.state == DealState.FUNDED and d.funding_simulated and d.funded_at is not None
    kinds = [e.kind for e in await svc.ctx.store.list_events("CMP-test")]
    assert "payment_detected" in kinds and "escrow_funded" in kinds


async def test_tampered_terms_are_never_invoiced(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    deal.terms = deal.terms.model_copy(update={"cap": Decimal("39")})
    await svc.ctx.store.save_deal(deal)
    with pytest.raises(DealError, match="terms changed"):
        await svc.request_funding("CMP-test", [deal.id], "USR-adv")
    assert await svc.ctx.store.list_invoices() == []


async def test_expired_funding_closes_deals_and_releases_budget(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    await svc.request_funding("CMP-test", [deal.id], "USR-adv")
    assert await svc.ctx.store.reserved_budget("CMP-test") == Decimal("40")
    svc.ctx.clock.advance(timedelta(hours=3))
    await svc.tick()
    assert (await svc.get(deal.id)).state == DealState.PAYMENT_EXPIRED
    assert await svc.ctx.store.reserved_budget("CMP-test") == 0


async def test_budget_limits_deals(svc, influencer):
    states = [(await new_deal(svc, influencer, n)).state for n in range(4)]
    assert states == [DealState.AWAITING_APPROVAL] * 3 + [DealState.BLOCKED]  # 120 budget, 40 caps
    assert any("budget exceeded" in v for v in (await svc.get("DEAL-3")).policy_violations)


async def test_concurrent_gates_cannot_overspend(svc, influencer):
    drafts = [await new_deal(svc, influencer, n, gate=False) for n in range(5)]
    spec = (await svc.campaign("CMP-test")).spec

    async def gate(deal):
        offer = Offer(deal_id=deal.id, campaign_id=spec.id, round=1, fixed_fee=Decimal("20"), cpm=Decimal("2.5"), cap=spec.cap_per_creator,
                      deliverable="youtube_short", publish_within_days=7, required_tags=["#ad"])  # fmt: skip
        return (await svc.gate(deal, spec, offer)).ok

    assert sum(await asyncio.gather(*(gate(d) for d in drafts))) == 3
    assert await svc.ctx.store.reserved_budget("CMP-test") == Decimal("120")


def test_deal_state_machine_blocks_skipping_funding():
    assert not transition_allowed(DealState.AWAITING_APPROVAL, DealState.FUNDED)
    assert not transition_allowed(DealState.AWAITING_FUNDING, DealState.VERIFIED)
    assert not transition_allowed(DealState.PAYMENT_EXPIRED, DealState.FUNDED)
    assert not transition_allowed(DealState.COMPLETED, DealState.FUNDED)
    assert transition_allowed(DealState.FUNDING, DealState.FUNDED)


async def test_illegal_transition_is_refused(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    with pytest.raises(DealError, match="not allowed"):
        await svc.save(deal, DealState.FUNDED)


async def test_verified_proof_pays_once_and_refunds_the_rest(svc, influencer):
    videos = FixtureVideoSource(svc.ctx.clock.now)
    svc.ctx.videos = videos
    deal = await funded(svc, influencer)
    videos.add(VideoInfo(video_id="abcdefghijk", channel_id=influencer.channel_id, title="t", description="@fitfuel #ad",
                         published_at=svc.ctx.clock.now(), privacy_status="public", views=0, duration_seconds=30), week_one_views=4_000)  # fmt: skip
    deal = await svc.submit_proof(deal.id, "https://youtube.com/shorts/abcdefghijk")
    assert deal.state == DealState.FIXED_RELEASED, deal.proof
    for _ in range(3):  # extra ticks must not decide twice
        svc.ctx.clock.advance(timedelta(days=4))
        await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.COMPLETED
    assert {p.kind: p.amount for p in deal.payouts} == {"fixed": Decimal("20"), "performance": Decimal("10.00"), "refund": Decimal("10")}
    assert all(p.simulated for p in deal.payouts)
    await approve_queue(svc)
    sent = svc.ctx.wallet.sent
    assert sorted((s.to, s.amount) for s in sent) == sorted([(ua("ava"), Decimal("20")), (ua("ava"), Decimal("10")), (ua("advertiser"), Decimal("10"))])
    assert svc.ctx.wallet.balance == FEE


async def test_instant_release_racing_the_scheduler_decides_once(svc, influencer):
    videos = FixtureVideoSource(svc.ctx.clock.now)
    svc.ctx.videos = videos
    deal = await funded(svc, influencer)
    videos.add(VideoInfo(video_id="abcdefghijk", channel_id=influencer.channel_id, title="t", description="@fitfuel #ad",
                         published_at=svc.ctx.clock.now(), privacy_status="public", views=0, duration_seconds=30))  # fmt: skip
    await asyncio.gather(svc.submit_proof(deal.id, "https://youtube.com/shorts/abcdefghijk"), *(svc.tick() for _ in range(3)))
    deal = await svc.get(deal.id)
    assert deal.state == DealState.FIXED_RELEASED and [p.kind for p in deal.payouts] == ["fixed"]
    assert len(await svc.ctx.store.list_payouts(deal_id=deal.id)) == 1


async def test_unproven_deal_is_refunded_after_deadline(svc, influencer):
    deal = await funded(svc, influencer)
    svc.ctx.clock.advance(timedelta(days=8))
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.EXPIRED_REFUNDED and [(p.kind, p.amount, p.to) for p in deal.payouts] == [("refund", Decimal("40"), ua("advertiser"))]


# --- agent credit, spending limits, payment links -------------------------------------------------------------------


async def topped_up(payments, wallet, amount: str) -> None:
    inv = await payments.create_topup("USR-adv", Decimal(amount))
    wallet.receive(inv.address, inv.amount, inv.memo, confirmations=3)
    await payments.scan()
    await payments.scan()


async def api_invoice(payments, amount: str = "0.05"):
    return await payments.create_invoice("api_access", "USR-adv", "search", Decimal(amount), scope="premium_search:fitness")


async def test_credit_only_from_a_verified_topup(payments, wallet):
    inv = await payments.create_topup("USR-adv", Decimal("0.2"))
    assert (await payments.credit_summary("USR-adv"))["balance"] == "0"
    wallet.receive(inv.address, inv.amount, inv.memo, confirmations=1)
    await payments.scan()
    assert (await payments.credit_summary("USR-adv"))["balance"] == "0"  # not confirmed yet
    wallet.confirm(2)
    await payments.scan()
    await payments.scan()
    assert Decimal((await payments.credit_summary("USR-adv"))["balance"]) == Decimal("0.2")


async def test_agent_pays_within_limits(payments, wallet):
    await topped_up(payments, wallet, "0.2")
    inv = await api_invoice(payments)
    assert (await payments.agent_pay("USR-adv", inv, "premium_search"))["status"] == "paid"
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "paid" and inv.paid_via == "agent_credit"
    assert Decimal((await payments.credit_summary("USR-adv"))["balance"]) == Decimal("0.15")
    assert (await payments.agent_pay("USR-adv", inv, "premium_search"))["reason"] == "already paid"  # never debited twice
    assert Decimal((await payments.credit_summary("USR-adv"))["balance"]) == Decimal("0.15")
    await payments.consume(inv.id, "USR-adv", "premium_search:fitness")
    with pytest.raises(PaymentError, match="already used"):
        await payments.consume(inv.id, "USR-adv", "premium_search:fitness")


@pytest.mark.parametrize(
    "policy,amount,needle",
    [
        ({"enabled": False}, "0.05", "switched off"),
        ({"allowed_categories": []}, "0.05", "not allowed"),
        ({"max_per_request": "0.01"}, "0.05", "per-request limit"),
        ({"max_daily": "0.04"}, "0.05", "daily limit"),
    ],
)
async def test_agent_payment_refused_outside_limits(payments, wallet, policy, amount, needle):
    await topped_up(payments, wallet, "1")
    await payments.save_spending_policy("USR-adv", SpendingPolicy(**{**SpendingPolicy().model_dump(), **policy}))
    result = await payments.agent_pay("USR-adv", await api_invoice(payments, amount), "premium_search")
    assert result["status"] == "refused" and needle in result["reason"]
    assert Decimal((await payments.credit_summary("USR-adv"))["balance"]) == 1


async def test_agent_payment_refused_without_credit(payments):
    result = await payments.agent_pay("USR-adv", await api_invoice(payments), "premium_search")
    assert result["status"] == "refused" and "top up" in result["reason"]


async def test_agent_payment_above_threshold_needs_owner_approval(payments, wallet):
    await topped_up(payments, wallet, "1")
    await payments.save_spending_policy("USR-adv", SpendingPolicy(approval_above=Decimal("0.01")))
    inv = await api_invoice(payments)
    first = await payments.agent_pay("USR-adv", inv, "premium_search")
    again = await payments.agent_pay("USR-adv", inv, "premium_search")
    assert first["status"] == again["status"] == "pending_approval" and first["request_id"] == again["request_id"]
    assert (await payments.get_invoice(inv.id)).status == "pending"
    with pytest.raises(PaymentError):
        await payments.decide_spend_request("USR-other", first["request_id"], True)
    assert (await payments.decide_spend_request("USR-adv", first["request_id"], True))["status"] == "paid"
    assert (await payments.get_invoice(inv.id)).status == "paid"


async def test_concurrent_agent_payments_respect_the_daily_limit(payments, wallet):
    await topped_up(payments, wallet, "1")
    await payments.save_spending_policy("USR-adv", SpendingPolicy(max_daily=Decimal("0.1")))
    invoices = [await api_invoice(payments) for _ in range(5)]
    results = await asyncio.gather(*(payments.agent_pay("USR-adv", i, "premium_search") for i in invoices))
    assert [r["status"] for r in results].count("paid") == 2


async def test_payment_link_is_forwarded_to_the_creator(payments, wallet):
    link = await payments.create_payment_link("USR-ava", "Shout-out", Decimal("0.5"))
    assert link.purpose == "payment_link" and link.owner_id == "USR-ava"
    wallet.receive(link.address, Decimal("0.5"), link.memo, confirmations=3)
    await payments.scan()
    [forward] = await payments.ctx.store.list_payouts()
    assert forward.kind == "link_forward" and forward.to_address == ua("ava") and forward.amount == Decimal("0.5") - FEE
    assert forward.status == "pending_approval"
    with pytest.raises(PaymentError):
        await payments.create_payment_link("USR-ava", "Too small", FEE)
