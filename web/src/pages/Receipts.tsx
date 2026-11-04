// Receipts of confirmed payments the signed-in user made or received. A receipt is issued only after the view-only
// scanner confirmed the payment in the recipient's own wallet; `receipt_hash` lets either party show a third party
// exactly what they were given. Simulated receipts are labelled: nothing moved on-chain for them.

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api";
import { CopyButton, PricingNotice, SimulatedChip, TestnetNotice, TxLink } from "../components/Payments";
import { Card, Empty, ErrorText, Spinner } from "../components/ui";
import { PURPOSE_LABEL, formatAsset, formatUsd, num, paymentAsset, when } from "../format";
import { useMe } from "../session";
import type { Receipt } from "../types";

/** The payment asset of a receipt (receipts issued before USD pricing carry none: they were in the legacy unit). */
const assetOf = (r: Receipt) => r.asset || paymentAsset();
const isLegacy = (r: Receipt) => r.usd_amount === null || r.usd_amount === undefined;

export function Receipts() {
  const { user } = useMe();
  const [list, setList] = useState<Receipt[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.receipts().then(setList, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Receipts</h1>
          <p className="muted">Confirmed payments you made or received. Every one was paid directly to the recipient's own wallet.</p>
          <PricingNotice />
        </div>
        <TestnetNotice inline />
      </header>
      <Card>
        <ErrorText error={error} />
        {!list ? (
          !error && <Spinner />
        ) : list.length === 0 ? (
          <Empty>No receipts yet. A receipt is issued once the scanner confirms a payment.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table compact pay-tx">
              <thead>
                <tr>
                  <th>Receipt</th>
                  <th>For</th>
                  <th>Amount</th>
                  <th>Direction</th>
                  <th>Confirmations</th>
                  <th>Issued</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/receipts/${encodeURIComponent(r.id)}`} className="mono small">
                        {r.id}
                      </Link>{" "}
                      <SimulatedChip simulated={r.simulated} />
                    </td>
                    <td>
                      {r.title}
                      <div className="muted small">{PURPOSE_LABEL[r.purpose] ?? r.purpose}</div>
                    </td>
                    <td>{formatUsd(isLegacy(r) ? r.amount : r.usd_amount)}</td>
                    <td className="small">{r.recipient_id === user.id ? "Received" : r.payer_id === user.id ? "Paid" : "—"}</td>
                    <td>{r.confirmations}</td>
                    <td className="small">{when(r.issued_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export function ReceiptView() {
  const { id = "" } = useParams();
  const [r, setR] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.receipt(id).then(setR, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  if (error) return <Card title="Receipt not found"><ErrorText error={error} /></Card>;
  if (!r) return <Spinner />;
  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="muted small">
            <Link to="/receipts">Receipts</Link> / {r.id}
          </p>
          <h1>{r.title}</h1>
        </div>
        <div className="row">
          <SimulatedChip simulated={r.simulated} />
          <TestnetNotice inline />
        </div>
      </header>
      {r.simulated && <p className="muted small">Confirmed by the simulated scanner: nothing moved on-chain. This receipt is not evidence of a real payment.</p>}
      <Card>
        <div className="checkout-amount">
          {isLegacy(r) ? (
            <>
              {formatAsset(r.received || r.amount, assetOf(r))} <span>legacy (priced in {assetOf(r)})</span>
            </>
          ) : (
            formatUsd(r.usd_amount)
          )}
        </div>
        <p className="muted small">
          {PURPOSE_LABEL[r.purpose] ?? r.purpose} · {when(r.issued_at)}
          {isLegacy(r) && " · legacy receipt from before USD pricing; no dollar value"}
        </p>
      </Card>
      <Card title={`Technical details${r.network.endsWith("testnet") ? " (Zcash testnet)" : ""}`}>
        <dl className="kv">
          <dt>Payment asset</dt>
          <dd>
            {assetOf(r)} (shielded Zcash{r.network.endsWith("testnet") ? ", testnet" : ""})
          </dd>
          <dt>Amount requested</dt>
          <dd>{formatAsset(r.amount, assetOf(r), 8)}</dd>
          <dt>Amount received</dt>
          <dd>{formatAsset(r.received, assetOf(r), 8)}</dd>
          {!isLegacy(r) && r.usd_rate && (
            <>
              <dt>Rate</dt>
              <dd>
                {formatUsd(r.usd_rate)} per {assetOf(r)}
                {r.test_rate && (
                  <div className="muted small">
                    <em>a test rate, not a market price; the testnet amount has no real value · no stablecoin</em>
                  </div>
                )}
                {r.rate_source && <div className="muted small">Source: {r.rate_source}</div>}
              </dd>
            </>
          )}
          <dt>Purpose</dt>
          <dd>{PURPOSE_LABEL[r.purpose] ?? r.purpose}</dd>
          <dt>Network</dt>
          <dd>{r.network}</dd>
          <dt>Shielded pools</dt>
          <dd>{r.pools.length ? r.pools.join(", ") : "—"}</dd>
          <dt>Transactions</dt>
          <dd>
            {r.txids.length === 0 ? (
              <span className="muted">—</span>
            ) : (
              <ul className="plain-list">
                {r.txids.map((t, i) => (
                  <li key={t}>
                    <TxLink txid={t} simulated={r.simulated} href={r.explorer_urls[i]} />
                  </li>
                ))}
              </ul>
            )}
          </dd>
          <dt>Confirmations</dt>
          <dd>{r.confirmations}</dd>
          <dt>Mined height</dt>
          <dd>{r.mined_height != null ? num(r.mined_height) : "—"}</dd>
          <dt>Memo</dt>
          <dd className="mono">{r.memo}</dd>
          <dt>Payment request</dt>
          <dd className="mono">{r.invoice_id}</dd>
          {r.deal_id && (
            <>
              <dt>Deal</dt>
              <dd className="mono">{r.deal_id}</dd>
            </>
          )}
          <dt>Issued</dt>
          <dd>{when(r.issued_at)}</dd>
          <dt>Receipt hash</dt>
          <dd>
            <code className="checkout-value">{r.receipt_hash}</code> <CopyButton value={r.receipt_hash} label="Copy hash" />
            <div className="muted small">sha256 of this receipt's contents: share it to let a third party check exactly what you were given.</div>
          </dd>
        </dl>
      </Card>
    </div>
  );
}
