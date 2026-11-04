import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api";
import { Avatar, CampaignBadge, ErrorText, Spinner, StateBadge } from "../../components/ui";
import { DELIVERABLE_LABEL, LEGACY_PRICING_NOTE, campaignName, formatUsd, isUsd, money, num, sumUsd, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { Icon } from "../landing/parts";
import { useMe, useSession } from "../../session";
import type { Campaign, Deal, DealState } from "../../types";
import "./dashboard.css";

/** Deals that are live: the fixed fee is settled and the creator is posting, being verified or measured. */
const LIVE: DealState[] = ["CONTENT_CREATION", "PROOF_SUBMITTED", "PROOF_REJECTED", "VERIFIED", "PERFORMANCE_DUE", "DISPUTED"];
/** Deals that wait on the advertiser or need a look. */
const ATTENTION: DealState[] = ["AWAITING_APPROVAL", "PAYMENT_REQUESTED", "ESCALATED", "DISPUTED", "BLOCKED"];

const PIPELINE: { label: string; states: DealState[]; tone: string }[] = [
  { label: "Negotiating", states: ["NEGOTIATING", "AGREED"], tone: "blue" },
  { label: "Awaiting you", states: ["AWAITING_APPROVAL", "PAYMENT_REQUESTED", "ESCALATED", "BLOCKED"], tone: "orange" },
  { label: "Confirming payment", states: ["PAYMENT_PENDING"], tone: "blue-soft" },
  { label: "Live", states: LIVE, tone: "ink" },
  { label: "Completed", states: ["COMPLETED"], tone: "blue" },
  { label: "Closed", states: ["REJECTED", "PAYMENT_EXPIRED", "UNDELIVERED", "ARCHIVED"], tone: "muted" },
];

// Amounts are summed as decimal strings in integer cents (no float accumulation). They are in the campaign's currency:
// Every campaign is shown in $, including closed campaigns created before USD pricing.
const confirmedAmounts = (d: Deal) => [
  ...(d.milestones ?? []).filter((m) => m.status === "confirmed").map((m) => m.amount),
  ...d.payouts.filter((p) => p.kind !== "refund").map((p) => p.amount), // legacy history of archived deals
];
/** Milestone payments requested from the advertiser and not yet confirmed. */
const requestedAmounts = (d: Deal) => (d.milestones ?? []).filter((m) => m.status === "requested" || m.status === "pending").map((m) => m.amount);

export async function loadDeals(): Promise<Deal[]> {
  const campaigns = await api.campaigns();
  const lists = await Promise.all(campaigns.map((c) => api.campaignDeals(c.spec.id)));
  return lists.flat();
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function AdvertiserHome() {
  const { user } = useMe();
  const { status } = useSession();
  const campaigns = useResource(api.campaigns, "campaigns");
  const deals = useResource(loadDeals, "advertiser-deals");
  const directory = useResource(api.directory, "directory");
  const wallet = useResource(api.walletStatus, "wallet-status");
  useLiveEvents((e) => e.type === "campaign" || e.type === "deal", () => {
    campaigns.reload();
    deals.reload();
  });

  const list = campaigns.data ?? [];
  const allDeals = deals.data ?? [];
  const byCampaign = new Map<string, Deal[]>();
  for (const d of allDeals) byCampaign.set(d.campaign_id, [...(byCampaign.get(d.campaign_id) ?? []), d]);

  // USD totals cover USD campaigns only; legacy campaigns (priced in test units) are counted separately
  const usdCampaigns = new Set(list.map((c) => c.spec.id)); // every campaign is shown in $, including ones from before USD pricing
  const legacyCount = 0;
  const usdDeals = allDeals.filter((d) => usdCampaigns.has(d.campaign_id));
  const budget = sumUsd(list.filter((c) => usdCampaigns.has(c.spec.id)).map((c) => c.spec.budget));
  const paid = sumUsd(usdDeals.flatMap(confirmedAmounts));
  const requested = sumUsd(usdDeals.flatMap(requestedAmounts));
  const agreed = allDeals.filter((d) => d.terms && d.state !== "REJECTED").length;
  const inProgress = list.filter((c) => !["done", "failed"].includes(c.status)).length;
  const attention = [
    ...list.filter((c) => c.status === "awaiting_approval").map((c) => ({ key: `c-${c.spec.id}`, campaign: c, deal: null as Deal | null })),
    ...allDeals
      .filter((d) => ATTENTION.includes(d.state) && !(d.state === "AWAITING_APPROVAL" && list.find((c) => c.spec.id === d.campaign_id)?.status === "awaiting_approval"))
      .map((d) => ({ key: d.id, campaign: list.find((c) => c.spec.id === d.campaign_id), deal: d })),
  ];
  const creators = [...(directory.data ?? [])].sort((a, b) => b.subscribers - a.subscribers).slice(0, 6);
  const simulated = status?.payments_simulated ?? true;
  const first = user.name.split(" ")[0] || "there";
  const loading = campaigns.loading && !campaigns.data;

  return (
    <div className="dash">
      <section className="dash-hero">
        <div>
          <p className="dash-eyebrow">{user.company || "Advertiser"} · dashboard</p>
          <h1>
            {greeting()}, {first}
          </h1>
          <p className="dash-sub">Your agents negotiate. Your rules decide. Here’s where every campaign stands.</p>
        </div>
        <div className="dash-hero-actions">
          <Link to="/advertiser/creators" className="btn btn-secondary">
            <Icon name="users" size={18} /> Browse creators
          </Link>
          <Link to="/advertiser/new" className="btn btn-primary">
            <Icon name="plus" size={18} /> New campaign
          </Link>
        </div>
      </section>

      <ErrorText error={campaigns.error ?? deals.error} />

      <section className="kpis" aria-label="Summary">
        <Kpi
          icon="ledger"
          label="Total budget"
          value={formatUsd(budget, true)}
          foot={`${list.length} campaign${list.length === 1 ? "" : "s"} · ${inProgress} in progress${legacyCount ? ` · ${legacyCount} legacy not included` : ""}`}
        />
        <Kpi
          icon="shield"
          label="Payments requested"
          value={formatUsd(requested, true)}
          foot={simulated ? "Simulated — no real payments" : "Awaiting payment from your own wallet"}
          tone="blue"
        />
        <Kpi icon="check" label="Paid to creators" value={formatUsd(paid, true)} foot="Confirmed in creators' wallets" tone="orange" />
        <Kpi icon="agents" label="Deals agreed" value={num(agreed)} foot={`${num(directory.data?.length ?? 0)} creator agents live`} />
      </section>

      <div className="dash-grid">
        <section className="panel dash-campaigns">
          <header className="panel-head">
            <h2>Campaigns</h2>
            <Link to="/advertiser/campaigns" className="panel-link">
              View all <Icon name="arrow" size={16} />
            </Link>
          </header>
          {loading ? (
            <Spinner />
          ) : list.length === 0 ? (
            <GetStarted hasProfile={!!(user.company && user.brand_handle)} />
          ) : (
            <CampaignList campaigns={list} byCampaign={byCampaign} />
          )}
        </section>

        <div className="dash-side">
          <section className="panel">
            <header className="panel-head">
              <h2>Needs your attention</h2>
              {attention.length > 0 && <span className="count">{attention.length}</span>}
            </header>
            {attention.length === 0 ? (
              <p className="panel-empty">
                <Icon name="check" size={18} /> All clear — nothing is waiting on you.
              </p>
            ) : (
              <ul className="todo">
                {attention.slice(0, 6).map((a) => (
                  <li key={a.key}>
                    <Link to={`/advertiser/campaigns/${a.campaign?.spec.id ?? a.deal?.campaign_id}`}>
                      <span className="todo-ic">
                        <Icon name={a.deal ? "alert" : "key"} size={18} />
                      </span>
                      <span className="todo-body">
                        <strong>{a.deal ? (a.campaign ? campaignName(a.campaign.spec) : "Campaign") : "Approve agreed deals"}</strong>
                        <span>{a.deal ? <StateBadge state={a.deal.state} /> : `${a.campaign ? campaignName(a.campaign.spec) : ""} · agreed terms are ready`}</span>
                      </span>
                      <Icon name="arrow" size={16} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <header className="panel-head">
              <h2>Deal pipeline</h2>
              <span className="muted small">{num(allDeals.length)} deals</span>
            </header>
            {allDeals.length === 0 ? (
              <p className="panel-empty">Deals appear here once your agent starts negotiating.</p>
            ) : (
              <ul className="pipeline">
                {PIPELINE.map((p) => {
                  const n = allDeals.filter((d) => p.states.includes(d.state)).length;
                  return (
                    <li key={p.label}>
                      <span className="pipeline-label">{p.label}</span>
                      <span className="pipeline-bar">
                        <span className={`fill fill-${p.tone}`} style={{ width: `${(n / allDeals.length) * 100}%` }} />
                      </span>
                      <strong>{n}</strong>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="panel wallet-panel">
            <header className="panel-head">
              <h2>Wallet &amp; payments</h2>
              <Link to="/advertiser/payments" className="panel-link">
                Open <Icon name="arrow" size={16} />
              </Link>
            </header>
            <div className="wallet-body">
                <span className="wallet-ic">
                  <Icon name="wallet" size={22} />
                </span>
                <div>
                  <strong>
                    Zcash {wallet.data?.network ?? status?.zcash.network ?? "testnet"}
                    {wallet.data?.simulated ? " · simulated" : ""}
                  </strong>
                  <span className="muted small">
                    {wallet.data ? (wallet.data.reachable ? "Payment scanner online" : "Payment scanner unreachable") : wallet.loading ? "…" : "Status unavailable"} · you
                    pay creators directly from your own wallet
                  </span>
                  {status?.zcash.testnet_notice && <span className="muted small">{status.zcash.testnet_notice}</span>}
                </div>
            </div>
          </section>
        </div>
      </div>

      <section className="panel">
        <header className="panel-head">
          <h2>Creator agents</h2>
          <Link to="/advertiser/creators" className="panel-link">
            See all <Icon name="arrow" size={16} />
          </Link>
        </header>
        {directory.loading && !directory.data ? (
          <Spinner />
        ) : creators.length === 0 ? (
          <p className="panel-empty">No creator agents are live yet. They appear as soon as creators finish onboarding.</p>
        ) : (
          <ul className="creator-grid">
            {creators.map((c) => (
              <li key={c.id}>
                <Link to={`/advertiser/creators/${c.id}`} className="creator-card">
                  <Avatar src={c.avatar_url} name={c.name} size={44} />
                  <span>
                    <strong>{c.name}</strong>
                    <span className="muted small">
                      {num(c.subscribers)} subscribers · {c.niches[0] ?? c.niche}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Kpi({ icon, label, value, foot, tone = "" }: { icon: string; label: string; value: string; foot: string; tone?: string }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <span className="kpi-ic">
        <Icon name={icon} size={20} />
      </span>
      <span className="kpi-label">{label}</span>
      <strong className="kpi-value">{value}</strong>
      <span className="kpi-foot">{foot}</span>
    </div>
  );
}

const FILTERS: { key: string; label: string; match: (c: Campaign) => boolean }[] = [
  { key: "all", label: "All", match: () => true },
  { key: "progress", label: "In progress", match: (c) => ["planning", "negotiating", "awaiting_approval", "awaiting_payment"].includes(c.status) },
  { key: "active", label: "Active", match: (c) => c.status === "active" },
  { key: "done", label: "Done", match: (c) => c.status === "done" },
  { key: "failed", label: "Failed", match: (c) => c.status === "failed" },
];
export function CampaignList({ campaigns, byCampaign, pageSize = 5 }: { campaigns: Campaign[]; byCampaign: Map<string, Deal[]>; pageSize?: number }) {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const q = query.trim().toLowerCase();
  const current = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
  const shown = campaigns
    .filter(current.match)
    .filter((c) => !q || [c.spec.name, c.spec.advertiser, c.spec.niche, c.spec.deliverable, c.status].some((v) => v?.toLowerCase().includes(q)));
  const pages = Math.max(1, Math.ceil(shown.length / pageSize));
  const at = Math.min(page, pages - 1);
  const slice = shown.slice(at * pageSize, at * pageSize + pageSize);
  const pick = (key: string) => {
    setFilter(key);
    setPage(0);
  };

  return (
    <div className="camp-wrap">
      <div className="camp-tools">
        <div className="seg" role="tablist" aria-label="Filter campaigns">
          {FILTERS.map((f) => {
            const n = campaigns.filter(f.match).length;
            return (
              <button
                key={f.key}
                type="button"
                role="tab"
                aria-selected={filter === f.key}
                className={filter === f.key ? "on" : ""}
                onClick={() => pick(f.key)}
                disabled={f.key !== "all" && n === 0}
              >
                {f.label} <span>{n}</span>
              </button>
            );
          })}
        </div>
        <label className="camp-search">
          <Icon name="search" size={16} />
          <input
            type="search"
            placeholder="Search niche, format…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            aria-label="Search campaigns"
          />
        </label>
      </div>

      {slice.length === 0 ? (
        <p className="panel-empty">No campaigns match this filter.</p>
      ) : (
        <ul className="camp-list" key={`${filter}-${at}-${q}`}>
          {slice.map((c) => (
            <CampaignRow key={c.spec.id} campaign={c} deals={byCampaign.get(c.spec.id) ?? []} />
          ))}
        </ul>
      )}

      <nav className="pager" aria-label="Campaign pages">
        <span className="muted small">
          {shown.length === 0 ? "0" : `${at * pageSize + 1}–${at * pageSize + slice.length}`} of {shown.length}
        </span>
        <div className="pager-btns">
          <button type="button" onClick={() => setPage(at - 1)} disabled={at === 0} aria-label="Previous page">
            <Icon name="arrowLeft" size={16} />
          </button>
          {Array.from({ length: pages }, (_, k) => (
            <button key={k} type="button" className={k === at ? "on" : ""} onClick={() => setPage(k)} aria-current={k === at ? "page" : undefined}>
              {k + 1}
            </button>
          ))}
          <button type="button" onClick={() => setPage(at + 1)} disabled={at >= pages - 1} aria-label="Next page">
            <Icon name="arrow" size={16} />
          </button>
        </div>
      </nav>
    </div>
  );
}

function CampaignRow({ campaign: c, deals }: { campaign: Campaign; deals: Deal[] }) {
  const cur = c.spec.currency;
  const budgetText = money(c.spec.budget, cur, true);
  const paidText = money(sumUsd(deals.flatMap(confirmedAmounts)), cur, true);
  const committedText = money(sumUsd(deals.flatMap(requestedAmounts)), cur, true);
  // bar widths only: a ratio, not a displayed amount
  const budget = Number(c.spec.budget) || 0;
  const paid = Number(sumUsd(deals.flatMap(confirmedAmounts)));
  const committed = Number(sumUsd(deals.flatMap(requestedAmounts)));
  const agreed = deals.filter((d) => d.terms && d.state !== "REJECTED").length;
  const pct = (v: number) => `${budget ? Math.min(100, (v / budget) * 100) : 0}%`;
  return (
    <li>
      <Link to={`/advertiser/campaigns/${c.spec.id}`} className="camp">
        <span className="camp-top">
          <span className="camp-name">
            <strong>{campaignName(c.spec)}</strong>
            <span className="muted small">
              {c.spec.name ? `${c.spec.advertiser} · ` : ""}
              {c.spec.niche} · {DELIVERABLE_LABEL[c.spec.deliverable] ?? c.spec.deliverable ?? "video"} · {when(c.created_at)}
            </span>
          </span>
          <CampaignBadge status={c.status} />
        </span>
        <span className="camp-bar" title={`Paid ${paidText} · requested ${committedText} of ${budgetText} budget`}>
          <span className="fill fill-orange" style={{ width: pct(paid) }} />
          <span className="fill fill-blue" style={{ width: pct(committed) }} />
        </span>
        <span className="camp-meta">
          <span>
            <b>{budgetText}</b> budget
          </span>
          <span>
            <b>{agreed}</b>/{c.spec.max_creators} creators
          </span>
          <span>
            <b>{paidText}</b> paid
          </span>
        </span>
        {!isUsd(cur) && <span className="muted small">{LEGACY_PRICING_NOTE}</span>}
        {c.error && isUsd(cur) && <span className="error small">{c.error}</span>}
      </Link>
    </li>
  );
}

function GetStarted({ hasProfile }: { hasProfile: boolean }) {
  const steps = [
    { done: hasProfile, title: "Complete your profile", text: "Company and brand handle. You pay creators from your own wallet.", to: "/account" },
    { done: false, title: "Set up your agent", text: "Budgets, ceilings and how it negotiates.", to: "/advertiser/agent" },
    { done: false, title: "Launch a campaign", text: "Your agent contacts matching creators.", to: "/advertiser/new" },
  ];
  return (
    <ol className="start">
      {steps.map((s, k) => (
        <li key={s.title} className={s.done ? "done" : ""}>
          <Link to={s.to}>
            <span className="start-n">{s.done ? <Icon name="check" size={16} /> : k + 1}</span>
            <span>
              <strong>{s.title}</strong>
              <span className="muted small">{s.text}</span>
            </span>
            <Icon name="arrow" size={16} />
          </Link>
        </li>
      ))}
    </ol>
  );
}
