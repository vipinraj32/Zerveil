"""Zcash payment rails: amounts, Unified Address and viewing-key checks, ZIP-321 payment URIs, memos and the
view-only payment scanners.

Zerveil holds no funds and no spending keys. Payers send shielded ZEC straight to the recipient's own wallet; the
scanner (`devtool.DevtoolScanner`, or `simulated.SimulatedScanner` in development and tests) watches the recipient's
receiving account with a viewing key the recipient connected, and reports what arrived.
"""

from .amounts import ZATOSHI, format_zec, to_zatoshi, zec_from_zatoshi
from .wallet import PaymentScanner, ReceivedNote, WalletError, WalletInfo, WalletUnavailable

__all__ = [
    "ZATOSHI",
    "PaymentScanner",
    "ReceivedNote",
    "WalletError",
    "WalletInfo",
    "WalletUnavailable",
    "format_zec",
    "to_zatoshi",
    "zec_from_zatoshi",
]
