"""ZIP-321 payment request URIs (`zcash:<address>?amount=…&memo=…&message=…`).

Wallets that support ZIP-321 open the URI with the address, amount and memo filled in. The memo travels encrypted
inside the shielded output; `message` is only shown to the payer by their wallet and never goes on chain.
"""

from __future__ import annotations

import base64
from decimal import Decimal
from urllib.parse import quote

from .amounts import format_zec


def memo_param(memo: str) -> str:
    """ZIP-321 encodes memos as base64url without padding."""
    return base64.urlsafe_b64encode(memo.encode()).decode().rstrip("=")


def payment_uri(address: str, amount: Decimal, memo: str = "", message: str = "") -> str:
    params = [f"amount={format_zec(amount)}"]
    if memo:
        params.append(f"memo={memo_param(memo)}")
    if message:
        params.append(f"message={quote(message, safe='')}")
    return f"zcash:{address}?{'&'.join(params)}"


def multi_payment_uri(payments: list[tuple[str, Decimal, str, str]]) -> str:
    """One ZIP-321 request paying several recipients at once: [(address, amount, memo, message), …]. Wallets that
    support multi-payment requests build one transaction with one output per recipient."""
    if len(payments) == 1:
        return payment_uri(*payments[0])
    params: list[str] = []
    for i, (address, amount, memo, message) in enumerate(payments):
        suffix = f".{i}" if i else ""
        params += [f"address{suffix}={address}", f"amount{suffix}={format_zec(amount)}"]
        if memo:
            params.append(f"memo{suffix}={memo_param(memo)}")
        if message:
            params.append(f"message{suffix}={quote(message, safe='')}")
    return f"zcash:?{'&'.join(params)}"
