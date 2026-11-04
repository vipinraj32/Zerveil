// Creator payments: payouts received from the escrow wallet, and shareable shielded payment links.

import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api } from "../../api";
import { CopyButton, InvoiceStatusChip, PayoutRequestsTable, PrivacyNote, SimulatedChip } from "../../components/Payments";
import { ActionButton, Card, Empty, ErrorText, Notice, Spinner, Stat } from "../../components/ui";
import { ticker, when, zec } from "../../format";
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
        Get paid directly in shielded {ticker()}: create a link, share it, and the payer sees a Zerveil payment page with a fresh shielded address and
        memo. The escrow wallet receives it and forwards it to your payout address after operator approval.
      </p>
      <form className="form" onSubmit={submit}>
        <div className="grid-2">
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={80} placeholder="Shout-out in my next video" />
          </label>
          <label>
            Amount ({ticker()})
            <input value={amount} onChange={(e) => setAmount(e.target.value)} required inputMode="decimal" placeholder="0.5" />
          </label>
        </div>
        <label>
          Description <small>(optional, shown on the payment page)</small>
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={300} />
        </label>
        <div className="row">
          <button className="btn btn-primary" disabled={busy || !title.trim() || !(Number(amount) > 0)}>
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
                    <td>{zec(l.amount)}</td>
                    <td>
                      <InvoiceStatusChip status={l.status} />
                      <SimulatedChip simulated={l.simulated} />
                    </td>
                    <td className="small">{when(l.created_at)}</td>
                    <td>
                      <div className="row">
                        <Link to={`/pay/${encodeURIComponent(l.id)}`} className="small">
                          open
                        </Link>
                        <CopyButton value={url} label="Copy link" />
                        {["pending", "underpaid"].includes(l.status) && (
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

export function CreatorPayments() {
  const { user } = useMe();
  const payouts = useResource(api.myPayouts, "my-payouts");
  useLiveEvents((e) => e.type === "payout", payouts.reload);
  const list = [...(payouts.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const delivered = list.filter((p) => p.status === "sent" || p.status === "confirmed").reduce((s, p) => s + Number(p.amount), 0);
  const waiting = list.filter((p) => ["pending_approval", "approved", "sending", "submitted"].includes(p.status)).reduce((s, p) => s + Number(p.amount), 0);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Payments</h1>
          <p className="muted">Shielded payouts to your Unified Address, and links to get paid directly.</p>
        </div>
      </div>

      {!user.zcash_address && (
        <Notice tone="warn">
          You have no payout address yet. <Link to="/account">Add your shielded Unified Address</Link> to get paid.
        </Notice>
      )}

      <div className="stats">
        <Stat label="Received" value={zec(delivered)} />
        <Stat label="On the way" value={zec(waiting)} hint="Decided by the rules; waiting for operator approval or confirmations" />
        <Stat label="Payouts" value={list.length} />
      </div>

      <Card title="Payouts received">
        <ErrorText error={payouts.error} />
        {payouts.data ? <PayoutRequestsTable payouts={list} showDeal /> : <Spinner />}
        <p className="muted small">
          Each payout is decided by deterministic rules from your verified post, approved by a Zerveil operator, then sent by the escrow wallet to your
          shielded address. "Waiting for operator approval" is normal and not a failure.
        </p>
      </Card>

      <PaymentLinks />

      <Card title="Privacy and custody">
        <PrivacyNote />
      </Card>
    </div>
  );
}
