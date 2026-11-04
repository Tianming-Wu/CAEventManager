# -*- coding: utf-8 -*-
"""SQLite storage layer for the ticket lottery service.

Design notes
------------
* ``marks`` holds the *current* state of (student, kind).  ``audit`` holds the
  full history so every action can be explained and (in unlocked mode) undone.
* ``tickets`` is append-only-ish: a ticket is "live" while ``released_at`` is
  NULL.  Undoing a mark releases its ticket instead of deleting the row, so the
  same number can be handed back to the same (student, kind) if it is re-marked.
* Ticket numbers are handed out sequentially (1, 2, 3, ...) in the order tickets
  are earned.  That keeps the numbering independent of the pool size, which is
  what makes the pool safe to raise *and* lower in the middle of the event.
"""

import os
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("LOTTERY_DATA_DIR") or os.path.join(BASE_DIR, "data")

# One file per event, named after its numeric id.  The id is a counter, so the
# name cannot collide, and the file is the unit of backup, export and deletion.
EVENTS_DIR = os.path.join(DATA_DIR, "events")
LEGACY_DB = os.path.join(DATA_DIR, "lottery.sqlite3")
DEFAULT_SEQ = 1


def use_data_dir(path):
    """Point everything at another data directory.

    The paths are derived once at import, which is fine for the service but not
    for tests or tools that build a throwaway installation.
    """
    global DATA_DIR, EVENTS_DIR, LEGACY_DB, DB_PATH
    DATA_DIR = os.path.abspath(path)
    EVENTS_DIR = os.path.join(DATA_DIR, "events")
    LEGACY_DB = os.path.join(DATA_DIR, "lottery.sqlite3")
    DB_PATH = event_path(DEFAULT_SEQ)
    _local.conn = None
    _local.path = None


def event_path(seq):
    return os.path.join(EVENTS_DIR, "%s.sqlite3" % int(seq))


# kept for the tools that used to poke at DB_PATH
DB_PATH = event_path(DEFAULT_SEQ)


def migrate_legacy():
    """Move the single-file database into the per-event layout.

    Runs once: the old name disappears the moment the move succeeds.
    """
    if not os.path.exists(LEGACY_DB) or os.path.exists(event_path(1)):
        return False
    os.makedirs(EVENTS_DIR, exist_ok=True)
    os.replace(LEGACY_DB, event_path(1))
    for suffix in ("-wal", "-shm"):
        old = LEGACY_DB + suffix
        if os.path.exists(old):
            os.replace(old, event_path(1) + suffix)
    return True


def set_current_event(seq):
    """Point this thread's connection at one event's database."""
    if seq is None:
        return
    if getattr(_local, "seq", None) != int(seq):
        _local.seq = int(seq)


def current_event():
    """Which event this thread is serving; the dispatcher sets it per request."""
    return getattr(_local, "seq", DEFAULT_SEQ)

KINDS = ("checkin", "task1", "task2")
KIND_LABEL = {"checkin": "签到", "task1": "任务一", "task2": "任务二", "bonus": "速度奖励"}

DEFAULT_CONFIG = {
    "kind": "nwuca.lottery",     # which activity kind draws this activity
    "title": "抽奖活动",
    "subtitle": "",
    # pool: "auto" => present * 3 + bonus_constant (recomputed live, follows the
    # real attendance and can never starve the guaranteed tickets)
    #       "fixed" => exactly pool_size
    "pool_mode": "auto",
    "pool_size": 200,           # used when pool_mode == "fixed"
    "bonus_constant": 20,       # extra speed-reward tickets in auto mode
    "nominal_guaranteed": 180,  # informational only
    "beta": 1.0,
    "public_base_url": "",
    "show_times": True,
    "show_sid": False,
    "tie_seed": 0,              # 0 => generated lazily; breaks remainder ties
    "finalized": False,
    "finalized_at": 0,
    # name + how many units + how they are drawn ("ticket" = by ticket,
    # "person" = equal chance among the people in the room)
    # order here is the draw order shown to the operator: smallest prize first
    "prizes": [
        {"name": "三等奖", "count": 10, "mode": "ticket"},
        {"name": "二等奖", "count": 5, "mode": "ticket"},
        {"name": "一等奖", "count": 2, "mode": "ticket"},
        {"name": "特等奖", "count": 1, "mode": "person"},
    ],
    "allow_bonus": True,
    # de-duplication, two independent axes: inside one prize, and across prizes.
    # by-person prizes only follow the first (see model.draw_rules)
    "draw_unique_inside": True,
    "draw_unique_across": False,
    "person_draw_no_repeat": True,  # older key, read when the new one is absent
    "screen_phase": "signin",       # signin | draw - drives what the big screen shows
    "screen_show_wall": False,      # the per-person name grid; off by default
    # short-lived /s/<code> alias, for machines where nobody can paste the
    # 32-character access id (the projector laptop, typically)
    "shortlink_code": "",
    "shortlink_target": "screen",
    "shortlink_expires": 0,
    "self_checkin": True,           # allow scanning the on-screen QR to check in
    "event_timezone_offset": 8,  # only used for CSV export / server-side text
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sid         TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    py          TEXT,
    pyi         TEXT,
    grp         TEXT,
    note        TEXT,
    role        TEXT    NOT NULL DEFAULT 'participant',  -- participant | staff | volunteer
    origin      TEXT    NOT NULL DEFAULT 'manual',       -- manual | import | roster | signup
    detached_at REAL    NOT NULL DEFAULT 0,              -- 绑定的名单里已经没有这个人
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS marks (
    student_id  INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    done_at     REAL    NOT NULL,
    source      TEXT,
    actor       TEXT,
    PRIMARY KEY (student_id, kind)
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    code        INTEGER NOT NULL,
    assigned_at REAL    NOT NULL,
    released_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS tickets_live_code
    ON tickets(code) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS tickets_live_student
    ON tickets(student_id) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    actor       TEXT,
    action      TEXT    NOT NULL,
    student_id  INTEGER,
    kind        TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);

CREATE TABLE IF NOT EXISTS draws (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seq         INTEGER NOT NULL,
    prize       TEXT,
    mode        TEXT    NOT NULL DEFAULT 'ticket',  -- 'ticket' | 'person'
    code        INTEGER,
    student_id  INTEGER,
    ts          REAL    NOT NULL,
    voided_at   REAL,
    batch       TEXT                                -- draws made by one click share it
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False
_write_lock = threading.Lock()


class tx:
    """Serialised write transaction.

    The service is tiny (one operator, ~60 rows), so writes take a process-wide
    lock and an IMMEDIATE transaction: no interleaving, no half-applied marks.
    """

    def __init__(self, conn=None):
        self.conn = conn

    def __enter__(self):
        _write_lock.acquire()
        self.conn = self.conn or connect()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except Exception:
            _write_lock.release()
            raise
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            _write_lock.release()
        return False


def connect():
    """Thread-local connection (http.server serves each request in its own thread)."""
    conn = getattr(_local, "conn", None)
    path = event_path(getattr(_local, "seq", None) or DEFAULT_SEQ)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    _local.conn = conn
    _local.path = path
    return conn


def init_db():
    """Create schema and seed missing config keys.  Safe to call repeatedly."""
    global _initialized
    migrate_legacy()
    with _init_lock:
        conn = connect()
        conn.executescript(SCHEMA)
        # migrations for databases created by an older build
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(draws)")}
        if "mode" not in cols:
            conn.execute("ALTER TABLE draws ADD COLUMN mode TEXT NOT NULL DEFAULT 'ticket'")
        if "batch" not in cols:
            conn.execute("ALTER TABLE draws ADD COLUMN batch TEXT")
        scols = {row["name"] for row in conn.execute("PRAGMA table_info(students)")}
        if "origin" not in scols:
            conn.execute(
                "ALTER TABLE students ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'"
            )
        if "detached_at" not in scols:
            conn.execute(
                "ALTER TABLE students ADD COLUMN detached_at REAL NOT NULL DEFAULT 0"
            )
        if "role" not in scols:
            conn.execute(
                "ALTER TABLE students ADD COLUMN role TEXT NOT NULL DEFAULT 'participant'"
            )
        now = time.time()
        for key, val in DEFAULT_CONFIG.items():
            cur = conn.execute("SELECT 1 FROM config WHERE key=?", (key,))
            if cur.fetchone() is None:
                set_config_raw(conn, key, val)
        conn.execute(
            "INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)",
            (now, "system", "init", "database initialised"),
        )
        _initialized = True


# --------------------------------------------------------------------------- #
# config helpers
# --------------------------------------------------------------------------- #
def get_config_raw(conn, key, default=None):
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_config_raw(conn, key, value):
    import json

    conn.execute(
        "INSERT INTO config (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def get_config(conn, key, default=None):
    import json

    raw = get_config_raw(conn, key, None)
    if raw is None:
        return default if default is not None else DEFAULT_CONFIG.get(key)
    try:
        return json.loads(raw)
    except Exception:
        return raw


def all_config(conn):
    import json

    out = dict(DEFAULT_CONFIG)
    for row in conn.execute("SELECT key, value FROM config"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except Exception:
            out[row["key"]] = row["value"]
    return out


def config_view(conn):
    """Config with secrets removed - this is what clients may see."""
    cfg = all_config(conn)
    return cfg


def public_config_view(conn):
    """Subset safe for the unauthenticated public page."""
    cfg = config_view(conn)
    keep = (
        "title",
        "subtitle",
        "pool_size",
        "show_times",
        "show_sid",
        "finalized",
        "finalized_at",
        "event_timezone_offset",
        "allow_bonus",
        "public_base_url",
        "screen_phase",
        "self_checkin",
        "screen_show_wall",
    )
    out = {k: cfg.get(k) for k in keep}
    out["prizes"] = list(cfg.get("prizes") or [])
    out["nominal_guaranteed"] = cfg.get("nominal_guaranteed")
    return out


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #
def audit(conn, action, student_id=None, kind=None, detail=None, actor="admin"):
    conn.execute(
        "INSERT INTO audit (ts, actor, action, student_id, kind, detail) VALUES (?,?,?,?,?,?)",
        (time.time(), actor, action, student_id, kind, detail),
    )


def audit_tail(conn, limit=200):
    rows = conn.execute(
        "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
