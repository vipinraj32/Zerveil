// The creator's receiving account: a viewing key (UFVK) of their own dedicated wallet account, so Zerveil's view-only
// scanner can confirm payments advertisers send there. A viewing key cannot spend. Seed phrases and spending keys are
// refused by the backend (400); this form also warns before sending anything that looks like one.

import { useEffect, useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import type { ReceivingAccountView } from "../types";
import { SimulatedChip } from "./Payments";
import { ActionButton, ErrorText, Notice } from "./ui";
import { when } from "../format";

/** A 12–24 word phrase: almost certainly a seed phrase, never sent. */
const looksLikeSeed = (s: string) => {
  const words = s.trim().split(/\s+/);
  return words.length >= 12 && words.every((w) => /^[a-z]+$/.test(w));
};
/** Spending-key prefixes (Sapling extended spending keys); the backend refuses them too. */
const looksLikeSpendingKey = (s: string) => /^secret-extended-key|^uskey|^usk/i.test(s.trim());

export function ReceivingAccountPanel({ onChange }: { onChange?: (view: ReceivingAccountView) => void }) {
  const { status, refresh } = useSession();
  const [view, setView] = useState<ReceivingAccountView | null>(null);
  const [ufvk, setUfvk] = useState("");
  const [birthday, setBirthday] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.receivingAccount().then(setView, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const testnet = (status?.zcash.network ?? "testnet") !== "mainnet";
  const prefix = testnet ? "uviewtest1" : "uview1";
  const key = ufvk.trim();
  const localProblem = !key
    ? null
    : looksLikeSeed(key)
      ? "This looks like a seed phrase. Never paste your seed phrase anywhere — only a viewing key."
      : looksLikeSpendingKey(key)
        ? "This looks like a spending key. Never paste a spending key — only a viewing key."
        : !key.startsWith(prefix)
          ? `A ${testnet ? "testnet " : ""}Unified Full Viewing Key starts with ${prefix}…`
          : null;
  const height = Number(birthday);
  const validHeight = birthday.trim() !== "" && Number.isInteger(height) && height >= 0;

  const done = (v: ReceivingAccountView) => {
    setView(v);
    onChange?.(v);
    void refresh();
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      done(await api.connectReceivingAccount({ ufvk: key, birthday: height, consent }));
      setUfvk("");
      setBirthday("");
      setConsent(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!view) return error ? <ErrorText error={error} /> : <p className="muted small">Loading…</p>;

  return (
    <div className="stack receiving-account">
      <ul className="small receiving-explainer">
        <li>Payments go directly to your own wallet. Zerveil never holds, forwards or returns your money.</li>
        <li>A viewing key lets Zerveil see this account's transactions to confirm payments; it cannot spend.</li>
        <li>Use a dedicated receiving account, so the viewing key reveals nothing else of your wallet.</li>
      </ul>

      {view.connected && view.account ? (
        <>
          <dl className="kv">
            <dt>Status</dt>
            <dd>
              <span className="badge tone-good">Connected</span> <SimulatedChip simulated={view.account.simulated} />
            </dd>
            <dt>Key fingerprint</dt>
            <dd className="mono">{view.account.key_fingerprint}</dd>
            <dt>Birthday height</dt>
            <dd>{view.account.birthday.toLocaleString("en-US")}</dd>
            <dt>Network</dt>
            <dd>{view.account.network}</dd>
            <dt>Scanner</dt>
            <dd>{view.account.backend}</dd>
            <dt>Authorized</dt>
            <dd>{when(view.account.authorized_at)}</dd>
          </dl>
          <div className="row">
            <ActionButton
              variant="danger"
              onClick={async () => {
                if (!window.confirm("Disconnect this viewing key? You will not be listed for new deals, and open payment requests cannot be confirmed, until you connect one again."))
                  return;
                done(await api.disconnectReceivingAccount());
              }}
            >
              Disconnect
            </ActionButton>
          </div>
        </>
      ) : (
        <form
          className="form"
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy) void submit();
          }}
        >
          <Notice tone="warn">
            <strong>Never paste your seed phrase or spending key — only a viewing key.</strong>
          </Notice>
          <label>
            Unified Full Viewing Key
            <textarea
              rows={4}
              value={ufvk}
              onChange={(e) => setUfvk(e.target.value)}
              placeholder={`${prefix}…`}
              spellCheck={false}
              autoComplete="off"
              className="mono"
            />
            <small>Export it from your wallet for the receiving account (in Zallet or zcash-devtool, the account's UFVK).</small>
          </label>
          {localProblem && <p className="error small">{localProblem}</p>}
          <label>
            Birthday height
            <input
              type="number"
              min={0}
              step={1}
              inputMode="numeric"
              value={birthday}
              onChange={(e) => setBirthday(e.target.value)}
              placeholder="e.g. 3100000"
            />
            <small>The block height the account was created at; the scanner starts there.</small>
          </label>
          <blockquote className="authorization small">{view.authorization}</blockquote>
          <label className="check">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>I agree to the statement above.</span>
          </label>
          <ErrorText error={error} />
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={busy || !key || !!localProblem || !validHeight || !consent}>
              {busy ? "Connecting…" : "Connect viewing key"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
