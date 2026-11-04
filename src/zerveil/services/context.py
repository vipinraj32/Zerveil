"""Wires backends from settings.

Payments: PAYMENT_MODE=zcash uses a real view-only payment scanner (zcash-devtool against lightwalletd) on Zcash
testnet and refuses to start without it; it never falls back to simulation. PAYMENT_MODE=simulated runs the same
invoice and scanning code against an in-process stand-in whose records are all labeled simulated.

The server holds no funds and no spending keys: advertisers pay from their own Zcash wallet directly to the creator's
own wallet. The scanner only holds viewing keys the recipients connected."""

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
from ..zcash.address import UA_HRP
from ..zcash.wallet import PaymentScanner
from .events import EventBus
from .registry import InfluencerRegistry


@dataclass
class AppContext:
    settings: Settings
    store: Store
    clock: Clock
    wallet: PaymentScanner  # view-only payment scanner
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
    missing = [name for name, value in {"ZCASH_DEVTOOL_WALLET_DIR": settings.zcash_devtool_wallet_dir,
                                        "ZCASH_LIGHTWALLETD_URL": settings.zcash_lightwalletd_url}.items() if not value]  # fmt: skip
    if settings.zcash_wallet_backend != "zcash-devtool":
        missing.append(f"ZCASH_WALLET_BACKEND=zcash-devtool (got {settings.zcash_wallet_backend!r}; no other real scanner is supported)")
    if settings.zcash_network != "testnet" or not settings.zcash_testnet_enabled:
        missing.append(f"ZCASH_NETWORK=testnet with ZCASH_TESTNET_ENABLED=true (got {settings.zcash_network!r}; this MVP runs on testnet only)")
    return missing


def build_wallet(settings: Settings) -> tuple[PaymentScanner, list[str]]:
    if settings.payment_mode == PaymentMode.ZCASH:
        missing = zcash_mode_missing(settings)
        if missing:
            raise RuntimeError("PAYMENT_MODE=zcash is unavailable, missing: " + ", ".join(missing))
        import shutil

        from ..zcash.devtool import DevtoolScanner, server_arg

        if shutil.which(settings.zcash_devtool_bin) is None:
            raise RuntimeError(f'PAYMENT_MODE=zcash: zcash-devtool binary not found ({settings.zcash_devtool_bin}); see README, "Wallet setup"')
        scanner = DevtoolScanner(
            binary=settings.zcash_devtool_bin,
            wallet_dir=settings.zcash_devtool_wallet_dir,
            server=server_arg(settings.zcash_lightwalletd_url),
            network=settings.zcash_network,
            receiver_pools=tuple(r.strip() for r in settings.zcash_receiver_types.split(",") if r.strip()),
            timeout_seconds=settings.zcash_scanner_timeout_seconds,
        )
        return scanner, [f"PAYMENT_MODE=zcash: direct shielded payments on Zcash testnet, confirmed by zcash-devtool via {scanner.server}"]
    from ..zcash.simulated import SimulatedScanner

    network = settings.zcash_network if settings.zcash_network in UA_HRP else "testnet"
    return SimulatedScanner(network=network), ["PAYMENT_MODE=simulated: invoices and scanning run against an in-process stand-in; no ZEC moves"]


async def build_context(
    settings: Settings,
    a2a_http: httpx.AsyncClient | None = None,
    clock: Clock | None = None,
    llm: BaseChatModel | None = None,
    wallet: PaymentScanner | None = None,
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
        warnings.append("ADMIN_EMAILS not set: nobody can see payment events and scanner status")

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
