// Advertiser payments: network and scanner status, the payment requests I pay (each directly to the recipient's own
// wallet), the agent's prepaid API credit and spending limits, its pending spend requests, and the HTTP 402
// premium-search demo. Amounts are USD; what is paid is shielded Zcash (testnet), converted per request at a test
// rate. Zerveil holds no funds.

import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api } from "../../api";
import { InvoiceStatusChip, PricingNotice, PrivacyNote, ShieldedCheckout, SimulatedChip, TestnetNotice, WalletStatusPanel } from "../../components/Payments";
import { ActionButton, Card, Empty, ErrorText, Notice, Spinner, Stat, UsdInput } from "../../components/ui";
import { LEGACY_ASSET, PURPOSE_LABEL, formatAsset, formatUsd, humanize, num, requestValue, toCents, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useSession } from "../../session";
import type { AgentPremiumSearch, CreditEntry, OwnerInvoice, PaymentRequired, PremiumSearchResult, SpendingPolicy } from "../../types";

/** A USD amount typed by the user: positive, at most 2 decimals (what the API accepts). */
const validUsd = (v: string) => /^\d+(\.\d{1,2})?$/.test(v.trim()) && (toCents(v) ?? 0n) > 0n;

function InvoiceHistory({ invoices, onOpen }: { invoices: OwnerInvoice[]; onOpen: (id: string) => void }) {
  if (invoices.length === 0) return <Empty>No payment requests yet.</Empty>;
  return (
    <div className="table-scroll">
      <table className="table compact">
        <thead>
          <tr>
            <th>Purpose</th>
            <th>Amount</th>
            <th>Status</th>
            <th>Created</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {invoices.map((i) => (
            <tr key={i.id}>
              <td>
                {PURPOSE_LABEL[i.purpose] ?? humanize(i.purpose)}
                <div className="muted small">{i.title}</div>
                {i.campaign_id && (
                  <Link className="small" to={`/advertiser/campaigns/${encodeURIComponent(i.campaign_id)}`}>
                    campaign
                  </Link>
                )}
              </td>
              <td>{requestValue(i, true)}</td>
              <td>
                <InvoiceStatusChip status={i.status} confirmations={i.confirmations} required={i.required_confirmations} />
                <SimulatedChip simulated={i.simulated} />
              </td>
              <td className="small">{when(i.created_at)}</td>
              <td>
                <button type="button" className="link" onClick={() => onOpen(i.id)}>
                  details
                </button>{" "}
                <Link className="small" to={`/pay/${encodeURIComponent(i.id)}`}>
                  pay page
                </Link>
                {i.receipt_id && (
                  <>
                    {" "}
                    <Link className="small" to={`/receipts/${encodeURIComponent(i.receipt_id)}`}>
                      receipt
                    </Link>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InvoiceDetail({ id, onClose, onChange }: { id: string; onClose: () => void; onChange: () => void }) {
  const inv = useResource(() => api.invoice(id), `inv:${id}`);
  useLiveEvents((e) => e.type === "invoice" && e.invoice_id === id, inv.reload);
  const i = inv.data;
  return (
    <Card
      title={i ? i.title || PURPOSE_LABEL[i.purpose] : "Payment request"}
      actions={
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      }
    >
      <ErrorText error={inv.error} />
      {!i ? (
        <Spinner />
      ) : (
        <div className="stack">
          <ShieldedCheckout
            invoice={i}
            onChange={async () => {
              await inv.reload();
              onChange();
            }}
          />
          {i.notes.length > 0 && (
            <>
              <h3 className="small muted">Received notes (payment details)</h3>
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Amount ({i.asset || i.ticker})</th>
                    <th>Pool</th>
                    <th>Confirmations</th>
                    <th>Mined height</th>
                    <th>Memo matched</th>
                    <th>Seen</th>
                  </tr>
                </thead>
                <tbody>
                  {i.notes.map((n) => (
                    <tr key={n.key}>
                      <td>{formatAsset(n.amount, i.asset || i.ticker)}</td>
                      <td>{n.pool}</td>
                      <td>{n.confirmations}</td>
                      <td>{n.mined_height ?? "unmined"}</td>
                      <td>{n.memo_matched ? "yes" : "no"}</td>
                      <td className="small">{when(n.first_seen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {(i.events?.length ?? 0) > 0 && (
            <>
              <h3>Events</h3>
              <ul className="small">
                {i.events!.map((e, k) => (
                  <li key={k}>
                    {when(e.at)} · {humanize(e.kind)}
                  </li>
                ))}
              </ul>
            </>
          )}
          {i.purpose !== "milestone" && i.status === "awaiting_payment" && i.notes.length === 0 && (
            <ActionButton
              variant="danger"
              onClick={async () => {
                await api.cancelInvoice(i.id);
                await inv.reload();
                onChange();
              }}
            >
              Cancel request
            </ActionButton>
          )}
        </div>
      )}
    </Card>
  );
}

function CreditCard({ onInvoice }: { onInvoice: (id: string) => void }) {
  const credit = useResource(api.credit, "agent-credit");
  useLiveEvents((e) => e.type === "invoice", credit.reload);
  const [amount, setAmount] = useState("1");
  const c = credit.data;
  return (
    <Card title="API credit">
      <p className="muted small">
        Prepaid API calls your advertiser agent may use on paid APIs (like premium creator search) within the limits below. Top-ups are shielded
        payments made directly to the API seller's own wallet; this is a prepayment for API calls, not a deposit, and it is not withdrawable. Credit
        is kept in dollars.
      </p>
      <ErrorText error={credit.error} />
      {c && (
        <div className="stats">
          <Stat label="Balance" value={formatUsd(c.balance)} />
          <Stat label="Spent (24 h)" value={formatUsd(c.spent_24h)} />
        </div>
      )}
      <div className="row" style={{ marginTop: "0.75rem" }}>
        <label className="grow">
          Top-up amount ($)
          <UsdInput type="number" min="0.01" max="1000" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <small>Dollars and cents, e.g. 25.00.</small>
        </label>
        <ActionButton
          disabled={!validUsd(amount)}
          onClick={async () => {
            const inv = await api.topUp(amount.trim());
            onInvoice(inv.id);
            await credit.reload();
          }}
        >
          Create top-up request
        </ActionButton>
      </div>

      {c && c.pending_requests.length > 0 && (
        <>
          <h3>Agent spend requests waiting for you</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th>Category</th>
                <th>Amount ($)</th>
                <th>Reason</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {c.pending_requests.map((r) => (
                <tr key={r.id}>
                  <td>{humanize(r.category)}</td>
                  <td>{formatUsd(r.amount, true)}</td>
                  <td className="small">{r.reason}</td>
                  <td>
                    <div className="row">
                      <ActionButton
                        onClick={async () => {
                          await api.decideSpend(r.id, true);
                          await credit.reload();
                        }}
                      >
                        Approve
                      </ActionButton>
                      <ActionButton
                        variant="ghost"
                        onClick={async () => {
                          await api.decideSpend(r.id, false);
                          await credit.reload();
                        }}
                      >
                        Reject
                      </ActionButton>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {c && c.entries.length > 0 && (
        <details>
          <summary className="small">Credit history ({c.entries.length})</summary>
          <table className="table compact">
            <tbody>
              {c.entries.map((e) => (
                <tr key={e.id}>
                  <td className="small">{when(e.at)}</td>
                  <td>
                    {e.kind === "topup" ? "Top-up" : e.kind === "spend" ? `Spend · ${humanize(e.category)}` : "Legacy credit (test units) closed by the move to USD pricing"}
                  </td>
                  <td>
                    {creditAmount(e, legacyBefore(c.entries))} <SimulatedChip simulated={e.simulated} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </Card>
  );
}

/** Entries up to the legacy_reset entry were kept in legacy test units before USD pricing ("" when there is none). */
const legacyBefore = (entries: CreditEntry[]) => entries.find((e) => e.kind === "legacy_reset")?.at ?? "";

/** A signed ledger amount: USD, or legacy test units for the reset entry and anything recorded before it. */
function creditAmount(e: CreditEntry, legacyUntil: string): string {
  const legacy = e.kind === "legacy_reset" || (legacyUntil !== "" && e.at <= legacyUntil);
  const neg = e.amount.trim().startsWith("-");
  const abs = neg ? e.amount.trim().slice(1) : e.amount;
  const sign = neg ? "−" : "+";
  return legacy ? `${sign}${formatAsset(abs, LEGACY_ASSET)} (legacy)` : `${sign}${formatUsd(abs)}`;
}

function SpendingLimits() {
  const policy = useResource(api.spendingPolicy, "spending-policy");
  const [form, setForm] = useState<SpendingPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (policy.data) setForm(policy.data);
  }, [policy.data]);
  if (!form) return <Card title="Agent spending limits">{policy.error ? <ErrorText error={policy.error} /> : <Spinner />}</Card>;
  const set = <K extends keyof SpendingPolicy>(k: K, v: SpendingPolicy[K]) => {
    setSaved(false);
    setForm({ ...form, [k]: v });
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setForm(await api.saveSpendingPolicy(form!));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <Card title="Agent spending limits">
      <p className="muted small">
        Hard limits in USD, checked by deterministic code before your agent spends anything. The LLM cannot change them. Campaign budgets above the
        campaign limit are refused.
      </p>
      <form className="form" onSubmit={submit}>
        <label className="check">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Agent may spend from credit
        </label>
        <div className="grid-2">
          <label>
            Max per request ($)
            <UsdInput value={form.max_per_request} onChange={(e) => set("max_per_request", e.target.value)} />
          </label>
          <label>
            Max per day ($)
            <UsdInput value={form.max_daily} onChange={(e) => set("max_daily", e.target.value)} />
          </label>
          <label>
            Max campaign budget ($)
            <UsdInput value={form.max_campaign_budget} onChange={(e) => set("max_campaign_budget", e.target.value)} />
          </label>
          <label>
            Ask me above ($)
            <UsdInput value={form.approval_above} onChange={(e) => set("approval_above", e.target.value)} />
            <small>Spends above this wait for your approval.</small>
          </label>
        </div>
        <label>
          Allowed categories
          <input
            value={form.allowed_categories.join(", ")}
            onChange={(e) =>
              set(
                "allowed_categories",
                e.target.value
                  .split(",")
                  .map((x) => x.trim())
                  .filter(Boolean),
              )
            }
          />
          <small>Comma separated, e.g. premium_search.</small>
        </label>
        <div className="row">
          <button className="btn btn-primary">Save limits</button>
          {saved && <span className="chip good">Saved</span>}
        </div>
        <ErrorText error={error} />
      </form>
    </Card>
  );
}

function ResultsTable({ results }: { results: PremiumSearchResult["results"] }) {
  if (results.length === 0) return <Empty>No listed creators in this niche.</Empty>;
  return (
    <div className="table-scroll">
      <table className="table compact">
        <thead>
          <tr>
            <th>Creator</th>
            <th>Niches</th>
            <th>Subscribers</th>
            <th>Channel views</th>
            <th>Videos</th>
            <th>Deals completed</th>
            <th>Proofs accepted first try</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.id}>
              <td>
                <Link to={`/advertiser/creators/${encodeURIComponent(r.id)}`}>{r.name}</Link>
                <div className="muted small mono">{r.channel_id}</div>
              </td>
              <td className="small">{r.niches.join(", ")}</td>
              <td>{num(r.subscribers)}</td>
              <td>{num(r.channel_views)}</td>
              <td>{num(r.videos)}</td>
              <td>{r.zerveil_deals_completed}</td>
              <td>{r.proofs_accepted_first_try}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** HTTP 402 demo: request → 402 with a shielded invoice → pay → retry with X-Payment-Id → results. */
function PremiumSearchDemo() {
  const { status } = useSession();
  const [niche, setNiche] = useState("fitness");
  const [challenge, setChallenge] = useState<PaymentRequired | null>(null);
  const [result, setResult] = useState<PremiumSearchResult | null>(null);
  const [agent, setAgent] = useState<AgentPremiumSearch | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function request() {
    setResult(null);
    setAgent(null);
    setNote(null);
    const r = await api.premiumSearch(niche);
    if ("required" in r) setChallenge(r.required);
    else setResult(r.paid);
  }

  async function retry() {
    if (!challenge) return;
    const r = await api.premiumSearch(niche, challenge.payment.id);
    if ("required" in r) {
      setChallenge(r.required);
      setNote(`Still 402: the payment is ${r.required.payment.status}. Access unlocks only after it is confirmed.`);
    } else {
      setResult(r.paid);
      setChallenge(null);
      setNote(null);
    }
  }

  async function refreshChallenge() {
    if (!challenge) return;
    const inv = await api.publicInvoice(challenge.payment.id);
    setChallenge({ ...challenge, payment: { ...challenge.payment, status: inv.status, confirmations: inv.confirmations } });
  }

  return (
    <Card title="Premium creator search (HTTP 402)">
      <p className="muted small">
        A paid API: <code>GET /api/influencers/premium-search</code> answers <code>402 Payment Required</code> with a shielded invoice for{" "}
        {formatUsd(status?.premium_search_price)}. Pay it, then repeat the request with the <code>X-Payment-Id</code> header. A transaction id is not accepted
        as proof; only the scanner seeing the confirmed payment in the API seller's wallet unlocks it, once.
      </p>
      <div className="row">
        <label className="grow">
          Niche
          <input value={niche} onChange={(e) => setNiche(e.target.value)} minLength={2} maxLength={40} />
        </label>
        <ActionButton disabled={niche.trim().length < 2} onClick={request}>
          Request (expect 402)
        </ActionButton>
        <ActionButton
          variant="secondary"
          disabled={niche.trim().length < 2}
          onClick={async () => {
            setChallenge(null);
            setResult(null);
            setAgent(await api.agentPremiumSearch(niche));
          }}
        >
          Let my agent pay from API credit
        </ActionButton>
      </div>

      {challenge && (
        <div className="stack" style={{ marginTop: "1rem" }}>
          <Notice tone="warn">
            <strong>402 Payment Required.</strong> {challenge.detail}
          </Notice>
          <ShieldedCheckout invoice={challenge.payment} onChange={refreshChallenge} title="Pay to unlock" />
          <div className="row">
            <ActionButton onClick={retry}>Retry with X-Payment-Id: {challenge.payment.id}</ActionButton>
          </div>
        </div>
      )}
      {note && <p className="muted small">{note}</p>}

      {agent && (
        <div className="stack" style={{ marginTop: "1rem" }}>
          <Notice tone={agent.payment.status === "paid" ? "good" : agent.payment.status === "refused" ? "bad" : "warn"}>
            Agent payment: <strong>{humanize(agent.payment.status)}</strong>
            {agent.payment.reason && ` · ${agent.payment.reason}`}
            {agent.payment.status === "pending_approval" && " · approve it under API credit above."}
          </Notice>
          {agent.results && <ResultsTable results={agent.results} />}
        </div>
      )}

      {result && (
        <div className="stack" style={{ marginTop: "1rem" }}>
          <p className="small">
            Paid with <span className="mono">{result.paid_with}</span> · niche {result.niche}
          </p>
          <ResultsTable results={result.results} />
        </div>
      )}
    </Card>
  );
}

export function WalletPayments() {
  const invoices = useResource(api.invoices, "my-invoices");
  const [open, setOpen] = useState<string | null>(null);
  useLiveEvents((e) => e.type === "invoice", invoices.reload);
  const list = [...(invoices.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Payments</h1>
          <p className="muted">
            Payment requests you pay from your own wallet, directly to each recipient, plus your agent's API credit and spending limits.
          </p>
        </div>
        <TestnetNotice inline />
      </div>
      <PricingNotice />

      <div className="split">
        <Card title="Network and scanner">
          <WalletStatusPanel />
        </Card>
        <Card title="Privacy and payments">
          <PrivacyNote />
        </Card>
      </div>

      {open && <InvoiceDetail id={open} onClose={() => setOpen(null)} onChange={() => void invoices.reload()} />}

      <Card title="Payment requests">
        <ErrorText error={invoices.error} />
        {invoices.data ? <InvoiceHistory invoices={list} onOpen={setOpen} /> : <Spinner />}
      </Card>

      <div className="split">
        <CreditCard
          onInvoice={(id) => {
            setOpen(id);
            void invoices.reload();
          }}
        />
        <SpendingLimits />
      </div>

      <PremiumSearchDemo />
    </div>
  );
}
