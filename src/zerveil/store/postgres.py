"""PostgreSQL store. Documents are JSONB (the pydantic models), with indexed columns for lookups."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from ..models import AuditEvent, Campaign, Deal, User
from ..payments.models import CreditEntry, Invoice, PaymentEvent, Receipt, ReceivingAccount, SpendRequest
from .migrations import migrate

log = logging.getLogger(__name__)


def _load_deals(rows: list[Any]) -> list[Deal]:
    """Deals that no longer validate (data from an older version) are logged and skipped, so one old record can't stop
    the scheduler or a listing. They stay in the table untouched."""
    deals = []
    for (data,) in rows:
        try:
            deals.append(Deal.model_validate(data))
        except ValueError as e:
            log.error("deal %s could not be read and is skipped: %s", data.get("id", "?") if isinstance(data, dict) else "?", e)
    return deals


# Hosted PostgreSQL (Render and similar) drops idle connections without telling the client. The pool checks each
# connection before lending it out, retires idle ones before the server's own cut-off, and keeps sockets alive, so a
# dead connection is replaced instead of failing the next query.
KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3, "connect_timeout": 10}


OPEN_TIMEOUT = 15  # seconds per attempt
OPEN_ATTEMPTS = 4  # a database that is waking up or briefly unreachable at startup gets about a minute and a half


class DatabaseUnavailable(RuntimeError):
    pass


async def open_pool(database_url: str, *, max_size: int = 10, **kwargs: Any) -> AsyncConnectionPool:
    for attempt in range(1, OPEN_ATTEMPTS + 1):
        pool = AsyncConnectionPool(
            database_url,
            min_size=1,
            max_size=max_size,
            open=False,
            check=AsyncConnectionPool.check_connection,
            max_idle=120,
            max_lifetime=30 * 60,
            reconnect_timeout=60,
            kwargs={**KEEPALIVE, **kwargs},
        )
        try:
            await pool.open(wait=True, timeout=OPEN_TIMEOUT)
            return pool
        except PoolTimeout:
            await pool.close(timeout=1)  # stop its connect workers before trying again
            if attempt == OPEN_ATTEMPTS:
                break
            log.warning("database not reachable yet (attempt %s of %s), retrying in %ss", attempt, OPEN_ATTEMPTS, 2**attempt)
            await asyncio.sleep(2**attempt)
    # never echo the URL: it holds the password
    raise DatabaseUnavailable(
        f"could not connect to PostgreSQL after {OPEN_ATTEMPTS} attempts. Check that the database is running "
        "(hosted free tiers can be suspended or expired) and that DATABASE_URL in .env is correct, then start again."
    )


class PostgresStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> PostgresStore:
        store = cls(await open_pool(database_url, max_size=10))
        async with store.pool.connection() as conn:
            applied = await migrate(conn)
        if applied:
            log.info("applied database migrations %s", applied)
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def save_campaign(self, campaign: Campaign) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO campaigns (id, created_at, data) VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (campaign.spec.id, campaign.created_at, Jsonb(campaign.model_dump(mode="json"))),
            )

    async def get_campaign(self, campaign_id: str) -> Campaign | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM campaigns WHERE id = %s", (campaign_id,))).fetchone()
        return Campaign.model_validate(row[0]) if row else None

    async def list_campaigns(self) -> list[Campaign]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM campaigns ORDER BY created_at DESC")).fetchall()
        return [Campaign.model_validate(r[0]) for r in rows]

    async def save_deal(self, deal: Deal) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO deals (id, campaign_id, state, data) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, data = EXCLUDED.data, updated_at = now()",
                (deal.id, deal.campaign_id, deal.state.value, Jsonb(deal.model_dump(mode="json"))),
            )
            for m in deal.milestones:  # an index of the milestones held in the deal document
                await conn.execute(
                    "INSERT INTO deal_milestones (id, deal_id, kind, status, invoice_id) VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, invoice_id = EXCLUDED.invoice_id, updated_at = now()",
                    (m.id, deal.id, m.kind, m.status, m.invoice_id),
                )

    async def get_deal(self, deal_id: str) -> Deal | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM deals WHERE id = %s", (deal_id,))).fetchone()
        return Deal.model_validate(row[0]) if row else None

    async def list_deals(self, campaign_id: str | None = None) -> list[Deal]:
        async with self.pool.connection() as conn:
            if campaign_id is None:
                cur = await conn.execute("SELECT data FROM deals ORDER BY id")
            else:
                cur = await conn.execute("SELECT data FROM deals WHERE campaign_id = %s ORDER BY id", (campaign_id,))
            rows = await cur.fetchall()
        return _load_deals(rows)

    async def add_event(self, event: AuditEvent) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO audit_events (campaign_id, deal_id, at, actor, kind, detail) VALUES (%s, %s, %s, %s, %s, %s)",
                (event.campaign_id, event.deal_id, event.at, event.actor, event.kind, Jsonb(event.detail)),
            )

    async def list_events(self, campaign_id: str, deal_id: str | None = None) -> list[AuditEvent]:
        sql = "SELECT campaign_id, deal_id, at, actor, kind, detail FROM audit_events WHERE campaign_id = %s"
        params: tuple = (campaign_id,)
        if deal_id is not None:
            sql += " AND deal_id = %s"
            params += (deal_id,)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY id", params)).fetchall()
        return [AuditEvent(campaign_id=r[0], deal_id=r[1], at=r[2], actor=r[3], kind=r[4], detail=r[5]) for r in rows]

    async def save_user(self, user: User) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO users (id, email, role, created_at, data) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, role = EXCLUDED.role, data = EXCLUDED.data",
                (user.id, user.email, user.role, user.created_at, Jsonb(user.model_dump(mode="json"))),
            )

    async def create_user(self, user: User) -> User:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO users (id, email, role, created_at, data) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
                (user.id, user.email, user.role, user.created_at, Jsonb(user.model_dump(mode="json"))),
            )
            row = await (await conn.execute("SELECT data FROM users WHERE email = %s", (user.email,))).fetchone()
        assert row is not None
        return User.model_validate(row[0])

    async def get_user(self, user_id: str) -> User | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM users WHERE id = %s", (user_id,))).fetchone()
        return User.model_validate(row[0]) if row else None

    async def get_user_by_email(self, email: str) -> User | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM users WHERE email = %s", (email,))).fetchone()
        return User.model_validate(row[0]) if row else None

    async def list_users(self, role: str | None = None) -> list[User]:
        async with self.pool.connection() as conn:
            if role is None:
                cur = await conn.execute("SELECT data FROM users ORDER BY created_at")
            else:
                cur = await conn.execute("SELECT data FROM users WHERE role = %s ORDER BY created_at", (role,))
            rows = await cur.fetchall()
        return [User.model_validate(r[0]) for r in rows]

    async def claim(self, kind: str, key: str, owner: str) -> str:
        async with self.pool.connection() as conn:
            await conn.execute("INSERT INTO claims (kind, key, owner) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (kind, key.lower(), owner))
            row = await (await conn.execute("SELECT owner FROM claims WHERE kind = %s AND key = %s", (kind, key.lower()))).fetchone()
        assert row is not None
        return row[0]

    async def reserve_budget(self, campaign_id: str, deal_id: str, amount: Decimal, budget: Decimal) -> bool:
        async with self.pool.connection() as conn, conn.transaction():
            # Row lock on the campaign serializes concurrent reservations across processes
            await conn.execute("SELECT 1 FROM campaigns WHERE id = %s FOR UPDATE", (campaign_id,))
            row = await (await conn.execute("SELECT amount FROM budget_reservations WHERE deal_id = %s", (deal_id,))).fetchone()
            if row is not None:
                return Decimal(row[0]) == amount
            used = await (await conn.execute("SELECT COALESCE(SUM(amount), 0) FROM budget_reservations WHERE campaign_id = %s", (campaign_id,))).fetchone()
            assert used is not None
            if Decimal(used[0]) + amount > budget:
                return False
            await conn.execute("INSERT INTO budget_reservations (deal_id, campaign_id, amount) VALUES (%s, %s, %s)", (deal_id, campaign_id, amount))
            return True

    async def release_budget(self, deal_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM budget_reservations WHERE deal_id = %s", (deal_id,))

    async def reserved_budget(self, campaign_id: str, exclude: str | None = None) -> Decimal:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM budget_reservations WHERE campaign_id = %s AND deal_id <> %s", (campaign_id, exclude or "")
            )).fetchone()  # fmt: skip
        assert row is not None
        return Decimal(row[0])

    async def save_version(self, kind: str, key: str, doc: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.connection() as conn, conn.transaction():
            # Serializes writers of the same document so version numbers never collide
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{kind}:{key}",))
            row = await (await conn.execute("SELECT COALESCE(MAX(version), 0) FROM versioned_docs WHERE kind = %s AND key = %s", (kind, key))).fetchone()
            assert row is not None
            saved = {**doc, "version": row[0] + 1, "updated_at": datetime.now(UTC).isoformat()}
            await conn.execute("INSERT INTO versioned_docs (kind, key, version, data) VALUES (%s, %s, %s, %s)", (kind, key, saved["version"], Jsonb(saved)))
        return saved

    async def get_version(self, kind: str, key: str, version: int | None = None) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            if version is None:
                cur = await conn.execute("SELECT data FROM versioned_docs WHERE kind = %s AND key = %s ORDER BY version DESC LIMIT 1", (kind, key))
            else:
                cur = await conn.execute("SELECT data FROM versioned_docs WHERE kind = %s AND key = %s AND version = %s", (kind, key, version))
            row = await cur.fetchone()
        return row[0] if row else None

    async def list_versions(self, kind: str, key: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM versioned_docs WHERE kind = %s AND key = %s ORDER BY version", (kind, key))).fetchall()
        return [r[0] for r in rows]

    async def add_decision(self, record: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO negotiation_decisions (id, deal_id, campaign_id, party, at, data) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (record["id"], record["deal_id"], record["campaign_id"], record["party"], record["at"], Jsonb(record)),
            )

    async def list_decisions(self, deal_id: str, party: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT data FROM negotiation_decisions WHERE deal_id = %s", [deal_id]
        if party is not None:
            sql += " AND party = %s"
            params.append(party)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY at, id", params)).fetchall()
        return [r[0] for r in rows]

    async def add_negotiation_event(self, event: dict[str, Any]) -> dict[str, Any]:
        deal_id = event["negotiation_id"]
        async with self.pool.connection() as conn, conn.transaction():
            # One writer per deal at a time, so event ids are gap-free and ordered
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"negotiation:{deal_id}",))
            row = await (await conn.execute("SELECT COALESCE(MAX(event_id), 0) FROM negotiation_messages WHERE deal_id = %s", (deal_id,))).fetchone()
            assert row is not None
            saved = {**event, "event_id": row[0] + 1}
            await conn.execute(
                "INSERT INTO negotiation_messages (deal_id, event_id, campaign_id, event_type, agent_role, message_id, at, data) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (deal_id, saved["event_id"], saved["campaign_id"], saved["event_type"], saved["agent_role"], saved.get("message_id"),
                 saved["timestamp"], Jsonb(saved)),
            )  # fmt: skip
        return saved

    async def list_negotiation_events(self, deal_id: str, after: int = 0) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT data FROM negotiation_messages WHERE deal_id = %s AND event_id > %s ORDER BY event_id", (deal_id, after)
            )).fetchall()  # fmt: skip
        return [r[0] for r in rows]

    async def queue_negotiations(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        async with self.pool.connection() as conn, conn.transaction():
            for row in rows:
                await conn.execute(
                    "INSERT INTO negotiation_sessions (negotiation_id, campaign_id, creator_id, position, status, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (campaign_id, creator_id) DO NOTHING",
                    (row["negotiation_id"], row["campaign_id"], row["creator_id"], row["position"], row["status"], Jsonb(row)),
                )
        return await self.list_negotiation_sessions(rows[0]["campaign_id"])

    async def save_negotiation_session(self, row: dict[str, Any], expect: str | None = None) -> bool:
        async with self.pool.connection() as conn:
            if expect is None:
                cur = await conn.execute(
                    "INSERT INTO negotiation_sessions (negotiation_id, campaign_id, creator_id, position, status, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (negotiation_id) DO UPDATE SET status = EXCLUDED.status, data = EXCLUDED.data",
                    (row["negotiation_id"], row["campaign_id"], row["creator_id"], row["position"], row["status"], Jsonb(row)),
                )
            else:
                cur = await conn.execute(
                    "UPDATE negotiation_sessions SET status = %s, data = %s WHERE negotiation_id = %s AND status = %s",
                    (row["status"], Jsonb(row), row["negotiation_id"], expect),
                )
            return cur.rowcount == 1

    async def list_negotiation_sessions(self, campaign_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT data FROM negotiation_sessions WHERE campaign_id = %s ORDER BY position, negotiation_id", (campaign_id,)
            )).fetchall()  # fmt: skip
        return [r[0] for r in rows]

    async def add_negotiation_round(self, row: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO negotiation_rounds (deal_id, round, campaign_id, at, data) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (deal_id, round) DO UPDATE SET data = EXCLUDED.data",
                (row["deal_id"], row["round"], row["campaign_id"], row["at"], Jsonb(row)),
            )

    async def list_negotiation_rounds(self, deal_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM negotiation_rounds WHERE deal_id = %s ORDER BY round", (deal_id,))).fetchall()
        return [r[0] for r in rows]

    async def save_feedback(self, record: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO negotiation_feedback (id, deal_id, campaign_id, party, user_id, created_at, data) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (deal_id, party) DO UPDATE SET id = EXCLUDED.id, user_id = EXCLUDED.user_id, "
                "created_at = EXCLUDED.created_at, data = EXCLUDED.data",
                (record["id"], record["deal_id"], record["campaign_id"], record["party"], record["user_id"], record["created_at"], Jsonb(record)),
            )

    async def list_feedback(self, *, deal_id: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT data FROM negotiation_feedback WHERE TRUE", []
        if deal_id is not None:
            sql += " AND deal_id = %s"
            params.append(deal_id)
        if user_id is not None:
            sql += " AND user_id = %s"
            params.append(user_id)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY created_at", params)).fetchall()
        return [r[0] for r in rows]

    async def save_performance(self, row: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO agent_performance (deal_id, campaign_id, advertiser_id, influencer_id, outcome, ended_at, data) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (deal_id) DO UPDATE SET outcome = EXCLUDED.outcome, "
                "ended_at = EXCLUDED.ended_at, data = EXCLUDED.data",
                (row["deal_id"], row["campaign_id"], row["advertiser_id"], row["influencer_id"], row["outcome"], row["ended_at"], Jsonb(row)),
            )

    async def list_performance(self, user_id: str, role: str) -> list[dict[str, Any]]:
        column = "advertiser_id" if role == "advertiser" else "influencer_id"
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(f"SELECT data FROM agent_performance WHERE {column} = %s ORDER BY ended_at", (user_id,))).fetchall()
        return [r[0] for r in rows]

    async def save_lesson(self, lesson: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO agent_improvement_examples (id, owner_id, role, status, feedback_id, created_at, data) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, data = EXCLUDED.data",
                (lesson["id"], lesson["owner_id"], lesson["role"], lesson["status"], lesson["feedback_id"], lesson["created_at"], Jsonb(lesson)),
            )

    async def get_lesson(self, lesson_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM agent_improvement_examples WHERE id = %s", (lesson_id,))).fetchone()
        return row[0] if row else None

    async def list_lessons(self, owner_id: str, role: str, status: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT data FROM agent_improvement_examples WHERE owner_id = %s AND role = %s", [owner_id, role]
        if status is not None:
            sql += " AND status = %s"
            params.append(status)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY created_at DESC", params)).fetchall()
        return [r[0] for r in rows]

    # ---- Zcash payments ---------------------------------------------------------------------------------------

    async def save_invoice(self, invoice: Invoice) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO invoices (id, purpose, owner_id, recipient_id, campaign_id, address, status, created_at, data) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, data = EXCLUDED.data",
                (invoice.id, invoice.purpose, invoice.owner_id, invoice.recipient_id, invoice.campaign_id, invoice.address, invoice.status,
                 invoice.created_at, Jsonb(invoice.model_dump(mode="json"))),
            )  # fmt: skip

    async def get_invoice(self, invoice_id: str) -> Invoice | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM invoices WHERE id = %s", (invoice_id,))).fetchone()
        return Invoice.model_validate(row[0]) if row else None

    async def list_invoices(self, *, status: set[str] | None = None, owner_id: str | None = None, campaign_id: str | None = None,
                            purpose: str | None = None) -> list[Invoice]:  # fmt: skip
        sql, params = "SELECT data FROM invoices WHERE TRUE", []
        for column, value in (("owner_id", owner_id), ("campaign_id", campaign_id), ("purpose", purpose)):
            if value is not None:
                sql += f" AND {column} = %s"
                params.append(value)
        if status is not None:
            sql += " AND status = ANY(%s)"
            params.append(list(status))
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY created_at DESC", params)).fetchall()
        return [Invoice.model_validate(r[0]) for r in rows]

    async def save_receiving_account(self, account: ReceivingAccount) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO receiving_accounts (id, owner_id, role, authorized_at, revoked_at, data) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET revoked_at = EXCLUDED.revoked_at, data = EXCLUDED.data",
                (account.id, account.owner_id, account.role, account.authorized_at, account.revoked_at, Jsonb(account.model_dump(mode="json"))),
            )

    async def get_receiving_account(self, owner_id: str) -> ReceivingAccount | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM receiving_accounts WHERE owner_id = %s ORDER BY authorized_at DESC LIMIT 1",
                                            (owner_id,))).fetchone()  # fmt: skip
        return ReceivingAccount.model_validate(row[0]) if row else None

    async def get_receiving_account_by_id(self, account_id: str) -> ReceivingAccount | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM receiving_accounts WHERE id = %s", (account_id,))).fetchone()
        return ReceivingAccount.model_validate(row[0]) if row else None

    async def list_receiving_accounts(self) -> list[ReceivingAccount]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM receiving_accounts ORDER BY authorized_at")).fetchall()
        return [ReceivingAccount.model_validate(r[0]) for r in rows]

    async def save_receipt(self, receipt: Receipt) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO payment_receipts (id, invoice_id, payer_id, recipient_id, issued_at, data) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (receipt.id, receipt.invoice_id, receipt.payer_id, receipt.recipient_id, receipt.issued_at, Jsonb(receipt.model_dump(mode="json"))),
            )

    async def get_receipt(self, receipt_id: str) -> Receipt | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM payment_receipts WHERE id = %s", (receipt_id,))).fetchone()
        return Receipt.model_validate(row[0]) if row else None

    async def list_receipts(self, user_id: str) -> list[Receipt]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM payment_receipts WHERE payer_id = %s OR recipient_id = %s ORDER BY issued_at DESC",
                                             (user_id, user_id))).fetchall()  # fmt: skip
        return [Receipt.model_validate(r[0]) for r in rows]

    async def add_credit(self, entry: CreditEntry) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO agent_credit_ledger (id, user_id, amount, kind, ref, at, data) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (entry.id, entry.user_id, entry.amount, entry.kind, entry.ref, entry.at, Jsonb(entry.model_dump(mode="json"))),
            )
            return cur.rowcount == 1

    async def credit_balance(self, user_id: str) -> Decimal:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT COALESCE(SUM(amount), 0) FROM agent_credit_ledger WHERE user_id = %s", (user_id,))).fetchone()
        assert row is not None
        return Decimal(row[0])

    async def list_credit(self, user_id: str) -> list[CreditEntry]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM agent_credit_ledger WHERE user_id = %s ORDER BY at DESC", (user_id,))).fetchall()
        return [CreditEntry.model_validate(r[0]) for r in rows]

    async def spent_since(self, user_id: str, since: datetime) -> Decimal:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT COALESCE(-SUM(amount), 0) FROM agent_credit_ledger WHERE user_id = %s AND kind = 'spend' AND at >= %s", (user_id, since)
            )).fetchone()  # fmt: skip
        assert row is not None
        return Decimal(row[0])

    async def save_spend_request(self, request: SpendRequest) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO agent_spend_requests (id, user_id, status, created_at, data) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, data = EXCLUDED.data",
                (request.id, request.user_id, request.status, request.created_at, Jsonb(request.model_dump(mode="json"))),
            )

    async def get_spend_request(self, request_id: str) -> SpendRequest | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM agent_spend_requests WHERE id = %s", (request_id,))).fetchone()
        return SpendRequest.model_validate(row[0]) if row else None

    async def list_spend_requests(self, user_id: str, status: str | None = None) -> list[SpendRequest]:
        sql, params = "SELECT data FROM agent_spend_requests WHERE user_id = %s", [user_id]
        if status is not None:
            sql += " AND status = %s"
            params.append(status)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY created_at DESC", params)).fetchall()
        return [SpendRequest.model_validate(r[0]) for r in rows]

    async def add_payment_event(self, event: PaymentEvent) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("INSERT INTO payment_events (invoice_id, payout_id, at, kind, detail) VALUES (%s, %s, %s, %s, %s)",
                               (event.invoice_id, event.payout_id, event.at, event.kind, Jsonb(event.detail)))  # fmt: skip

    async def list_payment_events(self, *, invoice_id: str | None = None, payout_id: str | None = None) -> list[PaymentEvent]:
        sql, params = "SELECT invoice_id, payout_id, at, kind, detail FROM payment_events WHERE TRUE", []
        if invoice_id is not None:
            sql += " AND invoice_id = %s"
            params.append(invoice_id)
        if payout_id is not None:
            sql += " AND payout_id = %s"
            params.append(payout_id)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY id", params)).fetchall()
        return [PaymentEvent(invoice_id=r[0], payout_id=r[1], at=r[2], kind=r[3], detail=r[4]) for r in rows]
