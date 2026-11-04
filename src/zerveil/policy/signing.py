"""Signing in fixed code paths only, never as an LLM or MCP tool."""

from __future__ import annotations

from ..auth.crypto import sign_text
from ..models import DealTerms


def sign_terms(private_key_hex: str, terms: DealTerms) -> DealTerms:
    """The influencer agent's Ed25519 signature over the canonical terms hash."""
    return terms.model_copy(update={"influencer_signature": sign_text(private_key_hex, terms.terms_hash())})
