"""Zcash primitives and the zcash-devtool view-only scanner adapter.

The scanner tests are MOCKED: subprocess.run is replaced by canned output built from zcash-devtool's own print
statements. They check what we run and how we parse, not that a real zcash-devtool behaves this way (see
test_zcash_live.py, which runs the real binary against testnet)."""

import base64
import json
from decimal import Decimal

import pytest

from tests.conftest import ua
from zerveil.config import PaymentMode, Settings
from zerveil.services.context import build_wallet, zcash_mode_missing
from zerveil.zcash.address import (
    bech32m_decode,
    bech32m_encode,
    convert_bits,
    is_unified_address,
    network_of,
    unified_address_problem,
    viewing_key_problem,
)
from zerveil.zcash.amounts import format_zec, to_zatoshi, zec_from_zatoshi
from zerveil.zcash.devtool import DevtoolScanner, notes_from, parse_list_tx, server_arg
from zerveil.zcash.memo import decode_memo_hex, memo_for, memo_hex, reference_in
from zerveil.zcash.wallet import WalletError, WalletUnavailable
from zerveil.zcash.zip321 import memo_param, multi_payment_uri, payment_uri

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


def test_zip321_multi_payment_uri():
    a, b = ua("creator-a"), ua("creator-b")
    uri = multi_payment_uri([(a, Decimal("20"), "ZRV1:PAY-aaaaaa", "Fixed fee"), (b, Decimal("15.5"), "ZRV1:PAY-bbbbbb", "")])
    assert uri.startswith("zcash:?")
    params = dict(p.split("=", 1) for p in uri.split("?", 1)[1].split("&"))
    assert params["address"] == a and params["amount"] == "20" and params["address.1"] == b and params["amount.1"] == "15.5"
    assert "memo.1" in params and "message.1" not in params
    assert multi_payment_uri([(a, Decimal("1"), "", "")]) == payment_uri(a, Decimal("1"))


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


def test_zcash_mode_without_scanner_config_refuses_to_start():
    settings = base_settings(payment_mode=PaymentMode.ZCASH)
    assert "ZCASH_DEVTOOL_WALLET_DIR" in zcash_mode_missing(settings)
    with pytest.raises(RuntimeError, match="PAYMENT_MODE=zcash is unavailable"):
        build_wallet(settings)
    mainnet = base_settings(payment_mode=PaymentMode.ZCASH, zcash_devtool_wallet_dir="w", zcash_network="mainnet")
    assert any("testnet only" in m for m in zcash_mode_missing(mainnet))
    other = base_settings(payment_mode=PaymentMode.ZCASH, zcash_devtool_wallet_dir="w", zcash_wallet_backend="zallet")
    assert any("ZCASH_WALLET_BACKEND" in m for m in zcash_mode_missing(other))


def test_zcash_mode_without_the_binary_never_falls_back_to_simulation():
    settings = base_settings(payment_mode=PaymentMode.ZCASH, zcash_devtool_wallet_dir="w", zcash_devtool_bin="no-such-zcash-devtool-binary")
    with pytest.raises(RuntimeError, match="binary not found"):
        build_wallet(settings)


def test_zcash_mode_with_config_uses_the_devtool_scanner(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/zcash-devtool")
    settings = base_settings(payment_mode=PaymentMode.ZCASH, zcash_devtool_wallet_dir="/secret/wallet-dir")
    scanner, warnings = build_wallet(settings)
    assert isinstance(scanner, DevtoolScanner) and not scanner.simulated and scanner.server == "testnet.zec.rocks:443"
    assert not any("/secret/wallet-dir" in w for w in warnings)  # the wallet directory is never surfaced


@pytest.mark.parametrize("mode", ["hosted", "sepolia"])
def test_removed_payment_modes_are_rejected(mode):
    with pytest.raises(ValueError, match="removed with the HSP/EVM payment flow"):
        base_settings(payment_mode=mode)


def test_escrow_mode_is_rejected():
    with pytest.raises(ValueError, match="escrow wallet was removed"):
        base_settings(payment_mode="escrow")


# --- viewing keys ----------------------------------------------------------------------------------------------------


def test_viewing_key_format_checks():
    key = bech32m_encode("uviewtest", convert_bits(bytes(range(96)), 8, 5))
    assert viewing_key_problem(key, "testnet") is None
    assert "mainnet viewing key" in viewing_key_problem(bech32m_encode("uview", convert_bits(bytes(96), 8, 5)), "testnet")
    assert "incoming viewing key" in viewing_key_problem(bech32m_encode("uivktest", convert_bits(bytes(96), 8, 5)), "testnet")
    assert "seed phrase" in viewing_key_problem(" ".join(["word"] * 24), "testnet")
    assert "spending key" in viewing_key_problem("secret-extended-key-test1abc", "testnet")
    assert "checksum" in viewing_key_problem(key[:-1] + ("q" if key[-1] != "q" else "p"), "testnet")


# --- zcash-devtool scanner (MOCKED: canned CLI output built from the upstream print statements) --------------------------

TIP = 4_470_010
UUID = "6f1c1a52-1c3e-4d6a-9a7e-2b8f1f0e9d11"
LIST_TX = f"""\
{"aa" * 32}
     Mined: 4470008 (2026-11-04 10:00:00 UTC)
    Amount: 1.25 TAZ
  Fee paid: Unknown
  Sent 0 notes, received 1 notes, 1 memos
  Output 0 (Ironwood)
    Value: 1.25 TAZ
    Received by account: {UUID} (zerveil-ava)
    To: {ua("inv")}
    Memo: Text(TextMemo("ZRV1:PAY-0123abcd"))
{"bb" * 32}
   Unmined (expiry height: 4470050)
    Amount: 0.3 TAZ
  Fee paid: Unknown
  Sent 0 notes, received 1 notes, 0 memos
  Output 1 (Orchard)
    Value: 0.3 TAZ
    Received by account: {UUID} (zerveil-ava)
    To: {ua("inv")}
{"cc" * 32}
     Mined: 4470001 (2026-11-04 09:00:00 UTC)
    Amount: -0.5 TAZ
  Fee paid: 0.0001 TAZ
  Sent 1 notes, received 1 notes, 1 memos
  Output 0 (Ironwood)
    Value: 0.5 TAZ
    Sent from account: {UUID} (zerveil-ava)
    To: {ua("someone-else")}
  Output 1 (Ironwood)
    Value: 0.2 TAZ (Change)
{"dd" * 32}
   Expired (expiry height: 4469000)
    Amount: 0.7 TAZ
  Fee paid: Unknown
  Sent 0 notes, received 1 notes, 0 memos
  Output 0 (Sapling)
    Value: 0.7 TAZ
    Received by account: {UUID} (zerveil-ava)
    To: {ua("inv")}
"""


def test_list_tx_parser_keeps_received_shielded_outputs_only():
    notes = notes_from(parse_list_tx(LIST_TX), TIP)
    assert [(n.txid[:2], n.pool, n.output_index, n.amount, n.confirmations, n.memo, n.expired) for n in notes] == [
        ("aa", "ironwood", 0, Decimal("1.25"), 3, "ZRV1:PAY-0123abcd", False),
        ("bb", "orchard", 1, Decimal("0.3"), 0, "", False),
        ("dd", "sapling", 0, Decimal("0.7"), 0, "", True),
    ]
    assert notes[0].address == ua("inv") and notes[0].mined_height == 4_470_008


def test_list_tx_parser_fails_closed_on_unknown_output():
    assert notes_from(parse_list_tx("not devtool output\n  Output 0 (Ironwood)\n    Value: 9 TAZ\n"), TIP) == []
    assert notes_from(parse_list_tx(LIST_TX), None)[0].confirmations == 0  # no tip known: nothing counts as confirmed


def test_server_arg():
    assert server_arg("https://testnet.zec.rocks:443") == "testnet.zec.rocks:443"
    assert server_arg("testnet.zec.rocks") == "testnet.zec.rocks:443"


class FakeDevtool:
    """Stands in for the zcash-devtool binary: answers by subcommand, records argv (never runs anything)."""

    def __init__(self, wallet_dir) -> None:
        self.calls: list[list[str]] = []
        self.accounts: list[tuple[str, str]] = []
        self.wallet_dir = wallet_dir

    def run(self, cmd, capture_output, text, timeout, check):  # noqa: ARG002 - subprocess.run signature
        import subprocess

        argv = cmd[4:]
        self.calls.append(argv)
        sub = argv[0]
        out, code = "", 0
        if sub in ("init-fvk", "import-ufvk"):
            key = argv[argv.index("--fvk") + 1] if sub == "init-fvk" else argv[3]
            self.accounts.append((UUID if not self.accounts else UUID[:-1] + "2", key))
            (self.wallet_dir / "data.sqlite").write_text("x")
        elif sub == "list-accounts":
            out = "".join(f"Account {u} (birthday height 4400000)\n     Name: n\n     UFVK: {k}\n" for u, k in self.accounts)
        elif sub == "generate-address":
            out = f"     Address: {ua('inv')}\n"
        elif sub == "get-info":
            out = json.dumps({"server_uri": "https://testnet.zec.rocks:443", "chain_name": "test", "chain_tip_height": TIP}) + "\n"
        elif sub == "list-tx":
            out = LIST_TX
        elif sub == "sync":
            out = ""
        else:
            code = 2
        return subprocess.CompletedProcess(cmd, code, out, "error: unknown")


@pytest.fixture
def devtool(tmp_path, monkeypatch):
    import subprocess

    fake = FakeDevtool(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake.run)
    scanner = DevtoolScanner(binary="zcash-devtool", wallet_dir=str(tmp_path), server="testnet.zec.rocks:443", sync_every_seconds=0)
    return fake, scanner


async def test_devtool_imports_a_viewing_key_once(devtool):
    fake, scanner = devtool
    key = bech32m_encode("uviewtest", convert_bits(bytes(range(96)), 8, 5))
    assert await scanner.import_viewing_key("zerveil-ava", key, 4_400_000) == UUID
    assert fake.calls[0][:2] == ["init-fvk", "--name"] and fake.calls[0][-4:] == ["-s", "testnet.zec.rocks:443", "--connection", "direct"]
    assert await scanner.import_viewing_key("zerveil-ava", key, 4_400_000) == UUID  # same key: no second import
    assert sum(c[0] in ("init-fvk", "import-ufvk") for c in fake.calls) == 1
    other = bech32m_encode("uviewtest", convert_bits(bytes(range(1, 97)), 8, 5))
    assert await scanner.import_viewing_key("zerveil-bo", other, 4_400_000) != UUID
    assert fake.calls[-2][0] == "import-ufvk" and fake.calls[-2][3:5] == [other, "4400000"]


async def test_devtool_address_notes_and_nu7_status(devtool):
    fake, scanner = devtool
    assert await scanner.new_address(UUID) == ua("inv")
    assert fake.calls[-1] == ["generate-address", UUID, "-p", "orchard", "-p", "sapling"]
    notes = await scanner.received(UUID, [ua("inv")])
    assert [c[0] for c in fake.calls[-3:]] == ["sync", "get-info", "list-tx"]
    assert [(n.txid[:2], n.confirmations) for n in notes] == [("aa", 3), ("bb", 0), ("dd", 0)]
    info = await scanner.info()
    assert info.reachable and info.tip_height == TIP and info.nu7_activation_height == 4_465_026 and info.nu7_active is True
    assert info.chain_name == "test" and not info.warnings


async def test_devtool_errors_never_echo_the_viewing_key(devtool, monkeypatch):
    import subprocess

    fake, scanner = devtool
    key = bech32m_encode("uviewtest", convert_bits(bytes(range(96)), 8, 5))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", f"error: bad key {cmd[-6]}\nfailed"))
    with pytest.raises(WalletError) as e:
        await scanner.import_viewing_key("zerveil-ava", key, 4_400_000)
    assert key not in str(e.value)

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(WalletUnavailable):
        await scanner.new_address(UUID)
    assert not (await scanner.info()).reachable
