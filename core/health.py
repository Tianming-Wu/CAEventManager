# -*- coding: utf-8 -*-
"""服务有效性：这台机器与这个服务现在的状态，只发警告，不发指令。

面板与活动后台右上角的通知中心会把这里的条目排在真实通知前面。条目是从一次读数**推出来**的：
条件消失，提示跟着消失，不落库、不改任何东西，也不需要谁来确认。

`sticky` 是两类的分界：
- False：占用一类的短期波动，降下来就没了，不必处理
- True：空间一类不会自己好转的，留着直到条件消失
"""

CPU_WARN = 90.0          # %
MEM_WARN = 92.0         # %
DISK_LOW_PCT = 12.0     # 剩余空间占比
DISK_LOW_MB = 500.0     # 或者剩余不到这么多
DISK_OUT_PCT = 3.0      # 到这一步就是快写不进去了
DISK_OUT_MB = 100.0


def _item(sid, level, title, body, sticky=False):
    return {"id": sid, "kind": "service", "level": level, "title": title,
            "body": body, "sticky": bool(sticky)}


def _gb(size):
    return "%.1f GB" % (size / 1073741824.0)


def signals(stats=None, disk=None):
    """按一次读数给出条目。

    stats：host_stats 的结果，可能为 None（非 Linux 读不到，就当没有）
    disk：shutil.disk_usage 的结果（总、已用、剩余），读不到也当没有
    """
    out = []

    cpu = (stats or {}).get("cpu")
    if isinstance(cpu, (int, float)) and cpu >= CPU_WARN:
        out.append(_item(
            "svc:cpu", "info", "服务器 CPU 占用偏高",
            "刚才的读数是 %.0f%%。占用持续偏高时，页面上的操作会变慢。" % cpu))

    mem = (stats or {}).get("mem")
    if isinstance(mem, (int, float)) and mem >= MEM_WARN:
        total = (stats or {}).get("memTotalMB")
        tail = "（共 %.1f GB）" % (total / 1024.0) if total else ""
        out.append(_item(
            "svc:mem", "info", "服务器内存占用偏高",
            "已经用掉 %.0f%%%s。内存紧张时，服务可能被系统结束。" % (mem, tail)))

    total = getattr(disk, "total", None)
    free = getattr(disk, "free", None)
    if total and free is not None:
        pct = 100.0 * free / total
        free_mb = free / 1048576.0
        if pct <= DISK_OUT_PCT or free_mb <= DISK_OUT_MB:
            out.append(_item(
                "svc:disk", "critical", "数据盘空间几乎用尽",
                "只剩 %s（%.0f%%）。写满之后，报名、签到与操作记录都写不进去。"
                % (_gb(free), pct), sticky=True))
        elif pct <= DISK_LOW_PCT or free_mb <= DISK_LOW_MB:
            out.append(_item(
                "svc:disk", "important", "数据盘空间不足",
                "只剩 %s（%.0f%%）。空间用尽后，报名、签到与操作记录都写不进去。"
                % (_gb(free), pct), sticky=True))

    return out
