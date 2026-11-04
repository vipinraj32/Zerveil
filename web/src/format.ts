import type { DealState, InvoiceStatus, MilestoneStatus, ZcashInfo } from "./types";

/** The payment asset (TAZ on testnet), set once /api/status loads. Only what is paid — never the business currency. */
let PAYMENT_ASSET = "TAZ";
export const setPaymentAsset = (a: string | null | undefined) => {
  if (a) PAYMENT_ASSET = a;
};
export const paymentAsset = () => PAYMENT_ASSET;

/** Plain number without a unit: up to 8 decimals, trailing zeros trimmed, at least `minDecimals`. */
export function amount(v: string | number | null | undefined, minDecimals = 2): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { minimumFractionDigits: minDecimals, maximumFractionDigits: 8 });
}

// ---- money: USD is the business currency; TAZ (shielded Zcash, testnet) is only the payment asset ----------------

/** Splits a decimal string ("-12.345") into sign, integer digits and fraction digits, or null when not a number. */
function splitDecimal(v: string | number | null | undefined): { neg: boolean; int: string; frac: string } | null {
  if (v === null || v === undefined || v === "") return null;
  const s = typeof v === "number" ? (Number.isFinite(v) ? v.toFixed(8) : "") : String(v).trim();
  const m = /^([+-])?(\d*)(?:\.(\d*))?$/.exec(s);
  if (!m || (!m[2] && !m[3])) return null;
  return { neg: m[1] === "-", int: (m[2] || "0").replace(/^0+(?=\d)/, ""), frac: m[3] ?? "" };
}

/** A decimal amount as integer cents (rounded half up), or null when it is not a number. No float arithmetic. */
export function toCents(v: string | number | null | undefined): bigint | null {
  const d = splitDecimal(v);
  if (!d) return null;
  const frac = d.frac.padEnd(3, "0");
  let cents = BigInt(d.int) * 100n + BigInt(frac.slice(0, 2));
  if (Number(frac[2]) >= 5) cents += 1n;
  return d.neg ? -cents : cents;
}

/** Integer cents → "1234.50". */
export function centsToString(c: bigint): string {
  const neg = c < 0n;
  const abs = neg ? -c : c;
  return `${neg ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

/** Sum of USD decimal strings, in integer cents (no float accumulation). Returns a 2-decimal string. */
export function sumUsd(values: Iterable<string | number | null | undefined>): string {
  let total = 0n;
  for (const v of values) total += toCents(v) ?? 0n;
  return centsToString(total);
}

/** Sum of asset decimal strings (up to 8 decimals), in integer zatoshis. Returns an 8-decimal string. */
export function sumAsset(values: Iterable<string | number | null | undefined>): string {
  let total = 0n;
  for (const v of values) {
    const d = splitDecimal(v);
    if (!d) continue;
    const z = BigInt(d.int) * 100_000_000n + BigInt(d.frac.slice(0, 8).padEnd(8, "0"));
    total += d.neg ? -z : z;
  }
  const neg = total < 0n;
  const abs = neg ? -total : total;
  return `${neg ? "-" : ""}${abs / 100_000_000n}.${String(abs % 100_000_000n).padStart(8, "0")}`;
}

const groupThousands = (int: string) => int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** The one USD formatter for the whole app: "$40.00", "$1,250.00", "−$3.10". Exact: works on decimal strings in integer
 * cents, never float arithmetic. `short` is accepted for older call sites and changes nothing. */
export function formatUsd(v: string | number | null | undefined, _short = true): string {
  const c = toCents(v);
  if (c === null) return v === null || v === undefined || v === "" ? "—" : String(v);
  const [int, frac] = centsToString(c < 0n ? -c : c).split(".");
  return `${c < 0n ? "−" : ""}$${groupThousands(int)}.${frac}`;
}

/** Alias names for the same formatter. */
export const formatUSD = (v: string | number | null | undefined) => formatUsd(v);
export const usd = formatUSD;

/** A payment-asset amount: "0.2 TAZ" (trailing zeros trimmed, at least `minDecimals`; 8 = the exact zatoshi amount). */
export function formatAsset(v: string | number | null | undefined, asset: string | null | undefined = PAYMENT_ASSET, minDecimals = 2): string {
  const d = splitDecimal(v);
  if (!d) return v === null || v === undefined || v === "" ? "—" : String(v);
  let frac = d.frac.replace(/0+$/, "");
  if (frac.length < minDecimals) frac = frac.padEnd(minDecimals, "0");
  const num = `${d.neg ? "−" : ""}${groupThousands(d.int)}${frac ? `.${frac}` : ""}`;
  return `${num} ${asset || PAYMENT_ASSET}`;
}

/** True when an amount is in USD. Anything else ("TAZ", "", null) was priced in TAZ before USD pricing. */
export const isUsd = (currency: string | null | undefined) => currency === "USD";

/** The unit legacy (pre-USD) amounts were priced in. Use with money()/formatAsset() for legacy labels only. */
export const LEGACY_ASSET = "TAZ";

/** Matches an amount in legacy units inside an agent transcript line ("19 TAZ fixed …"), for transcripts from before USD pricing. */
export const LEGACY_AMOUNT_RE = /\d (TAZ|ZEC)\b/;

export const LEGACY_PRICING_NOTE = "Legacy — priced in TAZ before USD pricing";

/** The currency of a deal's terms and milestones: "USD", or "TAZ" for terms signed before USD pricing (currency missing). */
export const termsCurrency = (terms: { currency?: string | null } | null | undefined): string => terms?.currency || (terms ? "TAZ" : "USD");

/** A campaign/deal amount in its currency: "$40.00", or "40.00 TAZ (legacy test units)" for
 * amounts from before USD pricing — never with a $ sign. */
export function money(v: string | number | null | undefined, currency: string | null | undefined = "USD", short = false): string {
  if (isUsd(currency)) return formatUsd(v, short);
  const a = formatAsset(v, currency || "TAZ");
  return a === "—" ? a : `${a} (${short ? "legacy" : "legacy test units"})`;
}

/** The value a payment request asks for: USD when priced in USD, otherwise (legacy) the asset amount. */
export function requestValue(r: { usd_amount?: string | null; amount: string; asset?: string | null; ticker?: string }, short = false): string {
  if (r.usd_amount !== null && r.usd_amount !== undefined) return formatUsd(r.usd_amount, short);
  return `${formatAsset(r.amount, r.asset || r.ticker)} (legacy)`;
}

/** "Prices in USD · paid in shielded Zcash (testnet) at a test rate · no stablecoin" */
export const PRICING_LINE = () => "Prices in USD · paid in shielded Zcash (testnet) at a test rate · no stablecoin";

/** Explorer link for a txid, or null (no explorer configured, or a simulated txid). */
export function txUrl(zcash: ZcashInfo | undefined | null, txid: string | null | undefined, simulated = false): string | null {
  if (!zcash || !txid || simulated || txid.startsWith("sim") || !zcash.explorer_tx_url) return null;
  const t = zcash.explorer_tx_url;
  return t.includes("{txid}") ? t.replace("{txid}", encodeURIComponent(txid)) : `${t}${encodeURIComponent(txid)}`;
}

export const INVOICE_STATUS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  awaiting_payment: { label: "Awaiting payment", tone: "warn" },
  seen: { label: "Payment seen (unmined)", tone: "info" },
  mined: { label: "Mined", tone: "info" },
  confirming: { label: "Confirming", tone: "info" },
  confirmed: { label: "Confirmed", tone: "good" },
  underpaid: { label: "Underpaid", tone: "bad" },
  overpaid: { label: "Confirmed (overpaid)", tone: "good" },
  expired: { label: "Expired", tone: "neutral" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** Invoice statuses the scanner still watches (payable). */
export const OPEN_INVOICE = new Set<InvoiceStatus>(["awaiting_payment", "seen", "mined", "confirming", "underpaid"]);
/** Paid in full with enough confirmations. */
export const PAID_INVOICE = new Set<InvoiceStatus>(["confirmed", "overpaid"]);
export const isPaid = (status: InvoiceStatus) => PAID_INVOICE.has(status);

/** "2 / 3 confirmations"; "" before the payment is seen. */
export function confirmationProgress(inv: { status: InvoiceStatus; confirmations: number; required_confirmations: number }): string {
  if (inv.status === "awaiting_payment" || inv.status === "cancelled" || (inv.status === "expired" && inv.confirmations === 0)) return "";
  const n = Math.min(inv.confirmations, inv.required_confirmations);
  return `${PAID_INVOICE.has(inv.status) ? inv.confirmations : n} / ${inv.required_confirmations} confirmations`;
}

export const MILESTONE_STATUS: Record<MilestoneStatus, { label: string; tone: Tone }> = {
  scheduled: { label: "Scheduled", tone: "neutral" },
  requested: { label: "Requested", tone: "warn" },
  pending: { label: "Payment seen, confirming", tone: "info" },
  confirmed: { label: "Confirmed in creator's wallet", tone: "good" },
  expired: { label: "Expired unpaid", tone: "bad" },
  waived: { label: "Not due", tone: "neutral" },
};

export const MILESTONE_KIND: Record<string, string> = { fixed: "Fixed fee", performance: "Performance fee" };

export const PURPOSE_LABEL: Record<string, string> = {
  milestone: "Creator milestone",
  api_access: "API access (HTTP 402)",
  credit_topup: "API credit top-up",
  payment_link: "Payment link",
};

/** Kinds of legacy payments shown on archived deals (history from an earlier payment flow). */
export const LEGACY_PAYMENT_KIND: Record<string, string> = { fixed: "Fixed fee", performance: "Performance fee", refund: "Returned to advertiser" };

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
  AWAITING_APPROVAL: { label: "Awaiting approval", tone: "warn", hint: "Accept the agreed deals; the fixed fee is then requested as a direct payment to the creator" },
  BLOCKED: { label: "Blocked by policy", tone: "bad", hint: "Deterministic policy refused the deal" },
  PAYMENT_REQUESTED: { label: "Payment requested", tone: "warn", hint: "The advertiser pays the fixed fee directly to the creator's wallet" },
  PAYMENT_PENDING: { label: "Payment confirming", tone: "info", hint: "The scanner sees the payment in the creator's wallet; waiting for confirmations" },
  PAYMENT_EXPIRED: { label: "Payment expired", tone: "neutral", hint: "The fixed-fee invoice expired unpaid; the deal is closed" },
  CONTENT_CREATION: { label: "Paid — post now", tone: "good", hint: "The fixed fee is confirmed in the creator's wallet; waiting for the video" },
  PROOF_SUBMITTED: { label: "Proof submitted", tone: "info", hint: "Verifier is checking the video" },
  PROOF_REJECTED: { label: "Proof rejected", tone: "bad", hint: "Fix the post and resubmit before the deadline" },
  VERIFIED: { label: "Verified", tone: "good", hint: "The post passed verification (a compliance review may still be pending)" },
  PERFORMANCE_DUE: { label: "Performance due", tone: "info", hint: "Views are measured at day 7; the performance fee is then requested from the advertiser" },
  DISPUTED: { label: "Disputed", tone: "bad", hint: "Under dispute until resolved" },
  COMPLETED: { label: "Completed", tone: "good", hint: "Every milestone settled" },
  UNDELIVERED: { label: "Undelivered", tone: "bad", hint: "The publish deadline passed without an accepted post (the fixed fee was already paid)" },
  ARCHIVED: { label: "Archived", tone: "neutral", hint: "From an earlier payment system; kept for the record" },
};

export const CAMPAIGN_TONE: Record<string, Tone> = {
  planning: "info",
  negotiating: "info",
  awaiting_approval: "warn",
  awaiting_payment: "warn",
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
      scanner: "Payment scanner",
      payments: "Payments",
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

/** "$19.00 fixed + $2.50 CPM, cap $40.00" for any compensation model (legacy TAZ terms are labeled legacy). */
export function termsText(t: { fixed_fee: string; cpm: string; cap: string }, model = "hybrid", currency: string | null | undefined = "USD"): string {
  const m = (v: string) => (isUsd(currency) ? usd(v) : formatAsset(v, currency || "TAZ"));
  const legacy = isUsd(currency) ? "" : " (legacy)";
  if (model === "fixed") return `${m(t.fixed_fee)} fixed${legacy}`;
  if (model === "cpm") return `${m(t.cpm)} CPM, cap ${m(t.cap)}${legacy}`;
  return `${m(t.fixed_fee)} fixed + ${m(t.cpm)} CPM, cap ${m(t.cap)}${legacy}`;
}

export const SOURCE_LABEL: Record<string, string> = {
  youtube_data_api: "YouTube API · verified",
  self_reported: "Self-reported",
  derived_estimate: "Estimate",
  unavailable: "Unknown",
};

/** What the advertiser calls a campaign; older campaigns without a name read as "Brand · niche". */
export const campaignName = (spec: { name?: string; advertiser: string; niche: string }) => spec.name?.trim() || `${spec.advertiser} · ${spec.niche}`;
