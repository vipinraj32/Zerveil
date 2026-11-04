"""Payment endpoints through the real app (MOCKED: simulated scanner): HTTP 402 premium search paid to the API
seller's wallet, the agent paying it from credit, payment links paid to the creator's own wallet, the public payment
page, invoice privacy, and operator-only read routes."""

from decimal import Decimal

from tests.conftest import ua

from .test_e2e import ADMIN, client_for, make_admin, onboarded, server  # noqa: F401 - pytest fixtures


async def test_premium_search_is_payment_gated(onboarded):
    app, _, advertiser_id = onboarded
    async with client_for(app, advertiser_id) as adv:
        res = await adv.get("/api/influencers/premium-search", params={"niche": "fitness"})
        assert res.status_code == 402
        body = res.json()
        pay = body["payment"]
        assert body["error"] == "payment_required" and res.headers["X-Payment-Id"] == pay["id"] == body["retry_with_header"]["X-Payment-Id"]
        assert pay["address"].startswith("utest1") and pay["memo"] == f"ZRV1:{pay['id']}" and Decimal(pay["amount"]) == Decimal("0.05")
        assert pay["zip321_uri"].startswith("zcash:") and pay["simulated"] is True and pay["direct"] is True
        seller = await app.state.zx.deals.payments.receiving_account("api-seller")
        assert (await app.state.zx.ctx.store.get_invoice(pay["id"])).receiving_account_id == seller.id
        headers = {"X-Payment-Id": pay["id"]}

        # Not paid yet: the same invoice again
        again = await adv.get("/api/influencers/premium-search", params={"niche": "fitness"}, headers=headers)
        assert again.status_code == 402 and again.json()["payment"]["id"] == pay["id"]
        # Paid with too few confirmations: still 402
        await adv.post(f"/api/dev/payments/{pay['id']}/simulate", json={"confirmations": 1})
        assert (await adv.get("/api/influencers/premium-search", params={"niche": "fitness"}, headers=headers)).json()["payment"]["status"] == "mined"
        app.state.zx.ctx.wallet.confirm(2)
        await app.state.zx.deals.tick()

        ok = await adv.get("/api/influencers/premium-search", params={"niche": "fitness"}, headers=headers)
        assert ok.status_code == 200, ok.text
        results = ok.json()["results"]
        assert {r["id"] for r in results} == {"USR-ava", "USR-raj", "USR-mia", "USR-leo"}
        assert all("rate_card" not in r and "payout_address" not in r for r in results)
        # One payment unlocks one request, for its own scope
        assert (await adv.get("/api/influencers/premium-search", params={"niche": "fitness"}, headers=headers)).status_code == 409
        assert (await adv.get("/api/influencers/premium-search", params={"niche": "food"}, headers=headers)).status_code == 400


async def test_payment_id_of_someone_else_is_refused(onboarded):
    app, _, advertiser_id = onboarded
    async with client_for(app, advertiser_id) as adv, await make_admin(app) as other:
        pay = (await adv.get("/api/influencers/premium-search", params={"niche": "fitness"})).json()["payment"]
        await adv.post(f"/api/dev/payments/{pay['id']}/simulate", json={})
        res = await other.get("/api/influencers/premium-search", params={"niche": "fitness"}, headers={"X-Payment-Id": pay["id"]})
        assert res.status_code == 400


async def test_agent_pays_premium_search_from_credit(onboarded):
    app, _, advertiser_id = onboarded
    async with client_for(app, advertiser_id) as adv:
        refused = (await adv.post("/api/agent/premium-search", json={"niche": "fitness"})).json()
        assert refused["payment"]["status"] == "refused" and "results" not in refused
        topup = (await adv.post("/api/agent/credit/topup", json={"amount": "0.2"})).json()
        assert topup["purpose"] == "credit_topup"
        await adv.post(f"/api/dev/payments/{topup['id']}/simulate", json={})
        await app.state.zx.deals.tick()
        assert Decimal((await adv.get("/api/agent/credit")).json()["balance"]) == Decimal("0.2")

        paid = (await adv.post("/api/agent/premium-search", json={"niche": "fitness"})).json()
        assert paid["payment"]["status"] == "paid" and paid["results"] and paid["invoice"]["paid_via"] == "agent_credit"
        assert Decimal((await adv.get("/api/agent/credit")).json()["balance"]) == Decimal("0.15")

        # Above the auto-approve limit the agent asks its owner
        policy = (await adv.get("/api/agent/spending-policy")).json()
        assert (await adv.put("/api/agent/spending-policy", json={**policy, "approval_above": "0.01"})).status_code == 200
        pending = (await adv.post("/api/agent/premium-search", json={"niche": "fitness"})).json()
        assert pending["payment"]["status"] == "pending_approval"
        request_id = pending["payment"]["request_id"]
        assert (await adv.get("/api/agent/credit")).json()["pending_requests"][0]["id"] == request_id
        assert (await adv.post(f"/api/agent/spend-requests/{request_id}", json={"approved": True})).json()["status"] == "paid"


async def test_payment_link_public_page_and_privacy(onboarded):
    app, _, advertiser_id = onboarded
    async with client_for(app, "USR-ava") as ava, client_for(app, advertiser_id) as adv, client_for(app) as anon:
        assert (await adv.post("/api/payments/links", json={"title": "Tip jar", "amount": "1"})).status_code == 403  # creators only
        link = (await ava.post("/api/payments/links", json={"title": "Shout-out", "amount": "0.5", "description": "One mention"})).json()
        page = (await anon.get(f"/api/pay/{link['id']}")).json()
        assert page["payee_name"] == "Ava TV" and page["amount"] == "0.5" and page["status"] == "awaiting_payment"
        assert "notes" not in page and "owner_id" not in page and ua("ava") not in str(page)  # the creator's own address stays private
        # anyone may pay a link (simulated)
        assert (await anon.post(f"/api/dev/payments/{link['id']}/simulate", json={})).status_code == 401
        paid = (await adv.post(f"/api/dev/payments/{link['id']}/simulate", json={})).json()
        assert paid["status"] == "confirmed" and "notes" not in paid
        mine = (await ava.get(f"/api/payments/invoices/{link['id']}")).json()
        assert mine["notes"][0]["txid"].startswith("sim") and mine["events"]
        assert (await adv.get(f"/api/payments/invoices/{link['id']}")).status_code == 404  # a payer does not see the creator's records
        receipts = (await ava.get("/api/receipts")).json()
        assert receipts[0]["invoice_id"] == link["id"] and receipts[0]["recipient_id"] == "USR-ava" and receipts[0]["payer_id"] == ""
        assert (await ava.get("/api/payments/payouts")).status_code in (404, 405)  # no payouts: nothing passes through Zerveil
        assert (await anon.get("/api/pay/PAY-doesnotexist")).status_code == 404


async def test_admin_routes_need_an_admin(onboarded):
    app, _, advertiser_id = onboarded
    async with client_for(app, advertiser_id) as adv, client_for(app) as anon, await make_admin(app) as admin:
        for path in ("/api/admin/scanner", "/api/admin/payment-events", "/api/admin/invoices"):
            assert (await adv.get(path)).status_code == 403, path
            assert (await anon.get(path)).status_code == 401, path
            assert (await admin.get(path)).status_code == 200, path
        # The escrow routes are gone: operators cannot approve, send or hold anything
        for path in ("/api/admin/payouts", "/api/admin/custody"):
            assert (await admin.get(path)).status_code == 404, path
        assert (await admin.post("/api/admin/payouts/PO-x/decision", json={"approved": True})).status_code in (404, 405)
    assert ADMIN in app.state.zx.ctx.settings.admins


async def test_milestone_invoice_cannot_be_cancelled_by_hand(onboarded):
    from .test_e2e import CAMPAIGN

    app, _, advertiser_id = onboarded
    async with client_for(app, advertiser_id) as adv:
        cid = (await adv.post("/api/campaigns", json=CAMPAIGN)).json()["spec"]["id"]
        await app.state.zx.runner.wait_idle()
        await adv.post(f"/api/campaigns/{cid}/decision", json={"approved": True})
        await app.state.zx.runner.wait_idle()
        invoice = (await adv.get(f"/api/campaigns/{cid}")).json()["payment_invoices"][0]
        assert (await adv.post(f"/api/payments/invoices/{invoice['id']}/cancel")).status_code == 409
        topup = (await adv.post("/api/agent/credit/topup", json={"amount": "0.1"})).json()
        assert (await adv.post(f"/api/payments/invoices/{topup['id']}/cancel")).json()["status"] == "cancelled"
