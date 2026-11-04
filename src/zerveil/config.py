from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaymentMode(StrEnum):
    # In-process wallet stand-in: invoices, scanning and payouts run end to end, every record is labeled simulated and
    # no ZEC moves. For development and tests.
    SIMULATED = "simulated"
    # A real Zcash wallet (Zallet or a zcashd-compatible wallet RPC) on the configured network. Refuses to start
    # without the wallet URL, escrow account and from-address; never falls back to simulation.
    ZCASH = "zcash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_name: str = "Zerveil"
    app_env: str = "development"

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"

    # Storage: empty → in-memory store (tests, quick local runs)
    database_url: str = ""

    # Login: Google OAuth (influencers also grant youtube.readonly to prove their channel)
    google_oauth_client_id: str = Field("", validation_alias=AliasChoices("google_oauth_client_id", "google_client_id"))
    google_oauth_client_secret: str = Field("", validation_alias=AliasChoices("google_oauth_client_secret", "google_client_secret"))
    session_secret: str = ""  # signs session cookies, encrypts influencer agent keys, MACs agent authorizations
    # Where the browser lands after login (the Vite dev server in development)
    frontend_url: str = ""
    # Bearer token for the stdio MCP server (read-only operator view); empty disables it
    mcp_api_token: str = ""
    # Accounts allowed to approve payouts and see the custody reconciliation (comma-separated Google emails)
    admin_emails: str = ""

    # ---- Zcash payments -------------------------------------------------------------------------------------
    payment_mode: PaymentMode = Field(PaymentMode.SIMULATED, validation_alias=AliasChoices("payment_mode", "zcash_payment_mode"))
    zcash_network: str = "testnet"  # testnet | mainnet | regtest; the MVP is built and documented for testnet only
    # Wallet JSON-RPC (Zallet). Plain HTTP: keep it on loopback, an SSH tunnel or a private network.
    zcash_wallet_rpc_url: str = Field("", validation_alias=AliasChoices("zcash_wallet_rpc_url", "zcash_wallet_backend_url"))
    zcash_wallet_rpc_user: str = ""
    zcash_wallet_rpc_password: str = Field("", validation_alias=AliasChoices("zcash_wallet_rpc_password", "zcash_wallet_auth_token"))
    # The escrow account inside that wallet (account UUID for Zallet, or a zcashd account number) and one of its
    # Unified Addresses, which z_sendmany spends from
    zcash_escrow_account: str = Field("", validation_alias=AliasChoices("zcash_escrow_account", "escrow_wallet_reference"))
    zcash_escrow_from_address: str = ""
    zcash_receiver_types: str = "orchard,sapling"  # receivers in invoice addresses: shielded only
    zcash_privacy_policy: str = "FullPrivacy"  # z_sendmany privacy policy for payouts
    zcash_confirmation_threshold: int = 3  # confirmations before an invoice counts as paid
    zcash_send_minconf: int = 3  # minimum confirmations of the notes a payout spends
    zcash_invoice_ttl_minutes: int = 120  # funding and top-up invoices
    zcash_fee_reserve_per_deal: Decimal = Decimal("0.0003")  # ZIP-317 fees for up to three payouts per deal
    zcash_explorer_tx_url: str = ""  # e.g. https://testnet.zcashexplorer.app/transactions/{txid}; empty = no links
    # Payouts wait for an operator's approval before the wallet sends them (recommended; see PRIVACY.md)
    payout_approval_required: bool = True
    payout_reconcile_after_minutes: int = 10  # a send whose result was lost is looked up in the wallet after this

    # HTTP 402: price of one premium API call, and how long its invoice stays payable
    premium_search_price: Decimal = Decimal("0.05")
    api_invoice_ttl_minutes: int = 30

    # Negotiation: how long one agent reply may take before that creator's negotiation ends as TIMED_OUT
    negotiation_reply_timeout_seconds: float = 90

    # Verifier
    youtube_api_key: str = ""  # required to verify video proofs

    # Server
    public_base_url: str = "http://localhost:8000"

    @field_validator("payment_mode", mode="before")
    @classmethod
    def _known_mode(cls, v: object) -> object:
        if isinstance(v, str) and v.lower() in ("hosted", "sepolia"):
            raise ValueError(f"PAYMENT_MODE={v} was removed with the HSP/EVM payment flow: use simulated or zcash")
        return v

    @property
    def admins(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def ticker(self) -> str:
        return "ZEC" if self.zcash_network == "mainnet" else "TAZ"


@lru_cache
def get_settings() -> Settings:
    return Settings()
