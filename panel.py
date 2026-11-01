# -*- coding: utf-8 -*-
"""Master panel: accounts, tiers, registration approval, event list.

Lives at the service root (``/``, ``/login``, ``/panel``, ``/api/panel/*``) of the
same process that serves the event pages under ``/<event-id>/``.  It keeps its
own database file so account data and event data can be backed up, wiped and
reasoned about separately.

Accounts
--------
role    owner (站长, unique) > superadmin (超级管理员) > admin (管理员)
status  pending (待审核, cannot sign in) / active / disabled / rejected

Registration always creates admin + pending; only approval flips it to active.
"""

import json
import os
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlparse

import localconfig

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_TPL = os.path.join(BASE_DIR, "tpl")
PANEL_STATIC = os.path.join(BASE_DIR, "panel_static")

ROLES = ("owner", "superadmin", "admin")
ROLE_LABEL = {"owner": "站长", "superadmin": "超级管理员", "admin": "管理员"}
STATUS_LABEL = {"pending": "待审核", "active": "正常", "disabled": "已停用", "rejected": "已拒绝"}
COOKIE = "xl_panel_session"
SESSION_TTL = 7 * 24 * 3600
FOOTER = "xlottery project | Powered with ❤ by Tianming &amp; Deepseek ❤🐋"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL DEFAULT '',
    role        TEXT    NOT NULL DEFAULT 'admin',
    status      TEXT    NOT NULL DEFAULT 'pending',
    pass        TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL,
    decided_at  REAL    NOT NULL DEFAULT 0,
    decided_by  TEXT    NOT NULL DEFAULT '',
    last_login  REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,          -- the 32-char token in the URL
    title       TEXT    NOT NULL DEFAULT '',
    owner       TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL,
    archived_at REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    account_id  INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    ip          TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    actor   TEXT,
    action  TEXT NOT NULL,
    target  TEXT,
    detail  TEXT
);
"""

_local = threading.local()
_write_lock = threading.Lock()


def db_path():
    return os.path.join(
        os.environ.get("LOTTERY_DATA_DIR")
        or os.path.join(BASE_DIR, "data"),
        "panel.sqlite3",
    )


def connect():
    path = db_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=15000")
    _local.conn = conn
    _local.path = path
    return conn


class tx:
    def __enter__(self):
        _write_lock.acquire()
        self.conn = connect()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            _write_lock.release()
        return False


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)",
        (time.time(), "system", "panel_init", None),
    )


# --------------------------------------------------------------------------- #
# passwords / sessions
# --------------------------------------------------------------------------- #
def hash_password(password, salt=None, iterations=180000):
    import hashlib

    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt.hex(), dk.hex())


def verify_password(password, stored):
    import hashlib
    import hmac

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


def create_session(conn, account_id, ip=""):
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (token, account_id, created_at, expires_at, ip) VALUES (?,?,?,?,?)",
        (token, account_id, now, now + SESSION_TTL, ip),
    )
    return token


def session_account(conn, token):
    if not token:
        return None
    row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if row is None:
        return None
    if row["expires_at"] < time.time():
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    acc = conn.execute("SELECT * FROM accounts WHERE id=?", (row["account_id"],)).fetchone()
    if acc is None or acc["status"] != "active":
        return None
    return acc


def destroy_session(conn, token):
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def audit(conn, actor, action, target=None, detail=None):
    conn.execute(
        "INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
        (time.time(), actor, action, target, detail),
    )


def view(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"],
        "role": row["role"],
        "roleLabel": ROLE_LABEL.get(row["role"], row["role"]),
        "status": row["status"],
        "statusLabel": STATUS_LABEL.get(row["status"], row["status"]),
        "note": row["note"],
        "createdAt": row["created_at"],
        "decidedAt": row["decided_at"],
        "decidedBy": row["decided_by"],
        "lastLogin": row["last_login"],
    }


def event_view(row, base):
    return {
        "id": row["id"],
        "title": row["title"],
        "owner": row["owner"],
        "note": row["note"],
        "createdAt": row["created_at"],
        "archivedAt": row["archived_at"],
        "url": base + "/" + row["id"] + "/",
        "screen": base + "/" + row["id"] + "/screen",
        "admin": base + "/" + row["id"] + "/admin",
    }


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def _sync_owner_identity(conn, owner, cfg, log=print):
    """Keep the owner row in step with config.json.

    The config file is the source of truth here, which also means a typo in it
    renames the login.  That is why every change is logged with both values.
    """
    wanted_user = (cfg.get("owner_username") or "").strip()
    wanted_name = (cfg.get("owner_name") or "").strip()
    new_user = wanted_user or owner["username"]
    new_name = wanted_name or owner["name"]
    if new_user == owner["username"] and new_name == owner["name"]:
        return
    with tx() as t:
        t.execute("UPDATE accounts SET username=?, name=? WHERE id=?",
                  (new_user, new_name, owner["id"]))
        audit(t, "system", "owner_identity", new_user,
              "%s -> %s / %r -> %r" % (owner["username"], new_user,
                                       owner["name"], new_name))
    log("站长账号已按 config.json 更新")
    log("  username: %s -> %s" % (owner["username"], new_user))
    log("  name    : %r -> %r" % (owner["name"], new_name))


def bootstrap(cfg, event_token_hint=None, log=print):
    """Create the owner account on first run; record the pre-existing event.

    ``config.json`` owns the owner's login name and display name, so an existing
    installation follows the file too - see ``_sync_owner_identity``.
    """
    conn = connect()
    owner = conn.execute("SELECT * FROM accounts WHERE role='owner'").fetchone()
    if owner is None:
        username = (cfg.get("owner_username") or "owner").strip() or "owner"
        display = (cfg.get("owner_name") or "").strip()
        password = os.environ.get("XTPANEL_OWNER_PASSWORD") or secrets.token_urlsafe(9)
        with tx() as t:
            t.execute(
                "INSERT INTO accounts (username, name, role, status, pass, note, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (username, display, "owner", "active", hash_password(password), "",
                 time.time()),
            )
            audit(t, "system", "bootstrap_owner", username, None)
        path = os.path.join(os.path.dirname(db_path()), "owner_password.txt")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("username: %s\npassword: %s\n" % (username, password))
        except OSError:
            pass
        log("=" * 60)
        log("站长账号已创建")
        log("  username: %s" % username)
        log("  password: %s" % password)
        log("  （同时写入 data/owner_password.txt）")
        log("  登录名与显示名都来自 config.json 的 owner_username / owner_name")
        log("=" * 60)
    else:
        _sync_owner_identity(conn, owner, cfg, log)

    # the first event already exists as the service's original access id
    if event_token_hint:
        row = conn.execute("SELECT 1 FROM events WHERE id=?", (event_token_hint,)).fetchone()
        if row is None:
            with tx() as t:
                t.execute(
                    "INSERT INTO events (id, title, owner, note, created_at) VALUES (?,?,?,?,?)",
                    (event_token_hint, "", "", "首个活动（沿用原有访问 id）", time.time()),
                )
                audit(t, "system", "event_adopt", event_token_hint, None)


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #
PANEL_PAGES = {"/": "panel_login.html", "/login": "panel_login.html",
               "/register": "panel_register.html", "/panel": "panel_panel.html"}


def _is_panel_route(path):
    return (path in PANEL_PAGES
            or path.startswith("/api/panel/")
            or path.startswith("/panel_static/"))


def event_tokens(extra=()):
    """Every id that identifies an event: registered ones plus the current one.

    Routing keys off this list, not off the configured public prefix, so the app
    answers on whatever path the request happened to arrive by (local, platform
    prefix, or a future subdomain).
    """
    tokens = {t for t in extra if t}
    try:
        conn = connect()
        for row in conn.execute("SELECT id FROM events"):
            tokens.add(row["id"])
    except Exception:
        pass
    return tokens


def classify(raw_path, tokens):
    """Decide which family a request belongs to.

    Returns ("event", route) / ("panel", route) / (None, None).

    A path segment that equals a known event token wins over everything else, so
    ``/<token>/login`` stays the event's login and is never mistaken for the
    panel's.  Anything else may carry any prefix - we peel segments until a panel
    route appears, which is what makes local access work regardless of prefix.
    """
    parts = raw_path.split("/")
    for seg in parts:
        if seg and seg in tokens:
            idx = parts.index(seg)
            return "event", "/" + "/".join(parts[idx + 1:])
    if _is_panel_route(raw_path):
        return "panel", raw_path
    for i in range(1, len(parts)):
        cand = "/" + "/".join(parts[i:])
        if _is_panel_route(cand):
            return "panel", cand
    return None, None


def cookie_path_for(raw_path, route):
    prefix = raw_path
    if route and prefix.endswith(route):
        prefix = prefix[: -len(route)]
    prefix = prefix.rstrip("/")
    return (prefix + "/") if prefix else "/"
