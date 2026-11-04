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

# 003: direct payments, no escrow. Zerveil stops holding funds: payers pay the creator's (or API seller's) own wallet,
# and a view-only scanner confirms receipt. Nothing is deleted:
# - payout_requests (the escrow payout queue) is renamed legacy_escrow_payouts and kept as read-only history;
# - deals still inside the escrow flow are archived with a reason (their money is in the operator's escrow wallet and
#   must be settled by the operator outside Zerveil), and their budget reservations are released;
# - open escrow invoices (campaign funding, top-ups and links paid to the escrow wallet) are cancelled; paid ones keep
#   their history under the new status names (paid → confirmed, pending → awaiting_payment, late_payment → expired).
DIRECT_PAYMENTS = """
CREATE TABLE IF NOT EXISTS receiving_accounts (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    role          TEXT NOT NULL,
    authorized_at TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ,
    data          JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS receiving_accounts_owner_idx ON receiving_accounts (owner_id, authorized_at);
CREATE TABLE IF NOT EXISTS payment_receipts (
    id           TEXT PRIMARY KEY,
    invoice_id   TEXT NOT NULL UNIQUE,
    payer_id     TEXT NOT NULL DEFAULT '',
    recipient_id TEXT NOT NULL,
    issued_at    TIMESTAMPTZ NOT NULL,
    data         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS receipts_payer_idx ON payment_receipts (payer_id, issued_at);
CREATE INDEX IF NOT EXISTS receipts_recipient_idx ON payment_receipts (recipient_id, issued_at);
CREATE TABLE IF NOT EXISTS deal_milestones (
    id         TEXT PRIMARY KEY,
    deal_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    invoice_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS deal_milestones_deal_idx ON deal_milestones (deal_id);
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS recipient_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS invoices_recipient_idx ON invoices (recipient_id, created_at);
ALTER TABLE IF EXISTS payout_requests RENAME TO legacy_escrow_payouts;
UPDATE deals
   SET state = 'ARCHIVED',
       data = jsonb_set(data, '{state}', '"ARCHIVED"')
              || jsonb_build_object('archived_reason', 'closed by the move to direct payments (escrow state ' || state || '); '
                                    || 'any funds still in the escrow wallet are settled by the operator')
 WHERE state IN ('AWAITING_FUNDING', 'FUNDING', 'FUNDED', 'PROOF_SUBMITTED', 'PROOF_REJECTED', 'VERIFIED', 'FIXED_RELEASED',
                 'DISPUTED', 'EXPIRED_REFUNDED');
DELETE FROM budget_reservations WHERE deal_id IN (SELECT id FROM deals WHERE state = 'ARCHIVED');
UPDATE invoices SET status = 'cancelled', data = jsonb_set(data, '{status}', '"cancelled"')
 WHERE status IN ('pending', 'underpaid', 'confirming');
UPDATE invoices SET status = 'confirmed', data = jsonb_set(data, '{status}', '"confirmed"') WHERE status = 'paid';
UPDATE invoices SET status = 'expired', data = jsonb_set(data, '{status}', '"expired"') WHERE status = 'late_payment';
UPDATE invoices SET purpose = 'milestone', data = jsonb_set(data, '{purpose}', '"milestone"') WHERE purpose = 'campaign_funding';
UPDATE campaigns SET data = jsonb_set(data, '{status}', '"awaiting_payment"') WHERE data->>'status' = 'funding';
"""

# 004: USD is the business currency. Amounts recorded before this were TAZ test units with no USD value, so they are
#   never relabeled as dollars. Nothing is deleted:
#   - existing campaigns are stamped spec.currency = "TAZ" and, unless already finished, closed (status failed, with a
#     reason; the negotiation queue stops), so no TAZ budget keeps running as if it were USD;
#   - their deals that could still move are archived with a reason and their budget reservations released;
#   - unpaid invoices priced in TAZ (awaiting payment / underpaid) are cancelled; ones already seen keep confirming;
#   - prepaid API credit held in TAZ is closed with one `legacy_reset` ledger entry per user (credit is USD now).
USD_PRICING = """
UPDATE campaigns SET data = jsonb_set(data, '{spec,currency}', '"TAZ"') WHERE data->'spec'->>'currency' IS NULL;
UPDATE campaigns
   SET data = data || jsonb_build_object('status', 'failed', 'negotiation_cancelled', true,
                                         'error', 'Closed when Zerveil moved to USD pricing.')
 WHERE data->'spec'->>'currency' = 'TAZ' AND data->>'status' NOT IN ('done', 'failed');
UPDATE deals
   SET state = 'ARCHIVED',
       data = jsonb_set(data, '{state}', '"ARCHIVED"')
              || jsonb_build_object('archived_reason', 'closed by the move to USD pricing (was ' || state || '); amounts were TAZ test units')
 WHERE state NOT IN ('REJECTED', 'ARCHIVED', 'COMPLETED', 'UNDELIVERED', 'BLOCKED', 'PAYMENT_EXPIRED')
   AND campaign_id IN (SELECT id FROM campaigns WHERE data->'spec'->>'currency' = 'TAZ');
DELETE FROM budget_reservations WHERE deal_id IN (SELECT id FROM deals WHERE state = 'ARCHIVED');
UPDATE invoices SET status = 'cancelled', data = jsonb_set(data, '{status}', '"cancelled"')
 WHERE data->>'usd_amount' IS NULL AND status IN ('awaiting_payment', 'underpaid');
INSERT INTO agent_credit_ledger (id, user_id, amount, kind, ref, at, data)
SELECT 'CR-usd-' || user_id, user_id, -SUM(amount), 'legacy_reset', 'usd-pricing:' || user_id, now(),
       jsonb_build_object('id', 'CR-usd-' || user_id, 'user_id', user_id, 'amount', (-SUM(amount))::text, 'kind', 'legacy_reset',
                          'ref', 'usd-pricing:' || user_id, 'category', 'TAZ credit closed by the move to USD pricing',
                          'simulated', false, 'at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
  FROM agent_credit_ledger GROUP BY user_id HAVING SUM(amount) <> 0
ON CONFLICT DO NOTHING;
"""

MIGRATIONS: list[Migration] = [
    Migration(1, "baseline", BASELINE),
    Migration(2, "zcash_payments", ZCASH_PAYMENTS),
    Migration(3, "direct_payments", DIRECT_PAYMENTS),
    Migration(4, "usd_pricing", USD_PRICING),
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
