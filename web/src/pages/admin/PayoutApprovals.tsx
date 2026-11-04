// Operators: approve or reject payouts decided by the rules, resolve unknown/failed sends, and check custody.

import { useState } from "react";
import { Navigate } from "react-router";
import { api } from "../../api";
import { PayoutStatusChip, SimulatedChip, TxLink } from "../../components/Payments";
import { ActionButton, Card, Empty, ErrorText, Notice, Spinner, Stat } from "../../components/ui";
import { PAYOUT_KIND, PURPOSE_LABEL, humanize, when, zec } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe } from "../../session";
import type { PayoutRequest } from "../../types";

function PayoutRow({ p, onDone }: { p: PayoutRequest; onDone: () => void }) {
  const [note, setNote] = useState("");
  const pending = p.status === "pending_approval";
  const stuck = p.status === "unknown" || p.status === "failed";
  return (
    <tr>
      <td>
        <strong>{PAYOUT_KIND[p.kind] ?? p.kind}</strong> · {zec(p.amount)} <SimulatedChip simulated={p.simulated} />
        <div className="muted small">{p.reason || "—"}</div>
        <div className="muted small">
          {p.campaign_id && <>campaign <span className="mono">{p.campaign_id}</span> · </>}
          {p.deal_id && <>deal <span className="mono">{p.deal_id}</span> · </>}
          {p.invoice_id && <>invoice <span className="mono">{p.invoice_id}</span> · </>}
          created {when(p.created_at)}
        </div>
      </td>
      <td className="small">
        <span className="mono" title={p.to_address}>
          {p.to_address.length > 24 ? `${p.to_address.slice(0, 14)}…${p.to_address.slice(-8)}` : p.to_address}
        </span>
        <div className="muted">beneficiary {p.beneficiary_id}</div>
      </td>
      <td className="small">
        <span className="mono" title={p.evidence_hash}>
          {p.evidence_hash ? `${p.evidence_hash.slice(0, 12)}…` : "—"}
        </span>
        {p.memo && <div className="muted">memo {p.memo}</div>}
      </td>
      <td>
        <PayoutStatusChip status={p.status} />
        {p.error && <div className="error small">{p.error}</div>}
        {p.txid && (
          <div className="small">
            <TxLink txid={p.txid} simulated={p.simulated} />
          </div>
        )}
        {p.attempts > 0 && <div className="muted small">{p.attempts} attempt(s)</div>}
        {p.approvals.map((a, i) => (
          <div key={i} className="muted small">
            {a.approved ? "approved" : "rejected"} by {a.by} {a.note && `· ${a.note}`}
          </div>
        ))}
      </td>
      <td>
        {(pending || stuck) && (
          <div className="stack" style={{ gap: "0.4rem" }}>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={stuck ? "Note (min 3 chars)" : "Note (optional)"} maxLength={300} />
            <div className="row">
              {pending && (
                <>
                  <ActionButton
                    onClick={async () => {
                      await api.adminDecidePayout(p.id, true, note);
                      onDone();
                    }}
                  >
                    Approve &amp; send
                  </ActionButton>
                  <ActionButton
                    variant="danger"
                    onClick={async () => {
                      await api.adminDecidePayout(p.id, false, note);
                      onDone();
                    }}
                  >
                    Reject
                  </ActionButton>
                </>
              )}
              {stuck && (
                <>
                  <ActionButton
                    disabled={note.trim().length < 3}
                    title="Only after checking the wallet: the earlier send did not go out"
                    onClick={async () => {
                      await api.adminResolvePayout(p.id, "resend", note);
                      onDone();
                    }}
                  >
                    Resend
                  </ActionButton>
                  <ActionButton
                    variant="danger"
                    disabled={note.trim().length < 3}
                    onClick={async () => {
                      await api.adminResolvePayout(p.id, "cancel", note);
                      onDone();
                    }}
                  >
                    Cancel
                  </ActionButton>
                </>
              )}
            </div>
          </div>
        )}
      </td>
    </tr>
  );
}

function Custody() {
  const custody = useResource(api.custody, "custody");
  const c = custody.data;
  return (
    <Card title="Custody" actions={<ActionButton variant="ghost" onClick={custody.reload}>Refresh</ActionButton>}>
      <ErrorText error={custody.error} />
      {!c ? (
        <Spinner />
      ) : (
        <>
          <div className="stats">
            <Stat label="Escrow balance" value={c.wallet.escrow_balance != null ? zec(c.wallet.escrow_balance) : "unknown"} />
            <Stat label="Received" value={zec(c.received_total)} />
            <Stat label="Paid out" value={zec(c.paid_out_total)} />
            <Stat label="Queued payouts" value={zec(c.queued_payouts_total)} />
            <Stat label="Expected before fees" value={zec(c.expected_balance_before_fees)} />
          </div>
          <p className="small">
            Wallet: {c.wallet.backend} on {c.wallet.network} <SimulatedChip simulated={c.wallet.simulated} />{" "}
            {c.wallet.reachable ? <span className="chip good">reachable</span> : <span className="chip warn">unreachable</span>}
            {c.wallet.detail && <span className="muted"> · {c.wallet.detail}</span>}
          </p>
          {c.wallet.warnings.map((w) => (
            <Notice key={w} tone="warn">
              {w}
            </Notice>
          ))}
          {c.note && <p className="muted small">{c.note}</p>}
        </>
      )}
    </Card>
  );
}

function Events() {
  const events = useResource(api.paymentEvents, "payment-events");
  useLiveEvents((e) => e.type === "invoice" || e.type === "payout", events.reload, 1000);
  const list = [...(events.data ?? [])].sort((a, b) => b.at.localeCompare(a.at)).slice(0, 60);
  return (
    <Card title="Recent payment events">
      <ErrorText error={events.error} />
      {!events.data ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Empty>No payment events yet.</Empty>
      ) : (
        <div className="table-scroll">
          <table className="table compact">
            <tbody>
              {list.map((e, i) => (
                <tr key={i}>
                  <td className="small">{when(e.at)}</td>
                  <td>{humanize(e.kind)}</td>
                  <td className="mono small">{e.invoice_id ?? e.payout_id ?? ""}</td>
                  <td className="small muted">{Object.keys(e.detail).length ? JSON.stringify(e.detail) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Invoices() {
  const invoices = useResource(api.adminInvoices, "admin-invoices");
  const list = [...(invoices.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 40);
  return (
    <details className="card">
      <summary>All invoices ({invoices.data?.length ?? "…"})</summary>
      <ErrorText error={invoices.error} />
      <table className="table compact">
        <tbody>
          {list.map((i) => (
            <tr key={i.id}>
              <td className="mono small">{i.id}</td>
              <td>{PURPOSE_LABEL[i.purpose] ?? i.purpose}</td>
              <td>{zec(i.amount)}</td>
              <td>
                {humanize(i.status)} <SimulatedChip simulated={i.simulated} />
              </td>
              <td className="small">{when(i.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

export function PayoutApprovals() {
  const me = useMe();
  const queue = useResource(() => (me.is_admin ? api.adminPayouts() : Promise.resolve([])), "admin-payouts");
  useLiveEvents((e) => e.type === "payout", queue.reload);
  if (!me.is_admin) return <Navigate to="/app" replace />;
  const list = queue.data ?? [];
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Payout approvals</h1>
          <p className="muted">
            Payouts are decided by deterministic rules from verified evidence. Check the reason and evidence hash, then approve; the escrow wallet sends
            only approved payouts. Escrow is a wallet account operated by Zerveil, not a smart contract.
          </p>
        </div>
      </div>

      <Card title={`Queue (${list.length})`} actions={<ActionButton variant="ghost" onClick={queue.reload}>Refresh</ActionButton>}>
        <ErrorText error={queue.error} />
        {!queue.data ? (
          <Spinner />
        ) : list.length === 0 ? (
          <Empty>Nothing waiting: no pending, unknown or failed payouts.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Payout</th>
                  <th>To</th>
                  <th>Evidence</th>
                  <th>Status</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <PayoutRow key={p.id} p={p} onDone={() => void queue.reload()} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">
          Unknown means the wallet may or may not have sent it: check the wallet before choosing Resend, or the creator could be paid twice.
        </p>
      </Card>

      <Custody />
      <Events />
      <Invoices />
    </div>
  );
}
