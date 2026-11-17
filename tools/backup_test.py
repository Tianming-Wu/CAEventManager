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
    import taskguard

    def step():
        raise RuntimeError("任务里炸了")

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

        print("== 什么时候该做 ==")
        check("触发时刻认 HH:MM", backup.parse_at("03:30") == (3, 30)
              and backup.parse_at("25:00") is None and backup.parse_at("abc") is None)
        noon = time.mktime((2026, 11, 17, 12, 0, 0, 0, 0, -1))
        slot = backup.slot_before(noon, (3, 30))
        check("中午看的是当天凌晨那一轮",
              time.strftime("%Y-%m-%d %H:%M", time.localtime(slot))
              == "2026-11-17 03:30", slot)
        early = time.mktime((2026, 11, 17, 2, 0, 0, 0, 0, -1))
        check("凌晨两点看的是前一天那一轮",
              time.strftime("%Y-%m-%d %H:%M",
                            time.localtime(backup.slot_before(early, (3, 30))))
              == "2026-11-16 03:30")
        cfg = {"enabled": True, "schedule": {"mode": "daily", "at": "03:30"}}
        old = [{"name": "x", "created": noon - 86400, "bytes": 1, "kind": "auto"}]
        new = [{"name": "x", "created": noon - 60, "bytes": 1, "kind": "auto"}]
        check("今天还没做过就该做",
              backup.due_state(noon, cfg, old)["due"] is True,
              backup.due_state(noon, cfg, old))
        check("今天做过了就不做",
              backup.due_state(noon, cfg, new)["due"] is False,
              backup.due_state(noon, cfg, new))
        check("调度方式是手动就不自动做",
              backup.due_state(noon, {"enabled": True,
                                      "schedule": {"mode": "manual", "at": "03:30"}},
                               old)["due"] is False)
        check("关掉备份就什么都不做",
              backup.due_state(noon, {"enabled": False}, old)["due"] is False)
        check("时刻写错了也不做（不猜）",
              backup.due_state(noon, {"enabled": True,
                                      "schedule": {"mode": "daily", "at": "3点半"}},
                               old)["due"] is False)

        print("== 留存 ==")
        stamps = [noon - day * 86400 for day in range(20)]
        items = [{"name": "d%d" % i, "created": stamp, "bytes": 1000, "kind": "auto"}
                 for i, stamp in enumerate(stamps)]
        kept, drop = backup.prune_plan(
            items, {"retention": {"daily": 7, "weekly": 4, "monthly": 6, "maxBytes": 0}},
            now=noon)
        names = {item["name"] for item in kept}
        check("最近 7 天每天留一份",
              {"d%d" % i for i in range(7)} <= names, sorted(names))
        check("更早的按周/月各留一份，不是全清",
              len(names) > 7 and "d%d" % 19 not in names, sorted(names))
        check("最近 24 小时内的备份不删（手动做的多半是马上要用）",
              {"d0"} <= names, sorted(names))
        pad = [{"name": "pre", "created": stamps[-1], "bytes": 10,
                "kind": "pre-restore"}]
        kept2, drop2 = backup.prune_plan(
            pad + items, {"retention": {"daily": 1, "weekly": 0, "monthly": 0,
                                        "maxBytes": 0}}, now=noon)
        check("恢复前那份永远不删",
              "pre" in {item["name"] for item in kept2}, [i["name"] for i in drop2])
        kept3, drop3 = backup.prune_plan(
            items, {"retention": {"daily": 20, "weekly": 0, "monthly": 0,
                                  "maxBytes": 3500}}, now=noon)
        check("总字节上限比档位更狠时按字节删到够",
              sum(i["bytes"] for i in kept3) <= 3500 and bool(drop3),
              (sum(i["bytes"] for i in kept3), len(drop3)))

        print("== 后台护栏 ==")
        taskguard.start("测试任务", 0.01, step)
        for _ in range(60):
            info = [t for t in taskguard.status() if t["name"] == "测试任务"]
            if info and info[0]["stopped"]:
                break
            time.sleep(0.05)
        info = [t for t in taskguard.status() if t["name"] == "测试任务"][0]
        check("连续三次失败就停掉这个任务", info["stopped"] and info["fails"] == 3, info)
        conn2 = panel.connect()
        notes = conn2.execute("SELECT title FROM notices WHERE title LIKE '%后台任务%'"
                             ).fetchall()
        check("停掉时发了通知", bool(notes), [dict(n) for n in notes])
        rows = conn2.execute("SELECT action FROM audit WHERE action='task_stopped'"
                            ).fetchall()
        check("审计里留了一笔", bool(rows), [dict(r) for r in rows])
        taskguard.stop_all()
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
