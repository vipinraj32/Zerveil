from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaymentMode(StrEnum):
    # In-process scanner stand-in: invoices and scanning run end to end, every record is labeled simulated and no
    # ZEC moves. For development and tests.
    SIMULATED = "simulated"
    # A real view-only scanner (zcash-devtool + lightwalletd) on Zcash testnet. Refuses to start without its binary
    # and wallet directory; never falls back to simulation.
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
    # Operators: see payment events and scanner status (comma-separated Google emails). They cannot move funds.
    admin_emails: str = ""

    # ---- Zcash payments (direct, no escrow) ------------------------------------------------------------------
    # Zerveil never holds funds. Payers pay the recipient's own wallet; a view-only scanner confirms receipt using
    # a viewing key the recipient connected.
    payment_mode: PaymentMode = Field(PaymentMode.SIMULATED, validation_alias=AliasChoices("payment_mode", "zcash_payment_mode"))
    zcash_network: str = "testnet"  # testnet | mainnet | regtest; built and documented for testnet only
    zcash_testnet_enabled: bool = True  # must stay true: PAYMENT_MODE=zcash refuses mainnet in this MVP
    zcash_wallet_backend: str = "zcash-devtool"  # the only real scanner backend (see README, "Wallet selection")
    zcash_lightwalletd_url: str = "https://testnet.zec.rocks:443"
    zcash_devtool_bin: str = "zcash-devtool"  # path to the zcash-devtool binary
    zcash_devtool_wallet_dir: str = ""  # view-only wallet directory (holds imported viewing keys; keep it private)
    zcash_scanner_timeout_seconds: float = 900  # one sync can take long right after a viewing key is imported
    zcash_payment_scanner_enabled: bool = True  # the scheduler scans watched accounts for payments
    zcash_receiver_types: str = "orchard,sapling"  # receivers in invoice addresses: shielded only (orchard -> Ironwood under NU7)
    zcash_confirmation_threshold: int = 3  # confirmations before a payment counts as confirmed
    zcash_invoice_ttl_minutes: int = 1440  # campaign milestone invoices
    zcash_explorer_tx_url: str = ""  # e.g. https://testnet.zcashexplorer.app/transactions/{txid}; empty = no links
    zcash_faucet_url: str = "https://zcash-faucet.vercel.app/"
    # The API seller's receiving account (HTTP 402 premium search and agent-credit top-ups are paid to it)
    zcash_api_seller_ufvk: str = ""
    zcash_api_seller_birthday: int = 0

    # USD is the business currency; the payment asset is shielded ZEC (TAZ on testnet), converted per invoice.
    # Testnet TAZ has no market price: this is a TEST RATE (USD per 1 TAZ), labeled as such wherever it is shown.
    # It is not a market price and gives TAZ no value. Mainnet pricing (a live ZEC/USD source) is not implemented.
    zcash_test_usd_rate: Decimal = Field(Decimal("100"), gt=0)

    # HTTP 402: price of one premium API call in USD, and how long its invoice stays payable
    premium_search_price: Decimal = Field(Decimal("0.05"), validation_alias=AliasChoices("premium_search_price_usd", "premium_search_price"))
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
        if isinstance(v, str) and v.lower() == "escrow":
            raise ValueError("the escrow wallet was removed: payments go directly to the recipient's wallet; use simulated or zcash")
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
