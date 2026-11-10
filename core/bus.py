# -*- coding: utf-8 -*-
"""Change notification between sessions.

Every successful write bumps a revision for its scope.  A session that is open
somewhere else hears about it through a server-sent event stream and re-reads
what it is looking at, so two people working on the same data do not drift apart.

Two pieces:

* ``revision(scope)`` / ``bump(scope)`` - the counter itself.  Scopes are
  ``panel`` for the master panel and ``event:<id>`` for one activity's console.
* ``wait_for_change(since, timeout)`` - blocks until something moves or the
  timeout expires.  A stream loop calls it in a row and sends whatever changed.

A dropped connection only delays the notice: the next event carries the current
revisions, and a client that missed several still ends up at the same number.
"""
import copy
import threading
import time

_cond = threading.Condition()
_rev = {}
_clients = {"count": 0, "last": 0.0}


def revision(scope):
    with _cond:
        return _rev.get(scope, 0)


def snapshot():
    with _cond:
        return copy.copy(_rev)


def bump(scope):
    """Record that something in this scope changed; wake the waiting streams."""
    with _cond:
        _rev[scope] = _rev.get(scope, 0) + 1
        _cond.notify_all()
        return _rev[scope]


def wait_for_change(since, timeout=25.0):
    """Block until any revision differs from ``since``, or the timeout passes.

    Returns the current revisions; an unchanged answer means "nothing happened,
    send a heartbeat and ask again".
    """
    deadline = time.time() + max(0.0, timeout)
    with _cond:
        while True:
            if snapshot_locked() != since:
                return snapshot_locked()
            left = deadline - time.time()
            if left <= 0:
                return snapshot_locked()
            _cond.wait(left)


def snapshot_locked():
    return copy.copy(_rev)


def watch_enter():
    with _cond:
        _clients["count"] += 1
        _clients["last"] = time.time()


def watch_leave():
    with _cond:
        _clients["count"] = max(0, _clients["count"] - 1)
        _clients["last"] = time.time()


def watchers():
    with _cond:
        return {"count": _clients["count"], "last": _clients["last"]}
