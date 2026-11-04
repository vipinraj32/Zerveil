// Operators: a read-only view of the payment scanner, open payment requests and the verification audit trail.
// Zerveil holds no funds, so there is nothing to approve, send or return here: every payment goes directly from the
// payer's wallet to the recipient's own wallet, and the view-only scanner only confirms what arrived.

import { Navigate } from "react-router";
import { api } from "../../api";
import { InvoiceStatusChip, SimulatedChip, TestnetNotice } from "../../components/Payments";
import { ActionButton, Card, Empty, ErrorText, Notice, Spinner, Stat } from "../../components/ui";
import { PURPOSE_LABEL, formatAsset, humanize, num, requestValue, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";
import { useMe } from "../../session";

function Scanner() {
  const scanner = useResource(api.adminScanner, "admin-scanner");
  const s = scanner.data;
  return (
    <Card title="Payment scanner" actions={<ActionButton variant="ghost" onClick={scanner.reload}>Refresh</ActionButton>}>
      <ErrorText error={scanner.error} />
      {!s ? (
        !scanner.error && <Spinner />
      ) : (
        <>
          <div className="stats">
            <Stat label="Backend" value={s.scanner.backend} />
            <Stat label="Reachable" value={s.scanner.reachable ? "yes" : "no"} />
            <Stat label="Chain tip" value={s.scanner.tip_height != null ? num(s.scanner.tip_height) : "—"} />
            <Stat label="Receiving accounts" value={num(s.receiving_accounts)} hint="Active viewing keys connected by creators (and the API seller)" />
            <Stat label="Open requests" value={num(s.open_invoices)} />
          </div>
          <dl className="kv">
            <dt>Network</dt>
            <dd>
              {s.scanner.network}
              {s.scanner.chain_name && <span className="muted small"> · {s.scanner.chain_name}</span>} <SimulatedChip simulated={s.scanner.simulated} />
            </dd>
            <dt>Server</dt>
            <dd className="mono small">{s.scanner.server || "—"}</dd>
            <dt>NU7</dt>
            <dd>
              {s.scanner.nu7_active == null ? "unknown" : s.scanner.nu7_active ? "active" : "not active"}
              {s.scanner.nu7_activation_height != null && <span className="muted small"> · activation height {num(s.scanner.nu7_activation_height)}</span>}
            </dd>
            <dt>Custody</dt>
            <dd className="small">{s.custody}</dd>
          </dl>
          {s.scanner.warnings.length > 0 && (
            <Notice tone="warn">
              {s.scanner.warnings.map((w) => (
                <div key={w}>{w}</div>
              ))}
            </Notice>
          )}
          {s.scanner.detail && <p className="muted small">{s.scanner.detail}</p>}
        </>
      )}
    </Card>
  );
}

function Events() {
  const events = useResource(api.paymentEvents, "payment-events");
  useLiveEvents((e) => e.type === "invoice", events.reload, 1000);
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
                  <td className="mono small">{e.invoice_id ?? ""}</td>
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
  useLiveEvents((e) => e.type === "invoice", invoices.reload, 1000);
  const list = [...(invoices.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 60);
  return (
    <Card title={`Payment requests (${invoices.data?.length ?? "…"})`}>
      <ErrorText error={invoices.error} />
      {!invoices.data ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Empty>No payment requests yet.</Empty>
      ) : (
        <div className="table-scroll">
          <table className="table compact pay-tx">
            <thead>
              <tr>
                <th>Request</th>
                <th>Purpose</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Recipient</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {list.map((i) => (
                <tr key={i.id}>
                  <td className="mono small">{i.id}</td>
                  <td>{PURPOSE_LABEL[i.purpose] ?? i.purpose}</td>
                  <td title={`Payment details: ${formatAsset(i.amount, i.asset || i.ticker, 8)} requested, ${formatAsset(i.received, i.asset || i.ticker, 8)} received`}>
                    {requestValue(i, true)}
                  </td>
                  <td>
                    <InvoiceStatusChip status={i.status} confirmations={i.confirmations} required={i.required_confirmations} />{" "}
                    <SimulatedChip simulated={i.simulated} />
                  </td>
                  <td className="mono small">{i.recipient_id || "—"}</td>
                  <td className="small">{when(i.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function AdminPayments() {
  const me = useMe();
  if (!me.is_admin) return <Navigate to="/app" replace />;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Payments (operator view)</h1>
          <p className="muted">
            Read-only. Zerveil holds no funds: advertisers pay each creator directly, and the view-only scanner confirms receipt with viewing keys the
            creators connected. Operators cannot move money.
          </p>
        </div>
        <TestnetNotice inline />
      </div>
      <Scanner />
      <Invoices />
      <Events />
    </div>
  );
}
