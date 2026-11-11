# -*- coding: utf-8 -*-
"""活动文件夹的归档、还原、打包与删除。

一个活动就是一个文件夹（`data/events/<活动号>/`）：业务库、审计日志与元数据都在里面。

- **归档**（活动负责人对自己活动的软删除）：把文件夹压成 `archive.tar.gz`，删掉业务库与审计库，
  只留元数据与这个包。归档之后不再保证活动能打开。
- **还原**（超管提权）：把包解开，活动回到可打开的状态，负责人就能下载数据、再归档一次。
- **打包**：活动正常开启时导出业务库与审计库；已归档时导出整个文件夹（含归档包）。
- **删除**（超管提权）：整个文件夹删掉，不可逆。
"""
import io
import os
import shutil
import sqlite3
import tarfile
import time
import zipfile

import db

ARCHIVE_NAME = "archive.tar.gz"
DB_FILES = ("data.sqlite3", "data.sqlite3-wal", "data.sqlite3-shm",
            "audit.sqlite3", "audit.sqlite3-wal", "audit.sqlite3-shm")


class EventFsError(Exception):
    """归档相关的操作被拒；消息可以直接给用户看。"""


def _folder(seq):
    return db.event_dir(seq)


def has_data(seq):
    """活动现在还能打开吗：业务库在就算在。"""
    return os.path.isfile(db.event_path(seq))


def is_archived(seq):
    return os.path.isfile(os.path.join(_folder(seq), ARCHIVE_NAME))


def _checkpoint(seq):
    """归档前把 WAL 落盘并关掉，免得打包时漏掉还没写的页。"""
    for path in (db.event_path(seq), db.audit_path(seq)):
        if not os.path.isfile(path):
            continue
        try:
            conn = sqlite3.connect(path, timeout=15.0, isolation_level=None)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except sqlite3.Error:
            pass


def archive(seq):
    """把活动打包，只留元数据与归档包。返回归档包的大小。"""
    folder = _folder(seq)
    if not os.path.isdir(folder):
        raise EventFsError("活动目录不存在")
    if is_archived(seq) and not has_data(seq):
        return os.path.getsize(os.path.join(folder, ARCHIVE_NAME))
    _checkpoint(seq)
    db.write_meta(seq, archivedAt=time.time())
    target = os.path.join(folder, ARCHIVE_NAME)
    names = [n for n in os.listdir(folder)
             if n in DB_FILES or n == "meta.json"]
    if not names:
        raise EventFsError("这个活动没有可归档的文件")
    # 先写临时文件再改名：半截的包比没有包更糟
    tmp = target + ".tmp"
    with tarfile.open(tmp, "w:gz") as tar:
        for name in sorted(names):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                tar.add(full, arcname=name)
    os.replace(tmp, target)
    # 库文件可能还被本进程的连接占着：先关掉再删，删不掉就再等一会儿
    db.close_event_connections(seq)
    for name in DB_FILES:
        full = os.path.join(folder, name)
        for attempt in range(6):
            try:
                if os.path.isfile(full):
                    os.remove(full)
                break
            except OSError:
                if attempt == 5:
                    raise EventFsError("有连接还占着 %s，稍后再试" % name)
                time.sleep(0.15)
    return os.path.getsize(target)


def restore(seq):
    """从归档包里把活动解回来。已经解开的直接返回。"""
    folder = _folder(seq)
    target = os.path.join(folder, ARCHIVE_NAME)
    if has_data(seq):
        return False
    if not os.path.isfile(target):
        raise EventFsError("这个活动没有归档包，无法还原")
    with tarfile.open(target, "r:gz") as tar:
        for member in tar.getmembers():
            name = os.path.basename(member.name)
            if name not in DB_FILES and name != "meta.json":
                continue
            if not member.isfile():
                continue
            src = tar.extractfile(member)
            if src is None:
                continue
            with open(os.path.join(folder, name), "wb") as out:
                shutil.copyfileobj(src, out)
    db.write_meta(seq, archivedAt=0)
    return True


def package(seq, full=False):
    """打成一份 zip 交给下载。

    full=False：只导出数据（业务库、审计库、元数据），给负责人与参与者；
    full=True ：连同归档包一起导出，给超管下载已归档的活动。
    """
    folder = _folder(seq)
    if not os.path.isdir(folder):
        raise EventFsError("活动目录不存在")
    if full:
        names = sorted(n for n in os.listdir(folder)
                       if os.path.isfile(os.path.join(folder, n)))
    else:
        if not has_data(seq):
            raise EventFsError("活动已归档，数据由管理员保管")
        _checkpoint(seq)
        names = [n for n in sorted(os.listdir(folder))
                 if os.path.isfile(os.path.join(folder, n))
                 and (n in DB_FILES or n == "meta.json")]
    if not names:
        raise EventFsError("没有可导出的文件")
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            full_path = os.path.join(folder, name)
            if os.path.isfile(full_path) and not name.endswith("-shm"):
                zf.write(full_path, arcname=name)
    blob.seek(0)
    return blob.getvalue()


def delete(seq):
    """彻底删除一个活动的文件夹。调用方负责确认它已经归档。"""
    folder = _folder(seq)
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder)
    return True
