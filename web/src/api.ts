import type { AgentLesson, AgentPerformance, FeedbackInput, NegotiationEvent, NegotiationFeedback, NegotiationProgress, NegotiationSession, AgentPolicy, AuditEvent, Campaign, CampaignDetail, CampaignInput, ComplianceView, ContentCheck, CreatorProfileView, Deal, DecisionRecord, DirectoryEntry, DisclosurePolicy, EvidenceItem, InfluencerDeal, Me, NegotiationView, NicheAnalysis, OwnerInvoice, PaymentRequest, PaymentRequired, PayoutRequest, PlatformStatus, PolicyResponse, PremiumSearchResult, SpendingPolicy, CreditSummary, AgentPremiumSearch, Custody, PaymentEvent, WalletStatus } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: unknown = null,
  ) {
    super(message);
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && "message" in d) return String((d as { message: unknown }).message);
    if (Array.isArray(d)) return d.map((e) => (e && typeof e === "object" && "msg" in e ? String(e.msg) : String(e))).join("; ");
  }
  return fallback;
}

async function request<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? headers : { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text; // proxy or server error page
  }
  if (!res.ok) throw new ApiError(res.status, detail(parsed, `${res.status} ${res.statusText}`), parsed);
  return parsed as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const put = <T>(path: string, body: unknown) => request<T>("PUT", path, body);

export const api = {
  status: () => get<PlatformStatus>("/api/status"),
  me: () => get<Me>("/api/me"),
  logout: () => post<{ status: string }>("/auth/logout"),
  loginUrl: (role: "advertiser" | "influencer") => `/auth/google/login?role=${role}`,

  /** Shielded Unified Address: the creator's payout address or the advertiser's refund address. 400 if not a valid UA. */
  saveZcashAddress: (address: string) => put<Me>("/api/me/zcash-address", { address }),
  saveAdvertiser: (company: string, brand_handle: string) => put<Me>("/api/me/advertiser", { company, brand_handle }),
  saveInfluencer: (body: { niches: string[]; fixed_min: string; cpm_min: string; preferred_fixed: string; persona: string; agent_active: boolean }) =>
    put<Me>("/api/me/influencer", body),

  niches: () => get<{ niches: string[]; max_per_creator: number }>("/api/niches"),
  analyzeNiches: () => post<NicheAnalysis>("/api/me/niche-analysis", {}),
  suggestChecks: (requirements: string, brand: string) =>
    post<{ checks: ContentCheck[]; method: "rules" | "llm"; caption_reader: boolean; vision: boolean }>("/api/campaigns/content-checks/suggest", { requirements, brand }),
  directory: () => get<DirectoryEntry[]>("/api/influencers"),

  campaigns: () => get<Campaign[]>("/api/campaigns"),
  campaign: (id: string) => get<CampaignDetail>(`/api/campaigns/${encodeURIComponent(id)}`),
  campaignDeals: (id: string) => get<Deal[]>(`/api/campaigns/${encodeURIComponent(id)}/deals`),
  campaignEvents: (id: string) => get<AuditEvent[]>(`/api/campaigns/${encodeURIComponent(id)}/events`),
  /** The same idempotency key for a retried or double-clicked submit returns the campaign the first request started. */
  createCampaign: (body: CampaignInput, idempotencyKey: string) => request<Campaign>("POST", "/api/campaigns", body, { "Idempotency-Key": idempotencyKey }),
  campaignNegotiations: (id: string) => get<{ progress: NegotiationProgress; sessions: NegotiationSession[] }>(`/api/campaigns/${encodeURIComponent(id)}/negotiations`),
  cancelCampaignNegotiations: (id: string) => post<NegotiationProgress>(`/api/campaigns/${encodeURIComponent(id)}/negotiations/cancel`),
  parseBrief: (brief: string) => post<CampaignInput>("/api/campaigns/parse-brief", { brief }),
  decide: (id: string, approved: boolean) => post<{ status: string }>(`/api/campaigns/${encodeURIComponent(id)}/decision`, { approved }),
  campaignPayouts: (id: string) => get<PayoutRequest[]>(`/api/campaigns/${encodeURIComponent(id)}/payouts`),

  deal: (id: string) => get<Deal>(`/api/deals/${encodeURIComponent(id)}`),
  dealEvents: (id: string) => get<AuditEvent[]>(`/api/deals/${encodeURIComponent(id)}/events`),
  submitProof: (id: string, video_url: string, attestations: Record<string, boolean> = {}) =>
    post<Deal>(`/api/deals/${encodeURIComponent(id)}/proof`, { video_url, attestations }),
  negotiation: (id: string) => get<NegotiationView>(`/api/deals/${encodeURIComponent(id)}/negotiation`),
  decisions: (id: string) => get<DecisionRecord[]>(`/api/deals/${encodeURIComponent(id)}/decisions`),
  review: (id: string, approved: boolean, note: string) => post<Deal>(`/api/deals/${encodeURIComponent(id)}/compliance/review`, { approved, note }),
  compliance: (id: string) => get<ComplianceView>(`/api/campaigns/${encodeURIComponent(id)}/compliance`),

  agentPolicy: () => get<PolicyResponse>("/api/me/agent-policy"),
  saveAgentPolicy: (policy: AgentPolicy, confirm_material_change = false) =>
    put<PolicyResponse>("/api/me/agent-policy", { policy, confirm_material_change }),
  policyVersions: () => get<AgentPolicy[]>("/api/me/agent-policy/versions"),
  myEvidence: () => get<EvidenceItem[]>("/api/me/evidence"),
  creatorEvidence: (id: string) =>
    get<{ evidence: EvidenceItem[]; profile: Record<string, unknown> | null }>(`/api/influencers/${encodeURIComponent(id)}/evidence`),
  creatorProfile: (id: string, videos = 12) => get<CreatorProfileView>(`/api/influencers/${encodeURIComponent(id)}/profile?videos=${videos}`),
  disclosurePolicies: () => get<{ policies: DisclosurePolicy[]; disclaimer: string }>("/api/disclosure-policies"),
  dispute: (id: string, reason: string) => post<Deal>(`/api/deals/${encodeURIComponent(id)}/dispute`, { reason }),

  influencerDeals: () => get<InfluencerDeal[]>("/api/influencer/deals"),
  influencerInbox: () => get<Record<string, unknown>[]>("/api/influencer/inbox"),

  negotiationEvents: (id: string, after = 0) => get<NegotiationEvent[]>(`/api/negotiations/${encodeURIComponent(id)}/events?after=${after}`),
  negotiationStreamUrl: (id: string, after = 0) => `/api/negotiations/${encodeURIComponent(id)}/stream?after=${after}`,
  cancelNegotiation: (id: string) => post<{ status: string }>(`/api/negotiations/${encodeURIComponent(id)}/cancel`),
  myFeedback: (id: string) => get<NegotiationFeedback | null>(`/api/negotiations/${encodeURIComponent(id)}/feedback`),
  sendFeedback: (id: string, body: FeedbackInput) =>
    post<{ feedback: NegotiationFeedback; lesson: AgentLesson | null }>(`/api/negotiations/${encodeURIComponent(id)}/feedback`, body),
  lessons: () => get<AgentLesson[]>("/api/me/agent-lessons"),
  reviewLesson: (id: string, status: "approved" | "dismissed", guidance?: string) =>
    put<AgentLesson>(`/api/me/agent-lessons/${encodeURIComponent(id)}`, { status, guidance }),
  agentPerformance: () => get<AgentPerformance>("/api/me/agent-performance"),

  ask: (question: string) => post<{ answer: string }>("/api/assistant", { question }),
  // ---- shielded payments
  publicInvoice: (id: string) => get<PaymentRequest>(`/api/pay/${encodeURIComponent(id)}`),
  invoices: () => get<OwnerInvoice[]>("/api/payments/invoices"),
  invoice: (id: string) => get<OwnerInvoice>(`/api/payments/invoices/${encodeURIComponent(id)}`),
  cancelInvoice: (id: string) => post<OwnerInvoice>(`/api/payments/invoices/${encodeURIComponent(id)}/cancel`),
  createPaymentLink: (body: { title: string; amount: string; description: string }) => post<OwnerInvoice>("/api/payments/links", body),
  myPayouts: () => get<PayoutRequest[]>("/api/payments/payouts"),
  walletStatus: () => get<WalletStatus>("/api/wallet/status"),
  /** Dev only (simulated payments): pretend a shielded payment arrived. No ZEC moves. */
  simulatePayment: (id: string, body: { amount?: string; confirmations?: number } = {}) =>
    post<PaymentRequest>(`/api/dev/payments/${encodeURIComponent(id)}/simulate`, body),

  spendingPolicy: () => get<SpendingPolicy>("/api/agent/spending-policy"),
  saveSpendingPolicy: (body: SpendingPolicy) => put<SpendingPolicy>("/api/agent/spending-policy", body),
  credit: () => get<CreditSummary>("/api/agent/credit"),
  topUp: (amount: string) => post<OwnerInvoice>("/api/agent/credit/topup", { amount }),
  decideSpend: (id: string, approved: boolean) => post<{ status: string }>(`/api/agent/spend-requests/${encodeURIComponent(id)}`, { approved }),
  agentPremiumSearch: (niche: string) => post<AgentPremiumSearch>("/api/agent/premium-search", { niche }),
  /** HTTP 402 demo: resolves to either the paid result or the payment challenge (never throws on 402). */
  premiumSearch: async (niche: string, paymentId?: string): Promise<{ paid: PremiumSearchResult } | { required: PaymentRequired }> => {
    const res = await fetch(`/api/influencers/premium-search?niche=${encodeURIComponent(niche)}`, {
      credentials: "same-origin",
      headers: paymentId ? { "X-Payment-Id": paymentId } : undefined,
    });
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = text;
    }
    if (res.status === 402) return { required: parsed as PaymentRequired };
    if (!res.ok) throw new ApiError(res.status, detail(parsed, `${res.status} ${res.statusText}`), parsed);
    return { paid: parsed as PremiumSearchResult };
  },

  adminPayouts: (status = "pending_approval,unknown,failed") => get<PayoutRequest[]>(`/api/admin/payouts?status=${encodeURIComponent(status)}`),
  adminDecidePayout: (id: string, approved: boolean, note: string) =>
    post<PayoutRequest>(`/api/admin/payouts/${encodeURIComponent(id)}/decision`, { approved, note }),
  adminResolvePayout: (id: string, action: "resend" | "cancel", note: string) =>
    post<PayoutRequest>(`/api/admin/payouts/${encodeURIComponent(id)}/resolve`, { action, note }),
  custody: () => get<Custody>("/api/admin/custody"),
  paymentEvents: () => get<PaymentEvent[]>("/api/admin/payment-events"),
  adminInvoices: () => get<OwnerInvoice[]>("/api/admin/invoices"),

  advance: (hours: number) => post<{ clock_offset_hours: number; actions: number }>("/api/dev/advance", { hours }),
};
