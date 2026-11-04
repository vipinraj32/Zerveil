"""Payment memos. A shielded output carries a 512-byte memo that only the sender and recipient can decrypt.

Zerveil puts nothing but a reference in it — `ZRV1:<id>` — never campaign terms, names or amounts. The reference is
a second matching signal: invoices are matched first by their own one-time Unified Address, because the payer's
wallet may drop or replace the memo.
"""

from __future__ import annotations

import re

PREFIX = "ZRV1:"
MEMO_MAX_BYTES = 512
_REF = re.compile(r"ZRV1:([A-Z]{2,4}-[0-9a-f]{6,32})")


def memo_for(reference: str) -> str:
    memo = PREFIX + reference
    if len(memo.encode()) > MEMO_MAX_BYTES:
        raise ValueError("memo too long")
    return memo


def memo_hex(memo: str) -> str:
    """zcashd-style RPCs take memos as hex."""
    return memo.encode().hex()


def reference_in(memo_text: str | None) -> str | None:
    """The Zerveil reference inside a decrypted memo, if there is one."""
    if not memo_text:
        return None
    m = _REF.search(memo_text)
    return m.group(1) if m else None


def decode_memo_hex(memo_hex_value: str | None) -> str:
    """Wallet RPCs return the 512-byte memo as hex; text memos are UTF-8 padded with zeros. 0xF6… means "no memo"."""
    if not memo_hex_value:
        return ""
    try:
        raw = bytes.fromhex(memo_hex_value)
    except ValueError:
        return ""
    if not raw or raw[0] == 0xF6 or raw[0] > 0xF4:
        return ""
    return raw.rstrip(b"\x00").decode("utf-8", errors="replace")
