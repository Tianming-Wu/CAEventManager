# -*- coding: utf-8 -*-
"""Activity modules: the capability boundary of one activity form.

A module is a directory::

    <module>/module.json     id, name, version, author, requires, description
    <module>/schema.json     the parameters that must be settled before creation
    <module>/templates/*     presets that ship with this module
    <module>/module.py       (later) the code that draws and plays this kind

Two places are searched, in this order:

* ``builtin/modules/<id>`` - shipped with the repository, part of the
  release, updated by deploying;
* ``modules/<id>`` - installed on this instance by an administrator, kept out
  of the repository.

A module is a boundary: enabling one is what makes its settings reachable at
all.  A preset narrows that boundary further - it is one worked-out configuration
inside the module, so the next event does not start from an empty form.

Only the metadata is read here.  ``module.py`` is loaded later, after a syntax
check, and only for modules whose ``requires`` matches this build.
"""
import json
import os
import paths
import re

BASE_DIR = paths.APP_ROOT
# shipped with the release, then whatever this instance installed itself
BUILTIN_DIR = os.path.join(BASE_DIR, "builtin")
BUILTIN_MODULES_DIR = os.path.join(BUILTIN_DIR, "modules")
INSTANCE_MODULES_DIR = os.path.join(BASE_DIR, "modules")

ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_-]+)+$")
REQ_RE = re.compile(r"^\s*(>=|<=|>|<|==)\s*([0-9]+(?:\.[0-9]+)*)\s*$")


class KindError(Exception):
    """A module directory that cannot be used, with a reason for the operator."""


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


# The pages a kind may ask for.  Anything else is the platform's, not the module's,
# so a module cannot invent a page the console has no section for.
MODULE_PAGES = ("ops", "roster", "tickets", "draw", "config")
PLATFORM_PAGES = (("settings", "\u6d3b\u52a8\u8bbe\u7f6e"), ("log", "\u65e5\u5fd7"))
# The public pages the core can draw.  A module declares which of them it uses;
# anything undeclared is a 404 for that activity.
DEFAULT_EXTERNAL = ("board", "screen", "checkin", "admin")
EXTERNAL_TITLE = {
    "board": "\u516c\u793a\u9875",
    "screen": "\u5927\u5c4f",
    "checkin": "\u7b7e\u5230",
    "admin": "\u63a7\u5236\u53f0",
}

PAGE_TITLE = {
    "ops": "\u64cd\u4f5c\u53f0",
    "roster": "\u540d\u5355",
    "tickets": "\u5956\u7968",
    "draw": "\u62bd\u5956",
    "config": "\u5956\u7968\u8bbe\u7f6e",
}


def normalize_pages(raw):
    """Turn a module's page list into [{"id", "title"}], dropping what we cannot draw."""
    out = []
    seen = set()
    for item in raw or []:
        if isinstance(item, str):
            page_id, title = item, ""
        elif isinstance(item, dict):
            page_id, title = str(item.get("id") or ""), str(item.get("title") or "")
        else:
            continue
        page_id = page_id.strip()
        if page_id not in MODULE_PAGES or page_id in seen:
            continue
        seen.add(page_id)
        out.append({"id": page_id, "title": title or PAGE_TITLE[page_id]})
    return out


def normalize_external(raw):
    """The public page ids a module declares; unknown ids are dropped."""
    if raw is None:
        return list(DEFAULT_EXTERNAL)
    out = []
    for item in raw:
        page_id = str(item).strip()
        if page_id in DEFAULT_EXTERNAL and page_id not in out:
            out.append(page_id)
    return out


# --------------------------------------------------------------------------- #
# 能力声明（module.json 的 features）
# --------------------------------------------------------------------------- #
# 站上有些能力不是白给的：模块要在 module.json 的 features 里申请，平台按声明给它配置，
# 没申请的模块在接口里根本看不到那一段。写法学安卓的 manifest——名字 + 自己的定义：
#
#   "features": {
#     "search": {"records": "students", "name": "name", "key": "sid",
#                "fields": ["grp"], "tags": [{"keys": ["w"], "field": "role", "eq": "staff"}]}
#   }
#
# 不需要申请的东西（版本号热更新、事件流、导出打包这类跟玩法无关的）平台一直给，
# 也就不必写进来。名字不认识的申请一律丢掉，并按模块记一条原因——
# 模块自己的错不该让整个活动起不来。
CAPABILITIES = {
    # 搜索：平台出算法（拼音音节、标签、求交），模块出"哪份清单、哪个字段是姓名"
    "search": {
        "records": str,        # state 里那份清单的键名
        "name": str,           # 姓名字段（汉字那个）
        "key": str,            # 学号/编号字段：片段匹配用
        "fields": list,        # 其余字段：只做"直接包含"
        "tags": list,          # 标签词 → 字段条件
    },
}

DEFAULT_SEARCH = {
    "records": "students",
    "name": "name",
    "key": "sid",
    "fields": ["grp", "note"],
    "tags": [],
}


def resolve_features(raw, errors=None):
    """把模块申请的权限解析成"平台认的那几个"，不认识的丢掉并记下原因。"""
    out = {}
    if isinstance(raw, (list, tuple)):
        raw = {str(name): {} for name in raw}
    if not isinstance(raw, dict):
        return out
    for name in raw:
        declared = raw[name]
        spec = CAPABILITIES.get(name)
        if spec is None:
            _note(errors, "没有这个能力：%s" % name)
            continue
        if declared is True or declared is None:
            declared = {}
        if not isinstance(declared, dict):
            _note(errors, "能力 %s 的声明应该是一个对象" % name)
            continue
        clean = {}
        for field, kind in spec.items():
            if field not in declared:
                if name == "search" and field in DEFAULT_SEARCH:
                    clean[field] = DEFAULT_SEARCH[field]
                continue
            value = declared[field]
            if kind is list:
                clean[field] = value if isinstance(value, list) else []
            elif isinstance(value, kind):
                clean[field] = value
            else:
                _note(errors, "能力 %s 的 %s 类型不对" % (name, field))
        if name == "search":
            clean = {k: clean.get(k, v) for k, v in DEFAULT_SEARCH.items()}
        out[name] = clean
    return out


def _note(errors, text):
    if errors is not None:
        errors.append(text)


def external_routes(module_id, version):
    """The set of public pages this activity's module is allowed to serve."""
    if not module_id:
        return set(DEFAULT_EXTERNAL)
    kind = get(module_id, version)
    if kind is None:
        return set()
    return set(kind.get("external") or [])


def console_pages(module_id, version, disabled=()):
    """Every page the console should show, in order: the module's, then the platform's."""
    kind = get(module_id, version, disabled=disabled) if module_id else None
    pages = list(kind["pages"]) if kind else [
        {"id": pid, "title": PAGE_TITLE[pid]} for pid in MODULE_PAGES]
    for page_id, title in PLATFORM_PAGES:
        pages.append({"id": page_id, "title": title, "platform": True})
    return pages


def load_module(folder, version, builtin=False, enabled=True):
    """Read one module directory; raises KindError when it is unusable."""
    meta_path = os.path.join(folder, "module.json")
    if not os.path.isfile(meta_path):
        raise KindError("缺少 module.json")
    try:
        meta = _read_json(meta_path)
    except ValueError as exc:
        raise KindError("module.json 不是合法 JSON：%s" % exc)

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
        when = raw.get("visible_when")
        visible_when = None
        if isinstance(when, dict) and when.get("key"):
            visible_when = {"key": str(when["key"]),
                            "equals": str(when.get("equals", ""))}
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
            # only shown while another field has a particular value
            "visibleWhen": visible_when,
            # the value is fixed: the field is not shown and not accepted
            "locked": raw.get("locked"),
        })

    templates = []
    tpl_dir = os.path.join(folder, "templates")
    if os.path.isdir(tpl_dir):
        templates = sorted(n[:-5] for n in os.listdir(tpl_dir) if n.endswith(".json"))

    features = resolve_features(meta.get("features"))

    return {
        "id": kind_id,
        "name": str(meta.get("name")),
        "version": str(meta.get("version") or "0"),
        "author": str(meta.get("author") or ""),
        "homepage": str(meta.get("homepage") or ""),
        "license": str(meta.get("license") or ""),
        "contact": str(meta.get("contact") or ""),
        "description": str(meta.get("description") or ""),
        "requires": requires,
        "pages": normalize_pages(meta.get("pages")),
        "external": normalize_external(meta.get("external")),
        "features": features,
        "fields": fields,
        "templates": templates,
        "path": folder,
        "builtin": builtin,
        "ok": satisfies(version, requires) and enabled,
        "reason": ("被管理员禁用" if not enabled
                   else "" if satisfies(version, requires)
                   else "需要系统版本 %s" % requires),
        "enabled": enabled,
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
        if field.get("locked") is not None:
            # a locked field always takes the value the kind decided on
            out[key] = field["locked"]
            continue
        when = field.get("visibleWhen")
        if when and str(given.get(when["key"], "")) != when["equals"]:
            continue
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


def module_dirs():
    """(folder, builtin) for every installed module, built-ins first."""
    found = []
    for root, builtin in ((BUILTIN_MODULES_DIR, True), (INSTANCE_MODULES_DIR, False)):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            folder = os.path.join(root, name)
            if os.path.isdir(folder):
                found.append((folder, builtin))
    return found


def all_modules(version, include_broken=False, disabled=()):
    """Every module on disk, in a stable order, with a usability verdict.

    ``disabled`` is the set of ids an administrator switched off.  A disabled
    module is still listed and still referenced by the activities and presets
    that use it - it just cannot be used to start anything new.
    """
    out = []
    seen = set()
    disabled = {(str(x)).strip() for x in (disabled or ())}
    for folder, builtin in module_dirs():
        name = os.path.basename(folder)
        try:
            kind = load_module(folder, version, builtin, name not in disabled)
        except KindError as exc:
            if include_broken:
                out.append({"id": name, "name": name, "ok": False,
                            "reason": str(exc), "fields": [], "templates": [],
                            "builtin": builtin, "enabled": True})
            continue
        if kind["id"] in seen:
            continue          # an instance copy never shadows the shipped one
        seen.add(kind["id"])
        out.append(kind)
    return out


def get(kind_id, version, disabled=()):
    for kind in all_modules(version, disabled=disabled):
        if kind["id"] == kind_id:
            return kind
    return None
