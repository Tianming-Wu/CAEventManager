#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑套件：自己挑空闲端口、起实例、跑测试、收摊。

    python tmp/run_suites.py unit panel_test panel_ui_test smoke_test ui_test
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def boot(tag):
    port = free_port()
    data = os.path.join(ROOT, "tmp", "t-%s" % tag)
    shutil.rmtree(data, ignore_errors=True)
    os.makedirs(data, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LOTTERY_DATA_DIR"] = os.path.join(data, "data")
    env["LOTTERY_PACKS_DIR"] = os.path.join(data, "packs")
    log = open(os.path.join(data, "server.log"), "wb")
    proc = subprocess.Popen([PY, "app.py", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        try:
            with urllib.request.urlopen(base + "/login", timeout=2) as res:
                if b"loginForm" in res.read():
                    break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        raise SystemExit("server did not come up (%s)" % data)
    return proc, base, data


def password(data):
    path = os.path.join(data, "data", "owner_password.txt")
    for line in open(path, encoding="utf-8"):
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("no owner password")


def run(tag, commands, need_server=True):
    """commands: [(tool, extra_args), ...] run in order against the same instance."""
    print("== %s ==" % tag)
    proc = data = None
    ok = True
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        base = pw = ""
        if need_server:
            proc, base, data = boot(tag)
            pw = password(data)
            # 有些套件会直接往数据目录里写通知，所以测试进程要看到同一份目录
            env["LOTTERY_DATA_DIR"] = os.path.join(data, "data")
            env["LOTTERY_PACKS_DIR"] = os.path.join(data, "packs")
        for tool, extra in commands:
            if need_server:
                cmd = [PY, os.path.join("tools", tool), base, pw] + extra
            else:
                cmd = [PY, os.path.join("tools", tool)] + extra
            out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=env)
            tail = [ln for ln in (out.stdout or "").splitlines() if ln.strip()][-3:]
            fails = [ln for ln in (out.stdout or "").splitlines() if "  FAIL" in ln]
            print("  %s exit=%d fail=%d" % (tool, out.returncode, len(fails)))
            for ln in fails:
                print("   ", ln.strip())
            for ln in tail:
                print("   ", ln.strip())
            if out.returncode != 0 and not fails:
                print("   stderr:", (out.stderr or "")[-400:])
            ok = ok and out.returncode == 0
        return ok
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()


SUITES = {
    "unit": ([("unit_test.py", [])], False),
    "panel_test": ([("panel_test.py", [])], True),
    "panel_ui_test": ([("panel_ui_test.py", ["--accounts"])], True),
    "smoke": ([("smoke_test.py", [])], True),
    "ui": ([("seed_demo.py", []), ("ui_test.py", [])], True),
}

wanted = sys.argv[1:] or ["unit", "panel_test", "panel_ui_test"]
ok = True
for name in wanted:
    if name not in SUITES:
        raise SystemExit("unknown suite: %s" % name)
    commands, needs = SUITES[name]
    ok = run(name, commands, needs) and ok
print("ALL SUITES OK" if ok else "SOME SUITES FAILED")
sys.exit(0 if ok else 1)
