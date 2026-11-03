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
import os
import sqlite3
import threading
import time

import model

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VISIBILITY = ("private", "public", "org")
VIS_LABEL = {"private": "私有", "public": "公开", "org": "组织公开"}
ENTRY_STATE = ("active", "left")
STATE_LABEL = {"active": "在职", "left": "已退出"}
SOURCE_LABEL = {"import": "导入", "manual": "手工", "signup": "报名", "event": "活动"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sid        TEXT    NOT NULL UNIQUE,
    name       TEXT    NOT NULL,
    py         TEXT    NOT NULL DEFAULT '',
    pyi        TEXT    NOT NULL DEFAULT '',
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
"""

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
        connect().executescript(SCHEMA)
        _initialized = True


# --------------------------------------------------------------------------- #
# people
# --------------------------------------------------------------------------- #
def _pinyin(name, py=None, pyi=None):
    return model.normalize_pinyin({"name": name, "py": py, "pyi": pyi})


def upsert_person(conn, sid, name, grp="", note="", py=None, pyi=None):
    """One row per 学号.  Returns (person_id, created)."""
    sid = (sid or "").strip()
    name = (name or "").strip()
    if not sid or not name:
        raise model.ApiError("学号和姓名都不能为空")
    now = time.time()
    py, pyi = _pinyin(name, py, pyi)
    row = conn.execute("SELECT id FROM people WHERE sid=?", (sid,)).fetchone()
    if row:
        conn.execute(
            "UPDATE people SET name=?, py=?, pyi=?, grp=?, updated_at=?"
            " WHERE id=? AND (name<>? OR grp<>? OR py<>?)",
            (name, py, pyi, grp or "", now, row["id"], name, grp or "", py),
        )
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO people (sid, name, py, pyi, grp, note, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (sid, name, py, pyi, grp or "", note or "", now, now),
    )
    return cur.lastrowid, True


def person(conn, person_id):
    return conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()


# --------------------------------------------------------------------------- #
# rosters
# --------------------------------------------------------------------------- #
def create_roster(conn, owner_id, name, note="", visibility="private"):
    name = (name or "").strip()
    if not name:
        raise model.ApiError("请填写名单名称")
    if len(name) > 60:
        raise model.ApiError("名单名称最长 60 个字")
    if visibility not in VISIBILITY:
        visibility = "private"
    now = time.time()
    cur = conn.execute(
        "INSERT INTO rosters (name, note, owner_id, visibility, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        (name, (note or "")[:200], owner_id, visibility, now, now),
    )
    return cur.lastrowid


def update_roster(conn, roster_id, **fields):
    sets, vals = [], []
    for key in ("name", "note", "visibility"):
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
        else:
            value = str(value)[:200]
        sets.append("%s=?" % key)
        vals.append(value)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(time.time())
    conn.execute("UPDATE rosters SET %s WHERE id=?" % ",".join(sets), vals + [roster_id])


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


def can_manage(acc, row):
    """Only the creator writes; admins get a read-only view of everybody's."""
    return bool(acc) and row is not None and row["owner_id"] == acc["id"]


def list_rosters(conn, acc, q="", scope="mine", offset=0, limit=50):
    where, args = [], []
    if scope == "mine":
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
        "rosters": [roster_view(r, counts.get(r["id"], 0), acc) for r in rows],
    }


def roster_view(row, members=0, acc=None):
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
        "mine": can_manage(acc, row),
        "editable": can_manage(acc, row),
    }


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
        "SELECT e.*, p.sid, p.name, p.grp FROM roster_entries e"
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
        "SELECT e.*, p.sid, p.name, p.grp FROM roster_entries e"
        " JOIN people p ON p.id = e.person_id WHERE " + clause +
        " ORDER BY p.sid LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    return {"total": int(total or 0), "offset": offset, "limit": limit,
            "entries": [entry_view(r) for r in rows]}


def entry_view(row):
    return {
        "personId": row["person_id"],
        "sid": row["sid"],
        "name": row["name"],
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
def import_text(conn, roster_id, text, source="import", tag=""):
    """Merge a CSV/TSV list into a roster.  People are matched by 学号.

    Returns counts the operator can check: new people, updated people, rows
    already on the list, rows that could not be read.
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

    header = model._detect_header(rows[0])
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
            conn, sid, name, cell("grp"), cell("note"), cell("py"), cell("pyi"))
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
    rows = [["学号", "姓名", "班级/院系", "分组", "备注", "来源", "状态", "加入时间"]]
    for r in conn.execute(
        "SELECT e.*, p.sid, p.name, p.grp FROM roster_entries e"
        " JOIN people p ON p.id = e.person_id WHERE " + where + " ORDER BY p.sid",
        (roster_id,),
    ):
        rows.append([
            r["sid"], r["name"], r["grp"], r["tag"], r["note"],
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
    return [{"id": r["id"], "sid": r["sid"], "name": r["name"], "grp": r["grp"]}
            for r in rows]
