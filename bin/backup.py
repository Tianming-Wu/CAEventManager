#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地备份的运维入口。

    python3 bin/backup.py run [--note 说明]     立刻做一份
    python3 bin/backup.py auto                  到点就做（给 cron 用；服务里也会自己跑）
    python3 bin/backup.py prune [--yes]         按留存策略清理（不带 --yes 只看）
    python3 bin/backup.py list                  列出备份（时间、体积、含哪些活动）
    python3 bin/backup.py verify <包|最新>       对一遍 sha256 与库完整性
    python3 bin/backup.py restore <包|最新> [--with-identity] [--yes]
    python3 bin/backup.py settings              看设置
    python3 bin/backup.py settings set webdav.url=https://dav.example/remote.php/dav/
    python3 bin/backup.py doctor                体检：备份多久没做、活动有没有坏形态

恢复必须先停服务（`./bin/service.sh stop`）；恢复前会自动把当前状态收一份。
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))

import backup          # noqa: E402
import app             # noqa: E402


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024.0


def stamp(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def pick(name):
    """`最新` 或文件名都能指到一份备份。"""
    if name in ("最新", "latest", "-"):
        found = backup.list_backups()
        if not found:
            raise backup.BackupError("当前没有可用的备份")
        return found[0]["path"]
    if os.path.isfile(name):
        return os.path.abspath(name)
    candidate = os.path.join(backup.backups_dir(), name)
    if os.path.isfile(candidate):
        return candidate
    raise backup.BackupError("找不到这份备份：%s" % name)


def cmd_run(args):
    path, manifest = backup.run(version=app.VERSION, kind=args.kind, note=args.note or "")
    print("备份完成：%s（%s）" % (path, human(os.path.getsize(path))))
    print("  库：%s" % "、".join(sorted(manifest["databases"])) or "—")
    shapes = {}
    for item in manifest["events"]:
        shapes[item["shape"]] = shapes.get(item["shape"], 0) + 1
    print("  活动：%d 个（%s）" % (
        len(manifest["events"]),
        "、".join("%s %d" % (k, v) for k, v in sorted(shapes.items())) or "无"))
    print("  身份文件：%s（恢复默认不写回）"
          % ("、".join(manifest["identity"]) or "无"))
    if getattr(args, "upload", False):
        backup.mirror_remote(path)
    return 0


def cmd_webdav(args):
    cfg = backup.settings()
    if args.action == "test":
        print("连接与写入正常：%s" % backup.dav_self_check(cfg))
        return 0
    if args.action == "list":
        names = backup.dav_list(cfg)
        print("远端 %d 份" % len(names))
        for name in names:
            print("  %s" % name)
        return 0
    if args.action == "push":
        path = pick(args.package) if args.package else None
        if path is None:
            found = backup.list_backups()
            if not found:
                raise backup.BackupError("当前没有可用的备份")
            path = found[0]["path"]
        print("上传：%s" % backup.dav_upload(path, cfg))
        return 0
    if args.action == "prune":
        kept, dropped = backup.dav_prune(cfg=cfg)
        print("远端保留 %d 份，删除 %d 份" % (len(kept), len(dropped)))
        for name in dropped:
            print("  删 %s" % name)
        return 0
    raise backup.BackupError("不认识的动作：%s" % args.action)


def cmd_auto(args):
    state = backup.due_state()
    print("本次判定：%s（最近一次备份 %s）" % (
        state["reason"] or "该做了",
        stamp(state["last"]) if state["last"] else "还没有"))
    if not state["due"]:
        return 0
    out = backup.auto(version=app.VERSION)
    if not out:
        return 0
    path, _manifest, kept, drop = out
    print("已做：%s（%s）；留存 %d 份，清掉 %d 份"
          % (path, human(os.path.getsize(path)), len(kept), len(drop)))
    return 0


def cmd_prune(args):
    kept, drop = backup.prune(apply=args.yes)
    print("保留 %d 份、删除 %d 份%s" % (
        len(kept), len(drop), "" if args.yes else "（仅预览，加 --yes 才执行删除）"))
    for item in drop:
        print("  删 %-26s %8s  %s" % (item["name"], human(item["bytes"] or 0),
                                    stamp(item["created"])))
    for item in kept:
        print("  留 %-26s %8s  %s" % (item["name"], human(item["bytes"] or 0),
                                    stamp(item["created"])))
    return 0


def cmd_list(args):
    found = backup.list_backups()
    if not found:
        print("备份目录：%s（无备份）" % backup.backups_dir())
        return 0
    print("备份目录：%s" % backup.backups_dir())
    for item in found:
        manifest = item.get("manifest") or {}
        print("  %-26s %8s  %s  %s 活动 %d 个" % (
            item["name"], human(item["bytes"]), stamp(manifest.get("created")),
            manifest.get("kind") or "?", len(manifest.get("events") or [])))
    return 0


def cmd_verify(args):
    path = pick(args.package)
    print("校验：%s" % path)
    problems = backup.verify(path)
    if problems:
        for line in problems:
            print("  ✗ %s" % line)
        return 1
    print("  ✓ sha256 与条目都对得上")
    return 0


def cmd_restore(args):
    path = pick(args.package)
    if not args.yes:
        print("恢复会用这份备份覆盖当前数据（身份文件%s写回）：" % (
            "会" if args.with_identity else "不"))
        print("  %s" % path)
        print("确认后请加 --yes；执行前会自动备份当前状态")
        return 1
    actions = backup.restore(path, with_identity=args.with_identity,
                             version=app.VERSION)
    for line in actions:
        print("  %s" % line)
    print("恢复完成：启动服务 ./bin/service.sh start")
    return 0


def cmd_settings(args):
    if args.action == "set":
        if not args.pair:
            print("要写 `段.键=值`，例如 webdav.url=https://dav.example/")
            return 1
        dotted, _, raw = args.pair.partition("=")
        backup.set_setting(dotted.strip(), raw.strip())
        print("已写入 %s" % backup.settings_path())
        return cmd_settings(argparse.Namespace(action="show", pair=None))
    cfg = backup.settings()
    print("设置文件：%s%s" % (backup.settings_path(),
                          "" if os.path.isfile(backup.settings_path()) else "（还没建，下面是默认值）"))
    print("备份目录：%s" % backup.backups_dir())
    for key in ("enabled", "schedule", "retention"):
        print("  %-10s %s" % (key, cfg[key]))
    dav = dict(cfg["webdav"])
    if dav.get("password"):
        dav["password"] = "***"
    print("  %-10s %s" % ("webdav", dav))
    print("  %-10s %s" % ("encrypt", cfg["encrypt"]))
    return 0


def cmd_doctor(args):
    for level, text in backup.doctor():
        print("  [%s] %s" % (level, text))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="backup.py", description="本地备份")
    subs = parser.add_subparsers(dest="cmd", required=True)

    one = subs.add_parser("run", help="立刻做一份备份")
    one.add_argument("--note", default="")
    one.add_argument("--kind", default="manual")
    one.add_argument("--upload", action="store_true", help="顺带推到 WebDAV")
    one.set_defaults(func=cmd_run)

    one = subs.add_parser("list", help="列出备份")
    one.set_defaults(func=cmd_list)

    one = subs.add_parser("auto", help="到点就做一份（服务里也会自己跑）")
    one.set_defaults(func=cmd_auto)

    one = subs.add_parser("prune", help="按留存策略清理")
    one.add_argument("--yes", action="store_true")
    one.set_defaults(func=cmd_prune)

    one = subs.add_parser("webdav", help="WebDAV：测试连接、上传、列远端、清远端")
    one.add_argument("action", nargs="?", default="test",
                     choices=("test", "list", "push", "prune"))
    one.add_argument("package", nargs="?", default="")
    one.set_defaults(func=cmd_webdav)

    one = subs.add_parser("verify", help="校验一份备份")
    one.add_argument("package")
    one.set_defaults(func=cmd_verify)

    one = subs.add_parser("restore", help="从备份恢复（必须先停服务）")
    one.add_argument("package")
    one.add_argument("--with-identity", action="store_true",
                     help="连 config.json 与 assistant_prompt.md 一起写回")
    one.add_argument("--yes", action="store_true")
    one.set_defaults(func=cmd_restore)

    one = subs.add_parser("settings", help="看或改备份设置")
    one.add_argument("action", nargs="?", default="show", choices=("show", "set"))
    one.add_argument("pair", nargs="?", default="")
    one.set_defaults(func=cmd_settings)

    one = subs.add_parser("doctor", help="体检")
    one.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except backup.BackupError as exc:
        print("做不下去：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
