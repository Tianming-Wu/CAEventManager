# -*- coding: utf-8 -*-
"""Post-event read-only review of a finished instance.

    python3 tools/event_review.py

Prints only aggregates: how many prizes were drawn versus configured, who got
drawn more than once, and whether any task-2 record exists without task-1.
"""
import collections
import json
import os
import sqlite3
import time

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(here)

cfg = json.load(open("config.json", encoding="utf-8"))
conn = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "data/events/1/data.sqlite3")
conn.row_factory = sqlite3.Row

off = 8 * 3600


def clock(ts):
    return time.strftime("%m-%d %H:%M:%S", time.gmtime(float(ts) + off)) if ts else ""


print("=== 奖品：配置 vs 实际 ===")
conf = json.loads(
    conn.execute("select value from config where key='prizes'").fetchone()[0]
)
drawn = collections.Counter()
for r in conn.execute("select prize, mode, count(*) n from draws where voided_at is null group by prize, mode"):
    drawn[(r["prize"], r["mode"])] = r["n"]
for p in conf:
    key = (p["name"], p["mode"])
    print("  %-8s 配置 %-3d  实抽 %-3d  %s"
          % (p["name"], p["count"], drawn.get(key, 0),
             "" if drawn.get(key, 0) == p["count"] else "  <-- 不一致"))

voided = conn.execute("select count(*) from draws where voided_at is not null").fetchone()[0]
print("  作废记录:", voided)

print()
print("=== 重复中奖 ===")
by_person = collections.defaultdict(list)
for r in conn.execute(
    "select d.seq, d.prize, d.mode, d.code, s.name, s.sid, s.role "
    "from draws d left join students s on s.id=d.student_id "
    "where d.voided_at is null order by d.seq"
):
    by_person[(r["name"], r["sid"])].append(r)

repeat = {k: v for k, v in by_person.items() if len(v) > 1}
print("  中奖人数 %d，其中重复中奖 %d 人" % (len(by_person), len(repeat)))
for (name, sid), rows in sorted(repeat.items(), key=lambda x: -len(x[1])):
    print("   %-6s %-11s %d 次: %s" % (
        name, sid, len(rows),
        "、".join("%s%s" % (r["prize"], "" if r["mode"] == "person" else " #%03d" % r["code"])
                  for r in rows)))

print()
print("=== 角色与到场 ===")
for role, n in conn.execute("select role, count(*) from students group by role"):
    print("  %-12s %d" % (role, n))
present = conn.execute(
    "select s.role, count(*) from marks m join students s on s.id=m.student_id "
    "where m.kind='checkin' group by s.role"
).fetchall()
print("  到场:", {r[0]: r[1] for r in present})

print()
print("=== 任务链完整性 ===")
broken = conn.execute(
    "select count(*) from marks t2 where t2.kind='task2' and not exists ("
    "  select 1 from marks t1 where t1.student_id=t2.student_id and t1.kind='task1')"
).fetchone()[0]
print("  有任务二但无任务一:", broken)
t1 = conn.execute("select count(*) from marks where kind='task1'").fetchone()[0]
t2 = conn.execute("select count(*) from marks where kind='task2'").fetchone()[0]
print("  完成任务一 %d 人，完成任务二 %d 人" % (t1, t2))

print()
print("=== 时间线 ===")
first = conn.execute("select min(ts) from audit where action='mark'").fetchone()[0]
last = conn.execute("select max(ts) from audit").fetchone()[0]
print("  首次标记", clock(first), " 最后动作", clock(last))
print("  结算:", "已结算" if json.loads(
    conn.execute("select value from config where key='finalized'").fetchone()[0]) else "未结算")
tickets = conn.execute("select count(*) from tickets where released_at is null").fetchone()[0]
print("  有效奖票:", tickets)
