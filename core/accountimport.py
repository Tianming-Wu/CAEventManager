# -*- coding: utf-8 -*-
"""批量建号：把导入表解析成一行行账号，按规则生成初始密码，给出预览结论。

这里只做"读懂表、算出每一行该是什么"，建号落库由调用方在事务里完成——
它才知道权限、上限与冲突（本模块要判断登录名是否被占用时，由调用方传一个查询函数）。

表里所有的列都留着：密码规则可以用任意一列拼（`{{学号}}`、`{{学号:6}}`）。
"""

import re

import pinyin
import xlsx

# 列名 → 规范键。中英文表头都认，比较时去空格、统一小写
HEADER_ALIASES = {
    "username": ("登录名", "用户名", "账号", "username", "user"),
    "name": ("显示名", "姓名", "名字", "name", "display"),
    "role": ("角色", "身份", "role"),
    "password": ("初始密码", "密码", "password", "pass"),
    "org": ("组织", "组织id", "组织标识", "org", "organization"),
    "note": ("备注", "说明", "note"),
}

# 生成的密码用的字母表：去掉 0/O、1/l/I 这些看错的
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_LEN = 12

# 界面上的下拉：前几个是预设，最后一个才是自定义规则
RULES = (
    {"id": "random", "label": "随机 12 位", "kind": "random", "template": ""},
    {"id": "sid", "label": "学号原样（不足 8 位补随机）", "kind": "template",
     "template": "{{学号}}"},
    {"id": "sid6", "label": "学号后六位", "kind": "template", "template": "{{学号:6}}"},
    {"id": "pinyin", "label": "姓名拼音加四位随机", "kind": "pinyin", "template": ""},
    {"id": "custom", "label": "自定义规则", "kind": "template", "template": ""},
)

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
GENERATE_REASON = "留空，按规则生成"


def rules():
    """给界面用：下拉框里那几项。"""
    return [dict(item) for item in RULES]


def rule_by_id(rule_id):
    for item in RULES:
        if item["id"] == rule_id:
            return dict(item)
    return dict(RULES[0])


def _rand(n):
    import rng
    return "".join(rng.choice(ALPHABET) for _ in range(n))


def random_password(length=PASSWORD_LEN):
    return _rand(length)


def _pad_password(text):
    """规则拼出来的长度不够 8 位就拿随机字符补齐：短密码进不了库。"""
    text = (text or "").strip()
    return text + _rand(8 - len(text)) if len(text) < 8 else text


def _header_key(text):
    low = str(text or "").strip().lower()
    for key, names in HEADER_ALIASES.items():
        if low in (n.lower() for n in names):
            return key
    return ""


def parse(data, filename=""):
    """导入文件 → 行列表。CSV（UTF-8 带不带 BOM、GBK 都试）与 XLSX 都认。"""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        matrix = xlsx.parse(data)
    else:
        text = ""
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if not text.strip():
            raise ValueError("文件读不出内容：转成 UTF-8 的 CSV 或 xlsx 再试")
        matrix = _csv_rows(text)
    return rows_from_matrix(matrix)


def _csv_rows(text):
    import csv
    import io

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    return [[cell.strip() for cell in row] for row in reader]


def rows_from_matrix(matrix):
    """二维表 → [{"username":…, "name":…, "extras":{列名: 值}}]。第一行是表头。"""
    matrix = [row for row in matrix if any((cell or "").strip() for cell in row)]
    if not matrix:
        return []
    header = matrix[0]
    keys = [_header_key(cell) for cell in header]
    out = []
    for raw in matrix[1:]:
        row = {"username": "", "name": "", "role": "", "password": "", "org": "",
               "note": "", "extras": {}}
        for at, cell in enumerate(raw):
            text = (cell or "").strip()
            if not text:
                continue
            key = keys[at] if at < len(keys) else ""
            if key:
                row[key] = text
            else:
                column = (header[at] or "").strip() if at < len(header) else ""
                if column:
                    row["extras"][column] = text
        if any(row[k] for k in ("username", "name", "password", "note")) or row["extras"]:
            out.append(row)
    return out


def rows_from_client(rows, keep_empty=False):
    """界面上编辑过的行（键已经是规范名）→ 与解析结果同一种形状。

    ``keep_empty=True`` 时连全空的行也留下：界面上刚点 [手动录入] 加的那一行
    本来就只有序号，丢掉它就成了"点一下闪回原样"。
    """
    out = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        row = {"username": str(item.get("username") or "").strip(),
               "name": str(item.get("name") or "").strip(),
               "role": str(item.get("role") or "").strip(),
               "password": str(item.get("password") or "").strip(),
               "org": str(item.get("org") or "").strip(),
               "note": str(item.get("note") or "").strip(),
               "extras": dict(item.get("extras") or {})}
        if keep_empty or any(row[k] for k in ("username", "name", "password", "note")) \
                or row["extras"]:
            out.append(row)
    return out


def render_password(rule, row):
    """按规则给一行算出初始密码，返回 (密码, 说明)。"""
    kind = (rule or {}).get("kind") or "random"
    if kind == "random":
        return random_password(), ""
    if kind == "pinyin":
        parts = pinyin.syllables(row.get("name") or "")
        base = "".join(parts).lower() or "user"
        return _pad_password(base + _rand(4)), ""
    template = (rule or {}).get("template") or "{{学号}}"

    missing = []

    def swap(match):
        expr = match.group(1)
        column, _sep, tail = expr.partition(":")
        value = row.get("extras", {}).get(column.strip()) or _builtin(column.strip(), row)
        if not value:
            missing.append(column.strip())
            return ""
        value = str(value)
        if tail.strip().isdigit():
            value = value[-int(tail.strip()):]
        return value

    rendered = PLACEHOLDER.sub(swap, template)
    if missing:
        return "", "规则里的「%s」这一列是空的" % "、".join(sorted(set(missing)))
    return _pad_password(rendered), ""


def _builtin(column, row):
    """模板里除了表里任意列，还能用这四个固定名。"""
    return {
        "登录名": row.get("username") or "",
        "显示名": row.get("name") or "",
        "角色": row.get("role") or "",
        "备注": row.get("note") or "",
    }.get(column) or ""


def username_seed(name):
    """姓名 → 登录名底子：拼音全拼，取不到就用 user。"""
    parts = pinyin.syllables(name or "")
    base = re.sub(r"[^0-9A-Za-z_-]", "", "".join(parts)).lower()
    return base[:20] or "user"


def unique_username(seed, taken):
    """被占用就加序号；``taken(name)`` 由调用方判断（含同一批里已经发出去的）。"""
    if not taken(seed):
        return seed
    for n in range(2, 1000):
        candidate = "%s%d" % (seed[:20], n)
        if not taken(candidate):
            return candidate
    return "%s%s" % (seed[:16], _rand(4))
