#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讲座：议程、计时与提问队列。

这套玩法跟抽奖毫无关系，存在的意义有两个：一是活动现场真的用得上（讲座、分享会、
宣讲），二是把"活动平台"从"抽奖平台"里验证出来——平台只提供外壳、账号、席位、
审计与公开页，议程与提问全在这里。
"""
import time

import db
import errors

ApiError = errors.ApiError

SECTION_DEFAULT_MINUTES = 15


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def sections(conn):
    rows = conn.execute(
        "SELECT * FROM talk_sections ORDER BY pos, id").fetchall()
    return [dict(r) for r in rows]


def questions(conn):
    rows = conn.execute(
        "SELECT * FROM talk_questions ORDER BY "
        "CASE WHEN answered_at IS NULL THEN 0 ELSE 1 END, asked_at, id"
    ).fetchall()
    return [dict(r) for r in rows]


def current_section(conn):
    row = conn.execute(
        "SELECT * FROM talk_sections WHERE started_at IS NOT NULL "
        "AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _next_pos(conn):
    row = conn.execute("SELECT MAX(pos) AS p FROM talk_sections").fetchone()
    return int((row["p"] or 0) + 1)


def snapshot(conn, cfg=None):
    """平台要的"记录清单"：这套玩法没有名单，就交一张空的。

    公示页与页签用的数据走 console_data()/present()，不靠这个名字。
    """
    return []


def stats(conn, cfg=None, records=None):
    """平台的统计位：几个小节、进行到哪、多少提问还没答。"""
    secs = sections(conn)
    qs = questions(conn)
    done = [s for s in secs if s["ended_at"]]
    cur = current_section(conn)
    return {
        "sections": len(secs),
        "done": len(done),
        "open_sections": len([s for s in secs
                              if s["started_at"] and not s["ended_at"]]),
        "questions": len(qs),
        "open_questions": len([q for q in qs if not q["answered_at"]]),
        "current": cur["title"] if cur else "",
        "current_id": cur["id"] if cur else 0,
        "started_at": cur["started_at"] if cur else 0,
        "minutes": sum(int(s["minutes"] or 0) for s in secs),
    }


def roster_info(conn, cfg=None):
    """这套玩法没有名单同步，也就没有"已不在名单"这件事。"""
    return {"detached": 0}


def console_data(conn, cfg=None):
    """控制台页签要的数据：平台原样塞进 state.data。"""
    return {"agenda": sections(conn), "questions": questions(conn)}


def _elapsed(sec, now=None):
    if not sec:
        return 0
    now = now or time.time()
    return max(0, int(now - float(sec)))


def present(conn, cfg=None):
    """大屏要的东西：正在讲哪一节、还剩多少提问。

    这个模块没有给参会者看的公示页（`external` 里只申请了 screen），所以这里只出大屏块。
    """
    cfg = cfg or db.all_config(conn)
    secs = sections(conn)
    qs = questions(conn)
    cur = current_section(conn)
    st = stats(conn, cfg)
    now = time.time()

    agenda = []
    for s in secs:
        state = "done" if s["ended_at"] else ("now" if s["started_at"] else "todo")
        agenda.append({
            "pos": s["pos"], "title": s["title"], "minutes": s["minutes"],
            "state": state,
            "elapsed": _elapsed(s["started_at"], now) if state == "now" else 0,
        })

    return {
        "agenda": agenda,
        "current": ({"title": cur["title"], "minutes": cur["minutes"],
                     "elapsed": _elapsed(cur["started_at"], now)} if cur else None),
        "stats": st,
        "screen": {
            "subtitle": "议程与提问",
            "banner": [
                {"tone": "green", "value": str(st["done"]), "of": "/%d" % st["sections"],
                 "label": "已完成"},
                {"tone": "gold", "value": str(st["open_questions"]), "label": "待回答"},
            ],
            "strip": {"label": "提问", "items": [
                {"title": "", "name": q["who"] or "匿名", "note": q["text"][:24],
                 "kind": "person" if q["answered_at"] else ""}
                for q in qs[-14:]
            ]},
            "wall": {"on": False, "items": []},
            "cards": ([{"title": "现在讲", "body": cur["title"]}] if cur else []),
            "reveal": None,
        },
    }


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
def start_section(conn, section_id, actor="admin"):
    secs = sections(conn)
    target = next((s for s in secs if s["id"] == section_id), None)
    if target is None:
        raise ApiError("没有这一节", 404)
    if target["ended_at"]:
        raise ApiError("这一节已经结束了", 409)
    now = time.time()
    conn.execute(
        "UPDATE talk_sections SET ended_at=? WHERE started_at IS NOT NULL "
        "AND ended_at IS NULL", (now,))
    conn.execute("UPDATE talk_sections SET started_at=?, ended_at=NULL WHERE id=?",
                 (now, section_id))
    db.audit(conn, "talk_start", detail=target["title"], actor=actor)
    return {"current": current_section(conn)}


def end_section(conn, actor="admin"):
    cur = current_section(conn)
    if cur is None:
        raise ApiError("现在没有在讲的小节", 409)
    conn.execute("UPDATE talk_sections SET ended_at=? WHERE id=?",
                 (time.time(), cur["id"]))
    db.audit(conn, "talk_end", detail=cur["title"], actor=actor)
    return {"ended": cur["title"]}


def add_section(conn, title, minutes, actor="admin"):
    title = (title or "").strip()
    if not title:
        raise ApiError("请填写小节标题")
    if len(title) > 60:
        raise ApiError("小节标题最长 60 个字")
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = SECTION_DEFAULT_MINUTES
    if minutes < 1 or minutes > 600:
        raise ApiError("时长需在 1..600 分钟之间")
    cur = conn.execute(
        "INSERT INTO talk_sections (pos, title, minutes) VALUES (?,?,?)",
        (_next_pos(conn), title, minutes))
    db.audit(conn, "talk_section_add", detail=title, actor=actor)
    return {"id": cur.lastrowid}


def drop_section(conn, section_id, actor="admin"):
    row = conn.execute("SELECT * FROM talk_sections WHERE id=?",
                       (section_id,)).fetchone()
    if row is None:
        raise ApiError("没有这一节", 404)
    conn.execute("DELETE FROM talk_sections WHERE id=?", (section_id,))
    db.audit(conn, "talk_section_drop", detail=row["title"], actor=actor)
    return {"dropped": row["title"]}


def ask(conn, who, text, actor="admin"):
    who = (who or "").strip()[:40]
    text = (text or "").strip()
    if not text:
        raise ApiError("请填写提问内容")
    if len(text) > 200:
        raise ApiError("提问最长 200 个字")
    cur = conn.execute(
        "INSERT INTO talk_questions (who, text, asked_at) VALUES (?,?,?)",
        (who or "匿名", text, time.time()))
    db.audit(conn, "talk_ask", detail=text[:40], actor=actor)
    return {"id": cur.lastrowid}


def answer(conn, question_id, actor="admin"):
    row = conn.execute("SELECT * FROM talk_questions WHERE id=?",
                       (question_id,)).fetchone()
    if row is None:
        raise ApiError("没有这条提问", 404)
    if row["answered_at"]:
        raise ApiError("这条提问已经标记过了", 409)
    conn.execute("UPDATE talk_questions SET answered_at=? WHERE id=?",
                 (time.time(), question_id))
    db.audit(conn, "talk_answer", detail=row["text"][:40], actor=actor)
    return {"answered": row["text"][:40]}


def drop_question(conn, question_id, actor="admin"):
    row = conn.execute("SELECT * FROM talk_questions WHERE id=?",
                       (question_id,)).fetchone()
    if row is None:
        raise ApiError("没有这条提问", 404)
    conn.execute("DELETE FROM talk_questions WHERE id=?", (question_id,))
    db.audit(conn, "talk_question_drop", detail=row["text"][:40], actor=actor)
    return {"dropped": True}


def action(name, ctx, payload):
    """平台按 module.json 的 actions 声明调到这里。"""
    conn = ctx["conn"]
    actor = ctx["account"]["name"]

    if name == "start_section":
        return start_section(conn, payload["id"], actor=actor)
    if name == "end_section":
        return end_section(conn, actor=actor)
    if name == "add_section":
        return add_section(conn, payload["title"],
                           payload.get("minutes") or SECTION_DEFAULT_MINUTES, actor=actor)
    if name == "drop_section":
        return drop_section(conn, payload["id"], actor=actor)
    if name == "ask":
        return ask(conn, payload.get("who") or "", payload["text"], actor=actor)
    if name == "answer":
        return answer(conn, payload["id"], actor=actor)
    if name == "drop_question":
        return drop_question(conn, payload["id"], actor=actor)

    raise ApiError("这个模块没有这个动作：%s" % name, 404)


def ensure_seed(conn):
    """新活动先给一节，免得界面空着让人不知道从哪开始。"""
    row = conn.execute("SELECT COUNT(*) AS n FROM talk_sections").fetchone()
    if int(row["n"] or 0):
        return False
    conn.execute(
        "INSERT INTO talk_sections (pos, title, minutes) VALUES (?,?,?)",
        (1, "开场", SECTION_DEFAULT_MINUTES))
    return True


# --------------------------------------------------------------------------- #
# 设置与导出
# --------------------------------------------------------------------------- #
def check_settings(conn, updates):
    """设置项的语义校验。"""
    if "talk_show_questions" in updates and not isinstance(
            updates["talk_show_questions"], bool):
        raise ApiError("「公示页显示提问」只能是开关")


def _csv(rows):
    import csv
    import io as _io
    buf = _io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def _clock(cfg, ts):
    if not ts:
        return ""
    off = float(cfg.get("event_timezone_offset") or 8)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(ts) + off * 3600))


def export_sheets(conn, cfg=None):
    """这套玩法的导出表：议程与提问。"""
    cfg = cfg or db.all_config(conn)
    secs = sections(conn)
    qs = questions(conn)

    agenda = [["序", "小节", "计划分钟", "开始", "结束"]]
    for s in secs:
        agenda.append([s["pos"], s["title"], s["minutes"],
                       _clock(cfg, s["started_at"]), _clock(cfg, s["ended_at"])])

    asked = [["序号", "谁", "提问", "时间", "状态"]]
    for i, q in enumerate(qs, 1):
        asked.append([i, q["who"], q["text"], _clock(cfg, q["asked_at"]),
                      "已回答" if q["answered_at"] else "待回答"])

    # 操作日志在活动自己的审计库里（平台记的 db.audit），这里只导这套玩法的两张表
    return [
        {"name": "01-议程.csv", "text": _csv(agenda), "primary": True},
        {"name": "02-提问.csv", "text": _csv(asked)},
    ]
