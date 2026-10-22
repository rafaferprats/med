"""Stripe subscriptions (BILLING_MODE=stripe), without the Stripe library: httpx and the REST API.

Flow:
1. create_checkout(): the user pays on a Stripe Checkout page (mode "subscription").
2. Stripe charges every week, month or year by itself and calls POST /billing/webhook.
3. handle_event(): each paid invoice (invoice.paid) is stored as a payment and moves Premium
   to the end of the paid period; cancellations and failed payments update the subscription.

Setup: see "Cobros con Stripe" in the README.
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import HTTPException

import billing
from config import PLANS, PUBLIC_URL, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
from db import connect

API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE = 300  # seconds; older webhook calls are rejected (replays)


def _post(path: str, data: dict) -> dict:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Los pagos aún no están configurados.")
    try:
        r = httpx.post(f"{API}{path}", data=data, auth=(STRIPE_SECRET_KEY, ""), timeout=20)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="No se puede conectar con el sistema de pago. Prueba más tarde.")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="El sistema de pago ha dado un error. Prueba más tarde.")
    return r.json()


def create_checkout(user, plan_id: str) -> str:
    """URL of the Stripe page where the user subscribes to the plan."""
    price = PLANS[plan_id]["stripe_price"]
    if not price:
        raise HTTPException(status_code=501, detail="Este plan aún no está configurado.")
    sub = billing.subscription(user["id"])
    data = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "client_reference_id": str(user["id"]),
        "metadata[user_id]": str(user["id"]),
        "metadata[plan_id]": plan_id,
        # Also on the subscription, so every invoice says whose it is
        "subscription_data[metadata][user_id]": str(user["id"]),
        "subscription_data[metadata][plan_id]": plan_id,
        "success_url": f"{PUBLIC_URL}/?checkout=ok",
        "cancel_url": f"{PUBLIC_URL}/?checkout=cancel",
        "locale": "es",
    }
    if sub and sub["stripe_customer_id"]:
        data["customer"] = sub["stripe_customer_id"]  # same Stripe customer as before
    else:
        data["customer_email"] = user["email"]
    return _post("/checkout/sessions", data)["url"]


def set_cancel(subscription_id: str, cancel: bool) -> None:
    _post(f"/subscriptions/{subscription_id}", {"cancel_at_period_end": "true" if cancel else "false"})


def verify(payload: bytes, header: Optional[str]) -> dict:
    """The webhook event, after checking Stripe's signature (Stripe-Signature header)."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=501, detail="Webhook not configured")
    parts = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    timestamp = next((v for k, v in parts if k == "t"), "")
    signatures = [v for k, v in parts if k == "v1"]
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE:
        raise HTTPException(status_code=400, detail="Invalid signature")
    expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), timestamp.encode() + b"." + payload,
                        hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise HTTPException(status_code=400, detail="Invalid signature")
    return json.loads(payload)


def _ts(seconds: int) -> datetime:
    return datetime.fromtimestamp(int(seconds), timezone.utc)


def _plan_for_price(price_id: Optional[str]) -> Optional[str]:
    return next((pid for pid, p in PLANS.items() if price_id and p["stripe_price"] == price_id), None)


def _invoice_subscription(invoice: dict) -> tuple[Optional[str], dict]:
    """Subscription id and its metadata; the fields moved between Stripe API versions."""
    details = (invoice.get("parent") or {}).get("subscription_details") or invoice.get("subscription_details") or {}
    sub_id = invoice.get("subscription") or details.get("subscription")
    if isinstance(sub_id, dict):
        sub_id = sub_id.get("id")
    return sub_id, details.get("metadata") or {}


def _user_for(conn, subscription_id: Optional[str], metadata: dict) -> Optional[int]:
    row = conn.execute("SELECT user_id FROM subscriptions WHERE stripe_subscription_id = ?",
                       (subscription_id,)).fetchone()
    if row:
        return row["user_id"]
    user_id = metadata.get("user_id")
    return int(user_id) if user_id and str(user_id).isdigit() else None


def _upsert(conn, user_id: int, plan_id: str, subscription_id: str, customer_id: Optional[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO subscriptions (user_id, plan_id, status, cycle_anchor, current_period_end, provider, "
        "stripe_customer_id, stripe_subscription_id) VALUES (?, ?, 'active', ?, ?, 'stripe', ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET plan_id = excluded.plan_id, provider = 'stripe', "
        "stripe_customer_id = COALESCE(excluded.stripe_customer_id, stripe_customer_id), "
        "status = CASE WHEN stripe_subscription_id IS excluded.stripe_subscription_id THEN status ELSE 'active' END, "
        "current_period_end = CASE WHEN stripe_subscription_id IS excluded.stripe_subscription_id "
        "THEN current_period_end ELSE excluded.current_period_end END, "
        "cancel_at_period_end = CASE WHEN stripe_subscription_id IS excluded.stripe_subscription_id "
        "THEN cancel_at_period_end ELSE 0 END, "
        "stripe_subscription_id = excluded.stripe_subscription_id, next_plan_id = NULL, updated_at = datetime('now')",
        (user_id, plan_id, now, now, customer_id, subscription_id),
    )


STATUSES = {"active": "active", "trialing": "active", "past_due": "past_due", "unpaid": "past_due",
            "canceled": "canceled", "incomplete_expired": "canceled"}


def handle_event(event: dict) -> None:
    kind, obj = event.get("type"), event.get("data", {}).get("object", {})
    with connect() as conn:
        if kind == "checkout.session.completed" and obj.get("mode") == "subscription":
            metadata = obj.get("metadata") or {}
            user_id = obj.get("client_reference_id") or metadata.get("user_id")
            if user_id and str(user_id).isdigit() and metadata.get("plan_id") in PLANS:
                _upsert(conn, int(user_id), metadata["plan_id"], obj.get("subscription"), obj.get("customer"))

        elif kind == "invoice.paid":
            sub_id, metadata = _invoice_subscription(obj)
            user_id = _user_for(conn, sub_id, metadata)
            lines = (obj.get("lines") or {}).get("data") or []
            if not sub_id or user_id is None or not lines:
                return
            line = lines[0]
            price = line.get("price") or ((line.get("pricing") or {}).get("price_details") or {}).get("price")
            plan_id = (_plan_for_price(price.get("id") if isinstance(price, dict) else price)
                       or metadata.get("plan_id"))
            if plan_id not in PLANS:
                return
            _upsert(conn, user_id, plan_id, sub_id, obj.get("customer"))
            start, end = _ts(line["period"]["start"]), _ts(line["period"]["end"])
            billing.record_payment(conn, user_id, plan_id, obj.get("amount_paid", 0) / 100, start, end,
                                   "stripe", obj.get("id"))
            conn.execute(
                "UPDATE subscriptions SET status = 'active', current_period_end = MAX(current_period_end, ?) "
                "WHERE user_id = ?", (end.isoformat(), user_id))

        elif kind == "invoice.payment_failed":
            sub_id, metadata = _invoice_subscription(obj)
            conn.execute("UPDATE subscriptions SET status = 'past_due', updated_at = datetime('now') "
                         "WHERE stripe_subscription_id = ?", (sub_id,))

        elif kind in ("customer.subscription.updated", "customer.subscription.deleted"):
            status = "canceled" if kind.endswith("deleted") else STATUSES.get(obj.get("status"), "active")
            items = (obj.get("items") or {}).get("data") or [{}]
            plan_id = _plan_for_price((items[0].get("price") or {}).get("id"))
            conn.execute(
                "UPDATE subscriptions SET status = ?, cancel_at_period_end = ?, plan_id = COALESCE(?, plan_id), "
                "updated_at = datetime('now') WHERE stripe_subscription_id = ?",
                (status, int(bool(obj.get("cancel_at_period_end"))), plan_id, obj.get("id")),
            )
