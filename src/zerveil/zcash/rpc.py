"""Real Zcash wallet over JSON-RPC: Zallet (the Zcash wallet that replaces zcashd's), or a zcashd-compatible wallet.

Methods used, all documented in Zallet's zcashd-compatibility table (https://zcash.github.io/zallet/zcashd/rpc_status.html):

    z_getaddressforaccount   a fresh diversified Unified Address of the escrow account, one per invoice
    z_listunspent            notes the wallet's scanner decrypted (txid, pool, outindex, address, value, memo, confirmations)
    z_sendmany               a shielded payout from the escrow account (ZIP-317 fee: `fee` must be null)
    z_getoperationstatus     result of an asynchronous send (txid)
    z_viewtransaction        confirmations and decrypted outgoing memos of a transaction
    z_listunifiedreceivers   which receivers a Unified Address holds (we only pay shielded ones)
    z_getbalanceforaccount   the escrow account's balance, for the custody reconciliation page
    getwalletinfo            liveness

Zallet's RPC is plain HTTP: keep it on loopback or behind an SSH tunnel / VPN, as its setup guide says.
Responses are parsed defensively, because Zallet altered some zcashd fields (`amount` → `value`, `txid` → `txids`).
"""

from __future__ import annotations

import itertools
import json
import logging
from decimal import Decimal
from typing import Any

import httpx

from .amounts import ZATOSHI, zec_from_zatoshi
from .memo import decode_memo_hex
from .wallet import OperationStatus, ReceivedNote, SendResult, TxStatus, WalletError, WalletInfo, WalletUnavailable

log = logging.getLogger(__name__)

SHIELDED_POOLS = {"orchard", "sapling"}


class ZalletRpcWallet:
    name = "zallet"
    simulated = False

    def __init__(
        self,
        url: str,
        *,
        network: str,
        account: str,
        from_address: str,
        user: str = "",
        password: str = "",
        receiver_types: list[str] | None = None,
        privacy_policy: str = "FullPrivacy",
        send_minconf: int = 3,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.network = network
        self.account: str | int = int(account) if account.isdigit() else account
        self.from_address = from_address
        self.receiver_types = receiver_types or ["orchard", "sapling"]
        self.privacy_policy = privacy_policy
        self.send_minconf = send_minconf
        auth = httpx.BasicAuth(user, password) if user else None
        self._http = httpx.AsyncClient(timeout=timeout, auth=auth, transport=transport)
        self._ids = itertools.count(1)

    async def call(self, method: str, *params: Any) -> Any:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": list(params)}
        try:
            res = await self._http.post(self.url, content=json.dumps(body), headers={"content-type": "application/json"})
        except httpx.HTTPError as e:
            raise WalletUnavailable(f"{method}: wallet unreachable ({type(e).__name__})") from e
        try:
            payload = res.json()
        except ValueError as e:
            raise WalletUnavailable(f"{method}: HTTP {res.status_code}, not a JSON-RPC response") from e
        if payload.get("error"):
            err = payload["error"]
            raise WalletError(f"{method}: {err.get('message', err) if isinstance(err, dict) else err}")
        if res.status_code >= 400:
            raise WalletUnavailable(f"{method}: HTTP {res.status_code}")
        return payload.get("result")

    # ---- receiving -----------------------------------------------------------------------------------------

    async def new_invoice_address(self) -> str:
        # No transparent receiver and no explicit index: Zallet derives a fresh (time-based) diversified address
        result = await self.call("z_getaddressforaccount", self.account, self.receiver_types)
        address = result.get("address") if isinstance(result, dict) else None
        if not isinstance(address, str) or not address:
            raise WalletError("z_getaddressforaccount returned no address")
        return address

    async def received(self, addresses: list[str], min_confirmations: int = 0) -> list[ReceivedNote]:
        if not addresses:
            return []
        wanted = set(addresses)
        # Filtered client-side: the `addresses` argument's handling of Unified Addresses differs between wallets
        rows = await self.call("z_listunspent", min_confirmations, 9_999_999, False)
        notes = []
        for row in rows or []:
            note = _note(row)
            if note is not None and note.address in wanted:
                notes.append(note)
        return notes

    # ---- sending ------------------------------------------------------------------------------------------

    async def send(self, to: str, amount: Decimal, memo: str) -> SendResult:
        from .memo import memo_hex

        recipient: dict[str, Any] = {"address": to, "amount": float(amount.quantize(ZATOSHI))}
        if memo:
            recipient["memo"] = memo_hex(memo)
        result = await self.call("z_sendmany", self.from_address, [recipient], self.send_minconf, None, self.privacy_policy)
        if isinstance(result, str):  # zcashd: an async operation id
            return SendResult(operation_id=result)
        if isinstance(result, dict):  # Zallet may return the transaction ids directly
            txids = tuple(result.get("txids") or ([result["txid"]] if result.get("txid") else []))
            if result.get("broadcast") is False:
                raise WalletError("the wallet built the transaction but did not broadcast it (external.broadcast is off)")
            return SendResult(operation_id=str(result.get("operationid", "")), txids=txids)
        raise WalletError(f"z_sendmany returned {type(result).__name__}")

    async def operation(self, operation_id: str) -> OperationStatus:
        rows = await self.call("z_getoperationstatus", [operation_id])
        row = next((r for r in rows or [] if r.get("id") == operation_id), None)
        if row is None:
            return OperationStatus("unknown")
        status = row.get("status", "unknown")
        result = row.get("result") or {}
        txids = tuple(result.get("txids") or ([result["txid"]] if result.get("txid") else []))
        error = (row.get("error") or {}).get("message", "")
        return OperationStatus(status if status in ("queued", "executing", "success", "failed", "cancelled") else "unknown", txids, error)

    async def transaction(self, txid: str) -> TxStatus | None:
        try:
            tx = await self.call("z_viewtransaction", txid)
        except WalletError as e:
            if isinstance(e, WalletUnavailable):
                raise
            return None
        confirmations = tx.get("confirmations")
        if confirmations is None:  # zcashd's z_viewtransaction has no confirmations: ask gettransaction
            try:
                confirmations = (await self.call("gettransaction", txid)).get("confirmations", 0)
            except WalletError:
                confirmations = 0
        memos = tuple(
            o.get("memoStr") or decode_memo_hex(o.get("memo")) for o in tx.get("outputs", []) if o.get("outgoing") and o.get("pool") in SHIELDED_POOLS
        )
        return TxStatus(txid=txid, confirmations=int(confirmations), outgoing_memos=memos)

    async def recent_outgoing(self, limit: int = 200) -> list[TxStatus]:
        txids: list[str] = []
        for method, params in (("z_listtransactions", ()), ("listtransactions", ("*", limit))):
            try:
                rows = await self.call(method, *params)
            except WalletUnavailable:
                raise
            except WalletError:
                continue
            txids = list(dict.fromkeys(r["txid"] for r in rows or [] if isinstance(r, dict) and r.get("txid")))[-limit:]
            break
        out = []
        for txid in txids:
            if (tx := await self.transaction(txid)) is not None and tx.outgoing_memos:
                out.append(tx)
        return out

    async def validate_address(self, address: str) -> str | None:
        try:
            receivers = await self.call("z_listunifiedreceivers", address)
        except WalletUnavailable:
            raise
        except WalletError as e:
            return f"the wallet rejected this address: {e}"
        if not isinstance(receivers, dict) or not (SHIELDED_POOLS & set(receivers)):
            return "this Unified Address has no shielded (Orchard or Sapling) receiver"
        return None

    async def info(self) -> WalletInfo:
        info = WalletInfo(backend=self.name, network=self.network, simulated=False, reachable=False)
        try:
            await self.call("getwalletinfo")
            info.reachable = True
            balance = await self.call("z_getbalanceforaccount", self.account, 1)
            pools = (balance or {}).get("pools", {})
            info.escrow_balance = zec_from_zatoshi(sum(int(p.get("valueZat", 0)) for p in pools.values()))
        except WalletUnavailable as e:
            info.detail = str(e)
        except WalletError as e:
            info.detail = str(e)
            info.warnings.append("the wallet answered but could not report the escrow balance")
        return info

    async def aclose(self) -> None:
        await self._http.aclose()


def _note(row: dict[str, Any]) -> ReceivedNote | None:
    pool = row.get("pool") or ("sapling" if "outindex" in row else "")
    if pool not in SHIELDED_POOLS or row.get("walletInternal") or row.get("is_watch_only"):
        return None  # change and transparent outputs never pay an invoice
    zat = row.get("valueZat", row.get("amountZat"))
    amount = zec_from_zatoshi(int(zat)) if zat is not None else Decimal(str(row.get("value", row.get("amount", "0")))).quantize(ZATOSHI)
    memo = row.get("memoStr") or decode_memo_hex(row.get("memo"))
    return ReceivedNote(
        txid=str(row["txid"]),
        pool=pool,
        output_index=int(row.get("outindex", row.get("outputIndex", row.get("jsoutindex", 0)))),
        address=str(row.get("address", "")),
        amount=amount,
        confirmations=int(row.get("confirmations", 0)),
        memo=memo,
    )
