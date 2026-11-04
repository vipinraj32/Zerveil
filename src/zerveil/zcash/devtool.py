"""A real view-only payment scanner: `zcash-devtool` (https://github.com/zcash/zcash-devtool), a light client built
on librustzcash, run as a subprocess against a lightwalletd server (testnet: testnet.zec.rocks:443).

Why this backend (see README, "Wallet selection"):
- It imports Unified Full Viewing Keys as view-only accounts (`wallet import-ufvk` / `wallet init-fvk`). Zallet's
  `z_importviewingkey` only takes Sapling keys, so it cannot watch Ironwood/Orchard receipts.
- It needs only a lightwalletd server, not a full node.
- Its main branch builds against the librustzcash NU7 pre-releases, and the public testnet faucet runs on it.

Only these subcommands are used, with flags taken from the zcash-devtool sources:
- `wallet -w DIR init-fvk --name N --fvk UFVK --birthday H -s SERVER`
- `wallet -w DIR import-ufvk --name N UFVK H -s SERVER`
- `wallet -w DIR list-accounts`
- `wallet -w DIR generate-address UUID -p orchard [-p sapling]`
- `wallet -w DIR sync -s SERVER`
- `wallet -w DIR list-tx UUID`
- `wallet -w DIR get-info -s SERVER`

The text output of `list-tx` is parsed (it has no per-output JSON). The format is pinned by tests built from the
upstream print statements. If a newer zcash-devtool changes it, the parser fails closed: it reports nothing as paid.

The wallet directory holds the imported viewing keys. Keep it private and backed up; it never holds a spending key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .address import unified_address_problem
from .wallet import ReceivedNote, WalletError, WalletInfo, WalletUnavailable

log = logging.getLogger(__name__)

# NU7 activation heights (zcash_protocol NU7 cohort; Zallet 0.1.0-beta.4 release notes)
NU7_ACTIVATION = {"testnet": 4_465_026, "mainnet": 3_543_405}

_TXID = re.compile(r"^([0-9a-f]{64})\s*$")
_MINED = re.compile(r"^\s+Mined: (\d+)")
_EXPIRED = re.compile(r"^\s+Expired \(expiry height")
_OUTPUT = re.compile(r"^\s+Output (\d+) \(([^)]*)\)")
_VALUE = re.compile(r"^\s+Value: ([0-9]+(?:\.[0-9]+)?)(.*)$")
_RECEIVED_BY = re.compile(r"^\s+Received by account: (\S+)")
_TO = re.compile(r"^\s+To: (\S+)")
_MEMO = re.compile(r"^\s+Memo: (.*)$")
_ACCOUNT = re.compile(r"^Account (\S+) \(birthday height (\d+)\)")
_UFVK_LINE = re.compile(r"^\s+UFVK: (\S+)")
_ADDRESS = re.compile(r"Address: (\S+)")
_MEMO_REF = re.compile(r"ZRV1:[A-Z]{2,4}-[0-9a-f]{6,32}")


def server_arg(url: str) -> str:
    """`https://testnet.zec.rocks:443` → `testnet.zec.rocks:443` (zcash-devtool takes host:port)."""
    host = re.sub(r"^[a-z]+://", "", url.strip()).rstrip("/")
    return host if ":" in host else f"{host}:443"


def _pool(text: str) -> str:
    t = text.lower()
    for name in ("ironwood", "orchard", "sapling", "transparent"):
        if name in t:
            return name
    return t


@dataclass
class _Output:
    index: int
    pool: str
    value: Decimal | None = None
    received: bool = False
    change: bool = False
    to: str = ""
    memo: str = ""


@dataclass
class _Tx:
    txid: str
    mined_height: int | None = None
    expired: bool = False
    outputs: list[_Output] = field(default_factory=list)


def parse_list_tx(text: str) -> list[_Tx]:
    """Parses `zcash-devtool wallet list-tx` text output into transactions and their outputs."""
    txs: list[_Tx] = []
    out: _Output | None = None
    for line in text.splitlines():
        if m := _TXID.match(line):
            txs.append(_Tx(txid=m.group(1)))
            out = None
            continue
        if not txs:
            continue
        tx = txs[-1]
        if m := _OUTPUT.match(line):
            out = _Output(index=int(m.group(1)), pool=_pool(m.group(2)))
            tx.outputs.append(out)
        elif out is None and (m := _MINED.match(line)):
            tx.mined_height = int(m.group(1))
        elif out is None and _EXPIRED.match(line):
            tx.expired = True
        elif out is not None and (m := _VALUE.match(line)):
            try:
                out.value = Decimal(m.group(1))
            except InvalidOperation:
                out.value = None
            out.change = "(Change)" in m.group(2) or "(Wallet Internal Transfer)" in m.group(2)
        elif out is not None and _RECEIVED_BY.match(line):
            out.received = True
        elif out is not None and (m := _TO.match(line)):
            out.to = m.group(1)
        elif out is not None and (m := _MEMO.match(line)):
            ref = _MEMO_REF.search(m.group(1))
            out.memo = ref.group(0) if ref else ""
    return txs


def notes_from(txs: list[_Tx], tip: int | None) -> list[ReceivedNote]:
    """Received shielded outputs (not change, not sent) with confirmations computed from the chain tip."""
    notes: list[ReceivedNote] = []
    for tx in txs:
        for o in tx.outputs:
            if not o.received or o.change or o.value is None or o.pool == "transparent":
                continue
            confs = tip - tx.mined_height + 1 if tx.mined_height is not None and tip is not None and tip >= tx.mined_height else 0
            notes.append(ReceivedNote(txid=tx.txid, pool=o.pool, output_index=o.index, address=o.to, amount=o.value, confirmations=confs,
                                      memo=o.memo, mined_height=tx.mined_height, expired=tx.expired))  # fmt: skip
    return notes


@dataclass
class DevtoolScanner:
    binary: str
    wallet_dir: str
    server: str  # host:port
    network: str = "testnet"
    receiver_pools: tuple[str, ...] = ("orchard", "sapling")
    timeout_seconds: float = 900
    sync_every_seconds: float = 20
    name: str = "zcash-devtool"
    simulated: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_sync: float = 0.0
    _tip: int | None = None

    def _run_sync(self, args: list[str], timeout: float) -> str:
        cmd = [self.binary, "wallet", "-w", self.wallet_dir, *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 - fixed argv, no shell
        except FileNotFoundError as e:
            raise WalletUnavailable(f"zcash-devtool not found at {self.binary}") from e
        except subprocess.TimeoutExpired as e:
            raise WalletUnavailable(f"zcash-devtool {args[0]} timed out after {timeout:.0f}s") from e
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip().splitlines()
            message = err[-1][:300] if err else str(proc.returncode)
            for arg in args:  # never echo a viewing key, even if the tool printed it
                if len(arg) > 40:
                    message = message.replace(arg, "[redacted]")
            raise WalletError(f"zcash-devtool {args[0]} failed: {message}")
        return proc.stdout

    async def _run(self, args: list[str], limit: float | None = None) -> str:
        return await asyncio.to_thread(self._run_sync, args, limit or self.timeout_seconds)

    def _initialised(self) -> bool:
        d = Path(self.wallet_dir)
        return d.is_dir() and any(d.iterdir())

    def _conn(self) -> list[str]:
        return ["-s", self.server, "--connection", "direct"]

    async def _accounts(self) -> list[tuple[str, str]]:
        """(uuid, ufvk) of every account in the wallet directory; [] when it is not initialised yet."""
        if not await asyncio.to_thread(self._initialised):
            return []
        out = await self._run(["list-accounts"], 60)
        accounts: list[tuple[str, str]] = []
        current = ""
        for line in out.splitlines():
            if m := _ACCOUNT.match(line):
                current = m.group(1)
                accounts.append((current, ""))
            elif current and (m := _UFVK_LINE.match(line)):
                accounts[-1] = (current, m.group(1))
        return accounts

    async def import_viewing_key(self, label: str, ufvk: str, birthday: int) -> str:
        async with self._lock:
            existing = await self._accounts()
            for uuid, key in existing:
                if key == ufvk:
                    return uuid
            if not existing:
                await asyncio.to_thread(Path(self.wallet_dir).mkdir, parents=True, exist_ok=True)
                await self._run(["init-fvk", "--name", label, "--fvk", ufvk, "--birthday", str(birthday), *self._conn()], 300)
            else:
                await self._run(["import-ufvk", "--name", label, ufvk, str(birthday), *self._conn()], 300)
            known = {u for u, _ in existing}
            for uuid, key in await self._accounts():
                if key == ufvk or uuid not in known:
                    return uuid
            raise WalletError("the viewing key was imported but its account was not found")

    async def new_address(self, account: str) -> str:
        pools = [a for p in self.receiver_pools for a in ("-p", p)]
        async with self._lock:
            out = await self._run(["generate-address", account, *pools], 120)
        m = _ADDRESS.search(out)
        if m is None:
            raise WalletError("zcash-devtool did not print an address")
        address = m.group(1)
        if problem := unified_address_problem(address, self.network):  # type: ignore[arg-type]
            raise WalletError(f"zcash-devtool returned an unusable address: {problem}")
        return address

    async def _tip_height(self) -> int | None:
        out = await self._run(["get-info", *self._conn()], 60)
        try:
            return int(json.loads(out.strip().splitlines()[-1])["chain_tip_height"])
        except (ValueError, KeyError, IndexError, TypeError):
            return None

    async def _sync(self) -> None:
        if time.monotonic() - self._last_sync < self.sync_every_seconds:
            return
        await self._run(["sync", *self._conn()])
        self._tip = await self._tip_height()
        self._last_sync = time.monotonic()

    async def received(self, account: str, addresses: list[str]) -> list[ReceivedNote]:
        async with self._lock:
            await self._sync()
            out = await self._run(["list-tx", account], 120)
        wanted = set(addresses)
        return [n for n in notes_from(parse_list_tx(out), self._tip) if n.address in wanted or (not n.address and n.memo)]

    async def info(self) -> WalletInfo:
        activation = NU7_ACTIVATION.get(self.network)
        try:
            out = await self._run(["get-info", *self._conn()], 60)
            data = json.loads(out.strip().splitlines()[-1])
            tip = int(data["chain_tip_height"])
        except WalletError as e:
            return WalletInfo(backend=self.name, network=self.network, simulated=False, reachable=False, server=self.server,
                              nu7_activation_height=activation, detail=str(e))  # fmt: skip
        except (ValueError, KeyError, IndexError, TypeError):
            return WalletInfo(backend=self.name, network=self.network, simulated=False, reachable=False, server=self.server,
                              nu7_activation_height=activation, detail="unexpected get-info output")  # fmt: skip
        warnings = []
        chain = str(data.get("chain_name", ""))
        expected = {"testnet": "test", "mainnet": "main"}.get(self.network, "")
        if expected and chain and chain != expected:
            warnings.append(f"lightwalletd serves chain {chain!r}, but ZCASH_NETWORK={self.network}")
        return WalletInfo(backend=self.name, network=self.network, simulated=False, reachable=True, server=self.server, chain_name=chain,
                          tip_height=tip, nu7_activation_height=activation, nu7_active=tip >= activation if activation else None,
                          warnings=warnings)  # fmt: skip
