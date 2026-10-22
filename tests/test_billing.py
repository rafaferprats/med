"""Free questions in total, subscriptions charged at the right frequency, and Stripe webhooks.

Runs without Ollama and without Stripe (the clock and the Stripe API are faked).
    ./venv/bin/python tests/test_billing.py
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.update({"DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"), "FREE_QUESTIONS": "4",
                   "FREE_IMAGES": "1", "QUESTIONS_PER_MINUTE": "100", "SIGNUPS_PER_HOUR": "100",
                   "STRIPE_WEBHOOK_SECRET": "whsec_test", "STRIPE_SECRET_KEY": "sk_test_x",
                   "STRIPE_PRICE_WEEKLY": "price_w", "STRIPE_PRICE_MONTHLY": "price_m",
                   "STRIPE_PRICE_YEARLY": "price_y"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import billing  # noqa: E402
import main  # noqa: E402
import stripe_billing  # noqa: E402

UTC = timezone.utc
clock = {"now": datetime(2026, 1, 31, 10, 0, tzinfo=UTC)}
billing._now = lambda: clock["now"]
results = []


def check(name, ok):
    results.append((name, bool(ok)))


class FakeChunk:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()


async def fake_chat(**kwargs):
    async def gen():
        yield FakeChunk("Respuesta.")
    return gen()


main.client.chat = fake_chat
ASK = {"message": "Me duele un poco la cabeza."}


def new_client(email):
    c = TestClient(main.app)
    assert c.post("/auth/register", json={"email": email, "password": "password123"}).status_code == 204
    return c, auth.authenticate(email, "password123")


def payments(user_id):
    return billing.payments(user_id)


def sub(user_id):
    return billing.subscription(user_id)


def at(*args):
    return datetime(*args, tzinfo=UTC)


def test_free_questions():
    c, uid = new_client("free@example.com")
    me = c.get("/me").json()
    check("al registrarse: 4 preguntas, 1 imagen y 1 analítica",
          me["remaining"] == {"text": 4, "image": 1, "lab": 1} and me["text_period"] == "welcome")
    codes = [c.post("/chat/stream", json=ASK) for _ in range(5)]
    check("4 preguntas gratis y la 5.ª pide pagar", [r.status_code for r in codes] == [200, 200, 200, 200, 402])
    check("ese día: el aviso dice que mañana habrá 1 gratis", "vuelve mañana" in codes[4].json()["detail"])
    check("la 4.ª ya avisa por cabecera de que pasa al modo diario",
          codes[3].headers.get("X-Usage-Text") == "0" and codes[3].headers.get("X-Usage-Period") == "day")
    clock["now"] += timedelta(days=1)
    me = c.get("/me").json()
    check("al día siguiente: 1 pregunta gratis", me["remaining"]["text"] == 1 and me["text_period"] == "day"
          and me["limits"]["text"] == 1)
    r1, r2 = c.post("/chat/stream", json=ASK), c.post("/chat/stream", json=ASK)
    check("al día siguiente: 1 respondida y la 2.ª pide pagar", r1.status_code == 200 and r2.status_code == 402)
    check("aviso de la diaria gastada", "pregunta gratis de hoy" in r2.json()["detail"])
    billing.refund(uid, "text")  # the model failed: the daily question comes back
    check("si el modelo falla, se devuelve la pregunta diaria", c.get("/me").json()["remaining"]["text"] == 1)
    check("y el paquete de bienvenida sigue gastado", auth.user_by_id(uid)["free_text_used"] == 4)
    c.post("/chat/stream", json=ASK)
    clock["now"] += timedelta(days=1)
    check("otro día: otra pregunta gratis", c.post("/chat/stream", json=ASK).status_code == 200)
    r = c.post("/chat/stream", json={"message": "Tengo un dolor en el pecho que se me va al brazo izquierdo"})
    check("urgencia respondida aunque no queden preguntas", r.status_code == 200 and r.headers.get("X-Emergency") == "1")
    check("imagen y analítica siguen sin usar", c.get("/me").json()["remaining"]["image"] == 1)


def test_intervals():
    check("mensual desde el 31 de enero: 28 feb, 31 mar, 30 abr",
          [billing.add_interval(at(2026, 1, 31), "monthly", n).date().isoformat() for n in (1, 2, 3)]
          == ["2026-02-28", "2026-03-31", "2026-04-30"])
    check("semanal: 7 días", billing.add_interval(at(2026, 1, 31), "weekly") == at(2026, 2, 7))
    check("anual desde el 29 feb: 28 feb del año siguiente", billing.add_interval(at(2028, 2, 29), "yearly") == at(2029, 2, 28))


def test_weekly_renewals():
    clock["now"] = at(2026, 3, 2, 9)
    c, uid = new_client("weekly@example.com")
    status = c.post("/billing/checkout", json={"plan_id": "weekly"}).json()
    check("al suscribirse se cobra el primer periodo", len(payments(uid)) == 1 and status["plan"] == "premium")
    check("primer periodo de 7 días", sub(uid)["current_period_end"] == at(2026, 3, 9, 9).isoformat())
    c.post("/billing/checkout", json={"plan_id": "weekly"})
    check("suscribirse otra vez no cobra dos veces", len(payments(uid)) == 1)
    clock["now"] = at(2026, 3, 9, 8, 59)
    billing.renew_due()
    check("no se cobra antes de que acabe el periodo", len(payments(uid)) == 1)
    clock["now"] = at(2026, 3, 9, 9, 1)
    billing.renew_due()
    p = payments(uid)
    check("al acabar la semana se cobra la siguiente", len(p) == 2 and p[1]["period_end"] == at(2026, 3, 16, 9).isoformat())
    clock["now"] = at(2026, 3, 30, 10)  # server off for two weeks: every missed week is charged once
    billing.renew_due()
    p = payments(uid)
    check("una renovación por semana, sin huecos ni duplicados",
          [x["period_start"][:10] for x in p] == ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23", "2026-03-30"])
    check("cada cobro es el precio semanal", all(x["amount_eur"] == 2.99 for x in p))
    check("Premium hasta el final del periodo pagado", c.get("/me").json()["premium_until"] == at(2026, 4, 6, 9).isoformat())


def test_monthly_and_lazy_renewal():
    clock["now"] = at(2026, 1, 31, 12)
    c, uid = new_client("monthly@example.com")
    c.post("/billing/checkout", json={"plan_id": "monthly"})
    clock["now"] = at(2026, 3, 31, 12, 30)
    me = c.get("/me").json()  # the renewal also runs when the user comes back, without the loop
    p = payments(uid)
    check("mensual: cobros el 31 ene, 28 feb y 31 mar",
          [x["period_start"][:10] for x in p] == ["2026-01-31", "2026-02-28", "2026-03-31"])
    check("mensual: próximo cobro el 30 de abril",
          me["plan"] == "premium" and me["subscription"]["current_period_end"][:10] == "2026-04-30")


def test_cancel_and_resume():
    clock["now"] = at(2026, 5, 1, 8)
    c, uid = new_client("cancel@example.com")
    c.post("/billing/checkout", json={"plan_id": "weekly"})
    me = c.post("/billing/cancel").json()
    check("cancelar mantiene Premium hasta el final del periodo",
          me["plan"] == "premium" and me["subscription"]["cancel_at_period_end"])
    me = c.post("/billing/resume").json()
    check("reanudar quita la cancelación", not me["subscription"]["cancel_at_period_end"])
    c.post("/billing/cancel")
    clock["now"] = at(2026, 5, 20)
    billing.renew_due()
    me = c.get("/me").json()
    check("cancelada: no se cobra más", len(payments(uid)) == 1)
    check("cancelada: vuelve a gratis al acabar el periodo", me["plan"] == "free" and me["subscription"] is None)
    check("cancelar sin suscripción da 404", c.post("/billing/cancel").status_code == 404)
    c.post("/billing/checkout", json={"plan_id": "monthly"})
    check("puede volver a suscribirse después", len(payments(uid)) == 2 and sub(uid)["plan_id"] == "monthly")


def test_plan_change():
    clock["now"] = at(2026, 6, 1, 8)
    c, uid = new_client("change@example.com")
    c.post("/billing/checkout", json={"plan_id": "weekly"})
    me = c.post("/billing/checkout", json={"plan_id": "yearly"}).json()
    check("cambio de plan: sin cobro inmediato", len(payments(uid)) == 1 and me["subscription"]["next_plan_id"] == "yearly")
    clock["now"] = at(2026, 6, 8, 9)
    billing.renew_due()
    p = payments(uid)
    check("cambio de plan: el anual empieza al acabar la semana",
          p[-1]["plan_id"] == "yearly" and p[-1]["amount_eur"] == 49.99
          and p[-1]["period_start"] == at(2026, 6, 8, 8).isoformat()
          and p[-1]["period_end"] == at(2027, 6, 8, 8).isoformat())


def signed(event, secret="whsec_test", ts=None):
    body = json.dumps(event).encode()
    ts = str(int(ts if ts is not None else time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


def invoice(inv_id, sub_id, uid, start, end, price="price_m", amount=799, new_api=True):
    line = {"period": {"start": int(start.timestamp()), "end": int(end.timestamp())}}
    details = {"subscription": sub_id, "metadata": {"user_id": str(uid), "plan_id": "monthly"}}
    obj = {"id": inv_id, "customer": "cus_1", "amount_paid": amount, "lines": {"data": [line]}}
    if new_api:  # API 2025+: subscription under parent, price under pricing
        line["pricing"] = {"price_details": {"price": price}}
        obj["parent"] = {"subscription_details": details}
    else:
        line["price"] = {"id": price}
        obj["subscription"] = sub_id
        obj["subscription_details"] = {"metadata": details["metadata"]}
    return {"type": "invoice.paid", "data": {"object": obj}}


def test_stripe():
    billing_mode = billing.BILLING_MODE
    billing.BILLING_MODE = "stripe"
    sent = {}

    def fake_post(path, data):
        sent[path] = data
        return {"url": "https://checkout.stripe.com/c/pay/cs_test"}
    stripe_billing._post = fake_post
    clock["now"] = datetime.now(UTC)  # Stripe dates are real ones
    c, uid = new_client("stripe@example.com")
    web = TestClient(main.app)
    try:
        r = c.post("/billing/checkout", json={"plan_id": "monthly"}).json()
        data = sent["/checkout/sessions"]
        check("Stripe: devuelve la página de pago", r == {"checkout_url": "https://checkout.stripe.com/c/pay/cs_test"})
        check("Stripe: suscripción con el precio del plan",
              data["mode"] == "subscription" and data["line_items[0][price]"] == "price_m"
              and data["subscription_data[metadata][user_id]"] == str(uid))

        body, headers = signed({"type": "invoice.paid"}, secret="otro")
        check("webhook: firma incorrecta rechazada", web.post("/billing/webhook", content=body, headers=headers).status_code == 400)
        body, headers = signed({"type": "invoice.paid"}, ts=time.time() - 3600)
        check("webhook: firma antigua rechazada", web.post("/billing/webhook", content=body, headers=headers).status_code == 400)

        now = datetime.now(UTC).replace(microsecond=0)
        end = now + timedelta(days=30)
        # invoice.paid can arrive before checkout.session.completed: the metadata says whose it is
        body, headers = signed(invoice("in_1", "sub_1", uid, now, end))
        check("webhook: factura pagada aceptada", web.post("/billing/webhook", content=body, headers=headers).status_code == 200)
        web.post("/billing/webhook", content=body, headers=headers)  # Stripe retries: paid once
        body, headers = signed({"type": "checkout.session.completed", "data": {"object": {
            "mode": "subscription", "client_reference_id": str(uid), "subscription": "sub_1",
            "customer": "cus_1", "metadata": {"user_id": str(uid), "plan_id": "monthly"}}}})
        web.post("/billing/webhook", content=body, headers=headers)
        me = c.get("/me").json()
        check("Stripe: Premium hasta el final del periodo pagado", me["plan"] == "premium" and me["premium_until"] == end.isoformat())
        check("Stripe: un webhook repetido no cobra dos veces", len(payments(uid)) == 1 and payments(uid)[0]["amount_eur"] == 7.99)
        check("Stripe: la suscripción guarda sus ids", sub(uid)["stripe_subscription_id"] == "sub_1" and sub(uid)["stripe_customer_id"] == "cus_1")

        end2 = end + timedelta(days=31)
        body, headers = signed(invoice("in_2", "sub_1", uid, end, end2, new_api=False))
        web.post("/billing/webhook", content=body, headers=headers)
        check("Stripe: la renovación mensual alarga Premium (API antigua también)",
              c.get("/me").json()["premium_until"] == end2.isoformat() and len(payments(uid)) == 2)

        c.post("/billing/cancel")
        check("Stripe: cancelar se pide a Stripe", sent.get("/subscriptions/sub_1") == {"cancel_at_period_end": "true"})
        body, headers = signed({"type": "customer.subscription.deleted", "data": {"object": {
            "id": "sub_1", "status": "canceled", "items": {"data": [{"price": {"id": "price_m"}}]}}}})
        web.post("/billing/webhook", content=body, headers=headers)
        check("Stripe: borrada en Stripe, mantiene lo ya pagado",
              sub(uid)["status"] == "canceled" and c.get("/me").json()["plan"] == "premium")
    finally:
        billing.BILLING_MODE = billing_mode


if __name__ == "__main__":
    for test in (test_free_questions, test_intervals, test_weekly_renewals, test_monthly_and_lazy_renewal,
                 test_cancel_and_resume, test_plan_change, test_stripe):
        test()
    failed = [name for name, ok in results if not ok]
    for name in failed:
        print("FALLA:", name)
    print(f"billing tests: {len(results) - len(failed)}/{len(results)} OK")
    sys.exit(1 if failed else 0)
