import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from zerveil.agents.influencer.identity import InfluencerIdentity
from zerveil.auth.crypto import delegation_mac, delegation_statement, public_key_of
from zerveil.models import CampaignSpec, DealTerms, InfluencerProfile, Offer, RateCard
from zerveil.policy import sign_terms
from zerveil.zcash.simulated import fake_unified_address

AGENT_KEY = "44" * 32  # Ed25519 private key (hex) of the influencer's agent
TEST_SECRET = "test-session-secret"


def ua(label: str, network: str = "testnet") -> str:
    """A deterministic string with Unified Address encoding and checksum (not a real wallet's address)."""
    return fake_unified_address(network, sha256(label.encode()).digest() * 2)


@pytest.fixture(scope="session")
def event_loop_policy():
    # psycopg's async driver cannot use Windows' default Proactor loop
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def campaign() -> CampaignSpec:
    return CampaignSpec(
        id="CMP-test",
        advertiser="FitFuel",
        brand_handle="@fitfuel",
        niche="fitness",
        budget=Decimal("120"),
        max_fixed=Decimal("25"),
        max_cpm=Decimal("3"),
        cap_per_creator=Decimal("40"),
        refund_address=ua("advertiser"),
    )


@pytest.fixture
def influencer() -> InfluencerProfile:
    return InfluencerProfile(
        id="inf-ava",
        name="Ava",
        channel_id="UC_ava",
        subscribers=42_000,
        niche="fitness",
        payout_address=ua("ava"),
        rate_card=RateCard(fixed_min=Decimal("18"), cpm_min=Decimal("2"), preferred_fixed=Decimal("24")),
        agent_signer=public_key_of(AGENT_KEY),
    )


@pytest.fixture
def offer(campaign: CampaignSpec) -> Offer:
    return Offer(
        deal_id="DEAL-1",
        campaign_id=campaign.id,
        round=1,
        fixed_fee=Decimal("20"),
        cpm=Decimal("2.5"),
        cap=campaign.cap_per_creator,
        deliverable="youtube_short",
        publish_within_days=7,
        required_tags=["#ad"],
    )


@pytest.fixture
def terms(campaign: CampaignSpec, influencer: InfluencerProfile, offer: Offer) -> DealTerms:
    raw = DealTerms(
        deal_id=offer.deal_id,
        campaign_id=campaign.id,
        advertiser=campaign.advertiser,
        influencer_id=influencer.id,
        channel_id=influencer.channel_id,
        payee=influencer.payout_address,
        fixed_fee=offer.fixed_fee,
        cpm=offer.cpm,
        cap=offer.cap,
        deliverable=offer.deliverable,
        publish_by=datetime.now(UTC) + timedelta(days=7),
        required_tags=offer.required_tags,
    )
    return sign_terms(AGENT_KEY, raw)


@pytest.fixture
def identity(influencer: InfluencerProfile) -> InfluencerIdentity:
    """The influencer's agent: its own Ed25519 key, authorized by the creator while signed in."""
    statement = delegation_statement("ava@example.com", influencer.channel_id, influencer.agent_signer, influencer.payout_address, "2026-11-04T00:00:00")
    profile = influencer.model_copy(update={"delegation": statement, "delegation_signature": delegation_mac(TEST_SECRET, statement)})
    return InfluencerIdentity(profile, AGENT_KEY, "friendly, flexible on CPM")
