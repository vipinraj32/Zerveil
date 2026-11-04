// Creator payments: the receiving account (viewing key), payments advertisers sent directly to the creator's wallet,
// shareable shielded payment links, and the network/scanner status. Zerveil holds no funds.

import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api } from "../../api";
import { CopyButton, InvoiceStatusChip, PricingNotice, PrivacyNote, SimulatedChip, TestnetNotice, WalletStatusPanel } from "../../components/Payments";
import { ReceivingAccountPanel } from "../../components/ReceivingAccount";
import { ActionButton, Card, Empty, ErrorText, Notice, Spinner, Stat, UsdInput } from "../../components/ui";
import {
  MILESTONE_KIND,
  OPEN_INVOICE,
  PAID_INVOICE,
  PURPOSE_LABEL,
  formatUsd,
  requestValue,
  sumUsd,
  toCents,
  when,
} from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe } from "../../session";

function payUrl(path: string): string {
  try {
    return new URL(path, window.location.origin).toString();
  } catch {
    return path;
  }
}

function PaymentLinks() {
  const invoices = useResource(api.invoices, "my-invoices");
  useLiveEvents((e) => e.type === "invoice", invoices.reload);
  const [title, setTitle] = useState("");
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const links = (invoices.data ?? []).filter((i) => i.purpose === "payment_link").sort((a, b) => b.created_at.localeCompare(a.created_at));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.createPaymentLink({ title: title.trim(), amount: amount.trim(), description: description.trim() });
      setTitle("");
      setAmount("");
      setDescription("");
      await invoices.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Payment links">
      <p className="muted small">
        Ask for a USD amount and get paid directly in shielded Zcash: create a link, share it, and the payer sees a Zerveil payment page with a fresh
        shielded address of your own receiving account and a memo. The money goes straight to your wallet; Zerveil only confirms it arrived. Needs a
        connected viewing key.
      </p>
      <form className="form" onSubmit={submit}>
        <div className="grid-2">
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={80} placeholder="Shout-out in my next video" />
          </label>
          <label>
            Amount ($)
            <UsdInput value={amount} onChange={(e) => setAmount(e.target.value)} required placeholder="25.00" />
            <small>Dollars and cents, e.g. 25.00.</small>
          </label>
        </div>
        <label>
          Description <small>(optional, shown on the payment page)</small>
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} />
        </label>
        <div className="row">
          <button className="btn btn-primary" disabled={busy || !title.trim() || !/^\d+(\.\d{1,2})?$/.test(amount.trim()) || !((toCents(amount) ?? 0n) > 0n)}>
            {busy ? "Creating…" : "Create payment link"}
          </button>
        </div>
        <ErrorText error={error} />
      </form>

      <h3>Your links</h3>
      <ErrorText error={invoices.error} />
      {!invoices.data ? (
        <Spinner />
      ) : links.length === 0 ? (
        <Empty>No payment links yet.</Empty>
      ) : (
        <div className="table-scroll">
          <table className="table compact">
            <thead>
              <tr>
                <th>Title</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Created</th>
                <th>Link</th>
              </tr>
            </thead>
            <tbody>
              {links.map((l) => {
                const url = payUrl(l.pay_url || `/pay/${l.id}`);
                return (
                  <tr key={l.id}>
                    <td>
                      {l.title}
                      {l.description && <div className="muted small">{l.description}</div>}
                    </td>
                    <td>{requestValue(l, true)}</td>
                    <td>
                      <InvoiceStatusChip status={l.status} confirmations={l.confirmations} required={l.required_confirmations} />
                      <SimulatedChip simulated={l.simulated} />
                    </td>
                    <td className="small">{when(l.created_at)}</td>
                    <td>
                      <div className="row">
                        <Link to={`/pay/${encodeURIComponent(l.id)}`} className="small">
                          open
                        </Link>
                        <CopyButton value={url} label="Copy link" />
                        {l.status === "awaiting_payment" && l.notes.length === 0 && (
                          <ActionButton
                            variant="ghost"
                            onClick={async () => {
                              await api.cancelInvoice(l.id);
                              await invoices.reload();
                            }}
                          >
                            Cancel
                          </ActionButton>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** Payment requests paid (or to be paid) directly into the creator's wallet: milestones and payment links. */
function Received() {
  const { user } = useMe();
  const invoices = useResource(api.invoices, "my-invoices");
  useLiveEvents((e) => e.type === "invoice", invoices.reload);
  const list = (invoices.data ?? []).filter((i) => i.recipient_id === user.id).sort((a, b) => b.created_at.localeCompare(a.created_at));
  // USD values of USD-priced requests, summed in integer cents; the asset actually received alongside
  const paid = list.filter((i) => PAID_INVOICE.has(i.status));
  const pending = list.filter((i) => OPEN_INVOICE.has(i.status));
  const confirmed = sumUsd(paid.map((i) => i.usd_amount));
  const open = sumUsd(pending.map((i) => i.usd_amount));
  const legacyPending = pending.filter((i) => i.usd_amount == null);
  return (
    <>
      <div className="stats">
        <Stat
          label="Confirmed in your wallet"
          value={formatUsd(confirmed)}
          hint="USD value of the requests the scanner confirmed through Zerveil. Not your wallet balance."
        />
        <Stat
          label="Requested, not yet confirmed"
          value={formatUsd(open)}
          hint={`Payment requests advertisers or payers have not completed yet${legacyPending.length ? ` (plus ${legacyPending.length} legacy request(s) in test units)` : ""}`}
        />
        <Stat label="Requests" value={list.length} />
      </div>
      <Card title="Payments to your wallet">
        <ErrorText error={invoices.error} />
        {!invoices.data ? (
          <Spinner />
        ) : list.length === 0 ? (
          <Empty>No payment requests to your wallet yet.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table compact pay-tx">
              <thead>
                <tr>
                  <th>For</th>
                  <th>Amount</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Receipt</th>
                </tr>
              </thead>
              <tbody>
                {list.map((i) => (
                  <tr key={i.id}>
                    <td>
                      {i.title}
                      <div className="muted small">
                        {PURPOSE_LABEL[i.purpose] ?? i.purpose}
                        {i.deal_ids.length > 0 && (
                          <>
                            {" · "}
                            <Link to={`/influencer/deals/${encodeURIComponent(i.deal_ids[0])}`}>deal</Link>
                          </>
                        )}
                      </div>
                    </td>
                    <td>{requestValue(i, true)}</td>
                    <td>
                      <InvoiceStatusChip status={i.status} confirmations={i.confirmations} required={i.required_confirmations} />{" "}
                      <SimulatedChip simulated={i.simulated} />
                    </td>
                    <td className="small">{when(i.created_at)}</td>
                    <td>
                      {i.receipt_id ? (
                        <Link to={`/receipts/${encodeURIComponent(i.receipt_id)}`} className="mono small">
                          {i.receipt_id}
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
        <p className="muted small">
          Advertisers pay each milestone ({Object.values(MILESTONE_KIND).join(", ").toLowerCase()}) directly from their wallet to yours. Zerveil's view-only
          scanner confirms it with your viewing key; nothing passes through Zerveil.
        </p>
      </Card>
    </>
  );
}

export function CreatorPayments() {
  const { user } = useMe();
  const connected = !!user.receiving_account_id;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Payments</h1>
          <p className="muted">Advertisers pay you directly, into your own wallet. Zerveil holds no funds; it only confirms what arrived.</p>
        </div>
        <TestnetNotice inline />
      </div>
      <PricingNotice />

      {!user.zcash_address && (
        <Notice tone="warn">
          You have no shielded address yet. <Link to="/account">Add your own wallet's Unified Address</Link> so your agent can name it in signed terms.
        </Notice>
      )}
      {!connected && (
        <Notice tone="warn">
          Connect a viewing key of your receiving account below. Until then you are not listed for new deals and cannot create payment links.
        </Notice>
      )}

      <Card title="Receiving account">
        <ReceivingAccountPanel />
      </Card>

      <Received />

      <PaymentLinks />

      <Card title="Network and scanner">
        <WalletStatusPanel />
      </Card>

      <Card title="Privacy and payments">
        <PrivacyNote />
      </Card>
    </div>
  );
}
