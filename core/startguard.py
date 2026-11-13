# -*- coding: utf-8 -*-
"""崩溃自检：连续三次起不来，下一次自动按安全模式启动。

出事时最难受的是「反复撞同一堵墙」：数据或配置坏掉之后，看护进程隔一秒拉起一次，
每次都在同一处崩掉，日志里堆满同样的回溯，却没人能进面板看一眼。

规矩很简单：**动手之前记一笔「这一次要起了」，真正开始监听之后再记一笔「这一次起来了」**。
中间崩掉就没人写第二笔，下一次启动读到的连续失败次数 +1；连着三次没起来，再下一次就把这一次
切成安全模式，并把 `safe_mode` 写进静态设置——安全模式跳过活动数据的初始化，坏掉的那份东西
不会被碰，人就进得来了。看护进程只管拉起，自检在进程里做，`./bin/run.sh` 手工起也一样生效。

状态记在 `data/startup.json`：

```json
{"attempts": 4, "ok": false, "attemptAt": 1750000000.0, "okAt": 0, "autoAt": 1750000000.0}
```

`attempts` 是**这一次是连续第几次尝试**（含这一次），`ok` 表示最近一次起来过。
写这个文件出错只记日志、不影响启动：自检是为了救人，不能变成新的故障点。
"""
import json
import os
import time

LIMIT = 3               # 连续几次起不来就进安全模式
FILE_NAME = "startup.json"


def path(data_dir):
    return os.path.join(data_dir, FILE_NAME)


def _read(data_dir):
    try:
        with open(path(data_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data_dir, data):
    try:
        os.makedirs(data_dir, exist_ok=True)
        tmp = path(data_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path(data_dir))
        return True
    except OSError:
        return False


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def begin(data_dir):
    """启动前记一笔，返回这一次的判断。

    ``{"attempts": 4, "failures": 3, "auto": True}``：``failures`` 是这一次之前
    已经有几次连着没起来，``auto`` 说这一次要不要按安全模式起。
    """
    before = _read(data_dir)
    try:
        previous = int(before.get("attempts") or 0)
    except (TypeError, ValueError):
        previous = 0
    failures = 0 if before.get("ok") else previous
    attempts = failures + 1
    auto = failures >= LIMIT
    now = time.time()
    auto_at = now if auto else _num(before.get("autoAt"))
    _write(data_dir, {"attempts": attempts, "ok": False, "attemptAt": now,
                      "okAt": _num(before.get("okAt")), "autoAt": auto_at})
    return {"attempts": attempts, "failures": failures, "auto": auto,
            "autoAt": auto_at}


def mark_ok(data_dir):
    """初始化全部过完：这一次算起来了，连续失败次数清零。"""
    now = time.time()
    before = _read(data_dir)
    _write(data_dir, {"attempts": 0, "ok": True, "attemptAt": now,
                      "okAt": now, "autoAt": _num(before.get("autoAt"))})
