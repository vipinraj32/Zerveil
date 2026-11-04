// Shielded Zcash payments: the checkout card for an invoice, payout delivery status, and the privacy explainers.
// Every status shown here is read from the backend (the escrow wallet's view of the chain); nothing here marks a
// payment done on its own. Simulated entries are labelled as such: no ZEC moved for them.

import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { INVOICE_STATUS, PAYOUT_KIND, PAYOUT_STATUS, txUrl, when, zec } from "../format";
import { useSession } from "../session";
import type { Deal, InvoiceStatus, PaymentChallenge, PaymentRequest, Payout, PayoutRequest, PayoutStatus } from "../types";
import { ActionButton } from "./ui";

const FUNDED_AT_LEAST = new Set(["FUNDED", "PROOF_SUBMITTED", "PROOF_REJECTED", "VERIFIED", "FIXED_RELEASED", "DISPUTED", "COMPLETED", "EXPIRED_REFUNDED"]);
const VERIFIED_AT_LEAST = new Set(["VERIFIED", "FIXED_RELEASED", "COMPLETED"]);

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

export function InvoiceStatusChip({ status }: { status: InvoiceStatus }) {
  const info = INVOICE_STATUS[status] ?? { label: status, tone: "neutral" };
  return <span className={`badge tone-${info.tone}`}>{info.label}</span>;
}

export function PayoutStatusChip({ status }: { status: PayoutStatus }) {
  const info = PAYOUT_STATUS[status] ?? { label: status, tone: "neutral" };
  return <span className={`badge tone-${info.tone}`}>{info.label}</span>;
}

export function SimulatedChip({ simulated }: { simulated: boolean }) {
  return simulated ? (
    <span className="chip warn" title="Simulated payment mode: no ZEC moved">
      simulated
    </span>
  ) : null;
}

/** A transaction id: an explorer link when one is configured and the txid is real, otherwise plain text. */
export function TxLink({ txid, simulated }: { txid: string | null | undefined; simulated?: boolean }) {
  const { status } = useSession();
  if (!txid) return <span className="muted">—</span>;
  const label = txid.length > 20 ? `${txid.slice(0, 10)}…${txid.slice(-6)}` : txid;
  const sim = simulated || txid.startsWith("sim");
  const url = txUrl(status?.zcash, txid, sim);
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

const OPEN = new Set<InvoiceStatus>(["pending", "underpaid", "confirming"]);

/** Pay a shielded invoice: amount, Unified Address, memo, ZIP-321 link, live status and (dev) simulate. */
export function ShieldedCheckout({
  invoice,
  onChange,
  title,
  showPayLink = true,
}: {
  invoice: PaymentRequest | PaymentChallenge;
  onChange?: () => void | Promise<void>;
  title?: ReactNode;
  showPayLink?: boolean;
}) {
  const { status, me } = useSession();
  const left = useCountdown(invoice.expires_at);
  const open = OPEN.has(invoice.status);
  const ticker = invoice.ticker || status?.zcash.ticker || "TAZ";
  const received = "received" in invoice ? invoice.received : null;
  const payPath = `/pay/${encodeURIComponent(invoice.id)}`;

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

      <dl className="kv checkout-kv">
        <dt>Pay to (shielded UA)</dt>
        <dd>
          <code className="checkout-value">{invoice.address}</code>
          <CopyButton value={invoice.address} label="Copy address" />
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
          <div className="muted small">Include this memo: it is how the escrow wallet matches your payment to this invoice.</div>
        </dd>
        <dt>Confirmations</dt>
        <dd>
          {invoice.confirmations} / {invoice.required_confirmations}
          {received && Number(received) > 0 && <span className="muted small"> · received {zec(received)}</span>}
        </dd>
        <dt>{open ? "Expires in" : "Expires"}</dt>
        <dd>{open && left !== null ? (left > 0 ? countdown(left) : "expired") : when(invoice.expires_at)}</dd>
      </dl>

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
              title="Development only: the stand-in wallet records a payment. No ZEC moves."
            >
              Simulate payment (dev, no ZEC moves)
            </ActionButton>
          )}
        </div>
      )}
      {invoice.status === "underpaid" && <p className="error">Less than the full amount arrived. Send the rest with the same memo before the invoice expires.</p>}
      <PrivacyNote compact />
    </div>
  );
}

/** Honest, short: what shielded means, what Zerveil sees, who holds the escrow. */
export function PrivacyNote({ compact = false }: { compact?: boolean }) {
  const { status } = useSession();
  const testnet = (status?.zcash.network ?? "testnet") !== "mainnet";
  if (compact)
    return (
      <p className="muted small privacy-note">
        Shielded: amount, sender and memo are encrypted on-chain. Zerveil sees this payment only because it holds the escrow wallet's viewing key.
        Escrow is a wallet account operated by Zerveil, not a smart contract.
        {testnet && ` ${status?.zcash.ticker ?? "TAZ"} is testnet coin with no monetary value.`}
      </p>
    );
  return (
    <details className="privacy-explainer" open>
      <summary>How privacy and custody work</summary>
      <ul>
        <li>
          <strong>Shielded.</strong> Payments go to fresh shielded Unified Addresses. Amount, sender and memo are encrypted on-chain; outside observers see
          only that a shielded transaction happened.
        </li>
        <li>
          <strong>What Zerveil can see.</strong> Zerveil holds the escrow wallet, so it sees amounts and memos arriving at its own addresses. It never
          publishes them. Creators' payout addresses are private to them and the operator; advertisers never see them.
        </li>
        <li>
          <strong>Custody.</strong> Escrow is a wallet account operated by Zerveil, not a smart contract. Releases follow deterministic rules and an
          operator approval.
        </li>
        {testnet && (
          <li>
            <strong>Testnet.</strong> {status?.zcash.ticker ?? "TAZ"} has no monetary value.
            {status?.payments_simulated && " Simulated payments are on: no ZEC moves at all."}
          </li>
        )}
      </ul>
    </details>
  );
}

/** Payout requests (decided by code, approved by an operator, sent by the escrow wallet) with their delivery status. */
export function PayoutRequestsTable({ payouts, showDeal, showTo = true }: { payouts: PayoutRequest[]; showDeal?: boolean; showTo?: boolean }) {
  if (payouts.length === 0) return <p className="muted small">No payouts yet.</p>;
  return (
    <div className="table-scroll">
      <table className="table compact pay-tx">
        <thead>
          <tr>
            {showDeal && <th>Deal</th>}
            <th>Payout</th>
            <th>Amount</th>
            {showTo && <th>To</th>}
            <th>Status</th>
            <th>Transaction</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {payouts.map((p) => (
            <tr key={p.id}>
              {showDeal && <td className="mono small">{p.deal_id ?? "—"}</td>}
              <td>
                {PAYOUT_KIND[p.kind] ?? p.kind}
                {p.reason && <div className="muted small">{p.reason}</div>}
              </td>
              <td>{zec(p.amount)}</td>
              {showTo && (
                <td className="small">
                  {p.to_address.startsWith("u") ? (
                    <span className="mono" title={p.to_address}>
                      {p.to_address.slice(0, 10)}…{p.to_address.slice(-6)}
                    </span>
                  ) : (
                    <span className="muted">{p.to_address}</span>
                  )}
                </td>
              )}
              <td>
                <PayoutStatusChip status={p.status} />
                {p.error && <div className="error small">{p.error}</div>}
                {(p.status === "sent" || p.status === "confirmed") && p.confirmations > 0 && <div className="muted small">{p.confirmations} confirmations</div>}
              </td>
              <td>
                <TxLink txid={p.txid} simulated={p.simulated} />
              </td>
              <td className="small">{when(p.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Step = { label: string; done: boolean; detail?: string; bad?: boolean };

function steps(d: Deal, requests: PayoutRequest[]): Step[] {
  const decided = (kind: Payout["kind"]) => d.payouts.find((p) => p.kind === kind);
  const delivery = (p: Payout | undefined) => (p ? requests.find((r) => r.id === p.payout_id) : undefined);
  const deliveredLabel = (p: Payout | undefined) => {
    const r = delivery(p);
    return r ? (PAYOUT_STATUS[r.status]?.label ?? r.status).toLowerCase() : "decided";
  };
  const isSent = (p: Payout | undefined) => {
    const r = delivery(p);
    return !!r && (r.status === "sent" || r.status === "confirmed");
  };
  const fixed = decided("fixed");
  const perf = decided("performance");
  const verified = VERIFIED_AT_LEAST.has(d.state) || !!fixed;
  const funded = !!d.funded_at && FUNDED_AT_LEAST.has(d.state);
  return [
    {
      label: funded ? "Escrow funded" : d.state === "FUNDING" ? "Payment seen, confirming" : "Escrow funding",
      done: funded,
      detail: d.terms ? `${zec(d.terms.cap)} cap${d.funded_at ? ` · ${when(d.funded_at)}` : ""}` : undefined,
    },
    { label: "Proof submitted", done: !!d.proof, detail: d.proof ? when(d.proof.submitted_at) : undefined },
    d.state === "PROOF_REJECTED"
      ? { label: "Proof rejected", done: false, bad: true, detail: d.proof?.review?.resubmit_by ? `resubmit by ${when(d.proof.review.resubmit_by)}` : undefined }
      : { label: "Proof verified", done: verified },
    {
      label: fixed ? "Fixed fee released" : "Fixed fee pending",
      done: isSent(fixed),
      detail: fixed ? `${zec(fixed.amount)} · ${deliveredLabel(fixed)}` : d.fixed_release_due ? `due ${when(d.fixed_release_due)}` : undefined,
    },
    {
      label: perf ? "Performance fee released" : "Performance fee pending",
      done: isSent(perf),
      detail: perf
        ? `${zec(perf.amount)}${perf.views != null ? ` for ${perf.views.toLocaleString()} views` : ""} · ${deliveredLabel(perf)}`
        : d.performance_due
          ? `views measured ${when(d.performance_due)}`
          : undefined,
    },
  ];
}

/** The payment lifecycle as a checklist; the first unfinished step is the current one. */
export function PaymentLifecycle({ deal }: { deal: Deal }) {
  const list = steps(deal, deal.payout_requests ?? []);
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

/** Funding of one deal: its shielded invoice (shared by the deals approved together). */
export function DealFunding({ deal }: { deal: Deal }) {
  const inv = deal.funding_invoice;
  if (!inv && !deal.funding_invoice_id) return null;
  return (
    <dl className="kv">
      <dt>Funding invoice</dt>
      <dd>
        {inv ? (
          <>
            <Link to={`/pay/${encodeURIComponent(inv.id)}`} className="mono">
              {inv.id}
            </Link>{" "}
            <InvoiceStatusChip status={inv.status} />
            <SimulatedChip simulated={inv.simulated} />
          </>
        ) : (
          <span className="mono">{deal.funding_invoice_id}</span>
        )}
      </dd>
      {inv && (
        <>
          <dt>Invoice amount</dt>
          <dd>
            {zec(inv.amount)} <span className="muted small">(all deals approved together, plus fee reserve)</span>
          </dd>
          <dt>Confirmations</dt>
          <dd>
            {inv.confirmations} / {inv.required_confirmations}
          </dd>
        </>
      )}
      <dt>Funded</dt>
      <dd>
        {deal.funded_at ? when(deal.funded_at) : "not yet"}
        {deal.funded_at && deal.funding_simulated && <span className="chip warn">simulated, no ZEC moved</span>}
      </dd>
    </dl>
  );
}
