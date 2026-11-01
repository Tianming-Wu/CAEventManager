# -*- coding: utf-8 -*-
"""Admin authentication: password hashing, sessions, the "unlock" guard."""

import hashlib
import hmac
import secrets
import time

COOKIE_NAME = "laffey_lot_session"
SESSION_TTL = 7 * 24 * 3600
UNLOCK_TTL = 30  # seconds of inactivity; every undo restarts the clock

_FAILS = {}  # ip -> [count, blocked_until]


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #
def hash_password(password, salt=None, iterations=180000):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        algo, iterations, salt_hex, dk_hex = (stored or "").split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(dk.hex(), dk_hex)


def login_blocked(ip):
    rec = _FAILS.get(ip)
    if not rec:
        return 0
    if rec[1] > time.time():
        return int(rec[1] - time.time())
    return 0


def note_login_failure(ip, threshold=6, block_seconds=60):
    rec = _FAILS.setdefault(ip, [0, 0])
    rec[0] += 1
    if rec[0] >= threshold:
        rec[0] = 0
        rec[1] = time.time() + block_seconds


def clear_login_failures(ip):
    _FAILS.pop(ip, None)


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
def create_session(conn, ip=""):
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (token, created_at, expires_at, unlock_until, ip) VALUES (?,?,?,?,?)",
        (token, now, now + SESSION_TTL, 0, ip),
    )
    return token


def get_session(conn, token):
    if not token:
        return None
    row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if row is None:
        return None
    if row["expires_at"] < time.time():
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    return row


def destroy_session(conn, token):
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def purge_expired(conn):
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))


def set_unlock(conn, token, on):
    until = time.time() + UNLOCK_TTL if on else 0
    conn.execute("UPDATE sessions SET unlock_until=? WHERE token=?", (until, token))
    return until


def is_unlocked(session):
    return bool(session and session["unlock_until"] > time.time())


def touch_unlock(conn, token):
    conn.execute(
        "UPDATE sessions SET unlock_until=? WHERE token=?",
        (time.time() + UNLOCK_TTL, token),
    )
