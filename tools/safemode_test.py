# -*- coding: utf-8 -*-
"""安全模式：外面看到什么。

先在正常模式里建一场活动，再带 LOTTERY_SAFE_MODE=1 重启同一个实例，逐项核对：
活动页变成维护页（地址不变）、活动接口 503、面板与登录页顶上挂横幅、
面板自己的设置照常可改、安全模式下建不出新活动。
"""
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tmp", "t-safemode-suite")
failures = []


def check(name, ok, detail=""):
    if ok:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + ("  " + str(detail) if detail else ""))
        failures.append(name)


sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()
BASE = "http://127.0.0.1:%d" % PORT

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(path):
    req = urllib.request.Request(BASE + path)
    try:
        with opener.open(req, timeout=15) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-Requested-With": "CAEventManager"})
    try:
        with opener.open(req, timeout=15) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode() or "{}")
        except ValueError:
            body = {}
        return exc.code, body


def boot(safe):
    shutil.rmtree(DATA, ignore_errors=True) if not safe else None
    os.makedirs(DATA, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LOTTERY_DATA_DIR"] = os.path.join(DATA, "data")
    env["LOTTERY_PACKS_DIR"] = os.path.join(DATA, "packs")
    if safe:
        env["LOTTERY_SAFE_MODE"] = "1"
    else:
        env.pop("LOTTERY_SAFE_MODE", None)
    log = open(os.path.join(DATA, "server.log"), "ab")
    proc = subprocess.Popen([sys.executable, "app.py", "--port", str(PORT)], cwd=ROOT,
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            with urllib.request.urlopen(BASE + "/login", timeout=2) as res:
                if b"loginForm" in res.read():
                    return proc
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit("服务没起来，日志在 %s" % os.path.join(DATA, "server.log"))


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    time.sleep(0.6)


def owner_password():
    path = os.path.join(DATA, "data", "owner_password.txt")
    for line in open(path, encoding="utf-8"):
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("没有 owner 密码")


def main():
    shutil.rmtree(DATA, ignore_errors=True)
    token = ""

    print("== 正常模式：先有一场活动 ==")
    proc = boot(safe=False)
    try:
        pw = owner_password()
        post("/api/panel/login", {"username": "owner", "password": pw})
        code, made = post("/api/panel/events/create", {"template": "nwuca.salon"})
        token = (made.get("event") or {}).get("token") or ""
        check("正常模式建得出活动", code == 200 and bool(token), (code, made))
        code, body = get("/%s/" % token)
        check("正常模式公示页是活动页", code == 200 and "服务暂时不可用".encode() not in body, code)
        code, body = get("/login")
        check("正常模式登录页没有横幅", "服务处于安全模式".encode() not in body, code)
    finally:
        stop(proc)

    print("== 安全模式：同一个实例重启 ==")
    jar.clear()
    proc = boot(safe=True)
    try:
        code, body = get("/login")
        check("登录页顶上挂横幅",
              code == 200 and "服务处于安全模式".encode() in body, code)

        for label, path in (("公示页", "/%s/" % token),
                            ("大屏", "/%s/screen" % token),
                            ("控制台", "/%s/admin" % token)):
            code, body = get(path)
            check("%s 变成维护页" % label,
                  code == 503 and "服务暂时不可用".encode() in body, code)
            check("%s 的地址没有跳转（还是原来那条）" % label, code == 503, code)

        code, body = get("/%s/api/board" % token)
        check("活动接口一律 503 并说明原因",
              code == 503 and "安全模式".encode() in body, (code, body[:40]))

        post("/api/panel/login", {"username": "owner", "password": pw})
        code, body = get("/panel")
        check("站长还能进面板", code == 200, code)
        check("面板顶上也有横幅", "服务处于安全模式".encode() in body, code)

        code, svc = get("/api/panel/service")
        check("服务提示里有一条安全模式",
              code == 200 and b"svc:safe-mode" in svc, code)

        code, made_roster = post("/api/panel/roster/create", {"name": "安全模式下的名单"})
        check("平台自己的东西照常可改（建名单）",
              code == 200 and bool((made_roster or {}).get("id")), (code, made_roster))

        code, made = post("/api/panel/events/create", {"template": "nwuca.salon"})
        check("安全模式下建不出活动，并说明原因",
              code == 409 and "安全模式" in (made.get("error") or ""), (code, made))
    finally:
        stop(proc)

    print("== 恢复：去掉开关再启动 ==")
    jar.clear()
    proc = boot(safe=False)
    try:
        code, body = get("/%s/" % token)
        check("活动页回来了", code == 200 and "服务暂时不可用".encode() not in body, code)
        code, body = get("/login")
        check("横幅不见了", "服务处于安全模式".encode() not in body, code)
    finally:
        stop(proc)
        shutil.rmtree(DATA, ignore_errors=True)

    if failures:
        print("SAFEMODE TEST FAILED (%d): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("SAFEMODE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
