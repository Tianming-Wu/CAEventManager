# -*- coding: utf-8 -*-
"""名单库：人员、名单、名单成员。

与活动并列的一层，放在自己的数据库文件里（``data/lists.sqlite3``）：报名页是
公开写入的，不该和账号数据共用一份文件；备份、清空、排查也都按文件走。

人员（people）
--------------
平台内一个人只有一行，学号是唯一键。名单（rosters）只圈人，条目（roster_entries）
上带这份名单自己的分组、备注与来源。这样去重天然成立，改一次姓名全平台一致。

看得见 ≠ 改得动
--------------
可见性三档（私有 / 公开 / 组织公开）只决定一条记录能不能被看见；能改的永远是创建者。
站长与超级管理员可以在总管理页看到全部名单（服务资源视角），但默认只读。
"""

import csv
import io
import json
import os
import secrets
import sqlite3
import threading
import time

import model

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VISIBILITY = ("private", "public", "org")
VIS_LABEL = {"private": "私有", "public": "公开", "org": "组织公开"}
ENTRY_STATE = ("active", "left")
STATE_LABEL = {"active": "在册", "left": "已退出"}
SOURCE_LABEL = {"import": "导入", "manual": "手工", "signup": "报名", "event": "活动"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sid        TEXT    NOT NULL UNIQUE,
    name       TEXT    NOT NULL,
    py         TEXT    NOT NULL DEFAULT '',
    pyi        TEXT    NOT NULL DEFAULT '',
    dept       TEXT    NOT NULL DEFAULT '',
    grp        TEXT    NOT NULL DEFAULT '',
    note       TEXT    NOT NULL DEFAULT '',
    created_at REAL    NOT NULL,
    updated_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS people_name ON people(name);
CREATE INDEX IF NOT EXISTS people_py ON people(py);

CREATE TABLE IF NOT EXISTS rosters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    owner_id   INTEGER NOT NULL,
    visibility TEXT    NOT NULL DEFAULT 'private',
    grant_mode TEXT    NOT NULL DEFAULT 'auto',      -- auto | confirm
    org_id     INTEGER NOT NULL DEFAULT 0,
    created_at REAL    NOT NULL,
    updated_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS roster_entries (
    roster_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    tag       TEXT    NOT NULL DEFAULT '',
    note      TEXT    NOT NULL DEFAULT '',
    state     TEXT    NOT NULL DEFAULT 'active',
    source    TEXT    NOT NULL DEFAULT 'manual',
    added_at  REAL    NOT NULL,
    PRIMARY KEY (roster_id, person_id)
);
CREATE INDEX IF NOT EXISTS roster_entries_person ON roster_entries(person_id);

-- 报名实例：一个公开链接 + 时间窗 + 往哪份名单里写
CREATE TABLE IF NOT EXISTS signups (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    token            TEXT    NOT NULL UNIQUE,
    title            TEXT    NOT NULL,
    note             TEXT    NOT NULL DEFAULT '',
    owner_id         INTEGER NOT NULL,
    roster_id        INTEGER NOT NULL,
    filter_roster_id INTEGER NOT NULL DEFAULT 0,   -- 0 = 不限制会员
    opens_at         REAL    NOT NULL DEFAULT 0,   -- 0 = 立即开始
    closes_at        REAL    NOT NULL DEFAULT 0,   -- 0 = 不设截止
    capacity         INTEGER NOT NULL DEFAULT 0,   -- 0 = 不限名额
    dedup            TEXT    NOT NULL DEFAULT 'skip',   -- 旧列，不再使用
    self_edit        TEXT    NOT NULL DEFAULT 'off',    -- 报名后能否自己改：off | on
    collect          TEXT    NOT NULL DEFAULT '["dept","grp"]',  -- 内置字段收哪些
    theme            TEXT    NOT NULL DEFAULT '{}',     -- 报名页外观
    fields           TEXT    NOT NULL DEFAULT '[]',     -- 自定义字段（JSON）
    state            TEXT    NOT NULL DEFAULT 'open',   -- open | closed
    visibility       TEXT    NOT NULL DEFAULT 'private',
    grant_mode       TEXT    NOT NULL DEFAULT 'auto',
    approve_mode     TEXT    NOT NULL DEFAULT 'owner',
    created_at       REAL    NOT NULL,
    updated_at       REAL    NOT NULL
);

-- 报名提交：一次一行，保留提交当时的原样
CREATE TABLE IF NOT EXISTS signup_entries (
    signup_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    sid       TEXT    NOT NULL,
    name      TEXT    NOT NULL,
    dept      TEXT    NOT NULL DEFAULT '',
    grp       TEXT    NOT NULL DEFAULT '',
    note      TEXT    NOT NULL DEFAULT '',
    answers   TEXT    NOT NULL DEFAULT '{}',
    ts        REAL    NOT NULL,
    ip        TEXT    NOT NULL DEFAULT '',
    state     TEXT    NOT NULL DEFAULT 'accepted',
    PRIMARY KEY (signup_id, person_id)
);

-- 谁能写一份名单/一个报名：拥有者之外的写入权都在这里，
-- 一条记录一个人，level=write 立即生效，level=pending 是等拥有者点头的申请。
CREATE TABLE IF NOT EXISTS grants (
    object_kind TEXT    NOT NULL,                    -- roster | signup
    object_id   INTEGER NOT NULL,
    account_id  INTEGER NOT NULL,
    level       TEXT    NOT NULL DEFAULT 'write',
    source      TEXT    NOT NULL DEFAULT 'ask',      -- ask | invite
    added_at    REAL    NOT NULL,
    PRIMARY KEY (object_kind, object_id, account_id)
);
"""

# 后加的列：init_db() 见到就补，老库不用手工迁移
LATE_COLUMNS = (
    ("people", "dept", "TEXT NOT NULL DEFAULT ''"),
    ("signups", "self_edit", "TEXT NOT NULL DEFAULT 'off'"),
    ("signups", "collect", "TEXT NOT NULL DEFAULT '[\"dept\",\"grp\"]'"),
    ("signups", "theme", "TEXT NOT NULL DEFAULT '{}'"),
    ("rosters", "grant_mode", "TEXT NOT NULL DEFAULT 'auto'"),
    ("rosters", "approve_mode", "TEXT NOT NULL DEFAULT 'owner'"),
)

GRANT_MODES = ("auto", "confirm")
SIGNUP_STATES = ("open", "disabled")
# 报名后能不能自己改信息：on 可以改，off 只能联系负责人
SELF_EDIT = ("off", "on")
# 公开页的内置字段：学号与姓名固定有，这两项可选
BUILTIN_FIELDS = ("dept", "grp")
BUILTIN_LABEL = {"dept": "院系", "grp": "班级"}
THEME_PRESETS = ("plain", "warm", "cool", "dark")
THEME_LABEL = {"plain": "素色", "warm": "暖色", "cool": "冷色", "dark": "暗色"}
# 自定义字段能存到哪里：人员属性、名单条目，或者只留在这次报名里
FIELD_TYPES = ("text", "textarea", "number", "select", "date")
FIELD_INTO = ("", "person.dept", "person.grp", "roster.tag", "roster.note")
FIELD_TYPE_LABEL = {"text": "单行文本", "textarea": "多行文本", "number": "数字",
                    "select": "单选", "date": "日期"}
FIELD_INTO_LABEL = {"": "只留在本次报名", "person.dept": "写入学号对应的院系",
                    "person.grp": "写入班级", "roster.tag": "写成名单里的分组",
                    "roster.note": "写成名单里的备注"}
STATUS_LABEL = {"disabled": "已禁用", "upcoming": "未开始", "open": "进行中",
                "full": "已满", "ended": "已结束"}
# 谁能批写入申请：只有拥有者，或拥有者让可写的人也来批
APPROVE_MODES = ("owner", "editors")
APPROVE_MODE_LABEL = {"owner": "只有我", "editors": "可写的人也可以"}
GRANT_MODE_LABEL = {"auto": "申请即生效", "confirm": "需要我确认"}
LEVEL_LABEL = {"owner": "拥有者", "write": "可写", "pending": "待确认",
               "revoked": "已移出", "read": "只读"}

_local = threading.local()
_write_lock = threading.Lock()
_initialized = False


def db_path():
    return os.path.join(
        os.environ.get("LOTTERY_DATA_DIR") or os.path.join(BASE_DIR, "data"),
        "lists.sqlite3",
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
    """One writer at a time, same shape as the other two databases."""

    def __enter__(self):
        _write_lock.acquire()
        self.conn = connect()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except Exception:
            _write_lock.release()
            raise
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            _write_lock.release()
        return False


def init_db():
    global _initialized
    with _write_lock:
        conn = connect()
        conn.executescript(SCHEMA)
        for table, column, decl in LATE_COLUMNS:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}
            if column not in cols:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
        _initialized = True


# --------------------------------------------------------------------------- #
# people
# --------------------------------------------------------------------------- #
def _pinyin(name, py=None, pyi=None):
    return model.normalize_pinyin({"name": name, "py": py, "pyi": pyi})


def upsert_person(conn, sid, name, grp="", note="", py=None, pyi=None, dept=""):
    """One row per 学号.  Returns (person_id, created).

    ``dept`` (院系) and ``grp`` (班级) stay separate columns: put together they
    have to be parsed apart again the moment anyone wants a per-department count.
    A field that is not supplied is left alone, so an import that only carries
    班级 never blanks an existing 院系.
    """
    sid = (sid or "").strip()
    name = (name or "").strip()
    if not sid or not name:
        raise model.ApiError("学号和姓名都不能为空")
    now = time.time()
    py, pyi = _pinyin(name, py, pyi)
    dept = (dept or "").strip()
    row = conn.execute("SELECT * FROM people WHERE sid=?", (sid,)).fetchone()
    if row:
        conn.execute(
            "UPDATE people SET name=?, py=?, pyi=?, dept=?, grp=?, updated_at=?"
            " WHERE id=?",
            (name, py, pyi, dept or row["dept"], grp or row["grp"], now, row["id"]),
        )
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO people (sid, name, py, pyi, dept, grp, note, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, name, py, pyi, dept, grp or "", note or "", now, now),
    )
    return cur.lastrowid, True


def person(conn, person_id):
    return conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()


# --------------------------------------------------------------------------- #
# rosters
# --------------------------------------------------------------------------- #
def create_roster(conn, owner_id, name, note="", visibility="private",
                  grant_mode="auto", approve_mode="owner"):
    name = (name or "").strip()
    if not name:
        raise model.ApiError("请填写名单名称")
    if len(name) > 60:
        raise model.ApiError("名单名称最长 60 个字")
    if visibility not in VISIBILITY:
        visibility = "private"
    if grant_mode not in GRANT_MODES:
        grant_mode = "auto"
    if approve_mode not in APPROVE_MODES:
        approve_mode = "owner"
    now = time.time()
    cur = conn.execute(
        "INSERT INTO rosters (name, note, owner_id, visibility, grant_mode,"
        " approve_mode, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (name, (note or "")[:200], owner_id, visibility, grant_mode,
         approve_mode, now, now),
    )
    return cur.lastrowid


def update_roster(conn, roster_id, **fields):
    current = roster(conn, roster_id)
    sets, vals = [], []
    for key in ("name", "note", "visibility", "grant_mode", "approve_mode"):
        if key not in fields or fields[key] is None:
            continue
        value = fields[key]
        if key == "name":
            value = str(value).strip()
            if not value:
                raise model.ApiError("请填写名单名称")
            if len(value) > 60:
                raise model.ApiError("名单名称最长 60 个字")
        elif key == "visibility":
            if value not in VISIBILITY:
                raise model.ApiError("可见性只能是私有 / 公开 / 组织公开")
        elif key == "grant_mode":
            if value not in GRANT_MODES:
                raise model.ApiError("写入权限只能是申请即生效或需要确认")
        elif key == "approve_mode":
            if value not in APPROVE_MODES:
                raise model.ApiError("审批人只能是拥有者或可写的人")
        else:
            value = str(value)[:200]
        if current is not None and key in current.keys() and current[key] == value:
            continue                      # 值没变就不写
        sets.append("%s=?" % key)
        vals.append(value)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(time.time())
    conn.execute("UPDATE rosters SET %s WHERE id=?" % ",".join(sets), vals + [roster_id])
    return True


def delete_roster(conn, roster_id):
    conn.execute("DELETE FROM roster_entries WHERE roster_id=?", (roster_id,))
    conn.execute("DELETE FROM rosters WHERE id=?", (roster_id,))


def roster(conn, roster_id):
    return conn.execute("SELECT * FROM rosters WHERE id=?", (roster_id,)).fetchone()


def roster_counts(conn, roster_ids=None):
    sql = ("SELECT roster_id, COUNT(*) AS n FROM roster_entries"
           " WHERE state='active'")
    args = []
    if roster_ids is not None:
        if not roster_ids:
            return {}
        sql += " AND roster_id IN (%s)" % ",".join("?" * len(roster_ids))
        args = list(roster_ids)
    sql += " GROUP BY roster_id"
    return {r["roster_id"]: r["n"] for r in conn.execute(sql, args)}


def visible_condition(acc, alias="r"):
    """(sql, args) for the rosters this account may open.

    The owner sees everything: a roster takes up server resources and has to be
    countable from the panel, but that is a *reading* right.  Writing stays with
    the creator (see ``can_manage``).
    """
    role = (acc["role"] if acc is not None else "") or ""
    if role in ("owner", "superadmin"):
        return "", []
    if acc is None:
        return "1=0", []
    return ("(%s.owner_id=? OR %s.visibility<>'private')" % (alias, alias),
            [acc["id"]])


def grant_level(conn, kind, object_id, account_id):
    """owner | write | pending | revoked | read for one account on one object."""
    row = conn.execute(
        "SELECT level FROM grants WHERE object_kind=? AND object_id=? AND account_id=?",
        (kind, object_id, account_id)).fetchone()
    return row["level"] if row else "read"


def grants_of(conn, kind, object_id):
    """Everybody with a row on this object, pending applications included."""
    rows = conn.execute(
        "SELECT * FROM grants WHERE object_kind=? AND object_id=? ORDER BY added_at",
        (kind, object_id)).fetchall()
    return [{"accountId": r["account_id"], "level": r["level"],
             "levelLabel": LEVEL_LABEL.get(r["level"], r["level"]),
             "source": r["source"], "addedAt": r["added_at"]} for r in rows]


def set_grant(conn, kind, object_id, account_id, level, source="ask"):
    conn.execute(
        "INSERT INTO grants (object_kind, object_id, account_id, level, source, added_at)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(object_kind, object_id, account_id) DO UPDATE SET"
        " level=excluded.level, source=excluded.source",
        (kind, object_id, account_id, level, source, time.time()))


def revoke_grant(conn, kind, object_id, account_id):
    """Taking write access back leaves a scar, not a hole.

    The row stays as ``revoked`` so the next application is not waved through by
    an ``auto`` roster: being removed on purpose has to mean something, otherwise
    the person just clicks 申请 again and is back inside.
    """
    set_grant(conn, kind, object_id, account_id, "revoked", "revoke")


def drop_grant(conn, kind, object_id, account_id):
    conn.execute(
        "DELETE FROM grants WHERE object_kind=? AND object_id=? AND account_id=?",
        (kind, object_id, account_id))


def apply_for_write(conn, kind, row, account_id):
    """A request to write: immediate on ``auto``, queued on ``confirm``.

    Whatever the mode, someone who was removed once always lands in the queue.
    """
    previous = grant_level(conn, kind, row["id"], account_id)
    if previous == "write":
        return "write"
    mode = row["grant_mode"] if "grant_mode" in row.keys() else "auto"
    level = "write" if (mode == "auto" and previous != "revoked") else "pending"
    set_grant(conn, kind, row["id"], account_id, level, "ask")
    return level


def grant_summary(conn, acc, kind, row):
    """What this account may do with one row, plus the editors worth showing."""
    if acc is None or row is None:
        return {"level": "read", "levelLabel": LEVEL_LABEL["read"],
                "editable": False, "owner": False, "canAsk": False,
                "canApprove": False, "askNote": "",
                "grantMode": "auto", "grantModeLabel": GRANT_MODE_LABEL["auto"],
                "approveMode": "owner",
                "approveModeLabel": APPROVE_MODE_LABEL["owner"]}
    owner = row["owner_id"] == acc["id"]
    level = "owner" if owner else grant_level(conn, kind, row["id"], acc["id"])
    mode = row["grant_mode"] if "grant_mode" in row.keys() else "auto"
    approve = row["approve_mode"] if "approve_mode" in row.keys() else "owner"
    visible = level != "read" or row_visible(acc, row)
    can_write = owner or level == "write"
    return {
        "level": level,
        "levelLabel": LEVEL_LABEL.get(level, level),
        "owner": owner,
        "editable": can_write,
        "canAsk": (not owner) and level not in ("write", "pending") and visible,
        # 申请由谁批：拥有者，或者拥有者点头让可写的人也来批
        "canApprove": owner or (approve == "editors" and level == "write"),
        "askNote": "你曾被移出这份名单，重新申请需拥有者确认"
                   if level == "revoked" else "",
        "grantMode": mode,
        "grantModeLabel": GRANT_MODE_LABEL.get(mode, mode),
        "approveMode": approve,
        "approveModeLabel": APPROVE_MODE_LABEL.get(approve, approve),
    }


def row_visible(acc, row):
    """Visibility alone: may this account open the object at all."""
    if acc is None or row is None:
        return False
    if acc["role"] in ("owner", "superadmin"):
        return True
    return row["owner_id"] == acc["id"] or row["visibility"] != "private"


def can_manage(acc, row):
    """Kept for the simple checks; grants are layered on in ``grant_summary``."""
    return bool(acc) and row is not None and row["owner_id"] == acc["id"]


def writable(conn, acc, kind, row):
    return bool(grant_summary(conn, acc, kind, row)["editable"])


def list_rosters(conn, acc, q="", scope="joined", offset=0, limit=50):
    where, args = [], []
    if scope == "joined":
        # 我参与的 = 我创建的 + 别人把写入权给我的
        where.append("(r.owner_id=? OR r.id IN (SELECT object_id FROM grants"
                     " WHERE object_kind='roster' AND account_id=? AND level='write'))")
        args += [acc["id"], acc["id"]]
    elif scope == "mine":
        where.append("r.owner_id=?")
        args.append(acc["id"])
    else:
        cond, extra = visible_condition(acc)
        if cond:
            where.append(cond)
            args += extra
    if q:
        where.append("(r.name LIKE ? OR r.note LIKE ?)")
        args += ["%" + q + "%"] * 2
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM rosters r" + clause, args).fetchone()["n"]
    rows = conn.execute(
        "SELECT r.* FROM rosters r" + clause +
        " ORDER BY r.updated_at DESC LIMIT ? OFFSET ?",
        args + [limit, offset]).fetchall()
    counts = roster_counts(conn, [r["id"] for r in rows])
    return {
        "total": int(total or 0),
        "offset": offset,
        "limit": limit,
        "rosters": [roster_view(r, counts.get(r["id"], 0), acc, conn) for r in rows],
    }


def roster_view(row, members=0, acc=None, conn=None):
    perms = grant_summary(conn, acc, "roster", row) if conn else {
        "level": "owner" if can_manage(acc, row) else "read",
        "levelLabel": "拥有者" if can_manage(acc, row) else "只读",
        "editable": can_manage(acc, row),
        "owner": can_manage(acc, row),
        "canAsk": False,
        "canApprove": can_manage(acc, row),
        "askNote": "",
        "grantMode": "auto",
        "grantModeLabel": GRANT_MODE_LABEL["auto"],
        "approveMode": "owner",
        "approveModeLabel": APPROVE_MODE_LABEL["owner"],
    }
    return {
        "id": row["id"],
        "name": row["name"],
        "note": row["note"],
        "ownerId": row["owner_id"],
        "visibility": row["visibility"],
        "visibilityLabel": VIS_LABEL.get(row["visibility"], row["visibility"]),
        "members": int(members or 0),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "mine": perms["owner"],
        "editable": perms["editable"],
        "owner": perms["owner"],
        "level": perms["level"],
        "levelLabel": perms["levelLabel"],
        "canAsk": perms["canAsk"],
        "canApprove": perms["canApprove"],
        "askNote": perms["askNote"],
        "grantMode": perms["grantMode"],
        "grantModeLabel": perms["grantModeLabel"],
        "approveMode": perms["approveMode"],
        "approveModeLabel": perms["approveModeLabel"],
    }


def pending_approvals(conn, acc):
    """写入申请里这个人能处理的那些：他拥有的对象，或可写且被授权审批的对象。"""
    if acc is None:
        return []
    rows = conn.execute(
        "SELECT g.object_kind, g.object_id, g.account_id, g.added_at,"
        "       r.name, r.owner_id, r.approve_mode"
        " FROM grants g JOIN rosters r ON r.id = g.object_id"
        " WHERE g.object_kind='roster' AND g.level='pending'"
        "   AND (r.owner_id = ?"
        "        OR (r.approve_mode='editors' AND EXISTS ("
        "            SELECT 1 FROM grants w WHERE w.object_kind='roster'"
        "              AND w.object_id=r.id AND w.account_id=? AND w.level='write')))"
        " ORDER BY g.added_at",
        (acc["id"], acc["id"])).fetchall()
    return [{"kind": r["object_kind"], "id": r["object_id"], "name": r["name"],
             "accountId": r["account_id"], "askedAt": r["added_at"]}
            for r in rows]


def my_asks(conn, acc):
    """我发出、还在等别人点头的申请。"""
    if acc is None:
        return []
    rows = conn.execute(
        "SELECT g.object_kind, g.object_id, g.added_at, r.name, r.owner_id"
        " FROM grants g JOIN rosters r ON r.id = g.object_id"
        " WHERE g.object_kind='roster' AND g.level='pending' AND g.account_id=?"
        " ORDER BY g.added_at DESC", (acc["id"],)).fetchall()
    return [{"kind": r["object_kind"], "id": r["object_id"], "name": r["name"],
             "ownerId": r["owner_id"], "askedAt": r["added_at"]} for r in rows]


# --------------------------------------------------------------------------- #
# roster entries
# --------------------------------------------------------------------------- #
def add_entry(conn, roster_id, person_id, tag="", note="", source="manual",
              state="active"):
    now = time.time()
    conn.execute(
        "INSERT INTO roster_entries"
        " (roster_id, person_id, tag, note, state, source, added_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(roster_id, person_id) DO UPDATE SET"
        " tag=excluded.tag, note=excluded.note, state=excluded.state",
        (roster_id, person_id, (tag or "")[:40], (note or "")[:200],
         state if state in ENTRY_STATE else "active", source, now),
    )
    touch(conn, roster_id)


def touch(conn, roster_id):
    conn.execute("UPDATE rosters SET updated_at=? WHERE id=?",
                 (time.time(), roster_id))


def drop_entry(conn, roster_id, person_id):
    conn.execute("DELETE FROM roster_entries WHERE roster_id=? AND person_id=?",
                 (roster_id, person_id))
    touch(conn, roster_id)


def entry(conn, roster_id, person_id):
    return conn.execute(
        "SELECT e.*, p.sid, p.name, p.dept, p.grp FROM roster_entries e"
        " JOIN people p ON p.id = e.person_id"
        " WHERE e.roster_id=? AND e.person_id=?", (roster_id, person_id)).fetchone()


def entries_page(conn, roster_id, q="", offset=0, limit=100, include_left=True):
    where = ["e.roster_id=?"]
    args = [roster_id]
    if not include_left:
        where.append("e.state='active'")
    if q:
        like = "%" + q.strip().lower() + "%"
        where.append("(lower(p.sid) LIKE ? OR p.name LIKE ? OR lower(p.py) LIKE ?"
                     " OR lower(p.pyi) LIKE ? OR p.grp LIKE ? OR e.tag LIKE ?)")
        args += [like] * 6
    clause = " AND ".join(where)
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM roster_entries e JOIN people p ON p.id=e.person_id"
        " WHERE " + clause, args).fetchone()["n"]
    rows = conn.execute(
        "SELECT e.*, p.sid, p.name, p.dept, p.grp FROM roster_entries e"
        " JOIN people p ON p.id = e.person_id WHERE " + clause +
        " ORDER BY p.sid LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return {"total": int(total or 0), "offset": offset, "limit": limit,
            "entries": [entry_view(r) for r in rows]}


def entry_view(row):
    return {
        "personId": row["person_id"],
        "sid": row["sid"],
        "name": row["name"],
        "dept": row["dept"],
        "grp": row["grp"],
        "tag": row["tag"],
        "note": row["note"],
        "state": row["state"],
        "stateLabel": STATE_LABEL.get(row["state"], row["state"]),
        "source": row["source"],
        "sourceLabel": SOURCE_LABEL.get(row["source"], row["source"]),
        "addedAt": row["added_at"],
    }


# --------------------------------------------------------------------------- #
# import / export
# --------------------------------------------------------------------------- #
# 名单比活动名单多一列院系，其余表头词沿用活动那份
ALIASES = dict(model.HEADER_ALIASES)
ALIASES["dept"] = ("院系", "学院", "系别", "系", "dept", "department", "faculty")


def _detect_header(row):
    mapping = {}
    for idx, cell in enumerate(row):
        key = (cell or "").strip().lower().replace(" ", "")
        for field, aliases in ALIASES.items():
            if key in aliases:
                mapping[field] = idx
                break
    return mapping if "sid" in mapping and "name" in mapping else None


def import_text(conn, roster_id, text, source="import", tag=""):
    """Merge a CSV/TSV list into a roster.  People are matched by 学号.

    Returns counts the operator can check: rows added to this roster, rows
    already on it, people created in the platform, rows that could not be read.
    """
    text = (text or "").strip("\ufeff").strip()
    if not text:
        raise model.ApiError("导入内容为空")
    sample = text.splitlines()[0]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise model.ApiError("没有解析到任何数据行")

    header = _detect_header(rows[0])
    start = 1
    if not header:
        header = {"sid": 0, "name": 1}
        if len(rows[0]) > 2:
            header["grp"] = 2
        start = 0

    added = unchanged = new_people = 0
    errors = []
    for lineno, row in enumerate(rows[start:], start=start + 1):
        def cell(field):
            idx = header.get(field)
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()

        sid, name = cell("sid"), cell("name")
        if not sid and not name:
            continue
        if not sid or not name:
            errors.append("第 %d 行缺少学号或姓名" % lineno)
            continue
        on_list = conn.execute(
            "SELECT 1 FROM people p JOIN roster_entries e ON e.person_id=p.id"
            " WHERE p.sid=? AND e.roster_id=?", (sid, roster_id)).fetchone()
        person_id, created = upsert_person(
            conn, sid, name, cell("grp"), cell("note"), cell("py"), cell("pyi"),
            cell("dept"))
        if created:
            new_people += 1
        if on_list:
            unchanged += 1
            conn.execute(
                "UPDATE roster_entries SET tag=COALESCE(NULLIF(?,''),tag),"
                " note=COALESCE(NULLIF(?,''),note), state='active'"
                " WHERE roster_id=? AND person_id=?",
                (cell("tag") or tag, cell("note"), roster_id, person_id))
        else:
            add_entry(conn, roster_id, person_id, cell("tag") or tag, cell("note"),
                      source)
            added += 1
    touch(conn, roster_id)
    return {"added": added, "unchanged": unchanged, "newPeople": new_people,
            "errors": errors[:10], "failed": len(errors)}


def export_rows(conn, roster_id, include_left=False):
    """Header + rows, ready for csv or a spreadsheet."""
    where = "e.roster_id=?"
    if not include_left:
        where += " AND e.state='active'"
    rows = [["学号", "姓名", "院系", "班级", "分组", "备注", "来源", "状态", "加入时间"]]
    for r in conn.execute(
        "SELECT e.*, p.sid, p.name, p.dept, p.grp FROM roster_entries e"
        " JOIN people p ON p.id = e.person_id WHERE " + where + " ORDER BY p.sid",
        (roster_id,),
    ):
        rows.append([
            r["sid"], r["name"], r["dept"], r["grp"], r["tag"], r["note"],
            SOURCE_LABEL.get(r["source"], r["source"]),
            STATE_LABEL.get(r["state"], r["state"]),
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["added_at"])),
        ])
    return rows


def search_people(conn, q, limit=20):
    q = (q or "").strip()
    if not q:
        return []
    like = "%" + q.lower() + "%"
    rows = conn.execute(
        "SELECT * FROM people WHERE lower(sid) LIKE ? OR name LIKE ?"
        " OR lower(py) LIKE ? OR lower(pyi) LIKE ? ORDER BY sid LIMIT ?",
        (like, like, like, like, limit)).fetchall()
    return [{"id": r["id"], "sid": r["sid"], "name": r["name"],
             "dept": r["dept"], "grp": r["grp"]} for r in rows]


# --------------------------------------------------------------------------- #
# signups
# --------------------------------------------------------------------------- #
def normalize_fields(raw):
    """自定义字段的定义；学号与姓名永远在，不进这份清单。"""
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:40]
        if not label:
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            key = "f%d" % (len(out) + 1)
        key = "".join(ch for ch in key if ch.isalnum() or ch in "_-")[:24] or "f%d" % (len(out) + 1)
        kind = item.get("type") if item.get("type") in FIELD_TYPES else "text"
        into = item.get("into") if item.get("into") in FIELD_INTO else ""
        options = [str(o).strip()[:40] for o in (item.get("options") or []) if str(o).strip()]
        if kind == "select" and not options:
            kind = "text"
        out.append({"key": key, "label": label, "type": kind,
                    "required": bool(item.get("required")),
                    "options": options, "into": into,
                    "typeLabel": FIELD_TYPE_LABEL[kind],
                    "intoLabel": FIELD_INTO_LABEL[into]})
    return out[:12]


def new_token():
    return secrets.token_urlsafe(24)


def _fields_of(row):
    try:
        return normalize_fields(json.loads(row["fields"] or "[]"))
    except (ValueError, TypeError):
        return []


def normalize_collect(raw):
    """公开页收哪几个内置字段；顺序固定，未知值丢掉。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (ValueError, TypeError):
            raw = []
    picked = [f for f in (raw or []) if f in BUILTIN_FIELDS]
    return [f for f in BUILTIN_FIELDS if f in picked]


def normalize_theme(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (ValueError, TypeError):
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    preset = raw.get("preset") if raw.get("preset") in THEME_PRESETS else "plain"
    accent = str(raw.get("accent") or "").strip()
    if accent and not (accent.startswith("#") and len(accent) in (4, 7)):
        accent = ""
    return {"preset": preset, "accent": accent}


def _collect_of(row):
    try:
        return normalize_collect(json.loads(row["collect"] or "[]"))
    except (ValueError, TypeError):
        return list(BUILTIN_FIELDS)


def _theme_of(row):
    try:
        return normalize_theme(json.loads(row["theme"] or "{}"))
    except (ValueError, TypeError):
        return normalize_theme({})


def check_window(opens_at, closes_at):
    if opens_at and closes_at and opens_at >= closes_at:
        raise model.ApiError("开始时间必须早于结束时间")


def create_signup(conn, owner_id, data):
    title = (data.get("title") or "").strip()
    if not title:
        raise model.ApiError("请填写报名名称")
    if len(title) > 60:
        raise model.ApiError("报名名称最长 60 个字")
    roster_id = int(data.get("roster_id") or 0)
    if not roster_id or roster(conn, roster_id) is None:
        raise model.ApiError("请选择报名写入的名单")
    opens_at = float(data.get("opens_at") or 0)
    closes_at = float(data.get("closes_at") or 0)
    check_window(opens_at, closes_at)
    now = time.time()
    cur = conn.execute(
        "INSERT INTO signups (token, title, note, owner_id, roster_id,"
        " filter_roster_id, opens_at, closes_at, capacity, self_edit, collect,"
        " theme, fields, state, visibility, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_token(), title, (data.get("note") or "").strip()[:300], owner_id,
         roster_id, int(data.get("filter_roster_id") or 0),
         opens_at, closes_at, max(0, int(data.get("capacity") or 0)),
         data.get("self_edit") if data.get("self_edit") in SELF_EDIT else "off",
         json.dumps(normalize_collect(data.get("collect") or list(BUILTIN_FIELDS))),
         json.dumps(normalize_theme(data.get("theme")), ensure_ascii=False),
         json.dumps(normalize_fields(data.get("fields")), ensure_ascii=False),
         "open", data.get("visibility") if data.get("visibility") in VISIBILITY
         else "private", now, now),
    )
    return cur.lastrowid


def update_signup(conn, signup_id, data):
    row = signup(conn, signup_id)
    if row is None:
        raise model.ApiError("报名不存在", 404)
    sets, vals = [], []

    def put(key, value):
        if key in row.keys() and row[key] == value:
            return                        # 值没变就不写
        sets.append("%s=?" % key)
        vals.append(value)

    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            raise model.ApiError("请填写报名名称")
        if len(title) > 60:
            raise model.ApiError("报名名称最长 60 个字")
        put("title", title)
    if "note" in data:
        put("note", (data.get("note") or "").strip()[:300])
    if "opens_at" in data or "closes_at" in data:
        opens_at = float(data.get("opens_at", row["opens_at"]) or 0)
        closes_at = float(data.get("closes_at", row["closes_at"]) or 0)
        check_window(opens_at, closes_at)
        put("opens_at", opens_at)
        put("closes_at", closes_at)
    if "capacity" in data:
        put("capacity", max(0, int(data.get("capacity") or 0)))
    if "self_edit" in data:
        put("self_edit", data.get("self_edit") if data.get("self_edit") in SELF_EDIT
            else "off")
    if "collect" in data:
        put("collect", json.dumps(normalize_collect(data.get("collect"))))
    if "theme" in data:
        put("theme", json.dumps(normalize_theme(data.get("theme")), ensure_ascii=False))
    if "state" in data:
        value = data.get("state")
        # 老值 closed 与新值 disabled 同义：都是"人关掉了"
        if value == "closed":
            value = "disabled"
        put("state", value if value in SIGNUP_STATES else "open")
    if "visibility" in data and data.get("visibility") in VISIBILITY:
        put("visibility", data.get("visibility"))
    if "filter_roster_id" in data:
        put("filter_roster_id", int(data.get("filter_roster_id") or 0))
    if "fields" in data:
        put("fields", json.dumps(normalize_fields(data.get("fields")),
                                 ensure_ascii=False))
    if not sets:
        return False
    put("updated_at", time.time())
    conn.execute("UPDATE signups SET %s WHERE id=?" % ",".join(sets),
                 vals + [signup_id])
    return True


def signup(conn, signup_id):
    return conn.execute("SELECT * FROM signups WHERE id=?", (signup_id,)).fetchone()


def signup_by_token(conn, token):
    return conn.execute("SELECT * FROM signups WHERE token=?", (token,)).fetchone()


def delete_signup(conn, signup_id):
    conn.execute("DELETE FROM signup_entries WHERE signup_id=?", (signup_id,))
    conn.execute("DELETE FROM signups WHERE id=?", (signup_id,))


def signup_counts(conn, ids=None):
    sql = "SELECT signup_id, COUNT(*) AS n FROM signup_entries"
    args = []
    if ids is not None:
        if not ids:
            return {}
        sql += " WHERE signup_id IN (%s)" % ",".join("?" * len(ids))
        args = list(ids)
    sql += " GROUP BY signup_id"
    return {r["signup_id"]: r["n"] for r in conn.execute(sql, args)}


def signup_status(row, count, now=None):
    """开不开放由时间与名额自动判定；只有"禁用"是人说了算的。"""
    now = time.time() if now is None else now
    if row["state"] in ("closed", "disabled"):
        return "disabled"
    if row["opens_at"] and now < row["opens_at"]:
        return "upcoming"
    if row["closes_at"] and now > row["closes_at"]:
        return "ended"
    if row["capacity"] and count >= row["capacity"]:
        return "full"
    return "open"


def signup_view(conn, row, count=0, acc=None):
    status = signup_status(row, count)
    roster_row = roster(conn, row["roster_id"])
    filter_row = roster(conn, row["filter_roster_id"]) if row["filter_roster_id"] else None
    owner = bool(acc) and row["owner_id"] == acc["id"]
    return {
        "id": row["id"],
        "token": row["token"],
        "title": row["title"],
        "note": row["note"],
        "ownerId": row["owner_id"],
        "rosterId": row["roster_id"],
        "rosterName": roster_row["name"] if roster_row else "",
        "filterRosterId": row["filter_roster_id"],
        "filterRosterName": filter_row["name"] if filter_row else "",
        "opensAt": row["opens_at"],
        "closesAt": row["closes_at"],
        "capacity": row["capacity"],
        "selfEdit": row["self_edit"] if "self_edit" in row.keys() else "off",
        "collect": _collect_of(row),
        "theme": _theme_of(row),
        "entries": int(count or 0),
        "fields": _fields_of(row),
        "state": row["state"],
        "status": status,
        "statusLabel": STATUS_LABEL.get(status, status),
        "visibility": row["visibility"],
        "visibilityLabel": VIS_LABEL.get(row["visibility"], row["visibility"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "owner": owner,
        "editable": owner,
    }


def list_signups(conn, acc, q="", scope="joined", offset=0, limit=30):
    where, args = [], []
    if scope == "mine":
        where.append("s.owner_id=?")
        args.append(acc["id"])
    elif scope == "joined":
        where.append("s.owner_id=?")
        args.append(acc["id"])
    else:
        cond, extra = visible_condition(acc, "s")
        if cond:
            where.append(cond)
            args += extra
    if q:
        where.append("(s.title LIKE ? OR s.note LIKE ?)")
        args += ["%" + q + "%"] * 2
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM signups s" + clause, args).fetchone()["n"]
    rows = conn.execute(
        "SELECT s.* FROM signups s" + clause +
        " ORDER BY s.updated_at DESC LIMIT ? OFFSET ?",
        args + [limit, offset]).fetchall()
    counts = signup_counts(conn, [r["id"] for r in rows])
    return {"total": int(total or 0), "offset": offset, "limit": limit,
            "signups": [signup_view(conn, r, counts.get(r["id"], 0), acc)
                        for r in rows]}


def signup_entries_page(conn, signup_id, q="", offset=0, limit=100):
    where, args = ["signup_id=?"], [signup_id]
    if q:
        like = "%" + q.strip().lower() + "%"
        where.append("(lower(sid) LIKE ? OR name LIKE ?)")
        args += [like, like]
    clause = " AND ".join(where)
    total = conn.execute("SELECT COUNT(*) AS n FROM signup_entries WHERE " + clause,
                         args).fetchone()["n"]
    rows = conn.execute(
        "SELECT * FROM signup_entries WHERE " + clause +
        " ORDER BY ts LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return {"total": int(total or 0), "offset": offset, "limit": limit,
            "entries": [signup_entry_view(r) for r in rows]}


def signup_entry_view(row):
    try:
        answers = json.loads(row["answers"] or "{}")
    except (ValueError, TypeError):
        answers = {}
    return {
        "personId": row["person_id"],
        "sid": row["sid"],
        "name": row["name"],
        "dept": row["dept"],
        "grp": row["grp"],
        "note": row["note"],
        "answers": answers,
        "ts": row["ts"],
        "state": row["state"],
    }


def _norm(text):
    return "".join(str(text or "").split()).lower()


def _coerce_field(field, raw):
    value = raw
    if field["type"] == "number":
        if value in (None, ""):
            return ""
        try:
            return str(float(value))
        except (TypeError, ValueError):
            raise model.ApiError("「%s」需要填数字" % field["label"])
    if field["type"] == "select":
        if value in (None, ""):
            return ""
        value = str(value).strip()
        if value not in field["options"]:
            raise model.ApiError("「%s」的选项不在可选范围内" % field["label"])
        return value
    return str(value or "").strip()[:500]


def submit_signup(conn, row, form, ip=""):
    """公开页的一次提交：校验范围与名额，然后把这个人写进名单。

    学号与姓名永远是必填项；限制会员时，两者都要和限制名单里的记录对上。
    """
    now = time.time()
    sid = (form.get("sid") or "").strip()
    name = (form.get("name") or "").strip()
    if not sid or not name:
        raise model.ApiError("学号和姓名均为必填")
    if len(sid) > 32 or len(name) > 32:
        raise model.ApiError("学号或姓名过长")
    count = signup_counts(conn, [row["id"]]).get(row["id"], 0)
    status = signup_status(row, count, now)
    if status == "disabled":
        raise model.ApiError("本次报名已关闭", 409)
    if status == "upcoming":
        raise model.ApiError("本次报名尚未开始", 409)
    if status == "ended":
        raise model.ApiError("本次报名已结束", 409)
    if status == "full":
        raise model.ApiError("名额已满", 409)

    member = None
    if row["filter_roster_id"]:
        member = conn.execute(
            "SELECT p.* FROM people p JOIN roster_entries e ON e.person_id = p.id"
            " WHERE e.roster_id=? AND e.state='active' AND p.sid=?",
            (row["filter_roster_id"], sid)).fetchone()
        if member is None:
            raise model.ApiError("你不在本次报名的范围名单内", 403)
        if _norm(member["name"]) != _norm(name):
            raise model.ApiError("姓名与名单中的记录不一致", 403)

    fields = _fields_of(row)
    answers = {}
    for field in fields:
        value = _coerce_field(field, (form.get("answers") or {}).get(field["key"]))
        if field["required"] and not value:
            raise model.ApiError("「%s」为必填项" % field["label"])
        answers[field["key"]] = value

    def into(target):
        for field in fields:
            if field["into"] == target and answers.get(field["key"]):
                return answers[field["key"]]
        return ""

    # 字段上标了去向就以字段为准：那是组织者明确指定的，公开页上的通用输入框只是兜底
    dept = into("person.dept") or (form.get("dept") or "").strip()
    grp = into("person.grp") or (form.get("grp") or "").strip()
    person_id, created = upsert_person(conn, sid, name, grp, "", None, None, dept)

    existing = conn.execute(
        "SELECT * FROM signup_entries WHERE signup_id=? AND person_id=?",
        (row["id"], person_id)).fetchone()
    payload = json.dumps(answers, ensure_ascii=False)
    if existing is not None:
        was = {"name": existing["name"], "dept": existing["dept"],
               "grp": existing["grp"],
               "answers": existing["answers"] or "{}"}
        try:
            was_answers = json.loads(was["answers"])
        except (ValueError, TypeError):
            was_answers = {}
        now_state = {"name": name, "dept": dept, "grp": grp, "answers": answers}
        same = (now_state["name"] == was["name"]
                and now_state["dept"] == was["dept"]
                and now_state["grp"] == was["grp"]
                and was_answers == answers)
        if same:
            return {"ok": True, "state": "same", "ts": existing["ts"],
                    "name": existing["name"], "count": count}
        # 信息不一致：能不能自己改，由这条报名自己的设置说了算
        if row["self_edit"] != "on":
            raise model.ApiError(
                "您已经报过名，但信息不一致。如果之前的信息有误，请联系负责人", 409)
        if not form.get("update"):
            return {"ok": True, "state": "differs", "ts": existing["ts"],
                    "count": count,
                    "current": {"name": was["name"], "dept": was["dept"],
                                "grp": was["grp"], "answers": was_answers}}
        conn.execute(
            "UPDATE signup_entries SET name=?, dept=?, grp=?, answers=?, ip=?"
            " WHERE signup_id=? AND person_id=?",
            (name, dept, grp, payload, ip, row["id"], person_id))
        state = "updated"
    else:
        conn.execute(
            "INSERT INTO signup_entries (signup_id, person_id, sid, name, dept,"
            " grp, note, answers, ts, ip, state) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], person_id, sid, name, dept, grp, "", payload, now, ip,
             "accepted"))
    add_entry(conn, row["roster_id"], person_id, into("roster.tag"),
              into("roster.note"), "signup")
    conn.execute("UPDATE signups SET updated_at=? WHERE id=?", (now, row["id"]))
    return {"ok": True, "state": state if existing is not None else "new",
            "ts": existing["ts"] if existing is not None else now,
            "count": signup_counts(conn, [row["id"]]).get(row["id"], 0)}


def signup_export_rows(conn, signup_id):
    row = signup(conn, signup_id)
    fields = _fields_of(row) if row else []
    head = ["学号", "姓名", "院系", "班级", "提交时间"] + [
        f["label"] for f in fields] + ["备注"]
    rows = [head]
    for r in conn.execute(
            "SELECT * FROM signup_entries WHERE signup_id=? ORDER BY ts", (signup_id,)):
        view = signup_entry_view(r)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"]))
        rows.append([view["sid"], view["name"], view["dept"], view["grp"], stamp]
                    + [str(view["answers"].get(f["key"], "")) for f in fields]
                    + [view["note"]])
    return rows
