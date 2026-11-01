# -*- coding: utf-8 -*-
"""Shared helper for the test/dev tools: build the secret URL prefix.

The service only answers under ``/<secret>/...`` where ``<secret>`` is the random
32-character id in ``config.json``.  Tools therefore take a plain base URL and
this helper appends the secret segment (unless it is already there).
"""
import json
import os
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


def secret():
    return str(load_local_config().get("path_secret") or "")


def api_base(url):
    """Return ``url`` with the secret segment appended when missing."""
    url = (url or "").rstrip("/")
    sec = secret()
    if not sec:
        return url
    path = urllib.parse.urlparse(url).path
    if ("/" + sec) in (path.rstrip("/") + "/"):
        return url
    return url + "/" + sec
