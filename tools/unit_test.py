#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit checks for the pure functions the panel leans on.

    python tools/unit_test.py

These run without a server.  They exist because ``host_stats()`` shipped with a
``NameError`` that no local test could reach: the failure happened after reading
``/proc/stat``, and Windows has no ``/proc``, so every run returned early.
Feeding it a fake ``/proc`` exercises the real code path on any platform.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import app  # noqa: E402

problems = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        problems.append(name)


def fake_proc(stat_a, stat_b, meminfo):
    """A /proc with two readings, so the CPU delta is deterministic."""
    root = tempfile.mkdtemp(prefix="fake-proc-")
    state = {"reads": 0}
    values = [stat_a, stat_b]

    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path).endswith("stat"):
            idx = min(state["reads"], 1)
            state["reads"] += 1
            tmp = os.path.join(root, "current-stat")
            with real_open(tmp, "w", encoding="utf-8") as fh:
                fh.write(values[idx])
            return real_open(tmp, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    with real_open(os.path.join(root, "meminfo"), "w", encoding="utf-8") as fh:
        fh.write(meminfo)
    return root, fake_open


def main():
    print("== host_stats reads a Linux /proc ==")
    # cpu lines: user nice system idle iowait irq softirq steal
    first = "cpu  100 0 100 800 0 0 0 0 0 0\n"
    second = "cpu  150 0 150 900 0 0 0 0 0 0\n"   # +100 busy, +200 total -> 50%
    meminfo = "MemTotal:       1000000 kB\nMemAvailable:    250000 kB\n"
    root, fake_open = fake_proc(first, second, meminfo)

    real_open = open
    builtins = sys.modules["builtins"]
    builtins.open = fake_open
    try:
        stats = app.host_stats(root=root, sleep=0)
    finally:
        builtins.open = real_open

    check("it returns something", stats is not None, stats)
    if not stats:
        return
    check("cpu is the delta, not the average", stats["cpu"] == 50.0, stats["cpu"])
    check("memory is used/total", abs(stats["mem"] - 75.0) < 0.01, stats["mem"])
    check("total memory in MB", abs(stats["memTotalMB"] - 1000000 / 1024.0) < 0.01,
          stats["memTotalMB"])
    check("host name is filled", bool(stats["host"]), stats["host"])
    check("uptime is positive", stats["uptime"] > 0, stats["uptime"])
    check("version is reported", stats["version"] == app.VERSION, stats["version"])
    check("data dir is reported", bool(stats["dataDir"]), stats["dataDir"])

    print("== it says so when /proc is not there ==")
    missing = app.host_stats(root=os.path.join(root, "nope"), sleep=0)
    check("missing /proc returns None", missing is None, missing)

    print("== it survives a mangled /proc ==")
    root2, fake2 = fake_proc("cpu\n", "cpu\n", "garbage\n")
    builtins.open = fake2
    try:
        broken = app.host_stats(root=root2, sleep=0)
    finally:
        builtins.open = real_open
    check("a broken file returns None instead of raising", broken is None, broken)

    print("== the panel's own helpers ==")
    check("view() maps the role label",
          app.panel.ROLE_LABEL.get("owner") == "站长", app.panel.ROLE_LABEL)
    check("classify() keeps the token first",
          app.panel.classify("/T" + "x" * 32 + "/panel", ("T" + "x" * 32,))[0] == "event",
          app.panel.classify("/T" + "x" * 32 + "/panel", ("T" + "x" * 32,)))

    print()
    if problems:
        print("FAILED %d check(s):" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("UNIT TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
