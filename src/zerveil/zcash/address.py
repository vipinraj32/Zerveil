"""Unified Address format checks (ZIP-316).

A Unified Address is Bech32m-encoded (without Bech32's 90-character limit) with the human-readable part `u`
(mainnet), `utest` (testnet) or `uregtest` (regtest). This module checks the encoding, checksum and network only.
Whether the receivers inside are valid (F4Jumble, Orchard/Sapling key encodings) is for the wallet to decide: in
zcash mode the app also asks the wallet (`z_listunifiedreceivers`) before it stores a payout address.
"""

from __future__ import annotations

from typing import Literal

Network = Literal["mainnet", "testnet", "regtest"]

UA_HRP: dict[str, str] = {"mainnet": "u", "testnet": "utest", "regtest": "uregtest"}
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32M_CONST = 0x2BC830A3


def _polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def bech32m_decode(text: str) -> tuple[str, list[int]] | None:
    """(hrp, 5-bit data without checksum), or None when the string is not valid Bech32m."""
    if text.lower() != text and text.upper() != text:
        return None
    text = text.lower()
    pos = text.rfind("1")
    if pos < 1 or pos + 7 > len(text):
        return None
    hrp, rest = text[:pos], text[pos + 1 :]
    if any(ord(c) < 33 or ord(c) > 126 for c in hrp) or any(c not in CHARSET for c in rest):
        return None
    data = [CHARSET.find(c) for c in rest]
    if _polymod(_hrp_expand(hrp) + data) != BECH32M_CONST:
        return None
    return hrp, data[:-6]


def bech32m_encode(hrp: str, data: list[int]) -> str:
    values = _hrp_expand(hrp) + data
    polymod = _polymod(values + [0] * 6) ^ BECH32M_CONST
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(CHARSET[d] for d in data + checksum)


def convert_bits(data: bytes | list[int], from_bits: int, to_bits: int, pad: bool = True) -> list[int]:
    acc = bits = 0
    out: list[int] = []
    maxv = (1 << to_bits) - 1
    for value in data:
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maxv)
    if pad and bits:
        out.append((acc << (to_bits - bits)) & maxv)
    return out


def unified_address_problem(address: str, network: Network) -> str | None:
    """Why `address` cannot be used as a Unified Address on `network`, or None when the format is right."""
    address = address.strip()
    if not address:
        return "enter a Unified Address"
    decoded = bech32m_decode(address)
    if decoded is None:
        if address.startswith(("t1", "t3", "tm", "t2")):
            return "this is a transparent address: Zerveil pays only to shielded Unified Addresses"
        if address.startswith(("zs", "ztestsapling")):
            return "this is a Sapling address: use your wallet's Unified Address (starts with u1, or utest1 on testnet)"
        return "not a valid Unified Address (bad characters or checksum)"
    hrp, data = decoded
    expected = UA_HRP[network]
    if hrp != expected:
        other = next((n for n, h in UA_HRP.items() if h == hrp), None)
        return f"this is a {other} address; Zerveil runs on Zcash {network}" if other else f"not a Unified Address (prefix {hrp})"
    if len(data) * 5 // 8 < 48:  # padding + at least one receiver
        return "address too short to be a Unified Address"
    return None


def is_unified_address(address: str, network: Network) -> bool:
    return unified_address_problem(address, network) is None


def network_of(address: str) -> str:
    """The network a Unified Address belongs to, from its prefix ("testnet" when it cannot tell)."""
    decoded = bech32m_decode(address.strip())
    hrp = decoded[0] if decoded else ""
    return next((n for n, h in UA_HRP.items() if h == hrp), "testnet")


def short(address: str) -> str:
    return address if len(address) <= 24 else f"{address[:12]}…{address[-8:]}"
