// Public payment page (/pay/:id): anyone with the link can pay a shielded Zcash payment request, directly to the
// recipient's own wallet. No sign-in required. It polls the public request every 10 s until it is confirmed, expired,
// failed or cancelled. The status comes only from Zerveil's view-only scanner; a transaction id is never proof.

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api";
import { Logo } from "../components/Logo";
import { PricingNotice, PrivacyNote, ShieldedCheckout } from "../components/Payments";
import { Card, ErrorText, Notice, Spinner } from "../components/ui";
import { PAID_INVOICE, PURPOSE_LABEL, confirmationProgress, formatAsset, requestValue, when } from "../format";
import { useSession } from "../session";
import type { PaymentRequest } from "../types";

const FINAL = new Set(["confirmed", "overpaid", "expired", "failed", "cancelled"]);

export function PublicPay() {
  const { id = "" } = useParams();
  const { status } = useSession();
  const [invoice, setInvoice] = useState<PaymentRequest | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setInvoice(await api.publicInvoice(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const done = invoice ? FINAL.has(invoice.status) : false;
  useEffect(() => {
    if (done) return;
    const t = setInterval(() => void load(), 10_000);
    return () => clearInterval(t);
  }, [done, load]);

  const testnet = (status?.zcash.network ?? "testnet") !== "mainnet";
  const notice = status?.zcash.testnet_notice;

  return (
    <div className="paypage">
      <header className="paypage-head">
        <Link to="/" aria-label="Zerveil home">
          <Logo size={30} />
        </Link>
        <div className="row">
          {notice && <span className="testnet-chip">{notice}</span>}
          {status?.payments_simulated && <span className="testnet-chip warn">Simulated — no real payments</span>}
        </div>
      </header>

      <main className="paypage-main">
        {notice && (
          <div className="testnet-banner" role="note">
            {notice}
          </div>
        )}
        <PricingNotice />
        {error && !invoice ? (
          <Card title="Payment not found">
            <ErrorText error={error} />
            <p className="muted small">Check the link with whoever sent it to you.</p>
          </Card>
        ) : !invoice ? (
          <Spinner />
        ) : (
          <div className="stack">
            <Card>
              <p className="muted small paypage-eyebrow">
                {PURPOSE_LABEL[invoice.purpose] ?? invoice.purpose}
                {invoice.payee_name && <> · to {invoice.payee_name}</>}
              </p>
              <h1>{invoice.title || "Shielded payment"}</h1>
              {invoice.description && <p>{invoice.description}</p>}
              <p className="muted small">
                Paid directly to {invoice.payee_name ? `${invoice.payee_name}'s` : "the recipient's"} own wallet. Zerveil never holds this money; it only
                confirms the payment arrived.
              </p>
            </Card>

            {PAID_INVOICE.has(invoice.status) && (
              <Notice tone="good">
                Confirmed{invoice.paid_at ? ` on ${when(invoice.paid_at)}` : ""}: {requestValue(invoice)} arrived in the recipient's wallet with{" "}
                {invoice.confirmations} confirmations.
                {invoice.status === "overpaid" && " More than the requested amount arrived; settle the difference with the recipient."} Thank you.
                {invoice.simulated && " (Simulated — no real payment.)"}
              </Notice>
            )}
            {(invoice.status === "seen" || invoice.status === "mined" || invoice.status === "confirming") && (
              <Notice tone="info">
                {invoice.status === "seen" ? "Payment seen, not yet mined." : "Payment mined."} {confirmationProgress(invoice)}. No need to send again.
              </Notice>
            )}
            {invoice.status === "underpaid" && (
              <Notice tone="warn">
                Only {formatAsset(invoice.received, invoice.asset || invoice.ticker)} of {formatAsset(invoice.amount, invoice.asset || invoice.ticker, 8)} arrived. Send the rest with the same memo before the request expires.
              </Notice>
            )}
            {invoice.status === "expired" && <Notice tone="warn">This payment request expired. Ask for a new link; do not send to this address.</Notice>}
            {invoice.status === "failed" && (
              <Notice tone="bad">The payment did not confirm (for example, the transaction expired unmined). Ask the recipient for a new request.</Notice>
            )}
            {invoice.status === "cancelled" && <Notice tone="warn">This payment request was cancelled. Do not send to this address.</Notice>}

            <Card>
              <ShieldedCheckout invoice={invoice} onChange={load} showPayLink={false} />
              {!done && <p className="muted small">This page refreshes every 10 seconds.</p>}
              {error && <ErrorText error={error} />}
            </Card>

            <Card title="How to pay">
              <ol className="small paypage-steps">
                <li>Open a Zcash wallet that supports shielded Unified Addresses ({testnet ? "on testnet" : "mainnet"}).</li>
                <li>
                  Use "Open in wallet", or copy the address, the exact amount (under Technical payment details) and the memo. Your wallet sends shielded testnet
                  Zcash, converted from the USD amount once, at a test rate.
                </li>
                <li>
                  Send. The status moves from Payment seen to Mined and Confirming, then Confirmed after {invoice.required_confirmations} confirmations.
                </li>
              </ol>
            </Card>

            <Card title="Privacy">
              <p className="small">
                Shielded: amount, sender and memo are encrypted on-chain. The address belongs to the recipient's own wallet; Zerveil sees the payment only
                through a viewing key the recipient connected, which cannot spend.
              </p>
              <PrivacyNote />
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}
