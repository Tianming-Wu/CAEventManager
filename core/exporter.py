# -*- coding: utf-8 -*-
"""导出：模块给表，平台打包。"""
import io
import re
import time
import zipfile

import db
import errors
import model


def module_sheets(conn, cfg=None):
    """模块说要导哪些表。

    每项是 ``{"name": "01-汇总.csv", "text": "...", "primary": True}``；
    ``primary`` 标记的那一张会单独当"仅名单 CSV"下载。模块没实现就一张也没有。
    """
    mod = model.current()
    build = getattr(mod, "export_sheets", None)
    if not callable(build):
        return []
    cfg = cfg or db.all_config(conn)
    out = []
    for item in build(conn, cfg) or []:
        item = item or {}
        name = str(item.get("name") or "").strip()
        text = item.get("text")
        if not name or not isinstance(text, str):
            continue
        out.append({"name": name, "text": text, "primary": bool(item.get("primary"))})
    return out


def _safe_name(text, fallback="activity"):
    name = re.sub(r'[\\/:*?"<>|]+', "_", str(text or "")).strip()
    return name[:40] or fallback


def build(conn, cfg=None):
    """Return (文件名, zip 字节)。表由模块给，平台只负责打包与命名。"""
    cfg = cfg or db.all_config(conn)
    items = module_sheets(conn, cfg)
    if not items:
        raise errors.ApiError("这个活动没有可导出的内容", 404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            # 只有 CSV 带 BOM（给 Excel 看中文用），纯文本日志不带
            encoding = "utf-8-sig" if item["name"].endswith(".csv") else "utf-8"
            zf.writestr(item["name"], item["text"].encode(encoding))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return "%s-%s.zip" % (_safe_name(cfg.get("title")), stamp), buf.getvalue()


def primary_csv(conn, cfg=None):
    """"仅名单 CSV"那颗按钮要的那一张：模块标 primary 的，没有就第一张 CSV。"""
    items = module_sheets(conn, cfg)
    csv_items = [i for i in items if i["name"].endswith(".csv")]
    for item in csv_items:
        if item["primary"]:
            return item["name"], item["text"]
    if csv_items:
        return csv_items[0]["name"], csv_items[0]["text"]
    return "", ""
