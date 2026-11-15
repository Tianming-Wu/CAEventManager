#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑套件：自己挑空闲端口、起实例、跑测试、收摊，并报每一项用了多久。

    python tools/run_suites.py unit panel_test panel_ui_test smoke ui safemode modules
    python tools/run_suites.py changed            # 按改动挑最小的那几套
    python tools/run_suites.py panel_ui_test --only=assistant   # 只验运维助手

改哪一块只验哪一块：`changed` 会看工作区里改过的文件，挑出受影响的套件；
碰到共用件（壳子、路由表、全局样式、app.py）就退回全套。
**提交之前仍然要跑一次全套**——这里是给中间的快速循环用的。
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import json
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
    theme_dir = os.path.join(data, "theme")
    # 随发布走的外观（classic）先拷进去，否则大屏没有兜底
    shutil.copytree(os.path.join(ROOT, "theme"), theme_dir, dirs_exist_ok=True)
    env["LOTTERY_THEME_DIR"] = theme_dir
    # 这个实例自己装的模块也放进自己的目录：套件要装临时模块时不弄脏仓库的 modules/
    modules_dir = os.path.join(data, "modules")
    os.makedirs(modules_dir, exist_ok=True)
    env["LOTTERY_MODULES_DIR"] = modules_dir
    # 自己的 config.json：仓库那份写着生产的对外前缀，测试照着它跳会跳到线上
    base = "http://127.0.0.1:%d" % port
    config_path = os.path.join(data, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"port": port, "public_base_prefix": base}, fh)
    env["LOTTERY_CONFIG"] = config_path
    log = open(os.path.join(data, "server.log"), "wb")
    proc = subprocess.Popen([PY, "app.py", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
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
    # 新装的服务上没有任何活动。套件都需要一个，所以这里建一个再跑，
    # 用的是内置预设（与真人点「创建活动」同一条接口）。
    make_event(base, password(data))
    return proc, base, data


def make_event(base, pw):
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"X-Requested-With": "CAEventManager", "Content-Type": "application/json"}

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers=headers)
        with opener.open(req, timeout=20) as res:
            return json.loads(res.read().decode("utf-8"))

    call("/api/panel/login", {"username": "owner", "password": pw})
    call("/api/panel/events/create",
         {"template": "nwuca.salon", "title": "测试活动", "params": {}})


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
            theme_dir = os.path.join(data, "theme")
            # 随发布走的外观（classic）先拷进去，否则大屏没有兜底
            shutil.copytree(os.path.join(ROOT, "theme"), theme_dir, dirs_exist_ok=True)
            env["LOTTERY_THEME_DIR"] = theme_dir
            # 套件往这里装临时模块（高危能力的用例要一个声明了 super.* 的模块）
            env["LOTTERY_MODULES_DIR"] = os.path.join(data, "modules")
        for tool, extra in commands:
            if need_server:
                cmd = [PY, os.path.join("tools", tool), base, pw] + extra
            else:
                cmd = [PY, os.path.join("tools", tool)] + extra
            t0 = time.time()
            out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=env)
            took = time.time() - t0
            tail = [ln for ln in (out.stdout or "").splitlines() if ln.strip()][-3:]
            fails = [ln for ln in (out.stdout or "").splitlines() if "  FAIL" in ln]
            print("  %s exit=%d fail=%d 用时 %.1fs" % (tool, out.returncode, len(fails), took))
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
    # 自己起两次实例（正常 → 安全模式 → 恢复），不需要外面再给一个
    "safemode": ([("safemode_test.py", [])], False),
    # 两个玩法完全不同的模块同时跑：讲座（自带页签与公示页）与抽奖
    "modules": ([("modules_test.py", [])], False),
}

# 改了什么 → 跑什么。键是路径前缀，值是 (套件, 页面测试的分组, 说明)。
# 共用件（壳子、路由、全局样式、服务端入口）不在这儿列：碰到它们就退回全套。
AREAS = [
    (("core/assistant.py", "core/policy.py", "doc/usage/assistant", "doc/usage/assistant-prompt"),
     (["unit", "panel_ui_test", "panel_test"], ["assistant"], "运维助手")),
    (("assets/js/common.js",),
     (["unit", "panel_ui_test"], ["assistant"], "共用脚本（Markdown 渲染等）")),
    (("core/lists.py", "core/panel.py"),
     (["unit", "panel_test", "panel_ui_test"], ["data", "admin"], "名单/账号的数据层")),
    (("builtin/modules/", "core/modules.py"),
     (["unit", "modules", "panel_ui_test"], ["mods"], "模块")),
    (("assets/js/docs.js", "assets/css/docs.css", "pages/docs.html"),
     (["unit", "panel_ui_test"], ["docs"], "使用文档页")),
    (("theme/", "assets/css/app.css"),
     (["unit", "ui"], [], "外观包与大屏样式")),
    (("doc/", "README.md", "ROADMAP.md"),
     (["unit"], [], "只有文档")),
]
# 这些一动就是全套：页面壳、路由、全局样式、服务端入口、测试工具本身
GLOBAL_FILES = ("app.py", "assets/js/panel.js", "assets/css/panel.css",
                "pages/", "tools/", "core/model.py", "core/db.py")


def changed_files():
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8").stdout
    files = []
    for line in out.splitlines():
        name = line[3:].strip()
        if " -> " in name:                      # 改名：取后半截
            name = name.split(" -> ", 1)[1]
        if name:
            files.append(name.replace("\\", "/"))
    return files


def plan_for(files):
    """(套件列表, 页面分组, 理由)。碰到共用件就返回全套。"""
    if not files:
        return (list(SUITES), [], "没有改动：跑全套")
    for name in files:
        if name.startswith(GLOBAL_FILES):
            return (list(SUITES), [], "动到了共用件 %s" % name)
    suites, groups, why = [], [], []
    for name in files:
        hit = None
        for prefixes, target in AREAS:
            if name.startswith(prefixes):
                hit = target
                break
        if hit is None:
            return (list(SUITES), [], "说不清 %s 影响哪些套件" % name)
        want_suites, want_groups, label = hit
        why.append(label)
        for s in want_suites:
            if s not in suites:
                suites.append(s)
        for g in want_groups:
            if g not in groups:
                groups.append(g)
    return (suites, groups, "、".join(sorted(set(why))))


args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = [a for a in sys.argv[1:] if a.startswith("--")]
only = [a.split("=", 1)[1] for a in flags if a.startswith("--only=")]
extra = (["--only=" + only[0]] if only else [])

if args == ["changed"]:
    suites, groups, why = plan_for(changed_files())
    args = suites
    if groups and not extra:
        extra = ["--only=" + ",".join(groups)]
    print("按改动挑了：%s  （%s）" % (" ".join(args), why))
    if groups:
        print("页面测试只跑：%s" % ", ".join(groups))
    print("提交之前记得跑一次全套：python tools/run_suites.py unit panel_test "
          "panel_ui_test smoke ui safemode modules")

if not args:
    args = ["unit", "panel_test", "panel_ui_test"]
    extra = []

ok = True
timings = []
started = time.time()
for name in args:
    if name not in SUITES:
        raise SystemExit("unknown suite: %s" % name)
    commands, needs = SUITES[name]
    if extra and name == "panel_ui_test":
        commands = [("panel_ui_test.py", ["--accounts"] + extra)]
    t0 = time.time()
    ok = run(name, commands, needs) and ok
    timings.append((name, time.time() - t0))

print()
print("用时：%s" % "，".join("%s %.1fs" % (n, t) for n, t in timings))
print("合计 %.1f 秒" % (time.time() - started))
print("ALL SUITES OK" if ok else "SOME SUITES FAILED")
sys.exit(0 if ok else 1)
