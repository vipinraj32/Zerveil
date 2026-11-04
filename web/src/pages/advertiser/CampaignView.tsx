import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../../api";
import { ComplianceCard, ReviewForm } from "../../components/Compliance";
import { needsReview } from "../../components/ContentChecks";
import { ActivityChart, DevClock } from "../../components/CampaignPanels";
import { LiveNegotiation } from "../../components/LiveNegotiation";
import { NegotiationQueue, NegotiationSummary, useNegotiationQueue } from "../../components/NegotiationQueue";
import { DealDetail } from "../../components/DealDetail";
import { CopyButton, InvoiceStatusChip, PricingNotice, PrivacyNote, ShieldedCheckout, SimulatedChip, TestnetNotice } from "../../components/Payments";
import { QrCode } from "../../components/QrCode";
import { Requirements } from "../../components/Requirements";
import { Timeline } from "../../components/Timeline";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { CreatorLink } from "../../components/CreatorLink";
import { ActionButton, Avatar, CampaignBadge, Card, Empty, ErrorText, Notice, Spinner, StateBadge, Stat } from "../../components/ui";
import {
  LEGACY_ASSET,
  LEGACY_PRICING_NOTE,
  MILESTONE_KIND,
  MODEL_LABEL,
  OPEN_INVOICE,
  campaignName,
  formatAsset,
  formatUsd,
  humanize,
  isUsd,
  money,
  requestValue,
  sumAsset,
  sumUsd,
  termsCurrency,
} from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe, useSession } from "../../session";
import type { CampaignDetail, CampaignPayments, CompensationModel, Deal, DirectoryEntry, NegotiationSession, OwnerInvoice } from "../../types";
import "./campaign.css";
import "./dashboard.css";

/** What the scanner confirmed in creators' wallets for one deal, in the deal's currency (USD; legacy deals: test
 * units, including payments of an earlier payment flow on archived deals). USD is summed in integer cents. */
function paidFor(d: Deal): string {
  const currency = termsCurrency(d.terms);
  const confirmed = (d.milestones ?? []).filter((m) => m.status === "confirmed").map((m) => m.amount);
  const legacy = d.payouts.filter((p) => p.kind !== "refund").map((p) => p.amount);
  if (isUsd(currency) && legacy.length === 0) return money(sumUsd(confirmed), currency, true);
  return money(sumAsset([...confirmed, ...legacy]), isUsd(currency) ? LEGACY_ASSET : currency, true);
}

/** Accept the agreed deals together: the backend then requests each creator's fixed fee as its own direct payment. */
function ApprovalPanel({ campaign, deals, names, onDone }: { campaign: CampaignDetail; deals: Deal[]; names: Map<string, string>; onDone: () => void }) {
  const { status } = useSession();
  const pending = campaign.pending_approval!;
  const cur = campaign.spec.currency;
  const rows = pending.deals.map((id) => deals.find((d) => d.id === id)).filter((d): d is Deal => !!d);

  async function decide(approved: boolean) {
    await api.decide(campaign.spec.id, approved);
    onDone();
  }

  return (
    <div className="signing card tone-warn">
      <div className="signing-head">
        <h2>
          Accept {pending.deals.length} deal{pending.deals.length > 1 ? "s" : ""} · {money(pending.upfront, cur, true)} upfront
        </h2>
        {status?.payments_simulated && (
          <span className="chip warn" title="Simulated payment mode: nothing moves on-chain">
            Simulated — no real payments
          </span>
        )}
      </div>

      <div className="stats compact-stats">
        <Stat label="Upfront (fixed fees)" value={money(pending.upfront, cur)} hint="Requested right after you accept: one direct payment to each creator's wallet" />
        <Stat label="Most these deals can pay" value={money(pending.total, cur)} hint="Sum of the deal caps: fixed fee plus the most the performance fee can add" />
        <Stat label="Budget remaining" value={money(campaign.payments.remaining, campaign.payments.currency ?? cur)} hint="The budget is a spending limit, not money held" />
      </div>

      {rows.length > 0 && (
        <table className="table compact signing-table">
          <thead>
            <tr>
              <th>Creator</th>
              <th>Fixed</th>
              <th>CPM (per 1K views)</th>
              <th>Cap</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id}>
                <td>
                  <CreatorLink id={d.influencer_id}>{names.get(d.influencer_id) ?? d.influencer_id}</CreatorLink>
                </td>
                <td>{money(d.terms?.fixed_fee, termsCurrency(d.terms), true)}</td>
                <td>{money(d.terms?.cpm, termsCurrency(d.terms), true)}</td>
                <td>
                  <strong>{money(d.terms?.cap, termsCurrency(d.terms), true)}</strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row">
        <ActionButton onClick={() => decide(true)}>Accept &amp; request payments</ActionButton>
        <ActionButton variant="ghost" onClick={() => decide(false)}>
          Decline
        </ActionButton>
      </div>

      <details className="signing-more">
        <summary>What happens when I accept?</summary>
        <p className="small">
          Amounts are in USD. Each creator's fixed fee becomes its own shielded payment request to a fresh address of that creator's own wallet. You pay from your own Zcash
          wallet, one by one or all at once with a multi-recipient ZIP-321 request. A payment counts only after Zerveil's view-only scanner sees it in the
          creator's wallet with {status?.zcash.confirmations_required ?? 1}+ confirmations. The performance fee is computed from verified views at day 7
          and requested the same way. Your wallet pays the equivalent in shielded testnet Zcash, converted once per request at a test rate (no real value; no
          stablecoin is involved).
        </p>
        <p className="small muted">Zerveil never holds your money: it only requests payments and confirms they arrived.</p>
      </details>
    </div>
  );
}

/** One milestone payment request: amount, creator, status with confirmations, address, memo, ZIP-321 link + QR, receipt. */
function MilestoneInvoiceCard({ invoice, creator, kind, onChange }: { invoice: OwnerInvoice; creator: string; kind?: string; onChange: () => void }) {
  return (
    <div className="report-card milestone-invoice">
      <ShieldedCheckout
        invoice={invoice}
        onChange={onChange}
        receiptId={invoice.receipt_id}
        title={
          <>
            {kind ? `${MILESTONE_KIND[kind] ?? kind} · ` : ""}
            {creator}
          </>
        }
      />
    </div>
  );
}

/** After acceptance: one card per creator milestone invoice, plus one "Pay all" ZIP-321 request for the open ones. */
function CampaignPaymentsSection({
  data,
  deals,
  name,
  onChange,
}: {
  data: CampaignPayments;
  deals: Deal[];
  name: (creatorId: string) => string;
  onChange: () => void;
}) {
  const [showClosed, setShowClosed] = useState(false);
  if (data.invoices.length === 0) return null;
  const kindOf = (inv: OwnerInvoice) => deals.flatMap((d) => d.milestones ?? []).find((m) => m.id === inv.milestone_id)?.kind;
  const creatorOf = (inv: OwnerInvoice) => {
    const deal = deals.find((d) => inv.deal_ids.includes(d.id));
    return name(deal?.influencer_id ?? inv.recipient_id);
  };
  const open = data.invoices.filter((i) => OPEN_INVOICE.has(i.status));
  const closed = data.invoices.filter((i) => !OPEN_INVOICE.has(i.status));
  return (
    <section className="report-card" aria-label="Creator payments">
      <div className="report-toolbar">
        <h2>Creator payments</h2>
        <TestnetNotice inline />
      </div>
      <p className="muted small">
        Each request pays one creator's own wallet directly. Creator addresses here are one-time addresses for this payment only.
      </p>
      <PricingNotice />
      {data.pay_all_uri && open.length > 1 && (
        <div className="pay-all">
          <div className="stack">
            <h3>
              Pay all {open.length} open requests · {formatUsd(data.open_total_usd)}
            </h3>
            <div className="test-payment" role="note">
              <div className="test-payment-label">Test-only payment (Zcash testnet)</div>
              <p className="test-payment-line">
                Your wallet sends {data.open_asset ? formatAsset(data.open_total, data.open_asset) : `${formatAsset(data.open_total, "")}(mixed assets)`} in total ·
                network {open[0]?.network || "zcash-testnet"}
                {open[0]?.usd_rate && data.open_asset ? ` · converted at a test rate (${formatUsd(open[0].usd_rate)} per ${data.open_asset}), not a market price` : " · converted at a test rate, not a market price"}{" "}
                · no stablecoin
              </p>
            </div>
            <p className="small">
              One ZIP-321 multi-recipient request: wallets that support it pay every creator in a single transaction. Each creator still receives their
              own payment directly.
            </p>
            <div className="row">
              <a className="btn btn-primary" href={data.pay_all_uri}>
                Open in wallet
              </a>
              <CopyButton value={data.pay_all_uri} label="Copy payment URI" />
            </div>
          </div>
          <QrCode value={data.pay_all_uri} label={`ZIP-321 request paying ${open.length} creators`} />
        </div>
      )}
      <div className="milestone-invoices">
        {open.map((inv) => (
          <MilestoneInvoiceCard key={inv.id} invoice={inv} creator={creatorOf(inv)} kind={kindOf(inv)} onChange={onChange} />
        ))}
      </div>
      {closed.length > 0 && (
        <>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowClosed((v) => !v)}>
            {showClosed ? "Hide" : "Show"} {closed.length} settled or closed request{closed.length > 1 ? "s" : ""}
          </button>
          {showClosed && (
            <div className="table-scroll">
              <table className="table compact pay-tx">
                <thead>
                  <tr>
                    <th>Creator</th>
                    <th>Milestone</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>Receipt</th>
                  </tr>
                </thead>
                <tbody>
                  {closed.map((inv) => (
                    <tr key={inv.id}>
                      <td>{creatorOf(inv)}</td>
                      <td>{MILESTONE_KIND[kindOf(inv) ?? ""] ?? "—"}</td>
                      <td>{requestValue(inv, true)}</td>
                      <td>
                        <InvoiceStatusChip status={inv.status} /> <SimulatedChip simulated={inv.simulated} />
                      </td>
                      <td>
                        {inv.receipt_id ? (
                          <Link to={`/receipts/${encodeURIComponent(inv.receipt_id)}`} className="mono small">
                            {inv.receipt_id}
                          </Link>
                        ) : (
                          <span className="muted small">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function DisputeForm({ dealId }: { dealId: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open)
    return (
      <button type="button" className="btn btn-danger" onClick={() => setOpen(true)}>
        Dispute the post
      </button>
    );
  return (
    <div className="row">
      <input className="grow" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What's wrong with the post?" maxLength={500} />
      <ActionButton variant="danger" disabled={reason.trim().length < 3} onClick={() => api.dispute(dealId, reason)}>
        Open dispute
      </ActionButton>
      <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </div>
  );
}

function Assistant() {
  const { status } = useSession();
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  if (!status?.llm) return null;
  return (
    <Card title="Ask the campaign assistant">
      <p className="muted small">It reads your campaigns through Zerveil's read-only MCP tools. It cannot move money.</p>
      <div className="row">
        <input className="grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="How much have I paid so far? Why was Raj rejected?" maxLength={1000} />
        <ActionButton
          disabled={q.trim().length < 2}
          onClick={async () => {
            setAnswer((await api.ask(q)).answer);
          }}
        >
          Ask
        </ActionButton>
      </div>
      {answer && <div className="answer">{answer}</div>}
    </Card>
  );
}

// A deal the list has not reloaded yet (its negotiation just started): enough for the live conversation to open
const pendingDeal = (s: NegotiationSession) =>
  ({ id: s.negotiation_id, campaign_id: s.campaign_id, influencer_id: s.creator_id, state: "NEGOTIATING" }) as Deal;

export function CampaignView() {
  const { id = "" } = useParams();
  useMe();
  const { status } = useSession();
  const campaign = useResource(() => api.campaign(id), `c:${id}`);
  const payments = useResource(() => api.campaignPayments(id), `p:${id}`);
  const deals = useResource(() => api.campaignDeals(id), `d:${id}`);
  const events = useResource(() => api.campaignEvents(id), `e:${id}`);
  const directory = useResource(api.directory, "directory");
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"chat" | "audit">("chat");
  const [allChats, setAllChats] = useState(false); // every creator's conversation stacked, in the order they started
  const [pinned, setPinned] = useState<string | null>(null); // a conversation the advertiser opened; null follows the live one
  const queue = useNegotiationQueue(id);
  const [scope, setScope] = useState<"deal" | "campaign">("deal");
  const [query, setQuery] = useState("");

  const reloadAll = () => {
    void campaign.reload();
    void payments.reload();
    void deals.reload();
    void events.reload();
  };
  useLiveEvents((e) => e.campaign_id === id || e.type === "invoice", reloadAll);

  const names = useMemo(() => new Map((directory.data ?? []).map((d: DirectoryEntry) => [d.id, d.name])), [directory.data]);
  const avatars = useMemo(() => new Map((directory.data ?? []).map((d: DirectoryEntry) => [d.id, d.avatar_url])), [directory.data]);
  // Your agent's reason for each of its moves (private audit events), shown under its message in your view of the chat
  const agentNotes = useMemo(() => {
    const byDeal = new Map<string, Record<number, string>>();
    const moves = (events.data ?? []).filter((e) => e.kind === "negotiation_move" && e.deal_id && typeof e.detail.reason === "string" && e.detail.reason);
    for (const e of [...moves].sort((a, b) => a.at.localeCompare(b.at))) {
      const notes = byDeal.get(e.deal_id!) ?? {};
      // older events carry no round: the n-th move answers round n and opens round n + 1
      const round = typeof e.detail.round === "number" ? e.detail.round : Object.keys(notes).length + 2;
      notes[round] = String(e.detail.reason);
      byDeal.set(e.deal_id!, notes);
    }
    return byDeal;
  }, [events.data]);
  const list = deals.data ?? [];
  // pick the first deal so the chat is never empty when there is something to show
  useEffect(() => {
    if (!selected && list.length) setSelected(list[0].id);
  }, [selected, list]);

  if (campaign.error) return <ErrorText error={campaign.error} />;
  if (!campaign.data) return <Spinner />;
  const c = campaign.data;
  const deal = list.find((d) => d.id === selected) ?? null;
  const sessions = queue.sessions;
  const started = sessions.filter((s) => s.started_at);
  const model = c.spec.compensation_model as CompensationModel;
  const cur = c.spec.currency;
  const pcur = c.payments.currency ?? cur;
  // The conversation on screen: the one the advertiser picked, else whichever creator the backend is talking to now
  const focusId = pinned ?? queue.progress?.current_negotiation_id ?? started.at(-1)?.negotiation_id ?? null;
  const focusSession = sessions.find((s) => s.negotiation_id === focusId) ?? null;
  const focusDeal = focusSession ? (list.find((d) => d.id === focusSession.negotiation_id) ?? pendingDeal(focusSession)) : null;
  const creatorName = (creatorId: string) => names.get(creatorId) ?? creatorId;
  const notesFor = (dealId: string) => agentNotes.get(dealId);
  const name = (d: Deal) => names.get(d.influencer_id) ?? d.influencer_id;
  const q = query.trim().toLowerCase();
  const rows = list.filter((d) => !q || name(d).toLowerCase().includes(q) || d.state.toLowerCase().includes(q));
  const trail = (events.data ?? []).filter((e) => scope === "campaign" || !deal || e.deal_id === deal.id);
  const working = c.status === "planning" || c.status === "negotiating";

  return (
    <div className="report">
      <header className="report-head">
        <div className="report-title">
          <Breadcrumbs items={[{ label: "Dashboard", to: "/advertiser" }, { label: "All campaigns", to: "/advertiser/campaigns" }, { label: campaignName(c.spec) }]} />
          <div className="row">
            <h1>
              {campaignName(c.spec)}{" "}
              <span className="muted">
                {c.spec.name ? `${c.spec.advertiser} ` : ""}
                {c.spec.brand_handle}
              </span>
            </h1>
            <CampaignBadge status={c.status} />
            {working && (
              <span className="live-chip">
                <span className="live-dot on" /> Live
              </span>
            )}
          </div>
          <ul className="report-meta" aria-label="Campaign terms">
            <li>{c.spec.niche}</li>
            <li>{MODEL_LABEL[c.spec.compensation_model] ?? c.spec.compensation_model}</li>
            <li>Up to {c.spec.max_creators} creators</li>
            <li>
              Max {money(c.spec.max_fixed, cur, true)} fixed + {money(c.spec.max_cpm, cur, true)} CPM
            </li>
            <li>Cap {money(c.spec.cap_per_creator, cur, true)} each</li>
            <li>{c.spec.max_rounds} rounds</li>
          </ul>
        </div>
        <DevClock />
      </header>

      {!isUsd(cur) && (
        <Notice tone="warn">
          <strong>{LEGACY_PRICING_NOTE}.</strong> This campaign's amounts are legacy test units, not dollars.
        </Notice>
      )}
      {c.error && <Notice tone="bad">{c.error}</Notice>}
      {c.pending_approval && <ApprovalPanel campaign={c} deals={list} names={names} onDone={reloadAll} />}
      {!c.pending_approval && payments.data && <CampaignPaymentsSection data={payments.data} deals={list} name={creatorName} onChange={reloadAll} />}
      {payments.error && <ErrorText error={payments.error} />}
      {!c.pending_approval && queue.progress && queue.progress.agreed_count > 0 && (working || queue.progress.workflow_status === "running") && (
        // Deals are approved once, for every agreed creator together, after the whole creator queue has finished
        <Notice>
          {queue.progress.workflow_status === "running" ? (
            <>
              <strong>
                {queue.progress.agreed_count} creator{queue.progress.agreed_count > 1 ? "s" : ""} agreed so far.
              </strong>{" "}
              Nothing to accept yet: the agreed creators are put to you together, after the remaining negotiations finish (
              {queue.progress.completed_count} of {queue.progress.total_creators} done).
            </>
          ) : (
            <>
              <strong>All negotiations finished.</strong> Preparing the approval for {queue.progress.agreed_count} agreed creator
              {queue.progress.agreed_count > 1 ? "s" : ""}…
            </>
          )}
        </Notice>
      )}

      <div className="report-grid">
        <div className="report-main">
          <section className="report-kpis" aria-label="Money">
            <Stat label="Budget" value={money(c.payments.budget, pcur)} hint="A spending limit, not money held" />
            <Stat label="Reserved" value={money(c.payments.reserved, pcur)} hint="Caps of deals that may still be paid or were paid" />
            <Stat label="Requested" value={money(c.payments.requested, pcur)} hint="Payment requests open right now" />
            <Stat label="Paid to creators" value={money(c.payments.paid, pcur)} hint="Confirmed by the scanner in creators' wallets" />
            <Stat label="Remaining" value={money(c.payments.remaining, pcur)} />
          </section>
          <p className="muted small payments-where">
            {status?.payments_simulated ? (
              <>
                <span className="chip warn">Simulated — no real payments</span> payment requests are settled with the dev simulate button.
              </>
            ) : status ? (
              <>
                <span className="chip good">Zcash {status.zcash.network}</span> prices in USD; you pay each creator directly from your own wallet. Zerveil holds no funds.
              </>
            ) : null}
          </p>

          <section className="report-card">{events.data ? <ActivityChart events={events.data} deals={list} currency={pcur} /> : <Spinner />}</section>

          <section className="report-card">
            <div className="report-toolbar">
              <h2>Deals ({list.length})</h2>
              <label className="camp-search">
                <input type="search" placeholder="Search creator or state…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search deals" />
              </label>
            </div>
            {list.length === 0 ? (
              <Empty>{working ? "Your agent is finding creator agents. Deals appear here as soon as talks start." : "No deals for this campaign."}</Empty>
            ) : (
              <div className="report-table">
                <table className="table selectable">
                  <thead>
                    <tr>
                      <th>Creator</th>
                      <th>State</th>
                      <th className="num">Fixed</th>
                      <th className="num">CPM</th>
                      <th className="num">Cap</th>
                      <th className="num">Rounds</th>
                      <th className="num">Paid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((d) => (
                      <tr key={d.id} className={d.id === selected ? "selected" : ""} onClick={() => setSelected(d.id)}>
                        <td>
                          <span className="who-cell">
                            <Avatar src={avatars.get(d.influencer_id)} name={name(d)} size={24} />
                            <CreatorLink id={d.influencer_id}>{name(d)}</CreatorLink>
                          </span>
                        </td>
                        <td>
                          <StateBadge state={d.state} />
                        </td>
                        <td className="num">{money(d.terms?.fixed_fee, termsCurrency(d.terms), true)}</td>
                        <td className="num">{money(d.terms?.cpm, termsCurrency(d.terms), true)}</td>
                        <td className="num">{money(d.terms?.cap, termsCurrency(d.terms), true)}</td>
                        <td className="num">{d.rounds.length}</td>
                        <td className="num">{paidFor(d)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {deal && (
            <DealDetail
              deal={deal}
              title={
                <span>
                  <CreatorLink id={deal.influencer_id}>{name(deal)}</CreatorLink> <span className="muted small mono">{deal.influencer_id}</span>
                </span>
              }
              actions={
                <>
                  {deal.state === "VERIFIED" && (c.spec.review_required || needsReview(deal.proof?.content)) && <ReviewForm dealId={deal.id} onDone={reloadAll} />}
                  {deal.state === "VERIFIED" && <DisputeForm dealId={deal.id} />}
                </>
              }
            />
          )}

          <ComplianceCard campaignId={c.spec.id} names={names} />

          <Card title="Content requirements">
            <Requirements deliverable={c.spec.deliverable} min={c.spec.min_duration_seconds} max={c.spec.max_duration_seconds} text={c.spec.requirements} />
          </Card>

          <Card title="Privacy and payments">
            <PrivacyNote />
          </Card>

          <Assistant />
        </div>

        <aside className="report-side" aria-label="Agent conversation and audit trail">
          <div className="side-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>
              A2A chat
            </button>
            <button type="button" role="tab" aria-selected={tab === "audit"} className={tab === "audit" ? "on" : ""} onClick={() => setTab("audit")}>
              Audit trail <span>{trail.length}</span>
            </button>
          </div>

          {list.length > 0 && (tab === "audit" || sessions.length === 0) && (
            <label className="side-pick">
              <span className="sr-only">Deal</span>
              <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value)}>
                {list.map((d) => (
                  <option key={d.id} value={d.id}>
                    {name(d)} · {humanize(d.state)}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="side-body">
            {tab === "chat" ? (
              sessions.length === 0 && list.length > 0 ? (
                // campaigns from before the creator queue existed
                deal && (
                  <LiveNegotiation
                    key={deal.id}
                    deal={deal}
                    viewer="advertiser"
                    notes={notesFor(deal.id)}
                    creator={name(deal)}
                    avatar={avatars.get(deal.influencer_id)}
                    brand={c.spec.advertiser}
                    limits={{ budget: c.spec.budget, max_fixed: c.spec.max_fixed, max_cpm: c.spec.max_cpm, cap: c.spec.cap_per_creator }}
                    currency={cur}
                    deliverable={c.spec.deliverable}
                  />
                )
              ) : (
                <>
                  {working || sessions.length > 0 ? (
                    <NegotiationQueue
                      progress={queue.progress}
                      sessions={sessions}
                      name={creatorName}
                      avatar={(i) => avatars.get(i)}
                      model={model}
                      currency={cur}
                      focus={focusId}
                      following={pinned === null}
                      all={allChats}
                      onFocus={(negotiationId) => {
                        setPinned(negotiationId);
                        setSelected(negotiationId);
                        setAllChats(false);
                      }}
                      onFollow={() => setPinned(null)}
                      onAll={() => setAllChats((v) => !v)}
                      canCancel
                    />
                  ) : null}
                  {queue.error && <p className="error small">{queue.error}</p>}
                  {queue.progress && <NegotiationSummary progress={queue.progress} sessions={sessions} name={creatorName} model={model} currency={cur} />}
                  {allChats ? (
                    <div className="ln-stack">
                      {started.map((s) => {
                        const d = list.find((x) => x.id === s.negotiation_id) ?? pendingDeal(s);
                        return (
                          <LiveNegotiation
                            key={s.negotiation_id}
                            compact
                            deal={d}
                            viewer="advertiser"
                            notes={notesFor(d.id)}
                            creator={creatorName(s.creator_id)}
                            avatar={avatars.get(s.creator_id)}
                            brand={c.spec.advertiser}
                            currency={cur}
                            onOpen={() => {
                              setPinned(s.negotiation_id);
                              setSelected(s.negotiation_id);
                              setAllChats(false);
                            }}
                          />
                        );
                      })}
                    </div>
                  ) : focusDeal && focusSession ? (
                    <LiveNegotiation
                      key={focusDeal.id}
                      deal={focusDeal}
                      viewer="advertiser"
                      notes={notesFor(focusDeal.id)}
                      creator={creatorName(focusSession.creator_id)}
                      avatar={avatars.get(focusSession.creator_id)}
                      brand={c.spec.advertiser}
                      limits={{ budget: c.spec.budget, max_fixed: c.spec.max_fixed, max_cpm: c.spec.max_cpm, cap: c.spec.cap_per_creator }}
                      currency={cur}
                      deliverable={c.spec.deliverable}
                    />
                  ) : (
                    <Empty>{working ? "Advertiser agent is preparing the first offer…" : "No conversations for this campaign."}</Empty>
                  )}
                </>
              )
            ) : (
              <>
                <div className="seg side-scope">
                  <button type="button" className={scope === "deal" ? "on" : ""} onClick={() => setScope("deal")} disabled={!deal}>
                    This deal
                  </button>
                  <button type="button" className={scope === "campaign" ? "on" : ""} onClick={() => setScope("campaign")}>
                    Whole campaign
                  </button>
                </div>
                {events.data ? <Timeline events={trail} limit={80} /> : <Spinner />}
              </>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
