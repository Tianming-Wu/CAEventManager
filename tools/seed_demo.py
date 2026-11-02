#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed a running instance with a realistic 60-student event (for UI testing).

    python3 tools/seed_demo.py http://127.0.0.1:8931 OWNER_PASSWORD

Creates 60 students, staggers check-ins, and completes the two tasks for most of
them with plausible timing.  Safe to re-run (it updates by student id).

The console signs in with a panel account, so the password here is the owner's.
"""
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import _baseurl  # noqa: E402

BASE = _baseurl.api_base(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


PANEL = BASE.rsplit("/", 1)[0]


def call(path, body=None, method=None, panel=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Requested-With": "CAEventManager" if panel else "lottery"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        (PANEL if panel else BASE) + path, data=data, headers=headers,
        method=method or ("POST" if data else "GET")
    )
    with opener.open(req, timeout=30) as res:
        return json.loads(res.read().decode())


SURNAMES = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴",
            "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗"]
GIVEN = ["伟", "芳", "娜", "敏", "静", "磊", "洋", "勇", "艳", "杰",
         "涛", "明", "超", "秀英", "霞", "平", "刚", "桂英", "文", "辉",
         "子墨", "浩然", "思远", "雨欣", "嘉怡"]


def main():
    # one account for both surfaces: the console rides on the panel session
    owner = os.environ.get("LOTTERY_OWNER") or "owner"
    call("/api/panel/login", {"username": owner, "password": PASSWORD}, panel=True)
    rng = random.Random(20261030)

    rows = ["学号,姓名,班级,身份"]
    for i in range(60):
        rows.append("2024%03d,%s%s,计科24%02d,participant" % (
            i + 1, rng.choice(SURNAMES), rng.choice(GIVEN), i // 30 + 1))
    for i in range(5):
        rows.append("2025%03d,%s%s,工作人员,staff" % (
            i + 1, rng.choice(SURNAMES), rng.choice(GIVEN)))
    for i in range(3):
        rows.append("2026%03d,%s%s,志愿者,volunteer" % (
            i + 1, rng.choice(SURNAMES), rng.choice(GIVEN)))
    res = call("/api/admin/import", {"text": "\n".join(rows)})
    print("imported:", res["result"])

    state = call("/api/admin/state")
    students = state["students"]
    parts = [s["id"] for s in students if s["role"] == "participant"]
    crew = [s["id"] for s in students if s["role"] != "participant"]

    # the crew shows up but has no tasks and no tickets
    for sid in crew:
        call("/api/admin/mark", {"student_id": sid, "kind": "checkin"})

    present = rng.sample(parts, 52)
    now = time.time()
    plan = []
    t = now - 3600
    for sid in present:
        t += rng.expovariate(1 / 75.0)
        plan.append((sid, t))

    for sid, ts in plan:
        call("/api/admin/mark", {"student_id": sid, "kind": "checkin", "at": ts})

    task1 = []
    for sid, ts in plan:
        if rng.random() < 0.92:
            t1 = ts + 900 + rng.expovariate(1 / 300.0)
            call("/api/admin/mark", {"student_id": sid, "kind": "task1", "at": t1})
            task1.append((sid, t1))

    for sid, t1 in task1:
        if rng.random() < 0.78:
            call("/api/admin/mark", {"student_id": sid, "kind": "task2",
                                     "at": t1 + rng.expovariate(1 / 260.0)})

    state = call("/api/admin/state")
    print("stats:", json.dumps(state["stats"], ensure_ascii=False))
    print("sample student:", json.dumps(state["students"][0], ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
