#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ticket lottery service - stdlib only, no dependencies.

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
import socket
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import auth
import assistant
import bus
import checkin
import db
import expr
import exporter
import modules
import policy
import presets
import localconfig
import lists
import model
import packs
import panel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(BASE_DIR, "tpl")
STATIC_DIR = os.path.join(BASE_DIR, "static")
# 四段：主.次.修.构。构只增不减，是这台机器的发布序号；每次上线都要推进——见 tools/bump_version.py
# （LOTTERY_VERSION 只给测试与预发用：起两个版本号不同的实例，验证页面的热更新提示）
VERSION = os.environ.get("LOTTERY_VERSION") or "1.7.0.4"
START_TIME = time.time()
MAX_BODY = 4 * 1024 * 1024

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
SECRET = ""
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
_SHORT_MISS = {"count": 0, "since": 0.0}
SHORTLINK_TTL_MIN = 30
SHORTLINK_MAX_MISS = 40        # wrong hits per window before cooling off
SHORTLINK_WINDOW = 60.0


def log(msg):
    sys.stdout.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stdout.flush()


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


def shortlink_redirect(raw_path):
    """Return a target path for /s/<code>, or None when it does not apply.

    Deliberately lives *outside* the secret so a short code can be typed on a
    machine that has no way to receive the long URL.  The code is 6 digits and
    dies after SHORTLINK_TTL_MIN minutes.
    """
    marker = "/s/"
    idx = raw_path.rfind(marker)
    if idx < 0:
        return None
    code = raw_path[idx + len(marker):].strip("/")
    if not code or "/" in code:
        return None
    now = time.time()
    if now - _SHORT_MISS["since"] > SHORTLINK_WINDOW:
        _SHORT_MISS["count"] = 0
        _SHORT_MISS["since"] = now
    conn = db.connect()
    stored = str(db.get_config(conn, "shortlink_code", "") or "")
    expires = float(db.get_config(conn, "shortlink_expires", 0) or 0)
    target = str(db.get_config(conn, "shortlink_target", "screen") or "screen")
    if target not in ("screen", "admin", "board"):
        target = "screen"
    if not stored or now > expires or code != stored:
        _SHORT_MISS["count"] += 1
        if _SHORT_MISS["count"] >= SHORTLINK_MAX_MISS:
            time.sleep(0.5)          # cheap brake on brute force
        return None
    # The platform strips /u/<user>/<service> before forwarding, so the server
    # cannot see the prefix the browser used.  Emit a root-relative path built
    # from the configured public URL instead - that keeps the browser on the
    # same origin, and this service accepts prefixed paths anyway.
    public = public_base_url(conn)
    prefix = urlparse(public).path.rstrip("/") if "://" in public else public.rstrip("/")
    if not prefix:
        prefix = "/" + SECRET
    return prefix + "/" + ("" if target == "board" else target)


def resolve_route(raw_path):
    """Map an incoming path onto a route, demanding the secret segment.

    Published URL:  https://example.org/<mount>/<secret>/admin
    The platform strips ``/<mount>`` and forwards ``/<secret>/admin``,
    but we also accept the untouched full path, so routing works either way.

    Returns the route ("/admin") or None when the secret is missing - which is
    what makes ordinary guessing of the URL useless.
    """
    if not raw_path.startswith("/"):
        return None
    parts = raw_path.split("/")
    try:
        idx = parts.index(SECRET)
    except ValueError:
        return None
    rest = parts[idx + 1:]
    route = "/" + "/".join(rest)
    if route == "/" and raw_path.endswith("/"):
        return "/"
    return route.rstrip("/") or "/"


def ensure_public_url(conn):
    """Make sure the configured public URL carries the current secret id.

    Covers two cases: a database seeded before the secret existed, and a manual
    rotation.  A hand-edited URL that already contains the secret is kept.
    """
    stored = (db.get_config(conn, "public_base_url") or "").strip().rstrip("/")
    default = localconfig.public_url(LOCAL_CFG)
    if stored and ("/" + SECRET) in (stored + "/"):
        return stored
    with db.tx(conn):
        db.set_config_raw(conn, "public_base_url", default)
    return default


PRESENCE_MAX_AGE = checkin.PRESENCE_TTL


def presence_cookie(value, cookie_path="/"):
    return "%s=%s; Path=%s; HttpOnly; SameSite=Lax; Max-Age=%d" % (
        checkin.COOKIE_NAME,
        value,
        cookie_path,
        PRESENCE_MAX_AGE,
    )


def has_presence(headers):
    raw = headers.get("Cookie") or ""
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(checkin.COOKIE_NAME + "="):
            return checkin.check_presence(SECRET, part.split("=", 1)[1])
    return False


def public_base_url(conn):
    """Configured public URL, falling back to the deployment default."""
    url = (db.get_config(conn, "public_base_url") or "").strip()
    if url:
        return url.rstrip("/")
    return localconfig.public_url(LOCAL_CFG)


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
    """<link rel="icon"> tags for whatever icon sits in static/.

    Drop static/favicon.png (or .svg / .ico / .webp) and it is picked up on the
    next page load - no code change, no rebuild.  With no icon present we emit
    the empty data URI so the browser stops asking for a root /favicon.ico,
    which this service deliberately does not serve.
    """
    links = []
    for name in FAVICON_CANDIDATES:
        if not os.path.isfile(os.path.join(STATIC_DIR, name)):
            continue
        href = "static/" + name
        links.append('<link rel="icon" type="%s" href="%s">' % (FAVICON_TYPES[name], href))
        if name == "favicon.png":
            touch = "static/apple-touch-icon.png"
            if not os.path.isfile(os.path.join(STATIC_DIR, "apple-touch-icon.png")):
                touch = href
            links.append('<link rel="apple-touch-icon" href="%s">' % touch)
    if not links:
        return '<link rel="icon" href="data:,">'
    return "\n".join(links)


def fmt_ts(ts):
    return None if ts is None else round(float(ts), 3)


def student_public(conn, stu, cfg):
    show_times = bool(cfg.get("show_times", True))
    show_sid = bool(cfg.get("show_sid", False))
    out = {
        "id": stu["id"],
        "name": stu["name"],
        "grp": stu.get("grp") or "",
        "role": stu.get("role") or "participant",
        "codes": [{"code": t["code"], "kind": t["kind"]} for t in (stu.get("tickets") or [])],
        "progress": stu.get("progress", 0),
        "tasks": stu.get("tasks", 0),
    }
    if show_sid:
        out["sid"] = stu["sid"]
    if show_times:
        out["times"] = {k: fmt_ts(v) for k, v in (stu.get("marks") or {}).items()}
    return out


def student_admin(stu):
    return {
        "id": stu["id"],
        "sid": stu["sid"],
        "name": stu["name"],
        "py": stu.get("py"),
        "pyi": stu.get("pyi"),
        "grp": stu.get("grp") or "",
        "note": stu.get("note") or "",
        "marks": {k: fmt_ts(v) for k, v in (stu.get("marks") or {}).items()},
        "role": stu.get("role") or "participant",
        "tickets": stu.get("tickets") or [],
        "next": stu.get("next"),
        "progress": stu.get("progress", 0),
        "tasks": stu.get("tasks", 0),
        "ticket_count": stu.get("ticket_count", 0),
    }

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


def presets_ok_slug(slug):
    return bool(slug) and bool(presets.ID_RE.match(slug))


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


def _scope_of(revs, scope):
    """The part of a revision map one stream cares about (all of it by default)."""
    if not scope:
        return revs
    return {k: v for k, v in revs.items() if k == scope}


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
    """A human name for a notice body; falls back to the login name."""
    row = conn.execute("SELECT name, username FROM accounts WHERE id=?",
                       (int(account_id or 0),)).fetchone()
    if row is None:
        return "有人"
    return row["name"] or row["username"]


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

    def _stream(self, scope=None):
        """Server-sent events: the panel says when something changed.

        A stream that drops only delays the notice - the next event carries the
        whole revision map, so a client cannot end up behind after a gap.

        ``scope`` narrows what is worth sending: a projector watching one
        activity should not be woken by another activity's writes, so those
        changes are noticed and dropped instead of forwarded.
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
                self._sse({"revs": mine, "version": VERSION})
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.watch_leave()

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
        seat = panel.member_of(conn, db.current_event(), acc["id"])
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
        if self.headers.get("X-Requested-With") != "lottery":
            raise model.ApiError("缺少请求标记(可能是跨站请求)", 403)

    # ---------------- verbs ----------------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        """Three route families, in order: panel (root), short link, event (token)."""
        global EVENT_TOKENS
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        EVENT_TOKENS = panel.event_tokens(extra=(SECRET,))
        try:
            if parsed.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")

            self._drain_body()

            kind, route, token = panel.classify(parsed.path, EVENT_TOKENS)
            self._scope = "panel" if kind == "panel" else None
            if kind == "panel":
                return self._panel(method, route, parsed.path, query)

            if kind == "join":
                return self._join(method, route, token)

            redirect_to = shortlink_redirect(parsed.path)
            if redirect_to is not None:
                log("shortlink accepted -> %s" % redirect_to)
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            # every event request reads and writes that event's own database
            db.set_current_event(panel.event_seq(token) or db.DEFAULT_SEQ)
            path = route
            if path is None:
                # No panel route and no event token: nothing to serve.
                self._send(
                    404,
                    b"<!doctype html><meta charset=utf-8><title>404</title>"
                    b"<body style=\"font-family:system-ui;padding:3em;color:#888\">404 Not Found</body>",
                    "text/html; charset=utf-8",
                )
            elif path.startswith("/api/"):
                self._scope = "event:%s" % db.current_event()
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

        if method == "GET" and route.startswith("/static/"):
            rel = route[len("/static/"):]
            if ".." in rel:
                return self._err("bad path", 400)
            full = os.path.join(panel.PANEL_STATIC, rel)
            if not os.path.isfile(full):
                return self._err("not found", 404)
            with open(full, "rb") as fh:
                body = fh.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            return self._send(200, body, ctype, [("Cache-Control", "public, max-age=60")])

        if method == "GET" and route in ("", "/"):
            # 相对路径的 static/… 只有在地址以 / 结尾时才算得对
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
                            "%s %s" % (data.get("sid"), data.get("name")))
            return self._json(res)

        return self._err("not found", 404)

    # ---------------- master panel ----------------

    def _panel(self, method, route, raw_path, query):
        conn = panel.connect()
        if method == "GET" and route.startswith("/panel_static/"):
            rel = route[len("/panel_static/"):]
            if ".." in rel:
                return self._err("bad path", 400)
            full = os.path.join(panel.PANEL_STATIC, rel)
            if not os.path.isfile(full):
                # 活动页那几件通用件（二维码库）借给面板用，避免同一份文件抄两遍
                full = os.path.join(TPL_DIR, "..", "static", rel)
            if not os.path.isfile(full):
                return self._err("not found", 404)
            with open(full, "rb") as fh:
                body = fh.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            return self._send(200, body, ctype, [("Cache-Control", "public, max-age=60")])

        if not route.startswith("/api/panel/"):
            if method != "GET":
                return self._err("method not allowed", 405)
            acc = panel.session_account(conn, self._panel_token())
            name = panel.PANEL_PAGES.get(route)
            if name is None:
                return self._err("not found", 404)
            # signed-in users go straight to the console from the auth pages
            if acc is not None and route in ("/", "/register", "/login"):
                prefix = panel.cookie_path_for(raw_path, route).rstrip("/")
                self.send_response(302)
                self.send_header("Location", (prefix + "/panel") or "panel")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if acc is None and route == "/panel":
                prefix = panel.cookie_path_for(raw_path, route).rstrip("/")
                self.send_response(302)
                self.send_header("Location", (prefix + "/login") or "login")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._serve_panel_page(name)

        return self._panel_api(method, route, raw_path, query)

    def _serve_panel_page(self, name):
        path = os.path.join(TPL_DIR, name)
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._err("not found", 404)
        body = body.replace(b"{{FOOTER}}", panel.FOOTER.encode("utf-8"))
        body = body.replace(b"{{VERSION}}", VERSION.encode("utf-8"))
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
                    db.set_current_event(r["seq"])
                    titles[r["seq"]] = (
                        db.all_config(db.connect()).get("title") or "")
            return self._json({"ok": True, "invites": [
                {"seq": r["seq"],
                 "title": r["title"] or titles.get(r["seq"], ""),
                 "role": r["role"],
                 "roleLabel": panel.SEAT_LABEL.get(r["role"], r["role"]),
                 "addedAt": r["added_at"],
                 "invitedBy": r["inviter_name"] or r["inviter_username"] or ""}
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
                    "INSERT INTO accounts (username, name, role, status, pass, note, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (username, name, "admin", "pending", panel.hash_password(password),
                     note, time.time()))
                panel.audit(tx, "anon", "register", username, note)
            return self._json({"ok": True, "status": "pending"})

        if path == "/login" and method == "POST":
            data = body()
            block = _PANEL_FAILS.get(ip, 0)
            if block > time.time():
                raise model.ApiError("尝试过于频繁，请稍后再试", 429)
            username = (data.get("username") or "").strip()
            row = conn.execute("SELECT * FROM accounts WHERE lower(username)=lower(?)",
                               (username,)).fetchone()
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
                panel.audit(tx, row["username"], "login", None, ip)
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
                panel.via("令牌「%s」" % token_row["name"])
        if acc is None:
            raise model.ApiError("未登录或登录已过期", 401)

        if token_row is not None:
            _check_token_scope(path, method, token_row)

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
            if panel.session_sudo(conn, sess) <= time.time():
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
                panel.audit(tx, acc["username"], "sudo_on", None, ip)
            return self._json({"ok": True, "sudoUntil": until,
                               "sudoTtl": panel.SUDO_TTL,
                               "now": time.time()})

        if path == "/sudo/exit" and method == "POST":
            body()
            with panel.tx() as tx:
                panel.set_sudo(tx, sess, 0)
                panel.audit(tx, acc["username"], "sudo_off")
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
                            else "event_member_decline", str(seq))
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
            total = conn.execute("SELECT COUNT(*) AS n FROM accounts" + clause,
                                 args).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM accounts" + clause +
                " ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'superadmin' THEN 1 ELSE 2 END,"
                " CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,"
                " created_at LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
            return self._json({"ok": True, "me": panel.view(acc), "total": int(total or 0),
                               "offset": offset, "limit": limit,
                               "accounts": [panel.view(r) for r in rows]})

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
                return self._json({
                    "ok": True,
                    "total": int(total or 0),
                    "offset": offset,
                    "limit": limit,
                    "events": [panel.event_view(r, base, None) for r in rows],
                })
            rows = conn.execute(
                "SELECT e.*, m.role AS my_role, o.name AS owner_name"
                " FROM events e"
                " LEFT JOIN event_members m ON m.seq = e.seq AND m.account_id = ?"
                "   AND m.state='active'"
                " LEFT JOIN accounts o ON o.id = e.owner_id"
                " WHERE e.archived_at=0 AND m.seq IS NOT NULL"
                " ORDER BY e.created_at", (acc["id"],)).fetchall()
            # the public base lives in an event database, not the panel one;
            # with a single event there is no ambiguity about which one
            if len(rows) == 1:
                db.set_current_event(rows[0]["seq"])
            event_conn = db.connect()
            # links come from the configured prefix plus each row's own token -
            # reading a per-event URL would point every row at one activity
            base = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            events = [panel.event_view(r, base, r["my_role"]) for r in rows]
            # The adopted first event has no name of its own; borrow the live one
            # while there is exactly one event, so the list is not a row of dashes.
            if len(events) == 1 and not events[0]["title"]:
                events[0]["title"] = db.all_config(event_conn).get("title") or ""
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
                panel.audit(tx, acc["username"], action, target["username"],
                            reason or None)
            return self._json({"ok": True, "status": status})

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
                panel.audit(tx, acc["username"], "role_change", target["username"], role)
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
                    panel.audit(tx, acc["username"], "preset_create", slug, name)
                else:
                    tx.execute(
                        "UPDATE presets SET name=?, note=?, params=?, title=?,"
                        " visibility=?, updated_at=?, description=? WHERE id=?",
                        (name, note, json.dumps(params, ensure_ascii=False), title,
                         visibility, now, description, row["id"]))
                    panel.audit(tx, acc["username"], "preset_update", slug, name)
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
                panel.audit(tx, acc["username"], "preset_delete", slug, row["name"])
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
            db.set_current_event(seq)
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
                            str(seq))
            return self._json({"ok": True, "id": slug, "name": name})

        if path == "/modules" and method == "GET":
            need("superadmin", "owner")
            return self._json({"ok": True, "version": VERSION,
                               "modules": modules.all_modules(
                                   VERSION, disabled=panel.disabled_modules(conn))})

        if path == "/module/toggle" and method == "POST":
            # switching a module off keeps every reference to it; it only stops
            # new activities and presets from being started on it
            need("superadmin", "owner")
            data = body()
            module_id = (data.get("id") or "").strip()
            known = {m["id"] for m in modules.all_modules(VERSION)}
            if module_id not in known:
                raise model.ApiError("没有这个模块", 404)
            on = bool(data.get("enabled", True))
            current = module_id not in panel.disabled_modules(conn)
            if current == on:
                return self._json({"ok": True, "enabled": on, "already": True})
            refuse_stale()
            with panel.tx() as tx:
                panel.set_module_enabled(tx, module_id, on, acc["username"])
                panel.audit(tx, acc["username"],
                            "module_enable" if on else "module_disable", module_id)
            return self._json({"ok": True, "enabled": on})

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
                panel.audit(tx, acc["username"], "preset_import", slug, module_id)
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
                panel.audit(tx, acc["username"], "event_create", str(seq), title)

            # the activity gets its own database, seeded from the kind's defaults
            db.set_current_event(seq)
            db.init_db()
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

            row = conn.execute(
                "SELECT e.*, 'owner' AS my_role, ? AS owner_name FROM events e"
                " WHERE e.seq=?", (acc["name"] or acc["username"], seq)).fetchone()
            base = public_base_url(event_conn)
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
                panel.audit(tx, acc["username"], "event_rotate", str(seq), row["title"])
            db.set_current_event(seq)
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

        if path == "/event/archive" and method == "POST":
            # Archiving is the owner's own delete.  The row and the database file
            # stay: an activity that happened is part of the record, and the
            # audit log has to keep pointing at something.
            data = body()
            seq = int(data.get("seq") or 0)
            mine = conn.execute(
                "SELECT role FROM event_members WHERE seq=? AND account_id=?",
                (seq, acc["id"])).fetchone()
            if mine is None or mine["role"] != "owner":
                raise model.ApiError("\u53ea\u6709\u8be5\u6d3b\u52a8\u7684\u8d1f\u8d23\u4eba\u80fd\u5b58\u6863", 403)
            need_sudo()
            row = conn.execute("SELECT title, archived_at FROM events WHERE seq=?",
                               (seq,)).fetchone()
            if row is None:
                raise model.ApiError("\u6d3b\u52a8\u4e0d\u5b58\u5728", 404)
            if row["archived_at"]:
                return self._json({"ok": True, "seq": seq, "archived": True,
                                   "already": True})
            refuse_stale()
            with panel.tx() as tx:
                tx.execute("UPDATE events SET archived_at=? WHERE seq=?",
                           (time.time(), seq))
                panel.audit(tx, acc["username"], "event_archive", str(seq),
                            row["title"])
            db.set_current_event(seq)
            with db.tx(db.connect()):
                db.audit(db.connect(), "event_archive", detail="archived")
            return self._json({"ok": True, "seq": seq, "archived": True,
                               "title": row["title"]})

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
                panel.audit(tx, acc["username"], "event_title", str(seq), title)
            # the event pages read their heading from the event database
            db.set_current_event(seq)
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
                    panel.audit(tx, acc["username"], "rename", username,
                                "%s -> %s" % (acc["username"], username))
                if name != acc["name"]:
                    panel.audit(tx, username, "profile_name", None, name)
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
                    panel.audit(tx, acc["username"], "avatar_url", None, cleaned[:120])
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
                panel.audit(tx, acc["username"], "avatar_set", None, stored)
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
                            "%d 个文件，%s" % (got["files"], packs.size_text(got["size"])))
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
            return self._json({"ok": True, "host": host_stats(),
                               "users": panel.presence(conn)})

        # ---- 运维助手：模型配置与一次对话 ----
        if path == "/assistant" and method == "GET":
            return self._json({"ok": True, "config": assistant.config_of(acc),
                               "tools": [t["name"] for t in assistant.TOOLS],
                               "writes": sorted(policy.WRITE_ACTIONS)})

        if path == "/assistant/config" and method == "POST":
            data = body()
            base = (data.get("base") or "").strip()
            if base and not re.match(r"^https?://", base):
                raise model.ApiError("接口地址要以 http:// 或 https:// 开头")
            with panel.tx() as tx:
                assistant.save_config(tx, acc["id"], data.get("base"),
                                      data.get("model"), data.get("key") or "",
                                      clear=bool(data.get("clear")))
                panel.audit(tx, acc["username"], "assistant_config", None,
                            "%s / %s" % (base or "—", (data.get("model") or "—")))
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
                result = assistant.run(conn, acc, client, full, messages)
            except Exception as exc:
                raise model.ApiError("调用模型失败：%s" % str(exc)[:120], 502)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "assistant_ask", None,
                            "%d 次工具调用" % len(result.get("steps") or []))
            return self._json({"ok": True, "reply": result["reply"],
                               "steps": result["steps"]})

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
                            "%s / %s" % (name, "、".join(
                                panel.TOKEN_SCOPE_LABEL.get(s, s) for s in scopes)))
            item = [t for t in panel.tokens_of(conn, acc["id"]) if t["id"] == tid]
            return self._json({"ok": True, "token": plain,
                               "item": item[0] if item else None})

        if path == "/token/revoke" and method == "POST":
            data = body()
            try:
                with panel.tx() as tx:
                    row = panel.revoke_token(tx, acc["id"], data.get("id"))
                    panel.audit(tx, acc["username"], "token_revoke", str(row["id"]),
                                row["name"])
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

        def open_roster(rid, write=False):
            """The roster row, after checking this account may look at it."""
            row = lists.roster(lists.connect(), rid)
            if row is None:
                raise model.ApiError("名单不存在", 404)
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
                            str(rid), long_row["name"])
                if level == "pending":
                    panel.notice(
                        tx, "important", "有人申请写入名单「%s」" % long_row["name"],
                        "%s 申请修改这份名单，需要你确认。" % _account_label(conn, acc["id"]),
                        account_id=long_row["owner_id"], actor=acc["username"],
                        obj="roster:%d" % rid)
            return self._json({"ok": True, "level": level,
                               "levelLabel": lists.LEVEL_LABEL[level]})

        if path == "/roster/grant" and method == "POST":
            # 拥有者直接把人加进写入名单：私有名单只有这一条路
            data = body()
            rid = int(data.get("id") or 0)
            row = need_roster_owner(open_roster(rid, write=True))
            who = (data.get("username") or "").strip()
            target = conn.execute(
                "SELECT * FROM accounts WHERE lower(username)=lower(?)",
                (who,)).fetchone()
            if target is None:
                raise model.ApiError("账号不存在", 404)
            if target["id"] == row["owner_id"]:
                raise model.ApiError("你已是该名单的拥有者")
            with lists.tx() as lx:
                lists.set_grant(lx, "roster", rid, target["id"], "write", "invite")
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_grant", str(rid),
                            "邀请 %s 写入" % target["username"])
            return self._json({"ok": True})

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
                            else "roster_ask_reject", str(rid), str(who))
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
                            str(who))
            return self._json({"ok": True})

        if path == "/rosters" and method == "GET":
            q = (query.get("q") or [""])[0].strip()
            scope = (query.get("scope") or ["mine"])[0]
            offset = _int_arg(query, "offset", 0, 0, 100000)
            limit = _int_arg(query, "limit", 30, 1, 100)
            if scope not in ("joined", "mine", "all"):
                scope = "joined"
            res = lists.list_rosters(lists.connect(), acc, q, scope, offset, limit)
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
                            (data.get("name") or "").strip())
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
                                json.dumps({k: data.get(k) for k in
                                            ("name", "visibility", "grant_mode",
                                             "approve_mode")
                                            if k in data}, ensure_ascii=False))
            return self._json({"ok": True, "changed": bool(changed)})

        if path == "/roster/delete" and method == "POST":
            data = body()
            rid = int(data.get("id") or 0)
            row = need_roster_owner(open_roster(rid, write=True))
            with lists.tx() as lx:
                lists.delete_roster(lx, rid)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "roster_delete", str(rid),
                            row["name"])
            return self._json({"ok": True})

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
                            "%s %s%s" % (data.get("sid"), data.get("name"),
                                         "" if created else "（已有人员记录）"))
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
                            "%s %s" % (row["sid"], row["name"]))
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
                            "新增 %d 人，名单内已有 %d 人，新增人员记录 %d 条，无法读取 %d 行"
                            % (res["added"], res["unchanged"], res["newPeople"],
                               res["failed"]))
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
        def open_signup(sid, write=False):
            lx = lists.connect()
            row = lists.signup(lx, sid)
            if row is None:
                raise model.ApiError("报名不存在", 404)
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
            res = lists.list_signups(lists.connect(), acc, q, scope, offset, limit)
            base = (LOCAL_CFG.get("public_base_prefix") or "").rstrip("/")
            for item in res["signups"]:
                item["url"] = "%s/join/%s" % (base, item["token"])
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
            if not lists.writable(lists.connect(), acc, "roster", roster_row):
                raise model.ApiError("对该名单没有写入权限，无法往里面写人", 403)
            with lists.tx() as lx:
                sid = lists.create_signup(lx, acc["id"], data)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "signup_create", str(sid),
                            (data.get("title") or "").strip())
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
                                json.dumps({k: data.get(k) for k in
                                            ("title", "state", "capacity")
                                            if k in data}, ensure_ascii=False))
            return self._json({"ok": True, "changed": bool(changed)})

        if path == "/signup/delete" and method == "POST":
            data = body()
            sid = int(data.get("id") or 0)
            row = open_signup(sid, write=True)
            with lists.tx() as lx:
                lists.delete_signup(lx, sid)
            with panel.tx() as tx:
                panel.audit(tx, acc["username"], "signup_delete", str(sid), row["title"])
            return self._json({"ok": True})

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
                where.append("(actor LIKE ? OR action LIKE ? OR target LIKE ?"
                             " OR detail LIKE ?)")
                args += ["%" + q + "%"] * 4
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute("SELECT COUNT(*) AS n FROM audit" + clause,
                                 args).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM audit" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
                args + [limit, offset]).fetchall()
            return self._json({"ok": True, "total": int(total or 0), "offset": offset,
                               "limit": limit, "audit": [dict(r) for r in rows]})

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
            if token and checkin.verify(SECRET, token):
                cookie_path = sanitize_cookie_path("/" + SECRET + "/")
                return self._page_html(
                    "checkin.html",
                    headers=[("Set-Cookie", presence_cookie(checkin.issue_presence(SECRET), cookie_path))],
                )
            return self._page_html("checkin.html")
        if path in routes:
            _page_id, name, ctype = routes[path]
            return self._serve_file(os.path.join(TPL_DIR, name), ctype, cache=False)
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            if ".." in rel or rel.startswith("/"):
                return self._err("bad path", 400)
            full = os.path.join(STATIC_DIR, rel)
            if not os.path.isfile(full):
                return self._err("not found", 404)
            return self._serve_file(full, None, cache=True)
        return self._err("not found", 404)

    def _screen_pack(self, path):
        """展示屏幕是一个可替换的插接件：只需一份 screen.html（和它自己的 js/css）。

        查找顺序：实例目录 screen_packs/<包>/ → 内置 builtin/screen_packs/<包>/。
        包名放在活动配置的 screen_pack 里，缺省用 classic。
        """
        conn = db.connect()
        # 活动自己没指定就用自己的内置屏幕；外部资源只被"引用"，不设平台默认值
        want = (db.get_config(conn, "screen_pack", "") or "").strip() or "classic"
        tail = path[len("/screen"):].lstrip("/")
        if ".." in tail or ".." in want:
            return self._err("bad path", 400)
        rel = tail or "screen.html"
        roots = [os.path.join(packs.INSTANCE, want),
                 os.path.join(packs.BUILTIN, want)]
        for root in roots:
            full = os.path.join(root, rel)
            if os.path.isfile(full):
                with open(full, "rb") as fh:
                    body = fh.read()
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype == "application/javascript":
                    ctype += "; charset=utf-8"
                if rel == "screen.html":
                    body = body.replace(b"{{FOOTER}}", panel.FOOTER.encode("utf-8"))
                    body = body.replace(b"{{VERSION}}", VERSION.encode("utf-8"))
                return self._send(200, body, ctype, [("Cache-Control", "no-store")])
        # 包不见了：退回内置的 classic，别让现场黑屏
        with open(os.path.join(packs.BUILTIN, "classic", "screen.html"), "rb") as fh:
            body = fh.read()
        body = body.replace(b"{{FOOTER}}", panel.FOOTER.encode("utf-8"))
        body = body.replace(b"{{VERSION}}", VERSION.encode("utf-8"))
        return self._send(200, body, "text/html; charset=utf-8")

    def _page_html(self, name, headers=None):
        with open(os.path.join(TPL_DIR, name), "rb") as fh:
            body = fh.read()
        conn = db.connect()
        cfg = db.all_config(conn)
        body = body.replace(b"{{TITLE}}", str(cfg.get("title") or "抽奖").encode("utf-8"))
        body = body.replace(b"{{FAVICON}}", favicon_markup().encode("utf-8"))
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
            cfg = db.all_config(conn)
            body = body.replace(b"{{TITLE}}", str(cfg.get("title") or "抽奖").encode("utf-8"))
            body = body.replace(b"{{VERSION}}", VERSION.encode("utf-8"))
            body = body.replace(b"{{FAVICON}}", favicon_markup().encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=120" if cache else "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ---------------- api ----------------
    def _api(self, method, path, query):
        global SECRET, LOCAL_CFG
        conn = db.connect()

        # ---- public ----
        if path == "/api/board" and method == "GET":
            cfg = db.all_config(conn)
            students = model.snapshot(conn, cfg)
            st = model.stats(conn, cfg, students)
            payload = {
                "ok": True,
                "config": db.public_config_view(conn),
                "stats": st,
                "students": [student_public(conn, s, cfg) for s in students],
                "lastDraw": model.last_draw(conn),
                "lastBatch": model.last_batch(conn),
                "winners": [d for d in model.draw_history(conn, 100) if not d["voided_at"]],
                "serverTime": time.time(),
            }
            return self._json_cached(payload)

        if path == "/api/stream" and method == "GET":
            # Public like the board: a projector and a phone showing the same
            # activity hear about changes instead of polling for them.
            return self._stream(scope="event:%s" % db.current_event())

        if path == "/api/screen/token" and method == "GET":
            # Public on purpose: the projector machine should not need a login.
            # Rotation is a freshness gate (a screenshot or a forwarded link goes
            # stale in ~20s), not an access control - see checkin.py.
            token, expires = checkin.current(SECRET)
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
            verified = has_presence(self.headers)
            st = model.stats(conn, cfg)
            return self._json(
                {
                    "ok": True,
                    "enabled": bool(cfg.get("self_checkin", True)),
                    "verified": verified,
                    "title": cfg.get("title") or "抽奖",
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
                return self._err("本活动未开启自助签到，请找工作人员", 403)
            if not has_presence(self.headers):
                return self._err("签到二维码已过期，请重新扫描现场大屏上的二维码", 403)
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
                    panel.audit(tx, "console", "logout")
            cookie = "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % panel.COOKIE
            return self._json({"ok": True}, headers=[("Set-Cookie", cookie)])

        if path == "/api/login" and method == "POST":
            # there is no console password any more; the panel is the only door
            raise model.ApiError("活动后台已改用面板账号登录", 410)

        # ---- everything below needs a seat on this activity ----
        me = self._require_console()
        self._target = console_target(path, self._peek(), db.current_event()) \
            if method == "POST" else None
        self._stale = False
        if self._target:
            base = self._base_rev(self._target)
            if base is not None and base < bus.revision(self._target):
                self._stale = True
                # marking is append-only: someone else's other fact about the
                # same person is not a disagreement, so those merge
                if path not in ("/api/admin/mark", "/api/admin/undo"):
                    raise Conflict("另一位工作人员刚刚改过这一项，本次更改没有生效")

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
            seq = db.current_event()
            with panel.tx() as tx:
                tx.execute("UPDATE events SET title=? WHERE seq=?", (title, seq))
                panel.audit(tx, me.acc["username"], "event_title", str(seq), title)
            with db.tx(conn):
                db.set_config_raw(conn, "title", title)
                db.audit(conn, "event_title", detail=title, actor=me.name)
            return self._json({"ok": True, "title": title})

        if path == "/api/admin/members" and method == "GET":
            # the roster is the platform's page, and the owner's business
            need_owner()
            seq = db.current_event()
            rows = panel.event_members(me.conn, seq)
            return self._json({
                "ok": True,
                "members": [panel.seat_view(r) for r in rows],
                "me": {"accountId": me.acc["id"], "role": me.role},
                "seats": panel.open_seats(me.conn, seq),
            })

        if path == "/api/admin/members/search" and method == "GET":
            need_owner()
            q = (query.get("q") or [""])[0].strip()
            if len(q) < 1:
                return self._json({"ok": True, "accounts": []})
            like = "%" + q + "%"
            rows = me.conn.execute(
                "SELECT id, username, name, role FROM accounts"
                " WHERE status='active'"
                "   AND (username LIKE ? OR name LIKE ?)"
                "   AND id NOT IN (SELECT account_id FROM event_members WHERE seq=?)"
                " ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'superadmin' THEN 1 ELSE 2 END,"
                "          name, username LIMIT 8",
                (like, like, db.current_event())).fetchall()
            return self._json({"ok": True, "accounts": [
                {"id": r["id"], "username": r["username"], "name": r["name"],
                 "role": r["role"], "roleLabel": panel.ROLE_LABEL.get(r["role"], r["role"])}
                for r in rows]})

        if path == "/api/admin/member" and method == "POST":
            self._check_csrf()
            need_owner()
            data = self._body()
            seq = db.current_event()
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
                                target["username"], str(seq))
                return self._json({"ok": True, "state": "pending"})
            if action == "cancel":
                if seat is None or seat["state"] != "pending":
                    raise model.ApiError("这条邀请已经不在等待中了")
                with panel.tx() as tx:
                    tx.execute("DELETE FROM event_members WHERE seq=? AND account_id=?",
                               (seq, account_id))
                    panel.audit(tx, me.acc["username"], "event_member_cancel",
                                target["username"] if target else str(account_id), str(seq))
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
                                target["username"] if target else str(account_id), str(seq))
                return self._json({"ok": True, "state": "revoked"})
            raise model.ApiError("未知操作")

        if path == "/api/admin/stream" and method == "GET":
            return self._stream()

        if path == "/api/admin/console" and method == "GET":
            cfg = db.all_config(conn)
            kind_id = cfg.get("module") or cfg.get("kind") or "nwuca.lottery"
            disabled = panel.disabled_modules(panel.connect())
            return self._json({"ok": True,
                               "pages": modules.console_pages(kind_id, VERSION,
                                                              disabled=disabled)})

        if path == "/api/admin/state" and method == "GET":
            cfg = db.all_config(conn)
            students = model.snapshot(conn, cfg)
            roster_id = int(cfg.get("roster_id") or 0)
            roster_name = ""
            if roster_id:
                row = lists.roster(lists.connect(), roster_id)
                roster_name = row["name"] if row else ""
            detached = conn.execute(
                "SELECT COUNT(*) AS n FROM students WHERE detached_at<>0"
            ).fetchone()["n"]
            return self._json(
                {
                    "ok": True,
                    "config": db.config_view(conn),
                    "roster": {"id": roster_id, "name": roster_name,
                               "syncedAt": cfg.get("roster_synced_at") or 0,
                               "detached": int(detached or 0)},
                    "publicBaseUrl": public_base_url(conn),
                    "stats": model.stats(conn, cfg, students),
                    "prizes": model.prize_status(conn, cfg),
                    "students": [student_admin(s) for s in students],
                    "unlockUntil": me.unlock_until(),
                    "unlocked": me.unlocked(),
                    "audit": db.audit_tail(conn, 60),
                    "draws": model.draw_history(conn, 30),
                    "version": VERSION,
                }
            )

        if path == "/api/admin/search" and method == "GET":
            q = (query.get("q") or [""])[0]
            limit = min(30, max(1, int((query.get("limit") or ["10"])[0] or 10)))
            cfg = db.all_config(conn)
            students = model.snapshot(conn, cfg)
            hits = model.search(conn, q, limit, students)
            return self._json({"ok": True, "results": [student_admin(s) for s in hits]})

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
            stu = model.snapshot(conn, db.all_config(conn))
            cur = next((s for s in stu if s["id"] == int(data.get("student_id"))), None)
            return self._json({"ok": True, "result": res, "student": student_admin(cur) if cur else None})

        if path == "/api/admin/undo" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("撤销操作需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.unmark(
                    conn,
                    int(data.get("student_id")),
                    data.get("kind"),
                    cascade=bool(data.get("cascade")),
                )
                me.touch_unlock()
            stu = model.snapshot(conn, db.all_config(conn))
            cur = next((s for s in stu if s["id"] == int(data.get("student_id"))), None)
            return self._json({"ok": True, "result": res, "student": student_admin(cur) if cur else None,
                               "unlockUntil": time.time() + panel.EVENT_UNLOCK_TTL})

        if path == "/api/admin/set_time" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("改完成时间需要先打开「解锁修改」开关", 403)
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
                return self._err("删除学生需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.delete_student(conn, int(data.get("id")))
                me.touch_unlock()
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/shortlink" and method == "POST":
            self._check_csrf()
            data = self._body()
            target = (data.get("target") or "screen").lower()
            if target not in ("screen", "admin", "board"):
                return self._err("target 只能是 screen / admin / board")
            minutes = max(1, min(180, int(data.get("minutes") or SHORTLINK_TTL_MIN)))
            code = "%06d" % secrets.randbelow(1000000)
            expires = time.time() + minutes * 60
            with db.tx(conn):
                db.set_config_raw(conn, "shortlink_code", code)
                db.set_config_raw(conn, "shortlink_target", target)
                db.set_config_raw(conn, "shortlink_expires", expires)
                db.audit(conn, "shortlink", detail="%s -> /%s (%d min)"
                         % (code, target, minutes))
            prefix = public_base_url(conn)
            if SECRET in prefix:
                prefix = prefix.split("/" + SECRET)[0]
            return self._json({
                "ok": True,
                "code": code,
                "url": prefix.rstrip("/") + "/s/" + code,
                "target": target,
                "expires": expires,
                "minutes": minutes,
            })

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
            allowed = {
                "title": str,
                "subtitle": str,
                "pool_mode": str,
                "pool_size": int,
                "pool_expr": str,
                "bonus_constant": int,
                "nominal_guaranteed": int,
                "beta": float,
                "public_base_url": str,
                "show_times": bool,
                "show_sid": bool,
                "allow_bonus": bool,
                "person_draw_no_repeat": bool,
                "draw_unique_inside": bool,
                "draw_unique_across": bool,
                "self_checkin": bool,
                "screen_show_wall": bool,
                "prizes": list,
                "event_timezone_offset": float,
                "screen_pack": str,
            }
            updates = {}
            for key, typ in allowed.items():
                if key not in data:
                    continue
                try:
                    val = typ(data[key])
                except Exception:
                    raise model.ApiError("设置项 %s 格式不对" % key)
                updates[key] = val
            if "pool_mode" in updates and updates["pool_mode"] not in ("auto", "fixed", "expr"):
                raise model.ApiError("奖票池模式只能是 auto 或 fixed")
            if "bonus_constant" in updates:
                if not (0 <= updates["bonus_constant"] <= 1000):
                    raise model.ApiError("额外奖票常数需在 0..1000 之间")
            if "pool_size" in updates:
                if updates["pool_size"] < 1 or updates["pool_size"] > 5000:
                    raise model.ApiError("奖票池数量需在 1..5000 之间")
                # Shrinking is fine as long as it does not invalidate tickets
                # that were already handed out - no reset needed.
                live = model.live_ticket_count(conn)
                if updates["pool_size"] < live:
                    raise model.ApiError(
                        "已经发出 %d 张奖票,奖票池不能小于 %d(不用重置,调大或改成自动模式即可)"
                        % (live, live)
                    )
            if updates.get("pool_mode") == "expr" or "pool_expr" in updates:
                text = updates.get("pool_expr")
                if text is None:
                    text = db.get_config(conn, "pool_expr") or ""
                try:
                    expr.check(text, model.POOL_NAMES)
                except expr.ExprError as exc:
                    raise model.ApiError(str(exc))
            if "beta" in updates and not (-0.0 <= updates["beta"] <= 5.0):
                raise model.ApiError("beta 需在 0..5 之间")
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
                return self._err("取消结算需要先打开「解锁修改」开关", 403)
            with db.tx(conn):
                res = model.unfinalize(conn)
                me.touch_unlock()
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/reset_tickets" and method == "POST":
            self._check_csrf()
            if not me.unlocked():
                return self._err("重置奖票需要先打开「解锁修改」开关", 403)
            with db.tx(conn):
                res = model.reset_tickets(conn)
                me.touch_unlock()
            return self._json({"ok": True, "result": res})

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
                return self._err("作废抽奖记录需要先打开「解锁修改」开关", 403)
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
            name, data = exporter.build(conn)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return

        return self._err("not found", 404)

    # ---------------- csv export ----------------
    def _export_csv(self, conn):
        cfg = db.all_config(conn)
        students = model.snapshot(conn, cfg)
        plan = model.plan_bonus(conn, cfg, students)
        pos = {r["id"]: r["pos"] for r in plan["rows"]}
        off = float(cfg.get("event_timezone_offset") or 8)

        def fmt(ts):
            if ts is None:
                return ""
            return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(ts) + off * 3600))

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        writer.writerow(
            [
                "学号",
                "姓名",
                "班级",
                "签到时间",
                "任务一时间",
                "任务二时间",
                "完成数",
                "速度名次",
                "奖票数",
                "奖票号码",
            ]
        )
        for s in students:
            codes = sorted(t["code"] for t in (s.get("tickets") or []))
            writer.writerow(
                [
                    s["sid"],
                    s["name"],
                    s.get("grp") or "",
                    fmt(s["marks"].get("checkin")),
                    fmt(s["marks"].get("task1")),
                    fmt(s["marks"].get("task2")),
                    s["tasks"],
                    pos.get(s["id"], ""),
                    len(codes),
                    " ".join("%03d" % c for c in codes),
                ]
            )
        body = "\ufeff" + buf.getvalue()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        data = body.encode("utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition", 'attachment; filename="lottery-%s.csv"' % stamp
        )
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
    # This activity used to carry its own console password.  The console signs in
    # through the panel now, so the hash is dropped instead of migrated: leaving
    # it in place would leave a credential nothing checks and nobody rotates.
    if cfg.get("admin_pass"):
        with db.tx(conn):
            db.set_config_raw(conn, "admin_pass", "")
    if not db.get_config(conn, "seeded"):
        with db.tx(conn):
            model.ensure_seed(conn)
            db.set_config_raw(conn, "seeded", 1)



def main():
    global SECRET, LOCAL_CFG
    LOCAL_CFG = localconfig.load()
    SECRET = LOCAL_CFG["path_secret"]

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("LOTTERY_HOST") or LOCAL_CFG.get("host") or "0.0.0.0")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("LOTTERY_PORT") or LOCAL_CFG.get("port") or 8931)
    )
    args = parser.parse_args()

    db.init_db()
    bootstrap_secrets()
    conn = db.connect()
    ensure_public_url(conn)
    panel.init_db()
    panel.bootstrap(LOCAL_CFG, event_token_hint=SECRET, log=log)
    lists.init_db()
    # the built-in presets live in templates/*.json; the table is what the app
    # reads, so they are copied in on every start
    with panel.tx() as tx:
        by_id = {m["id"]: m for m in modules.all_modules(VERSION)}
        presets.seed(tx, by_id)
    model.ensure_seed(conn)

    pidfile = os.path.join(db.DATA_DIR, "service.pid")
    try:
        os.makedirs(db.DATA_DIR, exist_ok=True)
        with open(pidfile, "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pidfile = None

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    cfg = db.all_config(conn)
    log("lottery service v%s" % VERSION)
    log("listening on http://%s:%d" % (args.host, args.port))
    log("secret path segment: %s" % SECRET)
    log("public board  : %s/" % public_base_url(conn))
    log("public screen : %s/screen" % public_base_url(conn))
    log("admin console : %s/admin" % public_base_url(conn))
    log("data dir: %s" % db.DATA_DIR)
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
