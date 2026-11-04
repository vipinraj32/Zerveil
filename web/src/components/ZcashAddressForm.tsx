import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useMe, useSession } from "../session";
import { ErrorText } from "./ui";

/** Save the user's own shielded Unified Address (from their own wallet). Creators name it as payee in signed terms.
 * The backend validates it (UA on the right network only: no transparent t-addresses, no Sapling-only addresses). */
export function ZcashAddressForm({ onSaved, submitLabel }: { onSaved?: () => void; submitLabel?: string }) {
  const { user } = useMe();
  const { setMe, status } = useSession();
  const [address, setAddress] = useState(user.zcash_address);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const creator = user.role === "influencer";
  const network = status?.zcash.network ?? "testnet";
  const prefix = network === "mainnet" ? "u1" : "utest1";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    setBusy(true);
    try {
      setMe(await api.saveZcashAddress(address.trim()));
      setSaved(true);
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={submit}>
      <p className="small">
        {creator
          ? "Your own wallet's shielded address, named as payee in the terms your agent signs. Payments go directly to your wallet; saving it also authorizes your creator agent's signing key to sign deal terms for you."
          : "Your own wallet's shielded address (optional). You pay creators directly from your own wallet; Zerveil never holds funds."}
      </p>
      <label>
        Shielded Unified Address ({network})
        <input
          className="mono"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder={`${prefix}…`}
          spellCheck={false}
          autoComplete="off"
          required
        />
        <small>
          A Unified Address starting with <code>{prefix}</code>. Transparent (t-) and Sapling-only addresses are not accepted.
        </small>
      </label>
      <div className="row">
        <button className="btn btn-primary" disabled={busy || !address.trim() || address.trim() === user.zcash_address}>
          {busy ? "Saving…" : (submitLabel ?? "Save address")}
        </button>
        {saved && <span className="chip good">Saved</span>}
      </div>
      <ErrorText error={error} />
      <p className="muted small">
        Privacy: your address is never listed in the directory or shown to the other party. Only you and the Zerveil operator can see it. Zerveil
        never holds or sends funds.
      </p>
    </form>
  );
}
