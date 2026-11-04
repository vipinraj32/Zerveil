# Zerveil

**Private Payments. Autonomous Agents.**

Zerveil is a privacy-powered AI influencer agent marketplace. Advertiser agents find creator agents and negotiate
sponsorships with them over A2A. Advertisers then pay each creator **directly**, with **shielded Zcash**, to a fresh
address of the creator's own wallet. Zerveil never holds the money. It issues invoices, watches the creator's account
through a viewing key the creator connected, confirms the payment, issues a receipt and tracks the campaign.

> **Zcash Testnet — TAZ — No real monetary value.**
> - By default the app runs in `PAYMENT_MODE=simulated`: an in-process scanner stand-in runs the full flow, every
>   record is labeled *simulated*, and **no TAZ moves**.
> - `PAYMENT_MODE=zcash` scans the real testnet with [zcash-devtool](https://github.com/zcash/zcash-devtool) through
>   the public lightwalletd `testnet.zec.rocks:443`. That path is tested against a stubbed `zcash-devtool` process; it
>   has **not yet been run against the live testnet by this project**. See [Status and blockers](#status-and-blockers).
> - **There is no escrow.** Zerveil has no spending key and cannot hold, lock, release or refund funds. See
>   [Custody](#custody-there-is-none) and [PRIVACY.md](PRIVACY.md).

## Contents

1. [The problem](#the-problem)
2. [The idea](#the-idea)
3. [How it works](#how-it-works)
4. [Why Zcash, and what "shielded" buys you](#why-zcash-and-what-shielded-buys-you)
5. [Custody: there is none](#custody-there-is-none)
6. [Payments in detail](#payments-in-detail)
7. [HTTP 402 and agent spending limits](#http-402-and-agent-spending-limits)
8. [What you see in the app](#what-you-see-in-the-app)
9. [Architecture](#architecture)
10. [Trust and safety design](#trust-and-safety-design)
11. [Payment modes](#payment-modes)
12. [Run it](#run-it)
13. [Wallet selection (NU7 / Ironwood)](#wallet-selection-nu7--ironwood)
14. [Real testnet setup](#real-testnet-setup)
15. [Configuration](#configuration)
16. [Database and migrations](#database-and-migrations)
17. [Tests: mocked vs real](#tests-mocked-vs-real)
18. [Deploy: Docker, CI/CD, Render](#deploy-docker-cicd-render)
19. [API reference (payments)](#api-reference-payments)
20. [Deep dive](#deep-dive)
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
  LLM never holds a key and never signs a payment.
- **Pay in milestones, wallet to wallet.** The fixed fee is paid upfront to the creator's own wallet. The performance
  fee is requested after the post is verified and its views are counted at T+7d. Nobody in between holds the money.
- **Payments stay private.** Every payment is a shielded Zcash transaction. Amounts, senders, receivers and memos are
  encrypted on-chain.

## How it works

```
Advertiser ──► Advertiser Agent ──A2A──► Creator Agents (one at a time)
                     │                         │ signs terms with its Ed25519 agent key
                     ▼                         ▼
              Policy gate (deterministic): terms, payee, budget
                     │
                     ▼
   Advertiser accepts ──► one invoice per creator: fixed fee, to a fresh address of THAT creator's account
                                       │ advertiser pays from their own wallet (ZIP-321 link / QR / "pay all")
                                       ▼
   Scanner (creator's viewing key) sees the note ─► seen ─► mined ─► confirming ─► confirmed (3 confs) ─► receipt
                                       │
        Creator posts ──► Verifier (YouTube API) ──► Verified / Rejected / Needs human review
                                       │
                     +7 days ──► performance-fee invoice (views × CPM, capped) ──► paid directly ──► Completed
```

1. **Discover and negotiate.** The advertiser agent reads each creator agent's A2A Agent Card and negotiates with
   creators one at a time, within the campaign's ceilings.
2. **Signed terms.** An accepted deal comes back as `DealTerms`, signed by the creator agent's Ed25519 key. The terms
   cover:
   - the deliverable, fees, cap and deadline;
   - the proof rules;
   - the payee (the creator's shielded Unified Address) and the network, `zcash-testnet`.
3. **Policy gate.** Deterministic code re-checks the signature, payee, network, amounts and content rules. It then
   reserves the cap from the campaign budget.
4. **Accept and pay.** The advertiser accepts the agreements. Each creator gets a **fixed-fee milestone invoice**
   containing:
   - a fresh diversified Unified Address of the creator's connected receiving account;
   - the amount;
   - a memo `ZRV1:PAY-…`;
   - a ZIP-321 link.

   One ZIP-321 "pay all" request covers every open invoice of the campaign.
5. **Confirmed by scanning.** The scanner holds the creator's viewing key. It finds the note on that address and
   follows it to `ZCASH_CONFIRMATION_THRESHOLD` confirmations (default 3). It then issues a receipt. A transaction id
   typed by a user is never accepted.
6. **Proof, then performance.** The verifier checks the post: channel, timing, length, tags, disclosure and prompt
   injection. At T+7d the performance fee is computed from views and requested as a second invoice.

Campaign progress in the UI: Draft → Agent Negotiation → Agreement Accepted → Payment Requested → Payment Pending →
Payment Confirmed → Content Creation → Content Submitted → Content Verification → Completed.

## Why Zcash, and what "shielded" buys you

| | Transparent chain (e.g. an EVM token) | Zerveil on Zcash (shielded) |
|---|---|---|
| Brand's total campaign spend | public | encrypted on-chain |
| What a creator was paid | public | encrypted on-chain |
| Who paid whom | public | encrypted on-chain |
| Payment memo / reference | public calldata | encrypted memo, readable only by the recipient |

Each invoice gets its own diversified Unified Address with shielded receivers only: Orchard (paid into Ironwood after
NU7) and Sapling, with no transparent receiver. Without the viewing key, those addresses cannot be linked to each
other or to the creator.

**What shielding does not hide:**
- The operator's scanner holds the viewing keys creators connected, so it can see those accounts' transactions.
- The app database stores deal amounts.

See [PRIVACY.md](PRIVACY.md).

## Custody: there is none

- **There is no escrow, operator wallet, payout queue or refund.** Zerveil has no spending key, so it cannot move
  anyone's money. Earlier versions had an operator-run escrow account; it was removed (see
  [Database and migrations](#database-and-migrations)).
- Advertisers pay creators from their own wallets, and creators receive in their own wallets.
- HTTP 402 API calls and agent API credit are paid directly to the **API seller's** own receiving account
  (`ZCASH_API_SELLER_UFVK`), not to an intermediary.
- **The trade-off is that neither side is protected by an intermediary:**
  - an advertiser is not protected from a creator who takes the fixed fee and does not post;
  - a creator is not protected from an advertiser who does not pay the performance fee.

  Milestones keep that exposure small. Disputes are settled between the parties' wallets.

## Payments in detail

**Invoice lifecycle** (`payments/service.py`):

```
awaiting_payment ──(note in mempool)──► seen ──(mined)──► mined ──► confirming ──(≥ 3 confirmations)──► confirmed
       │                                                                                    └─► overpaid (> amount)
       ├──(less than the amount)──► underpaid ──(rest arrives)──► …
       ├──(TTL passes, nothing seen)──► expired   (money that arrives later is recorded as a late payment; Zerveil
       │                                           cannot refund it — the parties settle it wallet to wallet)
       └──(owner cancels, nothing received)──► cancelled
```

- A payment seen before expiry keeps confirming after the TTL. An invoice expires only if nothing was seen in time.
- If the scanner cannot reach lightwalletd, that account is skipped for the round. Its invoices are **never expired
  or confirmed on a failed scan**.
- Each note is identified by `txid:pool:output_index`. An atomic claim ensures it counts for at most one invoice.
- Matching is by the invoice's own one-time address. The `ZRV1:` memo is a secondary match.
- On confirmation a **receipt** (`RCPT-…`) is issued once. Its `receipt_hash` is a SHA-256 over its fields.

**Milestones** (`models.Milestone`):
- **Kinds:**
  - `fixed` is requested when the agreement is accepted, and skipped for CPM-only deals.
  - `performance` is requested at T+7d after verification, and waived when it computes to zero.
- **Statuses:** `scheduled → requested → pending → confirmed`, or `expired` / `waived`.
- **Amount checks** (`policy.check_milestone`):
  - the fixed milestone must equal the signed fixed fee;
  - the performance milestone may not exceed `cap − fixed`.

**Amounts.**
- Amounts are exact decimals with at most 8 places (zatoshi).
- Network fees are paid by the payer's wallet on top of the invoice amount, calculated by that wallet (ZIP-317).
- Zerveil never adds a fee reserve.

## HTTP 402 and agent spending limits

`GET /api/influencers/premium-search?niche=fitness` returns **HTTP 402 Payment Required**:

```json
{
  "error": "payment_required",
  "payment": {"id": "PAY-…", "amount": "0.05", "ticker": "TAZ", "address": "utest1…", "memo": "ZRV1:PAY-…",
              "zip321_uri": "zcash:utest1…?amount=0.05&memo=…", "status": "awaiting_payment",
              "required_confirmations": 3, "direct": true},
  "retry_with_header": {"X-Payment-Id": "PAY-…"}
}
```

The address belongs to the API seller's own account. To use it:
1. Pay the invoice.
2. Repeat the request with `X-Payment-Id`.

Results unlock once the payment is confirmed. Each payment unlocks **one** request, for **its own** scope and user.

**Agents pay too.** `POST /api/agent/premium-search` lets the advertiser's agent pay the 402 from **prepaid API
credit**:
- Credit is topped up **only** through a confirmed shielded `credit_topup` invoice paid to the API seller.
- Spending follows the owner's `SpendingPolicy`:
  - on/off and allowed categories;
  - per-request and 24-hour limits;
  - a `max_campaign_budget` that also caps new campaigns.
- Above `approval_above`, the agent files a spend request and waits for its owner to approve.
- The limit check and the debit happen under one lock, so parallel requests cannot overspend.

## What you see in the app

- **"Zcash Testnet — TAZ — No real monetary value"** on every page, plus a "simulated" badge on simulated records.
- **Onboarding:**
  - Creators sign in with Google (YouTube channel) and set their Unified Address and rates. They then **connect a
    receiving account**: viewing key, birthday height and explicit consent. They are listed only once connected.
  - Advertisers add their company and brand handle. They need no wallet address.
- **Campaigns:**
  - the negotiation room and approval panel;
  - one payment card per creator: amount, address, memo, QR code, ZIP-321 link, status with confirmation progress,
    and receipt link;
  - a "pay all" QR code and link;
  - milestone status per deal;
  - in simulated mode, a *simulate payment* button.
- **Public payment page** `/pay/:id`: what to pay, a QR code, and whether it arrived. It shows no txids and no
  campaign terms.
- **Wallet status:**
  - scanner backend, network, lightwalletd reachability, chain tip and NU7 status;
  - your address;
  - "confirmed received via Zerveil", which is not a wallet balance;
  - a faucet link on testnet.
- **Receipts:** a list and a detail view (amount, pools, txids with explorer links, confirmations, mined height, hash).
- **Admin (read-only):** scanner status, open invoices and the payment audit trail. Operators cannot move funds.

**Frontend stack note:** the UI is React + TypeScript + Vite with **plain CSS**. The integration prompt asked to
"preserve Tailwind CSS", but this project never used Tailwind, so the existing plain-CSS design system was kept.

## Architecture

```
                   ┌──────────────────────── FastAPI (one process) ────────────────────────┐
 Browser (React) ──►  /api (campaigns, deals, payments, receipts, admin)   /api/stream (SSE) │
                   │  /a2a/influencers/{id} (A2A creator agents)    /mcp (read-only MCP)     │
                   │  Advertiser agent ─► DealService ─► PaymentService ─► Scanner ──────────┼─► zcash-devtool (subprocess,
                   │  Scheduler: payment scan · milestone due dates                         │   view-only wallet dir)
                   └──────────────┬─────────────────────────────────────────────────────────┘          │ gRPC/TLS
                                  ▼                                                                    ▼
                         PostgreSQL (or in-memory)                                   lightwalletd testnet.zec.rocks:443

 Advertiser's wallet ─────────── shielded tx, signed by the advertiser ───────────► creator's own wallet account
```

- `zcash/wallet.py` defines the `PaymentScanner` protocol: `import_viewing_key`, `new_address`, `received`, `info`.
  **There is no send method.**
- Scanner backends:
  - `zcash/devtool.py`: `DevtoolScanner`, the real backend. It runs the `zcash-devtool` CLI against a view-only
    wallet.
  - `zcash/simulated.py`: `SimulatedScanner`, for development and tests.
- `zcash/address.py`: Bech32m Unified Address and viewing-key checks. It rejects seed phrases and spending keys.
- Other `zcash/` helpers: `zip321.py` (single and multi-recipient URIs), `memo.py` and `amounts.py`.
- `payments/service.py`: receiving accounts, invoices, scanning, receipts, payment links, API credit and the 402.
- `services/deals.py`: the deal lifecycle and milestones.
- The other packages: `agents/`, `agent_policy/`, `policy/`, `verifier/`, `compliance/` and `store/`.

## Trust and safety design

- **The LLM proposes, code decides, the payer's wallet signs.** No LLM or MCP tool can sign, create invoices or move
  funds, and Zerveil has no key that could.
- **No payment without scan evidence.**
  - Only notes the scanner itself decrypted on the invoice's address count, and only with confirmations.
  - User-supplied txids are never evidence.
  - An unconfirmed payment is never treated as confirmed.
- **No double counting:** atomic note claims, compare-and-set statuses, and receipts issued once.
- **Terms are bound:**
  - approval is refused if the signed terms changed after the policy gate;
  - each milestone amount is checked against the signed terms.
- **Keys: viewing keys only.**
  - Seed phrases, spending keys, incoming-only keys and wrong-network keys are refused.
  - The viewing key goes to the scanner; the database keeps only a fingerprint.
  - Scanner errors are redacted.
- **Privacy by default:**
  - A creator's address is visible only to them and to operators. It is never in the directory or Agent Card.
  - `/api/status` never shows secrets or local paths.
- **Prompt injection:** the verifier rejects a video description that instructs "AI reviewers".
- **Fail closed:**
  - `PAYMENT_MODE=zcash` refuses to start without the scanner configuration or the binary.
  - It refuses mainnet.
  - It never falls back to simulation.
  - The removed `escrow`, `hosted` and `sepolia` modes are rejected.

## Payment modes

| Mode | Scanner | Money | Use |
|---|---|---|---|
| `simulated` (default) | in-process `SimulatedScanner` | none; fake `utest` addresses, `sim…` txids, everything labeled simulated | development, demos, CI |
| `zcash` | `DevtoolScanner` (`zcash-devtool` + lightwalletd) | real TAZ on testnet, paid wallet to wallet | real testnet |

## Run it

Requirements: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22. PostgreSQL is optional.

```bash
cp .env.example .env              # fill in Google OAuth, SESSION_SECRET, YOUTUBE_API_KEY
uv sync                           # Windows: set UV_LINK_MODE=copy
cd web && npm install && npm run build && cd ..
uv run zerveil serve              # http://localhost:8000
```

Hot reload: run `cd web && npm run dev` (port 5173, proxies the API) next to `uv run zerveil serve`.

Simulated walkthrough:
1. Sign in as a creator and set any valid `utest1…` Unified Address. Connect a receiving account; in simulated mode
   the form accepts a `sim:` key.
2. Sign in as an advertiser, create a campaign and watch the negotiation.
3. Accept the agreements and click **Simulate payment** on each payment card.
4. Submit a proof as the creator.
5. Use the dev clock to jump 7 days ahead, then pay the performance invoice.

## Wallet selection (NU7 / Ironwood)

The wallet integration has to meet these requirements:
- **Network:** testnet, through a public lightwalletd (`testnet.zec.rocks:443`).
- **NU7 compatibility:**
  - testnet activation height **4,465,026**, branch id `0x77190AD9`;
  - v6 transactions, with Orchard outputs going to the Ironwood pool;
  - no v4 transactions.
- **Fees:** ZIP-317 fees computed by the wallet.
- **Scanning with a viewing key**, because Zerveil must not hold funds.

| Option | NU7 | Viewing-key import | Light client (lightwalletd) | Verdict |
|---|---|---|---|---|
| **zcash-devtool** (main) | yes — built on the librustzcash cohort that supports NU7; the testnet faucet runs it | yes: `init-fvk`, `import-ufvk` (Unified Full Viewing Keys) | yes | **selected** |
| Zallet (beta) | follows NU7 | `z_importviewingkey` accepts Sapling keys only, so it cannot watch Orchard/Ironwood | no: needs a synced Zebra full node | rejected for scanning |
| zingolib / zingo-cli | NU7 cohort (v8) | view-only import exists, but there is no stable machine-readable CLI to integrate | yes | not selected |
| zcashd | deprecated, no NU7 | — | — | rejected |

**Selected: zcash-devtool.** Zerveil drives it as a subprocess (`zcash/devtool.py`) with a dedicated **view-only**
wallet directory. It uses these commands:
- `init-fvk` / `import-ufvk`;
- `list-accounts`;
- `generate-address -p orchard -p sapling`;
- `sync`, `get-info` and `list-tx`.

The text output is parsed fail-closed: output it does not recognize counts as nothing.

zcash-devtool itself carries two caveats:
- It is a developer tool, not production software.
- Its command-line interface can change without warning, so pin the commit you build.

For payers, any NU7-capable shielded wallet works. That includes zcash-devtool's own
`pay --payment-uri <zip321>`, which uses ZIP-317 fees. `send` accepts `--tx-version`: never pass `4`.

## Real testnet setup

Zerveil automates none of the steps below. It never creates wallets for you, never claims faucet funds, and never
handles a seed phrase or spending key.

1. **Build zcash-devtool** (requires a Rust toolchain):
   ```bash
   git clone https://github.com/zcash/zcash-devtool && cd zcash-devtool
   cargo build --release            # binary: target/release/zcash-devtool
   ```
2. **Each creator: create a dedicated receiving account** in their own wallet. Any NU7-capable wallet works. With
   zcash-devtool:
   ```bash
   zcash-devtool wallet -w ./creator-wallet init --name creator -i ./creator.age -n test
   zcash-devtool wallet -w ./creator-wallet sync
   zcash-devtool wallet -w ./creator-wallet list-accounts     # prints the account's UFVK (uviewtest1…)
   ```
   The age identity file encrypts the seed phrase. Keep it and the wallet directory private.
3. **Connect it in Zerveil:** go to Payments → Receiving account.
   - Paste the **UFVK** (`uviewtest1…`), never the seed phrase.
   - Enter the birthday height.
   - Read the authorization and tick consent.
4. **Each advertiser: create and fund a wallet.**
   - Create a wallet the same way.
   - Claim TAZ from the faucet (<https://zcash-faucet.vercel.app/>) yourself, to one of your own shielded addresses.
   - Shield any transparent funds before paying (`zcash-devtool wallet -w … shield`).
5. **Operator (Zerveil server):**
   ```
   PAYMENT_MODE=zcash
   ZCASH_NETWORK=testnet
   ZCASH_WALLET_BACKEND=zcash-devtool
   ZCASH_LIGHTWALLETD_URL=https://testnet.zec.rocks:443
   ZCASH_DEVTOOL_BIN=/path/to/zcash-devtool
   ZCASH_DEVTOOL_WALLET_DIR=/private/zerveil-scanner     # view-only: holds only imported viewing keys
   ZCASH_API_SELLER_UFVK=uviewtest1…                     # optional: the API seller's own receiving account
   ZCASH_API_SELLER_BIRTHDAY=<height>
   ```
   Start Zerveil. `/api/wallet/status` shows reachability, the chain tip and whether NU7 is active.
6. **Pay:**
   - Open the campaign's payment card.
   - Pay the ZIP-321 link or QR code from the advertiser's wallet, for example
     `zcash-devtool wallet -w ./advertiser-wallet pay -i ./advertiser.age --payment-uri 'zcash:…'`.
   - Watch the card go `seen → mined → confirming → confirmed`, then open the receipt.
7. **Real-testnet tests** (read-only):
   ```bash
   ZCASH_LIVE_DEVTOOL_BIN=… ZCASH_LIVE_WALLET_DIR=… ZCASH_LIVE_UFVK=uviewtest1… ZCASH_LIVE_BIRTHDAY=… \
   ZCASH_LIVE_EXPECT_TXID=<a txid you paid> uv run pytest tests/test_zcash_live.py -s
   ```

## Configuration

See [`.env.example`](.env.example). Key settings:

| Variable | Default | Meaning |
|---|---|---|
| `PAYMENT_MODE` | `simulated` | `simulated` or `zcash` |
| `ZCASH_NETWORK`, `ZCASH_TESTNET_ENABLED` | `testnet`, `true` | `zcash` mode is testnet-only |
| `ZCASH_WALLET_BACKEND` | `zcash-devtool` | the only real scanner backend |
| `ZCASH_LIGHTWALLETD_URL` | `https://testnet.zec.rocks:443` | lightwalletd server |
| `ZCASH_DEVTOOL_BIN`, `ZCASH_DEVTOOL_WALLET_DIR` | `zcash-devtool`, – | the scanner binary and its private view-only wallet |
| `ZCASH_PAYMENT_SCANNER_ENABLED` | true | the scheduler scans for payments |
| `ZCASH_CONFIRMATION_THRESHOLD` | 3 | confirmations before a payment counts as confirmed |
| `ZCASH_INVOICE_TTL_MINUTES` | 1440 | milestone invoice lifetime |
| `ZCASH_RECEIVER_TYPES` | `orchard,sapling` | receivers in invoice addresses |
| `ZCASH_API_SELLER_UFVK`, `_BIRTHDAY` | – | the API seller's receiving account (HTTP 402, API credit) |
| `ZCASH_EXPLORER_TX_URL`, `ZCASH_FAUCET_URL` | –, faucet | links in the UI |
| `ZCASH_WALLET_RPC_URL`, `ZCASH_WALLET_AUTH_TOKEN` | – | reserved; not used by the zcash-devtool backend |
| `ADMIN_EMAILS` | – | operators (read-only scanner and audit views) |
| `PREMIUM_SEARCH_PRICE` | 0.05 | price of the 402-gated search |

## Database and migrations

- The database is PostgreSQL via psycopg 3 (`DATABASE_URL`). Without it, Zerveil uses an in-memory store.
- Schema changes are versioned in `src/zerveil/store/migrations.py`. They are applied automatically at startup under
  an advisory lock, or by hand with `uv run zerveil migrate` (`--status` lists them).
- **Migration 3 (`direct_payments`) removes escrow while keeping the data:**
  - It creates `receiving_accounts`, `payment_receipts` and `deal_milestones`, and adds `invoices.recipient_id`.
  - It renames `payout_requests` to `legacy_escrow_payouts` (read-only history).
  - It moves deals left in escrow states to `ARCHIVED` with a reason, and releases their budget reservations.
  - It cancels open escrow invoices; their data is kept.
  - It maps old statuses:
    - `paid` → `confirmed`;
    - `late_payment` → `expired`;
    - `campaign_funding` → `milestone`;
    - campaign status `funding` → `awaiting_payment`.
  - **Money already in an old escrow account is not touched by the migration.** Its operator must return it from that
    wallet by hand.
- Migration 2 (`zcash_payments`) introduced invoices, credit, spend requests and the payment audit trail.

## Tests: mocked vs real

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

| Suite | Backend | What it proves |
|---|---|---|
| `test_e2e.py`, `test_payment_api.py`, `test_negotiation_*`, … | **MOCKED** (simulated scanner) | the whole app flow over HTTP: negotiation → per-creator invoices → scan → receipts → proofs → performance milestone; 402; links; admin auth; no secrets exposed |
| `test_payments.py` | **MOCKED** (simulated scanner) | invoice states (seen/mined/confirming/confirmed/underpaid/overpaid/expired), duplicate notes, scanner failure, receipts, credit and limits |
| `test_zcash.py` | **MOCKED** (`zcash-devtool` subprocess stubbed with canned output) | the commands we run, output parsing, NU7 status, key redaction; UA/UFVK checks, ZIP-321, memos, amounts; fail-closed config |
| `test_policy.py` | none | terms, signatures, payee and network checks, milestone checks, delegation |
| `test_store_postgres.py` | real PostgreSQL | store and migrations (needs `TEST_DATABASE_URL`, else skipped) |
| `test_zcash_live.py` | **REAL testnet** | lightwalletd past NU7, viewing-key import and fresh addresses, a real received payment (needs `ZCASH_LIVE_*`, else skipped) |

The canned `zcash-devtool` output is built from the print statements in its source. Only `test_zcash_live.py` proves
that the live binary answers that way.

## Deploy: Docker, CI/CD, Render

- **`Dockerfile`** builds the React app, then the Python app, and runs `zerveil serve` as a non-root user.
  **zcash-devtool is not in the image.** For `PAYMENT_MODE=zcash`, extend the image with the binary and mount a
  persistent private directory for `ZCASH_DEVTOOL_WALLET_DIR`.
- **`.github/workflows/ci.yml`:**
  1. Lint and tests (PostgreSQL service included).
  2. Frontend build.
  3. Docker build and a smoke run, which asserts `simulated` mode and testnet.
  4. Deploy to Render via a deploy hook on `main`.
- **`render.yaml`** defines one Docker web service. Secrets are entered in the dashboard (`sync: false`), never in git.
  Keep `PAYMENT_MODE=simulated` there unless the image includes zcash-devtool and a persistent disk.

## API reference (payments)

| Method & path | Who | Purpose |
|---|---|---|
| `PUT /api/me/zcash-address` | creator | set the creator's shielded Unified Address (named in signed terms) |
| `GET/PUT/DELETE /api/receiving-account` | creator (PUT) | connect a viewing key (`ufvk`, `birthday`, `consent`), show it, disconnect it |
| `POST /api/campaigns/{id}/decision` | advertiser | accept → one fixed-fee invoice per creator; decline → deals rejected |
| `GET /api/campaigns/{id}/payments` | advertiser | the campaign's invoices, a "pay all" ZIP-321 URI, open total, summary |
| `GET /api/pay/{id}` | public | payment page data (no txids, no campaign terms) |
| `GET /api/payments/invoices[/{id}]` | payer or recipient | invoices, detected notes, audit events |
| `POST /api/payments/invoices/{id}/cancel` | owner | cancel an untouched invoice (not milestone invoices) |
| `POST /api/payments/links` | creator | payment link, paid directly to the creator's account |
| `GET /api/receipts[/{id}]` | payer, recipient, admin | receipts |
| `GET /api/wallet/status` | user | scanner, network, tip, NU7, faucet, your address, receiving account |
| `GET/PUT /api/agent/spending-policy` | advertiser | agent spending limits |
| `GET /api/agent/credit`, `POST /api/agent/credit/topup` | advertiser | prepaid API credit (paid to the API seller) |
| `POST /api/agent/spend-requests/{id}` | advertiser | approve / reject an agent payment |
| `GET /api/influencers/premium-search` | advertiser | HTTP 402 |
| `POST /api/agent/premium-search` | advertiser | agent pays the 402 from credit |
| `GET /api/admin/scanner`, `/api/admin/payment-events`, `/api/admin/invoices` | admin | read-only scanner status and audit |
| `POST /api/dev/payments/{id}/simulate`, `POST /api/dev/advance` | user, simulated mode only | simulated payment, clock |

## Deep dive

### Deal state machine

Enforced by `models.DEAL_TRANSITIONS`; `DealService.save` refuses any other move.

```
NEGOTIATING → AGREED → AWAITING_APPROVAL → PAYMENT_REQUESTED → PAYMENT_PENDING → CONTENT_CREATION
 ├→ REJECTED     └→ BLOCKED (policy gate)       │  (CPM-only deals go straight to CONTENT_CREATION)
 └→ ESCALATED                                   └──(invoice expired, nothing seen)──→ PAYMENT_EXPIRED

CONTENT_CREATION → PROOF_SUBMITTED / VERIFIED → PERFORMANCE_DUE → COMPLETED (performance paid or waived)
        │                │        └→ DISPUTED
        │                └→ PROOF_REJECTED (resubmit before the deadline)
        └→ UNDELIVERED (no valid proof by the deadline)

ARCHIVED: deals left in escrow states by migration 3, or mid-payment under the older EVM flow
```

Creators are told to post once the fixed fee is confirmed (`CONTENT_CREATION`). Proofs are refused before then.

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

A read-only `zerveil` MCP server, served over streamable HTTP at `/mcp/` or over stdio. Its tools cover:
- campaigns, deals and milestones, and timelines;
- campaign spend: confirmed paid to creators, requested, and `held_by_zerveil: 0`;
- the influencer directory and profiles;
- channel and video statistics.

Signing, invoices and payments are deliberately **not** tools, and Zcash addresses are scrubbed from tool output.

### Project structure

```
src/zerveil/
  agents/            advertiser (LangGraph) and influencer (A2A) agents
  agent_policy/      deterministic negotiation engine + versioned profiles
  api/               FastAPI app (app.py) and payment routes (payments.py); static/ = built web app
  auth/              Google OAuth, sessions, Ed25519 agent keys and delegation
  compliance/        disclosure policies
  mcp_servers/       read-only MCP server
  payments/          invoices, receiving accounts, scanning, receipts, credit, links
  policy/            terms and milestone checks, terms signing
  services/          context (scanner selection), deals, registry
  store/             PostgreSQL + in-memory stores, migrations
  verifier/          YouTube checks, content agent
  zcash/             scanner protocol, zcash-devtool and simulated scanners, UA/UFVK, ZIP-321, memo, amounts
web/                 React + TypeScript + Vite frontend (plain CSS)
tests/               see "Tests: mocked vs real"
```

## Status and blockers

**Done:**
- **Escrow removed.**
  - Gone: the operator wallet, funding invoice, payout queue, refunds and custody view.
  - Gone: the `ZCASH_ESCROW_*` settings and the escrow routes, UI and tests.
  - Old data is preserved by migration 3.
- **Direct payments:**
  - per-creator milestone invoices and a "pay all" ZIP-321 request;
  - viewing-key receiving accounts;
  - scanner states and receipts;
  - payment links paid to the creator, and the 402 paid to the API seller.
- **zcash-devtool scanner backend**, with NU7 status reporting and fail-closed configuration.
- **Tests:** end to end with the simulated scanner, and the devtool backend against a stubbed subprocess.

**Not done / blockers:**
- **This project has not yet executed or observed a real testnet transaction, so there is no txid to show.** The build
  environment had no Rust toolchain (to build zcash-devtool), no Docker, and no testnet wallet or TAZ. To produce one,
  follow [Real testnet setup](#real-testnet-setup) and run `tests/test_zcash_live.py`.
- The `zcash-devtool` output parser is built from its source. It has not yet been checked against a live binary.
- Migration 3 has not been run against a real PostgreSQL database by this project, because `TEST_DATABASE_URL` was not
  available. Back up the database before the first start.
- Disconnecting a receiving account does not yet delete the key from the scanner's wallet directory. The operator
  must run `zcash-devtool wallet -w <dir> delete-account <uuid>`.
- Nothing protects against non-delivery after an upfront fixed fee. This is inherent to non-custodial payments.

## References

- Zcash Improvement Proposals (including NU7 deployment): https://zips.z.cash/
- ZIP-316 Unified Addresses and viewing keys: https://zips.z.cash/zip-0316
- ZIP-321 payment request URIs: https://zips.z.cash/zip-0321
- ZIP-317 proportional fees: https://zips.z.cash/zip-0317
- zcash-devtool: https://github.com/zcash/zcash-devtool
- Testnet lightwalletd: `testnet.zec.rocks:443`
- Testnet faucet: https://zcash-faucet.vercel.app/
- Zallet: https://zcash.github.io/zallet/
- A2A protocol: https://a2a-protocol.org/
- Model Context Protocol: https://modelcontextprotocol.io/
