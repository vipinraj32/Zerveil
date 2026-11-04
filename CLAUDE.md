# Zerveil — Private Payments. Autonomous Agents.

Privacy-powered AI influencer agent marketplace: advertiser and creator agents negotiate sponsorships over A2A; campaigns are funded and creators paid with **shielded Zcash** (testnet, TAZ) through a **backend-managed escrow wallet account** with operator-approved payouts.

## Product flow
1. **Advertiser Agent** discovers **Influencer Agents** via A2A Agent Cards and negotiates (one creator at a time, queued).
2. The Influencer Agent returns terms signed with its **Ed25519 agent key** (deliverable, fixed fee, per-1K-view rate, cap, deadlines, proof rules, payee = the creator's shielded Unified Address, network `zcash-testnet`).
3. The policy gate re-checks the terms (`policy/engine.check_terms`) and reserves the cap from the budget → `AWAITING_APPROVAL`.
4. The advertiser approves → **one shielded funding invoice** (sum of caps + `ZCASH_FEE_RESERVE_PER_DEAL` per deal) on a fresh Unified Address of the escrow account → `AWAITING_FUNDING` → `FUNDING` (seen, confirming) → `FUNDED` only from wallet scan data with enough confirmations.
5. The verifier checks the post via the YouTube API; the fixed fee, then the performance fee at T+7d, are decided and **queued as payout requests**; an operator (`ADMIN_EMAILS`) approves; the wallet sends shielded payouts; the remainder is refunded to the advertiser's refund address.

Core rule: **the LLM proposes, deterministic code decides, the wallet signs only after checks and approval.** No payment path may depend on LLM output alone. The escrow is a wallet account the operator controls — never describe it as trustless or as a smart contract.

## Layout
Python 3.13 project managed with uv; package in `src/zerveil/`.
- `services/deals.py` owns the deal lifecycle; `agents/advertiser/graph.py` only negotiates, gates and requests funding through it.
- `zcash/`: `rpc.py` (Zallet/zcashd JSON-RPC wallet), `simulated.py` (in-process stand-in, labels everything simulated), `address.py` (Bech32m Unified Address checks), `zip321.py`, `memo.py` (`ZRV1:<id>`), `amounts.py`. `payments/service.py`: invoices, scanner, payout queue, agent credit/spending limits, payment links, custody view.
- `services/context.build_wallet` picks the wallet from `PAYMENT_MODE` (`simulated` | `zcash`); `zcash` refuses to start without `ZCASH_WALLET_RPC_URL`, `ZCASH_ESCROW_ACCOUNT`, `ZCASH_ESCROW_FROM_ADDRESS`. `hosted`/`sepolia` are rejected (the HSP/EVM flow was removed).
- **Payments are verified only from wallet scan data** (`z_listunspent` notes on the invoice's own address, claimed once via `store.claim("zcash_note", txid:pool:index)`). Never accept a user-submitted txid as proof. Invoices: `pending → underpaid | confirming → paid`, `expired`, `late_payment` (refunded, needs approval), `cancelled`. Refunds of unusable money cover only what earlier refunds did not.
- **Payout queue** (`PayoutRequest`): `pending_approval → approved → sending → submitted → sent → confirmed`, or `failed` / `rejected` / `unknown`. Every status change is compare-and-set (`save_payout(expect_status=…)`); idempotent per key (`claim("payout", "<deal>:<kind>")`). A lost send outcome becomes `unknown` and is reconciled from the wallet's outgoing memos — **never resent automatically**; only an operator's `resolve` can resend.
- Negotiation decisions come from `agent_policy/engine.py` (deterministic; the LLM only suggests). Influencer floors never leave the creator's side. Disclosure policies live in `compliance/` and are snapshotted per campaign.
- Agent keys: Ed25519 (`auth/crypto.py`), encrypted with `SESSION_SECRET`; the creator authorizes theirs while signed in (server-MACed delegation statement binding email, channel, agent key and payout address). Signing never becomes an LLM/MCP tool.
- Privacy: a creator's payout address is visible only to that creator and operators (`deal_json` redacts it); the A2A agent card and directory never list it. `/api/status` never exposes the wallet RPC URL or credentials.
- HTTP 402: `GET /api/influencers/premium-search` returns a shielded invoice; a retry with `X-Payment-Id` unlocks one request once paid (`payments.consume`). Agents pay from prepaid credit (topped up only via verified shielded invoices) within the owner's `SpendingPolicy`.
- No demo fixtures: users sign in with Google (`auth/`); influencers come from the user store (`services/registry.py`). `FixtureVideoSource` is for tests only.
- Frontend: `web/` (React + TypeScript + Vite, plain CSS). `npm run build` writes to `src/zerveil/api/static` (gitignored); `npm run dev` on :5173 proxies the API on :8000. Live updates from `/api/stream` (SSE, filtered per user).
- State changes follow `models.DEAL_TRANSITIONS`. Budget caps are reserved atomically (`store.reserve_budget`).
- Creator queue: `discover` queues one `negotiation_sessions` row per creator; `negotiate_next` runs one creator per checkpointed step. Status changes are compare-and-set; a session found RUNNING on resume is closed as FAILED, never re-run. `CampaignRunner.recover()` resumes unfinished campaigns at startup.
- Database schema changes go in `store/migrations.py` (versioned, applied at connect under an advisory lock; `uv run zerveil migrate [--status]`).

## Stack
- Agents: LangChain 1.x, LangGraph, LangSmith tracing (`LANGSMITH_*`), `langchain-anthropic`.
- Tools: MCP Python SDK (`FastMCP`) + `langchain-mcp-adapters`.
- A2A: `a2a-sdk` 1.x — mount routes with `a2a.server.routes.fastapi_routes.add_a2a_routes_to_fastapi`, implement `AgentExecutor`.
- HTTP/API: FastAPI + uvicorn, httpx (tests mock with respx / `httpx.MockTransport`).
- Database: PostgreSQL via psycopg 3, env `DATABASE_URL`; LangGraph state via `langgraph.checkpoint.postgres`.
- Crypto: `cryptography` (Ed25519 agent keys). Zcash: Zallet JSON-RPC (zcashd-compatible methods `z_getaddressforaccount`, `z_listunspent`, `z_sendmany`, `z_getoperationstatus`, `z_viewtransaction`, `z_listunifiedreceivers`, `z_getbalanceforaccount`). Do not invent RPC methods or fields; check https://zcash.github.io/zallet/ first.

## Commands
- `uv sync` (set `UV_LINK_MODE=copy` on Windows) · `uv add <pkg>` then regenerate the requirements files:
  `uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o requirements.txt`
  `uv export --format requirements-txt --no-hashes --only-dev --no-emit-project -o requirements-dev.txt`
- `cd web && npm install && npm run build` then `uv run zerveil serve` (http://localhost:8000) · `uv run zerveil mcp` · `uv run zerveil migrate`
- `uv run pytest` · `uv run ruff check .` · `uv run ruff format .` — `TEST_DATABASE_URL` runs the PostgreSQL store tests; `ZCASH_LIVE_RPC_URL` (+ `ZCASH_LIVE_*`) runs the real-testnet wallet tests.
- Windows: psycopg async needs a SelectorEventLoop; the CLI and tests set it. Set `PYTHONUTF8=1` for scripts that write files.
- `pyproject.toml` + `uv.lock` are the source of truth; `requirements*.txt` are generated — never edit them by hand.

## Conventions
- Python ≥ 3.13, type hints throughout, pydantic v2 models, pytest (+ pytest-asyncio). Tests that use the simulated wallet or a mocked RPC are labeled as such; never present simulated payments as real.
- Never commit `.env`, `keys/`, or `*.pem`. Never print wallet RPC credentials or `DATABASE_URL`.
