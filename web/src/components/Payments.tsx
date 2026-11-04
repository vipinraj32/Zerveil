// Direct shielded Zcash payments: the checkout card for an invoice, a deal's milestones, the network/scanner panel,
// receipts and the privacy explainers. Zerveil holds no funds: every invoice is paid straight to the recipient's own
// wallet, and every status shown here is read from the backend (the view-only scanner's data). Nothing here marks a
// payment done on its own. Simulated entries are labelled as such: no ZEC moved for them.

import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { INVOICE_STATUS, LEGACY_PAYMENT_KIND, MILESTONE_KIND, MILESTONE_STATUS, OPEN_INVOICE, PAID_INVOICE, confirmationProgress, num, txUrl, when, zec } from "../format";
import { useSession } from "../session";
import type { Deal, InvoiceStatus, LegacyPayout, MilestoneInvoice, MilestoneStatus, PaymentChallenge, PaymentRequest, WalletStatus } from "../types";
import { QrCode } from "./QrCode";
import { ActionButton, Address, Notice } from "./ui";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked: the value is selectable on screen */
        }
      }}
    >
      {done ? "Copied" : label}
    </button>
  );
}

/** Invoice status, with "2 / 3 confirmations" once the scanner has seen a payment. */
export function InvoiceStatusChip({
  status,
  confirmations,
  required,
}: {
  status: InvoiceStatus;
  confirmations?: number;
  required?: number;
}) {
  const info = INVOICE_STATUS[status] ?? { label: status, tone: "neutral" };
  const progress =
    confirmations !== undefined && required !== undefined && !PAID_INVOICE.has(status) ? confirmationProgress({ status, confirmations, required_confirmations: required }) : "";
  return (
    <span className={`badge tone-${info.tone}`}>
      {info.label}
      {progress && ` · ${progress}`}
    </span>
  );
}

export function MilestoneStatusChip({ status }: { status: MilestoneStatus }) {
  const info = MILESTONE_STATUS[status] ?? { label: status, tone: "neutral" };
  return <span className={`badge tone-${info.tone}`}>{info.label}</span>;
}

export function SimulatedChip({ simulated }: { simulated: boolean }) {
  return simulated ? (
    <span className="chip warn" title="Simulated payment mode: no ZEC moved">
      Simulated — no ZEC moved
    </span>
  ) : null;
}

/** "Zcash Testnet — TAZ — No real monetary value", from /api/status (null on mainnet). */
export function TestnetNotice({ inline = false }: { inline?: boolean }) {
  const { status } = useSession();
  const notice = status?.zcash.testnet_notice;
  if (!notice) return null;
  return inline ? (
    <span className="testnet-chip">{notice}</span>
  ) : (
    <div className="testnet-banner" role="note">
      {notice}
    </div>
  );
}

/** A transaction id: an explorer link when one is configured and the txid is real, otherwise plain text. */
export function TxLink({ txid, simulated, href }: { txid: string | null | undefined; simulated?: boolean; href?: string }) {
  const { status } = useSession();
  if (!txid) return <span className="muted">—</span>;
  const label = txid.length > 20 ? `${txid.slice(0, 10)}…${txid.slice(-6)}` : txid;
  const sim = simulated || txid.startsWith("sim");
  const url = sim ? null : (href ?? txUrl(status?.zcash, txid, sim));
  return (
    <>
      {url ? (
        <a className="mono" href={url} target="_blank" rel="noreferrer" title={txid}>
          {label} ↗
        </a>
      ) : (
        <span className="mono" title={txid}>
          {label}
        </span>
      )}
      {sim && <span className="chip warn">simulated</span>}
    </>
  );
}

/** Seconds left until `iso`, ticking every second. */
function useCountdown(iso: string | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!iso) return null;
  return Math.max(0, Math.floor((new Date(iso).getTime() - now) / 1000));
}

function countdown(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}

/** Pay a shielded invoice directly to the recipient's wallet: amount, Unified Address, memo, ZIP-321 link + QR, live status. */
export function ShieldedCheckout({
  invoice,
  onChange,
  title,
  showPayLink = true,
  showQr = true,
  receiptId,
}: {
  invoice: PaymentRequest | PaymentChallenge;
  onChange?: () => void | Promise<void>;
  title?: ReactNode;
  showPayLink?: boolean;
  showQr?: boolean;
  receiptId?: string | null;
}) {
  const { status, me } = useSession();
  const left = useCountdown(invoice.expires_at);
  const open = OPEN_INVOICE.has(invoice.status);
  const ticker = invoice.ticker || status?.zcash.ticker || "TAZ";
  const received = "received" in invoice ? invoice.received : null;
  const payPath = `/pay/${encodeURIComponent(invoice.id)}`;
  const progress = confirmationProgress(invoice);
  const receipt = receiptId ?? ("receipt_id" in invoice ? (invoice as { receipt_id?: string | null }).receipt_id : null);

  return (
    <div className="checkout">
      <div className="checkout-head">
        <div>
          {title && <h2>{title}</h2>}
          <div className="checkout-amount">
            {invoice.amount} <span>{ticker}</span>
          </div>
        </div>
        <div className="row">
          <InvoiceStatusChip status={invoice.status} />
          <SimulatedChip simulated={invoice.simulated} />
        </div>
      </div>

      <div className="checkout-body">
        <dl className="kv checkout-kv">
          <dt>Pay to (shielded UA)</dt>
          <dd>
            <code className="checkout-value">{invoice.address}</code>
            <CopyButton value={invoice.address} label="Copy address" />
            <div className="muted small">A fresh address of the recipient's own wallet. Zerveil never holds the money.</div>
          </dd>
          <dt>Amount</dt>
          <dd>
            <code className="checkout-value">{invoice.amount}</code> {ticker}
            <CopyButton value={invoice.amount} label="Copy amount" />
          </dd>
          <dt>Memo</dt>
          <dd>
            <code className="checkout-value">{invoice.memo}</code>
            <CopyButton value={invoice.memo} label="Copy memo" />
            <div className="muted small">Include this memo: it is how the scanner matches your payment to this request.</div>
          </dd>
          <dt>Confirmations</dt>
          <dd>
            {progress || `0 / ${invoice.required_confirmations} confirmations`}
            {received && Number(received) > 0 && <span className="muted small"> · received {zec(received)}</span>}
          </dd>
          <dt>{open ? "Expires in" : "Expires"}</dt>
          <dd>{open && left !== null ? (left > 0 ? countdown(left) : "expired") : when(invoice.expires_at)}</dd>
          {receipt && (
            <>
              <dt>Receipt</dt>
              <dd>
                <Link to={`/receipts/${encodeURIComponent(receipt)}`} className="mono">
                  {receipt}
                </Link>
              </dd>
            </>
          )}
        </dl>
        {open && showQr && <QrCode value={invoice.zip321_uri} label={`ZIP-321 payment request for ${invoice.amount} ${ticker}`} />}
      </div>

      {open && (
        <div className="row">
          <a className="btn btn-primary" href={invoice.zip321_uri}>
            Open in wallet
          </a>
          <CopyButton value={invoice.zip321_uri} label="Copy payment URI" />
          {showPayLink && (
            <Link className="btn btn-secondary" to={payPath}>
              Payment page
            </Link>
          )}
          {status?.payments_simulated && me && (
            <ActionButton
              variant="ghost"
              onClick={async () => {
                await api.simulatePayment(invoice.id, { confirmations: invoice.required_confirmations });
                await onChange?.();
              }}
              title="Development only: the stand-in scanner records a payment. No ZEC moves."
            >
              Simulate payment (dev, no ZEC moves)
            </ActionButton>
          )}
        </div>
      )}
      {invoice.status === "underpaid" && <p className="error">Less than the full amount arrived. Send the rest with the same memo before the request expires.</p>}
      {invoice.status === "overpaid" && <p className="muted small">More than the requested amount arrived. Settle the difference directly with the recipient.</p>}
      {invoice.status === "failed" && <p className="error">The payment did not confirm (for example, the transaction expired unmined). Ask for a new request.</p>}
      <PrivacyNote compact />
    </div>
  );
}

/** Honest, short: what shielded means, what Zerveil sees, and that it holds nothing. */
export function PrivacyNote({ compact = false }: { compact?: boolean }) {
  const { status } = useSession();
  const notice = status?.zcash.testnet_notice;
  if (compact)
    return (
      <p className="muted small privacy-note">
        Shielded: amount, sender and memo are encrypted on-chain. Payments go directly to the recipient's own wallet; Zerveil sees them only through a
        viewing key the recipient connected, which cannot spend.
        {notice && ` ${notice}.`}
      </p>
    );
  return (
    <details className="privacy-explainer" open>
      <summary>How privacy and payments work</summary>
      <ul>
        <li>
          <strong>Direct.</strong> Each payment goes straight from the payer's wallet to a fresh shielded address of the recipient's own wallet. Zerveil
          never holds, forwards or returns funds.
        </li>
        <li>
          <strong>Shielded.</strong> Amount, sender and memo are encrypted on-chain; outside observers see only that a shielded transaction happened.
        </li>
        <li>
          <strong>What Zerveil can see.</strong> Creators connect a viewing key of a dedicated receiving account. It lets Zerveil's scanner see that
          account's transactions to confirm payments; it cannot spend. Creators' addresses stay private to them and the operator.
        </li>
        {notice && (
          <li>
            <strong>Testnet.</strong> {notice}.{status?.payments_simulated && " Simulated payments are on: no ZEC moves at all."}
          </li>
        )}
      </ul>
    </details>
  );
}

type Step = { label: string; done: boolean; detail?: string; bad?: boolean };

const VERIFIED_AT_LEAST = new Set(["VERIFIED", "PERFORMANCE_DUE", "COMPLETED"]);

function milestoneDetail(amount: string, status: MilestoneStatus, at: string | null, views?: number | null): string {
  const parts = [zec(amount)];
  if (views != null) parts.push(`for ${views.toLocaleString()} views`);
  parts.push(MILESTONE_STATUS[status]?.label.toLowerCase() ?? status);
  if (at) parts.push(when(at));
  return parts.join(" · ");
}

function steps(d: Deal): Step[] {
  const fixed = d.milestones.find((m) => m.kind === "fixed");
  const perf = d.milestones.find((m) => m.kind === "performance");
  const verified = VERIFIED_AT_LEAST.has(d.state);
  const list: Step[] = [];
  if (fixed)
    list.push({
      label: fixed.status === "confirmed" ? "Fixed fee confirmed in creator's wallet" : "Fixed fee paid directly to the creator",
      done: fixed.status === "confirmed",
      bad: fixed.status === "expired",
      detail: milestoneDetail(fixed.amount, fixed.status, fixed.confirmed_at ?? fixed.requested_at),
    });
  else if (d.terms && Number(d.terms.fixed_fee) === 0) list.push({ label: "No upfront payment (CPM only)", done: true });
  list.push({ label: "Proof submitted", done: !!d.proof, detail: d.proof ? when(d.proof.submitted_at) : undefined });
  list.push(
    d.state === "PROOF_REJECTED"
      ? { label: "Proof rejected", done: false, bad: true, detail: d.proof?.review?.resubmit_by ? `resubmit by ${when(d.proof.review.resubmit_by)}` : undefined }
      : { label: "Proof verified", done: verified },
  );
  list.push(
    perf
      ? {
          label: perf.status === "confirmed" ? "Performance fee confirmed in creator's wallet" : "Performance fee",
          done: perf.status === "confirmed" || perf.status === "waived",
          bad: perf.status === "expired",
          detail: milestoneDetail(perf.amount, perf.status, perf.confirmed_at ?? perf.requested_at ?? perf.due_at, perf.views),
        }
      : { label: "Performance fee", done: false, detail: d.performance_due ? `views measured ${when(d.performance_due)}` : undefined },
  );
  return list;
}

/** The payment lifecycle as a checklist; the first unfinished step is the current one. */
export function PaymentLifecycle({ deal }: { deal: Deal }) {
  if (deal.state === "ARCHIVED") return null;
  const list = steps(deal);
  const current = list.findIndex((s) => !s.done);
  return (
    <ol className="pay-steps">
      {list.map((s, i) => (
        <li key={s.label} className={s.done ? "done" : s.bad ? "bad current" : i === current ? "current" : ""}>
          <span className="pay-dot" aria-hidden>
            {s.done ? "✓" : s.bad ? "✗" : i + 1}
          </span>
          <span>
            {s.label}
            {s.detail && <span className="muted small"> · {s.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** A deal's milestones (fixed / performance) with their payment requests and receipts. */
export function MilestoneTable({ deal, invoices }: { deal: Deal; invoices?: MilestoneInvoice[] }) {
  if (deal.milestones.length === 0) return <p className="muted small">No payment milestones yet: they are scheduled when the advertiser accepts the agreement.</p>;
  const byMilestone = new Map((invoices ?? []).map((i) => [i.milestone_id, i]));
  return (
    <div className="table-scroll">
      <table className="table compact pay-tx">
        <thead>
          <tr>
            <th>Milestone</th>
            <th>Amount</th>
            <th>Status</th>
            <th>Payment request</th>
            <th>Receipt</th>
          </tr>
        </thead>
        <tbody>
          {deal.milestones.map((m) => {
            const inv = byMilestone.get(m.id);
            return (
              <tr key={m.id}>
                <td>
                  {MILESTONE_KIND[m.kind] ?? m.kind}
                  {m.views != null && <div className="muted small">{num(m.views)} verified views</div>}
                  {m.due_at && m.status === "scheduled" && <div className="muted small">due {when(m.due_at)}</div>}
                </td>
                <td>{zec(m.amount)}</td>
                <td>
                  <MilestoneStatusChip status={m.status} />
                  {m.confirmed_at && <div className="muted small">{when(m.confirmed_at)}</div>}
                </td>
                <td>
                  {inv ? (
                    <>
                      <Link to={`/pay/${encodeURIComponent(inv.id)}`} className="mono small">
                        {inv.id}
                      </Link>{" "}
                      <InvoiceStatusChip status={inv.status} confirmations={inv.confirmations} required={inv.required_confirmations} />
                      <SimulatedChip simulated={inv.simulated} />
                    </>
                  ) : m.invoice_id ? (
                    <span className="mono small">{m.invoice_id}</span>
                  ) : (
                    <span className="muted small">not requested yet</span>
                  )}
                </td>
                <td>
                  {(m.receipt_id ?? inv?.receipt_id) ? (
                    <Link to={`/receipts/${encodeURIComponent((m.receipt_id ?? inv?.receipt_id) as string)}`} className="mono small">
                      {m.receipt_id ?? inv?.receipt_id}
                    </Link>
                  ) : (
                    <span className="muted small">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {deal.payment_simulated && <p className="muted small">Confirmed by the simulated scanner: no ZEC moved.</p>}
    </div>
  );
}

/** Payments of an archived deal made by an earlier payment flow. Read-only history. */
export function LegacyPaymentHistory({ payouts }: { payouts: LegacyPayout[] }) {
  if (payouts.length === 0) return null;
  return (
    <details className="legacy-history">
      <summary className="muted small">History from an earlier payment system ({payouts.length})</summary>
      <div className="table-scroll">
        <table className="table compact pay-tx">
          <thead>
            <tr>
              <th>Kind</th>
              <th>Amount</th>
              <th>When</th>
              <th>Transaction</th>
            </tr>
          </thead>
          <tbody>
            {payouts.map((p, i) => (
              <tr key={p.payout_id || i}>
                <td>{LEGACY_PAYMENT_KIND[p.kind] ?? p.kind}</td>
                <td>{zec(p.amount)}</td>
                <td className="small">{when(p.at)}</td>
                <td>{p.legacy_tx_hash ? <span className="mono small">{p.legacy_tx_hash.slice(0, 12)}…</span> : <SimulatedChip simulated={p.simulated} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/** Network and scanner status for the signed-in user. Never shows credentials or scanner paths. */
export function WalletStatusPanel() {
  const { me } = useSession();
  const [w, setW] = useState<WalletStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.walletStatus().then(setW, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  if (error) return <p className="error small">{error}</p>;
  if (!w) return <p className="muted small">Loading network status…</p>;
  const creator = me?.user.role === "influencer";
  return (
    <div className="stack">
      <dl className="kv">
        <dt>Network</dt>
        <dd>
          Zcash {w.network} <SimulatedChip simulated={w.simulated} />
        </dd>
        <dt>Payment scanner</dt>
        <dd>
          {w.backend} · {w.reachable ? <span className="badge tone-good">reachable</span> : <span className="badge tone-bad">unreachable</span>}
          <div className="muted small">View-only: it confirms payments, it cannot send any.</div>
        </dd>
        <dt>Chain tip</dt>
        <dd>{w.tip_height != null ? num(w.tip_height) : "—"}</dd>
        <dt>NU7</dt>
        <dd>
          {w.nu7_active == null ? "unknown" : w.nu7_active ? "active" : "not active"}
          {w.nu7_activation_height != null && <span className="muted small"> · activation height {num(w.nu7_activation_height)}</span>}
        </dd>
        <dt>Your address</dt>
        <dd>{w.your_address ? <Address value={w.your_address} /> : <span className="muted">not set</span>}</dd>
        {creator && (
          <>
            <dt>Receiving account</dt>
            <dd>{w.receiving_account_connected ? <span className="badge tone-good">viewing key connected</span> : <span className="badge tone-warn">not connected</span>}</dd>
            <dt>Confirmed received via Zerveil</dt>
            <dd>
              {zec(w.confirmed_received_via_zerveil)}
              <div className="muted small">What the scanner confirmed arriving through Zerveil requests. Not your wallet balance.</div>
            </dd>
          </>
        )}
        <dt>Custody</dt>
        <dd className="small">{w.custody}</dd>
      </dl>
      {w.warnings.length > 0 && (
        <Notice tone="warn">
          {w.warnings.map((m) => (
            <div key={m}>{m}</div>
          ))}
        </Notice>
      )}
      {w.detail && <p className="muted small">{w.detail}</p>}
      {w.faucet_url && (
        <p className="small">
          Need testnet coins?{" "}
          <a href={w.faucet_url} target="_blank" rel="noreferrer">
            Testnet faucet ↗
          </a>{" "}
          <span className="muted">— you claim TAZ yourself, into your own wallet. TAZ has no real monetary value.</span>
        </p>
      )}
    </div>
  );
}
