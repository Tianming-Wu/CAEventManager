# -*- coding: utf-8 -*-
"""测试与开发工具的共享件：把活动地址段拼出来。

活动在地址里用 token，内部用活动号；两者都在 ``data/panel.sqlite3`` 的 events 表里。
工具拿到一个裸地址，这里补上第一个活动的 token。
"""
import json
import os
import sqlite3
import urllib.parse

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path():
    return os.environ.get("LOTTERY_CONFIG") or os.path.join(APP_DIR, "config.json")


def load_local_config():
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def data_dir():
    return os.environ.get("LOTTERY_DATA_DIR") or os.path.join(APP_DIR, "data")


def first_token():
    """第一个活动的 token；库里没有活动时返回空串。"""
    path = os.path.join(data_dir(), "panel.sqlite3")
    try:
        conn = sqlite3.connect(path, timeout=5.0)
    except sqlite3.Error:
        return ""
    try:
        row = conn.execute("SELECT token FROM events ORDER BY seq LIMIT 1").fetchone()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    return row[0] if row else ""


def api_base(url):
    """返回 ``url`` 补上活动 token 之后的地址（已经带了就不重复补）。"""
    url = (url or "").rstrip("/")
    token = first_token()
    if not token:
        return url
    path = urllib.parse.urlparse(url).path
    if ("/" + token) in (path.rstrip("/") + "/"):
        return url
    return url + "/" + token
