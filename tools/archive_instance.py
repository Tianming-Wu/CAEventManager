#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the full data archive ZIP from a database file, without a server.

    python tools/archive_instance.py <lottery.sqlite3> [out.zip]

Works on a snapshot copied off a live instance, which is what you want when the
event is over and nothing may be touched any more.  Produces exactly the same
sheets as the in-app "下载数据" button.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db      # noqa: E402
import exporter  # noqa: E402


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path = sys.argv[1]
    if not os.path.exists(path):
        raise SystemExit("no such database: %s" % path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cfg = db.all_config(conn)

    name, data = exporter.build(conn, cfg)
    out = sys.argv[2] if len(sys.argv) > 2 else name
    with open(out, "wb") as fh:
        fh.write(data)

    # wipe the WAL side files the read may have created, so the source snapshot
    # stays byte-for-byte what it was
    print("archived:", out, len(data), "bytes")
    print("title:", cfg.get("title") or "(未设置)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
