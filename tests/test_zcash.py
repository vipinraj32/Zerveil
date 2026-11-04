"""Zcash primitives and the Zallet JSON-RPC client.

The RPC tests run against httpx.MockTransport with responses shaped like Zallet/zcashd documentation describes them:
they check what we send and how we parse, not that a real wallet behaves this way (see test_zcash_live.py)."""

import base64
import json
from decimal import Decimal

import httpx
import pytest

from tests.conftest import ua
from zerveil.config import PaymentMode, Settings
from zerveil.services.context import build_wallet, zcash_mode_missing
from zerveil.zcash.address import bech32m_decode, bech32m_encode, convert_bits, is_unified_address, network_of, unified_address_problem
from zerveil.zcash.amounts import format_zec, to_zatoshi, zec_from_zatoshi
from zerveil.zcash.memo import decode_memo_hex, memo_for, memo_hex, reference_in
from zerveil.zcash.rpc import ZalletRpcWallet
from zerveil.zcash.wallet import WalletError, WalletUnavailable
from zerveil.zcash.zip321 import memo_param, payment_uri

# --- addresses ---------------------------------------------------------------------------------------------------


def test_bech32m_roundtrip_and_checksum():
    data = convert_bits(bytes(range(64)), 8, 5)
    text = bech32m_encode("utest", data)
    assert bech32m_decode(text) == ("utest", data)
    flipped = text[:-1] + ("q" if text[-1] != "q" else "p")
    assert bech32m_decode(flipped) is None
    assert bech32m_decode(text.upper()) == ("utest", data)  # Bech32 is case-insensitive, but not mixed case
    assert bech32m_decode(text[:10] + text[10:].upper()) is None


def test_unified_address_rules():
    assert is_unified_address(ua("a"), "testnet")
    assert network_of(ua("a")) == "testnet" and network_of(ua("a", "mainnet")) == "mainnet"
    assert "mainnet" in unified_address_problem(ua("a", "mainnet"), "testnet")
    assert "transparent" in unified_address_problem("tmBsTi2xWTjUdEXnuTceL7fecEQKeWaPDJd", "testnet")
    assert "Sapling" in unified_address_problem("ztestsapling1" + "q" * 70, "testnet")
    assert unified_address_problem("", "testnet") == "enter a Unified Address"
    assert "too short" in unified_address_problem(bech32m_encode("utest", convert_bits(b"\x01" * 8, 8, 5)), "testnet")


# --- ZIP-321, memos, amounts ----------------------------------------------------------------------------------------


def test_zip321_uri():
    address = ua("pay")
    uri = payment_uri(address, Decimal("1.50000000"), "ZRV1:PAY-abc123", "Zerveil campaign funding")
    assert uri.startswith(f"zcash:{address}?amount=1.5&")
    params = dict(p.split("=", 1) for p in uri.split("?", 1)[1].split("&"))
    padded = params["memo"] + "=" * (-len(params["memo"]) % 4)
    assert base64.urlsafe_b64decode(padded).decode() == "ZRV1:PAY-abc123"
    assert "=" not in memo_param("ab")  # base64url without padding
    assert params["message"] == "Zerveil%20campaign%20funding"


def test_memo_reference_and_hex():
    memo = memo_for("PAY-0123abcd")
    assert memo == "ZRV1:PAY-0123abcd" and reference_in(f"thanks! {memo}") == "PAY-0123abcd"
    assert reference_in("ZRV1:nothing") is None and reference_in(None) is None
    raw = memo_hex(memo) + "00" * (512 - len(memo))
    assert decode_memo_hex(raw) == memo
    assert decode_memo_hex("f6" + "00" * 511) == ""  # "no memo"
    assert decode_memo_hex(None) == ""


def test_amounts_are_exact():
    assert to_zatoshi(Decimal("1.23456789")) == 123_456_789
    assert zec_from_zatoshi(1) == Decimal("0.00000001")
    with pytest.raises(ValueError):
        to_zatoshi(Decimal("0.000000001"))
    with pytest.raises(ValueError):
        to_zatoshi(Decimal("21000001"))
    assert format_zec(Decimal("1.50000000")) == "1.5"


# --- configuration fails closed -------------------------------------------------------------------------------------


def base_settings(**over) -> Settings:
    return Settings(_env_file=None, database_url="", anthropic_api_key="", youtube_api_key="", session_secret="s", **over)


def test_zcash_mode_without_wallet_config_refuses_to_start():
    settings = base_settings(payment_mode=PaymentMode.ZCASH)
    assert {"ZCASH_WALLET_RPC_URL", "ZCASH_ESCROW_ACCOUNT", "ZCASH_ESCROW_FROM_ADDRESS"} <= set(zcash_mode_missing(settings))
    with pytest.raises(RuntimeError, match="PAYMENT_MODE=zcash is unavailable"):
        build_wallet(settings)
    wrong_net = base_settings(payment_mode=PaymentMode.ZCASH, zcash_wallet_rpc_url="http://127.0.0.1:28232", zcash_escrow_account="0",
                              zcash_escrow_from_address=ua("escrow", "mainnet"))  # fmt: skip
    assert any("ZCASH_ESCROW_FROM_ADDRESS" in m for m in zcash_mode_missing(wrong_net))


def test_zcash_mode_with_config_uses_the_rpc_wallet():
    settings = base_settings(payment_mode=PaymentMode.ZCASH, zcash_wallet_rpc_url="http://127.0.0.1:28232", zcash_escrow_account="0",
                             zcash_escrow_from_address=ua("escrow"))  # fmt: skip
    wallet, warnings = build_wallet(settings)
    assert isinstance(wallet, ZalletRpcWallet) and not wallet.simulated
    assert not any("127.0.0.1" in w for w in warnings)  # the RPC URL is never surfaced


@pytest.mark.parametrize("mode", ["hosted", "sepolia"])
def test_removed_payment_modes_are_rejected(mode):
    with pytest.raises(ValueError, match="removed with the HSP/EVM payment flow"):
        base_settings(payment_mode=mode)


# --- Zallet JSON-RPC client -----------------------------------------------------------------------------------------


class FakeZallet:
    """Answers JSON-RPC calls from a dict of method → result (or callable(params) → result); records every call."""

    def __init__(self, results: dict) -> None:
        self.results = results
        self.calls: list[tuple[str, list]] = []
        self.auth: list[str | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append((body["method"], body["params"]))
        self.auth.append(request.headers.get("authorization"))
        result = self.results.get(body["method"])
        if isinstance(result, Exception):
            return httpx.Response(500, json={"jsonrpc": "2.0", "id": body["id"], "result": None, "error": {"code": -8, "message": str(result)}})
        if callable(result):
            result = result(body["params"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result, "error": None})

    def wallet(self, **kw) -> ZalletRpcWallet:
        return ZalletRpcWallet("http://wallet.test/", network="testnet", account="0", from_address=ua("escrow"),
                               transport=httpx.MockTransport(self.handler), **kw)  # fmt: skip


async def test_rpc_new_address_requests_only_shielded_receivers():
    fake = FakeZallet({"z_getaddressforaccount": {"account": 0, "diversifier_index": 7, "receiver_types": ["orchard", "sapling"], "address": ua("inv")}})
    wallet = fake.wallet(user="zerveil", password="pw")
    assert await wallet.new_invoice_address() == ua("inv")
    assert fake.calls == [("z_getaddressforaccount", [0, ["orchard", "sapling"]])]
    assert fake.auth[0].startswith("Basic ")


async def test_rpc_received_parses_notes_and_skips_change_and_transparent():
    memo = memo_hex("ZRV1:PAY-0123abcd").ljust(1024, "0")
    rows = [
        {"txid": "aa" * 32, "pool": "orchard", "outindex": 0, "confirmations": 4, "spendable": True, "address": ua("inv"),
         "value": 1.25, "valueZat": 125_000_000, "memo": memo, "change": False},
        {"txid": "bb" * 32, "pool": "orchard", "outindex": 1, "confirmations": 4, "address": ua("inv"), "valueZat": 5, "walletInternal": True},
        {"txid": "cc" * 32, "pool": "transparent", "outindex": 0, "confirmations": 4, "address": "tmBsTi2xWTjUdEXnuTceL7fecEQKeWaPDJd", "valueZat": 9},
        {"txid": "dd" * 32, "outindex": 2, "confirmations": 0, "address": ua("inv"), "amount": 0.1, "memo": "f6" + "00" * 511},  # zcashd sapling row
        {"txid": "ee" * 32, "pool": "orchard", "outindex": 0, "confirmations": 9, "address": ua("someone-else"), "valueZat": 1},
    ]  # fmt: skip
    fake = FakeZallet({"z_listunspent": rows})
    notes = await fake.wallet().received([ua("inv")])
    assert fake.calls == [("z_listunspent", [0, 9_999_999, False])]
    assert [(n.txid[:2], n.pool, n.amount, n.memo) for n in notes] == [
        ("aa", "orchard", Decimal("1.25"), "ZRV1:PAY-0123abcd"),
        ("dd", "sapling", Decimal("0.1"), ""),
    ]
    assert notes[0].key == f"{'aa' * 32}:orchard:0"


async def test_rpc_send_uses_zip317_fee_and_hex_memo():
    fake = FakeZallet({"z_sendmany": "opid-1234", "z_getoperationstatus": [{"id": "opid-1234", "status": "success", "result": {"txid": "ff" * 32}}]})
    wallet = fake.wallet()
    result = await wallet.send(ua("creator"), Decimal("12.34567891"), "ZRV1:PO-0123abcd")
    method, params = fake.calls[0]
    assert method == "z_sendmany" and params[0] == ua("escrow")
    assert params[1] == [{"address": ua("creator"), "amount": 12.34567891, "memo": memo_hex("ZRV1:PO-0123abcd")}]
    assert params[2:] == [3, None, "FullPrivacy"]  # minconf, fee=null (ZIP-317), privacy policy
    assert result.operation_id == "opid-1234" and result.txids == ()
    op = await wallet.operation("opid-1234")
    assert op.status == "success" and op.txids == ("ff" * 32,)
    assert (await wallet.operation("opid-gone")).status == "unknown"


async def test_rpc_send_result_with_txids_and_unbroadcast():
    fake = FakeZallet({"z_sendmany": {"txids": ["ab" * 32]}})
    assert (await fake.wallet().send(ua("c"), Decimal("1"), "m")).txids == ("ab" * 32,)
    fake = FakeZallet({"z_sendmany": {"txids": ["ab" * 32], "broadcast": False}})
    with pytest.raises(WalletError, match="did not broadcast"):
        await fake.wallet().send(ua("c"), Decimal("1"), "m")


async def test_rpc_errors_and_unreachable_wallet():
    fake = FakeZallet({"z_sendmany": RuntimeError("Insufficient funds")})
    with pytest.raises(WalletError, match="Insufficient funds") as e:
        await fake.wallet().send(ua("c"), Decimal("1"), "m")
    assert not isinstance(e.value, WalletUnavailable)  # refused: safe to mark failed

    def down(request):
        raise httpx.ConnectError("refused")

    wallet = ZalletRpcWallet("http://wallet.test/", network="testnet", account="0", from_address=ua("escrow"), transport=httpx.MockTransport(down))
    with pytest.raises(WalletUnavailable):  # outcome unknown: never treated as "not sent"
        await wallet.send(ua("c"), Decimal("1"), "m")
    info = await wallet.info()
    assert not info.reachable and "unreachable" in info.detail and "wallet.test" not in info.detail


async def test_rpc_transaction_and_outgoing_memos():
    view = {"txid": "ab" * 32, "confirmations": 2, "outputs": [
        {"pool": "orchard", "outgoing": True, "memo": memo_hex("ZRV1:PO-0123abcd").ljust(1024, "0")},
        {"pool": "orchard", "outgoing": False, "walletInternal": True, "memo": "f6" + "00" * 511},
    ]}  # fmt: skip
    fake = FakeZallet({"z_viewtransaction": view, "z_listtransactions": RuntimeError("Method not found"),
                       "listtransactions": [{"txid": "ab" * 32}, {"txid": "ab" * 32}]})  # fmt: skip
    wallet = fake.wallet()
    tx = await wallet.transaction("ab" * 32)
    assert tx.confirmations == 2 and tx.outgoing_memos == ("ZRV1:PO-0123abcd",)
    recent = await wallet.recent_outgoing()
    assert [t.txid for t in recent] == ["ab" * 32]  # deduplicated; falls back to listtransactions


async def test_rpc_validate_address():
    fake = FakeZallet({"z_listunifiedreceivers": {"orchard": "…", "sapling": "…"}})
    assert await fake.wallet().validate_address(ua("x")) is None
    fake = FakeZallet({"z_listunifiedreceivers": {"p2pkh": "…"}})
    assert "no shielded" in await fake.wallet().validate_address(ua("x"))
    fake = FakeZallet({"z_listunifiedreceivers": RuntimeError("Invalid address")})
    assert "rejected" in await fake.wallet().validate_address(ua("x"))


async def test_rpc_info_reports_escrow_balance():
    fake = FakeZallet({"getwalletinfo": {}, "z_getbalanceforaccount": {"pools": {"orchard": {"valueZat": 150_000_000}, "sapling": {"valueZat": 1}}}})
    info = await fake.wallet().info()
    assert info.reachable and info.escrow_balance == Decimal("1.50000001") and not info.simulated
