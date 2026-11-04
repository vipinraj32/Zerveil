"""Direct Zcash payment rules with the simulated scanner: viewing-key connection, invoice scanning and statuses,
duplicate notes, expiry and late payments, receipts, milestone payments through the deal lifecycle, API credit and
spending limits, payment links.

Every test here is MOCKED: SimulatedScanner stands in for the real view-only scanner (zcash-devtool). What is real is
the code under test: the same scanner rules, invoice statuses and milestone lifecycle run in PAYMENT_MODE=zcash."""

import asyncio
import secrets
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from tests.conftest import AGENT_KEY, ua
from zerveil.config import Settings
from zerveil.models import Campaign, Deal, DealState, DealTerms, Offer, User, transition_allowed
from zerveil.payments.models import SpendingPolicy
from zerveil.payments.service import API_SELLER, PaymentError
from zerveil.policy import sign_terms
from zerveil.services.context import build_context
from zerveil.services.deals import DealError, DealService
from zerveil.verifier.youtube import FixtureVideoSource, VideoInfo
from zerveil.zcash.address import bech32m_encode, convert_bits
from zerveil.zcash.memo import reference_in
from zerveil.zcash.wallet import ReceivedNote, WalletUnavailable


def settings(**over) -> Settings:
    return Settings(_env_file=None, database_url="", anthropic_api_key="", youtube_api_key="", session_secret="s", **over)


def ufvk(hrp: str = "uviewtest") -> str:
    """A string with Unified Full Viewing Key encoding and checksum, from random bytes (not a real key)."""
    return bech32m_encode(hrp, convert_bits(secrets.token_bytes(96), 8, 5))


async def connect(svc: DealService, user_id: str, key: str | None = None):
    account = await svc.payments.connect_viewing_key(user_id, "creator", key or ufvk(), 4_400_000, consent=True)
    user = await svc.ctx.store.get_user(user_id)
    if user is not None:
        user.receiving_account_id = account.id
        await svc.ctx.store.save_user(user)
    return account


@pytest.fixture
async def svc(campaign, influencer):
    ctx = await build_context(settings())
    service = DealService(ctx)
    service.profile = AsyncMock(return_value=influencer)  # registry stand-in: the influencer's verified profile
    now = ctx.clock.now()
    await ctx.store.save_user(User(id="USR-adv", email="adv@example.com", name="Adv", role="advertiser", created_at=now))
    await ctx.store.save_user(User(id=influencer.id, email="ava@example.com", name="Ava", role="influencer", created_at=now, zcash_address=ua("ava")))
    await service.save_campaign(Campaign(spec=campaign.model_copy(update={"owner_id": "USR-adv", "refund_address": ""}), created_at=now))
    await connect(service, influencer.id)
    await service.payments.ensure_api_seller()
    return service


@pytest.fixture
def payments(svc):
    return svc.payments


@pytest.fixture
def scanner(svc):
    return svc.ctx.wallet


async def new_deal(svc: DealService, influencer, n: int, *, gate: bool = True, fixed: str = "20") -> Deal:
    """A deal with signed terms, through the policy gate: waiting for the advertiser's acceptance."""
    ctx = svc.ctx
    spec = (await svc.campaign("CMP-test")).spec
    did = f"DEAL-{n}"
    offer = Offer(deal_id=did, campaign_id=spec.id, round=1, fixed_fee=Decimal(fixed), cpm=Decimal("2.5"), cap=spec.cap_per_creator,
                  deliverable="youtube_short", publish_within_days=7, required_tags=["#ad"])  # fmt: skip
    terms = sign_terms(AGENT_KEY, DealTerms(deal_id=did, campaign_id=spec.id, advertiser=spec.advertiser, influencer_id=influencer.id,
                                            channel_id=influencer.channel_id, payee=influencer.payout_address, fixed_fee=offer.fixed_fee,
                                            cpm=offer.cpm, cap=offer.cap, deliverable=offer.deliverable,
                                            publish_by=ctx.clock.now() + timedelta(days=7), required_tags=["#ad"]))  # fmt: skip
    deal = await svc.save(Deal(id=did, campaign_id=spec.id, influencer_id=influencer.id, terms=terms, agreed_at=ctx.clock.now()), DealState.AGREED)
    if gate:
        await svc.gate(deal, spec, offer)
    return await svc.get(did)


async def paid_deal(svc, influencer, n: int = 1) -> Deal:
    """A deal whose fixed fee the scanner confirmed in the creator's wallet: ready for content."""
    deal = await new_deal(svc, influencer, n)
    [invoice] = await svc.request_payment("CMP-test", [deal.id], "USR-adv")
    svc.ctx.wallet.receive(invoice.address, invoice.amount, invoice.memo, confirmations=3)
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.CONTENT_CREATION
    return deal


async def creator_invoice(payments, amount: str = "1"):
    return await payments.create_invoice("payment_link", "inf-ava", "inf-ava", "Shout-out", Decimal(amount))


# --- receiving accounts (viewing keys) -------------------------------------------------------------------------------


async def test_zerveil_holds_no_funds_and_cannot_send(scanner, payments):
    assert not hasattr(scanner, "send") and not hasattr(payments, "request_payout") and not hasattr(payments, "custody")
    status = await payments.scanner_status()
    assert status["custody"].startswith("none")


@pytest.mark.parametrize(
    "key,needle",
    [
        (" ".join(["abandon"] * 24), "seed phrase"),
        ("secret-extended-key-test1qqqq", "spending key"),
        ("uviewtest1notachecksum", "not a valid"),
        (None, "incoming viewing key"),  # filled below with a uivktest key
        ("mainnet", "mainnet viewing key"),
    ],
)
async def test_viewing_key_rules(payments, key, needle):
    if key is None:
        key = ufvk("uivktest")
    elif key == "mainnet":
        key = ufvk("uview")
    with pytest.raises(PaymentError, match=needle):
        await payments.connect_viewing_key("inf-x", "creator", key, 0, consent=True)


async def test_connecting_needs_consent_and_stores_only_a_fingerprint(payments):
    key = ufvk()
    with pytest.raises(PaymentError, match="authorize"):
        await payments.connect_viewing_key("inf-x", "creator", key, 0, consent=False)
    account = await payments.connect_viewing_key("inf-x", "creator", key, 4_400_000, consent=True)
    assert key not in account.model_dump_json() and len(account.key_fingerprint) == 16 and account.simulated
    assert "cannot spend" in account.authorization
    again = await payments.connect_viewing_key("inf-x", "creator", key, 4_400_000, consent=True)
    assert again.id == account.id  # same key: same account
    other = await payments.connect_viewing_key("inf-x", "creator", ufvk(), 4_400_000, consent=True)
    assert other.id != account.id and (await payments.ctx.store.get_receiving_account_by_id(account.id)).revoked_at is not None
    await payments.disconnect_viewing_key("inf-x")
    assert await payments.receiving_account("inf-x") is None


async def test_no_invoice_without_the_recipients_viewing_key(payments):
    with pytest.raises(PaymentError, match="not connected a receiving account"):
        await payments.create_invoice("payment_link", "inf-nokey", "inf-nokey", "x", Decimal("1"))


# --- invoices and scanning -------------------------------------------------------------------------------------------


async def test_invoice_pays_a_fresh_address_of_the_recipients_own_account(payments):
    a = await creator_invoice(payments)
    b = await creator_invoice(payments)
    account = await payments.receiving_account("inf-ava")
    assert a.address != b.address and a.address.startswith("utest1") and a.receiving_account_id == account.id
    assert a.recipient_id == "inf-ava" and reference_in(a.memo) == a.id and a.simulated and a.status == "awaiting_payment"
    req = payments.payment_request(a)
    assert req["direct"] is True and req["ticker"] == "TAZ" and req["zip321_uri"].startswith(f"zcash:{a.address}?amount=1")
    assert req["pay_url"].endswith(f"/pay/{a.id}") and "notes" not in req and "deal_ids" not in req
    for bad in (Decimal("0"), Decimal("0.000000001")):
        with pytest.raises(PaymentError):
            await payments.create_invoice("payment_link", "inf-ava", "inf-ava", "x", bad)


async def test_seen_mined_confirming_confirmed(payments, scanner):
    inv = await creator_invoice(payments)
    scanner.receive(inv.address, Decimal("1"), inv.memo, confirmations=0)
    await payments.scan()
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "seen" and not inv.is_paid and inv.receipt_id is None  # unconfirmed is never paid
    for status in ("mined", "confirming", "confirmed"):
        scanner.confirm(1)
        await payments.scan()
        inv = await payments.get_invoice(inv.id)
        assert inv.status == status, (status, inv.confirmations)
    assert inv.is_paid and inv.paid_via == "shielded" and inv.receipt_id
    receipt = await payments.get_receipt(inv.receipt_id)
    assert receipt.recipient_id == "inf-ava" and receipt.payer_id == "" and receipt.amount == Decimal("1") and receipt.pools == ["ironwood"]
    assert receipt.receipt_hash == receipt.compute_hash() and inv.address not in receipt.model_dump_json()
    scanner.confirm(5)
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).receipt_id == receipt.id  # one receipt per invoice


async def test_underpaid_then_paid_and_overpaid(payments, scanner):
    inv = await creator_invoice(payments)
    scanner.receive(inv.address, Decimal("0.4"), inv.memo, confirmations=3)
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "underpaid"
    scanner.receive(inv.address, Decimal("0.6"), inv.memo, confirmations=3)
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "confirmed"
    over = await creator_invoice(payments)
    scanner.receive(over.address, Decimal("1.5"), over.memo, confirmations=3)
    await payments.scan()
    over = await payments.get_invoice(over.id)
    assert over.status == "overpaid" and over.is_paid
    assert any(e.kind == "overpaid" for e in await payments.ctx.store.list_payment_events(invoice_id=over.id))


async def test_a_note_counts_for_one_invoice_only(payments):
    a, b = await creator_invoice(payments), await creator_invoice(payments)
    note = ReceivedNote(txid="ab" * 32, pool="ironwood", output_index=0, address="", amount=Decimal("1"), confirmations=3, memo=a.memo)
    now = payments.ctx.clock.now()
    assert await payments._apply(a, [note], now)
    assert not await payments._apply(b, [note], now)
    assert (await payments.get_invoice(a.id)).is_paid and (await payments.get_invoice(b.id)).status == "awaiting_payment"


async def test_memo_matches_when_the_scanner_does_not_know_the_address(payments, scanner):
    inv = await creator_invoice(payments)
    account = await payments.receiving_account("inf-ava")
    scanner.notes[account.scanner_account].append(
        ReceivedNote(txid="cd" * 32, pool="ironwood", output_index=0, address="", amount=Decimal("1"), confirmations=3, memo=inv.memo)
    )
    await payments.scan()
    inv = await payments.get_invoice(inv.id)
    assert inv.is_paid and inv.notes[0].memo_matched


async def test_a_typed_txid_is_not_evidence(payments):
    inv = await creator_invoice(payments)
    assert not hasattr(payments, "submit_txid") and not hasattr(payments, "mark_paid")
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "awaiting_payment"


async def test_expiry_and_late_payment_is_recorded_not_refunded(payments, scanner):
    inv = await creator_invoice(payments)
    payments.ctx.clock.advance(timedelta(minutes=payments.ctx.settings.zcash_invoice_ttl_minutes + 1))
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "expired"
    scanner.receive(inv.address, Decimal("1"), inv.memo, confirmations=3)
    await payments.scan()
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "expired" and inv.received == Decimal("1") and inv.receipt_id is None
    events = [e.kind for e in await payments.ctx.store.list_payment_events(invoice_id=inv.id)]
    assert "late_payment" in events and not any("refund" in e for e in events)


async def test_paid_in_time_never_expires_while_confirming(payments, scanner):
    inv = await creator_invoice(payments)
    scanner.receive(inv.address, Decimal("1"), inv.memo, confirmations=0)
    await payments.scan()
    payments.ctx.clock.advance(timedelta(days=3))
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "seen"
    scanner.confirm(3)
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "confirmed"


async def test_payment_that_expired_unmined_is_failed(payments, scanner):
    inv = await creator_invoice(payments)
    note = scanner.receive(inv.address, Decimal("1"), inv.memo, confirmations=0)
    await payments.scan()
    scanner.expire(note.txid)
    await payments.scan()
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "failed" and inv.received == 0
    scanner.receive(inv.address, Decimal("1"), inv.memo, confirmations=3)  # the payer tries again
    await payments.scan()
    assert (await payments.get_invoice(inv.id)).status == "confirmed"


async def test_cancel_only_an_untouched_invoice(payments, scanner):
    inv = await creator_invoice(payments)
    assert (await payments.cancel_invoice(inv, "inf-ava")).status == "cancelled"
    touched = await creator_invoice(payments)
    scanner.receive(touched.address, Decimal("0.1"), touched.memo, confirmations=0)
    await payments.scan()
    with pytest.raises(PaymentError):
        await payments.cancel_invoice(await payments.get_invoice(touched.id), "inf-ava")


async def test_a_scanner_outage_never_expires_an_invoice(payments, scanner, monkeypatch):
    inv = await creator_invoice(payments)
    payments.ctx.clock.advance(timedelta(days=2))
    monkeypatch.setattr(scanner, "received", AsyncMock(side_effect=WalletUnavailable("lightwalletd down")))
    assert await payments.scan() == 0
    assert (await payments.get_invoice(inv.id)).status == "awaiting_payment"


# --- deals: milestones paid directly to the creator ----------------------------------------------------------------


def test_deal_state_machine_blocks_skipping_payment():
    assert not transition_allowed(DealState.AWAITING_APPROVAL, DealState.VERIFIED)
    assert not transition_allowed(DealState.PAYMENT_REQUESTED, DealState.VERIFIED)
    assert not transition_allowed(DealState.PAYMENT_EXPIRED, DealState.CONTENT_CREATION)
    assert not transition_allowed(DealState.COMPLETED, DealState.CONTENT_CREATION)
    assert transition_allowed(DealState.PAYMENT_PENDING, DealState.CONTENT_CREATION)


async def test_illegal_transition_is_refused(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    with pytest.raises(DealError, match="not allowed"):
        await svc.save(deal, DealState.VERIFIED)


async def test_accepting_requests_the_fixed_fee_straight_to_the_creator(svc, influencer, scanner):
    deal = await new_deal(svc, influencer, 1)
    [invoice] = await svc.request_payment("CMP-test", [deal.id], "USR-adv")
    creator_account = await svc.payments.receiving_account(influencer.id)
    assert invoice.purpose == "milestone" and invoice.amount == Decimal("20") and invoice.owner_id == "USR-adv"
    assert invoice.recipient_id == influencer.id and invoice.receiving_account_id == creator_account.id
    deal = await svc.get(deal.id)
    assert deal.state == DealState.PAYMENT_REQUESTED and deal.milestone("fixed").status == "requested"
    scanner.receive(invoice.address, invoice.amount, invoice.memo, confirmations=1)
    await svc.tick()
    assert (await svc.get(deal.id)).state == DealState.PAYMENT_PENDING
    scanner.confirm(2)
    await svc.tick()
    deal = await svc.get(deal.id)
    m = deal.milestone("fixed")
    assert deal.state == DealState.CONTENT_CREATION and m.status == "confirmed" and m.receipt_id and deal.payment_simulated
    assert (await svc.campaign("CMP-test")).status == "active"
    summary = await svc.payment_summary("CMP-test")
    assert summary["paid"] == "20" and summary["requested"] == "0"


async def test_one_invoice_per_creator(svc, influencer):
    d1, d2 = await new_deal(svc, influencer, 1), await new_deal(svc, influencer, 2)
    invoices = await svc.request_payment("CMP-test", [d1.id, d2.id], "USR-adv")
    assert len(invoices) == 2 and len({i.address for i in invoices}) == 2 and {i.deal_ids[0] for i in invoices} == {d1.id, d2.id}


async def test_tampered_terms_are_never_invoiced(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    deal.terms = deal.terms.model_copy(update={"fixed_fee": Decimal("25")})
    await svc.ctx.store.save_deal(deal)
    with pytest.raises(DealError, match="terms changed"):
        await svc.request_payment("CMP-test", [deal.id], "USR-adv")
    assert await svc.ctx.store.list_invoices(purpose="milestone") == []


async def test_creator_without_viewing_key_is_never_invoiced(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    await svc.payments.disconnect_viewing_key(influencer.id)
    user = await svc.ctx.store.get_user(influencer.id)
    user.receiving_account_id = ""
    await svc.ctx.store.save_user(user)
    with pytest.raises(DealError, match="receiving account"):
        await svc.request_payment("CMP-test", [deal.id], "USR-adv")


async def test_expired_fixed_fee_closes_the_deal_and_releases_budget(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    await svc.request_payment("CMP-test", [deal.id], "USR-adv")
    assert await svc.committed_spend("CMP-test") == Decimal("40")
    svc.ctx.clock.advance(timedelta(minutes=svc.ctx.settings.zcash_invoice_ttl_minutes + 1))
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.PAYMENT_EXPIRED and deal.milestone("fixed").status == "expired"
    assert await svc.committed_spend("CMP-test") == 0


async def test_cpm_only_deal_needs_no_upfront_payment(svc, influencer):
    deal = await new_deal(svc, influencer, 1, fixed="0")
    assert await svc.request_payment("CMP-test", [deal.id], "USR-adv") == []
    assert (await svc.get(deal.id)).state == DealState.CONTENT_CREATION


async def test_budget_limits_deals(svc, influencer):
    deals = [await new_deal(svc, influencer, n) for n in range(1, 5)]  # budget 120, cap 40: three fit
    assert [d.state for d in deals].count(DealState.AWAITING_APPROVAL) == 3
    assert deals[3].state == DealState.BLOCKED and any("budget" in v for v in deals[3].policy_violations)


async def test_concurrent_gates_cannot_overspend(svc, influencer):
    ungated = [await new_deal(svc, influencer, n, gate=False) for n in range(1, 6)]
    spec = (await svc.campaign("CMP-test")).spec
    offer = Offer(deal_id="x", campaign_id=spec.id, round=1, fixed_fee=Decimal("20"), cpm=Decimal("2.5"), cap=spec.cap_per_creator,
                  deliverable="youtube_short", publish_within_days=7, required_tags=["#ad"])  # fmt: skip
    results = await asyncio.gather(*(svc.gate(d, spec, offer.model_copy(update={"deal_id": d.id})) for d in ungated))
    assert sum(r.ok for r in results) == 3 and await svc.committed_spend("CMP-test") == Decimal("120")


def _video(influencer, now, views: int = 0):
    return VideoInfo(video_id="abcdefghijk", channel_id=influencer.channel_id, title="t", description="@fitfuel #ad", published_at=now,
                     privacy_status="public", views=views, duration_seconds=30)  # fmt: skip


async def test_verified_post_then_performance_milestone_then_completed(svc, influencer, scanner):
    videos = FixtureVideoSource(svc.ctx.clock.now)
    svc.ctx.videos = videos
    deal = await paid_deal(svc, influencer)
    videos.add(_video(influencer, svc.ctx.clock.now()), week_one_views=4_000)
    deal = await svc.submit_proof(deal.id, "https://youtube.com/shorts/abcdefghijk")
    assert deal.state == DealState.PERFORMANCE_DUE, deal.proof
    svc.ctx.clock.advance(timedelta(days=8))
    for _ in range(3):  # extra ticks must not request twice
        await svc.tick()
    deal = await svc.get(deal.id)
    perf = deal.milestone("performance")
    assert deal.state == DealState.PERFORMANCE_DUE and perf.amount == Decimal("10.00") and perf.views == 4_000 and perf.status == "requested"
    assert len([i for i in await svc.ctx.store.list_invoices(purpose="milestone") if i.milestone_id == perf.id]) == 1
    invoice = await svc.payments.get_invoice(perf.invoice_id)
    assert invoice.recipient_id == influencer.id and invoice.owner_id == "USR-adv"
    scanner.receive(invoice.address, invoice.amount, invoice.memo, confirmations=3)
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.COMPLETED and [m.status for m in deal.milestones] == ["confirmed", "confirmed"]
    assert sum(m.amount for m in deal.milestones) <= deal.terms.cap


async def test_proof_racing_the_scheduler_requests_once(svc, influencer):
    videos = FixtureVideoSource(svc.ctx.clock.now)
    svc.ctx.videos = videos
    deal = await paid_deal(svc, influencer)
    videos.add(_video(influencer, svc.ctx.clock.now()), week_one_views=4_000)
    await asyncio.gather(svc.submit_proof(deal.id, "https://youtube.com/shorts/abcdefghijk"), *(svc.tick() for _ in range(3)))
    svc.ctx.clock.advance(timedelta(days=8))
    await asyncio.gather(*(svc.tick() for _ in range(4)))
    deal = await svc.get(deal.id)
    assert [m.kind for m in deal.milestones] == ["fixed", "performance"]


async def test_no_views_waives_the_performance_milestone(svc, influencer):
    videos = FixtureVideoSource(svc.ctx.clock.now)
    svc.ctx.videos = videos
    deal = await paid_deal(svc, influencer)
    videos.add(_video(influencer, svc.ctx.clock.now()), week_one_views=0)
    await svc.submit_proof(deal.id, "https://youtube.com/shorts/abcdefghijk")
    svc.ctx.clock.advance(timedelta(days=8))
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.COMPLETED and deal.milestone("performance").status == "waived"


async def test_missed_deadline_is_undelivered_and_nothing_is_refunded(svc, influencer):
    deal = await paid_deal(svc, influencer)
    svc.ctx.clock.advance(timedelta(days=8))
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.UNDELIVERED and deal.payouts == []
    events = [e.kind for e in await svc.ctx.store.list_events("CMP-test", deal.id)]
    assert "deadline_missed" in events and not any("refund" in e for e in events)


# --- API credit, spending limits, payment links ---------------------------------------------------------------------


async def topped_up(payments, scanner, amount: str) -> None:
    inv = await payments.create_topup("USR-adv", Decimal(amount))
    scanner.receive(inv.address, inv.amount, inv.memo, confirmations=3)
    await payments.scan()


async def api_invoice(payments, amount: str = "0.05"):
    return await payments.create_invoice("api_access", "USR-adv", API_SELLER, "search", Decimal(amount), scope="premium_search:fitness")


async def test_topups_and_402_invoices_pay_the_api_sellers_own_account(payments):
    seller = await payments.receiving_account(API_SELLER)
    topup, api = await payments.create_topup("USR-adv", Decimal("0.2")), await api_invoice(payments)
    assert topup.recipient_id == api.recipient_id == API_SELLER and topup.receiving_account_id == api.receiving_account_id == seller.id


async def test_credit_only_from_a_confirmed_topup(payments, scanner):
    inv = await payments.create_topup("USR-adv", Decimal("0.2"))
    assert (await payments.credit_summary("USR-adv"))["balance"] == "0"
    scanner.receive(inv.address, inv.amount, inv.memo, confirmations=1)
    await payments.scan()
    assert (await payments.credit_summary("USR-adv"))["balance"] == "0"  # not confirmed yet
    scanner.confirm(2)
    await payments.scan()
    await payments.scan()
    assert Decimal((await payments.credit_summary("USR-adv"))["balance"]) == Decimal("0.2")


async def test_agent_pays_within_limits(payments, scanner):
    await topped_up(payments, scanner, "0.2")
    inv = await api_invoice(payments)
    assert (await payments.agent_pay("USR-adv", inv, "premium_search"))["status"] == "paid"
    inv = await payments.get_invoice(inv.id)
    assert inv.status == "confirmed" and inv.paid_via == "agent_credit"
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
async def test_agent_payment_refused_outside_limits(payments, scanner, policy, amount, needle):
    await topped_up(payments, scanner, "1")
    await payments.save_spending_policy("USR-adv", SpendingPolicy(**{**SpendingPolicy().model_dump(), **policy}))
    result = await payments.agent_pay("USR-adv", await api_invoice(payments, amount), "premium_search")
    assert result["status"] == "refused" and needle in result["reason"]
    assert Decimal((await payments.credit_summary("USR-adv"))["balance"]) == 1


async def test_agent_payment_refused_without_credit(payments):
    result = await payments.agent_pay("USR-adv", await api_invoice(payments), "premium_search")
    assert result["status"] == "refused" and "top up" in result["reason"]


async def test_agent_payment_above_threshold_needs_owner_approval(payments, scanner):
    await topped_up(payments, scanner, "1")
    await payments.save_spending_policy("USR-adv", SpendingPolicy(approval_above=Decimal("0.01")))
    inv = await api_invoice(payments)
    first = await payments.agent_pay("USR-adv", inv, "premium_search")
    again = await payments.agent_pay("USR-adv", inv, "premium_search")
    assert first["status"] == again["status"] == "pending_approval" and first["request_id"] == again["request_id"]
    assert (await payments.get_invoice(inv.id)).status == "awaiting_payment"
    with pytest.raises(PaymentError):
        await payments.decide_spend_request("USR-other", first["request_id"], True)
    assert (await payments.decide_spend_request("USR-adv", first["request_id"], True))["status"] == "paid"
    assert (await payments.get_invoice(inv.id)).status == "confirmed"


async def test_concurrent_agent_payments_respect_the_daily_limit(payments, scanner):
    await topped_up(payments, scanner, "1")
    await payments.save_spending_policy("USR-adv", SpendingPolicy(max_daily=Decimal("0.1")))
    invoices = [await api_invoice(payments) for _ in range(5)]
    results = await asyncio.gather(*(payments.agent_pay("USR-adv", i, "premium_search") for i in invoices))
    assert [r["status"] for r in results].count("paid") == 2


async def test_payment_link_pays_the_creators_own_wallet(payments, scanner):
    link = await payments.create_payment_link("inf-ava", "Shout-out", Decimal("0.5"))
    account = await payments.receiving_account("inf-ava")
    assert link.purpose == "payment_link" and link.recipient_id == "inf-ava" and link.receiving_account_id == account.id
    scanner.receive(link.address, Decimal("0.5"), link.memo, confirmations=3)
    await payments.scan()
    link = await payments.get_invoice(link.id)
    assert link.status == "confirmed" and link.receipt_id
    with pytest.raises(PaymentError, match="viewing key"):
        await payments.create_payment_link("inf-nokey", "x", Decimal("1"))
