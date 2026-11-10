# -*- coding: utf-8 -*-
"""展示屏幕包：把一个上传的 zip 变成一组静态文件。

大屏是可替换的插接件，所以要接收外部文件；上传是常见攻击点，因此校验全部集
中在这里，原则只有一条：**先让 zip 变成"一串相对路径 + 字节"，再由我们自己
建目录写盘**，从不把压缩包交给解压器去决定磁盘上出现什么。

下面这些限制是服务端的静态配置，改了需要重启：

* ``EXT_ALLOWED``  允许的扩展名；``PAGE`` 那一份 html 另外单独放行
* ``ZIP_MAX``      上传的 zip 本体上限
* ``FILE_MAX`` / ``TOTAL_MAX`` / ``COUNT_MAX``  解压后单文件、总量、条目数上限
* ``RATIO_MAX``    单个条目的压缩比上限（压缩炸弹）
* ``DEPTH_MAX`` / ``SEG_MAX`` / ``PATH_MAX``    目录层数与路径长度上限

条目名逐段只允许 ``[A-Za-z0-9._-]``，不接受 ``..``、绝对路径、盘符、备用数据
流（``:``）、符号链接；不认得的条目丢弃并回报，认得的才落盘。
"""

import io
import os
import paths
import re
import shutil
import stat as statmod
import tempfile
import time
import zipfile

BASE_DIR = paths.APP_ROOT
# 导入的屏幕放在实例目录里（内置的只读，随发布走）。测试用 LOTTERY_PACKS_DIR 另开
# 一处，免得跑一遍测试就往工作区里塞东西。
INSTANCE = (os.environ.get("LOTTERY_PACKS_DIR")
            or os.path.join(BASE_DIR, "screen_packs"))
BUILTIN = os.path.join(BASE_DIR, "builtin", "screen_packs")

# 入口页固定；活动页按 /<token>/screen 取它
PAGE = "screen.html"

# 白名单：包里允许出现的扩展名（小写，含点）
EXT_ALLOWED = (".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".webp")

ZIP_MAX = 2 * 1024 * 1024
FILE_MAX = 2 * 1024 * 1024
TOTAL_MAX = 8 * 1024 * 1024
COUNT_MAX = 80
RATIO_MAX = 200
DEPTH_MAX = 3
SEG_MAX = 60
PATH_MAX = 120
ID_MAX = 40

SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class PackError(ValueError):
    """Something about the upload is not acceptable; the message is for people."""


def size_text(num):
    """A readout people can compare against the numbers in the dialog."""
    num = int(num or 0)
    if num >= 1024 * 1024:
        return "%.1f MB" % (num / 1024.0 / 1024.0)
    if num >= 1024:
        return "%.0f KB" % (num / 1024.0)
    return "%d B" % num


def limits():
    """What the two sides must agree on, shipped to the upload dialog as-is."""
    return {"page": PAGE, "ext": list(EXT_ALLOWED), "zipMax": ZIP_MAX,
            "fileMax": FILE_MAX, "totalMax": TOTAL_MAX, "countMax": COUNT_MAX,
            "depthMax": DEPTH_MAX, "zipText": size_text(ZIP_MAX),
            "fileText": size_text(FILE_MAX), "totalText": size_text(TOTAL_MAX)}


# ------------------------------------------------------------------ 校验 ---
def _segments(raw):
    """A zip entry name as path segments, or None when it may not be written."""
    if not raw or "\\" in raw or "\x00" in raw or ":" in raw:
        return None
    if raw.startswith("/") or raw.startswith("."):
        # hidden or dot-leading entries are never part of a pack
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or len(parts) > DEPTH_MAX:
        return None
    for part in parts:
        if part == ".." or len(part) > SEG_MAX or not SEGMENT.match(part):
            return None
        if part.strip(".") == "":
            return None
    if len("/".join(parts)) > PATH_MAX:
        return None
    return parts


def check_zip(blob):
    """(files, skipped): ``{relative path: bytes}`` plus what was dropped.

    Everything that could refuse the whole upload is raised here, before a single
    byte reaches the disk.
    """
    if not blob:
        raise PackError("上传的文件为空")
    if len(blob) > ZIP_MAX:
        raise PackError("压缩包超过 %s" % size_text(ZIP_MAX))
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        raise PackError("这不是一个可以读取的 zip 压缩包")

    files, skipped, total = {}, [], 0
    try:
        members = archive.infolist()
        if len(members) > COUNT_MAX * 4:
            # 目录条目也占名额，所以先给一个宽松的总数，真正的上限按文件算
            raise PackError("压缩包内条目过多")
        for info in members:
            name = info.filename or ""
            if info.is_dir() or name.endswith("/"):
                continue
            mode = info.external_attr >> 16
            if statmod.S_ISLNK(mode):
                skipped.append(name)
                continue
            parts = _segments(name)
            if parts is None:
                skipped.append(name)
                continue
            rel = "/".join(parts)
            ext = os.path.splitext(parts[-1])[1].lower()
            if rel != PAGE and ext not in EXT_ALLOWED:
                skipped.append(name)
                continue
            if rel in files:
                raise PackError("压缩包内有重名文件：%s" % rel)
            if len(files) >= COUNT_MAX:
                raise PackError("文件数超过 %d 个" % COUNT_MAX)
            if info.file_size > FILE_MAX:
                raise PackError("%s 超过单文件上限 %s" % (rel, size_text(FILE_MAX)))
            if (info.compress_size and info.file_size > 65536
                    and info.file_size / float(info.compress_size) > RATIO_MAX):
                raise PackError("%s 的压缩比异常，已拒绝解压" % rel)
            # 头部写的大小不作数：只读到上限多一个字节，多出来就是超了
            try:
                with archive.open(info) as handle:
                    data = handle.read(FILE_MAX + 1)
            except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
                raise PackError("%s 无法解开" % rel)
            if len(data) > FILE_MAX:
                raise PackError("%s 超过单文件上限 %s" % (rel, size_text(FILE_MAX)))
            total += len(data)
            if total > TOTAL_MAX:
                raise PackError("解压后总量超过 %s" % size_text(TOTAL_MAX))
            files[rel] = data
    finally:
        archive.close()

    if PAGE not in files:
        raise PackError("压缩包里没有 %s" % PAGE)
    return files, skipped


# ------------------------------------------------------------------ 落盘 ---
def slug(text):
    """A usable pack id from a file name: lower case, letters/digits/._- only."""
    name = os.path.basename(str(text or "")).strip()
    name = re.sub(r"\.zip$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._").lower()
    return name[:ID_MAX].strip("-._")


def _taken(root):
    """Ids already in use, built-in ones included: an upload never shadows them."""
    out = set()
    for base in (root, BUILTIN):
        if os.path.isdir(base):
            try:
                out.update(os.listdir(base))
            except OSError:
                pass
    return out


def unique_id(hint, root=None):
    root = root or INSTANCE
    base = slug(hint) or "pack"
    taken = _taken(root)
    if base not in taken:
        return base
    for n in range(2, 100):
        stem = base[:ID_MAX - 4].strip("-._") or "pack"
        cand = "%s-%d" % (stem, n)
        if cand not in taken:
            return cand
    return "%s-%d" % (base[:ID_MAX - 12].strip("-._") or "pack", int(time.time()))


def install(blob, hint="", root=None):
    """Check the upload, then write it as ``<root>/<id>/``.  Never overwrites.

    Files land in a staging directory inside ``root`` first and the whole pack is
    renamed into place, so a failure half way through cannot leave a pack that
    looks complete (the入口页 missing is how a half-written pack shows up).
    """
    root = root or INSTANCE
    files, skipped = check_zip(blob)
    os.makedirs(root, exist_ok=True)
    pack_id = unique_id(hint, root)
    staging = tempfile.mkdtemp(prefix=".incoming-", dir=root)
    try:
        for rel in sorted(files):
            full = os.path.join(staging, *rel.split("/"))
            parent = os.path.dirname(full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(full, "wb") as handle:
                handle.write(files[rel])
        try:
            os.rename(staging, os.path.join(root, pack_id))
        except OSError:
            # 另一个会话刚好占了这个标识，或者这个文件系统不收这个名字
            raise PackError("导入没有完成，请重试")
        staging = None
    except OSError:
        raise PackError("写入文件失败，请检查磁盘空间")
    finally:
        if staging and os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
    return {"id": pack_id, "files": len(files),
            "size": sum(len(v) for v in files.values()), "skipped": skipped}


def remove(pack_id, root=None):
    """Delete one imported pack.  Built-in ones are not ours to delete."""
    root = root or INSTANCE
    pack_id = str(pack_id or "").strip()
    if not pack_id or slug(pack_id) != pack_id:
        raise PackError("标识不合法")
    full = os.path.join(root, pack_id)
    if not os.path.isdir(full):
        if os.path.isdir(os.path.join(BUILTIN, pack_id)):
            raise PackError("内置屏幕不能删除")
        raise PackError("找不到这一项")
    real_root = os.path.realpath(root)
    real_full = os.path.realpath(full)
    if os.path.dirname(real_full) != real_root:
        raise PackError("标识不合法")
    shutil.rmtree(real_full)
    return True


# ------------------------------------------------------------------ 清单 ---
def measure(folder):
    """How much is in one pack: file count, total bytes, newest stamp."""
    count, size, stamp = 0, 0, 0
    for base, _dirs, names in os.walk(folder):
        for name in names:
            try:
                info = os.stat(os.path.join(base, name))
            except OSError:
                continue
            count += 1
            size += info.st_size
            stamp = max(stamp, info.st_mtime)
    return {"files": count, "size": size, "at": stamp}


def listing(root=None):
    """Usable packs: the instance directory first, built-ins filling the rest."""
    root = root or INSTANCE
    seen, out = set(), []
    for base, builtin in ((root, False), (BUILTIN, True)):
        if not os.path.isdir(base):
            continue
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            folder = os.path.join(base, name)
            if name in seen or not os.path.isfile(os.path.join(folder, PAGE)):
                continue
            seen.add(name)
            info = measure(folder)
            out.append({"id": name, "name": name, "builtin": builtin,
                        "source": "内置" if builtin else "已导入",
                        "files": info["files"], "size": info["size"],
                        "sizeText": size_text(info["size"]), "at": info["at"]})
    return out
