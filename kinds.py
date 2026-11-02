# -*- coding: utf-8 -*-
"""Activity kinds ("templates"): what a new activity is allowed to be.

A kind is a directory under ``templates/``::

    templates/<id>/
        template.json    id, name, version, requires, description
        schema.json      the parameters that must be settled before creation
        module.py        (later) the code that draws and plays this kind

Only the metadata is read here.  ``module.py`` is loaded later, after a syntax
check, and only for kinds whose ``requires`` matches this build.
"""
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_-]+)+$")
REQ_RE = re.compile(r"^\s*(>=|<=|>|<|==)\s*([0-9]+(?:\.[0-9]+)*)\s*$")


class KindError(Exception):
    """A kind directory that cannot be used, with a reason for the operator."""


def parse_version(text):
    return tuple(int(p) for p in str(text or "0").split(".") if p.isdigit())


def satisfies(version, spec):
    """``">=1.5,<2"`` against a version like ``1.5.0``."""
    spec = (spec or "").strip()
    if not spec:
        return True
    have = parse_version(version)
    for part in spec.split(","):
        match = REQ_RE.match(part)
        if match is None:
            raise KindError("无法解析的版本约束：%s" % spec)
        op, want = match.group(1), parse_version(match.group(2))
        have_cut = have[:len(want)] or (0,)
        if op == ">=" and not have_cut >= want:
            return False
        if op == "<=" and not have_cut <= want:
            return False
        if op == ">" and not have_cut > want:
            return False
        if op == "<" and not have_cut < want:
            return False
        if op == "==" and not have_cut == want:
            return False
    return True


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_kind(folder, version):
    """Read one kind directory; raises KindError when it is unusable."""
    meta_path = os.path.join(folder, "template.json")
    if not os.path.isfile(meta_path):
        raise KindError("缺少 template.json")
    try:
        meta = _read_json(meta_path)
    except ValueError as exc:
        raise KindError("template.json 不是合法 JSON：%s" % exc)

    kind_id = str(meta.get("id") or "").strip()
    if not ID_RE.match(kind_id):
        raise KindError("id 需要形如 nwuca.lottery")
    if not str(meta.get("name") or "").strip():
        raise KindError("缺少 name")
    requires = str(meta.get("requires") or "").strip()

    schema = []
    schema_path = os.path.join(folder, "schema.json")
    if os.path.isfile(schema_path):
        try:
            schema = _read_json(schema_path).get("fields") or []
        except (ValueError, AttributeError) as exc:
            raise KindError("schema.json 读不出来：%s" % exc)

    fields = []
    for raw in schema:
        key = str(raw.get("key") or "").strip()
        if not key.isalnum() and "_" not in key:
            raise KindError("参数名只能用小写字母、数字和下划线：%s" % key)
        fields.append({
            "key": key,
            "label": str(raw.get("label") or key),
            "type": raw.get("type") or "text",
            "required": bool(raw.get("required")),
            "default": raw.get("default"),
            "help": str(raw.get("help") or ""),
            "options": raw.get("options") or [],
            "min": raw.get("min"),
            "max": raw.get("max"),
        })

    return {
        "id": kind_id,
        "name": str(meta.get("name")),
        "version": str(meta.get("version") or "0"),
        "author": str(meta.get("author") or ""),
        "description": str(meta.get("description") or ""),
        "requires": requires,
        "builtin": bool(meta.get("builtin")),
        "fields": fields,
        "ok": satisfies(version, requires),
        "reason": "" if satisfies(version, requires)
                  else "需要系统版本 %s" % requires,
    }


def coerce_params(kind, given):
    """Validate a submission against the schema; raises ValueError with a reason.

    A required field with no default stays empty until someone fills it in -
    which is the whole point of marking it required.
    """
    out = {}
    given = given or {}
    for field in kind["fields"]:
        key = field["key"]
        label = field["label"]
        raw = given.get(key, field.get("default"))
        if raw is None or raw == "":
            if field["required"]:
                raise ValueError("请填写「%s」" % label)
            continue
        typ = field["type"]
        if typ == "bool":
            if isinstance(raw, str):
                out[key] = raw.strip().lower() in ("1", "true", "on", "yes")
            else:
                out[key] = bool(raw)
        elif typ == "int":
            try:
                val = int(raw)
            except (TypeError, ValueError):
                raise ValueError("「%s」需要是整数" % label)
            low, high = field.get("min"), field.get("max")
            if low is not None and val < low:
                raise ValueError("「%s」不能小于 %s" % (label, low))
            if high is not None and val > high:
                raise ValueError("「%s」不能大于 %s" % (label, high))
            out[key] = val
        elif typ == "select":
            allowed = [str(o.get("value")) for o in (field.get("options") or [])]
            if allowed and str(raw) not in allowed:
                raise ValueError("「%s」只能是 %s"
                                 % (label, " / ".join(allowed)))
            out[key] = str(raw)
        else:
            out[key] = str(raw)[:500]
    return out


def all_kinds(version, include_broken=False):
    """Every kind on disk, in a stable order, with a compatibility verdict."""
    out = []
    if not os.path.isdir(TEMPLATES_DIR):
        return out
    for name in sorted(os.listdir(TEMPLATES_DIR)):
        folder = os.path.join(TEMPLATES_DIR, name)
        if not os.path.isdir(folder):
            continue
        try:
            kind = load_kind(folder, version)
        except KindError as exc:
            if include_broken:
                out.append({"id": name, "name": name, "ok": False,
                            "reason": str(exc), "fields": []})
            continue
        out.append(kind)
    return out


def get(kind_id, version):
    for kind in all_kinds(version):
        if kind["id"] == kind_id:
            return kind
    return None
