# -*- coding: utf-8 -*-
"""两个模块并跑：讲座活动的页签、动作、公示页、导出，与抽奖活动互不影响。"""
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tmp", "t-modules")
failures = []


def check(name, ok, detail=""):
    if ok:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + ("  " + str(detail) if detail else ""))
        failures.append(name)


shutil.rmtree(DATA, ignore_errors=True)
os.makedirs(DATA, exist_ok=True)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()
BASE = "http://127.0.0.1:%d" % PORT

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
env["LOTTERY_DATA_DIR"] = os.path.join(DATA, "data")
theme = os.path.join(DATA, "theme")
shutil.copytree(os.path.join(ROOT, "theme"), theme, dirs_exist_ok=True)
env["LOTTERY_THEME_DIR"] = theme
config = os.path.join(DATA, "config.json")
with open(config, "w", encoding="utf-8") as fh:
    json.dump({"port": PORT, "public_base_prefix": BASE}, fh)
env["LOTTERY_CONFIG"] = config
log = open(os.path.join(DATA, "server.log"), "wb")
proc = subprocess.Popen([sys.executable, "app.py", "--port", str(PORT)], cwd=ROOT,
                        env=env, stdout=log, stderr=subprocess.STDOUT)

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(path, body=None, method=None, expect=200):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Requested-With": "CAEventManager"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    try:
        with opener.open(req, timeout=20) as res:
            raw = res.read()
            status = res.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    if expect is not None and status != expect:
        raise SystemExit("%s -> %s（期望 %s）：%s"
                         % (path, status, expect, raw[:200]))
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return raw


try:
    for _ in range(80):
        try:
            with urllib.request.urlopen(BASE + "/login", timeout=2) as res:
                if b"loginForm" in res.read():
                    break
        except Exception:
            pass
        time.sleep(0.25)

    pw = [l for l in open(os.path.join(DATA, "data", "owner_password.txt"),
                          encoding="utf-8").read().splitlines()
          if l.startswith("password:")][0].split(":", 1)[1].strip()
    call("/api/panel/login", {"username": "owner", "password": pw})

    print("== 两个模块都在册 ==")
    mods = {m["id"]: m for m in call("/api/panel/modules")["modules"]}
    check("讲座模块被认出来", "nwuca.talk" in mods, sorted(mods))
    talk = mods.get("nwuca.talk") or {}
    check("它声明了自己的页签",
          [p["id"] for p in (talk.get("pages") or [])] == ["agenda", "questions", "config"],
          talk.get("pages"))
    check("页签名可以不是那五个默认 id",
          all(p["title"] for p in (talk.get("pages") or [])))
    check("抽奖模块还在", "nwuca.lottery" in mods)

    print("== 建一场讲座 ==")
    made = call("/api/panel/events/create",
                {"template": "nwuca.talk.salon", "title": "Python 入门分享"})
    ev = made["event"]
    token = ev["token"]
    base = "/" + token
    check("讲座活动建出来了", ev.get("kind") == "nwuca.talk", ev)

    state = call(base + "/api/admin/state")
    check("控制台拿到的是自己的数据键",
          "records" in state and state["records"] == [], sorted(state.keys()))
    check("议程数据在 state.data 里",
          isinstance(state.get("data"), dict)
          and isinstance(state["data"].get("agenda"), list), state.get("data"))
    check("新活动自带一节", len(state["data"]["agenda"]) == 1, state["data"]["agenda"])

    print("== 动作：加一节、开始、结束 ==")
    sec = state["data"]["agenda"][0]
    call(base + "/api/do/add_section", {"title": "第二段：环境与工具", "minutes": 20})
    state = call(base + "/api/admin/state")
    check("加了一节", len(state["data"]["agenda"]) == 2, state["data"]["agenda"])
    second = state["data"]["agenda"][1]
    call(base + "/api/do/start_section", {"id": second["id"]})
    state = call(base + "/api/admin/state")
    cur = [s for s in state["data"]["agenda"] if s["started_at"] and not s["ended_at"]]
    check("有一节在讲", len(cur) == 1 and cur[0]["id"] == second["id"], cur)
    check("统计跟着动", state["stats"]["current"] == "第二段：环境与工具", state["stats"])
    call(base + "/api/do/end_section", {})
    state = call(base + "/api/admin/state")
    check("结束了", state["stats"]["open_sections"] == 0, state["stats"])

    print("== 解锁之后才能删 ==")
    call(base + "/api/do/drop_section", {"id": sec["id"]}, expect=403)
    check("没解锁不能删小节", True)
    call(base + "/api/admin/unlock", {"on": True})
    call(base + "/api/do/drop_section", {"id": sec["id"]})
    call(base + "/api/admin/unlock", {"on": False})
    state = call(base + "/api/admin/state")
    check("解锁之后删掉了", len(state["data"]["agenda"]) == 1, state["data"]["agenda"])

    print("== 提问 ==")
    call(base + "/api/do/ask", {"who": "小林", "text": "虚拟环境要不要用 poetry？"})
    call(base + "/api/do/ask", {"text": "这门课有作业吗？"})
    state = call(base + "/api/admin/state")
    qs = state["data"]["questions"]
    check("两条提问都在", len(qs) == 2, qs)
    check("没写名字的算匿名", any(q["who"] in ("", "匿名") for q in qs), qs)
    first = qs[0]
    call(base + "/api/do/answer", {"id": first["id"]})
    state = call(base + "/api/admin/state")
    answered = [q for q in state["data"]["questions"] if q["answered_at"]]
    check("标记了已回答", len(answered) == 1, state["data"]["questions"])
    check("待回答数量对得上", state["stats"]["open_questions"] == 1, state["stats"])

    print("== 公开页：只申请了大屏 ==")
    call(base + "/", expect=404)          # 没申请 board：这一页不该存在
    check("没申请的公示页不存在", True)
    call(base + "/checkin", expect=404)
    check("没申请的登记页也不存在", True)
    screen = call(base + "/screen", expect=200)
    text = screen.decode("utf-8") if isinstance(screen, bytes) else str(screen)
    check("大屏能打开", "screen.css" in text or "screen.html" in text or "大屏" in text,
          text[:80])
    board = call(base + "/api/board")
    check("大屏块由模块给", isinstance(board["present"].get("screen"), dict),
          sorted((board["present"].get("screen") or {}).keys()))
    check("横幅里有已完成的节数",
          isinstance((board["present"]["screen"] or {}).get("banner"), list),
          (board["present"]["screen"] or {}).get("banner"))
    con = call(base + "/api/admin/console")
    check("控制台知道这个模块只申请了哪些公开页",
          con.get("external") == ["admin", "screen"], con.get("external"))

    print("== 名单是模块申请了才有的能力 ==")
    call(base + "/api/admin/rosters", expect=404)
    check("没申请的模块没有名单接口", True)

    print("== 导出：模块说有几张表 ==")
    blob = call(base + "/api/admin/export.zip", expect=200)
    names = sorted(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    check("导出的是这套玩法的表", names == ["01-议程.csv", "02-提问.csv"], names)
    csv = call(base + "/api/admin/export.csv", expect=200)
    check("仅名单 CSV 给的是议程那一张",
          "小节" in (csv.decode("utf-8-sig") if isinstance(csv, bytes) else str(csv)),
          (csv[:80] if isinstance(csv, bytes) else str(csv)[:80]))

    print("== 另一个模块不受影响 ==")
    lottery = call("/api/panel/events/create", {"template": "nwuca.salon"})
    loto_token = lottery["event"]["token"]
    loto = call("/" + loto_token + "/api/admin/state")
    check("抽奖活动仍然有 students 键", isinstance(loto.get("students"), list),
          sorted(loto.keys())[:8])
    check("抽奖的奖票表还在", isinstance(loto.get("prizes"), list))
    check("抽奖模块申请了名单，接口在",
          isinstance(call("/" + loto_token + "/api/admin/rosters").get("rosters"), list))
    check("抽奖模块申请了名单能力",
          "roster" in (loto.get("features") or {}), loto.get("features"))
    check("抽奖的控制台页签没变",
          [p["id"] for p in call("/" + loto_token + "/api/admin/console")["pages"]
           if not p.get("platform")] ==
          ["ops", "roster", "tickets", "draw", "config"])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    tail = ""
    try:
        with open(os.path.join(DATA, "server.log"), "rb") as fh:
            tail = fh.read().decode("utf-8", "replace")[-800:]
    except OSError:
        pass
    shutil.rmtree(DATA, ignore_errors=True)
    if failures:
        print(tail)
        print("MODULES TEST FAILED (%d): %s" % (len(failures), ", ".join(failures)))
        sys.exit(1)
    print("MODULES TEST PASSED")
