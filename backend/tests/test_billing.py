"""Koma Portal billing: grounded costs, freemium grants, Stripe top-ups.

All sync except the webhook (driven with asyncio.run). Stripe is faked:
no network, no keys — the fake module is injected into sys.modules.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api.ai import (
    COSTS,
    ensure_monthly_grant,
    get_balance,
    quote_cost,
    refund_job,
    spend_credits,
)
from komaverse.api.billing import (
    SUBSCRIPTION_PLANS,
    CheckoutBody,
    SubscribeBody,
    _credit_completed,
    cancel_subscription,
    checkout,
    portal,
    subscribe,
    webhook,
)
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, JobRow, PurchaseRow, SubscriptionRow


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory):
    from conftest import create_user

    with factory() as s:
        return create_user(s)


def test_quote_costs_are_grounded():
    assert COSTS == {"image": 3, "video": 25, "chat": 1, "voice": 2, "vision": 1}
    assert quote_cost("image") == 3
    assert quote_cost("video", 4.0) == 100  # 25/whole second, rounded up
    assert quote_cost("video", 4.2) == 125
    assert quote_cost("chat") == 1
    assert quote_cost("unknown-kind") == 0


def test_spend_free_when_paywall_off(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", False)
    with factory() as s:
        assert spend_credits(s, user.id, 100, "x") is True
        assert get_balance(s, user.id) == 0


def test_monthly_grant_applies_once(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 100)
    with factory() as s:
        assert ensure_monthly_grant(s, user.id) == 100
        s.commit()
        assert get_balance(s, user.id) == 100
    with factory() as s:
        assert ensure_monthly_grant(s, user.id) == 0
        assert get_balance(s, user.id) == 100


def test_spend_insufficient_without_grant(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 0)
    with factory() as s:
        assert spend_credits(s, user.id, 5, "image generation") is False
        assert get_balance(s, user.id) == 0


def test_generate_local_is_free(factory, user, monkeypatch):
    from komaverse.api.ai import GenerateRequest, generate

    monkeypatch.setattr(settings, "paywall_enabled", True)
    with factory() as s:
        job = generate(
            GenerateRequest(kind="image", prompt="x", provider="local"),
            BackgroundTasks(),
            s,
            user,
        )
    assert job.cost_credits == 0
    with factory() as s:
        assert get_balance(s, user.id) == 0  # free actions accrue no grant


def test_generate_video_bills_per_second(factory, user, monkeypatch):
    from komaverse.api.ai import GenerateRequest, generate

    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 100)
    with factory() as s:
        job = generate(
            GenerateRequest(kind="video", prompt="x", provider="openrouter", seconds=4.0),
            BackgroundTasks(),
            s,
            user,
        )
    assert job.cost_credits == 100
    with factory() as s:
        assert get_balance(s, user.id) == 0  # 100 free - 100 video


def test_generate_insufficient_is_402(factory, user, monkeypatch):
    from komaverse.api.ai import GenerateRequest, generate

    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 0)
    with factory() as s, pytest.raises(HTTPException) as exc:
        generate(
            GenerateRequest(kind="image", prompt="x", provider="cloud"),
            BackgroundTasks(),
            s,
            user,
        )
    assert exc.value.status_code == 402


def test_refund_pays_back_exact_debit(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    with factory() as s:
        job = JobRow(user_id=user.id, kind="video", provider="openrouter", cost_credits=100)
        s.add(job)
        s.commit()
        assert refund_job(s, job) == 100
        s.commit()
        assert get_balance(s, user.id) == 100
    with factory() as s:
        legacy = JobRow(user_id=user.id, kind="image", provider="cloud")
        s.add(legacy)
        s.commit()
        assert refund_job(s, legacy) == 0
        assert get_balance(s, user.id) == 100  # unchanged: legacy rows mint nothing


def _fake_stripe(session_id="cs_test_1", credits=500):
    fake = types.SimpleNamespace()
    created = {}

    class Sessions:
        @staticmethod
        def create(**kwargs):
            created.update(kwargs)
            return types.SimpleNamespace(
                id=session_id, url="https://checkout.stripe.test/s/1"
            )

    fake.checkout = types.SimpleNamespace(Session=Sessions)
    fake.created_kwargs = created
    return fake


def test_checkout_needs_stripe_keys(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    with factory() as s, pytest.raises(HTTPException) as exc:
        checkout(CheckoutBody(pack_id="topup-5"), s, user)
    assert exc.value.status_code == 503


def test_checkout_creates_pending_purchase(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setitem(sys.modules, "stripe", _fake_stripe())
    with factory() as s:
        out = checkout(CheckoutBody(pack_id="topup-5"), s, user)
    assert out["url"].startswith("https://checkout.stripe.test")
    with factory() as s:
        row = s.execute(
            __import__("sqlalchemy").select(PurchaseRow)
        ).scalars().one()
        assert (row.status, row.credits, row.amount_usd_cents) == ("pending", 500, 500)


def _run(coro):
    return asyncio.run(coro)


def _webhook_request(body: bytes = b"{}"):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "headers": [(b"stripe-signature", b"sig")]}, receive
    )


def test_webhook_credits_once_and_idempotent(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 0)

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "metadata": {"user_id": user.id, "credits": "500"},
            }
        },
    }
    fake = types.SimpleNamespace(
        Webhook=types.SimpleNamespace(
            construct_event=staticmethod(lambda payload, sig, secret: event)
        )
    )
    monkeypatch.setitem(sys.modules, "stripe", fake)
    with factory() as s:
        s.add(
            PurchaseRow(
                user_id=user.id,
                stripe_session_id="cs_test_1",
                amount_usd_cents=500,
                credits=500,
                status="pending",
            )
        )
        s.commit()
    with factory() as s:
        assert _run(webhook(_webhook_request(), s)) == {"status": "ok"}
    with factory() as s:
        assert get_balance(s, user.id) == 500
    # Retried delivery credits nothing more.
    with factory() as s:
        assert _run(webhook(_webhook_request(), s)) == {"status": "ok"}
    with factory() as s:
        assert get_balance(s, user.id) == 500


def test_credit_completed_unknown_session_is_quiet(factory, user):
    with factory() as s:
        _credit_completed(s, {"id": "cs_nope", "metadata": {}})
        assert get_balance(s, user.id) == 0


def test_cloud_chat_downgrades_when_broke(factory, user, monkeypatch):
    """Broke + cloud configured: free demo brain answers, no 402."""
    import komaverse.agent as agent_mod
    from komaverse.agent import run_agent_turn

    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 0)
    monkeypatch.setattr(agent_mod, "chat_configured", lambda *a, **k: True)
    with factory() as s:
        project = create_project(ProjectCreate(name="P"), s, user)
        pid = project.id
    with factory() as s:
        out = run_agent_turn("hello", pid, s, user)
    assert out["brain"] == "demo"
    assert "out of credits" in (out.get("cloud_error") or "")
    with factory() as s:
        assert get_balance(s, user.id) == 0


def test_demo_chat_stays_free(factory, user, monkeypatch):
    from komaverse.agent import run_agent_turn

    monkeypatch.setattr(settings, "paywall_enabled", True)
    with factory() as s:
        project = create_project(ProjectCreate(name="P"), s, user)
        pid = project.id
    with factory() as s:
        out = run_agent_turn("status", pid, s, user)
    assert out["brain"] == "demo"
    with factory() as s:
        assert get_balance(s, user.id) == 0


def test_portal_summary_shape(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 100)
    monkeypatch.setattr(settings, "stripe_publishable_key", None)
    with factory() as s:
        out = portal(s, user)
    assert out.balance == 100  # first portal view takes the free grant
    assert out.free_monthly_credits == 100
    assert out.free_remaining == 100
    assert out.packs[0].usd_cents == 500 and out.packs[0].credits == 500
    assert out.costs["video"] == 25 and out.costs["image"] == 3
    assert out.publishable_key is None
    with factory() as s:
        second = portal(s, user)
    assert second.free_remaining == 0  # grant taken once per month


def _fake_sub_stripe(url="https://checkout.stripe.test/s/sub"):
    """Fake stripe with subscription-mode checkout, customers, cancels."""
    fake = _fake_stripe()
    calls = {"customer_create": [], "sub_delete": []}

    class Customers:
        @staticmethod
        def create(**kwargs):
            calls["customer_create"].append(kwargs)
            return types.SimpleNamespace(id="cus_test_1")

    class Subscriptions:
        @staticmethod
        def delete(sub_id):
            calls["sub_delete"].append(sub_id)
            return types.SimpleNamespace(id=sub_id, status="canceled")

    orig_create = fake.checkout.Session.create

    class SubSessions:
        @staticmethod
        def create(**kwargs):
            if kwargs.get("mode") == "subscription":
                fake.created_kwargs.update(kwargs)
                kwargs.setdefault("customer", "cus_test_1")
                return types.SimpleNamespace(id="cs_sub_1", url=url)
            return orig_create(**kwargs)

    fake.checkout = types.SimpleNamespace(Session=SubSessions)
    fake.Customer = Customers
    fake.Subscription = Subscriptions
    fake.sub_calls = calls
    return fake


def _sub_event(kind, obj):
    return {
        "type": kind,
        "data": {"object": obj},
    }


def _fake_webhook(monkeypatch, event):
    fake = types.SimpleNamespace(
        Webhook=types.SimpleNamespace(
            construct_event=staticmethod(lambda payload, sig, secret: event)
        )
    )
    monkeypatch.setitem(sys.modules, "stripe", fake)


def test_plans_beat_topups_per_dollar():
    by_id = {p["id"]: p for p in SUBSCRIPTION_PLANS}
    assert set(by_id) == {"hobby", "studio", "atelier"}
    # Commitment buys bonus credits: every plan beats the $5=500 rate.
    for p in SUBSCRIPTION_PLANS:
        assert p["credits"] / p["usd_cents"] > 500 / 500
    assert by_id["hobby"]["credits"] == 1200
    assert by_id["studio"]["credits"] == 5000
    assert by_id["atelier"]["credits"] == 15000


def test_subscribe_needs_stripe_keys(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    with factory() as s, pytest.raises(HTTPException) as exc:
        subscribe(SubscribeBody(plan_id="hobby"), s, user)
    assert exc.value.status_code == 503


def test_subscribe_unknown_plan_is_400(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setitem(sys.modules, "stripe", _fake_sub_stripe())
    with factory() as s, pytest.raises(HTTPException) as exc:
        subscribe(SubscribeBody(plan_id="mega"), s, user)
    assert exc.value.status_code == 400


def test_subscribe_reuses_one_customer(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    fake = _fake_sub_stripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    with factory() as s:
        out = subscribe(SubscribeBody(plan_id="studio"), s, user)
        assert out["url"].startswith("https://checkout.stripe.test")
    assert fake.created_kwargs.get("mode") == "subscription"
    line = [li for li in fake.created_kwargs["line_items"]][0]["price_data"]
    assert line["recurring"] == {"interval": "month"}
    assert line["unit_amount"] == 2900  # studio plan price
    with factory() as s:
        u = s.merge(user)
        assert u.stripe_customer_id == "cus_test_1"
    # Second plan attempt while none active still creates no new customer.
    with factory() as s:
        subscribe(SubscribeBody(plan_id="hobby"), s, s.merge(user))
    assert len(fake.sub_calls["customer_create"]) == 1


def test_subscribe_conflicts_when_active(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setitem(sys.modules, "stripe", _fake_sub_stripe())
    with factory() as s:
        s.add(
            SubscriptionRow(
                user_id=user.id,
                stripe_subscription_id="sub_active",
                plan_id="hobby",
                status="active",
            )
        )
        s.commit()
    with factory() as s, pytest.raises(HTTPException) as exc:
        subscribe(SubscribeBody(plan_id="studio"), s, user)
    assert exc.value.status_code == 409


def test_signup_webhook_grants_once(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 0)
    _fake_webhook(
        monkeypatch,
        _sub_event(
            "checkout.session.completed",
            {
                "id": "cs_sub_1",
                "customer": "cus_test_1",
                "subscription": "sub_1",
                "metadata": {"user_id": user.id, "plan_id": "hobby", "kind": "subscription", "credits": "1200"},
            },
        ),
    )
    with factory() as s:
        assert _run(webhook(_webhook_request(), s)) == {"status": "ok"}
    with factory() as s:
        assert get_balance(s, user.id) == 1200
    with factory() as s:
        assert _run(webhook(_webhook_request(), s)) == {"status": "ok"}
    with factory() as s:
        assert get_balance(s, user.id) == 1200  # replay mints nothing


def test_renewal_credits_once_per_invoice(factory, user, monkeypatch):
    from komaverse.api.ai import get_balance as bal

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(settings, "paywall_enabled", True)
    monkeypatch.setattr(settings, "free_monthly_credits", 0)
    with factory() as s:
        s.add(
            SubscriptionRow(
                user_id=user.id,
                stripe_subscription_id="sub_9",
                plan_id="hobby",
                status="active",
            )
        )
        s.commit()

    def renew(invoice):
        _fake_webhook(
            monkeypatch,
            _sub_event(
                "invoice.payment_succeeded",
                {"id": invoice, "subscription": "sub_9"},
            ),
        )
        with factory() as s:
            assert _run(webhook(_webhook_request(), s)) == {"status": "ok"}

    renew("in_1")
    with factory() as s:
        assert bal(s, user.id) == 1200
    renew("in_1")  # retried delivery
    with factory() as s:
        assert bal(s, user.id) == 1200
    renew("in_2")  # next month pays again
    with factory() as s:
        assert bal(s, user.id) == 2400


def test_subscription_deleted_cancels(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_x")
    with factory() as s:
        s.add(
            SubscriptionRow(
                user_id=user.id,
                stripe_subscription_id="sub_gone",
                plan_id="studio",
                status="active",
            )
        )
        s.commit()
    _fake_webhook(
        monkeypatch, _sub_event("customer.subscription.deleted", {"id": "sub_gone"})
    )
    with factory() as s:
        assert _run(webhook(_webhook_request(), s)) == {"status": "ok"}
    with factory() as s:
        row = (
            s.execute(
                __import__("sqlalchemy").select(SubscriptionRow).where(
                    SubscriptionRow.stripe_subscription_id == "sub_gone"
                )
            )
            .scalars()
            .one()
        )
        assert row.status == "canceled"


def test_cancel_endpoint(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    fake = _fake_sub_stripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    with factory() as s:
        s.add(
            SubscriptionRow(
                user_id=user.id,
                stripe_subscription_id="sub_bye",
                plan_id="hobby",
                status="active",
            )
        )
        s.commit()
    with factory() as s:
        assert cancel_subscription(s, s.merge(user)) == {"status": "canceled"}
    assert fake.sub_calls["sub_delete"] == ["sub_bye"]
    with factory() as s, pytest.raises(HTTPException) as exc:
        cancel_subscription(s, user)
    assert exc.value.status_code == 404


def test_portal_includes_plans_and_subscription(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "paywall_enabled", False)
    with factory() as s:
        s.add(
            SubscriptionRow(
                user_id=user.id,
                stripe_subscription_id="sub_show",
                plan_id="studio",
                status="active",
            )
        )
        s.commit()
    with factory() as s:
        out = portal(s, s.merge(user))
    assert [p.id for p in out.plans] == ["hobby", "studio", "atelier"]
    assert out.subscription is not None
    assert (out.subscription.plan_id, out.subscription.status) == ("studio", "active")
