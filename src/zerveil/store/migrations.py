"""Versioned PostgreSQL migrations. Applied in order at startup, each once, inside one transaction, under an
advisory lock so two instances starting together cannot race. Never edit a migration that shipped: add the next one.

    uv run zerveil migrate            apply pending migrations and exit
    uv run zerveil migrate --status   list applied and pending migrations
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


# 001: the tables Zerveil inherited (users, campaigns, deals, negotiation, feedback, audit). Written with IF NOT EXISTS
# so databases created before migrations existed adopt it unchanged.
BASELINE = """
CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS deals (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    state       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS deals_campaign_idx ON deals (campaign_id);
CREATE TABLE IF NOT EXISTS audit_events (
    id          BIGSERIAL PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    deal_id     TEXT,
    at          TIMESTAMPTZ NOT NULL,
    actor       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_campaign_idx ON audit_events (campaign_id, id);
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    owner       TEXT NOT NULL,
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS budget_reservations (
    deal_id     TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    amount      NUMERIC(20, 6) NOT NULL,
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS budget_campaign_idx ON budget_reservations (campaign_id);
CREATE TABLE IF NOT EXISTS versioned_docs (
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    version     INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    data        JSONB NOT NULL,
    PRIMARY KEY (kind, key, version)
);
CREATE TABLE IF NOT EXISTS negotiation_decisions (
    id          TEXT PRIMARY KEY,
    deal_id     TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    party       TEXT NOT NULL,
    at          TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_deal_idx ON negotiation_decisions (deal_id, at);
CREATE TABLE IF NOT EXISTS negotiation_messages (
    deal_id     TEXT NOT NULL,
    event_id    INTEGER NOT NULL,
    campaign_id TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    agent_role  TEXT NOT NULL,
    message_id  TEXT,
    at          TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL,
    PRIMARY KEY (deal_id, event_id)
);
CREATE TABLE IF NOT EXISTS negotiation_rounds (
    deal_id     TEXT NOT NULL,
    round       INTEGER NOT NULL,
    campaign_id TEXT NOT NULL,
    at          TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL,
    PRIMARY KEY (deal_id, round)
);
CREATE TABLE IF NOT EXISTS negotiation_sessions (
    negotiation_id TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL,
    creator_id     TEXT NOT NULL,
    position       INTEGER NOT NULL,
    status         TEXT NOT NULL,
    data           JSONB NOT NULL,
    UNIQUE (campaign_id, creator_id)
);
CREATE TABLE IF NOT EXISTS negotiation_feedback (
    id          TEXT PRIMARY KEY,
    deal_id     TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    party       TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL,
    UNIQUE (deal_id, party)
);
CREATE INDEX IF NOT EXISTS feedback_user_idx ON negotiation_feedback (user_id, created_at);
CREATE TABLE IF NOT EXISTS agent_performance (
    deal_id       TEXT PRIMARY KEY,
    campaign_id   TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    influencer_id TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    ended_at      TIMESTAMPTZ,
    data          JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS performance_advertiser_idx ON agent_performance (advertiser_id);
CREATE INDEX IF NOT EXISTS performance_influencer_idx ON agent_performance (influencer_id);
CREATE TABLE IF NOT EXISTS agent_improvement_examples (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    role        TEXT NOT NULL,
    status      TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS lessons_owner_idx ON agent_improvement_examples (owner_id, role, status);
"""

# 002: Zcash payments. Deals from the former EVM payment flow cannot be paid out in ZEC: any deal still in one of that
# flow's payment states is archived (its JSON keeps the full history), never silently moved into a Zcash state.
# Linked EVM wallets are dropped: creators enter a Zcash Unified Address and switch their agent on again.
ZCASH_PAYMENTS = """
CREATE TABLE IF NOT EXISTS invoices (
    id          TEXT PRIMARY KEY,
    purpose     TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    campaign_id TEXT,
    address     TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS invoices_status_idx ON invoices (status);
CREATE INDEX IF NOT EXISTS invoices_owner_idx ON invoices (owner_id, created_at);
CREATE INDEX IF NOT EXISTS invoices_campaign_idx ON invoices (campaign_id);
CREATE TABLE IF NOT EXISTS payout_requests (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL,
    deal_id        TEXT,
    campaign_id    TEXT,
    beneficiary_id TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL,
    data           JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS payouts_status_idx ON payout_requests (status);
CREATE INDEX IF NOT EXISTS payouts_deal_idx ON payout_requests (deal_id);
CREATE INDEX IF NOT EXISTS payouts_beneficiary_idx ON payout_requests (beneficiary_id);
CREATE TABLE IF NOT EXISTS agent_credit_ledger (
    id        TEXT PRIMARY KEY,
    user_id   TEXT NOT NULL,
    amount    NUMERIC(20, 8) NOT NULL,
    kind      TEXT NOT NULL,
    ref       TEXT NOT NULL,
    at        TIMESTAMPTZ NOT NULL,
    data      JSONB NOT NULL,
    UNIQUE (ref, kind)
);
CREATE INDEX IF NOT EXISTS credit_user_idx ON agent_credit_ledger (user_id, at);
CREATE TABLE IF NOT EXISTS agent_spend_requests (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    data       JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS spend_requests_user_idx ON agent_spend_requests (user_id, status);
CREATE TABLE IF NOT EXISTS payment_events (
    id         BIGSERIAL PRIMARY KEY,
    invoice_id TEXT,
    payout_id  TEXT,
    at         TIMESTAMPTZ NOT NULL,
    kind       TEXT NOT NULL,
    detail     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS payment_events_invoice_idx ON payment_events (invoice_id, id);
CREATE INDEX IF NOT EXISTS payment_events_payout_idx ON payment_events (payout_id, id);
ALTER TABLE budget_reservations ALTER COLUMN amount TYPE NUMERIC(20, 8);
DROP TABLE IF EXISTS webhook_receipts;
UPDATE deals
   SET state = 'ARCHIVED',
       data = jsonb_set(data, '{state}', '"ARCHIVED"') || jsonb_build_object('archived_reason', 'closed by the migration to Zcash payments')
 WHERE state IN ('CART_CREATED', 'AWAITING_APPROVAL', 'AWAITING_CHECKOUT', 'SIGNED', 'FUNDING', 'PAYMENT_UNKNOWN', 'PAYMENT_FAILED',
                 'FUNDED', 'PROOF_SUBMITTED', 'PROOF_REJECTED', 'VERIFIED', 'FIXED_RELEASED', 'DISPUTED')
   AND NOT (data ? 'funding_invoice_id');
DELETE FROM budget_reservations WHERE deal_id IN (SELECT id FROM deals WHERE state = 'ARCHIVED');
UPDATE users SET data = (data - 'wallet' - 'delegation' - 'delegation_signature') || jsonb_build_object('agent_active', false)
 WHERE data ? 'wallet' AND NOT (data ? 'zcash_address');
"""

MIGRATIONS: list[Migration] = [
    Migration(1, "baseline", BASELINE),
    Migration(2, "zcash_payments", ZCASH_PAYMENTS),
]

LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
LOCK_KEY = 7_401_920_355  # pg_advisory_xact_lock key shared by every Zerveil instance


async def migrate(conn: Any) -> list[int]:
    """Applies pending migrations (psycopg AsyncConnection); returns the versions applied now."""
    applied_now: list[int] = []
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        await conn.execute(LEDGER)
        done = {r[0] for r in await (await conn.execute("SELECT version FROM schema_migrations")).fetchall()}
        for m in MIGRATIONS:
            if m.version in done:
                continue
            await conn.execute(m.sql)
            await conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (m.version, m.name))
            applied_now.append(m.version)
    return applied_now


async def migration_status(conn: Any) -> list[tuple[int, str, bool]]:
    await conn.execute(LEDGER)
    done = {r[0] for r in await (await conn.execute("SELECT version FROM schema_migrations")).fetchall()}
    return [(m.version, m.name, m.version in done) for m in MIGRATIONS]
