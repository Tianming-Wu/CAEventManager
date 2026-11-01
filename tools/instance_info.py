#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print this instance's access id and admin password as KEY=value lines.

    python3 tools/instance_info.py

Meant for scripts (`secret=$(...)`), and as the answer to "what is my admin
password again" without opening config.json by hand.
"""
import json
import os

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(here)

try:
    cfg = json.load(open("config.json", encoding="utf-8"))
except (OSError, ValueError):
    cfg = {}

print("secret=" + (cfg.get("path_secret") or ""))
print("port=" + str(cfg.get("port") or 8931))
prefix = (cfg.get("public_base_prefix") or "").rstrip("/")
secret = cfg.get("path_secret") or ""
if prefix and secret:
    base = prefix + "/" + secret
    print("board=" + base + "/")
    print("screen=" + base + "/screen")
    print("admin=" + base + "/admin")

try:
    for line in open("data/admin_password.txt", encoding="utf-8"):
        if line.startswith("password:"):
            print("password=" + line.split(":", 1)[1].strip())
        elif line.startswith("username:"):
            print("username=" + line.split(":", 1)[1].strip())
except OSError:
    pass
