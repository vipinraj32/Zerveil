"""Deal lifecycle after negotiation: policy gate → advertiser accepts the agreement → fixed-fee invoice paid by the
advertiser directly to the creator's own shielded wallet → confirmed by the view-only scanner → content creation →
proof verification → performance milestone (computed from verified views at T+7d, paid the same way) → completed.

Zerveil holds no funds: it requests, verifies and records payments. Every transition is written to the audit log and
pushed to the participants' live streams."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..agent_policy import AdvertiserPolicy, DecisionRecord, calculate
from ..compliance import proof_checks as disclosure_checks
from ..feedback import performance_row
from ..models import (
    Approval,
    AuditEvent,
    Campaign,
    CampaignSpec,
    Deal,
    DealState,
    DealTerms,
    InfluencerProfile,
    Milestone,
    Offer,
    Proof,
    ProofCheck,
    new_id,
    transition_allowed,
)
from ..payments.models import Invoice
from ..payments.service import PaymentError, PaymentService
from ..policy import PolicyResult, check_milestone, check_terms, evidence_hash, performance_payout
from ..verifier.content import analyze_content
from ..verifier.explain import explain_rejection, explain_sent_back
from ..verifier.verify import all_passed, check_video
from ..verifier.youtube import parse_video_id
from .context import AppContext

log = logging.getLogger(__name__)

# Payment requested, not confirmed yet
PAYMENT_PENDING = {DealState.PAYMENT_REQUESTED, DealState.PAYMENT_PENDING}
# Entering these frees the deal's budget reservation: nothing more will be paid for it
RELEASES_BUDGET = {DealState.REJECTED, DealState.BLOCKED, DealState.PAYMENT_EXPIRED, DealState.ARCHIVED}
POLICY_KIND = "agent_policy"
# Audit kinds only the advertiser sees: they name the advertiser's limits or internal reasoning
ADVERTISER_PRIVATE_EVENTS = {"negotiation_move", "negotiation_decision", "policy_gate", "approval_decision", "campaign_full",
                             "discovered", "agent_unreachable", "lessons_applied", "milestone_refused"}  # fmt: skip


# Negotiation sessions (one per creator in a campaign, run one after another)
SESSION_TERMINAL = frozenset({"AGREED", "REJECTED", "TIMED_OUT", "FAILED", "CANCELLED"})
OUTCOME_STATUS = {
    "agreed": "AGREED",
    "rejected": "REJECTED",
    "walked_away": "REJECTED",
    "screened_out": "REJECTED",
    "escalated": "REJECTED",
    "timeout": "TIMED_OUT",  # round limit reached
    "no_reply": "TIMED_OUT",  # an agent did not answer within negotiation_reply_timeout_seconds
    "error": "FAILED",
    "interrupted": "FAILED",  # the server stopped mid-negotiation; it is never re-run
    "cancelled": "CANCELLED",
}


def _cap(offer: dict[str, Any] | None) -> Decimal:
    return Decimal(str(offer["cap"])) if offer else Decimal("0")


def campaign_progress(campaign: Campaign, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the campaign's creator queue stands, computed only from the persisted sessions. Agreed spend counts the
    caps (most each deal can pay) of AGREED negotiations; proposed spend is the latest offer on the table for every
    creator, agreed or not. Neither is a payment: nothing moves until the advertiser's own wallet signs."""

    def count(*statuses: str) -> int:
        return sum(1 for x in sessions if x["status"] in statuses)

    running = next((x for x in sessions if x["status"] == "RUNNING"), None)
    queued = count("QUEUED")
    if running or (queued and not campaign.negotiation_cancelled):
        workflow = "running"
    elif sessions:
        workflow = "cancelled" if campaign.negotiation_cancelled else "completed"
    else:
        workflow = "failed" if campaign.status == "failed" else "starting"
    return {
        "campaign_id": campaign.spec.id,
        "total_creators": len(sessions),
        "completed_count": count(*SESSION_TERMINAL),
        "agreed_count": count("AGREED"),
        "rejected_count": count("REJECTED"),
        "failed_count": count("FAILED", "TIMED_OUT"),
        "cancelled_count": count("CANCELLED"),
        "queued_count": queued,
        "current_creator_id": running["creator_id"] if running else None,
        "current_negotiation_id": running["negotiation_id"] if running else None,
        "workflow_status": workflow,
        "agreed_spend": str(sum((_cap(x["final_offer"]) for x in sessions if x["status"] == "AGREED"), Decimal("0"))),
        "proposed_spend": str(sum((_cap(x["latest_offer"]) for x in sessions), Decimal("0"))),
    }


class DealError(Exception):
    pass


class DealService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = asyncio.Lock()
        self._owners: dict[str, str] = {}  # campaign → advertiser user, for live-event routing
        self._deal_influencers: dict[str, str] = {}
        self._deal_locks: dict[str, asyncio.Lock] = {}
        self._negotiating: set[str] = set()  # deals whose negotiation loop is running in this process
        self._cancel: set[str] = set()
        self.payments = PaymentService(ctx)
        self.payments.on_status["milestone"] = self.on_milestone_invoice

    # ---- helpers -----------------------------------------------------------

    async def audit(self, campaign_id: str, deal_id: str | None, actor: str, kind: str, /, **detail: Any) -> None:
        event = AuditEvent(at=self.ctx.clock.now(), campaign_id=campaign_id, deal_id=deal_id, actor=actor, kind=kind,
                           detail=json.loads(json.dumps(detail, default=str)))  # fmt: skip
        await self.ctx.store.add_event(event)
        await self._publish({"type": "audit", "campaign_id": campaign_id, "deal_id": deal_id, "actor": actor, "kind": kind}, campaign_id, deal_id)

    async def save(self, deal: Deal, state: DealState | None = None) -> Deal:
        if state is not None:
            stored = await self.ctx.store.get_deal(deal.id)
            if stored is not None and not transition_allowed(stored.state, state):
                raise DealError(f"deal {deal.id}: {stored.state} → {state} is not allowed")
            deal.state = state
        deal.updated_at = self.ctx.clock.now()
        await self.ctx.store.save_deal(deal)
        if deal.state in RELEASES_BUDGET:
            await self.ctx.store.release_budget(deal.id)
        self._deal_influencers[deal.id] = deal.influencer_id
        await self._publish({"type": "deal", "campaign_id": deal.campaign_id, "deal_id": deal.id, "state": deal.state}, deal.campaign_id, deal.id)
        return deal

    async def save_campaign(self, campaign: Campaign) -> None:
        await self.ctx.store.save_campaign(campaign)
        self._owners[campaign.spec.id] = campaign.spec.owner_id
        await self._publish({"type": "campaign", "campaign_id": campaign.spec.id, "status": campaign.status}, campaign.spec.id, None)

    async def _publish(self, event: dict[str, Any], campaign_id: str, deal_id: str | None) -> None:
        if campaign_id not in self._owners and (c := await self.ctx.store.get_campaign(campaign_id)):
            self._owners[campaign_id] = c.spec.owner_id
        audience = {self._owners.get(campaign_id, "")}
        if deal_id:
            if deal_id not in self._deal_influencers and (d := await self.ctx.store.get_deal(deal_id)):
                self._deal_influencers[deal_id] = d.influencer_id
            audience.add(self._deal_influencers.get(deal_id, ""))
        audience.discard("")
        self.ctx.bus.publish(event, audience)

    async def get(self, deal_id: str) -> Deal:
        deal = await self.ctx.store.get_deal(deal_id)
        if deal is None:
            raise DealError(f"unknown deal {deal_id}")
        return deal

    async def campaign(self, campaign_id: str) -> Campaign:
        c = await self.ctx.store.get_campaign(campaign_id)
        if c is None:
            raise DealError(f"unknown campaign {campaign_id}")
        return c

    async def committed_spend(self, campaign_id: str, exclude: str | None = None) -> Decimal:
        """Caps reserved from the campaign budget by deals that are being, or were, paid."""
        return await self.ctx.store.reserved_budget(campaign_id, exclude=exclude)

    async def profile(self, influencer_id: str) -> InfluencerProfile:
        reg = await self.ctx.registry.get(influencer_id)
        if reg is None:
            raise DealError(f"influencer {influencer_id} is no longer active")
        return reg.profile

    async def notify_influencer(self, deal: Deal, message_type: str, /, **data: Any) -> None:
        """Tell the influencer's agent over A2A. Best effort: a down agent never blocks settlement."""
        reg = await self.ctx.registry.get(deal.influencer_id)
        if reg is None:
            return
        try:
            await self.ctx.a2a.send(reg.agent_url, {"type": message_type, "deal_id": deal.id, **json.loads(json.dumps(data, default=str))})
            await self.audit(deal.campaign_id, deal.id, "advertiser-agent", f"a2a_{message_type}_sent", **data)
        except Exception as e:  # noqa: BLE001
            log.warning("A2A notify %s failed: %s", message_type, e)

    async def refresh_campaign_status(self, campaign_id: str) -> None:
        c = await self.campaign(campaign_id)
        deals = await self.ctx.store.list_deals(campaign_id)
        states = {d.state for d in deals}
        if c.status in ("planning", "negotiating", "failed") and not (states - {DealState.NEGOTIATING}):
            return
        if not c.negotiation_cancelled and any(x["status"] in ("QUEUED", "RUNNING") for x in await self.negotiation_sessions(campaign_id)):
            return  # creators are still waiting their turn: the campaign is negotiating
        terminal = {DealState.REJECTED, DealState.BLOCKED, DealState.PAYMENT_EXPIRED, DealState.COMPLETED, DealState.UNDELIVERED,
                    DealState.ARCHIVED}  # fmt: skip
        if DealState.AWAITING_APPROVAL in states:
            status = "awaiting_approval"
        elif states & PAYMENT_PENDING:
            status = "awaiting_payment"
        elif states and states <= terminal:
            status = "done"
        else:
            status = "active"
        if status != c.status:
            c.status = status  # type: ignore[assignment]
            await self.save_campaign(c)

    # ---- negotiation bookkeeping ------------------------------------------

    async def advertiser_policy(self, spec: CampaignSpec) -> AdvertiserPolicy:
        """The advertiser policy version the campaign snapshotted at creation (defaults when it had none)."""
        if spec.agent_policy_version and spec.owner_id:
            doc = await self.ctx.store.get_version(POLICY_KIND, spec.owner_id, spec.agent_policy_version)
            if doc and doc.get("role") == "advertiser":
                return AdvertiserPolicy.model_validate(doc)
        return AdvertiserPolicy()

    async def record_decision(self, record: DecisionRecord | dict[str, Any]) -> None:
        """Stores a decision record for its owner. Only the public explanation ever reaches the other party."""
        rec = record if isinstance(record, DecisionRecord) else DecisionRecord.model_validate(record)
        await self.ctx.store.add_decision(rec.model_dump(mode="json"))
        actor = "advertiser-agent" if rec.party == "advertiser" else f"influencer-agent:{rec.actor_id}"
        kind = "negotiation_decision" if rec.party == "advertiser" else "influencer_decision"
        await self.audit(rec.campaign_id, rec.deal_id, actor, kind, decision_id=rec.id, action=rec.action, round=rec.round,
                         decided_by=rec.decided_by, policy_version=rec.policy_version)  # fmt: skip

    async def end_negotiation(self, deal: Deal, state: DealState, outcome: str) -> Deal:
        deal.negotiation_outcome = outcome
        deal = await self.save(deal, state)
        await self.record_performance(deal)
        return deal

    async def record_performance(self, deal: Deal) -> None:
        if (row := performance_row(deal, (await self.campaign(deal.campaign_id)).spec)) is not None:
            await self.ctx.store.save_performance(row)

    # ---- live negotiation transcript ------------------------------------------

    async def negotiation_event(self, deal: Deal, event_type: str, agent_role: str, agent_id: str, agent_name: str, /, *,
                                content: str = "", offer: Offer | None = None, round: int | None = None,
                                message_id: str | None = None, **extra: Any) -> dict[str, Any]:  # fmt: skip
        """Persists one transcript event, then pushes it to the browsers that may see the deal. Only what the two
        agents exchanged goes in here (public notes and offers): never private limits or decision rationale."""
        event = {
            "negotiation_id": deal.id,
            "campaign_id": deal.campaign_id,
            "creator_id": deal.influencer_id,
            "event_type": event_type,
            "agent_role": agent_role,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "message_id": message_id,
            "round": round if round is not None else (offer.round if offer else None),
            "timestamp": self.ctx.clock.now().isoformat(),
            "content": content,
            "offer": {k: str(getattr(offer, k)) for k in ("fixed_fee", "cpm", "cap")} | {"round": offer.round} if offer else None,
            **json.loads(json.dumps(extra, default=str)),
        }
        saved = await self.ctx.store.add_negotiation_event(event)
        await self._publish({"type": "negotiation", "campaign_id": deal.campaign_id, "deal_id": deal.id, "event": saved}, deal.campaign_id, deal.id)
        return saved

    def negotiation_started(self, deal_id: str) -> None:
        self._negotiating.add(deal_id)

    def negotiation_stopped(self, deal_id: str) -> None:
        self._negotiating.discard(deal_id)
        self._cancel.discard(deal_id)

    def cancel_requested(self, deal_id: str) -> bool:
        return deal_id in self._cancel

    async def cancel_negotiation(self, deal: Deal, by: str) -> bool:
        """Asks the running loop to stop before its next message. A negotiation with no live loop (the server
        restarted mid-way) is closed here directly. False when the negotiation already ended."""
        if deal.state != DealState.NEGOTIATING:
            return False
        await self.audit(deal.campaign_id, deal.id, "human", "negotiation_cancel_requested", by=by)
        if deal.id in self._negotiating:
            self._cancel.add(deal.id)
            return True
        await self.end_negotiation(deal, DealState.REJECTED, "cancelled by the advertiser")
        system = ("system", "zerveil", "Zerveil")
        await self.negotiation_event(deal, "negotiation_rejected", *system, content="Cancelled by the advertiser.", outcome="cancelled")
        session = next((x for x in await self.negotiation_sessions(deal.campaign_id) if x["negotiation_id"] == deal.id), None)
        if session is not None and session["status"] not in SESSION_TERMINAL:
            await self.finish_session(session, "cancelled", reason="cancelled by the advertiser")
        await self.negotiation_event(deal, "negotiation_completed", *system, outcome="cancelled")
        return True

    # ---- negotiation sessions: the campaign's creator queue ----------------------------
    # The campaign graph runs one session at a time (see agents/advertiser/graph.py). Every status change is a
    # compare-and-set on the stored status, so a cancel and a start can never both win, and nothing runs twice.

    async def queue_negotiations(self, spec: CampaignSpec, creator_ids: list[str]) -> list[dict[str, Any]]:
        """One QUEUED session per creator, in selection order. Re-queuing (a resumed run) keeps the sessions already stored."""
        unique = list(dict.fromkeys(creator_ids))  # the same creator is never negotiated with twice in one campaign
        rows = [
            {
                "negotiation_id": new_id("DEAL"),
                "campaign_id": spec.id,
                "creator_id": creator_id,
                "position": position,
                "advertiser_agent_id": f"advertiser-agent:{spec.owner_id}",
                "influencer_agent_id": f"influencer-agent:{creator_id}",
                "status": "QUEUED",
                "outcome": None,
                "current_round": 0,
                "maximum_rounds": spec.max_rounds,
                "initial_offer": None,
                "latest_offer": None,
                "final_offer": None,
                "queued_at": self.ctx.clock.now().isoformat(),
                "started_at": None,
                "completed_at": None,
                "failure_reason": "",
            }
            for position, creator_id in enumerate(unique)
        ]
        sessions = await self.ctx.store.queue_negotiations(rows)
        await self.publish_progress(spec.id)
        return sessions

    async def negotiation_sessions(self, campaign_id: str) -> list[dict[str, Any]]:
        return await self.ctx.store.list_negotiation_sessions(campaign_id)

    async def start_session(self, session: dict[str, Any]) -> dict[str, Any] | None:
        """QUEUED → RUNNING. None when someone else moved it first (cancelled, or another run started it)."""
        row = {**session, "status": "RUNNING", "started_at": self.ctx.clock.now().isoformat()}
        if not await self.ctx.store.save_negotiation_session(row, expect="QUEUED"):
            return None
        await self.publish_progress(session["campaign_id"])
        return row

    async def session_offer(self, session: dict[str, Any], offer: Offer) -> None:
        """Records the offer now on the table (either side) and the round it belongs to."""
        amounts = {"fixed_fee": str(offer.fixed_fee), "cpm": str(offer.cpm), "cap": str(offer.cap), "round": offer.round, "proposer": offer.proposer}
        session["latest_offer"] = amounts
        session["initial_offer"] = session["initial_offer"] or amounts
        session["current_round"] = max(session["current_round"], offer.round)
        await self.ctx.store.save_negotiation_session(session, expect="RUNNING")
        await self.publish_progress(session["campaign_id"])

    async def finish_session(self, session: dict[str, Any], outcome: str, *, final_offer: Offer | None = None, reason: str = "") -> dict[str, Any]:
        """The one place a creator's negotiation becomes terminal. The campaign graph then starts the next QUEUED creator."""
        status = OUTCOME_STATUS.get(outcome, "FAILED")
        session.update(status=status, outcome=outcome, completed_at=self.ctx.clock.now().isoformat(),
                       failure_reason=reason if status in ("FAILED", "TIMED_OUT", "CANCELLED") else "")  # fmt: skip
        if final_offer is not None:
            session["final_offer"] = {"fixed_fee": str(final_offer.fixed_fee), "cpm": str(final_offer.cpm), "cap": str(final_offer.cap),
                                      "round": final_offer.round, "offer": final_offer.model_dump(mode="json")}  # fmt: skip
        if not await self.ctx.store.save_negotiation_session(session, expect="RUNNING"):
            await self.ctx.store.save_negotiation_session(session)  # not RUNNING (an interrupted run being closed): still record it
        await self.audit(session["campaign_id"], session["negotiation_id"], "advertiser-agent", "negotiation_finished",
                         creator=session["creator_id"], status=status, outcome=outcome)  # fmt: skip
        await self.publish_progress(session["campaign_id"])
        return session

    async def abandon_session(self, session: dict[str, Any], reason: str,
                              message: str = "This negotiation stopped when the server restarted. Nothing was agreed or paid.") -> dict[str, Any]:  # fmt: skip
        """A session left RUNNING by a run that died (server restart). Re-running it could repeat what the agents already
        said and signed, so it is closed as FAILED with a visible message, and the queue moves on."""
        deal = await self.ctx.store.get_deal(session["negotiation_id"])
        if deal is None or deal.state != DealState.NEGOTIATING:
            return await self.finish_session(session, "interrupted", reason=reason)
        await self.end_negotiation(deal, DealState.REJECTED, reason)
        system = ("system", "zerveil", "Zerveil")
        await self.negotiation_event(deal, "negotiation_error", *system, content=message, outcome="error", error="interrupted")
        await self.finish_session(session, "interrupted", reason=reason)
        await self.negotiation_event(deal, "negotiation_completed", *system, outcome="error")
        return session

    async def cancel_campaign_negotiations(self, campaign: Campaign, by: str) -> dict[str, Any]:
        """Stops the creator queue: QUEUED sessions become CANCELLED and the running one is asked to stop before its
        next message. Agreements already reached stay; they still need the advertiser's signature to be paid."""
        campaign.negotiation_cancelled = True
        await self.save_campaign(campaign)
        await self.audit(campaign.spec.id, None, "human", "negotiations_cancelled", by=by)
        for x in await self.negotiation_sessions(campaign.spec.id):
            if x["status"] == "QUEUED":
                row = {**x, "status": "CANCELLED", "outcome": "cancelled", "completed_at": self.ctx.clock.now().isoformat(),
                       "failure_reason": "cancelled by the advertiser before it started"}  # fmt: skip
                await self.ctx.store.save_negotiation_session(row, expect="QUEUED")
            elif x["status"] == "RUNNING" and (deal := await self.ctx.store.get_deal(x["negotiation_id"])) is not None:
                await self.cancel_negotiation(deal, by)
        await self.publish_progress(campaign.spec.id)
        return await self.progress(campaign.spec.id)

    async def progress(self, campaign_id: str) -> dict[str, Any]:
        return campaign_progress(await self.campaign(campaign_id), await self.negotiation_sessions(campaign_id))

    async def publish_progress(self, campaign_id: str) -> None:
        event = {"type": "negotiation_progress", "campaign_id": campaign_id, "progress": await self.progress(campaign_id)}
        await self._publish(event, campaign_id, None)  # the advertiser only: other creators' terms are not the creator's business

    async def record_approval(self, deal_id: str, kind: str, by: str, approved: bool, note: str = "") -> Deal:
        deal = await self.get(deal_id)
        terms = self._terms(deal)
        deal.approvals.append(Approval(kind=kind, by=by, approved=approved, terms_hash=terms.terms_hash(), note=note, at=self.ctx.clock.now()))  # type: ignore[arg-type]
        return await self.save(deal)

    def negotiation_view(self, deal: Deal, spec: CampaignSpec) -> dict[str, Any]:
        """The Negotiation Room: structured offers, what moved between rounds, the payable breakdown and the outcome.
        Built from what both parties already exchanged; no private limits or decision records."""

        def amounts(o: dict[str, Any] | None) -> dict[str, str] | None:
            return {k: o[k] for k in ("fixed_fee", "cpm", "cap")} if o else None

        def delta(a: dict[str, str] | None, b: dict[str, str] | None) -> dict[str, str] | None:
            if not a or not b:
                return None
            return {k: str(Decimal(b[k]) - Decimal(a[k])) for k in ("fixed_fee", "cpm")}

        rounds, prev_offer, prev_counter = [], None, None
        for r in deal.rounds:
            offer, reply = amounts(r["offer"]), r["reply"]
            counter = amounts(reply.get("counter")) if reply.get("kind") == "counter" else None
            rounds.append({
                "round": r["offer"]["round"],
                "advertiser_offer": offer,
                "advertiser_moved": delta(prev_offer, offer),
                "reply": reply.get("kind"),
                "explanation": reply.get("reason", ""),
                "counter": counter,
                "creator_moved": delta(prev_counter, counter),
            })  # fmt: skip
            prev_offer, prev_counter = offer, counter or prev_counter
        latest = deal.terms or (deal.rounds[-1]["offer"] if deal.rounds else None)
        breakdown = None
        if latest is not None:
            t = latest if isinstance(latest, dict) else latest.model_dump(mode="json")
            fixed, cpm, cap = Decimal(t["fixed_fee"]), Decimal(t["cpm"]), Decimal(t["cap"])
            model = t.get("compensation_model", spec.compensation_model)
            views_to_cap = int(((cap - fixed) / cpm * 1000).to_integral_value(rounding="ROUND_CEILING")) if cpm > 0 and cap > fixed else None
            samples = sorted({0, 10_000, *([views_to_cap] if views_to_cap else [])})
            breakdown = {"model": model, "views_to_cap": views_to_cap,
                         "examples": [calculate(model, fixed, cpm, v, cap).as_dict() for v in samples]}  # fmt: skip
        status = {DealState.NEGOTIATING: "open", DealState.REJECTED: "failed", DealState.ESCALATED: "escalated"}.get(deal.state, "agreed")
        if deal.state == DealState.REJECTED and deal.terms is not None:
            status = "agreed_then_cancelled"
        return {
            "deal_id": deal.id,
            "status": status,
            "outcome": deal.negotiation_outcome,
            "max_rounds": spec.max_rounds,
            "rounds_used": len(deal.rounds),
            "rounds_remaining": max(0, spec.max_rounds - len(deal.rounds)) if deal.state == DealState.NEGOTIATING else 0,
            "compensation_model": spec.compensation_model,
            "scope": spec.scope.model_dump(),
            "rounds": rounds,
            "payable": breakdown,
            "agreement": {
                "terms": deal.terms.model_dump(mode="json"),
                "terms_hash": deal.terms.terms_hash(),
                "approved_terms_hash": deal.approved_terms_hash,
                "approvals": [a.model_dump(mode="json") for a in deal.approvals],
            }
            if deal.terms
            else None,  # fmt: skip
        }

    async def compliance_view(self, spec: CampaignSpec) -> dict[str, Any]:
        """Campaign Compliance & Audit: which disclosure policy versions apply, and each deal's gates."""
        from ..compliance import DISCLAIMER

        deals = []
        for d in await self.ctx.store.list_deals(spec.id):
            if d.terms is None:
                continue
            checks = [c.model_dump() for c in (d.proof.checks if d.proof else []) if c.name.startswith("disclosure")]
            h = d.terms.terms_hash()
            review = next((a for a in reversed(d.approvals) if a.kind == "compliance_review" and a.terms_hash == h), None)
            payment = next((a for a in reversed(d.approvals) if a.kind == "agreement" and a.terms_hash == h), None)
            gates = []
            if d.proof is None:
                gates.append("waiting for the post")
            elif not all(c["passed"] for c in checks):
                gates.append("disclosure checks failed")
            if self.review_pending(d, spec) and d.state == DealState.VERIFIED:
                gates.append("advertiser review pending" + (" (content agent could not settle every check)" if not spec.review_required else ""))
            deals.append({
                "deal_id": d.id,
                "influencer_id": d.influencer_id,
                "state": d.state,
                "disclosure_policies": d.terms.disclosure_policies,
                "checks": checks,
                "attestations": d.proof.attestations if d.proof else {},
                "review": "not_required" if not self.review_required(d, spec) else (("approved" if review.approved else "rejected") if review else "pending"),
                "payment_approval": payment.model_dump(mode="json") if payment else None,
                "terms_hash": h,
                "terms_hash_matches_approval": d.approved_terms_hash in (None, h),
                "gates": gates,
                "payment_simulated": d.payment_simulated,
                "milestones": [m.model_dump(mode="json", exclude={"evidence"}) for m in d.milestones],
            })  # fmt: skip
        return {
            "campaign_id": spec.id,
            "jurisdictions": spec.jurisdictions,
            "required_tags": spec.required_tags,
            "review_required": spec.review_required,
            "policies": [p.model_dump(mode="json") for p in self.ctx.disclosure.resolve_all(spec.disclosure_policies)],
            "disclaimer": DISCLAIMER,
            "settlement_simulated": self.ctx.wallet.simulated,
            "deals": deals,
        }

    async def record_round(self, deal: Deal, offer: Offer, reply: dict[str, Any], actor: str) -> None:
        deal.rounds.append({"offer": offer.model_dump(mode="json"), "reply": reply})
        await self.save(deal)
        await self.ctx.store.add_negotiation_round({"deal_id": deal.id, "campaign_id": deal.campaign_id, "round": offer.round,
                                                    "at": self.ctx.clock.now().isoformat(), "offer": deal.rounds[-1]["offer"], "reply": reply})  # fmt: skip
        await self.audit(deal.campaign_id, deal.id, actor, "negotiation_round", round=offer.round,
                         fixed=offer.fixed_fee, cpm=offer.cpm, reply=reply.get("kind"), reason=reply.get("reason", ""))  # fmt: skip

    # ---- policy gate → advertiser acceptance → milestone invoices paid directly to the creator ---------------

    async def gate(self, deal: Deal, spec: CampaignSpec, agreed: Offer) -> PolicyResult:
        """Everything that must hold before the advertiser is asked to pay a creator. Failure → BLOCKED.
        Passing reserves the deal's cap from the campaign budget atomically (store.reserve_budget)."""
        terms = self._terms(deal)
        result = check_terms(spec, terms, await self.profile(deal.influencer_id), agreed, self.ctx.settings.zcash_network)  # type: ignore[arg-type]
        if result.ok and not await self.ctx.store.reserve_budget(spec.id, deal.id, terms.cap, spec.budget):
            result.violations.append(f"budget exceeded: another deal reserved the remaining budget of {spec.budget}")
        await self.audit(deal.campaign_id, deal.id, "policy", "policy_gate", ok=result.ok, violations=result.violations)
        if not result.ok:
            deal.policy_violations = result.violations
            await self.save(deal, DealState.BLOCKED)
        else:
            deal.approved_terms_hash = terms.terms_hash()  # the payment request covers exactly these terms
            await self.save(deal, DealState.AWAITING_APPROVAL)
        return result

    def _evidence(self, deal: Deal, kind: str, amount: Decimal, views: int | None) -> dict[str, Any]:
        terms = self._terms(deal)
        return {"deal_id": deal.id, "terms_hash": terms.terms_hash(), "kind": kind, "amount": str(amount), "views": views,
                "video_id": deal.proof.video_id if deal.proof else None,
                "checks": [c.model_dump() for c in deal.proof.checks] if deal.proof else []}  # fmt: skip

    async def _request_milestone(self, deal: Deal, kind: str, amount: Decimal, *, views: int | None = None, by: str = "scheduler") -> Deal:
        """Decides one milestone payment and issues its invoice: paid by the advertiser, from their own wallet,
        directly to a fresh address of the creator's own receiving account. Idempotent per deal and kind."""
        terms = self._terms(deal)
        spec = (await self.campaign(deal.campaign_id)).spec
        if (existing := deal.milestone(kind)) is not None and existing.invoice_id:
            return deal
        check = check_milestone(terms, kind, amount)
        if not check.ok:
            await self.audit(deal.campaign_id, deal.id, "policy", "milestone_refused", kind=kind, violations=check.violations)
            raise DealError("; ".join(check.violations))
        evidence = self._evidence(deal, kind, amount, views)
        milestone = existing or Milestone(id=new_id("MS"), kind=kind, amount=amount)  # type: ignore[arg-type]
        milestone.amount, milestone.views, milestone.evidence, milestone.evidence_hash = amount, views, evidence, evidence_hash(evidence)
        title = f"{'Fixed fee' if kind == 'fixed' else 'Performance fee'}: {spec.advertiser} sponsorship"
        invoice = await self.payments.create_invoice(
            "milestone", spec.owner_id, deal.influencer_id, title, amount, campaign_id=deal.campaign_id, deal_ids=[deal.id],
            milestone_id=milestone.id, description="Paid directly to the creator's own shielded wallet. Zerveil never holds the funds.",
        )  # fmt: skip
        milestone.invoice_id, milestone.status, milestone.requested_at = invoice.id, "requested", self.ctx.clock.now()
        if existing is None:
            deal.milestones.append(milestone)
        deal = await self.save(deal)
        await self.audit(deal.campaign_id, deal.id, "payments", "milestone_requested", milestone=milestone.id, kind=kind, amount=amount,
                         invoice=invoice.id, views=views, evidence_hash=milestone.evidence_hash, by=by, simulated=invoice.simulated)  # fmt: skip
        await self.notify_influencer(deal, "payment_requested", kind=kind, amount=amount, invoice=invoice.id, simulated=invoice.simulated)
        return deal

    async def request_payment(self, campaign_id: str, deal_ids: list[str], by: str) -> list[Invoice]:
        """The advertiser accepted the agreements: one fixed-fee invoice per creator, each payable straight to that
        creator's wallet. A deal moves on to content creation only once the scanner confirms its payment."""
        campaign = await self.campaign(campaign_id)
        deals = [await self.get(i) for i in deal_ids]
        for d in deals:
            if d.state != DealState.AWAITING_APPROVAL:
                raise DealError(f"deal {d.id} is {d.state}, not awaiting approval")
            if d.approved_terms_hash != self._terms(d).terms_hash():
                raise DealError(f"deal {d.id}: the terms changed after the policy gate")
            creator = await self.ctx.store.get_user(d.influencer_id)
            if creator is None or not creator.receiving_account_id:
                raise DealError(f"deal {d.id}: the creator has not connected a receiving account, so a payment could not be confirmed")
        committed = await self.committed_spend(campaign_id)
        if committed > campaign.spec.budget:
            raise DealError(f"budget exceeded: {committed} > {campaign.spec.budget}")
        invoices: list[Invoice] = []
        for d in deals:
            fixed = self._terms(d).fixed_fee
            if fixed <= 0:  # CPM-only deals: nothing is due up front
                await self.save(d, DealState.CONTENT_CREATION)
                await self.audit(campaign_id, d.id, "payments", "no_upfront_payment", reason="CPM-only deal: no fixed fee")
                continue
            d = await self._request_milestone(d, "fixed", fixed, by=by)
            await self.save(d, DealState.PAYMENT_REQUESTED)
            milestone = d.milestone("fixed")
            assert milestone is not None and milestone.invoice_id
            invoices.append(await self.payments.get_invoice(milestone.invoice_id))
        await self.audit(campaign_id, None, "advertiser-agent", "payment_requested", invoices=[i.id for i in invoices], deals=deal_ids, by=by,
                         total=sum((i.amount for i in invoices), Decimal("0")))  # fmt: skip
        await self.refresh_campaign_status(campaign_id)
        return invoices

    async def reject_agreements(self, campaign_id: str, deal_ids: list[str], by: str) -> None:
        for deal_id in deal_ids:
            deal = await self.get(deal_id)
            if deal.state == DealState.AWAITING_APPROVAL:
                await self.save(deal, DealState.REJECTED)
        await self.audit(campaign_id, None, "human", "agreements_rejected", deals=deal_ids, by=by)
        await self.refresh_campaign_status(campaign_id)

    async def on_milestone_invoice(self, invoice: Invoice) -> None:
        """Called by the payment scanner whenever a milestone invoice changes status."""
        for deal_id in invoice.deal_ids:
            async with self._deal_lock(deal_id):
                deal = await self.ctx.store.get_deal(deal_id)
                if deal is None:
                    continue
                milestone = next((m for m in deal.milestones if m.invoice_id == invoice.id), None)
                if milestone is None:
                    continue
                now = self.ctx.clock.now()
                if invoice.status in ("seen", "mined", "confirming") and milestone.status == "requested":
                    milestone.status = "pending"
                    if milestone.kind == "fixed" and deal.state == DealState.PAYMENT_REQUESTED:
                        await self.save(deal, DealState.PAYMENT_PENDING)
                    else:
                        await self.save(deal)
                    await self.audit(deal.campaign_id, deal.id, "scanner", "payment_detected", milestone=milestone.id, invoice=invoice.id,
                                     received=invoice.received, confirmations=invoice.confirmations, simulated=invoice.simulated)  # fmt: skip
                elif invoice.is_paid and milestone.status in ("requested", "pending"):
                    milestone.status, milestone.confirmed_at, milestone.receipt_id = "confirmed", now, invoice.receipt_id
                    deal.payment_simulated = deal.payment_simulated or invoice.simulated
                    await self.audit(deal.campaign_id, deal.id, "scanner", "payment_confirmed", milestone=milestone.id, kind=milestone.kind,
                                     invoice=invoice.id, received=invoice.received, confirmations=invoice.confirmations,
                                     receipt=invoice.receipt_id, simulated=invoice.simulated)  # fmt: skip
                    if milestone.kind == "fixed" and deal.state in (DealState.PAYMENT_REQUESTED, DealState.PAYMENT_PENDING):
                        await self.save(deal, DealState.CONTENT_CREATION)
                        await self.notify_influencer(deal, "payment_confirmed", kind="fixed", amount=invoice.received, receipt=invoice.receipt_id,
                                                     simulated=invoice.simulated)  # fmt: skip
                    elif milestone.kind == "performance" and deal.state == DealState.PERFORMANCE_DUE:
                        await self.save(deal, DealState.COMPLETED)
                        await self.audit(deal.campaign_id, deal.id, "payments", "deal_completed", views=milestone.views)
                    else:
                        await self.save(deal)
                elif invoice.status in ("expired", "cancelled") and milestone.status == "requested":
                    milestone.status = "expired"
                    await self.audit(deal.campaign_id, deal.id, "scanner", "payment_expired", milestone=milestone.id, kind=milestone.kind,
                                     invoice=invoice.id, received=invoice.received)  # fmt: skip
                    if milestone.kind == "fixed" and deal.state in (DealState.PAYMENT_REQUESTED, DealState.PAYMENT_PENDING):
                        await self.save(deal, DealState.PAYMENT_EXPIRED)
                    elif milestone.kind == "performance" and deal.state == DealState.PERFORMANCE_DUE:
                        await self.save(deal, DealState.COMPLETED)
                        await self.audit(deal.campaign_id, deal.id, "payments", "deal_completed", views=milestone.views,
                                         note="the performance invoice expired unpaid")  # fmt: skip
                    else:
                        await self.save(deal)
        if invoice.campaign_id:
            await self.refresh_campaign_status(invoice.campaign_id)

    async def payment_summary(self, campaign_id: str) -> dict[str, str]:
        """Budget is a spending limit, not money held: reserved = caps of deals that may still be paid or were paid;
        paid = milestone payments the scanner confirmed in creators' wallets."""
        spec = (await self.campaign(campaign_id)).spec
        reserved = await self.ctx.store.reserved_budget(campaign_id)
        deals = await self.ctx.store.list_deals(campaign_id)
        paid = sum((m.amount for d in deals for m in d.milestones if m.status == "confirmed"), Decimal("0"))
        requested = sum((m.amount for d in deals for m in d.milestones if m.status in ("requested", "pending")), Decimal("0"))
        return {"budget": str(spec.budget), "reserved": str(reserved), "paid": str(paid), "requested": str(requested),
                "remaining": str(spec.budget - reserved)}  # fmt: skip

    def _deal_lock(self, deal_id: str) -> asyncio.Lock:
        return self._deal_locks.setdefault(deal_id, asyncio.Lock())

    # ---- proof + performance milestone ----------------------------------------------------------------------

    async def submit_proof(self, deal_id: str, video_ref: str, attestations: dict[str, bool] | None = None) -> Deal:
        deal = await self.get(deal_id)
        if deal.state not in (DealState.CONTENT_CREATION, DealState.PROOF_REJECTED):
            raise DealError(f"deal is {deal.state}, not awaiting proof")
        video_id = parse_video_id(video_ref)
        if video_id is None:
            raise DealError("not a YouTube URL or video id")
        if self.ctx.videos is None:
            raise DealError("the verifier is offline: set YOUTUBE_API_KEY")
        if self.ctx.clock.now() > self._terms(deal).publish_by:
            raise DealError("the publish deadline has passed")
        spec = (await self.campaign(deal.campaign_id)).spec
        video = await self.ctx.videos.get_video(video_id)
        assert deal.agreed_at is not None
        terms = self._terms(deal)
        checks = check_video(terms, video, deal.agreed_at, spec.brand_handle)
        if video is not None:
            checks += self._disclosure_checks(terms, video, attestations or {})
        now = self.ctx.clock.now()
        content = None
        if video is not None and spec.content_checks:
            content = await analyze_content(spec.content_checks, video_id, self.ctx.captions, self.ctx.frames, self.ctx.llm, now)
            # Only deterministic results (caption counts) decide; vision and "unverified" go to the advertiser's review
            checks += [ProofCheck(name=f"content:{r.check_id}", passed=r.status == "pass", detail=r.detail) for r in content.results if r.decisive]
        if deal.proof is not None:
            deal.proof_history.append(deal.proof)
        deal.proof = Proof(video_id=video_id, submitted_at=now, checks=checks, attestations=attestations or {}, attempt=len(deal.proof_history) + 1,
                           published_at=video.published_at if video else None, views=video.views if video else None, content=content)  # fmt: skip
        passed = all_passed(checks)
        if content is not None:
            await self.audit(deal.campaign_id, deal.id, "content-agent", "content_checked", video_id=video_id, transcript=content.transcript,
                             results={r.check_id: r.status for r in content.results}, needs_review=content.needs_human)  # fmt: skip
        await self.audit(deal.campaign_id, deal.id, "verifier", "proof_checked", video_id=video_id, passed=passed, attempt=deal.proof.attempt,
                         failed=[f"{c.name}: {c.detail}" for c in checks if not c.passed])  # fmt: skip
        if not passed:
            review = await explain_rejection(checks, terms, spec.brand_handle, deal.agreed_at, now, deal.proof.published_at, self.ctx.llm)
            deal.proof.review = review
            await self.audit(deal.campaign_id, deal.id, "verifier", "proof_rejected", summary=review.summary, can_resubmit=review.can_resubmit,
                             resubmit_by=review.resubmit_by, issues=[i.check for i in review.issues])  # fmt: skip
            return await self.save(deal, DealState.PROOF_REJECTED)
        terms = self._terms(deal)
        # The performance fee waits for the day-7 views; that re-check also catches a video taken down meanwhile
        assert video is not None
        deal.performance_due = max(video.published_at + timedelta(days=terms.views_measured_after_days), now)
        await self.save(deal, DealState.VERIFIED)
        deal = await self.advance_due(deal.id)
        await self.refresh_campaign_status(deal.campaign_id)
        return deal

    def _disclosure_checks(self, terms: DealTerms, video: Any, attestations: dict[str, bool]) -> list[ProofCheck]:
        policies = self.ctx.disclosure.resolve_all(terms.disclosure_policies)
        unknown = [i for i in terms.disclosure_policies if self.ctx.disclosure.resolve(i) is None]
        checks = disclosure_checks(policies, video, attestations)
        if unknown:
            checks.append(ProofCheck(name="disclosure_policy_known", passed=False, detail=f"unknown disclosure policy {unknown}"))
        return checks

    async def compliance_review(self, deal_id: str, reviewer: str, approved: bool, note: str) -> Deal:
        """The advertiser's disclosure review of a verified post. Approval accepts the content (the performance
        milestone follows at T+7d); rejection sends the proof back, and the creator can resubmit until the deadline."""
        deal = await self.get(deal_id)
        if deal.state != DealState.VERIFIED:
            raise DealError(f"deal is {deal.state}: only verified posts can be reviewed")
        deal = await self.record_approval(deal_id, "compliance_review", reviewer, approved, note)
        await self.audit(deal.campaign_id, deal.id, "human", "compliance_reviewed", approved=approved, note=note)
        if not approved:
            assert deal.proof is not None
            deal.proof.review = explain_sent_back(note, self._terms(deal), self.ctx.clock.now())
            await self.audit(deal.campaign_id, deal.id, "human", "proof_rejected", summary=deal.proof.review.summary,
                             can_resubmit=deal.proof.review.can_resubmit, resubmit_by=deal.proof.review.resubmit_by)  # fmt: skip
            deal = await self.save(deal, DealState.PROOF_REJECTED)
        else:
            deal = await self.advance_due(deal.id)  # the review was the only thing holding the content back
            await self.refresh_campaign_status(deal.campaign_id)
        return deal

    @staticmethod
    def review_required(deal: Deal, spec: CampaignSpec) -> bool:
        """The advertiser reviews when the campaign asks for it or the content agent could not settle a check."""
        return spec.review_required or bool(deal.proof and deal.proof.content and deal.proof.content.needs_human)

    def review_pending(self, deal: Deal, spec: CampaignSpec) -> bool:
        if not self.review_required(deal, spec) or deal.terms is None:
            return False
        h = deal.terms.terms_hash()
        return not any(a.kind == "compliance_review" and a.approved and a.terms_hash == h for a in deal.approvals)

    async def dispute(self, deal_id: str, reason: str) -> Deal:
        deal = await self.get(deal_id)
        if deal.state != DealState.VERIFIED:
            raise DealError("disputes are only possible while the post awaits your review")
        await self.audit(deal.campaign_id, deal.id, "human", "disputed", reason=reason)
        return await self.save(deal, DealState.DISPUTED)

    async def tick(self) -> int:
        """Scheduler: scan watched accounts for payments, then decide milestones and deadlines that are due.
        Returns the number of actions taken."""
        actions = await self.payments.scan() if self.ctx.settings.zcash_payment_scanner_enabled or self.ctx.wallet.simulated else 0
        now = self.ctx.clock.now()
        for deal in await self.ctx.store.list_deals():
            if not await self._due(deal, now):
                continue
            before = deal.state
            deal = await self.advance_due(deal.id)
            if deal.state != before:
                actions += 1
            await self.refresh_campaign_status(deal.campaign_id)
        return actions

    async def _due(self, deal: Deal, now: datetime) -> bool:
        if deal.state in (DealState.CONTENT_CREATION, DealState.PROOF_REJECTED):
            return now > self._terms(deal).publish_by
        if deal.state == DealState.VERIFIED:
            # the advertiser's disclosure review, when the campaign asks for one, gates the performance milestone
            return not self.review_pending(deal, (await self.campaign(deal.campaign_id)).spec)
        if deal.state == DealState.PERFORMANCE_DUE:
            m = deal.milestone("performance")
            return (m is None or m.status == "scheduled") and bool(deal.performance_due and now >= deal.performance_due)
        return False

    async def advance_due(self, deal_id: str) -> Deal:
        """Moves one deal on when something is due. Callers race (the scheduler, a verified proof, an approved
        review): the deal lock, a fresh read and the per-deal milestone check make sure each step happens once."""
        async with self._deal_lock(deal_id):
            deal = await self.get(deal_id)
            if not await self._due(deal, self.ctx.clock.now()):
                return deal
            try:
                if deal.state == DealState.VERIFIED:
                    await self.save(deal, DealState.PERFORMANCE_DUE)
                    await self.audit(deal.campaign_id, deal.id, "verifier", "content_accepted", performance_due=deal.performance_due)
                elif deal.state == DealState.PERFORMANCE_DUE:
                    await self._performance_milestone(deal)
                else:
                    await self.save(deal, DealState.UNDELIVERED)
                    await self.audit(deal.campaign_id, deal.id, "verifier", "deadline_missed", publish_by=self._terms(deal).publish_by,
                                     note="no accepted post by the deadline; the fixed fee was already paid to the creator, so the "
                                     "advertiser and creator settle it between them (Zerveil holds no funds)")  # fmt: skip
            except (PaymentError, DealError) as e:
                await self.audit(deal.campaign_id, deal.id, "payments", "milestone_error", reason=str(e))
            return await self.get(deal_id)

    async def _recheck(self, deal: Deal) -> tuple[bool, int]:
        assert deal.proof is not None and deal.agreed_at is not None
        spec = (await self.campaign(deal.campaign_id)).spec
        if self.ctx.videos is None:
            raise DealError("the verifier is offline: set YOUTUBE_API_KEY")
        video = await self.ctx.videos.get_video(deal.proof.video_id)
        checks = check_video(self._terms(deal), video, deal.agreed_at, spec.brand_handle)
        return all_passed(checks), video.views if video else 0

    async def _performance_milestone(self, deal: Deal) -> None:
        """At T+7d: re-check the post, compute the performance fee from verified views (deterministic), and request
        it. Nothing due → the deal completes."""
        still_ok, views = await self._recheck(deal)
        amount = performance_payout(self._terms(deal), views) if still_ok else Decimal("0")
        if not still_ok:
            await self.audit(deal.campaign_id, deal.id, "verifier", "post_removed_before_performance", views=views)
        if amount > 0:
            await self._request_milestone(deal, "performance", amount, views=views)
            return
        evidence = self._evidence(deal, "performance", Decimal("0"), views)
        deal.milestones.append(Milestone(id=new_id("MS"), kind="performance", amount=Decimal("0"), status="waived", views=views,
                                         evidence=evidence, evidence_hash=evidence_hash(evidence)))  # fmt: skip
        await self.save(deal, DealState.COMPLETED)
        await self.audit(deal.campaign_id, deal.id, "payments", "deal_completed", views=views, performance=Decimal("0"))

    @staticmethod
    def _terms(deal: Deal) -> DealTerms:
        if deal.terms is None:
            raise DealError(f"deal {deal.id} has no agreed terms")
        return deal.terms
