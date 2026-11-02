"""Presets: a saved arrangement on top of a module.

A *module* is the infrastructure of one activity form - it knows how tickets are
issued, how a draw works, which console pages exist.  A *preset* is a way of
running such a form: which module, which parameters, and what to call it.  The
club's own recurring event is a preset, not a module.

A preset used to be a JSON file and nothing else.  It is a database row now,
because saveable snapshots have an owner, a visibility and a favourite flag:

    presets(id, slug, owner_id, name, note, module, params, title,
            visibility, org_id, builtin, created_at, updated_at)

`templates/*.json` still exists and is still the source of the built-in ones:
they are seeded into the table on startup, so there is one place that lists
presets instead of two.  A preset can only choose a module and fill in the
parameters that module already declared - nothing here is executable.
"""
import json
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_-]+)+$")

# Who may see a preset.  "org" is written down and stored now; the organisation
# itself does not exist yet (UNWIRED(orgs)), so it behaves like "public" until
# the tenancy work lands.
VISIBILITY = ("private", "public", "org")
VISIBILITY_LABEL = {"private": "私有", "public": "公开", "org": "组织公开"}


class TemplateError(Exception):
    """A preset that cannot be used, with a reason for the operator."""


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
    # a preset may only set parameters the module actually declared
    known = {f["key"] for f in module["fields"]}
    unknown = [k for k in params if k not in known]
    if unknown:
        raise TemplateError("模块没有这些参数：%s" % "、".join(sorted(unknown)))

    return {
        "id": template_id,
        "name": str(meta.get("name") or template_id),
        "module": module_id,
        "description": str(meta.get("description") or ""),
        "title": str(meta.get("title") or ""),
        "params": params,
        "builtin": bool(meta.get("builtin")),
    }


def load_files(modules_by_id):
    """The built-in presets, read from templates/*.json."""
    out = []
    if not os.path.isdir(TEMPLATES_DIR):
        return out
    for name in sorted(os.listdir(TEMPLATES_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            out.append(load_file(os.path.join(TEMPLATES_DIR, name), modules_by_id))
        except TemplateError:
            continue
    return out


def seed(conn, modules_by_id):
    """Copy the file presets into the table, so everything reads from one place."""
    now = time.time()
    for tpl in load_files(modules_by_id):
        row = conn.execute("SELECT id FROM presets WHERE slug=?", (tpl["id"],)).fetchone()
        payload = (
            tpl["name"], tpl["module"], json.dumps(tpl["params"], ensure_ascii=False),
            tpl["description"], tpl["title"])
        if row is None:
            conn.execute(
                "INSERT INTO presets (slug, owner_id, name, note, module, params,"
                " title, visibility, org_id, builtin, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (tpl["id"], 0, tpl["name"], tpl["description"], tpl["module"],
                 json.dumps(tpl["params"], ensure_ascii=False), tpl["title"],
                 "public", 0, 1, now, now))
        else:
            # a built-in keeps its identity but follows the file it came from
            conn.execute(
                "UPDATE presets SET name=?, module=?, params=?, note=?, title=?,"
                " updated_at=? WHERE id=? AND builtin=1",
                payload + (now, row["id"]))


def _view(row, modules_by_id, account_id):
    module_id = row["module"]
    module = modules_by_id.get(module_id) or {}
    try:
        params = json.loads(row["params"] or "{}")
    except ValueError:
        params = {}
    if not isinstance(params, dict):
        params = {}
    # a preset saved when the module had other fields may not match it today
    known = {f["key"] for f in module.get("fields", [])}
    params = {k: v for k, v in params.items() if k in known} if known else params
    return {
        "presetId": row["id"],
        "id": row["slug"],
        "name": row["name"],
        "note": row["note"] or "",
        "module": module_id,
        "moduleName": module.get("name") or module_id,
        "moduleVersion": module.get("version") or "",
        "description": row["note"] or "",
        "title": row["title"] or "",
        "params": params,
        "builtin": bool(row["builtin"]),
        "visibility": row["visibility"],
        "visibilityLabel": VISIBILITY_LABEL.get(row["visibility"], row["visibility"]),
        "orgId": row["org_id"],
        "ownerName": row["owner_name"] or ("内置" if row["builtin"] else ""),
        "mine": bool(account_id) and row["owner_id"] == account_id,
        "fav": bool(row["fav"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "ok": bool(module.get("ok")),
        "reason": module.get("reason") or "",
        "fields": module.get("fields") or [],
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
