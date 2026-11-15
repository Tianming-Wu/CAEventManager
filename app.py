#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Activity service - stdlib only, no dependencies.

Pages
-----
/          public board  (no auth, what the QR code opens)
/screen    big screen for the classroom projector
/admin     operator console (password protected)
/login     login form

Every URL is relative on purpose: the lab platform publishes this app under
``https://example.org/u/<user>/<service>/`` and strips that prefix before
forwarding, so a leading "/" in the HTML/JS would escape the prefix.

Run:  python3 app.py [--host 0.0.0.0] [--port 8931]
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import mimetypes
import os
import platform
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
# 程序模块在 core/ 里，入口（本文件）留在仓库根目录；把 core 放进搜索路径，
# 各模块之间与工具脚本里的导入写法都不用改。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))

import xlsx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import auth
import assistant
import bus
import checkin
import db
import eventfs
import expr
import exporter
import health
import modules
import pinyintable
import policy
import rng
import safemode
import startguard
import presets
import shortlinks
import localconfig
import lists
import model
import packs
import panel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR = os.path.join(BASE_DIR, "pages")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
# 四段：主.次.修.构。构只增不减，是这台机器的发布序号；每次上线都要推进——见 tools/bump_version.py
# （LOTTERY_VERSION 只给测试与预发用：起两个版本号不同的实例，验证页面的热更新提示）
VERSION = os.environ.get("LOTTERY_VERSION") or "1.13.0.126"
START_TIME = time.time()
MAX_BODY = 4 * 1024 * 1024
XLSX_MAX = 4 * 1024 * 1024          # 导入的 xlsx 上限：再大就不像一份名单了

# Served when a signed-in account opens a console it has no seat on.  A redirect
# would loop: it can pass the login, and the console would just refuse again.
NOT_A_MEMBER_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>无权访问 · CAEventManager</title>
<body style="font-family:system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
             margin:0;display:grid;place-items:center;min-height:100vh;background:#f5f2ee;
             color:#1c1917">
  <div style="text-align:center;padding:2em">
    <p style="font-size:1.05rem;font-weight:600;margin:0 0 .5em">你不是这个活动的成员</p>
    <p style="color:#57534e;font-size:.88rem;margin:0">
      请让活动负责人把你加进成员名单，或换一个账号登录</p>
  </div>
</body>""".encode("utf-8")

# Filled in by main() before the server starts listening.
LOCAL_CFG = {}

_PANEL_FAILS = {}
EVENT_TOKENS = set()

# 注册口是最容易被拿来做"这个名字在不在"的探针：同一个人连着试几个就说明不对劲
_REGISTER_HITS = {}      # ip -> 冷却到什么时候
REGISTER_HIT_MAX = 8     # 十分钟内这么多次"不可用"就歇一会
REGISTER_COOL = 600


def _note_register_hit(ip):
    now = time.time()
    hits = _REGISTER_HITS.get(ip + ":n", 0) + 1
    _REGISTER_HITS[ip + ":n"] = hits
    if hits >= REGISTER_HIT_MAX:
        _REGISTER_HITS[ip] = now + REGISTER_COOL
        _REGISTER_HITS[ip + ":n"] = 0

# Very small guard against somebody hammering the short-code space.
# 界面上给几个现成的目标候选；模块想指向别的路径也行
SHORTLINK_SUGGEST = [
    {"key": "", "label": "公示页"},
    {"key": "screen", "label": "大屏"},
    {"key": "checkin", "label": "登记页"},
    {"key": "admin", "label": "活动后台"},
]

def conn_kind(conn):
    """这个活动用的模块 id。"""
    return str(db.get_config(conn, "kind") or "").strip()


def _record_by_id(conn, rec_id):
    """动作做完回一条记录给前端：按 id 在模块快照里找，形状原样带下去。"""
    cfg = db.all_config(conn)
    spec = ((modules.get(conn_kind(conn), VERSION) or {}).get("features") or {}).get("search")
    for rec in model.snapshot(conn, cfg):
        if rec.get("id") == rec_id:
            return module_record(rec, spec)
    return None


def module_extra(name, *args):
    """模块有就调，没有就返回 None——平台不假设任何玩法都有这一份数据。"""
    try:
        fn = getattr(model.current(), name, None)
    except Exception:
        return None
    return fn(*args) if callable(fn) else None


def module_roster(conn, cfg, roster_id, roster_name):
    """绑定名单的几条信息；模块想补（例如"已不在名单"多少人）就实现 roster_info。"""
    info = {"id": roster_id, "name": roster_name,
            "syncedAt": cfg.get("roster_synced_at") or 0}
    extra = module_extra("roster_info", conn, cfg)
    if isinstance(extra, dict):
        info.update(extra)
    info.setdefault("detached", 0)
    return info


def module_pages_html(cfg):
    """模块自己那几张页签的标记，平台把它们拼进外壳的 {{MODULE_PAGES}}。"""
    kind = cfg.get("kind") or ""
    mod = _modules_by_id(panel.connect()).get(kind) or {}
    return "\n".join(modules.page_fragments(kind, mod.get("pages") or []))


def supervised():
    """服务是不是由 bin/supervise.sh 看着：是的话，让进程退出就等于重启。"""
    return bool(os.environ.get("LOTTERY_SUPERVISED"))


def restart_after_response():
    """回完这一条就把进程退掉；看护进程会立刻把它重新起来。"""
    import threading

    def bye():
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(0)

    timer = threading.Timer(0.6, bye)
    timer.daemon = True
    timer.start()


def safe_mode_banner():
    """安全模式下贴在页面顶上的一条：说清为什么只能看、怎么退出。"""
    if not safemode.enabled():
        return ""
    return (
        '<div class="svc-band"><b>服务处于安全模式</b>：活动内的数据暂不开放，'
        "平台的账号、模块与设置仍可修改；改完配置重启即可退出。</div>"
    )


def substitute_page(body, cfg):
    """页面里的占位符：标题、版本、图标、模块页签片段、安全模式横幅。"""
    body = body.replace(b"{{TITLE}}", str(cfg.get("title") or "活动").encode("utf-8"))
    body = body.replace(b"{{VERSION}}", VERSION.encode("utf-8"))
    body = body.replace(b"{{FAVICON}}", favicon_markup().encode("utf-8"))
    if b"{{MODULE_PAGES}}" in body:
        body = body.replace(b"{{MODULE_PAGES}}", module_pages_html(cfg).encode("utf-8"))
    if b"{{SAFE_MODE}}" in body:
        body = body.replace(b"{{SAFE_MODE}}", safe_mode_banner().encode("utf-8"))
    return body


def not_found_page():
    """404 的页面：tpl/404.html 在就用它，没有就用一句白话。状态码始终是 404。"""
    path = os.path.join(PAGES_DIR, "404.html")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            html = fh.read()
        return html.replace("{{VERSION}}", VERSION).encode("utf-8")
    except OSError:
        return (b"<!doctype html><meta charset=utf-8><title>404</title>"
            b"<body style=\"font-family:system-ui;padding:3em;color:#888\">"
            b"404 Not Found</body>")


NOT_FOUND_PAGE = (
    "<!doctype html><meta charset=utf-8><title>404</title>"
    "<body style=\"font-family:system-ui;padding:3em;color:#888\">404 Not Found</body>"
)

SHORTLINK_MISS_PAGE = (
    "<!doctype html><meta charset=utf-8><title>链接已失效</title>"
    "<body style=\"font-family:system-ui;padding:3em;color:#888\">"
    "这个短链已失效，请向发起人索取新链接。</body>").encode("utf-8")

# 短链号只有 7 位，连着试错就直接拖慢响应
SHORT_MISS = {"count": 0, "since": 0.0}
SHORT_MAX_MISS = 40            # wrong hits per window before cooling off
SHORT_WINDOW = 60.0


# 服务日志：run.sh 用 `>>data/service.log` 起服务，所以轮转要在进程内做——
# 把老文件改名之后 dup2 一个同名的新文件到 fd 1，之后照旧往 stdout 写就行。
LOG_MAX = int(os.environ.get("LOTTERY_LOG_MAX") or 8 * 1024 * 1024)   # 0 = 不轮转
LOG_KEEP = 3                    # service.log.1 / .2 / .3，再多就是占地方
_log_state = {"path": None, "checked": 0}
_log_lock = threading.Lock()


def _stdout_file():
    """stdout 被重定向到的那个普通文件；不是重定向（终端、管道）就返回 None。"""
    try:
        path = os.readlink("/proc/self/fd/1")
    except OSError:
        return None
    return path if os.path.isfile(path) else None


def rotate_file(path, keep=LOG_KEEP):
    """把 path 换成 path.1，旧的依次往后挪，最多留 keep 份。返回是否真的转了。"""
    if not os.path.isfile(path):
        return False
    for i in range(keep - 1, 0, -1):
        older = "%s.%d" % (path, i)
        if os.path.exists(older):
            os.replace(older, "%s.%d" % (path, i + 1))
    os.replace(path, path + ".1")
    return True


def _maybe_rotate_log(length):
    """写日志之前看一眼文件多大，超了就换一份。每 32 行看一次，别每次 stat。"""
    if LOG_MAX <= 0:
        return
    _log_state["checked"] += 1
    if _log_state["checked"] % 32:
        return
    with _log_lock:
        path = _log_state["path"] or _stdout_file()
        _log_state["path"] = path
        if path is None:
            return
        try:
            if os.fstat(1).st_size + length < LOG_MAX:
                return
            sys.stdout.flush()
            if not rotate_file(path):
                return
            # 同一个路径重新开一份挂到 fd 1 上：原来的 stdout 对象继续用它，
            # 不要去换 sys.stdout —— 旧对象被回收时会把 fd 1 关掉
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.dup2(fd, 1)
            finally:
                os.close(fd)
            # 这句直接写：log() 会回到这里，锁不是可重入的
            sys.stdout.write("%s 日志超过 %d 字节，已换成 %s.1\n"
                             % (time.strftime("%Y-%m-%d %H:%M:%S"), LOG_MAX, path))
            sys.stdout.flush()
        except OSError:
            return


def log(msg):
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        _maybe_rotate_log(len(line))
        sys.stdout.write(line)
        sys.stdout.flush()
    except (OSError, ValueError):
        # 日志写不出去不该把请求带塌：fd 没了就悄悄算了
        pass


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def sanitize_cookie_path(raw):
    """Accept only a plain absolute path; anything odd falls back to '/'."""
    if not raw or not isinstance(raw, str):
        return "/"
    raw = raw.strip()
    if not raw.startswith("/") or len(raw) > 200:
        return "/"
    if "//" in raw or ".." in raw or "\\" in raw:
        return "/"
    if not all(c.isalnum() or c in "/_-." for c in raw):
        return "/"
    if not raw.endswith("/"):
        raw += "/"
    return raw








PRESENCE_MAX_AGE = checkin.PRESENCE_TTL


def presence_cookie(value, cookie_path="/"):
    return "%s=%s; Path=%s; HttpOnly; SameSite=Lax; Max-Age=%d" % (
        checkin.COOKIE_NAME,
        value,
        cookie_path,
        PRESENCE_MAX_AGE,
    )


def has_presence(headers, secret):
    raw = headers.get("Cookie") or ""
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(checkin.COOKIE_NAME + "="):
            return checkin.check_presence(secret, part.split("=", 1)[1])
    return False


def public_base_url(seq=None):
    """活动的对外地址：配置里的前缀 + 它自己的 token（token 变了地址就变）。"""
    prefix = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
    token = panel.event_token(db.event_id() if seq is None else seq)
    return (prefix + "/" + token) if token else prefix


MIME_OVERRIDES = {
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

FAVICON_CANDIDATES = ("favicon.png", "favicon.svg", "favicon.ico", "favicon.webp")
FAVICON_TYPES = {
    "favicon.png": "image/png",
    "favicon.svg": "image/svg+xml",
    "favicon.ico": "image/x-icon",
    "favicon.webp": "image/webp",
}


def favicon_markup():
    """<link rel="icon"> tags for whatever icon sits in assets/img/.

    Drop assets/img/favicon.png (or .svg / .ico / .webp) and it is picked up on
    the next page load - no code change, no rebuild.  With no icon present we emit
    the empty data URI so the browser stops asking for a root /favicon.ico,
    which this service deliberately does not serve.
    """
    links = []
    for name in FAVICON_CANDIDATES:
        if not os.path.isfile(os.path.join(ASSETS_DIR, "img", name)):
            continue
        href = "assets/img/" + name
        links.append('<link rel="icon" type="%s" href="%s">' % (FAVICON_TYPES[name], href))
        if name == "favicon.png":
            touch = "assets/img/apple-touch-icon.png"
            if not os.path.isfile(os.path.join(ASSETS_DIR, "img", "apple-touch-icon.png")):
                touch = href
            links.append('<link rel="apple-touch-icon" href="%s">' % touch)
    if not links:
        return '<link rel="icon" href="data:,">'
    return "\n".join(links)


def fmt_ts(ts):
    return None if ts is None else round(float(ts), 3)


def module_record(rec, spec=None, with_pinyin=True):
    """模块给的记录原样带下去，平台只按搜索声明补读音索引。

    平台不认识记录里的字段（那是模块的快照形状）；唯一插手的地方是"姓名字段怎么念"——
    前端没有汉字读音表，所以这里按 `features.search.name` 指的那个字段算好。
    """
    out = dict(rec or {})
    if with_pinyin:
        field = (spec or {}).get("name") or ""
        text = str(out.get(field) or "") if field else ""
        if text:
            out["pys"] = model.name_syllables(text)
    return out

# --------------------------------------------------------------------------- #
# handler
# --------------------------------------------------------------------------- #
_HOST_CACHE = {"ts": 0.0, "value": None}


def host_stats(root="/proc", sleep=0.12):
    """CPU and memory of this machine, for the owner's title bar.

    Linux only: it reads /proc/stat and /proc/meminfo.  Anywhere else it returns
    None, and the panel hides the widget instead of showing invented numbers.
    The 0.12 s sample window is fine - ThreadingHTTPServer gives every request its
    own thread, and the panel asks for this once every five seconds.
    """
    try:
        with open(os.path.join(root, "stat"), "r") as fh:
            first = fh.readline().split()
        time.sleep(sleep)
        with open(os.path.join(root, "stat"), "r") as fh:
            second = fh.readline().split()
    except OSError:
        return None
    try:
        def split(row):
            vals = [int(v) for v in row[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return sum(vals) - idle, sum(vals)

        busy_a, total_a = split(first)
        busy_b, total_b = split(second)
    except (ValueError, IndexError):
        return None

    cpu = None
    if total_b > total_a:
        cpu = max(0.0, min(100.0, 100.0 * (busy_b - busy_a) / (total_b - total_a)))

    mem_total = mem_avail = None
    try:
        info = {}
        with open(os.path.join(root, "meminfo"), "r") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                field = rest.split()
                if field:
                    info[key] = int(field[0])
        mem_total = info.get("MemTotal")
        mem_avail = info.get("MemAvailable", info.get("MemFree"))
    except (OSError, ValueError):
        pass

    mem = None
    mem_mb = None
    if mem_total:
        mem_mb = mem_total / 1024.0
        if mem_avail is not None:
            mem = 100.0 * (mem_total - mem_avail) / mem_total

    return {
        "cpu": cpu,
        "mem": mem,
        "memTotalMB": mem_mb,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "python": platform.python_version(),
        "dataDir": db.DATA_DIR,
        "uptime": time.time() - START_TIME,
        "version": VERSION,
    }


def _modules_by_id(conn):
    """Every installed module, by id, with the administrator's switch applied."""
    return {m["id"]: m for m in
            modules.all_modules(VERSION, disabled=panel.disabled_modules(conn))}


# 模块被停用时，活动接口与页面统一用这一句话说明白（与 pages/module-off.html 同一句）
MODULE_OFF_MSG = "此活动必需的资源已被管理员停用"


def _with_stopped(view, stopped):
    """活动列表上标一笔：它用的模块现在停着（页面打不开，数据一条没动）。"""
    view["stopped"] = bool(view.get("kind")) and view["kind"] in (stopped or set())
    return view


def _with_orphan(view, row):
    """活动列表上再标一笔：负责人账号已经被彻底删掉了。

    只有已归档的活动会这样（删账号时不拦归档的活动）；取消归档时得同时指定新负责人。
    """
    view["ownerMissing"] = not row["owner_id"] or not (view.get("ownerName") or "")
    return view


def presets_ok_slug(slug):
    return bool(slug) and bool(presets.ID_RE.match(slug))


def service_items():
    """服务有效性提示：这台机器现在的状态，只读推导，随时可再取一次。"""
    try:
        disk = shutil.disk_usage(db.DATA_DIR)
    except OSError:
        disk = None
    items = health.signals(host_stats_cached(), disk)
    if safemode.enabled():
        items.insert(0, {
            "id": "svc:safe-mode",
            "kind": "service",
            "level": "important",
            "title": "服务处于安全模式",
            "body": "活动内的数据暂不开放，平台设置仍可修改；改完配置文件重启即退出。",
            "sticky": True,
        })
    return items


class Conflict(model.ApiError):
    """A write that lost the race with another session."""

    def __init__(self, msg):
        super().__init__(msg, 409)


class SudoRequired(model.ApiError):
    """A sensitive endpoint refused: this session has not re-entered a password.

    Not an error the operator has to read - the panel turns it into a password
    prompt and repeats the request - but it is an error on the wire, because
    nothing happened and nothing may be reported as done.
    """

    def __init__(self):
        super().__init__("该操作需要 sudo 模式", 403)


# Routes whose handler can tell that the requested state is already the truth.
# They get to answer "already done" instead of "conflict".
DEFERRED_CONFLICT_ROUTES = ("/account/decide", "/event/archive", "/module/toggle",
                            "/preset/favorite", "/preset/delete")

# The console is where several staff work at once, so the objects are small:
# one student is one object.  Two volunteers registering two different people
# have nothing to disagree about.
CONSOLE_TARGETS = (
    ("/api/admin/mark", "student_id", "student:%s"),
    ("/api/admin/undo", "student_id", "student:%s"),
    ("/api/admin/set_time", "student_id", "student:%s"),
    ("/api/admin/student", "id", "student:%s"),
    ("/api/admin/student_delete", "id", "student:%s"),
    ("/api/admin/import", None, "roster"),
    ("/api/admin/reset_tickets", None, "draw"),
    ("/api/admin/settings", None, "settings"),
    ("/api/admin/phase", None, "settings"),
    ("/api/admin/finalize", None, "draw"),
    ("/api/admin/unfinalize", None, "draw"),
    ("/api/admin/draw", None, "draw"),
    ("/api/admin/draw_void", None, "draw"),
    ("/api/admin/member", None, "members"),
)

WRITE_TARGETS = (
    # (route, field holding the id, scope template)
    ("/account/decide", "id", "account:%s"),
    ("/account/role", "id", "account:%s"),
    ("/profile", None, "account:self"),
    ("/avatar", None, "account:self"),
    ("/password", None, "account:self"),
    ("/event/title", "seq", "event:%s"),
    ("/event/rotate", "seq", "event:%s"),
    ("/event/archive", "seq", "event:%s"),
    ("/event/member", "seq", "event:%s:members"),
    ("/preset", "id", "preset:%s"),
    ("/preset/delete", "id", "preset:%s"),
    ("/preset/favorite", "id", "preset:%s"),
    ("/module/toggle", "id", "module:%s"),
)


def console_target(path, data, seq):
    """Which object a console write touches, named within its activity."""
    for route, field, template in CONSOLE_TARGETS:
        if path != route:
            continue
        if field is None:
            return "event:%s:%s" % (seq, template % ())
        raw = (data or {}).get(field)
        if raw in (None, ""):
            return None
        return "event:%s:%s" % (seq, template % raw)
    return None


def write_target(path, data):
    """Which object a panel write touches; no target means nothing to compare.

    A conflict is only ever about one object.  Two administrators working on
    different accounts, or on an account and an activity, have nothing to
    disagree about, however busy the panel is.
    """
    for route, field, template in WRITE_TARGETS:
        if path != route:
            continue
        if field is None:
            return template % ()
        raw = (data or {}).get(field)
        if raw in (None, ""):
            return None
        return template % raw
    return None


# CPU/内存的采样要读两次 /proc 并 sleep 0.12s：多个标签页各轮询一次就是白白干活。
# 缓存三秒，同一次轮询的人共用一份读数。
_HOST_CACHE = {"at": 0.0, "payload": None, "ttl": 3.0}


def host_stats_cached(ttl=None):
    """host_stats() with a few seconds of reuse; safe to call per request."""
    now = time.time()
    ttl = _HOST_CACHE["ttl"] if ttl is None else ttl
    if _HOST_CACHE["payload"] is not None and now - _HOST_CACHE["at"] < ttl:
        return _HOST_CACHE["payload"]
    stats = host_stats()
    _HOST_CACHE["payload"] = stats
    _HOST_CACHE["at"] = now
    return stats


def _scope_of(revs, scope):
    """The part of a revision map one stream cares about (all of it by default).

    ``modules`` 是站级的开关（模块停用/启用）：任何一个流都得转发，页面才能
    立刻知道自己这一场已经不可用，不必等下一次轮询。
    """
    if not scope:
        return revs
    return {k: v for k, v in revs.items() if k == scope or k == "modules"}


# 令牌能碰的边界。分三档比给每个接口写一遍判断靠谱：默认就是"读写按方法分"，
# 平台级的东西单独列出来，要 admin 范围才放行。
TOKEN_ADMIN_ROUTES = (
    "/account/",       # 审批、停用、改角色
    "/event/",         # 活动创建、改名、重置地址、归档、成员
    "/module/",        # 模块开关
    "/screen_pack",    # 外部资源导入/删除
    "/preset/import",
    "/preset/delete",
    "/host",           # 机器与在线人数
    "/register",       # 令牌不该能注册新账号
)

# 令牌管不了自己的账号：改昵称、换头像、改登录名、改密码、进出 sudo、发/吊销令牌。
# 这几件事都要求"人在这里"，令牌背后没有人可以问，出问题也没法自证。
TOKEN_FORBIDDEN = (
    "/token", "/profile", "/avatar", "/password", "/sudo", "/logout", "/switch",
    "/assistant",      # 里面存着这个人自己的模型密钥，属于账号隐私
)


def _check_token_scope(path, method, token_row):
    """Refuse a token request its scopes do not cover.  The account's own role still applies."""
    scopes = [s for s in (token_row["scopes"] or "").split(",") if s]
    if any(path.startswith(prefix) for prefix in TOKEN_FORBIDDEN):
        raise model.ApiError("令牌不能改账号自己的信息，也不能管理令牌，请登录面板操作", 403)
    needs = "read" if method == "GET" else "write"
    if any(path.startswith(prefix) for prefix in TOKEN_ADMIN_ROUTES):
        needs = "admin"
    if needs not in scopes:
        raise model.ApiError("这张令牌没有「%s」权限" % panel.TOKEN_SCOPE_LABEL.get(needs, needs),
                             403)


def _attachment_header(filename):
    """A download name that survives a Chinese filename.

    HTTP headers are latin-1, so the readable name goes in ``filename*`` (RFC
    5987) and the plain ``filename`` stays as an ASCII fallback for old clients.
    """
    plain = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "download"
    return ("Content-Disposition",
            'attachment; filename="%s"; filename*=UTF-8\'\'%s'
            % (plain, quote(filename, safe="")))


def _screen_packs():
    """可用的展示屏幕包：实例目录优先，内置目录补充，同名以实例为准。"""
    return packs.listing()


# 外部资源是全平台共享的代码，只有这两个角色能往里放东西
SCREEN_PACK_ROLES = ("owner", "superadmin")


def _can_manage_packs(acc):
    return bool(acc) and acc["role"] in SCREEN_PACK_ROLES


def _account_label(conn, account_id):
    """A human name for a notice body; never the login name (that is a credential)."""
    row = conn.execute("SELECT name, uid FROM accounts WHERE id=?",
                       (int(account_id or 0),)).fetchone()
    if row is None:
        return "有人"
    return panel.public_name(row["name"], row["uid"])


def _roster_viewable(acc, row):
    """Who may open a roster: its creator, an admin, or anyone when it is shared.

    Visibility only decides "can this be seen".  Being allowed to change it is a
    separate question (``lists.can_manage``), and everyone else's rosters stay
    read-only in the panel.
    """
    if row is None or acc is None:
        return False
    if acc["role"] in ("owner", "superadmin"):
        return True
    return row["owner_id"] == acc["id"] or row["visibility"] != "private"


def _int_arg(query, name, default, low, high):
    try:
        value = int((query.get(name) or [str(default)])[0])
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def quote_path(value):
    from urllib.parse import quote
    return quote(value, safe="/")


def _return_to(back, mount):
    """登录页的 ``s_return`` 换成一个地址；什么都不说的时候是空串。

    只认两种：面板里某个页面的名字（在面板里被拒，回到那一页），以及本机上的
    单斜杠路径（活动控制台）。别的都不给，免得被拿去跳到本站之外。
    """
    back = (back or "").strip()
    if re.fullmatch(r"[a-z][a-z-]{0,30}", back):
        return "%s/panel#/%s" % (mount, back)
    if re.fullmatch(r"/(?!/)[\w\-./%~]*", back):
        return back
    return ""


def _accounts_with_usage(conn, rows, days=0):
    """用户管理那张表要的账号：带上各自存了多少对话，以及能不能彻底删掉。

    配额是平台资源，谁用了多少得一眼看见，不然调配额就是猜。一次聚合查询，
    不按账号各查一次。``days`` 是彻底删除的停用门槛（静态设置）。
    """
    used = {}
    for row in conn.execute(
            "SELECT account_id, COALESCE(SUM(size), 0) AS n FROM assistant_sessions"
            " WHERE deleted_at=0 GROUP BY account_id"):
        used[int(row["account_id"])] = int(row["n"] or 0)
    out = []
    for row in rows:
        view = panel.view(row)
        view["chatUsed"] = used.get(int(row["id"]), 0)
        # 不能删的理由（空串就是能删）：界面照着它决定按钮出不出现
        view["deleteBlocked"] = panel.delete_blocker(conn, row, days)[1]
        out.append(view)
    return out


class ConsoleUser:
    """Who is at an activity console: a panel account with a seat on it.

    The console used to keep its own password and its own session table.  It is
    a panel page now - same account, same cookie, and a seat on the activity is
    what opens the door - so the only identity here is a panel account, and
    ``role`` is the seat's role inside this one activity.
    """

    def __init__(self, acc, role, token, conn):
        self.acc = acc
        self.role = role
        self.token = token
        self.conn = conn

    @property
    def name(self):
        return self.acc["name"] or self.acc["username"]

    def unlock_until(self):
        return panel.unlock_until(self.conn, self.token)

    def unlocked(self):
        return self.unlock_until() > time.time()

    def touch_unlock(self):
        with panel.tx() as tx:
            return panel.touch_unlock(tx, self.token)

    def set_unlock(self, on):
        with panel.tx() as tx:
            return panel.set_unlock(tx, self.token, on)


class Handler(BaseHTTPRequestHandler):
    server_version = "LotteryHTTP/" + VERSION
    protocol_version = "HTTP/1.1"
    # which scope a write belongs to; set by the dispatcher before routing
    _scope = None

    # ---------------- plumbing ----------------
    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self._status = status
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for key, val in (headers or []):
            self.send_header(key, val)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=200, headers=None):
        if self.command == "POST" and status == 200:
            if self._scope:
                bus.bump(self._scope)
            if getattr(self, "_target", None):
                bus.bump(self._target)
        if isinstance(obj, dict):
            # 每个回答都带上版本：页面据此发现自己跑的是重新部署前的脚本
            if "version" not in obj:
                obj = dict(obj, version=VERSION)
            if "revs" not in obj:
                obj = dict(obj, revs=bus.snapshot())
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str), headers=headers)

    def _err(self, msg, status=400):
        self._json({"ok": False, "error": msg}, status)

    def _json_cached(self, obj):
        """A read-only body a client may re-ask for cheaply.

        The projector and the public board are the pages that poll hardest, so
        an unchanged answer is a 304 rather than the whole roster again.  The
        clock is left out of the comparison: it moves every second and would
        make every revalidation look different.
        """
        if "revs" not in obj:
            obj = dict(obj, revs=bus.snapshot())
        if "version" not in obj:
            # 带上版本：部署换了脚本，ETag 也跟着变，投影页会拿到新的一份并自行刷新
            obj = dict(obj, version=VERSION)
        stable = {k: v for k, v in obj.items() if k != "serverTime"}
        stamp = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)
        etag = '"%s"' % hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        body = json.dumps(obj, ensure_ascii=False, default=str)
        self._send(200, body, "application/json; charset=utf-8", headers=[("ETag", etag)])

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location or "/")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _peek(self):
        """The request body, read for routing decisions only."""
        raw = getattr(self, "_raw_body", b"") or b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _base_rev(self, target):
        raw = self.headers.get("X-Rev-Map") or ""
        try:
            known = json.loads(raw) if raw else {}
        except ValueError:
            return None
        if not isinstance(known, dict) or target not in known:
            return None
        try:
            return int(known[target])
        except (TypeError, ValueError):
            return None

    def _stream(self, scope=None, of_event=False):
        """Server-sent events: the panel says when something changed.

        A stream that drops only delays the notice - the next event carries the
        whole revision map, so a client cannot end up behind after a gap.

        ``scope`` narrows what is worth sending: a projector watching one
        activity should not be woken by another activity's writes, so those
        changes are noticed and dropped instead of forwarded.

        ``of_event`` says this stream belongs to one activity's pages: only they
        care that the activity's module was just switched off, and only they may
        ask (``db.event_id()`` needs a selected activity).
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        bus.watch_enter()
        try:
            seen = bus.snapshot()
            sent = _scope_of(seen, scope)
            self._sse({"revs": sent, "version": VERSION})
            self.wfile.flush()
            while True:
                revs = bus.wait_for_change(seen, timeout=25.0)
                if revs == seen:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                seen = revs
                mine = _scope_of(revs, scope)
                if mine == sent:
                    continue  # somebody else's activity moved
                sent = mine
                if of_event and self._module_gone(revs):
                    # 这一场刚被停用：告诉页面收工，它落地就是那张说明页
                    self._sse({"revs": mine, "version": VERSION, "off": True})
                    self.wfile.flush()
                    return
                self._sse({"revs": mine, "version": VERSION})
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.watch_leave()

    def _module_gone(self, revs):
        """模块开关动过、而且这一场正被停用：流可以收尾了。

        只在站级的 ``modules`` 键动过时才查一次库，平时一毛钱开销都没有。
        """
        if not revs.get("modules"):
            return False
        try:
            seq = db.event_id()
            return bool(panel.stopped_module(panel.connect(), seq))
        except Exception:
            return False

    def _sse(self, payload):
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(("data: %s\n\n" % data).encode("utf-8"))

    def _console_account(self):
        """The panel account behind this request, with its session token.

        None means there is no session at all - that is the case that should
        send someone to the login page.  A signed-in account that is simply not
        on this activity is a different answer (403): bouncing it to a login it
        can already pass would loop forever.
        """
        conn = panel.connect()
        token = self._panel_token()
        acc = panel.session_account(conn, token)
        if acc is None:
            return None
        return acc, token, conn

    def _console_seat(self):
        """The panel account behind this request and its seat on the activity.

        None when there is no session, when the account is not on this activity,
        or when its seat was revoked - the console has no identity of its own to
        fall back on any more.
        """
        found = self._console_account()
        if found is None:
            return None
        acc, token, conn = found
        seat = panel.member_of(conn, db.event_id(), acc["id"])
        if seat is None or seat["state"] != "active":
            return None
        return ConsoleUser(acc, seat["role"], token, conn)

    def _require_console(self):
        if self._console_account() is None:
            raise model.ApiError("未登录或登录已过期", 401)
        me = self._console_seat()
        if me is None:
            raise model.ApiError("你不是这个活动的成员", 403)
        return me

    def _console_url(self):
        """The /admin URL of whichever activity this request is addressed to."""
        raw = (self.path or "").split("?")[0]
        for seg in raw.split("/"):
            if seg and panel.event_seq(seg) is not None:
                return panel.mount_prefix(raw, seg) + "/" + seg + "/admin"
        return ""

    def _feature(self, name):
        """这个活动的模块申没申请某个能力。"""
        try:
            cfg = db.all_config(db.connect())
        except Exception:
            return False
        kind = str(cfg.get("kind") or "").strip()
        if not kind:
            return False
        feats = (modules.get(kind, VERSION) or {}).get("features") or {}
        return name in feats

    def _panel_login_url(self, back=None):
        """Where this request should sign in, and where to come back to."""
        token = None
        for seg in (self.path or "").split("/"):
            if seg and panel.event_seq(seg) is not None:
                token = seg
                break
        mount = panel.mount_prefix((self.path or "").split("?")[0], token)
        target = back if back is not None else (self.path or "")
        return "%s/login?s_return=%s" % (mount, quote_path(target.split("#")[0]))

    def _drain_body(self):
        """Read the request body once, before any handler can bail out.

        With HTTP/1.1 keep-alive, returning before consuming Content-Length
        leaves the body in the socket, and the *next* request line is parsed out
        of it - which is how a failed login turned into
        "Unsupported method ('{"username":...}POST')".  So the body is consumed
        up front and the handlers only parse it.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._raw_body = b""
            return
        if length > MAX_BODY:
            # Still consume it, so the connection stays in sync, but never hold
            # an unbounded body in memory.
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            self._raw_body = b""
            raise model.ApiError("请求体过大", 413)
        self._raw_body = self.rfile.read(length)

    def _body(self):
        raw = getattr(self, "_raw_body", None)
        if raw is None:
            self._drain_body()
            raw = self._raw_body
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise model.ApiError("请求体不是合法 JSON", 400)
        if not isinstance(data, dict):
            raise model.ApiError("请求体必须是对象", 400)
        return data

    def _check_csrf(self):
        # A custom header cannot be set by a cross-site HTML form.
        if self.headers.get("X-Requested-With") != "CAEventManager":
            raise model.ApiError("缺少请求标记(可能是跨站请求)", 403)

    # ---------------- verbs ----------------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _shortlink(self, raw_path):
        """`/s/<code>`：查表、记一次访问、跳到那个活动的目标页。

        短链随时可能被删除、过期或用尽次数，所以每次都重新查，不缓存。
        """
        code = raw_path[len("/s/"):].strip("/")
        now = time.time()
        if now - SHORT_MISS["since"] > SHORT_WINDOW:
            SHORT_MISS["count"] = 0
            SHORT_MISS["since"] = now
        conn = panel.connect()
        link = shortlinks.resolve(conn, code, now) if code else None
        if link is None:
            SHORT_MISS["count"] += 1
            if SHORT_MISS["count"] >= SHORT_MAX_MISS:
                time.sleep(0.5)          # cheap brake on guessing
            return self._send(404, SHORTLINK_MISS_PAGE, "text/html; charset=utf-8")
        with panel.tx() as tx:
            shortlinks.hit(tx, link["code"])
        token = panel.event_token(link["eventSeq"])
        archived = panel.connect().execute(
            "SELECT archived_at FROM events WHERE seq=?", (link["eventSeq"],)).fetchone()
        if not token or (archived and archived["archived_at"]):
            return self._send(404, b"404", "text/plain; charset=utf-8")
        prefix = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
        location = "%s/%s/%s" % (prefix, token, link["target"])
        log("shortlink %s -> %s" % (link["code"], location))
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _dispatch(self, method):
        """Three route families, in order: panel (root), short link, event (token)."""
        global EVENT_TOKENS
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        EVENT_TOKENS = panel.event_tokens()
        try:
            if parsed.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")

            self._drain_body()

            if parsed.path.startswith("/s/"):
                return self._shortlink(parsed.path)

            kind, route, token = panel.classify(parsed.path, EVENT_TOKENS)
            self._scope = "panel" if kind == "panel" else None
            if kind == "panel":
                return self._panel(method, route, parsed.path, query)

            if kind == "join":
                return self._join(method, route, token)

            # every event request reads and writes that event's own database;
            # 活动号只在内部传递，对外一律用地址里的 token
            seq = panel.event_seq(token) if token else None
            path = route
            if seq is None or path is None:
                # No panel route and no event token: nothing to serve.
                return self._send(404, not_found_page(), "text/html; charset=utf-8")
            db.set_event(seq)
            self._token = token
            # 静态资源不算活动内容：安全模式与模块停用都照给，不然说明页自己没样式
            static_ok = path.startswith("/assets/") or path == "/favicon.ico"
            if safemode.enabled() and not static_ok:
                # 安全模式：活动页与活动接口一律不提供。数据原样留着，不兜底、不改写
                if not path.startswith("/api/"):
                    # 页面给一张维护页，地址不动：服务恢复后刷新就回到原来那页
                    return self._maintenance_page(cfg=None)
                raise safemode.refuse()
            # 模块被管理员停用：这一场的页面与接口一律不提供（数据一条不动，
            # 启用之后刷新即恢复）。停用是全站级的，见 panel.stopped_module
            stopped_kind = None if static_ok else panel.stopped_module(
                panel.connect(), seq)
            if stopped_kind:
                if not path.startswith("/api/"):
                    return self._module_off_page(stopped_kind)
                raise model.ApiError(MODULE_OFF_MSG, 503)
            if path.startswith("/api/"):
                self._scope = "event:%s" % seq
                self._api(method, path, query)
            else:
                self._page(method, path)
        except Conflict as exc:
            self._json({"ok": False, "error": exc.msg, "conflict": True,
                        "revs": bus.snapshot()}, 409)
        except SudoRequired as exc:
            # The client answers this by asking for the password and repeating
            # the same request, so it must be able to tell it apart from a plain
            # refusal.
            self._json({"ok": False, "error": exc.msg, "sudoRequired": True}, 403)
        except model.ApiError as exc:
            self._err(exc.msg, exc.code)
        except BrokenPipeError:
            pass
        except Exception:
            log("ERROR %s %s\n%s" % (method, self.path, traceback.format_exc()))
            self._err("服务器内部错误,请看服务日志", 500)

    # ---------------- public signup page ----------------

    def _join(self, method, route, token):
        """A public 报名 page: no account, no session, one token in the URL."""
        lx = lists.connect()
        row = lists.signup_by_token(lx, token)
        if row is None:
            return self._err("not found", 404)
        # 归档的报名对外就当它不在：链接还指着它，但页面与提交都停下
        target = lists.roster(lx, row["roster_id"]) if row is not None else None
        if lists.is_archived(row) or lists.is_archived(target):
            return self._err("这个报名已经结束", 410)

        if method == "GET" and route.startswith("/assets/"):
            rel = route[len("/assets/"):]
            if ".." in rel:
                return self._err("bad path", 400)
            full = os.path.join(ASSETS_DIR, rel)
            if not os.path.isfile(full):
                return self._err("not found", 404)
            with open(full, "rb") as fh:
                body = fh.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            return self._send(200, body, ctype, [("Cache-Control", "public, max-age=60")])

        if method == "GET" and route in ("", "/"):
            # 相对路径的 assets/… 只有在地址以 / 结尾时才算得对
            raw = (self.path or "").split("?")[0]
            if not raw.endswith("/"):
                return self._redirect(raw + "/")
            return self._serve_panel_page("signup.html")

        if method == "GET" and route == "/api/state":
            count = lists.signup_counts(lx, [row["id"]]).get(row["id"], 0)
            view = lists.signup_view(lx, row, count)
            target = lists.roster(lx, row["roster_id"])
            return self._json({
                "ok": True,
                "signup": {k: view[k] for k in (
                    "title", "note", "status", "statusLabel", "capacity",
                    "opensAt", "closesAt", "entries", "fields", "selfEdit",
                    "collect", "theme")},
                "rosterName": target["name"] if target else "",
                "limited": bool(row["filter_roster_id"]),
            })

        if method == "POST" and route == "/api/submit":
            data = self._body()
            with lists.tx() as tx:
                res = lists.submit_signup(tx, row, data, self.client_address[0])
            with panel.tx() as px:
                panel.audit(px, "signup", "signup_submit", str(row["id"]),
                            panel.audit_note(sid=data.get("sid"),
                                             name=data.get("name")))
            return self._json(res)

        return self._err("not found", 404)

    # ---------------- master panel ----------------

    def _panel(self, method, route, raw_path, query):
        conn = panel.connect()
        if method == "GET" and route.startswith("/assets/"):
            # 面板与活动族共用同一份静态资源：没有第二份，也没有回退链
            rel = route[len("/assets/"):]
            if ".." in rel:
                return self._err("bad path", 400)
            full = os.path.join(ASSETS_DIR, rel)
            if not os.path.isfile(full):
                return self._err("not found", 404)
            with open(full, "rb") as fh:
                body = fh.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            return self._send(200, body, ctype, [("Cache-Control", "public, max-age=60")])

        # ---- 使用文档：自带一套骨架，接口只有两个 ----
        if route.startswith("/api/docs/"):
            if method != "GET":
                return self._err("method not allowed", 405)
            acc = panel.session_account(conn, self._panel_token())
            if acc is None:
                raise model.ApiError("未登录或登录已过期", 401)
            if route == "/api/docs/tree":
                return self._json({"ok": True, "groups": assistant.doc_tree()})
            if route == "/api/docs/doc":
                want = (query.get("path") or [""])[0]
                text, err = assistant.read_doc(want)
                if err:
                    return self._err(err, 404)
                title = ""
                for group in assistant.doc_tree():
                    for item in group["docs"]:
                        if item["path"] == want:
                            title = item["title"]
                return self._json({"ok": True, "path": want, "title": title,
                                   "text": text})
            return self._err("not found", 404)

        if not route.startswith("/api/panel/"):
            if method != "GET":
                return self._err("method not allowed", 405)
            acc = panel.session_account(conn, self._panel_token())
            name = panel.PANEL_PAGES.get(route)
            if name is None:
                return self._err("not found", 404)
            # 挂载前缀被拼重过的旧地址（…/panel/panel）送回正主，别在错地址上待着
            mount = panel.cookie_path_for(raw_path, route).rstrip("/")
            home = panel.collapse_mount(mount)
            if route == "/panel" and home != mount:
                return self._redirect(home + "/panel")
            # signed-in users go straight to the console from the auth pages
            if acc is not None and route in ("/", "/register", "/login"):
                back = (query.get("s_return") or [""])[0]
                return self._redirect(_return_to(back, home) or (home + "/panel") or "panel")
            if acc is None and route in ("/panel", "/docs"):
                return self._redirect((home + "/login") or "login")
            return self._serve_panel_page(name)

        return self._panel_api(method, route, raw_path, query)

    def _serve_panel_page(self, name):
        path = os.path.join(PAGES_DIR, name)
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._err("not found", 404)
        body = body.replace(b"{{FOOTER}}", panel.FOOTER.encode("utf-8"))
        # 面板页面不属于任何活动：只补安全模式那条横幅，不碰活动的占位符
        body = substitute_page(body, {})
        return self._send(200, body, "text/html; charset=utf-8")

    def _panel_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(panel.COOKIE + "="):
                return part.split("=", 1)[1]
        return None

    def _bearer(self):
        """The API token presented in Authorization, if any."""
        raw = (self.headers.get("Authorization") or "").strip()
        if not raw.lower().startswith("bearer "):
            return ""
        return raw[7:].strip()

    def _panel_api(self, method, route, raw_path, query=None):
        conn = panel.connect()
        ip = self.client_address[0]
        path = route[len("/api/panel"):] or "/"
        query = query or {}
        # 每条请求自己负责把"经由令牌"的标记清掉：同一个线程会连着服务好几个请求
        panel.via("")
        self._via_token = None

        def body():
            # 令牌不会被人顺手带上（那是 cookie 才有的问题），所以不必要求标记头
            if getattr(self, "_via_token", None) is None and \
                    self.headers.get("X-Requested-With") != "CAEventManager":
                raise model.ApiError("缺少请求标记", 403)
            return self._body()

        def peek():
            raw = getattr(self, "_raw_body", b"") or b""
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                return {}
            return data if isinstance(data, dict) else {}

        if path == "/stream" and method == "GET":
            acc = panel.session_account(conn, self._panel_token())
            if acc is None:
                raise model.ApiError("未登录或登录已过期", 401)
            panel.touch_session(conn, self._panel_token())
            return self._stream()

        if path == "/me" and method == "GET":
            token = self._panel_token()
            acc = panel.session_account(conn, token)
            sudo_until = panel.session_sudo(conn, token) if acc else 0
            token_row = None
            if acc is None:
                # 令牌也可以问这一句：外部程序用它可以确认令牌好不好、是谁的
                presented = self._bearer()
                if presented:
                    acc, token_row = panel.token_account(conn, presented)
                    if acc is not None:
                        panel.touch_token(conn, token_row["id"])
            return self._json({"ok": True, "authed": acc is not None,
                               "me": panel.view(acc) if acc else None,
                               "sudoUntil": sudo_until,
                               "sudoTtl": panel.SUDO_TTL,
                               "viaToken": token_row["name"] if token_row else "",
                               "scopes": ([s for s in (token_row["scopes"] or "").split(",") if s]
                                          if token_row else []),
                               # 刷新之后侧栏上的小红点还得在：这里把挂着的对话带出去
                               "assistantPending": (sorted(assistant.pending_sessions(acc))
                                                    if acc else []),
                               "now": time.time(),
                               "version": VERSION})

        if path == "/invites" and method == "GET":
            acc = panel.session_account(conn, self._panel_token())
            if acc is None:
                raise model.ApiError("未登录或登录已过期", 401)
            rows = panel.invites_for(conn, acc["id"])
            titles = {}
            for r in rows:
                # the row on the events table is often empty: the name lives
                # in the event's own database, borrowed the way /events does it
                if not r["title"]:
                    db.set_event(r["seq"])
                    titles[r["seq"]] = (
                        db.all_config(db.connect()).get("title") or "")
            return self._json({"ok": True, "invites": [
                {"seq": r["seq"],
                 "title": r["title"] or titles.get(r["seq"], ""),
                 "role": r["role"],
                 "roleLabel": panel.SEAT_LABEL.get(r["role"], r["role"]),
                 "addedAt": r["added_at"],
                 "invitedBy": panel.public_name(r["inviter_name"], r["inviter_uid"])}
                for r in rows]})

        if path == "/register" and method == "POST":
            data = body()
            block = _REGISTER_HITS.get(ip)
            if block and block > time.time():
                raise model.ApiError("提交过于频繁，请稍后再试", 429)
            username = (data.get("username") or "").strip()
            name = (data.get("name") or "").strip()
            password = data.get("password") or ""
            note = (data.get("note") or "").strip()[:200]
            problem = panel.username_problem(username)
            if problem:
                # 「被占用」与「被保留」连状态码都一样，否则一看就知道是哪种
                if problem == panel.USERNAME_TAKEN:
                    _note_register_hit(ip)
                raise model.ApiError(problem, 400 if problem != panel.USERNAME_TAKEN else 409)
            if not name:
                raise model.ApiError("请填写称呼")
            if len(password) < 8:
                raise model.ApiError("密码至少 8 位")
            with panel.tx() as tx:
                # 被占用与被保留是同一句话：注册口不该成为"哪些名字存在"的探针
                if panel.username_taken(tx, username):
                    _note_register_hit(ip)
                    raise model.ApiError(panel.USERNAME_TAKEN, 409)
                tx.execute(
                    "INSERT INTO accounts (uid, username, name, role, status, pass, note, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (panel.next_uid(tx), username, name, "admin", "pending",
                     panel.hash_password(password), note, time.time()))
                panel.audit(tx, "anon", "register", username,
                            panel.audit_note(note=note))
            return self._json({"ok": True, "status": "pending"})

        if path == "/login" and method == "POST":
            data = body()
            block = _PANEL_FAILS.get(ip, 0)
            if block > time.time():
                raise model.ApiError("尝试过于频繁，请稍后再试", 429)
            username = (data.get("username") or "").strip()
            row = conn.execute("SELECT * FROM accounts WHERE lower(username)=lower(?)",
                               (username,)).fetchone()
            if row is not None and safemode.login_blocked(row):
                raise safemode.refuse_login()
            if row is None or not panel.verify_password(data.get("password") or "", row["pass"]):
                fails = _PANEL_FAILS.get(ip + ":n", 0) + 1
                _PANEL_FAILS[ip + ":n"] = fails
                if fails >= 6:
                    _PANEL_FAILS[ip] = time.time() + 30
                    _PANEL_FAILS[ip + ":n"] = 0
                raise model.ApiError("用户名或密码错误", 401)
            if row["status"] == "pending":
                raise model.ApiError("账号待审核，通过后才能登录", 403)
            if row["status"] == "rejected":
                raise model.ApiError("注册申请未通过", 403)
            if row["status"] == "disabled":
                raise model.ApiError("账号已停用", 403)
            if row["status"] == "banned":
                reason = (row["ban_reason"] or "").strip() \
                    if "ban_reason" in row.keys() else ""
                raise model.ApiError(
                    "账号已被封禁" + ("：" + reason if reason else ""), 403)
            _PANEL_FAILS.pop(ip, None)
            _PANEL_FAILS.pop(ip + ":n", None)
            with panel.tx() as tx:
                token = panel.create_session(tx, row["id"], ip)
                tx.execute("UPDATE accounts SET last_login=? WHERE id=?",
                           (time.time(), row["id"]))
                panel.audit(tx, row["username"], "login", None,
                            panel.audit_note(ip=ip))
            cookie = "%s=%s; Path=%s; HttpOnly; SameSite=Lax; Max-Age=%d" % (
                panel.COOKIE, token, panel.cookie_path_for(raw_path, route), panel.SESSION_TTL)
            return self._json({"ok": True, "me": panel.view(row)},
                              headers=[("Set-Cookie", cookie)])

        if path == "/logout" and method == "POST":
            body()
            token = self._panel_token()
            if token:
                with panel.tx() as tx:
                    panel.destroy_session(tx, token)
            cookie = "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % panel.COOKIE
            return self._json({"ok": True}, headers=[("Set-Cookie", cookie)])

        # ---- everything below needs a signed-in account ----
        # 两种凭据：面板会话 cookie，或 Authorization: Bearer cae-…（API 令牌）。
        # 令牌背后还是同一个账号，所以权限判断、审计一处都不用改；区别只有三点：
        # 没有 CSRF 标记这回事、走不到需要重新输密码的动作、只做令牌声明的范围。
        sess = self._panel_token()
        acc = panel.session_account(conn, sess)
        token_row = None
        if acc is not None:
            # 一次带登录态的请求就是「这个人还在」的证据，在线人数据此统计（内部按分钟节流）
            panel.touch_session(conn, sess)
        else:
            presented = self._bearer()
            if presented:
                acc, token_row = panel.token_account(conn, presented)
                if acc is None:
                    raise model.ApiError("令牌无效或已吊销", 401)
                panel.touch_token(conn, token_row["id"])
                self._via_token = token_row
                panel.via("token %s" % token_row["name"])
        if acc is None:
            raise model.ApiError("未登录或登录已过期", 401)

        if token_row is not None:
            _check_token_scope(path, method, token_row)
        elif (self.headers.get("X-Via") or "").lower() == "assistant":
            # 助手替这个人做的写操作，审计里要能看出来（读操作本来就不进审计）
            panel.via("assistant")

        # A conflict is only ever about the object this write touches: a change
        # to the same thing that the client has not seen yet.  Everything else
        # merges, however many sessions are open.
        target = write_target(path, peek()) if method == "POST" else None
        self._target = target
        self._stale = False
        if target:
            raw = self.headers.get("X-Rev-Map") or ""
            try:
                known = json.loads(raw) if raw else {}
            except ValueError:
                known = {}
            if isinstance(known, dict) and target in known:
                try:
                    base = int(known[target])
                except (TypeError, ValueError):
                    base = None
                if base is not None and base < bus.revision(target):
                    self._stale = True
                    if path not in DEFERRED_CONFLICT_ROUTES:
                        raise Conflict("另一个会话刚刚改过这一项，本次更改没有生效")

        def refuse_stale():
            """Called by a route that knows whether it would change anything."""
            if self._stale:
                raise Conflict("另一个会话刚刚改过这一项，本次更改没有生效")

        def need(*roles):
            if roles and acc["role"] not in roles:
                raise model.ApiError("当前账号无权执行该操作", 403)
            return acc


        def need_sudo():
            """Elevation is per session, so this is the only place it is checked."""
            if getattr(self, "_via_token", None) is not None:
                # 提权靠的是"人再输一次密码"，令牌背后没有人可以问
                raise model.ApiError("这个操作需要重新输入密码，令牌做不到", 403)
            until = panel.session_sudo(conn, sess)
            if until <= time.time():
                if until:
                    # 提权已经过期：清掉并记一次，reason 与手动退出分开写；
                    # 两个会话同时开着 sudo 时，靠 ip 分清关的是哪一个
                    with panel.tx() as tx:
                        panel.set_sudo(tx, sess, 0)
                        panel.audit(tx, acc["username"], "sudo_off", None,
                                    panel.audit_note(ip=ip, reason="timeout"))
                raise SudoRequired()

        if path == "/sudo" and method == "POST":
            data = body()
            block = _PANEL_FAILS.get(ip, 0)
            if block > time.time():
                raise model.ApiError("尝试过于频繁，请稍后再试", 429)
            if not panel.verify_password(data.get("password") or "", acc["pass"]):
                fails = _PANEL_FAILS.get(ip + ":n", 0) + 1
                _PANEL_FAILS[ip + ":n"] = fails
                if fails >= 6:
                    _PANEL_FAILS[ip] = time.time() + 30
                    _PANEL_FAILS[ip + ":n"] = 0
                raise model.ApiError("密码错误", 403)
            _PANEL_FAILS.pop(ip, None)
            _PANEL_FAILS.pop(ip + ":n", None)
            with panel.tx() as tx:
                until = panel.set_sudo(tx, sess, panel.SUDO_TTL)
                panel.audit(tx, acc["username"], "sudo_on", None,
                            panel.audit_note(ip=ip))
            return self._json({"ok": True, "sudoUntil": until,
                               "sudoTtl": panel.SUDO_TTL,
                               "now": time.time()})

        if path == "/sudo/exit" and method == "POST":
            body()
            with panel.tx() as tx:
                panel.set_sudo(tx, sess, 0)
                panel.audit(tx, acc["username"], "sudo_off", None,
                            panel.audit_note(ip=ip, reason="manual"))
            return self._json({"ok": True, "sudoUntil": 0})

        if path == "/invite" and method == "POST":
            data = body()
            seq = int(data.get("seq") or 0)
            action = (data.get("action") or "").strip()
            seat = conn.execute(
                "SELECT state FROM event_members WHERE seq=? AND account_id=?",
                (seq, acc["id"])).fetchone()
            if seat is None or seat["state"] != "pending":
                raise model.ApiError("这条邀请已经不在等待中了", 404)
            if action not in ("accept", "decline"):
                raise model.ApiError("未知操作")
            state = "active" if action == "accept" else "declined"
            with panel.tx() as tx:
                tx.execute(
                    "UPDATE event_members SET state=?, state_at=?"
                    " WHERE seq=? AND account_id=?",
                    (state, time.time(), seq, acc["id"]))
                panel.audit(tx, acc["username"],
                            "event_member_accept" if action == "accept"
                            else "event_member_decline", str(acc["uid"]),
                            panel.audit_note(event_id=seq))
            return self._json({"ok": True, "state": state})

        if path == "/password" and method == "POST":
            data = body()
            if not panel.verify_password(data.get("old") or "", acc["pass"]):
                raise model.ApiError("当前密码错误", 403)
            new = (data.get("new") or "").strip()
            if len(new) < 8:
                raise model.ApiError("新密码至少 8 位")
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET pass=? WHERE id=?",
                           (panel.hash_password(new), acc["id"]))
                panel.audit(tx, acc["username"], "password_change")
            return self._json({"ok": True})

        if path == "/accounts" and method == "GET":
            # 账号清单是超管才看得到的东西：普通管理员不该能枚举全站的人
            need("superadmin", "owner")
            # an installation can hold a lot of accounts: one page at a time,
            # and the search runs on the server so paging stays honest
            q = (query.get("q") or [""])[0].strip()
            decided = (query.get("decided") or ["0"])[0] in ("1", "true", "yes")
            offset = _int_arg(query, "offset", 0, 0, 100000)
            limit = _int_arg(query, "limit", 50, 1, 200)
            where, args = [], []
            if q:
                where.append("(username LIKE ? OR name LIKE ? OR note LIKE ?)")
                args += ["%" + q + "%"] * 3
            if decided:
                where.append("status <> 'pending' AND decided_at > 0")
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute("SELECT COUNT(*) AS n FROM accounts" + clause,   # sql-ok 只拼列名/表名/SET 片段，值都走 ?
                                 args).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM accounts" + clause +   # sql-ok 只拼列名/表名/SET 片段，值都走 ?
                " ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'superadmin' THEN 1 ELSE 2 END,"
                " CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,"
                " created_at LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
            return self._json({"ok": True, "me": panel.view(acc), "total": int(total or 0),
                               "offset": offset, "limit": limit,
                               "quotaDefault": panel.CHAT_QUOTA_DEFAULT,
                               "quotaMax": panel.CHAT_QUOTA_MAX,
                               "accounts": _accounts_with_usage(
                                   conn, rows,
                                   localconfig.static_limit(LOCAL_CFG,
                                                            "delete_account_after_days"))})

        if path == "/people" and method == "GET":
            # 「选人」的唯一数据源：只回编号、称呼与头像。登录名是凭据，一个都不给。
            # scene 决定"谁能查谁"——转移负责人、名单授权、邀请成员各有各的门槛，
            # 界面那边是同一份弹窗（assets/js/people.js）。
            scene = (query.get("scene") or [""])[0].strip()
            if scene == "transfer":
                seq = _int_arg(query, "seq", 0, 0, 1 << 30)
                seat = conn.execute(
                    "SELECT role, state FROM event_members WHERE seq=? AND account_id=?",
                    (seq, acc["id"])).fetchone()
                mine = seat is not None and seat["role"] == "owner" \
                    and seat["state"] == "active"
                if not mine:
                    # 负责人登不进来时由超管代做，与转移那个动作同一套规矩
                    need("superadmin", "owner")
            elif scene == "roster":
                rid = _int_arg(query, "id", 0, 0, 1 << 30)
                roster_row = lists.roster(lists.connect(), rid)
                if roster_row is None:
                    raise model.ApiError("名单不存在", 404)
                if roster_row["owner_id"] != acc["id"]:
                    raise model.ApiError("仅名单拥有者可以选人", 403)
            else:
                # 没有场景就是"全站可邀的人"，仍按账号清单的规矩
                need("superadmin", "owner")
            q = (query.get("q") or [""])[0].strip()
            ids = [int(x) for x in ((query.get("ids") or [""])[0] or "").split(",")
                   if x.strip().isdigit()]
            where = ["status='active'"]
            args = []
            if ids:
                where.append("id IN (%s)" % ",".join("?" * len(ids)))   # sql-ok 只拼占位符个数，值都走 ?
                args += ids
            if q:
                where.append("(username LIKE ? OR name LIKE ?)")
                args += ["%" + q + "%", "%" + q + "%"]
            sql = ("SELECT id, uid, name, avatar, avatar_at FROM accounts WHERE "
                   + " AND ".join(where) +    # sql-ok 只拼 WHERE 片段，值都走 ?
                   " ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'superadmin' THEN 1 ELSE 2 END,"
                   " name, id LIMIT 48")
            return self._json({"ok": True, "people": [
                panel.person_view(r) for r in conn.execute(sql, args).fetchall()]})

        # ---- 短链：活动的平台资产；模块申请 features.shortlink 之后才能生成 ----
        def event_seat(seq):
            seat = panel.member_of(conn, seq, acc["id"])
            if seat is None or seat["state"] != "active":
                raise model.ApiError("只有这个活动的成员能管它的短链", 403)
            return seat

        def shortlink_ok(seq):
            """模块申请了 features.shortlink，这个活动才有短链能力。"""
            if "shortlink" not in (_event_module(seq).get("features") or {}):
                raise model.ApiError("这个活动的模块没有申请短链能力", 403)

        def _event_module(seq):
            """这场活动用的模块。"""
            row = conn.execute("SELECT kind FROM events WHERE seq=?", (seq,)).fetchone()
            return _modules_by_id(conn).get(row["kind"] if row else "") or {}

        if path == "/shortlink" and method == "GET":
            seq = int((query.get("seq") or ["0"])[0] or 0)
            event_seat(seq)
            shortlink_ok(seq)
            return self._json({
                "ok": True,
                "links": shortlinks.list_for(conn, seq),
                # 只是给界面几个现成的候选，模块可以指向别的路径
                "suggest": SHORTLINK_SUGGEST,
                "limit": localconfig.static_limit(LOCAL_CFG, "shortlink_per_event"),
            })

        if path == "/shortlink" and method == "POST":
            data = body()
            seq = int(data.get("seq") or 0)
            event_seat(seq)
            shortlink_ok(seq)
            # 拿到 super.anyshortlink 的模块可以指向活动之外的地方（平台的三道拦截全放开）
            relaxed = "super.anyshortlink" in (_event_module(seq).get("features") or {})
            try:
                with panel.tx() as tx:
                    link = shortlinks.create(
                        tx, seq, str(data.get("target") or ""), acc["username"],
                        minutes=int(data.get("minutes") or 0),
                        max_hits=int(data.get("max_hits") or 0),
                        limit=localconfig.static_limit(LOCAL_CFG, "shortlink_per_event"),
                        relaxed=relaxed)
            except shortlinks.ShortlinkError as exc:
                raise model.ApiError(str(exc))
            prefix = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            return self._json({"ok": True, "link": link,
                               "url": prefix + "/s/" + link["code"]})

        if path == "/shortlink/delete" and method == "POST":
            data = body()
            code = str(data.get("code") or "").strip()
            row = conn.execute("SELECT event_seq FROM shortlinks WHERE code=?",
                               (code,)).fetchone()
            if row is None:
                raise model.ApiError("没有这条短链", 404)
            seat = panel.member_of(conn, int(row["event_seq"]), acc["id"])
            if seat is None or seat["state"] != "active":
                # 删别人的短链是替人做破坏性改动：只有超管能做，而且要进 sudo
                need("superadmin", "owner")
                need_sudo()
            with panel.tx() as tx:
                shortlinks.revoke(tx, code)
            return self._json({"ok": True, "code": code})

        if path == "/shortlink/overview" and method == "GET":
            need("superadmin", "owner")
            return self._json({"ok": True, "rows": shortlinks.overview(conn)})

        if path == "/events" and method == "GET":
            archived = (query.get("archived") or ["0"])[0] in ("1", "true", "yes")
            if archived:
                # archived activities are history: paged, never all at once
                try:
                    offset = max(0, int((query.get("offset") or ["0"])[0]))
                except ValueError:
                    offset = 0
                try:
                    limit = min(50, max(1, int((query.get("limit") or ["20"])[0])))
                except ValueError:
                    limit = 20
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM events WHERE archived_at<>0").fetchone()["n"]
                rows = conn.execute(
                    "SELECT e.*, NULL AS my_role, o.name AS owner_name"
                    " FROM events e"
                    " LEFT JOIN accounts o ON o.id = e.owner_id"
                    " WHERE e.archived_at<>0"
                    " ORDER BY e.archived_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)).fetchall()
                base = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
                stopped = panel.disabled_modules(conn)
                return self._json({
                    "ok": True,
                    "total": int(total or 0),
                    "offset": offset,
                    "limit": limit,
                    "events": [_with_orphan(_with_stopped(
                        panel.event_view(r, base, None), stopped), r) for r in rows],
                })
            rows = conn.execute(
                "SELECT e.*, m.role AS my_role, o.name AS owner_name"
                " FROM events e"
                " LEFT JOIN event_members m ON m.seq = e.seq AND m.account_id = ?"
                "   AND m.state='active'"
                " LEFT JOIN accounts o ON o.id = e.owner_id"
                " WHERE e.archived_at=0 AND m.seq IS NOT NULL"
                " ORDER BY e.created_at", (acc["id"],)).fetchall()
            # links come from the configured prefix plus each row's own token -
            # reading a per-event URL would point every row at one activity
            base = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            stopped = panel.disabled_modules(conn)
            events = [_with_orphan(_with_stopped(
                panel.event_view(r, base, r["my_role"]), stopped), r) for r in rows]
            # 活动名在每个活动自己的库里：缺名字的按它自己的活动号去补
            for row, view in zip(rows, events):
                if view["title"]:
                    continue
                db.set_event(row["seq"])
                view["title"] = db.all_config(db.connect()).get("title") or ""
            return self._json({"ok": True, "events": events})

        if path == "/account/decide" and method == "POST":
            actor = need("superadmin", "owner")
            data = body()
            target = conn.execute("SELECT * FROM accounts WHERE id=?",
                                  (int(data.get("id") or 0),)).fetchone()
            if target is None:
                raise model.ApiError("账号不存在", 404)
            if target["role"] == "owner":
                raise model.ApiError("站长的账号不可操作", 403)
            if actor["role"] == "superadmin" and target["role"] != "admin":
                raise model.ApiError("超级管理员只能审核普通管理员", 403)
            action = (data.get("action") or "").strip()
            status = {"approve": "active", "reject": "rejected",
                      "disable": "disabled", "enable": "active",
                      "ban": "banned", "unban": "active"}.get(action)
            if status is None:
                raise model.ApiError("未知操作")
            reason = (data.get("reason") or "").strip()[:120]
            if action == "ban" and not reason:
                raise model.ApiError("封禁需要写明原因")
            if target["status"] == status:
                # somebody already put it there: the same intent twice is not a
                # conflict, and the first decision keeps its time and author
                return self._json({"ok": True, "status": status, "already": True})
            refuse_stale()
            # Locking someone out is the one account decision that cannot be
            # undone by the other side, so it asks for the password again.
            if action in ("ban", "disable"):
                need_sudo()
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET status=?, decided_at=?, decided_by=?,"
                           " ban_reason=? WHERE id=?",
                           (status, time.time(), acc["username"],
                            reason if action == "ban" else "", target["id"]))
                panel.audit(tx, acc["username"], action, str(target["uid"]),
                            panel.audit_note(reason=reason))
            return self._json({"ok": True, "status": status})

        if path == "/account/delete" and method == "POST":
            # 彻底删除账号：不可逆。超管及以上 + sudo，而且只删"已经停用够久、
            # 名下没有还在用的东西"的账号（门槛见 localconfig 的静态设置）
            actor = need("superadmin", "owner")
            need_sudo()
            data = body()
            target = conn.execute("SELECT * FROM accounts WHERE id=?",
                                  (int(data.get("id") or 0),)).fetchone()
            if target is None:
                raise model.ApiError("账号不存在", 404)
            if actor["role"] == "superadmin" and target["role"] != "admin":
                raise model.ApiError("超级管理员只能删除普通管理员", 403)
            days = localconfig.static_limit(LOCAL_CFG, "delete_account_after_days")
            ok, why = panel.delete_blocker(conn, target, days)
            if not ok:
                raise model.ApiError(why, 409)
            # 名单与报名是长期资产：人可以删，别人做的东西不能跟着消失
            lx = lists.connect()
            owned = lx.execute(
                "SELECT (SELECT COUNT(*) FROM rosters WHERE owner_id=?) AS rosters,"
                "       (SELECT COUNT(*) FROM signups WHERE owner_id=?) AS signups",
                (target["id"], target["id"])).fetchone()
            if int(owned["rosters"] or 0) or int(owned["signups"] or 0):
                raise model.ApiError(
                    "名下还有 %d 份名单、%d 条报名：先删掉或处理掉，再删账号"
                    % (int(owned["rosters"] or 0), int(owned["signups"] or 0)), 409)
            refuse_stale()
            signed = conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE account_id=?",
                (target["id"],)).fetchone()["n"]
            panel.drop_avatar(target["id"])
            with panel.tx() as tx:
                counts = panel.purge_account(tx, target["id"])
                # 已归档的活动不拦着删人：负责人留空，取消归档时再指定新的
                left = tx.execute(
                    "UPDATE events SET owner_id=NULL"
                    " WHERE owner_id=? AND archived_at<>0", (target["id"],)).rowcount
                panel.audit(tx, acc["username"], "account_delete", str(target["uid"]),
                            panel.audit_note(uid=target["uid"],
                                             sessions=int(signed or 0),
                                             tokens=counts.get("api_tokens", 0),
                                             events_orphaned=left))
            return self._json({"ok": True, "deleted": True,
                               "uid": target["uid"], "eventsOrphaned": left})

        if path == "/account/role" and method == "POST":
            actor = need("owner")
            need_sudo()
            data = body()
            role = (data.get("role") or "").strip()
            if role not in ("superadmin", "admin"):
                raise model.ApiError("只能设为超级管理员或管理员")
            target = conn.execute("SELECT * FROM accounts WHERE id=?",
                                  (int(data.get("id") or 0),)).fetchone()
            if target is None:
                raise model.ApiError("账号不存在", 404)
            if target["role"] == "owner":
                raise model.ApiError("站长的角色不可更改", 403)
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET role=? WHERE id=?", (role, target["id"]))
                panel.audit(tx, acc["username"], "role_change", str(target["uid"]),
                            panel.audit_note(role=role))
            return self._json({"ok": True, "role": role})

        if path == "/templates" and method == "GET":
            # every preset this account may see: its own, its favourites and the
            # square.  The old name stays so a stale page still gets an answer.
            by_id = _modules_by_id(conn)
            rows = presets.visible(conn, acc["id"], by_id)
            return self._json({
                "ok": True, "version": VERSION,
                "presets": rows, "templates": rows,
                "visibility": [{"value": v, "label": presets.VISIBILITY_LABEL[v]}
                               for v in presets.VISIBILITY]})

        if path == "/preset" and method == "POST":
            data = body()
            by_id = _modules_by_id(conn)
            name = (data.get("name") or "").strip()
            if not name:
                raise model.ApiError("请给这个预设起个名字")
            if len(name) > 40:
                raise model.ApiError("预设名称最长 40 个字")
            module_id = (data.get("module") or "").strip()
            kind = by_id.get(module_id)
            if kind is None:
                raise model.ApiError("没有这个活动模块", 404)
            visibility = (data.get("visibility") or "private").strip()
            if visibility not in presets.VISIBILITY:
                raise model.ApiError("可见性只能是私有、公开或组织公开")
            try:
                params = modules.coerce_params(kind, data.get("params") or {})
            except ValueError as exc:
                raise model.ApiError(str(exc))
            note = (data.get("note") or "").strip()[:200]
            description = (data.get("description") or "").strip()[:600]
            title = (data.get("title") or "").strip()[:60]
            slug = (data.get("id") or "").strip()
            now = time.time()
            with panel.tx() as tx:
                row = None
                if slug:
                    row = tx.execute(
                        "SELECT * FROM presets WHERE slug=?", (slug,)).fetchone()
                    if row is None or row["builtin"] or row["owner_id"] != acc["id"]:
                        raise model.ApiError("这个预设不能改", 403)
                if row is None:
                    slug = presets.new_slug(tx, acc["id"])
                    tx.execute(
                        "INSERT INTO presets (slug, owner_id, name, note, module,"
                        " params, title, visibility, org_id, builtin, created_at,"
                        " updated_at, description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (slug, acc["id"], name, note, module_id,
                         json.dumps(params, ensure_ascii=False), title, visibility,
                         0, 0, now, now, description))
                    panel.audit(tx, acc["username"], "preset_create", slug,
                                panel.audit_note(name=name))
                else:
                    tx.execute(
                        "UPDATE presets SET name=?, note=?, params=?, title=?,"
                        " visibility=?, updated_at=?, description=? WHERE id=?",
                        (name, note, json.dumps(params, ensure_ascii=False), title,
                         visibility, now, description, row["id"]))
                    panel.audit(tx, acc["username"], "preset_update", slug,
                                panel.audit_note(name=name))
            return self._json({"ok": True, "id": slug})

        if path == "/preset/favorite" and method == "POST":
            data = body()
            slug = (data.get("id") or "").strip()
            row = conn.execute("SELECT * FROM presets WHERE slug=?", (slug,)).fetchone()
            if row is None:
                raise model.ApiError("没有这个预设", 404)
            on = bool(data.get("on", True))
            with panel.tx() as tx:
                if on:
                    tx.execute(
                        "INSERT OR IGNORE INTO preset_favorites"
                        " (account_id, preset_id, created_at) VALUES (?,?,?)",
                        (acc["id"], row["id"], time.time()))
                else:
                    tx.execute(
                        "DELETE FROM preset_favorites WHERE account_id=? AND preset_id=?",
                        (acc["id"], row["id"]))
            return self._json({"ok": True, "favorite": on})

        if path == "/preset/delete" and method == "POST":
            data = body()
            slug = (data.get("id") or "").strip()
            row = conn.execute("SELECT * FROM presets WHERE slug=?", (slug,)).fetchone()
            if row is None:
                raise model.ApiError("没有这个预设", 404)
            if row["builtin"] or row["owner_id"] != acc["id"]:
                raise model.ApiError("只能删除自己创建的预设", 403)
            with panel.tx() as tx:
                tx.execute("DELETE FROM presets WHERE id=?", (row["id"],))
                tx.execute("DELETE FROM preset_favorites WHERE preset_id=?",
                           (row["id"],))
                panel.audit(tx, acc["username"], "preset_delete", slug,
                            panel.audit_note(name=row["name"]))
            return self._json({"ok": True})

        if path == "/event/preset" and method == "POST":
            # a preset is a snapshot: take the activity exactly as it is set up
            data = body()
            seq = int(data.get("seq") or 0)
            row = conn.execute(
                "SELECT e.*, m.role AS my_role FROM events e"
                " LEFT JOIN event_members m ON m.seq = e.seq AND m.account_id = ?"
                "   AND m.state='active'"
                " WHERE e.seq=?", (acc["id"], seq)).fetchone()
            if row is None or row["my_role"] not in ("owner", "member"):
                raise model.ApiError("你不是这个活动的成员", 403)
            by_id = _modules_by_id(conn)
            module_id = row["kind"] or panel.DEFAULT_KIND
            kind = by_id.get(module_id)
            if kind is None:
                raise model.ApiError("没有这个活动模块", 404)
            db.set_event(seq)
            cfg = db.all_config(db.connect())
            keys = {f["key"] for f in kind["fields"]}
            params = {k: cfg[k] for k in keys if k in cfg and cfg[k] is not None}
            name = (data.get("name") or "").strip() or \
                (row["title"] or cfg.get("title") or "未命名预设")
            if len(name) > 40:
                name = name[:40]
            visibility = (data.get("visibility") or "private").strip()
            if visibility not in presets.VISIBILITY:
                visibility = "private"
            description = (data.get("description") or "").strip()[:600]
            now = time.time()
            with panel.tx() as tx:
                slug = presets.new_slug(tx, acc["id"])
                tx.execute(
                    "INSERT INTO presets (slug, owner_id, name, note, module, params,"
                    " title, visibility, org_id, builtin, created_at, updated_at,"
                    " description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (slug, acc["id"], name, (data.get("note") or "").strip()[:200],
                     module_id, json.dumps(params, ensure_ascii=False),
                     row["title"] or cfg.get("title") or "", visibility, 0, 0,
                     now, now, description))
                panel.audit(tx, acc["username"], "preset_from_event", slug,
                            panel.audit_note(event_id=seq))
            return self._json({"ok": True, "id": slug, "name": name})

        if path == "/modules" and method == "GET":
            # 模块管理是站长页面：装了什么、停没停是整台机器的事
            need("owner")
            stopped = panel.disabled_modules(conn)
            mods = modules.all_modules(VERSION, disabled=stopped)
            for m in mods:
                # 有几个活动正在用：停用之前先让人看见代价
                m["events"] = panel.events_on_module(conn, m["id"])
            return self._json({"ok": True, "version": VERSION, "modules": mods})

        if path == "/module/toggle" and method == "POST":
            # 停用一个模块是站级动作：正在用它的活动会一并停摆（数据不动，启用即恢复），
            # 新活动与预设也建不了。所以照别的破坏性动作来：先提权，再记审计
            need("owner")
            need_sudo()
            data = body()
            module_id = (data.get("id") or "").strip()
            known = {m["id"] for m in modules.all_modules(VERSION)}
            if module_id not in known:
                raise model.ApiError("没有这个模块", 404)
            on = bool(data.get("enabled", True))
            current = module_id not in panel.disabled_modules(conn)
            affected = panel.events_on_module(conn, module_id)
            if current == on:
                return self._json({"ok": True, "enabled": on, "already": True,
                                   "affected": affected})
            refuse_stale()
            with panel.tx() as tx:
                panel.set_module_enabled(tx, module_id, on, acc["username"])
                panel.audit(tx, acc["username"],
                            "module_enable" if on else "module_disable", module_id,
                            panel.audit_note(affected=affected))
            # 站级的开关变了：让所有活动的流都醒一次，页面据此立刻换成"不可用"
            bus.bump("modules")
            return self._json({"ok": True, "enabled": on, "affected": affected})

        if path == "/module/upload" and method == "POST":
            # 导入模块：上传 zip → 服务端校验、解到暂存目录、把模块申请的能力解析出来。
            # 这一步**不装**，只回答「装上会发生什么」，界面据此摆出高危提示
            need("owner")
            data = body()
            raw = data.get("data") or ""
            if not isinstance(raw, str):
                raise model.ApiError("文件内容无法识别", 400)
            if "," in raw and raw.strip().lower().startswith("data:"):
                raw = raw.split(",", 1)[1]
            try:
                blob = base64.b64decode(raw, validate=True)
            except Exception:
                raise model.ApiError("文件内容无法识别", 400)
            try:
                info = modules.inspect_zip(blob)
            except modules.InstallError as exc:
                raise model.ApiError(str(exc))
            return self._json({"ok": True, "limits": modules.install_limits(), **info})

        if path == "/module/install" and method == "POST":
            # 真的装：站长 + 提权。模块的代码会在这个进程里跑，所以照破坏性动作来
            need("owner")
            need_sudo()
            data = body()
            try:
                module_id = modules.install_staged(data.get("stage"))
            except modules.InstallError as exc:
                raise model.ApiError(str(exc))
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "module_install", module_id,
                            panel.audit_note(version=str(data.get("version") or "")))
            bus.bump("modules")
            return self._json({"ok": True, "id": module_id})

        if path == "/module/remove" and method == "POST":
            # 卸载：只对自己装的模块，而且它手上不能还挂着活动
            need("owner")
            need_sudo()
            data = body()
            module_id = str(data.get("id") or "").strip()
            known = {m["id"]: m for m in modules.all_modules(VERSION)}
            if module_id not in known:
                raise model.ApiError("没有这个模块", 404)
            if known[module_id].get("builtin"):
                raise model.ApiError("平台自带的模块不能卸载", 409)
            affected = panel.events_on_module(conn, module_id)
            if affected:
                raise model.ApiError(
                    "还有 %d 个活动在用它，先归档或转移那些活动" % affected, 409)
            try:
                modules.remove_installed(module_id)
            except modules.InstallError as exc:
                raise model.ApiError(str(exc))
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "module_remove", module_id)
            bus.bump("modules")
            return self._json({"ok": True, "id": module_id})

        if path == "/preset/import" and method == "POST":
            # a preset file from somewhere else.  If it needs a module this
            # installation does not have, the answer says so and the operator
            # decides; importing anyway keeps it listed but unusable.
            # Bringing content into the installation is the station master's
            # call, not every account's.
            need("owner")
            data = body()
            by_id = _modules_by_id(conn)
            raw = data.get("preset")
            if not isinstance(raw, dict):
                raise model.ApiError("预设内容不是合法对象")
            slug = str(raw.get("id") or "").strip()
            if not presets_ok_slug(slug):
                raise model.ApiError("预设 id 需要形如 nwuca.salon")
            dep = raw.get("dependency") or {}
            module_id = str((dep or {}).get("module") or raw.get("module") or "").strip()
            base = str((dep or {}).get("template") or "").strip()
            module = by_id.get(module_id)
            missing = ""
            if module is None:
                missing = "依赖的模块不存在：%s" % (module_id or "(未声明)")
            elif not module.get("ok"):
                missing = "%s 不可用：%s" % (module.get("name") or module_id,
                                          module.get("reason") or "")
            if missing and not data.get("force"):
                return self._json({"ok": False, "error": missing,
                                   "dependencyMissing": True,
                                   "module": module_id}, 409)
            params = raw.get("params") or {}
            if not isinstance(params, dict):
                raise model.ApiError("params 应该是一个对象")
            if module is not None:
                try:
                    params = modules.coerce_params(module, params)
                except ValueError as exc:
                    raise model.ApiError(str(exc))
            name = str(raw.get("name") or slug).strip()[:40]
            note = str(raw.get("note") or raw.get("description") or "").strip()[:200]
            title = str(raw.get("title") or "").strip()[:60]
            visibility = (data.get("visibility") or "private").strip()
            if visibility not in presets.VISIBILITY:
                visibility = "private"
            now = time.time()
            with panel.tx() as tx:
                row = tx.execute("SELECT id, owner_id, builtin FROM presets"
                                 " WHERE slug=?", (slug,)).fetchone()
                if row is not None and (row["builtin"] or row["owner_id"] != acc["id"]):
                    raise model.ApiError("这个预设已经存在，且不属于你", 409)
                if row is None:
                    tx.execute(
                        "INSERT INTO presets (slug, owner_id, name, note, module,"
                        " params, title, visibility, org_id, builtin, created_at,"
                        " updated_at, dep_module, dep_template, source)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (slug, acc["id"], name, note, module_id,
                         json.dumps(params, ensure_ascii=False), title, visibility,
                         0, 0, now, now, module_id, base, "instance"))
                else:
                    tx.execute(
                        "UPDATE presets SET name=?, note=?, module=?, params=?,"
                        " title=?, visibility=?, updated_at=?, dep_module=?,"
                        " dep_template=? WHERE id=?",
                        (name, note, module_id,
                         json.dumps(params, ensure_ascii=False), title, visibility,
                         now, module_id, base, row["id"]))
                panel.audit(tx, acc["username"], "preset_import", slug,
                            panel.audit_note(module=module_id))
            return self._json({"ok": True, "id": slug, "missing": missing})

        if path == "/draft" and method == "GET":
            key = (query.get("key") or [""])[0]
            return self._json({"ok": True, "draft": panel.draft_get(conn, acc["id"], key)})

        if path == "/draft" and method == "POST":
            data = body()
            key = (data.get("key") or "").strip()
            if not key:
                raise model.ApiError("缺少草稿标识")
            payload = data.get("payload")
            with panel.tx() as tx:
                if payload is None:
                    panel.draft_drop(tx, acc["id"], key)
                else:
                    panel.draft_put(tx, acc["id"], key,
                                    json.dumps(payload, ensure_ascii=False))
            return self._json({"ok": True})

        if path == "/events/create" and method == "POST":
            if safemode.enabled():
                # 安全模式不加载模块，模板全都依赖模块：先说清楚，别让人以为模块坏了
                raise safemode.refuse_create()
            data = body()
            by_id = _modules_by_id(conn)
            chosen = presets.get(conn, (data.get("template") or "").strip(), by_id,
                                 acc["id"])
            if chosen is None:
                raise model.ApiError("\u6ca1\u6709\u8fd9\u4e2a\u6d3b\u52a8\u6a21\u677f", 404)
            if not chosen["ok"]:
                raise model.ApiError(chosen["reason"] or "\u5f53\u524d\u7248\u672c\u4e0d\u652f\u6301\u8fd9\u4e2a\u6a21\u677f")
            kind = by_id[chosen["module"]]
            title = (data.get("title") or "").strip() or chosen.get("title") or ""
            if not title:
                raise model.ApiError("\u8bf7\u586b\u5199\u6d3b\u52a8\u540d\u79f0")
            if len(title) > 60:
                raise model.ApiError("\u6d3b\u52a8\u540d\u79f0\u6700\u957f 60 \u4e2a\u5b57")
            note = (data.get("note") or "").strip()[:200]
            merged = dict(chosen.get("params") or {})
            merged.update(data.get("params") or {})
            try:
                params = modules.coerce_params(kind, merged)
            except ValueError as exc:
                raise model.ApiError(str(exc))

            invited = []
            for raw in data.get("members") or []:
                try:
                    who = int(raw)
                except (TypeError, ValueError):
                    continue
                if who != acc["id"]:
                    invited.append(who)

            token = localconfig.new_secret()
            now = time.time()
            with panel.tx() as tx:
                cur = tx.execute(
                    "INSERT INTO events (token, title, note, kind, params, owner_id,"
                    " created_at) VALUES (?,?,?,?,?,?,?)",
                    (token, title, note, kind["id"],
                     json.dumps(params, ensure_ascii=False), acc["id"], now))
                seq = cur.lastrowid
                tx.execute(
                    "INSERT OR REPLACE INTO event_members"
                    " (seq, account_id, role, state, added_at) VALUES (?,?,?,?,?)",
                    (seq, acc["id"], "owner", "active", now))
                for who in invited:
                    exists = tx.execute("SELECT 1 FROM accounts WHERE id=?",
                                        (who,)).fetchone()
                    if exists is None:
                        continue
                    tx.execute(
                        "INSERT OR IGNORE INTO event_members"
                        " (seq, account_id, role, state, added_at) VALUES (?,?,?,?,?)",
                        (seq, who, "member", "pending", now))
                panel.audit(tx, acc["username"], "event_create", str(seq),
                            panel.audit_note(title=title))

            # the activity gets its own database: 平台表 + 这个模块声明的表
            db.set_event(seq)
            db.init_db(kind["id"])
            db.write_meta(seq, title=title, kind=kind["id"], createdAt=now)
            event_conn = db.connect()
            prefix = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            with db.tx(event_conn):
                db.set_config_raw(event_conn, "title", title)
                # the new activity's own address, not the configured one
                if prefix:
                    db.set_config_raw(event_conn, "public_base_url",
                                      prefix + "/" + token)
                db.set_config_raw(event_conn, "kind", kind["id"])
                if note:
                    db.set_config_raw(event_conn, "subtitle", note)
                for key, val in params.items():
                    db.set_config_raw(event_conn, key, val)
                db.audit(event_conn, "event_create", detail="kind=%s" % kind["id"])
            # 模块自己的初始化（幂等）：例如先给一节议程，免得界面空着
            model.ensure_event(event_conn)
            bootstrap_secrets()

            row = conn.execute(
                "SELECT e.*, 'owner' AS my_role, ? AS owner_name FROM events e"
                " WHERE e.seq=?", (acc["name"] or acc["username"], seq)).fetchone()
            base = public_base_url(seq)
            return self._json({"ok": True, "event": panel.event_view(row, base, "owner"),
                               "invited": len(invited)})

        if path == "/event/rotate" and method == "POST":
            # A fresh token invalidates every address that carried the old one.
            # The numeric id does not change, so the panel keeps finding the
            # activity and can always rebuild the links.
            data = body()
            seq = int(data.get("seq") or 0)
            mine = conn.execute(
                "SELECT role FROM event_members WHERE seq=? AND account_id=?",
                (seq, acc["id"])).fetchone()
            if mine is None or mine["role"] != "owner":
                raise model.ApiError("\u53ea\u6709\u8be5\u6d3b\u52a8\u7684\u8d1f\u8d23\u4eba\u80fd\u91cd\u7f6e\u8bbf\u95ee\u5730\u5740", 403)
            # every address handed out stops working, so ask for the password
            need_sudo()
            row = conn.execute("SELECT token, title FROM events WHERE seq=?",
                               (seq,)).fetchone()
            if row is None:
                raise model.ApiError("\u6d3b\u52a8\u4e0d\u5b58\u5728", 404)
            token = localconfig.new_secret()
            with panel.tx() as tx:
                tx.execute("UPDATE events SET token=? WHERE seq=?", (token, seq))
                panel.audit(tx, acc["username"], "event_rotate", str(seq),
                            panel.audit_note(title=row["title"]))
            db.set_event(seq)
            prefix = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            event_conn = db.connect()
            with db.tx(event_conn):
                if prefix:
                    db.set_config_raw(event_conn, "public_base_url",
                                      prefix + "/" + token)
                db.audit(event_conn, "event_rotate", detail="token rotated")
            return self._json({"ok": True, "seq": seq, "token": token,
                               "admin": (prefix + "/" + token + "/admin") if prefix
                                        else "/" + token + "/admin"})

        def event_row(seq):
            row = conn.execute("SELECT * FROM events WHERE seq=?", (seq,)).fetchone()
            if row is None:
                raise model.ApiError("活动不存在", 404)
            return row

        def seat_of(seq, account_id=None):
            return conn.execute(
                "SELECT role, state FROM event_members WHERE seq=? AND account_id=?",
                (seq, account_id or acc["id"])).fetchone()

        def do_archive(seq, title, forced_by=""):
            """打包、留元数据、销短链、写审计。归档之后活动不再保证能打开。"""
            try:
                size = eventfs.archive(seq)
            except eventfs.EventFsError as exc:
                raise model.ApiError(str(exc))
            with panel.tx() as tx:
                tx.execute("UPDATE events SET archived_at=? WHERE seq=?",
                           (time.time(), seq))
                shortlinks.purge_event(tx, seq)
                panel.audit(tx, acc["username"],
                            "event_force_archive" if forced_by else "event_archive",
                            str(seq), panel.audit_note(title=title))
            return size

        if path == "/event/archive" and method == "POST":
            # 归档是负责人对自己活动的软删除：服务端把文件夹打包，外面只留元数据。
            # 只是把自己的东西收起来，而且超管能取消归档，所以不设密码——
            # 界面上的那一句确认就够了。真正不可逆的「彻底删除」才走 sudo
            data = body()
            seq = int(data.get("seq") or 0)
            seat = seat_of(seq)
            if seat is None or seat["role"] != "owner":
                raise model.ApiError("只有该活动的负责人能归档", 403)
            row = event_row(seq)
            if row["archived_at"]:
                return self._json({"ok": True, "seq": seq, "archived": True,
                                   "already": True})
            refuse_stale()
            size = do_archive(seq, row["title"])
            return self._json({"ok": True, "seq": seq, "archived": True,
                               "title": row["title"], "bytes": size})

        if path == "/event/transfer" and method == "POST":
            # 转移负责人：负责人自己能做；负责人登不进来时由超管提权代做，
            # 否则这一场就卡死在没人能管的状态
            data = body()
            seq = int(data.get("seq") or 0)
            uid = int(data.get("uid") or 0)
            who = str(data.get("username") or "").strip()
            seat = seat_of(seq)
            mine = seat is not None and seat["role"] == "owner" and seat["state"] == "active"
            if not mine:
                need("superadmin", "owner")
                need_sudo()
            row = event_row(seq)
            # 选人弹窗给的是编号；登录名这条路留着，脚本里还在用
            if uid:
                target = conn.execute(
                    "SELECT id, uid, name, status FROM accounts WHERE uid=?",
                    (uid,)).fetchone()
            else:
                target = conn.execute(
                    "SELECT id, uid, name, status FROM accounts"
                    " WHERE lower(username)=lower(?)", (who,)).fetchone()
            if target is None:
                raise model.ApiError("没有这个账号", 404)
            if target["status"] != "active":
                raise model.ApiError("这个账号不是正常状态，不能当负责人", 409)
            label = panel.public_name(target["name"], target["uid"])
            if target["id"] == row["owner_id"]:
                return self._json({"ok": True, "seq": seq, "owner": label, "already": True})
            now = time.time()
            with panel.tx() as tx:
                tx.execute("UPDATE events SET owner_id=? WHERE seq=?", (target["id"], seq))
                tx.execute(
                    "INSERT INTO event_members (seq, account_id, role, state, added_at)"
                    " VALUES (?,?,?,?,?) ON CONFLICT(seq, account_id)"
                    " DO UPDATE SET role='owner', state='active'",
                    (seq, target["id"], "owner", "active", now))
                tx.execute(
                    "UPDATE event_members SET role='member'"
                    " WHERE seq=? AND account_id=? AND account_id<>?",
                    (seq, row["owner_id"] or 0, target["id"]))
                panel.audit(tx, acc["username"], "event_transfer", str(seq),
                            panel.audit_note(title=row["title"], to=label))
            return self._json({"ok": True, "seq": seq, "owner": label})

        if path == "/event/reclaim" and method == "POST":
            # 站内资源回收：超管显式把这场活动占着的东西收回来（目前是短链）
            data = body()
            seq = int(data.get("seq") or 0)
            need("superadmin", "owner")
            need_sudo()
            event_row(seq)
            before = shortlinks.count_for(conn, seq)
            with panel.tx() as tx:
                shortlinks.purge_event(tx, seq)
                panel.audit(tx, acc["username"], "event_reclaim", str(seq),
                            panel.audit_note(shortlinks=before))
            return self._json({"ok": True, "seq": seq, "shortlinks": before})

        if path == "/event/force_archive" and method == "POST":
            # 强制归档别人的活动：超管提权操作
            data = body()
            seq = int(data.get("seq") or 0)
            need("superadmin", "owner")
            row = event_row(seq)
            if row["archived_at"]:
                return self._json({"ok": True, "seq": seq, "archived": True,
                                   "already": True})
            need_sudo()
            size = do_archive(seq, row["title"], forced_by=acc["username"])
            return self._json({"ok": True, "seq": seq, "archived": True,
                               "title": row["title"], "bytes": size})

        if path == "/event/unarchive" and method == "POST":
            # 取消归档：超管提权，把归档包解开，负责人就能下载后再归一次。
            # 负责人账号可能已经被彻底删了（归档的活动不拦着删人），
            # 这种时候要同时指定新的负责人：转移与恢复是同一步
            data = body()
            seq = int(data.get("seq") or 0)
            need("superadmin", "owner")
            need_sudo()
            row = event_row(seq)
            owner_uid = int(data.get("owner") or 0)
            current = conn.execute("SELECT uid, name, status FROM accounts WHERE id=?",
                                   (row["owner_id"] or 0,)).fetchone()
            new_owner = None
            if current is None:
                if not owner_uid:
                    raise model.ApiError(
                        "这场活动的负责人账号已经不存在，取消归档要同时指定新的负责人", 409)
                new_owner = conn.execute(
                    "SELECT id, uid, name, status FROM accounts WHERE uid=?",
                    (owner_uid,)).fetchone()
                if new_owner is None or new_owner["status"] != "active":
                    raise model.ApiError("新的负责人需要是一个正常状态的账号", 404)
            try:
                restored = eventfs.restore(seq)
            except eventfs.EventFsError as exc:
                raise model.ApiError(str(exc))
            with panel.tx() as tx:
                tx.execute("UPDATE events SET archived_at=0 WHERE seq=?", (seq,))
                if new_owner is not None:
                    tx.execute("UPDATE events SET owner_id=? WHERE seq=?",
                               (new_owner["id"], seq))
                    tx.execute(
                        "INSERT INTO event_members (seq, account_id, role, state, added_at)"
                        " VALUES (?,?,?,?,?) ON CONFLICT(seq, account_id)"
                        " DO UPDATE SET role='owner', state='active'",
                        (seq, new_owner["id"], "owner", "active", time.time()))
                    tx.execute(
                        "UPDATE event_members SET role='member'"
                        " WHERE seq=? AND account_id<>? AND role='owner'",
                        (seq, new_owner["id"]))
                panel.audit(tx, acc["username"], "event_unarchive", str(seq),
                            panel.audit_note(title=row["title"],
                                             new_owner=panel.public_name(
                                                 new_owner["name"] if new_owner else "",
                                                 new_owner["uid"] if new_owner else 0)))
            if restored:
                db.set_event(seq)
                db.init_db()
            return self._json({"ok": True, "seq": seq, "archived": False,
                               "restored": restored,
                               "owner": panel.public_name(
                                   new_owner["name"], new_owner["uid"])
                                        if new_owner is not None else ""})

        if path == "/event/delete" and method == "POST":
            # 彻底删除已归档的活动：不可逆，只有超管能做
            data = body()
            seq = int(data.get("seq") or 0)
            need("superadmin", "owner")
            row = event_row(seq)
            if not row["archived_at"]:
                raise model.ApiError("只能删除已归档的活动；正在用的先归档", 409)
            need_sudo()
            removed = eventfs.delete(seq)
            with panel.tx() as tx:
                tx.execute("DELETE FROM event_members WHERE seq=?", (seq,))
                tx.execute("DELETE FROM events WHERE seq=?", (seq,))
                panel.audit(tx, acc["username"], "event_delete", str(seq),
                            panel.audit_note(title=row["title"]))
            return self._json({"ok": True, "seq": seq, "removed": removed})

        if path == "/event/download" and method == "GET":
            # 活动正常开启时负责人与参与者都能下载；归档之后只有超管提权能拿
            seq = int((query.get("seq") or ["0"])[0] or 0)
            row = event_row(seq)
            seat = seat_of(seq)
            mine = seat is not None and seat["state"] == "active"
            if row["archived_at"]:
                need("superadmin", "owner")
                need_sudo()
                blob = eventfs.package(seq, full=True)
            else:
                if not mine:
                    need("superadmin", "owner")
                blob = eventfs.package(seq)
            title = db.read_meta(seq).get("title") or row["title"] or ""
            ascii_name = "".join(ch for ch in title if ch.isascii() and
                                 (ch.isalnum() or ch in "-_"))
            name = "event-%d-%s.zip" % (seq, ascii_name or "activity")
            # HTTP 头只认 latin-1：ASCII 名给老客户端，UTF-8 名按 RFC 5987 附在后面
            utf8_name = quote("活动%d-%s.zip" % (seq, title or "数据"))
            disposition = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
                name, utf8_name)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "event_download", str(seq),
                            panel.audit_note(file=name))
            return self._send(200, blob, "application/zip",
                              headers=[("Content-Disposition", disposition)])

        if path == "/event/title" and method == "POST":
            # The name belongs to the event's top-level data, so only that
            # event's owner may set it - a platform owner or superadmin who is
            # not on the event does not count.
            data = body()
            seq = int(data.get("seq") or 0)
            title = (data.get("title") or "").strip()
            if len(title) > 60:
                raise model.ApiError("\u6d3b\u52a8\u540d\u79f0\u6700\u957f 60 \u4e2a\u5b57")
            mine = conn.execute(
                "SELECT role FROM event_members WHERE seq=? AND account_id=?",
                (seq, acc["id"])).fetchone()
            if mine is None or mine["role"] != "owner":
                raise model.ApiError("\u53ea\u6709\u8be5\u6d3b\u52a8\u7684\u8d1f\u8d23\u4eba\u80fd\u6539\u540d", 403)
            row = conn.execute("SELECT token FROM events WHERE seq=?", (seq,)).fetchone()
            if row is None:
                raise model.ApiError("\u6d3b\u52a8\u4e0d\u5b58\u5728", 404)
            with panel.tx() as tx:
                tx.execute("UPDATE events SET title=? WHERE seq=?", (title, seq))
                panel.audit(tx, acc["username"], "event_title", str(seq),
                            panel.audit_note(title=title))
            # the event pages read their heading from the event database
            db.set_event(seq)
            event_conn = db.connect()
            with db.tx(event_conn):
                db.set_config_raw(event_conn, "title", title)
                db.audit(event_conn, "event_title", detail=title)
            return self._json({"ok": True, "seq": seq, "title": title})

        if path == "/profile" and method == "POST":
            data = body()
            name = (data.get("name") or "").strip()
            username = (data.get("username") or "").strip()
            if not name:
                raise model.ApiError("请填写显示名")
            if len(name) > 24:
                raise model.ApiError("显示名最长 24 个字")
            if username and username != acc["username"]:
                if acc["role"] == "owner":
                    raise model.ApiError(
                        "站长的登录名由 config.json 的 "
                        "owner_username 决定", 403)
                problem = panel.username_problem(username)
                if problem:
                    raise model.ApiError(problem,
                                         400 if problem != panel.USERNAME_TAKEN else 409)
                taken = panel.username_taken(conn, username, acc["id"])
                if taken:
                    raise model.ApiError(panel.USERNAME_TAKEN, 409)
            else:
                username = acc["username"]
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET name=?, username=? WHERE id=?",
                           (name, username, acc["id"]))
                if username != acc["username"]:
                    panel.audit(tx, acc["username"], "rename", str(acc["uid"]),
                                panel.audit_note(**{"from": acc["username"],
                                                    "to": username}))
                if name != acc["name"]:
                    panel.audit(tx, acc["username"], "profile_name",
                                str(acc["uid"]), panel.audit_note(name=name))
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (acc["id"],)).fetchone()
            return self._json({"ok": True, "me": panel.view(row)})

        if path == "/avatar" and method == "POST":
            data = body()
            if data.get("clear"):
                with panel.tx() as tx:
                    tx.execute("UPDATE accounts SET avatar='', avatar_at=? WHERE id=?",
                               (time.time(), acc["id"]))
                    panel.audit(tx, acc["username"], "avatar_clear")
                panel.drop_avatar(acc["id"])
                row = conn.execute("SELECT * FROM accounts WHERE id=?",
                                   (acc["id"],)).fetchone()
                return self._json({"ok": True, "me": panel.view(row)})

            link = data.get("url")
            if link is not None:
                cleaned = panel.clean_avatar_url(link)
                if cleaned is None:
                    raise model.ApiError(
                        "\u5934\u50cf\u94fe\u63a5\u9700\u8981\u662f http \u6216 https \u5f00\u5934", 400)
                stamp = time.time()
                with panel.tx() as tx:
                    tx.execute("UPDATE accounts SET avatar=?, avatar_at=? WHERE id=?",
                               (cleaned, stamp, acc["id"]))
                    panel.audit(tx, acc["username"], "avatar_url", None,
                                panel.audit_note(url=cleaned[:120]))
                panel.drop_avatar(acc["id"])
                row = conn.execute("SELECT * FROM accounts WHERE id=?",
                                   (acc["id"],)).fetchone()
                return self._json({"ok": True, "me": panel.view(row)})

            raw = data.get("data") or ""
            if "," in raw and raw.strip().lower().startswith("data:"):
                raw = raw.split(",", 1)[1]
            try:
                blob = base64.b64decode(raw, validate=True)
            except Exception:
                raise model.ApiError("图片数据无法识别", 400)
            if len(blob) > panel.AVATAR_MAX:
                raise model.ApiError("图片超过 512 KB", 413)
            try:
                stored = panel.save_avatar(acc["id"], blob)
            except ValueError as exc:
                if "large" in str(exc):
                    raise model.ApiError("图片超过 512 KB", 413)
                raise model.ApiError("只支持 PNG、JPEG 或 WebP", 400)
            stamp = time.time()
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET avatar=?, avatar_at=? WHERE id=?",
                           (stored, stamp, acc["id"]))
                panel.audit(tx, acc["username"], "avatar_set", None,
                            panel.audit_note(file=stored))
            row = conn.execute("SELECT * FROM accounts WHERE id=?",
                               (acc["id"],)).fetchone()
            return self._json({"ok": True, "me": panel.view(row)})

        if path.startswith("/avatar/") and method == "GET":
            try:
                who = int(path[len("/avatar/"):])
            except ValueError:
                raise model.ApiError("bad id", 400)
            row = conn.execute("SELECT avatar FROM accounts WHERE id=?", (who,)).fetchone()
            name = row["avatar"] if row else ""
            if panel.is_avatar_url(name):
                # a remote avatar is loaded straight from its host, not through us
                return self._err("not found", 404)
            full = panel.avatar_path(name)
            if not full or not os.path.exists(full):
                return self._err("not found", 404)
            with open(full, "rb") as fh:
                blob = fh.read()
            kind = panel.sniff_image(blob)
            ctype = kind[1] if kind else "application/octet-stream"
            return self._send(200, blob, ctype,
                              [("Cache-Control", "private, max-age=86400")])

        if path == "/screen_packs" and method == "GET":
            # 只读的资源清单：这些是活动可以引用的外部资源，没有"平台默认值"
            return self._json({"ok": True, "packs": _screen_packs(),
                               "canManage": _can_manage_packs(acc),
                               "limits": packs.limits()})

        if path == "/screen_pack/upload" and method == "POST":
            # 外部资源是全平台共享的代码，导入与删除只留给站长与超级管理员
            need(*SCREEN_PACK_ROLES)
            data = body()
            raw = data.get("data") or ""
            if not isinstance(raw, str):
                raise model.ApiError("文件内容无法识别", 400)
            if "," in raw and raw.strip().lower().startswith("data:"):
                raw = raw.split(",", 1)[1]
            try:
                blob = base64.b64decode(raw, validate=True)
            except Exception:
                raise model.ApiError("文件内容无法识别", 400)
            if len(blob) > packs.ZIP_MAX:
                raise model.ApiError("压缩包超过 %s" % packs.size_text(packs.ZIP_MAX), 413)
            try:
                got = packs.install(blob, data.get("filename") or "")
            except packs.PackError as exc:
                raise model.ApiError(str(exc), 400)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "screen_pack_add", got["id"],
                            panel.audit_note(files=got["files"],
                                             size=packs.size_text(got["size"])))
            item = None
            for row in _screen_packs():
                if row["id"] == got["id"]:
                    item = row
            return self._json({"ok": True, "pack": item,
                               "skipped": got["skipped"]})

        if path == "/screen_pack/delete" and method == "POST":
            need(*SCREEN_PACK_ROLES)
            data = body()
            pack_id = str(data.get("id") or "").strip()
            try:
                packs.remove(pack_id)
            except packs.PackError as exc:
                raise model.ApiError(str(exc), 400)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "screen_pack_del", pack_id)
            return self._json({"ok": True, "id": pack_id, "packs": _screen_packs()})

        if path == "/notices" and method == "GET":
            items = panel.notices_for(conn, acc)
            return self._json({"ok": True, "notices": items,
                               "unread": int(panel.unread_count(conn, acc) or 0),
                               "revs": bus.snapshot()})

        if path == "/service" and method == "GET":
            # 服务有效性：只发警告、不发指令，任何登录账号看到的都一样
            return self._json({"ok": True, "version": VERSION,
                               "at": time.time(), "items": service_items()})

        if path == "/service/safe_mode" and method == "POST":
            # 进/出安全模式是主站破坏性动作：站长 + 重输一次密码，并记审计
            need("owner")
            need_sudo()
            data = body()
            if "on" not in data:
                raise model.ApiError("请说明是进入还是退出安全模式")
            want = bool(data.get("on"))
            if want == safemode.enabled():
                return self._json({"ok": True, "safeMode": want, "already": True,
                                   "supervised": supervised()})
            localconfig.set_static("safe_mode", want)
            with panel.tx() as tx:
                # 与 sudo 同一套写法：进入/退出各一条，说明里写清为什么
                panel.audit(tx, acc["username"],
                            "enter_safe_mode" if want else "exit_safe_mode", None,
                            panel.audit_note(reason="manual"))
            can_restart = supervised()
            if can_restart:
                restart_after_response()
            return self._json({"ok": True, "safeMode": want, "restarting": can_restart,
                               "supervised": can_restart})

        if path == "/service/restart" and method == "POST":
            need("owner")
            need_sudo()
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "service_restart", None)
            can_restart = supervised()
            if can_restart:
                restart_after_response()
            return self._json({"ok": True, "restarting": can_restart,
                               "supervised": can_restart})

        if path == "/notices/read" and method == "POST":
            data = body()
            ids = data.get("ids") or []
            if not isinstance(ids, list):
                ids = []
            with panel.tx() as tx:
                panel.mark_notices(tx, acc, ids)
            return self._json({"ok": True,
                               "unread": int(panel.unread_count(conn, acc) or 0)})

        if path == "/host" and method == "GET":
            need("owner")
            # 设备占用只有 Linux 读得到；用户数是面板自己的账，哪里都算得出
            return self._json({"ok": True, "host": host_stats_cached(),
                               "users": panel.presence(conn),
                               "safeMode": safemode.enabled(),
                               "supervised": supervised()})

        # ---- 运维助手：模型配置与一次对话 ----
        if path == "/assistant" and method == "GET":
            return self._json({"ok": True, "config": assistant.config_of(acc),
                               "tools": sorted(policy.visible(acc)),
                               "writes": sorted(policy.WRITE_ACTIONS),
                               # 动作的正式名称：过程标签与批准条都用这一份，别各写一套
                               "labels": dict(policy.ACTION_LABELS),
                               # 还挂着等人点头的对话：侧栏与会话卡片上的小红点靠它
                               "pending": sorted(assistant.pending_sessions(acc))})

        if path == "/assistant/config" and method == "POST":
            data = body()
            base = (data.get("base") or "").strip()
            if base and not re.match(r"^https?://", base):
                raise model.ApiError("接口地址要以 http:// 或 https:// 开头")
            with panel.tx() as tx:
                resume = data.get("resume")
                ctx = data.get("context")
                assistant.save_config(tx, acc["id"], data.get("base"),
                                      data.get("model"), data.get("key") or "",
                                      clear=bool(data.get("clear")),
                                      resume=None if resume is None else bool(resume),
                                      ctx=None if ctx is None else int(ctx or 0))
                panel.audit(tx, acc["username"], "assistant_config", None,
                            panel.audit_note(base=base, model=data.get("model")))
            row = conn.execute("SELECT * FROM accounts WHERE id=?",
                               (acc["id"],)).fetchone()
            return self._json({"ok": True, "config": assistant.config_of(row)})

        if path == "/assistant/chat" and method == "POST":
            data = body()
            cfg = assistant.config_of(acc)
            if not cfg["ready"]:
                raise model.ApiError("还没有配置模型：先在「运维助手」页填接口地址、模型名与密钥")
            messages = data.get("messages")
            if not isinstance(messages, list):
                raise model.ApiError("对话内容格式不对")
            full = dict(cfg, key=acc["model_key"] or "")
            prefix = (raw_path or "").split("/api/panel")[0].rstrip("/")
            client = assistant.PanelClient(self.server.server_address[1], prefix,
                                           self.headers.get("Cookie") or "")
            try:
                session = int(data.get("session") or 0)
            except (TypeError, ValueError):
                session = 0
            try:
                result = assistant.run(conn, acc, client, full, messages,
                                       version=VERSION, session=session)
            except Exception as exc:
                raise model.ApiError("调用模型失败：%s" % str(exc)[:120], 502)
            # 这里**不写审计**：问一句不是一次操作，攒在审计里会把它淹掉。
            # 助手真正改了东西时，那次写入自己会记一条，detail 里带「via: assistant」。
            # 要写东西的那一步会带 pending 回来：人点了批准才真的执行。
            return self._json({"ok": True, "reply": result.get("reply", ""),
                               "steps": result.get("steps") or [],
                               "pending": result.get("pending")})

        if path == "/assistant/stream" and method == "POST":
            # 和 /assistant/chat 做同一件事，只是把最后那段回答逐字推回去（SSE）。
            # 收尾用关连接表示：写完就 close，浏览器读到 EOF。
            data = body()
            cfg = assistant.config_of(acc)
            if not cfg["ready"]:
                raise model.ApiError("还没有配置模型：先在「运维助手」页填接口地址、模型名与密钥")
            full = dict(cfg, key=acc["model_key"] or "")
            prefix = (raw_path or "").split("/api/panel")[0].rstrip("/")
            client = assistant.PanelClient(self.server.server_address[1], prefix,
                                           self.headers.get("Cookie") or "")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(event):
                try:
                    self._sse(event)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass                      # 人已经把页面关了，别再往下算

            try:
                if data.get("token"):
                    # 批准/拒绝之后接着跑这一轮
                    result = assistant.approve(acc, data.get("token"),
                                               bool(data.get("allow")), emit=emit)
                else:
                    messages = data.get("messages")
                    if not isinstance(messages, list):
                        raise model.ApiError("对话内容格式不对")
                    try:
                        session = int(data.get("session") or 0)
                    except (TypeError, ValueError):
                        session = 0
                    result = assistant.run(conn, acc, client, full, messages,
                                           version=VERSION, session=session, emit=emit)
                emit(dict({"type": "done", "reply": result.get("reply", ""),
                           "steps": result.get("steps") or [],
                           "pending": result.get("pending")}, ok=True))
            except model.ApiError as exc:
                emit({"type": "error", "error": exc.message})
            except Exception as exc:
                emit({"type": "error", "error": "调用模型失败：%s" % str(exc)[:120]})
            return

        if path == "/assistant/approve" and method == "POST":
            # 助手要改数据的动作：批准才执行。批准本身不写审计——被批准的那次写入
            # 自己会记一条，说明里带「via: assistant」
            data = body()
            try:
                result = assistant.approve(acc, data.get("token"), bool(data.get("allow")))
            except ValueError as exc:
                raise model.ApiError(str(exc), 409)
            except Exception as exc:
                raise model.ApiError("调用模型失败：%s" % str(exc)[:120], 502)
            return self._json({"ok": True, "reply": result.get("reply", ""),
                               "steps": result.get("steps") or [],
                               "pending": result.get("pending")})

        if path == "/assistant/title" and method == "POST":
            # 给会话起个名字：纯装饰，起不出来前端照旧用第一句话，所以不写审计
            data = body()
            full = dict(assistant.config_of(acc), key=acc["model_key"] or "")
            return self._json({"ok": True,
                               "title": assistant.suggest_title(full,
                                                                data.get("text") or "")})

        if path == "/assistant/models" and method == "POST":
            data = body()
            cfg = assistant.config_of(acc)
            base = (data.get("base") or cfg["base"] or "").strip()
            key = (data.get("key") or "").strip() or (acc["model_key"] or "")
            if not key:
                raise model.ApiError("先填密钥，再拉模型列表")
            items, err = assistant.fetch_models(base, key)
            if err:
                raise model.ApiError("拿不到模型列表：%s" % err, 502)
            return self._json({"ok": True, "models": items})

        # ---- 对话存档 ----
        if path == "/assistant/sessions" and method == "GET":
            pending = assistant.pending_sessions(acc)
            rows = panel.chat_sessions(conn, acc["id"])
            for row in rows:
                row["pending"] = row["id"] in pending
            return self._json({"ok": True, "sessions": rows,
                               "quota": panel.chat_quota(acc),
                               "used": panel.chat_used(conn, acc["id"])})

        if path == "/assistant/pending" and method == "GET":
            # 刷新页面之后，那条待批准的提示要到这儿捡回来：令牌在服务端还等着
            sid = _int_arg(query, "session", 0, 0, 100000000)
            view = assistant.pending_sessions(acc).get(sid)
            return self._json({"ok": True, "pending": view or None})

        if path == "/assistant/progress" and method == "GET":
            # 这一轮走到哪儿了：等的时候界面每隔一会儿问一次，把"正在思考"换成
            # "正在执行「读取名单成员」""正在生成回答"
            sid = _int_arg(query, "session", 0, 0, 100000000)
            return self._json(dict({"ok": True}, **assistant.progress_of(acc, sid)))

        if path == "/assistant/context" and method == "GET":
            # 这条对话大概占了多少上下文：会话条右边那条环与它的弹窗用
            sid = _int_arg(query, "session", 0, 0, 100000000)
            row = panel.chat_session(conn, acc["id"], sid) if sid else None
            msgs = (row or {}).get("messages") or []
            stats = assistant.context_stats(acc, assistant.config_of(acc), msgs, VERSION)
            return self._json(dict({"ok": True}, **stats))

        if path == "/assistant/session" and method == "GET":
            sid = _int_arg(query, "id", 0, 0, 100000000)
            row = panel.chat_session(conn, acc["id"], sid)
            if row is None:
                raise model.ApiError("找不到这段会话", 404)
            return self._json({"ok": True, "session": row})

        if path == "/assistant/session/save" and method == "POST":
            data = body()
            messages = data.get("messages")
            if not isinstance(messages, list):
                raise model.ApiError("对话内容格式不对")
            if len(messages) > 400:
                messages = messages[-400:]
            try:
                with panel.tx() as tx:
                    sid = panel.chat_save(tx, acc, int(data.get("id") or 0),
                                          data.get("title") or "", messages)
            except ValueError as exc:
                raise model.ApiError(str(exc), 413)
            except LookupError as exc:
                raise model.ApiError(str(exc), 404)
            # 刚存下来的这段对话如果是"还没有 id 的那一段"，把挂着的动作认领过来：
            # 否则待批准的提示在会话卡片上点不到（见 assistant.adopt_session）
            assistant.adopt_session(acc, sid, messages)
            return self._json({"ok": True, "id": sid,
                               "used": panel.chat_used(conn, acc["id"]),
                               "quota": panel.chat_quota(acc)})

        if path == "/assistant/session/rename" and method == "POST":
            data = body()
            title = (data.get("title") or "").strip()[:panel.CHAT_TITLE_MAX]
            if not title:
                raise model.ApiError("请填一个名称")
            row = conn.execute(
                "SELECT * FROM assistant_sessions WHERE id=? AND account_id=? AND deleted_at=0",
                (int(data.get("id") or 0), acc["id"])).fetchone()
            if row is None:
                raise model.ApiError("找不到这段会话", 404)
            with panel.tx() as tx:
                tx.execute("UPDATE assistant_sessions SET title=?, updated_at=? WHERE id=?",
                           (title, time.time(), row["id"]))
            return self._json({"ok": True, "id": row["id"], "title": title})

        if path == "/assistant/session/delete" and method == "POST":
            data = body()
            try:
                with panel.tx() as tx:
                    panel.chat_delete(tx, acc["id"], data.get("id"))
            except LookupError as exc:
                raise model.ApiError(str(exc), 404)
            # 这段对话没了，挂在上面的那个待批准动作也没有地方点了
            assistant.drop_session(acc, data.get("id"))
            return self._json({"ok": True,
                               "used": panel.chat_used(conn, acc["id"]),
                               "quota": panel.chat_quota(acc)})

        if path == "/assistant/session/export" and method == "GET":
            sid = _int_arg(query, "id", 0, 0, 100000000)
            row = panel.chat_session(conn, acc["id"], sid)
            if row is None:
                raise model.ApiError("找不到这段会话", 404)
            lines = ["# %s" % row["title"], ""]
            for m in row["messages"]:
                who = "我" if m.get("role") == "user" else "助手"
                tools = [s.get("tool") for s in (m.get("steps") or [])]
                lines.append("**%s**%s" % (who, ("（工具：%s）" % "、".join(tools))
                                           if tools else ""))
                lines.append("")
                lines.append(str(m.get("content") or ""))
                lines.append("")
            text = "\n".join(lines).encode("utf-8")
            return self._send(200, text, "text/markdown; charset=utf-8",
                              [_attachment_header("%s.md" % (row["title"] or "会话"))])

        if path == "/account/quota" and method == "POST":
            # 存档配额是平台资源：谁把谁的配额调成了多少，要留在审计里
            need("superadmin", "owner")
            data = body()
            target = conn.execute("SELECT * FROM accounts WHERE id=?",
                                  (int(data.get("id") or 0),)).fetchone()
            if target is None:
                raise model.ApiError("账号不存在", 404)
            try:
                quota = int(data.get("quota") or 0)
            except (TypeError, ValueError):
                raise model.ApiError("配额需要一个字节数")
            if quota < 0 or quota > panel.CHAT_QUOTA_MAX:
                raise model.ApiError("配额需在 0 到 %d 之间" % panel.CHAT_QUOTA_MAX)
            before = int(target["chat_quota"] or 0)
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET chat_quota=? WHERE id=?",
                           (quota, target["id"]))
                panel.audit(tx, acc["username"], "account_quota", str(target["uid"]),
                            panel.audit_note(bytes="%d -> %d" % (before, quota)))
            row = conn.execute("SELECT * FROM accounts WHERE id=?",
                               (target["id"],)).fetchone()
            return self._json({"ok": True, "account": panel.view(row)})

        # ---- API 令牌：一人一张或多张，明文只在创建时出现一次 ----
        if path == "/tokens" and method == "GET":
            return self._json({"ok": True, "tokens": panel.tokens_of(conn, acc["id"]),
                               "scopes": list(panel.TOKEN_SCOPES),
                               "scopeLabels": panel.TOKEN_SCOPE_LABEL,
                               "prefix": panel.TOKEN_PREFIX})

        if path == "/token/create" and method == "POST":
            data = body()
            name = (data.get("name") or "").strip()
            if not name:
                raise model.ApiError("请给这张令牌起个名字，方便日后认出它")
            scopes = panel.normalize_scopes(data.get("scopes"))
            if not scopes:
                raise model.ApiError("无法创建空权限令牌")
            try:
                days = int(data.get("days") or 0)
            except (TypeError, ValueError):
                raise model.ApiError("有效期格式不对")
            if days < 0 or days > 3650:
                raise model.ApiError("有效期最长 10 年")
            # 首选：令牌在浏览器里生成，只把 sha256 与前四位/后五位送过来——
            # 明文既没经过网络，也没在服务端的内存里落过脚。
            digest = (data.get("hash") or "").strip().lower()
            plain = ""
            with panel.tx() as tx:
                if digest:
                    head = (data.get("head") or "").strip()
                    tail = (data.get("tail") or "").strip()
                    if not re.fullmatch(r"[0-9a-f]{64}", digest):
                        raise model.ApiError("令牌摘要格式不对")
                    if not (re.fullmatch(r"[A-Za-z0-9_-]{4}", head)
                            and re.fullmatch(r"[A-Za-z0-9_-]{5}", tail)):
                        raise model.ApiError("令牌首尾几位不完整")
                    tid = panel.create_token_hash(tx, acc["id"], name, scopes, days,
                                                  digest, head, tail)
                else:
                    # 没有 WebCrypto（比如用 http 从局域网访问）时退回服务端生成
                    plain, tid = panel.create_token(tx, acc["id"], name, scopes, days)
                panel.audit(tx, acc["username"], "token_create", str(tid),
                            panel.audit_note(name=name, scopes=",".join(scopes)))
            item = [t for t in panel.tokens_of(conn, acc["id"]) if t["id"] == tid]
            return self._json({"ok": True, "token": plain,
                               "item": item[0] if item else None})

        if path == "/token/revoke" and method == "POST":
            data = body()
            try:
                with panel.tx() as tx:
                    row = panel.revoke_token(tx, acc["id"], data.get("id"))
                    panel.audit(tx, acc["username"], "token_revoke", str(row["id"]),
                                panel.audit_note(name=row["name"]))
            except LookupError as exc:
                raise model.ApiError(str(exc), 404)
            return self._json({"ok": True, "tokens": panel.tokens_of(conn, acc["id"])})


        # ---- 名单：与活动并列的一层，存在自己的库里 ----
        def roster_owner_names(ids):
            ids = [int(i) for i in ids if i]
            if not ids:
                return {}
            rows = conn.execute(
                "SELECT id, name, username FROM accounts WHERE id IN (%s)"
                % ",".join("?" * len(ids)), ids)
            return {r["id"]: (r["name"] or r["username"]) for r in rows}

        def open_roster(rid, write=False, allow_archived=False):
            """The roster row, after checking this account may look at it."""
            row = lists.roster(lists.connect(), rid)
            if row is None:
                raise model.ApiError("名单不存在", 404)
            if lists.is_archived(row) and not allow_archived:
                raise model.ApiError("这份名单已经归档，请联系超级管理员", 409)
            if write:
                if not lists.writable(lists.connect(), acc, "roster", row):
                    raise model.ApiError("没有该名单的写入权限", 403)
            elif not _roster_viewable(acc, row):
                raise model.ApiError("无权查看该名单", 403)
            return row

        def need_roster_owner(row):
            """Destructive things (rename, delete, who may write) stay with the owner."""
            if row is None or row["owner_id"] != acc["id"]:
                raise model.ApiError("仅拥有者可执行该操作", 403)
            return row

        if path == "/roster/ask" and method == "POST":
            # 看得见就能申请写入；私有名单没有这一条路，只能等拥有者邀请
            data = body()
            rid = int(data.get("id") or 0)
            long_row = open_roster(rid)
            with lists.tx() as lx:
                level = lists.apply_for_write(lx, "roster", long_row, acc["id"])
            with panel.tx() as tx:
                panel.audit(tx, acc["username"],
                            "roster_ask" if level == "pending" else "roster_grant",
                            str(rid), panel.audit_note(name=long_row["name"]))
                if level == "pending":
                    panel.notice(
                        tx, "important", "有人申请写入名单「%s」" % long_row["name"],
                        "%s 申请修改这份名单，需要你确认。" % _account_label(conn, acc["id"]),
                        account_id=long_row["owner_id"], actor=acc["username"],
                        obj="roster:%d" % rid)
            return self._json({"ok": True, "level": level,
                               "levelLabel": lists.LEVEL_LABEL[level]})

        if path == "/roster/grant" and method == "POST":
            # 拥有者直接把人加进写入名单：私有名单只有这一条路。
            # 一次可以给多个人（选人弹窗是多选）；uids 优先，username 留给脚本。
            data = body()
            rid = int(data.get("id") or 0)
            row = need_roster_owner(open_roster(rid, write=True))
            uids = data.get("uids")
            who = (data.get("username") or "").strip()
            targets = []
            if isinstance(uids, list) and uids:
                marks = ",".join("?" * len(uids))
                targets = conn.execute(
                    "SELECT id, uid, name, username FROM accounts"
                    " WHERE status='active' AND uid IN (" + marks + ")",   # sql-ok 只拼占位符个数，值都走 ?
                    [int(u) for u in uids]).fetchall()
            elif who:
                one = conn.execute(
                    "SELECT id, uid, name, username FROM accounts"
                    " WHERE lower(username)=lower(?)", (who,)).fetchone()
                if one is not None:
                    targets = [one]
            if not targets:
                raise model.ApiError("账号不存在", 404)
            # 拥有者本来就能写，不算"授予"
            targets = [t for t in targets if t["id"] != row["owner_id"]]
            if not targets:
                raise model.ApiError("你已是该名单的拥有者")
            with lists.tx() as lx:
                for t in targets:
                    lists.set_grant(lx, "roster", rid, t["id"], "write", "invite")
            names = "、".join(panel.public_name(t["name"], t["uid"]) for t in targets)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_grant", str(rid),
                            panel.audit_note(granted=len(targets)))
            return self._json({"ok": True, "granted": len(targets), "who": names})

        if path == "/roster/grant/decide" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            row = open_roster(rid, write=True)
            perms = lists.grant_summary(lists.connect(), acc, "roster", row)
            if not perms["canApprove"]:
                raise model.ApiError("该名单的写入申请仅拥有者可审批", 403)
            who = int(data.get("accountId") or 0)
            action = (data.get("action") or "").strip()
            if action not in ("approve", "reject"):
                raise model.ApiError("未知操作")
            with lists.tx() as lx:
                if action == "approve":
                    lists.set_grant(lx, "roster", rid, who, "write", "ask")
                else:
                    lists.drop_grant(lx, "roster", rid, who)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"],
                            "roster_ask_approve" if action == "approve"
                            else "roster_ask_reject", str(rid),
                            panel.audit_note(account_id=who))
                panel.notice(
                    tx, "info",
                    "写入名单「%s」的申请%s" % (row["name"],
                                            "已通过" if action == "approve" else "被拒绝"),
                    "", account_id=who, actor=acc["username"],
                    obj="roster:%d" % rid)
            return self._json({"ok": True})

        if path == "/roster/grant/revoke" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            row = need_roster_owner(open_roster(rid, write=True))
            who = int(data.get("accountId") or 0)
            if who == row["owner_id"]:
                raise model.ApiError("无法移除拥有者自身的写入权限")
            with lists.tx() as lx:
                lists.revoke_grant(lx, "roster", rid, who)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_grant_revoke", str(rid),
                            panel.audit_note(account_id=who))
            return self._json({"ok": True})

        if path == "/rosters" and method == "GET":
            q = (query.get("q") or [""])[0].strip()
            scope = (query.get("scope") or ["mine"])[0]
            offset = _int_arg(query, "offset", 0, 0, 100000)
            limit = _int_arg(query, "limit", 30, 1, 100)
            if scope not in ("joined", "mine", "all"):
                scope = "joined"
            # 归档的名单只给超级管理员看（它已经从「我的名单」里退下去了）
            archived = (query.get("archived") or ["0"])[0] in ("1", "true", "yes")
            if archived:
                need("superadmin", "owner")
            res = lists.list_rosters(lists.connect(), acc, q, scope, offset, limit,
                                     archived=archived)
            names = roster_owner_names([r["ownerId"] for r in res["rosters"]])
            for r in res["rosters"]:
                r["ownerName"] = names.get(r["ownerId"], "")
            return self._json({"ok": True, **res})
        if path == "/roster" and method == "GET":
            rid = _int_arg(query, "id", 0, 0, 1000000)
            row = open_roster(rid)
            lx = lists.connect()
            counts = lists.roster_counts(lx, [rid])
            view = lists.roster_view(row, counts.get(rid, 0), acc, lx)
            view["ownerName"] = roster_owner_names([row["owner_id"]]).get(
                row["owner_id"], "")
            editors = lists.grants_of(lx, "roster", rid)
            names = roster_owner_names([e["accountId"] for e in editors])
            for e in editors:
                e["name"] = names.get(e["accountId"], "")
            view["editors"] = [e for e in editors if e["level"] == "write"]
            view["pending"] = [e for e in editors if e["level"] == "pending"]
            return self._json({"ok": True, "roster": view})

        if path == "/roster/entries" and method == "GET":
            rid = _int_arg(query, "id", 0, 0, 1000000)
            open_roster(rid)
            q = (query.get("q") or [""])[0].strip()
            offset = _int_arg(query, "offset", 0, 0, 1000000)
            limit = _int_arg(query, "limit", 100, 1, 200)
            res = lists.entries_page(lists.connect(), rid, q, offset, limit)
            return self._json({"ok": True, **res})

        if path == "/roster/create" and method == "POST":
            data = body()
            with lists.tx() as lx:
                rid = lists.create_roster(
                    lx, acc["id"], data.get("name"), data.get("note"),
                    data.get("visibility") or "private",
                    data.get("grant_mode") or "auto",
                    data.get("approve_mode") or "owner")
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_create", str(rid),
                            panel.audit_note(name=(data.get("name") or "").strip()))
            return self._json({"ok": True, "id": rid})

        if path == "/roster/update" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            open_roster(rid, write=True)
            with lists.tx() as lx:
                changed = lists.update_roster(
                    lx, rid,
                    name=data.get("name"), note=data.get("note"),
                    visibility=data.get("visibility"),
                    grant_mode=data.get("grant_mode"),
                    approve_mode=data.get("approve_mode"))
            if changed:
                with panel.tx() as tx:
                    panel.audit(tx, acc["username"], "roster_update", str(rid),
                                panel.audit_note(updated=",".join(sorted(
                                    k for k in ("name", "visibility", "grant_mode",
                                                "approve_mode")
                                    if k in data))))
            return self._json({"ok": True, "changed": bool(changed)})

        if path == "/roster/archive" and method == "POST":
            # 归档：拥有者对自己那份名单的软删除。收得回来就不算危险动作，所以不要密码；
            # 但超管替别人做是「用自己的权限动别人的数据」，走的是提权那一档
            data = body()
            rid = int(data.get("id") or 0)
            cascade = bool(data.get("cascade"))
            row = open_roster(rid)                 # 看得见就行：拥有者一定看得见自己那份
            by_admin = row["owner_id"] != acc["id"]
            if by_admin:
                need("superadmin", "owner")
                need_sudo()
            linked = lists.live_signups(lists.connect(), rid)
            if linked and not cascade:
                # 不拦着人，只把选择摆出来：一并归档，或者先去处理那几个报名。
                # linkedSignups 让界面能把话说准（「还有 2 个报名链接到这份名单」）
                return self._json({
                    "ok": False,
                    "error": "还有 %d 个报名链接到这份名单" % len(linked),
                    "linkedSignups": len(linked),
                }, 409)
            now = time.time()
            with lists.tx() as lx:
                lists.set_archived(lx, "roster", rid, now)
                for r in linked:
                    # 一并收起来：报名页当场失效，已经收到的报名一条不删
                    lists.set_archived(lx, "signup", r["id"], now)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_archive", str(rid),
                            panel.audit_note(name=row["name"],
                                             by="admin" if by_admin else "owner",
                                             cascade=len(linked) or None))
            return self._json({"ok": True, "archivedAt": now,
                               "signupsArchived": [r["id"] for r in linked]})

        if path == "/roster/unarchive" and method == "POST":
            # 取消归档是超管的事：普通账号只负责把事情收起来，放回来要平台点头
            data = body()
            rid = int(data.get("id") or 0)
            need("superadmin", "owner")
            need_sudo()
            row = open_roster(rid, allow_archived=True)
            if not lists.is_archived(row):
                return self._json({"ok": True, "already": True})
            with lists.tx() as lx:
                lists.set_archived(lx, "roster", rid, 0)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_unarchive", str(rid),
                            panel.audit_note(name=row["name"]))
            return self._json({"ok": True})

        if path == "/roster/delete" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            # 权限先说话：没有这个身份的人连它是什么状态都不该知道
            need("superadmin", "owner")
            need_sudo()
            row = open_roster(rid, allow_archived=True)
            # 彻底删除只对归档过的名单开放：这份东西先由拥有者归档、再由超管清掉，
            # 中间那一步保证「谁都不能一下就抹掉别人的东西」
            if not lists.is_archived(row):
                raise model.ApiError("先归档这份名单，再由超级管理员彻底删除", 409)
            used = lists.archived_roster_used(lists.connect(), rid)
            if used:
                raise model.ApiError(
                    "还有 %d 个报名指着这份名单，先处理那几个报名" % used, 409)
            with lists.tx() as lx:
                lists.delete_roster(lx, rid)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_delete", str(rid),
                            panel.audit_note(name=row["name"]))
            return self._json({"ok": True})

        if path == "/roster/transfer" and method == "POST":
            # 名单换人：超管提权代做。原来的拥有者留一份写入权，免得自己建出来的东西
            # 被收走之后想改又没门路
            data = body()
            rid = int(data.get("id") or 0)
            need("superadmin", "owner")
            need_sudo()
            row = open_roster(rid, write=True)
            target = conn.execute("SELECT id, uid, name, status FROM accounts WHERE uid=?",
                                  (int(data.get("uid") or 0),)).fetchone()
            if target is None or target["status"] != "active":
                raise model.ApiError("新的拥有者需要是一个正常状态的账号", 404)
            if target["id"] == row["owner_id"]:
                return self._json({"ok": True, "already": True})
            with lists.tx() as lx:
                lx.execute("UPDATE rosters SET owner_id=?, updated_at=? WHERE id=?",
                           (target["id"], time.time(), rid))
                lists.set_grant(lx, "roster", rid, row["owner_id"], "write", "transfer")
            label = panel.public_name(target["name"], target["uid"])
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_transfer", str(rid),
                            panel.audit_note(name=row["name"], to=label))
            return self._json({"ok": True, "owner": label})

        if path == "/roster/entry" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            open_roster(rid, write=True)
            with lists.tx() as lx:
                person_id, created = lists.upsert_person(
                    lx, data.get("sid"), data.get("name"),
                    data.get("grp") or "", dept=data.get("dept") or "")
                lists.add_entry(lx, rid, person_id, data.get("tag"),
                                data.get("note"), "manual")
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_entry", str(rid),
                            panel.audit_note(sid=data.get("sid"),
                                             name=data.get("name"),
                                             person="new" if created else "existing"))
            return self._json({"ok": True, "personId": person_id})

        if path == "/roster/entry/drop" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            pid = int(data.get("personId") or 0)
            open_roster(rid, write=True)
            row = lists.entry(lists.connect(), rid, pid)
            if row is None:
                raise model.ApiError("该名单中没有此人", 404)
            with lists.tx() as lx:
                lists.drop_entry(lx, rid, pid)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_entry_drop", str(rid),
                            panel.audit_note(sid=row["sid"], name=row["name"]))
            return self._json({"ok": True})

        if path == "/roster/entry/state" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            pid = int(data.get("personId") or 0)
            state = data.get("state") or "active"
            if state not in lists.ENTRY_STATE:
                raise model.ApiError("状态只能是在册或已退出")
            open_roster(rid, write=True)
            with lists.tx() as lx:
                lx.execute(
                    "UPDATE roster_entries SET state=? WHERE roster_id=? AND person_id=?",
                    (state, rid, pid))
                lists.touch(lx, rid)
            return self._json({"ok": True})

        if path == "/roster/import" and method == "POST":
            data = body()
            text = data.get("text") or ""
            rid = int(data.get("id") or 0)
            created_new = False
            if rid:
                open_roster(rid, write=True)
            else:
                name = (data.get("name") or "").strip()
                if not name:
                    raise model.ApiError("请填写新名单的名称")
                with lists.tx() as lx:
                    rid = lists.create_roster(lx, acc["id"], name,
                                              data.get("note"),
                                              data.get("visibility") or "private")
                created_new = True
            with lists.tx() as lx:
                res = lists.import_text(lx, rid, text)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_import", str(rid),
                            panel.audit_note(added=res["added"],
                                             unchanged=res["unchanged"],
                                             people=res["newPeople"],
                                             unreadable=res["failed"]))
            return self._json({"ok": True, "id": rid, "created": created_new, **res})

        if path == "/roster/export.csv" and method == "GET":
            rid = _int_arg(query, "id", 0, 0, 1000000)
            row = open_roster(rid)
            rows = lists.export_rows(lists.connect(), rid)
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="\r\n")
            for line in rows:
                writer.writerow(line)
            name = re.sub(r'[\\/:*?"<>|]+', "_", row["name"] or "roster")
            return self._send(
                200, "\ufeff" + buf.getvalue(),
                "text/csv; charset=utf-8",
                headers=[_attachment_header(name + ".csv")])

        if path == "/roster/export.xlsx" and method == "GET":
            # 与 CSV 同一份行：表头 + 数据，不加任何说明行
            rid = _int_arg(query, "id", 0, 0, 1000000)
            row = open_roster(rid)
            rows = lists.export_rows(lists.connect(), rid)
            name = re.sub(r'[\\/:*?"<>|]+', "_", row["name"] or "roster")
            return self._send(200, xlsx.build(rows, name[:28] or "名单"),
                              xlsx.CONTENT_TYPE,
                              headers=[_attachment_header(name + ".xlsx")])

        if path == "/roster/import.xlsx" and method == "POST":
            # 文件按原始字节收：xlsx 是 zip，不能当 JSON 解析
            rid = _int_arg(query, "id", 0, 0, 1000000)
            raw = getattr(self, "_raw_body", b"") or b""
            if not raw:
                raise model.ApiError("没有收到文件")
            if len(raw) > XLSX_MAX:
                raise model.ApiError("文件超过 %d MB，先拆小一点再导入"
                                     % (XLSX_MAX // (1024 * 1024)))
            try:
                rows = xlsx.parse(raw)
            except ValueError as exc:
                raise model.ApiError(str(exc))
            created_new = False
            if rid:
                open_roster(rid, write=True)
            else:
                # 没有 id 就是"用这份表格开一份新名单"，与 CSV 那条路一致
                name = (query.get("name") or [""])[0].strip()
                if not name:
                    raise model.ApiError("请填写新名单的名称，或者先打开一份名单")
                with lists.tx() as lx:
                    rid = lists.create_roster(lx, acc["id"], name, "", "private")
                created_new = True
            with lists.tx() as lx:
                res = lists.import_rows(lx, rid, rows)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_import", str(rid),
                            panel.audit_note(added=res["added"],
                                             unchanged=res["unchanged"],
                                             people=res["newPeople"],
                                             unreadable=res["failed"]))
            return self._json({"ok": True, "id": rid, "created": created_new, **res})

        if path == "/list_approvals" and method == "GET":
            # 写入申请的统一入口：普通账号也用得上，所以和超管的
            # 活跃审批分开两条路由，鉴权各管各的
            lx = lists.connect()
            pending = lists.pending_approvals(lx, acc)
            asks = lists.my_asks(lx, acc)
            ids = {p["accountId"] for p in pending} | {a["ownerId"] for a in asks}
            names = roster_owner_names(ids)
            for p in pending:
                p["name"] = p["name"] or ""
                p["accountName"] = names.get(p["accountId"], "")
            for a in asks:
                a["ownerName"] = names.get(a["ownerId"], "")
            return self._json({"ok": True, "pending": pending, "asks": asks,
                               "count": len(pending)})

        # ---- 报名：一个公开链接 + 时间窗，往名单里写人 ----
        def open_signup(sid, write=False, allow_archived=False):
            lx = lists.connect()
            row = lists.signup(lx, sid)
            if row is None:
                raise model.ApiError("报名不存在", 404)
            if lists.is_archived(row) and not allow_archived:
                raise model.ApiError("这个报名已经归档，请联系超级管理员", 409)
            if write:
                if row["owner_id"] != acc["id"]:
                    raise model.ApiError("仅报名创建者可修改", 403)
            elif not (row["owner_id"] == acc["id"]
                      or row["visibility"] != "private"
                      or acc["role"] in ("owner", "superadmin")):
                raise model.ApiError("无权查看该报名", 403)
            return row

        if path == "/signups" and method == "GET":
            q = (query.get("q") or [""])[0].strip()
            scope = (query.get("scope") or ["joined"])[0]
            if scope not in ("joined", "mine", "all"):
                scope = "joined"
            offset = _int_arg(query, "offset", 0, 0, 100000)
            limit = _int_arg(query, "limit", 30, 1, 100)
            # 归档的报名只给超级管理员看（它已经不在这几个人的列表里了）
            archived = (query.get("archived") or ["0"])[0] in ("1", "true", "yes")
            if archived:
                need("superadmin", "owner")
            res = lists.list_signups(lists.connect(), acc, q, scope, offset, limit,
                                     archived=archived)
            base = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            names = roster_owner_names([item["ownerId"] for item in res["signups"]])
            for item in res["signups"]:
                item["url"] = "%s/join/%s" % (base, item["token"])
                item["ownerName"] = names.get(item["ownerId"], "")
            return self._json({"ok": True, **res})

        if path == "/signup" and method == "GET":
            sid = _int_arg(query, "id", 0, 0, 1000000)
            row = open_signup(sid)
            lx = lists.connect()
            count = lists.signup_counts(lx, [sid]).get(sid, 0)
            view = lists.signup_view(lx, row, count, acc)
            base = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            view["url"] = "%s/join/%s" % (base, row["token"])
            return self._json({"ok": True, "signup": view})

        if path == "/signup/create" and method == "POST":
            data = body()
            roster_id = int(data.get("roster_id") or 0)
            roster_row = lists.roster(lists.connect(), roster_id)
            if roster_row is None:
                raise model.ApiError("请选择报名写入的名单")
            if lists.is_archived(roster_row):
                raise model.ApiError("这份名单已经归档，先取消归档再往里写人", 409)
            if not lists.writable(lists.connect(), acc, "roster", roster_row):
                raise model.ApiError("对该名单没有写入权限，无法往里面写人", 403)
            with lists.tx() as lx:
                sid = lists.create_signup(lx, acc["id"], data)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "signup_create", str(sid),
                            panel.audit_note(title=(data.get("title") or "").strip()))
            return self._json({"ok": True, "id": sid})

        if path == "/signup/update" and method == "POST":
            data = body()
            sid = int(data.get("id") or 0)
            open_signup(sid, write=True)
            with lists.tx() as lx:
                changed = lists.update_signup(lx, sid, data)
            if changed:
                with panel.tx() as tx:
                    panel.audit(tx, acc["username"], "signup_update", str(sid),
                                panel.audit_note(updated=",".join(sorted(
                                    k for k in ("title", "state", "capacity")
                                    if k in data))))
            return self._json({"ok": True, "changed": bool(changed)})

        if path == "/signup/archive" and method == "POST":
            # 归档报名：拥有者的软删除。自己归档不要密码；替别人做要提权
            data = body()
            sid = int(data.get("id") or 0)
            row = open_signup(sid)                 # 看得见就行
            by_admin = row["owner_id"] != acc["id"]
            if by_admin:
                need("superadmin", "owner")
                need_sudo()
            with lists.tx() as lx:
                lists.set_archived(lx, "signup", sid, time.time())
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "signup_archive", str(sid),
                            panel.audit_note(title=row["title"],
                                             by="admin" if by_admin else "owner"))
            return self._json({"ok": True, "archivedAt": time.time()})

        if path == "/signup/unarchive" and method == "POST":
            data = body()
            sid = int(data.get("id") or 0)
            need("superadmin", "owner")
            need_sudo()
            row = open_signup(sid, allow_archived=True)
            if not lists.is_archived(row):
                return self._json({"ok": True, "already": True})
            with lists.tx() as lx:
                lists.set_archived(lx, "signup", sid, 0)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "signup_unarchive", str(sid),
                            panel.audit_note(title=row["title"]))
            return self._json({"ok": True})

        if path == "/signup/delete" and method == "POST":
            data = body()
            sid = int(data.get("id") or 0)
            # 权限先说话，再看状态
            need("superadmin", "owner")
            need_sudo()
            row = open_signup(sid, allow_archived=True)
            # 与名单同一条：先归档，再由超管彻底删除
            if not lists.is_archived(row):
                raise model.ApiError("先归档这个报名，再由超级管理员彻底删除", 409)
            with lists.tx() as lx:
                lists.delete_signup(lx, sid)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "signup_delete", str(sid),
                            panel.audit_note(title=row["title"]))
            return self._json({"ok": True})

        if path == "/signup/transfer" and method == "POST":
            # 报名换人：与名单同一条规矩（超管提权代做，原拥有者留一份写入权）
            data = body()
            sid = int(data.get("id") or 0)
            need("superadmin", "owner")
            need_sudo()
            row = open_signup(sid, write=True)
            target = conn.execute("SELECT id, uid, name, status FROM accounts WHERE uid=?",
                                  (int(data.get("uid") or 0),)).fetchone()
            if target is None or target["status"] != "active":
                raise model.ApiError("新的拥有者需要是一个正常状态的账号", 404)
            if target["id"] == row["owner_id"]:
                return self._json({"ok": True, "already": True})
            with lists.tx() as lx:
                lx.execute("UPDATE signups SET owner_id=? WHERE id=?",
                           (target["id"], sid))
                lists.set_grant(lx, "signup", sid, row["owner_id"], "write", "transfer")
            label = panel.public_name(target["name"], target["uid"])
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "signup_transfer", str(sid),
                            panel.audit_note(title=row["title"], to=label))
            return self._json({"ok": True, "owner": label})

        if path == "/signup/entries" and method == "GET":
            sid = _int_arg(query, "id", 0, 0, 1000000)
            open_signup(sid)
            q = (query.get("q") or [""])[0].strip()
            offset = _int_arg(query, "offset", 0, 0, 1000000)
            limit = _int_arg(query, "limit", 100, 1, 200)
            res = lists.signup_entries_page(lists.connect(), sid, q, offset, limit)
            return self._json({"ok": True, **res})

        if path == "/signup/export.csv" and method == "GET":
            sid = _int_arg(query, "id", 0, 0, 1000000)
            row = open_signup(sid)
            rows = lists.signup_export_rows(lists.connect(), sid)
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="\r\n")
            for line in rows:
                writer.writerow(line)
            name = re.sub(r'[\\/:*?"<>|]+', "_", row["title"] or "signup")
            return self._send(
                200, "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8",
                headers=[_attachment_header(name + ".csv")])

        if path == "/signup/export.xlsx" and method == "GET":
            sid = _int_arg(query, "id", 0, 0, 1000000)
            row = open_signup(sid)
            rows = lists.signup_export_rows(lists.connect(), sid)
            name = re.sub(r'[\\/:*?"<>|]+', "_", row["title"] or "signup")
            return self._send(200, xlsx.build(rows, "报名"),
                              xlsx.CONTENT_TYPE,
                              headers=[_attachment_header(name + ".xlsx")])

        if path == "/people/search" and method == "GET":
            q = (query.get("q") or [""])[0].strip()
            return self._json({"ok": True,
                               "people": lists.search_people(lists.connect(), q)})

        if path == "/audit" and method == "GET":
            need("superadmin", "owner")
            q = (query.get("q") or [""])[0].strip()
            offset = _int_arg(query, "offset", 0, 0, 1000000)
            limit = _int_arg(query, "limit", 60, 1, 200)
            where, args = [], []
            if q:
                # 操作人存的是编号：拿名字搜也能搜到，所以再按账号名对一次
                where.append("(actor LIKE ? OR action LIKE ? OR target LIKE ?"
                             " OR detail LIKE ?"
                             " OR actor IN (SELECT CAST(uid AS TEXT) FROM accounts"
                             "              WHERE name LIKE ? OR username LIKE ?))")
                args += ["%" + q + "%"] * 6
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute("SELECT COUNT(*) AS n FROM audit" + clause,   # sql-ok 只拼列名/表名/SET 片段，值都走 ?
                                 args).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM audit" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",   # sql-ok 只拼列名/表名/SET 片段，值都走 ?
                args + [limit, offset]).fetchall()
            return self._json({"ok": True, "total": int(total or 0), "offset": offset,
                               "limit": limit,
                               "audit": panel.with_actor_labels(conn, rows)})

        return self._err("not found", 404)

    # ---------------- pages ----------------
    def _external_allowed(self):
        """Which public pages this activity's module declares.

        The core knows how to draw a handful of pages; a module has to say which
        of them it uses.  Anything it does not declare is a 404, so a route
        cannot come into existence just because the code happens to have one.
        """
        try:
            cfg = db.all_config(db.connect())
        except Exception:
            return set(modules.DEFAULT_EXTERNAL)
        module_id = cfg.get("module") or cfg.get("kind") or ""
        declared = modules.external_routes(module_id, VERSION)
        return declared if declared else set(modules.DEFAULT_EXTERNAL)

    def _page(self, method, path):
        if method != "GET":
            return self._err("method not allowed", 405)
        allowed = self._external_allowed()
        catalogue = {
            "/": ("board", "board.html", "text/html; charset=utf-8"),
            "/index.html": ("board", "board.html", "text/html; charset=utf-8"),
            "/board": ("board", "board.html", "text/html; charset=utf-8"),
            "/screen": ("screen", "screen.html", "text/html; charset=utf-8"),
            "/admin": ("admin", "admin.html", "text/html; charset=utf-8"),
            "/checkin": ("checkin", "checkin.html", "text/html; charset=utf-8"),
        }
        routes = {k: v for k, v in catalogue.items() if v[0] in allowed}

        if path == "/screen" or path.startswith("/screen/"):
            return self._screen_pack(path)
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path == "/login":
            # the console signs in through the panel now; old links still work
            return self._redirect(self._panel_login_url(back=self._console_url()))
        if path == "/admin":
            # a console with no session behind it goes to the panel and comes
            # back; a session without a seat here is refused, not redirected
            if self._console_account() is None:
                return self._redirect(self._panel_login_url())
            if self._console_seat() is None:
                return self._send(403, NOT_A_MEMBER_PAGE, "text/html; charset=utf-8")
        if path == "/checkin":
            # A QR scan carries a time-sliced token.  Valid -> hand the phone a
            # presence cookie and let it type at leisure; invalid -> just serve
            # the page, which then explains that a fresh scan is needed.
            token = (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
            conn = db.connect()
            cfg = db.all_config(conn)
            if not cfg.get("self_checkin", True):
                return self._page_html(
                    "checkin.html",
                    headers=[("Set-Cookie", "laffey_lot_presence=; Path=/; Max-Age=0")],
                )
            secret = db.checkin_secret(conn)
            if token and checkin.verify(secret, token):
                # cookie 的路径按这个活动自己的 token 来：不同活动之间互不影响
                cookie_path = sanitize_cookie_path("/" + (self._token or "") + "/")
                return self._page_html(
                    "checkin.html",
                    headers=[("Set-Cookie", presence_cookie(checkin.issue_presence(secret), cookie_path))],
                )
            return self._page_html("checkin.html")
        if path in routes:
            _page_id, name, _ctype = routes[path]
            return self._page_html(name)
        if path.startswith("/external/"):
            # 模块自带的公开资源：页面自己的 js / css，路径里不许有花活
            rel = path[len("/external/"):]
            if not rel or "/" in rel or ".." in rel or rel.startswith("."):
                return self._err("not found", 404)
            kind = db.get_config(db.connect(), "kind") or ""
            full = modules.external_file(kind, rel)
            if not full:
                return self._err("not found", 404)
            return self._serve_file(full, None, cache=True)
        if path.startswith("/module/") and path.endswith(".js"):
            # 模块页面脚本：只认 pages/<页签>.js，路径里不许有花活
            name = path[len("/module/"):-len(".js")]
            if not name or "/" in name or ".." in name or name.startswith("."):
                return self._err("not found", 404)
            kind = db.get_config(db.connect(), "kind") or ""
            folder = modules.kind_dir(kind)
            full = os.path.join(folder, "pages", name + ".js") if folder else ""
            if not full or not os.path.isfile(full):
                # 没有脚本的页签很正常：回一份空的 JS，别让浏览器报 404
                return self._send(200, b"/* this page has no script */",
                                  "application/javascript; charset=utf-8")
            return self._serve_file(full, "application/javascript; charset=utf-8", cache=True)
        if path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            if ".." in rel or rel.startswith("/"):
                return self._err("bad path", 400)
            full = os.path.join(ASSETS_DIR, rel)
            if not os.path.isfile(full):
                return self._err("not found", 404)
            return self._serve_file(full, None, cache=True)
        return self._err("not found", 404)

    def _screen_fill(self, body, conn):
        """大屏页面和面板页面用同一套占位符：标题、版本、图标、安全模式横幅。

        外观包只认这几个名字，缺一个就会原样漏在页面上（{{FAVICON}} 甚至会变成
        左上角的一行字），所以替补规则集中在这里，别再各写一份。
        """
        body = substitute_page(body, db.all_config(conn))
        return body.replace(b"{{FOOTER}}", panel.FOOTER.encode("utf-8"))

    def _screen_pack(self, path):
        """展示屏幕是一个可替换的插接件：只需一份 screen.html（和它自己的 js/css）。

        外观都在 theme/<包>/ 里，包名放在活动配置的 screen_pack 里，缺省用 classic。
        """
        conn = db.connect()
        # 活动自己没指定就用自己的内置屏幕；外部资源只被"引用"，不设平台默认值
        want = (db.get_config(conn, "screen_pack", "") or "").strip() or "classic"
        tail = path[len("/screen"):].lstrip("/")
        if ".." in tail or ".." in want:
            return self._err("bad path", 400)
        rel = tail or "screen.html"
        full = os.path.join(packs.THEME_DIR, want, rel)
        if os.path.isfile(full):
            with open(full, "rb") as fh:
                body = fh.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            if rel == "screen.html":
                body = self._screen_fill(body, conn)
            return self._send(200, body, ctype, [("Cache-Control", "no-store")])
        # 包不见了：退回 classic，别让现场黑屏
        with open(os.path.join(packs.THEME_DIR, "classic", "screen.html"), "rb") as fh:
            body = fh.read()
        body = self._screen_fill(body, conn)
        return self._send(200, body, "text/html; charset=utf-8")

    def _module_off_page(self, module_id):
        """模块被停用：活动页换成一张说明页，地址不动，启用之后刷新即回来。"""
        try:
            cfg = db.all_config(db.connect())
        except Exception:
            cfg = {}
        name = module_id
        for m in modules.all_modules(VERSION):
            if m["id"] == module_id:
                name = m.get("name") or module_id
        with open(os.path.join(PAGES_DIR, "module-off.html"), "rb") as fh:
            body = substitute_page(fh.read(), cfg)
        body = body.replace(b"{{MODULE}}", str(name).encode("utf-8"))
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _maintenance_page(self, cfg=None):
        """安全模式下活动页的替身：503、地址不变、自己重试。"""
        try:
            cfg = cfg if cfg is not None else db.all_config(db.connect())
        except Exception:
            cfg = {}
        with open(os.path.join(PAGES_DIR, "maintenance.html"), "rb") as fh:
            body = substitute_page(fh.read(), cfg)
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Retry-After", "15")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _page_body(self, name):
        """公开页的正文：模块自带这一页就用模块的，没有才回落到平台模板。"""
        kind = db.get_config(db.connect(), "kind") or ""
        full = modules.external_file(kind, name)
        if full:
            with open(full, "rb") as fh:
                return fh.read()
        platform = os.path.join(PAGES_DIR, name)
        if os.path.isfile(platform):
            with open(platform, "rb") as fh:
                return fh.read()
        return None

    def _page_html(self, name, headers=None):
        body = self._page_body(name)
        if body is None:
            return self._err("not found", 404)
        conn = db.connect()
        cfg = db.all_config(conn)
        body = substitute_page(body, cfg)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, val in (headers or []):
            self.send_header(key, val)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_file(self, full, ctype, cache):
        try:
            with open(full, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._err("not found", 404)
        if ctype is None:
            ext = os.path.splitext(full)[1].lower()
            ctype = MIME_OVERRIDES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
        if ctype.startswith("text/html"):
            conn = db.connect()
            body = substitute_page(body, db.all_config(conn))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=120" if cache else "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ---------------- api ----------------
    def _module_action(self, me, name):
        """执行模块声明的动作。鉴权、解锁、结算、参数、审计、版本号都在这里。"""
        self._check_csrf()
        conn = db.connect()
        cfg = db.all_config(conn)
        kind = cfg.get("kind") or ""
        mod = _modules_by_id(panel.connect()).get(kind) or {}
        decl = (mod.get("actions") or {}).get(name)
        if not decl:
            raise model.ApiError("这个活动没有这个动作：%s" % name, 404)
        if decl["seat"] == "owner" and me.role != "owner":
            raise model.ApiError("只有该活动的负责人能做这个操作", 403)
        if decl["unlock"] and not me.unlocked():
            raise model.ApiError("这个操作需要先打开「解锁修改」开关", 403)
        if decl["finalized"] == "block" and (cfg.get("finalized") or False):
            raise model.ApiError("已结算，如需继续请先取消结算", 409)
        try:
            payload = modules.coerce_action_args(decl, self._body())
        except (TypeError, ValueError) as exc:
            raise model.ApiError(str(exc))
        ctx = {
            "conn": conn, "cfg": cfg,
            "event": {"seq": db.event_id(), "title": cfg.get("title") or "",
                      "kind": kind},
            "account": me.acc, "seat": me.role,
            "unlocked": me.unlocked(), "now": time.time(),
        }
        with db.tx(conn):
            result = model.action(name, ctx, payload)
        if not isinstance(result, dict):
            result = {"result": result}
        scope = decl.get("target") or "event"
        for arg, value in payload.items():
            scope = scope.replace("{%s}" % arg, str(value))
        scope = "event:%s:%s" % (db.event_id(), scope)
        bus.bump(scope)
        detail = str(result.pop("audit", "") or "")
        db.audit(conn, name, detail=(detail or decl["title"])[:200],
                 actor=me.name)
        payload_out = {"ok": True, "action": name, "target": scope}
        payload_out.update(result)
        return self._json(payload_out)

    def _api(self, method, path, query):
        global LOCAL_CFG
        conn = db.connect()

        # ---- public ----
        if path == "/api/board" and method == "GET":
            # 公示页与大屏的公开数据：平台出壳（活动名、时间、版本），里面是什么由模块说
            cfg = db.all_config(conn)
            payload = {
                "ok": True,
                "config": db.public_config_view(conn),
                "present": model.present(conn, cfg),
                "serverTime": time.time(),
            }
            return self._json_cached(payload)

        if path == "/api/service" and method == "GET":
            # 与面板那份同一口数据：活动后台右上角也提示服务降级。
            # 公开，和 /api/board 一样——里面只有占用与剩余空间，没有别人的东西。
            return self._json({"ok": True, "version": VERSION,
                               "at": time.time(), "items": service_items()})

        if path == "/api/stream" and method == "GET":
            # Public like the board: a projector and a phone showing the same
            # activity hear about changes instead of polling for them.
            return self._stream(scope="event:%s" % db.event_id(), of_event=True)

        if path == "/api/screen/token" and method == "GET":
            # Public on purpose: the projector machine should not need a login.
            # Rotation is a freshness gate (a screenshot or a forwarded link goes
            # stale in ~20s), not an access control - see checkin.py.
            token, expires = checkin.current(db.checkin_secret(conn))
            return self._json(
                {
                    "ok": True,
                    "token": token,
                    "expires": expires,
                    "window": checkin.WINDOW,
                }
            )

        if path == "/api/checkin/status" and method == "GET":
            cfg = db.all_config(conn)
            verified = has_presence(self.headers, db.checkin_secret(conn))
            st = model.stats(conn, cfg)
            return self._json(
                {
                    "ok": True,
                    "enabled": bool(cfg.get("self_checkin", True)),
                    "verified": verified,
                    "title": cfg.get("title") or "",
                    "subtitle": cfg.get("subtitle") or "",
                    "checkin_count": st["checkin"],
                    "total": st["total"],
                }
            )

        if path == "/api/checkin/submit" and method == "POST":
            self._check_csrf()
            ip = self.client_address[0]
            wait = auth.login_blocked(ip)
            if wait:
                return self._err("尝试太频繁了，请 %d 秒后再试" % wait, 429)
            cfg = db.all_config(conn)
            if not cfg.get("self_checkin", True):
                return self._err("本活动未开启自助登记，请找工作人员", 403)
            if not has_presence(self.headers, db.checkin_secret(conn)):
                return self._err("登记二维码已过期，请重新扫描现场大屏上的二维码", 403)
            data = self._body()
            try:
                with db.tx(conn):
                    res = model.self_checkin(conn, data.get("sid"), data.get("name"))
            except model.ApiError as exc:
                auth.note_login_failure(ip, threshold=12, block_seconds=30)
                raise exc
            return self._json(
                {
                    "ok": True,
                    "already": res["already"],
                    "name": res["student"]["name"],
                    "sid": res["student"]["sid"],
                    "grp": res["student"].get("grp") or "",
                    "position": res["position"],
                    "tickets": res["tickets"],
                    "marks": res["marks"],
                    "role": res.get("role") or "participant",
                }
            )

        if path == "/api/me" and method == "GET":
            me = self._console_seat()
            return self._json(
                {
                    "ok": True,
                    "authed": me is not None,
                    "user": me.name if me else None,
                    "role": me.role if me else None,
                    "unlockUntil": me.unlock_until() if me else 0,
                    "version": VERSION,
                }
            )

        if path == "/api/logout" and method == "POST":
            # one session for both surfaces: signing out here signs out of the
            # panel as well, which is what the operator means by 退出
            self._check_csrf()
            token = self._panel_token()
            if token:
                with panel.tx() as tx:
                    panel.destroy_session(tx, token)
                    panel.audit(tx, me.acc["username"], "logout")
            cookie = "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % panel.COOKIE
            return self._json({"ok": True}, headers=[("Set-Cookie", cookie)])

        if path == "/api/login" and method == "POST":
            # there is no console password any more; the panel is the only door
            raise model.ApiError("活动后台已改用面板账号登录", 410)

        # ---- 模块的动作：/<token>/api/do/<name> ----
        # 平台按 module.json 的 actions 声明做鉴权、解锁、结算、参数与审计，模块只管做什么
        if path.startswith("/api/do/") and method == "POST":
            me = self._require_console()
            return self._module_action(me, path[len("/api/do/"):])

        # ---- everything below needs a seat on this activity ----
        me = self._require_console()

        def need_owner():
            if me.role != "owner":
                raise model.ApiError("只有该活动的负责人能管理成员", 403)
            return me

        if path == "/api/admin/event_info" and method == "POST":
            # the activity's own information is edited where it is displayed:
            # the platform's 活动设置 page, by the activity's owner
            if me.role != "owner":
                raise model.ApiError("只有该活动的负责人能改活动名称", 403)
            data = self._body()
            title = (data.get("title") or "").strip()
            if not title:
                raise model.ApiError("名称不能为空")
            if len(title) > 60:
                raise model.ApiError("活动名称最长 60 个字")
            seq = db.event_id()
            with panel.tx() as tx:
                tx.execute("UPDATE events SET title=? WHERE seq=?", (title, seq))
                panel.audit(tx, me.acc["username"], "event_title", str(seq),
                            panel.audit_note(title=title))
            with db.tx(conn):
                db.set_config_raw(conn, "title", title)
                db.audit(conn, "event_title", detail=title, actor=me.name)
            db.write_meta(seq, title=title)
            return self._json({"ok": True, "title": title})

        if path == "/api/admin/members" and method == "GET":
            # the roster is the platform's page, and the owner's business
            need_owner()
            seq = db.event_id()
            rows = panel.event_members(me.conn, seq)
            return self._json({
                "ok": True,
                "members": [panel.seat_view(r) for r in rows],
                "me": {"accountId": me.acc["id"], "role": me.role},
                "seats": panel.open_seats(me.conn, seq),
            })

        if path == "/api/admin/people" and method == "GET":
            # 邀请成员用的人名单：还没在这个活动里的人才列出来。
            # 与面板的 /people 是同一份载荷（panel.person_view），界面也是同一个弹窗。
            need_owner()
            q = (query.get("q") or [""])[0].strip()
            sql = ("SELECT a.id, a.uid, a.name, a.avatar, a.avatar_at FROM accounts a"
                   " WHERE a.status='active'"
                   "   AND a.id NOT IN (SELECT account_id FROM event_members WHERE seq=?)")
            args = [db.event_id()]
            if q:
                sql += " AND (a.username LIKE ? OR a.name LIKE ?)"
                args += ["%" + q + "%", "%" + q + "%"]
            sql += (" ORDER BY CASE a.role WHEN 'owner' THEN 0 WHEN 'superadmin' THEN 1 ELSE 2 END,"
                    "          a.name, a.id LIMIT 48")
            return self._json({"ok": True, "people": [
                panel.person_view(r) for r in me.conn.execute(sql, args).fetchall()]})

        if path == "/api/admin/member" and method == "POST":
            self._check_csrf()
            need_owner()
            data = self._body()
            seq = db.event_id()
            account_id = int(data.get("accountId") or 0)
            action = (data.get("action") or "").strip()
            seat = me.conn.execute(
                "SELECT * FROM event_members WHERE seq=? AND account_id=?",
                (seq, account_id)).fetchone()
            target = me.conn.execute("SELECT * FROM accounts WHERE id=?",
                                     (account_id,)).fetchone()
            now = time.time()
            if action == "invite":
                if target is None or target["status"] != "active":
                    raise model.ApiError("这个账号现在不能加入活动", 404)
                if seat is not None and seat["state"] == "active":
                    raise model.ApiError("这个人已经在活动里了")
                if seat is not None and seat["state"] == "pending":
                    raise model.ApiError("已经邀请过了，等对方确认")
                role = (seat["role"] if seat is not None and seat["role"] == "owner"
                        else "member")
                with panel.tx() as tx:
                    tx.execute(
                        "INSERT INTO event_members"
                        " (seq, account_id, role, state, added_at, invited_by, state_at)"
                        " VALUES (?,?,?,?,?,?,?)"
                        " ON CONFLICT(seq, account_id) DO UPDATE SET"
                        "   state='pending', invited_by=excluded.invited_by,"
                        "   state_at=excluded.state_at",
                        (seq, account_id, role, "pending", now, me.acc["id"], now))
                    panel.audit(tx, me.acc["username"],
                                "event_member_invite" if seat is None
                                else "event_member_reinvite",
                                str(target["uid"]),
                                panel.audit_note(event_id=seq))
                return self._json({"ok": True, "state": "pending"})
            if action == "cancel":
                if seat is None or seat["state"] != "pending":
                    raise model.ApiError("这条邀请已经不在等待中了")
                with panel.tx() as tx:
                    tx.execute("DELETE FROM event_members WHERE seq=? AND account_id=?",
                               (seq, account_id))
                    panel.audit(tx, me.acc["username"], "event_member_cancel",
                                str(target["uid"] if target else account_id),
                                panel.audit_note(event_id=seq))
                return self._json({"ok": True, "state": "none"})
            if action == "revoke":
                if seat is None or seat["state"] != "active":
                    raise model.ApiError("这个人现在不在活动里")
                if account_id == me.acc["id"]:
                    raise model.ApiError("不能把自己移出活动，请先转让负责人")
                if seat["role"] == "owner" and panel.owners_of(me.conn, seq) <= 1:
                    raise model.ApiError("这是活动唯一的负责人，不能移出")
                with panel.tx() as tx:
                    # the row stays: who was there and when they left is part of
                    # the record, and re-inviting is a new fact, not an undo
                    tx.execute(
                        "UPDATE event_members SET state='revoked', state_at=?"
                        " WHERE seq=? AND account_id=?", (now, seq, account_id))
                    panel.audit(tx, me.acc["username"], "event_member_revoke",
                                str(target["uid"] if target else account_id),
                                panel.audit_note(event_id=seq))
                return self._json({"ok": True, "state": "revoked"})
            raise model.ApiError("未知操作")

        if path == "/api/admin/stream" and method == "GET":
            return self._stream(of_event=True)

        if path == "/api/admin/console" and method == "GET":
            cfg = db.all_config(conn)
            kind_id = cfg.get("module") or cfg.get("kind") or ""
            disabled = panel.disabled_modules(panel.connect())
            return self._json({"ok": True,
                               "external": sorted(modules.external_routes(
                                   kind_id, VERSION) or []),
                               "pages": modules.console_pages(kind_id, VERSION,
                                                              disabled=disabled),
                               "scripts": modules.console_scripts(kind_id, VERSION,
                                                                  disabled=disabled)})

        if path == "/api/admin/state" and method == "GET":
            cfg = db.all_config(conn)
            kind_id = str(cfg.get("kind") or "")
            # 拼音音节是搜索用的索引：模块申请了 search 才发，没申请就一个字节也不发
            want_search = "search" in ((modules.get(kind_id, VERSION) or {})
                                       .get("features") or {})
            records = model.snapshot(conn, cfg)
            spec = ((modules.get(kind_id, VERSION) or {}).get("features") or {}).get("search")
            records_key = (spec or {}).get("records") or "records"
            roster_id = int(cfg.get("roster_id") or 0)
            roster_name = ""
            if roster_id:
                row = lists.roster(lists.connect(), roster_id)
                roster_name = row["name"] if row else ""
            return self._json(
                {
                    "ok": True,
                    "config": db.config_view(conn),
                    "roster": module_roster(conn, cfg, roster_id, roster_name),
                    "publicBaseUrl": public_base_url(),
                    "seq": db.event_id(),
                    "shortlinkLimit": localconfig.static_limit(
                        LOCAL_CFG, "shortlink_per_event"),
                    "stats": model.stats(conn, cfg, records),
                    "data": module_extra("console_data", conn, cfg) or {},
                    "prizes": module_extra("prize_status", conn, cfg) or [],
                    "features": (modules.get(kind_id, VERSION) or {}).get(
                        "features") or {},
                    records_key: [module_record(s, spec, with_pinyin=want_search)
                                  for s in records],
                    "unlockUntil": me.unlock_until(),
                    "unlocked": me.unlocked(),
                    "audit": db.audit_tail(conn, 60),
                    "draws": module_extra("draw_history", conn, 30) or [],
                    "screenPacks": [p["id"] for p in _screen_packs()],
                    "version": VERSION,
                }
            )

        if path == "/api/admin/search" and method == "GET":
            q = (query.get("q") or [""])[0]
            limit = min(30, max(1, int((query.get("limit") or ["10"])[0] or 10)))
            cfg = db.all_config(conn)
            records = model.snapshot(conn, cfg)
            hits = model.search(conn, q, limit, records)
            spec = ((modules.get(conn_kind(conn), VERSION) or {}).get("features") or {}).get("search")
            return self._json({"ok": True,
                               "results": [module_record(s, spec) for s in hits]})

        if path == "/api/admin/mark" and method == "POST":
            self._check_csrf()
            data = self._body()
            with db.tx(conn):
                res = model.mark(
                    conn,
                    int(data.get("student_id")),
                    data.get("kind"),
                    actor="admin",
                    at=data.get("at"),
                    # out of order only while the console is unlocked, and the
                    # audit line then says so
                    allow_skip=me.unlocked(),
                )
            cur = _record_by_id(conn, int(data.get("student_id")))
            return self._json({"ok": True, "result": res, "student": cur})

        if path == "/api/admin/undo" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("这个操作需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.unmark(
                    conn,
                    int(data.get("student_id")),
                    data.get("kind"),
                    cascade=bool(data.get("cascade")),
                )
                me.touch_unlock()
            cur = _record_by_id(conn, int(data.get("student_id")))
            return self._json({"ok": True, "result": res, "student": cur,
                               "unlockUntil": time.time() + panel.EVENT_UNLOCK_TTL})

        if path == "/api/admin/set_time" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("这个操作需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.set_mark_time(
                    conn, int(data.get("student_id")), data.get("kind"), data.get("at")
                )
                me.touch_unlock()
            return self._json({"ok": True, "result": res, "unlockUntil": time.time() + panel.EVENT_UNLOCK_TTL})

        if path == "/api/admin/unlock" and method == "POST":
            self._check_csrf()
            data = self._body()
            on = bool(data.get("on"))
            with db.tx(conn):
                until = me.set_unlock(on)
                db.audit(conn, "unlock_on" if on else "unlock_off", detail=None)
            return self._json({"ok": True, "unlocked": on, "unlockUntil": until})

        if path == "/api/admin/rotate_secret" and method == "POST":
            # the console's own copy of the old "rotate the access id" action;
            # the panel owns this now, with the password behind it
            raise model.ApiError("重置访问地址已移到面板的活动卡片菜单", 410)

        if path == "/api/admin/import" and method == "POST":
            self._check_csrf()
            data = self._body()
            with db.tx(conn):
                res = model.import_roster(conn, data.get("text") or "")
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/student" and method == "POST":
            self._check_csrf()
            data = self._body()
            with db.tx(conn):
                if data.get("id"):
                    res = model.update_student(
                        conn,
                        int(data["id"]),
                        name=data.get("name"),
                        py=data.get("py"),
                        pyi=data.get("pyi"),
                        grp=data.get("grp"),
                        note=data.get("note"),
                        role=data.get("role"),
                    )
                else:
                    res = model.add_student(
                        conn,
                        data.get("sid"),
                        data.get("name"),
                        data.get("py"),
                        data.get("pyi"),
                        data.get("grp"),
                        data.get("note"),
                        data.get("role") or "participant",
                    )
            return self._json({"ok": True, "student": res})

        if path == "/api/admin/student_delete" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("这个操作需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.delete_student(conn, int(data.get("id")))
                me.touch_unlock()
            return self._json({"ok": True, "result": res})


        if path == "/api/admin/phase" and method == "POST":
            self._check_csrf()
            data = self._body()
            phase = (data.get("phase") or "").lower()
            if phase not in ("signin", "draw"):
                return self._err("阶段只能是 signin 或 draw")
            with db.tx(conn):
                db.set_config_raw(conn, "screen_phase", phase)
                db.audit(conn, "screen_phase", detail=phase)
            return self._json({"ok": True, "phase": phase})

        if path == "/api/admin/settings" and method == "POST":
            self._check_csrf()
            data = self._body()
            # 平台自己的键写在这里，玩法相关的键由模块在 module.json 的 settings 里声明；
            # 类型取自模块 defaults.json 里那个默认值
            platform_keys = {
                "title": str,
                "subtitle": str,
                "public_base_url": str,
                "self_checkin": bool,
                "screen_pack": str,
            }
            kind = db.get_config(conn, "kind") or ""
            declared = set((modules.get(kind, VERSION) or {}).get("settings") or [])
            defaults = modules.kind_defaults(kind)
            updates = {}
            for key, value in data.items():
                if key in platform_keys:
                    typ = platform_keys[key]
                elif key in declared:
                    typ = type(defaults.get(key))
                    if typ is int and isinstance(value, bool):
                        typ = bool
                else:
                    raise model.ApiError("没有这个设置项：%s" % key)
                try:
                    updates[key] = typ(value)
                except Exception:
                    raise model.ApiError("设置项 %s 格式不对" % key)
            # 语义校验由模块自己说（池子够不够大这类）
            check = getattr(model.current(), "check_settings", None)
            if callable(check):
                check(conn, updates)
            if "screen_pack" in updates:
                # 空表示这一项不引用外部资源，用活动自带的展示页
                want = (updates["screen_pack"] or "").strip()
                if want and want not in {p["id"] for p in _screen_packs()}:
                    raise model.ApiError("没有这一份屏幕：%s" % want)
                updates["screen_pack"] = want
            with db.tx(conn):
                for key, val in updates.items():
                    db.set_config_raw(conn, key, val)
                db.audit(conn, "settings", detail=json.dumps(updates, ensure_ascii=False))
            return self._json({"ok": True, "config": db.config_view(conn)})

        if path == "/api/admin/plan" and method == "GET":
            cfg = db.all_config(conn)
            plan = model.plan_bonus(conn, cfg)
            return self._json({"ok": True, **plan})

        if path == "/api/admin/finalize" and method == "POST":
            self._check_csrf()
            with db.tx(conn):
                res = model.finalize(conn)
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/unfinalize" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("这个操作需要先打开「解锁修改」开关", 403)
            with db.tx(conn):
                res = model.unfinalize(conn)
                me.touch_unlock()
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/reset_tickets" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("这个操作需要先打开「解锁修改」开关", 403)
            with db.tx(conn):
                res = model.reset_tickets(conn)
                me.touch_unlock()
            return self._json({"ok": True, "result": res})

        if path.startswith("/api/admin/roster") and not self._feature("roster"):
            # 名单是模块申请了才有的能力：没申请的玩法不该看到这几个接口
            return self._err("这个活动没有启用名单", 404)

        if path == "/api/admin/rosters" and method == "GET":
            # 活动能绑哪些名单：只看自己看得见、并且有写入权的
            lx = lists.connect()
            mine = lists.list_rosters(lx, me.acc, "", "all", 0, 200)
            out = []
            for r in mine["rosters"]:
                # 活动只读名单，不往回写：看得见就能绑，不要求写入权
                counts = lists.roster_counts(lx, [r["id"]])
                out.append({"id": r["id"], "name": r["name"],
                            "members": counts.get(r["id"], 0)})
            bound = db.get_config(conn, "roster_id", 0) or 0
            return self._json({"ok": True, "rosters": out, "bound": int(bound)})

        if path == "/api/admin/roster_bind" and method == "POST":
            need_owner()
            data = self._body()
            rid = int(data.get("roster_id") or 0)
            if rid:
                # 只要求看得见：活动从名单里读人，不写回名单
                lx = lists.connect()
                row = lists.roster(lx, rid)
                if row is None:
                    raise model.ApiError("名单不存在", 404)
                if lists.is_archived(row):
                    raise model.ApiError("这份名单已经归档，换一份再绑", 409)
                if not lists.row_visible(me.acc, row):
                    raise model.ApiError("无权查看该名单", 403)
            with db.tx(conn):
                db.set_config_raw(conn, "roster_id", rid)
                db.audit(conn, "roster_bind", detail=str(rid), actor=me.name)
            return self._json({"ok": True, "rosterId": rid})

        if path == "/api/admin/roster_sync" and method == "POST":
            need_owner()
            rid = int(db.get_config(conn, "roster_id", 0) or 0)
            if not rid:
                raise model.ApiError("这场活动还没有绑定名单", 409)
            lx = lists.connect()
            row = lists.roster(lx, rid)
            if row is None:
                raise model.ApiError("绑定的名单已经不存在", 404)
            if not lists.row_visible(me.acc, row):
                raise model.ApiError("无权查看该名单", 403)
            entries = [
                (r["sid"], r["name"], r["dept"], r["grp"])
                for r in lx.execute(
                    "SELECT p.sid, p.name, p.dept, p.grp FROM roster_entries e"
                    " JOIN people p ON p.id = e.person_id"
                    " WHERE e.roster_id=? AND e.state='active' ORDER BY p.sid", (rid,))
            ]
            with db.tx(conn):
                res = model.sync_from_roster(conn, entries, actor=me.name)
                db.set_config_raw(conn, "roster_synced_at", time.time())
            res["rosterId"] = rid
            res["rosterName"] = row["name"]
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/draw" and method == "POST":
            self._check_csrf()
            data = self._body()
            mode = (data.get("mode") or "ticket").lower()
            if mode not in ("ticket", "person"):
                raise model.ApiError("未知的抽取模式: %s" % mode)
            prize = data.get("prize") or ("特等奖" if mode == "person" else "")
            with db.tx(conn):
                if data.get("batch"):
                    # one click for the whole remaining quota of the prize
                    res = model.draw_batch(conn, prize, mode)
                    out = {
                        "ok": True,
                        "draw": res["draws"][-1],
                        "draws": res["draws"],
                        "batch": res["batch"],
                        "stopped": res["stopped"],
                    }
                else:
                    if mode == "person":
                        res = model.draw_person(conn, prize)
                    else:
                        res = model.draw_next(conn, prize)
                    out = {"ok": True, "draw": res, "draws": [res]}
            return self._json(out)

        if path == "/api/admin/draw_void" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("这个操作需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.void_draw(conn, int(data.get("id")))
                me.touch_unlock()
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/audit" and method == "GET":
            limit = min(500, int((query.get("limit") or ["200"])[0] or 200))
            return self._json({"ok": True, "audit": db.audit_tail(conn, limit)})

        if path == "/api/admin/export.csv" and method == "GET":
            return self._export_csv(conn)

        if path == "/api/admin/export.zip" and method == "GET":
            # 表由模块给，平台只打包；文件名带上活动名
            name, data = exporter.build(conn)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            for key, val in [_attachment_header(name)]:
                self.send_header(key, val)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return

        return self._err("not found", 404)

    # ---------------- csv export ----------------
    def _export_csv(self, conn):
        """仅名单 CSV：模块标了 primary 的那一张。"""
        name, text = exporter.primary_csv(conn)
        if not text:
            return self._err("这个活动没有名单可以导出", 404)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        title = re.sub(r'[\\/:*?"<>|]+', "_", (db.get_config(conn, "title") or "").strip())[:40]
        filename = "%s-%s.csv" % (title or "activity", stamp)
        data = ("\ufeff" + text).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, val in [_attachment_header(filename)]:
            self.send_header(key, val)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def bootstrap_secrets():
    conn = db.connect()
    cfg = db.all_config(conn)
    if not (cfg.get("public_base_url") or "").strip():
        with db.tx(conn):
            db.set_config_raw(conn, "public_base_url", localconfig.public_url(LOCAL_CFG))
        cfg = db.all_config(conn)
    if not db.get_config(conn, "seeded"):
        with db.tx(conn):
            model.ensure_seed(conn)
            db.set_config_raw(conn, "seeded", 1)



def main():
    global LOCAL_CFG
    LOCAL_CFG = localconfig.load()

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("LOTTERY_HOST") or LOCAL_CFG.get("host") or "0.0.0.0")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("LOTTERY_PORT") or LOCAL_CFG.get("port") or 8931)
    )
    args = parser.parse_args()

    # 崩溃自检：动手之前先记一笔「这一次要起了」。中间崩掉就没人写 ok，
    # 连着三次起不来，再下一次直接按安全模式起（安全模式不碰活动数据，坏东西不会被反复撞）
    guard = startguard.begin(db.DATA_DIR)
    if guard["auto"]:
        localconfig.set_static("safe_mode", True)
        LOCAL_CFG = localconfig.load()
        safemode.reset()
        log("崩溃自检：连续 %d 次启动失败，这一次按安全模式启动" % guard["failures"])

    panel.init_db()
    panel.bootstrap(LOCAL_CFG, log=log)
    lists.init_db()
    # 每个活动有自己的库：启动时逐个建表、补默认配置、给公示页写好地址。
    # 已归档的活动业务库被压进包里了，不能在这里凭空再造一个空的出来——
    # 那样它既没有模块也没有配置，取消归档之前就一直是坏的。
    # 安全模式下一律不动活动数据：出事时坏的多半就是这里这份东西，原样留着等人来看
    if safemode.enabled():
        log("安全模式：不搬移、不初始化活动数据")
    else:
        for seq in db.migrate_to_folders():
            log("活动 %d 搬到文件夹布局" % seq)
        for seq in panel.event_seqs():
            if not eventfs.has_data(seq):
                continue
            db.set_event(seq)
            db.init_db()
            conn = db.connect()
            bootstrap_secrets()
            model.ensure_event(conn)
    # the built-in presets live in templates/*.json; the table is what the app
    # reads, so they are copied in on every start
    with panel.tx() as tx:
        by_id = {m["id"]: m for m in modules.all_modules(VERSION)}
        presets.seed(tx, by_id)
    if guard["auto"]:
        # 自检替人做的决定也要留下痕迹：面板库这会儿已经能用了
        try:
            with panel.tx() as tx:
                panel.audit(tx, "system", "enter_safe_mode", None,
                            panel.audit_note(reason="multiple crash detected"))
        except Exception as exc:                       # noqa: BLE001 - 审计失败不该拦住启动
            log("安全模式自检的审计没写成：%s" % exc)

    # 初始化都过完了：这一次算起来了，自检清零，下次再崩从一数起。
    # 端口被占这类事发生在这一步之后，不该算进「起不来」里
    startguard.mark_ok(db.DATA_DIR)

    pidfile = os.path.join(db.DATA_DIR, "service.pid")
    try:
        os.makedirs(db.DATA_DIR, exist_ok=True)
        with open(pidfile, "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pidfile = None

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    log("service v%s" % VERSION)
    log("listening on http://%s:%d" % (args.host, args.port))
    log("public base : %s" % ((LOCAL_CFG.get("public_base_prefix") or "").rstrip("/") or "—"))
    log("activities  : %s" % (", ".join(str(s) for s in panel.event_seqs()) or "还没有活动"))
    log("data dir    : %s" % db.DATA_DIR)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("bye")
    finally:
        httpd.server_close()
        if pidfile:
            try:
                with open(pidfile) as fh:
                    if fh.read().strip() == str(os.getpid()):
                        os.unlink(pidfile)
            except OSError:
                pass


if __name__ == "__main__":
    main()
