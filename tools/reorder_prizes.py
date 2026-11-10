# -*- coding: utf-8 -*-
"""Reorder the prize list on a running instance (data only, no deploy).

    python3 tools/reorder_prizes.py "三等奖,二等奖,一等奖,特等奖"

Keeps every existing count/mode, only changes the order — which is the order the
operator sees and therefore the order prizes get drawn in.
"""
import http.cookiejar
import json
import os
import sys
import urllib.request

ORDER = [x.strip() for x in (sys.argv[1] if len(sys.argv) > 1 else
                             "三等奖,二等奖,一等奖,特等奖").split(",") if x.strip()]

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(here)

cfg = json.load(open("config.json", encoding="utf-8"))
secret = cfg["path_secret"]
base = "http://127.0.0.1:%d/%s" % (int(cfg.get("port") or 8931), secret)
password = ""
for line in open("data/owner_password.txt", encoding="utf-8"):
    if line.startswith("password:"):
        password = line.split(":", 1)[1].strip()

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json", "X-Requested-With": "lottery"},
    )
    with opener.open(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


call("/api/login", {"user": "laffey", "pass": password, "cookie_path": "/%s/" % secret})
state = call("/api/admin/state")
current = {p["name"]: p for p in state["prizes"]}

ordered, missing = [], []
for name in ORDER:
    if name in current:
        p = current[name]
        ordered.append({"name": p["name"], "count": p["count"], "mode": p["mode"]})
    else:
        missing.append(name)
for name, p in current.items():
    if name not in ORDER:
        ordered.append({"name": p["name"], "count": p["count"], "mode": p["mode"]})

if missing:
    print("警告: 名单里没有这些奖品，已忽略:", ", ".join(missing))

call("/api/admin/settings", {"prizes": ordered})
after = call("/api/admin/state")["prizes"]
print("新的抽奖顺序:")
for i, p in enumerate(after, 1):
    print("  %d. %-8s %-7s 数量 %-3d 已抽 %d / 剩余 %d"
          % (i, p["name"], "均等" if p["mode"] == "person" else "按票",
             p["count"], p["drawn"], p["remaining"]))
