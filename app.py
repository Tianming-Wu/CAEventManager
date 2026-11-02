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
``https://lab.nwuca.tech/u/<user>/<service>/`` and strips that prefix before
forwarding, so a leading "/" in the HTML/JS would escape the prefix.

Run:  python3 app.py [--host 0.0.0.0] [--port 8931]
"""

import argparse
import base64
import csv
import io
import json
import mimetypes
import os
import platform
import secrets
import socket
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import auth
import checkin
import db
import exporter
import localconfig
import model
import panel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(BASE_DIR, "tpl")
STATIC_DIR = os.path.join(BASE_DIR, "static")
VERSION = "1.4.0"
START_TIME = time.time()
MAX_BODY = 4 * 1024 * 1024

# Filled in by main() before the server starts listening.
SECRET = ""
LOCAL_CFG = {}

_PANEL_FAILS = {}
EVENT_TOKENS = set()

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

    Published URL:  https://lab.nwuca.tech/u/laffey/xlottery/<secret>/admin
    The platform strips ``/u/laffey/xlottery`` and forwards ``/<secret>/admin``,
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


class Handler(BaseHTTPRequestHandler):
    server_version = "LotteryHTTP/" + VERSION
    protocol_version = "HTTP/1.1"

    # ---------------- plumbing ----------------
    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=None):
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
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str), headers=headers)

    def _err(self, msg, status=400):
        self._json({"ok": False, "error": msg}, status)

    def _session(self, conn):
        cookie = self.headers.get("Cookie") or ""
        token = None
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith(auth.COOKIE_NAME + "="):
                token = part.split("=", 1)[1]
                break
        return auth.get_session(conn, token), token

    def _require_admin(self, conn):
        session, token = self._session(conn)
        if session is None:
            raise model.ApiError("未登录或登录已过期", 401)
        return session, token

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

            kind, route = panel.classify(parsed.path, EVENT_TOKENS)
            if kind == "panel":
                return self._panel(method, route, parsed.path, query)

            redirect_to = shortlink_redirect(parsed.path)
            if redirect_to is not None:
                log("shortlink accepted -> %s" % redirect_to)
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

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
                self._api(method, path, query)
            else:
                self._page(method, path)
        except model.ApiError as exc:
            self._err(exc.msg, exc.code)
        except BrokenPipeError:
            pass
        except Exception:
            log("ERROR %s %s\n%s" % (method, self.path, traceback.format_exc()))
            self._err("服务器内部错误,请看服务日志", 500)

    # ---------------- master panel ----------------

    def _panel(self, method, route, raw_path, query):
        conn = panel.connect()
        if method == "GET" and route.startswith("/panel_static/"):
            rel = route[len("/panel_static/"):]
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

        return self._panel_api(method, route, raw_path)

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

    def _panel_api(self, method, route, raw_path):
        conn = panel.connect()
        ip = self.client_address[0]
        path = route[len("/api/panel"):] or "/"

        def body():
            if self.headers.get("X-Requested-With") != "xlottery":
                raise model.ApiError("缺少请求标记", 403)
            return self._body()

        if path == "/me" and method == "GET":
            acc = panel.session_account(conn, self._panel_token())
            return self._json({"ok": True, "authed": acc is not None,
                               "me": panel.view(acc) if acc else None,
                               "version": VERSION})

        if path == "/register" and method == "POST":
            data = body()
            username = (data.get("username") or "").strip()
            name = (data.get("name") or "").strip()
            password = data.get("password") or ""
            note = (data.get("note") or "").strip()[:200]
            if not (3 <= len(username) <= 24) or not all(
                    c.isalnum() or c in "._-" for c in username):
                raise model.ApiError("用户名需 3~24 位，只能用字母数字和 . _ -")
            if not name:
                raise model.ApiError("请填写称呼")
            if len(password) < 8:
                raise model.ApiError("密码至少 8 位")
            with panel.tx() as tx:
                if tx.execute("SELECT 1 FROM accounts WHERE lower(username)=lower(?)",
                              (username,)).fetchone():
                    raise model.ApiError("用户名已被占用", 409)
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
                raise model.ApiError("用户名或密码不对", 401)
            if row["status"] == "pending":
                raise model.ApiError("账号待审核，通过后才能登录", 403)
            if row["status"] == "rejected":
                raise model.ApiError("注册申请未通过", 403)
            if row["status"] == "disabled":
                raise model.ApiError("账号已停用", 403)
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
        acc = panel.session_account(conn, self._panel_token())
        if acc is None:
            raise model.ApiError("未登录或登录已过期", 401)

        def need(*roles):
            if roles and acc["role"] not in roles:
                raise model.ApiError("当前账号无权执行该操作", 403)
            return acc

        if path == "/password" and method == "POST":
            data = body()
            if not panel.verify_password(data.get("old") or "", acc["pass"]):
                raise model.ApiError("当前密码不对", 403)
            new = (data.get("new") or "").strip()
            if len(new) < 8:
                raise model.ApiError("新密码至少 8 位")
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET pass=? WHERE id=?",
                           (panel.hash_password(new), acc["id"]))
                panel.audit(tx, acc["username"], "password_change")
            return self._json({"ok": True})

        if path == "/accounts" and method == "GET":
            rows = conn.execute(
                "SELECT * FROM accounts ORDER BY "
                "CASE role WHEN 'owner' THEN 0 WHEN 'superadmin' THEN 1 ELSE 2 END, "
                "CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, created_at"
            ).fetchall()
            return self._json({"ok": True, "me": panel.view(acc),
                               "accounts": [panel.view(r) for r in rows]})

        if path == "/events" and method == "GET":
            # the public base lives in the event database, not the panel one
            event_conn = db.connect()
            base = public_base_url(event_conn)
            rows = conn.execute(
                "SELECT e.*, m.role AS my_role, o.name AS owner_name"
                " FROM events e"
                " LEFT JOIN event_members m ON m.seq = e.seq AND m.account_id = ?"
                " LEFT JOIN accounts o ON o.id = e.owner_id"
                " ORDER BY e.created_at", (acc["id"],)).fetchall()
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
                      "disable": "disabled", "enable": "active"}.get(action)
            if status is None:
                raise model.ApiError("未知操作")
            with panel.tx() as tx:
                tx.execute("UPDATE accounts SET status=?, decided_at=?, decided_by=? WHERE id=?",
                           (status, time.time(), acc["username"], target["id"]))
                panel.audit(tx, acc["username"], action, target["username"])
            return self._json({"ok": True, "status": status})

        if path == "/account/role" and method == "POST":
            actor = need("owner")
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
                if not (3 <= len(username) <= 24) or not all(
                        c.isalnum() or c in "._-" for c in username):
                    raise model.ApiError(
                        "登录名需 3~24 位，只能用字母数字和 . _ -")
                taken = conn.execute(
                    "SELECT 1 FROM accounts WHERE lower(username)=lower(?) AND id<>?",
                    (username, acc["id"])).fetchone()
                if taken:
                    raise model.ApiError("登录名已被占用", 409)
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

        if path == "/host" and method == "GET":
            need("owner")
            return self._json({"ok": True, "host": host_stats()})

        if path == "/audit" and method == "GET":
            need("superadmin", "owner")
            rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()
            return self._json({"ok": True, "audit": [dict(r) for r in rows]})

        return self._err("not found", 404)

    # ---------------- pages ----------------
    def _page(self, method, path):
        if method != "GET":
            return self._err("method not allowed", 405)
        routes = {
            "/": ("board.html", "text/html; charset=utf-8"),
            "/index.html": ("board.html", "text/html; charset=utf-8"),
            "/board": ("board.html", "text/html; charset=utf-8"),
            "/screen": ("screen.html", "text/html; charset=utf-8"),
            "/admin": ("admin.html", "text/html; charset=utf-8"),
            "/login": ("login.html", "text/html; charset=utf-8"),
            "/checkin": ("checkin.html", "text/html; charset=utf-8"),
        }
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
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
            name, ctype = routes[path]
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
                "winners": [d for d in model.draw_history(conn, 100) if not d["voided_at"]],
                "serverTime": time.time(),
            }
            return self._json(payload)

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
            session, _ = self._session(conn)
            cfg = db.all_config(conn)
            return self._json(
                {
                    "ok": True,
                    "authed": session is not None,
                    "user": cfg.get("admin_user") if session else None,
                    "unlockUntil": session["unlock_until"] if session else 0,
                    "version": VERSION,
                }
            )

        if path == "/api/login" and method == "POST":
            self._check_csrf()
            data = self._body()
            ip = self.client_address[0]
            wait = auth.login_blocked(ip)
            if wait:
                return self._err("密码错误次数过多,请 %d 秒后再试" % wait, 429)
            cfg = db.all_config(conn)
            user = (data.get("user") or "").strip()
            pw = data.get("pass") or ""
            stored = cfg.get("admin_pass") or ""
            ok = user == (cfg.get("admin_user") or "") and auth.verify_password(pw, stored)
            if not ok:
                auth.note_login_failure(ip)
                db.audit(conn, "login_failed", detail="user=%s ip=%s" % (user, ip), actor="anon")
                return self._err("用户名或密码不对", 401)
            auth.clear_login_failures(ip)
            with db.tx(conn):
                token = auth.create_session(conn, ip)
                auth.purge_expired(conn)
                db.audit(conn, "login", detail="ip=%s" % ip)
            cpath = sanitize_cookie_path(data.get("cookie_path"))
            cookie = "%s=%s; Path=%s; HttpOnly; SameSite=Lax; Max-Age=%d" % (
                auth.COOKIE_NAME,
                token,
                cpath,
                auth.SESSION_TTL,
            )
            return self._json({"ok": True, "user": cfg.get("admin_user")}, headers=[("Set-Cookie", cookie)])

        if path == "/api/logout" and method == "POST":
            self._check_csrf()
            session, token = self._session(conn)
            if token:
                with db.tx(conn):
                    auth.destroy_session(conn, token)
            cookie = "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % auth.COOKIE_NAME
            return self._json({"ok": True}, headers=[("Set-Cookie", cookie)])

        # ---- everything below needs a session ----
        session, token = self._require_admin(conn)

        if path == "/api/admin/state" and method == "GET":
            cfg = db.all_config(conn)
            students = model.snapshot(conn, cfg)
            return self._json(
                {
                    "ok": True,
                    "config": db.config_view(conn),
                    "publicBaseUrl": public_base_url(conn),
                    "stats": model.stats(conn, cfg, students),
                    "prizes": model.prize_status(conn, cfg),
                    "students": [student_admin(s) for s in students],
                    "unlockUntil": session["unlock_until"],
                    "unlocked": session["unlock_until"] > time.time(),
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
                )
            stu = model.snapshot(conn, db.all_config(conn))
            cur = next((s for s in stu if s["id"] == int(data.get("student_id"))), None)
            return self._json({"ok": True, "result": res, "student": student_admin(cur) if cur else None})

        if path == "/api/admin/undo" and method == "POST":
            self._check_csrf()
            if not auth.is_unlocked(session):
                return self._err("撤销操作需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.unmark(
                    conn,
                    int(data.get("student_id")),
                    data.get("kind"),
                    cascade=bool(data.get("cascade")),
                )
                auth.touch_unlock(conn, token)
            stu = model.snapshot(conn, db.all_config(conn))
            cur = next((s for s in stu if s["id"] == int(data.get("student_id"))), None)
            return self._json({"ok": True, "result": res, "student": student_admin(cur) if cur else None,
                               "unlockUntil": time.time() + auth.UNLOCK_TTL})

        if path == "/api/admin/set_time" and method == "POST":
            self._check_csrf()
            if not auth.is_unlocked(session):
                return self._err("改完成时间需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.set_mark_time(
                    conn, int(data.get("student_id")), data.get("kind"), data.get("at")
                )
                auth.touch_unlock(conn, token)
            return self._json({"ok": True, "result": res, "unlockUntil": time.time() + auth.UNLOCK_TTL})

        if path == "/api/admin/unlock" and method == "POST":
            self._check_csrf()
            data = self._body()
            on = bool(data.get("on"))
            with db.tx(conn):
                until = auth.set_unlock(conn, token, on)
                db.audit(conn, "unlock_on" if on else "unlock_off", detail=None)
            return self._json({"ok": True, "unlocked": on, "unlockUntil": until})

        if path == "/api/admin/rotate_secret" and method == "POST":
            self._check_csrf()
            if not auth.is_unlocked(session):
                return self._err("重新生成访问 id 需要先打开「解锁修改」开关", 403)
            LOCAL_CFG["path_secret"] = localconfig.new_secret()
            localconfig.save(LOCAL_CFG)
            SECRET = LOCAL_CFG["path_secret"]
            new_url = localconfig.public_url(LOCAL_CFG)
            with db.tx(conn):
                db.set_config_raw(conn, "public_base_url", new_url)
                db.audit(conn, "rotate_secret", detail="new public url: " + new_url)
            return self._json({"ok": True, "publicBaseUrl": new_url, "secret": SECRET})

        if path == "/api/admin/password" and method == "POST":
            self._check_csrf()
            data = self._body()
            cfg = db.all_config(conn)
            if not auth.verify_password(data.get("old") or "", cfg.get("admin_pass") or ""):
                return self._err("原密码不对", 403)
            new = (data.get("new") or "").strip()
            if len(new) < 6:
                return self._err("新密码至少 6 位", 400)
            with db.tx(conn):
                db.set_config_raw(conn, "admin_pass", auth.hash_password(new))
                db.audit(conn, "password_change")
            return self._json({"ok": True})

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
            if not auth.is_unlocked(session):
                return self._err("删除学生需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.delete_student(conn, int(data.get("id")))
                auth.touch_unlock(conn, token)
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
                "bonus_constant": int,
                "nominal_guaranteed": int,
                "beta": float,
                "public_base_url": str,
                "show_times": bool,
                "show_sid": bool,
                "allow_bonus": bool,
                "person_draw_no_repeat": bool,
                "self_checkin": bool,
                "screen_show_wall": bool,
                "prizes": list,
                "admin_user": str,
                "event_timezone_offset": float,
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
            if "pool_mode" in updates and updates["pool_mode"] not in ("auto", "fixed"):
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
            if "beta" in updates and not (-0.0 <= updates["beta"] <= 5.0):
                raise model.ApiError("beta 需在 0..5 之间")
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
            if not auth.is_unlocked(session):
                return self._err("取消结算需要先打开「解锁修改」开关", 403)
            with db.tx(conn):
                res = model.unfinalize(conn)
                auth.touch_unlock(conn, token)
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/reset_tickets" and method == "POST":
            self._check_csrf()
            if not auth.is_unlocked(session):
                return self._err("重置奖票需要先打开「解锁修改」开关", 403)
            with db.tx(conn):
                res = model.reset_tickets(conn)
                auth.touch_unlock(conn, token)
            return self._json({"ok": True, "result": res})

        if path == "/api/admin/draw" and method == "POST":
            self._check_csrf()
            data = self._body()
            mode = (data.get("mode") or "ticket").lower()
            with db.tx(conn):
                if mode == "person":
                    res = model.draw_person(conn, data.get("prize") or "特等奖")
                elif mode == "ticket":
                    res = model.draw_next(conn, data.get("prize") or "")
                else:
                    raise model.ApiError("未知的抽取模式: %s" % mode)
            return self._json({"ok": True, "draw": res})

        if path == "/api/admin/draw_void" and method == "POST":
            self._check_csrf()
            if not auth.is_unlocked(session):
                return self._err("作废抽奖记录需要先打开「解锁修改」开关", 403)
            data = self._body()
            with db.tx(conn):
                res = model.void_draw(conn, int(data.get("id")))
                auth.touch_unlock(conn, token)
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
    if cfg.get("admin_pass"):
        return
    pw = os.environ.get("LOTTERY_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    with db.tx(conn):
        db.set_config_raw(conn, "admin_pass", auth.hash_password(pw))
        model.ensure_seed(conn)
    path = os.path.join(db.DATA_DIR, "admin_password.txt")
    try:
        os.makedirs(db.DATA_DIR, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("username: %s\npassword: %s\n" % (cfg.get("admin_user"), pw))
    except OSError:
        pass
    log("=" * 62)
    log("FIRST RUN - admin account created")
    log("  username: %s" % cfg.get("admin_user"))
    log("  password: %s" % pw)
    log("  (also written to data/admin_password.txt, chmod 600)")
    log("=" * 62)


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
