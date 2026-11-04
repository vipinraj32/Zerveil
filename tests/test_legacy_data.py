"""Deals saved by the removed HSP/EVM payment flow stay readable as history: they must load, and the scheduler must
never act on them."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from zerveil.models import Deal, DealState

NOW = datetime(2026, 11, 2, 11, 50, tzinfo=UTC)

# A COMPLETED deal as the EVM-era code stored it (migration 2 archives only deals that were still mid-payment)
LEGACY = {
    "id": "DEAL-old1",
    "campaign_id": "CMP-old",
    "influencer_id": "USR-ava",
    "state": "COMPLETED",
    "terms": {
        "deal_id": "DEAL-old1", "campaign_id": "CMP-old", "advertiser": "FitFuel", "influencer_id": "USR-ava", "channel_id": "UCava",
        "payee": "0x" + "ab" * 20, "asset": "USDC", "network": "hashkey-testnet", "fixed_fee": "20", "cpm": "2.5", "cap": "40",
        "deliverable": "youtube_short", "publish_by": (NOW + timedelta(days=7)).isoformat(), "required_tags": ["#ad"],
        "influencer_signature": "0x" + "11" * 65,
    },
    "approvals": [{"kind": "payment", "by": "USR-adv", "approved": True, "terms_hash": "0x" + "22" * 32, "at": NOW.isoformat()}],
    "payouts": [
        {"kind": "fixed", "to": "0x" + "ab" * 20, "amount": "20", "tx_hash": "0x" + "33" * 32, "at": NOW.isoformat(), "simulated": True,
         "block_number": None, "chain_id": 133, "token": "0x" + "44" * 20, "views": None},
        {"kind": "refund", "to": "0x" + "cd" * 20, "amount": "20", "tx_hash": "0x" + "55" * 32, "at": NOW.isoformat(), "simulated": True},
    ],
    "funding_tx": "0x" + "66" * 32,
    "payment_status": "payment-finalized",
    "payment_attempts": [{"attempt_id": "x"}],
    "attestations": [{"kind": "fixed"}],
}  # fmt: skip


def test_legacy_evm_deal_loads_as_history():
    deal = Deal.model_validate(LEGACY)
    assert deal.state == DealState.COMPLETED
    assert deal.approvals[0].kind == "payment"
    assert [(p.kind, p.amount, p.payout_id, p.legacy_tx_hash) for p in deal.payouts] == [
        ("fixed", Decimal("20"), "", "0x" + "33" * 32),
        ("refund", Decimal("20"), "", "0x" + "55" * 32),
    ]
    assert deal.terms.network == "hashkey-testnet"
    Deal.model_validate(deal.model_dump(mode="json"))  # and round-trips


async def test_one_unreadable_deal_does_not_stop_the_store(caplog):
    from zerveil.store.postgres import _load_deals

    rows = [(LEGACY,), ({"id": "DEAL-broken", "state": "NO_SUCH_STATE"},)]
    deals = _load_deals(rows)
    assert [d.id for d in deals] == ["DEAL-old1"]
    assert "DEAL-broken" in caplog.text
