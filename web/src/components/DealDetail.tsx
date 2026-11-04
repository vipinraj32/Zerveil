import type { ReactNode } from "react";
import { api } from "../api";
import { LEGACY_PRICING_NOTE, MODEL_LABEL, humanize, isUsd, money, num, sumUsd, termsCurrency, videoLength, when } from "../format";
import { useLiveEvents, useResource } from "../live";
import { useMe } from "../session";
import type { Deal } from "../types";
import { ContentReportView } from "./ContentChecks";
import { LiveNegotiation } from "./LiveNegotiation";
import { NegotiationRoom } from "./NegotiationRoom";
import { LegacyPaymentHistory, MilestoneTable, PaymentLifecycle } from "./Payments";
import { ProofHistory, ProofReviewCard } from "./ProofReview";
import { Timeline } from "./Timeline";
import { Card, Empty, StateBadge } from "./ui";

/** Everything about one deal, shared by the advertiser and influencer views. `actions` renders role-specific buttons. */
export function DealDetail({ deal, title, actions, conversation = true }: { deal: Deal; title: ReactNode; actions?: ReactNode; conversation?: boolean }) {
  const me = useMe().user;
  const role = me.role;
  // the advertiser reads this history in the campaign's Audit trail tab: no second copy here
  const events = useResource(() => (role === "advertiser" ? Promise.resolve([]) : api.dealEvents(deal.id)), `${deal.id}:${role}`);
  useLiveEvents((e) => e.deal_id === deal.id, events.reload);
  // the payment requests of this deal's milestones come with GET /api/deals/{id} only
  const milestones = deal.milestones ?? [];
  const full = useResource(() => (milestones.length ? api.deal(deal.id) : Promise.resolve(null)), `${deal.id}:full:${milestones.map((m) => m.status).join(",")}`);
  useLiveEvents((e) => e.deal_id === deal.id || e.type === "invoice", full.reload);
  const t = deal.terms;
  const cur = termsCurrency(t);
  const paid = sumUsd(milestones.filter((m) => m.status === "confirmed").map((m) => m.amount)); // integer cents

  return (
    <Card title={title} actions={<StateBadge state={deal.state} />} className="deal-detail">
      {actions && <div className="deal-actions">{actions}</div>}

      {deal.policy_violations.length > 0 && (
        <div className="notice tone-bad">
          <strong>Policy blocked this deal:</strong>
          <ul>
            {deal.policy_violations.map((v) => (
              <li key={v}>{v}</li>
            ))}
          </ul>
        </div>
      )}

      {role === "influencer" && conversation && (
        <>
          <h3>Conversation with the advertiser's agent</h3>
          <LiveNegotiation key={deal.id} deal={deal} viewer="influencer" creator={me.name} avatar={me.picture} brand={deal.terms?.advertiser ?? "Advertiser"} />
        </>
      )}

      <h3>Negotiation room</h3>
      <NegotiationRoom deal={deal} />

      {t && (
        <>
          <h3>Signed terms</h3>
          <dl className="kv">
            <dt>Model</dt>
            <dd>
              {MODEL_LABEL[t.compensation_model] ?? t.compensation_model} <span className="muted small">({t.pricing_version})</span>
            </dd>
            <dt>Fixed fee</dt>
            <dd>{money(t.fixed_fee, cur)}</dd>
            <dt>Per 1,000 views</dt>
            <dd>{money(t.cpm, cur)}</dd>
            <dt>Cap (most it can pay)</dt>
            <dd>{money(t.cap, cur)}</dd>
            <dt>Paid in</dt>
            <dd>
              {isUsd(cur) ? (
                <>
                  shielded {t.asset} on <span className="mono">{t.network}</span>, converted per payment request at a test rate
                </>
              ) : (
                <span className="muted">{LEGACY_PRICING_NOTE}</span>
              )}
            </dd>
            <dt>Publish by</dt>
            <dd>{when(t.publish_by)}</dd>
            <dt>Must stay live</dt>
            <dd>{t.min_live_hours} h</dd>
            <dt>Required tags</dt>
            <dd>{t.required_tags.join(" ")}</dd>
            <dt>Disclosure rules</dt>
            <dd>{t.disclosure_policies.length ? t.disclosure_policies.join(", ") : "—"}</dd>
            <dt>Rights</dt>
            <dd>
              {humanize(t.scope.usage_rights)}
              {t.scope.license_days ? `, ${t.scope.license_days}-day license` : ""}
              {t.scope.paid_amplification ? ", paid amplification" : ""}
              {t.scope.exclusivity_days ? `, ${t.scope.exclusivity_days}-day exclusivity` : ""}
            </dd>
            <dt>Video length</dt>
            <dd>{videoLength(t.min_duration_seconds, t.max_duration_seconds)}</dd>
            <dt>Requirements hash</dt>
            <dd className="mono" title={t.requirements_hash}>
              {t.requirements_hash ? `${t.requirements_hash.slice(0, 10)}…${t.requirements_hash.slice(-6)}` : "none"}
            </dd>
            <dt>Payee</dt>
            <dd>
              {role === "influencer" && t.payee.startsWith("u") ? (
                <span className="mono" title={t.payee}>
                  {t.payee.slice(0, 10)}…{t.payee.slice(-6)}
                </span>
              ) : (
                <span className="muted">shielded address (private to the creator)</span>
              )}
            </dd>
            <dt>Channel</dt>
            <dd className="mono">{t.channel_id}</dd>
          </dl>
        </>
      )}

      {deal.archived_reason && <div className="notice tone-neutral">Archived: {deal.archived_reason}</div>}
      {deal.state === "ARCHIVED" && <LegacyPaymentHistory payouts={deal.payouts} />}

      {deal.proof && (
        <>
          <h3>
            Proof: <a href={`https://youtube.com/watch?v=${deal.proof.video_id}`} target="_blank" rel="noreferrer">{deal.proof.video_id}</a>
            {deal.proof.attempt > 1 && <span className="muted small"> · submission #{deal.proof.attempt}</span>}
          </h3>
          {deal.proof.review && deal.state === "PROOF_REJECTED" && <ProofReviewCard review={deal.proof.review} viewer={role === "advertiser" ? "advertiser" : "creator"} />}
          <ul className="checks">
            {deal.proof.checks.map((c) => (
              <li key={c.name} className={c.passed ? "pass" : "fail"}>
                <span aria-hidden>{c.passed ? "✓" : "✗"}</span> {humanize(c.name)}
                {c.detail && <span className="muted"> — {c.detail}</span>}
              </li>
            ))}
          </ul>
          {deal.proof.content && <ContentReportView report={deal.proof.content} />}
          {Object.keys(deal.proof.attestations ?? {}).length > 0 && (
            <p className="small muted">Creator attested: {Object.keys(deal.proof.attestations).map(humanize).join(", ")} (statements, not machine-verified)</p>
          )}
          <p className="muted small">
            Views at check: {num(deal.proof.views)} · performance fee computed {when(deal.performance_due)}
          </p>
          <ProofHistory history={deal.proof_history ?? []} />
        </>
      )}

      {/* the creator's deal page shows these in its own Payment card, at the top */}
      {role === "advertiser" && milestones.length > 0 && (
        <>
          <h3>Payments · {money(paid, cur)} confirmed in the creator's wallet</h3>
          <PaymentLifecycle deal={deal} />
          <MilestoneTable deal={deal} invoices={full.data?.milestone_invoices} />
        </>
      )}

      {role !== "advertiser" && (
        // advertisers read the same history in the campaign's Audit trail tab, next to the conversation
        <details className="deal-timeline">
          <summary>Activity log{events.data ? ` (${events.data.length})` : ""}</summary>
          {events.data ? <Timeline events={events.data} /> : <Empty>Loading…</Empty>}
        </details>
      )}
    </Card>
  );
}
