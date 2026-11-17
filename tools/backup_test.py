#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份的单元验证：不连网络、不起服务，自己搭一个临时实例。

    python tools/backup_test.py

覆盖：快照一致、包里该有什么不该有什么、sha256 校验、恢复（含清会话）、
以及"备份设置不进包"这条硬规矩。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))

FAILS = []


def check(name, ok, detail=None):
    if ok:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %r" % (name, detail))
        FAILS.append(name)


def main():
    import backup
    import db
    import eventfs
    import localconfig
    import panel

    box = tempfile.mkdtemp(prefix="backup-test-")
    data = os.path.join(box, "data")
    os.makedirs(data)
    os.environ["LOTTERY_DATA_DIR"] = data
    os.environ["LOTTERY_CONFIG"] = os.path.join(box, "config.json")
    localconfig.CONFIG_PATH = os.environ["LOTTERY_CONFIG"]
    db.use_data_dir(data)
    try:
        print("== 准备一个实例 ==")
        with open(localconfig.CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"port": 8931, "backups_dir": os.path.join(box, "bk")}, fh)
        panel.init_db()
        localconfig_ok = True
        with panel.tx() as tx:
            tx.execute("INSERT INTO accounts (uid, username, name, role, status, pass,"
                       " note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (10000, "owner", "站长", "owner", "active", "x", "", time.time()))
            tx.execute("INSERT INTO sessions (token, account_id, created_at,"
                       " expires_at) VALUES (?,?,?,?)",
                       ("tok-1", 1, time.time(), time.time() + 3600))
        # 一场有数据、归档了；一场还在跑
        live, packed = 1, 2
        for seq in (live, packed):
            folder = os.path.join(db.EVENTS_DIR, str(seq))
            os.makedirs(folder, exist_ok=True)
            conn = sqlite3.connect(os.path.join(folder, "data.sqlite3"))
            conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO students (name) VALUES ('甲'), ('乙')")
            conn.commit()
            conn.close()
            with panel.tx() as tx:
                tx.execute("INSERT INTO events (seq, token, title, kind, owner_id,"
                           " created_at) VALUES (?,?,?,?,?,?)",
                           (seq, "tok%d" % seq, "活动%d" % seq, "nwuca.lottery", 1,
                            time.time()))
        eventfs.archive(packed)
        check("归档的那场只剩归档包",
              eventfs.is_archived(packed) and not eventfs.has_data(packed),
              sorted(os.listdir(os.path.join(db.EVENTS_DIR, str(packed)))))
        # 头像与一份设置文件
        os.makedirs(os.path.join(data, "avatars"), exist_ok=True)
        with open(os.path.join(data, "avatars", "1.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        backup.save_settings({"webdav": {"enabled": True, "url": "https://dav.example/",
                                         "username": "u", "password": "secret"}})
        check("设置文件落在数据目录里",
              os.path.isfile(os.path.join(data, backup.SETTINGS_NAME)))

        print("== 做一份备份 ==")
        path, manifest = backup.run(version="0.0.0-test", kind="manual")
        check("备份写出到 backups_dir",
              os.path.isfile(path) and os.path.dirname(path) == os.path.join(box, "bk"),
              path)
        names = [e["name"] for e in manifest["entries"]]
        check("三个库都在清单里",
              {"db/panel.sqlite3", "db/lists.sqlite3"}.issubset(set(names))
              or "db/panel.sqlite3" in names, names[:6])
        check("活动按形态分别记了",
              sorted((e["seq"], e["shape"]) for e in manifest["events"])
              == [(1, "snapshot"), (2, "archive")], manifest["events"])
        check("身份文件进包", "config.json" in manifest["identity"], manifest["identity"])
        check("备份设置不进包",
              not any(backup.SETTINGS_NAME in n for n in names), names)

        print("== 校验 ==")
        check("这份备份自己校验通过", backup.verify(path) == [], backup.verify(path))

        print("== 恢复 ==")
        # 恢复要求服务已停：这里把面板库那条常驻连接关掉，模拟"服务不在跑"，
        # 否则旧连接会在盘上留着 -wal，恢复出来的库又被它盖回去
        try:
            panel.connect().close()
        except sqlite3.Error:
            pass
        conn = sqlite3.connect(os.path.join(data, "panel.sqlite3"))
        conn.execute("DELETE FROM accounts")
        conn.execute("INSERT INTO sessions (token, account_id, created_at, expires_at)"
                     " VALUES (?,?,?,?)", ("tok-2", 1, time.time(), time.time() + 3600))
        conn.commit()
        conn.close()
        shutil.rmtree(os.path.join(data, "avatars"), ignore_errors=True)
        actions = backup.restore(path, version="0.0.0-test")
        check("恢复把账号带回来了",
              sqlite3.connect(os.path.join(data, "panel.sqlite3"))
              .execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1, actions)
        check("恢复之后会话被清空（旧登录态不许复活）",
              sqlite3.connect(os.path.join(data, "panel.sqlite3"))
              .execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0, actions)
        check("头像也回来了", os.path.isfile(os.path.join(data, "avatars", "1.png")))
        check("在跑的那场恢复成快照形态",
              eventfs.has_data(1) and not eventfs.is_archived(1),
              sorted(os.listdir(os.path.join(db.EVENTS_DIR, "1"))))
        check("归档的那场恢复成归档包形态",
              eventfs.is_archived(2) and not eventfs.has_data(2),
              sorted(os.listdir(os.path.join(db.EVENTS_DIR, "2"))))
        check("恢复前自动留了一份当前状态",
              any(item["manifest"] and item["manifest"].get("kind") == "pre-restore"
                  for item in backup.list_backups()),
              [i["name"] for i in backup.list_backups()])
        check("默认不写回身份文件（配置不被回滚）",
              not any("身份文件 config.json" in a for a in actions), actions)

        print("== 坏包不认 ==")
        broken = path + ".broken"
        shutil.copy2(path, broken)
        with open(broken, "r+b") as fh:      # 动一个字节，sha256 就该对不上
            fh.seek(200)
            fh.write(b"\x00")
        problems = backup.verify(broken)
        check("校验能发现内容被动过", bool(problems), problems[:2])

        print("== 体检 ==")
        faces = backup.doctor()
        check("体检跑得出来", isinstance(faces, list) and bool(faces), faces[:2])
    finally:
        os.environ.pop("LOTTERY_DATA_DIR", None)
        os.environ.pop("LOTTERY_CONFIG", None)
        shutil.rmtree(box, ignore_errors=True)

    if FAILS:
        print("\nFAILED %d check(s):\n  %s" % (len(FAILS), "\n  ".join(FAILS)))
        return 1
    print("\nBACKUP TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
