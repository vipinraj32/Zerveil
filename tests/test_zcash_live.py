"""REAL Zcash testnet checks against a running Zallet (or zcashd-compatible) wallet. Skipped unless configured:

    ZCASH_LIVE_RPC_URL        e.g. http://127.0.0.1:28232 (keep the RPC on loopback or a tunnel)
    ZCASH_LIVE_RPC_USER / ZCASH_LIVE_RPC_PASSWORD   if the wallet's RPC needs basic auth
    ZCASH_LIVE_ACCOUNT        the escrow account (UUID or number)
    ZCASH_LIVE_FROM_ADDRESS   a Unified Address of that account (the payout source)

Read-only by default. To also send 0.0001 TAZ to an address of your own and wait for the wallet to see it:

    ZCASH_LIVE_SEND=1  ZCASH_LIVE_SEND_TO=utest1...

Nothing here runs in CI; every other test uses the simulated wallet or a mocked RPC transport."""

import asyncio
import os
from decimal import Decimal

import pytest

from zerveil.zcash.address import is_unified_address
from zerveil.zcash.memo import memo_for
from zerveil.zcash.rpc import ZalletRpcWallet

URL = os.environ.get("ZCASH_LIVE_RPC_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="ZCASH_LIVE_RPC_URL not set: no real Zcash wallet to test against")


@pytest.fixture
async def wallet():
    w = ZalletRpcWallet(URL, network="testnet", account=os.environ.get("ZCASH_LIVE_ACCOUNT", "0"),
                        from_address=os.environ.get("ZCASH_LIVE_FROM_ADDRESS", ""), user=os.environ.get("ZCASH_LIVE_RPC_USER", ""),
                        password=os.environ.get("ZCASH_LIVE_RPC_PASSWORD", ""))  # fmt: skip
    yield w
    await w.aclose()


async def test_live_wallet_is_reachable(wallet):
    info = await wallet.info()
    assert info.reachable, info.detail
    print(f"escrow balance: {info.escrow_balance} TAZ")


async def test_live_invoice_address_is_a_fresh_shielded_ua(wallet):
    a, b = await wallet.new_invoice_address(), await wallet.new_invoice_address()
    assert a != b and is_unified_address(a, "testnet") and is_unified_address(b, "testnet")
    assert await wallet.validate_address(a) is None


async def test_live_scan_reads_notes(wallet):
    notes = await wallet.received([await wallet.new_invoice_address()])
    assert notes == []  # a brand-new address has received nothing


@pytest.mark.skipif(os.environ.get("ZCASH_LIVE_SEND") != "1", reason="ZCASH_LIVE_SEND=1 not set: no TAZ is sent")
async def test_live_shielded_send_and_outgoing_memo(wallet):
    to = os.environ["ZCASH_LIVE_SEND_TO"]
    memo = memo_for("PO-livetest01")
    result = await wallet.send(to, Decimal("0.0001"), memo)
    txids = result.txids
    for _ in range(60):
        if txids:
            break
        op = await wallet.operation(result.operation_id)
        assert op.status not in ("failed", "cancelled"), op.error
        txids = op.txids
        await asyncio.sleep(5)
    assert txids, "the wallet did not report a txid within 5 minutes"
    print(f"testnet txid: {txids[0]}")
    tx = await wallet.transaction(txids[0])
    assert tx is not None and memo in tx.outgoing_memos
