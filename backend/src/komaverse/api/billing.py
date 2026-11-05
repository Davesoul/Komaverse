"""Koma Portal billing: Stripe top-ups, freemium grants, portal summary.

Money flow: the app frontend creates a Stripe Checkout Session here, the
user pays on Stripe's hosted page (PCI stays with Stripe), and Stripe's
signed webhook credits the ledger. Server API keys pay the model
providers, so users never paste keys — credits are the only currency.

Idempotency: PurchaseRow.stripe_session_id is unique. Retried webhooks,
double-clicks, and replayed events credit exactly once.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from komaverse.api.ai import (
    COSTS,
    CREDIT_PACKS,
    add_entry,
    ensure_monthly_grant,
    get_balance,
)
from komaverse.auth import get_current_user
from komaverse.config import settings
from komaverse.db import get_session
from komaverse.db_models import LedgerRow, PurchaseRow, SubscriptionRow, UserRow
from komaverse.models import PortalOut, PortalPack, PortalPlan

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger("komaverse.billing")


class CheckoutBody(BaseModel):
    pack_id: str = "topup-5"


class SubscribeBody(BaseModel):
    plan_id: str = "hobby"


#: Monthly plans: commitment buys bonus credits over pay-as-you-go
#: ($5 top-up = 100 credits/$, Hobby = 133/$ — the discount deepens
#: with the tier, funded by breakage + lower per-seat support cost).
#: See docs/BUSINESS_MODEL.md for the per-model margin math.
SUBSCRIPTION_PLANS: tuple[dict[str, str | int], ...] = (
    {
        "id": "hobby",
        "name": "Hobby",
        "usd_cents": 900,
        "credits": 1200,
        "blurb": "Weekend projects — ~400 images or 48 video-seconds a month",
    },
    {
        "id": "studio",
        "name": "Studio",
        "usd_cents": 2900,
        "credits": 5000,
        "blurb": "Series work — ~1,600 images or 200 video-seconds a month",
    },
    {
        "id": "atelier",
        "name": "Atelier",
        "usd_cents": 7900,
        "credits": 15000,
        "blurb": "Teams — ~5,000 images or 600 video-seconds a month",
    },
)


def _plan(plan_id: str) -> dict:
    for plan in SUBSCRIPTION_PLANS:
        if plan["id"] == plan_id:
            return plan
    raise HTTPException(400, f"unknown plan: {plan_id} (want hobby/studio/atelier)")


def _active_subscription(session: Session, user_id: str) -> SubscriptionRow | None:
    return session.execute(
        select(SubscriptionRow)
        .where(
            SubscriptionRow.user_id == user_id,
            SubscriptionRow.status == "active",
        )
        .order_by(desc(SubscriptionRow.created_at), desc(SubscriptionRow.id))
    ).scalar_one_or_none()


def _pack(pack_id: str) -> dict:
    for pack in CREDIT_PACKS:
        if pack["id"] == pack_id:
            return pack
    raise HTTPException(400, f"unknown pack: {pack_id} (want topup-5/10/25)")


def _stripe_or_503():
    if not settings.stripe_secret_key:
        raise HTTPException(503, "top-ups are not configured on this server")
    try:
        import stripe
    except ImportError as exc:
        raise HTTPException(503, "stripe package not installed") from exc
    stripe.api_key = settings.stripe_secret_key
    return stripe


@router.get("/billing/portal", response_model=PortalOut)
def portal(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Everything the Koma Portal page needs in one round trip."""
    balance = get_balance(session, user.id)
    granted = ensure_monthly_grant(session, user.id) if settings.paywall_enabled else 0
    if granted:
        session.commit()
        balance = get_balance(session, user.id)
    free_remaining = (
        settings.free_monthly_credits if granted else 0
    )
    purchases = (
        session.execute(
            select(PurchaseRow)
            .where(PurchaseRow.user_id == user.id)
            .order_by(PurchaseRow.created_at.desc(), PurchaseRow.id.desc())
        )
        .scalars()
        .all()
    )
    sub = _active_subscription(session, user.id)
    return PortalOut(
        balance=balance,
        free_monthly_credits=settings.free_monthly_credits,
        free_remaining=free_remaining,
        paywall_enabled=settings.paywall_enabled,
        publishable_key=settings.stripe_publishable_key,
        plans=[
            PortalPlan(
                id=str(p["id"]),
                name=str(p["name"]),
                usd_cents=int(p["usd_cents"]),
                credits=int(p["credits"]),
                blurb=str(p["blurb"]),
            )
            for p in SUBSCRIPTION_PLANS
        ],
        subscription=sub.to_schema() if sub else None,
        packs=[
            PortalPack(id=str(p["id"]), usd_cents=int(p["usd_cents"]), credits=int(p["credits"]))
            for p in CREDIT_PACKS
        ],
        costs=dict(COSTS),
        purchases=[r.to_schema() for r in purchases],
    )


@router.post("/billing/checkout")
def checkout(
    body: CheckoutBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Create a Stripe Checkout Session for a credit pack ($5 minimum)."""
    stripe = _stripe_or_503()
    pack = _pack(body.pack_id)
    try:
        checkout_session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": pack["usd_cents"],
                        "product_data": {
                            "name": f"Komaverse credits ({pack['credits']})",
                        },
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "user_id": user.id,
                "pack_id": pack["id"],
                "credits": str(pack["credits"]),
            },
            success_url=f"{settings.app_url}/portal?topup=ok",
            cancel_url=f"{settings.app_url}/portal?topup=cancelled",
        )
    except Exception as exc:
        logger.warning("stripe checkout failed (%s)", exc)
        raise HTTPException(502, "could not start checkout") from exc
    row = PurchaseRow(
        user_id=user.id,
        stripe_session_id=checkout_session.id,
        amount_usd_cents=pack["usd_cents"],
        credits=pack["credits"],
        status="pending",
    )
    session.add(row)
    session.commit()
    return {"url": checkout_session.url, "purchase_id": row.id}


def _customer_id(stripe, session: Session, user: UserRow) -> str:
    """One Stripe customer per user, reused across top-ups and plans."""
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(email=user.email, metadata={"user_id": user.id})
    user.stripe_customer_id = customer.id
    session.commit()
    return customer.id


@router.post("/billing/subscribe")
def subscribe(
    body: SubscribeBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Start a monthly plan: subscription-mode Checkout, credits land
    via webhook (first month on signup, renewals on paid invoices)."""
    if _active_subscription(session, user.id) is not None:
        raise HTTPException(409, "a subscription is already active (cancel it first)")
    stripe = _stripe_or_503()
    plan = _plan(body.plan_id)
    customer_id = _customer_id(stripe, session, user)
    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": plan["usd_cents"],
                        "recurring": {"interval": "month"},
                        "product_data": {
                            "name": f"Komaverse {plan['name']} ({plan['credits']} credits/mo)"
                        },
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "user_id": user.id,
                "plan_id": plan["id"],
                "kind": "subscription",
                "credits": str(plan["credits"]),
            },
            success_url=f"{settings.app_url}/portal?sub=ok",
            cancel_url=f"{settings.app_url}/portal?sub=cancelled",
        )
    except Exception as exc:
        logger.warning("stripe subscribe failed (%s)", exc)
        raise HTTPException(502, "could not start subscription checkout") from exc
    return {"url": checkout_session.url}


@router.post("/billing/subscription/cancel")
def cancel_subscription(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Cancel now: Stripe stops renewal, granted credits stay (no
    mid-month clawback), freemium keeps working."""
    stripe = _stripe_or_503()
    sub = _active_subscription(session, user.id)
    if sub is None:
        raise HTTPException(404, "no active subscription")
    try:
        stripe.Subscription.delete(sub.stripe_subscription_id)
    except Exception as exc:
        logger.warning("stripe cancel failed (%s)", exc)
        raise HTTPException(502, "could not cancel on Stripe") from exc
    sub.status = "canceled"
    session.commit()
    return {"status": "canceled"}


@router.post("/billing/webhook")
async def webhook(request: Request, session: Session = Depends(get_session)):
    """Stripe event funnel. Signature-verified; unknown events are 200 no-ops."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(503, "webhooks are not configured on this server")
    stripe = _stripe_or_503()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    except Exception as exc:
        raise HTTPException(400, f"bad webhook signature ({exc})") from exc
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed":
        if (obj.get("metadata") or {}).get("kind") == "subscription":
            _credit_subscription_signup(session, obj)
        else:
            _credit_completed(session, obj)
    elif kind == "checkout.session.expired":
        _mark_expired(session, obj)
    elif kind == "invoice.payment_succeeded":
        _credit_subscription_renewal(session, obj)
    elif kind == "customer.subscription.deleted":
        _mark_subscription(session, obj.get("id", ""), "canceled")
    elif kind == "customer.subscription.updated":
        _sync_subscription(session, obj)
    return {"status": "ok"}


def _find_purchase(session: Session, stripe_session_id: str) -> PurchaseRow | None:
    return session.execute(
        select(PurchaseRow).where(PurchaseRow.stripe_session_id == stripe_session_id)
    ).scalar_one_or_none()


def _credit_completed(session: Session, obj: dict) -> None:
    sid = obj.get("id", "")
    row = _find_purchase(session, sid)
    if row is None:
        logger.warning("webhook for unknown session %s", sid)
        return
    if row.status == "done":
        return  # retried delivery: already credited, stay silent
    meta = obj.get("metadata") or {}
    try:
        credits = int(meta.get("credits", row.credits))
    except (TypeError, ValueError):
        credits = row.credits
    add_entry(session, row.user_id, credits, f"top-up ${row.amount_usd_cents / 100:.2f}")
    row.status = "done"
    session.commit()


def _mark_expired(session: Session, obj: dict) -> None:
    row = _find_purchase(session, obj.get("id", ""))
    if row is None or row.status != "pending":
        return
    row.status = "expired"
    session.commit()


def _find_subscription(session: Session, stripe_sub_id: str) -> SubscriptionRow | None:
    return session.execute(
        select(SubscriptionRow).where(
            SubscriptionRow.stripe_subscription_id == stripe_sub_id
        )
    ).scalar_one_or_none()


def _plan_credits(plan_id: str, fallback: int = 0) -> int:
    try:
        return int(_plan(plan_id)["credits"])
    except HTTPException:
        return fallback


def _credit_subscription_signup(session: Session, obj: dict) -> None:
    """First paid month: create the subscription row + grant plan credits."""
    meta = obj.get("metadata") or {}
    sub_id = obj.get("subscription", "") or ""
    row = _find_subscription(session, sub_id) if sub_id else None
    if row is not None:
        return  # retried delivery: already created + credited
    user_id = meta.get("user_id", "")
    plan_id = meta.get("plan_id", "")
    if not user_id or not sub_id:
        logger.warning("subscription signup missing user/subscription ids")
        return
    try:
        credits = int(meta.get("credits", 0)) or _plan_credits(plan_id)
    except (TypeError, ValueError):
        credits = _plan_credits(plan_id)
    session.add(
        SubscriptionRow(
            user_id=user_id,
            stripe_subscription_id=sub_id,
            stripe_customer_id=obj.get("customer", "") or "",
            plan_id=plan_id,
            status="active",
        )
    )
    add_entry(session, user_id, credits, f"subscription {plan_id} first month")
    session.commit()


def _credit_subscription_renewal(session: Session, obj: dict) -> None:
    """Each paid invoice re-grants the month's credits (once per invoice)."""
    sub_id = obj.get("subscription", "") or ""
    row = _find_subscription(session, sub_id) if sub_id else None
    if row is None or row.status != "active":
        return
    invoice_id = obj.get("id", "") or ""
    if invoice_id and row.last_invoice_id == invoice_id:
        return  # retried delivery of an already-credited invoice
    row.last_invoice_id = invoice_id
    row.status = "active"
    add_entry(
        session, row.user_id, _plan_credits(row.plan_id), f"subscription {row.plan_id} renewal"
    )
    session.commit()


def _mark_subscription(session: Session, stripe_sub_id: str, status: str) -> None:
    row = _find_subscription(session, stripe_sub_id) if stripe_sub_id else None
    if row is None:
        return
    row.status = status
    session.commit()


def _sync_subscription(session: Session, obj: dict) -> None:
    row = _find_subscription(session, obj.get("id", "")) if obj.get("id") else None
    if row is None:
        return
    row.status = obj.get("status", row.status) or row.status
    period_end = obj.get("current_period_end")
    if isinstance(period_end, (int, float)):
        row.current_period_end = datetime.fromtimestamp(period_end)
    session.commit()


@router.get("/billing/ledger")
def ledger(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Full credit history (portal 'activity'); newest last, like /billing/me."""
    res = session.execute(select(LedgerRow).where(LedgerRow.user_id == user.id))
    entries = [r.to_schema() for r in res.scalars().all()]
    return {"balance": sum(e.delta for e in entries), "entries": entries}
