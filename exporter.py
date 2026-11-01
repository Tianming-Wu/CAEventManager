# -*- coding: utf-8 -*-
"""Build the end-of-event data export as a ZIP of CSV files.

Everything the operator might want to keep or hand in afterwards, split by what
it answers rather than by table:

    01-汇总.csv        一场活动的基本数字与状态
    02-名单.csv        每个人的身份、三个时间点、名次、手里的票
    03-奖票明细.csv    每张票的号码、归属、来源、是否已作废
    04-抽奖记录.csv    按抽取顺序，含作废标记
    05-中奖汇总.csv    按人聚合：一个人中了哪些奖
    06-操作日志.csv    谁在什么时候做了什么

CSV carries a UTF-8 BOM so Excel opens Chinese without asking.
"""

import csv
import io
import time
import zipfile

import db
import model

KIND_LABEL = {"checkin": "签到", "task1": "任务一", "task2": "任务二", "bonus": "速度奖励"}


def _csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def _clock(cfg, ts):
    if not ts:
        return ""
    off = float(cfg.get("event_timezone_offset") or 8)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(ts) + off * 3600))


def _role(cfg):
    return {"participant": "参加人员", "staff": "工作人员", "volunteer": "志愿者"}


def summary(conn, cfg, students=None):
    st = model.stats(conn, cfg, students)
    prizes = model.prize_status(conn, cfg)
    plans = model.plan_bonus(conn, cfg, students)["summary"]
    rows = [["项目", "值"]]
    rows += [
        ["活动标题", cfg.get("title") or ""],
        ["导出时间", _clock(cfg, time.time())],
        ["", ""],
        ["名单总人数", st["total"]],
        ["参加人员", st["participants"]],
        ["工作人员 / 志愿者", st["staff"]],
        ["参加人员已签到", st["checkin"]],
        ["工作人员已到场", st["staff_present"]],
        ["特等奖候选人数", st["present_all"]],
        ["完成任务一", st["task1"]],
        ["完成任务二", st["task2"]],
        ["", ""],
        ["奖票池", st["pool_size"]],
        ["奖票池模式", "自动（到场×3+常数）" if st["pool_mode"] == "auto" else "固定"],
        ["额外奖票常数", st["bonus_constant"]],
        ["保底奖票", st["guaranteed"]],
        ["速度奖励票", st["bonus_pool"]],
        ["已发出奖票", st["live_tickets"]],
        ["是否已结算", "是" if st["finalized"] else "否"],
        ["结算时间", _clock(cfg, cfg.get("finalized_at"))],
        ["参与速度排名人数", plans.get("present")],
        ["速度权重 β", plans.get("beta")],
        ["", ""],
        ["中奖记录数", len(model.draw_history(conn, 10000))],
    ]
    rows.append(["", ""])
    rows.append(["奖品", "中奖方式 / 数量 / 已抽 / 剩余"])
    for p in prizes:
        rows.append([
            p["name"],
            "%s / %d / %d / %d" % (
                "到场均等" if p["mode"] == "person" else "按票",
                p["count"], p["drawn"], p["remaining"]),
        ])
    return _csv(rows)


def roster(conn, cfg, students=None):
    students = students if students is not None else model.snapshot(conn, cfg)
    order = {r["id"]: r["pos"] for r in model.plan_bonus(conn, cfg, students)["rows"]}
    rows = [["学号", "姓名", "班级/院系", "身份", "签到时间", "任务一时间", "任务二时间",
             "完成任务数", "速度名次", "奖票数", "奖票号码"]]
    for s in students:
        codes = sorted(t["code"] for t in (s.get("tickets") or []))
        rows.append([
            s["sid"], s["name"], s.get("grp") or "", _role(cfg).get(s["role"], s["role"]),
            _clock(cfg, s["marks"].get("checkin")),
            _clock(cfg, s["marks"].get("task1")),
            _clock(cfg, s["marks"].get("task2")),
            s["tasks"], order.get(s["id"], ""), len(codes),
            " ".join("%03d" % c for c in codes),
        ])
    return _csv(rows)


def tickets(conn, cfg):
    rows = [["奖票号码", "持有人", "学号", "身份", "来源", "发出时间", "状态"]]
    for r in conn.execute(
        "SELECT t.code, t.kind, t.assigned_at, t.released_at, s.name, s.sid, s.role "
        "FROM tickets t JOIN students s ON s.id=t.student_id ORDER BY t.code"
    ):
        rows.append([
            "%03d" % r["code"], r["name"], r["sid"], _role(cfg).get(r["role"], r["role"]),
            KIND_LABEL.get(r["kind"], r["kind"]),
            _clock(cfg, r["assigned_at"]),
            "已作废" if r["released_at"] else "有效",
        ])
    return _csv(rows)


def draws(conn, cfg):
    rows = [["序号", "奖品", "抽取方式", "奖票号码", "得主", "学号", "身份", "时间", "状态"]]
    for r in conn.execute(
        "SELECT d.*, s.name, s.sid, s.role FROM draws d "
        "LEFT JOIN students s ON s.id=d.student_id ORDER BY d.seq"
    ):
        person = r["mode"] == "person"
        rows.append([
            r["seq"], r["prize"] or "",
            "到场均等" if person else "按票",
            "" if person or r["code"] is None else "%03d" % r["code"],
            r["name"] or "", r["sid"] or "",
            _role(cfg).get(r["role"], r["role"] or ""),
            _clock(cfg, r["ts"]),
            "已作废" if r["voided_at"] else "有效",
        ])
    return _csv(rows)


def winners(conn, cfg):
    """One row per person: with ticket-level draws a person can win repeatedly."""
    bucket = {}
    for r in conn.execute(
        "SELECT d.prize, d.mode, d.code, d.ts, d.student_id, s.name, s.sid, s.role "
        "FROM draws d LEFT JOIN students s ON s.id=d.student_id "
        "WHERE d.voided_at IS NULL ORDER BY d.seq"
    ):
        key = r["student_id"] or -1
        row = bucket.setdefault(key, {
            "name": r["name"] or "", "sid": r["sid"] or "",
            "role": _role(cfg).get(r["role"], r["role"] or ""), "won": [],
        })
        label = r["prize"] or ""
        if r["mode"] != "person" and r["code"] is not None:
            label += " #%03d" % r["code"]
        row["won"].append(label)
    rows = [["姓名", "学号", "身份", "中奖次数", "中奖明细"]]
    for row in sorted(bucket.values(), key=lambda x: -len(x["won"])):
        rows.append([row["name"], row["sid"], row["role"], len(row["won"]), "；".join(row["won"])])
    return _csv(rows)


def audit_log(conn, cfg, limit=5000):
    rows = [["时间", "动作", "对象", "内容"]]
    for r in conn.execute(
        "SELECT a.*, s.name FROM audit a LEFT JOIN students s ON s.id=a.student_id "
        "ORDER BY a.id LIMIT ?", (limit,)
    ):
        who = r["name"] or ""
        if r["kind"]:
            who = (who + " " + KIND_LABEL.get(r["kind"], r["kind"])).strip()
        rows.append([_clock(cfg, r["ts"]), r["action"], who, r["detail"] or ""])
    return _csv(rows)


def build(conn, cfg=None, students=None):
    """Return (filename, zip bytes)."""
    cfg = cfg or db.all_config(conn)
    students = students if students is not None else model.snapshot(conn, cfg)
    parts = [
        ("01-汇总.csv", summary(conn, cfg, students)),
        ("02-名单.csv", roster(conn, cfg, students)),
        ("03-奖票明细.csv", tickets(conn, cfg)),
        ("04-抽奖记录.csv", draws(conn, cfg)),
        ("05-中奖汇总.csv", winners(conn, cfg)),
        ("06-操作日志.csv", audit_log(conn, cfg)),
    ]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts:
            zf.writestr(name, text.encode("utf-8-sig"))
    return "lottery-export-%s.zip" % stamp, buf.getvalue()
