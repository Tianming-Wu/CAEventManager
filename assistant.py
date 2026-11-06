# -*- coding: utf-8 -*-
"""运维助手：把"查一下、改一下"变成一次对话。

三条硬约束（与 `doc/ca-event-manager-assistant.md` 一致）：

1. **权限不另立一套**：助手以当前登录账号的会话发出请求，打到本机自己的接口上。
   权限、CSRF 标记、并发冲突、审计全部沿用现成那一套，一处都不用重写。
2. **模型在服务端调**：`base_url` / `model` / `api_key` 每人一份，key 只写不读，
   浏览器永远拿不到它。
3. **放行只走一个口**：每个动作先问 `policy.verdict()`，默认拒绝。

对话本身不落库：只留"调了什么、改了什么"的审计（在接口那一侧自然写下的）。
"""

import json
import urllib.error
import urllib.request

import policy

# 一轮对话里最多来回几次工具调用，防止模型绕圈
MAX_STEPS = 6
TIMEOUT = 60
UA = "CAEventManager-assistant/1"


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
    conn.execute("UPDATE accounts SET %s WHERE id=?" % ", ".join(sets), args)
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


# OpenAI 风格的参数描述
TOOLS = [
    {"name": "list_events", "run": _tool_list_events,
     "description": "列出这个账号能看到的活动（含自己在其中的角色）",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "event_board", "run": _tool_event_board,
     "description": "读一场活动的公示数据：人数、签到、奖票、奖品与最近中奖",
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
]

BY_NAME = {t["name"]: t for t in TOOLS}

SYSTEM_PROMPT = (
    "你是 CAEventManager 的运维助手。这个平台用来管理社团活动、名单与报名。"
    "规则：\n"
    "1. 只回答平台范围内的事；不清楚就先调用工具查，别猜。\n"
    "2. 你只能做当前账号有权做的事。工具报无权时照实说，别绕。\n"
    "3. 改数据的动作目前一律不开放：告诉用户去对应页面操作，并说清是哪个页面。\n"
    "4. 回答用简体中文、书面语，简洁；数字与名字要准确，不要编造。\n"
    "5. 不要把学号、姓名等个人信息成段罗列，只给用户当下需要的那部分。"
)


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


def run(conn, acc, client, cfg, messages, log=print):
    """One question in, one answer out.  Returns {reply, steps}."""
    convo = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in (messages or [])[-20:]:
        role = m.get("role")
        if role in ("user", "assistant") and (m.get("content") or "").strip():
            convo.append({"role": role, "content": str(m["content"])[:4000]})
    if not convo or convo[-1]["role"] != "user":
        raise ValueError("最后一句应该是用户的话")

    steps = []
    for _ in range(MAX_STEPS):
        payload = _post_chat(cfg, convo, account=acc)
        choice = (payload.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"reply": (msg.get("content") or "").strip(), "steps": steps}
        convo.append({"role": "assistant", "content": msg.get("content") or "",
                      "tool_calls": calls})
        for call in calls:
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
                except Exception as exc:                    # 工具自己出错不该炸整轮
                    result = {"error": "工具执行失败：%s" % exc}
            # 接口那边的 403 不必原样转述：模型只要知道"这条路走不通"就够了
            err = str(result.get("error") or "")
            if "403" in err or "无权" in err or "权限" in err:
                result = {"error": "这一步没有权限，换一种问法或者换个账号试试"}
            steps.append({"tool": name, "ok": "error" not in result,
                          "note": result.get("error") or ""})
            convo.append({"role": "tool", "tool_call_id": call.get("id") or name,
                          "content": _clip(json.dumps(result, ensure_ascii=False), 6000)})
    return {"reply": "这一步想问的东西有点多，先说到这里。可以换个更具体的问法。",
            "steps": steps}
