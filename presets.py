"""Presets: one worked-out configuration inside a module.

A *module* is a capability boundary: enabling it is what makes its settings
reachable at all.  A *preset* narrows that boundary further - it is a single
arrangement of the module's parameters, saved so the next event does not start
from an empty form.

Presets live in three places, all of which end up in one table:

    builtin/modules/<module>/templates/<slug>.json   ships inside the module
    builtin/templates/<slug>.json                    ships with the release
    templates/<slug>.json                            installed on this instance

A preset file declares what it needs:

    {
      "id": "nwuca.salon",
      "name": "计算机杂谈活动",
      "dependency": { "module": "nwuca.lottery", "template": "nwuca.lottery.salon" },
      "params": { "pool_mode": "auto" }
    }

``dependency.module`` is the boundary it belongs to; ``dependency.template``
names the preset it is a variation of (optional).  A preset whose module is
missing or switched off is kept and listed, but cannot be used - the activities
that already started from it are unaffected.
"""
import json
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILTIN_TEMPLATES_DIR = os.path.join(BASE_DIR, "builtin", "templates")
INSTANCE_TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_-]+)+$")

# Who may see a preset.  "org" is written down and stored now; the organisation
# itself does not exist yet (UNWIRED(orgs)), so it behaves like "public" until
# the tenancy work lands.
VISIBILITY = ("private", "public", "org")
VISIBILITY_LABEL = {"private": "私有", "public": "公开", "org": "组织公开"}


class TemplateError(Exception):
    """A preset that cannot be used, with a reason for the operator."""


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_file(path, modules_by_id):
    try:
        meta = _read(path)
    except (OSError, ValueError) as exc:
        raise TemplateError("读不出来：%s" % exc)
    if not isinstance(meta, dict):
        raise TemplateError("应该是一个对象")

    template_id = str(meta.get("id") or "").strip()
    if not ID_RE.match(template_id):
        raise TemplateError("id 需要形如 nwuca.salon")

    dep = meta.get("dependency") or {}
    if not isinstance(dep, dict):
        raise TemplateError("dependency 应该是一个对象")
    module_id = str(dep.get("module") or meta.get("module") or "").strip()
    base_template = str(dep.get("template") or "").strip()
    if not module_id:
        raise TemplateError("缺少 dependency.module")

    params = meta.get("params") or {}
    if not isinstance(params, dict):
        raise TemplateError("params 应该是一个对象")
    module = modules_by_id.get(module_id)
    if module is not None:
        # a preset may only set parameters the module actually declared
        known = {f["key"] for f in module["fields"]}
        unknown = [k for k in params if k not in known]
        if unknown:
            raise TemplateError("模块没有这些参数：%s" % "、".join(sorted(unknown)))

    return {
        "id": template_id,
        "name": str(meta.get("name") or template_id),
        "note": str(meta.get("note") or ""),
        "description": str(meta.get("description") or ""),
        "title": str(meta.get("title") or ""),
        "module": module_id,
        "baseTemplate": base_template,
        "params": params,
        "builtin": bool(meta.get("builtin")),
    }


def file_presets(modules_by_id):
    """Every preset file on this installation: module-owned, release, instance.

    A file whose module is missing is still returned, with a reason, so it can
    be listed as unusable instead of disappearing.
    """
    out = []
    seen = set()

    def take(path):
        try:
            tpl = load_file(path, modules_by_id)
        except TemplateError as exc:
            slug = os.path.basename(path)[:-5]
            if slug in seen:
                return
            seen.add(slug)
            out.append({"id": slug, "name": slug, "note": str(exc),
                        "description": "", "title": "", "module": "",
                        "baseTemplate": "", "params": {}, "builtin": True,
                        "source": "broken"})
            return
        if tpl["id"] in seen:
            return
        seen.add(tpl["id"])
        out.append(tpl)

    # module-owned presets ride with the module
    for module in modules_by_id.values():
        folder = os.path.join(module.get("path") or "", "templates")
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                take(os.path.join(folder, name))
                out[-1]["source"] = "module"

    for root, source in ((BUILTIN_TEMPLATES_DIR, "builtin"),
                         (INSTANCE_TEMPLATES_DIR, "instance")):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            take(os.path.join(root, name))
            out[-1]["source"] = source
    return out


def seed(conn, modules_by_id):
    """Copy the preset files into the table; the table is what the app reads."""
    now = time.time()
    for tpl in file_presets(modules_by_id):
        row = conn.execute("SELECT id, builtin FROM presets WHERE slug=?",
                           (tpl["id"],)).fetchone()
        params = json.dumps(tpl["params"], ensure_ascii=False)
        if row is None:
            conn.execute(
                "INSERT INTO presets (slug, owner_id, name, note, module, params,"
                " title, visibility, org_id, builtin, created_at, updated_at,"
                " description, dep_module, dep_template, source)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tpl["id"], 0, tpl["name"], tpl["note"], tpl["module"], params,
                 tpl["title"], "public", 0, 1, now, now, tpl["description"],
                 tpl["module"], tpl["baseTemplate"],
                 tpl.get("source") or "builtin"))
        elif row["builtin"]:
            # a shipped preset keeps its identity but follows its file
            conn.execute(
                "UPDATE presets SET name=?, note=?, module=?, params=?, title=?,"
                " updated_at=?, description=?, dep_module=?, dep_template=?,"
                " source=? WHERE id=?",
                (tpl["name"], tpl["note"], tpl["module"], params, tpl["title"],
                 now, tpl["description"], tpl["module"], tpl["baseTemplate"],
                 tpl.get("source") or "builtin", row["id"]))


def _view(row, modules_by_id, account_id):
    module_id = row["module"] or row["dep_module"] or ""
    module = modules_by_id.get(module_id)
    try:
        params = json.loads(row["params"] or "{}")
    except ValueError:
        params = {}
    if not isinstance(params, dict):
        params = {}
    known = {f["key"] for f in (module or {}).get("fields", [])}
    if known:
        params = {k: v for k, v in params.items() if k in known}
    if module is None:
        ok, reason = False, "依赖的模块不存在：%s" % (module_id or "(未声明)")
    elif not module.get("ok"):
        ok, reason = False, (module.get("reason") or "模块不可用")
    else:
        ok, reason = True, ""
    return {
        "presetId": row["id"],
        "id": row["slug"],
        "name": row["name"],
        "note": row["note"] or "",
        "description": row["description"] or "",
        "module": module_id,
        "moduleName": (module or {}).get("name") or module_id,
        "moduleVersion": (module or {}).get("version") or "",
        "baseTemplate": row["dep_template"] or "",
        "source": row["source"] or ("builtin" if row["builtin"] else "user"),
        "description": row["description"] or "",
        "title": row["title"] or "",
        "params": params,
        "builtin": bool(row["builtin"]),
        "visibility": row["visibility"],
        "visibilityLabel": VISIBILITY_LABEL.get(row["visibility"], row["visibility"]),
        "orgId": row["org_id"],
        "ownerName": row["owner_name"] or "",
        "mine": bool(account_id) and row["owner_id"] == account_id,
        "fav": bool(row["fav"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "ok": ok,
        "reason": reason,
        "fields": (module or {}).get("fields") or [],
    }


def visible(conn, account_id, modules_by_id):
    """Everything this account may see: its own, its favourites, and the square.

    UNWIRED(orgs): "org" presets are shown to everyone until organisations
    exist; the column is already written so the filter can land later.
    """
    rows = conn.execute(
        "SELECT p.*, a.name AS owner_name,"
        "       (f.account_id IS NOT NULL) AS fav"
        " FROM presets p"
        " LEFT JOIN accounts a ON a.id = p.owner_id"
        " LEFT JOIN preset_favorites f ON f.preset_id = p.id AND f.account_id = ?"
        " WHERE p.visibility IN ('public','org') OR p.owner_id = ?"
        " ORDER BY p.builtin DESC, p.updated_at DESC",
        (account_id, account_id)).fetchall()
    return [_view(r, modules_by_id, account_id) for r in rows]


def get(conn, slug, modules_by_id, account_id=0):
    row = conn.execute(
        "SELECT p.*, a.name AS owner_name, 0 AS fav FROM presets p"
        " LEFT JOIN accounts a ON a.id = p.owner_id WHERE p.slug=?",
        (slug,)).fetchone()
    if row is None:
        return None
    if account_id and not row["builtin"] and row["visibility"] == "private" \
            and row["owner_id"] != account_id:
        return None
    return _view(row, modules_by_id, account_id)


def new_slug(conn, account_id):
    """A short, stable address for a user preset: what the URL carries."""
    base = "u%d" % int(account_id)
    n = 1
    while True:
        slug = "%s.p%d" % (base, n)
        if conn.execute("SELECT 1 FROM presets WHERE slug=?", (slug,)).fetchone() is None:
            return slug
        n += 1
