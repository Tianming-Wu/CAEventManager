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
HEADER = {"X-Requested-With": "xlottery"}
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


def call(opener, path, body=None, expect=200, method=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(HEADER)
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


def post(opener, path, body=None, expect=200):
    return call(opener, path, body if body is not None else {}, expect=expect, method="POST")


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
            "Content-Type: application/json\r\nX-Requested-With: xlottery\r\n"
            "Content-Length: %d\r\n\r\n" % (host, len(bad))).encode()
    ok = b"GET /api/panel/me HTTP/1.1\r\nHost: " + host.encode() + b"\r\n" \
         b"X-Requested-With: xlottery\r\nConnection: close\r\n\r\n"
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

    accs = call(owner, "/api/panel/accounts")
    pending = [a for a in accs["accounts"] if a["status"] == "pending"]
    check("owner sees the pending request", len(pending) == 1, accs["accounts"])
    teacher_id = pending[0]["id"]

    print("== approval ==")
    post(owner, "/api/panel/account/decide", {"id": teacher_id, "action": "approve"})
    res = post(teacher, "/api/panel/login",
               {"username": "teacher", "password": "teacher-pass-1"})
    check("approved account can log in", res and res["ok"], res)
    check("new account is a plain admin",
          call(teacher, "/api/panel/me")["me"]["role"] == "admin")
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
