"""Zerveil: privacy-powered AI influencer agent marketplace. Agents negotiate sponsorships over A2A; campaigns are
funded and creators paid with shielded Zcash transactions."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _serve(host: str, port: int) -> None:
    import uvicorn

    from .api.app import create_app

    config = uvicorn.Config(create_app(), host=host, port=port, log_level="info")
    # psycopg's async driver cannot run on Windows' default Proactor loop
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=loop_factory)


async def _migrate(show_status: bool) -> None:
    from .config import get_settings
    from .store.migrations import migrate, migration_status
    from .store.postgres import open_pool

    settings = get_settings()
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set: the in-memory store needs no migrations")
    pool = await open_pool(settings.database_url, max_size=1)
    try:
        async with pool.connection() as conn:
            if show_status:
                for version, name, applied in await migration_status(conn):
                    print(f"{version:03d} {name:<20} {'applied' if applied else 'pending'}")
            else:
                applied = await migrate(conn)
                print(f"applied migrations: {applied}" if applied else "database is up to date")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="zerveil")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the platform server (API, dashboard, A2A agents, MCP, payment scanner)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp", help="run the read-only MCP server over stdio (set ZERVEIL_API_URL)")
    mig = sub.add_parser("migrate", help="apply pending PostgreSQL migrations (also run automatically at startup)")
    mig.add_argument("--status", action="store_true", help="list applied and pending migrations")
    args = parser.parse_args()

    # pydantic-settings reads .env into Settings only; LangSmith (LANGSMITH_*) and the currency label (ZCASH_NETWORK)
    # read the process environment
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        _serve(args.host, args.port)
    elif args.cmd == "mcp":
        from .mcp_servers.server import main as mcp_main

        mcp_main()
    elif args.cmd == "migrate":
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        asyncio.run(_migrate(args.status), loop_factory=loop_factory)
