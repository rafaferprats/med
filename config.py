"""App settings. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "app.db"))

# Free plan: questions and images an account gets in total when it signs up (not per day)
FREE_QUESTIONS = int(os.getenv("FREE_QUESTIONS", "4"))
FREE_IMAGES = int(os.getenv("FREE_IMAGES", "1"))
FREE_LABS = int(os.getenv("FREE_LABS", "1"))  # lab report PDFs explained
# After the sign-up questions are used: this many free questions a day, from the next day on
FREE_DAILY_QUESTIONS = int(os.getenv("FREE_DAILY_QUESTIONS", "1"))

# Visitors without an account: questions before they are asked to sign up
GUEST_QUESTIONS = int(os.getenv("GUEST_QUESTIONS", "1"))
GUEST_COOKIE_NAME = "medapp_guest"

# "test" simulates the payments (renewals included) without charging, "stripe" charges
# with Stripe, "off" disables checkout
BILLING_MODE = os.getenv("BILLING_MODE", "test")

# Stripe (only with BILLING_MODE=stripe). Each plan needs a recurring Price created in Stripe
# with the same amount and interval as below.
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# Public address of the app, where Stripe sends the user back after paying
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")

# interval: how often the subscription is charged (week, month or year)
PLANS = {
    "weekly": {"name": "Semanal", "price_eur": 2.99, "interval": "week",
               "stripe_price": os.getenv("STRIPE_PRICE_WEEKLY", "")},
    "monthly": {"name": "Mensual", "price_eur": 7.99, "interval": "month",
                "stripe_price": os.getenv("STRIPE_PRICE_MONTHLY", "")},
    "yearly": {"name": "Anual", "price_eur": 49.99, "interval": "year", "best_value": True,
               "stripe_price": os.getenv("STRIPE_PRICE_YEARLY", "")},
}

# What each plan includes, shown in the plans window (✓ / – or a short text). Only what the app does.
FEATURES = [
    ("Preguntas de salud", f"{FREE_QUESTIONS} al registrarte y {FREE_DAILY_QUESTIONS} al día", "Sin límite"),
    ("Fotos de piel, heridas o medicamentos", f"{FREE_IMAGES} gratis", "Sin límite"),
    ("Analíticas en PDF explicadas valor a valor", f"{FREE_LABS} gratis", "Sin límite"),
    ("Seguir preguntando cuando se acaban las gratis", False, True),
    ("Medicamentos sin receta adaptados a embarazo, niños y alergias", True, True),
    ("Avisos de urgencia", True, True),
]

TOKEN_DAYS = 30
COOKIE_NAME = "medapp_session"
MIN_PASSWORD_LENGTH = 8
