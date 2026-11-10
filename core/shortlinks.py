# -*- coding: utf-8 -*-
"""短链：一张自动转发的路由表，短链是活动的平台资产。

- 归属：每条短链记在它所属的活动上；活动归档时平台把它的短链一并销毁
- 生成：只有模块申请的 `features.shortlink` 之后，平台接口才会替它生成；
  界面上不提供手动填地址的入口
- 删除：删除即消失，不做软删除，也不写审计——它只是一次跳转，没有复盘价值
- 使用：调用方每次都要按返回的有效性自己判断，短链随时可能被删、过期、用尽次数

表在 `panel.sqlite3` 里：短链要能在不知道活动上下文的时候被解析出来。
"""
import secrets
import time

CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # 去掉 0 O 1 I 这类易混字符
CODE_LEN = 7
PER_EVENT_LIMIT = 5          # 单个活动同时持有的上限，见 localconfig.STATIC_DEFAULTS
TTL_MAX_MIN = 60 * 24 * 30   # 有效期上限一个月；0 表示不设过期
TARGET_MAX = 200             # 目标路径长度上限


class ShortlinkError(Exception):
    """短链操作被拒；消息可以直接给用户看。"""


def _code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def _view(row, now=None):
    now = time.time() if now is None else now
    hits = int(row["hits"] or 0)
    max_hits = int(row["max_hits"] or 0)
    expired = bool(row["expires_at"]) and now >= float(row["expires_at"])
    exhausted = bool(max_hits) and hits >= max_hits
    return {
        "code": row["code"],
        "eventSeq": int(row["event_seq"]),
        "target": row["target"],
        "createdBy": row["created_by"] or "",
        "createdAt": float(row["created_at"] or 0),
        "expiresAt": float(row["expires_at"] or 0),
        "maxHits": max_hits,
        "hits": hits,
        "revoked": bool(row["revoked_at"]),
        "expired": expired,
        "exhausted": exhausted,
        "valid": not (row["revoked_at"] or expired or exhausted),
    }


def create(conn, seq, target, created_by="", minutes=0, max_hits=0, limit=PER_EVENT_LIMIT):
    """给一个活动生成一条短链。超出上限、目标不对都拒绝。"""
    target = (target or "").strip().lstrip("/")
    if not target or len(target) > TARGET_MAX:
        raise ShortlinkError("目标地址不对")
    if "/s/" in "/" + target:
        raise ShortlinkError("短链不能再指向短链")
    minutes = int(minutes or 0)
    if minutes < 0 or minutes > TTL_MAX_MIN:
        raise ShortlinkError("有效期最多 30 天；不设过期请留空")
    max_hits = int(max_hits or 0)
    if max_hits < 0:
        raise ShortlinkError("访问次数上限不能是负数")
    live = conn.execute(
        "SELECT COUNT(*) AS n FROM shortlinks WHERE event_seq=? AND revoked_at=0",
        (seq,)).fetchone()["n"]
    if live >= int(limit):
        raise ShortlinkError("这个活动已经有 %d 条短链了，先删掉一条再生成" % live)
    now = time.time()
    expires_at = now + minutes * 60 if minutes else 0.0
    for _ in range(8):
        code = _code()
        if conn.execute("SELECT 1 FROM shortlinks WHERE code=?", (code,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO shortlinks (code, event_seq, target, created_by, created_at,"
            " expires_at, max_hits, hits, revoked_at) VALUES (?,?,?,?,?,?,?,0,0)",
            (code, seq, target, created_by or "", now, expires_at, max_hits))
        row = conn.execute("SELECT * FROM shortlinks WHERE code=?", (code,)).fetchone()
        return _view(row, now)
    raise ShortlinkError("短链号分配失败了，再试一次")


def resolve(conn, code, now=None):
    """按短链号找目标；无效（不存在 / 过期 / 用尽 / 已删）返回 None。"""
    code = (code or "").strip()
    if not code or len(code) != CODE_LEN:
        return None
    row = conn.execute("SELECT * FROM shortlinks WHERE code=?", (code,)).fetchone()
    if row is None:
        return None
    view = _view(row, now)
    return view if view["valid"] else None


def hit(conn, code):
    """记一次访问。次数上限是硬限制，所以计数与判断在同一处加锁完成。"""
    conn.execute("UPDATE shortlinks SET hits=hits+1 WHERE code=?", (code,))


def list_for(conn, seq, include_dead=True):
    rows = conn.execute(
        "SELECT * FROM shortlinks WHERE event_seq=? ORDER BY created_at DESC",
        (seq,)).fetchall()
    views = [_view(r) for r in rows]
    return views if include_dead else [v for v in views if v["valid"]]


def revoke(conn, code):
    """删除即消失。返回是否真的删掉了一条。"""
    cur = conn.execute("DELETE FROM shortlinks WHERE code=?", (code,))
    return cur.rowcount > 0


def count_for(conn, seq):
    return conn.execute("SELECT COUNT(*) AS n FROM shortlinks WHERE event_seq=?",
                        (seq,)).fetchone()["n"]


def purge_event(conn, seq):
    """活动归档时调用：它的短链由平台销毁。"""
    return conn.execute("DELETE FROM shortlinks WHERE event_seq=?", (seq,)).rowcount


def overview(conn):
    """超管视图：每个活动还活着多少条短链。"""
    rows = conn.execute(
        "SELECT event_seq, COUNT(*) AS n FROM shortlinks"
        " WHERE revoked_at=0 GROUP BY event_seq ORDER BY n DESC").fetchall()
    return [{"eventSeq": int(r["event_seq"]), "count": int(r["n"])} for r in rows]
