// One deal, as the creator sees it: what the brand wants, what it pays, where it stands, and the one action the
// creator can take (submit the video once the fixed fee is confirmed in their wallet). Offers are made and answered by the creator's agent
// inside the limits set on the My agent page, so there is no manual accept, reject or counter here.

import { Link, useParams } from "react-router";
import { api } from "../../api";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { DealDetail } from "../../components/DealDetail";
import { LiveNegotiation } from "../../components/LiveNegotiation";
import { LegacyPaymentHistory, MilestoneTable, PaymentLifecycle, PrivacyNote } from "../../components/Payments";
import { ProofForm } from "../../components/ProofForm";
import { RejectionDialog } from "../../components/RejectionDialog";
import { Requirements } from "../../components/Requirements";
import { Card, Notice, Spinner, StateBadge } from "../../components/ui";
import { DEAL_GROUPS, NEEDS_PROOF, awaitingFor, dealCurrency, dealGroup, latestOffer, receivedFor } from "../../creatorDeals";
import { DELIVERABLE_LABEL, LEGACY_PRICING_NOTE, isUsd, money, termsText, videoLength, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe } from "../../session";
import type { DealState, InfluencerDeal } from "../../types";
import "./deals.css";

/** What happens next, in the creator's words, for states where they wait on someone else. */
const NEXT: Partial<Record<DealState, string>> = {
  NEGOTIATING: "Your agent is negotiating with the brand's agent right now. Follow the conversation below.",
  ESCALATED: "An agent's policy handed this decision to a person. Nothing was agreed.",
  AGREED: "Your agent signed these terms. The brand now reviews and accepts them.",
  AWAITING_APPROVAL: "Terms agreed. Waiting for the brand to accept the deal.",
  BLOCKED: "The brand's policy refused this deal, so nothing was requested.",
  PAYMENT_REQUESTED: "Accepted. The brand has been asked to pay your fixed fee directly to your wallet.",
  PAYMENT_PENDING: "The brand's payment was seen arriving in your wallet. Your video window starts once it has enough confirmations.",
  PROOF_SUBMITTED: "Your video is being checked against the signed terms.",
  VERIFIED: "Your video passed. The performance fee is computed from views at day 7 (after the brand's review, if the campaign requires one).",
  PERFORMANCE_DUE: "The performance fee is computed from verified views at day 7 and requested from the brand, paid directly to your wallet.",
  DISPUTED: "The brand disputed this deal. You and the brand settle it between you; Zerveil holds no funds.",
  COMPLETED: "Every milestone is settled.",
  REJECTED: "No deal was made.",
  PAYMENT_EXPIRED: "The brand did not pay the fixed-fee request before it expired, so the deal is closed. Do not post for it.",
  UNDELIVERED: "No accepted video by the deadline. The fixed fee you already received stays in your wallet; no performance fee is due.",
  ARCHIVED: "This deal is from an earlier payment system and is kept only for the record.",
};

export function InfluencerDealPage() {
  const { id = "" } = useParams();
  const me = useMe().user;
  const deals = useResource(api.influencerDeals, "my-deals");
  const full = useResource(() => api.deal(id), `deal:${id}`); // adds the milestones' payment requests
  useLiveEvents(
    (e) => e.deal_id === id || e.type === "invoice",
    () => {
      void deals.reload();
      void full.reload();
    },
  );
  const deal = deals.data?.find((d) => d.id === id) ?? null;
  const crumbs = [
    { label: "Overview", to: "/influencer" },
    { label: "My deals", to: "/influencer/deals" },
  ];

  if (!deals.data) {
    return (
      <div className="stack">
        <Breadcrumbs items={[...crumbs, { label: "Deal" }]} />
        {deals.error ? (
          <div className="md-state card" role="alert">
            <strong>Could not load this deal</strong>
            <span className="muted">{deals.error}</span>
            <button type="button" className="btn btn-secondary" onClick={() => void deals.reload()}>
              Retry
            </button>
          </div>
        ) : (
          <Spinner />
        )}
      </div>
    );
  }
  if (!deal) {
    return (
      <div className="stack">
        <Breadcrumbs items={[...crumbs, { label: "Not found" }]} />
        <div className="md-state card">
          <strong>Deal not found</strong>
          <span className="muted">It is not one of your deals, or the link is wrong.</span>
          <Link to="/influencer/deals" className="btn btn-secondary">
            Back to My deals
          </Link>
        </div>
      </div>
    );
  }

  const c = deal.campaign;
  const group = DEAL_GROUPS.find((g) => g.key === dealGroup(deal.state))!;
  const review = deal.state === "PROOF_REJECTED" ? deal.proof?.review : null;
  const toProofForm = () => {
    const form = document.getElementById("md-next");
    form?.scrollIntoView({ behavior: "smooth", block: "start" });
    form?.querySelector<HTMLInputElement>("input.grow")?.focus({ preventScroll: true });
  };
  return (
    <div className="stack md-detail">
      <Breadcrumbs items={[...crumbs, { label: c.advertiser }]} />
      <header className="md-detail-head">
        <div>
          <h1>{c.advertiser}</h1>
          <p className="muted">
            {c.brand_handle} · {c.niche} · {DELIVERABLE_LABEL[c.deliverable] ?? c.deliverable}
          </p>
        </div>
        <div className="md-status">
          <span className="chip">{group.label}</span>
          <StateBadge state={deal.state} />
        </div>
      </header>
      {review && <RejectionDialog dealId={deal.id} review={review} onFix={toProofForm} />}

      <div className="md-split">
        <div className="md-main stack">
          {NEEDS_PROOF.has(deal.state) ? (
            <Card title={review ? "Fix and resubmit" : "Your turn: post the video"} className="md-next md-next-turn" id="md-next">
              {review && (
                <p className="md-why">
                  <strong>Rejected:</strong> {review.issues.map((i) => i.problem).join(" ") || review.summary}
                </p>
              )}
              <ProofForm key={deal.proof?.submitted_at ?? deal.id} deal={deal} onDone={() => void deals.reload()} />
            </Card>
          ) : (
            <Notice tone={group.key === "rejected" ? "warn" : group.key === "completed" ? "good" : "info"}>{NEXT[deal.state] ?? group.hint}</Notice>
          )}

          <Pay deal={deal} />

          {(deal.milestones.length > 0 || deal.payouts.length > 0) && (
            <Card title="Payments to your wallet">
              <PaymentLifecycle deal={deal} />
              {deal.milestones.length > 0 && <MilestoneTable deal={deal} invoices={full.data?.milestone_invoices} />}
              {deal.state === "ARCHIVED" && <LegacyPaymentHistory payouts={deal.payouts} />}
              <PrivacyNote compact />
            </Card>
          )}

          <details className="md-more card">
            <summary>Campaign and requirements</summary>
            {c.brief && <p className="brief">“{c.brief}”</p>}
            <dl className="kv">
              <dt>Deliverable</dt>
              <dd>
                {DELIVERABLE_LABEL[c.deliverable] ?? c.deliverable}, {videoLength(c.min_duration_seconds, c.max_duration_seconds).toLowerCase()}
              </dd>
              <dt>Required tags</dt>
              <dd>{(deal.terms?.required_tags ?? c.required_tags).join(" ") || "—"}</dd>
            </dl>
            <Requirements
              deliverable={c.deliverable}
              min={c.min_duration_seconds}
              max={c.max_duration_seconds}
              text={c.requirements}
              hash={deal.terms?.requirements_hash}
            />
          </details>

          <details className="md-more">
            <summary>Full deal record: terms, payment, proof checks</summary>
            <DealDetail deal={deal} title="Deal record" conversation={false} />
          </details>
        </div>

        <aside className="md-side">
          <Card title="Conversation">
            <LiveNegotiation key={deal.id} deal={deal} viewer="influencer" creator={me.name} avatar={me.picture} brand={deal.terms?.advertiser ?? c.advertiser} currency={dealCurrency(deal)} />
            {deal.state === "NEGOTIATING" && (
              <p className="small muted">
                Your agent decides within the limits on <Link to="/influencer/agent">My agent</Link>.
              </p>
            )}
          </Card>
        </aside>
      </div>
    </div>
  );
}

function paymentStatus(d: InfluencerDeal): string {
  const sim = d.payment_simulated ? " (simulated, no real payment)" : "";
  const fixed = d.milestones.find((m) => m.kind === "fixed");
  if (d.state === "ARCHIVED") return "Earlier payment system (history only)";
  if (d.state === "COMPLETED") return `Every milestone settled${sim}`;
  if (d.state === "PAYMENT_EXPIRED") return "The fixed-fee request expired unpaid";
  if (d.state === "PAYMENT_PENDING") return "Payment seen in your wallet, confirming";
  if (d.state === "PAYMENT_REQUESTED") return "Fixed fee requested from the brand";
  if (fixed?.status === "confirmed") return `Fixed fee confirmed in your wallet${sim}`;
  if (d.milestones.length === 0) return "Nothing requested yet";
  return "In progress";
}

/** Proposed vs agreed pay, what has been paid, and the dates that matter. */
function Pay({ deal }: { deal: InfluencerDeal }) {
  const offer = latestOffer(deal);
  const t = deal.terms;
  const paid = receivedFor(deal);
  const awaiting = awaitingFor(deal);
  const cur = dealCurrency(deal);
  return (
    <Card title="Pay">
      <dl className="kv">
        {offer && !t && (
          <>
            <dt>Latest offer</dt>
            <dd>
              {termsText(offer, offer.compensation_model, cur)}{" "}
              <span className="muted small">
                round {offer.round}/{offer.max_rounds}, from {offer.proposer === "influencer" ? "your agent" : "the brand's agent"}
              </span>
            </dd>
          </>
        )}
        {t && (
          <>
            <dt>Agreed terms</dt>
            <dd>
              <strong>{termsText(t, t.compensation_model, cur)}</strong>
              {!isUsd(cur) && <div className="muted small">{LEGACY_PRICING_NOTE}</div>}
            </dd>
          </>
        )}
        {!offer && !t && (
          <>
            <dt>Offer</dt>
            <dd className="muted">No offer yet</dd>
          </>
        )}
        <dt>Confirmed in your wallet</dt>
        <dd>{money(paid, cur)}</dd>
        {Number(awaiting) > 0 && (
          <>
            <dt>Requested, not yet confirmed</dt>
            <dd>{money(awaiting, cur)}</dd>
          </>
        )}
        <dt>Payment status</dt>
        <dd>{paymentStatus(deal)}</dd>
        <dt>Publish by</dt>
        <dd>{t ? when(t.publish_by) : offer ? `${offer.publish_within_days} days after payment` : "—"}</dd>
        {deal.performance_due && (
          <>
            <dt>Performance fee due</dt>
            <dd>{when(deal.performance_due)}</dd>
          </>
        )}
      </dl>
    </Card>
  );
}
