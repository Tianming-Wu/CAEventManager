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
import shutil
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


def modules_dir():
    """这个实例装模块的目录：套件往里放临时模块，跑完就删。"""
    return os.environ.get("LOTTERY_MODULES_DIR") or os.path.join(HERE, "modules")


def _bare_zip():
    """一个没有 module.json 的压缩包：用来试「这玩意儿不是模块」。"""
    import zipfile as _zip
    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w") as zf:
        zf.writestr("readme.md", "not a module")
    return buf.getvalue()


def _module_zip(module_id):
    """一个 id 与平台自带撞车的模块包。"""
    import zipfile as _zip
    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w") as zf:
        zf.writestr("module.json", json.dumps({
            "id": module_id, "name": "冒名模块", "version": "9.9"}))
    return buf.getvalue()

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))
import xlsx  # noqa: E402  读写自测要用同一份实现

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


def sse_call(opener, path, body=None):
    """打一条 SSE：把每一段 data: 解析成事件列表（连接关闭即结束）。"""
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, method="POST",
        headers={"X-Requested-With": "CAEventManager",
                 "Content-Type": "application/json"})
    events = []
    with opener.open(req, timeout=60) as res:
        for raw in res:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                events.append(json.loads(payload))
    return events


def post(opener, path, body=None, expect=200, marker="CAEventManager"):
    return call(opener, path, body if body is not None else {}, expect=expect,
                method="POST", marker=marker)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """页面会把人送去别处：要看见跳转目标，就不能让 urllib 先跟过去。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_page(opener, path):
    """取一页，返回 (状态码, 跳转目标)。同一份 cookie 用来判断身份。"""
    jar = None
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.HTTPCookieProcessor):
            jar = handler.cookiejar
    probe = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar or http.cookiejar.CookieJar()),
        _NoRedirect())
    try:
        with probe.open(ROOT + path, timeout=20) as res:
            res.read()
            return res.status, res.geturl()
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, exc.headers.get("Location")


def post_bytes(opener, path, data, expect=200, ctype="application/octet-stream"):
    """原样上送一段字节（xlsx 是 zip，不能当 JSON）。"""
    req = urllib.request.Request(
        BASE + path, data=data, method="POST",
        headers={"Content-Type": ctype, "X-Requested-With": "CAEventManager"})
    try:
        with opener.open(req) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == expect:
            return json.loads(exc.read().decode("utf-8") or "{}")
        raise


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
    # 只看状态行：「501」这三个字符在 JSON 正文里也可能出现（时间戳就撞过），
    # 拿整段文本找会误报
    check("rejected login + follow-up request keep the socket in sync",
          "Unsupported method" not in text and "HTTP/1.1 501" not in text
          and text.count("HTTP/1.1 ") >= 2,
          "responses=%d %r" % (text.count("HTTP/1.1 "), text[:120]))
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
          and "注册".encode() in reg and "参加活动并不需要注册账户".encode() in reg,
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
        script = open(os.path.join(HERE, "assets", "js", script_name),
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
    check("the activity the runner made is listed", len(events) == 1, events)
    ev = events[0] if events else {}
    check("event links point at the token path",
          bool(ev) and "/" in ev["admin"] and ev["token"] in ev["url"], ev)
    check("the event has a numeric id apart from its token",
          isinstance(ev.get("seq"), int) and ev["seq"] >= 1
          and ev.get("token") != str(ev.get("seq")), ev)
    # 活动号不出现在地址里（"127.0.0.1" 里也有数字，所以不能只判断子串）
    check("the id is not part of the address",
          ("/%d/" % ev["seq"]) not in ev["admin"], ev.get("admin"))
    check("the event records its module", bool(ev.get("kind")), ev)
    check("the owner is the owner", ev.get("role") == "owner", ev.get("role"))

    accs = call(owner, "/api/panel/accounts")
    pending = [a for a in accs["accounts"] if a["status"] == "pending"]
    check("owner sees the pending request", len(pending) == 1, accs["accounts"])
    teacher_id = pending[0]["id"]
    check("the owner is numbered from 10000",
          [a["uid"] for a in accs["accounts"] if a["role"] == "owner"] == [10000],
          accs["accounts"])
    check("every account has its own number",
          len({a["uid"] for a in accs["accounts"]}) == len(accs["accounts"]),
          accs["accounts"])

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

    print("== 服务有效性提示 ==")
    svc = call(owner, "/api/panel/service") or {}
    check("接口回一份清单", isinstance(svc.get("items"), list), svc)
    check("带版本号与时间", bool(svc.get("version")) and bool(svc.get("at")), sorted(svc))
    keys = {"id", "kind", "level", "title", "body", "sticky"}
    check("每条只有这几个键，没有夹带动作",
          all(set(i) == keys for i in svc.get("items") or []), svc.get("items"))
    check("级别用通知那三档",
          all(i["level"] in ("info", "important", "critical")
              for i in svc.get("items") or []), svc.get("items"))
    check("普通管理员看到的是同一份",
          (call(plain, "/api/panel/service") or {}).get("items") == svc.get("items"),
          (call(plain, "/api/panel/service") or {}).get("items"))

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
    check("模块管理是站长的页面：超管看不到",
          call(teacher, "/api/panel/modules", expect=403) is not None)
    mods = call(owner, "/api/panel/modules")["modules"]
    mod = mods[0]
    check("a module knows where it came from", mod["builtin"] is True, mod)
    check("and whether it is switched on", mod["enabled"] is True, mod)
    check("a module ships its own presets", "salon" in mod["templates"], mod)
    check("and says how many activities live on it",
          isinstance(mod.get("events"), int) and mod["events"] >= 1, mod)

    print("== 停用模块：要提权，而且真的停住正在用的活动 ==")
    post(owner, "/api/panel/sudo/exit")
    refused = post(owner, "/api/panel/module/toggle",
                   {"id": "nwuca.lottery", "enabled": False}, expect=403)
    check("停用模块要进 sudo",
          refused and refused.get("sudoRequired") is True, refused)
    check("没提权时什么都没变",
          call(owner, "/api/panel/modules")["modules"][0]["enabled"] is True)
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    off = post(owner, "/api/panel/module/toggle",
               {"id": "nwuca.lottery", "enabled": False})
    check("提权之后可以停用，并说出牵连几个活动",
          off and off["enabled"] is False and off["affected"] >= 1, off)
    trail = [a for a in call(owner, "/api/panel/audit?limit=10")["audit"]
             if a["action"] == "module_disable"]
    check("审计里记了这次停用", bool(trail), trail)
    check("说明写英文键值，不写中文句子",
          trail and trail[0]["detail"].startswith("affected:"), trail[:1])
    stopped_page = call(owner, "/" + ev["token"] + "/admin", expect=503)
    check("正在用它的活动页面变成一张说明页",
          isinstance(stopped_page, bytes)
          and "此活动不可用" in stopped_page.decode("utf-8", "replace"), stopped_page[:120])
    stopped_api = call(owner, "/" + ev["token"] + "/api/admin/state", expect=503)
    check("活动接口也不再提供",
          stopped_api and "已被管理员停用" in (stopped_api.get("error") or ""), stopped_api)
    listed = call(owner, "/api/panel/events")["events"]
    check("活动列表上标着它停着",
          all(e["stopped"] for e in listed if e["seq"] == ev["seq"]), listed)
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
    check("启用之后活动立刻回来",
          call(owner, "/" + ev["token"] + "/api/admin/state") is not None)
    listed = call(owner, "/api/panel/events")["events"]
    check("列表上的标记也跟着去掉",
          not any(e["stopped"] for e in listed if e["seq"] == ev["seq"]), listed)

    print("== 导入模块：上传 → 看清能力 → 提权安装 ==")
    import zipfile as _zip
    probe_id = "nwuca.superprobe"
    probe_zip = io.BytesIO()
    with _zip.ZipFile(probe_zip, "w") as zf:
        # 压缩包里多包一层目录也算数（顶层那个目录会被去掉）
        zf.writestr("superprobe/module.json", json.dumps({
            "id": probe_id, "name": "导入测试模块", "version": "1.2.0",
            "author": "panel_test",
            "features": {"shortlink": {}, "super.anyshortlink": True}}))
        zf.writestr("superprobe/doc.md", "# 导入测试模块\n")
    try:
        uploaded = post(owner, "/api/panel/module/upload",
                        {"filename": "superprobe.zip",
                         "data": base64.b64encode(probe_zip.getvalue()).decode()})
        check("上传之后先只做检查，不落盘",
              uploaded and uploaded.get("stage") and uploaded.get("id") == probe_id,
              uploaded)
        check("检查结果里带着它申请的能力",
              [f["name"] for f in (uploaded.get("superFeatures") or [])]
              == ["super.anyshortlink"]
              and "shortlink" in (uploaded.get("features") or []),
              (uploaded.get("features"), uploaded.get("superFeatures")))
        check("高危能力带着一句后果",
              "短链接" in ((uploaded.get("superFeatures") or [{}])[0].get("note") or ""),
              uploaded.get("superFeatures"))
        check("还没装的时候列表里没有它",
              probe_id not in [m["id"] for m in call(owner, "/api/panel/modules")["modules"]])
        post(owner, "/api/panel/sudo/exit", {})
        refused = post(owner, "/api/panel/module/install",
                       {"stage": uploaded["stage"]}, expect=403)
        check("装模块要先进 sudo",
              refused and refused.get("sudoRequired") is True, refused)
        post(owner, "/api/panel/sudo", {"password": owner_password()})
        installed = post(owner, "/api/panel/module/install",
                         {"stage": uploaded["stage"], "version": uploaded["version"]})
        check("提权之后装上了", installed and installed.get("id") == probe_id, installed)
        mods = call(owner, "/api/panel/modules")["modules"]
        got = [m for m in mods if m["id"] == probe_id]
        check("列表里出现，并且标着不是自带的",
              got and got[0]["builtin"] is False, got)
        check("声明的高危能力直接生效",
              got and "super.anyshortlink" in (got[0].get("features") or {}),
              got[0].get("features") if got else None)
        trail = [a for a in call(owner, "/api/panel/audit?limit=10")["audit"]
                 if a["action"] == "module_install"]
        check("审计里记了装的是什么", trail and trail[0]["target"] == probe_id,
              trail[:1])
        check("压缩包里没有 module.json 就说清楚",
              post(owner, "/api/panel/module/upload",
                   {"filename": "x.zip",
                    "data": base64.b64encode(_bare_zip()).decode()}, expect=400) is not None)
        check("不能用同名的模块顶掉平台自带的",
              post(owner, "/api/panel/module/upload",
                   {"filename": "dup.zip",
                    "data": base64.b64encode(_module_zip("nwuca.lottery")).decode()},
                   expect=400) is not None)
        # 卸载：模块上没有活动就删得掉
        post(owner, "/api/panel/module/remove", {"id": probe_id})
        check("卸载之后列表里没有它",
              probe_id not in [m["id"] for m in call(owner, "/api/panel/modules")["modules"]])
        check("平台自带的模块不给卸载",
              post(owner, "/api/panel/module/remove", {"id": "nwuca.lottery"},
                   expect=409) is not None)
    finally:
        shutil.rmtree(os.path.join(modules_dir(), probe_id), ignore_errors=True)

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
        headers={"X-Requested-With": "CAEventManager"})
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
        headers={"X-Requested-With": "CAEventManager"})
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
          [m["name"] for m in roster["members"]] == ["站长"], roster["members"])
    found = call(owner, base + "/api/admin/people?q=teach")
    check("a search finds an account that is not in yet",
          found and [a["label"] for a in found["people"]] == ["张老师"], found)
    check("and never hands out a login name",
          found and all("username" not in a for a in found["people"]), found)
    post(owner, base + "/api/admin/member",
         {"accountId": teacher_id, "action": "invite"}, marker="CAEventManager")
    check("the invitation is waiting for them", True)
    roster = call(owner, base + "/api/admin/members")
    seat = [m for m in roster["members"] if m["name"] == "张老师"][0]
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
         marker="CAEventManager")
    check("a plain member cannot manage members", True)
    post(owner, base + "/api/admin/member",
         {"accountId": teacher_id, "action": "revoke"}, marker="CAEventManager")
    roster = call(owner, base + "/api/admin/members")
    seat = [m for m in roster["members"] if m["accountId"] == teacher_id][0]
    check("a revoked seat stays on the record", seat["state"] == "revoked", seat)
    check("with the time it happened", seat["stateAt"] > 0, seat)
    check("their console access ends",
          call(teacher, base + "/api/admin/state", expect=403) is not None)
    check("and the activity leaves their list",
          all(e["seq"] != ev["seq"] for e in call(teacher, "/api/panel/events")["events"]))
    post(owner, base + "/api/admin/member",
         {"accountId": teacher_id, "action": "invite"}, marker="CAEventManager")
    check("a revoked seat can be invited again",
          call(teacher, "/api/panel/invites")["invites"][0]["seq"] == ev["seq"])
    post(teacher, "/api/panel/invite", {"seq": ev["seq"], "action": "decline"})
    roster = call(owner, base + "/api/admin/members")
    seat = [m for m in roster["members"] if m["accountId"] == teacher_id][0]
    check("a decline is recorded too", seat["state"] == "declined", seat)
    post(owner, base + "/api/admin/member",
         {"accountId": owner_id, "action": "revoke"}, expect=400,
         marker="CAEventManager")
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

    print("== 活动归档：打包、下载、还原、删除 ==")
    spare = post(owner, "/api/panel/events/create",
                 {"template": "nwuca.salon", "title": "归档用例"})
    aseq = spare["event"]["seq"] if spare and spare.get("event") else 0
    check("建出一场用来归档的活动", bool(aseq), spare)
    if aseq:
        # 活动正常开启时：负责人能下载
        blob = call(owner, "/api/panel/event/download?seq=%d" % aseq)
        check("开启时负责人能下载数据", blob is not None and len(blob) > 200, blob if blob is None else len(blob))
        # 归档自己的活动不要密码（超管能放回来，不是不可逆的事）
        post(owner, "/api/panel/sudo/exit", {})
        res = post(owner, "/api/panel/event/archive", {"seq": aseq})
        check("没提权也能归档自己的活动", res and res.get("archived"), res)
        # 后面的取消归档、下载、彻底删除都要提权
        post(owner, "/api/panel/sudo", {"password": owner_password()})
        listed = [e for e in call(owner, "/api/panel/events?archived=1")["events"]
                  if e["seq"] == aseq]
        check("归档后退到已归档列表", len(listed) == 1, listed)
        # 归档之后数据由管理员保管
        post(owner, "/api/panel/event/unarchive", {"seq": aseq})
        restored = call(owner, "/api/panel/events")["events"]
        check("取消归档之后回到正常列表",
              any(e["seq"] == aseq for e in restored), [e["seq"] for e in restored])
        again = call(owner, "/api/panel/event/download?seq=%d" % aseq)
        check("还原之后又能下载", again is not None and len(again) > 200,
              again if again is None else len(again))
        post(owner, "/api/panel/event/archive", {"seq": aseq})
        post(owner, "/api/panel/event/delete", {"seq": aseq})
        gone = [e for e in call(owner, "/api/panel/events")["events"]
                if e["seq"] == aseq]
        check("已归档的活动能彻底删除", gone == [], gone)

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

    print("== 名单：xlsx 导出与导入 ==")
    book = call(owner, "/api/panel/roster/export.xlsx?id=%d" % roster_id)
    check("导出 xlsx 是 zip（xlsx 的本质）",
          isinstance(book, bytes) and book[:2] == b"PK", (book or b"")[:8])
    rows = xlsx.parse(book)
    check("读回来只有表头 + 数据行", rows and rows[0][:4] == ["学号", "姓名", "院系", "班级"],
          rows[:2])
    check("学号还是文本，前导零没丢",
          any(r and r[0].startswith("2024") for r in rows[1:]), rows[1:3])

    payload = xlsx.build([["学号", "姓名", "院系", "班级"],
                          ["0001234", "戊五", "外国语学院", "英语 2501"],
                          ["20240200", "己六", "", ""]])
    merged = post_bytes(owner, "/api/panel/roster/import.xlsx?id=%d" % roster_id, payload)
    check("xlsx 能导入进名单", merged and merged["added"] == 2, merged)
    after = call(owner, "/api/panel/roster/entries?id=%d" % roster_id)
    names = [e["name"] for e in after["entries"]]
    check("导入的人在里面", "戊五" in names and "己六" in names, names)
    check("前导零的学号原样存下",
          any(e["sid"] == "0001234" for e in after["entries"]),
          [e["sid"] for e in after["entries"]][:4])
    again = post_bytes(owner, "/api/panel/roster/import.xlsx?id=%d" % roster_id, payload)
    check("再导入同一份不会重复",
          again and again["added"] == 0 and again["unchanged"] == 2, again)
    post_bytes(owner, "/api/panel/roster/import.xlsx?id=%d" % roster_id,
               b"not a zip", expect=400)
    check("不是 xlsx 的文件会被拒", True)

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
    # 选人弹窗走的是编号（多选一次给几个人）
    post(owner, "/api/panel/roster/grant/revoke",
         {"id": roster_id, "accountId": alice_id})
    alice_uid = [a["uid"] for a in call(owner, "/api/panel/accounts")["accounts"]
                 if a["id"] == alice_id][0]
    by_uid = post(owner, "/api/panel/roster/grant",
                  {"id": roster_id, "uids": [alice_uid]})
    check("a grant by account number works too",
          by_uid and by_uid["ok"] and by_uid["granted"] == 1, by_uid)
    post(owner, "/api/panel/roster/grant", {"id": roster_id, "uids": [999999]}, expect=404)
    check("an unknown account number is refused", True)
    people = call(owner, "/api/panel/people?scene=roster&id=%d" % roster_id)
    check("the picker can list people for this roster",
          people and any(p["id"] == alice_id for p in people["people"]), people)
    check("the picker payload carries no login name",
          all("username" not in p for p in people["people"]), people["people"])
    picked = call(owner, "/api/panel/people?ids=%d" % alice_id)
    check("it can also resolve ids, so a draft shows names again",
          picked and [p["label"] for p in picked["people"]] == ["王同学"], picked)
    # 场景决定谁能查人：不在这一场里的账号没法用"转移负责人"去翻人名
    call(helper, "/api/panel/people?scene=roster&id=%d" % roster_id, expect=403)
    check("a roster's people list is the owner's alone", True)
    post(helper, "/api/panel/event/transfer",
         {"seq": ev["seq"], "uid": alice_uid}, expect=403)
    check("a plain account cannot hand an activity over", True)
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

    print("== 账号的彻底删除 ==")
    doomed = session()
    post(anon, "/api/panel/register", {"username": "doomed", "name": "要删的人",
                                       "password": "doomed-pass-1", "note": ""})
    doomed_row = [a for a in call(owner, "/api/panel/accounts")["accounts"]
                  if a["username"] == "doomed"][0]
    doomed_id, doomed_uid = doomed_row["id"], doomed_row["uid"]
    check("新账号还不能删（没停用）", bool(doomed_row["deleteBlocked"]), doomed_row)
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    refused = post(owner, "/api/panel/account/delete", {"id": doomed_id}, expect=409)
    check("审核前删不掉", refused and "停用" in refused["error"], refused)
    post(owner, "/api/panel/account/decide", {"id": doomed_id, "action": "reject"})
    post(owner, "/api/panel/account/decide", {"id": doomed_id, "action": "approve"})
    post(owner, "/api/panel/account/decide", {"id": doomed_id, "action": "disable"},
         marker="CAEventManager")
    refused = post(owner, "/api/panel/account/delete", {"id": doomed_id}, expect=409)
    check("刚停用的账号也删不掉，并说清还差多久",
          refused and "才能删除" in refused["error"], refused)

    # 停用满三天是静态门槛：把停用时间往前挪，走完剩下的路
    import sqlite3
    raw = sqlite3.connect(os.path.join(DATA, "panel.sqlite3"))
    raw.execute("UPDATE accounts SET decided_at=? WHERE id=?",
                (time.time() - 4 * 86400, doomed_id))
    raw.commit()
    raw.close()
    blocked = [a for a in call(owner, "/api/panel/accounts")["accounts"]
               if a["id"] == doomed_id][0]
    check("停满之后就能删了", blocked["deleteBlocked"] == "", blocked)

    # 名下还有在用的活动时不许删：先转交
    doomed2 = session()
    post(anon, "/api/panel/register", {"username": "doomed2", "name": "还有个活动",
                                       "password": "doomed-pass-2", "note": ""})
    doomed2_row = [a for a in call(owner, "/api/panel/accounts")["accounts"]
                   if a["username"] == "doomed2"][0]
    post(owner, "/api/panel/account/decide",
         {"id": doomed2_row["id"], "action": "approve"})
    post(doomed2, "/api/panel/login",
         {"username": "doomed2", "password": "doomed-pass-2"})
    theirs = post(doomed2, "/api/panel/events/create",
                  {"template": "nwuca.salon", "title": "它的活动"})
    their_seq = theirs["event"]["seq"]
    post(owner, "/api/panel/account/decide",
         {"id": doomed2_row["id"], "action": "disable"})
    raw = sqlite3.connect(os.path.join(DATA, "panel.sqlite3"))
    raw.execute("UPDATE accounts SET decided_at=? WHERE id=?",
                (time.time() - 4 * 86400, doomed2_row["id"]))
    raw.commit()
    raw.close()
    refused = post(owner, "/api/panel/account/delete",
                   {"id": doomed2_row["id"]}, expect=409)
    check("名下有在用的活动就拦住，让人先转交",
          refused and "转移负责人" in refused["error"], refused)

    # 归档之后就不拦了：删人、活动留空，取消归档时再指定负责人。
    # 这场的负责人不是站长，所以走「强制归档」那条路
    post(owner, "/api/panel/event/force_archive", {"seq": their_seq})
    gone = post(owner, "/api/panel/account/delete", {"id": doomed2_row["id"]})
    check("归档的活动不拦着删人", gone and gone["eventsOrphaned"] == 1, gone)
    check("账号确实没了",
          not any(a["uid"] == doomed2_row["uid"]
                  for a in call(owner, "/api/panel/accounts")["accounts"]))
    archived = [e for e in call(owner, "/api/panel/events?archived=1")["events"]
                if e["seq"] == their_seq]
    check("那场活动标着负责人已删除",
          archived and archived[0]["ownerMissing"] is True, archived)
    refused = post(owner, "/api/panel/event/unarchive", {"seq": their_seq}, expect=409)
    check("取消归档要同时指定新的负责人",
          refused and "负责人" in refused["error"], refused)
    owner_uid = [a["uid"] for a in call(owner, "/api/panel/accounts")["accounts"]
                 if a["role"] == "owner"][0]
    moved = post(owner, "/api/panel/event/unarchive",
                 {"seq": their_seq, "owner": owner_uid})
    check("指定之后转移与恢复一起完成",
          moved and moved["archived"] is False and moved["owner"], moved)
    back = [e for e in call(owner, "/api/panel/events")["events"]
            if e["seq"] == their_seq]
    check("活动现在挂在新的负责人名下",
          back and back[0]["ownerMissing"] is False and back[0]["ownerName"], back)

    trail = json.dumps(call(owner, "/api/panel/audit?q=account_delete&limit=10")["audit"],
                       ensure_ascii=False)
    check("删账号写进了审计", "account_delete" in trail, trail[:200])

    post(owner, "/api/panel/sudo/exit")
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    dropped = post(owner, "/api/panel/account/delete", {"id": doomed_id})
    check("停满三天的账号可以删掉", dropped and dropped["deleted"] is True, dropped)
    check("删完之后账号表里没有它",
          not any(a["uid"] == doomed_uid
                  for a in call(owner, "/api/panel/accounts")["accounts"]))

    print("== 名单管理：超管转交与代删 ==")
    spare = post(owner, "/api/panel/roster/create", {"name": "转交测试名单"})
    spare_id = spare["id"]
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    moved = post(owner, "/api/panel/roster/transfer",
                 {"id": spare_id, "uid": alice_uid})
    check("超管能把名单交给别人", moved and moved.get("owner"), moved)
    detail = call(owner, "/api/panel/roster?id=%d" % spare_id)["roster"]
    check("拥有者换成了那个人", detail["ownerId"] == alice_id, detail.get("ownerId"))
    check("原来的拥有者留了一份写入权",
          any(e["accountId"] == owner_id for e in detail["editors"]), detail["editors"])
    post(helper, "/api/panel/roster/entry",
         {"id": spare_id, "sid": "20240900", "name": "转交后能写"})
    check("新的拥有者能直接改", True)
    refused = post(owner, "/api/panel/roster/delete", {"id": spare_id}, expect=409)
    check("没归档的名单不能直接删",
          "归档" in (refused.get("error") or ""), refused)
    # 替别人归档是提权操作：自己的东西不收密码，动别人的要
    post(owner, "/api/panel/sudo/exit", {})
    post(owner, "/api/panel/roster/archive", {"id": spare_id}, expect=403)
    check("替别人归档要先进 sudo", True)
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    archived = post(owner, "/api/panel/roster/archive", {"id": spare_id})
    check("超管能替别人归档", bool(archived and archived.get("ok")), archived)
    gone = post(owner, "/api/panel/roster/delete", {"id": spare_id})
    check("归档之后超管能彻底删除", bool(gone and gone.get("ok")), gone)
    every = call(owner, "/api/panel/signups?scope=all")["signups"]
    check("全站的报名也带拥有者", all("ownerName" in s for s in every), every[:1])

    print("== 名单与报名：先归档，再彻底删除 ==")
    # 拥有者自己归档：不要密码，名单退出列表、不能再改也不能再往里写
    own = post(helper, "/api/panel/roster/create", {"name": "归档测试名单"})
    own_id = own["id"]
    post(helper, "/api/panel/roster/entry",
         {"id": own_id, "sid": "20240901", "name": "归档前的人"})
    post(helper, "/api/panel/sudo/exit", {})
    check("没提权也能归档自己的名单",
          bool(post(helper, "/api/panel/roster/archive", {"id": own_id}).get("ok")))
    check("归档之后自己的列表里没有它",
          own_id not in [r["id"] for r in call(helper, "/api/panel/rosters?scope=mine")["rosters"]])
    call(helper, "/api/panel/roster?id=%d" % own_id, expect=409)
    check("打不开了", True)
    post(helper, "/api/panel/roster/entry",
         {"id": own_id, "sid": "20240902", "name": "归档后的人"}, expect=409)
    check("也写不进去了", True)
    call(helper, "/api/panel/rosters?scope=all&archived=1", expect=403)
    check("归档的名单只有超管能列出来", True)
    listed = call(owner, "/api/panel/rosters?scope=all&archived=1")["rosters"]
    check("超管在归档那一栏看得到它", own_id in [r["id"] for r in listed], listed[:1])
    check("归档的名单带着归档时间", all(r.get("archivedAt") for r in listed), listed[:1])
    post(helper, "/api/panel/roster/unarchive", {"id": own_id}, expect=403)
    check("拥有者自己取消不了归档", True)
    post(owner, "/api/panel/roster/unarchive", {"id": own_id})
    check("超管能取消归档",
          own_id in [r["id"] for r in call(helper, "/api/panel/rosters?scope=mine")["rosters"]])
    post(helper, "/api/panel/roster/entry",
         {"id": own_id, "sid": "20240903", "name": "放出来又能写"})
    check("取消归档之后又能写进去了", True)

    # 报名：名单还挂着报名时，先问一句「一并归档？」，确认之后一起收
    signup = post(helper, "/api/panel/signup/create",
                  {"title": "归档测试报名", "roster_id": own_id})["id"]
    linked = post(helper, "/api/panel/roster/archive", {"id": own_id}, expect=409)
    check("名单上还挂着报名时先把数说清",
          (linked or {}).get("linkedSignups") == 1, linked)
    cascaded = post(helper, "/api/panel/roster/archive",
                    {"id": own_id, "cascade": True})
    check("一并归档：名单与那几个报名一起收",
          (cascaded or {}).get("signupsArchived") == [signup], cascaded)
    tok = [s for s in call(owner, "/api/panel/signups?scope=all&archived=1")["signups"]
           if s["id"] == signup]
    check("报名也跟着进了归档那一栏", bool(tok), tok)
    if tok:
        call(anon, "/join/%s" % tok[0]["token"], expect=410)
        check("归档之后公开链接打不开了", True)
    post(owner, "/api/panel/roster/delete", {"id": own_id}, expect=409)
    check("还有报名指着它时不许彻底删除", True)
    post(owner, "/api/panel/signup/delete", {"id": signup})
    check("超管能彻底删除归档的报名", True)
    post(owner, "/api/panel/roster/delete", {"id": own_id})
    check("最后一个引用没了，名单也能彻底删除", True)

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
    book = call(owner, "/api/panel/signup/export.xlsx?id=%d" % signup_id)
    grid = xlsx.parse(book) if isinstance(book, bytes) and book[:2] == b"PK" else []
    check("报名也能导出 xlsx",
          grid and "是否住宿" in grid[0] and len(grid) >= 1, grid[:1])

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
    listed = call(owner, base + "/rosters", marker="CAEventManager")
    check("the console lists the rosters this account may write",
          any(r["id"] == club_id for r in listed["rosters"]), listed)
    post(owner, base + "/roster_bind", {"roster_id": club_id}, marker="CAEventManager")
    state = call(owner, base + "/state", marker="CAEventManager")
    check("the binding is stored", state["roster"]["id"] == club_id, state.get("roster"))
    synced = post(owner, base + "/roster_sync", {}, marker="CAEventManager")
    check("the first sync brings everybody in",
          synced and synced["result"]["added"] == 3, synced)
    state = call(owner, base + "/state", marker="CAEventManager")
    sids = {x["sid"] for x in state["students"]}
    check("the people are on the activity roster",
          {"20250001", "20250002", "20250003"} <= sids, sorted(sids)[:6])
    check("the sync is remembered", bool(state["roster"]["syncedAt"]))

    again = post(owner, base + "/roster_sync", {}, marker="CAEventManager")
    check("a second sync changes nothing",
          again["result"]["added"] == 0 and again["result"]["same"] == 3, again["result"])

    target = [e for e in call(owner, "/api/panel/roster/entries?id=%d" % club_id)["entries"]
              if e["sid"] == "20250003"][0]
    post(owner, "/api/panel/roster/entry/drop",
         {"id": club_id, "personId": target["personId"]})
    third = post(owner, base + "/roster_sync", {}, marker="CAEventManager")
    check("someone who left the roster is reported, not deleted",
          [m["sid"] for m in third["result"]["missing"]] == ["20250003"], third["result"])
    state = call(owner, base + "/state", marker="CAEventManager")
    check("but is still on the activity roster",
          "20250003" in {x["sid"] for x in state["students"]})
    check("and is counted as detached", state["roster"]["detached"] == 1,
          state["roster"])

    post(owner, base + "/roster_bind", {"roster_id": 0}, marker="CAEventManager")
    state = call(owner, base + "/state", marker="CAEventManager")
    check("the binding can be cleared", state["roster"]["id"] == 0, state["roster"])
    post(owner, base + "/roster_sync", {}, expect=409, marker="CAEventManager")
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
    post(owner, base + "/settings", {"screen_pack": pack_id}, marker="CAEventManager")
    page = call(anon, "/%s/screen" % events[0]["id"])
    check("活动引用后大屏换成这一份",
          isinstance(page, bytes) and marker.encode() in page, (page or b"")[:80])
    sheet = call(anon, "/%s/screen/pack.css" % events[0]["id"])
    check("包内其它文件按 screen/ 取",
          isinstance(sheet, bytes) and b"margin:0" in sheet, (sheet or b"")[:80])
    post(owner, base + "/settings", {"screen_pack": ""}, marker="CAEventManager")
    page = call(anon, "/%s/screen" % events[0]["id"])
    check("不引用时回到活动自带的展示页",
          isinstance(page, bytes) and marker.encode() not in page
          and "大屏".encode() in page, (page or b"")[:80])
    post(owner, base + "/settings", {"screen_pack": "no-such-pack"}, expect=400,
         marker="CAEventManager")
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
    check("一项权限都不给就建不出来",
          post(owner, "/api/panel/token/create",
               {"name": "空权限", "scopes": []}, expect=400) is not None)
    made_roster = bearer("/api/panel/roster/import", plain2,
                         {"name": "令牌建的名单",
                          "text": "学号,姓名,院系\n20259001,甲一,计算机学院"})
    check("有「写入」就能写", made_roster and made_roster.get("id"), made_roster)
    bearer("/api/panel/host", plain2, expect=403)
    check("仍然碰不到平台级接口", True)
    bearer("/api/panel/account/role", plain2, {"id": 1, "role": "admin"}, expect=403)
    check("要重新输密码的动作令牌做不到", True)

    entries = call(owner, "/api/panel/audit?limit=40")
    items = entries.get("audit") or []
    rows = json.dumps(items, ensure_ascii=False)
    check("审计里写明经过哪张令牌", "via: token 写入脚本" in rows, rows[:200])
    check("操作人存的是编号，显示时才转回名字",
          all(a["actor"].isdigit() for a in items)
          and any("(" in a["actorLabel"] and ")" in a["actorLabel"] for a in items),
          [(a["actor"], a["actorLabel"]) for a in items[:3]])
    check("操作人那一列写「名字 (编号)」",
          any(a["actorLabel"].endswith("(10000)") for a in items),
          [a["actorLabel"] for a in items[:3]])
    check("创建令牌本身也留痕", "token_create" in rows, rows[:400])
    check("说明都写成 key: value",
          all(not a["detail"] or ":" in a["detail"] for a in items),
          [a["detail"] for a in items if a["detail"]][:3])
    check("sudo 的进出都带 ip 与原因",
          any(a["action"] == "sudo_on" and (a["detail"] or "").startswith("ip: ")
              for a in (call(owner, "/api/panel/audit?q=sudo&limit=40")["audit"]))
          and any(a["action"] == "sudo_off"
                  and "ip: " in (a["detail"] or "")
                  and "reason: " in (a["detail"] or "")
                  for a in (call(owner, "/api/panel/audit?q=sudo_off&limit=40")["audit"])))

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
                    tool_msgs = [m for m in body.get("messages", [])
                                 if m.get("role") == "tool"]
                    # 认工具表而不是认提示词里的字：提示词改一句话不该把桩带翻
                    if not body.get("tools"):
                        # 起名字那一路：不给工具，只要一句短标题
                        message = {"role": "assistant", "content": "活动报名排查。"}
                    elif "文档" in " ".join(m.get("content") or ""
                                            for m in body.get("messages", [])
                                            if m.get("role") == "user"):
                        message = {"role": "assistant", "content": "",
                                   "tool_calls": [{"id": "call_doc", "type": "function",
                                                   "function": {"name": "read_doc",
                                                                "arguments":
                                                                    '{"path": "doc/usage/start.md"}'}}]}
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
                    if body.get("stream"):
                        # 逐字：把回答切成小段，按 OpenAI 的 SSE 形状吐出来
                        self.send_response(200)
                        self.send_header("Content-Type",
                                         "text/event-stream; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        text = message.get("content") or ""
                        for i in range(0, len(text), 6):
                            chunk = {"choices": [{"delta":
                                                  {"content": text[i:i + 6]}}]}
                            self.wfile.write(b"data: " +
                                             json.dumps(chunk).encode("utf-8") +
                                             b"\n\n")
                            self.wfile.flush()
                        for idx, call in enumerate(message.get("tool_calls") or []):
                            chunk = {"choices": [{"delta": {"tool_calls": [{
                                "index": idx, "id": call["id"],
                                "function": {"name": call["function"]["name"],
                                             "arguments": call["function"]["arguments"]}}]}}]}
                            self.wfile.write(b"data: " +
                                             json.dumps(chunk).encode("utf-8") +
                                             b"\n\n")
                            self.wfile.flush()
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        return
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
        check("每一步还带着摘要（界面上悬停胶囊看的就是它）",
              answer and any((s.get("detail") or {}).get("result") for s in answer["steps"]),
              answer["steps"] if answer else None)

        # 逐字输出：同一条路走 SSE，边算边把增量推回来
        stream_events = sse_call(owner, "/api/panel/assistant/stream",
                                 {"messages": [{"role": "user", "content": "我有哪些活动？"}]})
        kinds = [e.get("type") for e in stream_events]
        deltas = "".join(e.get("text") or "" for e in stream_events
                         if e.get("type") == "delta")
        check("流式：先报工具阶段，再逐字给增量，最后收尾",
              "phase" in kinds and kinds.count("delta") >= 2 and kinds[-1] == "done",
              kinds)
        check("增量拼起来就是最终回答",
              deltas.startswith("你能看到的活动：") or deltas == "",
              (deltas[:40], stream_events[-1].get("reply", "")[:40]))
        check("收尾带着 steps 与 reply",
              bool(stream_events[-1].get("steps")) and bool(stream_events[-1].get("reply")),
              stream_events[-1])
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
              tool_msgs and "开始使用" in (tool_msgs[-1].get("content") or ""),
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
              "当前用户：站长（owner）" in body, body[:400])
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

    # 名字是人改的：存对话不能把它冲回第一句话
    post(owner, "/api/panel/assistant/session/rename", {"id": sid, "title": "测试"})
    post(owner, "/api/panel/assistant/session/save",
         {"id": sid, "title": "平台上现在有多少活动？", "messages": [
             {"role": "user", "content": "平台上现在有多少活动？"},
             {"role": "assistant", "content": "你有 1 场活动。"},
             {"role": "user", "content": "那名单呢"}]})
    named = call(owner, "/api/panel/assistant/session?id=%d" % sid)
    check("存对话不会把人改的名字冲掉",
          (named or {}).get("session", {}).get("title") == "测试",
          (named or {}).get("session", {}).get("title"))

    # 标题有上限：24 个字，服务端一刀切（它要出现在会话条、抽屉、导出文件名里）
    post(owner, "/api/panel/assistant/session/rename",
         {"id": sid, "title": "超" * 60})
    long_name = call(owner, "/api/panel/assistant/session?id=%d" % sid)["session"]["title"]
    check("标题最多 24 个字", len(long_name) == 24, (len(long_name), long_name[:30]))
    post(owner, "/api/panel/assistant/session/rename", {"id": sid, "title": "测试"})

    # 上下文用量：三块加起来等于总数，窗口是配置值（没配就是缺省 128000）
    post(owner, "/api/panel/assistant/config", {"context": 1000})
    ctx = call(owner, "/api/panel/assistant/context?session=%d" % sid)
    parts = (ctx or {}).get("parts") or {}
    check("上下文用量给出三块的分项",
          all(k in parts for k in ("system", "tools", "messages")) and ctx["used"] > 0,
          ctx)
    check("三块加起来等于总数",
          parts.get("system", 0) + parts.get("tools", 0) + parts.get("messages", 0)
          == (ctx or {}).get("used"), ctx)
    check("窗口用的是配置值，百分比按它算",
          ctx.get("window") == 1000 and ctx.get("percent") >= 1, ctx)
    check("会话变长，用量跟着涨",
          call(owner, "/api/panel/assistant/context?session=%d" % sid)["used"]
          > 0, ctx)
    post(owner, "/api/panel/assistant/config", {"context": 0, "model": "deepseek-flash"})
    back_ctx = call(owner, "/api/panel/assistant/context?session=%d" % sid)
    check("0 就是按模型名查表（deepseek-flash 是 1M）",
          back_ctx.get("window") == 1000000, back_ctx)
    post(owner, "/api/panel/assistant/config", {"model": "some-local-model"})
    other = call(owner, "/api/panel/assistant/context?session=%d" % sid)
    check("表里没有的模型退回 128000", other.get("window") == 128000, other)
    post(owner, "/api/panel/assistant/config", {"model": "deepseek-flash"})

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

    print("== 备份：设置、手动做一份、WebDAV 未开 ==")
    state = call(owner, "/api/panel/backup")
    check("站长看得到备份状态", state.get("ok") and "settings" in state, list(state)[:6])
    check("设置里带默认的调度与留存",
          (state.get("settings") or {}).get("schedule", {}).get("at") == "03:30"
          and (state.get("settings") or {}).get("retention", {}).get("daily") == 7,
          state.get("settings"))
    check("不回传 WebDAV 密码，只说配没配",
          "password" not in ((state.get("settings") or {}).get("webdav") or {})
          and "passwordSet" in ((state.get("settings") or {}).get("webdav") or {}),
          state.get("settings", {}).get("webdav"))
    check("备份目录指向 backups/", (state.get("dir") or "").endswith("backups"),
          state.get("dir"))

    # 前面的用例可能已经提过权：先退出来，才能验"改设置要重输密码"
    post(owner, "/api/panel/sudo/exit", {})
    refused = call(owner, "/api/panel/backup/settings",
                   {"retention": {"daily": 5}}, expect=403)
    check("改设置要重输一次密码", refused and refused.get("sudoRequired") is True, refused)
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    saved = post(owner, "/api/panel/backup/settings", {"retention": {"daily": 5}})
    check("提权之后能存", saved and saved.get("ok"), saved)
    again = call(owner, "/api/panel/backup")
    check("存下来的留存读得回来",
          (again.get("settings") or {}).get("retention", {}).get("daily") == 5,
          again.get("settings", {}).get("retention"))

    made = post(owner, "/api/panel/backup/run", {})
    check("点了立即备份就真的有一份",
          made and made.get("ok") and (made.get("bytes") or 0) > 0, made)
    after = call(owner, "/api/panel/backup")
    check("列表里能看到刚做的那一份",
          any(b["name"] == made.get("name") for b in (after.get("backups") or [])),
          [b["name"] for b in (after.get("backups") or [])][:3])

    dav = call(owner, "/api/panel/backup/webdav_test", {}, expect=400)
    check("WebDAV 没开时测试按钮说清楚",
          dav and "WebDAV" in (dav.get("error") or ""), dav)

    print("== 账号：重置别人的密码 ==")
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    made = post(anon, "/api/panel/register",
                {"username": "pwdprobe", "name": "改密码用的",
                 "password": "old-password-1"})
    check("先造一个待审核账号", made and made.get("ok"), made)
    accounts = call(owner, "/api/panel/accounts?limit=200")
    probe = [a for a in accounts["accounts"] if a["username"] == "pwdprobe"]
    check("账号在清单里", bool(probe), [a["username"] for a in accounts["accounts"]][:5])
    pid = probe[0]["id"] if probe else 0
    post(owner, "/api/panel/account/decide", {"id": pid, "action": "approve"})

    # 被重置的那个账号先登录一次，用来验证"会话会被踢掉"
    victim = session()
    post(victim, "/api/panel/login",
         {"username": "pwdprobe", "password": "old-password-1"})
    check("旧密码能登录", call(victim, "/api/panel/me").get("authed") is True)

    post(owner, "/api/panel/sudo/exit", {})
    refused = call(owner, "/api/panel/account/password", {"id": pid}, expect=403)
    check("重置别人的密码要重输一次密码",
          refused and refused.get("sudoRequired") is True, refused)
    post(owner, "/api/panel/sudo", {"password": owner_password()})

    given = call(owner, "/api/panel/account/password",
                 {"id": pid, "password": "manager-picked-1"}, expect=400)
    check("管理员不能自己定密码内容",
          given and "平台生成" in (given.get("error") or ""), given)

    reset = post(owner, "/api/panel/account/password", {"id": pid})
    new_pw = (reset or {}).get("password") or ""
    check("不给新密码就生成一串",
          bool(reset and reset.get("generated")) and len(new_pw) >= 12, reset)
    check("顺手把旧会话作废了", (reset or {}).get("sessions", 0) >= 1, reset)
    check("被重置的账号现在处于未登录",
          call(victim, "/api/panel/me").get("authed") is False)

    fresh = session()
    post(fresh, "/api/panel/login",
         {"username": "pwdprobe", "password": "old-password-1"}, expect=401)
    check("旧密码登录不上去了", True)
    post(fresh, "/api/panel/login", {"username": "pwdprobe", "password": new_pw})
    check("新密码能登录", call(fresh, "/api/panel/me").get("authed") is True)

    owner_row = [a for a in call(owner, "/api/panel/accounts?limit=200")["accounts"]
                 if a["role"] == "owner"]
    if owner_row:
        denied = call(owner, "/api/panel/account/password",
                      {"id": owner_row[0]["id"]}, expect=403)
        check("站长的密码不能在用户管理里改",
              denied and "账户信息" in (denied.get("error") or ""), denied)

    print("== 管理员手动开号：临时密码 + 登录后只能去重设 ==")
    post(owner, "/api/panel/sudo/exit", {})
    made2 = post(owner, "/api/panel/account/create",
                 {"username": "madebyadmin", "name": "管理员开的号", "role": "admin"})
    check("没提权也能建号，而且拿到一串临时密码",
          made2 and made2.get("ok") and len(made2.get("password") or "") >= 12, made2)
    temp_pw = (made2 or {}).get("password") or ""
    listed = call(owner, "/api/panel/accounts?limit=200")
    row = [a for a in listed["accounts"] if a["username"] == "madebyadmin"]
    check("新账号在清单里，而且标着临时密码",
          bool(row) and row[0].get("passTemp") is True, row)
    check("新账号直接是可用状态（不用再审）",
          bool(row) and row[0].get("status") == "active", row)

    fresh_acc = session()
    login = post(fresh_acc, "/api/panel/login",
                 {"username": "madebyadmin", "password": temp_pw})
    check("临时密码能登录，但登录结果说要重设",
          login and login.get("mustReset") is True, login)
    blocked = call(fresh_acc, "/api/panel/accounts", expect=403)
    check("重设之前别的接口一律不放行",
          blocked and blocked.get("resetRequired") is True, blocked)

    bad = call(fresh_acc, "/api/panel/password/reset",
               {"new": "first-new-pass", "confirm": "second-new-pass"}, expect=400)
    check("两次输的不一样就不收", bad and "不一致" in (bad.get("error") or ""), bad)
    reset2 = post(fresh_acc, "/api/panel/password/reset",
                  {"new": "first-new-pass", "confirm": "first-new-pass"})
    check("重设成功", reset2 and reset2.get("ok"), reset2)
    check("重设之后能用了",
          call(fresh_acc, "/api/panel/me").get("mustReset") is False)
    listed2 = call(owner, "/api/panel/accounts?limit=200")
    row2 = [a for a in listed2["accounts"] if a["username"] == "madebyadmin"]
    check("临时密码的标记撤掉了",
          bool(row2) and row2[0].get("passTemp") is False, row2)

    again = call(fresh_acc, "/api/panel/password/reset",
                 {"new": "another-pass-1", "confirm": "another-pass-1"}, expect=409)
    check("状态正常之后重设接口不再受理",
          again and "无需重设" in (again.get("error") or ""), again)

    print("== 重设密码页：只对临时密码的账号开 ==")
    status, where = open_page(anon, "/reset")
    check("未登录访问重设页先去登录",
          status in (301, 302, 303) and (where or "").endswith("/login"),
          (status, where))
    status, where = open_page(fresh_acc, "/reset")
    check("状态正常的账号访问重设页回主界面",
          status in (301, 302, 303) and (where or "").endswith("/panel"),
          (status, where))
    status, where = open_page(fresh_acc, "/panel")
    check("状态正常的账号照常进控制台", status == 200, (status, where))

    print("== 自己改密码：别处下线，本机还在 ==")
    here = session()
    post(here, "/api/panel/login",
         {"username": "madebyadmin", "password": "first-new-pass"})
    other = session()
    post(other, "/api/panel/login",
         {"username": "madebyadmin", "password": "first-new-pass"})
    check("两条会话都在线",
          call(here, "/api/panel/me").get("authed") is True
          and call(other, "/api/panel/me").get("authed") is True)
    changed = post(here, "/api/panel/password",
                   {"old": "first-new-pass", "new": "brand-new-pass-9"})
    check("改密码成功", changed and changed.get("ok"), changed)
    check("改密码的这一台还在线", call(here, "/api/panel/me").get("authed") is True)
    check("另一台被踢下线", call(other, "/api/panel/me").get("authed") is False)
    fresh2 = session()
    post(fresh2, "/api/panel/login",
         {"username": "madebyadmin", "password": "brand-new-pass-9"})
    check("新密码能登录", call(fresh2, "/api/panel/me").get("authed") is True)

    # 管理员再重置一次：这个账号又回到临时密码状态
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    reset3 = post(owner, "/api/panel/account/password",
                  {"id": (row2 or [{}])[0].get("id")})
    back = [a for a in call(owner, "/api/panel/accounts?limit=200")["accounts"]
            if a["username"] == "madebyadmin"]
    check("管理员重置之后又标回临时密码",
          bool(back) and back[0].get("passTemp") is True, back)

    print("== 重设密码页：临时密码的账号只能停在这一页 ==")
    status, where = open_page(anon, "/reset")
    check("未登录访问重设页先去登录",
          status in (301, 302, 303) and (where or "").endswith("/login"),
          (status, where))
    temp_acc = session()
    post(temp_acc, "/api/panel/login",
         {"username": "madebyadmin", "password": (reset3 or {}).get("password")})
    status, where = open_page(temp_acc, "/reset")
    check("临时密码的账号能打开重设页", status == 200, (status, where))
    status, where = open_page(temp_acc, "/panel")
    check("临时密码的账号进控制台被送回重设页",
          status in (301, 302, 303) and (where or "").endswith("/reset"),
          (status, where))
    status, where = open_page(owner, "/panel")
    check("别的账号照常进控制台", status == 200, (status, where))

    print("== 批量导入：预览 → 导入 ==")
    post(owner, "/api/panel/sudo/exit", {})
    rules = call(owner, "/api/panel/account/import/rules")
    ids = [r["id"] for r in (rules.get("rules") or [])]
    check("导入带一组密码规则", ids[:2] == ["random", "sid"] and "custom" in ids, ids)
    check("规则接口说出了还剩多少名额", "cap" in rules, rules.get("cap"))

    csv_text = ("登录名,显示名,角色,初始密码,学号\n"
                ",导入甲,管理员,,2025001\n"
                ",导入乙,,Sf9xQ2mV,2025002\n"
                "owner,导入丙,,,2025003\n")
    blob = base64.b64encode(csv_text.encode("utf-8")).decode()
    plan = post(owner, "/api/panel/account/import/preview",
                {"filename": "a.csv", "data": blob, "rule": {"id": "sid6"}})
    rows = plan.get("rows") or []
    check("三行都读进来了", len(rows) == 3, len(rows))
    check("预览不替人补内容：登录名与密码留空",
          bool(rows) and rows[0]["username"] == "" and rows[0]["password"] == "",
          rows[:1])
    check("缺内容的行标成不能导入",
          bool(rows) and rows[0]["status"] == "blocked"
          and any("登录名" in p for p in rows[0]["problems"]), rows[:1])
    check("表里给的密码原样留着", bool(rows) and rows[1]["password"] == "Sf9xQ2mV",
          rows[1:2])

    filled = post(owner, "/api/panel/account/import/preview",
                  {"filename": "a.csv", "data": blob, "rule": {"id": "sid6"},
                   "autofill": {"username": True, "password": True}})
    fill_rows = filled.get("rows") or []
    check("点了自动填充才补登录名",
          bool(fill_rows) and fill_rows[0]["username"] != "", fill_rows[:1])
    check("点了自动填充才补密码",
          bool(fill_rows) and len(fill_rows[0]["password"]) >= 8, fill_rows[:1])
    check("撞名的那一行标成不能导入",
          bool(rows) and rows[2]["status"] == "blocked"
          and any("登录名" in p for p in rows[2]["problems"]), rows[2:])
    check("预览统计对得上",
          plan["counts"]["blocked"] == 3 and filled["counts"]["ready"] == 2,
          (plan.get("counts"), filled.get("counts")))

    custom = post(owner, "/api/panel/account/import/preview",
                  {"filename": "a.csv", "data": blob,
                   "rule": {"id": "custom", "template": "cae-{{学号}}"},
                   "autofill": {"username": True, "password": True}})
    check("自定义规则用得上表里别的列",
          (custom["rows"][0]["password"] or "").startswith("cae-2025001"),
          custom["rows"][0]["password"])

    ready = [r for r in fill_rows if r["status"] == "ready"]
    made = post(owner, "/api/panel/account/import", {"rows": ready})
    check("能建的两行建出来了", made.get("created") == 2, made)
    listed = call(owner, "/api/panel/accounts?limit=200")["accounts"]
    names = [m["username"] for m in made.get("accounts") or []]
    fresh = [a for a in listed if a["username"] in names]
    check("导入出来的号是临时密码",
          len(fresh) == 2 and all(a["passTemp"] is True for a in fresh),
          [(a["username"], a["passTemp"]) for a in fresh])
    bad = call(owner, "/api/panel/account/import", {"rows": rows}, expect=400)
    check("有问题的行整批不收", bad and "不能导入" in (bad.get("error") or ""), bad)
    post(owner, "/api/panel/account/import/downloaded", {"rows": 2})
    audit = call(owner, "/api/panel/audit?q=account_import&limit=20")
    actions = [r["action"] for r in audit.get("audit") or []]
    check("导入与下载都进了审计",
          "account_import" in actions and "account_import_export" in actions, actions)

    print("== 组织：建、成员、归档、彻底删除 ==")
    post(owner, "/api/panel/sudo/exit", {})
    bad = call(owner, "/api/panel/org/create",
               {"id": "Bad Org!", "name": "非法标识", "heads": [1]}, expect=400)
    check("组织标识不合法就拒", bad and "标识" in (bad.get("error") or ""), bad)
    nohead = call(owner, "/api/panel/org/create",
                  {"id": "nohead", "name": "没有负责人", "heads": []}, expect=400)
    check("没有负责人建不出来", nohead and "负责人" in (nohead.get("error") or ""), nohead)
    accounts = call(owner, "/api/panel/accounts?limit=200")["accounts"]
    owner_id = [a["id"] for a in accounts if a["role"] == "owner"][0]
    others = [a["id"] for a in accounts if a["role"] != "owner"]
    mate = others[0] if others else owner_id
    made = post(owner, "/api/panel/org/create",
                {"id": "cs-club", "name": "计算机社", "note": "测试用",
                 "heads": [owner_id], "admins": [mate], "members": []})
    check("组织建出来了", made and made.get("ok"), made)
    dup = call(owner, "/api/panel/org/create",
               {"id": "cs-club", "name": "重名", "heads": [owner_id]}, expect=409)
    check("标识重复就拒", dup and "占用" in (dup.get("error") or ""), dup)

    listed = call(owner, "/api/panel/orgs")["orgs"]
    row = [o for o in listed if o["id"] == "cs-club"]
    check("清单里有这个组织", bool(row), listed)
    check("成员数与我的身份都在", row and row[0]["members"] == 2
          and row[0]["myRole"] == "head", row[0] if row else None)

    detail = call(owner, "/api/panel/org?id=cs-club")
    check("详情里有成员与身份",
          [m["role"] for m in detail["members"]] == ["head", "admin"],
          detail.get("members"))

    # 平台侧改别人的组织要提权：站长本人是这个组织的负责人也一样
    # 同一件事在两个页面上规矩不同：平台页要 sudo，组织自己的设置页不要
    selfedit = call(owner, "/api/panel/org/update",
                    {"id": "cs-club", "name": "计算机社", "scope": "self"})
    check("负责人从「我的组织」改不必提权",
          selfedit and selfedit.get("ok"), selfedit)
    refused = call(owner, "/api/panel/org/update",
                   {"id": "cs-club", "name": "平台改名", "scope": "platform"}, expect=403)
    check("平台页改组织要先进 sudo",
          refused and refused.get("sudoRequired") is True, refused)
    refused = call(owner, "/api/panel/org/member",
                   {"org": "cs-club", "account": mate, "action": "remove",
                    "scope": "platform"}, expect=403)
    check("平台侧改成员要先进 sudo",
          refused and refused.get("sudoRequired") is True, refused)
    refused = call(owner, "/api/panel/org/archive", {"id": "cs-club"}, expect=403)
    check("归档要先进 sudo", refused and refused.get("sudoRequired") is True, refused)
    post(owner, "/api/panel/sudo", {"password": owner_password()})

    post(owner, "/api/panel/org/member",
         {"org": "cs-club", "account": mate, "action": "remove",
          "scope": "platform"})
    check("可以踢人",
          len(call(owner, "/api/panel/org?id=cs-club")["members"]) == 1)
    post(owner, "/api/panel/org/member",
         {"org": "cs-club", "account": mate, "action": "add", "role": "member",
          "scope": "platform"})
    check("可以再加回来",
          [m["role"] for m in call(owner, "/api/panel/org?id=cs-club")["members"]]
          == ["head", "member"])
    keep = call(owner, "/api/panel/org/member",
                {"org": "cs-club", "account": owner_id, "action": "remove",
                 "scope": "platform"}, expect=409)
    check("最后一个负责人不能被踢掉", keep and "负责人" in (keep.get("error") or ""), keep)

    mine = call(owner, "/api/panel/org/mine")["orgs"]
    check("我的组织里看得到", any(o["id"] == "cs-club" for o in mine), mine)

    print("== 组织外观：站内上传、外链、清除 ==")
    pixel = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
             "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    got = post(owner, "/api/panel/org/cover",
               {"id": "cs-club", "scope": "self", "data": "data:image/png;base64," + pixel})
    check("组织负责人能传一张图上来", got and got.get("coverBytes", 0) > 0, got)
    served = urllib.request.Request(
        ROOT + "/api/panel/org_cover/cs-club", headers=dict(HEADER))
    with owner.open(served, timeout=20) as res:
        blob = res.read()
    check("站内那张图能取回来", blob[:4] == b"\x89PNG", blob[:8])
    linked = post(owner, "/api/panel/org/cover",
                  {"id": "cs-club", "scope": "self",
                   "url": "https://img.example.com/a.png"})
    check("也可以只填一个外链", linked and linked.get("coverBytes") == 0, linked)
    bad = call(owner, "/api/panel/org/cover",
               {"id": "cs-club", "scope": "self", "url": "ftp://x/a.png"}, expect=400)
    check("外链只收 http(s)", bad and "http" in (bad.get("error") or ""), bad)
    listed = [o for o in call(owner, "/api/panel/orgs")["orgs"] if o["id"] == "cs-club"]
    check("清单里带着外观", bool(listed) and
          listed[0]["cover"] == "https://img.example.com/a.png",
          listed[0] if listed else None)
    cleared = post(owner, "/api/panel/org/cover",
                   {"id": "cs-club", "scope": "self", "clear": True})
    check("可以清除", cleared and cleared.get("cover") == "", cleared)

    post(owner, "/api/panel/org/archive", {"id": "cs-club"})
    archived = [o for o in call(owner, "/api/panel/orgs")["orgs"] if o["id"] == "cs-club"]
    check("归档之后成员数还在（折进组织行）",
          archived and archived[0]["archived"] is True and archived[0]["members"] == 2,
          archived[0] if archived else None)
    frozen = call(owner, "/api/panel/org/member",
                  {"org": "cs-club", "account": mate, "action": "remove",
                   "scope": "platform"}, expect=409)
    check("归档之后不能再改成员", frozen and "归档" in (frozen.get("error") or ""), frozen)
    post(owner, "/api/panel/org/restore", {"id": "cs-club"})
    back = [o for o in call(owner, "/api/panel/orgs")["orgs"] if o["id"] == "cs-club"]
    check("恢复之后明细回来了",
          back and back[0]["archived"] is False and back[0]["members"] == 2,
          back[0] if back else None)

    post(owner, "/api/panel/org/archive", {"id": "cs-club"})
    post(owner, "/api/panel/sudo/exit", {})
    refused = call(owner, "/api/panel/org/delete", {"id": "cs-club"}, expect=403)
    check("彻底删除要提权", refused and refused.get("sudoRequired") is True, refused)
    post(owner, "/api/panel/sudo", {"password": owner_password()})
    gone = post(owner, "/api/panel/org/delete", {"id": "cs-club"})
    check("提权之后能彻底删掉", gone and gone.get("ok"), gone)
    check("删完之后清单里没有了",
          not any(o["id"] == "cs-club" for o in call(owner, "/api/panel/orgs")["orgs"]))

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
