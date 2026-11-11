# -*- coding: utf-8 -*-
"""Deployment-level config (``config.json``) and the event-token path segment.

One process serves two things:

    /                       master panel (login, accounts, event list)
    /<event-token>/...      one event (board, screen, admin, checkin)

每个活动自己的 token 存在 ``data/panel.sqlite3`` 的 events 表里，不在这个文件里：
这里只放服务层配置（监听地址、对外前缀、站长身份）与必须重启才生效的静态设置。

Routing never looks at ``public_base_prefix``: the server answers on whatever
path the request arrives by, so local access, the current
the mount it is published under and a future subdomain all work without a code
change.  The prefix is used only to build absolute links
(QR codes, share links, cookie paths) - set it when the deployment moves.

Everything event-related (title, pool, beta, ...) lives in SQLite instead; this
file is only for things that must be known before the database is opened.
"""

import json
import os
import paths
import base64
import rng
import tempfile

BASE_DIR = paths.APP_ROOT
CONFIG_PATH = os.environ.get("LOTTERY_CONFIG") or os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8931,
    # Who the owner signs in as, and what the console calls them.  The two are
    # separate on purpose: the display name is what anyone looking at the screen
    # sees, the login name is what actually opens the account.
    "owner_username": "owner",
    "owner_name": "站长",
    # Where the service is published.  Used ONLY to build links (QR codes, short
    # links, cookie paths).  Routing does not depend on it: the app answers on
    # whatever path the request arrives by, so local access always works and a
    # future subdomain is a config change, not a code change.
    "public_base_prefix": "http://127.0.0.1:8931",
}

# 静态设置：改配置文件后重启生效，界面上没有入口。
# 单个活动同时能持有多条短链，超出就拒绝生成。
STATIC_DEFAULTS = {
    "shortlink_per_event": 5,
    # 服务安全模式：启动时生效，外部资源不加载、活动内数据只读
    "safe_mode": False,
}

SECRET_LEN = 32


def new_secret():
    """32 URL-safe base64 characters."""
    return base64.urlsafe_b64encode(rng.bytes(24)).decode()[:SECRET_LEN]


def raw():
    """The file exactly as written, without defaults filled in."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load():
    cfg = dict(DEFAULTS)
    changed = False
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if v is not None})
        except (OSError, ValueError):
            pass
    # which of these the operator actually wrote down, as opposed to defaults
    cfg["_explicit"] = set(c for c in raw() if c in DEFAULTS)

    # 静态设置只有写在文件里才生效：缺的补默认值，但不回写，
    # 这样文件里始终只留运维真正决定过的项
    for key, value in STATIC_DEFAULTS.items():
        cfg.setdefault(key, value)
    if changed:
        save(cfg)
    return cfg


def save(cfg):
    payload = {
        "host": cfg.get("host") or DEFAULTS["host"],
        "port": int(cfg.get("port") or DEFAULTS["port"]),
        "public_base_prefix": (cfg.get("public_base_prefix") or DEFAULTS["public_base_prefix"]).rstrip("/"),
        "owner_username": (cfg.get("owner_username") or DEFAULTS["owner_username"]).strip(),
        "owner_name": (cfg.get("owner_name") or DEFAULTS["owner_name"]).strip(),
    }
    directory = os.path.dirname(CONFIG_PATH) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return payload


def public_url(cfg):
    """配置里的对外前缀（不含活动 token，那一节由活动自己带）。"""
    return (cfg.get("public_base_prefix") or DEFAULTS["public_base_prefix"]).rstrip("/")


def static_limit(cfg, key):
    """静态设置取整；写错了回落到默认值。"""
    try:
        return int(cfg.get(key, STATIC_DEFAULTS.get(key, 0)))
    except (TypeError, ValueError):
        return int(STATIC_DEFAULTS.get(key, 0))
