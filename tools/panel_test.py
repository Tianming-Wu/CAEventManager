#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Account/tier test for the master panel that now lives at the service root.

    python tools/panel_test.py http://127.0.0.1:8931 [owner_password]

The owner password is read from data/owner_password.txt when not given.
"""
import http.cookiejar
import json
import os
import socket
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931").rstrip("/")
ROOT = BASE
HEADER = {"X-Requested-With": "CAEventManager"}
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    plain = session()
    # member's password was changed by the section above
    post(plain, "/api/panel/login", {"username": "member", "password": "member-pass-2"})
    call(plain, "/api/panel/host", expect=403)
    check("a plain admin cannot read them", True)

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
    check("and the roster shows up under 我的名单",
          call(helper, "/api/panel/rosters?scope=mine")["total"] == 1)
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

    allseen = call(teacher, "/api/panel/rosters?scope=all")
    check("a superadmin can count every roster", allseen["total"] >= 2, allseen["total"])
    left = [r for r in allseen["rosters"] if r["id"] == roster_id][0]
    check("an admin's view of someone else's roster is read-only",
          left["editable"] is False, left)

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
