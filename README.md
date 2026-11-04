# Zerveil

**Private Payments. Autonomous Agents.**

Zerveil is a privacy-powered AI influencer agent marketplace. Advertiser agents find creator agents and negotiate
sponsorships with them over A2A. Campaigns are funded with **shielded Zcash** into an escrow wallet account. Creators
are paid to their own shielded Unified Address once their post is verified on YouTube.

> **Status: testnet MVP.**
> - By default the app runs in `PAYMENT_MODE=simulated`: an in-process wallet stand-in runs the full payment flow,
>   every record is labeled *simulated*, and **no ZEC moves**.
> - `PAYMENT_MODE=zcash` connects a real Zcash wallet (Zallet) over JSON-RPC on **testnet** (TAZ has no monetary
>   value). That code path is tested against a mocked RPC transport, but it has **not yet been run against a live
>   testnet wallet**. See [Status and blockers](#status-and-blockers).
> - The escrow is a **wallet account operated by Zerveil**, not a smart contract and not trustless. See
>   [Custody](#custody-what-the-escrow-is-and-is-not) and [PRIVACY.md](PRIVACY.md).

## Contents

1. [The problem](#the-problem)
2. [The idea](#the-idea)
3. [How it works](#how-it-works)
4. [Why Zcash, and what "shielded" buys you](#why-zcash-and-what-shielded-buys-you)
5. [Custody: what the escrow is and is not](#custody-what-the-escrow-is-and-is-not)
6. [Payments in detail](#payments-in-detail)
7. [HTTP 402 and agent spending limits](#http-402-and-agent-spending-limits)
8. [What you see in the app](#what-you-see-in-the-app)
9. [Architecture](#architecture)
10. [Trust and safety design](#trust-and-safety-design)
11. [Payment modes](#payment-modes)
12. [Run it](#run-it)
13. [Connect a Zcash testnet wallet](#connect-a-zcash-testnet-wallet)
14. [Configuration](#configuration)
15. [Database and migrations](#database-and-migrations)
16. [Tests: mocked vs real](#tests-mocked-vs-real)
17. [Deploy: Docker, CI/CD, Render](#deploy-docker-ci-cd-render)
18. [API reference (payments)](#api-reference-payments)
19. [Deep dive](#deep-dive)
20. [Project structure](#project-structure)
21. [Status and blockers](#status-and-blockers)
22. [References](#references)

---

## The problem

Influencer sponsorships are slow and leaky:
- **Negotiation is manual.** Rates, deliverables and deadlines bounce through DMs and spreadsheets.
- **Payment is all-or-nothing trust.** Either the brand pays up front and hopes the post appears, or the creator posts
  first and hopes to get paid.
- **Payments are public.** On transparent chains, every brand's spend and every creator's income is readable by
  competitors, by other brands negotiating with the same creator, and by anyone else.

## The idea

- **Agents negotiate.** Each creator runs an agent that knows their floors. Each advertiser runs an agent that knows the
  campaign's ceilings. They meet over A2A.
- **Code decides.** The LLM may suggest a move, but a deterministic policy engine accepts, counters or rejects. The
  LLM never holds a key.
- **Money waits for proof.** The agreed cap is paid into escrow first. The fixed fee is released when the post is
  verified, the performance fee after 7 days of views, and the remainder is refunded.
- **Payments stay private.** Funding, payouts and refunds are shielded Zcash transactions. Amounts, senders, receivers
  and memos are encrypted on-chain.

## How it works

```
Advertiser ──► Advertiser Agent ──A2A──► Creator Agents (one at a time)
                     │                         │ signs terms with its Ed25519 agent key
                     ▼                         ▼
              Policy gate (deterministic): terms, payee, budget
                     │
                     ▼
     Advertiser approves ──► one shielded funding invoice (fresh Unified Address + memo)
                                       │ advertiser pays from any Zcash wallet
                                       ▼
            Wallet scanner sees the note on that address, waits for confirmations ──► deals FUNDED
                                       │
        Creator posts ──► Verifier (YouTube API) ──► fixed fee decided ──► operator approves ──► shielded payout
                                       │
                     +7 days ──► performance fee + refund of the remainder (each approved, then sent)
```

1. **Discover and negotiate.** The advertiser agent reads each creator agent's A2A Agent Card and negotiates with
   creators one at a time, within the campaign's ceilings.
2. **Signed terms.** An accepted deal comes back as `DealTerms`, signed by the creator agent's Ed25519 key. The terms
   cover the deliverable, the fees, the cap, the deadline and the proof rules. The payee is the creator's shielded
   Unified Address and the network is `zcash-testnet`. The creator authorized that key while signed in, for that
   channel and that payout address.
3. **Policy gate.** Deterministic code re-checks the signature, payee, network, amounts and content rules. It then
   reserves the cap from the campaign budget.
4. **Approve and fund.** The advertiser approves the agreements and receives **one invoice**: the sum of the caps plus
   a small fee reserve per deal. It names a fresh shielded Unified Address, an amount, a memo `ZRV1:PAY-…` and a
   ZIP-321 payment link.
5. **Verified by the wallet.** The deals become `FUNDED` only when the escrow wallet itself has received enough on that
   address, with the required confirmations. A transaction id typed by a user is never accepted.
6. **Proof, then payouts.** The verifier checks the post: channel, timing, length, tags, disclosure and prompt
   injection. Payouts are decided by code and queued; an operator approves each one before the wallet sends it.

## Why Zcash, and what "shielded" buys you

| | Transparent chain (e.g. an EVM token) | Zerveil on Zcash (shielded) |
|---|---|---|
| Brand's total campaign spend | public | encrypted on-chain |
| What a creator was paid | public | encrypted on-chain |
| Who paid whom | public | encrypted on-chain |
| Payment memo / reference | public calldata | encrypted memo, readable only by the recipient |

Each invoice gets its own diversified Unified Address with Orchard and Sapling receivers only, no transparent
receiver. Zerveil only pays to shielded Unified Addresses: it refuses transparent `t…` addresses and asks for a Unified
Address instead of a bare Sapling address.

**What shielding does not hide:** the Zerveil operator holds the escrow wallet, so it sees what that wallet receives
and sends. The app database also stores deal amounts. See [PRIVACY.md](PRIVACY.md) for exactly who sees what.

## Custody: what the escrow is and is not

- The escrow is **an account in a Zcash wallet that the Zerveil operator runs** (Zallet). It is **not a smart
  contract** and **not trustless**. Shielded Zcash has no general-purpose contracts to lock funds under on-chain
  rules.
- Release rules are **deterministic code** in `services/deals.py` and `payments/service.py`. By default
  (`PAYOUT_APPROVAL_REQUIRED=true`) **every payout also needs an operator's approval** before the wallet signs it.
- Refunds of unusable money (late payments, partial payments to an expired invoice) **always** need an operator,
  whatever that setting says.
- The **custody view** (`/api/admin/custody`) puts the wallet's escrow balance next to what the records say it should
  hold. Every invoice, note, payout and decision is in an append-only `payment_events` audit trail.

Advertisers and creators trust the operator to run the wallet honestly. The MVP makes that trust auditable; it does
not remove it.

## Payments in detail

**Invoice lifecycle** (`payments/service.py`):

```
pending ──(note below amount)──► underpaid ──(rest arrives)──► confirming ──(≥ confirmations)──► paid
   │                                 │                                    (never expires once paid in full)
   └──(TTL passes)──► expired ◄──────┘
                         └──(money arrives after expiry)──► late_payment ──► refund queued (operator approval)
pending ──(owner cancels, nothing received)──► cancelled
```

The scanner reads the wallet's received notes. Each note is identified by `txid:pool:output_index` and counted for at
most one invoice, through an atomic claim; rescans never count it twice. Matching is by the invoice's own one-time
address. The memo is a secondary signal, recorded as `memo_matched`.

**Payout queue:**

```
pending_approval ─► approved ─► sending ─► submitted ─► sent ─► confirmed
        │                          │           │
        └─► rejected               ├─► failed  └─► unknown ──(found by memo in wallet history)──► sent
                                   └─► unknown ──(operator checked: not sent)──► approved (resend) / rejected
```

- Every transition is compare-and-set, so two workers can never both send.
- Each payout is idempotent on its key (e.g. `<deal>:fixed`).
- A send whose outcome was lost (timeout, crash) becomes `unknown`. It is reconciled from the wallet's outgoing
  memos (`ZRV1:PO-…`) and is **never resent automatically**.

**Amounts.**
- Amounts are exact decimals with at most 8 places (zatoshi).
- Each deal's funding includes `ZCASH_FEE_RESERVE_PER_DEAL` (default 0.0003) to pay ZIP-317 fees for its payouts.
- The fee reserve that is not spent stays in the escrow account; the custody view shows it.

## HTTP 402 and agent spending limits

`GET /api/influencers/premium-search?niche=fitness` returns **HTTP 402 Payment Required**:

```json
{
  "error": "payment_required",
  "payment": {"id": "PAY-…", "amount": "0.05", "ticker": "TAZ", "address": "utest1…", "memo": "ZRV1:PAY-…",
              "zip321_uri": "zcash:utest1…?amount=0.05&memo=…", "status": "pending", "required_confirmations": 3},
  "retry_with_header": {"X-Payment-Id": "PAY-…"}
}
```

Pay the invoice, then repeat the request with `X-Payment-Id`. Results unlock once the wallet sees the payment
confirmed. Each payment unlocks **one** request, for **its own** scope and user.

**Agents pay too.** `POST /api/agent/premium-search` lets the advertiser's agent pay the 402 from **prepaid agent
credit**:
- Credit is topped up **only** through a verified shielded `credit_topup` invoice.
- Spending follows the owner's `SpendingPolicy`: on/off, allowed categories, per-request and 24-hour limits, and a
  `max_campaign_budget` that also caps new campaigns.
- Above `approval_above`, the agent files a spend request and waits for its owner to approve.
- The limit check and the debit happen under one lock, so parallel requests cannot overspend.

## What you see in the app

- **Testnet badge** everywhere, plus a "simulated: no ZEC moves" badge in simulated mode.
- **Onboarding:**
  - Creators sign in with Google (YouTube channel), add a shielded payout address and set their rates.
  - Advertisers add a shielded refund address and their brand.
- **Campaigns:** live negotiation room, approval panel and a **shielded checkout**: amount, Unified Address, memo,
  "open in wallet" ZIP-321 link, status and confirmations, expiry. In dev, a *simulate payment* button.
- **Public payment page** `/pay/:id`: what to pay and whether it arrived. No transaction ids, no campaign terms.
- **Payments:**
  - Advertisers: invoices, agent credit and top-up, spending limits, spend approvals, the 402 demo.
  - Creators: payouts received and **payment links** (paid in, then forwarded to their own address after
    approval).
- **Admin:** payout approval queue with evidence hashes, unknown-outcome resolution, custody reconciliation and the
  payment audit trail.

**Frontend stack note:** the UI is React + TypeScript + Vite with **plain CSS** (CSS variables, dark violet/cyan
theme). The original prompt suggested Tailwind. We kept the existing design system because rewriting every
component's styling would add risk without changing behavior.

## Architecture

```
                         ┌───────────────────────── FastAPI (one process) ─────────────────────────┐
 Browser (React) ──HTTPS─►  /api (auth, campaigns, deals, payments, admin)   /api/stream (SSE)        │
                         │  /a2a/influencers/{id}  (A2A creator agents)       /mcp (read-only MCP)    │
                         │  Advertiser agent (LangGraph) ─► DealService ─► PaymentService ─► Wallet ──┼─JSON-RPC─► Zallet
                         │  Scheduler: payment scan · payout decisions · payout queue                 │   (testnet)
                         └──────────────┬─────────────────────────────────────────────────────────────┘
                                        ▼
                               PostgreSQL (or in-memory)
```

- `agents/`: LangGraph advertiser agent; A2A creator agents (`a2a-sdk`), whose LLM only suggests.
- `agent_policy/`, `policy/`: deterministic negotiation and payment rules.
- `services/deals.py`: deal lifecycle. `payments/service.py`: invoices, scanner, payouts, credit, links, custody.
- `zcash/`:
  - `rpc.py`: the real wallet over JSON-RPC.
  - `simulated.py`: the dev and test stand-in.
  - Address, ZIP-321, memo and amount helpers.
- `verifier/`: YouTube Data API checks. `compliance/`: disclosure policies.
- `store/`: PostgreSQL and in-memory stores; `migrations.py`.

## Trust and safety design

- **The LLM proposes, code decides, the wallet signs after checks and approval.** No LLM or MCP tool can sign, create
  invoices or release funds.
- **No payment without wallet evidence.** Only notes the escrow wallet decrypted on the invoice's own address count,
  with confirmations. User-supplied txids are never evidence.
- **No double counting, no double paying:** atomic note claims, idempotent payout keys, compare-and-set statuses, and
  `unknown` reconciled from the wallet's history, never blindly resent.
- **Terms are bound:** approval is refused if the signed terms changed after the policy gate, and the funding amount
  must equal the caps plus the fee reserve.
- **Address checks:**
  - Payees must be shielded Unified Addresses on the configured network (Bech32m checksum, network prefix).
  - In zcash mode the wallet also checks the receivers (`z_listunifiedreceivers`).
- **Privacy by default:**
  - A creator's payout address is visible only to them and operators; it is redacted for advertisers and never
    listed in the directory or Agent Card.
  - `/api/status` never shows the RPC URL or credentials.
- **Prompt injection:** a video description that instructs "AI reviewers" is rejected by the verifier.
- **Fail closed:** `PAYMENT_MODE=zcash` refuses to start without wallet configuration and never falls back to
  simulation. The removed `hosted`/`sepolia` modes are rejected with a clear message.

## Payment modes

| Mode | Wallet | Money | Use |
|---|---|---|---|
| `simulated` (default) | in-process `SimulatedZcashWallet` | none; fake `utest` addresses, `sim…` txids, everything labeled simulated | development, demos, CI |
| `zcash` | Zallet JSON-RPC (`zcash/rpc.py`) | real TAZ on testnet | integration with a real wallet |

## Run it

Requirements: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22. PostgreSQL is optional.

```bash
cp .env.example .env              # fill in Google OAuth, SESSION_SECRET, YOUTUBE_API_KEY
uv sync
cd web && npm install && npm run build && cd ..
uv run zerveil serve              # http://localhost:8000
```

Hot reload: run `cd web && npm run dev` (port 5173, proxies the API) next to `uv run zerveil serve`.

Simulated walkthrough:
1. Sign in as an advertiser and add a refund address. Any valid `utest1…` Unified Address works; in simulated mode it
   is only checked for format.
2. Create a campaign and watch the negotiation.
3. Approve the agreements and click **Simulate payment** on the checkout.
4. Submit a proof as the creator.
5. Approve the payouts as an `ADMIN_EMAILS` account.
6. Use the dev clock to jump 7 days ahead.

## Connect a Zcash testnet wallet

Zerveil talks to a **Zallet** wallet (the successor to zcashd's wallet), which needs a **Zebra** full node. Follow the
official guides; this list only names the steps:

1. Run Zebra on testnet and let it sync.
2. Install Zallet, point it at Zebra, set the network to testnet, and enable its JSON-RPC on `127.0.0.1` (Zallet's
   `[rpc]` settings; add RPC auth if you expose it beyond loopback).
3. Create or choose a wallet account for the escrow (see Zallet's account commands; `z_listaccounts` lists them) and get
   a Unified Address for it (`z_getaddressforaccount`).
4. Fund the escrow with testnet TAZ from a faucet, to cover payout fees.
5. Set the following in `.env`:
   ```
   PAYMENT_MODE=zcash
   ZCASH_NETWORK=testnet
   ZCASH_WALLET_RPC_URL=http://127.0.0.1:<rpc port>
   ZCASH_WALLET_RPC_USER=…            # if RPC auth is on
   ZCASH_WALLET_RPC_PASSWORD=…
   ZCASH_ESCROW_ACCOUNT=<account uuid or number>
   ZCASH_ESCROW_FROM_ADDRESS=utest1…
   ADMIN_EMAILS=you@example.com
   ```
6. Start Zerveil. It refuses to start if any of these are missing. `/api/wallet/status` (and the admin custody view) shows
   whether the wallet answers.
7. Optional: run the real-testnet checks with `ZCASH_LIVE_RPC_URL=… uv run pytest tests/test_zcash_live.py -s`. Add
   `ZCASH_LIVE_SEND=1 ZCASH_LIVE_SEND_TO=utest1…` to also send 0.0001 TAZ and print the txid.

Wallet RPC methods used are listed in `zcash/rpc.py`; all are in Zallet's zcashd-compatibility table. The RPC is
plain HTTP: **never expose it to the internet.**

## Configuration

See [`.env.example`](.env.example). Key settings:

| Variable | Default | Meaning |
|---|---|---|
| `PAYMENT_MODE` | `simulated` | `simulated` or `zcash` |
| `ZCASH_NETWORK` | `testnet` | `testnet` / `mainnet` / `regtest` (MVP: testnet) |
| `ZCASH_WALLET_RPC_URL`, `_USER`, `_PASSWORD` | – | Zallet JSON-RPC |
| `ZCASH_ESCROW_ACCOUNT`, `ZCASH_ESCROW_FROM_ADDRESS` | – | the escrow account and the UA payouts are sent from |
| `ZCASH_CONFIRMATION_THRESHOLD` | 3 | confirmations before an invoice counts as paid |
| `ZCASH_INVOICE_TTL_MINUTES` | 120 | funding / top-up invoice lifetime |
| `ZCASH_FEE_RESERVE_PER_DEAL` | 0.0003 | added to funding for payout fees |
| `PAYOUT_APPROVAL_REQUIRED` | true | operator approves each payout |
| `ADMIN_EMAILS` | – | operators (payout approval, custody) |
| `PREMIUM_SEARCH_PRICE` | 0.05 | price of the 402-gated search |

## Database and migrations

- PostgreSQL via psycopg 3 (`DATABASE_URL`); without it, an in-memory store.
- Schema changes are versioned in `src/zerveil/store/migrations.py`. They are applied automatically at startup under
  an advisory lock, or by hand with `uv run zerveil migrate` (`--status` lists them).
- Migration 2 (`zcash_payments`) adds `invoices` (unique address), `payout_requests`, `agent_credit_ledger`,
  `agent_spend_requests` and `payment_events`. It also widens budget amounts to 8 decimals and drops the old webhook
  table.
- **Old EVM-era data:**
  - Deals that were mid-payment under the removed EVM flow are moved to `ARCHIVED` and their budget is released.
  - Old wallet links are removed and creators' agents are paused until they add a shielded payout address.

## Tests: mocked vs real

```bash
uv run pytest          # everything below except the real-wallet tests
uv run ruff check . && uv run ruff format --check .
```

| Suite | Wallet | What it proves |
|---|---|---|
| `test_e2e.py`, `test_payment_api.py`, `test_negotiation_*`, … | **simulated** | the full app flow through the HTTP API: negotiation → funding invoice → scan → proofs → approved payouts; 402; links; admin auth |
| `test_payments.py` | **simulated** | invoice states, duplicate notes, expiry and late refunds, payout queue (approval, races, failures, lost outcomes), agent credit and limits |
| `test_zcash.py` | **mocked RPC** (`httpx.MockTransport`) | what we send to Zallet and how we parse its answers; Bech32m/UA, ZIP-321, memos, amounts; fail-closed config |
| `test_policy.py` | none | terms, signatures, payee and network checks, funding check, delegation |
| `test_store_postgres.py` | none | PostgreSQL store and migrations (needs `TEST_DATABASE_URL`, else skipped) |
| `test_zcash_live.py` | **REAL testnet wallet** | reachability, fresh shielded addresses, scanning, optional real send (needs `ZCASH_LIVE_*`, else skipped) |

The mocked RPC tests use response shapes taken from the Zallet/zcashd documentation. They do not prove that a live
wallet answers exactly that way; only `test_zcash_live.py` can.

## Deploy: Docker, CI/CD, Render

- `Dockerfile`: builds the React app, then the Python app; runs `zerveil serve` as a non-root user. The Zcash wallet is
  **not** in the image: run Zallet separately and reach it over a private network.
- `.github/workflows/ci.yml`:
  1. Lint and tests (PostgreSQL service included).
  2. Frontend build.
  3. Docker build and a smoke run, which asserts `payment_mode == simulated` and testnet.
  4. Deploy to Render via a deploy hook on `main`.
- `render.yaml`: one Docker web service. Secrets are entered in the dashboard (`sync: false`), never in git. Keep
  `PAYMENT_MODE=simulated` unless the service can reach a private Zallet RPC.

## API reference (payments)

| Method & path | Who | Purpose |
|---|---|---|
| `PUT /api/me/zcash-address` | user | set payout (creator) / refund (advertiser) Unified Address |
| `POST /api/campaigns/{id}/decision` | advertiser | approve → one funding invoice; decline → deals rejected |
| `GET /api/pay/{id}` | public | payment page data (no txids, no campaign terms) |
| `GET /api/payments/invoices[/{id}]` | owner | invoices, detected notes, audit events |
| `POST /api/payments/invoices/{id}/cancel` | owner | cancel an untouched invoice (not funding invoices) |
| `POST /api/payments/links` | creator | payment link, forwarded to the creator after approval |
| `GET /api/payments/payouts` | user | payouts to me |
| `GET /api/campaigns/{id}/payouts` | advertiser | campaign payouts (creator addresses redacted) |
| `GET /api/wallet/status` | user | backend, network, reachability (balance for admins) |
| `GET/PUT /api/agent/spending-policy` | advertiser | agent spending limits |
| `GET /api/agent/credit`, `POST /api/agent/credit/topup` | advertiser | prepaid agent credit |
| `POST /api/agent/spend-requests/{id}` | advertiser | approve / reject an agent payment |
| `GET /api/influencers/premium-search` | advertiser | HTTP 402 demo |
| `POST /api/agent/premium-search` | advertiser | agent pays the 402 from credit |
| `GET /api/admin/payouts`, `POST …/{id}/decision`, `POST …/{id}/resolve` | admin | payout approval, unknown-outcome resolution |
| `GET /api/admin/custody`, `/api/admin/payment-events`, `/api/admin/invoices` | admin | custody reconciliation, audit |
| `POST /api/dev/payments/{id}/simulate`, `POST /api/dev/advance` | user, simulated mode only | simulated payment, clock |

## Deep dive

### Deal state machine

Enforced by `models.DEAL_TRANSITIONS`; `DealService.save` refuses any other move.

```
NEGOTIATING → AGREED → AWAITING_APPROVAL → AWAITING_FUNDING → FUNDING → FUNDED
 ├→ REJECTED     └→ BLOCKED (policy gate)    │                    │
 └→ ESCALATED                                 └──(invoice expired)─┴→ PAYMENT_EXPIRED

FUNDED → VERIFIED → FIXED_RELEASED → COMPLETED (performance decided, remainder refunded)
  │         │  └→ DISPUTED
  │         └→ PROOF_REJECTED (resubmit before the deadline)
  └→ EXPIRED_REFUNDED (no valid proof by the deadline)

ARCHIVED: deals left mid-payment by the removed EVM flow (migration 2)
```

Creators are told to post only at `FUNDED`, and proofs are refused before then.

### Configurable agents

Each side configures its agent in the **Agent Profile Builder**. A profile has three layers and is saved as an
immutable version; campaigns snapshot the advertiser's version.

| Layer | Creator agent | Advertiser agent |
|---|---|---|
| Objective | earnings, guaranteed income, brand fit, partnership or workload, with weights | ROI, cost, reach, relevance, evidence quality or speed, with weights |
| Hard constraints | minimum fixed fee and CPM, accepted pricing models, shortest publish window, max revisions, licensing and exclusivity limits, forbidden categories | per-campaign ceilings (fixed fee, CPM, cap, budget), minimum verified subscribers, blocked niches |
| Preferences | opening ask, premiums, acceptance score, escalate instead of decline | opening percentages, concession per round, niche-fit premium, acceptance score |

**Decision order** (no LLM, no I/O): hard constraints → money limits → utility score → `ACCEPT`, `COUNTEROFFER`,
`REJECT`, `REQUEST_CLARIFICATION` or `ESCALATE_TO_HUMAN`.

**Pricing** (versioned and signed into the terms):
- `fixed` pays the fee;
- `cpm` pays `min(cap, views/1000 × CPM)`;
- `hybrid` pays `min(cap, fixed + views/1000 × CPM)`.

**Human feedback.** Each party rates its own agent. A lesson reaches the agent only after its owner approves it, and
only as prompt guidance; it never touches the policy engine.

### Verification and the content agent

The verifier checks:
- the video exists and is public;
- it is owned by the deal's channel;
- it was published after the agreement and before the deadline;
- its length, the required tags and the brand handle;
- the disclosure label;
- no AI-directed instructions.

Spoken mentions are counted from captions. A logo check by a vision model is advisory only and routes the post to the
advertiser's review; it never decides a payout.

### Sponsorship disclosure

Disclosure policies are versioned per jurisdiction: India (ASCI) and the US (FTC Endorsement Guides). They are
snapshotted per campaign and signed into the terms. These are product rules that summarize public guidance: **not
legal advice, and no guarantee of compliance.**

### MCP server (what the LLM may touch)

A read-only `zerveil` MCP server (streamable HTTP at `/mcp/`, or stdio). Tools: campaigns, deals, timelines, campaign
spend, the influencer directory and profiles, and channel and video statistics. Signing, invoices and payouts are
deliberately **not** tools.

## Project structure

```
src/zerveil/
  agents/            advertiser (LangGraph) and influencer (A2A) agents
  agent_policy/      deterministic negotiation engine + versioned profiles
  api/               FastAPI app (app.py) and payment routes (payments.py); static/ = built web app
  auth/              Google OAuth, sessions, Ed25519 agent keys and delegation
  compliance/        disclosure policies
  mcp_servers/       read-only MCP server
  payments/          invoices, scanner, payout queue, credit, links, custody
  policy/            terms / funding checks, terms signing
  services/          context (wallet selection), deals, registry
  store/             PostgreSQL + in-memory stores, migrations
  verifier/          YouTube checks, content agent
  zcash/             Zallet RPC wallet, simulated wallet, UA / ZIP-321 / memo / amounts
web/                 React + TypeScript + Vite frontend
tests/               see "Tests: mocked vs real"
```

## Status and blockers

**Done:**
- The HSP/HashKey/EVM payment path was removed: no Solidity, no EVM escrow, no EIP-712/EIP-3009, no web3.
- Zcash invoices, scanner, payout queue, credit, 402, payment links, admin approval, migrations, UI and docs.
- The full flow is tested end to end with the simulated wallet. The Zallet client is tested against a mocked RPC.

**Not done / blockers:**
- **No real testnet transaction has been executed by this project yet.** The environment it was built in had no
  synced Zebra node, no Zallet wallet and no TAZ. There is therefore **no testnet txid to show**. To produce one,
  follow [Connect a Zcash testnet wallet](#connect-a-zcash-testnet-wallet) and run `tests/test_zcash_live.py` with
  `ZCASH_LIVE_SEND=1`.
- Zallet is still evolving. Its RPC differs from zcashd in places, and `zcash/rpc.py` parses both shapes
  defensively. Verify against the Zallet version you run.
- The escrow is custodial. Making it trust-minimized, for example with multisig/FROST-controlled spending, is future
  work.
- Viewing-key based auditing (sharing an incoming viewing key with an auditor) is not implemented.

## References

- Zcash protocol and Unified Addresses (ZIP-316): https://zips.z.cash/zip-0316
- ZIP-321 payment request URIs: https://zips.z.cash/zip-0321
- ZIP-317 proportional fees: https://zips.z.cash/zip-0317
- Zallet: https://zcash.github.io/zallet/ (RPC compatibility: https://zcash.github.io/zallet/zcashd/rpc_status.html)
- Zebra: https://zebra.zfnd.org/
- A2A protocol: https://a2a-protocol.org/
- Model Context Protocol: https://modelcontextprotocol.io/
