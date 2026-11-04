"""Zcash payment rails: amounts, Unified Address checks, ZIP-321 payment URIs, memos and the wallet backends.

Cryptography (keys, proving, signing, note scanning) stays inside the wallet. Zerveil only talks to it over its
JSON-RPC interface (`rpc.ZalletRpcWallet`), or to the in-process `simulated.SimulatedZcashWallet` in development and
tests, which never claims a real transaction.
"""

from .amounts import ZATOSHI, format_zec, to_zatoshi, zec_from_zatoshi
from .wallet import (
    OperationStatus,
    ReceivedNote,
    SendResult,
    TxStatus,
    WalletError,
    WalletInfo,
    WalletUnavailable,
    ZcashWallet,
)

__all__ = [
    "ZATOSHI",
    "OperationStatus",
    "ReceivedNote",
    "SendResult",
    "TxStatus",
    "WalletError",
    "WalletInfo",
    "WalletUnavailable",
    "ZcashWallet",
    "format_zec",
    "to_zatoshi",
    "zec_from_zatoshi",
]
