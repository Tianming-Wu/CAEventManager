#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end smoke test for the lottery service.

    python3 tools/smoke_test.py http://127.0.0.1:8931 [password]

Uses a throw-away data dir if it has to create one, and always talks over HTTP
so it exercises the real routing/serialisation.  Exits non-zero on failure.
"""
import http.cookiejar
import io
import json
import os
import zipfile
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import _baseurl  # noqa: E402

# The service only answers under /<secret>/..., so resolve that first.
BASE = _baseurl.api_base(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931")

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
failures = []


def call(path, body=None, method=None, expect=200):
    url = BASE + path
    data = None
    headers = {"X-Requested-With": "lottery"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    try:
        with opener.open(req, timeout=20) as res:
            status = res.status
            raw = res.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    if status != expect:
        failures.append("%s %s -> %d (expected %d): %s" % (method or "GET", path, status, expect, raw[:200]))
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return raw


def post(path, body=None, expect=200):
    """POST with an optional body (call() would otherwise default to GET)."""
    return call(path, body if body is not None else {}, method="POST", expect=expect)


# The panel sits above the activity's address, and it is the only door now:
# the console signs in with a panel account and a seat on the activity.
PANEL = BASE.rsplit("/", 1)[0]


def panel_call(path, body=None, expect=200, method=None):
    url = PANEL + path
    data = None
    headers = {"X-Requested-With": "CAEventManager"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, headers=headers,
        method=method or ("POST" if data is not None else "GET"))
    try:
        with opener.open(req, timeout=20) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    if status != expect:
        failures.append("%s %s -> %d (expected %d): %s"
                        % (method or "GET", path, status, expect, raw[:200]))
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return raw


def console_login(username, password):
    return panel_call("/api/panel/login",
                      {"username": username, "password": password})


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        failures.append(name)


TOKEN = BASE.rsplit("/", 1)[-1]
BASE_ROOT = BASE.rsplit("/", 1)[0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def state_students(state):
    return state["students"]


def owner_credentials():
    """The panel account that owns this activity; the console rides on it now."""
    user = os.environ.get("LOTTERY_OWNER") or "owner"
    if len(sys.argv) > 2:
        return user, sys.argv[2]
    for name in ("owner_password.txt",):
        path = os.path.join(HERE, "data", name)
        if not os.path.exists(path):
            continue
        found = {}
        for line in open(path, encoding="utf-8"):
            key, _, value = line.partition(":")
            found[key.strip()] = value.strip()
        if found.get("password"):
            return found.get("username", user), found["password"]
    env = os.environ.get("LOTTERY_OWNER_PASSWORD")
    if env:
        return user, env
    raise SystemExit("no owner password: pass it as argv[2] or set LOTTERY_OWNER_PASSWORD")


def main():
    print("== routing: panel at the root, events under the token ==")
    import urllib.request as _ur
    root = BASE.rsplit("/", 1)[0]
    with _ur.urlopen(root + "/", timeout=5) as res:
        body = res.read()
    check("service root now serves the panel",
          res.status == 200 and "参加抽奖并不需要注册账户".encode() in body, res.status)
    for probe in ("/admin", "/screen", "/checkin", "/api/board", "/static/app.css"):
        try:
            _ur.urlopen(root + probe, timeout=5)
            check("event route needs the token (%s)" % probe, False, "got 200")
        except urllib.error.HTTPError as exc:
            check("event route needs the token (%s)" % probe, exc.code == 404, exc.code)

    print("== public endpoints ==")
    svc = call("/api/service")
    check("service now has a public read",
          isinstance(svc.get("items"), list) and bool(svc.get("version")), svc)
    check("it carries no instructions, only readings",
          all(set(i) == {"id", "kind", "level", "title", "body", "sticky"}
              for i in svc.get("items") or []), svc.get("items"))
    board = call("/api/board")
    check("board reaches server", board is not None and board.get("ok"))
    check("board has no secrets", board is None or "admin_pass" not in json.dumps(board))

    print("== auth ==")
    # The console has no password of its own any more: the panel account is the
    # only identity, and a seat on the activity is what opens the console.
    user, pw = owner_credentials()
    me = call("/api/me")
    check("not authed yet", me and me.get("authed") is False)
    call("/api/admin/state", expect=401)
    check("admin api guarded", True)
    panel_call("/api/panel/login", {"username": user, "password": "definitely-wrong"},
               expect=401)
    check("a bad panel password is rejected", True)
    call("/api/admin/state", expect=401)
    check("still no console without a session", True)
    console_login(user, pw)
    me = call("/api/me")
    check("authed after the panel login", me and me.get("authed") is True, me)
    check("the console knows who it is", me and me.get("user"), me)
    check("and that they run this activity", me and me.get("role") == "owner", me)
    post("/api/login", {"user": "laffey", "pass": "x"}, expect=410)
    check("the old console password is gone", True)

    print("== csrf ==")
    req = urllib.request.Request(
        BASE + "/api/admin/mark",
        data=json.dumps({"student_id": 1, "kind": "checkin"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        opener.open(req, timeout=10)
        check("post without marker rejected", False, "request succeeded")
    except urllib.error.HTTPError as exc:
        check("post without marker rejected", exc.code == 403, "code=%d" % exc.code)

    print("== roster import ==")
    # includes a 9-digit graduate id next to the short fixtures: the app must not
    # care about id length anywhere
    csv_text = "\n".join(
        ["学号,姓名,班级"]
        + ["900%d,测试%d,计科24" % (i, i) for i in range(1, 8)]
        + ["202622298,研究生,电子信息学院"]
    )
    res = post("/api/admin/import", {"text": csv_text})
    check("import added 8", res and res["result"]["added"] >= 8, res)

    state = call("/api/admin/state")
    students = {s["sid"]: s for s in state["students"]}
    ids = [students["900%d" % i]["id"] for i in range(1, 7)]
    check("pinyin generated", all(students["900%d" % i]["py"] for i in range(1, 7)))

    print("== marking + chain rules ==")
    post("/api/admin/mark", {"student_id": ids[0], "kind": "task1"}, expect=409)
    check("task1 blocked before checkin", True)
    for i, sid in enumerate(ids):
        post("/api/admin/mark", {"student_id": sid, "kind": "checkin"})
        time.sleep(0.02)
    state = call("/api/admin/state")
    check("6 checked in", state["stats"]["checkin"] == 6, state["stats"])
    dup = post("/api/admin/mark", {"student_id": ids[0], "kind": "checkin"})
    check("mark is idempotent", dup and dup["result"]["already"] is True)

    # everyone does task1; the first three also do task2 (so ranking is interesting)
    for sid in ids:
        post("/api/admin/mark", {"student_id": sid, "kind": "task1"})
        time.sleep(0.02)
    for sid in ids[:3]:
        post("/api/admin/mark", {"student_id": sid, "kind": "task2"})
        time.sleep(0.02)

    state = call("/api/admin/state")
    tickets = sum(len(s["tickets"]) for s in state["students"])
    check("guaranteed tickets issued", tickets == 6 + 6 + 3, tickets)

    print("== undo needs unlock ==")
    target = students["9001"]["id"]
    post("/api/admin/undo", {"student_id": target, "kind": "task2"}, expect=403)
    check("undo blocked while locked", True)
    call("/api/admin/unlock", {"on": True})
    res = post("/api/admin/undo", {"student_id": target, "kind": "task2", "cascade": True})
    check("undo works when unlocked", res and res["ok"], res)
    post("/api/admin/mark", {"student_id": target, "kind": "task2"})
    post("/api/admin/unlock", {"on": False})
    post("/api/admin/undo", {"student_id": target, "kind": "task2"}, expect=403)
    check("undo re-locked after switch off", True)

    print("== bonus maths ==")
    post("/api/admin/settings", {"pool_size": 200, "beta": 1.0, "allow_bonus": True})
    plan = call("/api/admin/plan")
    rows = plan["rows"]
    check("plan ranks every present student", len(rows) == 6, len(rows))
    check("plan sums to bonus pool", sum(r["bonus"] for r in rows) == plan["summary"]["bonus_pool"],
          (sum(r["bonus"] for r in rows), plan["summary"]["bonus_pool"]))
    check("faster finisher outranks slower", rows[0]["tasks"] >= rows[-1]["tasks"])
    check("first weight = 1+beta", abs(rows[0]["weight"] - 2.0) < 1e-6, rows[0]["weight"])
    top, bottom = rows[0]["expected"], rows[-1]["expected"]
    check("expected bonus is monotonic", top >= bottom, (top, bottom))

    print("== person draw (特等奖) ==")
    present_ids = {s["id"] for s in state_students(state) if s["marks"]["checkin"]}
    live_before = sum(len(s["tickets"]) for s in state_students(state))
    pd = post("/api/admin/draw", {"prize": "特等奖", "mode": "person"})
    check("person draw works without finalizing", pd is not None, pd)
    if pd:
        w = pd["draw"]
        check("person draw picks a student, not a ticket",
              w["mode"] == "person" and w["code"] is None, w)
        check("person draw winner is present", w["student_id"] in present_ids, w)
        check("person draw reports the pool size", w["candidates"] == len(present_ids), w)
    live_after = sum(len(s["tickets"]) for s in state_students(state))
    check("person draw consumes no ticket", live_after == live_before,
          "%s -> %s" % (live_before, live_after))
    # 特等奖 is configured with count 1: the quota must stop a second draw
    post("/api/admin/draw", {"prize": "特等奖", "mode": "person"}, expect=409)
    check("person draw honours the configured quota", True)

    # no-repeat rule, on an ad-hoc prize so the quota cannot interfere
    a = post("/api/admin/draw", {"prize": "临时奖", "mode": "person"})
    b = post("/api/admin/draw", {"prize": "临时奖", "mode": "person"})
    check("a person cannot win the person draw twice",
          a and b and a["draw"]["student_id"] != b["draw"]["student_id"], (a, b))

    print("== prize configuration ==")
    st = call("/api/admin/state")
    conf = {p["name"]: p for p in st["prizes"]}
    check("default prizes match the plan's counts",
          conf.get("特等奖", {}).get("count") == 1
          and conf.get("一等奖", {}).get("count") == 2
          and conf.get("二等奖", {}).get("count") == 5
          and conf.get("三等奖", {}).get("count") == 10, conf)
    check("特等奖 is a person draw", conf["特等奖"]["mode"] == "person", conf["特等奖"])
    check("特等奖 shows as drawn once", conf["特等奖"]["drawn"] == 1, conf["特等奖"])
    check("quota grows for multi-unit prizes",
          conf["二等奖"]["count"] - conf["二等奖"]["drawn"] == 5, conf["二等奖"])

    print("== finalize / draw ==")
    fin = post("/api/admin/finalize")
    check("finalize assigned bonus tickets", fin and fin["result"]["assigned"] == plan["summary"]["bonus_pool"], fin)
    post("/api/admin/finalize", expect=409)
    check("double finalize rejected", True)
    state = call("/api/admin/state")
    live = sum(len(s["tickets"]) for s in state["students"])
    check("live tickets == pool guaranteed + bonus", live == 6 + 6 + 3 + plan["summary"]["bonus_pool"], live)
    codes = [t["code"] for s in state["students"] for t in s["tickets"]]
    check("ticket codes unique", len(codes) == len(set(codes)))
    check("ticket codes in range", all(1 <= c <= 200 for c in codes))

    draws_before = call("/api/admin/state")["draws"]
    draw = post("/api/admin/draw", {"prize": "一等奖"})
    check("draw returns a ticket", draw and draw["draw"]["code"] in codes, draw)
    # 一等奖 has 2 units: one more must work, a third must be refused
    second = post("/api/admin/draw", {"prize": "一等奖", "mode": "ticket"})
    check("a multi-unit ticket prize can be drawn again",
          second and second["draw"]["prize"] == "一等奖", second)
    post("/api/admin/draw", {"prize": "一等奖", "mode": "ticket"}, expect=409)
    check("ticket prize quota is enforced", True)
    st = call("/api/admin/state")
    one = [p for p in st["prizes"] if p["name"] == "一等奖"][0]
    check("drawn counter catches up", one["drawn"] == 2 and one["remaining"] == 0, one)

    board = call("/api/board")
    modes = sorted(w["mode"] for w in (board["winners"] if board else []))
    check("board exposes every winner", len(modes) == 5, modes)
    check("board keeps both draw modes apart",
          modes == ["person", "person", "person", "ticket", "ticket"], modes)
    check("board hides student ids by default", "sid" not in board["students"][0])
    check("board lists participants only",
          all((x.get("role") or "participant") == "participant" for x in board["students"]),
          [x.get("role") for x in board["students"]])

    print("== settings guards ==")
    live = 6 + 6 + 3 + plan["summary"]["bonus_pool"]
    post("/api/admin/settings", {"pool_mode": "fixed", "pool_size": live - 1}, expect=400)
    check("cannot shrink the pool below issued tickets", True)
    res = post("/api/admin/settings", {"pool_mode": "fixed", "pool_size": live + 17})
    check("can grow the pool any time", res and res["config"]["pool_size"] == live + 17, res)
    res = post("/api/admin/settings", {"pool_mode": "fixed", "pool_size": live})
    check("can shrink back down to exactly what was issued", res is not None)
    st = call("/api/admin/state")["stats"]
    check("tickets survive a resize", st["live_tickets"] == live, st["live_tickets"])
    post("/api/admin/settings", {"pool_mode": "auto", "bonus_constant": 20})
    st = call("/api/admin/state")["stats"]
    check("auto pool = present*3 + constant", st["pool_size"] == 6 * 3 + 20, st["pool_size"])
    post("/api/admin/settings", {"beta": 9}, expect=400)
    check("beta range enforced", True)

    print("== export ==")
    raw = call("/api/admin/export.csv")
    check("csv exported", isinstance(raw, bytes) and "学号".encode() in raw)

    raw = call("/api/admin/export.zip")
    check("zip exported", isinstance(raw, bytes) and raw[:2] == b"PK",
          raw[:8] if isinstance(raw, bytes) else raw)
    book = zipfile.ZipFile(io.BytesIO(raw))
    names = book.namelist()
    check("zip carries every sheet", len(names) == 6, names)
    for want in ("汇总", "名单", "奖票明细", "抽奖记录", "中奖汇总", "操作日志"):
        check("sheet present: " + want, any(want in n for n in names), names)

    text = book.read("05-中奖汇总.csv").decode("utf-8-sig")
    check("winners sheet aggregates per person", "中奖次数" in text, text.splitlines()[0])
    text = book.read("04-抽奖记录.csv").decode("utf-8-sig")
    check("draw log keeps both modes", "到场均等" in text and "按票" in text, text[:80])
    text = book.read("02-名单.csv").decode("utf-8-sig")
    check("roster sheet has an identity column", "身份" in text.splitlines()[0],
          text.splitlines()[0])

    print("== screen token + self check-in ==")
    tok = call("/api/screen/token")
    check("screen token needs no login", tok and len(tok.get("token") or "") >= 8, tok)
    st = call("/api/checkin/status")
    check("check-in status is reachable", st and st.get("enabled") is True, st)
    check("no presence before scanning", st.get("verified") is False, st)
    post("/api/checkin/submit", {"sid": "9007", "name": "测试7"}, expect=403)
    check("submit without a scan is refused", True)

    call("/checkin?t=deadbeefdeadbeef", expect=200)
    check("bogus token grants nothing", call("/api/checkin/status")["verified"] is False)
    call("/checkin?t=" + (tok or {}).get("token", ""), expect=200)
    check("fresh token grants presence", call("/api/checkin/status")["verified"] is True)

    post("/api/checkin/submit", {"sid": "9007", "name": "对不上"}, expect=404)
    check("wrong name is rejected without hints", True)
    post("/api/checkin/submit", {"sid": "9999999", "name": "测试7"}, expect=404)
    check("unknown id is rejected the same way", True)

    # The event has already been finalised by now: a late arrival must be told
    # to un-finalise rather than silently squeezing a ticket out of a settled pool.
    post("/api/admin/unlock", {"on": True})
    late = post("/api/checkin/submit", {"sid": "9007", "name": "测试7"}, expect=409)
    check("late check-in after finalize is refused with a hint",
          call("/api/checkin/status")["verified"] is True)

    post("/api/admin/unfinalize")
    check("un-finalise allowed while unlocked", True)
    res = post("/api/checkin/submit", {"sid": "9007", "name": "测试7"})
    check("self check-in succeeds after un-finalising", res and res.get("name") == "测试7", res)
    check("self check-in issues a ticket", res and len(res["tickets"]) == 1, res)
    check("self check-in reports the door position", res and res["position"] == 7, res)
    again = post("/api/checkin/submit", {"sid": "9007", "name": "测试7"})
    check("re-scanning is idempotent", again and again["already"] is True
          and len(again["tickets"]) == 1, again)

    # a graduate id (9 digits) must go through the same self check-in path
    grad = post("/api/checkin/submit", {"sid": "202622298", "name": "研究生"})
    check("a 9-digit graduate id can self check-in",
          grad and grad.get("sid") == "202622298", grad)
    check("the graduate id keeps its exact width",
          grad and len(grad["sid"]) == 9, grad and grad["sid"])
    check("a graduate check-in issues a ticket", grad and len(grad["tickets"]) == 1, grad)

    print("== roles: crew shows up but earns nothing ==")
    st = call("/api/admin/state")
    add = post("/api/admin/student", {"sid": "2025000001", "name": "工作人员甲",
                                      "grp": "会务", "role": "staff"})
    check("staff record created", add and add["ok"], add)
    crew = [x for x in call("/api/admin/state")["students"] if x["sid"] == "2025000001"][0]
    check("role is reported back", crew["role"] == "staff", crew["role"])

    res = post("/api/admin/mark", {"student_id": crew["id"], "kind": "checkin"})
    check("staff can be marked present", res and res["ok"], res)
    check("staff gets no ticket", res["result"]["code"] is None, res["result"])

    post("/api/admin/mark", {"student_id": crew["id"], "kind": "task1"}, expect=409)
    check("staff cannot be marked for a task", True)

    st = call("/api/admin/state")["stats"]
    check("staff presence is counted separately",
          st["staff_present"] == 1 and st["checkin"] == 8, st)
    check("grand-prize pool includes staff",
          st["present_all"] == st["checkin"] + st["staff_present"], st)
    check("participants and staff add up to the roster",
          st["participants"] + st["staff"] == st["total"], st)
    check("the ticket pool only counts participants",
          st["pool_size"] == st["checkin"] * 3 + st["bonus_constant"], st)

    before = call("/api/admin/plan")["summary"]["present"]
    check("bonus ranking ignores staff", before == st["checkin"], (before, st["checkin"]))

    print("== 短链：平台级的转发表，按活动记 ==")
    # 短链在面板族：活动成员生成、列自己的、删自己的
    events = panel_call("/api/panel/events") or {}
    seq = (events.get("events") or [{}])[0].get("seq") or 0
    made = panel_call("/api/panel/shortlink",
                      {"seq": seq, "target": "screen", "minutes": 30, "max_hits": 2})
    check("短链由平台生成", bool(made and made.get("ok")), made)
    code = ((made or {}).get("link") or {}).get("code") or ""
    check("短链号是 7 位", len(code) == 7, code)
    check("短链号全大写", code == code.upper(), code)
    check("短链地址是 /s/<code>", ((made or {}).get("url") or "").endswith("/s/" + code),
          (made or {}).get("url"))
    check("短链地址里没有活动 token", TOKEN not in ((made or {}).get("url") or ""),
          (made or {}).get("url"))

    no_redirect = urllib.request.build_opener(_NoRedirect)
    try:
        no_redirect.open(BASE_ROOT + "/s/" + code, timeout=10)
        check("短链会跳转", False, "no redirect")
    except urllib.error.HTTPError as exc:
        loc = exc.headers.get("Location") or ""
        check("短链会跳转", exc.code == 302, exc.code)
        check("跳到该活动的目标页", loc.endswith("/screen") and TOKEN in loc, loc)

    # 短链号不区分大小写：小写地址一样能跳
    try:
        no_redirect.open(BASE_ROOT + "/s/" + code.lower(), timeout=10)
        check("小写也能跳", False, "no redirect")
    except urllib.error.HTTPError as exc:
        check("小写也能跳", exc.code == 302, exc.code)

    # 模块可以指向活动范围内的任意路径
    deep = panel_call("/api/panel/shortlink",
                      {"seq": seq, "target": "admin#/ops", "minutes": 5, "max_hits": 1}) or {}
    check("目标可以是任意相对路径", bool(deep.get("ok")), deep)
    try:
        no_redirect.open(BASE_ROOT + "/s/" + (deep.get("link") or {}).get("code", ""), timeout=10)
        check("深层目标也能跳", False, "no redirect")
    except urllib.error.HTTPError as exc:
        check("深层目标也能跳", exc.code == 302
              and (exc.headers.get("Location") or "").endswith("/admin#/ops"), exc.code)
    # 400：面板接口拒绝，不带协议地址
    bad = panel_call("/api/panel/shortlink",
                     {"seq": seq, "target": "https://evil.example/x", "minutes": 5},
                     expect=400) or {}
    check("带协议的绝对地址被拒", bool(bad.get("error")), bad)

    listed = panel_call("/api/panel/shortlink?seq=%d" % seq) or {}
    check("列表里能看到它",
          any(l["code"] == code for l in listed.get("links") or []), listed)

    for _ in range(2):
        try:
            no_redirect.open(BASE_ROOT + "/s/" + code, timeout=10)
        except urllib.error.HTTPError:
            pass
    try:
        no_redirect.open(BASE_ROOT + "/s/" + code, timeout=10)
        check("次数用尽后不再跳转", False, "still redirecting")
    except urllib.error.HTTPError as exc:
        check("次数用尽后不再跳转", exc.code == 404, exc.code)

    deleted = panel_call("/api/panel/shortlink/delete", {"code": code}) or {}
    check("活动能删自己的短链", deleted.get("code") == code, deleted)
    try:
        no_redirect.open(BASE_ROOT + "/s/" + code, timeout=10)
        check("删除即失效", False, "still works")
    except urllib.error.HTTPError as exc:
        check("删除即失效", exc.code == 404, exc.code)

    try:
        no_redirect.open(BASE_ROOT + "/s/ZZZZZZZ", timeout=10)
        check("不存在的短链是 404", False, "accepted")
    except urllib.error.HTTPError as exc:
        check("不存在的短链是 404", exc.code == 404, exc.code)

    print("== accounting invariants ==")
    # turning a ticket holder into staff must take the ticket back
    st = call("/api/admin/state")
    holder = [x for x in st["students"]
              if (x.get("role") or "participant") == "participant" and x["tickets"]][0]
    before_live = st["stats"]["live_tickets"]
    post("/api/admin/unlock", {"on": True})
    post("/api/admin/student", {"id": holder["id"], "role": "staff"})
    st2 = call("/api/admin/state")
    after = [x for x in st2["students"] if x["id"] == holder["id"]][0]
    check("role change to staff revokes the tickets", after["tickets"] == [], after["tickets"])
    check("the pool follows", st2["stats"]["live_tickets"] == before_live - len(holder["tickets"]),
          (before_live, st2["stats"]["live_tickets"]))
    post("/api/admin/settings", {"pool_mode": "auto"})
    plan = call("/api/admin/plan")["summary"]
    check("issued + bonus equals the pool",
          plan["live_tickets"] + plan["bonus_pool"] == plan["pool_size"], plan)
    post("/api/admin/student", {"id": holder["id"], "role": "participant"})

    print("== screen phase ==")
    res = post("/api/admin/phase", {"phase": "draw"})
    check("phase can switch to draw", res and res["phase"] == "draw", res)
    check("public board reflects the phase",
          call("/api/board")["config"]["screen_phase"] == "draw")
    post("/api/admin/phase", {"phase": "signin"})
    check("phase can switch back", call("/api/board")["config"]["screen_phase"] == "signin")
    post("/api/admin/phase", {"phase": "nonsense"}, expect=400)
    check("invalid phase rejected", True)

    print("== the projector reads cheaply, and hears about changes ==")
    board_req = urllib.request.Request(BASE + "/api/board")
    with opener.open(board_req, timeout=20) as res:
        tag = res.headers.get("ETag")
        full = res.read()
    check("the board carries an ETag", bool(tag), tag)
    again = urllib.request.Request(BASE + "/api/board", headers={"If-None-Match": tag})
    try:
        with opener.open(again, timeout=20) as res:
            status, body = res.status, res.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    check("an unchanged board answers 304", status == 304, status)
    check("and re-sends nothing", not body and len(full) > 500, (len(body), len(full)))

    stream = opener.open(BASE + "/api/stream", timeout=20)
    check("the event stream speaks text/event-stream",
          "text/event-stream" in (stream.headers.get("Content-Type") or ""),
          stream.headers.get("Content-Type"))
    first = stream.readline().decode("utf-8").strip()
    check("it opens with the current revision map",
          first.startswith("data: ") and "revs" in first, first)
    stream.close()

    print("== 活动级 sudo：30 秒、只对本活动 ==")
    post("/api/sudo/exit", {})
    # delete_student 在模块里声明了 sudo：没提权时应被拦下
    victim = post("/api/do/add_student", {"sid": "2026SUDO1", "name": "提权测试"})
    vid = ((victim or {}).get("student") or {}).get("id")
    if vid:
        post("/api/admin/unlock", {"on": True})
        blocked = post("/api/do/delete_student", {"id": vid}, expect=403)
        check("要提权的动作先被拦下",
              bool(blocked and blocked.get("sudoRequired")), blocked)
        wrong = post("/api/sudo", {"password": "nope"}, expect=403)
        check("密码不对不给提权", wrong is None or wrong.get("ok") is not True, wrong)
        done = post("/api/sudo", {"password": owner_credentials()[1]})
        check("密码对了就能提权 30 秒",
              bool(done and done.get("sudoTtl") == 30), done)
        gone = post("/api/do/delete_student", {"id": vid})
        check("提权之后同一个请求就过了", bool(gone and gone.get("ok")), gone)
        post("/api/admin/unlock", {"on": False})
        post("/api/sudo/exit", {})

    print("== 模块动作：/<token>/api/do/<name> ==")
    # 放在最后：这里会加一个人再删掉，不碰前面那些断言的口径
    made = post("/api/do/add_student", {"sid": "2026ACT001", "name": "动作测试"})
    check("动作跑通了", bool(made and made.get("ok")), made)
    check("动作带回了版本号对象", (made or {}).get("target", "").startswith("event:"),
          (made or {}).get("target"))
    listed = [x for x in (call("/api/admin/state") or {}).get("students", [])
              if x["sid"] == "2026ACT001"]
    check("动作落到数据上", len(listed) == 1, len(listed))
    if listed:
        # 前面的用例可能把「解锁修改」打开了：先关上，才能验证拦截
        post("/api/admin/unlock", {"on": False})
        refused = post("/api/do/delete_student", {"id": listed[0]["id"]}, expect=403)
        check("要解锁的动作没解锁时被拒", bool(refused and refused.get("error")), refused)
        post("/api/admin/unlock", {"on": True})
        # delete_student 在模块里声明了 sudo：解锁之外还要活动级提权
        post("/api/sudo", {"password": owner_credentials()[1]})
        gone = post("/api/do/delete_student", {"id": listed[0]["id"]})
        check("解锁 + 提权之后删得掉", bool(gone and gone.get("ok")), gone)
        post("/api/admin/unlock", {"on": False})
        post("/api/sudo/exit", {})
    miss = post("/api/do/nope", {}, expect=404)
    check("没声明的动作是 404", bool(miss and miss.get("error")), miss)
    bad = post("/api/do/mark", {"kind": "checkin"}, expect=400)
    check("缺必填参数是 400", bool(bad and bad.get("error")), bad)

    print()
    if failures:
        print("FAILED %d check(s):" % len(failures))
        for f in failures:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
