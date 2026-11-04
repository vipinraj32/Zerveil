"""Influencer agent keys and the creator's authorization of them.

An influencer's agent signs DealTerms while the creator is offline, so its key is generated server-side (Ed25519) and
stored encrypted with a key derived from SESSION_SECRET. It never leaves the server and is never exposed to an LLM or
MCP tool. When the creator switches the agent on while signed in, the server records a delegation statement (agent
key, channel, payout address) and MACs it, so a statement edited in the database no longer lists the creator.

Zcash wallets do not offer a general message-signing API for shielded addresses, so the payout address is not
proven by a wallet signature: the creator enters it while signed in, the wallet backend checks that it is a valid
shielded Unified Address, and it is locked into every deal the agent signs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = "ed25519:"


def _fernet(secret: str) -> Fernet:
    key = hashlib.sha256(f"zerveil-agent-keys:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def public_key_of(private_hex: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return PREFIX + raw.hex()


def new_agent_key(secret: str) -> tuple[str, str]:
    """Returns (public key "ed25519:<hex>", encrypted private key)."""
    raw = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return public_key_of(raw.hex()), _fernet(secret).encrypt(raw.hex().encode()).decode()


def decrypt_agent_key(secret: str, encrypted: str) -> str:
    return _fernet(secret).decrypt(encrypted.encode()).decode()


def sign_text(private_hex: str, message: str) -> str:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex)).sign(message.encode()).hex()


def verify_text(public_key: str, message: str, signature: str) -> bool:
    if not public_key.startswith(PREFIX):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key.removeprefix(PREFIX))).verify(bytes.fromhex(signature), message.encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def delegation_statement(email: str, channel_id: str, agent_signer: str, payout_address: str, issued: str) -> str:
    return "\n".join([
        "Zerveil agent authorization",
        f"Account: {email}",
        f"Channel: {channel_id}",
        f"Agent signer: {agent_signer}",
        f"Payout address: {payout_address}",
        "I authorize this agent key to sign Zerveil deal terms for my channel. Payouts go to this shielded address.",
        f"Issued: {issued}",
    ])  # fmt: skip


def delegation_mac(secret: str, statement: str) -> str:
    key = hashlib.sha256(f"zerveil-delegation:{secret}".encode()).digest()
    return "hmac-sha256:" + hmac.new(key, statement.encode(), hashlib.sha256).hexdigest()


def delegation_valid(secret: str, statement: str, mac: str, *, channel_id: str, agent_signer: str, payout_address: str) -> bool:
    lines = set(statement.splitlines())
    return (
        hmac.compare_digest(delegation_mac(secret, statement), mac)
        and f"Channel: {channel_id}" in lines
        and f"Agent signer: {agent_signer}" in lines
        and f"Payout address: {payout_address}" in lines
    )
