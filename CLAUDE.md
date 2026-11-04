# Zerveil — Private Payments. Autonomous Agents.

Privacy-powered AI influencer agent marketplace: advertiser and creator agents negotiate sponsorships over A2A; advertisers pay creators **directly** with **shielded Zcash** (testnet, TAZ — no real monetary value) to the creator's own wallet. **Zerveil holds no funds: no escrow, operator wallet, payout queue or refunds.**

## Product flow
1. **Advertiser Agent** discovers **Influencer Agents** via A2A Agent Cards and negotiates (one creator at a time, queued).
2. The Influencer Agent returns terms signed with its **Ed25519 agent key** (deliverable, fixed fee, per-1K-view rate, cap, deadlines, proof rules, payee = the creator's shielded Unified Address, network `zcash-testnet`).
3. The policy gate re-checks the terms (`policy/engine.check_terms`) and reserves the cap from the budget → `AWAITING_APPROVAL`.
4. The advertiser accepts → one **fixed-fee milestone invoice per creator** on a fresh diversified address of that creator's connected receiving account → `PAYMENT_REQUESTED` → `PAYMENT_PENDING` (seen/mined/confirming) → `CONTENT_CREATION` only from scan data with enough confirmations (default 3). CPM-only deals skip straight to `CONTENT_CREATION`.
5. The verifier checks the post via the YouTube API → `VERIFIED` → `PERFORMANCE_DUE`; at T+7d the performance fee (views × CPM, ≤ cap − fixed, `policy.check_milestone`) is requested as a second invoice, or waived → `COMPLETED`.

Currency: **USD is the business currency** (budgets, agent floors/ceilings, offers, signed terms `currency="USD"`, milestones, API price, credit, limits, analytics, receipts). The **payment asset** is shielded ZEC (TAZ on testnet), converted once per invoice by `money.quote` (stored `usd_amount`, `usd_rate`, `rate_source`, `test_rate`; asset amount rounded up to the zatoshi). There is no USD stablecoin on Zcash: never label TAZ as USD or a stablecoin. Testnet uses `ZCASH_TEST_USD_RATE`, a labeled test rate; mainnet pricing is refused (no live price source). Legacy (pre-USD) records have `spec.currency="TAZ"` / `terms.currency=None` / `usd_amount=None` and are never relabeled as dollars or invoiced. Money is `Decimal`, never float.

Core rule: **the LLM proposes, deterministic code decides, the payer's wallet signs.** Zerveil has no spending key; never add one, never route or hold funds through an operator wallet, never describe anything as escrow.

## Layout
Python 3.13 project managed with uv; package in `src/zerveil/`.
- `services/deals.py` owns the deal lifecycle and milestones (`models.Milestone`: fixed | performance); `agents/advertiser/graph.py` only negotiates, gates and calls `deals.request_payment`.
- `zcash/`: `wallet.py` (`PaymentScanner` protocol: `import_viewing_key`, `new_address`, `received`, `info` — **no send**), `devtool.py` (`DevtoolScanner`: runs the `zcash-devtool` CLI against a view-only wallet dir + lightwalletd; output parsed fail-closed), `simulated.py` (`SimulatedScanner`, labels everything simulated), `address.py` (Bech32m UA + UFVK checks; refuses seeds/spending keys), `zip321.py` (incl. multi-recipient), `memo.py` (`ZRV1:<id>`), `amounts.py`. `payments/service.py`: receiving accounts, invoices, scanner, receipts, payment links, API credit/spending limits, 402.
- `services/context.build_wallet` picks the scanner from `PAYMENT_MODE` (`simulated` | `zcash`); `zcash` refuses to start without `ZCASH_DEVTOOL_WALLET_DIR`, the binary, backend `zcash-devtool`, and testnet. `escrow`/`hosted`/`sepolia` are rejected.
- **Receiving accounts**: a creator connects a Unified Full Viewing Key (`uviewtest1…`) + birthday with explicit consent (`PUT /api/receiving-account`). The key goes only to the scanner; the DB keeps a fingerprint. Creators without one are not listed. HTTP 402 and API credit pay the API seller's own account (`ZCASH_API_SELLER_UFVK`).
- **Payments are verified only from scan data** (notes on the invoice's own address, or its `ZRV1:` memo, claimed once via `store.claim("zcash_note", txid:pool:index)`). Never accept a user-submitted txid as proof; never treat unconfirmed as confirmed; a failed scan never expires or confirms anything. Invoices: `awaiting_payment → seen → mined → confirming → confirmed | overpaid`, `underpaid`, `expired`, `failed`, `cancelled`. A confirmed invoice issues one receipt (`receipt_hash`). Late payments are recorded only — Zerveil cannot refund.
- Negotiation decisions come from `agent_policy/engine.py` (deterministic; the LLM only suggests). Influencer floors never leave the creator's side. Disclosure policies live in `compliance/` and are snapshotted per campaign.
- Agent keys: Ed25519 (`auth/crypto.py`), encrypted with `SESSION_SECRET`; the creator authorizes theirs while signed in (server-MACed delegation statement binding email, channel, agent key and payout address). Signing never becomes an LLM/MCP tool.
- Privacy: a creator's address is visible only to that creator and operators (`deal_json` redacts it); the A2A agent card and directory never list it. Viewing keys never reach the DB, browser, logs or LLM; `/api/status` never exposes secrets or the wallet dir. See `PRIVACY.md`.
- HTTP 402: `GET /api/influencers/premium-search` returns a shielded invoice paid to the API seller; a retry with `X-Payment-Id` unlocks one request once confirmed (`payments.consume`). Agents pay from prepaid API credit (topped up only via confirmed shielded invoices) within the owner's `SpendingPolicy`.
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
- Crypto: `cryptography` (Ed25519 agent keys). Zcash: `zcash-devtool` (main, NU7 cohort) as a subprocess — `wallet -w DIR init-fvk|import-ufvk|list-accounts|generate-address|sync|get-info|list-tx`, server `testnet.zec.rocks:443`. NU7 testnet activation 4,465,026; Orchard outputs land in Ironwood. Do not invent CLI subcommands, flags or output fields; check https://github.com/zcash/zcash-devtool source first. Zallet was not selected (its `z_importviewingkey` takes Sapling keys only).

## Commands
- `uv sync` (set `UV_LINK_MODE=copy` on Windows) · `uv add <pkg>` then regenerate the requirements files:
  `uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o requirements.txt`
  `uv export --format requirements-txt --no-hashes --only-dev --no-emit-project -o requirements-dev.txt`
- `cd web && npm install && npm run build` then `uv run zerveil serve` (http://localhost:8000) · `uv run zerveil mcp` · `uv run zerveil migrate`
- `uv run pytest` · `uv run ruff check .` · `uv run ruff format .` — `TEST_DATABASE_URL` runs the PostgreSQL store tests; `ZCASH_LIVE_DEVTOOL_BIN` + `ZCASH_LIVE_WALLET_DIR` (+ `ZCASH_LIVE_*`) run the read-only real-testnet tests.
- Windows: psycopg async needs a SelectorEventLoop; the CLI and tests set it. Set `PYTHONUTF8=1` for scripts that write files.
- `pyproject.toml` + `uv.lock` are the source of truth; `requirements*.txt` are generated — never edit them by hand.

## Conventions
- Python ≥ 3.13, type hints throughout, pydantic v2 models, pytest (+ pytest-asyncio). Tests that use the simulated scanner or a stubbed zcash-devtool are labeled MOCKED; never present simulated payments as real.
- Never commit `.env`, `keys/`, or `*.pem`. Never print viewing keys, `DATABASE_URL` or other secrets. Never automate faucet claims.
