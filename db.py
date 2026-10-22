"""SQLite storage for users, login tokens, usage, subscriptions and payments."""
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    premium_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    text_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    lab_count INTEGER NOT NULL DEFAULT 0,
    free_daily_used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
-- One subscription per user. Access to Premium comes from users.premium_until, which is
-- moved forward only when a period is paid.
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL,
    next_plan_id TEXT,                          -- plan change applied at the next renewal
    status TEXT NOT NULL,                       -- active, past_due or canceled
    cycle_anchor TEXT NOT NULL,                 -- start of the first period with this plan
    periods_paid INTEGER NOT NULL DEFAULT 0,    -- periods paid since cycle_anchor
    current_period_end TEXT NOT NULL,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL,                     -- test or stripe
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT UNIQUE,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL,
    amount_eur REAL NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_ref TEXT UNIQUE,                   -- Stripe invoice id: a repeated webhook pays once
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS guests (
    id TEXT PRIMARY KEY,
    questions INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Free questions used in total (added after the first version: counted from the usage so far)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for col, usage_col in (("free_text_used", "text_count"), ("free_images_used", "image_count")):
            if col not in columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
                conn.execute(f"UPDATE users SET {col} = (SELECT COALESCE(SUM({usage_col}), 0) "
                             "FROM usage WHERE usage.user_id = users.id)")
        if "free_labs_used" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN free_labs_used INTEGER NOT NULL DEFAULT 0")
        if "free_pack_done_on" not in columns:  # day the sign-up questions ran out
            conn.execute("ALTER TABLE users ADD COLUMN free_pack_done_on TEXT")
        usage_columns = {row["name"] for row in conn.execute("PRAGMA table_info(usage)")}
        if "lab_count" not in usage_columns:
            conn.execute("ALTER TABLE usage ADD COLUMN lab_count INTEGER NOT NULL DEFAULT 0")
        if "free_daily_used" not in usage_columns:  # the daily free question
            conn.execute("ALTER TABLE usage ADD COLUMN free_daily_used INTEGER NOT NULL DEFAULT 0")


@contextmanager
def connect():
    """Opens a connection, commits on success and always closes it."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
