"""Wires backends from settings.

Payments: PAYMENT_MODE=zcash uses a real Zcash wallet over JSON-RPC (Zallet) and refuses to start without it; it
never falls back to the simulated wallet. PAYMENT_MODE=simulated runs the same invoice, scanning and payout code
against an in-process stand-in whose records are all labeled simulated.

The server holds no advertiser or creator wallet keys: advertisers pay from their own Zcash wallet, creators are paid
to their own shielded addresses. The escrow wallet's keys stay inside the wallet process."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

import httpx
from langchain_core.language_models import BaseChatModel

from ..agents.a2a_client import A2AConnector
from ..clock import Clock
from ..compliance import DisclosureRegistry
from ..config import PaymentMode, Settings
from ..store import MemoryStore, PostgresStore, Store
from ..verifier.channels import ChannelSource, YouTubeChannelApi
from ..verifier.content import CaptionSource, FrameSource, YouTubeCaptions, YouTubeFrames
from ..verifier.youtube import VideoSource, YouTubeDataApi
from ..zcash.address import UA_HRP, unified_address_problem
from ..zcash.wallet import ZcashWallet
from .events import EventBus
from .registry import InfluencerRegistry


@dataclass
class AppContext:
    settings: Settings
    store: Store
    clock: Clock
    wallet: ZcashWallet
    videos: VideoSource | None  # None until YOUTUBE_API_KEY is set: proofs cannot be verified
    a2a: A2AConnector
    llm: BaseChatModel | None
    registry: InfluencerRegistry
    disclosure: DisclosureRegistry  # versioned sponsorship-disclosure policies
    channels: ChannelSource | None = None  # public channel stats and uploads for creator profiles (YOUTUBE_API_KEY)
    captions: CaptionSource | None = None  # content agent: what is said in a posted video
    frames: FrameSource | None = None  # content agent: frames YouTube publishes for a posted video
    bus: EventBus = field(default_factory=EventBus)
    warnings: list[str] = field(default_factory=list)


def chat_model(settings: Settings) -> BaseChatModel | None:
    if not settings.anthropic_api_key:
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=settings.llm_model, api_key=settings.anthropic_api_key, max_tokens=1024)


def zcash_mode_missing(settings: Settings) -> list[str]:
    """Configuration PAYMENT_MODE=zcash cannot run without."""
    required = {
        "ZCASH_WALLET_RPC_URL": settings.zcash_wallet_rpc_url,
        "ZCASH_ESCROW_ACCOUNT": settings.zcash_escrow_account,
        "ZCASH_ESCROW_FROM_ADDRESS": settings.zcash_escrow_from_address,
    }
    missing = [name for name, value in required.items() if not value]
    if settings.zcash_network not in UA_HRP:
        missing.append(f"ZCASH_NETWORK (testnet, mainnet or regtest; got {settings.zcash_network!r})")
    elif settings.zcash_escrow_from_address and (problem := unified_address_problem(settings.zcash_escrow_from_address, settings.zcash_network)):  # type: ignore[arg-type]
        missing.append(f"ZCASH_ESCROW_FROM_ADDRESS ({problem})")
    return missing


def build_wallet(settings: Settings) -> tuple[ZcashWallet, list[str]]:
    if settings.payment_mode == PaymentMode.ZCASH:
        missing = zcash_mode_missing(settings)
        if missing:
            raise RuntimeError("PAYMENT_MODE=zcash is unavailable, missing: " + ", ".join(missing))
        from ..zcash.rpc import ZalletRpcWallet

        wallet = ZalletRpcWallet(
            settings.zcash_wallet_rpc_url,
            network=settings.zcash_network,
            account=settings.zcash_escrow_account,
            from_address=settings.zcash_escrow_from_address,
            user=settings.zcash_wallet_rpc_user,
            password=settings.zcash_wallet_rpc_password,
            receiver_types=[r.strip() for r in settings.zcash_receiver_types.split(",") if r.strip()],
            privacy_policy=settings.zcash_privacy_policy,
            send_minconf=settings.zcash_send_minconf,
        )
        warnings = [f"PAYMENT_MODE=zcash: real shielded payments on Zcash {settings.zcash_network} through the wallet at the configured RPC URL"]
        if settings.zcash_network == "mainnet":
            warnings.append("ZCASH_NETWORK=mainnet: this MVP is built and tested for testnet; real ZEC is at risk")
        return wallet, warnings
    from ..zcash.simulated import SimulatedZcashWallet

    network = settings.zcash_network if settings.zcash_network in UA_HRP else "testnet"
    return SimulatedZcashWallet(network=network), [
        "PAYMENT_MODE=simulated: invoices, scanning and payouts run against an in-process wallet stand-in; no ZEC moves"
    ]


async def build_context(
    settings: Settings,
    a2a_http: httpx.AsyncClient | None = None,
    clock: Clock | None = None,
    llm: BaseChatModel | None = None,
    wallet: ZcashWallet | None = None,
) -> AppContext:
    warnings: list[str] = []
    clock = clock or Clock()

    if wallet is None:
        wallet, wallet_warnings = build_wallet(settings)
        warnings += wallet_warnings

    store: Store
    if settings.database_url:
        store = await PostgresStore.connect(settings.database_url)
    else:
        store = MemoryStore()
        warnings.append("DATABASE_URL not set: using the in-memory store (data is lost on restart)")

    secret = settings.session_secret
    if not secret:
        secret = secrets.token_hex(32)
        warnings.append("SESSION_SECRET not set: sessions and influencer agent keys reset on restart")
    if not settings.admins:
        warnings.append("ADMIN_EMAILS not set: nobody can approve payouts")

    videos: VideoSource | None = None
    channels: ChannelSource | None = None
    if settings.youtube_api_key:
        videos = YouTubeDataApi(settings.youtube_api_key)
        channels = YouTubeChannelApi(settings.youtube_api_key, now=clock.now)
    else:
        warnings.append("YOUTUBE_API_KEY not set: video proofs cannot be verified")
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        warnings.append("GOOGLE_OAUTH_CLIENT_ID / _SECRET not set: nobody can sign in")

    ctx = AppContext(
        settings=settings,
        store=store,
        clock=clock,
        wallet=wallet,
        videos=videos,
        a2a=A2AConnector(a2a_http or httpx.AsyncClient(timeout=60)),
        llm=llm if llm is not None else chat_model(settings),
        registry=InfluencerRegistry(store, settings.public_base_url, secret, settings.zcash_network),
        disclosure=DisclosureRegistry(store),
        channels=channels,
        captions=YouTubeCaptions(),
        frames=YouTubeFrames(),
        warnings=warnings,
    )
    await ctx.disclosure.load()
    ctx.settings = settings.model_copy(update={"session_secret": secret})
    if ctx.llm is None:
        warnings.append("ANTHROPIC_API_KEY not set: agents negotiate with deterministic strategies")
    return ctx
