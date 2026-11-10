#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给面板的通知中心塞一条通知（运维用）。

    python tools/notify.py "标题" [正文] [--level info|important|critical]
                          [--account 登录名] [--data-dir 目录]

不带 --account 表示发给所有人；带 --level critical 时，未读的账号下次打开面板
会看到一个必须点"已读/关闭"才会消失的全屏提示。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

import panel  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("title")
    parser.add_argument("body", nargs="?", default="")
    parser.add_argument("--level", default="info",
                        choices=["info", "important", "critical"])
    parser.add_argument("--account", default="", help="只发给这个登录名")
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["LOTTERY_DATA_DIR"] = args.data_dir
    panel.init_db()
    conn = panel.connect()
    account_id = 0
    if args.account:
        row = conn.execute("SELECT id FROM accounts WHERE lower(username)=lower(?)",
                           (args.account,)).fetchone()
        if row is None:
            print("没有这个账号：%s" % args.account)
            return 2
        account_id = row["id"]
    with panel.tx() as tx:
        nid = panel.notice(tx, args.level, args.title, args.body,
                           account_id=account_id, actor="cli")
    print("已排入通知 #%d（%s）" % (nid, panel.LEVEL_LABEL[args.level]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
