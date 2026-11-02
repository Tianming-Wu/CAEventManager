#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-deploy smoke check against the published service.

    python tools/prod_check.py https://example.org/mount <token>

Read-only: it never logs in and never writes data, so it is safe to run against
production at any time.  The token is taken from the command line (or from the
local ``config.json`` for a local instance) so it never lands in the repository.
"""
import http.client
import json
import sys
import urllib.parse

import _baseurl

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931").rstrip("/")
TOKEN = (sys.argv[2] if len(sys.argv) > 2 else _baseurl.secret()).strip("/")

parsed = urllib.parse.urlparse(URL)
if parsed.netloc:
    HOST, BASE = parsed.netloc, parsed.path.rstrip("/")
else:  # "host:port/prefix" without a scheme
    HOST, _, rest = URL.partition("/")
    BASE = "/" + rest if rest else ""
HTTPS = parsed.scheme != "http"

problems = []


def connect():
    cls = http.client.HTTPSConnection if HTTPS else http.client.HTTPConnection
    return cls(HOST, timeout=20)


def get(path):
    conn = connect()
    try:
        conn.request("GET", BASE + path, headers={"User-Agent": "curl/8"})
        res = conn.getresponse()
        return res.status, res.read()
    finally:
        conn.close()


def check(name, cond, detail=""):
    print("  %-5s %s %s" % ("PASS" if cond else "FAIL", name, "" if cond else detail))
    if not cond:
        problems.append(name)


def show(path, expect=200, want=None):
    status, body = get(path)
    text = body.decode("utf-8", "replace")
    check("%-42s %d%s" % (path, status, "" if want is None else " " + repr(want)),
          status == expect and (want is None or want in text), text[:70])
    return text


def redirect(path, want="login"):
    conn = connect()
    try:
        conn.request("GET", BASE + path, headers={"User-Agent": "curl/8"})
        res = conn.getresponse()
        res.read()
        loc = res.getheader("Location") or ""
        check("%-42s %d -> %s" % (path, res.status, loc or "(none)"),
              res.status == 302 and loc.endswith(want), loc)
    finally:
        conn.close()


print("== routing (%s%s) ==" % (HOST, BASE))
root = show("/", 200, "\u767b\u5f55")
check("root is the login page", "<title>\u767b\u5f55" in root, root[:70])
show("/register", 200, "\u53bb\u767b\u5f55")
redirect("/panel")
show("/panel_static/login.js", 200, "loginForm")
show("/panel_static/panel.css", 200, "auth-card")
for page in ("", "/screen", "/checkin", "/admin", "/login"):
    show("/" + TOKEN + page, 200)
show("/whatever/" + TOKEN + "/", 200)
show("/nope-not-a-route", 404)

print()
print("== login page ==")
for needle in ("\u6ca1\u6709\u8d26\u53f7\uff1f", "\u53bb\u6ce8\u518c", "loginForm",
               'onsubmit="return false"', "login.js"):
    check("contains %s" % needle, needle in root)
check("does not load register.js", "register.js" not in root)

print()
print("== keep-alive after a rejected login ==")
bad = json.dumps({"username": "_nobody_", "password": "wrong-password-1"}).encode()
conn = connect()
try:
    conn.request("POST", BASE + "/api/panel/login", body=bad, headers={
        "Content-Type": "application/json", "X-Requested-With": "CAEventManager"})
    first = conn.getresponse()
    first.read()
    print("  first response: %d %s" % (first.status, first.reason))
    conn.request("GET", BASE + "/api/panel/me",
                 headers={"X-Requested-With": "CAEventManager"})
    second = conn.getresponse()
    payload = second.read()[:70].decode("utf-8", "replace")
    check("follow-up on the same connection", second.status < 500,
          "%d %s" % (second.status, payload))
finally:
    conn.close()

print()
if problems:
    print("FAILED %d check(s):" % len(problems))
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("ALL PRODUCTION CHECKS PASSED")
