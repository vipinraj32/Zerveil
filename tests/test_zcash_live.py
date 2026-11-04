"""REAL Zcash testnet checks against the actual zcash-devtool binary and a public lightwalletd server.

Skipped unless you configure them, because they need software and keys this repository cannot provide:

    ZCASH_LIVE_DEVTOOL_BIN    path to a zcash-devtool built from main (NU7 cohort)
    ZCASH_LIVE_WALLET_DIR     an empty or existing directory for the view-only wallet (kept between runs)
    ZCASH_LIVE_UFVK           Unified Full Viewing Key (uviewtest1…) of YOUR OWN testnet receiving account
    ZCASH_LIVE_BIRTHDAY       block height to scan from (the account's creation height)
    ZCASH_LIVE_LIGHTWALLETD   default https://testnet.zec.rocks:443
    ZCASH_LIVE_EXPECT_TXID    optional: a txid you sent to an address of that account (e.g. from the faucet)

Nothing here sends ZEC, claims faucet funds or touches a spending key. Results are reported as they are: a test that
cannot reach the server fails; it never falls back to simulation.
"""

import os

import pytest

from zerveil.zcash.address import unified_address_problem, viewing_key_problem
from zerveil.zcash.devtool import NU7_ACTIVATION, DevtoolScanner, server_arg

BIN = os.environ.get("ZCASH_LIVE_DEVTOOL_BIN", "")
DIR = os.environ.get("ZCASH_LIVE_WALLET_DIR", "")
UFVK = os.environ.get("ZCASH_LIVE_UFVK", "")
BIRTHDAY = int(os.environ.get("ZCASH_LIVE_BIRTHDAY", "0") or 0)
SERVER = server_arg(os.environ.get("ZCASH_LIVE_LIGHTWALLETD", "https://testnet.zec.rocks:443"))

pytestmark = pytest.mark.skipif(not (BIN and DIR), reason="real testnet: set ZCASH_LIVE_DEVTOOL_BIN and ZCASH_LIVE_WALLET_DIR")


def scanner() -> DevtoolScanner:
    return DevtoolScanner(binary=BIN, wallet_dir=DIR, server=SERVER, network="testnet", sync_every_seconds=0)


async def test_live_lightwalletd_is_testnet_past_nu7():
    info = await scanner().info()
    assert info.reachable, info.detail
    assert info.chain_name == "test", info.chain_name
    assert info.tip_height is not None and info.tip_height >= NU7_ACTIVATION["testnet"], info.tip_height
    assert info.nu7_active is True


@pytest.mark.skipif(not UFVK, reason="set ZCASH_LIVE_UFVK (your own testnet receiving account's viewing key)")
async def test_live_import_viewing_key_and_derive_addresses():
    assert viewing_key_problem(UFVK, "testnet") is None
    s = scanner()
    account = await s.import_viewing_key("zerveil-live-test", UFVK, BIRTHDAY)
    assert await s.import_viewing_key("zerveil-live-test", UFVK, BIRTHDAY) == account  # idempotent
    a, b = await s.new_address(account), await s.new_address(account)
    assert a != b and unified_address_problem(a, "testnet") is None and a.startswith("utest1")


@pytest.mark.skipif(not (UFVK and os.environ.get("ZCASH_LIVE_EXPECT_TXID")), reason="set ZCASH_LIVE_EXPECT_TXID to a payment you made")
async def test_live_scanner_sees_a_real_payment():
    s = scanner()
    account = await s.import_viewing_key("zerveil-live-test", UFVK, BIRTHDAY)
    notes = await s.received(account, [])
    txid = os.environ["ZCASH_LIVE_EXPECT_TXID"].lower()
    mine = [n for n in notes if n.txid == txid]
    assert mine, f"txid {txid} not among {len(notes)} received notes"
    print({"txid": txid, "pools": sorted({n.pool for n in mine}), "confirmations": max(n.confirmations for n in mine)})
