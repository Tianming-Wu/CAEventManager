# -*- coding: utf-8 -*-
"""运维助手：把"查一下、改一下"变成一次对话。

三条硬约束（与 `doc/ca-event-manager-assistant.md` 一致）：

1. **权限不另立一套**：助手以当前登录账号的会话发出请求，打到本机自己的接口上。
   权限、CSRF 标记、并发冲突、审计全部沿用现成那一套，一处都不用重写。
2. **模型在服务端调**：`base_url` / `model` / `api_key` 每人一份，key 只写不读，
   浏览器永远拿不到它。
3. **放行只走一个口**：每个动作先问 `policy.verdict()`，默认拒绝。

对话本身按会话存进数据库（每人有配额），这里只负责一问一答。
"""

import json
import os
import paths
import re
import time
import urllib.error
import urllib.request

import db
import panel
import policy

# 一轮对话里最多来回几次工具调用，防止模型绕圈
MAX_STEPS = 6
TIMEOUT = 60
UA = "CAEventManager-assistant/1"

# 给会话起名：只要一个短标题，多一个字都是浪费
TITLE_RULE = ("给下面这段话起个标题：不超过 12 个字，只回标题本身，"
              "不要引号、不要标点、不要解释。")
TITLE_MAX = 24


# --------------------------------------------------------------------------- #
# 模型配置（挂在账号上）
# --------------------------------------------------------------------------- #
# DeepSeek 的默认值：base 不带 /v1（带了也认），模型名以 GET /models 返回的为准，
# 目前是 deepseek-flash 与 deepseek-v4-pro。旧的 deepseek-chat 已经下线。
DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


def config_of(acc):
    """What the panel may know about this account's model settings (never the key)."""
    row = acc if acc is not None else {}
    stored_base = (row["model_base"] or "").strip() if "model_base" in _keys(row) else ""
    stored_model = (row["model_name"] or "").strip() if "model_name" in _keys(row) else ""
    key = (row["model_key"] or "").strip() if "model_key" in _keys(row) else ""
    resume = True
    if "chat_resume" in _keys(row):
        resume = bool(row["chat_resume"])
    return {"base": stored_base or DEFAULT_BASE,
            "model": stored_model or DEFAULT_MODEL,
            "storedBase": stored_base,
            "storedModel": stored_model,
            "hasKey": bool(key),
            # 接口地址与模型名都有默认值，所以只差一把密钥就算配好了
            "ready": bool(key),
            "defaultBase": DEFAULT_BASE,
            "defaultModel": DEFAULT_MODEL,
            # 从侧栏进来时：接着上次那段，还是开一段新的
            "resume": resume}


def _keys(row):
    try:
        return row.keys()
    except AttributeError:
        return ()


def save_config(conn, account_id, base, model, key, clear=False, resume=None):
    """Empty key keeps the stored one: 界面上只显示"已配置"，不该逼人每次重填。"""
    sets, args = [], []
    if base is not None:
        sets.append("model_base=?")
        args.append((base or "").strip()[:200])
    if model is not None:
        sets.append("model_name=?")
        args.append((model or "").strip()[:80])
    if resume is not None:
        sets.append("chat_resume=?")
        args.append(1 if resume else 0)
    if clear:
        sets.append("model_key=?")
        args.append("")
    elif key:
        sets.append("model_key=?")
        args.append(key.strip()[:400])
    if not sets:
        return False
    args.append(int(account_id))
    conn.execute("UPDATE accounts SET %s WHERE id=?" % ", ".join(sets), args)   # sql-ok 只拼列名/表名/SET 片段，值都走 ?
    return True


def fetch_models(base, key):
    """GET {base}/models：把可选的模型名列出来，省得手抄错。

    OpenAI 兼容的服务都实现了这一条；有的要 /v1 前缀，两种都试一下。
    """
    roots = [(base or DEFAULT_BASE).rstrip("/")]
    if not roots[0].endswith("/v1"):
        roots.append(roots[0] + "/v1")
    last = ""
    for root in roots:
        req = urllib.request.Request(
            root + "/models", method="GET",
            headers={"Authorization": "Bearer " + str(key or ""), "User-Agent": UA,
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                payload = json.loads(res.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            last = "接口返回 %d" % exc.code
            continue
        except Exception as exc:
            last = str(exc)[:120]
            continue
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            last = "返回里没有模型列表"
            continue
        out = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                out.append({"id": item["id"], "name": item.get("name") or ""})
        return out, ""
    return [], last or "拿不到模型列表"


# --------------------------------------------------------------------------- #
# 打到自己的面板接口上：权限与审计都是现成的
# --------------------------------------------------------------------------- #
class PanelClient:
    """Calls this very service over loopback with the caller's own session cookie."""

    def __init__(self, port, prefix, cookie, marker="CAEventManager"):
        self.origin = "http://127.0.0.1:%d" % int(port)
        self.prefix = prefix or ""
        self.cookie = cookie or ""
        self.marker = marker

    def _request(self, method, path, body=None, marker=None):
        url = self.origin + self.prefix + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"User-Agent": UA, "Accept": "application/json",
                   # 让服务端知道这次是助手替他做的：写操作会在审计里带上这一句
                   "X-Via": "assistant"}
        use = marker if marker is not None else self.marker
        if use:
            headers["X-Requested-With"] = use
        if self.cookie:
            headers["Cookie"] = self.cookie
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read()
                return json.loads(raw.decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
            except Exception:
                payload = {}
            return {"ok": False, "error": payload.get("error")
                    or ("HTTP %d" % exc.code)}

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, body=None):
        return self._request("POST", path, body if body is not None else {})


# --------------------------------------------------------------------------- #
# 工具：名字、说明、参数、实现
# --------------------------------------------------------------------------- #
def _clip(text, limit=1200):
    text = str(text if text is not None else "")
    return text if len(text) <= limit else text[:limit] + "…"


def _tool_list_events(client, args):
    data = client.get("/api/panel/events")
    events = []
    for e in (data.get("events") or []):
        events.append({"seq": e.get("seq"), "title": e.get("title"),
                       "role": e.get("role"), "archived": bool(e.get("archivedAt"))})
    return {"events": events}


def _tool_event_board(client, args):
    seq = args.get("seq")
    events = client.get("/api/panel/events").get("events") or []
    row = None
    for e in events:
        if str(e.get("seq")) == str(seq) or str(e.get("id")) == str(seq):
            row = e
            break
    if row is None:
        return {"error": "没有这个活动，或者你不在里面"}
    token = row.get("id")
    board = client._request("GET", "/%s/api/board" % token, marker="lottery")
    if not board.get("ok"):
        return {"error": board.get("error") or "读不到这场活动的公示数据"}
    stats = board.get("stats") or {}
    cfg = board.get("config") or {}
    winners = (board.get("winners") or [])[:10]
    return {"title": cfg.get("title") or row.get("title"),
            "finalized": bool(cfg.get("finalized")),
            "stats": {k: stats.get(k) for k in
                      ("total", "checkin", "present_all", "live_tickets", "pool_size")},
            "prizes": cfg.get("prizes") or [],
            "recent_winners": [{"prize": w.get("prize"), "name": w.get("name")}
                               for w in winners]}


def _tool_list_rosters(client, args):
    scope = args.get("scope") or "joined"
    data = client.get("/api/panel/rosters?scope=%s" % scope)
    out = []
    for r in (data.get("rosters") or [])[:40]:
        out.append({"id": r.get("id"), "name": r.get("name"),
                    "members": r.get("count"), "visibility": r.get("visibilityLabel"),
                    "mine": bool(r.get("mine"))})
    return {"rosters": out}


def _tool_roster_entries(client, args):
    rid = int(args.get("roster_id") or 0)
    if not rid:
        return {"error": "需要 roster_id"}
    q = (args.get("q") or "").strip()
    path = "/api/panel/roster/entries?id=%d&limit=30" % rid
    if q:
        path += "&q=" + urllib.request.quote(q)
    data = client.get(path)
    if not data.get("ok"):
        return {"error": data.get("error") or "读不到这份名单"}
    return {"total": data.get("total"),
            "entries": [{"sid": e.get("sid"), "name": e.get("name"),
                         "dept": e.get("dept"), "state": e.get("stateLabel")}
                        for e in (data.get("entries") or [])[:30]]}


def _tool_list_signups(client, args):
    data = client.get("/api/panel/signups?limit=30")
    out = []
    for s in (data.get("signups") or [])[:30]:
        out.append({"id": s.get("id"), "title": s.get("title"),
                    "state": s.get("statusLabel"), "entries": s.get("entries"),
                    "capacity": s.get("capacity"), "roster": s.get("rosterName")})
    return {"signups": out}


def _tool_signup_entries(client, args):
    sid = int(args.get("signup_id") or 0)
    if not sid:
        return {"error": "需要 signup_id"}
    data = client.get("/api/panel/signup/entries?id=%d&limit=30" % sid)
    if not data.get("ok"):
        return {"error": data.get("error") or "读不到这次报名"}
    return {"total": data.get("total"),
            "entries": [{"sid": e.get("sid"), "name": e.get("name"),
                         "at": e.get("createdAt")}
                        for e in (data.get("entries") or [])[:30]]}


def _tool_search_people(client, args):
    q = (args.get("q") or "").strip()
    if not q:
        return {"error": "需要 q"}
    data = client.get("/api/panel/people/search?q=" + urllib.request.quote(q))
    return {"people": [{"sid": p.get("sid"), "name": p.get("name"),
                        "dept": p.get("dept")}
                       for p in (data.get("people") or [])[:20]]}


def _tool_recent_audit(client, args):
    q = (args.get("q") or "").strip()
    path = "/api/panel/audit?limit=20" + ("&q=" + urllib.request.quote(q) if q else "")
    data = client.get(path)
    if not data.get("ok"):
        return {"error": data.get("error") or "读不到审计日志"}
    return {"entries": [{"at": a.get("ts"), "actor": a.get("actor"),
                         "action": a.get("action"), "target": a.get("target"),
                         "detail": _clip(a.get("detail"), 160)}
                        for a in (data.get("audit") or [])]}


def _tool_host_stats(client, args):
    data = client.get("/api/panel/host")
    if not data.get("ok"):
        return {"error": data.get("error") or "读不到服务器状态"}
    host = data.get("host") or {}
    return {"host": {k: host.get(k) for k in ("cpu", "mem", "uptime", "version")},
            "users": data.get("users") or {}}


# --------------------------------------------------------------------------- #
# 说明文档：随应用一起装到机器上的 doc/，以及每个模块自己的 doc.md
# --------------------------------------------------------------------------- #
# 为什么要让助手读文档：把全部说明塞进系统提示会白白烧 token，而助手不知道
# "这个平台怎么用"就会瞎猜。文档是纯文本、随部署一起过去，读一份只在用到时花一次。
DOC_DIR = "doc"
DOC_EXTS = (".md",)
# 助手只读**操作指南**：给运维做事要用的那部分（怎么用、接口、术语、模块自己的现场说明）。
# 开发指南（doc/dev、doc/ops）不给：让 web agent 具备开发/写入能力是另一回事，
# 二开是开发者自己在他的环境里做，他本来就能读仓库。少给一份就少烧一份 token。
DOC_ALLOWED = ("doc/usage/", "doc/reference/")
DOC_LIST_MAX = 60
DOC_MAX_CHARS = 12000          # 一份文档一次最多回这么多字，太长就截断并说明
DOC_ROOTS = ("builtin/modules", "modules")


def _docs_base():
    """文档根：应用根目录（程序模块在 core/ 里，文档在根目录的 doc/ 下）。"""
    return paths.APP_ROOT


def _doc_title(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for _ in range(5):
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        pass
    return os.path.basename(path)


def doc_entries():
    """所有可读文档：``doc/`` 下的 markdown，加上每个模块自己那份 ``doc.md``。"""
    base = _docs_base()
    out = []

    def add(full, rel):
        try:
            size = os.path.getsize(full)
        except OSError:
            return
        out.append({"path": rel, "title": _doc_title(full), "size": size})

    root = os.path.join(base, DOC_DIR)
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if not name.lower().endswith(DOC_EXTS):
                continue
            full = os.path.join(here, name)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            if not rel.startswith(DOC_ALLOWED):
                continue
            add(full, rel)
    for kind in DOC_ROOTS:
        parent = os.path.join(base, kind)
        if not os.path.isdir(parent):
            continue
        for module in sorted(os.listdir(parent)):
            full = os.path.join(parent, module, "doc.md")
            if os.path.isfile(full):
                add(full, "%s/%s/doc.md" % (kind, module))
    # 按 DOC_ALLOWED 的次序排：先操作指南，再参考，模块说明最后。
    # 清单是给模型看的，最常读的排前面。
    out.sort(key=lambda item: (_doc_rank(item["path"]), item["path"]))
    return out


def _doc_rank(rel):
    for i, prefix in enumerate(DOC_ALLOWED):
        if rel.startswith(prefix):
            return i
    return len(DOC_ALLOWED)


def read_doc(rel):
    """按清单里的路径读一份文档。只认清单里出现过的那些路径，别的都拒。"""
    want = (rel or "").strip().replace("\\", "/").lstrip("./")
    if not want:
        return None, "需要 path，可以先用 list_docs 看有哪些"
    for item in doc_entries():
        if item["path"] != want:
            continue
        full = os.path.join(_docs_base(), want)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                text = fh.read(DOC_MAX_CHARS + 1)
        except OSError:
            return None, "这份文档读不出来"
        if len(text) > DOC_MAX_CHARS:
            text = text[:DOC_MAX_CHARS] + "\n\n（后面还有，一次只回这么多字；需要的话换个更具体的问法）"
        return text, ""
    return None, "没有这份文档：" + want


# --------------------------------------------------------------------------- #
# 写动作：真正执行的那一段。它只会在用户点过"批准"之后被调用
# --------------------------------------------------------------------------- #
def _tool_roster_add_person(client, args):
    rid = int(args.get("roster_id") or 0)
    sid = str(args.get("sid") or "").strip()
    name = str(args.get("name") or "").strip()
    if not rid or not sid or not name:
        return {"error": "需要 roster_id、学号与姓名"}
    payload = {"id": rid, "sid": sid, "name": name}
    for key in ("grp", "dept", "note"):
        if args.get(key):
            payload[key] = str(args[key])[:200]
    res = client.post("/api/panel/roster/entry", payload)
    if res.get("ok") is False:
        return {"error": res.get("error") or "没加成"}
    return {"ok": True, "roster_id": rid, "sid": sid, "name": name}


def _minute_input(text):
    """``yyyy-mm-dd HH:MM`` → epoch；空串表示不限。看不懂就说看不懂。"""
    text = str(text or "").strip()
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(text, fmt))
        except ValueError:
            continue
    raise ValueError("时间要写成 yyyy-mm-dd HH:MM")


def _tool_signup_window(client, args):
    sid = int(args.get("signup_id") or 0)
    if not sid:
        return {"error": "需要 signup_id"}
    body = {"id": sid}
    try:
        for key in ("opens_at", "closes_at"):
            if key in args:
                body[key] = _minute_input(args.get(key))
    except ValueError as exc:
        return {"error": str(exc)}
    if "capacity" in args:
        body["capacity"] = max(0, int(args.get("capacity") or 0))
    if args.get("state") in ("open", "closed"):
        body["state"] = args["state"]
    if len(body) == 1:
        return {"error": "没说清要改哪一项（时间窗 / 名额 / 开关）"}
    res = client.post("/api/panel/signup/update", body)
    if res.get("ok") is False:
        return {"error": res.get("error") or "没改成"}
    return {"ok": True, "signup_id": sid, "changed": sorted(k for k in body if k != "id")}


def _tool_event_rename(client, args):
    seq = str(args.get("seq") or "").strip()
    title = str(args.get("title") or "").strip()
    if not seq or not title:
        return {"error": "需要 seq 与新的名称"}
    res = client.post("/api/panel/event/title", {"seq": seq, "title": title[:60]})
    if res.get("ok") is False:
        return {"error": res.get("error") or "没改成"}
    return {"ok": True, "seq": seq, "title": title[:60]}


def _tool_list_docs(client, args):
    q = (args.get("q") or "").strip().lower()
    rows = [d for d in doc_entries()
            if not q or q in d["path"].lower() or q in d["title"].lower()]
    if not rows:
        return {"count": 0, "docs": [], "note": "这台机器上没有装说明文档（doc/ 为空）"}
    return {"count": len(rows), "docs": rows[:DOC_LIST_MAX],
            "note": "用 read_doc 读其中一份的正文" if len(rows) <= DOC_LIST_MAX
                    else "只列出前 %d 份，用 q 缩小范围" % DOC_LIST_MAX}


def _tool_read_doc(client, args):
    text, err = read_doc(args.get("path") or "")
    if err:
        return {"error": err}
    return {"path": (args.get("path") or "").strip(), "chars": len(text), "text": text}


# OpenAI 风格的参数描述
TOOLS = [
    {"name": "list_events", "run": _tool_list_events,
     "description": "列出这个账号能看到的活动（含自己在其中的角色）",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "event_board", "run": _tool_event_board,
     "description": "读一场活动的公示数据（有什么由活动的模块决定）",
     "parameters": {"type": "object",
                    "properties": {"seq": {"type": "string",
                                           "description": "活动的 seq 或访问标识"}},
                    "required": ["seq"]}},
    {"name": "list_rosters", "run": _tool_list_rosters,
     "description": "列出名单。scope 可为 joined（我参与的）/ mine（我创建的）/ all",
     "parameters": {"type": "object",
                    "properties": {"scope": {"type": "string",
                                             "enum": ["joined", "mine", "all"]}}}},
    {"name": "roster_entries", "run": _tool_roster_entries,
     "description": "读一份名单里的成员，可按学号或姓名过滤",
     "parameters": {"type": "object",
                    "properties": {"roster_id": {"type": "integer"},
                                   "q": {"type": "string"}},
                    "required": ["roster_id"]}},
    {"name": "list_signups", "run": _tool_list_signups,
     "description": "列出报名项：状态、已报人数、名额、写进哪份名单",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "signup_entries", "run": _tool_signup_entries,
     "description": "读一次报名的提交记录",
     "parameters": {"type": "object",
                    "properties": {"signup_id": {"type": "integer"}},
                    "required": ["signup_id"]}},
    {"name": "search_people", "run": _tool_search_people,
     "description": "按学号或姓名在平台人员库里找人",
     "parameters": {"type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"]}},
    {"name": "recent_audit", "run": _tool_recent_audit,
     "description": "最近的审计日志（谁能看谁的，沿用面板权限）",
     "parameters": {"type": "object",
                    "properties": {"q": {"type": "string"}}}},
    {"name": "host_stats", "run": _tool_host_stats,
     "description": "服务器状态与在线人数（仅站长）",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_docs", "run": _tool_list_docs,
     "description": "列出随应用装好的说明文档（平台用法、接口参考）与各模块的现场说明",
     "parameters": {"type": "object",
                    "properties": {"q": {"type": "string",
                                         "description": "只看路径或标题里含这个词的"}}}},
    {"name": "read_doc", "run": _tool_read_doc,
     "description": "读一份说明文档的正文；路径来自 list_docs",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string",
                                            "description": "例如 doc/usage/rosters.md"}},
                    "required": ["path"]}},
    # ---- 写动作：调用时会先停下来等用户批准（见 run/approve） ----
    {"name": "roster_add_person", "run": _tool_roster_add_person, "write": True,
     "description": "往一份名单里加一个人。学号与姓名必填；同一个学号已在名单里就更新他的分组与备注",
     "parameters": {"type": "object",
                    "properties": {"roster_id": {"type": "integer"},
                                   "sid": {"type": "string", "description": "学号"},
                                   "name": {"type": "string", "description": "姓名"},
                                   "grp": {"type": "string", "description": "分组或班级"},
                                   "dept": {"type": "string", "description": "院系"},
                                   "note": {"type": "string"}},
                    "required": ["roster_id", "sid", "name"]},
     "summary": lambda a: "把 %s（%s）加进名单 #%s" % (
         a.get("name") or "?", a.get("sid") or "?", a.get("roster_id") or "?")},
    {"name": "signup_window", "run": _tool_signup_window, "write": True,
     "description": "改一个报名的时间窗、名额或开关（只传要改的那几项）",
     "parameters": {"type": "object",
                    "properties": {"signup_id": {"type": "integer"},
                                   "opens_at": {"type": "string",
                                                "description": "开始时间 yyyy-mm-dd HH:MM，空串表示不限"},
                                   "closes_at": {"type": "string",
                                                 "description": "截止时间 yyyy-mm-dd HH:MM，空串表示不限"},
                                   "capacity": {"type": "integer", "description": "名额，0 表示不限"},
                                   "state": {"type": "string", "enum": ["open", "closed"]}},
                    "required": ["signup_id"]},
     "summary": lambda a: "改报名 #%s 的%s" % (
         a.get("signup_id") or "?",
         "、".join(k for k in ("opens_at", "closes_at", "capacity", "state") if k in a) or "设置")},
    {"name": "event_rename", "run": _tool_event_rename, "write": True,
     "description": "给一场活动改名称",
     "parameters": {"type": "object",
                    "properties": {"seq": {"type": "string", "description": "活动的 seq 或访问标识"},
                                   "title": {"type": "string"}},
                    "required": ["seq", "title"]},
     "summary": lambda a: "把活动 #%s 改名为「%s」" % (a.get("seq") or "?", a.get("title") or "")},
]

BY_NAME = {t["name"]: t for t in TOOLS}

SYSTEM_PROMPT = (
    "你是 CAEventManager 的运维助手。这个平台用来管理社团活动、名单与报名。"
    "规则：\n"
    "1. 只回答平台范围内的事；不清楚就先调用工具查，别猜。\n"
    "2. 你只能做当前账号有权做的事。工具报无权时照实说，别绕。\n"
    "3. 要改数据就直接调用对应的写工具：往名单里加人、改报名的时间窗与名额、改活动名称。"
    "调用之后平台会把动作摆到用户面前，用户点了批准才真的写进去；没批准就当没做成，"
    "照实说一句，不要换个说法反复重试。这三件以外的事平台不开放，告诉用户去哪个页面操作，"
    "并说清是哪个页面。\n"
    "4. 回答用简体中文、书面语，简洁；数字与名字要准确，不要编造。\n"
    "5. 不要把学号、姓名等个人信息成段罗列，只给用户当下需要的那部分。\n"
    "6. 讲到平台怎么用时（页面在哪、字段什么意思、部署与备份怎么做），先用 list_docs / "
    "read_doc 读随应用装好的说明文档，别凭印象说；模块自己的现场操作在模块的 doc.md 里。\n"
    "7. 页面位置与功能一律以文档为准。文档没写出来的能力与功能，就当作平台没有：不要推测，"
    "也不要把它安到一个看起来像的页面上。拿不到准确位置时直说文档里没有这项说明，"
    "或者请对方问管理员；给出错误的位置比说不知道更糟。"
)


# --------------------------------------------------------------------------- #
# 平台自己的提示词：一份放在数据目录里的 markdown，手工改，改完下一句就生效
# --------------------------------------------------------------------------- #
# 位置可以改到别处（测试与特殊部署用）；默认就在数据目录里，和数据库作伴，
# 重新部署不会覆盖它。内容是**追加**在下面这些固定规则之后的，不用重抄一遍规则。
PROMPT_NAME = "assistant_prompt.md"
PROMPT_ENV = "LOTTERY_ASSISTANT_PROMPT"
PROMPT_MAX = 20000            # 一份提示词写成小说就没意义了，多出来的截掉

_prompt = {"mtime": None, "text": ""}


def prompt_path():
    """平台提示词放在哪：``LOTTERY_ASSISTANT_PROMPT`` 优先，否则数据目录里那份。"""
    where = os.environ.get(PROMPT_ENV)
    if where:
        return os.path.abspath(where)
    return os.path.join(db.DATA_DIR, PROMPT_NAME)


def platform_prompt():
    """读一份平台提示词。文件不在、读不动、空着，都当没有。

    按 mtime 缓存：改完文件不用重启，也不用每个请求都读一次磁盘。
    """
    path = prompt_path()
    try:
        stat = os.stat(path)
    except OSError:
        _prompt["mtime"], _prompt["text"] = None, ""
        return ""
    if _prompt["mtime"] == stat.st_mtime:
        return _prompt["text"]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read(PROMPT_MAX + 1)[:PROMPT_MAX].strip()
    except OSError:
        text = ""
    _prompt["mtime"], _prompt["text"] = stat.st_mtime, text
    return text


def _who(values):
    """「张三（管理员；zhangsan）」：名字、身份、登录名里不重复的那几个。"""
    name = values.get("user.name") or ""
    rest = [x for x in (values.get("user.role") or "", values.get("user.username") or "")
            if x and x != name]
    return name + ("（%s）" % "；".join(rest) if rest else "")


def doc_catalog():
    """能读的文档清单（路径 + 标题），写进系统提示：不塞正文，只告诉它有哪些。"""
    return ["%s（%s）" % (item["path"], item["title"]) for item in doc_entries()]


def system_prompt(account=None, tools=None, version=""):
    """发给模型的那段系统提示：固定规则 + 当前是谁 + 可读文档清单 + 平台提示词。"""
    values = prompt_values(account, tools, version)
    parts = [SYSTEM_PROMPT]
    catalog = doc_catalog()
    if catalog:
        parts.append("可读的说明文档（用 read_doc 读其中一份）：\n"
                     + "\n".join("- " + line for line in catalog))
    if values.get("user.username"):
        # 助手光靠工具清单看不出自己在跟谁说话：把人和身份说清楚，语气与能做的事才对得上
        parts.append("当前使用者：%s。" % _who(values))
    extra = platform_prompt()
    if extra:
        parts.append("平台补充说明：\n" + fill_prompt(extra, values))
    return "\n\n".join(parts)


# 提示词里可以写的变量：``${user.role}``、``${time.date}`` 这种。一次请求算一份。
# 没定义的留在原样——写错了能一眼看见，好过悄悄变成空白。
PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z][A-Za-z0-9_]*)(?:\.([A-Za-z][A-Za-z0-9_]*))?\}")


def prompt_values(account=None, tools=None, version=""):
    """提示词里能用的变量。`{类.名: 值}`，类与名都只认字母数字下划线。"""
    def pick(row, name, default=""):
        try:
            value = row[name]
        except (KeyError, IndexError, TypeError):
            return default
        return default if value is None else value

    now = time.localtime()
    values = {
        "panel.name": "CAEventManager",
        "panel.version": version or "",
        "time.date": time.strftime("%Y-%m-%d", now),
        "time.time": time.strftime("%H:%M", now),
        "time.now": time.strftime("%Y-%m-%d %H:%M", now),
        "tools": "、".join(tools or ()),
    }
    if account is not None:
        role = pick(account, "role")
        values["user.username"] = pick(account, "username")
        values["user.name"] = pick(account, "name") or values["user.username"]
        values["user.role"] = panel.ROLE_LABEL.get(role, role)
        values["user.roleKey"] = role
        values["user.id"] = pick(account, "id")
    return values


def fill_prompt(text, values):
    """把 ``${类.名}`` 换成值；没定义的原样留着。"""
    if not text:
        return ""

    def sub(match):
        key = match.group(1) + ("." + match.group(2) if match.group(2) else "")
        if key in values:
            return str(values[key])
        return match.group(0)

    return PLACEHOLDER_RE.sub(sub, text)


def tool_schema(account=None):
    """The `tools` array an OpenAI-compatible endpoint expects.

    ``account`` 给了就只发它用得上的那些：看得见的权限才看得见工具，
    别让模型先去撞一次"你无权查看"再学会。
    """
    allow = set(policy.visible(account)) if account is not None else None
    out = []
    for tool in TOOLS:
        if allow is not None and tool["name"] not in allow:
            continue
        out.append({"type": "function",
                    "function": {"name": tool["name"], "description": tool["description"],
                                 "parameters": tool["parameters"] or {"type": "object",
                                                                      "properties": {}}}})
    return out


# --------------------------------------------------------------------------- #
# 与模型对话
# --------------------------------------------------------------------------- #
def _post_chat(cfg, messages, account=None):
    base = (cfg.get("base") or "").rstrip("/")
    if not base.endswith("/chat/completions"):
        base = base + "/chat/completions"
    body = {"model": cfg.get("model"), "messages": messages, "temperature": 0.2,
            "stream": False}
    tools = tool_schema(account)
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        base, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["key"],
                 "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def _parse_arguments(raw):
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def suggest_title(cfg, text):
    """给一段对话起个短标题。

    名字只是好看一点：模型不给、调不通、回一段废话，都当没有，前端照旧用第一句话。
    所以这里既不抛错，也不写审计。
    """
    if not cfg.get("ready") or not str(text or "").strip():
        return ""
    base = (cfg.get("base") or "").rstrip("/")
    if not base.endswith("/chat/completions"):
        base = base + "/chat/completions"
    body = {"model": cfg.get("model"), "temperature": 0.2, "max_tokens": 32,
            "stream": False,
            "messages": [{"role": "system", "content": TITLE_RULE},
                         {"role": "user", "content": str(text)[:800]}]}
    try:
        req = urllib.request.Request(
            base, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + cfg["key"],
                     "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            payload = json.loads(res.read().decode("utf-8") or "{}")
        choice = (payload.get("choices") or [{}])[0]
        raw = ((choice.get("message") or {}).get("content") or "")
    except Exception:
        return ""
    title = " ".join(str(raw).split()).strip(" \t\"'“”‘’。，、！？：:,.!?-—")
    return title[:TITLE_MAX]


# --------------------------------------------------------------------------- #
# 一轮问答的执行：遇到写动作就停在那里，等人点"批准"
# --------------------------------------------------------------------------- #
# 写动作不会自己跑：碰到它就把握把（模型上下文 + 还没跑的那几个调用 + 调用它的那条会话）
# 存进内存，回一个 pending 给界面；用户点批准后才继续（approve）。令牌一次性、5 分钟过期，
# 存的是内存里的握把，不落盘——重启就作废，重来一次即可。
PENDING_TTL = 300
_PENDING = {}


def _drop_stale():
    now = time.time()
    for token in [t for t, r in _PENDING.items() if now - r["at"] > PENDING_TTL]:
        _PENDING.pop(token, None)


def _stash(acc, cfg, client, convo, steps, calls, version):
    import secrets
    _drop_stale()
    token = secrets.token_urlsafe(16)
    _PENDING[token] = {"at": time.time(), "acc": acc["id"], "cfg": cfg,
                       "client": client, "convo": convo, "steps": steps,
                       "calls": calls, "version": version}
    return token


def _pending_view(token, call):
    fn = (call.get("function") or {})
    name = fn.get("name") or ""
    args = _parse_arguments(fn.get("arguments"))
    tool = BY_NAME.get(name) or {}
    summary = ""
    try:
        summary = tool["summary"](args) if tool.get("summary") else ""
    except Exception:                                   # 说人话的那一句不值得炸整轮
        summary = ""
    return {"token": token, "action": name,
            "label": policy.describe(name) or name,
            "summary": summary or (policy.describe(name) or name)}


def _call_tool(acc, client, call):
    """执行一个工具调用，返回给模型看的结果（写动作也是从这里走的，只是在批准之后）。"""
    fn = (call.get("function") or {})
    name = fn.get("name") or ""
    args = _parse_arguments(fn.get("arguments"))
    tool = BY_NAME.get(name)
    allowed, reason = policy.verdict(acc, name, args)
    if tool is None:
        result = {"error": "没有这个工具"}
    elif not allowed:
        result = {"error": reason}
    else:
        try:
            result = tool["run"](client, args)
        except Exception as exc:                        # 工具自己出错不该炸整轮
            result = {"error": "工具执行失败：%s" % exc}
    # 接口那边的 403 不必原样转述：模型只要知道"这条路走不通"就够了
    err = str(result.get("error") or "")
    if "403" in err or "无权" in err or "权限" in err:
        result = {"error": "这一步没有权限，换一种问法或者换个账号试试"}
    return name, args, result


def _drive(acc, cfg, client, convo, steps, version, calls):
    """跑模型这一轮。``calls`` 非空表示从"已经问过模型、手上还有几个调用"接着跑。"""
    for _ in range(MAX_STEPS):
        if calls is None:
            payload = _post_chat(cfg, convo, account=acc)
            msg = ((payload.get("choices") or [{}])[0].get("message") or {})
            calls = msg.get("tool_calls") or []
            if not calls:
                return {"reply": (msg.get("content") or "").strip(), "steps": steps}
            convo.append({"role": "assistant", "content": msg.get("content") or "",
                          "tool_calls": calls})
        while calls:
            call = calls[0]
            fn = (call.get("function") or {})
            name = fn.get("name") or ""
            tool = BY_NAME.get(name) or {}
            allowed, _reason = policy.verdict(acc, name, _parse_arguments(fn.get("arguments")))
            if tool.get("write") and allowed:
                # 停在这里：剩下的调用跟着握把一起存起来，等人点头
                token = _stash(acc, cfg, client, convo, steps, calls, version)
                return {"reply": "", "steps": steps,
                        "pending": _pending_view(token, call)}
            name, _args, result = _call_tool(acc, client, call)
            steps.append({"tool": name, "ok": "error" not in result,
                          "note": result.get("error") or ""})
            convo.append({"role": "tool", "tool_call_id": call.get("id") or name,
                          "content": _clip(json.dumps(result, ensure_ascii=False), 6000)})
            calls = calls[1:]
        calls = None
    return {"reply": "这一步想问的东西有点多，先说到这里。可以换个更具体的问法。",
            "steps": steps}


def run(conn, acc, client, cfg, messages, log=print, version=""):
    """One question in, one answer out.  Returns {reply, steps} or {pending}."""
    names = [t["function"]["name"] for t in tool_schema(acc)]
    convo = [{"role": "system", "content": system_prompt(acc, names, version)}]
    for m in (messages or [])[-20:]:
        role = m.get("role")
        if role in ("user", "assistant") and (m.get("content") or "").strip():
            convo.append({"role": role, "content": str(m["content"])[:4000]})
    if not convo or convo[-1]["role"] != "user":
        raise ValueError("最后一句应该是用户的话")
    return _drive(acc, cfg, client, convo, [], version, None)


def approve(acc, token, allow):
    """用户对某个写动作点了批准或拒绝：接着把这一轮跑完。

    ``allow`` 为假时把"用户拒绝了"写进工具结果，让模型自己收尾——它知道该说没改成。
    """
    _drop_stale()
    grip = _PENDING.pop(str(token or ""), None)
    if grip is None or grip["acc"] != acc["id"]:
        raise ValueError("这次确认已经过期，请重新问一遍")
    calls = grip["calls"]
    call = calls[0]
    fn = (call.get("function") or {})
    name = fn.get("name") or ""
    if allow:
        name, _args, result = _call_tool(acc, grip["client"], call)
    else:
        result = {"ok": False, "refused": "用户拒绝了这一步，没有执行"}
    grip["steps"].append({"tool": name, "ok": "error" not in result
                          and not result.get("refused"),
                          "note": result.get("error") or ("已拒绝" if not allow else "")})
    grip["convo"].append({"role": "tool", "tool_call_id": call.get("id") or name,
                          "content": _clip(json.dumps(result, ensure_ascii=False), 6000)})
    return _drive(acc, grip["cfg"], grip["client"], grip["convo"], grip["steps"],
                  grip["version"], calls[1:])
