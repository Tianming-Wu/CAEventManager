# -*- coding: utf-8 -*-
"""姓名的拼音：逐字音节与首字母。平台自己用（名单、报名），模块也可以调。"""
import pinyintable
import surnames


def syllables(name):
    """姓名 → 逐字音节。姓那一个字用姓氏读音（曾 = zeng，不是 ceng）。"""
    parts = pinyintable.syllables(name or "")
    surname = surnames.surname_reading(name)
    if surname and parts:
        parts = [surname] + list(parts[1:])
    return parts


def pi(stu):
    """拼音与首字母：填了就用填的，没填按姓名算。"""
    name = stu.get("name") or ""
    parts = syllables(name)
    py = (stu.get("py") or "").strip().lower() or "".join(parts)
    pyi = (stu.get("pyi") or "").strip().lower() or "".join(s[0] for s in parts if s)
    return py, pyi
