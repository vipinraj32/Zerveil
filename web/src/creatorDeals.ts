// How a creator reads their deals: the backend's DealState grouped into the stages they act on. Every state belongs to
// exactly one group, so the filter counts add up to the total. Nothing here decides anything; it only labels states.

import type { Deal, DealState, InfluencerDeal, Offer } from "./types";

export type DealGroup = "pending" | "negotiating" | "accepted" | "active" | "completed" | "rejected";

export const DEAL_GROUPS: { key: DealGroup; label: string; hint: string; states: DealState[] }[] = [
  { key: "negotiating", label: "Negotiating", hint: "Your agent and the brand's agent are exchanging offers", states: ["NEGOTIATING"] },
  {
    key: "pending",
    label: "Pending",
    hint: "Terms agreed or paused: waiting for the brand to accept and pay the fixed fee to your wallet",
    states: ["ESCALATED", "AGREED", "AWAITING_APPROVAL", "BLOCKED", "PAYMENT_REQUESTED", "PAYMENT_PENDING"],
  },
  { key: "accepted", label: "Accepted", hint: "Fixed fee confirmed in your wallet: post your video", states: ["CONTENT_CREATION", "PROOF_REJECTED"] },
  {
    key: "active",
    label: "Active",
    hint: "Video submitted: being verified; the performance fee is requested at day 7",
    states: ["PROOF_SUBMITTED", "VERIFIED", "PERFORMANCE_DUE", "DISPUTED"],
  },
  { key: "completed", label: "Completed", hint: "Every milestone settled", states: ["COMPLETED"] },
  {
    key: "rejected",
    label: "Rejected",
    hint: "No deal, the payment request expired, the deadline passed, or archived",
    states: ["REJECTED", "PAYMENT_EXPIRED", "UNDELIVERED", "ARCHIVED"],
  },
];

const GROUP_OF = new Map<DealState, DealGroup>(DEAL_GROUPS.flatMap((g) => g.states.map((s) => [s, g.key] as const)));

export const dealGroup = (state: DealState): DealGroup => GROUP_OF.get(state) ?? "pending";

/** States where the creator has to do something: post (or re-post) the video. */
export const NEEDS_PROOF = new Set<DealState>(["CONTENT_CREATION", "PROOF_REJECTED"]);
/** States where a payment to the creator's wallet has been requested and is not yet confirmed. */
export const PAYMENT_OPEN = new Set<DealState>(["PAYMENT_REQUESTED", "PAYMENT_PENDING"]);

/** The last offer on the table: the counter in the latest reply if there was one, else the latest offer. */
export function latestOffer(d: InfluencerDeal): Offer | null {
  const last = d.rounds.at(-1);
  if (!last) return null;
  return last.reply.counter ?? last.offer;
}

/** What this deal pays: the signed terms once agreed, otherwise the latest proposal. */
export function dealPay(d: InfluencerDeal): { fixed_fee: string; cpm: string; cap: string; model: string; final: boolean } | null {
  if (d.terms) return { fixed_fee: d.terms.fixed_fee, cpm: d.terms.cpm, cap: d.terms.cap, model: d.terms.compensation_model, final: true };
  const o = latestOffer(d);
  return o ? { fixed_fee: o.fixed_fee, cpm: o.cpm, cap: o.cap, model: o.compensation_model, final: false } : null;
}

/** What the scanner confirmed in the creator's wallet for this deal (confirmed milestones), plus legacy history of archived deals. */
export const receivedFor = (d: Deal) =>
  d.milestones.filter((m) => m.status === "confirmed").reduce((s, m) => s + Number(m.amount), 0) +
  d.payouts.filter((p) => p.kind !== "refund").reduce((s, p) => s + Number(p.amount), 0);

/** Milestones requested from the advertiser and not yet confirmed. */
export const awaitingFor = (d: Deal) => d.milestones.filter((m) => m.status === "requested" || m.status === "pending").reduce((s, m) => s + Number(m.amount), 0);

/** Search text: only what the creator is shown (brand, handle, niche, brief, format). */
export const dealSearchText = (d: InfluencerDeal) =>
  [d.campaign.advertiser, d.campaign.brand_handle, d.campaign.niche, d.campaign.brief, d.campaign.deliverable].join(" ").toLowerCase();
