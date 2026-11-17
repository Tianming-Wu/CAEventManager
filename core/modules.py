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
import importlib.util
import io
import json
import os
import paths
import safemode
import shutil
import stat
import sys
import re
import tempfile
import time
import zipfile

BASE_DIR = paths.APP_ROOT
# shipped with the release, then whatever this instance installed itself
BUILTIN_DIR = os.path.join(BASE_DIR, "builtin")
BUILTIN_MODULES_DIR = os.path.join(BUILTIN_DIR, "modules")
# 这个实例自己装的模块。默认在仓库根的 modules/（已在 .gitignore 里）；
# LOTTERY_MODULES_DIR 给测试与多实例部署用
INSTANCE_MODULES_DIR = os.environ.get("LOTTERY_MODULES_DIR") or os.path.join(BASE_DIR, "modules")

ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_-]+)+$")
# `super.` 是平台保留给高危功能的命名空间（见 RESERVED_PREFIX），模块 id 与功能名都不能用
# 平台支持的接口版本；模块声明的 api 不在这个范围里就不加载
SUPPORTED_API = (1,)

RESERVED_PREFIX = "super."
SUPER_FEATURES = {
    "super.anyshortlink": "不限目标的短链接跳转：可以指向活动地址之外的地方",
}


def is_super_feature(name):
    return str(name or "").startswith(RESERVED_PREFIX)


def super_features(raw):
    """声明里出现的 super.* 能力，导入模块时要一条条列给人看。"""
    names = list(raw) if isinstance(raw, (list, tuple)) else list(raw or {})
    return [{"name": n, "note": SUPER_FEATURES.get(n, "平台保留的高危能力")}
            for n in names if is_super_feature(n)]
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


# 平台最常给的那几张页签：模块不写标题时用这里的名字。页签 id 本身由模块自己起，
# 平台只按 id 注入标记、载入脚本、切换显示，不预设玩法。
DEFAULT_MODULE_PAGES = ("ops", "roster", "tickets", "draw", "config")
PAGE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
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
    """模块的页签声明 → [{"id", "title"}]，写坏的那一条丢掉。"""
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
        if not PAGE_ID_RE.match(page_id) or page_id in seen:
            continue
        seen.add(page_id)
        out.append({"id": page_id, "title": title or PAGE_TITLE.get(page_id, page_id)})
    return out


SCRIPT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def normalize_scripts(raw):
    """模块自己那几份共用脚本，按声明顺序载入，先于页签脚本。

    页签脚本只画自己那一页，几页都要用的东西（共用的画法与请求）放这里。
    名字认不出来就丢掉：少一份脚本顶多某段画不出来，不该让整个活动起不来。
    """
    out = []
    for item in raw or []:
        name = str(item or "").strip()
        if name.endswith(".js"):
            name = name[:-3]
        if not SCRIPT_RE.match(name) or name in out:
            continue
        out.append(name)
    return out


def normalize_settings(raw):
    """模块声明"哪些配置项可以从界面改"；写错的名字丢掉。"""
    out = []
    for item in raw or []:
        key = str(item or "").strip()
        if not re.match(r"^[a-z][a-z0-9_]{0,31}$", key) or key in out:
            continue
        out.append(key)
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
# 能力之间可以互相依赖：声明的依赖没申请，这个能力就不给（并记一条原因）。
CAPABILITY_NEEDS = {
    # 名单同步要先把名单接过来
    "roster_sync": ("roster",),
}

CAPABILITIES = {
    # 名单：申请之后这个活动的后台才有「名单来源」，可以从平台的名单库里绑定一份、
    # 按学号增量同步。没有名单的玩法（例如讲座）不申请，界面上也就不会出现这一段。
    "roster": {
        "sync": bool,       # 是否要"立即同步"这个动作
    },
    # 短链：申请之后，平台替这个模块负责的活动生成短链。指向哪里由模块自己决定
    # （活动地址下任意路径，模块也可以把自己的一套 token 拼在后面），平台只管
    # 跳转、归属、上限与回收。所以这里没有可声明的字段。
    "shortlink": {},
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
    """把模块申请的权限解析成"平台认的那几个"，不认识的丢掉并记下原因。

    `super.*` 那批高危能力也一样按声明给：模块只有装与没装两种状态，不做逐条授权开关
    （见 doc/dev/module-interface.md 的「高危能力」）。
    """
    out = {}
    if isinstance(raw, (list, tuple)):
        raw = {str(name): {} for name in raw}
    if not isinstance(raw, dict):
        return out
    for name in raw:
        declared = raw[name]
        spec = CAPABILITIES.get(name)
        if spec is None:
            if is_super_feature(name):
                # 声明即生效：声明里写的东西一律不收，它拿到的是一个开关
                out[name] = {}
            else:
                _note(errors, "没有这个能力：%s" % name)
            continue
        if declared is True or declared is None:
            declared = {}
        if not isinstance(declared, dict):
            _note(errors, "能力 %s 的声明应该是一个对象" % name)
            continue
        needs = CAPABILITY_NEEDS.get(name) or ()
        missing = [n for n in needs if n not in raw]
        if missing:
            _note(errors, "能力 %s 需要先申请：%s" % (name, "、".join(missing)))
            continue
        defaults = FEATURE_DEFAULTS.get(name) or {}
        clean = {}
        for field, kind in spec.items():
            if field not in declared:
                if field in defaults:
                    clean[field] = defaults[field]
                continue
            value = declared[field]
            if kind is list:
                clean[field] = value if isinstance(value, list) else []
            elif isinstance(value, kind):
                clean[field] = value
            else:
                _note(errors, "能力 %s 的 %s 类型不对" % (name, field))
        if defaults:
            clean = {k: clean.get(k, v) for k, v in defaults.items()}
        out[name] = clean
    return out


FEATURE_DEFAULTS = {
    "search": DEFAULT_SEARCH,
    "roster": {"sync": True},
}




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


def external_dir(kind_id):
    """模块自带的公开页目录（公示页、登记页这些），没有就是空串。"""
    folder = kind_dir(kind_id)
    if not folder:
        return ""
    return os.path.join(folder, "external")


def external_file(kind_id, name):
    """模块自带的某一份公开资源；没带就返回空串。"""
    name = str(name or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return ""
    folder = external_dir(kind_id)
    full = os.path.join(folder, name) if folder else ""
    return full if full and os.path.isfile(full) else ""


def console_pages(module_id, version, disabled=()):
    """Every page the console should show, in order: the module's, then the platform's."""
    kind = get(module_id, version, disabled=disabled) if module_id else None
    pages = list(kind["pages"]) if kind else [
        {"id": pid, "title": PAGE_TITLE[pid]} for pid in DEFAULT_MODULE_PAGES]
    for page_id, title in PLATFORM_PAGES:
        pages.append({"id": page_id, "title": title, "platform": True})
    return pages


def console_scripts(module_id, version, disabled=()):
    """The module's shared console scripts, to be loaded before its page scripts."""
    kind = get(module_id, version, disabled=disabled) if module_id else None
    return list(kind["scripts"]) if kind else []


ACTION_TYPES = {
    "int": int,
    "str": str,
    "bool": bool,
    "float": float,
    "json": (dict, list),
}


def normalize_actions(raw, errors=None):
    """把 module.json 的 actions 解析成平台认的形状；写错的那一条丢掉并记原因。

    这一层只管声明：字段是否合法、类型认不认识。真正执行时怎么鉴权、怎么记审计，
    在接口文档里（doc/dev/module-interface.md）。
    """
    out = {}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        _note(errors, "actions 应该是一个对象")
        return out
    for name, spec in raw.items():
        name = str(name or "").strip()
        if not name or not re.match(r"^[a-z][a-z0-9_]{0,31}$", name):
            _note(errors, "动作名不合法：%s" % name)
            continue
        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            _note(errors, "动作 %s 的声明应该是一个对象" % name)
            continue
        args = {}
        bad = None
        for arg, info in (spec.get("args") or {}).items():
            info = info if isinstance(info, dict) else {}
            kind = str(info.get("type") or "str").strip()
            if kind not in ACTION_TYPES:
                bad = "动作 %s 的参数 %s 类型不认识：%s" % (name, arg, kind)
                break
            args[str(arg)] = {
                "type": kind,
                "required": bool(info.get("required")),
                "max": int(info.get("max") or 0),
            }
        if bad:
            _note(errors, bad)
            continue
        seat = str(spec.get("seat") or "member")
        if seat not in ("member", "owner"):
            _note(errors, "动作 %s 的席位只能是 member 或 owner" % name)
            seat = "member"
        out[name] = {
            "name": name,
            "title": str(spec.get("title") or name)[:40],
            "args": args,
            "seat": seat,
            "unlock": bool(spec.get("unlock")),
            "sudo": bool(spec.get("sudo")),
            "finalized": "allow" if spec.get("finalized") == "allow" else "block",
            "target": str(spec.get("target") or "event")[:60],
        }
    return out


def coerce_action_args(decl, payload):
    """按声明取值并校验；缺必填、类型不对、超长都抛 ValueError（平台翻成 400）。"""
    given = payload if isinstance(payload, dict) else {}
    out = {}
    for arg, info in (decl.get("args") or {}).items():
        if arg not in given or given[arg] in (None, ""):
            if info["required"]:
                raise ValueError("缺少参数：%s" % arg)
            continue
        value = given[arg]
        kind = info["type"]
        if kind == "int":
            value = int(value)
        elif kind == "float":
            value = float(value)
        elif kind == "bool":
            value = bool(value)
        elif kind == "str":
            value = str(value)
            if info["max"] and len(value) > info["max"]:
                raise ValueError("参数 %s 太长（最多 %d 个字）" % (arg, info["max"]))
        else:
            if not isinstance(value, (dict, list)):
                raise ValueError("参数 %s 应该是 JSON 对象或数组" % arg)
        out[arg] = value
    return out


def kind_dir(kind_id):
    """模块目录：实例自己装的优先，其次内置。"""
    kind_id = str(kind_id or "").strip()
    if not kind_id or "/" in kind_id or "\\" in kind_id or ".." in kind_id:
        return None
    for root in ("modules", "builtin/modules"):
        folder = os.path.join(paths.APP_ROOT, root, kind_id)
        if os.path.isdir(folder):
            return folder
    return None


def kind_schema(kind_id):
    """模块在自己那一份 schema.sql 里声明的表；没有就返回空串。"""
    folder = kind_dir(kind_id)
    if folder is None:
        return ""
    path = os.path.join(folder, "schema.sql")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def kind_defaults(kind_id):
    """模块的默认配置（活动库里的 config 键）。"""
    folder = kind_dir(kind_id)
    if folder is None:
        return {}
    path = os.path.join(folder, "defaults.json")
    if not os.path.isfile(path):
        return {}
    try:
        data = _read_json(path)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def page_fragments(kind_id, pages=None):
    """模块自己那几张页签的 HTML（`pages/<id>.html`），按 pages 的顺序拼起来。

    平台只提供外壳与页签条，页签里画什么由模块给；脚本仍由平台按 id 绑，
    所以这里只搬标记，不搬逻辑。
    """
    folder = kind_dir(kind_id)
    if folder is None:
        return []
    out = []
    for page in pages or []:
        page_id = str(page.get("id") or "").strip()
        if not page_id or "/" in page_id or ".." in page_id:
            continue
        path = os.path.join(folder, "pages", "%s.html" % page_id)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                out.append(fh.read())
        except OSError:
            continue
    return out


def load_kind_model(kind_id):
    """按活动所用的模块把它的 model.py 载进来；同一个模块只载一次。"""
    kind_id = str(kind_id or "").strip()
    if kind_id in _MODELS:
        return _MODELS[kind_id]
    folder = kind_dir(kind_id)
    path = os.path.join(folder, "model.py") if folder else ""
    if not path or not os.path.isfile(path):
        raise KindError("模块 %s 没有提供 model.py" % kind_id)
    name = "kindmodel_" + kind_id.replace(".", "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _MODELS[kind_id] = module
    return module


_MODELS = {}


def reload_kind_model(kind_id):
    """把这个模块的 model.py 重新载一遍；模块内的全局状态就此清掉。

    模块代码跑在服务进程里，没有能杀掉重起的子进程，重载就是它能有的"重启"。
    载不起来时把异常交给调用方（调用方是保护层，它自己决定怎么办）。
    """
    kind_id = str(kind_id or "").strip()
    _MODELS.pop(kind_id, None)
    sys.modules.pop("kindmodel_" + kind_id.replace(".", "_").replace("-", "_"), None)
    return load_kind_model(kind_id)


def load_module(folder, version, builtin=False, enabled=True):
    """Read one module directory; raises KindError when it is unusable."""
    meta_path = os.path.join(folder, "module.json")
    if not os.path.isfile(meta_path):
        raise KindError("缺少 module.json")
    try:
        meta = _read_json(meta_path)
    except ValueError as exc:
        raise KindError("module.json 不是合法 JSON：%s" % exc)

    api = meta.get("api", 1)
    try:
        api = int(api)
    except (TypeError, ValueError):
        raise KindError("api 应该是一个整数")
    if api not in SUPPORTED_API:
        raise KindError("接口版本 %d 这个平台不支持（支持：%s）"
                        % (api, ", ".join(str(v) for v in SUPPORTED_API)))

    kind_id = str(meta.get("id") or "").strip()
    if not ID_RE.match(kind_id):
        raise KindError("id 需要形如 nwuca.<名字>")
    if is_super_feature(kind_id):
        raise KindError("%s 是平台保留的命名空间，模块 id 不能用" % RESERVED_PREFIX)
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

    notes = []
    features = resolve_features(meta.get("features"), notes)
    # 声明了哪些高危能力：模块管理页据此挂胶囊，让人在装的时候就看见
    declared_super = super_features(meta.get("features"))

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
        "scripts": normalize_scripts(meta.get("scripts")),
        "settings": normalize_settings(meta.get("settings")),
        "external": normalize_external(meta.get("external")),
        "features": features,
        "superFeatures": declared_super,
        "api": api,
        "actions": normalize_actions(meta.get("actions"), notes),
        "notes": notes,
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
            if name.startswith("."):
                continue          # 正在导入的暂存目录（.incoming-*）不是模块
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
    if safemode.enabled():
        # 安全模式：外部资源一律不加载，内置模块也算外部资源
        return out
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


# --------------------------------------------------------------------------- #
# 导入：把上传的 zip 变成一个装好的模块
# --------------------------------------------------------------------------- #
# 装模块等于让一段别人写的代码在这个进程里跑，所以这一段的规矩和屏幕包一样严：
# **先让 zip 变成"一串相对路径 + 字节"，再由我们自己建目录写盘**，绝不让解压器决定
# 磁盘上出现什么。装只在站长手里，界面上装之前要把模块申请的能力（尤其 super.*）
# 一条条摆出来——那不是"能不能装"的开关，是"装之前你知不知道"。
ZIP_MAX = 2 * 1024 * 1024
FILE_MAX = 1 * 1024 * 1024
TOTAL_MAX = 8 * 1024 * 1024
COUNT_MAX = 400
RATIO_MAX = 200
DEPTH_MAX = 6
SEG_MAX = 60
PATH_MAX = 200
STAGE_PREFIX = ".incoming-"
STAGE_TTL = 3600                 # 一个小时还没确认的暂存目录当垃圾清掉

# 模块目录里允许出现的文件：代码、数据、文档、页面资源。可执行文件一律不收
EXT_ALLOWED = (".py", ".json", ".md", ".txt", ".js", ".css", ".html", ".svg",
               ".png", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2", ".csv")

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class InstallError(ValueError):
    """这次导入不行；消息可以直接给用户看。"""


def _size_text(num):
    num = int(num or 0)
    if num >= 1024 * 1024:
        return "%.1f MB" % (num / 1024.0 / 1024.0)
    if num >= 1024:
        return "%.0f KB" % (num / 1024.0)
    return "%d B" % num


def install_limits():
    """两个界面（前端提示与服务端判定）共用的一份数字。"""
    return {"zipMax": ZIP_MAX, "zipText": _size_text(ZIP_MAX),
            "fileText": _size_text(FILE_MAX), "totalText": _size_text(TOTAL_MAX),
            "countMax": COUNT_MAX, "ext": list(EXT_ALLOWED)}


def _segments(raw):
    """一个 zip 条目名拆成的路径段；不许写就返回 None。"""
    if not raw or "\\" in raw or "\x00" in raw or ":" in raw:
        return None
    if raw.startswith("/") or raw.startswith("."):
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or len(parts) > DEPTH_MAX:
        return None
    for part in parts:
        if part == ".." or part.startswith(".") or len(part) > SEG_MAX:
            return None
        if not _SEGMENT.match(part) or part.strip(".") == "":
            return None
    if len("/".join(parts)) > PATH_MAX:
        return None
    return parts


def read_zip(blob):
    """把上传的 zip 读成 ``{相对路径: 字节}``，并把不接受的东西挡在这里。

    压缩包里可以整个模块目录打个包（顶层是那个目录），也可以是模块目录的内容本身。
    """
    if not blob:
        raise InstallError("上传的文件为空")
    if len(blob) > ZIP_MAX:
        raise InstallError("压缩包超过 %s" % _size_text(ZIP_MAX))
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        raise InstallError("这不是一个可以读取的 zip 压缩包")

    files, total = {}, 0
    try:
        members = archive.infolist()
        if len(members) > COUNT_MAX * 4:
            raise InstallError("压缩包内条目过多")
        for info in members:
            name = info.filename or ""
            if info.is_dir() or name.endswith("/"):
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise InstallError("%s 是符号链接，模块里不允许" % name)
            parts = _segments(name)
            if parts is None:
                raise InstallError("压缩包里有不能用的路径：%s" % name)
            rel = "/".join(parts)
            if os.path.splitext(parts[-1])[1].lower() not in EXT_ALLOWED:
                raise InstallError("%s 这种文件模块里用不到" % rel)
            if rel in files:
                raise InstallError("压缩包内有重名文件：%s" % rel)
            if len(files) >= COUNT_MAX:
                raise InstallError("文件数超过 %d 个" % COUNT_MAX)
            if info.file_size > FILE_MAX:
                raise InstallError("%s 超过单文件上限 %s" % (rel, _size_text(FILE_MAX)))
            if (info.compress_size and info.file_size > 65536
                    and info.file_size / float(info.compress_size) > RATIO_MAX):
                raise InstallError("%s 的压缩比异常，已拒绝解压" % rel)
            try:
                with archive.open(info) as handle:
                    data = handle.read(FILE_MAX + 1)
            except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
                raise InstallError("%s 无法解开" % rel)
            if len(data) > FILE_MAX:
                raise InstallError("%s 超过单文件上限 %s" % (rel, _size_text(FILE_MAX)))
            total += len(data)
            if total > TOTAL_MAX:
                raise InstallError("解压后总量超过 %s" % _size_text(TOTAL_MAX))
            files[rel] = data
    finally:
        archive.close()
    return files


def _strip_root(files):
    """压缩包里多包了一层目录（``mymod/module.json``）时，把那层去掉。"""
    if "module.json" in files:
        return files, ""
    tops = {rel.split("/", 1)[0] for rel in files}
    if len(tops) == 1:
        top = tops.pop()
        if any(rel == top + "/module.json" for rel in files):
            return ({rel.split("/", 1)[1]: data for rel, data in files.items()}, top)
    return files, ""


def _clean_stage(root):
    """清掉没人确认的暂存目录（超过一小时）。"""
    if not os.path.isdir(root):
        return
    now = time.time()
    for name in os.listdir(root):
        if not name.startswith(STAGE_PREFIX):
            continue
        full = os.path.join(root, name)
        try:
            if now - os.path.getmtime(full) > STAGE_TTL:
                shutil.rmtree(full, ignore_errors=True)
        except OSError:
            pass


def inspect_zip(blob):
    """校验 + 解析 + 落到暂存目录，返回"要不要装"需要的全部信息。

    真正装好要再调一次 :func:`install_staged`——中间那一步是给人看的时间：
    模块申请了哪些能力、有没有高危的、装上去会不会顶掉谁。
    """
    files = read_zip(blob)
    files, dropped_root = _strip_root(files)
    if "module.json" not in files:
        raise InstallError("压缩包里没有 module.json（模块目录要放在最外层）")
    root = INSTANCE_MODULES_DIR
    os.makedirs(root, exist_ok=True)
    _clean_stage(root)
    stage = tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=root)
    try:
        for rel in sorted(files):
            full = os.path.join(stage, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as handle:
                handle.write(files[rel])
        try:
            kind = load_module(stage, "0.0.0")
        except KindError as exc:
            raise InstallError("这个模块读不出来：%s" % exc)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    kind_id = kind["id"]
    if is_super_feature(kind_id):
        raise InstallError("%s 是平台保留的命名空间，模块 id 不能用" % RESERVED_PREFIX)
    for folder, builtin in module_dirs():
        if os.path.basename(folder) != kind_id:
            continue
        if builtin:
            raise InstallError("平台自带 %s，不能用一个同名的模块顶掉它" % kind_id)
        raise InstallError("%s 已经装过了；要换版本先卸载再装" % kind_id)

    return {
        "stage": os.path.basename(stage),
        "id": kind_id,
        "name": kind["name"],
        "version": kind["version"],
        "author": kind["author"],
        "api": kind["api"],
        "requires": kind["requires"],
        "ok": kind["ok"],
        "reason": kind["reason"],
        "notes": kind["notes"],
        # 申请了哪些能力：高危的自带一句后果，其余的在界面上也照原样列出来
        "features": sorted(kind["features"]),
        "superFeatures": kind["superFeatures"],
        "files": len(files),
        "size": sum(len(v) for v in files.values()),
        "sizeText": _size_text(sum(len(v) for v in files.values())),
        "droppedRoot": dropped_root,
    }


def install_staged(stage):
    """把暂存目录整体挪成 ``modules/<id>``。挪不动就报错，不留半份模块。"""
    stage = str(stage or "").strip()
    root = INSTANCE_MODULES_DIR
    if not stage.startswith(STAGE_PREFIX) or "/" in stage or "\\" in stage:
        raise InstallError("这次导入已经过期，请重新选择文件")
    source = os.path.join(root, stage)
    if not os.path.isdir(source):
        raise InstallError("这次导入已经过期，请重新选择文件")
    try:
        kind = load_module(source, "0.0.0")
    except KindError as exc:
        raise InstallError("这个模块读不出来：%s" % exc)
    target = os.path.join(root, kind["id"])
    if os.path.exists(target):
        raise InstallError("%s 已经装过了" % kind["id"])
    try:
        os.rename(source, target)
    except OSError:
        raise InstallError("装不上：写入目录失败，请检查磁盘空间")
    return kind["id"]


def remove_installed(module_id):
    """卸载一个自己装的模块。平台自带的不归这儿管。"""
    module_id = str(module_id or "").strip()
    if not module_id or not ID_RE.match(module_id):
        raise InstallError("模块标识不合法")
    for folder, builtin in module_dirs():
        if kind_folder_id(folder) != module_id:
            continue
        if builtin:
            raise InstallError("平台自带的模块不能卸载")
        real_root = os.path.realpath(INSTANCE_MODULES_DIR)
        real_dir = os.path.realpath(folder)
        if os.path.dirname(real_dir) != real_root:
            raise InstallError("模块标识不合法")
        shutil.rmtree(real_dir)
        return module_id
    raise InstallError("找不到这个模块")


def kind_folder_id(folder):
    """目录名就是模块 id（装的时候按 id 落盘）。"""
    return os.path.basename(str(folder or "").rstrip("/\\"))
