"""Deterministic payment policy. Pure functions only — no LLM, no I/O.

Every value the LLM proposes and every artifact another agent sends passes through here before
anything is signed. A non-empty violation list means "do not sign".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..auth.crypto import verify_text
from ..models import DEFAULT_MAX_ROUNDS, CampaignSpec, DealTerms, InfluencerProfile, Offer, requirements_hash
from ..zcash.address import Network, unified_address_problem

MAX_ROUNDS = DEFAULT_MAX_ROUNDS  # campaigns may set their own `max_rounds`


@dataclass
class PolicyResult:
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.violations.append(message)


def check_offer(campaign: CampaignSpec, offer: Offer) -> PolicyResult:
    """Bounds for anything the advertiser agent proposes or accepts."""
    r = PolicyResult()
    zero = Decimal("0")
    r.require(offer.campaign_id == campaign.id, "offer is for a different campaign")
    r.require(offer.round <= campaign.max_rounds, f"negotiation exceeded {campaign.max_rounds} rounds")
    r.require(offer.compensation_model == campaign.compensation_model, "compensation model changed")
    match campaign.compensation_model:
        case "fixed":
            r.require(zero < offer.fixed_fee <= campaign.max_fixed, f"fixed fee {offer.fixed_fee} outside (0, {campaign.max_fixed}]")
            r.require(offer.cpm == zero, "a fixed-fee deal has no CPM")
            r.require(offer.cap == offer.fixed_fee <= campaign.cap_per_creator, f"cap {offer.cap} must equal the fixed fee, at most {campaign.cap_per_creator}")
        case "cpm":
            r.require(offer.fixed_fee == zero, "a CPM deal has no fixed fee")
            r.require(zero < offer.cpm <= campaign.max_cpm, f"CPM {offer.cpm} outside (0, {campaign.max_cpm}]")
            r.require(offer.cap == campaign.cap_per_creator, f"cap {offer.cap} must equal campaign cap {campaign.cap_per_creator}")
        case _:
            r.require(zero < offer.fixed_fee <= campaign.max_fixed, f"fixed fee {offer.fixed_fee} outside (0, {campaign.max_fixed}]")
            r.require(zero <= offer.cpm <= campaign.max_cpm, f"CPM {offer.cpm} outside [0, {campaign.max_cpm}]")
            r.require(offer.cap == campaign.cap_per_creator, f"cap {offer.cap} must equal campaign cap {campaign.cap_per_creator}")
    r.require(offer.fixed_fee <= offer.cap, "fixed fee exceeds cap")
    r.require(offer.scope == campaign.scope, "content scope changed")
    r.require(offer.disclosure_policies == campaign.disclosure_policies, "disclosure policies changed")
    r.require(offer.deliverable == campaign.deliverable, "deliverable changed")
    r.require(set(campaign.required_tags) <= set(offer.required_tags), "required tags were dropped")
    r.require(_content_rules(offer) == _campaign_content_rules(campaign), "video length or requirements changed")
    return r


def _content_rules(x: Offer | DealTerms) -> tuple[int, int, str]:
    return (x.min_duration_seconds, x.max_duration_seconds, x.requirements_hash)


def _campaign_content_rules(campaign: CampaignSpec) -> tuple[int, int, str]:
    return (campaign.min_duration_seconds, campaign.max_duration_seconds, requirements_hash(campaign.requirements_document()))


def terms_signature_valid(terms: DealTerms, signer: str) -> bool:
    """The influencer agent's Ed25519 signature over `terms_hash()`."""
    return bool(terms.influencer_signature) and verify_text(signer, terms.terms_hash(), terms.influencer_signature)


def check_terms(campaign: CampaignSpec, terms: DealTerms, influencer: InfluencerProfile, agreed: Offer, network: Network = "testnet") -> PolicyResult:
    """Signed terms coming back over A2A must match what was negotiated and be signed by the payee."""
    r = PolicyResult()
    r.require(terms.campaign_id == campaign.id and terms.deal_id == agreed.deal_id, "terms reference another deal")
    r.require(terms.influencer_id == influencer.id, "terms name a different influencer")
    r.require(terms.channel_id == influencer.channel_id, "channel does not match the OAuth-verified channel")
    r.require(terms.payee == influencer.payout_address, "payee is not the creator's registered payout address")
    r.require(unified_address_problem(terms.payee, network) is None, "payee is not a shielded Unified Address on this network")
    r.require(terms.network == f"zcash-{network}", f"terms are for {terms.network}, not zcash-{network}")
    r.require(terms.currency == campaign.currency == "USD", f"terms are priced in {terms.currency or 'TAZ'}, not the campaign's USD")
    asset = "ZEC" if network == "mainnet" else "TAZ"
    r.require(terms.asset == asset, f"terms pay in {terms.asset}, not {asset} on zcash-{network}")
    r.require(
        (terms.fixed_fee, terms.cpm, terms.cap) == (agreed.fixed_fee, agreed.cpm, agreed.cap),
        "amounts differ from the agreed offer",
    )
    r.require(set(campaign.required_tags) <= set(terms.required_tags), "required tags were dropped")
    r.require(_content_rules(terms) == _campaign_content_rules(campaign), "signed terms cover different video length or requirements")
    r.require(
        (terms.compensation_model, terms.scope, terms.disclosure_policies) == (campaign.compensation_model, campaign.scope, campaign.disclosure_policies),
        "signed terms cover a different compensation model, scope or disclosure policy",
    )
    r.require(terms.pricing_version == agreed.pricing_version, "signed terms use a different pricing version")
    r.require(bool(influencer.agent_signer) and terms_signature_valid(terms, influencer.agent_signer), "influencer agent signature invalid")
    return r


def check_milestone(terms: DealTerms, kind: str, amount: Decimal) -> PolicyResult:
    """Gate before a milestone invoice is issued: the fixed milestone asks for exactly the agreed fixed fee; the
    performance milestone for at most what the cap leaves after the fixed fee."""
    r = PolicyResult()
    r.require(amount > 0, "a milestone must request a positive amount")
    if kind == "fixed":
        r.require(amount == terms.fixed_fee, f"fixed milestone {amount} differs from the agreed fixed fee {terms.fixed_fee}")
    elif kind == "performance":
        r.require(amount <= terms.cap - terms.fixed_fee, f"performance milestone {amount} exceeds the cap left after the fixed fee")
    else:
        r.violations.append(f"unknown milestone kind {kind}")
    return r


def evidence_hash(evidence: dict[str, Any]) -> str:
    """sha256 of the evidence a payment decision was made on (terms hash, proof checks, views)."""
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()


def needs_human_approval(amount: Decimal, threshold: Decimal) -> bool:
    return amount > threshold


def performance_payout(terms: DealTerms, views: int) -> Decimal:
    """min(cap - fixed, views / 1000 × CPM), rounded down to cents (agent_policy.pricing)."""
    from ..agent_policy.pricing import calculate

    return calculate(terms.compensation_model, terms.fixed_fee, terms.cpm, views, terms.cap).performance
