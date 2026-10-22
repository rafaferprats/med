"""Free questions, subscriptions and payments.

Free accounts get FREE_QUESTIONS questions, FREE_IMAGES images and FREE_LABS lab reports when
they sign up. When those questions run out the plans window opens; from the next day on there
are FREE_DAILY_QUESTIONS free questions a day. Premium is a
subscription charged every week, month or year: each paid period moves users.premium_until
forward, so access always matches what has been paid.

- BILLING_MODE=test: the payments are simulated here, renewals included (renew_due).
- BILLING_MODE=stripe: Stripe charges and tells us through the webhook (stripe_billing.py).
"""
import calendar
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from config import (BILLING_MODE, FREE_DAILY_QUESTIONS, FREE_IMAGES, FREE_LABS, FREE_QUESTIONS, GUEST_QUESTIONS,
                    PLANS)
from db import connect

LIMITS = {"text": FREE_QUESTIONS, "image": FREE_IMAGES, "lab": FREE_LABS}
USED = {"text": "free_text_used", "image": "free_images_used", "lab": "free_labs_used"}
DAILY = {"text": "text_count", "image": "image_count", "lab": "lab_count"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().date().isoformat()


def _user(user_id: int) -> sqlite3.Row:
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def add_interval(anchor: datetime, plan_id: str, periods: int = 1) -> datetime:
    """End of the given number of billing periods counted from anchor. Months and years are
    calendar ones and keep the anchor's day when the month has it, like Stripe does:
    31 Jan -> 28 Feb -> 31 Mar."""
    interval = PLANS[plan_id]["interval"]
    if interval == "week":
        return anchor + timedelta(weeks=periods)
    months = anchor.month - 1 + periods * (12 if interval == "year" else 1)
    year, month = anchor.year + months // 12, months % 12 + 1
    return anchor.replace(year=year, month=month, day=min(anchor.day, calendar.monthrange(year, month)[1]))


def is_premium(user: sqlite3.Row) -> bool:
    until = user["premium_until"]
    return user["plan"] == "premium" and until is not None and datetime.fromisoformat(until) > _now()


def _fresh(user: sqlite3.Row) -> sqlite3.Row:
    """The user after charging any test renewal that is due."""
    if BILLING_MODE == "test" and not is_premium(user) and renew_due(user["id"]):
        return _user(user["id"])
    return user


def subscription(user_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()


def _subscription_status(user_id: int) -> Optional[dict]:
    sub = subscription(user_id)
    if sub is None or sub["status"] == "canceled":
        return None
    plan = PLANS.get(sub["plan_id"], {})
    next_plan = PLANS.get(sub["next_plan_id"] or "", {})
    return {
        "plan_id": sub["plan_id"],
        "plan_name": plan.get("name", sub["plan_id"]),
        "price_eur": plan.get("price_eur"),
        "status": sub["status"],
        "current_period_end": sub["current_period_end"],
        "cancel_at_period_end": bool(sub["cancel_at_period_end"]),
        "next_plan_id": sub["next_plan_id"],
        "next_plan_name": next_plan.get("name"),
        "provider": sub["provider"],
    }


def _daily_used(user_id: int) -> int:
    with connect() as conn:
        row = conn.execute("SELECT free_daily_used FROM usage WHERE user_id = ? AND day = ?",
                           (user_id, _today())).fetchone()
    return row["free_daily_used"] if row else 0


def text_quota(user: sqlite3.Row) -> tuple[int, int, str]:
    """(left, limit, period) of free questions: "welcome" while the sign-up ones last, then
    "day". The day the sign-up questions run out there is none left (the plans window opens)."""
    left = FREE_QUESTIONS - user["free_text_used"]
    if left > 0:
        return left, FREE_QUESTIONS, "welcome"
    if user["free_pack_done_on"] == _today():
        return 0, FREE_DAILY_QUESTIONS, "day"
    return max(0, FREE_DAILY_QUESTIONS - _daily_used(user["id"])), FREE_DAILY_QUESTIONS, "day"


def status(user: sqlite3.Row) -> dict:
    user = _fresh(user)
    premium = is_premium(user)
    used = {k: user[USED[k]] for k in LIMITS}
    text_left, text_limit, period = text_quota(user)
    remaining = {k: max(0, LIMITS[k] - used[k]) for k in LIMITS} | {"text": text_left}
    return {
        "email": user["email"],
        "plan": "premium" if premium else "free",
        "premium_until": user["premium_until"] if premium else None,
        "usage": used,
        "limits": None if premium else LIMITS | {"text": text_limit},
        "remaining": None if premium else remaining,
        "text_period": None if premium else period,
        "subscription": _subscription_status(user["id"]),
        "billing_mode": BILLING_MODE,
    }


def has_quota(user: sqlite3.Row, kind: str) -> bool:
    user = _fresh(user)
    if is_premium(user):
        return True
    if kind == "text":
        return text_quota(user)[0] > 0
    return user[USED[kind]] < LIMITS[kind]


def consume(user: sqlite3.Row, kind: str) -> None:
    """Counts one question, or raises 402 if a free user has used all the free ones."""
    user = _fresh(user)
    col = DAILY[kind]
    with connect() as conn:
        # Daily usage is kept for statistics only
        conn.execute("INSERT INTO usage (user_id, day) VALUES (?, ?) ON CONFLICT DO NOTHING",
                     (user["id"], _today()))
        conn.execute(f"UPDATE usage SET {col} = {col} + 1 WHERE user_id = ? AND day = ?",
                     (user["id"], _today()))
        if is_premium(user):
            return
        # Single statements: check and increment together, so parallel requests can't overshoot
        used = USED[kind]
        if kind == "text":
            # Sign-up questions first; the one that uses the last of them records the day
            cur = conn.execute(
                "UPDATE users SET free_text_used = free_text_used + 1, free_pack_done_on = "
                "CASE WHEN free_text_used + 1 >= ? THEN ? ELSE free_pack_done_on END "
                "WHERE id = ? AND free_text_used < ?",
                (FREE_QUESTIONS, _today(), user["id"], FREE_QUESTIONS))
            if cur.rowcount == 0:
                # Then the daily one, but not on the day the sign-up questions ran out
                cur = conn.execute(
                    "UPDATE usage SET free_daily_used = free_daily_used + 1 WHERE user_id = ? AND day = ? "
                    "AND free_daily_used < ? AND (SELECT COALESCE(free_pack_done_on, '') FROM users "
                    "WHERE id = ?) <> ?",
                    (user["id"], _today(), FREE_DAILY_QUESTIONS, user["id"], _today()))
        else:
            cur = conn.execute(f"UPDATE users SET {used} = {used} + 1 WHERE id = ? AND {used} < ?",
                               (user["id"], LIMITS[kind]))
        if cur.rowcount == 0:
            conn.rollback()  # the question isn't answered, so it isn't counted either
    if cur.rowcount == 0:
        raise HTTPException(status_code=402, detail=_no_quota_message(user, kind))


def _no_quota_message(user: sqlite3.Row, kind: str) -> str:
    if kind == "lab":
        return "Ya has usado tu analítica gratis. Hazte Premium para que te explique todas tus analíticas."
    if kind == "image":
        return "Ya has usado tu imagen gratis. Hazte Premium para analizar todas las que quieras."
    daily = f"{FREE_DAILY_QUESTIONS} pregunta gratis al día" if FREE_DAILY_QUESTIONS == 1 else \
        f"{FREE_DAILY_QUESTIONS} preguntas gratis al día"
    if FREE_QUESTIONS - user["free_text_used"] <= 0 and user["free_pack_done_on"] in (None, _today()) \
            and _daily_used(user["id"]) == 0:
        return (f"Ya has usado tus {FREE_QUESTIONS} preguntas gratis. Hazte Premium para seguir preguntando "
                f"sin límite, o vuelve mañana: tendrás {daily}.")
    return "Ya has usado tu pregunta gratis de hoy. Vuelve mañana o hazte Premium para preguntar sin límite."


def refund(user_id: int, kind: str) -> None:
    """Gives a question back when the model failed to answer."""
    col, used = DAILY[kind], USED[kind]
    with connect() as conn:
        conn.execute(f"UPDATE usage SET {col} = MAX(0, {col} - 1) WHERE user_id = ? AND day = ?",
                     (user_id, _today()))
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or is_premium(user):
            return
        if kind == "text":
            # The daily question is only used once the sign-up ones are gone: give that back first
            cur = conn.execute("UPDATE usage SET free_daily_used = free_daily_used - 1 WHERE user_id = ? "
                               "AND day = ? AND free_daily_used > 0", (user_id, _today()))
            if cur.rowcount:
                return
            conn.execute("UPDATE users SET free_text_used = MAX(0, free_text_used - 1), "
                         "free_pack_done_on = NULL WHERE id = ?", (user_id,))
            return
        conn.execute(f"UPDATE users SET {used} = MAX(0, {used} - 1) WHERE id = ?", (user_id,))


def guest_consume(guest_id: str) -> bool:
    """Counts a question from a visitor without an account; False if none are left."""
    with connect() as conn:
        conn.execute("INSERT INTO guests (id) VALUES (?) ON CONFLICT DO NOTHING", (guest_id,))
        cur = conn.execute(
            "UPDATE guests SET questions = questions + 1 WHERE id = ? AND questions < ?",
            (guest_id, GUEST_QUESTIONS),
        )
    return cur.rowcount == 1


def guest_remaining(guest_id: Optional[str]) -> int:
    if not guest_id:
        return GUEST_QUESTIONS
    with connect() as conn:
        row = conn.execute("SELECT questions FROM guests WHERE id = ?", (guest_id,)).fetchone()
    return max(0, GUEST_QUESTIONS - (row["questions"] if row else 0))


def guest_refund(guest_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE guests SET questions = MAX(0, questions - 1) WHERE id = ?", (guest_id,)
        )


# --- Payments (shared by the test mode and Stripe) ---

def record_payment(conn, user_id: int, plan_id: str, amount_eur: float, start: datetime,
                   end: datetime, provider: str, ref: Optional[str] = None) -> bool:
    """Stores a paid period and extends Premium to its end. False if ref was already paid."""
    cur = conn.execute(
        "INSERT INTO payments (user_id, plan_id, amount_eur, period_start, period_end, provider, provider_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (provider_ref) DO NOTHING",
        (user_id, plan_id, amount_eur, start.isoformat(), end.isoformat(), provider, ref),
    )
    if cur.rowcount == 0:
        return False
    row = conn.execute("SELECT premium_until FROM users WHERE id = ?", (user_id,)).fetchone()
    until = max(end, datetime.fromisoformat(row["premium_until"])) if row["premium_until"] else end
    conn.execute("UPDATE users SET plan = 'premium', premium_until = ? WHERE id = ?",
                 (until.isoformat(), user_id))
    return True


def payments(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT plan_id, amount_eur, period_start, period_end, provider, created_at "
                            "FROM payments WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def _check_plan(plan_id: str) -> None:
    if plan_id not in PLANS:
        raise HTTPException(status_code=400, detail="Plan desconocido.")


def _active(sub: Optional[sqlite3.Row]) -> bool:
    return (sub is not None and sub["status"] in ("active", "past_due")
            and datetime.fromisoformat(sub["current_period_end"]) > _now())


# --- Test mode: simulated payments, charged at the right frequency ---

def subscribe_test(user: sqlite3.Row, plan_id: str) -> None:
    """Simulated checkout. A new subscription is charged now for its first period; with one
    already running, a different plan is applied at the next renewal (no double charge)."""
    if BILLING_MODE != "test":
        raise HTTPException(status_code=501, detail="Los pagos aún no están disponibles.")
    _check_plan(plan_id)
    sub = subscription(user["id"])
    if _active(sub):
        with connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET next_plan_id = ?, cancel_at_period_end = 0, "
                "updated_at = datetime('now') WHERE user_id = ?",
                (None if plan_id == sub["plan_id"] else plan_id, user["id"]),
            )
        return
    now = _now()
    end = add_interval(now, plan_id)
    with connect() as conn:
        conn.execute(
            "INSERT INTO subscriptions (user_id, plan_id, status, cycle_anchor, periods_paid, "
            "current_period_end, provider) VALUES (?, ?, 'active', ?, 1, ?, 'test') "
            "ON CONFLICT (user_id) DO UPDATE SET plan_id = excluded.plan_id, next_plan_id = NULL, "
            "status = 'active', cycle_anchor = excluded.cycle_anchor, periods_paid = 1, "
            "current_period_end = excluded.current_period_end, cancel_at_period_end = 0, "
            "provider = 'test', updated_at = datetime('now')",
            (user["id"], plan_id, now.isoformat(), end.isoformat()),
        )
        record_payment(conn, user["id"], plan_id, PLANS[plan_id]["price_eur"], now, end, "test")


def renew_due(user_id: Optional[int] = None) -> int:
    """Charges every test subscription whose period has ended, one payment per period, until
    it is up to date. Subscriptions set to cancel end instead. Returns the payments made."""
    now, made = _now(), 0
    query = ("SELECT * FROM subscriptions WHERE provider = 'test' AND status = 'active' "
             "AND current_period_end <= ?")
    args = [now.isoformat()]
    if user_id is not None:
        query += " AND user_id = ?"
        args.append(user_id)
    with connect() as conn:
        for sub in conn.execute(query, args).fetchall():
            plan_id, anchor, paid = sub["plan_id"], sub["cycle_anchor"], sub["periods_paid"]
            next_plan, end = sub["next_plan_id"], datetime.fromisoformat(sub["current_period_end"])
            new_status = "active"
            while end <= now:
                if sub["cancel_at_period_end"]:
                    new_status = "canceled"
                    break
                if next_plan:  # the new plan starts when the old one ends
                    plan_id, anchor, paid, next_plan = next_plan, end.isoformat(), 0, None
                start, paid = end, paid + 1
                end = add_interval(datetime.fromisoformat(anchor), plan_id, paid)
                record_payment(conn, sub["user_id"], plan_id, PLANS[plan_id]["price_eur"], start, end, "test")
                made += 1
            conn.execute(
                "UPDATE subscriptions SET plan_id = ?, next_plan_id = ?, status = ?, cycle_anchor = ?, "
                "periods_paid = ?, current_period_end = ?, updated_at = datetime('now') WHERE user_id = ?",
                (plan_id, next_plan, new_status, anchor, paid, end.isoformat(), sub["user_id"]),
            )
    return made


def set_cancel(user: sqlite3.Row, cancel: bool) -> None:
    """Cancels at the end of the paid period (Premium stays until then), or undoes it."""
    sub = subscription(user["id"])
    if not _active(sub):
        raise HTTPException(status_code=404, detail="No tienes ninguna suscripción activa.")
    if sub["provider"] == "stripe":
        import stripe_billing
        stripe_billing.set_cancel(sub["stripe_subscription_id"], cancel)
    with connect() as conn:
        conn.execute(
            "UPDATE subscriptions SET cancel_at_period_end = ?, next_plan_id = NULL, "
            "updated_at = datetime('now') WHERE user_id = ?",
            (int(cancel), user["id"]),
        )


def checkout(user: sqlite3.Row, plan_id: str) -> dict:
    """Starts a subscription: simulated in test mode, or a Stripe Checkout page to pay."""
    if BILLING_MODE == "stripe":
        _check_plan(plan_id)
        if _active(subscription(user["id"])):
            raise HTTPException(status_code=409, detail="Ya tienes una suscripción. Para cambiar de plan, "
                                "cancela la actual y elige el nuevo cuando termine.")
        import stripe_billing
        return {"checkout_url": stripe_billing.create_checkout(user, plan_id)}
    subscribe_test(user, plan_id)
    return status(_user(user["id"]))
