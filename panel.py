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
import re
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
# The console's own "解锁修改": 30 seconds of quiet put the dangerous buttons
# back to sleep.  It is ergonomics for a live event, not a permission - the
# account is checked separately and does not change while the clock runs.
EVENT_UNLOCK_TTL = 30
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
    state      TEXT    NOT NULL DEFAULT 'pending',  -- pending | active | revoked | declined
    added_at   REAL    NOT NULL,
    invited_by INTEGER NOT NULL DEFAULT 0,          -- account id, 0 when unknown
    state_at   REAL    NOT NULL DEFAULT 0,
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
    sudo_until  REAL NOT NULL DEFAULT 0,
    unlock_until REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS presets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT    NOT NULL UNIQUE,       -- what a URL carries
    owner_id   INTEGER NOT NULL DEFAULT 0,    -- 0 for the built-in ones
    name       TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    module     TEXT    NOT NULL,
    params     TEXT    NOT NULL DEFAULT '{}',
    title      TEXT    NOT NULL DEFAULT '',
    visibility TEXT    NOT NULL DEFAULT 'private',  -- private | public | org
    org_id     INTEGER NOT NULL DEFAULT 0,          -- UNWIRED(orgs)
    builtin    INTEGER NOT NULL DEFAULT 0,
    created_at REAL    NOT NULL,
    updated_at REAL    NOT NULL,
    description  TEXT  NOT NULL DEFAULT '',         -- what this preset can do
    dep_module   TEXT  NOT NULL DEFAULT '',         -- the boundary it needs
    dep_template TEXT  NOT NULL DEFAULT '',         -- the preset it derives from
    source       TEXT  NOT NULL DEFAULT ''          -- module | builtin | instance | user
);

CREATE TABLE IF NOT EXISTS preset_favorites (
    account_id INTEGER NOT NULL,
    preset_id  INTEGER NOT NULL,
    created_at REAL    NOT NULL,
    PRIMARY KEY (account_id, preset_id)
);

CREATE TABLE IF NOT EXISTS module_state (
    module_id  TEXT    PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    updated_at REAL    NOT NULL,
    updated_by TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS drafts (
    account_id INTEGER NOT NULL,
    key        TEXT    NOT NULL,
    payload    TEXT    NOT NULL,
    updated_at REAL    NOT NULL,
    PRIMARY KEY (account_id, key)
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    level        TEXT    NOT NULL DEFAULT 'info',   -- info | important | critical
    title        TEXT    NOT NULL,
    body         TEXT    NOT NULL DEFAULT '',
    -- all（所有人）/ 0（只给某个账号，见 account_id）
    audience     TEXT    NOT NULL DEFAULT 'all',
    account_id   INTEGER NOT NULL DEFAULT 0,
    actor        TEXT    NOT NULL DEFAULT '',
    object       TEXT    NOT NULL DEFAULT '',       -- 例如 roster:3，用来做跳转
    read_at      REAL    NOT NULL DEFAULT 0,
    dismissed_at REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS notices_account ON notices(account_id, ts);

CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    actor   TEXT,
    action  TEXT NOT NULL,
    target  TEXT,
    detail  TEXT
);

-- API 令牌：明文只在创建那一刻出现，库里只留 sha256 与前四位/后五位（用来辨认是哪一张）
CREATE TABLE IF NOT EXISTS api_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL,
    name         TEXT    NOT NULL DEFAULT '',
    head         TEXT    NOT NULL DEFAULT '',    -- 明文的前四位
    tail         TEXT    NOT NULL DEFAULT '',    -- 明文的末五位
    hash         TEXT    NOT NULL,               -- sha256(完整令牌)，不存明文
    scopes       TEXT    NOT NULL DEFAULT 'read',
    created_at   REAL    NOT NULL,
    last_used_at REAL    NOT NULL DEFAULT 0,
    expires_at   REAL    NOT NULL DEFAULT 0,     -- 0 = 永不过期
    revoked_at   REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS api_tokens_owner ON api_tokens(account_id, created_at);

-- 运维助手的对话存档：一次会话一行，内容是一段 JSON（便于整体导出与算体积）
CREATE TABLE IF NOT EXISTS assistant_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    messages    TEXT    NOT NULL DEFAULT '[]',
    size        INTEGER NOT NULL DEFAULT 0,     -- 这段 JSON 的字节数，用来算配额
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    deleted_at  REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS assistant_sessions_owner
    ON assistant_sessions(account_id, updated_at);
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
    # 每个账号自己的模型配置（运维助手用）：key 只写不读，接口里不会回传
    ("accounts", "model_base", "TEXT NOT NULL DEFAULT ''"),
    ("accounts", "model_name", "TEXT NOT NULL DEFAULT ''"),
    ("accounts", "model_key", "TEXT NOT NULL DEFAULT ''"),
    # 对话存档的容量配额（字节）。0 表示用默认值；站点管理员可以按人调整
    ("accounts", "chat_quota", "INTEGER NOT NULL DEFAULT 0"),
    # 从侧栏进入运维助手时，是否接着上次那段（具体哪一段记在各自设备上）
    ("accounts", "chat_resume", "INTEGER NOT NULL DEFAULT 1"),
    ("sessions", "sudo_until", "REAL NOT NULL DEFAULT 0"),
    ("sessions", "unlock_until", "REAL NOT NULL DEFAULT 0"),
    # 最近一次带着这个会话发出请求的时刻：在线人数由此得来（见 presence()）
    ("sessions", "seen_at", "REAL NOT NULL DEFAULT 0"),
    ("event_members", "invited_by", "INTEGER NOT NULL DEFAULT 0"),
    ("event_members", "state_at", "REAL NOT NULL DEFAULT 0"),
    ("presets", "description", "TEXT NOT NULL DEFAULT ''"),
    ("presets", "dep_module", "TEXT NOT NULL DEFAULT ''"),
    ("presets", "dep_template", "TEXT NOT NULL DEFAULT ''"),
    ("presets", "source", "TEXT NOT NULL DEFAULT ''"),
)


LEVEL_LABEL = {"info": "通知", "important": "重要", "critical": "紧急"}
LEVELS = ("info", "important", "critical")

# --------------------------------------------------------------------------- #
# 登录名
# --------------------------------------------------------------------------- #
# 只收这些字符：中文、空格、点号都容易在别处（URL、脚本、日志）出岔子
USERNAME_RE = re.compile(r"^[0-9A-Za-z_-]{3,24}$")
USERNAME_RULE = "登录名需 3~24 位，只能用字母、数字、_ 和 -"

# 看着像官方、或者会被人当真的名字一律留着。宁可拒错，不留后门。
USERNAME_RESERVED = (
    "admin", "administrator", "root", "system", "sysadmin", "support", "help",
    "owner", "staff", "official", "service", "api", "bot", "security", "moderator",
    "superadmin", "caeventmanager", "caevent", "lottery", "eventmanager",
    "null", "undefined", "anonymous", "nobody", "me", "self", "test", "guest",
)

# 对外只有两句话：格式不对讲清楚怎么填，"不能用"不解释为什么——
# 分辨"被占用"与"被保留"等于告诉试探的人哪些名字值得抢。
USERNAME_TAKEN = "这个登录名不可用"


def username_problem(name):
    """登录名本身的毛病；格式之外的都不细说（见上面 USERNAME_TAKEN）。"""
    text = str(name or "")
    if not USERNAME_RE.match(text):
        return USERNAME_RULE
    if text.lower() in USERNAME_RESERVED:
        return USERNAME_TAKEN
    return ""


def username_taken(conn, name, skip_id=0):
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE lower(username)=lower(?) AND id<>?",
        (str(name or ""), int(skip_id or 0))).fetchone()
    return row is not None


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
        "INSERT INTO sessions (token, account_id, created_at, expires_at, ip, seen_at) "
        "VALUES (?,?,?,?,?,?)",
        (token, account_id, now, now + SESSION_TTL, ip, now),
    )
    return token


# 「在线」靠请求留下的痕迹判断：会话七天不过期，只看创建时间等于把七天都算成在线。
# 每分钟最多写一次，免得每轮轮询都落一次盘。
PRESENCE_TOUCH = 60
PRESENCE_WINDOW = 5 * 60


def touch_session(conn, token):
    """Note that this session is in use, at most once a minute."""
    if not token:
        return
    now = time.time()
    conn.execute(
        "UPDATE sessions SET seen_at=? WHERE token=? AND seen_at<?",
        (now, token, now - PRESENCE_TOUCH),
    )


def presence(conn):
    """(在线, 注册)：最近五分钟有请求的账号数，以及状态正常的账号总数。"""
    online = conn.execute(
        "SELECT COUNT(DISTINCT account_id) FROM sessions WHERE seen_at>=?",
        (time.time() - PRESENCE_WINDOW,),
    ).fetchone()[0]
    registered = conn.execute(
        "SELECT COUNT(*) FROM accounts WHERE status='active'").fetchone()[0]
    return {"online": int(online or 0), "registered": int(registered or 0)}


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


def unlock_until(conn, token):
    """When the console's 解锁修改 lapses, 0.0 when it is off."""
    if not token:
        return 0.0
    row = conn.execute("SELECT unlock_until FROM sessions WHERE token=?",
                       (token,)).fetchone()
    if row is None:
        return 0.0
    return float(row["unlock_until"] or 0)


def set_unlock(conn, token, on):
    until = time.time() + EVENT_UNLOCK_TTL if on else 0.0
    conn.execute("UPDATE sessions SET unlock_until=? WHERE token=?", (until, token))
    return until


def touch_unlock(conn, token):
    """Every undo restarts the countdown, so a run of them is one action."""
    return set_unlock(conn, token, True)


SEAT_LABEL = {"owner": "负责人", "member": "参与者"}
SEAT_STATE_LABEL = {"pending": "待确认", "active": "在场",
                    "revoked": "已移出", "declined": "已拒绝"}


def member_of(conn, seq, account_id):
    """This account's row on that event: role/state, or None if it is not on it."""
    if seq is None or account_id is None:
        return None
    return conn.execute(
        "SELECT role, state FROM event_members WHERE seq=? AND account_id=?",
        (int(seq), int(account_id))).fetchone()


def event_members(conn, seq):
    """Every seat on one event: those in place first, then pending, then history.

    A revoked seat is never deleted - it is the trace of who was there, so the
    list keeps it and the interface greys it out.
    """
    return conn.execute(
        "SELECT m.*, a.username, a.name, a.avatar, a.avatar_at,"
        "       i.name AS invited_by_name, i.username AS invited_by_username"
        " FROM event_members m"
        " JOIN accounts a ON a.id = m.account_id"
        " LEFT JOIN accounts i ON i.id = m.invited_by"
        " WHERE m.seq = ?"
        " ORDER BY CASE m.state WHEN 'active' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,"
        "          CASE m.role WHEN 'owner' THEN 0 ELSE 1 END, m.added_at",
        (int(seq),)).fetchall()


def seat_view(row):
    return {
        "accountId": row["account_id"],
        "username": row["username"],
        "name": row["name"],
        "avatar": row["avatar"] or "",
        "avatarAt": row["avatar_at"] or 0,
        "role": row["role"],
        "roleLabel": SEAT_LABEL.get(row["role"], row["role"]),
        "state": row["state"],
        "stateLabel": SEAT_STATE_LABEL.get(row["state"], row["state"]),
        "addedAt": row["added_at"],
        "stateAt": row["state_at"] or 0,
        "invitedBy": row["invited_by_name"] or row["invited_by_username"] or "",
    }


def invites_for(conn, account_id):
    """Invitations waiting on this account, newest first."""
    return conn.execute(
        "SELECT m.seq, m.role, m.added_at, e.title, e.token,"
        "       i.name AS inviter_name, i.username AS inviter_username"
        " FROM event_members m"
        " JOIN events e ON e.seq = m.seq"
        " LEFT JOIN accounts i ON i.id = m.invited_by"
        " WHERE m.account_id = ? AND m.state = 'pending' AND e.archived_at = 0"
        " ORDER BY m.added_at DESC", (int(account_id),)).fetchall()


def open_seats(conn, seq):
    """How many people are actually in the activity; the last one cannot leave."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM event_members"
        " WHERE seq=? AND state='active'", (int(seq),)).fetchone()
    return int(row["n"] or 0)


def disabled_modules(conn):
    """Module ids an administrator switched off; everything else is live."""
    return {r["module_id"] for r in
            conn.execute("SELECT module_id FROM module_state WHERE enabled=0")}


def set_module_enabled(conn, module_id, on, actor=""):
    conn.execute(
        "INSERT INTO module_state (module_id, enabled, updated_at, updated_by)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(module_id) DO UPDATE SET"
        "   enabled=excluded.enabled, updated_at=excluded.updated_at,"
        "   updated_by=excluded.updated_by",
        (module_id, 1 if on else 0, time.time(), actor))
    return bool(on)


def draft_get(conn, account_id, key):
    row = conn.execute(
        "SELECT payload, updated_at FROM drafts WHERE account_id=? AND key=?",
        (int(account_id), key)).fetchone()
    if row is None:
        return None
    return {"payload": row["payload"], "updatedAt": row["updated_at"]}


def draft_put(conn, account_id, key, payload):
    conn.execute(
        "INSERT INTO drafts (account_id, key, payload, updated_at) VALUES (?,?,?,?)"
        " ON CONFLICT(account_id, key) DO UPDATE SET"
        "   payload=excluded.payload, updated_at=excluded.updated_at",
        (int(account_id), key, payload, time.time()))


def draft_drop(conn, account_id, key):
    conn.execute("DELETE FROM drafts WHERE account_id=? AND key=?",
                 (int(account_id), key))


def owners_of(conn, seq):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM event_members"
        " WHERE seq=? AND state='active' AND role='owner'", (int(seq),)).fetchone()
    return int(row["n"] or 0)


def audit(conn, actor, action, target=None, detail=None):
    extra = getattr(_local, "via", "")
    if extra:
        # 同一个人可以既用面板也用令牌：审计里分开写，事后才分得清是谁的手
        detail = ("%s（%s）" % (detail, extra)) if detail else extra
    conn.execute(
        "INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
        (time.time(), actor, action, target, detail),
    )


def via(text=""):
    """Mark how this thread was authenticated ("令牌「笔记本」"); empty clears it."""
    _local.via = text


# --------------------------------------------------------------------------- #
# API 令牌
# --------------------------------------------------------------------------- #
TOKEN_PREFIX = "cae-"
TOKEN_SCOPES = ("read", "write", "admin")
TOKEN_SCOPE_LABEL = {"read": "读取", "write": "写入", "admin": "平台管理"}


def _token_hash(plain):
    import hashlib
    return hashlib.sha256(str(plain or "").encode("utf-8")).hexdigest()


def new_token():
    """(明文, 前四位, 末五位)。前缀让人一眼看出这串东西是什么，也方便以后换算法。"""
    secret = secrets.token_urlsafe(32)
    plain = TOKEN_PREFIX + secret
    return plain, secret[:4], secret[-5:]


def mask_token(head, tail):
    return "%s%s•••••%s" % (TOKEN_PREFIX, head or "????", tail or "?????")


def normalize_scopes(raw):
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",")]
    out = [s for s in TOKEN_SCOPES if s in (raw or [])]
    return out or ["read"]


def create_token(conn, account_id, name, scopes, days=0):
    """Server-generated token (used only where the browser has no WebCrypto)."""
    plain, head, tail = new_token()
    tid = create_token_hash(conn, account_id, name, scopes, days,
                            _token_hash(plain), head, tail)
    return plain, tid


def create_token_hash(conn, account_id, name, scopes, days, hash_hex, head, tail):
    """Store a token whose plaintext only ever existed in the user's browser."""
    now = time.time()
    conn.execute(
        "INSERT INTO api_tokens (account_id, name, head, tail, hash, scopes,"
        " created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (int(account_id), (name or "").strip()[:40] or "未命名",
         head, tail, hash_hex, ",".join(normalize_scopes(scopes)),
         now, now + days * 86400 if days else 0))
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def token_view(row):
    """What the panel may see: never the hash, never the plaintext."""
    expired = bool(row["expires_at"]) and row["expires_at"] < time.time()
    return {"id": row["id"],
            "name": row["name"],
            "masked": mask_token(row["head"], row["tail"]),
            "scopes": [s for s in (row["scopes"] or "").split(",") if s],
            "scopeLabels": [TOKEN_SCOPE_LABEL.get(s, s)
                            for s in (row["scopes"] or "").split(",") if s],
            "createdAt": row["created_at"],
            "lastUsedAt": row["last_used_at"],
            "expiresAt": row["expires_at"],
            "revokedAt": row["revoked_at"],
            "state": ("已吊销" if row["revoked_at"]
                      else "已过期" if expired else "可用")}


def tokens_of(conn, account_id):
    return [token_view(r) for r in conn.execute(
        "SELECT * FROM api_tokens WHERE account_id=? ORDER BY created_at DESC",
        (int(account_id),))]


def revoke_token(conn, account_id, token_id):
    row = conn.execute("SELECT * FROM api_tokens WHERE id=?",
                       (int(token_id or 0),)).fetchone()
    if row is None or row["account_id"] != int(account_id):
        raise LookupError("找不到这张令牌")
    if not row["revoked_at"]:
        conn.execute("UPDATE api_tokens SET revoked_at=? WHERE id=?",
                     (time.time(), row["id"]))
    return row


def token_account(conn, plain):
    """(账号行, 令牌行) for a presented token, or (None, None).

    明文不落库：现算 sha256 再逐位比较（compare_digest 不泄露"前几位对了"）。
    """
    import hmac
    text = str(plain or "").strip()
    if not text.startswith(TOKEN_PREFIX):
        return None, None
    digest = _token_hash(text)
    row = conn.execute("SELECT * FROM api_tokens WHERE hash=?",
                       (digest,)).fetchone()
    if row is None:
        # 索引查不到就等于没有这张；仍然走一次常量时间比较，不给计时留线索
        hmac.compare_digest(digest, "0" * 64)
        return None, None
    if not hmac.compare_digest(digest, row["hash"]):
        return None, None
    if row["revoked_at"] or (row["expires_at"] and row["expires_at"] < time.time()):
        return None, None
    acc = conn.execute("SELECT * FROM accounts WHERE id=?",
                       (row["account_id"],)).fetchone()
    if acc is None or acc["status"] != "active":
        return None, None
    return acc, row


def touch_token(conn, token_id):
    conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?",
                 (time.time(), int(token_id)))


# --------------------------------------------------------------------------- #
# 对话存档
# --------------------------------------------------------------------------- #
CHAT_QUOTA_DEFAULT = 512 * 1024      # 512 KB；不够的人向站长申请
CHAT_QUOTA_MAX = 64 * 1024 * 1024


def chat_quota(acc):
    try:
        want = int(acc["chat_quota"] or 0)
    except (TypeError, KeyError, IndexError):
        want = 0
    if want <= 0:
        return CHAT_QUOTA_DEFAULT
    return min(want, CHAT_QUOTA_MAX)


def chat_used(conn, account_id, skip_id=0):
    row = conn.execute(
        "SELECT COALESCE(SUM(size), 0) AS n FROM assistant_sessions"
        " WHERE account_id=? AND deleted_at=0 AND id<>?",
        (int(account_id), int(skip_id or 0))).fetchone()
    return int(row["n"] or 0)


def chat_session_view(row, with_messages=False):
    out = {"id": row["id"], "title": row["title"] or "未命名会话",
           "size": row["size"], "createdAt": row["created_at"],
           "updatedAt": row["updated_at"]}
    if with_messages:
        try:
            out["messages"] = json.loads(row["messages"] or "[]")
        except ValueError:
            out["messages"] = []
    return out


def chat_sessions(conn, account_id, limit=50):
    rows = conn.execute(
        "SELECT * FROM assistant_sessions WHERE account_id=? AND deleted_at=0"
        " ORDER BY updated_at DESC LIMIT ?", (int(account_id), int(limit))).fetchall()
    return [chat_session_view(r) for r in rows]


def chat_session(conn, account_id, sid):
    row = conn.execute(
        "SELECT * FROM assistant_sessions WHERE id=? AND account_id=? AND deleted_at=0",
        (int(sid or 0), int(account_id))).fetchone()
    return chat_session_view(row, with_messages=True) if row else None


def chat_save(conn, acc, sid, title, messages):
    """Create or update one session.  Raises ValueError when over quota."""
    blob = json.dumps(messages or [], ensure_ascii=False)
    size = len(blob.encode("utf-8"))
    quota = chat_quota(acc)
    used = chat_used(conn, acc["id"], skip_id=sid or 0)
    if used + size > quota:
        raise ValueError("对话存档已满（%d / %d 字节），删掉一些旧会话再保存"
                         % (used + size, quota))
    now = time.time()
    title = (title or "").strip()[:60]
    if sid:
        row = conn.execute(
            "SELECT * FROM assistant_sessions WHERE id=? AND account_id=? AND deleted_at=0",
            (int(sid), int(acc["id"]))).fetchone()
        if row is None:
            raise LookupError("找不到这段会话")
        conn.execute(
            "UPDATE assistant_sessions SET title=?, messages=?, size=?, updated_at=?"
            " WHERE id=?", (title or row["title"], blob, size, now, row["id"]))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO assistant_sessions (account_id, title, messages, size,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (int(acc["id"]), title or "未命名会话", blob, size, now, now))
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def chat_delete(conn, account_id, sid):
    row = conn.execute(
        "SELECT * FROM assistant_sessions WHERE id=? AND account_id=? AND deleted_at=0",
        (int(sid or 0), int(account_id))).fetchone()
    if row is None:
        raise LookupError("找不到这段会话")
    conn.execute("UPDATE assistant_sessions SET deleted_at=? WHERE id=?",
                 (time.time(), row["id"]))
    return row


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
        # 0 表示用默认配额；界面据此显示"默认 / 已单独调整"
        "chatQuota": int(row["chat_quota"] or 0) if "chat_quota" in row.keys() else 0,
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
               "/register": "panel_register.html", "/panel": "panel_panel.html",
               # 打印页：报名表 / 已报名单，只有一张纸，不带面板外壳
               "/print": "panel_print.html"}


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


JOIN_SEGMENT = "join"


def _join_index(parts):
    """The token index when this path is a 报名 page (``/join/<token>/...``)."""
    for i, seg in enumerate(parts):
        if seg == JOIN_SEGMENT and i + 1 < len(parts) and parts[i + 1]:
            return i + 1
    return None


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
    # 报名页是公开的，地址里带自己的 token，和活动 token 同一层但不共用
    idx = _join_index(parts)
    if idx is not None:
        return "join", "/" + "/".join(parts[idx + 1:]), parts[idx]
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


def collapse_mount(mount):
    """还原被拼重一次的挂载前缀：``…/panel`` 其实是 ``…``。

    面板的地址是 ``<mount>/panel``，而 ``classify`` 允许任意前缀，所以一份写成
    ``<mount>/panel/panel`` 的地址照样能打开页面——只是页面把自己认成了另一处挂载，
    于是接口、Cookie 都跟着错。这里把多出来的那一段认出来。
    """
    m = (mount or "").rstrip("/")
    tail = "/panel"
    if m.endswith(tail):
        return m[: -len(tail)]
    return m


def mount_prefix(raw_path, token):
    """Everything in front of the token segment: where the panel lives here.

    The app answers on whatever path a request arrives by, so the prefix is read
    off the request instead of being configured twice.
    """
    if not token:
        return ""
    head = (raw_path or "").split("/" + token)[0]
    return head.rstrip("/")


# --------------------------------------------------------------------------- #
# notices
# --------------------------------------------------------------------------- #
def notice(tx, level, title, body="", account_id=0, actor="", obj=""):
    """排队一条通知。account_id=0 表示发给所有人。"""
    if level not in LEVELS:
        level = "info"
    cur = tx.execute(
        "INSERT INTO notices (ts, level, title, body, audience, account_id, actor, object)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (time.time(), level, title[:120], (body or "")[:600],
         "one" if account_id else "all", int(account_id or 0), actor or "", obj or ""))
    return cur.lastrowid


def notices_for(conn, acc, limit=30):
    """这个人看得到的通知：发给所有人的，加上点名给他的。"""
    rows = conn.execute(
        "SELECT * FROM notices WHERE dismissed_at=0"
        " AND (account_id=0 OR account_id=?)"
        " ORDER BY ts DESC LIMIT ?", (acc["id"], limit)).fetchall()
    return [notice_view(r) for r in rows]


def notice_view(row):
    return {"id": row["id"], "ts": row["ts"], "level": row["level"],
            "levelLabel": LEVEL_LABEL.get(row["level"], row["level"]),
            "title": row["title"], "body": row["body"],
            "object": row["object"], "unread": not row["read_at"]}


def unread_count(conn, acc):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM notices WHERE dismissed_at=0 AND read_at=0"
        " AND (account_id=0 OR account_id=?)", (acc["id"],)).fetchone()["n"]


def mark_notices(conn, acc, ids=None):
    """标为已读；ids 为空表示全部已读。"""
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(
            "UPDATE notices SET read_at=? WHERE read_at=0 AND (account_id=0"
            " OR account_id=?) AND id IN (%s)" % marks,
            [time.time(), acc["id"]] + [int(i) for i in ids])
    else:
        conn.execute(
            "UPDATE notices SET read_at=? WHERE read_at=0 AND (account_id=0"
            " OR account_id=?)", (time.time(), acc["id"]))


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value, actor=""):
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
        " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, value or "", time.time(), actor or ""))
