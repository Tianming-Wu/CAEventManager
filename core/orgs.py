# -*- coding: utf-8 -*-
"""组织：跨账号的分组，资源与可见性以后挂在它上面。

这一层只管"组织本身"：建、改、归档、恢复、彻底删除，以及成员与组织内身份。
平台的账号权限（删账号、重置密码）不经过这里——组织负责人最多加人、踢人、改身份。

归档不是删：成员关系折叠成一列紧凑 JSON 存回组织行，明细行删掉，恢复时再摊开。
彻底删除只在已归档的组织上出现，由站长加提权执行。
"""

import json
import os
import time

import panel

ROLES = ("head", "admin", "member")
ROLE_LABEL = {"head": "负责人", "admin": "管理", "member": "成员"}
RANK = {"head": 3, "admin": 2, "member": 1}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    created_by  TEXT NOT NULL DEFAULT '',
    archived_at REAL NOT NULL DEFAULT 0,
    -- 归档之后成员关系折叠在这里：只留账号编号、身份与加入时间
    folded      TEXT NOT NULL DEFAULT '',
    -- 外观：cover 存外链地址，或我们自己写到 data/org_covers 下的文件名
    cover       TEXT NOT NULL DEFAULT '',
    cover_at    REAL NOT NULL DEFAULT 0,
    cover_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS org_members (
    org_id     TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    role       TEXT NOT NULL,
    joined_at  REAL NOT NULL,
    PRIMARY KEY (org_id, account_id)
);
CREATE INDEX IF NOT EXISTS org_members_account ON org_members(account_id);
"""


LATE_COLUMNS = (
    ("orgs", "cover", "TEXT NOT NULL DEFAULT ''"),
    ("orgs", "cover_at", "REAL NOT NULL DEFAULT 0"),
    ("orgs", "cover_bytes", "INTEGER NOT NULL DEFAULT 0"),
)


def ensure(conn):
    conn.executescript(SCHEMA)
    # 老库里没有外观这几列：建表语句只管新库，这里补上
    for table, column, decl in LATE_COLUMNS:
        have = {r["name"] for r in conn.execute(
            "PRAGMA table_info(%s)" % table)}       # sql-ok：表名进不了占位符
        if column not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s"
                         % (table, column, decl))   # sql-ok：同上
    conn.commit()


# --------------------------------------------------------------------------- #
# 外观：一张图，可以是外链，也可以是我们替他存下来的
# --------------------------------------------------------------------------- #
COVER_MAX = 2 * 1024 * 1024          # 单张 2 MB 封顶
COVER_DIR = "org_covers"


def cover_dir():
    path = os.path.join(os.path.dirname(panel.db_path()), COVER_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def cover_path(name):
    """只有我们自己写下的文件名才会走到文件系统上。"""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    return os.path.join(cover_dir(), name)


def clean_cover_url(value):
    """外链只收 http(s)，长度也要有个限度。"""
    url = (value or "").strip()
    if not url or len(url) > 500:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url


def save_cover(org_id, data):
    """存下这张图，返回 (文件名, 字节数)。"""
    found = panel.sniff_image(data)
    if found is None:
        raise ValueError("unsupported image")
    if len(data) > COVER_MAX:
        raise ValueError("too large")
    drop_cover(org_id)
    name = "%s.%s" % (org_id, found[0])
    path = cover_path(name)
    with open(path, "wb") as fh:
        fh.write(data)
    return name, len(data)


def drop_cover(org_id):
    for ext in ("png", "jpg", "webp"):
        stale = cover_path("%s.%s" % (org_id, ext))
        if stale and os.path.exists(stale):
            os.unlink(stale)


def cover_view(row):
    """给界面用的两件东西：图片地址怎么来、站内那份有多大。"""
    raw = (row["cover"] or "") if "cover" in row.keys() else ""
    return {"cover": raw,
            "coverAt": float(row["cover_at"] or 0) if "cover_at" in row.keys() else 0,
            "coverBytes": int(row["cover_bytes"] or 0) if "cover_bytes" in row.keys() else 0}


# --------------------------------------------------------------------------- #
# 成员关系的折叠与摊开
# --------------------------------------------------------------------------- #
def _fold(conn, org_id):
    # 名字一起折进去：归档之后不再回查账号，但那几行还得认得出来是谁
    rows = conn.execute(
        "SELECT m.account_id, m.role, m.joined_at, a.name FROM org_members m"
        " LEFT JOIN accounts a ON a.id=m.account_id"
        " WHERE m.org_id=? ORDER BY m.joined_at", (org_id,)).fetchall()
    return json.dumps([{"a": r["account_id"], "n": r["name"] or "",
                        "r": r["role"], "t": r["joined_at"]}
                       for r in rows], ensure_ascii=False)


def _unfold(conn, org_id, folded):
    conn.execute("DELETE FROM org_members WHERE org_id=?", (org_id,))
    try:
        items = json.loads(folded or "[]")
    except ValueError:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("r") if item.get("r") in ROLES else "member"
        conn.execute(
            "INSERT OR IGNORE INTO org_members (org_id, account_id, role, joined_at)"
            " VALUES (?,?,?,?)",
            (org_id, int(item.get("a") or 0), role, float(item.get("t") or 0)))


def _members(conn, org_id):
    rows = conn.execute(
        "SELECT m.account_id, m.role, m.joined_at, a.uid, a.name, a.username,"
        " a.avatar, a.avatar_at"
        " FROM org_members m LEFT JOIN accounts a ON a.id=m.account_id"
        " WHERE m.org_id=? ORDER BY CASE m.role WHEN 'head' THEN 0 WHEN 'admin' THEN 1"
        " ELSE 2 END, a.name COLLATE NOCASE, m.joined_at", (org_id,)).fetchall()
    return [{"accountId": r["account_id"], "uid": r["uid"], "name": r["name"] or "",
             "avatar": r["avatar"] or "", "avatarAt": float(r["avatar_at"] or 0),
             "role": r["role"], "roleLabel": ROLE_LABEL[r["role"]],
             "joinedAt": r["joined_at"]} for r in rows]


def archived_members(org):
    """归档组织里那些成员：只读快照，账号名字不再回查。"""
    try:
        items = json.loads(org["folded"] or "[]")
    except ValueError:
        items = []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("r") if item.get("r") in ROLES else "member"
        out.append({"accountId": int(item.get("a") or 0),
                    "name": str(item.get("n") or ""), "avatar": "", "avatarAt": 0,
                    "role": role, "roleLabel": ROLE_LABEL[role],
                    "joinedAt": float(item.get("t") or 0)})
    out.sort(key=lambda m: (-RANK[m["role"]], m["name"], m["joinedAt"]))
    return out


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
ORG_ID_RE = None


def id_problem(text):
    import re
    global ORG_ID_RE
    if ORG_ID_RE is None:
        ORG_ID_RE = re.compile(r"^[a-z0-9_-]{3,24}$")
    return "" if ORG_ID_RE.match(str(text or "")) else "组织标识用 3～24 位小写字母、数字、- 或 _"


def id_taken(conn, text):
    row = conn.execute("SELECT 1 FROM orgs WHERE id=?", (str(text or ""),)).fetchone()
    return row is not None


# --------------------------------------------------------------------------- #
# 建、改、归档、恢复、删
# --------------------------------------------------------------------------- #
def create(tx, org_id, name, note="", heads=(), members=(), admin_ids=(),
           actor=""):
    """建组织。``heads`` 至少要有一个，调用方先校验。"""
    now = time.time()
    tx.execute(
        "INSERT INTO orgs (id, name, note, created_at, created_by) VALUES (?,?,?,?,?)",
        (org_id, name, note or "", now, actor or ""))
    for account_id, role in ([(a, "head") for a in heads]
                             + [(a, "admin") for a in admin_ids]
                             + [(a, "member") for a in members]):
        tx.execute(
            "INSERT OR IGNORE INTO org_members (org_id, account_id, role, joined_at)"
            " VALUES (?,?,?,?)", (org_id, int(account_id), role, now))
    panel.audit(tx, actor or "system", "org_create", org_id,
                panel.audit_note(name=name, heads=len(heads),
                                 admins=len(admin_ids), members=len(members)))
    return org_id


def update(tx, org_id, name=None, note=None, actor=""):
    sets, args = [], []
    if name is not None:
        sets.append("name=?")
        args.append(name)
    if note is not None:
        sets.append("note=?")
        args.append(note)
    if not sets:
        return
    tx.execute("UPDATE orgs SET " + ", ".join(sets) + " WHERE id=?",   # sql-ok 只拼列名
               args + [org_id])
    panel.audit(tx, actor or "system", "org_update", org_id,
                panel.audit_note(name=name, note=note))


def archive(tx, org_id, actor=""):
    """归档：成员关系折进一列，明细行删掉；组织本身留着，可恢复。"""
    folded = _fold(tx, org_id)
    count = tx.execute("SELECT COUNT(*) AS n FROM org_members WHERE org_id=?",
                       (org_id,)).fetchone()["n"]
    tx.execute("DELETE FROM org_members WHERE org_id=?", (org_id,))
    tx.execute("UPDATE orgs SET archived_at=?, folded=? WHERE id=?",
               (time.time(), folded, org_id))
    panel.audit(tx, actor or "system", "org_archive", org_id,
                panel.audit_note(members=int(count or 0)))


def restore(tx, org_id, actor=""):
    row = tx.execute("SELECT folded FROM orgs WHERE id=?", (org_id,)).fetchone()
    _unfold(tx, org_id, row["folded"] if row else "")
    tx.execute("UPDATE orgs SET archived_at=0, folded='' WHERE id=?", (org_id,))
    panel.audit(tx, actor or "system", "org_restore", org_id)


def drop(tx, org_id, actor=""):
    """彻底删除：只在已归档的组织上调用（调用方校验状态）。"""
    count = tx.execute("SELECT COUNT(*) AS n FROM org_members WHERE org_id=?",
                       (org_id,)).fetchone()["n"]
    tx.execute("DELETE FROM org_members WHERE org_id=?", (org_id,))
    tx.execute("DELETE FROM orgs WHERE id=?", (org_id,))
    panel.audit(tx, actor or "system", "org_delete", org_id,
                panel.audit_note(members=int(count or 0)))


# --------------------------------------------------------------------------- #
# 成员
# --------------------------------------------------------------------------- #
def set_member(tx, org_id, account_id, role, actor=""):
    role = role if role in ROLES else "member"
    tx.execute(
        "INSERT INTO org_members (org_id, account_id, role, joined_at) VALUES (?,?,?,?)"
        " ON CONFLICT(org_id, account_id) DO UPDATE SET role=excluded.role",
        (org_id, int(account_id), role, time.time()))
    panel.audit(tx, actor or "system", "org_member_role", org_id,
                panel.audit_note(account=int(account_id), role=role))


def drop_member(tx, org_id, account_id, actor=""):
    count = tx.execute("DELETE FROM org_members WHERE org_id=? AND account_id=?",
                       (org_id, int(account_id))).rowcount
    if count:
        panel.audit(tx, actor or "system", "org_member_remove", org_id,
                    panel.audit_note(account=int(account_id)))
    return count


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def row(conn, org_id):
    return conn.execute("SELECT * FROM orgs WHERE id=?", (str(org_id or ""),)).fetchone()


def heads_count(conn, org_id):
    return int(conn.execute(
        "SELECT COUNT(*) AS n FROM org_members WHERE org_id=? AND role='head'",
        (org_id,)).fetchone()["n"] or 0)


def listing(conn, account_id=None, include_archived=False):
    """组织清单：成员数、负责人数、我在里面的身份。"""
    sql = ("SELECT o.*, (SELECT COUNT(*) FROM org_members m WHERE m.org_id=o.id) AS n"
           " FROM orgs o")
    if not include_archived:
        sql += " WHERE o.archived_at=0"
    sql += " ORDER BY o.archived_at<>0, o.created_at"
    mine = {}
    if account_id:
        for r in conn.execute("SELECT org_id, role FROM org_members WHERE account_id=?",
                              (int(account_id),)).fetchall():
            mine[r["org_id"]] = r["role"]
    out = []
    for r in conn.execute(sql).fetchall():
        archived = bool(r["archived_at"])
        members = archived_members(r) if archived else None
        count = len(members) if archived else int(r["n"] or 0)
        heads = sum(1 for m in members if m["role"] == "head") if archived else \
            heads_count(conn, r["id"])
        cover = cover_view(r)
        out.append({"id": r["id"], "name": r["name"], "note": r["note"],
                    "cover": cover["cover"], "coverAt": cover["coverAt"],
                    "coverBytes": cover["coverBytes"],
                    "createdAt": r["created_at"], "createdBy": r["created_by"],
                    "members": count, "heads": heads, "archived": archived,
                    "archivedAt": r["archived_at"],
                    "myRole": mine.get(r["id"], ""),
                    "myRoleLabel": ROLE_LABEL.get(mine.get(r["id"], ""), "")})
    return out


def my_role(conn, org_id, account_id):
    r = conn.execute("SELECT role FROM org_members WHERE org_id=? AND account_id=?",
                     (str(org_id or ""), int(account_id or 0))).fetchone()
    return r["role"] if r else ""
