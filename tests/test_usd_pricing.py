"""USD is the business currency; the payment asset is shielded ZEC (TAZ on testnet), converted per invoice.

There is no USD stablecoin on Zcash, so nothing here is a stablecoin and TAZ is never presented as USD. Testnet TAZ has
no market price: invoices are converted at a configured, labeled *test rate*. Invoice tests are MOCKED (simulated
scanner); the conversion, terms and policy rules are the real code."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.conftest import AGENT_KEY
from tests.test_payments import connect, new_deal, settings
from zerveil.models import Campaign, DealState, User
from zerveil.money import RateUnavailable, exact_usd, format_usd, quote, usd
from zerveil.payments.models import Invoice, Receipt
from zerveil.payments.service import PaymentError
from zerveil.policy import check_terms, sign_terms
from zerveil.services.context import build_context
from zerveil.services.deals import DealError, DealService

NOW = datetime(2026, 11, 4, tzinfo=UTC)


# --- the currency model ----------------------------------------------------------------------------------------------


def test_usd_amounts_are_exact_decimals():
    assert usd("19.995") == Decimal("20.00") and usd(Decimal("1")) == Decimal("1.00")
    with pytest.raises(TypeError):
        usd(0.1)  # floats never become money
    with pytest.raises(ValueError, match="2 decimal places"):
        exact_usd(Decimal("1.005"))
    assert format_usd(Decimal("1234.5")) == "$1,234.50" and format_usd(Decimal("40"), code=True) == "$40.00 USD"


def test_test_rate_converts_up_to_the_zatoshi_and_is_labeled():
    rate = quote(settings(zcash_test_usd_rate=Decimal("37")), NOW)
    assert rate.asset == "TAZ" and rate.network == "zcash-testnet" and rate.is_test_rate and "test rate" in rate.source
    assert rate.to_asset(Decimal("10.00")) == Decimal("0.27027028")  # 0.270270270… rounded UP: never less than agreed
    assert rate.to_usd(Decimal("0.27027028")) == Decimal("10.00")


def test_mainnet_pricing_is_refused_without_a_live_price_source():
    with pytest.raises(RateUnavailable, match="live ZEC/USD price source"):
        quote(settings(zcash_network="mainnet"), NOW)


# --- invoices: USD value, asset amount, rate -------------------------------------------------------------------------


@pytest.fixture
async def svc(campaign, influencer):
    """At the default test rate ($100 per TAZ), unlike test_payments.py."""
    from unittest.mock import AsyncMock

    ctx = await build_context(settings(zcash_test_usd_rate=Decimal("100")))
    service = DealService(ctx)
    service.profile = AsyncMock(return_value=influencer)
    now = ctx.clock.now()
    await ctx.store.save_user(User(id="USR-adv", email="adv@example.com", name="Adv", role="advertiser", created_at=now))
    await ctx.store.save_user(User(id=influencer.id, email="ava@example.com", name="Ava", role="influencer", created_at=now))
    await service.save_campaign(Campaign(spec=campaign.model_copy(update={"owner_id": "USR-adv"}), created_at=now))
    await connect(service, influencer.id)
    await service.payments.ensure_api_seller()
    return service


async def test_milestone_invoice_is_usd_priced_and_paid_in_taz(svc, influencer):
    deal = await new_deal(svc, influencer, 1, fixed="20")
    [invoice] = await svc.request_payment("CMP-test", [deal.id], "USR-adv")
    assert invoice.currency == "USD" and invoice.usd_amount == Decimal("20.00") and invoice.asset == "TAZ"
    assert invoice.amount == Decimal("0.2") and invoice.usd_rate == Decimal("100") and invoice.test_rate is True
    request = svc.payments.payment_request(invoice)
    assert request["ticker"] == "TAZ" and "amount=0.2&" in request["zip321_uri"]  # the wallet is asked for TAZ, never "USD"
    assert (await svc.get(deal.id)).milestone("fixed").amount == Decimal("20")  # the milestone stays in USD
    svc.ctx.wallet.receive(invoice.address, invoice.amount, invoice.memo, confirmations=3)
    await svc.tick()
    paid = await svc.payments.get_invoice(invoice.id)
    receipt = await svc.payments.get_receipt(paid.receipt_id)
    assert receipt.currency == "USD" and receipt.usd_amount == Decimal("20.00") and receipt.amount == Decimal("0.2") and receipt.test_rate
    assert receipt.simulated is True  # MOCKED: never presented as a real transfer
    summary = await svc.payment_summary("CMP-test")
    assert summary == {"currency": "USD", "budget": summary["budget"], "reserved": summary["reserved"], "paid": "20.00",
                       "requested": "0.00", "remaining": summary["remaining"]}  # fmt: skip


async def test_credit_and_agent_limits_are_usd(svc):
    payments = svc.payments
    topup = await payments.create_topup("USR-adv", Decimal("1.00"))
    assert topup.usd_amount == Decimal("1.00") and topup.amount == Decimal("0.01")
    await payments.simulate_payment(topup, topup.amount + Decimal("0.005"))  # overpaid in TAZ
    assert (await payments.credit_summary("USR-adv"))["balance"] == "1.00"  # credited the USD requested, not TAZ
    with pytest.raises(PaymentError, match="2 decimal places"):
        await payments.create_topup("USR-adv", Decimal("0.001"))


async def test_legacy_taz_topup_is_not_credited_as_usd(svc):
    payments = svc.payments
    topup = await payments.create_topup("USR-adv", Decimal("1.00"))
    legacy = topup.model_copy(update={"currency": "", "usd_amount": None, "usd_rate": None, "asset": ""})
    await svc.ctx.store.save_invoice(legacy)
    await payments.simulate_payment(legacy)
    assert (await payments.credit_summary("USR-adv"))["balance"] == "0"


# --- signed terms ----------------------------------------------------------------------------------------------------


def test_usd_terms_are_signed_and_checked(campaign, terms, influencer, offer):
    assert terms.currency == "USD" and terms.asset == "TAZ" and "currency" in terms.signing_payload()
    assert check_terms(campaign, terms, influencer, offer).ok
    zec = sign_terms(AGENT_KEY, terms.model_copy(update={"asset": "ZEC"}))
    assert any("pay in ZEC" in v for v in check_terms(campaign, zec, influencer, offer).violations)


def test_legacy_terms_keep_their_signature_but_fail_the_gate(campaign, terms, influencer, offer):
    legacy = terms.model_copy(update={"currency": None})
    assert "currency" not in legacy.signing_payload()  # hashes of terms signed before USD pricing are unchanged
    signed = sign_terms(AGENT_KEY, legacy)
    old_payload = json.loads(json.dumps(signed.signing_payload()))
    assert "currency" not in old_payload
    assert any("priced in TAZ" in v for v in check_terms(campaign, signed, influencer, offer).violations)


async def test_no_payment_is_requested_for_a_taz_priced_campaign(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    campaign = await svc.campaign("CMP-test")
    await svc.save_campaign(campaign.model_copy(update={"spec": campaign.spec.model_copy(update={"currency": "TAZ"})}))
    with pytest.raises(DealError, match="priced in TAZ"):
        await svc.request_payment("CMP-test", [deal.id], "USR-adv")
    assert (await svc.get(deal.id)).state == DealState.AWAITING_APPROVAL
    assert await svc.ctx.store.list_invoices(campaign_id="CMP-test") == []


# --- receipts --------------------------------------------------------------------------------------------------------


def test_receipts_from_before_usd_pricing_keep_their_hash():
    base = {"id": "RCPT-1", "invoice_id": "PAY-1", "purpose": "milestone", "title": "t", "payer_id": "a", "recipient_id": "b",
            "amount": "1", "received": "1", "network": "zcash-testnet", "pools": ["ironwood"], "txids": ["aa"], "confirmations": 3,
            "memo": "ZRV1:PAY-1", "issued_at": NOW.isoformat()}  # fmt: skip
    old = Receipt.model_validate(base)
    body = old.model_dump(mode="json", exclude={"receipt_hash", "confirmations", "currency", "usd_amount", "asset", "usd_rate", "rate_source",
                                                "test_rate"})  # fmt: skip
    import hashlib

    assert old.compute_hash() == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    priced = old.model_copy(update={"currency": "USD", "usd_amount": Decimal("100"), "asset": "TAZ", "usd_rate": Decimal("100"),
                                    "rate_source": "x", "test_rate": True})  # fmt: skip
    assert priced.compute_hash() != old.compute_hash()  # the USD value is covered by the hash


def test_legacy_invoices_load_without_usd_fields():
    data = {"id": "PAY-1", "purpose": "milestone", "owner_id": "a", "title": "t", "amount": "40", "network": "zcash-testnet",
            "address": "utest1x", "memo": "ZRV1:PAY-1", "created_at": NOW.isoformat(), "expires_at": NOW.isoformat()}  # fmt: skip
    inv = Invoice.model_validate(data)
    assert inv.usd_amount is None and inv.currency == "" and inv.amount == Decimal("40")


def test_published_pricing_rules_are_usd():
    from zerveil.agent_policy.pricing import rules

    published = rules()
    assert "$0.01" in published["rounding"] and "ZEC" not in str(published) and "TAZ" not in str(published)
