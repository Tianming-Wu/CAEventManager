#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit checks for the pure functions the panel leans on.

    python tools/unit_test.py

These run without a server.  They exist because ``host_stats()`` shipped with a
``NameError`` that no local test could reach: the failure happened after reading
``/proc/stat``, and Windows has no ``/proc``, so every run returned early.
Feeding it a fake ``/proc`` exercises the real code path on any platform.
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

import app  # noqa: E402

problems = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        problems.append(name)


def fake_proc(stat_a, stat_b, meminfo):
    """A /proc with two readings, so the CPU delta is deterministic."""
    root = tempfile.mkdtemp(prefix="fake-proc-")
    state = {"reads": 0}
    values = [stat_a, stat_b]

    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path).endswith("stat"):
            idx = min(state["reads"], 1)
            state["reads"] += 1
            tmp = os.path.join(root, "current-stat")
            with real_open(tmp, "w", encoding="utf-8") as fh:
                fh.write(values[idx])
            return real_open(tmp, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    with real_open(os.path.join(root, "meminfo"), "w", encoding="utf-8") as fh:
        fh.write(meminfo)
    return root, fake_open


OLD_PANEL_DDL = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'admin',
    status TEXT NOT NULL DEFAULT 'pending', pass TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
    decided_at REAL NOT NULL DEFAULT 0, decided_by TEXT NOT NULL DEFAULT '',
    last_login REAL NOT NULL DEFAULT 0
);
CREATE TABLE events (
    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, archived_at REAL NOT NULL DEFAULT 0
);
"""


def migrate_events():
    """An installation from before the id/token split must come out readable."""
    import sqlite3

    import panel

    root = tempfile.mkdtemp(prefix="panel-migrate-")
    path = os.path.join(root, "panel.sqlite3")
    db = sqlite3.connect(path)
    db.executescript(OLD_PANEL_DDL)
    db.execute("INSERT INTO accounts (username, name, role, status, pass, created_at)"
               " VALUES ('owner','老站长','owner','active','x',?)", (time.time(),))
    db.execute("INSERT INTO events (id, title, owner, note, created_at)"
               " VALUES (?,?,?,?,?)",
               ("Old" + "A" * 29, "旧活动", "nobody", "note", time.time() - 60))
    db.commit()
    db.close()

    previous = os.environ.get("LOTTERY_DATA_DIR")
    os.environ["LOTTERY_DATA_DIR"] = root
    try:
        panel.init_db()
        conn = panel.connect()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        check("the events table has the new columns",
              {"seq", "token", "kind", "params", "owner_id"} <= columns, sorted(columns))
        row = conn.execute("SELECT * FROM events").fetchone()
        check("the token is kept", row["token"] == "Old" + "A" * 29, dict(row))
        check("a numeric id is assigned", row["seq"] == 1, row["seq"])
        check("the old title survives", row["title"] == "旧活动", row["title"])
        check("a kind is recorded", bool(row["kind"]), row["kind"])
        member = conn.execute("SELECT * FROM event_members").fetchone()
        check("the owner becomes a member",
              member is not None and member["role"] == "owner", dict(member) if member else None)
        check("routing still finds the token",
              "Old" + "A" * 29 in panel.event_tokens(), sorted(panel.event_tokens()))
        view = panel.event_view(row, "https://example.test/prefix", "owner")
        check("links are rebuilt from the token",
              view["admin"] == "https://example.test/prefix/Old" + "A" * 29 + "/admin",
              view["admin"])

        print("== a second run must not migrate again ==")
        panel.init_db()
        again = panel.connect().execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        check("no duplicate rows", again == 1, again)
    finally:
        if previous is None:
            os.environ.pop("LOTTERY_DATA_DIR", None)
        else:
            os.environ["LOTTERY_DATA_DIR"] = previous


BAD_EXPRESSIONS = [
    '__import__("os")',
    "open",
    "a.b",
    "[1][0]",
    "x if 1 else 2",
    "signed_in ** 99",
    "2 / 0",
    "nope + 1",
    "lambda: 1",
    "[x for x in (1, 2)]",
    "open('x')",
]


def expressions():
    """The pool expression must add numbers and nothing else."""
    import expr

    ctx = {"signed_in": 12, "staff": 3, "guaranteed": 30}
    check("arithmetic works", expr.evaluate("signed_in * 3 + 20", ctx) == 56)
    check("functions work", expr.evaluate("max(signed_in, staff) * 2", ctx) == 24)
    check("names are reported",
          expr.names("signed_in*3+guaranteed") == ["guaranteed", "signed_in"],
          expr.names("signed_in*3+guaranteed"))
    check("unknown names are caught before running",
          [n for n in expr.names("a + b") if n not in ctx] == ["a", "b"])
    accepted = []
    for bad in BAD_EXPRESSIONS:
        try:
            expr.evaluate(bad, ctx)
            accepted.append(bad)
        except expr.ExprError:
            continue
    check("nothing outside arithmetic is accepted", not accepted, accepted)
    check("an empty expression is refused",
          refuses(expr, "", ctx))

    print("== the ticket pool follows the expression ==")
    import db
    import model

    root = tempfile.mkdtemp(prefix="expr-pool-")
    previous = os.environ.get("LOTTERY_DATA_DIR")
    os.environ["LOTTERY_DATA_DIR"] = root
    db.use_data_dir(root)
    try:
        db.set_event(1)
        db.init_db("nwuca.lottery")
        conn = db.connect()
        with db.tx(conn):
            db.set_config_raw(conn, "pool_mode", "expr")
            db.set_config_raw(conn, "pool_expr", "signed_in * 2 + 10")
        check("an expression drives the pool",
              model.effective_pool(conn) == 10, model.effective_pool(conn))
        with db.tx(conn):
            db.set_config_raw(conn, "pool_expr", "signed_in * 99")
        check("and it is re-evaluated each time",
              model.effective_pool(conn) == 0, model.effective_pool(conn))
    finally:
        if previous is None:
            os.environ.pop("LOTTERY_DATA_DIR", None)
        else:
            os.environ["LOTTERY_DATA_DIR"] = previous


def refuses(expr, src, ctx):
    try:
        expr.evaluate(src, ctx)
        return False
    except expr.ExprError:
        return True


def _seed_batch(students, prizes, pool=40, inside=True):
    """A throwaway activity: everyone present with one ticket, then finalized."""
    import db
    import model

    root = tempfile.mkdtemp(prefix="batch-")
    os.environ["LOTTERY_DATA_DIR"] = root
    db.use_data_dir(root)
    db.set_event(1)
    db.init_db("nwuca.lottery")
    conn = db.connect()
    with db.tx(conn):
        db.set_config_raw(conn, "pool_mode", "fixed")
        db.set_config_raw(conn, "pool_size", pool)
        db.set_config_raw(conn, "draw_unique_inside", inside)
        db.set_config_raw(conn, "draw_unique_across", False)
        for i, name in enumerate(students):
            model.add_student(conn, "2026%04d" % i, name)
        ids = {s["sid"]: s["id"] for s in model.snapshot(conn)}
        for sid in ids:
            model.mark(conn, ids[sid], "checkin")
        model.finalize(conn)
        db.set_config_raw(conn, "prizes", prizes)
    return db, model, conn


def draw_batches():
    """「一次抽完」: one click fills the prize, tickets still go one by one."""
    if "LOTTERY_DATA_DIR" in os.environ:
        previous = os.environ["LOTTERY_DATA_DIR"]
    else:
        previous = None
    try:
        db, model, conn = _seed_batch(
            ["甲一", "乙二", "丙三", "丁四", "戊五", "己六"],
            [{"name": "三等奖", "count": 3, "mode": "ticket", "batch": True}],
        )
        with db.tx(conn):
            res = model.draw_batch(conn, "三等奖", "ticket")
        draws = res["draws"]
        check("一次抽完把名额抽满", len(draws) == 3, len(draws))
        check("每一份各消耗一张不同的奖票",
              len({d["code"] for d in draws}) == 3, [d["code"] for d in draws])
        check("奖项内去重跨整批生效",
              len({d["student_id"] for d in draws}) == 3)
        check("同一批共用一个批次号",
              {d["batch"] for d in draws} == {res["batch"]})
        check("抽完一遍就没有剩余",
              model.prize_status(conn, db.all_config(conn))[0]["remaining"] == 0)
        last = model.last_batch(conn)
        check("大屏能拿到整批名单",
              bool(last) and len(last["winners"]) == 3, last)
        refused = False
        try:
            with db.tx(conn):
                model.draw_batch(conn, "三等奖", "ticket")
        except model.ApiError:
            refused = True
        check("抽完的奖不能再抽", refused)
        unknown = False
        try:
            with db.tx(conn):
                model.draw_batch(conn, "没有这个奖", "ticket")
        except model.ApiError:
            unknown = True
        check("不在奖品清单里的奖不能一次抽完", unknown)

        print("== 一次抽完在材料不够时停下 ==")
        db, model, conn = _seed_batch(
            ["甲一", "乙二"],
            [{"name": "三等奖", "count": 5, "mode": "ticket", "batch": True}],
        )
        with db.tx(conn):
            res = model.draw_batch(conn, "三等奖", "ticket")
        check("抽到没有票可抽就停",
              len(res["draws"]) == 2 and res["stopped"], res)
        check("停下时说明原因", "票" in (res["stopped"] or ""), res["stopped"])

        print("== 按人抽的奖也能一次抽完 ==")
        db, model, conn = _seed_batch(
            ["甲一", "乙二", "丙三"],
            [{"name": "特等奖", "count": 2, "mode": "person", "batch": True}],
        )
        with db.tx(conn):
            res = model.draw_batch(conn, "特等奖", "person")
        check("按人抽的奖抽满名额", len(res["draws"]) == 2, res["draws"])
        check("按人抽的奖批内不重复",
              len({d["student_id"] for d in res["draws"]}) == 2)
        check("按人抽的奖不占奖票",
              all(d["code"] is None for d in res["draws"]))
        db, model, conn = _seed_batch(
            ["甲一", "乙二"],
            [{"name": "特等奖", "count": 4, "mode": "person", "batch": True}],
        )
        with db.tx(conn):
            res = model.draw_batch(conn, "特等奖", "person")
        check("到场的人抽完就停",
              len(res["draws"]) == 2 and res["stopped"], res)
    finally:
        if previous is None:
            os.environ.pop("LOTTERY_DATA_DIR", None)
        else:
            os.environ["LOTTERY_DATA_DIR"] = previous


def screen_packs():
    """上传的 zip 要先变成"路径 + 字节"才允许落盘，这里逐条试拒绝分支。"""
    import io
    import zipfile

    import packs

    root = tempfile.mkdtemp(prefix="packs-")

    def make(entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries:
                zf.writestr(name, data)
        return buf.getvalue()

    good = [("screen.html", b"<html></html>"), ("pack.css", b"body{}"),
            ("img/bg.png", b"\x89PNG\r\n\x1a\n")]
    got = packs.install(make(good), "Hall Screen.zip", root=root)
    check("一包装上后有自己的标识", got["id"] == "hall-screen", got)
    check("入口页在包里", os.path.isfile(
        os.path.join(root, "hall-screen", "screen.html")))
    check("包内子目录保留", os.path.isfile(
        os.path.join(root, "hall-screen", "img", "bg.png")))
    again = packs.install(make(good), "Hall Screen.zip", root=root)
    check("同名不覆盖，另起一个标识", again["id"] == "hall-screen-2", again)
    check("清单里两项都在",
          [p["id"] for p in packs.listing(root) if not p["builtin"]]
          == ["hall-screen", "hall-screen-2"])
    check("清单带上文件数与体积",
          packs.listing(root)[0]["files"] == 3 and packs.listing(root)[0]["size"] > 0,
          packs.listing(root)[0])

    print("== 危险的条目一律丢弃 ==")
    evil = make(good + [("../up.js", b"x"), ("/abs.js", b"x"), ("c/../../d.js", b"x"),
                        ("ads.js:zone", b"x"), (".hidden.js", b"x"),
                        ("note.txt", b"x"), ("deeper/page.html", b"x")])
    files, skipped = packs.check_zip(evil)
    check("只留下认得的三份",
          sorted(files) == ["img/bg.png", "pack.css", "screen.html"], sorted(files))
    check("其余七份被退回", len(skipped) == 7, skipped)

    print("== 符号链接不收 ==")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("screen.html", b"ok")
        info = zipfile.ZipInfo("link.js")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, b"/etc/passwd")
    files, skipped = packs.check_zip(buf.getvalue())
    check("链接被退回", "link.js" not in files and skipped == ["link.js"], skipped)

    print("== 不合要求的包直接拒收 ==")

    def refused(name, blob, word):
        try:
            packs.check_zip(blob)
        except packs.PackError as exc:
            check("%s（%s）" % (name, exc), word in str(exc), str(exc))
            return
        check(name, False, "没有报错")

    refused("缺入口页", make([("pack.css", b"x")]), "screen.html")
    refused("不是 zip", b"this is text", "zip")
    refused("单文件超过上限",
            make([("screen.html", b"ok" * (packs.FILE_MAX // 2 + 1))]), "单文件上限")
    refused("压缩包本身超过上限",
            make([("screen.html", os.urandom(packs.ZIP_MAX + 1024))]), "压缩包超过")
    refused("文件数过多", make([("screen.html", b"ok")]
                            + [("f%d.js" % i, b"x") for i in range(packs.COUNT_MAX)]),
            "文件数")
    refused("压缩比异常", make([("screen.html", b"ok"),
                             ("bomb.js", b"\0" * 1024 * 1024)]), "压缩比")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("screen.html", b"1")
        zf.writestr("screen.html", b"2")
    refused("重名文件", buf.getvalue(), "重名")

    print("== 删除只对导入的那一份有效 ==")
    check("删得掉自己导入的",
          packs.remove("hall-screen-2", root=root)
          and not os.path.isdir(os.path.join(root, "hall-screen-2")))
    for bad, word in (("..", "不合法"), ("../etc", "不合法"),
                      ("classic", "随发布走"), ("nope", "找不到")):
        try:
            packs.remove(bad, root=root)
            check("不该删掉 %s" % bad, False)
        except packs.PackError as exc:
            check("拒绝删除 %s（%s）" % (bad, exc), word in str(exc), str(exc))

    print("== 标识从文件名来 ==")
    check("转成小写并去掉空格",
          packs.slug("My Pack.v2.ZIP") == "my-pack.v2", packs.slug("My Pack.v2.ZIP"))
    check("没有可用字符时留空",
          packs.slug("拍照 大屏.zip") == "", packs.slug("拍照 大屏.zip"))


def accounts_and_tokens():
    """登录名规则与 API 令牌的纯函数部分。"""
    import panel

    print("== 登录名 ==")
    for good in ("abc", "Zhang_2025", "a-b_c", "A1" * 6):
        check("收下 %r" % good, panel.username_problem(good) == "", good)
    for bad in ("ab", "a" * 25, "a.b", "张老师", "has space", "a+b", ""):
        check("拒绝 %r" % bad, panel.username_problem(bad) != "", bad)
    check("保留词被当成「不可用」",
          panel.username_problem("admin") == panel.USERNAME_TAKEN)
    check("大小写不影响保留词",
          panel.username_problem("ADMIN") == panel.USERNAME_TAKEN)
    check("格式问题与「不可用」是两句话",
          panel.username_problem("a.b") != panel.USERNAME_TAKEN)

    print("== 令牌 ==")
    header, head, tail = panel.new_token()
    check("带 cae- 前缀", header.startswith(panel.TOKEN_PREFIX), header)
    check("明文中段不落库的形状", len(head) == 4 and len(tail) == 5, (head, tail))
    masked = panel.mask_token(head, tail)
    check("打码只看得到首尾", head in masked and tail in masked
          and header[4:-5] not in masked, masked)
    check("权限只认已知的几档",
          panel.normalize_scopes(["write", "nonsense"]) == ["write"],
          panel.normalize_scopes(["write", "nonsense"]))
    # 空权限的令牌一次请求都发不出去（连自己是谁都问不出来），所以不补默认值、直接拒绝
    check("一条都不给就是空的，不偷偷补一个 read",
          panel.normalize_scopes([]) == [] and panel.normalize_scopes(["nope"]) == [],
          panel.normalize_scopes([]))


def main():
    print("== host_stats reads a Linux /proc ==")
    # cpu lines: user nice system idle iowait irq softirq steal
    first = "cpu  100 0 100 800 0 0 0 0 0 0\n"
    second = "cpu  150 0 150 900 0 0 0 0 0 0\n"   # +100 busy, +200 total -> 50%
    meminfo = "MemTotal:       1000000 kB\nMemAvailable:    250000 kB\n"
    root, fake_open = fake_proc(first, second, meminfo)

    real_open = open
    builtins = sys.modules["builtins"]
    builtins.open = fake_open
    try:
        stats = app.host_stats(root=root, sleep=0)
    finally:
        builtins.open = real_open

    check("it returns something", stats is not None, stats)
    if not stats:
        return
    check("cpu is the delta, not the average", stats["cpu"] == 50.0, stats["cpu"])
    check("memory is used/total", abs(stats["mem"] - 75.0) < 0.01, stats["mem"])
    check("total memory in MB", abs(stats["memTotalMB"] - 1000000 / 1024.0) < 0.01,
          stats["memTotalMB"])
    check("host name is filled", bool(stats["host"]), stats["host"])
    check("uptime is positive", stats["uptime"] > 0, stats["uptime"])
    check("version is reported", stats["version"] == app.VERSION, stats["version"])
    check("data dir is reported", bool(stats["dataDir"]), stats["dataDir"])

    print("== it says so when /proc is not there ==")
    missing = app.host_stats(root=os.path.join(root, "nope"), sleep=0)
    check("missing /proc returns None", missing is None, missing)

    print("== it survives a mangled /proc ==")
    root2, fake2 = fake_proc("cpu\n", "cpu\n", "garbage\n")
    builtins.open = fake2
    try:
        broken = app.host_stats(root=root2, sleep=0)
    finally:
        builtins.open = real_open
    check("a broken file returns None instead of raising", broken is None, broken)

    print("== the expression language ==")
    expressions()

    print("== the event table migration ==")
    migrate_events()

    print("== draws made in one go ==")
    draw_batches()

    print("== 展示屏幕的导入 ==")
    screen_packs()

    print("== 登录名与令牌 ==")
    accounts_and_tokens()

    print("== 服务日志轮转 ==")
    with tempfile.TemporaryDirectory() as box:
        path = os.path.join(box, "service.log")
        for i in range(4):
            name = path if i == 0 else "%s.%d" % (path, i)
            with open(name, "w", encoding="utf-8") as fh:
                fh.write("第 %d 份\n" % i)
        check("轮转前只有一份是正的", os.path.isfile(path) and not
              os.path.exists(path + ".4"))
        app.rotate_file(path)
        check("正在写的那份挪成 .1", not os.path.exists(path))
        check("旧的依次往后挪",
              open(path + ".1", encoding="utf-8").read() == "第 0 份\n"
              and open(path + ".2", encoding="utf-8").read() == "第 1 份\n"
              and open(path + ".3", encoding="utf-8").read() == "第 2 份\n")
        check("最多留三份老的", not os.path.exists(path + ".4"))
        check("文件不在就当没这回事", app.rotate_file(os.path.join(box, "nope.log")) is False)

    print("== 模块的能力声明（module.json 的 features）==")
    errs = []
    got = app.modules.resolve_features(
        {"search": {"name": "xm", "records": "students", "key": "id"},
         "nope": 1}, errs)
    check("申请了的才给，没申请的丢掉",
          list(got) == ["search"] and "没有这个能力：nope" in errs, (got, errs))
    check("没写的字段用默认值",
          got["search"]["fields"] == ["grp", "note"] and got["search"]["tags"] == [],
          got["search"])
    check("写了的字段照抄", got["search"]["name"] == "xm", got["search"]["name"])
    check("列表写法的申请等价于空声明",
          app.modules.resolve_features(["search"])["search"]["name"] == "name")
    bad = []
    check("声明写错就丢掉这个能力",
          app.modules.resolve_features({"search": "x"}, bad) == {}
          and bad and "应该是一个对象" in bad[-1], bad)
    lottery = app.modules.get("nwuca.lottery", app.VERSION)
    perms = (lottery or {}).get("features") or {}
    check("抽奖模块声明了搜索", "search" in perms, list(perms))
    check("它的词表里有工作人员与未签到",
          any("w" in t["keys"] for t in perms["search"]["tags"])
          and any("未签到" in t["keys"] for t in perms["search"]["tags"]),
          [t.get("label") for t in perms["search"]["tags"]])
    rec = {"id": 1, "sid": "2024001", "name": "高勇", "marks": {}, "tickets": []}
    spec = perms["search"]
    check("没申请搜索就不发拼音索引",
          "pys" not in app.module_record(rec, spec, with_pinyin=False)
          and "pys" in app.module_record(rec, spec))
    check("记录整包透传，平台不改形状",
          app.module_record(rec, spec, with_pinyin=False)["tickets"] == [])

    print("== 高危能力（super.*）：声明即生效，只做提示 ==")
    errs = []
    got = app.modules.resolve_features(["super.anyshortlink"], errs)
    check("声明了就生效，不当成不认识的能力",
          list(got) == ["super.anyshortlink"] and not errs, (got, errs))
    check("它只是开关，不带字段", got["super.anyshortlink"] == {}, got)
    listed = app.modules.super_features(["shortlink", "super.anyshortlink"])
    check("模块管理页要列的那一份带说明",
          [f["name"] for f in listed] == ["super.anyshortlink"] and listed[0]["note"],
          listed)
    check("模块 id 不能用这个命名空间",
          app.modules.is_super_feature("super.evil") is True)

    def target(raw, relaxed=False):
        try:
            return app.shortlinks.clean_target(raw, relaxed)
        except app.shortlinks.ShortlinkError:
            return None

    check("普通模块的短链目标不能带协议", target("https://example.com/x") is None)
    check("也不许跳出活动目录", target("../other/x") is None)
    check("还不许套短链", target("s/ABCDEFG") is None)
    check("相对路径照旧", target("/board") == "board")
    check("拿到 super.anyshortlink 之后三道拦截全放开",
          target("https://example.com/x", True) == "https://example.com/x"
          and target("../other/x", True) == "../other/x", None)

    print("== 运维助手：写动作要先批准 ==")

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, path, body=None):
            self.calls.append((path, body or {}))
            return {"ok": True}

        def get(self, path):
            self.calls.append((path, {}))
            return {"ok": True}

    a = app.assistant
    acc = {"id": 7, "uid": 10007, "role": "admin", "username": "alice", "name": "王同学"}
    add_plan = [{"tool_calls": [{"id": "c1", "function": {
        "name": "roster_add_person",
        "arguments": json.dumps({"roster_id": 3, "sid": "2024001", "name": "张三"})}}]},
        {"content": "已经加进去了"}]
    real_post = a._post_chat

    def fake_post(cfg, convo, account=None):
        step = add_plan.pop(0)
        return {"choices": [{"message": dict(step)}]}

    a._post_chat = fake_post
    try:
        client = FakeClient()
        got = a.run(None, acc, client, {"ready": True},
                    [{"role": "user", "content": "把张三加到名单 3"}])
        check("写动作先停住，不执行",
              bool(got.get("pending")) and not client.calls, (got.get("pending"), client.calls))
        check("停住的那一句说清要做什么",
              "张三" in (got["pending"]["summary"] or ""), got["pending"])
        check("标签用正式名称", got["pending"]["label"] == "写入名单",
              got["pending"]["label"])
        check("参数一起带出来（批准条上能展开看）",
              (got["pending"].get("args") or {}).get("sid") == "2024001",
              got["pending"].get("args"))
        token = got["pending"]["token"]
        done = a.approve(acc, token, True)
        check("批准之后才真的写",
              client.calls and client.calls[0][0] == "/api/panel/roster/entry",
              client.calls)
        check("写的是模型说的那个人",
              client.calls[0][1].get("sid") == "2024001", client.calls[0][1])
        check("批准之后把这一轮跑完",
              (done.get("reply") or "").startswith("已经加进去了"), done)
        try:
            a.approve(acc, token, True)
            check("令牌只能用一次", False, "第二次居然还能用")
        except ValueError:
            check("令牌只能用一次", True)
        try:
            a.approve({"id": 9, "role": "admin"}, token, True)
            check("别人的令牌用不了", False, "别人的令牌竟然通过了")
        except ValueError:
            check("别人的令牌用不了", True)

        # 拒绝：不写任何东西，把"用户拒绝了"交给模型收尾
        add_plan[:] = [{"tool_calls": [{"id": "c2", "function": {
            "name": "event_rename",
            "arguments": json.dumps({"seq": "12", "title": "新名字"})}}]},
            {"content": "那就不改了"}]
        client2 = FakeClient()
        first = a.run(None, acc, client2, {"ready": True},
                      [{"role": "user", "content": "把活动 12 改名"}])
        refused = a.approve(acc, first["pending"]["token"], False)
        check("点了拒绝就一条都没写", client2.calls == [], client2.calls)
        check("拒绝之后模型接着把话说完了",
              (refused.get("reply") or "").startswith("那就不改了"), refused)
        check("拒绝也记进了这一步的过程",
              any(s.get("tool") == "event_rename" for s in refused.get("steps") or []),
              refused.get("steps"))

        # 不需要批准的只读动作照旧直接跑
        add_plan[:] = [{"tool_calls": [{"id": "c3", "function": {
            "name": "list_rosters", "arguments": "{}"}}]}, {"content": "你有两份名单"}]
        client3 = FakeClient()
        plain = a.run(None, acc, client3, {"ready": True},
                      [{"role": "user", "content": "我有几份名单"}])
        check("只读动作不用批准就跑了",
              client3.calls and "pending" not in plain, (client3.calls, plain))

        # 挂着的动作记在哪段对话上：界面靠它点小红点
        a._PENDING.clear()
        add_plan[:] = [{"tool_calls": [{"id": "c4", "function": {
            "name": "roster_add_person",
            "arguments": json.dumps({"roster_id": 3, "sid": "2024002",
                                     "name": "李四"})}}]}]
        a.run(None, acc, FakeClient(), {"ready": True},
              [{"role": "user", "content": "把李四加到名单 3"}], session=11)
        held = a.pending_sessions(acc)
        check("挂着的动作记在它那段对话上", list(held) == [11], held)
        check("别人看不见这段挂起",
              a.pending_sessions({"id": 8, "role": "admin"}) == {},
              a.pending_sessions({"id": 8, "role": "admin"}))

        # 还没存过的对话（id 0）：会话存下来的那一刻认领过去
        a._PENDING.clear()
        add_plan[:] = [{"tool_calls": [{"id": "c5", "function": {
            "name": "roster_add_person",
            "arguments": json.dumps({"roster_id": 3, "sid": "2024003",
                                     "name": "王五"})}}]}]
        a.run(None, acc, FakeClient(), {"ready": True},
              [{"role": "user", "content": "把王五加到名单 3"}])
        check("新对话先挂在 0 上", list(a.pending_sessions(acc)) == [0],
              a.pending_sessions(acc))
        a.adopt_session(acc, 42, [{"role": "user", "content": "把王五加到名单 3"}])
        check("会话存下来之后认领到那段对话",
              list(a.pending_sessions(acc)) == [42], a.pending_sessions(acc))
        a.adopt_session(acc, 43, [{"role": "user", "content": "另一句话"}])
        check("对不上号的不认领", list(a.pending_sessions(acc)) == [42],
              a.pending_sessions(acc))
        a.drop_session(acc, 42)
        check("会话删掉之后挂起也一起丢", a.pending_sessions(acc) == {},
              a.pending_sessions(acc))

        # 移出名单：模型只给学号，工具自己去名单里换成 personId
        class RosterClient:
            def __init__(self):
                self.calls = []

            def get(self, path):
                self.calls.append(("GET", path, {}))
                return {"ok": True, "total": 1,
                        "entries": [{"personId": 91, "sid": "2024001", "name": "张三"}]}

            def post(self, path, body=None):
                self.calls.append(("POST", path, body or {}))
                return {"ok": True}

        rc = RosterClient()
        ok = a._tool_roster_remove_person(rc, {"roster_id": 3, "sid": "2024001"})
        drop = [c for c in rc.calls if c[0] == "POST"]
        check("移出名单按学号找到人再删",
              ok.get("ok") and drop and drop[0][1] == "/api/panel/roster/entry/drop"
              and drop[0][2].get("personId") == 91, (ok, rc.calls))
        rc2 = RosterClient()
        miss = a._tool_roster_remove_person(rc2, {"roster_id": 3, "sid": "2024999"})
        check("名单里没有这个人就不删",
              miss.get("error") and not [c for c in rc2.calls if c[0] == "POST"],
              (miss, rc2.calls))
        check("移出名单在策略里是写动作",
              app.policy.is_write("roster_remove_person")
              and "移出" in app.policy.describe("roster_remove_person"),
              app.policy.describe("roster_remove_person"))
        check("提示词要求在当轮重新取数，不吃上一轮的结论",
              "每一轮都重新取数" in a.SYSTEM_PROMPT
              and "不能当成结论" in a.SYSTEM_PROMPT, a.SYSTEM_PROMPT[-160:])
        check("提示词写了回答的 Markdown 语法与站内链接怎么写",
              "[文字](路径)" in a.SYSTEM_PROMPT
              and "usage/rosters.md" in a.SYSTEM_PROMPT
              and "assistant.md#它会查文档" in a.SYSTEM_PROMPT
              and "图片不渲染" in a.SYSTEM_PROMPT, a.SYSTEM_PROMPT[:80])

        # 改当前这段对话的标题：会话 id 由平台注入，模型不必知道自己在哪一段里
        a._PENDING.clear()
        add_plan[:] = [{"tool_calls": [{"id": "c6", "function": {
            "name": "session_title",
            "arguments": json.dumps({"title": "报名排查"})}}]},
            {"content": "改好了"}]
        tc = FakeClient()
        want = a.run(None, acc, tc, {"ready": True},
                     [{"role": "user", "content": "把这段对话改名叫报名排查"}], session=42)
        check("改名也要先问一句",
              (want.get("pending") or {}).get("action") == "session_title"
              and not tc.calls, (want.get("pending"), tc.calls))
        check("批准条上写清改成什么",
              "报名排查" in ((want.get("pending") or {}).get("summary") or ""),
              want.get("pending"))
        done_t = a.approve(acc, (want.get("pending") or {}).get("token"), True)
        ren = [c for c in tc.calls
               if c[0] == "/api/panel/assistant/session/rename"]
        check("批准之后改的是当前这一段",
              bool(ren) and ren[0][1].get("id") == 42
              and ren[0][1].get("title") == "报名排查", tc.calls)
        check("改名之后模型把话说完了",
              (done_t.get("reply") or "").startswith("改好了"), done_t)

        # 还没存档的对话没有 id 可改：说清楚，别瞎写一个
        noname = a._tool_session_title(FakeClient(), {"title": "x"})
        check("没有会话 id 时不往下写",
              bool(noname.get("error")) and "存档" in noname.get("error", ""), noname)
        check("改名在策略里是写动作", app.policy.is_write("session_title"),
              app.policy.describe("session_title"))
        check("写动作的名称是正式的短名",
              (want.get("pending") or {}).get("label") == "修改会话标题",
              want.get("pending"))
        # 每个工具都有正式名称：批准条、过程标签、文档都用这一份
        missing = [t["name"] for t in a.TOOLS
                   if not (app.policy.ACTION_LABELS.get(t["name"]) or "").strip()]
        check("每个工具都有正式名称", not missing, missing)
        bad = [v for v in app.policy.ACTION_LABELS.values()
               if len(v) > 8 or "看" in v or "查" in v or "把" in v]
        check("名称简短、书面语，不出现“看了/查了/把…”", not bad, bad)

        # 这一轮走到哪儿了：界面靠它把"正在思考"换成具体那一步
        seen = []
        real_progress = a.say_progress
        a.say_progress = lambda acc_, sid, phase, label="": (
            seen.append((phase, label)), real_progress(acc_, sid, phase, label))[1]
        a._PENDING.clear()
        add_plan[:] = [{"tool_calls": [{"id": "c8", "function": {
            "name": "list_rosters", "arguments": "{}"}}]}, {"content": "你有两份名单"}]
        try:
            a.run(None, acc, FakeClient(), {"ready": True},
                  [{"role": "user", "content": "我有几份名单"}], session=7)
        finally:
            a.say_progress = real_progress
        phases = [p for p, _ in seen]
        check("一轮里依次是 思考 → 执行工具 → 再思考 → 收尾",
              phases[:1] == ["think"] and "tools" in phases
              and phases[-1] == "done" and "write" not in phases, seen)
        check("执行工具那一步带上了正式名称",
              ("tools", "读取名单列表") in seen, seen)
        check("收尾之后进度清空", a.progress_of(acc, 7) == {"phase": "", "label": ""},
              a.progress_of(acc, 7))
        check("别的会话看不到这段进度", a.progress_of(acc, 8) == {"phase": "", "label": ""},
              a.progress_of(acc, 8))
    finally:
        a._post_chat = real_post
    print("== SQL 拼接审计 ==")
    import subprocess as _sp
    lint = _sp.run([sys.executable, os.path.join("tools", "sql_lint.py"), "--strict"],
                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   capture_output=True, text=True, encoding="utf-8")
    check("没有未标记的 SQL 拼接", lint.returncode == 0, (lint.stdout or "")[:400])

    print("== 提示词里的变量 ==")
    acc = {"id": 7, "username": "laffey", "name": "吴天明", "role": "owner"}
    vals = app.assistant.prompt_values(acc, ["list_events", "host_stats"], "9.9.9.9")
    check("账号的信息都能取到",
          vals["user.name"] == "吴天明" and vals["user.role"] == "站长"
          and vals["user.roleKey"] == "owner" and vals["user.id"] == 7, vals)
    check("工具清单拼成一行", vals["tools"] == "list_events、host_stats", vals["tools"])
    check("版本与时间都在",
          vals["panel.version"] == "9.9.9.9" and len(vals["time.date"]) == 10
          and ":" in vals["time.time"], vals)
    filled = app.assistant.fill_prompt(
        "你好 ${user.name}（${user.role}），今天 ${time.date}，工具 ${tools}", vals)
    check("占位符换成值", "吴天明" in filled and "${" not in filled, filled)
    check("没定义的占位符原样留着",
          app.assistant.fill_prompt("${nope.x} 与 ${user.nope}", vals)
          == "${nope.x} 与 ${user.nope}")
    check("没有账号时不带 user 变量",
          not [k for k in app.assistant.prompt_values(None) if k.startswith("user.")],
          app.assistant.prompt_values(None))
    check("名字缺省时用登录名",
          app.assistant.prompt_values({"username": "owner", "role": "admin"})["user.name"]
          == "owner")

    print("== 助手能读的文档 ==")
    rows = app.assistant.doc_entries()
    paths = [r["path"] for r in rows]
    check("操作指南都在清单里",
          "doc/usage/start.md" in paths and "doc/reference/api.md" in paths, paths[:5])
    check("开发指南不在清单里（助手读不到）",
          not [x for x in paths if x.startswith("doc/dev/") or x.startswith("doc/ops/")]
          and "doc/README.md" not in paths, paths)
    check("模块自己的说明也在清单里",
          "builtin/modules/nwuca.lottery/doc.md" in paths, paths[-3:])
    check("每份都有标题、有大小",
          all(r["title"] and r["size"] > 0 for r in rows))
    text, err = app.assistant.read_doc("doc/usage/start.md")
    check("正文读得出来", not err and text.startswith("#"), (text or "")[:20])
    check("跳出目录的路径一律拒", bool(app.assistant.read_doc("../app.py")[1])
          and bool(app.assistant.read_doc("doc/../../app.py")[1]))
    check("不在清单里的路径也拒", bool(app.assistant.read_doc("doc/nope.md")[1]))
    probe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "doc", "usage", "_probe-long.md")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("# 长文\n" + "字" * (app.assistant.DOC_MAX_CHARS + 500))
        long_text, long_err = app.assistant.read_doc("doc/usage/_probe-long.md")
        check("太长就截断并说明",
              not long_err and len(long_text) <= app.assistant.DOC_MAX_CHARS + 120
              and "后面还有" in long_text, len(long_text or ""))
    finally:
        if os.path.exists(probe):
            os.remove(probe)
    check("删掉之后就查不到了",
          bool(app.assistant.read_doc("doc/usage/_probe-long.md")[1]))
    check("读文档对所有角色开放",
          app.policy.verdict({"role": "admin"}, "read_doc", {})[0]
          and app.policy.verdict({"role": "owner"}, "list_docs", {})[0])

    # 图片：文档页给的路径与文档键同一套（usage/x.png），要和 /api/docs/doc 一样补前缀
    pic = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "doc", "usage", "_probe-pic.png")
    try:
        with open(pic, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        full, why = app.assistant.doc_image("usage/_probe-pic.png")
        check("图片路径按 doc/ 补全", bool(full) and os.path.basename(full) == "_probe-pic.png",
              why)
        check("带上 doc/ 前缀也认",
              app.assistant.doc_image("doc/usage/_probe-pic.png")[0] == full)
        check("图片只认那几种格式", bool(app.assistant.doc_image("usage/_probe-pic.md")[1]))
        check("跳出文档目录的图片拒掉",
              bool(app.assistant.doc_image("../app.py")[1])
              and bool(app.assistant.doc_image("usage/../../app.py")[1]))
        check("没有这张图就说没有",
              bool(app.assistant.doc_image("usage/_probe-none.png")[1]))
    finally:
        if os.path.exists(pic):
            os.remove(pic)

    print("== 模块动作的声明 ==")
    errs = []
    acts = app.modules.normalize_actions({
        "mark": {"title": "标记完成", "unlock": True,
                 "args": {"student": {"type": "int", "required": True}},
                 "target": "student:{student}"},
        "Bad": {},
        "weird": {"args": {"x": {"type": "nope"}}},
    }, errs)
    check("合法的动作留下", list(acts) == ["mark"], list(acts))
    check("写错的动作丢掉并记原因", len(errs) == 2, errs)
    check("默认席位是成员", acts["mark"]["seat"] == "member", acts["mark"])
    check("声明原样带过来",
          acts["mark"]["unlock"] is True and acts["mark"]["title"] == "标记完成", acts["mark"])
    try:
        app.modules.coerce_action_args(acts["mark"], {})
        missing = False
    except ValueError:
        missing = True
    check("缺必填参数直接拒绝", missing)
    got = app.modules.coerce_action_args(acts["mark"], {"student": "7", "extra": 1})
    check("参数按声明转类型，多余的丢掉", got == {"student": 7}, got)
    builtin = app.modules.get("nwuca.lottery", app.VERSION) or {}
    check("内置模块声明了接口版本", builtin.get("api") == 1, builtin.get("api"))
    check("内置模块声明了动作", "mark" in (builtin.get("actions") or {}),
          sorted((builtin.get("actions") or {})))

    print("== 活动目录：一个活动一个文件夹 ==")
    check("业务库在文件夹里", app.db.event_path(1).replace("\\", "/").endswith("/1/data.sqlite3"),
          app.db.event_path(1))
    check("审计日志与业务库分开",
          app.db.audit_path(1).replace("\\", "/").endswith("/1/audit.sqlite3"),
          app.db.audit_path(1))
    app.db.set_event(1)
    app.db.write_meta(1, title="演示活动", kind="nwuca.lottery")
    meta = app.db.read_meta(1)
    check("元数据写得进去读得出来", meta.get("title") == "演示活动", meta)
    app.db.audit(app.db.connect(), "unit_probe", detail="写一条审计")
    tail = app.db.audit_tail(None, 5)
    check("审计写进活动自己的库",
          any(r["action"] == "unit_probe" for r in tail), [r["action"] for r in tail])
    import sqlite3 as _sq
    with _sq.connect(app.db.event_path(1)) as biz:
        biz_actions = [r[0] for r in biz.execute("SELECT action FROM audit").fetchall()] \
            if biz.execute("SELECT count(*) FROM sqlite_master WHERE name='audit'").fetchone()[0] \
            else []
    check("业务库里不再写审计", "unit_probe" not in biz_actions, biz_actions[:5])

    print("== 静态设置写回 ==")
    import tempfile as _tempfile
    import shutil as _shutil
    box = _tempfile.mkdtemp(prefix="cfg-")
    old_path = app.localconfig.CONFIG_PATH
    try:
        app.localconfig.CONFIG_PATH = os.path.join(box, "config.json")
        app.localconfig.set_static("safe_mode", True)
        cfg = app.localconfig.load()
        check("写进去的静态设置读得回来", cfg.get("safe_mode") is True, cfg.get("safe_mode"))
        app.localconfig.save({"host": "0.0.0.0", "port": 8931,
                              "public_base_prefix": "http://example",
                              "owner_username": "owner", "owner_name": "站长"})
        kept = app.localconfig.raw()
        check("一次 save 不会把安全模式抹掉", kept.get("safe_mode") is True, kept)
        check("save 也没有丢掉服务配置", kept.get("port") == 8931, kept)
        app.localconfig.set_static("safe_mode", False)
        check("改回 false 也照写", app.localconfig.raw().get("safe_mode") is False)
        try:
            app.localconfig.set_static("nope", True)
            check("不认识的静态设置名会报错", False)
        except KeyError:
            check("不认识的静态设置名会报错", True)
    finally:
        app.localconfig.CONFIG_PATH = old_path
        _shutil.rmtree(box, ignore_errors=True)

    print("== 服务安全模式 ==")
    app.safemode.reset()
    check("默认不进安全模式", app.safemode.enabled() is False)
    check("模块照常加载", len(app.modules.all_modules(app.VERSION)) >= 1)
    os.environ["LOTTERY_SAFE_MODE"] = "1"
    app.safemode.reset()
    try:
        check("读到开关", app.safemode.enabled() is True)
        check("模块一个都不加载",
              app.modules.all_modules(app.VERSION) == [], app.modules.all_modules(app.VERSION))
        refused = app.safemode.refuse()
        check("写入被拒是 503", getattr(refused, "code", None) == 503, getattr(refused, "code", None))
        check("别的账号登录被拒",
              bool(app.safemode.login_blocked({"role": "admin"})))
        check("站长还能进", app.safemode.login_blocked({"role": "owner"}) is None)
    finally:
        os.environ.pop("LOTTERY_SAFE_MODE", None)
        app.safemode.reset()
    check("关掉之后恢复正常",
          app.safemode.enabled() is False
          and len(app.modules.all_modules(app.VERSION)) >= 1)

    print("== 随机源 ==")
    check("报得出来源", app.rng.source() in ("hwrng", "krng"), app.rng.source())
    check("字节长度对", len(app.rng.bytes(13)) == 13)
    check("两次不一样", app.rng.bytes(16) != app.rng.bytes(16))
    check("below 落在区间内",
          all(0 <= app.rng.below(7) < 7 for _ in range(200)))
    check("below 不取模（区间不整除也均匀地落进来）",
          len({app.rng.below(6) for _ in range(400)}) == 6)
    try:
        app.rng.below(0)
        bad = False
    except ValueError:
        bad = True
    check("上限为 0 直接拒绝", bad)
    pick = app.rng.sample(list(range(5)), 3)
    check("不放回抽样", len(pick) == 3 and len(set(pick)) == 3, pick)
    deck = list(range(20))
    app.rng.shuffle(deck)
    check("洗牌只换顺序", sorted(deck) == list(range(20)), deck[:5])

    print("== 服务有效性提示 ==")

    class Disk(object):
        def __init__(self, total, used, free):
            self.total, self.used, self.free = total, used, free

    GB = 1073741824
    check("一切正常就不吵人",
          app.health.signals({"cpu": 12.0, "mem": 40.0, "memTotalMB": 32768.0},
                             Disk(100 * GB, 40 * GB, 60 * GB)) == [])
    check("读不到就不说，不自己造一条",
          app.health.signals(None, None) == [])
    busy = app.health.signals({"cpu": 95.0}, None)
    check("CPU 偏高提示一条", [i["id"] for i in busy] == ["svc:cpu"], busy)
    check("读数写进正文，能自己复核", "95%" in busy[0]["body"], busy[0])
    check("占用类的降下来就没了（不必处理）", busy[0]["sticky"] is False, busy[0])
    check("条目只有这几个键，没有夹带动作",
          set(busy[0]) == {"id", "kind", "level", "title", "body", "sticky"}
          and busy[0]["kind"] == "service", sorted(busy[0]))
    mem = app.health.signals({"mem": 95.0, "memTotalMB": 32768.0}, None)
    check("内存偏高带上总量", mem and "GB" in mem[0]["body"], mem)
    tight = app.health.signals(None, Disk(100 * GB, 92 * GB, 8 * GB))
    check("盘快满了要留到条件消失",
          [i["id"] for i in tight] == ["svc:disk"] and tight[0]["sticky"] is True
          and tight[0]["level"] == "important", tight)
    check("几乎写不进去是紧急",
          app.health.signals(None, Disk(100 * GB, 99 * GB, 1 * GB))[0]["level"]
          == "critical")
    check("大盘也不例外（按剩余字节兜底）",
          [i["level"] for i in app.health.signals(None, Disk(3 * GB, 2.6 * GB,
                                                            400 * 1048576))]
          == ["important"])

    print("== xlsx 读写 ==")
    sheet = [["学号", "姓名", "院系", "班级"],
             ["20250001", "甲一", "计算机学院", "计科 2401"],
             ["0000012", "乙二", "含,逗号与\"引号\"", "换\n行"],
             ["", "", "", ""],
             ["20250013", "丙三", "很长" * 300, ""]]
    book = app.xlsx.build(sheet, "名单")
    check("写出来是 zip", book[:2] == b"PK", book[:8])
    back = app.xlsx.parse(book)
    check("表头一字不差", back[0] == sheet[0], back[0])
    check("前导零没丢", back[2][0] == "0000012", back[2][:2])
    check("逗号、引号、换行都还活着",
          back[2][2] == "含,逗号与\"引号\"" and back[2][3] == "换\n行", back[2])
    check("整行空白的丢掉", len(back) == 4, len(back))
    check("长文本不截断", back[3][2] == "很长" * 300, len(back[3][2]))
    check("空表也能写出来", app.xlsx.build([])[:2] == b"PK")
    try:
        app.xlsx.parse(b"definitely not a zip")
        refused = False
    except ValueError as exc:
        refused = "xlsx" in str(exc)
    check("不是 xlsx 就明说", refused)
    check("列名换算到 AA 也对", app.xlsx._col_name(26) == "AA", app.xlsx._col_name(26))

    print("== the panel's own helpers ==")
    check("view() maps the role label",
          app.panel.ROLE_LABEL.get("owner") == "站长", app.panel.ROLE_LABEL)
    check("classify() keeps the token first",
          app.panel.classify("/T" + "x" * 32 + "/panel", ("T" + "x" * 32,))[0] == "event",
          app.panel.classify("/T" + "x" * 32 + "/panel", ("T" + "x" * 32,)))

    print("== a stream only hears about its own activity ==")
    revs = {"panel": 3, "event:1": 7, "event:1:members": 2, "event:2": 5}
    check("the whole map goes to the panel",
          app._scope_of(revs, None) == revs)
    check("one activity's own scope is all a projector gets",
          app._scope_of(revs, "event:1") == {"event:1": 7},
          app._scope_of(revs, "event:1"))
    check("another activity's writes are dropped",
          app._scope_of(revs, "event:9") == {}, app._scope_of(revs, "event:9"))

    print()
    if problems:
        print("FAILED %d check(s):" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("UNIT TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
