// Public payment page (/pay/:id): anyone with the link can pay a shielded Zcash invoice. No sign-in required.
// It polls the public invoice every 10 s until it is paid, expired or cancelled.

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api";
import { Logo } from "../components/Logo";
import { PrivacyNote, ShieldedCheckout } from "../components/Payments";
import { Card, ErrorText, Notice, Spinner } from "../components/ui";
import { PURPOSE_LABEL, when, zec } from "../format";
import { useSession } from "../session";
import type { PaymentRequest } from "../types";

const FINAL = new Set(["paid", "expired", "cancelled", "late_payment"]);

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

  return (
    <div className="paypage">
      <header className="paypage-head">
        <Link to="/" aria-label="Zerveil home">
          <Logo size={30} />
        </Link>
        <div className="row">
          {testnet && <span className="testnet-chip">Zcash {status?.zcash.network ?? "testnet"} · {status?.zcash.ticker ?? "TAZ"} has no value</span>}
          {status?.payments_simulated && <span className="testnet-chip warn">Simulated payments: no ZEC moves</span>}
        </div>
      </header>

      <main className="paypage-main">
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
            </Card>

            {invoice.status === "paid" && (
              <Notice tone="good">
                Paid{invoice.paid_at ? ` on ${when(invoice.paid_at)}` : ""}: {zec(invoice.received || invoice.amount)} received with {invoice.confirmations}{" "}
                confirmations. Thank you.
              </Notice>
            )}
            {invoice.status === "expired" && <Notice tone="warn">This payment request expired. Ask for a new link; do not send to this address.</Notice>}
            {invoice.status === "cancelled" && <Notice tone="warn">This payment request was cancelled. Do not send to this address.</Notice>}
            {invoice.status === "late_payment" && <Notice tone="warn">A payment arrived after expiry. The recipient will be in touch about it.</Notice>}

            <Card>
              <ShieldedCheckout invoice={invoice} onChange={load} showPayLink={false} />
              {!done && <p className="muted small">This page refreshes every 10 seconds.</p>}
              {error && <ErrorText error={error} />}
            </Card>

            <Card title="How to pay">
              <ol className="small paypage-steps">
                <li>Open a Zcash wallet that supports shielded Unified Addresses ({testnet ? "on testnet" : "mainnet"}).</li>
                <li>Use "Open in wallet", or copy the address, the exact amount and the memo.</li>
                <li>Send. The status changes to Confirming when the payment is seen, then Paid after enough confirmations.</li>
              </ol>
            </Card>

            <Card title="Privacy">
              <p className="small">
                Shielded: amount, sender and memo are encrypted on-chain; Zerveil sees the payment only because it holds the escrow wallet's viewing key.
              </p>
              <PrivacyNote />
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}
