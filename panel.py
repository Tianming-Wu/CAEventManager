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
STATUS_LABEL = {"pending": "待审核", "active": "正常", "disabled": "已停用",
                "rejected": "已拒绝", "banned": "已封禁"}
COOKIE = "xl_panel_session"
SESSION_TTL = 7 * 24 * 3600
# How long a re-entered password keeps the session elevated.  It lives on the
# session row, not on the account: another device signing in as the same person
# starts from zero, and signing out drops it with the session.
SUDO_TTL = 600
FOOTER = "CAEventManager | Powered with ❤ by Tianming &amp; Deepseek ❤🐋"

# The events table: ``seq`` is the storage id, ``token`` is the access one.
# Keeping them apart makes rotating the token a one-column update instead of a
# data move, and it keeps the numeric id out of every URL.
EVENTS_DDL = """CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT '',
    params      TEXT    NOT NULL DEFAULT '{}',
    owner_id    INTEGER,
    created_at  REAL    NOT NULL,
    archived_at REAL    NOT NULL DEFAULT 0
);"""

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
    last_login  REAL    NOT NULL DEFAULT 0,
    avatar      TEXT    NOT NULL DEFAULT '',
    avatar_at   REAL    NOT NULL DEFAULT 0,
    ban_reason  TEXT    NOT NULL DEFAULT ''
);

""" + EVENTS_DDL + """

CREATE TABLE IF NOT EXISTS event_members (
    seq        INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    role       TEXT    NOT NULL DEFAULT 'member',   -- owner | member
    state      TEXT    NOT NULL DEFAULT 'pending',  -- pending | active
    added_at   REAL    NOT NULL,
    PRIMARY KEY (seq, account_id)
);

CREATE TABLE IF NOT EXISTS account_groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS account_group_members (
    group_id   INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    PRIMARY KEY (group_id, account_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    account_id  INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    ip          TEXT,
    sudo_until  REAL NOT NULL DEFAULT 0
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


# Columns added after the first release.  init_db() adds whatever is missing,
# so an existing database gains them without a migration step on the operator.
LATE_COLUMNS = (
    ("accounts", "avatar", "TEXT NOT NULL DEFAULT ''"),
    ("accounts", "avatar_at", "REAL NOT NULL DEFAULT 0"),
    ("accounts", "ban_reason", "TEXT NOT NULL DEFAULT ''"),
    ("sessions", "sudo_until", "REAL NOT NULL DEFAULT 0"),
)


DEFAULT_KIND = "nwuca.lottery"


def _migrate_events(conn):
    """Rewrite the first events table into the id/token shape.

    The old table keyed an event by its token, so rotating a token meant moving
    data.  ``seq`` is the id now: a counter, so it cannot collide, and it never
    appears in a URL.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if not columns or "seq" in columns:
        return False

    owner_id = None
    row = conn.execute("SELECT id FROM accounts WHERE role='owner'").fetchone()
    if row is not None:
        owner_id = row["id"]

    conn.execute("ALTER TABLE events RENAME TO events_old")
    conn.executescript(EVENTS_DDL)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS event_members (
            seq        INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            role       TEXT    NOT NULL DEFAULT 'member',
            state      TEXT    NOT NULL DEFAULT 'pending',
            added_at   REAL    NOT NULL,
            PRIMARY KEY (seq, account_id)
        );
    """)

    for old in conn.execute("SELECT * FROM events_old ORDER BY created_at"):
        who = owner_id
        if old["owner"]:
            found = conn.execute("SELECT id FROM accounts WHERE username=?",
                                 (old["owner"],)).fetchone()
            if found is not None:
                who = found["id"]
        cur = conn.execute(
            "INSERT INTO events (token, title, note, kind, params, owner_id,"
            " created_at, archived_at) VALUES (?,?,?,?,?,?,?,?)",
            (old["id"], old["title"], old["note"], DEFAULT_KIND, "{}",
             who, old["created_at"], old["archived_at"]))
        if who is not None:
            conn.execute(
                "INSERT OR IGNORE INTO event_members"
                " (seq, account_id, role, state, added_at) VALUES (?,?,?,?,?)",
                (cur.lastrowid, who, "owner", "active", old["created_at"]))

    conn.execute("DROP TABLE events_old")
    conn.execute(
        "INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)",
        (time.time(), "system", "events_migrated", "id/token split"))
    return True


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    if _migrate_events(conn):
        conn.executescript(SCHEMA)
    for table, column, spec in LATE_COLUMNS:
        have = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, spec))
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


def session_sudo(conn, token):
    """Unix time the elevation ends, 0.0 when this session is not elevated."""
    if not token:
        return 0.0
    row = conn.execute("SELECT sudo_until FROM sessions WHERE token=?",
                       (token,)).fetchone()
    if row is None:
        return 0.0
    return float(row["sudo_until"] or 0)


def set_sudo(conn, token, seconds):
    """Elevate (seconds > 0) or drop the elevation; returns the new deadline."""
    until = time.time() + seconds if seconds else 0.0
    conn.execute("UPDATE sessions SET sudo_until=? WHERE token=?", (until, token))
    return until


def audit(conn, actor, action, target=None, detail=None):
    conn.execute(
        "INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
        (time.time(), actor, action, target, detail),
    )


AVATAR_DIR = "avatars"
AVATAR_TYPES = {
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
}
AVATAR_MAX = 512 * 1024


def avatar_dir():
    path = os.path.join(os.path.dirname(db_path()), AVATAR_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def avatar_path(name):
    """Only a file name we wrote ourselves ever reaches the filesystem."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    return os.path.join(avatar_dir(), name)


def sniff_image(data):
    """Return (extension, mime) for a PNG, JPEG or WebP payload, else None."""
    for magic, kind in AVATAR_TYPES.items():
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("webp", "image/webp")
    return None


def save_avatar(account_id, data):
    """Store the bytes and return the stored file name."""
    found = sniff_image(data)
    if found is None:
        raise ValueError("unsupported image")
    if len(data) > AVATAR_MAX:
        raise ValueError("too large")
    ext = found[0]
    # one file per account: replacing an avatar must not leave the old one around
    for old in (".png", ".jpg", ".webp"):
        stale = avatar_path("%d%s" % (account_id, old))
        if stale and os.path.exists(stale):
            os.unlink(stale)
    name = "%d.%s" % (account_id, ext)
    path = avatar_path(name)
    with open(path, "wb") as fh:
        fh.write(data)
    return name


def is_avatar_url(value):
    return (value or "").startswith(("http://", "https://"))


def clean_avatar_url(value):
    """Only http(s) URLs of a sane length are stored as a remote avatar."""
    url = (value or "").strip()
    if not url or len(url) > 500:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url


def drop_avatar(account_id):
    """Remove every stored file for this account."""
    for ext in (".png", ".jpg", ".webp"):
        path = avatar_path("%d%s" % (account_id, ext))
        if path and os.path.exists(path):
            os.unlink(path)


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
        "banReason": row["ban_reason"] if "ban_reason" in row.keys() else "",
        "avatar": row["avatar"] or "",
        "avatarAt": row["avatar_at"] or 0,
    }


def event_view(row, base, role=None):
    """Links and metadata for one event.

    Links are rebuilt from ``token`` every time and never stored, so rotating a
    token invalidates the old addresses without touching the event itself.
    ``base`` may already end in the token; both shapes are accepted.
    """
    base = (base or "").rstrip("/")
    token = row["token"]
    root = base if base.endswith("/" + token) else base + "/" + token
    return {
        "seq": row["seq"],
        "id": token,
        "token": token,
        "kind": row["kind"],
        "role": role,
        "title": row["title"],
        "note": row["note"],
        "ownerName": (row["owner_name"] if "owner_name" in row.keys() else "") or "",
        "createdAt": row["created_at"],
        "archivedAt": row["archived_at"],
        "url": root + "/",
        "screen": root + "/screen",
        "admin": root + "/admin",
    }


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def _sync_owner_identity(conn, owner, cfg, log=print, explicit=None):
    """Keep the owner row in step with config.json.

    Only keys that are literally present in the file count.  Without that rule a
    default value would fight the console: rename yourself to "Laffey" in the UI,
    restart, and the profile would silently go back to the default.  A typo in an
    explicit owner_username still renames the login, so both values are logged.
    """
    explicit = explicit or set()
    wanted_user = (cfg.get("owner_username") or "").strip() if "owner_username" in explicit else ""
    wanted_name = (cfg.get("owner_name") or "").strip() if "owner_name" in explicit else ""
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
        _sync_owner_identity(conn, owner, cfg, log, cfg.get("_explicit") or set())

    # the first event already exists as the service's original access id
    if event_token_hint:
        row = conn.execute("SELECT 1 FROM events WHERE token=?",
                           (event_token_hint,)).fetchone()
        if row is None:
            owner_row = conn.execute("SELECT id FROM accounts WHERE role='owner'").fetchone()
            with tx() as t:
                cur = t.execute(
                    "INSERT INTO events (token, title, note, kind, params, owner_id,"
                    " created_at) VALUES (?,?,?,?,?,?,?)",
                    (event_token_hint, "", "首个活动（沿用原有访问 id）",
                     DEFAULT_KIND, "{}", owner_row["id"] if owner_row else None,
                     time.time()),
                )
                if owner_row is not None:
                    t.execute(
                        "INSERT OR IGNORE INTO event_members"
                        " (seq, account_id, role, state, added_at) VALUES (?,?,?,?,?)",
                        (cur.lastrowid, owner_row["id"], "owner", "active", time.time()))
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
    """Every token that addresses an event: registered ones plus the configured one.

    Routing keys off this list, not off the configured public prefix, so the app
    answers on whatever path the request happened to arrive by (local, platform
    prefix, or a future subdomain).
    """
    tokens = {t for t in extra if t}
    try:
        conn = connect()
        for row in conn.execute("SELECT token FROM events WHERE archived_at=0"):
            tokens.add(row["token"])
    except Exception:
        pass
    return tokens


def event_seq(token):
    """The numeric id behind a token, or None."""
    if not token:
        return None
    try:
        row = connect().execute("SELECT seq FROM events WHERE token=?",
                                (token,)).fetchone()
    except Exception:
        return None
    return row["seq"] if row else None


def classify(raw_path, tokens):
    """Decide which family a request belongs to.

    Returns ("event", route, token) / ("panel", route, None) / (None, None, None).

    A path segment that equals a known event token wins over everything else, so
    ``/<token>/login`` stays the event's login and is never mistaken for the
    panel's.  Anything else may carry any prefix - we peel segments until a panel
    route appears, which is what makes local access work regardless of prefix.
    """
    parts = raw_path.split("/")
    for seg in parts:
        if seg and seg in tokens:
            idx = parts.index(seg)
            return "event", "/" + "/".join(parts[idx + 1:]), seg
    if _is_panel_route(raw_path):
        return "panel", raw_path, None
    for i in range(1, len(parts)):
        cand = "/" + "/".join(parts[i:])
        if _is_panel_route(cand):
            return "panel", cand, None
    return None, None, None


def cookie_path_for(raw_path, route):
    prefix = raw_path
    if route and prefix.endswith(route):
        prefix = prefix[: -len(route)]
    prefix = prefix.rstrip("/")
    return (prefix + "/") if prefix else "/"
