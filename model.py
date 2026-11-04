# -*- coding: utf-8 -*-
"""Business logic: state folding, fuzzy search, ticket assignment, bonus maths.

Ticket rules
------------
* 1 ticket for showing up (签到), 1 for task one, 1 for task two -> 1..3 tickets.
* ``pool_size`` tickets exist in total.  What is left after the guaranteed ones
  (``bonus = pool - guaranteed``) is shared by the people who showed up,
  weighted by *how early* they finished the two tasks.

Bonus weighting
---------------
People are ranked by (tasks finished desc, mean normalised task rank asc,
check-in time asc).  Rank ``p`` out of ``M`` gets weight

    w(p) = 1 + beta * (M - p) / (M - 1)

so the fastest finisher has weight ``1 + beta`` and the slowest ``1``.  The
expected share is ``bonus * w / sum(w)``; integers are handed out with the
largest-remainder (Hamilton) method, so the total is exactly ``bonus`` and no
ticket is created or destroyed.  ``beta`` controls how steep the reward is:

    beta=0.5  ->  fastest gets 1.5x the share of the slowest (very flat)
    beta=1.0  ->  2x (default)
    beta=2.0  ->  3x (fast finishers can pocket two extra tickets)
"""

import csv
import hashlib
import io
import random
import time
import uuid

import db
import expr
import pinyintable

TASK_KINDS = ("task1", "task2")
ROLES = ("participant", "staff", "volunteer")
ROLE_LABEL = {"participant": "参加人员", "staff": "工作人员", "volunteer": "志愿者"}


def is_participant(stu):
    """Only participants earn tickets; staff and volunteers are only "present".

    They still count as people in the room, which is all the grand prize needs.
    """
    return (stu.get("role") or "participant") == "participant"


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg = msg
        self.code = code


# --------------------------------------------------------------------------- #
# ticket numbers
# --------------------------------------------------------------------------- #
def ensure_seed(conn):
    """Seed for the deterministic remainder tie-break (not for the numbering)."""
    seed = int(db.get_config(conn, "tie_seed", 0) or 0)
    if not seed:
        seed = int(db.get_config(conn, "deck_seed", 0) or 0)  # older database
        if not seed:
            seed = random.SystemRandom().randrange(1, 2**31 - 1)
        db.set_config_raw(conn, "tie_seed", seed)
    return seed


def _live_codes(conn):
    return {
        row["code"]
        for row in conn.execute("SELECT code FROM tickets WHERE released_at IS NULL")
    }


def assign_ticket(conn, student_id, kind):
    """Return the ticket code for (student, kind), assigning one if needed.

    Numbers are sequential ("the next number on the roll").  A previous undo may
    have released a code for this very (student, kind); that one is handed back
    instead, so re-marking never silently renumbers somebody.

    Sequential numbering keeps the numbers independent of the pool size, which
    is what makes the pool safe to raise *and* lower mid-event.
    """
    row = conn.execute(
        "SELECT * FROM tickets WHERE student_id=? AND kind=? AND released_at IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (student_id, kind),
    ).fetchone()
    if row is not None:
        used = _live_codes(conn)
        if row["code"] not in used:
            conn.execute(
                "UPDATE tickets SET released_at=NULL, assigned_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            return row["code"]
    high = conn.execute("SELECT COALESCE(MAX(code), 0) AS m FROM tickets").fetchone()["m"]
    code = int(high) + 1
    conn.execute(
        "INSERT INTO tickets (student_id, kind, code, assigned_at) VALUES (?,?,?,?)",
        (student_id, kind, code, time.time()),
    )
    return code


def release_ticket(conn, student_id, kind):
    conn.execute(
        "UPDATE tickets SET released_at=? WHERE student_id=? AND kind=? AND released_at IS NULL",
        (time.time(), student_id, kind),
    )


def live_ticket_count(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE released_at IS NULL"
    ).fetchone()
    return row["n"]


def live_ticket_count_of(conn, student_id):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE student_id=? AND released_at IS NULL",
        (student_id,),
    ).fetchone()
    return row["n"]


def max_live_code(conn):
    row = conn.execute(
        "SELECT COALESCE(MAX(code), 0) AS m FROM tickets WHERE released_at IS NULL"
    ).fetchone()
    return int(row["m"])


def effective_pool(conn, cfg=None, students=None):
    """How many tickets exist in total.

    ``auto`` mode follows the real attendance: ``present * 3 + bonus_constant``.
    It can never starve the guaranteed tickets (they alone need at most
    ``present * 3``), it grows and shrinks with the room, and the constant alone
    decides how much the speed bonus is worth.

    ``fixed`` mode is the manual escape hatch (exactly ``pool_size``), for when a
    specific number of tickets has been printed.

    ``expr`` mode evaluates ``pool_expr`` against the live counts, so the pool can
    follow the room in a shape the operator chooses.  It is re-evaluated on every
    read, which is the whole difference from ``fixed``.
    """
    cfg = cfg or db.all_config(conn)
    mode = cfg.get("pool_mode") or "auto"
    if mode == "fixed":
        return max(0, int(cfg.get("pool_size") or 0))
    students = students if students is not None else snapshot(conn, cfg)
    if mode == "expr":
        try:
            return max(0, int(expr.evaluate(cfg.get("pool_expr") or "",
                                            pool_context(students))))
        except expr.ExprError:
            # a broken expression must not take the whole page down; the
            # settings page shows the same message
            return 0
    # only participants earn tickets, so only they may grow the pool
    present = sum(1 for s in students if s["marks"]["checkin"] and is_participant(s))
    return max(0, present * 3 + int(cfg.get("bonus_constant") or 0))


POOL_NAMES = ("signed_in", "present", "participants", "staff_present",
              "tasks_done", "guaranteed", "bonus_constant", "pool_size")


def pool_context(students):
    """The names a pool expression may use."""
    signed_in = sum(1 for s in students
                    if s["marks"]["checkin"] and is_participant(s))
    participants = sum(1 for s in students if is_participant(s))
    staff = sum(1 for s in students
                if not is_participant(s) and s["marks"]["checkin"])
    tasks = sum(1 for s in students
                if s["marks"]["checkin"] and s["marks"]["task1"]
                and s["marks"]["task2"])
    return {
        "signed_in": signed_in,
        "present": signed_in + staff,
        "participants": participants,
        "staff_present": staff,
        "tasks_done": tasks,
        "guaranteed": signed_in * 3,
    }


# --------------------------------------------------------------------------- #
# mark / undo
# --------------------------------------------------------------------------- #
def _get_student(conn, student_id):
    row = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if row is None:
        raise ApiError("学生不存在", 404)
    return row


def _marks(conn):
    out = {}
    for row in conn.execute("SELECT * FROM marks"):
        out.setdefault(row["student_id"], {})[row["kind"]] = row["done_at"]
    return out


def mark(conn, student_id, kind, actor="admin", at=None, source="admin",
         allow_skip=False):
    """Mark one step complete.  Idempotent: marking twice keeps the first time.

    ``allow_skip`` is the live-event escape hatch: with the console unlocked an
    operator may mark a later step first.  The step is recorded normally but the
    audit line says it was out of order, so the deviation is in the record and in
    the exported log rather than silently invisible.
    """
    if kind not in db.KINDS:
        raise ApiError("未知的任务类型: %s" % kind)
    stu = _get_student(conn, student_id)
    if db.get_config(conn, "finalized", False):
        raise ApiError("已结算奖票,如需继续操作请先在设置中取消结算(需解锁)", 409)
    marks = _marks(conn).get(student_id, {})
    participant = is_participant(dict(stu))
    if not participant and kind != "checkin":
        raise ApiError(
            "%s只记录到场，不参与任务登记" % ROLE_LABEL.get(stu["role"] or "staff", "工作人员"), 409
        )
    need = {"checkin": None, "task1": "checkin", "task2": "task1"}[kind]
    skipped = None
    if need and need not in marks:
        if not allow_skip:
            raise ApiError(
                "需要先完成「%s」才能标记「%s」"
                % (db.KIND_LABEL[need], db.KIND_LABEL[kind]), 409
            )
        skipped = [k for k in db.KINDS
                   if k != kind and k not in marks
                   and db.KINDS.index(k) < db.KINDS.index(kind)]
    if kind in marks:
        return {
            "already": True,
            "done_at": marks[kind],
            "code": _live_code(conn, student_id, kind),
        }
    ts = float(at) if at is not None else time.time()
    conn.execute(
        "INSERT INTO marks (student_id, kind, done_at, source, actor) VALUES (?,?,?,?,?) "
        "ON CONFLICT(student_id, kind) DO UPDATE SET done_at=excluded.done_at",
        (student_id, kind, ts, source, actor),
    )
    code = assign_ticket(conn, student_id, kind) if participant else None
    detail = "%s %s%s" % (stu["name"], db.KIND_LABEL[kind],
                          (" -> #%03d" % code) if code else " (不计奖票)")
    if skipped:
        detail += "（越序：未完成 %s）" % "、".join(
            db.KIND_LABEL[k] for k in skipped)
    db.audit(conn, "mark", student_id, kind, detail, actor)
    return {"already": False, "done_at": ts, "code": code,
            "out_of_order": bool(skipped)}


def _live_code(conn, student_id, kind):
    row = conn.execute(
        "SELECT code FROM tickets WHERE student_id=? AND kind=? AND released_at IS NULL",
        (student_id, kind),
    ).fetchone()
    return row["code"] if row else None


def unmark(conn, student_id, kind, actor="admin", cascade=False):
    if kind not in db.KINDS:
        raise ApiError("未知的任务类型: %s" % kind)
    stu = _get_student(conn, student_id)
    marks = _marks(conn).get(student_id, {})
    if kind not in marks:
        raise ApiError("「%s」本来就没有标记" % db.KIND_LABEL[kind], 409)

    dependents = []
    if kind == "checkin":
        dependents = [k for k in TASK_KINDS if k in marks]
    elif kind == "task1":
        dependents = [k for k in ("task2",) if k in marks]
    if dependents and not cascade:
        names = "、".join(db.KIND_LABEL[k] for k in dependents)
        raise ApiError(
            "「%s」之后已完成「%s」,请先撤销后面的步骤(或确认级联撤销)"
            % (db.KIND_LABEL[kind], names),
            409,
        )

    targets = [kind] + (dependents if cascade else [])
    released = []
    for k in reversed(targets):
        if k in marks:
            conn.execute("DELETE FROM marks WHERE student_id=? AND kind=?", (student_id, k))
            release_ticket(conn, student_id, k)
            released.append(k)
    db.audit(
        conn,
        "undo",
        student_id,
        kind,
        "%s 撤销 %s%s"
        % (
            stu["name"],
            "、".join(db.KIND_LABEL[k] for k in targets),
            ("(级联)" if len(targets) > 1 else ""),
        ),
        actor,
    )
    return {"released": released, "cascaded": targets}


def self_checkin(conn, sid, name, actor="self"):
    """Check a member in from their own phone.

    Identity is a strict pair: exact id AND exact name, because the page shows no
    roster and must not allow enumerating classmates.  Everything else about
    checking in (ticket assignment, ordering, audit) is the same code path the
    operator uses, so the two entry points can never diverge.
    """
    sid = (sid or "").strip()
    name = (name or "").strip()
    if not sid or not name:
        raise ApiError("请把学号和姓名都填上")
    row = conn.execute(
        "SELECT * FROM students WHERE lower(sid)=lower(?) AND name=?", (sid, name)
    ).fetchone()
    if row is None:
        # Deliberately vague: no hint about which half was wrong, and no
        # suggestion that the id exists at all.
        raise ApiError("学号或姓名不符", 404)

    existing = conn.execute(
        "SELECT done_at, source FROM marks WHERE student_id=? AND kind='checkin'",
        (row["id"],),
    ).fetchone()
    if existing is not None:
        return {"already": True, "student": dict(row),
                "role": row["role"] or "participant",
                **_self_checkin_view(conn, row["id"])}

    res = mark(conn, row["id"], "checkin", actor=actor, source="self")
    db.audit(conn, "self_checkin", row["id"], "checkin", "%s %s" % (row["sid"], row["name"]), actor)
    out = {"already": False, "student": dict(row), **_self_checkin_view(conn, row["id"])}
    out["code"] = res.get("code")
    out["role"] = row["role"] or "participant"
    return out


def _self_checkin_view(conn, student_id):
    """Position at the door + this member's own tickets/tasks."""
    row = conn.execute(
        "SELECT done_at FROM marks WHERE student_id=? AND kind='checkin'", (student_id,)
    ).fetchone()
    done_at = row["done_at"] if row else None
    position = 0
    if done_at is not None:
        position = conn.execute(
            "SELECT COUNT(*) AS n FROM marks WHERE kind='checkin' AND done_at<=?",
            (done_at,),
        ).fetchone()["n"]
    tickets = [
        {"code": r["code"], "kind": r["kind"]}
        for r in conn.execute(
            "SELECT code, kind FROM tickets WHERE student_id=? AND released_at IS NULL ORDER BY code",
            (student_id,),
        )
    ]
    marks = {
        r["kind"]: r["done_at"]
        for r in conn.execute("SELECT kind, done_at FROM marks WHERE student_id=?", (student_id,))
    }
    return {"position": position, "tickets": tickets, "marks": marks}


def set_mark_time(conn, student_id, kind, at, actor="admin"):
    stu = _get_student(conn, student_id)
    marks = _marks(conn).get(student_id, {})
    if kind not in marks:
        raise ApiError("「%s」还没有标记" % db.KIND_LABEL[kind], 409)
    conn.execute(
        "UPDATE marks SET done_at=? WHERE student_id=? AND kind=?", (float(at), student_id, kind)
    )
    db.audit(
        conn,
        "edit_time",
        student_id,
        kind,
        "%s %s 时间改为 %s" % (stu["name"], db.KIND_LABEL[kind], _fmt_local(conn, at)),
        actor,
    )
    return {"done_at": float(at)}


def _fmt_local(conn, ts):
    off = float(db.get_config(conn, "event_timezone_offset", 8) or 0)
    return time.strftime("%m-%d %H:%M:%S", time.gmtime(float(ts) + off * 3600))


# --------------------------------------------------------------------------- #
# roster
# --------------------------------------------------------------------------- #
ROLE_WORDS = (
    (("志愿者", "志愿", "volunteer"), "volunteer"),
    (("工作人员", "工作", "干事", "管理", "主办", "协办", "staff", "crew"), "staff"),
)


def parse_role(text):
    """Map a free-form label from a spreadsheet onto one of ROLES."""
    raw = (text or "").strip().lower()
    if not raw:
        return "participant"
    for words, role in ROLE_WORDS:
        if any(w in raw for w in words):
            return role
    return "participant"


def normalize_pinyin(stu):
    name = stu.get("name") or ""
    py = (stu.get("py") or "").strip().lower() or pinyintable.pinyin_full(name)
    pyi = (stu.get("pyi") or "").strip().lower() or "".join(
        s[0] for s in pinyintable.syllables(name) if s
    )
    return py, pyi


def add_student(conn, sid, name, py=None, pyi=None, grp=None, note=None,
                role="participant", actor="admin"):
    sid = (sid or "").strip()
    name = (name or "").strip()
    if not sid or not name:
        raise ApiError("学号和姓名都不能为空")
    py, pyi = normalize_pinyin({"name": name, "py": py, "pyi": pyi})
    try:
        conn.execute(
            "INSERT INTO students (sid, name, py, pyi, grp, note, role, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, name, py, pyi, grp or "", note or "",
             role if role in ROLES else "participant", time.time()),
        )
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise ApiError("学号 %s 已存在" % sid, 409)
        raise
    db.audit(conn, "add_student", detail="%s %s" % (sid, name), actor=actor)
    return {"sid": sid, "name": name}


def update_student(conn, student_id, actor="admin", **fields):
    stu = _get_student(conn, student_id)
    sets, vals = [], []
    for key in ("name", "py", "pyi", "grp", "note"):
        if key in fields and fields[key] is not None:
            sets.append("%s=?" % key)
            vals.append(fields[key])
    role_changed = None
    if fields.get("role") in ROLES and fields["role"] != (stu["role"] or "participant"):
        sets.append("role=?")
        vals.append(fields["role"])
        role_changed = fields["role"]
    if "name" in fields and fields["name"] and not fields.get("py"):
        py, pyi = normalize_pinyin({"name": fields["name"], "py": None, "pyi": fields.get("pyi")})
        sets += ["py=?", "pyi=?"]
        vals += [py, pyi]
    if not sets:
        return dict(stu)
    conn.execute("UPDATE students SET %s WHERE id=?" % ",".join(sets), vals + [student_id])
    # Turning someone into staff/volunteer must take back the tickets they were
    # given while they still counted as a participant, otherwise they stay in the
    # ticket draw and the pool no longer adds up.
    if role_changed and role_changed != "participant":
        held = live_ticket_count_of(conn, student_id)
        if held:
            conn.execute(
                "UPDATE tickets SET released_at=? WHERE student_id=? AND released_at IS NULL",
                (time.time(), student_id),
            )
            db.audit(conn, "revoke_tickets", student_id, None,
                     "身份改为%s，收回 %d 张奖票" % (ROLE_LABEL[role_changed], held), actor)
    row = _get_student(conn, student_id)
    db.audit(conn, "edit_student", student_id, None, str(fields), actor)
    return dict(row)


def delete_student(conn, student_id, actor="admin"):
    stu = _get_student(conn, student_id)
    conn.execute("UPDATE tickets SET released_at=? WHERE student_id=? AND released_at IS NULL",
                 (time.time(), student_id))
    conn.execute("DELETE FROM marks WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM students WHERE id=?", (student_id,))
    db.audit(conn, "del_student", None, None, "%s %s" % (stu["sid"], stu["name"]), actor)
    return {"deleted": stu["sid"]}


HEADER_ALIASES = {
    "sid": ("学号", "sid", "id", "号码", "studentid", "student_id", "工号", "准考证号"),
    "name": ("姓名", "name", "名字", "学生姓名"),
    "py": ("拼音", "py", "pinyin", "全拼"),
    "pyi": ("首字母", "pyi", "initials", "简拼"),
    "grp": ("班级", "分组", "grp", "group", "小组", "专业"),
    "note": ("备注", "note", "说明"),
    "role": ("身份", "类别", "角色", "人员类别", "role"),
}


def _detect_header(row):
    mapping = {}
    for idx, cell in enumerate(row):
        key = (cell or "").strip().lower().replace(" ", "")
        for field, aliases in HEADER_ALIASES.items():
            if key in aliases:
                mapping[field] = idx
                break
    return mapping if "sid" in mapping and "name" in mapping else None


def import_roster(conn, text, actor="admin", replace=False):
    """Parse CSV/TSV roster text.  Upserts by student id."""
    text = (text or "").strip("\ufeff").strip()
    if not text:
        raise ApiError("导入内容为空")
    sample = text.splitlines()[0]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise ApiError("没有解析到任何数据行")

    header = _detect_header(rows[0])
    if header:
        body = rows[1:]
    else:
        header = {"sid": 0, "name": 1}
        if len(rows[0]) > 2:
            header["grp"] = 2
        body = rows

    added = updated = skipped = 0
    errors = []
    for lineno, row in enumerate(body, start=2 if _detect_header(rows[0]) else 1):
        def cell(field):
            idx = header.get(field)
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()

        sid, name = cell("sid"), cell("name")
        role = parse_role(cell("role")) if "role" in header else "participant"
        if not sid and not name:
            continue
        if not sid or not name:
            skipped += 1
            errors.append("第 %d 行缺少学号或姓名" % lineno)
            continue
        py, pyi = normalize_pinyin({"name": name, "py": cell("py"), "pyi": cell("pyi")})
        exists = conn.execute("SELECT id FROM students WHERE sid=?", (sid,)).fetchone()
        if exists:
            conn.execute(
                "UPDATE students SET name=?, py=?, pyi=?, grp=?, note=?, role=? WHERE id=?",
                (name, py, pyi, cell("grp"), cell("note"), role, exists["id"]),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO students (sid, name, py, pyi, grp, note, role, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (sid, name, py, pyi, cell("grp"), cell("note"), role, time.time()),
            )
            added += 1
    db.audit(conn, "import_roster", detail="added=%d updated=%d skipped=%d" % (added, updated, skipped), actor=actor)
    return {"added": added, "updated": updated, "skipped": skipped, "errors": errors[:10]}


# --------------------------------------------------------------------------- #
# snapshot / search
# --------------------------------------------------------------------------- #
def snapshot(conn, cfg=None):
    cfg = cfg or db.all_config(conn)
    marks = _marks(conn)
    tickets = {}
    for row in conn.execute(
        "SELECT student_id, kind, code FROM tickets WHERE released_at IS NULL ORDER BY code"
    ):
        tickets.setdefault(row["student_id"], []).append(
            {"code": row["code"], "kind": row["kind"]}
        )

    students = []
    for row in conn.execute("SELECT * FROM students ORDER BY sid"):
        item = dict(row)
        m = marks.get(item["id"], {})
        t = tickets.get(item["id"], [])
        item["role"] = item.get("role") or "participant"
        item["marks"] = {k: m.get(k) for k in db.KINDS}
        item["tickets"] = t
        item["ticket_count"] = len(t)
        # next pending step, in the enforced chain order
        nxt = None
        for kind in db.KINDS:
            if kind not in m:
                nxt = kind
                break
        if not is_participant(item) and "checkin" in m:
            nxt = None  # staff have nothing left to do after showing up
        item["next"] = nxt
        item["progress"] = sum(1 for k in db.KINDS if k in m)
        item["tasks"] = sum(1 for k in TASK_KINDS if k in m)
        students.append(item)
    return students


def stats(conn, cfg=None, students=None):
    cfg = cfg or db.all_config(conn)
    students = students if students is not None else snapshot(conn, cfg)
    parts = [s for s in students if is_participant(s)]
    staff = [s for s in students if not is_participant(s)]
    present = [s for s in parts if s["marks"]["checkin"]]
    staff_present = [s for s in staff if s["marks"]["checkin"]]
    c = {
        "total": len(students),
        "participants": len(parts),
        "staff": len(staff),
        "checkin": len(present),          # participants in the room: drives the pool
        "staff_present": len(staff_present),
        "present_all": len(present) + len(staff_present),   # grand-prize pool
    }
    for kind in TASK_KINDS:
        c[kind] = sum(1 for s in students if s["marks"][kind])
    c["guaranteed"] = c["checkin"] + c["task1"] + c["task2"]
    c["pool_mode"] = cfg.get("pool_mode") or "auto"
    c["bonus_constant"] = int(cfg.get("bonus_constant") or 0)
    c["pool_size"] = effective_pool(conn, cfg, students)
    c["fixed_pool_size"] = int(cfg.get("pool_size") or 0)
    c["live_tickets"] = live_ticket_count(conn)
    c["max_code"] = max_live_code(conn)
    c["bonus_pool"] = max(0, c["pool_size"] - c["guaranteed"])
    c["remaining"] = c["pool_size"] - c["live_tickets"]
    c["shortfall"] = max(0, c["live_tickets"] - c["pool_size"])
    c["finalized"] = bool(cfg.get("finalized"))
    return c


def _search_score(stu, q, qcompact):
    sid = (stu.get("sid") or "").lower()
    name = (stu.get("name") or "")
    py = (stu.get("py") or "").lower()
    pyi = (stu.get("pyi") or "").lower()
    best = 0
    if not q:
        return 0
    if sid == qcompact or py == qcompact:
        best = 1000
    elif name == q:
        best = 990
    elif sid.startswith(qcompact):
        best = 900
    elif py.startswith(qcompact):
        best = 860
    elif pyi == qcompact:
        best = 850
    elif name.startswith(q):
        best = 820
    elif pyi.startswith(qcompact):
        best = 780
    elif q in name:
        best = 700
    elif qcompact in py:
        best = 600
    elif qcompact in pyi:
        best = 500
    elif q in sid:
        best = 400
    return best


def search(conn, query, limit=10, students=None):
    q = (query or "").strip().lower()
    if not q:
        return []
    qcompact = q.replace(" ", "")
    students = students if students is not None else snapshot(conn)
    scored = []
    for stu in students:
        score = _search_score(stu, q, qcompact)
        if score:
            scored.append((score, stu))
    scored.sort(key=lambda x: (-x[0], x[1]["sid"]))
    return [s for _, s in scored[:limit]]


# --------------------------------------------------------------------------- #
# bonus maths
# --------------------------------------------------------------------------- #
def _task_ranks(students):
    """kind -> {student_id: normalised rank in [0,1]} (0 = fastest)."""
    out = {}
    for kind in TASK_KINDS:
        done = [(s["marks"][kind], s["id"]) for s in students if s["marks"][kind]]
        times = sorted({t for t, _ in done})
        n = len(done)
        pos = {t: i + 1 for i, t in enumerate(times)}
        ranks = {}
        for t, sid in done:
            if n <= 1:
                ranks[sid] = 0.0
            else:
                ranks[sid] = (pos[t] - 1) / float(n - 1)
        out[kind] = ranks
    return out


def ranking(conn, students=None, cfg=None):
    """Ordered list of present students, best first, with the numbers behind it."""
    cfg = cfg or db.all_config(conn)
    students = students if students is not None else snapshot(conn, cfg)
    present = [s for s in students if s["marks"]["checkin"] and is_participant(s)]
    ranks = _task_ranks(students)

    def key(s):
        us = [ranks[k][s["id"]] for k in TASK_KINDS if s["id"] in ranks[k]]
        mean_u = sum(us) / len(us) if us else 1.0
        return (-s["tasks"], round(mean_u, 9), s["marks"]["checkin"] or 0, s["sid"])

    present.sort(key=key)
    out = []
    for i, s in enumerate(present):
        us = [ranks[k][s["id"]] for k in TASK_KINDS if s["id"] in ranks[k]]
        out.append(
            {
                "id": s["id"],
                "sid": s["sid"],
                "name": s["name"],
                "tasks": s["tasks"],
                "mean_rank": (sum(us) / len(us)) if us else 1.0,
                "checkin": s["marks"]["checkin"],
            }
        )
    return out


def plan_bonus(conn, cfg=None, students=None):
    """Compute the bonus allocation without writing anything.

    Returns {"rows": [...], "summary": {...}, "beta": .., "bonus_pool": ..}.
    """
    cfg = cfg if cfg is not None else db.all_config(conn)
    students = students if students is not None else snapshot(conn, cfg)
    beta = float(cfg.get("beta") if cfg.get("beta") is not None else 1.0)
    st = stats(conn, cfg, students)
    pool = st["pool_size"]
    # "already handed out" is the number of live tickets, not the number of
    # marks: if anything ever makes the two differ (a staff member who held a
    # ticket, a ticket kept after a role change), the total still lands on the
    # pool exactly instead of overshooting it.
    issued = max(st["live_tickets"], st["guaranteed"])
    bonus = max(0, pool - issued) if cfg.get("allow_bonus", True) else 0

    order = ranking(conn, students, cfg)
    m = len(order)
    weights = []
    for i in range(m):
        if m <= 1:
            w = 1.0 + beta
        else:
            w = 1.0 + beta * (m - 1 - i) / float(m - 1)
        weights.append(w)
    total_w = sum(weights) or 1.0
    expected = [bonus * w / total_w for w in weights]

    # largest remainder.  Ties are broken by a deterministic pseudo-random key
    # derived from the deck seed, *not* by rank: otherwise the leftover tickets
    # would keep flowing to the best-ranked students even at beta = 0, which
    # would make the beta knob a lie.
    floors = [int(x) for x in expected]
    left = bonus - sum(floors)
    seed = int(cfg.get("tie_seed") or cfg.get("deck_seed") or 0)

    def tie_key(idx):
        raw = "%d|%d" % (seed, order[idx]["id"])
        return hashlib.sha256(raw.encode("utf-8")).digest()

    frac = sorted(range(m), key=lambda i: (-(expected[i] - floors[i]), tie_key(i)))
    for i in frac[:left]:
        floors[i] += 1

    rows = []
    for i, item in enumerate(order):
        rows.append(
            {
                **item,
                "pos": i + 1,
                "weight": round(weights[i], 4),
                "expected": round(expected[i], 3),
                "bonus": floors[i],
            }
        )

    hist = {}
    for r in rows:
        hist[r["bonus"]] = hist.get(r["bonus"], 0) + 1
    summary = {
        "present": m,
        "bonus_pool": bonus,
        "pool_size": pool,
        "guaranteed": st["guaranteed"],
        "beta": beta,
        "min_bonus": min([r["bonus"] for r in rows]) if rows else 0,
        "max_bonus": max([r["bonus"] for r in rows]) if rows else 0,
        "histogram": {str(k): hist[k] for k in sorted(hist)},
        "finalized": bool(cfg.get("finalized")),
        "allow_bonus": bool(cfg.get("allow_bonus", True)),
        "pool_mode": st["pool_mode"],
        "bonus_constant": st["bonus_constant"],
        "fixed_pool_size": st["fixed_pool_size"],
        "live_tickets": st["live_tickets"],
        "will_use_all": True,
    }
    return {"rows": rows, "summary": summary}


def finalize(conn, actor="admin"):
    cfg = db.all_config(conn)
    if cfg.get("finalized"):
        raise ApiError("已经结算过了", 409)
    students = snapshot(conn, cfg)
    st = stats(conn, cfg, students)
    if st["checkin"] == 0:
        raise ApiError("还没有人签到,无法结算", 409)
    plan = plan_bonus(conn, cfg, students)
    if st["guaranteed"] > st["pool_size"]:
        raise ApiError(
            "保底奖票 %d 张已超过奖票池 %d 张:请在设置里把奖票池(或额外奖票常数)调大"
            % (st["guaranteed"], st["pool_size"]),
            409,
        )
    if st["live_tickets"] > st["pool_size"]:
        raise ApiError(
            "已经发出 %d 张奖票,超过奖票池 %d 张:请调大奖票池" % (st["live_tickets"], st["pool_size"]),
            409,
        )
    assigned = 0
    for row in plan["rows"]:
        for _ in range(row["bonus"]):
            assign_ticket(conn, row["id"], "bonus")
            assigned += 1
    db.set_config_raw(conn, "finalized", True)
    db.set_config_raw(conn, "finalized_at", time.time())
    db.set_config_raw(conn, "final_plan", {
        "rows": plan["rows"],
        "summary": plan["summary"],
    })
    db.audit(
        conn,
        "finalize",
        detail="bonus tickets assigned: %d (pool=%d, guaranteed=%d)"
        % (assigned, st["pool_size"], st["guaranteed"]),
        actor=actor,
    )
    return {"assigned": assigned, "summary": plan["summary"]}


def unfinalize(conn, actor="admin"):
    cfg = db.all_config(conn)
    if not cfg.get("finalized"):
        raise ApiError("当前没有结算", 409)
    conn.execute(
        "UPDATE tickets SET released_at=? WHERE kind='bonus' AND released_at IS NULL",
        (time.time(),),
    )
    db.set_config_raw(conn, "finalized", False)
    db.set_config_raw(conn, "finalized_at", 0)
    db.audit(conn, "unfinalize", detail="bonus tickets released", actor=actor)
    return {"ok": True}


def reset_tickets(conn, actor="admin"):
    conn.execute("UPDATE tickets SET released_at=? WHERE released_at IS NULL", (time.time(),))
    db.set_config_raw(conn, "finalized", False)
    db.set_config_raw(conn, "finalized_at", 0)
    db.audit(conn, "reset_tickets", detail="all tickets released", actor=actor)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# draws
# --------------------------------------------------------------------------- #
def normalize_prizes(cfg):
    """Prize list as dicts: {"name", "count", "mode", "batch"}.

    Accepts plain strings too (an older config listed just names), so a stored
    config never has to be hand-migrated.  ``batch`` marks a prize that is drawn
    out in one go instead of one winner per click.
    """
    out = []
    for item in cfg.get("prizes") or []:
        if isinstance(item, str):
            name, count, mode, batch = item, 1, "ticket", False
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            try:
                count = int(item.get("count"))
            except (TypeError, ValueError):
                count = 1
            mode = item.get("mode") if item.get("mode") in ("ticket", "person") else "ticket"
            batch = bool(item.get("batch"))
        else:
            continue
        if not name:
            continue
        out.append({"name": name, "count": max(0, count), "mode": mode, "batch": batch})
    return out


def prize_status(conn, cfg=None):
    """Configured prizes with how many have actually been drawn (voids excluded)."""
    cfg = cfg or db.all_config(conn)
    counted = {
        (r["prize"], r["mode"]): r["n"]
        for r in conn.execute(
            "SELECT prize, mode, COUNT(*) AS n FROM draws WHERE voided_at IS NULL "
            "GROUP BY prize, mode"
        )
    }
    out = []
    for p in normalize_prizes(cfg):
        drawn = counted.get((p["name"], p["mode"]), 0)
        out.append({**p, "drawn": drawn, "remaining": max(0, p["count"] - drawn)})
    return out


def _require_prize_quota(conn, cfg, prize, mode):
    for p in prize_status(conn, cfg):
        if p["name"] == prize and p["mode"] == mode:
            if p["remaining"] <= 0:
                raise ApiError(
                    "「%s」已抽完（%d/%d）" % (prize, p["drawn"], p["count"]), 409
                )
            return p
    return None  # not configured: allowed (ad-hoc prize)


# De-duplication has two independent axes:
#   inside  - one person cannot take the same prize twice
#   across  - one person cannot take two different prizes
# A prize drawn *by person* (rather than by ticket) only ever follows the first:
# it is a draw among those present, so it is not part of the ticket prizes'
# mutual exclusion, and winning it neither blocks nor is blocked by them.
def draw_rules(cfg):
    inside = cfg.get("draw_unique_inside")
    if inside is None:
        inside = cfg.get("person_draw_no_repeat", True)
    return {"inside": bool(inside),
            "across": bool(cfg.get("draw_unique_across", False))}


def _winners(conn, where, args):
    return {
        r["student_id"]
        for r in conn.execute(
            "SELECT student_id FROM draws WHERE voided_at IS NULL AND " + where, args)
    }


def draw_next(conn, prize="", actor="admin", batch=None):
    cfg = db.all_config(conn)
    if not cfg.get("finalized"):
        raise ApiError("请先结算奖票再抽奖", 409)
    live = {}
    for row in conn.execute(
        "SELECT t.code, t.student_id, s.name, s.sid FROM tickets t "
        "JOIN students s ON s.id=t.student_id WHERE t.released_at IS NULL"
    ):
        live[row["code"]] = row

    used = {
        r["code"]
        for r in conn.execute(
            "SELECT code FROM draws WHERE voided_at IS NULL AND mode='ticket' AND code IS NOT NULL"
        )
    }
    pool = [c for c in live if c not in used]
    rules = draw_rules(cfg)
    if rules["inside"] and prize:
        taken = _winners(conn, "prize=?", (prize,))
        pool = [c for c in pool if live[c]["student_id"] not in taken]
    if rules["across"]:
        taken = _winners(conn, "mode='ticket' AND prize<>?", (prize,))
        pool = [c for c in pool if live[c]["student_id"] not in taken]
    if not pool:
        if rules["inside"] or rules["across"]:
            raise ApiError("没有可抽的奖票了（剩下的票属于已经中过奖的人）", 409)
        raise ApiError("没有可抽的奖票了", 409)
    _require_prize_quota(conn, cfg, prize, "ticket")
    code = random.SystemRandom().choice(sorted(pool))
    winner = live[code]
    seq = _next_seq(conn)
    conn.execute(
        "INSERT INTO draws (seq, prize, mode, code, student_id, ts, batch) "
        "VALUES (?,?,?,?,?,?,?)",
        (seq, prize, "ticket", code, winner["student_id"], time.time(), batch),
    )
    db.audit(
        conn,
        "draw",
        winner["student_id"],
        None,
        "%s -> #%03d %s %s" % (prize or "抽奖", code, winner["name"], winner["sid"]),
        actor,
    )
    return {
        "seq": seq,
        "prize": prize,
        "mode": "ticket",
        "code": code,
        "student_id": winner["student_id"],
        "name": winner["name"],
        "sid": winner["sid"],
        "batch": batch,
    }


def draw_person(conn, prize="特等奖", actor="admin", batch=None):
    """Draw one *person* among those who showed up, all equally likely.

    Deliberately independent of the ticket draw: it consumes no ticket, a person
    with one ticket has the same chance as someone with five, and winning here
    does not affect (or get affected by) the ticket prizes.
    """
    students = snapshot(conn)
    present = [s for s in students if s["marks"]["checkin"]]
    if not present:
        raise ApiError("还没有人签到,无法抽取", 409)

    rules = draw_rules(db.all_config(conn))
    if rules["inside"]:
        # only this prize: a by-person draw sits outside the ticket prizes'
        # mutual exclusion, in both directions
        already = _winners(conn, "prize=?", (prize,))
        candidates = [s for s in present if s["id"] not in already]
        if not candidates:
            raise ApiError("到场的人都已经拿过「%s」了" % (prize or "这个奖"), 409)
    else:
        candidates = present

    _require_prize_quota(conn, db.all_config(conn), prize, "person")
    winner = random.SystemRandom().choice(sorted(candidates, key=lambda s: s["sid"]))
    seq = _next_seq(conn)
    conn.execute(
        "INSERT INTO draws (seq, prize, mode, code, student_id, ts, batch) "
        "VALUES (?,?,?,?,?,?,?)",
        (seq, prize, "person", None, winner["id"], time.time(), batch),
    )
    db.audit(
        conn,
        "draw_person",
        winner["id"],
        None,
        "%s -> %s %s (到场 %d 人均等)"
        % (prize or "特等奖", winner["name"], winner["sid"], len(candidates)),
        actor,
    )
    return {
        "seq": seq,
        "prize": prize,
        "mode": "person",
        "code": None,
        "student_id": winner["id"],
        "name": winner["name"],
        "sid": winner["sid"],
        "candidates": len(candidates),
        "batch": batch,
    }


def draw_batch(conn, prize="", mode="ticket", actor="admin"):
    """Draw every remaining quota of one prize in a single call.

    The winners are picked one at a time through the ordinary single-draw path,
    so tickets are still consumed one by one and both de-duplication rules still
    apply between the winners of the same batch.  A batch that runs out of
    material before the quota is filled keeps what it drew and reports why it
    stopped.  All rows share one batch id, which is what the big screen needs in
    order to reveal the list at once instead of one animation after another.
    """
    cfg = db.all_config(conn)
    quota = _require_prize_quota(conn, cfg, prize, mode)
    if quota is None:
        raise ApiError("「%s」不在奖品清单里，无法一次抽完" % (prize or "这个奖"), 409)
    want = quota["remaining"]
    batch = uuid.uuid4().hex[:12]
    draws, stopped = [], None
    for _ in range(want):
        try:
            if mode == "person":
                draws.append(draw_person(conn, prize, actor, batch=batch))
            else:
                draws.append(draw_next(conn, prize, actor, batch=batch))
        except ApiError as exc:
            stopped = exc.msg
            break
    if not draws:
        raise ApiError(stopped or "没有可抽的了", 409)
    return {"batch": batch, "draws": draws, "stopped": stopped}


def _next_seq(conn):
    row = conn.execute("SELECT COALESCE(MAX(seq),0) AS s FROM draws").fetchone()
    return int(row["s"]) + 1


def void_draw(conn, draw_id, actor="admin"):
    row = conn.execute("SELECT * FROM draws WHERE id=?", (draw_id,)).fetchone()
    if row is None:
        raise ApiError("没有这条抽奖记录", 404)
    if row["voided_at"]:
        raise ApiError("这条抽奖记录已经作废", 409)
    conn.execute("UPDATE draws SET voided_at=? WHERE id=?", (time.time(), draw_id))
    label = ("#%03d " % row["code"]) if row["code"] else ""
    db.audit(
        conn,
        "void_draw",
        row["student_id"],
        None,
        "%s%s %s" % (label, row["prize"] or "", "(人级抽取)" if row["mode"] == "person" else ""),
        actor,
    )
    return {"ok": True}


def draw_history(conn, limit=200):
    rows = conn.execute(
        "SELECT d.*, s.name, s.sid FROM draws d LEFT JOIN students s ON s.id=d.student_id "
        "ORDER BY d.seq DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def last_draw(conn):
    row = conn.execute(
        "SELECT d.*, s.name, s.sid FROM draws d LEFT JOIN students s ON s.id=d.student_id "
        "WHERE d.voided_at IS NULL ORDER BY d.seq DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def batch_draws(conn, batch):
    """Live winners of one batch, in the order they were drawn."""
    if not batch:
        return []
    rows = conn.execute(
        "SELECT d.*, s.name, s.sid FROM draws d LEFT JOIN students s ON s.id=d.student_id "
        "WHERE d.batch=? AND d.voided_at IS NULL ORDER BY d.seq",
        (batch,),
    ).fetchall()
    return [dict(r) for r in rows]


def last_batch(conn):
    """The newest batch, or None when it only ever held a single winner."""
    last = last_draw(conn)
    if not last or not last.get("batch"):
        return None
    rows = batch_draws(conn, last["batch"])
    if len(rows) < 2:
        return None
    return {
        "batch": last["batch"],
        "prize": last["prize"],
        "mode": last["mode"],
        "winners": rows,
    }


def sync_from_roster(conn, entries, actor="admin"):
    """并入一份名单：按学号增量更新，只增不删。

    ``entries`` 是 [(学号, 姓名, 院系, 班级), ...]，由调用方从名单库里读出来。
    已经在这场的名单里但**不在**这份名单里的人不会被删——他可能已经有奖票、有签到记录，
    改成 detached_at 标记，界面单独列出来由人决定。
    """
    now = time.time()
    if not entries:
        raise ApiError("这份名单是空的", 409)
    wanted = {}
    for sid, name, dept, grp in entries:
        sid = (sid or "").strip()
        name = (name or "").strip()
        if sid and name:
            wanted[sid] = {"name": name, "dept": (dept or "").strip(),
                           "grp": (grp or "").strip()}
    if not wanted:
        raise ApiError("这份名单里没有有效的学号与姓名", 409)

    existing = {
        row["sid"]: dict(row)
        for row in conn.execute("SELECT * FROM students")
    }
    added = updated = restored = same = 0
    for sid, item in wanted.items():
        row = existing.get(sid)
        grp = item["grp"] or item["dept"]
        if row is None:
            conn.execute(
                "INSERT INTO students (sid, name, py, pyi, grp, note, role, origin,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, item["name"], *normalize_pinyin({"name": item["name"]}),
                 grp, "", "participant", "roster", now),
            )
            added += 1
            continue
        changed = (row["name"] != item["name"] or (row["grp"] or "") != grp
                   or row["detached_at"])
        if not changed:
            same += 1
            continue
        conn.execute(
            "UPDATE students SET name=?, py=?, pyi=?, grp=?, detached_at=0 WHERE id=?",
            (item["name"], *normalize_pinyin({"name": item["name"]}), grp, row["id"]),
        )
        if row["detached_at"]:
            restored += 1
        else:
            updated += 1

    missing = []
    for sid, row in existing.items():
        if sid in wanted or row["detached_at"]:
            continue
        conn.execute("UPDATE students SET detached_at=? WHERE id=?", (now, row["id"]))
        missing.append({"sid": sid, "name": row["name"]})

    db.audit(conn, "roster_sync", detail=
             "新增 %d 人，更新 %d 人，恢复 %d 人，未变化 %d 人，已不在名单 %d 人"
             % (added, updated, restored, same, len(missing)), actor=actor)
    return {"added": added, "updated": updated, "restored": restored,
            "same": same, "missing": sorted(missing, key=lambda x: x["sid"])}
