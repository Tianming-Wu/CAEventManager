#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simulate a realistic event and show what the bonus maths actually does.

    python3 tools/simulate.py [--students 60] [--pool 200] [--showup 0.85]

Uses its own throw-away database (in a temp dir) so it never touches real data.
Prints, for several beta values, how the leftover tickets get spread.
"""
import argparse
import os
import random
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="lottery-sim-")
os.environ["LOTTERY_DATA_DIR"] = TMP

import db  # noqa: E402
import model  # noqa: E402


def build(args):
    db.DB_PATH = os.path.join(TMP, "sim.sqlite3")
    db.init_db()
    conn = db.connect()
    rng = random.Random(args.seed)

    names = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴",
             "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗"]
    given = ["伟", "芳", "娜", "敏", "静", "磊", "洋", "勇", "艳", "杰",
             "涛", "明", "超", "秀英", "霞", "平", "刚", "桂英", "文", "辉"]
    sid_list = []
    with db.tx(conn):
        for i in range(args.students):
            sid = "2024%03d" % (i + 1)
            name = rng.choice(names) + rng.choice(given)
            model.add_student(conn, sid, name, grp="计科24%02d" % (i // 30 + 1))
            sid_list.append(sid)

    students = {s["sid"]: s["id"] for s in model.snapshot(conn)}
    start = time.time() - 3600
    present = [sid for sid in sid_list if rng.random() < args.showup]
    rng.shuffle(present)

    with db.tx(conn):
        t = start
        for sid in present:
            t += rng.expovariate(1 / args.checkin_gap)
            model.mark(conn, students[sid], "checkin", at=t)
        # task one: everyone present eventually (some give up)
        t1_done = []
        for sid in present:
            if rng.random() > args.task_drop:
                base = start + 15 * 60 + rng.expovariate(1 / args.task_gap)
                model.mark(conn, students[sid], "task1", at=base)
                t1_done.append((base, sid))
        # task two: a fraction of those who finished task one
        for base, sid in t1_done:
            if rng.random() < args.task2_rate:
                model.mark(conn, students[sid], "task2", at=base + rng.expovariate(1 / args.task_gap))
    return conn


def show(conn, args):
    cfg = db.all_config(conn)
    students = model.snapshot(conn, cfg)
    st = model.stats(conn, cfg, students)
    print()
    print("模拟设定")
    print("  报名 %d 人，到场 %d 人 (%.0f%%)，奖票池 %d 张 (%s)"
          % (st["total"], st["checkin"], 100.0 * st["checkin"] / max(1, st["total"]),
             st["pool_size"],
             "自动 = %d×3+%d" % (st["checkin"], st["bonus_constant"])
             if st["pool_mode"] == "auto" else "固定"))
    print("  完成任务一 %d 人，完成任务二 %d 人" % (st["task1"], st["task2"]))
    print("  保底发出 %d 张，剩余可分配 %d 张" % (st["guaranteed"], st["bonus_pool"]))
    print()
    head = "  %-6s %-7s %-8s %-8s %s" % ("beta", "最多的人", "拿到的", "没拿到", "分布 (+n 张的人数)")
    print(head)
    print("  " + "-" * (len(head) + 6))
    for beta in args.betas:
        db.set_config_raw(conn, "beta", beta)
        cfg = db.all_config(conn)
        plan = model.plan_bonus(conn, cfg, students)
        rows = plan["rows"]
        if not rows:
            continue
        hist = {}
        for r in rows:
            hist[r["bonus"]] = hist.get(r["bonus"], 0) + 1
        best = rows[0]["bonus"]
        none = hist.get(0, 0)
        dist = "  ".join("+%d:%d" % (k, hist[k]) for k in sorted(hist))
        print("  %-6.1f %-9s %-10s %-10s %s" % (
            beta,
            "%d 张" % best,
            "%d 人" % hist.get(best, 0),
            "%d 人" % none,
            dist,
        ))
    print()

    db.set_config_raw(conn, "beta", args.report_beta)
    cfg = db.all_config(conn)
    plan = model.plan_bonus(conn, cfg, students)
    rows = plan["rows"]
    print("beta = %.1f 时的前 8 名 / 后 3 名 (期望额外票 vs 实得)" % args.report_beta)
    for label, chunk in (("头部", rows[:8]), ("尾部", rows[-3:])):
        print("  %s:" % label)
        for r in chunk:
            print("    #%-2d %-8s 完成 %d 个任务  平均名次分 %.3f  权重 %.2f  期望 %+.2f  实得 %+d"
                  % (r["pos"], r["name"], r["tasks"], r["mean_rank"], r["weight"], r["expected"], r["bonus"]))
    print()
    total = sum(r["bonus"] for r in rows)
    print("  校验：实得合计 %d 张 == 剩余池 %d 张 -> %s"
          % (total, plan["summary"]["bonus_pool"], "OK" if total == plan["summary"]["bonus_pool"] else "MISMATCH"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=60)
    ap.add_argument("--constant", type=int, default=20, help="auto 模式的额外奖票常数")
    ap.add_argument("--pool", type=int, default=None, help="指定则用固定池模式")
    ap.add_argument("--showup", type=float, default=0.85)
    ap.add_argument("--task-drop", type=float, default=0.1, help="放弃任务一的比例")
    ap.add_argument("--task2-rate", type=float, default=0.7, help="完成一之后继续任务二的比例")
    ap.add_argument("--checkin-gap", type=float, default=90.0, help="签到平均间隔(秒)")
    ap.add_argument("--task-gap", type=float, default=240.0, help="任务平均耗时(秒)")
    ap.add_argument("--betas", type=float, nargs="*", default=[0.0, 0.5, 1.0, 1.5, 2.0, 3.0])
    ap.add_argument("--report-beta", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    try:
        conn = build(args)
        if args.pool:
            db.set_config_raw(conn, "pool_mode", "fixed")
            db.set_config_raw(conn, "pool_size", args.pool)
        else:
            db.set_config_raw(conn, "pool_mode", "auto")
            db.set_config_raw(conn, "bonus_constant", args.constant)
        show(conn, args)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
