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
import base64
import json
import modules
import rng
import paths
import sqlite3
import threading
import time

BASE_DIR = paths.APP_ROOT
DATA_DIR = os.environ.get("LOTTERY_DATA_DIR") or os.path.join(BASE_DIR, "data")

# 一个活动一个文件夹：业务库、审计日志与元数据都在里面，打包就是整个文件夹
EVENTS_DIR = os.path.join(DATA_DIR, "events")


def use_data_dir(path):
    """Point everything at another data directory.

    The paths are derived once at import, which is fine for the service but not
    for tests or tools that build a throwaway installation.
    """
    global DATA_DIR, EVENTS_DIR
    DATA_DIR = os.path.abspath(path)
    EVENTS_DIR = os.path.join(DATA_DIR, "events")
    _local.conn = None
    _local.path = None
    _local.seq = None


def event_dir(seq):
    """活动的目录；文件夹本身就是归档与备份的单位。"""
    return os.path.join(EVENTS_DIR, str(int(seq)))


def event_path(seq):
    """活动业务库：文件夹里的 data.sqlite3。"""
    return os.path.join(event_dir(seq), "data.sqlite3")


def audit_path(seq):
    """活动的审计日志：只追加，与业务库分开，随活动一起打包。"""
    return os.path.join(event_dir(seq), "audit.sqlite3")


def meta_path(seq):
    return os.path.join(event_dir(seq), "meta.json")


def write_meta(seq, **fields):
    """活动文件夹的元数据：打包之后外面还认得出这是哪一场。"""
    path = meta_path(seq)
    data = read_meta(seq)
    data.update({k: v for k, v in fields.items() if v is not None})
    os.makedirs(event_dir(seq), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return data


def read_meta(seq):
    try:
        with open(meta_path(seq), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}





def migrate_to_folders():
    """把旧的 `events/<号>.sqlite3` 搬成 `events/<号>/data.sqlite3`，并把审计日志分出去。

    只跑一次：文件夹已经存在就跳过。搬完把旧的 audit 表内容写进 audit.sqlite3，
    旧库里的那张表留着不删——它不再被读，但也不该在搬迁时丢东西。
    """
    if not os.path.isdir(EVENTS_DIR):
        return []
    moved = []
    for name in sorted(os.listdir(EVENTS_DIR)):
        if not name.endswith(".sqlite3") or name.endswith("-wal") or name.endswith("-shm"):
            continue
        stem = name[:-len(".sqlite3")]
        if not stem.isdigit():
            continue
        seq = int(stem)
        old = os.path.join(EVENTS_DIR, name)
        new = event_path(seq)
        if not os.path.isfile(old) or os.path.exists(new):
            continue
        os.makedirs(event_dir(seq), exist_ok=True)
        os.replace(old, new)
        for suffix in ("-wal", "-shm"):
            side = old + suffix
            if os.path.exists(side):
                os.replace(side, new + suffix)
        with sqlite3.connect(new) as conn:
            try:
                rows = conn.execute(
                    "SELECT ts, actor, action, detail FROM audit ORDER BY id").fetchall()
            except sqlite3.Error:
                rows = []
        if rows:
            with audit_tx(seq) as ac:
                ac.executemany(
                    "INSERT INTO audit (ts, actor, action, detail, student_id, kind)"
                    " VALUES (?,?,?,?,NULL,NULL)", rows)
        moved.append(seq)
    return moved


AUDIT_SCHEMA = """
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
"""


def audit_connect(seq=None):
    """活动审计库的连接：一个活动一个文件，只追加。"""
    seq = event_id() if seq is None else int(seq)
    os.makedirs(event_dir(seq), exist_ok=True)
    conn = sqlite3.connect(audit_path(seq), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(AUDIT_SCHEMA)
    return conn


class audit_tx(object):
    """审计库的写事务；与业务库分开，失败也不影响业务写入。"""

    def __init__(self, seq=None, conn=None):
        self.seq = seq
        self.conn = conn

    def __enter__(self):
        self.conn = self.conn or audit_connect(self.seq)
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self.conn.close()
            self.conn = None
        return False


def set_event(seq):
    """Point this thread's connection at one event's database (its numeric id)."""
    _local.seq = int(seq)
    if getattr(_local, "path", None) != event_path(seq):
        _local.conn = None
        _local.path = None


def event_id():
    """The event this thread is serving.  The dispatcher sets it per request."""
    seq = getattr(_local, "seq", None)
    if seq is None:
        raise RuntimeError("还没有选活动：先调用 db.set_event(seq)")
    return seq

KINDS = ("checkin", "task1", "task2")
KIND_LABEL = {"checkin": "签到", "task1": "任务一", "task2": "任务二", "bonus": "速度奖励"}

# 平台自己的默认配置；玩法相关的默认值在模块的 defaults.json 里，建活动时一并写入
DEFAULT_CONFIG = {
    "kind": "nwuca.lottery",      # 这个活动用的是哪个模块
    "title": "",
    "subtitle": "",
    "public_base_url": "",        # 对外地址由前缀 + token 算出来，这里只留旧值备用
    "self_checkin": True,         # 允许扫大屏二维码自助签到
    "event_timezone_offset": 8,   # 导出与文案里的时区
}

PLATFORM_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);;
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
    path = event_path(event_id())
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


def checkin_secret(conn):
    """这个活动的签到签名密钥；第一次用到时生成并写进它自己的库。"""
    secret = get_config(conn, "checkin_secret")
    if secret:
        return secret
    secret = base64.urlsafe_b64encode(rng.bytes(24)).decode()[:32]
    with tx(conn):
        set_config_raw(conn, "checkin_secret", secret)
    return secret


def init_db(kind=None):
    """建表并补默认配置：平台自己的表加这个活动的模块声明的表。可重复调用。"""
    global _initialized
    with _init_lock:
        conn = connect()
        conn.executescript(PLATFORM_SCHEMA)
        conn.execute("PRAGMA user_version")  # 打开一次，确保文件真的建出来
        audit_connect().close()             # 审计库也建出来，归档时两个文件都在
        # 干什么用的模块决定了它自己那几张表；没写 kind 就看库里的配置
        cfg_kind = kind or get_config(conn, "kind") or DEFAULT_CONFIG["kind"]
        module_schema = modules.kind_schema(cfg_kind)
        if module_schema:
            conn.executescript(module_schema)
        now = time.time()
        defaults = dict(DEFAULT_CONFIG)
        defaults.setdefault("kind", cfg_kind)
        # 模块的默认配置只在它自己的键缺的时候才写，平台的键以平台为准
        for key, val in modules.kind_defaults(cfg_kind).items():
            defaults.setdefault(key, val)
        for key, val in defaults.items():
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
    """活动级审计：写在活动自己的 audit.sqlite3 里，与业务库分开。"""
    with audit_tx(event_id()) as ac:
        ac.execute(
            "INSERT INTO audit (ts, actor, action, student_id, kind, detail)"
            " VALUES (?,?,?,?,?,?)",
            (time.time(), actor, action, student_id, kind, detail),
        )


def audit_tail(conn=None, limit=200):
    """最近的活动审计；从活动自己的审计库里读。"""
    ac = audit_connect()
    try:
        rows = ac.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        ac.close()
