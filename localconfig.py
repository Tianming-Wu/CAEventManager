# -*- coding: utf-8 -*-
"""Deployment-level config (``config.json``) and the event-token path segment.

One process serves two things:

    /                       master panel (login, accounts, event list)
    /<event-token>/...      one event (board, screen, admin, checkin)

``path_secret`` is that event token: a random 32-character base64url id generated
once on first run and kept stable in ``config.json``.  It is the only guard on
the event pages, so it must not be guessable, and it must be passed to players
as part of the URL.

Routing never looks at ``public_base_prefix``: the server answers on whatever
path the request arrives by, so local access, the current
``https://lab.nwuca.tech/u/laffey/xlottery`` mount and a future subdomain all
work without a code change.  The prefix is used only to build absolute links
(QR codes, share links, cookie paths) - set it when the deployment moves.

Everything event-related (title, pool, beta, ...) lives in SQLite instead; this
file is only for things that must be known before the database is opened.
"""

import json
import os
import secrets
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("LOTTERY_CONFIG") or os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    "path_secret": "",
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
    "public_base_prefix": "https://lab.nwuca.tech/u/laffey/xlottery",
}

SECRET_LEN = 32


def new_secret():
    """32 URL-safe base64 characters."""
    return secrets.token_urlsafe(24)[:SECRET_LEN]


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

    secret = str(cfg.get("path_secret") or "").strip()
    if not secret or not all(c.isalnum() or c in "-_" for c in secret):
        cfg["path_secret"] = new_secret()
        changed = True
    else:
        cfg["path_secret"] = secret
    if changed:
        save(cfg)
    return cfg


def save(cfg):
    payload = {
        "path_secret": cfg.get("path_secret") or "",
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
    """Full public base URL including the secret segment (no trailing slash)."""
    prefix = (cfg.get("public_base_prefix") or DEFAULTS["public_base_prefix"]).rstrip("/")
    return prefix + "/" + cfg["path_secret"]
