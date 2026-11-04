"""Domain models shared by agents, services, API and MCP tools.

Money is always `Decimal` (serialized as a string) — A2A data parts carry JSON numbers as
doubles, so amounts must never travel as numbers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# Version of the payable-amount rules (agent_policy.pricing). Signed into DealTerms, so a later rule change
# never re-prices an agreed deal.
PRICING_VERSION = "pricing-2026.10"
DEFAULT_MAX_ROUNDS = 4  # offers the advertiser may send: opening + up to 3 responses to counter-offers
CompensationModel = Literal["fixed", "cpm", "hybrid"]


class RateCard(BaseModel):
    fixed_min: Decimal  # floor for the fixed fee
    cpm_min: Decimal  # floor per 1,000 views
    preferred_fixed: Decimal  # opening counter-offer anchor


class InfluencerProfile(BaseModel):
    id: str
    name: str
    platform: Literal["youtube"] = "youtube"
    channel_id: str  # OAuth-verified at onboarding
    subscribers: int
    niche: str  # primary niche
    niches: list[str] = Field(default_factory=list)  # every niche the creator confirmed, primary first
    payout_address: str  # shielded Zcash Unified Address the creator is paid to
    # Private to the creator's own agent. The directory the advertiser side reads strips it (registry.public_profile).
    rate_card: RateCard | None = None
    # Ed25519 key the influencer's agent signs DealTerms with ("ed25519:<hex>"). `delegation` is the statement the
    # creator approved while signed in (agent key, channel, payout address); the server MACs it (auth.crypto).
    agent_signer: str = ""
    delegation: str = ""
    delegation_signature: str = ""
    avatar_url: str = ""

    @property
    def all_niches(self) -> list[str]:
        return [n.lower() for n in (self.niches or [self.niche])]


class ChannelInfo(BaseModel):
    channel_id: str
    title: str
    subscribers: int
    thumbnail_url: str = ""
    verified_at: datetime  # when the YouTube Data API returned these statistics
    # Channel statistics from the same response; None when YouTube did not return them
    view_count: int | None = None
    video_count: int | None = None
    subscribers_hidden: bool = False  # the creator hides the subscriber count: treat it as unknown


class NicheSuggestion(BaseModel):
    niche: str
    score: float  # 0.8 × share of recent uploads matching + 0.2 when the channel's topics/keywords match
    video_share: float
    matched_videos: int
    keywords: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)  # titles of matching uploads
    channel_signal: bool = False
    llm_confidence: float | None = None  # set when an LLM ranked it (from the same fixed taxonomy)
    reason: str = ""


class NicheAnalysis(BaseModel):
    """A suggestion derived from public YouTube data. The creator confirms or edits it; it is never applied silently."""

    channel_id: str
    analyzed_at: datetime
    videos_analyzed: int
    method: Literal["rules", "llm+rules"]
    suggestions: list[NicheSuggestion] = Field(default_factory=list)
    note: str = ""


class User(BaseModel):
    """A signed-in Google account. Influencers prove their YouTube channel through the same OAuth."""

    id: str
    email: str
    name: str
    picture: str = ""
    role: Literal["advertiser", "influencer"]
    created_at: datetime
    # Shielded Zcash Unified Address: where creators are paid, and where advertisers' unspent escrow is refunded
    zcash_address: str = ""
    # advertiser
    company: str = ""
    brand_handle: str = ""
    # influencer
    channel: ChannelInfo | None = None
    niche: str = ""  # primary niche (= niches[0])
    niches: list[str] = Field(default_factory=list)
    niche_source: Literal["", "youtube_analysis", "manual"] = ""  # analysis = the creator kept only suggested niches
    niche_analysis: NicheAnalysis | None = None
    rate_card: RateCard | None = None
    persona: str = ""
    agent_active: bool = False
    agent_signer: str = ""
    agent_key_encrypted: str = ""  # Fernet; never leaves the server
    delegation: str = ""
    delegation_signature: str = ""

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"agent_key_encrypted"})

    def influencer_profile(self) -> InfluencerProfile | None:
        """The profile advertisers' agents see, once onboarding is complete."""
        if self.role != "influencer" or not (self.channel and self.zcash_address and self.rate_card and self.niche and self.agent_signer):
            return None
        return InfluencerProfile(
            id=self.id,
            name=self.channel.title,
            channel_id=self.channel.channel_id,
            subscribers=self.channel.subscribers,
            niche=self.niche,
            niches=self.niches or [self.niche],
            payout_address=self.zcash_address,
            rate_card=self.rate_card,
            agent_signer=self.agent_signer,
            delegation=self.delegation,
            delegation_signature=self.delegation_signature,
            avatar_url=self.channel.thumbnail_url,
        )


class ContentScope(BaseModel):
    """What the advertiser may do with the content, beyond one organic post. Part of the signed terms."""

    usage_rights: Literal["organic_only", "brand_repost", "paid_ads"] = "organic_only"
    license_days: int = Field(default=0, ge=0, le=730)  # how long the brand may reuse the content
    paid_amplification: bool = False  # brand may boost the post with paid media
    exclusivity_days: int = Field(default=0, ge=0, le=365)  # no competing sponsors in `exclusivity_category`
    exclusivity_category: str = Field(default="", max_length=60)
    revisions: int = Field(default=1, ge=0, le=5)  # rounds of changes the brand may request before publishing
    deliverable_count: int = Field(default=1, ge=1, le=1)  # one verifiable post per deal for now


class ContentCheck(BaseModel):
    """A requirement the content agent checks in the posted video itself (not just its title and description).

    spoken_mention: count how often `phrase` (or an alias) is said, from the video's captions. Deterministic count.
    logo_visible:   look for the brand's logo in frames YouTube publishes for the video. Vision model, advisory only.
    """

    id: str = Field(pattern=r"^[a-z0-9_]{2,30}$")
    kind: Literal["spoken_mention", "logo_visible"]
    phrase: str = Field(min_length=2, max_length=60)  # the brand / product name to hear, or whose logo to see
    aliases: list[str] = Field(default_factory=list, max_length=5)  # other spellings captions may produce ("fit fuel")
    min_count: int = Field(default=1, ge=1, le=50)  # spoken_mention only
    max_count: int | None = Field(default=None, ge=1, le=50)  # reported, never a reason to reject
    description: str = Field(default="", max_length=200)

    def text(self) -> str:
        if self.kind == "spoken_mention":
            times = f"{self.min_count}" + (f"-{self.max_count}" if self.max_count and self.max_count > self.min_count else "")
            alias = f" (also counts: {', '.join(self.aliases)})" if self.aliases else ""
            return f'Say "{self.phrase}" out loud at least {self.min_count} time(s) (asked: {times}){alias}; checked from the video\'s captions.'
        return f"Show the {self.phrase} logo clearly on screen; checked by AI on frames YouTube publishes, then confirmed by the advertiser."


CAMPAIGN_NAME_MAX = 80


def clean_campaign_name(name: str) -> str:
    """One line, single spaces, printable characters only: what the advertiser sees in every list."""
    return " ".join("".join(ch for ch in name if ch.isprintable() or ch.isspace()).split())


class CampaignSpec(BaseModel):
    id: str = Field(default_factory=lambda: new_id("CMP"))
    # The advertiser's own label for this campaign, to tell campaigns apart. Never sent to creators or their agents,
    # and not part of the signed terms. Campaigns from before names existed have "" (shown as brand · niche).
    name: str = ""
    advertiser: str
    brand_handle: str  # must appear in the video description, e.g. "@fitfuel"
    niche: str
    budget: Decimal
    max_creators: int = 3
    min_subscribers: int = 0
    max_subscribers: int = 10_000_000
    max_fixed: Decimal  # most we pay as a fixed fee per creator
    max_cpm: Decimal  # most we pay per 1,000 views
    cap_per_creator: Decimal  # escrow amount per deal (fixed + max performance)
    deliverable: Literal["youtube_short", "youtube_video"] = "youtube_short"
    publish_within_days: int = 7
    required_tags: list[str] = Field(default_factory=lambda: ["#ad"])
    brief: str = ""  # one-paragraph summary
    # Content requirements: video length and the full creative brief (up to ~3 pages).
    # Influencers sign `requirements_hash()`, so the document they agreed to cannot change afterwards.
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0  # 0 = no length rule
    requirements: str = ""
    owner_id: str = ""  # advertiser user
    refund_address: str = ""  # advertiser's Unified Address: unspent escrow goes back here
    # Commercial structure: fixed (cap = fixed fee), cpm (no fixed fee) or hybrid (fixed + CPM up to the cap)
    compensation_model: CompensationModel = "hybrid"
    scope: ContentScope = Field(default_factory=ContentScope)
    category: str = ""  # brand category, matched against creators' forbidden categories
    # Sponsorship-disclosure rules, snapshotted as "<jurisdiction>@<version>" when the campaign is created
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN", "US"])
    disclosure_policies: list[str] = Field(default_factory=list)
    max_rounds: int = DEFAULT_MAX_ROUNDS
    review_required: bool = False  # advertiser reviews disclosure compliance before the fixed fee is released
    agent_policy_version: int = 0  # advertiser agent policy in force when the campaign started (0 = defaults)
    content_checks: list[ContentCheck] = Field(default_factory=list)  # checked in the video by the content agent

    @property
    def display_name(self) -> str:
        return self.name or f"{self.advertiser} · {self.niche}"

    def requirements_document(self) -> str:
        """What creators sign (`requirements_hash`): the brief plus the automated checks, when there are any."""
        if not self.content_checks:
            return self.requirements
        lines = "\n".join(f"- [{c.id}] {c.text()}" for c in self.content_checks)
        return f"{self.requirements}\n\n## Checked in the video by the content agent\n{lines}\n".lstrip()


def requirements_hash(text: str) -> str:
    """sha256 of the content brief; empty when the campaign has none."""
    return sha256_hex(text) if text else ""


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Offer(BaseModel):
    deal_id: str
    campaign_id: str
    round: int
    proposer: Literal["advertiser", "influencer"] = "advertiser"
    max_rounds: int = DEFAULT_MAX_ROUNDS
    compensation_model: CompensationModel = "hybrid"
    fixed_fee: Decimal
    cpm: Decimal
    cap: Decimal
    deliverable: str
    publish_within_days: int
    required_tags: list[str]
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0
    requirements_hash: str = ""
    scope: ContentScope = Field(default_factory=ContentScope)
    category: str = ""
    disclosure_policies: list[str] = Field(default_factory=list)
    pricing_version: str = PRICING_VERSION
    note: str = ""

    @property
    def key(self) -> str:
        """Stable id of this offer within its negotiation, used by decision records."""
        return f"{self.deal_id}:r{self.round}:{self.proposer}"


class NegotiationReply(BaseModel):
    # clarify: the creator's agent needs something before it can decide; escalate: a human must decide
    kind: Literal["counter", "accept", "reject", "clarify", "escalate"]
    offer: Offer | None = None  # the counter-offer, or the accepted offer
    terms: DealTerms | None = None  # signed terms, on accept
    reason: str = ""


class DealTerms(BaseModel):
    """What both agents agreed. The influencer signs `terms_hash()`; the hash anchors every later step."""

    deal_id: str
    campaign_id: str
    advertiser: str
    influencer_id: str
    channel_id: str
    payee: str  # the creator's shielded Unified Address
    asset: str = "ZEC"  # TAZ on testnet
    network: str = "zcash-testnet"
    compensation_model: CompensationModel = "hybrid"
    pricing_version: str = PRICING_VERSION
    fixed_fee: Decimal
    cpm: Decimal
    cap: Decimal
    deliverable: str
    publish_by: datetime
    required_tags: list[str]
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0
    requirements_hash: str = ""  # sha256 of the campaign's requirements document
    scope: ContentScope = Field(default_factory=ContentScope)
    disclosure_policies: list[str] = Field(default_factory=list)  # "<jurisdiction>@<version>" the post must satisfy
    min_live_hours: int = 48
    views_measured_after_days: int = 7
    review_window_hours: int = 0  # the fixed fee is released as soon as the proof is verified
    influencer_signature: str = ""

    def signing_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"influencer_signature"})

    def terms_hash(self) -> str:
        canonical = json.dumps(self.signing_payload(), sort_keys=True, separators=(",", ":"))
        return sha256_hex(canonical)


class DealState(StrEnum):
    NEGOTIATING = "NEGOTIATING"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"  # an agent's policy handed the decision to a human; nothing was agreed or reserved
    AGREED = "AGREED"
    BLOCKED = "BLOCKED"  # the deterministic policy gate refused the signed terms
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # the advertiser approves the agreement before any funding is requested
    AWAITING_FUNDING = "AWAITING_FUNDING"  # a shielded funding invoice is open
    FUNDING = "FUNDING"  # the wallet sees the payment, waiting for confirmations
    FUNDED = "FUNDED"  # the invoice is paid (wallet scan data) and the cap is held in the escrow account
    PAYMENT_EXPIRED = "PAYMENT_EXPIRED"  # the funding invoice expired unpaid
    PROOF_SUBMITTED = "PROOF_SUBMITTED"
    PROOF_REJECTED = "PROOF_REJECTED"
    VERIFIED = "VERIFIED"
    FIXED_RELEASED = "FIXED_RELEASED"  # fixed-fee payout decided and handed to the payout queue
    DISPUTED = "DISPUTED"
    COMPLETED = "COMPLETED"
    EXPIRED_REFUNDED = "EXPIRED_REFUNDED"
    ARCHIVED = "ARCHIVED"  # closed by the migration from the former EVM payment flow


# Every state change goes through DealService.save, which refuses anything not listed here.
DEAL_TRANSITIONS: dict[DealState, frozenset[DealState]] = {
    k: frozenset(v)
    for k, v in {
        DealState.NEGOTIATING: {DealState.AGREED, DealState.REJECTED, DealState.ESCALATED},
        DealState.ESCALATED: {DealState.REJECTED},
        DealState.AGREED: {DealState.AWAITING_APPROVAL, DealState.BLOCKED, DealState.REJECTED},
        DealState.AWAITING_APPROVAL: {DealState.AWAITING_FUNDING, DealState.REJECTED},
        DealState.AWAITING_FUNDING: {DealState.FUNDING, DealState.FUNDED, DealState.PAYMENT_EXPIRED},
        DealState.FUNDING: {DealState.FUNDED, DealState.PAYMENT_EXPIRED},
        DealState.FUNDED: {DealState.PROOF_SUBMITTED, DealState.PROOF_REJECTED, DealState.VERIFIED, DealState.EXPIRED_REFUNDED},
        DealState.PROOF_SUBMITTED: {DealState.PROOF_REJECTED, DealState.VERIFIED},
        DealState.PROOF_REJECTED: {DealState.PROOF_SUBMITTED, DealState.VERIFIED, DealState.EXPIRED_REFUNDED},
        DealState.VERIFIED: {DealState.FIXED_RELEASED, DealState.PROOF_REJECTED, DealState.DISPUTED},
        DealState.FIXED_RELEASED: {DealState.COMPLETED},
    }.items()
}  # fmt: skip


def transition_allowed(current: DealState, new: DealState) -> bool:
    return current == new or new in DEAL_TRANSITIONS.get(current, frozenset())


class Approval(BaseModel):
    """A human decision bound to the exact terms it approved."""

    kind: Literal["agreement", "compliance_review", "payment"]  # "payment": EVM-era wallet signature (history only)
    by: str  # user id
    approved: bool
    terms_hash: str
    note: str = ""
    at: datetime


class ProofCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class ContentResult(BaseModel):
    check_id: str
    kind: str
    requirement: str
    status: Literal["pass", "fail", "unverified"]  # unverified: the agent could not tell; a human decides
    method: Literal["captions", "vision", "none"]
    decisive: bool  # True only for deterministic results (caption counts); vision results are advisory
    observed: dict[str, Any] = Field(default_factory=dict)  # e.g. {"count": 3} or {"visible": true, "confidence": 0.8}
    evidence: list[str] = Field(default_factory=list)  # caption lines with timestamps, frame descriptions
    detail: str = ""


class ContentReport(BaseModel):
    """What the content agent extracted from the video and how each content check came out."""

    video_id: str
    analyzed_at: datetime
    transcript: Literal["manual_captions", "auto_captions", "unavailable"]
    transcript_language: str = ""
    transcript_note: str = ""
    frames: list[str] = Field(default_factory=list)  # image URLs analyzed
    vision: Literal["llm", "unavailable"] = "unavailable"
    results: list[ContentResult] = Field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        """Anything the deterministic checks could not settle goes to the advertiser's review."""
        return any(r.status == "unverified" or not r.decisive for r in self.results)


class ProofIssue(BaseModel):
    check: str
    problem: str
    fix: str
    fixable: bool = True  # a resubmission before the deadline can fix it


class ProofReview(BaseModel):
    """Why a proof was not accepted, shown to both parties. The checks decide; this only explains."""

    decided_by: Literal["verifier", "advertiser"]
    explained_by: Literal["rules", "llm"]  # who wrote `summary`; issues are always the fixed templates
    summary: str
    issues: list[ProofIssue] = Field(default_factory=list)
    can_resubmit: bool
    resubmit_by: datetime | None = None
    at: datetime


class Proof(BaseModel):
    video_id: str
    submitted_at: datetime
    checks: list[ProofCheck] = Field(default_factory=list)
    published_at: datetime | None = None
    views: int | None = None
    # The creator's own statements about what the API cannot see (e.g. an on-screen disclosure). Recorded, not verified.
    attestations: dict[str, bool] = Field(default_factory=dict)
    attempt: int = 1
    review: ProofReview | None = None  # set when this submission was rejected or sent back
    content: ContentReport | None = None  # the content agent's findings in the video itself


class Payout(BaseModel):
    """A payout decided for this deal by deterministic code. The money moves through its PayoutRequest
    (payments.models): an operator approves it and the Zcash wallet sends it; its delivery status lives there."""

    payout_id: str = ""  # PayoutRequest id; "" only on EVM-era history (see legacy_tx_hash)
    kind: Literal["fixed", "performance", "refund"]
    to: str
    amount: Decimal
    at: datetime
    views: int | None = None  # performance payouts: the verified view count the amount was computed from
    evidence_hash: str = ""
    simulated: bool = False
    legacy_tx_hash: str = ""  # payouts made by the removed EVM escrow, kept as read-only history

    @model_validator(mode="before")
    @classmethod
    def _legacy_evm_payout(cls, data: Any) -> Any:
        if isinstance(data, dict) and "payout_id" not in data and "tx_hash" in data:
            data = {**data, "legacy_tx_hash": data["tx_hash"]}
        return data


class Deal(BaseModel):
    id: str
    campaign_id: str
    influencer_id: str
    state: DealState = DealState.NEGOTIATING
    rounds: list[dict[str, Any]] = Field(default_factory=list)
    negotiation_outcome: str = ""  # why negotiation ended (agreed, round limit, no movement, escalated, ...)
    terms: DealTerms | None = None
    approved_terms_hash: str | None = None  # terms hash the payment authorization was drafted for
    approvals: list[Approval] = Field(default_factory=list)
    agreed_at: datetime | None = None
    funding_invoice_id: str | None = None  # shielded invoice that funds this deal (shared by the deals approved together)
    funded_at: datetime | None = None
    funding_simulated: bool = False
    proof: Proof | None = None
    proof_history: list[Proof] = Field(default_factory=list)  # earlier submissions, oldest first
    payouts: list[Payout] = Field(default_factory=list)
    archived_reason: str = ""
    fixed_release_due: datetime | None = None
    performance_due: datetime | None = None
    policy_violations: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class Campaign(BaseModel):
    spec: CampaignSpec
    status: Literal["planning", "negotiating", "awaiting_approval", "funding", "active", "done", "failed"] = "planning"
    created_at: datetime
    error: str = ""
    negotiation_cancelled: bool = False  # the advertiser stopped the creator queue: no further negotiations start


class AuditEvent(BaseModel):
    at: datetime
    campaign_id: str
    deal_id: str | None = None
    actor: str  # advertiser-agent, influencer-agent:<id>, policy, wallet, verifier, escrow, human
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)


NegotiationReply.model_rebuild()
