# -*- coding: utf-8 -*-
"""Throttling for the one password prompt left on the public pages.

The console's own password, its session table and its unlock flag all belonged
to a console that no longer has an identity of its own: it signs in with the
panel account now (see ``panel.py``).  What is still needed here is the counter
that stops someone from guessing at the self check-in form.
"""

import time

_FAILS = {}  # ip -> [count, blocked_until]


def login_blocked(ip):
    rec = _FAILS.get(ip)
    if not rec:
        return 0
    if rec[1] > time.time():
        return int(rec[1] - time.time())
    return 0


def note_login_failure(ip, threshold=6, block_seconds=60):
    rec = _FAILS.setdefault(ip, [0, 0])
    rec[0] += 1
    if rec[0] >= threshold:
        rec[0] = 0
        rec[1] = time.time() + block_seconds


def clear_login_failures(ip):
    _FAILS.pop(ip, None)
