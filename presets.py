"""Templates: an arrangement on top of a module.

A *module* is the infrastructure of one activity form - it knows how tickets are
issued, how a draw works, which console pages exist.  A *template* is a way of
running such a form: which module, which parameters, and what to call it.  The
club's own recurring event is a template, not a module.

A template is a JSON file:

    templates/<id>.json
    {
      "id": "nwuca.salon",
      "name": "计算机杂谈活动",
      "module": "nwuca.lottery",
      "description": "...",
      "title": "计算机杂谈沙龙",        # suggested activity name
      "params": { "pool_mode": "auto" }, # prefills the creation form
      "builtin": true
    }

Nothing here is executable: a template can only choose a module and fill in the
parameters that module already declared.
"""
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_-]+)+$")


class TemplateError(Exception):
    """A template file that cannot be used, with a reason for the operator."""


def load_file(path, modules_by_id):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        raise TemplateError("读不出来：%s" % exc)
    if not isinstance(meta, dict):
        raise TemplateError("应该是一个对象")

    template_id = str(meta.get("id") or "").strip()
    if not ID_RE.match(template_id):
        raise TemplateError("id 需要形如 nwuca.salon")
    module_id = str(meta.get("module") or "").strip()
    module = modules_by_id.get(module_id)
    if module is None:
        raise TemplateError("没有这个活动模块：%s" % (module_id or "(空)"))

    params = meta.get("params") or {}
    if not isinstance(params, dict):
        raise TemplateError("params 应该是一个对象")
    # a template may only set parameters the module actually declared
    known = {f["key"] for f in module["fields"]}
    unknown = [k for k in params if k not in known]
    if unknown:
        raise TemplateError("模块没有这些参数：%s" % "、".join(sorted(unknown)))

    return {
        "id": template_id,
        "name": str(meta.get("name") or template_id),
        "module": module_id,
        "moduleName": module["name"],
        "description": str(meta.get("description") or ""),
        "title": str(meta.get("title") or ""),
        "params": params,
        "builtin": bool(meta.get("builtin")),
        "ok": module["ok"],
        "reason": module.get("reason") or "",
        "fields": module["fields"],
    }


def all_templates(version, modules_by_id, include_broken=False):
    out = []
    if not os.path.isdir(TEMPLATES_DIR):
        return out
    for name in sorted(os.listdir(TEMPLATES_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            out.append(load_file(os.path.join(TEMPLATES_DIR, name), modules_by_id))
        except TemplateError as exc:
            if include_broken:
                out.append({"id": name[:-5], "name": name[:-5], "ok": False,
                            "reason": str(exc), "fields": [], "params": {},
                            "module": "", "moduleName": "", "builtin": False,
                            "description": "", "title": ""})
    return out


def get(template_id, version, modules_by_id):
    for tpl in all_templates(version, modules_by_id):
        if tpl["id"] == template_id:
            return tpl
    return None
