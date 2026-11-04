import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";
import { api } from "../../api";
import { ComplianceCard, ReviewForm } from "../../components/Compliance";
import { needsReview } from "../../components/ContentChecks";
import { ActivityChart, DevClock } from "../../components/CampaignPanels";
import { LiveNegotiation } from "../../components/LiveNegotiation";
import { NegotiationQueue, NegotiationSummary, useNegotiationQueue } from "../../components/NegotiationQueue";
import { DealDetail } from "../../components/DealDetail";
import { PayoutRequestsTable, PrivacyNote, ShieldedCheckout } from "../../components/Payments";
import { Requirements } from "../../components/Requirements";
import { Timeline } from "../../components/Timeline";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { CreatorLink } from "../../components/CreatorLink";
import { ActionButton, Avatar, CampaignBadge, Card, Empty, ErrorText, Notice, Spinner, StateBadge, Stat } from "../../components/ui";
import { MODEL_LABEL, campaignName, humanize, zec } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe, useSession } from "../../session";
import type { CampaignDetail, CompensationModel, Deal, DirectoryEntry, NegotiationSession } from "../../types";
import "./campaign.css";
import "./dashboard.css";

function spend(deals: Deal[]) {
  const locked = deals.filter((d) => d.funded_at && d.terms).reduce((s, d) => s + Number(d.terms!.cap), 0);
  const paid = deals.flatMap((d) => d.payouts).filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0);
  const refunded = deals.flatMap((d) => d.payouts).filter((p) => p.kind === "refund").reduce((s, p) => s + Number(p.amount), 0);
  return { locked, paid, refunded, inEscrow: locked - paid - refunded };
}

/** Approve the agreed deals together: the backend then issues one shielded funding invoice for all of them. */
function ApprovalPanel({ campaign, deals, names, onDone }: { campaign: CampaignDetail; deals: Deal[]; names: Map<string, string>; onDone: () => void }) {
  const { status } = useSession();
  const pending = campaign.pending_approval!;
  const rows = pending.deals.map((id) => deals.find((d) => d.id === id)).filter((d): d is Deal => !!d);
  const feeTotal = Number(pending.fee_reserve_per_deal) * pending.deals.length;

  async function decide(approved: boolean) {
    await api.decide(campaign.spec.id, approved);
    onDone();
  }

  return (
    <div className="signing card tone-warn">
      <div className="signing-head">
        <h2>
          Approve {pending.deals.length} deal{pending.deals.length > 1 ? "s" : ""} · {zec(pending.invoice_amount)}
        </h2>
        {status?.payments_simulated && (
          <span className="chip warn" title="Simulated payment mode: no ZEC moves">
            Simulated payments
          </span>
        )}
      </div>

      <div className="stats compact-stats">
        <Stat label="Sum of deal caps" value={zec(pending.total)} hint="The most these deals can pay out; unused amounts are refunded" />
        <Stat label="Fee reserve" value={zec(feeTotal)} hint={"Per deal, for network fees on payouts and refunds: " + zec(pending.fee_reserve_per_deal)} />
        <Stat label="Invoice amount" value={zec(pending.invoice_amount)} />
        <Stat label="Budget remaining" value={zec(campaign.funding.remaining)} />
      </div>

      {rows.length > 0 && (
        <table className="table compact signing-table">
          <thead>
            <tr>
              <th>Creator</th>
              <th>Fixed</th>
              <th>CPM</th>
              <th>Cap</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id}>
                <td>
                  <CreatorLink id={d.influencer_id}>{names.get(d.influencer_id) ?? d.influencer_id}</CreatorLink>
                </td>
                <td>{zec(d.terms?.fixed_fee)}</td>
                <td>{zec(d.terms?.cpm)}</td>
                <td>
                  <strong>{zec(d.terms?.cap)}</strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row">
        <ActionButton onClick={() => decide(true)}>Approve &amp; create funding invoice</ActionButton>
        <ActionButton variant="ghost" onClick={() => decide(false)}>
          Decline
        </ActionButton>
      </div>

      <details className="signing-more">
        <summary>What happens when I approve?</summary>
        <p className="small">
          Zerveil creates one shielded Zcash invoice for all these deals: the sum of their caps plus a small fee reserve per deal. You pay it from any
          Zcash wallet to a fresh shielded address with the invoice memo. Deals count as funded only after the escrow wallet sees the payment with{" "}
          {status?.zcash.confirmations_required ?? 1}+ confirmations. Unused amounts are refunded to your refund address.
        </p>
        <p className="small muted">Escrow is a wallet account operated by Zerveil, not a smart contract. Payouts follow deterministic rules and an operator approval.</p>
      </details>
    </div>
  );
}

function FundingCard({ campaign, onChange }: { campaign: CampaignDetail; onChange: () => void }) {
  const invoice = campaign.funding_invoices[0];
  if (!invoice) return null;
  const paid = invoice.status === "paid";
  return (
    <Card title={paid ? "Campaign funded" : "Fund the campaign"} className="funding-card">
      {paid ? (
        <p className="small">
          The escrow wallet received {zec(invoice.amount)} with {invoice.confirmations}/{invoice.required_confirmations} confirmations
          {invoice.paid_at ? " on " + new Date(invoice.paid_at).toLocaleString() : ""}.
          {invoice.simulated && <span className="chip warn">simulated, no ZEC moved</span>}
        </p>
      ) : (
        <ShieldedCheckout invoice={invoice} onChange={onChange} />
      )}
      {campaign.funding_invoices.length > 1 && <p className="muted small">{campaign.funding_invoices.length - 1} earlier invoice(s) for this campaign.</p>}
    </Card>
  );
}

function DisputeForm({ dealId }: { dealId: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open)
    return (
      <button type="button" className="btn btn-danger" onClick={() => setOpen(true)}>
        Dispute before release
      </button>
    );
  return (
    <div className="row">
      <input className="grow" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What's wrong with the post?" maxLength={500} />
      <ActionButton variant="danger" disabled={reason.trim().length < 3} onClick={() => api.dispute(dealId, reason)}>
        Freeze payout
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
        <input className="grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="How much is still in escrow? Why was Raj rejected?" maxLength={1000} />
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
  const payouts = useResource(() => api.campaignPayouts(id), `p:${id}`);
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
    void payouts.reload();
    void deals.reload();
    void events.reload();
  };
  useLiveEvents((e) => e.campaign_id === id || e.type === "invoice" || e.type === "payout", reloadAll);

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
  const money = spend(list);
  const deal = list.find((d) => d.id === selected) ?? null;
  const sessions = queue.sessions;
  const started = sessions.filter((s) => s.started_at);
  const model = c.spec.compensation_model as CompensationModel;
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
              Max {zec(c.spec.max_fixed)} fixed + {zec(c.spec.max_cpm)} CPM
            </li>
            <li>Cap {zec(c.spec.cap_per_creator)} each</li>
            <li>{c.spec.max_rounds} rounds</li>
          </ul>
        </div>
        <DevClock />
      </header>

      {c.error && <Notice tone="bad">{c.error}</Notice>}
      {c.pending_approval && <ApprovalPanel campaign={c} deals={list} names={names} onDone={reloadAll} />}
      {!c.pending_approval && <FundingCard campaign={c} onChange={reloadAll} />}
      {!c.pending_approval && queue.progress && queue.progress.agreed_count > 0 && (working || queue.progress.workflow_status === "running") && (
        // Deals are approved once, for every agreed creator together, after the whole creator queue has finished
        <Notice>
          {queue.progress.workflow_status === "running" ? (
            <>
              <strong>
                {queue.progress.agreed_count} creator{queue.progress.agreed_count > 1 ? "s" : ""} agreed so far.
              </strong>{" "}
              Nothing to approve yet: one funding invoice is prepared for all agreed creators together, after the remaining negotiations finish (
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
            <Stat label="Budget" value={zec(c.spec.budget)} />
            <Stat label="Funded into escrow" value={zec(money.locked)} />
            <Stat label="Paid to creators" value={zec(money.paid)} />
            <Stat label="Refunded" value={zec(money.refunded)} />
            <Stat label="Still locked" value={zec(money.inEscrow)} />
          </section>
          <p className="muted small escrow-where">
            {status?.payments_simulated ? (
              <>
                <span className="chip warn">simulated payments</span> no ZEC moves; invoices are paid with the dev simulate button.
              </>
            ) : status ? (
              <>
                <span className="chip good">Zcash {status.zcash.network}</span> shielded {status.zcash.ticker} held in the Zerveil-operated escrow wallet (not a
                smart contract).
              </>
            ) : null}
          </p>

          <section className="report-card">{events.data ? <ActivityChart events={events.data} deals={list} /> : <Spinner />}</section>

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
                        <td className="num">{zec(d.terms?.fixed_fee)}</td>
                        <td className="num">{zec(d.terms?.cpm)}</td>
                        <td className="num">{zec(d.terms?.cap)}</td>
                        <td className="num">{d.rounds.length}</td>
                        <td className="num">{zec(d.payouts.filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {(payouts.data?.length ?? 0) > 0 && (
            <section className="report-card">
              <h2>Payouts</h2>
              <p className="muted small">Creator addresses stay private to the creator and the operator.</p>
              <PayoutRequestsTable showDeal payouts={[...(payouts.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))} />
            </section>
          )}

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

          <Card title="Privacy and custody">
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
                  {queue.progress && <NegotiationSummary progress={queue.progress} sessions={sessions} name={creatorName} model={model} />}
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
