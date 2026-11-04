"""Deterministic policy: offers, signed terms (Ed25519 agent key + shielded payee), funding invoices, payouts."""

from decimal import Decimal

import pytest

from tests.conftest import AGENT_KEY, TEST_SECRET, ua
from zerveil.auth.crypto import delegation_mac, delegation_statement, delegation_valid, public_key_of, sign_text, verify_text
from zerveil.models import requirements_hash
from zerveil.policy import check_funding, check_offer, check_terms, needs_human_approval, performance_payout, sign_terms

# --- offers --------------------------------------------------------------------------------------------------------


def test_offer_within_bounds(campaign, offer):
    assert check_offer(campaign, offer).ok


@pytest.mark.parametrize(
    "change,needle",
    [
        ({"fixed_fee": Decimal("99")}, "fixed fee"),
        ({"cpm": Decimal("10")}, "CPM"),
        ({"cap": Decimal("400")}, "cap"),
        ({"round": 5}, "rounds"),
        ({"required_tags": []}, "tags"),
    ],
)
def test_offer_violations(campaign, offer, change, needle):
    r = check_offer(campaign, offer.model_copy(update=change))
    assert not r.ok and any(needle in v for v in r.violations)


# --- signed terms ----------------------------------------------------------------------------------------------------


def test_terms_signed_by_the_agent_key(campaign, influencer, offer, terms):
    assert check_terms(campaign, terms, influencer, offer).ok
    assert terms.influencer_signature and terms.network == "zcash-testnet"


def test_payee_swap_is_blocked(campaign, influencer, offer, terms):
    r = check_terms(campaign, terms.model_copy(update={"payee": ua("attacker")}), influencer, offer)
    assert any("payee" in v for v in r.violations) and any("signature" in v for v in r.violations)
    # even re-signed by the agent, a payee other than the registered payout address is refused
    resigned = sign_terms(AGENT_KEY, terms.model_copy(update={"payee": ua("attacker")}))
    assert "payee is not the creator's registered payout address" in check_terms(campaign, resigned, influencer, offer).violations


def test_payee_must_be_a_shielded_address_on_this_network(campaign, influencer, offer, terms):
    for payee in ("tmBsTi2xWTjUdEXnuTceL7fecEQKeWaPDJd", ua("ava", "mainnet")):
        profile = influencer.model_copy(update={"payout_address": payee})
        bad = sign_terms(AGENT_KEY, terms.model_copy(update={"payee": payee}))
        assert "payee is not a shielded Unified Address on this network" in check_terms(campaign, bad, profile, offer).violations
    mainnet = sign_terms(AGENT_KEY, terms.model_copy(update={"network": "zcash-mainnet"}))
    assert any("zcash-mainnet" in v for v in check_terms(campaign, mainnet, influencer, offer).violations)


def test_amount_tamper_breaks_signature(campaign, influencer, offer, terms):
    r = check_terms(campaign, terms.model_copy(update={"cap": Decimal("400")}), influencer, offer)
    assert "influencer agent signature invalid" in r.violations


def test_signature_from_another_key_is_refused(campaign, influencer, offer, terms):
    forged = sign_terms("55" * 32, terms)
    assert "influencer agent signature invalid" in check_terms(campaign, forged, influencer, offer).violations


def test_content_rules_cannot_change_during_negotiation(campaign, offer, influencer, terms):
    spec = campaign.model_copy(update={"requirements": "Brief v1", "min_duration_seconds": 15, "max_duration_seconds": 60})
    good = offer.model_copy(update={"requirements_hash": requirements_hash("Brief v1"), "min_duration_seconds": 15, "max_duration_seconds": 60})
    assert check_offer(spec, good).ok
    for change in ({"requirements_hash": requirements_hash("Brief v2")}, {"max_duration_seconds": 120}):
        assert "video length or requirements changed" in check_offer(spec, good.model_copy(update=change)).violations
    # Terms signed without the brief do not match a campaign that has one
    assert "signed terms cover different video length or requirements" in check_terms(spec, terms, influencer, good).violations


# --- agent key and delegation ----------------------------------------------------------------------------------------


def test_ed25519_sign_and_verify():
    signer = public_key_of(AGENT_KEY)
    assert signer.startswith("ed25519:")
    sig = sign_text(AGENT_KEY, "hello")
    assert verify_text(signer, "hello", sig) and not verify_text(signer, "hellO", sig)
    assert not verify_text(public_key_of("55" * 32), "hello", sig)
    assert not verify_text(signer, "hello", "not-a-signature")


def test_delegation_binds_channel_agent_and_payout_address(identity):
    p = identity.profile

    def valid(secret, statement, mac, channel, signer, address):
        return delegation_valid(secret, statement, mac, channel_id=channel, agent_signer=signer, payout_address=address)

    assert valid(TEST_SECRET, p.delegation, p.delegation_signature, p.channel_id, p.agent_signer, p.payout_address)
    assert not valid("other-secret", p.delegation, p.delegation_signature, p.channel_id, p.agent_signer, p.payout_address)
    assert not valid(TEST_SECRET, p.delegation, p.delegation_signature, p.channel_id, p.agent_signer, ua("new-address"))
    assert not valid(TEST_SECRET, p.delegation, p.delegation_signature, "UC_other", p.agent_signer, p.payout_address)
    # a statement for another address, MACed with the right secret, does not carry over
    other = delegation_statement("ava@example.com", p.channel_id, p.agent_signer, ua("new-address"), "2026-11-04T00:00:00")
    assert not valid(TEST_SECRET, other, delegation_mac(TEST_SECRET, p.delegation), p.channel_id, p.agent_signer, ua("new-address"))


# --- funding and payouts -------------------------------------------------------------------------------------------------


def test_funding_check(campaign):
    caps, fee = [Decimal("40")] * 3, Decimal("0.0003")
    assert check_funding(campaign, caps, fee, Decimal("120.0009"), Decimal("120")).ok
    assert "invoice amount differs from the agreed caps plus fee reserve" in check_funding(campaign, caps, fee, Decimal("120"), Decimal("120")).violations
    assert any("budget exceeded" in v for v in check_funding(campaign, caps, fee, Decimal("120.0009"), Decimal("121")).violations)
    no_refund = campaign.model_copy(update={"refund_address": ""})
    assert "the advertiser has no refund address for unspent escrow" in check_funding(no_refund, caps, fee, Decimal("120.0009"), Decimal("0")).violations
    assert "no deals to fund" in check_funding(campaign, [], fee, Decimal("0"), Decimal("0")).violations


def test_approval_and_performance(terms):
    assert needs_human_approval(Decimal("50.01"), Decimal("50"))
    assert not needs_human_approval(Decimal("50"), Decimal("50"))
    assert performance_payout(terms, 4_000) == Decimal("10.00")  # 4 × 2.5
    assert performance_payout(terms, 1_000_000) == terms.cap - terms.fixed_fee  # capped
    assert performance_payout(terms, 333) == Decimal("0.83")  # rounded down


def test_mcp_results_never_carry_zcash_addresses():
    from zerveil.mcp_servers.server import scrub_addresses

    out = scrub_addresses({"terms": {"payee": ua("ava")}, "payouts": [{"to": ua("adv", "mainnet")}], "note": f"pay {ua('x')} now",
                           "t": "tmBsTi2xWTjUdEXnuTceL7fecEQKeWaPDJd", "id": "DEAL-1", "n": 3})  # fmt: skip
    assert out == {"terms": {"payee": "[shielded address]"}, "payouts": [{"to": "[shielded address]"}], "note": "pay [shielded address] now",
                   "t": "[shielded address]", "id": "DEAL-1", "n": 3}  # fmt: skip
