# -*- coding: utf-8 -*-
"""本地备份：把实例的数据收成一份 tar.gz，并校验与恢复。

一份备份里放什么
----------------
- **数据**：``panel.sqlite3``、``lists.sqlite3``、每个活动的 ``data.sqlite3`` / ``audit.sqlite3``
  / ``meta.json``、``avatars/``，以及实例自己导入的 ``theme`` 包与 ``modules``
- **身份**（进包但不自动恢复）：``config.json``、``data/assistant_prompt.md``
  换机器重建时用得上，日常恢复不该把端口、前缀、站长身份一起倒回去，所以只有显式
  ``--with-identity`` 才写回
- **不放**：``data/backup_settings.json``（备份功能自己的配置，恢复数据不该回滚它）、
  日志、``service.pid`` / ``service.lock`` / ``supervise.pid`` / ``startup.json``、
  所有 ``-wal`` / ``-shm``、旧的 ``data/backup-*``

库一律用 SQLite 的在线备份 API 取快照：事务一致、不含 WAL、体积就是主干那么大。
归档的活动直接带它的 ``archive.tar.gz``（几十 KB 的冻结件），不再展开成快照。

包里带一份 ``manifest.json``：格式版本、应用版本、时间、每个条目的 sha256 与字节数、
各库的行数、每个活动是快照还是归档包。``verify`` 就是拿它对一遍。
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time

import db
import eventfs
import localconfig

FORMAT = 1
SETTINGS_NAME = "backup_settings.json"
IDENTITY_FILES = ("config.json", "assistant_prompt.md")
SKIP_DIRS = ("__pycache__",)

# 设置文件放数据目录里，位置固定：包按 backups_dir 走，设置要是也跟着走就成了
# "得先读设置才知道设置在哪"
DEFAULTS = {
    "enabled": True,
    "schedule": {"mode": "daily", "at": "03:30"},
    "retention": {"daily": 7, "weekly": 4, "monthly": 6, "maxBytes": 0},
    "webdav": {"enabled": False, "url": "", "username": "", "password": "",
               "path": "/", "verify": True},
    "encrypt": {"enabled": False, "method": "none"},
}


class BackupError(Exception):
    """备份或恢复做不下去；消息可以直接给人看。"""


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
def settings_path():
    return os.path.join(db.data_dir(), SETTINGS_NAME)


def backups_dir():
    """备份包放哪：`config.json` 的 ``backups_dir``，空着就是应用根目录下的 ``backups/``。"""
    return localconfig.static_path(localconfig.load(), "backups_dir", "backups")


def settings():
    """读设置；缺的键按默认值补齐。文件不在就返回默认值，不写盘。"""
    data = {}
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    merged = json.loads(json.dumps(DEFAULTS))
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def save_settings(cfg):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return cfg


def set_setting(dotted, raw):
    """`webdav.url=https://…` 这种写法改一项；值按 JSON 解析，解析不了就当字符串。"""
    if "." not in dotted:
        raise BackupError("要写 `段.键=值` 的形式，例如 webdav.url=https://dav.example/")
    head, key = dotted.split(".", 1)
    if head not in DEFAULTS:
        raise BackupError("没有这一段：%s" % head)
    if not isinstance(DEFAULTS[head], dict):
        raise BackupError("%s 不是一组设置" % head)
    if key not in DEFAULTS[head]:
        raise BackupError("%s 里没有这一项：%s" % (head, key))
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    cfg = settings()
    cfg[head][key] = value
    return save_settings(cfg)


# --------------------------------------------------------------------------- #
# 取快照
# --------------------------------------------------------------------------- #
def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest(), os.path.getsize(path)


def _snapshot(src, dest):
    """一份一致快照。库不在就返回 False（活动可能是归档形态）。

    快照出来的库因为文件头里的 journal_mode 仍是 WAL，开一次就会在盘上留下
    `-wal` / `-shm`：包里不要这两个文件，收尾时删掉。
    """
    if not os.path.isfile(src):
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    source = sqlite3.connect(src, timeout=15.0)
    try:
        out = sqlite3.connect(dest)
        try:
            source.backup(out)
        finally:
            out.close()
    except sqlite3.Error as exc:
        raise BackupError("快照 %s 失败：%s" % (os.path.basename(src), exc))
    finally:
        source.close()
    _drop_side_files(dest)
    return True


def _row_counts(path):
    """各表行数：恢复之后一眼看得出数据对不对。"""
    counts = {}
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                if name.startswith("sqlite_"):
                    continue
                counts[name] = conn.execute(
                    'SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]   # sql-ok 只拼表名，表名来自 sqlite_master
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return counts


def _event_folders():
    """活动号列表：以磁盘为准，面板里那条索引可能与它对不上。"""
    root = db.EVENTS_DIR
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if name.isdigit() and os.path.isdir(os.path.join(root, name)):
            out.append(int(name))
    return out


def _add(tar, stage, arcname, manifest):
    path = os.path.join(stage, arcname)
    if not os.path.isfile(path):
        return
    digest, size = _sha256(path)
    manifest["entries"].append({"name": arcname, "bytes": size, "sha256": digest})
    tar.add(path, arcname=arcname)


def _collect(stage, version, kind):
    """把该进包的东西摊到 stage 里，返回 manifest。"""
    manifest = {"format": FORMAT, "kind": kind, "created": time.time(),
                "version": version, "host": os.uname().nodename
                if hasattr(os, "uname") else "",
                "entries": [], "databases": {}, "events": [], "identity": []}

    for name, label in (("panel.sqlite3", "panel"), ("lists.sqlite3", "lists")):
        dest = os.path.join(stage, "db", name)
        if _snapshot(os.path.join(db.data_dir(), name), dest):
            manifest["databases"][label] = _row_counts(dest)

    for seq in _event_folders():
        folder = os.path.join(db.EVENTS_DIR, str(seq))
        arc = "archive.tar.gz"
        if os.path.isfile(os.path.join(folder, arc)):
            # 归档的活动：带它的冻结件，不展开
            target = os.path.join(stage, "events", str(seq))
            os.makedirs(target, exist_ok=True)
            for name in (arc, "meta.json"):
                src = os.path.join(folder, name)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(target, name))
            manifest["events"].append({"seq": seq, "shape": "archive"})
            continue
        target = os.path.join(stage, "events", str(seq))
        made = False
        for name in ("data.sqlite3", "audit.sqlite3"):
            if _snapshot(os.path.join(folder, name), os.path.join(target, name)):
                made = made or (name == "data.sqlite3")
        src_meta = os.path.join(folder, "meta.json")
        if os.path.isfile(src_meta):
            os.makedirs(target, exist_ok=True)
            shutil.copy2(src_meta, os.path.join(target, "meta.json"))
        if made:
            manifest["events"].append({
                "seq": seq, "shape": "snapshot",
                "rows": _row_counts(os.path.join(target, "data.sqlite3"))})

    # 头像与实例自己导入的外部资源：丢了不好补
    avatars = os.path.join(db.data_dir(), "avatars")
    if os.path.isdir(avatars):
        shutil.copytree(avatars, os.path.join(stage, "files", "avatars"),
                        dirs_exist_ok=True)
    for extra in ("modules",):
        root = os.path.join(db.BASE_DIR, extra)
        if os.path.isdir(root):
            shutil.copytree(root, os.path.join(stage, "files", extra),
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP_DIRS))
    theme_root = os.path.join(db.BASE_DIR, "theme")
    if os.path.isdir(theme_root):
        for name in sorted(os.listdir(theme_root)):
            if name == "classic":          # 随发布走，不用备
                continue
            full = os.path.join(theme_root, name)
            if os.path.isdir(full):
                shutil.copytree(full, os.path.join(stage, "files", "theme", name),
                                dirs_exist_ok=True, ignore=shutil.ignore_patterns(*SKIP_DIRS))

    # 身份信息：进包，但恢复默认不动它们
    os.makedirs(os.path.join(stage, "identity"), exist_ok=True)
    if os.path.isfile(localconfig.CONFIG_PATH):
        shutil.copy2(localconfig.CONFIG_PATH, os.path.join(stage, "identity", "config.json"))
    prompt = os.path.join(db.data_dir(), "assistant_prompt.md")
    if os.path.isfile(prompt):
        shutil.copy2(prompt, os.path.join(stage, "identity", "assistant_prompt.md"))
    manifest["identity"] = [name for name in IDENTITY_FILES
                            if os.path.isfile(os.path.join(stage, "identity", name))]
    return manifest


def run(version="", kind="manual", note=""):
    """做一份备份，返回 (路径, manifest)。"""
    root = backups_dir()
    os.makedirs(root, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    name = "%s-%s.tar.gz" % (stamp, kind)
    target = os.path.join(root, name)
    stage = tempfile.mkdtemp(prefix="backup-")
    try:
        manifest = _collect(stage, version, kind)
        manifest["note"] = note
        # 摊好的东西逐个记账：校验与恢复都靠这份清单
        manifest["entries"] = list(_walk_files(stage))
        with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        tmp = target + ".tmp"
        with tarfile.open(tmp, "w:gz") as tar:
            tar.add(os.path.join(stage, "manifest.json"), arcname="manifest.json")
            for entry in manifest["entries"]:
                tar.add(os.path.join(stage, entry["name"]), arcname=entry["name"])
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return target, manifest


def _walk_files(stage):
    """stage 里该进包的每个文件：算 sha256 与字节数，顺手排掉 WAL 这类旁支。"""
    out = []
    for here, dirs, files in os.walk(stage):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(here, name)
            rel = os.path.relpath(full, stage).replace(os.sep, "/")
            if rel == "manifest.json" or rel.endswith(("-wal", "-shm")):
                continue
            digest, size = _sha256(full)
            out.append({"name": rel, "bytes": size, "sha256": digest})
    out.sort(key=lambda item: item["name"])
    return out


# --------------------------------------------------------------------------- #
# 看与校验
# --------------------------------------------------------------------------- #
def read_manifest(path):
    with tarfile.open(path, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        if member is None:
            raise BackupError("包里没有 manifest.json")
        return json.loads(member.read().decode("utf-8"))


def list_backups():
    """现有备份，按时间从新到旧。"""
    root = backups_dir()
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root), reverse=True):
        if not name.endswith(".tar.gz"):
            continue
        full = os.path.join(root, name)
        entry = {"name": name, "path": full, "bytes": os.path.getsize(full)}
        try:
            entry["manifest"] = read_manifest(full)
        except (BackupError, OSError, ValueError, tarfile.TarError):
            entry["manifest"] = None
        out.append(entry)
    return out


def verify(path):
    """对一遍 manifest：条目齐不齐、sha256 对不对、库能不能打开。返回问题清单。"""
    problems = []
    try:
        manifest = read_manifest(path)
    except (BackupError, OSError, ValueError, tarfile.TarError) as exc:
        return ["读不出 manifest：%s" % exc]
    if manifest.get("format") != FORMAT:
        problems.append("格式版本不认识：%s" % manifest.get("format"))
    seen = set()
    with tarfile.open(path, "r:gz") as tar:
        names = {m.name for m in tar.getmembers() if m.isfile()}
        for entry in manifest.get("entries", []):
            seen.add(entry["name"])
            if entry["name"] not in names:
                problems.append("缺条目：%s" % entry["name"])
                continue
            member = tar.extractfile(entry["name"])
            digest = hashlib.sha256(member.read()).hexdigest() if member else ""
            if digest != entry["sha256"]:
                problems.append("校验对不上：%s" % entry["name"])
        for name in names:
            if name != "manifest.json" and name not in seen:
                problems.append("manifest 里没记这个条目：%s" % name)
    return problems


# --------------------------------------------------------------------------- #
# 恢复
# --------------------------------------------------------------------------- #
def service_running():
    """服务是不是还开着：靠数据目录里的 pidfile 判断。"""
    path = os.path.join(db.data_dir(), "service.pid")
    try:
        with open(path, encoding="utf-8") as fh:
            pid = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _drop_side_files(path):
    """删掉 `-wal` / `-shm`。删不掉就报错：留着它，恢复出来的库会被旧 WAL 盖掉。"""
    for suffix in ("-wal", "-shm"):
        side = path + suffix
        for _ in range(6):
            if not os.path.exists(side):
                break
            try:
                os.remove(side)
            except OSError:
                time.sleep(0.1)
        if os.path.exists(side):
            raise BackupError("还有进程占着 %s：先把服务停掉再来"
                              % os.path.basename(side))


def restore(path, with_identity=False, version=""):
    """从一份备份恢复数据。调用前必须停服务（这里只做检查，不停服务）。

    返回动作清单（打印给运维看）。
    """
    if service_running():
        raise BackupError("服务还在跑：先 ./bin/service.sh stop 再恢复")
    problems = verify(path)
    if problems:
        raise BackupError("这份备份没通过校验：\n  " + "\n  ".join(problems))
    actions = []
    # 恢复之前先把当下的状态收一份：恢复本身也是一次破坏性操作
    safety, _ = run(version=version, kind="pre-restore")
    actions.append("已把当前状态备份到 %s" % safety)

    stage = tempfile.mkdtemp(prefix="restore-")
    try:
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(stage)
        data = db.data_dir()

        # 库：先铺新的，再清掉旁边可能残留的 WAL
        for name in ("panel.sqlite3", "lists.sqlite3"):
            src = os.path.join(stage, "db", name)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(data, name)
            os.makedirs(data, exist_ok=True)
            shutil.copy2(src, dest)
            _drop_side_files(dest)
            actions.append("换上 %s" % name)

        manifest = read_manifest(path)
        for item in manifest.get("events", []):
            seq = item["seq"]
            src = os.path.join(stage, "events", str(seq))
            dest = os.path.join(db.EVENTS_DIR, str(seq))
            if not os.path.isdir(src):
                continue
            os.makedirs(dest, exist_ok=True)
            for name in sorted(os.listdir(src)):
                full = os.path.join(src, name)
                if not os.path.isfile(full):
                    continue
                if name.endswith("-wal") or name.endswith("-shm"):
                    continue
                shutil.copy2(full, os.path.join(dest, name))
            if item["shape"] == "snapshot":
                # 换成快照形态：归档包得拿掉，否则会同时存在两种形态
                packed = os.path.join(dest, eventfs.ARCHIVE_NAME)
                if os.path.isfile(packed):
                    os.remove(packed)
                for name in eventfs.DB_FILES:
                    _drop_side_files(os.path.join(dest, name))
            else:
                for name in ("data.sqlite3", "audit.sqlite3"):
                    live = os.path.join(dest, name)
                    if os.path.isfile(live):
                        os.remove(live)
                    _drop_side_files(live)
            actions.append("活动 #%d（%s）" % (seq, "归档包" if item["shape"] == "archive"
                                              else "快照"))

        files = os.path.join(stage, "files")
        if os.path.isdir(os.path.join(files, "avatars")):
            dest = os.path.join(data, "avatars")
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(os.path.join(files, "avatars"), dest)
            actions.append("换上 avatars/")
        for extra in ("modules", "theme"):
            src = os.path.join(files, extra)
            if os.path.isdir(src):
                dest = os.path.join(db.BASE_DIR, extra)
                os.makedirs(dest, exist_ok=True)
                for name in sorted(os.listdir(src)):
                    shutil.copytree(os.path.join(src, name), os.path.join(dest, name),
                                    dirs_exist_ok=True)
                actions.append("换上 %s/" % extra)

        # 恢复旧库等于把旧登录态一起带回来：清掉会话与提权，所有人重新登录
        panel_db = os.path.join(data, "panel.sqlite3")
        if os.path.isfile(panel_db):
            conn = sqlite3.connect(panel_db)
            try:
                for table in ("sessions", "event_sudo", "super_grants"):
                    try:
                        conn.execute("DELETE FROM %s" % table)   # sql-ok 只拼表名，来自上面这张常量清单
                    except sqlite3.Error:
                        pass
                conn.commit()
            finally:
                conn.close()
            actions.append("清掉登录会话（所有人要重新登录）")

        if with_identity:
            for name in IDENTITY_FILES:
                src = os.path.join(stage, "identity", name)
                if not os.path.isfile(src):
                    continue
                dest = (localconfig.CONFIG_PATH if name == "config.json"
                        else os.path.join(data, name))
                shutil.copy2(src, dest)
                actions.append("换上身份文件 %s" % name)
        else:
            actions.append("身份文件（config.json、assistant_prompt.md）没动；"
                           "要写回加 --with-identity")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return actions


# --------------------------------------------------------------------------- #
# 体检
# --------------------------------------------------------------------------- #
def doctor():
    """恢复与备份之前值得看一眼的几件事，返回 [(级别, 说明)]。"""
    out = []
    root = backups_dir()
    if not os.path.isdir(root):
        out.append(("注意", "还没有备份目录：%s" % root))
    else:
        found = list_backups()
        if not found:
            out.append(("注意", "备份目录里还没有备份"))
        else:
            newest = found[0]
            age = (time.time() - newest["manifest"]["created"]) / 86400.0 \
                if newest.get("manifest") else None
            if age is not None and age > 2:
                out.append(("注意", "最近一份备份是 %.1f 天前的" % age))
    conn = None
    try:
        conn = sqlite3.connect(os.path.join(db.data_dir(), "panel.sqlite3"), timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT seq, title FROM events ORDER BY seq").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        if conn is not None:
            conn.close()
    for row in rows:
        seq = row["seq"]
        folder = os.path.join(db.EVENTS_DIR, str(seq))
        if not os.path.isdir(folder):
            out.append(("问题", "活动 #%d 没有目录：%s" % (seq, folder)))
            continue
        packed = eventfs.is_archived(seq)
        live = eventfs.has_data(seq)
        if not packed and not live:
            out.append(("问题", "活动 #%d 既没有业务库也没有归档包" % seq))
        elif packed and live:
            out.append(("问题", "活动 #%d 归档包与业务库同时存在" % seq))
        counts = _row_counts(os.path.join(folder, "data.sqlite3")) if live else {}
        if live and counts and not any(counts.get(k) for k in ("students", "marks", "tickets")):
            out.append(("提示", "活动 #%d「%s」没有人员数据，像是空活动"
                        % (seq, row["title"] or "")))
    if not out:
        out.append(("正常", "没发现问题"))
    return out
