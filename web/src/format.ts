import type { DealState, InvoiceStatus, PayoutStatus, ZcashInfo } from "./types";

/** The network's ticker (TAZ on testnet, ZEC on mainnet), set once /api/status loads. */
let TICKER = "TAZ";
export const setTicker = (t: string | null | undefined) => {
  if (t) TICKER = t;
};
export const ticker = () => TICKER;

/** Plain amount without the ticker: up to 8 decimals, trailing zeros trimmed, at least `minDecimals`. */
export function amount(v: string | number | null | undefined, minDecimals = 2): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { minimumFractionDigits: minDecimals, maximumFractionDigits: 8 });
}

/** "12.5 TAZ". Amounts are decimal strings with up to 8 decimals. */
export const zec = (v: string | number | null | undefined, minDecimals = 2) => {
  const a = amount(v, minDecimals);
  return a === "—" ? a : `${a} ${TICKER}`;
};

/** Explorer link for a txid, or null (no explorer configured, or a simulated txid). */
export function txUrl(zcash: ZcashInfo | undefined | null, txid: string | null | undefined, simulated = false): string | null {
  if (!zcash || !txid || simulated || txid.startsWith("sim") || !zcash.explorer_tx_url) return null;
  const t = zcash.explorer_tx_url;
  return t.includes("{txid}") ? t.replace("{txid}", encodeURIComponent(txid)) : `${t}${encodeURIComponent(txid)}`;
}

export const INVOICE_STATUS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  pending: { label: "Waiting for payment", tone: "warn" },
  underpaid: { label: "Underpaid", tone: "bad" },
  confirming: { label: "Confirming", tone: "info" },
  paid: { label: "Paid", tone: "good" },
  expired: { label: "Expired", tone: "neutral" },
  late_payment: { label: "Paid late", tone: "warn" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const PAYOUT_STATUS: Record<PayoutStatus, { label: string; tone: Tone }> = {
  pending_approval: { label: "Waiting for operator approval", tone: "warn" },
  approved: { label: "Approved, queued", tone: "info" },
  sending: { label: "Sending", tone: "info" },
  submitted: { label: "Submitted", tone: "info" },
  sent: { label: "Sent", tone: "good" },
  confirmed: { label: "Confirmed", tone: "good" },
  failed: { label: "Failed", tone: "bad" },
  rejected: { label: "Rejected by operator", tone: "bad" },
  unknown: { label: "Outcome unknown, being reconciled", tone: "warn" },
};

export const PURPOSE_LABEL: Record<string, string> = {
  campaign_funding: "Campaign funding",
  api_access: "API access (HTTP 402)",
  credit_topup: "Agent credit top-up",
  payment_link: "Payment link",
};

export const PAYOUT_KIND: Record<string, string> = { fixed: "Fixed fee", performance: "Performance fee", refund: "Refund", link_forward: "Payment link" };

export const num = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-US"));

export const short = (addr?: string | null, n = 6) => (!addr ? "—" : addr.length <= 2 * n + 2 ? addr : `${addr.slice(0, n)}…${addr.slice(-4)}`);

export function when(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export type Tone = "neutral" | "info" | "warn" | "good" | "bad";

export const STATE_INFO: Record<DealState, { label: string; tone: Tone; hint: string }> = {
  NEGOTIATING: { label: "Negotiating", tone: "info", hint: "Agents are exchanging offers over A2A" },
  REJECTED: { label: "Rejected", tone: "neutral", hint: "No agreement, or not selected" },
  ESCALATED: { label: "Needs a human", tone: "warn", hint: "An agent's policy handed this decision to a person; nothing was agreed or reserved" },
  AGREED: { label: "Agreed", tone: "info", hint: "Influencer agent signed the terms" },
  AWAITING_APPROVAL: { label: "Awaiting approval", tone: "warn", hint: "Approve the agreed deals to get a shielded funding invoice" },
  BLOCKED: { label: "Blocked by policy", tone: "bad", hint: "Deterministic policy refused the deal" },
  AWAITING_FUNDING: { label: "Awaiting funding", tone: "warn", hint: "Pay the shielded funding invoice into the escrow wallet" },
  FUNDING: { label: "Payment seen", tone: "info", hint: "Shielded payment detected; waiting for confirmations" },
  FUNDED: { label: "Funded — post now", tone: "good", hint: "Confirmed in the escrow wallet; waiting for the video" },
  PAYMENT_EXPIRED: { label: "Invoice expired", tone: "neutral", hint: "The funding invoice expired before it was paid; nothing is held" },
  PROOF_SUBMITTED: { label: "Proof submitted", tone: "info", hint: "Verifier is checking the video" },
  PROOF_REJECTED: { label: "Proof rejected", tone: "bad", hint: "Fix the post and resubmit before the deadline" },
  VERIFIED: { label: "Verified", tone: "good", hint: "Fixed fee releases now, or after your review if required" },
  FIXED_RELEASED: { label: "Fixed fee paid", tone: "good", hint: "Performance fee pays out at day 7" },
  DISPUTED: { label: "Disputed", tone: "bad", hint: "Funds frozen until resolved" },
  COMPLETED: { label: "Completed", tone: "good", hint: "All payouts done, remainder refunded" },
  EXPIRED_REFUNDED: { label: "Expired · refunded", tone: "neutral", hint: "No valid proof by the deadline" },
  ARCHIVED: { label: "Archived", tone: "neutral", hint: "From an earlier payment system; kept for the record" },
};

export const CAMPAIGN_TONE: Record<string, Tone> = {
  planning: "info",
  negotiating: "info",
  awaiting_approval: "warn",
  funding: "info",
  active: "good",
  done: "neutral",
  failed: "bad",
};

export const ACTOR_LABEL = (actor: string) => {
  if (actor.startsWith("influencer-agent")) return "Influencer agent";
  return (
    {
      "advertiser-agent": "Advertiser agent",
      policy: "Policy engine",
      wallet: "Wallet",
      escrow: "Escrow",
      verifier: "Verifier",
      human: "Human",
    }[actor] ?? actor
  );
};

/** 75 → "1:15" */
export function clock(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function videoLength(min: number, max: number): string {
  if (!max) return "Any length";
  return min ? `${clock(min)} – ${clock(max)}` : `Up to ${clock(max)}`;
}

export const DELIVERABLE_LABEL: Record<string, string> = { youtube_short: "YouTube Short", youtube_video: "YouTube video" };

const WORDS_PER_PAGE = 500;

export function docSize(text: string): { words: number; pages: number } {
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  return { words, pages: words / WORDS_PER_PAGE };
}

export const MODEL_LABEL: Record<string, string> = { fixed: "Fixed fee", cpm: "CPM only", hybrid: "Fixed + CPM" };

/** "19 TAZ fixed + 2.5 TAZ CPM, cap 40 TAZ" for any compensation model. */
export function termsText(t: { fixed_fee: string; cpm: string; cap: string }, model = "hybrid"): string {
  if (model === "fixed") return `${zec(t.fixed_fee)} fixed`;
  if (model === "cpm") return `${zec(t.cpm)} CPM, cap ${zec(t.cap)}`;
  return `${zec(t.fixed_fee)} fixed + ${zec(t.cpm)} CPM, cap ${zec(t.cap)}`;
}

export const SOURCE_LABEL: Record<string, string> = {
  youtube_data_api: "YouTube API · verified",
  self_reported: "Self-reported",
  derived_estimate: "Estimate",
  unavailable: "Unknown",
};

/** What the advertiser calls a campaign; older campaigns without a name read as "Brand · niche". */
export const campaignName = (spec: { name?: string; advertiser: string; niche: string }) => spec.name?.trim() || `${spec.advertiser} · ${spec.niche}`;
