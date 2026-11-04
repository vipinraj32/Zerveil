import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useMe, useSession } from "../session";
import { ErrorText } from "./ui";

/** Save the user's shielded Unified Address: the creator's payout address or the advertiser's refund address.
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
          ? "Your payouts are sent to this shielded address. Saving it also authorizes your creator agent's signing key to sign deal terms for you."
          : "Unused campaign funds are refunded to this shielded address. You need one before you can create a campaign."}
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
        Privacy: your address is never listed in the directory or shown to the other party. Only you and the Zerveil operator, who sends payouts and
        refunds from the escrow wallet, can see it.
      </p>
    </form>
  );
}
