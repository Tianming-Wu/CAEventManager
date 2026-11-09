#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Account/tier test for the master panel that now lives at the service root.

    python tools/panel_test.py http://127.0.0.1:8931 [owner_password]

The owner password is read from data/owner_password.txt when not given.
"""
import base64
import http.cookiejar
import io
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import zipfile

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931").rstrip("/")
ROOT = BASE
HEADER = {"X-Requested-With": "CAEventManager"}
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 服务实例的数据目录：跑套件时会传进来，平台提示词就写在它下面
DATA = os.environ.get("LOTTERY_DATA_DIR") or os.path.join(HERE, "data")

problems = []


def owner_password():
    if len(sys.argv) > 2:
        return sys.argv[2]
    path = os.path.join(HERE, "data", "owner_password.txt")
    for line in open(path, encoding="utf-8"):
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("no owner password")


def session():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(opener, path, body=None, expect=200, method=None,
         marker="CAEventManager"):
    """Talk to either surface: the console wants its own request marker."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Requested-With": marker}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ROOT + path, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    try:
        with opener.open(req, timeout=20) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    if status != expect:
        problems.append("%s -> %d (expected %d): %s" % (path, status, expect, raw[:160]))
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return raw


def post(opener, path, body=None, expect=200, marker="CAEventManager"):
    return call(opener, path, body if body is not None else {}, expect=expect,
                method="POST", marker=marker)


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        problems.append(name)


def keepalive_desync():
    """Two POSTs on one keep-alive connection, the first one rejected.

    A handler that returns before consuming Content-Length leaves the JSON body
    in the socket, and the server then parses it as the next request line
    ("Unsupported method ('{"username":...}POST')" -> 501).
    """
    host, port = "127.0.0.1", 8931
    if BASE.startswith("http://"):
        netloc = BASE[len("http://"):].split("/")[0]
        if ":" in netloc:
            host, port = netloc.rsplit(":", 1)
            port = int(port)
        else:
            host = netloc
    bad = json.dumps({"username": "nobody", "password": "wrong-pass-9"}).encode()
    head = ("POST /api/panel/login HTTP/1.1\r\nHost: %s\r\n"
            "Content-Type: application/json\r\nX-Requested-With: CAEventManager\r\n"
            "Content-Length: %d\r\n\r\n" % (host, len(bad))).encode()
    ok = b"GET /api/panel/me HTTP/1.1\r\nHost: " + host.encode() + b"\r\n" \
         b"X-Requested-With: CAEventManager\r\nConnection: close\r\n\r\n"
    sock = socket.create_connection((host, int(port)), timeout=10)
    try:
        sock.sendall(head + bad + ok)
        chunks = []
        while True:
            try:
                piece = sock.recv(65536)
            except socket.timeout:
                break
            if not piece:
                break
            chunks.append(piece)
        raw = b"".join(chunks)
    finally:
        sock.close()
    text = raw.decode("utf-8", "replace")
    first = text.split("HTTP/1.1 ", 2)[1][:3] if "HTTP/1.1 " in text else "???"
    check("rejected login + follow-up request keep the socket in sync",
          "Unsupported method" not in text and "501" not in text
          and text.count("HTTP/1.1 ") >= 2,
          text[:160])
    return first


def main():
    print("== pages at the service root ==")
    anon = session()
    page = call(anon, "/")
    check("root serves login", isinstance(page, bytes)
          and "登录".encode() in page and "没有账号？".encode() in page,
          (page or b"")[:60])
    reg = call(anon, "/register")
    check("register lives at /register", isinstance(reg, bytes)
          and "注册".encode() in reg and "参加抽奖并不需要注册账户".encode() in reg,
          (reg or b"")[:60])
    landed = call(anon, "/panel")
    check("anonymous /panel lands on login",
          isinstance(landed, bytes) and "登录".encode() in landed
          and "主面板".encode() not in landed)

    print("== registration needs approval ==")
    res = post(anon, "/api/panel/register", {
        "username": "teacher", "name": "张老师",
        "password": "teacher-pass-1", "note": "想给班里办活动"})
    check("register accepted", res and res["ok"], res)
    post(anon, "/api/panel/register",
         {"username": "teacher", "name": "x", "password": "12345678"}, expect=409)
    check("duplicate username rejected", True)
    post(anon, "/api/panel/register",
         {"username": "ab", "name": "x", "password": "12345678"}, expect=400)
    check("short username rejected", True)

    teacher = session()
    post(teacher, "/api/panel/login",
         {"username": "teacher", "password": "teacher-pass-1"}, expect=403)
    check("pending account cannot log in", True)

    print("== the page and the api agree on the endpoint ==")
    import re
    for page_name, script_name, wanted in (
            ("panel_login.html", "login.js", "/login"),
            ("panel_register.html", "register.js", "/register")):
        script = open(os.path.join(HERE, "panel_static", script_name),
                      encoding="utf-8").read()
        calls = re.findall(r'PX\.api\(\s*"([^"]+)"', script)
        check("%s calls %s" % (script_name, wanted),
              wanted in calls and not any(c.startswith("/api/") for c in calls),
              calls)

    print("== keep-alive stays in sync after a rejected login ==")
    keepalive_desync()
    call(teacher, "/api/panel/accounts", expect=401)
    check("pending account has no session", True)

    print("== owner ==")
    owner = session()
    res = post(owner, "/api/panel/login",
               {"username": "owner", "password": owner_password()})
    check("owner logs in", res and res["ok"], res)
    me = call(owner, "/api/panel/me")
    check("owner role reported", me and me["me"]["role"] == "owner", me)
    events = call(owner, "/api/panel/events")["events"]
    check("the existing event is listed", len(events) == 1, events)
    check("event links point at the token path",
          events and "/" in events[0]["admin"] and events[0]["id"] in events[0]["url"], events)
    ev = events[0]
    check("the event has a numeric id apart from its token",
          isinstance(ev.get("seq"), int) and ev["seq"] >= 1
          and ev["seq"] != ev["token"], ev)
    # the numeric id must never appear as a path segment ("127.0.0.1" contains
    # digits, so a substring test would be wrong)
    check("the id is not part of the address",
          ("/%d/" % ev["seq"]) not in ev["admin"], ev["admin"])
    check("the event records its module", bool(ev.get("kind")), ev)
    check("the owner is the owner",
          ev.get("role") == "owner", ev.get("role"))

    accs = call(owner, "/api/panel/accounts")
    pending = [a for a in accs["accounts"] if a["status"] == "pending"]
    check("owner sees the pending request", len(pending) == 1, accs["accounts"])
    teacher_id = pending[0]["id"]

    print("== sudo mode ==")
    check("a fresh session is not elevated", me.get("sudoUntil") == 0, me)
    post(owner, "/api/panel/sudo", {"password": "not-the-password"}, expect=403)
    check("a wrong password does not elevate anything", True)
    refused = call(owner, "/api/panel/event/rotate", {"seq": ev["seq"]}, expect=403)
    check("a sensitive action is refused while not elevated", True)
    check("and says why, so the panel can ask for the password",
          refused and refused.get("sudoRequired") is True, refused)
    check("a refusal is not an error message to read",
          refused and refused.get("ok") is False, refused)
    promoted = post(owner, "/api/panel/sudo", {"password": owner_password()})
    check("the password elevates the session",
          promoted and promoted.get("sudoUntil", 0) > 0, promoted)
    check("the elevation has a deadline",
          0 < promoted.get("sudoUntil", 0) - promoted.get("now", 0) <= 600, promoted)
    check("and the session reports it",
          call(owner, "/api/panel/me")["sudoUntil"] > 0)

    print("== approval ==")
    post(owner, "/api/panel/account/decide", {"id": teacher_id, "action": "approve"})
    res = post(teacher, "/api/panel/login",
               {"username": "teacher", "password": "teacher-pass-1"})
    check("approved account can log in", res and res["ok"], res)
    check("new account is a plain admin",
          call(teacher, "/api/panel/me")["me"]["role"] == "admin")
    # the elevation belongs to the session that earned it, not to the account
    check("a second sign-in of another account is not elevated",
          call(teacher, "/api/panel/me")["sudoUntil"] == 0)
    post(teacher, "/api/panel/account/decide", {"id": teacher_id, "action": "disable"},
         expect=403)
    check("admin cannot decide anything", True)
    post(teacher, "/api/panel/account/role", {"id": teacher_id, "role": "superadmin"},
         expect=403)
    check("admin cannot change roles", True)

    print("== role matrix ==")
    post(owner, "/api/panel/account/role", {"id": teacher_id, "role": "superadmin"})
    row = [a for a in call(owner, "/api/panel/accounts")["accounts"]
           if a["id"] == teacher_id][0]
    check("owner can promote to superadmin", row["role"] == "superadmin", row)

    anon2 = session()
    post(anon2, "/api/panel/register", {"username": "member", "name": "李同学",
                                        "password": "member-pass-1", "note": "助手"})
    member_id = [a for a in call(teacher, "/api/panel/accounts")["accounts"]
                 if a["username"] == "member"][0]["id"]
    post(teacher, "/api/panel/account/decide", {"id": member_id, "action": "approve"})
    check("superadmin can approve a plain admin", True)

    owner_id = [a for a in accs["accounts"] if a["role"] == "owner"][0]["id"]
    post(teacher, "/api/panel/account/decide", {"id": owner_id, "action": "disable"},
         expect=403)
    check("superadmin cannot touch the owner", True)
    post(teacher, "/api/panel/account/role", {"id": member_id, "role": "superadmin"},
         expect=403)
    check("superadmin cannot change roles", True)

    print("== disable / enable ==")
    post(owner, "/api/panel/account/decide", {"id": member_id, "action": "disable"})
    member = session()
    post(member, "/api/panel/login",
         {"username": "member", "password": "member-pass-1"}, expect=403)
    check("disabled account cannot log in", True)
    post(owner, "/api/panel/account/decide", {"id": member_id, "action": "enable"})
    res = post(member, "/api/panel/login",
               {"username": "member", "password": "member-pass-1"})
    check("re-enabled account can log in", res and res["ok"], res)

    print("== password change ==")
    post(member, "/api/panel/password",
         {"old": "wrong", "new": "member-pass-2"}, expect=403)
    check("wrong current password refused", True)
    post(member, "/api/panel/password",
         {"old": "member-pass-1", "new": "member-pass-2"})
    check("new password works",
          post(session(), "/api/panel/login",
               {"username": "member", "password": "member-pass-2"}) is not None)
    post(session(), "/api/panel/login",
         {"username": "member", "password": "member-pass-1"}, expect=401)
    check("old password stops working", True)

    print("== csrf + audit ==")
    req = urllib.request.Request(
        ROOT + "/api/panel/account/decide",
        data=json.dumps({"id": member_id, "action": "disable"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        owner.open(req, timeout=10)
        check("post without marker rejected", False, "accepted")
    except urllib.error.HTTPError as exc:
        check("post without marker rejected", exc.code == 403, exc.code)

    actions = {a["action"] for a in call(owner, "/api/panel/audit")["audit"]}
    check("audit records the lifecycle",
          {"register", "approve", "role_change", "password_change"} <= actions,
          sorted(actions))

    print("== device stats are owner-only ==")
    host = call(owner, "/api/panel/host")
    check("owner can read the host stats", host and "host" in host, host)
    if host and host.get("host"):
        h = host["host"]
        check("host payload is complete",
              all(k in h for k in ("cpu", "mem", "uptime", "version", "dataDir")),
              sorted(h))
    users = (host or {}).get("users") or {}
    check("the owner's own request counts as online", users.get("online", 0) >= 1, users)
    check("every approved account is counted as registered",
          users.get("registered", 0) >= 2, users)
    plain = session()
    # member's password was changed by the section above
    post(plain, "/api/panel/login", {"username": "member", "password": "member-pass-2"})
    call(plain, "/api/panel/host", expect=403)
    check("a plain admin cannot read them", True)
    again = (call(owner, "/api/panel/host") or {}).get("users") or {}
    check("a second account signing in raises the online count",
          again.get("online", 0) >= 2, again)

    print("== presets and modules ==")
    tpls = call(owner, "/api/panel/templates")["presets"]
    ids = [t["id"] for t in tpls]
    check("the built-in template is listed", "nwuca.salon" in ids, ids)
    tpl = [t for t in tpls if t["id"] == "nwuca.salon"][0]
    check("a template names the module behind it",
          tpl["module"] == "nwuca.lottery", tpl)
    check("the module brings the fields", bool(tpl["fields"]), tpl["fields"])
    check("the module declares conditional fields",
          any(f.get("visibleWhen") for f in tpl["fields"]),
          [f.get("visibleWhen") for f in tpl["fields"]])
    check("the template carries preset parameters",
          tpl["params"].get("pool_mode") == "auto", tpl["params"])
    check("the built-in preset is visible to everyone",
          tpl["visibility"] == "public" and tpl["builtin"], tpl)
    visibility = [v["value"] for v in call(owner, "/api/panel/templates")["visibility"]]
    check("private, public and organisation-wide are offered",
          visibility == ["private", "public", "org"], visibility)
    mods = call(owner, "/api/panel/modules")
    check("the owner can list modules",
          mods and mods["modules"] and mods["modules"][0]["id"], mods)
    check("a superadmin can read the module list too",
          call(teacher, "/api/panel/modules") is not None)
    mods = call(owner, "/api/panel/modules")["modules"]
    mod = mods[0]
    check("a module knows where it came from", mod["builtin"] is True, mod)
    check("and whether it is switched on", mod["enabled"] is True, mod)
    check("a module ships its own presets", "salon" in mod["templates"], mod)
    post(owner, "/api/panel/module/toggle", {"id": "nwuca.lottery", "enabled": False})
    shelf = call(owner, "/api/panel/templates")["presets"]
    check("a switched-off module takes its presets out of use",
          all(not p["ok"] for p in shelf if p["module"] == "nwuca.lottery"), shelf)
    check("and says why",
          any("被管理员禁用" in p["reason"] for p in shelf), shelf)
    post(owner, "/api/panel/events/create", {"template": "nwuca.salon", "title": "不该建出来"},
         expect=400)
    check("and nothing can be created on it", True)
    post(owner, "/api/panel/module/toggle", {"id": "nwuca.lottery", "enabled": True})
    shelf = call(owner, "/api/panel/templates")["presets"]
    check("switching it back on restores them",
          all(p["ok"] for p in shelf if p["module"] == "nwuca.lottery"), shelf)

    print("== a preset that arrives from outside ==")
    orphan = {"id": "demo.orphan", "name": "外部预设",
              "dependency": {"module": "demo.missing"}}
    refused = post(owner, "/api/panel/preset/import", {"preset": orphan}, expect=409)
    check("a missing module is reported", refused and refused.get("dependencyMissing"),
          refused)
    check("a superadmin cannot import content",
          post(teacher, "/api/panel/preset/import", {"preset": orphan},
               expect=403) is not None)
    forced = post(owner, "/api/panel/preset/import",
                  {"preset": orphan, "force": True})
    check("importing anyway is allowed", forced and forced.get("id") == "demo.orphan",
          forced)
    shelf = call(owner, "/api/panel/templates")["presets"]
    orphan_row = [p for p in shelf if p["id"] == "demo.orphan"][0]
    check("it is listed but unusable", orphan_row["ok"] is False, orphan_row)
    check("with the reason spelled out",
          "依赖的模块不存在" in orphan_row["reason"], orphan_row)

    print("== a preset saved from an activity ==")
    made = post(owner, "/api/panel/event/preset",
                {"seq": ev["seq"], "name": "快照一", "visibility": "private"})
    check("an activity can be snapshotted", made and made.get("id"), made)
    shelf = call(owner, "/api/panel/templates")["presets"]
    snap = [p for p in shelf if p["id"] == made["id"]][0]
    check("the snapshot carries the module", snap["module"], snap)
    check("and its parameters", isinstance(snap["params"], dict), snap)
    check("it is mine and private", snap["mine"] and snap["visibility"] == "private", snap)
    post(owner, "/api/panel/preset/favorite", {"id": made["id"], "on": True})
    shelf = call(owner, "/api/panel/templates")["presets"]
    check("favourites are marked",
          [p for p in shelf if p["id"] == made["id"]][0]["fav"])
    check("nobody else can delete it",
          post(teacher, "/api/panel/preset/delete", {"id": made["id"]},
               expect=403) is not None)
    post(owner, "/api/panel/preset/delete", {"id": made["id"]})
    shelf = call(owner, "/api/panel/templates")["presets"]
    check("and the owner can", all(p["id"] != made["id"] for p in shelf), shelf)

    print("== drafts ==")
    post(owner, "/api/panel/draft",
         {"key": "nwuca.salon", "payload": {"title": "写了一半"}})
    got = call(owner, "/api/panel/draft?key=nwuca.salon")
    check("a draft comes back", got and "写了一半" in got["draft"]["payload"], got)
    post(owner, "/api/panel/draft", {"key": "nwuca.salon", "payload": None})
    got = call(owner, "/api/panel/draft?key=nwuca.salon")
    check("and can be thrown away", got and got["draft"] is None, got)

    print("== creating an activity ==")
    before = len(call(owner, "/api/panel/events")["events"])
    post(owner, "/api/panel/events/create",
         {"template": "nope.nope", "title": "x"}, expect=404)
    check("an unknown template is refused", True)
    post(owner, "/api/panel/events/create",
         {"template": "nwuca.salon", "title": "x", "params": {"pool_mode": ""}},
         expect=400)
    check("a required parameter cannot be emptied", True)

    made = post(owner, "/api/panel/events/create", {
        "template": "nwuca.salon", "title": "第二场",
        "params": {"allow_bonus": False}})
    check("an activity can be created", made and made["ok"], made)
    ev2 = made["event"]
    check("it gets its own token and id",
          ev2["seq"] != ev["seq"] and ev2["token"] not in ev["admin"], ev2)
    check("its links use its own token", ev2["token"] in ev2["admin"], ev2["admin"])
    check("the creator owns it", ev2["role"] == "owner", ev2)
    listing = call(owner, "/api/panel/events")["events"]
    check("exactly one more activity is listed", len(listing) == before + 1, listing)
    check("the original is untouched",
          any(e["seq"] == ev["seq"] for e in listing), listing)

    print("== the template fills what it can ==")
    named = post(owner, "/api/panel/events/create", {"template": "nwuca.salon"})
    check("a template supplies the name", named and named["event"]["title"], named)
    if named:
        post(owner, "/api/panel/event/archive", {"seq": named["event"]["seq"]})
    check("and it can be archived again", True)

    print("== rotating the access token ==")
    post(teacher, "/api/panel/event/rotate", {"seq": ev2["seq"]}, expect=403)
    check("a non-owner cannot rotate it", True)
    old_token = ev2["token"]
    rotated = post(owner, "/api/panel/event/rotate", {"seq": ev2["seq"]})
    check("the owner can rotate it", rotated and rotated["token"] != old_token, rotated)
    listed = call(owner, "/api/panel/events")["events"]
    row = [e for e in listed if e["seq"] == ev2["seq"]][0]
    check("the panel shows the new token", row["token"] == rotated["token"], row)
    check("and links follow it", rotated["token"] in row["admin"], row["admin"])
    old_req = urllib.request.Request(
        ROOT + "/" + old_token + "/api/board", headers={"User-Agent": "curl/8"})
    try:
        owner.open(old_req, timeout=10)
        check("the old address stops answering", False, "still served")
    except urllib.error.HTTPError as exc:
        check("the old address stops answering", exc.code == 404, exc.code)
    check("the numeric id is unchanged", row["seq"] == ev2["seq"], row)
    ev2["token"] = rotated["token"]      # later checks use the live address

    print("== the console rides on the panel session ==")
    console_req = urllib.request.Request(
        ROOT + "/" + ev2["token"] + "/api/admin/console",
        headers={"X-Requested-With": "lottery"})
    try:
        anon.open(console_req, timeout=20)
        check("the console needs a panel session", False, "served without one")
    except urllib.error.HTTPError as exc:
        check("the console needs a panel session", exc.code == 401, exc.code)
    # a signed-in account that is not on this activity gets nothing
    try:
        member.open(console_req, timeout=20)
        check("a seat on the activity is what opens its console", False, "served anyway")
    except urllib.error.HTTPError as exc:
        check("a seat on the activity is what opens its console", exc.code == 403,
              exc.code)
    mine = urllib.request.Request(
        ROOT + "/" + ev["token"] + "/api/admin/console",
        headers={"X-Requested-With": "lottery"})
    with owner.open(mine, timeout=20) as res:
        pages = json.loads(res.read())
        check("the activity the owner runs answers", res.status == 200 and pages["ok"],
              res.status)
    # and a browser with no session is sent to the panel's login, not a console one
    landing = session()
    with landing.open(ROOT + "/" + ev["token"] + "/admin", timeout=20) as res:
        check("an unsigned console lands on the panel login",
              "/login" in res.geturl() and "s_return=" in res.geturl(), res.geturl())
    # a signed-in account with no seat is refused in place, not sent in circles
    try:
        member.open(ROOT + "/" + ev["token"] + "/admin", timeout=20)
        check("a signed-in non-member is refused, not redirected", False, "served anyway")
    except urllib.error.HTTPError as exc:
        refusal = exc.read()
        check("a signed-in non-member is refused, not redirected",
              exc.code == 403 and "不是这个活动的成员".encode("utf-8") in refusal,
              exc.code)

    print("== members: invite, accept, revoke ==")
    base = "/" + ev["token"]
    roster = call(owner, base + "/api/admin/members")
    check("the owner can read the roster", roster and roster["members"], roster)
    check("it starts with just the owner",
          [m["username"] for m in roster["members"]] == ["owner"], roster["members"])
    found = call(owner, base + "/api/admin/members/search?q=teach")
    check("a search finds an account that is not in yet",
          found and [a["username"] for a in found["accounts"]] == ["teacher"], found)
    post(owner, base + "/api/admin/member",
         {"accountId": teacher_id, "action": "invite"}, marker="lottery")
    check("the invitation is waiting for them", True)
    roster = call(owner, base + "/api/admin/members")
    seat = [m for m in roster["members"] if m["username"] == "teacher"][0]
    check("a pending seat is not a seat yet", seat["state"] == "pending", seat)
    check("and it says who asked", seat["invitedBy"] == "站长", seat)
    invites = call(teacher, "/api/panel/invites")
    check("the invitee sees it in the panel",
          invites and len(invites["invites"]) == 1, invites)
    check("the activity is not on their list before accepting",
          all(e["seq"] != ev["seq"] for e in call(teacher, "/api/panel/events")["events"]))
    post(teacher, "/api/panel/invite", {"seq": ev["seq"], "action": "accept"})
    check("accepting turns it into a seat", True)
    check("now it is on their list",
          any(e["seq"] == ev["seq"]
              for e in call(teacher, "/api/panel/events")["events"]))
    check("and the console opens for them",
          call(teacher, base + "/api/admin/state") is not None)
    post(teacher, base + "/api/admin/member",
         {"accountId": teacher_id, "action": "revoke"}, expect=403,
         marker="lottery")
    check("a plain member cannot manage members", True)
    post(owner, base + "/api/admin/member",
         {"accountId": teacher_id, "action": "revoke"}, marker="lottery")
    roster = call(owner, base + "/api/admin/members")
    seat = [m for m in roster["members"] if m["username"] == "teacher"][0]
    check("a revoked seat stays on the record", seat["state"] == "revoked", seat)
    check("with the time it happened", seat["stateAt"] > 0, seat)
    check("their console access ends",
          call(teacher, base + "/api/admin/state", expect=403) is not None)
    check("and the activity leaves their list",
          all(e["seq"] != ev["seq"] for e in call(teacher, "/api/panel/events")["events"]))
    post(owner, base + "/api/admin/member",
         {"accountId": teacher_id, "action": "invite"}, marker="lottery")
    check("a revoked seat can be invited again",
          call(teacher, "/api/panel/invites")["invites"][0]["seq"] == ev["seq"])
    post(teacher, "/api/panel/invite", {"seq": ev["seq"], "action": "decline"})
    roster = call(owner, base + "/api/admin/members")
    seat = [m for m in roster["members"] if m["username"] == "teacher"][0]
    check("a decline is recorded too", seat["state"] == "declined", seat)
    post(owner, base + "/api/admin/member",
         {"accountId": owner_id, "action": "revoke"}, expect=400,
         marker="lottery")
    check("the owner cannot remove themselves", True)

    print("== archiving an activity ==")
    post(teacher, "/api/panel/event/archive", {"seq": ev2["seq"]}, expect=403)
    check("a non-owner cannot archive it", True)
    archived = post(owner, "/api/panel/event/archive", {"seq": ev2["seq"]})
    check("the owner can archive it", archived and archived["ok"], archived)
    after = call(owner, "/api/panel/events")["events"]
    check("an archived activity leaves the list",
          all(e["seq"] != ev2["seq"] for e in after), after)
    gone = urllib.request.Request(
        ROOT + "/" + ev2["token"] + "/api/board",
        headers={"User-Agent": "curl/8"})
    try:
        owner.open(gone, timeout=10)
        check("its public page stops answering", False, "still served")
    except urllib.error.HTTPError as exc:
        check("its public page stops answering", exc.code in (404, 403), exc.code)
    again = post(owner, "/api/panel/event/archive", {"seq": ev2["seq"]})
    check("archiving twice is harmless", again and again["ok"], again)

    print("== renaming an activity ==")
    ev = call(owner, "/api/panel/events")["events"][0]
    got = post(owner, "/api/panel/event/title",
               {"seq": ev["seq"], "title": "改名测试"})
    check("the event's owner can rename it", got and got["title"] == "改名测试", got)
    check("the list follows",
          call(owner, "/api/panel/events")["events"][0]["title"] == "改名测试")
    post(teacher, "/api/panel/event/title",
         {"seq": ev["seq"], "title": "不该成功"}, expect=403)
    check("a platform admin who is not on the event cannot rename it", True)
    # the permission check comes first on purpose: a non-member cannot tell a
    # missing event from one they simply do not own
    post(owner, "/api/panel/event/title", {"seq": 9999, "title": "x"}, expect=403)
    check("a missing event looks like a forbidden one", True)

    print("== profile: display name and login name ==")
    me = call(member, "/api/panel/me")["me"]
    check("a display name is set", bool(me["name"]), me)
    post(member, "/api/panel/profile", {"name": "李同学", "username": "member"})
    check("the display name can change",
          call(member, "/api/panel/me")["me"]["name"] == "李同学")
    post(member, "/api/panel/profile", {"name": "", "username": "member"}, expect=400)
    check("an empty display name is refused", True)
    post(member, "/api/panel/profile", {"name": "x" * 25, "username": "member"}, expect=400)
    check("an over-long display name is refused", True)
    post(member, "/api/panel/profile",
         {"name": "李同学", "username": "teacher"}, expect=409)
    check("a taken login name is refused", True)
    post(member, "/api/panel/profile",
         {"name": "李同学", "username": "a b"}, expect=400)
    check("a malformed login name is refused", True)

    moved = post(member, "/api/panel/profile",
                 {"name": "李同学", "username": "member2"})
    check("the login name can change",
          moved and moved["me"]["username"] == "member2", moved)
    again = session()
    post(again, "/api/panel/login", {"username": "member2", "password": "member-pass-2"})
    check("the new login name works", True)
    post(session(), "/api/panel/login",
         {"username": "member", "password": "member-pass-2"}, expect=401)
    check("the old login name stops working", True)
    post(owner, "/api/panel/profile", {"name": "x", "username": "owner2"}, expect=403)
    check("the owner's login name is owned by the config file", True)
    current = call(owner, "/api/panel/me")["me"]["name"]
    kept = post(owner, "/api/panel/profile", {"name": current, "username": "owner"})
    check("the owner can still change the display name",
          kept and kept["me"]["username"] == "owner", kept)

    print("== banning an account ==")
    plain = session()
    post(plain, "/api/panel/login", {"username": "member2", "password": "member-pass-2"})
    target = [a for a in call(owner, "/api/panel/accounts")["accounts"]
              if a["username"] == "member2"][0]
    # leaving sudo mode is what the chip's 退出 does
    left = post(owner, "/api/panel/sudo/exit")
    check("sudo can be left", left and left.get("sudoUntil") == 0, left)
    check("and the session is back to normal",
          call(owner, "/api/panel/me")["sudoUntil"] == 0)
    post(owner, "/api/panel/account/decide",
         {"id": target["id"], "action": "ban", "reason": "测试封禁"}, expect=403)
    check("a ban needs the password again", True)
    banner = [a for a in call(owner, "/api/panel/accounts")["accounts"]
              if a["id"] == target["id"]][0]
    check("the refused ban changed nothing", banner["status"] != "banned", banner)

    post(owner, "/api/panel/sudo", {"password": owner_password()})
    post(owner, "/api/panel/account/decide",
         {"id": target["id"], "action": "ban"}, expect=400)
    check("a ban needs a reason", True)
    post(owner, "/api/panel/account/decide",
         {"id": target["id"], "action": "ban", "reason": "测试封禁"})
    banned = [a for a in call(owner, "/api/panel/accounts")["accounts"]
              if a["id"] == target["id"]][0]
    check("the account is banned", banned["status"] == "banned", banned)
    check("the reason is kept", banned["banReason"] == "测试封禁", banned)
    fresh = session()
    res = post(fresh, "/api/panel/login",
               {"username": "member2", "password": "member-pass-2"}, expect=403)
    check("a banned account cannot log in", True)
    post(owner, "/api/panel/account/decide",
         {"id": target["id"], "action": "unban"})
    check("and can be released", post(session(), "/api/panel/login",
                                      {"username": "member2",
                                       "password": "member-pass-2"}) is not None)
    check("unbanning does not need the password again",
          call(owner, "/api/panel/me")["sudoUntil"] > 0)

    print("== profile: avatar ==")
    pixel = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
             "z8AAAwAB/AL+2QAAAABJRU5ErkJggg==")
    got = post(member, "/api/panel/avatar", {"data": "data:image/png;base64," + pixel})
    check("a png is accepted", got and got["me"]["avatar"], got)
    name = got["me"]["avatar"]
    who = got["me"]["id"]
    raw = urllib.request.Request(
        ROOT + "/api/panel/avatar/%d" % who, headers=dict(HEADER))
    with member.open(raw, timeout=20) as res:
        blob = res.read()
        check("the image is served back", res.status == 200 and blob[:4] == b"\x89PNG",
              res.status)
    post(member, "/api/panel/avatar",
         {"data": "bm90IGFuIGltYWdl"}, expect=400)
    check("a non-image is refused", True)
    post(member, "/api/panel/avatar", {"data": "!!!!"}, expect=400)
    check("junk base64 is refused", True)
    post(member, "/api/panel/avatar",
         {"data": "A" * (800 * 1024)}, expect=413)
    check("an oversized image is refused", True)
    cleared = post(member, "/api/panel/avatar", {"clear": True})
    check("the avatar can be cleared", cleared and not cleared["me"]["avatar"], cleared)
    missing = urllib.request.Request(
        ROOT + "/api/panel/avatar/%d" % who, headers=dict(HEADER))
    try:
        member.open(missing, timeout=20)
        check("a cleared avatar is a 404", False, "still served")
    except urllib.error.HTTPError as exc:
        check("a cleared avatar is a 404", exc.code == 404, exc.code)
    data_dir = os.environ.get("LOTTERY_DATA_DIR") or os.path.join(HERE, "data")
    check("the old file is gone from disk",
          not os.path.exists(os.path.join(data_dir, "avatars", name)))

    print("== 名单：人员、名单、合并导入 ==")
    made = post(owner, "/api/panel/roster/create",
                {"name": "动漫社 2026 秋", "note": "社团成员", "visibility": "private"})
    roster_id = (made or {}).get("id")
    check("a roster can be created", bool(roster_id), made)
    csv_text = "学号,姓名,班级\n20240101,高勇,计科2401\n20240102,曾萍静,计科2402\n"
    res = post(owner, "/api/panel/roster/import", {"id": roster_id, "text": csv_text})
    check("import adds the rows", res and res["added"] == 2, res)
    res = post(owner, "/api/panel/roster/import", {"id": roster_id, "text": csv_text})
    check("importing the same file again changes nothing",
          res and res["added"] == 0 and res["unchanged"] == 2, res)
    made2 = post(owner, "/api/panel/roster/import",
                 {"name": "秋日祭报名", "text": "学号\t姓名\n20240101\t高勇\n"})
    check("importing without an id makes the roster",
          made2 and made2["created"] and made2["added"] == 1, made2)
    other_id = (made2 or {}).get("id")
    check("the same person is one row in the platform",
          call(owner, "/api/panel/people/search?q=%E9%AB%98%E5%8B%87")["people"]
          != [], "search by name finds the person")

    rows = call(owner, "/api/panel/roster/entries?id=%d" % roster_id)
    check("the roster lists its members", rows and rows["total"] == 2, rows)
    found = call(owner, "/api/panel/roster/entries?id=%d&q=pingjing" % roster_id)
    check("members are searchable by pinyin",
          found and found["total"] == 1 and found["entries"][0]["sid"] == "20240102",
          found)

    added = post(owner, "/api/panel/roster/entry",
                 {"id": roster_id, "sid": "20240103", "name": "马平", "grp": "计科2401"})
    check("a person can be added by hand", added and added["personId"], added)
    post(owner, "/api/panel/roster/entry/state",
         {"id": roster_id, "personId": added["personId"], "state": "left"})
    one = [e for e in call(owner, "/api/panel/roster/entries?id=%d" % roster_id)["entries"]
           if e["personId"] == added["personId"]][0]
    check("a member can be marked as left", one["state"] == "left", one)
    post(owner, "/api/panel/roster/entry/drop",
         {"id": roster_id, "personId": added["personId"]})
    check("a member can be removed",
          call(owner, "/api/panel/roster/entries?id=%d" % roster_id)["total"] == 2)

    sheet = call(owner, "/api/panel/roster/export.csv?id=%d" % roster_id)
    check("the roster exports as csv",
          isinstance(sheet, bytes) and sheet.startswith(b"\xef\xbb\xbf")
          and "学号".encode() in sheet and "高勇".encode() in sheet,
          (sheet or b"")[:40])

    print("== 名单：什么都没改就不算保存 ==")
    same = post(owner, "/api/panel/roster/update",
                {"id": roster_id, "name": "动漫社 2026 秋"})
    check("saving without a change reports that nothing changed",
          same and same.get("changed") is False, same)
    audit_now = call(owner, "/api/panel/audit?limit=5")["audit"]
    check("and leaves no audit row",
          not any(a["action"] == "roster_update" for a in audit_now[:1]),
          [a["action"] for a in audit_now[:3]])
    real = post(owner, "/api/panel/roster/update",
                {"id": roster_id, "note": "社团成员名单"})
    check("a real change still saves", real and real.get("changed") is True, real)

    print("== 名单：看得见 ≠ 改得动 ==")
    helper = session()
    post(anon, "/api/panel/register", {"username": "alice", "name": "王同学",
                                       "password": "alice-pass-1"})
    alice_id = [a for a in call(owner, "/api/panel/accounts")["accounts"]
                if a["username"] == "alice"][0]["id"]
    post(owner, "/api/panel/account/decide", {"id": alice_id, "action": "approve"})
    post(helper, "/api/panel/login", {"username": "alice", "password": "alice-pass-1"})
    check("someone else's private roster is not listed",
          call(helper, "/api/panel/rosters?scope=all")["total"] == 0)
    call(helper, "/api/panel/roster?id=%d" % roster_id, expect=403)
    check("and it cannot be opened", True)
    post(owner, "/api/panel/roster/update",
         {"id": roster_id, "visibility": "public"})
    opened = call(helper, "/api/panel/roster?id=%d" % roster_id)
    check("a public roster can be opened by others", opened and opened["ok"], opened)
    check("but it is read-only for them", opened["roster"]["editable"] is False,
          opened["roster"])
    check("and it offers to ask for write access",
          opened["roster"]["canAsk"] is True, opened["roster"])
    post(helper, "/api/panel/roster/update",
         {"id": roster_id, "name": "改个名"}, expect=403)
    check("and cannot be changed", True)
    post(helper, "/api/panel/roster/entry",
         {"id": roster_id, "sid": "20240199", "name": "路人"}, expect=403)
    check("nor added to", True)
    check("the owner still sees it",
          call(owner, "/api/panel/rosters?scope=mine")["total"] == 2)

    print("== 名单：申请写入权限 ==")
    asked = post(helper, "/api/panel/roster/ask", {"id": roster_id})
    check("申请立刻生效 when the roster says so",
          asked and asked["level"] == "write", asked)
    again = call(helper, "/api/panel/roster?id=%d" % roster_id)["roster"]
    check("the applicant can now write", again["editable"] is True, again)
    check("and the roster shows up under 我参与的",
          call(helper, "/api/panel/rosters?scope=joined")["total"] == 1)
    added_by_other = post(helper, "/api/panel/roster/entry",
                          {"id": roster_id, "sid": "20240104", "name": "徐静",
                           "dept": "计算机学院"})
    check("the granted account can add people", bool(added_by_other), added_by_other)
    found = [e for e in call(owner, "/api/panel/roster/entries?id=%d" % roster_id)["entries"]
             if e["sid"] == "20240104"]
    check("院系 is stored next to the class", found and found[0]["dept"] == "计算机学院",
          found)
    post(helper, "/api/panel/roster/delete", {"id": roster_id}, expect=403)
    check("deleting stays with the owner", True)
    editors = [e for e in call(owner, "/api/panel/roster?id=%d" % roster_id)
               ["roster"]["editors"]]
    check("the owner sees who may write",
          [e["accountId"] for e in editors] == [alice_id], editors)
    post(owner, "/api/panel/roster/grant/revoke",
         {"id": roster_id, "accountId": alice_id})
    post(helper, "/api/panel/roster/entry",
         {"id": roster_id, "sid": "20240105", "name": "又一个人"}, expect=403)
    check("a revoked account loses the write right", True)

    print("== 名单：被移出过的人要重新走审批 ==")
    # auto 模式下也一样：被拥有者移出过，再申请就得等确认，否则移除毫无意义
    again = post(helper, "/api/panel/roster/ask", {"id": roster_id})
    check("a removed account cannot walk back in on an auto roster",
          again and again["level"] == "pending", again)
    queue = call(helper, "/api/panel/list_approvals")
    check("the applicant sees the request it is waiting on",
          [a["id"] for a in queue["asks"]] == [roster_id], queue["asks"])
    post(owner, "/api/panel/roster/grant/decide",
         {"id": roster_id, "accountId": alice_id, "action": "approve"})
    check("approving it lets the person back in",
          (lambda r: r["editable"] is True)(
              call(helper, "/api/panel/roster?id=%d" % roster_id)["roster"]))
    post(owner, "/api/panel/roster/grant/revoke",
         {"id": roster_id, "accountId": alice_id})

    print("== 名单：申请需要确认时 ==")
    post(owner, "/api/panel/roster/update",
         {"id": roster_id, "grant_mode": "confirm"})
    pending = post(helper, "/api/panel/roster/ask", {"id": roster_id})
    check("the application waits for the owner", pending and pending["level"] == "pending",
          pending)
    post(helper, "/api/panel/roster/entry",
         {"id": roster_id, "sid": "20240106", "name": "还没批"}, expect=403)
    check("and gives no write access yet", True)
    row = call(owner, "/api/panel/roster?id=%d" % roster_id)["roster"]
    check("the owner sees the pending request",
          [p["accountId"] for p in row["pending"]] == [alice_id], row["pending"])
    post(owner, "/api/panel/roster/grant/decide",
         {"id": roster_id, "accountId": alice_id, "action": "approve"})
    post(helper, "/api/panel/roster/entry",
         {"id": roster_id, "sid": "20240106", "name": "批了"}, )
    check("after approval the write goes through", True)
    post(owner, "/api/panel/roster/grant/revoke",
         {"id": roster_id, "accountId": alice_id})
    invited = post(owner, "/api/panel/roster/grant",
                   {"id": roster_id, "username": "alice"})
    check("the owner can invite by login name", invited and invited["ok"], invited)
    post(helper, "/api/panel/roster/entry",
         {"id": roster_id, "sid": "20240107", "name": "被邀请"})
    check("an invited account can write right away", True)
    post(owner, "/api/panel/roster/grant",
         {"id": roster_id, "username": "no-such-user"}, expect=404)
    check("inviting an unknown account is refused", True)
    private_cannot_ask = post(owner, "/api/panel/roster/update",
                              {"id": other_id, "visibility": "private"})
    post(helper, "/api/panel/roster/ask", {"id": other_id}, expect=403)
    check("a private roster cannot be applied to", True)

    print("== 名单：可写的人也能审批（拥有者点头之后） ==")
    bob = session()
    post(anon, "/api/panel/register", {"username": "bob", "name": "赵同学",
                                       "password": "bob-pass-1"})
    bob_id = [a for a in call(owner, "/api/panel/accounts")["accounts"]
              if a["username"] == "bob"][0]["id"]
    post(owner, "/api/panel/account/decide", {"id": bob_id, "action": "approve"})
    post(bob, "/api/panel/login", {"username": "bob", "password": "bob-pass-1"})
    post(owner, "/api/panel/roster/grant",
         {"id": roster_id, "username": "bob"})
    post(owner, "/api/panel/roster/update",
         {"id": roster_id, "grant_mode": "confirm"})
    # alice still holds write from the invitation: take it away so there is a
    # request for bob to look at
    post(owner, "/api/panel/roster/grant/revoke",
         {"id": roster_id, "accountId": alice_id})
    post(helper, "/api/panel/roster/ask", {"id": roster_id})
    post(bob, "/api/panel/roster/grant/decide",
         {"id": roster_id, "accountId": alice_id, "action": "approve"}, expect=403)
    check("by default only the owner approves", True)
    post(owner, "/api/panel/roster/update",
         {"id": roster_id, "approve_mode": "editors"})
    mine = call(bob, "/api/panel/list_approvals")
    check("a writer sees the request once the owner allows it",
          [p["accountId"] for p in mine["pending"]] == [alice_id], mine["pending"])
    post(bob, "/api/panel/roster/grant/decide",
         {"id": roster_id, "accountId": alice_id, "action": "approve"})
    check("and can approve it",
          (lambda r: r["editable"] is True)(
              call(helper, "/api/panel/roster?id=%d" % roster_id)["roster"]))
    outsider = call(teacher, "/api/panel/list_approvals")
    check("someone with no seat sees an empty queue",
          outsider["pending"] == [] and outsider["asks"] == [], outsider)

    print("== 名单：三个范围 ==")
    check("我参与的 covers what I own",
          call(owner, "/api/panel/rosters?scope=joined")["total"] >= 2)
    check("我创建的 is only mine",
          all(r["ownerId"] == owner_id
              for r in call(owner, "/api/panel/rosters?scope=mine")["rosters"]))
    check("我参与的 includes the ones granted to me",
          call(helper, "/api/panel/rosters?scope=joined")["total"] >= 1)
    check("但是 我创建的 不含别人的",
          call(helper, "/api/panel/rosters?scope=mine")["total"] == 0)
    check("全部名单 is what the visibility allows",
          # 只有一份是公开的，另一份是私有的
          call(helper, "/api/panel/rosters?scope=all")["total"] == 1,
          call(helper, "/api/panel/rosters?scope=all"))

    allseen = call(teacher, "/api/panel/rosters?scope=all")
    check("a superadmin can count every roster", allseen["total"] >= 2, allseen["total"])
    left = [r for r in allseen["rosters"] if r["id"] == roster_id][0]
    check("an admin's view of someone else's roster is read-only",
          left["editable"] is False, left)

    print("== 报名：公开页与提交 ==")
    made = post(owner, "/api/panel/signup/create",
                {"title": "秋日祭现场报名", "roster_id": roster_id,
                 "capacity": 2,
                 "fields": [{"label": "是否住宿", "type": "select",
                             "options": ["住", "不住"], "required": True,
                             "into": "roster.note"},
                            {"label": "院系补充", "type": "text",
                             "into": "person.dept"}]})
    signup_id = (made or {}).get("id")
    check("报名 can be created", bool(signup_id), made)
    detail = call(owner, "/api/panel/signup?id=%d" % signup_id)["signup"]
    check("报名 points at its roster and link", roster_id == detail["rosterId"]
          and "/join/" in detail["url"], detail.get("url"))
    check("报名 is open from the start", detail["status"] == "open", detail["status"])
    token = detail["token"]

    anon3 = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = anon3.open(ROOT + "/join/" + token, timeout=20)
    body = page.read()
    check("the public page needs no session",
          page.status == 200 and "提交报名".encode() in body or "报名".encode() in body)
    check("it redirects to a directory address",
          page.geturl().endswith("/"), page.geturl())
    state = json.loads(anon3.open(ROOT + "/join/" + token + "/api/state",
                                  timeout=20).read())
    check("the public state carries the fields",
          [f["label"] for f in state["signup"]["fields"]] == ["是否住宿", "院系补充"], state)
    check("an unrestricted 报名 says so", state["limited"] is False, state)

    def submit(body):
        req = urllib.request.Request(
            ROOT + "/join/" + token + "/api/submit",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Requested-With": "signup"}, method="POST")
        try:
            with anon3.open(req, timeout=20) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    code, res = submit({"sid": "20240201", "name": "新同学", "dept": "外语学院",
                        "answers": {"f1": "住", "f2": "英语系"}})
    check("a submission is accepted", code == 200 and res["ok"], (code, res))
    entries = call(owner, "/api/panel/signup/entries?id=%d" % signup_id)
    check("the submission is recorded", entries["total"] == 1, entries)
    row = entries["entries"][0]
    check("the answers are kept per submission",
          row["answers"] == {"f1": "住", "f2": "英语系"}, row["answers"])
    member = [e for e in call(owner, "/api/panel/roster/entries?id=%d" % roster_id)["entries"]
              if e["sid"] == "20240201"]
    check("the person lands on the target roster", bool(member), member)
    check("a field marked 写成名单备注 arrives there",
          member and member[0]["note"] == "住", member)
    check("a field marked 写入院系 arrives there",
          member and member[0]["dept"] == "英语系", member)

    code, res = submit({"sid": "20240201", "name": "新同学", "dept": "外语学院",
                        "answers": {"f1": "住", "f2": "英语系"}})
    check("the same answers again are recognised as already registered",
          code == 200 and res["state"] == "same", (code, res))
    code, res = submit({"sid": "20240201", "name": "新同学", "dept": "外语学院",
                        "answers": {"f1": "不住", "f2": "英语系"}})
    check("different answers are refused while self-edit is off",
          code == 409 and "联系负责人" in res.get("error", ""), (code, res))
    post(owner, "/api/panel/signup/update", {"id": signup_id, "self_edit": "on"})
    code, res = submit({"sid": "20240201", "name": "新同学", "dept": "外语学院",
                        "answers": {"f1": "不住", "f2": "英语系"}})
    check("with self-edit on, a difference asks first",
          code == 200 and res["state"] == "differs", (code, res))
    code, res = submit({"sid": "20240201", "name": "新同学", "dept": "外语学院",
                        "answers": {"f1": "不住", "f2": "英语系"}, "update": True})
    check("confirming the update overwrites the record",
          code == 200 and res["state"] == "updated", (code, res))
    row = [e for e in call(owner, "/api/panel/signup/entries?id=%d" % signup_id)["entries"]
           if e["sid"] == "20240201"][0]
    check("the stored answers are the new ones",
          row["answers"]["f1"] == "不住", row["answers"])
    member = [e for e in call(owner, "/api/panel/roster/entries?id=%d" % roster_id)["entries"]
              if e["sid"] == "20240201"][0]
    check("the roster entry follows the update", member["note"] == "不住", member)
    post(owner, "/api/panel/signup/update", {"id": signup_id, "self_edit": "off"})
    code, res = submit({"sid": "20240202", "name": "另一个人",
                        "answers": {"f1": "住"}})
    check("a second person is accepted", code == 200 and res["count"] == 2, (code, res))
    code, res = submit({"sid": "20240203", "name": "第三个人",
                        "answers": {"f1": "住"}})
    check("the capacity stops further submissions",
          code == 409 and "名额" in res.get("error", ""), (code, res))
    code, res = submit({"sid": "20240204", "name": "没填必填项"})
    check("a required field is enforced",
          code == 409 or code == 400, (code, res))

    sheet = call(owner, "/api/panel/signup/export.csv?id=%d" % signup_id)
    check("报名 exports with its own columns",
          isinstance(sheet, bytes) and "是否住宿".encode() in sheet, (sheet or b"")[:40])

    print("== 报名：限制会员 ==")
    post(owner, "/api/panel/signup/update",
         {"id": signup_id, "capacity": 0, "filter_roster_id": roster_id})
    code, res = submit({"sid": "20240209", "name": "名单外的人",
                        "answers": {"f1": "住"}})
    check("someone outside the roster is refused",
          code == 403 and "范围" in res.get("error", ""), (code, res))
    code, res = submit({"sid": "20240101", "name": "写错的名字",
                        "answers": {"f1": "住"}})
    check("a mismatched name is refused",
          code == 403 and "不一致" in res.get("error", ""), (code, res))
    code, res = submit({"sid": "20240101", "name": "高勇",
                        "answers": {"f1": "住"}})
    check("a member of that roster gets in", code == 200, (code, res))

    print("== 报名：收集字段与时间校验 ==")
    state = json.loads(anon3.open(ROOT + "/join/" + token + "/api/state",
                                  timeout=20).read())
    check("both built-in fields are collected by default",
          state["signup"]["collect"] == ["dept", "grp"], state["signup"]["collect"])
    post(owner, "/api/panel/signup/update",
         {"id": signup_id, "collect": ["dept"]})
    state = json.loads(anon3.open(ROOT + "/join/" + token + "/api/state",
                                  timeout=20).read())
    check("a built-in field can be turned off",
          state["signup"]["collect"] == ["dept"], state["signup"]["collect"])
    post(owner, "/api/panel/signup/update", {"id": signup_id, "collect": ["dept", "grp"]})
    post(owner, "/api/panel/signup/update",
         {"id": signup_id, "opens_at": 2000, "closes_at": 1000}, expect=400)
    check("a start after the end is refused", True)
    post(owner, "/api/panel/signup/update",
         {"id": signup_id, "opens_at": 1000, "closes_at": 2000})
    check("an empty end is allowed",
          call(owner, "/api/panel/signup?id=%d" % signup_id)["signup"]["closesAt"] == 2000)

    print("== 报名：时间窗与关闭 ==")
    future = time.time() + 3600
    post(owner, "/api/panel/signup/update",
         {"id": signup_id, "filter_roster_id": 0, "opens_at": future,
          "closes_at": 0})
    code, res = submit({"sid": "20240210", "name": "太早了", "answers": {"f1": "住"}})
    check("before the start it refuses",
          code == 409 and "尚未开始" in res.get("error", ""), (code, res))
    post(owner, "/api/panel/signup/update",
         {"id": signup_id, "opens_at": 0, "closes_at": time.time() - 10})
    code, res = submit({"sid": "20240210", "name": "太晚了", "answers": {"f1": "住"}})
    check("after the deadline it refuses",
          code == 409 and "结束" in res.get("error", ""), (code, res))
    post(owner, "/api/panel/signup/update", {"id": signup_id, "closes_at": 0})
    post(owner, "/api/panel/signup/update", {"id": signup_id, "state": "closed"})
    code, res = submit({"sid": "20240210", "name": "关了", "answers": {"f1": "住"}})
    check("a closed 报名 refuses",
          code == 409 and "关闭" in res.get("error", ""), (code, res))
    post(owner, "/api/panel/signup/update", {"id": signup_id, "state": "open"})

    print("== 活动绑定名单与同步 ==")
    club = post(owner, "/api/panel/roster/import",
                {"name": "活动用名单",
                 "text": "学号,姓名,院系,班级\n20250001,甲一,计算机学院,计科2501\n"
                         "20250002,乙二,软件学院,软工2501\n"
                         "20250003,丙三,计算机学院,计科2502\n"})
    club_id = (club or {}).get("id")
    check("a roster to bind exists", bool(club_id), club)
    token = events[0]["id"]
    base = "/%s/api/admin" % token
    listed = call(owner, base + "/rosters", marker="lottery")
    check("the console lists the rosters this account may write",
          any(r["id"] == club_id for r in listed["rosters"]), listed)
    post(owner, base + "/roster_bind", {"roster_id": club_id}, marker="lottery")
    state = call(owner, base + "/state", marker="lottery")
    check("the binding is stored", state["roster"]["id"] == club_id, state.get("roster"))
    synced = post(owner, base + "/roster_sync", {}, marker="lottery")
    check("the first sync brings everybody in",
          synced and synced["result"]["added"] == 3, synced)
    state = call(owner, base + "/state", marker="lottery")
    sids = {x["sid"] for x in state["students"]}
    check("the people are on the activity roster",
          {"20250001", "20250002", "20250003"} <= sids, sorted(sids)[:6])
    check("the sync is remembered", bool(state["roster"]["syncedAt"]))

    again = post(owner, base + "/roster_sync", {}, marker="lottery")
    check("a second sync changes nothing",
          again["result"]["added"] == 0 and again["result"]["same"] == 3, again["result"])

    target = [e for e in call(owner, "/api/panel/roster/entries?id=%d" % club_id)["entries"]
              if e["sid"] == "20250003"][0]
    post(owner, "/api/panel/roster/entry/drop",
         {"id": club_id, "personId": target["personId"]})
    third = post(owner, base + "/roster_sync", {}, marker="lottery")
    check("someone who left the roster is reported, not deleted",
          [m["sid"] for m in third["result"]["missing"]] == ["20250003"], third["result"])
    state = call(owner, base + "/state", marker="lottery")
    check("but is still on the activity roster",
          "20250003" in {x["sid"] for x in state["students"]})
    check("and is counted as detached", state["roster"]["detached"] == 1,
          state["roster"])

    post(owner, base + "/roster_bind", {"roster_id": 0}, marker="lottery")
    state = call(owner, base + "/state", marker="lottery")
    check("the binding can be cleared", state["roster"]["id"] == 0, state["roster"])
    post(owner, base + "/roster_sync", {}, expect=409, marker="lottery")
    check("syncing without a binding is refused", True)

    print("== 展示屏幕：导入、引用、删除 ==")

    def make_zip(entries):
        """A zip of static files, base64 后的样子（和面板里发出去的一样）。"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries:
                zf.writestr(name, data)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    marker = "SCREEN-PACK-PROBE"
    # plain 是一个已登录的普通管理员（上面 banning 一节留下的会话）：能看，不能导入
    shelf = call(owner, "/api/panel/screen_packs")
    check("内置的屏幕在清单里", "classic" in [p["id"] for p in shelf["packs"]], shelf)
    check("站长能管理外部资源", shelf["canManage"] is True, shelf)
    check("普通管理员只能看",
          call(plain, "/api/panel/screen_packs")["canManage"] is False)

    post(plain, "/api/panel/screen_pack/upload", {
        "filename": "nope.zip", "data": make_zip([("screen.html", b"x")])
    }, expect=403)
    check("普通管理员传不上去", True)

    good = make_zip([("screen.html",
                      ("<html><body>" + marker + "</body></html>").encode()),
                     ("pack.css", b"body{margin:0}")])
    added = post(owner, "/api/panel/screen_pack/upload",
                 {"filename": "Demo Screen.zip", "data": good})
    pack_id = (added or {}).get("pack", {}).get("id")
    check("导入后有自己的标识", pack_id == "demo-screen", added)
    check("导入的项标为已导入",
          bool(added) and added["pack"]["source"] == "已导入", added)
    check("清单里能看到它",
          pack_id in [p["id"] for p in call(owner, "/api/panel/screen_packs")["packs"]])

    base = "/%s/api/admin" % events[0]["id"]
    post(owner, base + "/settings", {"screen_pack": pack_id}, marker="lottery")
    page = call(anon, "/%s/screen" % events[0]["id"])
    check("活动引用后大屏换成这一份",
          isinstance(page, bytes) and marker.encode() in page, (page or b"")[:80])
    sheet = call(anon, "/%s/screen/pack.css" % events[0]["id"])
    check("包内其它文件按 screen/ 取",
          isinstance(sheet, bytes) and b"margin:0" in sheet, (sheet or b"")[:80])
    post(owner, base + "/settings", {"screen_pack": ""}, marker="lottery")
    page = call(anon, "/%s/screen" % events[0]["id"])
    check("不引用时回到活动自带的展示页",
          isinstance(page, bytes) and marker.encode() not in page
          and "大屏".encode() in page, (page or b"")[:80])
    post(owner, base + "/settings", {"screen_pack": "no-such-pack"}, expect=400,
         marker="lottery")
    check("不存在的那一份引用不了", True)

    mixed = post(owner, "/api/panel/screen_pack/upload", {
        "filename": "Mixed.zip",
        "data": make_zip([("screen.html", b"<html></html>"),
                          ("../escape.js", b"alert(1)"),
                          ("notes.txt", b"hello")])})
    check("不认得的条目被丢掉并回报",
          bool(mixed) and len(mixed["skipped"]) == 2, mixed)
    check("只写进了认得的文件", mixed["pack"]["files"] == 1, mixed["pack"])

    post(owner, "/api/panel/screen_pack/upload", {
        "filename": "NoPage.zip", "data": make_zip([("pack.css", b"x")])}, expect=400)
    check("没有入口页的包拒收", True)
    post(owner, "/api/panel/screen_pack/upload", {
        "filename": "Broken.zip", "data": "bm90IGEgemlw"}, expect=400)
    check("不是 zip 的内容拒收", True)

    post(plain, "/api/panel/screen_pack/delete", {"id": pack_id}, expect=403)
    check("普通管理员删不掉", True)
    post(owner, "/api/panel/screen_pack/delete", {"id": "classic"}, expect=400)
    check("内置的删不掉", True)
    gone = post(owner, "/api/panel/screen_pack/delete", {"id": pack_id})
    check("删掉导入的那一份", (gone or {}).get("ok") is True, gone)
    check("删完清单里就没有了",
          pack_id not in [p["id"] for p in call(owner, "/api/panel/screen_packs")["packs"]])
    post(owner, "/api/panel/screen_pack/delete", {"id": "mixed"})
    check("测试导入的第二份也清掉了", True)

    print("== API 令牌：创建、按范围使用、吊销 ==")

    def bearer(path, token, body=None, expect=200):
        """一个不带 cookie、也不带 CSRF 标记的外部程序。"""
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + token}
        if payload:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(ROOT + path, data=payload, headers=headers,
                                     method="POST" if payload else "GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                status, raw = res.status, res.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        if status != expect:
            problems.append("%s(bearer) -> %d (expected %d): %s"
                            % (path, status, expect, raw[:160]))
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return raw

    made = post(owner, "/api/panel/token/create",
                {"name": "测试脚本", "scopes": ["read"], "days": 0})
    plain = (made or {}).get("token") or ""
    check("拿到 cae- 开头的明文", plain.startswith("cae-") and len(plain) > 40, plain)
    check("列表里只有首尾几位",
          (made or {}).get("item", {}).get("masked", "").find("•••••") >= 0, made)
    shelf = call(owner, "/api/panel/tokens")
    check("清单里能看到它",
          any(t["id"] == made["item"]["id"] for t in shelf["tokens"]), shelf)
    check("清单里既没有明文也没有哈希",
          plain not in json.dumps(shelf) and "hash" not in json.dumps(shelf))

    who = bearer("/api/panel/me", plain)
    check("令牌可以问「我是谁」", who and who.get("authed") is True, who)
    check("并且说出这是哪张令牌", (who or {}).get("viaToken") == "测试脚本", who)
    check("读接口放行",
          bearer("/api/panel/events", plain) is not None)
    bearer("/api/panel/roster/import", plain,
           {"name": "x", "text": "学号,姓名\n1,甲"}, expect=403)
    check("只有「读取」的令牌写不了东西", True)
    bearer("/api/panel/host", plain, expect=403)
    check("平台级接口要「平台管理」范围", True)
    bearer("/api/panel/tokens", plain, expect=403)
    check("令牌不能管理令牌", True)
    bearer("/api/panel/events", "cae-not-a-real-token", expect=401)
    check("乱写的令牌一律 401", True)

    writer = post(owner, "/api/panel/token/create",
                  {"name": "写入脚本", "scopes": ["read", "write"], "days": 30})
    plain2 = writer["token"]
    made_roster = bearer("/api/panel/roster/import", plain2,
                         {"name": "令牌建的名单",
                          "text": "学号,姓名,院系\n20259001,甲一,计算机学院"})
    check("有「写入」就能写", made_roster and made_roster.get("id"), made_roster)
    bearer("/api/panel/host", plain2, expect=403)
    check("仍然碰不到平台级接口", True)
    bearer("/api/panel/account/role", plain2, {"id": 1, "role": "admin"}, expect=403)
    check("要重新输密码的动作令牌做不到", True)

    entries = call(owner, "/api/panel/audit?limit=40")
    rows = json.dumps(entries.get("audit") or [], ensure_ascii=False)
    check("审计里写明经过哪张令牌", "令牌「写入脚本」" in rows, rows[:200])
    check("操作人仍是本人", "owner" in rows, rows[:200])
    check("创建令牌本身也留痕", "token_create" in rows, rows[:400])

    post(owner, "/api/panel/token/revoke", {"id": writer["item"]["id"]})
    bearer("/api/panel/events", plain2, expect=401)
    check("吊销之后立刻失效", True)
    check("另一张不受影响", (bearer("/api/panel/me", plain) or {}).get("authed") is True)

    print("== 登录名：字符、保留词、被占用 ==")
    taker = session()
    post(taker, "/api/panel/register", {
        "username": "nameclash", "name": "甲", "password": "name-pass-1"}, expect=200)
    again = post(taker, "/api/panel/register", {
        "username": "NameClash", "name": "乙", "password": "name-pass-1"}, expect=409)
    check("重名（不分大小写）只说「不可用」",
          again and again.get("error") == "这个登录名不可用", again)
    reserved = post(taker, "/api/panel/register", {
        "username": "admin", "name": "丙", "password": "name-pass-1"}, expect=409)
    check("保留词与被占用是同一句话",
          reserved and reserved.get("error") == again.get("error"), reserved)
    for bad in ("a.b", "张老师", "ab", "a" * 25, "has space"):
        post(taker, "/api/panel/register", {
            "username": bad, "name": "丁", "password": "name-pass-1"}, expect=400)
    check("点和中文、过短过长都不收", True)

    print("== 运维助手：配置、一轮对话、权限 ==")

    class StubModel:
        """一个最小的 OpenAI 兼容端点：先要工具，再说人话。"""

        def __init__(self):
            import threading
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            self.requests = []
            stub = self

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    stub.requests.append({"auth": self.headers.get("Authorization"),
                                          "body": body})
                    system = " ".join(m.get("content") or ""
                                      for m in body.get("messages", [])
                                      if m.get("role") == "system")
                    tool_msgs = [m for m in body.get("messages", [])
                                 if m.get("role") == "tool"]
                    if "标题" in system:
                        # 起名字那一路：不给工具，只要一句短标题
                        message = {"role": "assistant", "content": "活动报名排查。"}
                    elif "文档" in " ".join(m.get("content") or ""
                                            for m in body.get("messages", [])
                                            if m.get("role") == "user"):
                        message = {"role": "assistant", "content": "",
                                   "tool_calls": [{"id": "call_doc", "type": "function",
                                                   "function": {"name": "read_doc",
                                                                "arguments":
                                                                    '{"path": "doc/README.md"}'}}]}
                    elif tool_msgs:
                        titles = re.findall(r'"title": "([^"]+)"',
                                            tool_msgs[-1].get("content") or "")
                        message = {"role": "assistant",
                                   "content": "你能看到的活动：" + ("、".join(titles) or "（没有）")}
                    else:
                        asked = ""
                        for m in body.get("messages", []):
                            if m.get("role") == "user":
                                asked = m.get("content") or ""
                        name = "list_events"
                        if "审计" in asked:
                            name = "recent_audit"
                        elif "服务器" in asked:
                            name = "host_stats"
                        message = {"role": "assistant", "content": "",
                                   "tool_calls": [{"id": "call_1", "type": "function",
                                                   "function": {"name": name,
                                                                "arguments": "{}"}}]}
                    raw = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

                def do_GET(self):
                    # GET /models：拉模型列表
                    if not self.path.endswith("/models"):
                        self.send_response(404)
                        self.end_headers()
                        return
                    raw = json.dumps({"object": "list", "data": [
                        {"id": "stub-flash", "name": "Stub Flash"},
                        {"id": "stub-pro", "name": "Stub Pro"}]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.port = self.server.server_address[1]
            threading.Thread(target=self.server.serve_forever, daemon=True).start()

        def url(self):
            return "http://127.0.0.1:%d/v1" % self.port

        def stop(self):
            self.server.shutdown()

    stub = StubModel()
    try:
        state = call(owner, "/api/panel/assistant")
        check("助手一开始没有配置模型",
              state and state["config"]["ready"] is False, state)
        early = post(owner, "/api/panel/assistant/chat",
                     {"messages": [{"role": "user", "content": "你好"}]}, expect=400)
        check("没配置时问不了，并说明去配什么",
              early and "配置" in (early.get("error") or ""), early)
        saved = post(owner, "/api/panel/assistant/config",
                     {"base": stub.url(), "model": "stub-model", "key": "sk-test-123"})
        check("保存配置后进入就绪", saved and saved["config"]["ready"] is True, saved)
        check("接口不回传密钥", "sk-test-123" not in json.dumps(saved), saved["config"])
        bare = post(owner, "/api/panel/assistant/config", {"base": "", "model": ""})
        check("只填密钥也算配好：地址与模型有默认值",
              bare and bare["config"]["ready"] is True
              and bare["config"]["base"].startswith("https://"), bare)
        check("默认模型是 deepseek-flash",
              bare and bare["config"]["model"] == "deepseek-flash", bare["config"]["model"]
              if bare else None)
        post(owner, "/api/panel/assistant/config", {"base": "ftp://nope"}, expect=400)
        check("接口地址必须是 http(s)", True)
        post(owner, "/api/panel/assistant/config",
             {"base": stub.url(), "model": "stub-model"})
        models = post(owner, "/api/panel/assistant/models", {})
        ids = [m["id"] for m in (models or {}).get("models") or []]
        check("能从服务端拉到模型清单", ids == ["stub-flash", "stub-pro"], ids)

        answer = post(owner, "/api/panel/assistant/chat",
                      {"messages": [{"role": "user", "content": "我有哪些活动？"}]})
        check("拿到了回答", bool(answer and answer.get("reply")), answer)
        check("回答里带上了活动名", bool(answer and answer.get("reply", "")
                                         .startswith("你能看到的活动：")
                                         and "（没有）" not in answer["reply"]), answer)
        check("过程里记下了调过的工具",
              answer and any(s["tool"] == "list_events" for s in answer["steps"]), answer)
        check("密钥在服务端填进请求头",
              stub.requests and stub.requests[0]["auth"] == "Bearer sk-test-123",
              stub.requests[0]["auth"] if stub.requests else None)
        check("请求里带了工具清单",
              stub.requests and stub.requests[0]["body"].get("tools"))
        names = [t["function"]["name"] for t in
                 (stub.requests[0]["body"].get("tools") or [])]
        check("站长拿得到全部工具", "host_stats" in names and "recent_audit" in names, names)

        titled = post(owner, "/api/panel/assistant/title",
                      {"text": "我最近的活动为什么没人报名？"})
        check("能给会话起个短名字",
              titled and titled.get("title") == "活动报名排查", titled)
        check("起名字那一路不要工具", not (stub.requests[-1]["body"].get("tools")),
              stub.requests[-1]["body"])

        print("== 助手会去读随应用装好的文档 ==")
        before = len(stub.requests)
        post(owner, "/api/panel/assistant/chat",
             {"messages": [{"role": "user", "content": "文档里怎么说名单的"}]})
        first = (stub.requests[before]["body"].get("messages") or [])
        check("工具清单里有读文档那两个",
              "list_docs" in json.dumps(stub.requests[before]["body"].get("tools") or [])
              and "read_doc" in json.dumps(stub.requests[before]["body"].get("tools") or []))
        tool_msgs = [m for m in (stub.requests[before + 1]["body"].get("messages") or [])
                     if m.get("role") == "tool"]
        check("读回来的正文进了上下文",
              tool_msgs and "文档目录" in (tool_msgs[-1].get("content") or ""),
              (tool_msgs[-1].get("content") or "")[:80] if tool_msgs else first[:1])

        print("== 平台自己的提示词：数据目录里放一份 markdown ==")
        version = (call(owner, "/api/panel/me") or {}).get("version") or ""
        prompt_file = os.path.join(DATA, "assistant_prompt.md")
        with open(prompt_file, "w", encoding="utf-8") as fh:
            fh.write("# 本社团的习惯\n\n"
                     "- 回答里把活动称作「场次」。\n"
                     "- 现在说话的人是 ${user.username}（${user.role}）。\n"
                     "- 平台版本 ${panel.version}，今天 ${time.date}。\n")
        os.utime(prompt_file, (time.time() + 1, time.time() + 1))
        before = len(stub.requests)
        post(owner, "/api/panel/assistant/chat",
             {"messages": [{"role": "user", "content": "我们有几个活动"}]})
        system = (stub.requests[before]["body"]["messages"] or [{}])[0]
        body = system.get("content") or ""
        check("平台提示词进了系统消息",
              system.get("role") == "system" and "把活动称作" in body, body[-120:])
        check("固定规则还在", "只回答平台范围内的事" in body)
        check("变量换成了这个人", "是 owner（站长）" in body, body[-160:])
        check("版本与日期也换掉了",
              version and ("平台版本 " + version) in body, body[-160:])
        check("没有剩下没换的占位符", "${user." not in body and "${panel." not in body,
              body[-160:])
        check("系统提示里写明了是谁在用",
              "当前使用者：站长（owner）" in body, body[:400])
        os.remove(prompt_file)
        before = len(stub.requests)
        post(owner, "/api/panel/assistant/chat",
             {"messages": [{"role": "user", "content": "我们有几个活动"}]})
        system = (stub.requests[before]["body"]["messages"] or [{}])[0]
        check("删掉文件就回到只有固定规则",
              "把活动称作" not in (system.get("content") or ""),
              (system.get("content") or "")[-80:])

        # 普通管理员：模型连"有服务器状态这个工具"都不该知道
        post(anon2, "/api/panel/register", {"username": "asprobe", "name": "助手权限",
                                            "password": "asprobe-pass-1"})
        probe_id = [a for a in call(owner, "/api/panel/accounts")["accounts"]
                    if a["username"] == "asprobe"][0]["id"]
        post(owner, "/api/panel/account/decide", {"id": probe_id, "action": "approve"})
        probe = session()
        post(probe, "/api/panel/login",
             {"username": "asprobe", "password": "asprobe-pass-1"})
        post(probe, "/api/panel/assistant/config",
             {"base": stub.url(), "model": "stub-model", "key": "sk-other"})
        before = len(stub.requests)
        denied = post(probe, "/api/panel/assistant/chat",
                      {"messages": [{"role": "user", "content": "看看审计日志"}]})
        sent = [t["function"]["name"] for t in
                (stub.requests[before]["body"].get("tools") or [])]
        check("普通管理员根本看不到站长的工具",
              "host_stats" not in sent and "recent_audit" not in sent, sent)
        check("他看得到的那些还在", "list_events" in sent and "host_stats" not in sent, sent)
        steps = {s["tool"]: s["ok"] for s in (denied or {}).get("steps") or []}
        check("他也调不动审计日志", steps.get("recent_audit") is not True, steps)
        server_ask = post(probe, "/api/panel/assistant/chat",
                          {"messages": [{"role": "user", "content": "服务器状态怎么样"}]})
        steps2 = {s["tool"]: s["ok"] for s in (server_ask or {}).get("steps") or []}
        check("也调不动服务器状态", steps2.get("host_stats") is not True, steps2)

        rows = json.dumps(call(owner, "/api/panel/audit?limit=60").get("audit") or [],
                          ensure_ascii=False)
        check("问一句不往审计里塞东西", "assistant_ask" not in rows, rows[:200])
    finally:
        stub.stop()

    print("== 对话存档：存、取、配额、删除、导出 ==")
    saved = post(owner, "/api/panel/assistant/session/save",
                 {"title": "第一次", "messages": [
                     {"role": "user", "content": "平台上现在有多少活动？"},
                     {"role": "assistant", "content": "你有 1 场活动。",
                      "steps": [{"tool": "list_events", "ok": True}]}]})
    sid = (saved or {}).get("id")
    check("存下来了", bool(sid), saved)
    check("按账号算出了用量", saved and saved["used"] > 0 and saved["quota"] > 0, saved)
    listed = call(owner, "/api/panel/assistant/sessions")
    check("列表里有它",
          [s["id"] for s in listed["sessions"]][:1] == [sid], listed["sessions"])
    back = call(owner, "/api/panel/assistant/session?id=%d" % sid)
    check("取回来的消息是两条",
          len((back or {}).get("session", {}).get("messages") or []) == 2, back)

    # 配额是平台资源：超管调别人的配额要留痕
    target_id = [a for a in call(owner, "/api/panel/accounts")["accounts"]
                 if a["username"] == "asprobe"][0]["id"]
    quota = post(owner, "/api/panel/account/quota", {"id": target_id, "quota": 1024})
    check("能改某个账号的配额",
          quota and quota["account"]["chatQuota"] == 1024, quota)
    rows = json.dumps(call(owner, "/api/panel/audit?limit=40").get("audit") or [],
                      ensure_ascii=False)
    check("改配额写进了审计", "account_quota" in rows and "1024" in rows, rows[:200])
    post(probe, "/api/panel/account/quota", {"id": target_id, "quota": 4096}, expect=403)
    check("普通管理员改不了别人的配额", True)

    # 配额很小的时候，超了就拒收
    post(owner, "/api/panel/account/quota", {"id": target_id, "quota": 200})
    big = post(probe, "/api/panel/assistant/session/save",
               {"title": "超了", "messages": [
                   {"role": "user", "content": "x" * 400}]}, expect=413)
    check("超过配额时明确拒绝", big and "存档" in (big.get("error") or ""), big)

    export = call(owner, "/api/panel/assistant/session/export?id=%d" % sid)
    check("导出的是一份 Markdown",
          isinstance(export, bytes) and "你有 1 场活动" in export.decode("utf-8"),
          (export or b"")[:60])
    gone = post(owner, "/api/panel/assistant/session/delete", {"id": sid})
    check("删除后用量归零", gone and gone["used"] == 0, gone)
    check("列表里也没有了",
          not [s for s in call(owner, "/api/panel/assistant/sessions")["sessions"]
               if s["id"] == sid])

    print("== event pages still reachable ==")
    token = events[0]["id"]
    board = call(anon, "/%s/api/board" % token)
    check("event api still works under its token", board and board.get("ok"), board)

    print()
    if problems:
        print("FAILED %d check(s):" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
