#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick roster summary straight from the database (no personal data dumped).

    python3 tools/roster_summary.py [db_path]
"""
import collections
import os
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "data/events/1.sqlite3"
if not os.path.exists(path):
    raise SystemExit("no database at %s" % path)

conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT sid, name, grp, py, pyi FROM students").fetchall()
print("名单人数:", len(rows))

by_grp = collections.Counter((r["grp"] or "(未填)") for r in rows)
print("院系分布:")
for grp, n in by_grp.most_common():
    print("   %-16s %d" % (grp, n))

no_py = [r for r in rows if not r["py"]]
print("缺拼音:", len(no_py))

lens = collections.Counter(len(r["sid"]) for r in rows)
print("学号长度分布（本科 10 位 / 研究生 9 位）:", dict(sorted(lens.items())))
odd = [r["sid"] + " " + r["name"] for r in rows if not (6 <= len(r["sid"]) <= 16)]
if odd:
    print("非 10 位学号:")
    for item in odd:
        print("   ", item)

marks = conn.execute("SELECT kind, COUNT(*) n FROM marks GROUP BY kind").fetchall()
live = conn.execute("SELECT COUNT(*) c FROM tickets WHERE released_at IS NULL").fetchone()["c"]
draws = conn.execute("SELECT COUNT(*) c FROM draws WHERE voided_at IS NULL").fetchone()["c"]
print("签到/任务:", {r["kind"]: r["n"] for r in marks} or "无")
print("有效奖票:", live, " 抽奖记录:", draws)
