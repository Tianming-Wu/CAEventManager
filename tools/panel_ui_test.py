#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser suite for the master panel: the pages people actually click.

    python tools/panel_ui_test.py <base-url> <owner-password> [--accounts]

``tools/panel_test.py`` talks to ``/api/panel/*`` directly, so it stayed green
while both auth pages were posting to the wrong endpoint - the page asked
"login", the server answered "not logged in".  This suite drives the pages
themselves, which is the only thing that catches that.

``--accounts`` additionally creates throw-away accounts so the role matrix can be
exercised.  Run that against a development instance, not production.
"""
import json
import os
import random
import re
import shutil
import struct
import sys
import tempfile
import time
import urllib.request
import zipfile
import zlib

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931").rstrip("/")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""
LOGIN = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") \
    else "owner"
WITH_ACCOUNTS = "--accounts" in sys.argv
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 只跑其中一部分：`--only=assistant` 这类。动哪一块就只验哪一块，
# 免得为了改运维助手把整页面板重跑一遍。分组与「依赖」写在这儿：
#   assistant  运维助手（一条对话、批准条、抽屉、Markdown）——只要壳子能登录就行
#   shell      侧栏、路由表、导航键、面包屑、布局档、触屏
#   data       名单与报名（含打印、二维码、xlsx）——这几步互相用对方造的数据
#   mods       模块（现场说明、导入、加载态）
#   admin      账号（资料、删除、角色、越权）
#   ops        服务器、sudo、审计、弹窗、主题、搜索、通知、展示屏幕、活动
#   all / 不写 全套
ONLY = []
for _arg in sys.argv[3:]:
    if _arg.startswith("--only="):
        ONLY += [x for x in _arg.split("=", 1)[1].split(",") if x]
if "all" in ONLY:
    ONLY = []


def want(group):
    return not ONLY or group in ONLY


# 这两组会用到"别人"的账号（转移拥有者、角色矩阵），只跑其中一组时先造几个
NEEDS_ACCOUNTS = ("data", "admin")
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "core"))
import xlsx  # noqa: E402  xlsx 用例要用同一份实现写测试文件
TAG = str(random.randint(1000, 9999))

problems = []


def check(name, cond, detail=""):
    print("  %-5s %s %s" % ("PASS" if cond else "FAIL", name, "" if cond else detail))
    if not cond:
        problems.append(name)
        # 控制台经 Windows 编码一转，中文细节就看不清了：失败项另存一份 UTF-8
        try:
            with open(os.path.join(ROOT_DIR, "tmp", "ui-failures.log"), "a",
                      encoding="utf-8") as fh:
                fh.write("%s | %s\n" % (name, detail))
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def owner_opener():
    jar = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(jar)
    call(opener, "/api/panel/login", {"username": LOGIN, "password": PASSWORD})
    return opener


def call(opener, path, body=None):
    """Talk to the panel API directly; used to set up state, not to test it."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method="POST" if data else "GET",
        headers={"X-Requested-With": "CAEventManager", "Content-Type": "application/json"})
    with opener.open(req) if opener else urllib.request.urlopen(req) as res:
        return json.loads(res.read().decode())


def seed_accounts(count=2):
    """不经过界面，直接注册几个已通过的账号：转移拥有者之类要有个"别人"可选。"""
    opener = owner_opener()
    made = []
    for i in range(count):
        user = "seeded%d%s" % (i, TAG)
        call(opener, "/api/panel/register",
             {"username": user, "name": "备用%d" % i, "password": "seeded-pass-1",
              "note": ""})
        made.append(user)
    for a in call(opener, "/api/panel/accounts?limit=200")["accounts"]:
        if a["username"] in made and a["status"] == "pending":
            call(opener, "/api/panel/account/decide", {"id": a["id"], "action": "approve"})
    return made


def drop_signup(opener, sid):
    """收尾：平台不给直接删，先归档再由超管彻底删除。"""
    if not sid:
        return
    for path in ("/api/panel/signup/archive", "/api/panel/signup/delete"):
        try:
            call(opener, path, {"id": sid})
        except Exception:
            pass


def drop_roster(opener, rid):
    if not rid:
        return
    for path in ("/api/panel/roster/archive", "/api/panel/roster/delete"):
        try:
            call(opener, path, {"id": rid})
        except Exception:
            pass


def new_page(browser, width=1440, height=900, touch=False, mobile=False, dpr=1):
    ctx = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch,
        is_mobile=mobile, device_scale_factor=dpr)
    page = ctx.new_page()
    page._errors = []

    def keep_error(text):
        # 收尾时页面被关掉、请求被中止，这类网络噪声不是 JS 错误：
        # 面板在后台轮询通知、服务提示与机器占用，关上下文时正好可能撞上
        for noise in ("ERR_CONNECTION_CLOSED", "ERR_ABORTED", "ERR_CONNECTION_RESET",
                      "Failed to load resource: net::"):
            if noise in text:
                return
        page._errors.append(text)

    page.on("pageerror", lambda e: keep_error(str(e)))
    page.on("console", lambda m: keep_error("console: " + m.text)
            if m.type == "error" else None)
    return ctx, page


def enter_sudo(page, expect_prompt=True):
    """Answer the password prompt that a sensitive action raises."""
    if expect_prompt:
        page.wait_for_selector("#sudoPass", timeout=8000)
        page.fill("#sudoPass", PASSWORD)
        page.click("#sudoEnter")
    page.wait_for_function(
        "() => document.getElementById('shell').classList.contains('sudo')",
        timeout=8000)


def sudo_if_asked(page, timeout=2500):
    """写操作要提权：弹窗出来就答，没出来说明 sudo 还在有效期内。"""
    try:
        page.wait_for_selector("#sudoPass", timeout=timeout)
    except Exception:
        return False
    page.fill("#sudoPass", PASSWORD)
    page.click("#sudoEnter")
    page.wait_for_function(
        "() => document.getElementById('shell').classList.contains('sudo')",
        timeout=8000)
    page.wait_for_timeout(200)
    return True


def ui_login(page, user=None, password=None):
    user = user or LOGIN
    password = password or PASSWORD
    page.goto(BASE + "/login", wait_until="domcontentloaded")
    page.fill("#username", user)
    page.fill("#password", password)
    page.click("#go")
    page.wait_for_url("**/panel", timeout=20000)
    page.wait_for_selector(".shell", timeout=20000)
    # panel.js sets data-booted once the account and the first page are in place
    page.wait_for_function(
        "() => document.documentElement.getAttribute('data-booted') === '1'",
        timeout=25000)
    page.wait_for_timeout(200)


def visible_nav(page):
    return page.evaluate("""() => {
        const items = [], groups = [];
        document.querySelectorAll('[data-nav]').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) items.push(el.getAttribute('data-nav'));
        });
        document.querySelectorAll('.nav-group').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) groups.push(el.getAttribute('data-group'));
        });
        return {items: items, groups: groups};
    }""")


def has_current(page):
    return page.evaluate("""() => document.getElementById('shell')
                                   .classList.contains('nav-open')""")


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def register_page(page):
    user = "uitest" + TAG
    print("== registration page ==")
    page.goto(BASE + "/register", wait_until="domcontentloaded")
    page.wait_for_selector("#regForm", timeout=20000)
    check("register form present", page.locator("#regForm").count() == 1)
    check("links back to login", page.locator('a[href="./"]').count() == 1)
    page.fill("#username", user)
    page.fill("#name", "UI 测试")
    page.fill("#password", "browser-pass-1")
    page.fill("#note", "自动化测试")
    page.click("#go")
    page.wait_for_function(
        "document.getElementById('done').style.display === 'block' ||"
        " document.getElementById('err').textContent.trim().length > 0", timeout=15000)
    done = page.locator("#done").inner_text().strip()
    err = page.locator("#err").inner_text().strip()
    check("submission accepted", done != "" and err == "", "%r / %r" % (done, err))
    check("form is locked after submitting", page.locator("#go").is_disabled())


def login_page(page):
    print("== root defaults to login ==")
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#loginForm", timeout=20000)
    check("login form is the default view", page.locator("#loginForm").count() == 1)
    check("title says 登录", "登录" in page.title(), page.title())
    check("no registration form on the default view",
          page.locator("#regForm").count() == 0)
    check("link to registration is offered",
          page.locator('a[href*="register"]').count() == 1)

    print("== a rejected attempt stays on the page ==")
    page.fill("#username", LOGIN)
    page.fill("#password", "definitely-not-the-password")
    page.click("#go")
    page.wait_for_function(
        "document.getElementById('err').textContent.trim().length > 0", timeout=15000)
    message = page.locator("#err").inner_text().strip()
    check("the reason is shown on the page", len(message) > 0, message)
    check("the wording is about the credentials",
          "密码" in message or "不存在" in message, message)
    check("the url did not change", page.url.rstrip("/") == BASE, page.url)
    check("the button is usable again", not page.locator("#go").is_disabled())

    print("== the retry on the same page works ==")
    page.fill("#password", PASSWORD)
    page.click("#go")
    try:
        page.wait_for_url("**/panel", timeout=20000)
        landed = True
    except Exception:
        landed = False
    check("second attempt signs in", landed, page.url + " | " + page.title())
    # the console is a separate page load; wait for its own boot, not for a
    # static string that the server already sent
    page.wait_for_selector(".shell", timeout=25000)
    page.wait_for_function(
        "() => document.documentElement.getAttribute('data-booted') === '1'",
        timeout=25000)
    body = page.content()
    check("console rendered", "总览" in body or "我的活动" in body, page.title())
    check("no 501 page", "Unsupported method" not in body)
    # the rejected attempt above logs a 401 in the console on purpose; from here
    # on any console error is a real one.
    page._errors = []


def shell_page(page):
    print("== the shell ==")
    check("the sidebar card shows a name",
          page.locator("#sideName").inner_text().strip() != "",
          page.locator("#sideName").inner_text())
    check("the sidebar card shows the role",
          "站长" in page.locator("#sideRole").inner_text(),
          page.locator("#sideRole").inner_text())
    avatar = page.evaluate("""() => {
        const el = document.getElementById('sideAvatar');
        return el.querySelector('img') ? 'picture' : el.textContent.trim();
    }""")
    check("the avatar is a picture or one letter",
          avatar == "picture" or len(avatar) == 1, avatar)
    check("「服务已更新」的条默认不占地方",
          page.locator("#updateBar").count() == 1
          and not page.locator("#updateBar").is_visible())
    print("== 总览的四张牌 ==")
    page.goto(BASE + "/panel#/overview")
    page.wait_for_timeout(700)
    check("「我的名单」「我的报名」都在",
          page.locator("#statRosters").count() == 1 and page.locator("#statSignups").count() == 1)
    check("它们能点去各自的页面", page.evaluate(
        """() => { const a = document.querySelector('.stat[data-href="#/rosters"]');
                   const b = document.querySelector('.stat[data-href="#/signups"]');
                   return !!a && !!b; }"""))
    check("站长看得到账号总数", page.locator("#statAccountsBox").is_visible())
    check("版本号是四段", len(page.inner_text(".brand-ver").strip().lstrip("v").split(".")) == 4,
          page.inner_text(".brand-ver"))
    print("== the title bar keeps its right side free ==")
    bar = page.evaluate("""() => {
        const title = document.querySelector('.titlebar');
        const kids = Array.from(title.children).map(el => el.className);
        return {kids: kids,
                hasAccount: !!title.querySelector('.acct'),
                text: title.innerText.replace(/\\s+/g, ' ').trim()};
    }""")
    check("no account card left in the title bar", not bar["hasAccount"], bar["kids"])
    check("nothing personal stays up there", LOGIN not in bar["text"], bar["text"])
    nav = visible_nav(page)
    for item in ("overview", "events", "templates", "users", "all-events",
                 "server", "audit", "account", "appearance"):
        check("nav has %s" % item, item in nav["items"], nav["items"])
    check("nav has the approvals group", "approvals" in nav["groups"], nav["groups"])

    order = page.evaluate(
        "() => Array.from(document.querySelector('.nav-foot').children)"
        ".map(el => el.className)")
    check("界面设置 sits above a rule, the account card below",
          "nav-item" in order[0] and "nav-foot-sep" in order[1]
          and "nav-account" in order[2], order)
    card = page.evaluate("""() => {
        const c = document.querySelector('.nav-account').getBoundingClientRect();
        const av = document.querySelector('.nav-account-avatar').getBoundingClientRect();
        const name = document.getElementById('sideName').getBoundingClientRect();
        const tag = document.getElementById('sideRole').getBoundingClientRect();
        return {h: Math.round(c.height), avatarLeft: av.left < name.left,
                nameAboveTag: name.top < tag.top,
                avatarRound: getComputedStyle(
                    document.querySelector('.nav-account-avatar')).borderRadius};
    }""")
    check("it is a card, not a row", card["h"] >= 52, card)
    check("the avatar is round and on the left",
          card["avatarLeft"] and card["avatarRound"] == "999px", card)
    check("the name sits above the role tag", card["nameAboveTag"], card)

    print("== the login name is not on display ==")
    shown = page.evaluate(
        "() => document.getElementById('sideName').textContent + '|' +"
        " document.querySelector('.nav-account').title")
    check("the card shows a display name", shown.replace("|", "").strip() != "", shown)
    check("it does not show the login name", LOGIN not in shown, shown)


def roster_page(page):
    print("== 我的名单 ==")
    page.goto(BASE + "/panel#/rosters")
    page.wait_for_timeout(600)
    check("名单 has its own nav entry",
          page.locator('[data-nav="rosters"]').count() == 1)
    check("the list says it is empty",
          "还没有名单" in page.inner_text("#rosterTable tbody"),
          page.inner_text("#rosterTable tbody"))
    pills = page.locator("#rosterScopeBox .pill")
    check("three scopes are offered", pills.count() == 3, pills.count())
    check("我参与的 is the default", "on" in (pills.first.get_attribute("class") or ""),
          pills.first.get_attribute("class"))
    check("the pills are capsule shaped", page.evaluate(
        """() => {
        const b = document.querySelector('#rosterScopeBox .pill');
        return getComputedStyle(b).borderTopLeftRadius;
      }""").startswith("999"), page.evaluate(
        "() => getComputedStyle(document.querySelector('#rosterScopeBox .pill'))"
        ".borderTopLeftRadius"))
    check("the pills sit on the left of the card head", page.evaluate(
        """() => {
        const pills = document.querySelector('#rosterScopeBox');
        const head = pills.closest('.card-head');
        return Math.round(pills.getBoundingClientRect().left -
                          head.getBoundingClientRect().left) < 40;
      }"""))

    page.click("#rosterNewBtn")
    page.wait_for_selector("#rosterName", timeout=8000)
    page.fill("#rosterName", "UI 测试名单")
    page.fill("#rosterNote", "自动化创建")
    page.click("#rosterSave")
    page.wait_for_timeout(1000)
    check("creating a roster opens it",
          "UI 测试名单" in page.inner_text('.page[data-page="roster"]:not([hidden]) h1'),
          page.inner_text('.page[data-page="roster"]:not([hidden]) h1'))

    check("the owner is listed as the only writer",
          "拥有者" in page.inner_text("#rosterShareBody") and
          "除拥有者外暂无其他账号" in page.inner_text("#rosterShareBody"),
          page.inner_text("#rosterShareBody"))

    check("the roster header is one line: 人数 / 搜索 / 手动录入",
          page.locator('.page[data-page="roster"] .card-head .searchbox').count() == 1 and
          page.locator("#rosterAddBtn").is_visible())
    page.click("#rosterAddBtn")
    page.wait_for_selector("#rosterAddSid", timeout=8000)
    page.fill("#rosterAddSid", "20249001")
    page.fill("#rosterAddName", "测试甲")
    page.fill("#rosterAddDept", "计算机学院")
    page.fill("#rosterAddGrp", "计科2401")
    page.click("#rosterAddGo")
    page.wait_for_timeout(1000)
    check("a person can be added from the page",
          "20249001" in page.inner_text("#rosterEntryTable tbody"),
          page.inner_text("#rosterEntryTable tbody"))
    check("院系 is its own column",
          "计算机学院" in page.inner_text("#rosterEntryTable tbody"))

    page.fill("#rosterEntrySearch", "ceshi")
    page.wait_for_timeout(800)
    check("the member search matches pinyin",
          page.locator("#rosterEntryTable tbody tr").count() >= 1,
          page.locator("#rosterEntryTable tbody tr").count())
    page.fill("#rosterEntrySearch", "")
    page.wait_for_timeout(800)

    page.set_input_files("#rosterMergeFile", {
        "name": "members.csv", "mimeType": "text/csv",
        "buffer": "学号,姓名,班级\n20249002,测试乙,计科2402\n".encode("utf-8")})
    page.wait_for_timeout(1500)
    check("merging a file adds its rows",
          "20249002" in page.inner_text("#rosterEntryTable tbody"),
          page.inner_text("#rosterEntryTable tbody"))

    # 移出会把这一条删掉（分组、标签、备注一起没），所以先问一句、不用密码
    drop_btn = '#rosterEntryTable tbody tr:has-text("20249002") [data-entry-drop]'
    page.click(drop_btn)
    page.wait_for_selector("#confirmGo", timeout=8000)
    check("点移出先问一句", "移出名单" in page.inner_text("#overlayTitle"),
          page.inner_text("#overlayTitle"))
    check("确认里点明删掉什么、还给了留记录的那条路",
          "标为退出" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:160])
    page.click('#overlayBody [data-close-overlay="1"]')
    page.wait_for_timeout(400)
    check("取消之后人还在名单里",
          "20249002" in page.inner_text("#rosterEntryTable tbody"),
          page.inner_text("#rosterEntryTable tbody")[:120])
    page.click(drop_btn)
    page.wait_for_selector("#confirmGo", timeout=8000)
    page.click("#confirmGo")
    page.wait_for_timeout(1200)
    check("真的确认了才移出去",
          "20249002" not in page.inner_text("#rosterEntryTable tbody"),
          page.inner_text("#rosterEntryTable tbody")[:160])

    shot_page = page
    with page.expect_download(timeout=15000) as caught:
        page.click("#rosterExportBtn")
    downloaded = caught.value
    check("the roster downloads as csv",
          downloaded.suggested_filename.endswith(".csv"),
          downloaded.suggested_filename)

    page.click("#rosterEditBtn")
    page.wait_for_selector("#rosterVis", timeout=8000)
    check("the dialog decides how write access is granted",
          page.locator("#rosterGrantMode").count() == 1)
    # 平台不给普通账号删东西：这里只有打包收起来的「归档名单」
    check("设置里能归档，但没有删除",
          page.locator("#rosterArchive").count() == 1
          and "删除名单" not in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:120])
    page.select_option("#rosterVis", "public")
    page.select_option("#rosterGrantMode", "confirm")
    page.click("#rosterSave")
    page.wait_for_timeout(900)
    check("the visibility can be changed",
          "公开" in page.inner_text("#rosterSub"), page.inner_text("#rosterSub"))
    check("the write-in mode is remembered",
          "需要我确认" in page.inner_text("#rosterShareHint"),
          page.inner_text("#rosterShareHint"))

    page.click('#rosterShareBody a.btn')
    page.wait_for_timeout(900)
    check("the roster links to the approval centre",
          page.locator('.page[data-page="write-approvals"]').is_visible())
    check("arriving with ?back shows the usual left-hand back button",
          page.locator("#approvalsBack.crumb-back").is_visible())
    page.click("#approvalsBack")
    page.wait_for_timeout(900)
    check("and it returns to where it came from",
          page.locator('.page[data-page="roster"]').is_visible())
    page.goto(BASE + "/panel#/write-approvals")
    page.wait_for_timeout(900)
    check("a top-level page has no back button on its own",
          page.locator("#approvalsBack").is_hidden())
    check("and its crumb is not pretending to have a parent",
          page.locator('.page[data-page="write-approvals"] .crumb').is_hidden())
    check("the centre explains both lists",
          "暂无待审批的申请" in page.inner_text("#approvalsTable tbody")
          or page.locator("#approvalsTable tbody tr").count() >= 1,
          page.inner_text("#approvalsTable tbody"))

    page.goto(BASE + "/panel#/rosters")
    page.wait_for_timeout(900)
    page.click("[data-open-roster]")
    page.wait_for_timeout(900)
    page.click("#rosterBack")
    page.wait_for_timeout(800)
    body = page.inner_text("#rosterTable tbody")
    # 上面把 20249002 移出了，所以现在只剩一个人
    check("the list shows it with its member count", "UI 测试名单" in body and "1" in body, body)


def signup_page(page, browser):
    print("== 报名 ==")
    page.goto(BASE + "/panel#/signups")
    page.wait_for_timeout(600)
    check("the list starts empty",
          "还没有报名" in page.inner_text("#signupTable tbody"),
          page.inner_text("#signupTable tbody"))

    page.click("#signupNewBtn")
    page.wait_for_selector("#signupNewName", timeout=8000)
    page.fill("#signupNewName", "UI 报名")
    page.click("#signupNewGo")
    page.wait_for_timeout(1400)
    check("creating a signup opens it",
          "UI 报名" in page.inner_text("#signupTitle"), page.inner_text("#signupTitle"))
    check("the page carries a breadcrumb back to the list",
          page.locator('.page[data-page="signup"] .crumb[data-href="#/signups"]').count() == 1)
    check("the public link is selectable text, not an input",
          page.locator("#signupUrl").evaluate("el => el.tagName") == "DIV")
    check("a QR code is drawn next to it",
          page.locator("#signupQr svg").count() == 1)
    layout = page.evaluate(
        """() => {
        const box = document.querySelector('.page[data-page="signup"] .cols-2');
        if (!box) return null;
        const cols = [...box.querySelectorAll(':scope > .col')];
        const bottoms = cols.map(c => {
          const cards = [...c.querySelectorAll(':scope > .card')];
          return Math.round(cards[cards.length - 1].getBoundingClientRect().bottom);
        });
        const cards = [...box.querySelectorAll(':scope > .col > .card')]
          .map(c => ({id: c.id || c.querySelector('h2').textContent,
                      top: Math.round(c.getBoundingClientRect().top)}));
        return {cols: cols.length,
                flow: getComputedStyle(box).flexDirection,
                gap: Math.max(...bottoms) - Math.min(...bottoms),
                cards: cards};
      }""")
    check("two independent columns", layout and layout["cols"] == 2, layout)
    check("the two columns end on one line",
          layout and layout["gap"] <= 2, layout)
    rhythm = page.evaluate(
        """() => {
        const pageEl = document.querySelector('.page[data-page="signup"]');
        const cols = pageEl.querySelector('.cols-2');
        const colEls = [...cols.querySelectorAll(':scope > .col')];
        const left = [...colEls[0].querySelectorAll(':scope > .card')];
        const right = [...colEls[1].querySelectorAll(':scope > .card')];
        const cards = [...pageEl.querySelectorAll(':scope > .card')];
        const link = cards[0].getBoundingClientRect();
        const collect = cards.find(c => c.querySelector('h2') &&
          c.querySelector('h2').textContent === '收集字段').getBoundingClientRect();
        const colTop = cols.getBoundingClientRect().top;
        const colBottom = Math.max(...colEls.map(c =>
          c.querySelector(':scope > .card:last-child').getBoundingClientRect().bottom));
        return [
          Math.round(colTop - link.bottom),
          Math.round(left[1].getBoundingClientRect().top -
                     left[0].getBoundingClientRect().bottom),
          Math.round(right[0].getBoundingClientRect().left -
                     left[0].getBoundingClientRect().right),
          Math.round(collect.top - colBottom)
        ];
      }""")
    check("纵向与横向的卡片间距一致", len(set(rhythm)) == 1, rhythm)
    check("两列与下面整宽卡片之间留出了间距", rhythm[3] > 0, rhythm)
    check("the短 column starts the next card right under itself", layout and (
        # 左列第二张卡片紧跟在第一张后面，而不是等到右列第一张结束
        [c for c in layout["cards"] if c["id"] == "signupTimeCard"][0]["top"]
        < [c for c in layout["cards"] if c["id"] == "signupCapCard"][0]["top"]),
        layout["cards"] if layout else None)

    # 时间与名额各自一个开关
    check("the switch carries no visible text",
          page.inner_text("#signupTimeCard .card-head").strip() == "时间",
          page.inner_text("#signupTimeCard .card-head"))
    check("an off card is dimmed as a whole", page.evaluate(
        """() => {
        const head = document.querySelector('#signupCapCard .card-head h2');
        const field = document.querySelector('#signupCapCard .field');
        return getComputedStyle(field).opacity !== '1' ||
               getComputedStyle(head).color !== getComputedStyle(
                 document.querySelector('#signupTimeCard .card-head h2')).color;
      }"""))
    check("time starts switched off", not page.locator("#signupTimeOn").is_checked())
    check("capacity starts switched off", not page.locator("#signupCapOn").is_checked())
    page.click('#signupCapCard .switch')       # 开关本体是视觉隐藏的 checkbox

    page.fill("#signupCap", "5")
    page.click("#signupSaveBtn")
    page.wait_for_timeout(1000)
    check("the switch turns the limit on",
          "0 / 5" in page.inner_text("#signupSub"), page.inner_text("#signupSub"))

    # 日期与时刻是两个框，占位符不再中英混排
    check("date and time are separate fields",
          page.locator("#signupOpensDate").count() == 1 and
          page.locator("#signupOpensTime").count() == 1)
    check("the placeholder text is ours, not the browser's",
          page.get_attribute("#signupOpensDate", "placeholder") == "yyyy-mm-dd" and
          page.get_attribute("#signupOpensTime", "placeholder") == "HH:MM",
          page.get_attribute("#signupOpensDate", "placeholder"))
    check("a readable date is refused when malformed", page.evaluate(
        """() => {
        const d = document.querySelector('#signupOpensDate');
        return d.type === 'text' && d.inputMode === 'numeric';
      }"""))

    # 开始晚于结束要被拦下
    page.click('#signupTimeCard .switch')
    page.fill("#signupOpensDate", "2026-12-02")
    page.fill("#signupOpensTime", "10:00")
    page.fill("#signupClosesDate", "2026-12-01")
    page.fill("#signupClosesTime", "10:00")
    page.click("#signupSaveBtn")
    page.wait_for_timeout(700)
    check("a start after the end is refused",
          "开始时间必须早于结束时间" in page.inner_text(".toast, body"),
          page.inner_text(".toast, body")[:60])

    # 撤销：把日期清掉再撤销，值应该回来
    page.click("#signupOpensClear")
    page.wait_for_timeout(300)
    check("the clear button empties the pair",
          page.input_value("#signupOpensDate") == "", page.input_value("#signupOpensDate"))
    page.click("#signupClosesDate")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(400)
    check("undo brings the cleared date back",
          page.input_value("#signupOpensDate") == "2026-12-02",
          page.input_value("#signupOpensDate"))
    page.keyboard.press("Control+Shift+z")
    page.wait_for_timeout(400)
    check("redo re-applies the change",
          page.input_value("#signupOpensDate") == "", page.input_value("#signupOpensDate"))
    page.keyboard.press("Control+z")
    page.wait_for_timeout(300)

    # 宽松格式：斜杠分隔、带时刻的紧凑写法都认，保存后改写成标准格式
    page.fill("#signupOpensDate", "2026/11/01")
    page.fill("#signupOpensTime", "10:00")
    page.fill("#signupClosesDate", "20261203T2359")
    page.fill("#signupClosesTime", "")
    page.click("#signupSaveBtn")
    page.wait_for_timeout(1200)
    check("a valid window is saved", "进行中" in page.inner_text("#signupSub"),
          page.inner_text("#signupSub"))
    # 平台不给普通账号删东西：报名页上只有把链接收起来的「归档报名」
    check("报名页上能归档、没有删除",
          page.locator("#signupArchiveBtn").count() == 1
          and page.inner_text("#signupArchiveBtn").strip() == "归档报名"
          and "删除报名" not in page.inner_text('[data-page="signup"]'),
          page.inner_text('[data-page="signup"]')[:120])
    check("a slash date is rewritten as yyyy-mm-dd",
          page.input_value("#signupOpensDate") == "2026-11-01",
          page.input_value("#signupOpensDate"))
    check("a compact timestamp is understood",
          page.input_value("#signupClosesDate") == "2026-12-03" and
          page.input_value("#signupClosesTime") == "23:59",
          [page.input_value("#signupClosesDate"), page.input_value("#signupClosesTime")])
    page.fill("#signupOpensTime", "")
    page.click("#signupSaveBtn")
    page.wait_for_timeout(900)
    check("an empty time becomes 00:00",
          page.input_value("#signupOpensTime") == "00:00",
          page.input_value("#signupOpensTime"))
    page.fill("#signupOpensTime", "10:00")

    # Ctrl+S 保存的是这张表单，而不是浏览器弹出的"保存网页"
    page.fill("#signupNote", "快捷键保存")
    page.click("#signupTitle")
    page.keyboard.press("Control+s")
    page.wait_for_timeout(900)
    check("Ctrl+S saves the form", "快捷键保存" in page.input_value("#signupNote"),
          page.input_value("#signupNote"))

    check("the time card is not dimmed once its switch is on", page.evaluate(
        """() => !document.querySelector('#signupTimeCard')
                   .classList.contains('is-off')"""))

    # 收集字段的两个复选框
    check("built-in fields are checkboxes",
          page.locator("#collectDept").count() == 1 and page.locator("#collectGrp").count() == 1)
    page.click('label:has(#collectGrp)')
    page.click("#signupSaveBtn")
    page.wait_for_timeout(900)

    # 视觉效果：主题与预览
    page.select_option("#signupTheme", "cool")
    page.wait_for_timeout(300)
    check("the preview follows the theme", page.evaluate(
        """() => getComputedStyle(document.querySelector('#signupPreview'))
                 .backgroundColor""") != "rgba(0, 0, 0, 0)")
    page.click("#signupSaveBtn")
    page.wait_for_timeout(900)

    # 公开页走一遍：重复提交的三种情形
    token = page.evaluate(
        "() => document.querySelector('#signupUrl').textContent").rsplit("/", 1)[-1]
    guest = browser.new_context(viewport={"width": 430, "height": 900}, locale="zh-CN")
    pub = guest.new_page()
    pub.goto(BASE + "/join/" + token, wait_until="networkidle")
    pub.wait_for_timeout(900)
    check("the public page asks for the student id",
          pub.locator("#sid").count() == 1)
    check("班级 is not collected after unchecking it",
          pub.locator("#grp").count() == 0)
    pub.fill("#sid", "20249001")
    pub.fill("#name", "测试甲")
    pub.click("#go")
    pub.wait_for_timeout(1000)
    check("the first submission succeeds",
          "报名成功" in pub.inner_text("#body"), pub.inner_text("#body")[:80])

    pub.goto(BASE + "/join/" + token, wait_until="networkidle")
    pub.wait_for_timeout(800)
    pub.fill("#sid", "20249001")
    pub.fill("#name", "测试甲")
    pub.click("#go")
    pub.wait_for_timeout(1000)
    check("the same answers say already registered",
          "您已经报过名" in pub.inner_text("#body"), pub.inner_text("#body")[:80])

    pub.goto(BASE + "/join/" + token, wait_until="networkidle")
    pub.wait_for_timeout(800)
    pub.fill("#sid", "20249001")
    pub.fill("#name", "测试乙")
    pub.click("#go")
    pub.wait_for_timeout(1000)
    check("different answers are refused while self-edit is off",
          "联系负责人" in pub.inner_text("#err"), pub.inner_text("#err")[:80])

    page.select_option("#signupSelfEdit", "on")
    page.click("#signupSaveBtn")
    page.wait_for_timeout(900)
    pub.goto(BASE + "/join/" + token, wait_until="networkidle")
    pub.wait_for_timeout(800)
    pub.fill("#sid", "20249001")
    pub.fill("#name", "测试乙")
    pub.click("#go")
    pub.wait_for_timeout(1000)
    check("with self-edit on the page offers an update",
          "您希望更新信息吗" in pub.inner_text("#body"), pub.inner_text("#body")[:80])
    pub.click("#doUpdate")
    pub.wait_for_timeout(1100)
    check("confirming updates the record",
          "信息已更新" in pub.inner_text("#body"), pub.inner_text("#body")[:80])
    guest.close()


def print_page(browser):
    """打印：名单与报名单都只是一张表，页码在左下角；标题与二维码是选项。"""
    print("== 打印 ==")
    owner = owner_opener()
    rows = "\n".join("2025%04d,成员%03d" % (i, i) for i in range(1, 61))
    rid = call(owner, "/api/panel/roster/import", {
        "name": "打印测试名单", "text": "学号,姓名\n" + rows})["id"]
    sid = call(owner, "/api/panel/signup/create", {
        "title": "打印测试报名", "roster_id": rid})["id"]
    token = (call(owner, "/api/panel/signup?id=%d" % sid) or {}).get("signup", {}).get("token")
    ctx, page = new_page(browser, 900, 950)
    gate = None
    try:
        ui_login(page)

        print("== 名单：一张简单的表 ==")
        page.goto(BASE + "/panel#/roster/%d" % rid)
        page.wait_for_timeout(800)
        check("名单详情里有打印入口", page.locator("#rosterPrintBtn").count() == 1)
        page.goto(BASE + "/print?kind=roster&id=%d" % rid)
        page.wait_for_selector(".sheet-page", timeout=15000)
        page.wait_for_timeout(400)
        sheet = page.evaluate("""() => {
            const t = document.querySelector('.sheet-table');
            const cols = [...t.querySelectorAll('thead th')].map(x => x.textContent.trim());
            return {pages: document.querySelectorAll('.sheet-page').length,
                    nos: [...document.querySelectorAll('.page-no')]
                          .map(x => x.textContent.trim()),
                    cols: cols,
                    rows: document.querySelectorAll('.sheet-table tbody tr').length,
                    cap: !!document.querySelector('.sheet-cap'),
                    body: document.getElementById('paper').innerText};
        }""")
        check("默认没有抬头（标题、二维码都不加）", not sheet["cap"])
        check("也没有打印时间", "打印于" not in sheet["body"], sheet["body"][:80])
        check("列是学号、姓名、院系、班级",
              ["序号", "学号", "姓名", "院系", "班级"] == sheet["cols"], sheet["cols"])
        index = page.evaluate("""() => {
            const th = document.querySelector('.sheet-table thead th');
            const box = th.getBoundingClientRect();
            const range = document.createRange();
            range.selectNodeContents(th);
            const text = range.getBoundingClientRect();
            return {colW: Math.round(box.width), textW: Math.round(text.width),
                    leftGap: Math.round(text.left - box.left),
                    rightGap: Math.round(box.right - text.right),
                    align: getComputedStyle(th).textAlign};
        }""")
        check("序号列的表头居中，左右留白一样",
              index["align"] == "center"
              and abs(index["leftGap"] - index["rightGap"]) <= 1
              and index["textW"] + 2 <= index["colW"], index)
        check("60 人都在表里", sheet["rows"] == 60, sheet["rows"])
        check("每页左下角标页码",
              sheet["nos"] and all("第 " in n and "/" in n for n in sheet["nos"])
              and sheet["nos"][0] == "第 1 / %d 页" % sheet["pages"], sheet["nos"])
        check("页码数与页数一致", len(sheet["nos"]) == sheet["pages"], sheet)

        page.click("#optTitle")
        page.wait_for_timeout(350)
        cap = page.evaluate("""() => {
            const cap = document.querySelector('.sheet-cap');
            return {there: !!cap, text: cap ? cap.innerText.trim() : ""};
        }""")
        check("勾上标题才出现抬头", cap["there"] and "打印测试名单" in cap["text"], cap)
        page.click("#optTitle")
        page.wait_for_timeout(250)
        check("取消勾选又收回去",
              not page.evaluate("() => !!document.querySelector('.sheet-cap')"))

        page.emulate_media(media="print")
        page.wait_for_timeout(250)
        check("打印时工具栏整块消失", page.locator("#toolbar").is_hidden())
        check("纸永远是白底黑字",
              page.evaluate("() => getComputedStyle(document.body).backgroundColor")
              == "rgb(255, 255, 255)")
        page.emulate_media(media="screen")

        print("== 报名单 ==")
        page.goto(BASE + "/panel#/signup/%d" % sid)
        page.wait_for_timeout(700)
        check("报名详情里有打印入口", page.locator("#signupPrintBtn").count() == 1)
        page.goto(BASE + "/print?kind=signup&id=%d" % sid)
        page.wait_for_selector(".sheet-page", timeout=15000)
        page.wait_for_timeout(400)
        check("默认打已报名单，抬头与二维码都不加", page.evaluate(
            """() => !document.querySelector('.sheet-cap')
                     && !document.getElementById('optTitle').checked
                     && !document.getElementById('optQr').checked
                     && !!document.querySelector('[data-mode=list][aria-pressed=true]')"""))
        check("没人报名时说明白",
              "还没有人报名" in page.inner_text("#paper"), page.inner_text("#paper")[:60])

        # 有人报名之后名单里就有他
        gate = browser.new_context(viewport={"width": 430, "height": 900}, locale="zh-CN")
        pub = gate.new_page()
        pub.goto(BASE + "/join/" + str(token), wait_until="networkidle")
        pub.wait_for_timeout(700)
        pub.fill("#sid", "20250031")
        pub.fill("#name", "甲一")
        pub.click("#go")
        pub.wait_for_timeout(1000)
        page.reload()
        page.wait_for_selector(".sheet-table", timeout=15000)
        page.wait_for_timeout(400)
        listed = page.evaluate("""() => {
            const tr = document.querySelector('.sheet-table tbody tr');
            return {rows: document.querySelectorAll('.sheet-table tbody tr').length,
                    text: tr ? tr.innerText.replace(/\s+/g, " ").trim() : "",
                    pages: document.querySelectorAll('.sheet-page').length,
                    no: document.querySelector('.page-no').textContent.trim()};
        }""")
        check("报名的人出现在表里", listed["rows"] == 1 and "20250031" in listed["text"],
              listed)
        check("一页就够时页码写 1 / 1", listed["no"] == "第 1 / 1 页", listed["no"])

        # 空白表还留着，但要人自己去选
        page.click('[data-mode="blank"]')
        page.wait_for_timeout(400)
        blank = page.evaluate("""() => ({
            rows: document.querySelectorAll('.sheet-table tbody tr').length,
            rowsField: !document.getElementById('rowsField').hidden})""")
        check("选空白表才有空行与行数", blank["rowsField"] and blank["rows"] > 0, blank)
        check("这一段没有 javascript 错误", not page._errors,
              "; ".join(page._errors)[:200])
    finally:
        if gate is not None:
            gate.close()
        ctx.close()
        # 收尾：平台不给直接删，先归档再由超管彻底删除
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        drop_signup(owner, sid)
        drop_roster(owner, rid)


def png_1bit(path):
    """读 1 位灰度 PNG：宽、高、位深、颜色类型、黑像素数、文件字节数。"""
    import struct
    import zlib
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", data[16:24])
    depth, ctype = data[24], data[25]
    pos, idat = 8, b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = 1 + (width + 7) // 8
    dark = 0
    for row in range(height):
        line = raw[row * stride:(row + 1) * stride]
        for col in range(width):
            if not ((line[1 + col // 8] >> (7 - (col % 8))) & 1):
                dark += 1
    return {"w": width, "h": height, "depth": depth, "type": ctype,
            "dark": dark, "bytes": len(data)}


def qr_formats(browser):
    """二维码的其它格式：按钮拼成一颗，菜单里 1:1 单色 PNG 与 SVG 都能下。"""
    print("== 二维码的更多格式 ==")
    owner = owner_opener()
    rid = call(owner, "/api/panel/roster/import", {
        "name": "二维码格式名单", "text": "学号,姓名\n20250051,甲一"})["id"]
    sid = call(owner, "/api/panel/signup/create", {
        "title": "二维码格式报名", "roster_id": rid})["id"]
    out = os.path.join(ROOT_DIR, "tmp", "ui")
    os.makedirs(out, exist_ok=True)
    ctx, page = new_page(browser, 1280, 950)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/signup/%d" % sid)
        page.wait_for_timeout(1200)
        box = page.evaluate("""() => {
            const save = document.getElementById('signupQrSave').getBoundingClientRect();
            const more = document.getElementById('signupQrMore').getBoundingClientRect();
            const qr = document.getElementById('signupQr').getBoundingClientRect();
            return {saveH: Math.round(save.height), moreH: Math.round(more.height),
                    gap: Math.round(more.left - save.right),
                    off: Math.round((save.top + save.bottom) / 2 -
                                    (more.top + more.bottom) / 2),
                    qrMid: Math.round((qr.left + qr.right) / 2),
                    rowMid: Math.round((save.left + more.right) / 2)};
        }""")
        check("「更多格式」和下载按钮同一个框、同一中线",
              box["gap"] == 0 and box["off"] == 0
              and abs(box["saveH"] - box["moreH"]) <= 1, box)
        check("这一排与二维码居中对齐", abs(box["qrMid"] - box["rowMid"]) <= 2, box)

        page.click("#signupQrMore")
        page.wait_for_timeout(300)
        items = page.evaluate("""() => [...document.querySelectorAll('.card-menu [data-qr]')]
            .map(b => b.getAttribute('data-qr'))""")
        check("菜单里给三种格式",
              items == ["png-1to1", "png-print", "svg"], items)

        with page.expect_download(timeout=15000) as got:
            page.click('[data-qr="png-1to1"]')
        name = got.value.suggested_filename or ""
        target = os.path.join(out, "qr-1to1.png")
        got.value.save_as(target)
        info = png_1bit(target)
        modules = page.evaluate("""() => {
            const qr = qrcode(0, 'M');
            qr.addData(document.getElementById('signupUrl').textContent.trim());
            qr.make();
            let n = 0;
            for (let r = 0; r < qr.getModuleCount(); r++)
              for (let c = 0; c < qr.getModuleCount(); c++)
                if (qr.isDark(r, c)) n++;
            return {count: qr.getModuleCount(), dark: n};
        }""")
        check("1:1 的 PNG：一格一个像素", info and info["w"] == modules["count"]
              and info["h"] == modules["count"], (info, modules))
        check("单色：1 位灰度，没有 alpha",
              info and info["depth"] == 1 and info["type"] == 0, info)
        check("每一格都对得上（黑格数与矩阵一致）",
              info and info["dark"] == modules["dark"], (info, modules))
        check("文件名里写着尺寸", str(modules["count"]) + "px" in name, name)

        page.click("#signupQrMore")
        page.wait_for_timeout(250)
        with page.expect_download(timeout=15000) as got2:
            page.click('[data-qr="svg"]')
        svg_path = os.path.join(out, "qr.svg")
        got2.value.save_as(svg_path)
        with open(svg_path, encoding="utf-8") as fh:
            svg = fh.read()
        check("SVG 是矢量，没有白底 rect",
              svg.lstrip().startswith("<svg") and 'fill="white"' not in svg,
              svg[:60])
        check("SVG 的 viewBox 正合模块数",
              "0 0 %d %d" % (modules["count"] * 8, modules["count"] * 8) in svg,
              svg[:80])
        check("这一段没有 javascript 错误", not page._errors,
              "; ".join(page._errors)[:200])
    finally:
        ctx.close()
        # 收尾：平台不给直接删，先归档再由超管彻底删除
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        drop_signup(owner, sid)
        drop_roster(owner, rid)
        for name in ("qr-1to1.png", "qr.svg"):
            try:
                os.remove(os.path.join(out, name))
            except OSError:
                pass


def xlsx_page(browser):
    """xlsx：名单与报名都能导出，名单能把 xlsx 导进来。"""
    print("== xlsx 导入导出 ==")
    owner = owner_opener()
    rid = call(owner, "/api/panel/roster/import", {
        "name": "表格测试名单", "text": "学号,姓名\n20250041,甲一"})["id"]
    sid = call(owner, "/api/panel/signup/create", {
        "title": "表格测试报名", "roster_id": rid})["id"]
    blank = os.path.join(ROOT_DIR, "tmp", "ui")
    os.makedirs(blank, exist_ok=True)
    book = os.path.join(blank, "import-%d.xlsx" % rid)
    with open(book, "wb") as fh:
        fh.write(xlsx.build([["学号", "姓名", "院系", "班级"],
                             ["0009001", "导入甲", "计算机学院", "计科 2501"],
                             ["0009002", "导入乙", "", ""]], "名单"))
    ctx, page = new_page(browser, 1280, 900)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/roster/%d" % rid)
        page.wait_for_timeout(800)
        check("名单页有导出 CSV 与导出 Excel",
              page.locator("#rosterExportBtn").count() == 1
              and page.locator("#rosterXlsxBtn").count() == 1)

        with page.expect_download(timeout=15000) as got:
            page.click("#rosterXlsxBtn")
        download = got.value
        name = download.suggested_filename or ""
        check("导出的文件名带 .xlsx", name.endswith(".xlsx"), name)
        saved = os.path.join(blank, "down-%d.xlsx" % rid)
        download.save_as(saved)
        with open(saved, "rb") as fh:
            rows = xlsx.parse(fh.read())
        check("下载到的确实是 xlsx（能读回来）",
              rows and rows[0][:2] == ["学号", "姓名"], rows[:1])
        check("里面有人", any("20250041" in r for r in rows[1:]), rows[1:2])

        print("== 把 xlsx 导进名单 ==")
        page.set_input_files("#rosterMergeFile", book)
        page.wait_for_timeout(1800)
        names = page.evaluate("""() => [...document.querySelectorAll('#rosterEntryTable tbody td')]
            .map(td => td.textContent.trim())""")
        joined = " ".join(names)
        check("导入的人出现在名单里", "导入甲" in joined and "导入乙" in joined,
              (joined or page.inner_text("#rosterEntryTable"))[:160])
        check("前导零的学号也在", names.count("0009001") == 1, names[:8])

        page.goto(BASE + "/panel#/signup/%d" % sid)
        page.wait_for_timeout(800)
        check("报名页也有导出 Excel", page.locator("#signupXlsxBtn").count() == 1)
        with page.expect_download(timeout=15000) as got2:
            page.click("#signupXlsxBtn")
        check("报名的 xlsx 也是 xlsx",
              (got2.value.suggested_filename or "").endswith(".xlsx"),
              got2.value.suggested_filename)
        check("这一段没有 javascript 错误", not page._errors,
              "; ".join(page._errors)[:200])
    finally:
        ctx.close()
        # 收尾：平台不给直接删，先归档再由超管彻底删除
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        drop_signup(owner, sid)
        drop_roster(owner, rid)
        for name in ("import-%d.xlsx" % rid, "down-%d.xlsx" % rid):
            try:
                os.remove(os.path.join(blank, name))
            except OSError:
                pass


def notice_page(page):
    print("== 通知中心 ==")
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import panel as _panel

    _panel.init_db()
    with _panel.tx() as tx:
        _panel.notice(tx, "important", "周末活动报名已开放", "报名截止到周五 18:00。")
        _panel.notice(tx, "critical", "服务将在 22:00 重启", "重启约需 30 秒。")

    page.goto(BASE + "/panel#/overview", wait_until="networkidle")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1600)
    check("a critical notice opens full screen",
          page.locator("#alertBack").is_visible())
    check("it says what it is about",
          "服务将在 22:00 重启" in page.inner_text("#alertTitle"),
          page.inner_text("#alertTitle"))
    page.mouse.click(30, 400)
    page.wait_for_timeout(400)
    check("clicking around it does not dismiss it",
          page.locator("#alertBack").is_visible())
    page.click("#alertRead")
    page.wait_for_timeout(800)
    check("已读 closes it", page.locator("#alertBack").is_hidden())
    check("the bell keeps a red dot for the rest",
          page.locator("#bellDot").is_visible())
    page.click("#bellBtn")
    page.wait_for_timeout(600)
    check("the panel lists the notices",
          page.locator(".notice-item:not(.svc)").count() == 2,
          page.locator(".notice-item").count())
    check("等级用图标表示",
          page.locator(".notice-item .notice-ico").count() >= 2
          and page.locator(".notice-level").count() == 0,
          page.evaluate("""() => ({
              icons: document.querySelectorAll('.notice-item .notice-ico').length,
              pills: document.querySelectorAll('.notice-level').length,
          })"""))
    page.click("#noticeReadAll")
    page.wait_for_timeout(700)
    check("全部已读 clears the dot", page.locator("#bellDot").is_hidden())
    check("the panel has no title bar of its own",
          page.locator("#noticePanel .notice-head").count() == 0)
    page.click("#bellBtn")   # 再点铃铛收起
    page.wait_for_timeout(90)
    closing = page.evaluate("""() => {
        const p = document.getElementById('noticePanel');
        const cs = getComputedStyle(p);
        return {open: p.classList.contains('open'), vis: cs.visibility,
                tf: cs.transform};
    }""")
    page.wait_for_timeout(400)
    shut = page.evaluate("""() => {
        const p = document.getElementById('noticePanel');
        const cs = getComputedStyle(p);
        const box = p.getBoundingClientRect();
        const bar = document.querySelector('.titlebar');
        return {open: p.classList.contains('open'), vis: cs.visibility,
                tf: cs.transform, top: Math.round(box.top), h: Math.round(box.height),
                zPanel: Number(cs.zIndex),
                zBar: Number(getComputedStyle(bar).zIndex),
                barBottom: Math.round(bar.getBoundingClientRect().bottom)};
    }""")
    check("收起时缩到顶栏底下，不是直接消失",
          not shut["open"] and shut["vis"] == "hidden"
          and shut["tf"] != "none" and shut["top"] < shut["barBottom"], shut)
    check("它在顶栏下面（滑入时被顶栏盖住）", shut["zPanel"] < shut["zBar"], shut)
    check("收起的过程是滑出去的", closing["tf"] != "none", closing)

    page.click("#bellBtn")
    page.wait_for_timeout(60)
    mid = page.evaluate("""() => {
        const cs = getComputedStyle(document.getElementById('noticePanel'));
        return {tf: cs.transform, vis: cs.visibility};
    }""")
    page.wait_for_timeout(400)
    shown = page.evaluate("""() => {
        const p = document.getElementById('noticePanel');
        const cs = getComputedStyle(p);
        const box = p.getBoundingClientRect();
        const bar = document.querySelector('.titlebar').getBoundingClientRect();
        return {open: p.classList.contains('open'), tf: cs.transform,
                vis: cs.visibility, gap: Math.round(box.top - bar.bottom)};
    }""")
    check("滑入是从中间位置过来的", mid["tf"] != "none" and mid["vis"] == "visible", mid)
    check("展开后停在顶栏下方 10px 处",
          shown["open"] and shown["tf"] == "none" and shown["gap"] == 10, shown)
    page.click("#bellBtn")
    page.wait_for_timeout(400)


def routes_page(page):
    print("== every route renders its own page ==")
    for route, expect in [
            ("overview", "总览"), ("assistant", "运维助手"), ("events", "我的活动"),
            ("rosters", "我的名单"),
            ("signups", "我的报名"), ("write-approvals", "写入审批"),
            ("screens", "展示屏幕"),
            ("templates", "预设管理"),
            ("users", "用户管理"), ("all-events", "活动管理"),
            ("approval-users", "注册申请"), ("approval-events", "活动申请"),
            ("modules", "模块管理"), ("server", "服务器管理"), ("audit", "审计日志"),
            ("account", "账户信息"), ("appearance", "界面设置")]:
        page.goto(BASE + "/panel#/" + route)
        page.wait_for_timeout(230)
        title = page.locator('.page[data-page="%s"] h1' % route).inner_text()
        check("%-16s -> %s" % (route, title), expect in title, title)


def docs_links(browser):
    """文档页里的站内链接都该落在清单里：漏一份文档就会显示成「链接已断」。"""
    print("== 文档页的站内链接 ==")
    ctx, page = new_page(browser, 1280, 900)
    try:
        ui_login(page)
        for path in ("usage/platform-pages.md", "usage/orgs.md", "usage/start.md"):
            page.goto(BASE + "/docs#/" + path)
            page.wait_for_timeout(1500)
            info = page.evaluate("""() => ({
                broken: Array.from(document.querySelectorAll('.md-broken'))
                    .map(x => x.textContent),
                docs: Array.from(document.querySelectorAll('.md-doc')).length,
            })""")
            check("%s 里没有断链" % path, not info["broken"], info["broken"])
    finally:
        ctx.close()


def docs_page(browser):
    """使用文档：侧栏那一项是新标签页，那一页自带一套骨架。"""
    print("== 使用文档（独立页面）==")
    owner = owner_opener()
    ctx, page = new_page(browser, 1280, 820)
    try:
        ui_login(page)
        item = page.evaluate("""() => {
            const a = document.querySelector('.nav-ext');
            if (!a) return {none: true};
            const mark = a.querySelector('.nav-ext-mark');
            return {href: a.getAttribute('href'), target: a.getAttribute('target'),
                    rel: a.getAttribute('rel') || '', text: a.innerText.trim(),
                    mark: !!mark};
        }""")
        check("侧栏里有「使用文档」，点了开新标签页",
              item.get("text") == "使用文档" and item.get("target") == "_blank"
              and item.get("href", "").endswith("docs")
              and "noopener" in item.get("rel", ""), item)
        check("它右边有一枚开新窗口的标记", item.get("mark"), item)

        page.goto(BASE + "/docs", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        info = page.evaluate("""() => {
            const art = document.getElementById('docsArticle');
            return {groups: document.querySelectorAll('.tree-group').length,
                    docs: document.querySelectorAll('.tree-doc').length,
                    h1: (art.querySelector('h1') || {}).innerText || '',
                    crumb: document.getElementById('docsCrumb').innerText,
                    hash: location.hash,
                    panelChrome: !!document.querySelector('.sidebar')
                                  || !!document.getElementById('shell')};
        }""")
        check("文档页不带面板外壳", not info["panelChrome"], info)
        check("左边是按目录分组的树", info["groups"] >= 2 and info["docs"] >= 5, info)
        check("默认打开第一篇并渲染出正文",
              bool(info["h1"]) and bool(info["crumb"]) and info["hash"].startswith("#"),
              info)
        check("地址里省掉了 doc/ 前缀",
              info["hash"].startswith("#/") and "/doc/" not in info["hash"]
              and "doc/" not in info["hash"].lstrip("#/"), info["hash"])
        tree = page.evaluate("""() => ({
            open: document.querySelectorAll('.tree-group.open').length,
            withOn: Array.from(document.querySelectorAll('.tree-group')).filter(
                g => g.querySelector('.tree-doc.on')).length})""")
        check("默认只展开当前这一篇所在的那一组",
              tree["open"] == 1 and tree["withOn"] == 1, tree)
        head = page.evaluate("""() => {
            const h1 = document.querySelector('.docs-article > h1');
            const meta = document.querySelector('.docs-meta');
            const first = document.querySelector('#docsArticle .md h1');
            return {title: h1 ? h1.innerText.trim() : '',
                    meta: meta ? meta.innerText.trim() : '',
                    dupTitle: !!first};
        }""")
        check("大标题提出来单独显示，正文里不再重复",
              bool(head["title"]) and not head["dupTitle"], head)
        check("标题下面写着上次更新日期",
              re.fullmatch(r"上次更新 \d{4}/\d{2}/\d{2}", head["meta"] or "") is not None,
              head["meta"])

        tones = page.evaluate("""() => {
            const el = document.querySelector('#docsArticle .md-table th')
                    || document.querySelector('#docsArticle .md-table');
            const line = document.querySelector('#docsArticle .md-meta, .docs-meta');
            const cs = el ? getComputedStyle(el) : null;
            return {table: cs ? cs.borderTopColor : '',
                    line: line ? getComputedStyle(line).borderBottomColor : ''};
        }""")
        def grey(rgb):
            nums = [int(n) for n in re.findall(r"\d+", rgb)[:3]]
            return len(nums) == 3 and max(nums) - min(nums) <= 6
        check("表格与分隔线走灰度，不带主站那种暖色",
              grey(tones["table"]) or not tones["table"], tones)
        check("正文用的是同一套 Markdown 渲染",
              page.evaluate("""() => !!document.querySelector('#docsArticle h1')
                  && !!document.querySelector('#docsArticle p')"""))

        # 换一篇：另一组默认是收着的，先展开再点
        page.click(".tree-group:nth-child(2) .tree-head")
        page.wait_for_timeout(300)
        check("点分组标题能展开", page.evaluate(
            "() => document.querySelector('.tree-group:nth-child(2)').classList.contains('open')"))
        page.click(".tree-group:nth-child(2) .tree-doc")
        page.wait_for_timeout(700)
        second = page.evaluate("""() => ({
            hash: location.hash,
            h1: (document.querySelector('#docsArticle h1') || {}).innerText || '',
            on: document.querySelectorAll('.tree-doc.on').length,
            open: document.querySelectorAll('.tree-group.open').length})""")
        check("点另一条就换一篇，选中态跟着走",
              bool(second["h1"]) and second["on"] == 1, second)
        page.click(".tree-group:nth-child(2) .tree-head")
        page.wait_for_timeout(300)
        check("再点一下能收起", not page.evaluate(
            "() => document.querySelector('.tree-group:nth-child(2)').classList.contains('open')"))
        page.click(".tree-group:nth-child(2) .tree-head")
        page.wait_for_timeout(200)

        # 深浅色：存在本机，键名与面板分开；第一次进来沿用面板的选择
        before = page.evaluate(
            "() => document.documentElement.getAttribute('data-theme')")
        page.click("#docsTheme")
        page.wait_for_timeout(300)
        after = page.evaluate("""() => ({
            theme: document.documentElement.getAttribute('data-theme'),
            docs: localStorage.getItem('xl.docs.theme'),
            panel: localStorage.getItem('xl.panel.theme')})""")
        check("右上角那颗切换深浅色",
              after["theme"] != before and after["docs"] in ("dark", "light"), after)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        check("刷新之后还是刚才那个色",
              page.evaluate("() => document.documentElement.getAttribute('data-theme')")
              == after["theme"], after)
        # 第一次进来（docs 的键还没有）时沿用面板的设置
        page.evaluate("""(theme) => {
            localStorage.removeItem('xl.docs.theme');
            localStorage.setItem('xl.panel.theme', theme);
        }""", "dark" if after["theme"] == "light" else "light")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        inherited = page.evaluate("""() => ({
            theme: document.documentElement.getAttribute('data-theme'),
            docs: localStorage.getItem('xl.docs.theme')})""")
        check("第一次进文档页时沿用面板的深浅色",
              inherited["docs"] in ("dark", "light")
              and inherited["theme"] == inherited["docs"], inherited)

        # 链接解析：只认白名单里的文档，站外与不认的路径退化成普通文字
        links = page.evaluate("""() => {
            const docs = ['usage/approval.md', 'reference/glossary.md',
                          'builtin/modules/a/doc.md', 'builtin/modules/b/doc.md'];
            const at = (target, base) => MD.resolveLink(target, {
                docs: docs, base: base,
                href: (path, anchor) => '#/' + path + (anchor ? '~' + anchor : ''),
                selfAnchor: anchor => '#/usage/approval.md~' + anchor
            });
            const render = (t, base) => MD.render(t, {
                docs: docs, base: base, anchors: true, link: target => at(target, base)
            });
            const box = document.createElement('div');
            box.innerHTML = render('[审批](approval.md) 和 [词表](../reference/glossary.md) 和 ' +
                '[没了](nope.md) 和 [站外](https://example.com)', 'usage');
            const a = Array.from(box.querySelectorAll('a'));
            const same = render('[跳到某节](#登录名)', 'usage');
            const jump = document.createElement('div');
            jump.innerHTML = same;
            // 助手那边没有「当前目录」，路径从文档根算：这四种写法都该认
            const forms = ['../usage/approval.md', 'usage/approval.md',
                           'doc/usage/approval.md', 'approval.md#注册申请']
                .map(t => {
                    const hit = at(t, '');
                    return hit ? (hit.broken ? 'broken' : hit.href) : 'text';
                });
            const ambiguous = at('doc.md', '');
            return {count: a.length,
                    first: a[0] ? a[0].getAttribute('data-doc-link') : '',
                    second: a[1] ? a[1].getAttribute('href') : '',
                    third: a[2] ? a[2].getAttribute('href') : '',
                    icon: a[0] ? !!a[0].querySelector('.md-doc-ico') : false,
                    extIcon: a[2] ? !!a[2].querySelector('.md-ext-ico') : false,
                    extTarget: a[2] ? a[2].getAttribute('target') : '',
                    extTitle: a[2] ? a[2].getAttribute('title') : '',
                    broken: box.querySelector('.md-broken')
                            ? box.querySelector('.md-broken').innerText.trim() : '',
                    anchorHref: jump.querySelector('a')
                            ? jump.querySelector('a').getAttribute('href') : '',
                    forms: forms,
                    ambiguous: ambiguous && ambiguous.broken ? 'broken' : 'hit'};
        }""")
        check("相对链接认得清单里的文档（同目录、上级目录都行）",
              links["first"] == "usage/approval.md"
              and links["second"] == "#/reference/glossary.md", links)
        check("链接前面带一枚文档图标", links["icon"], links)
        check("站外链接照常可点", links["third"] == "https://example.com", links)
        check("站外链接带开新窗口的图标，在新标签页打开",
              links["extIcon"] and links["extTarget"] == "_blank"
              and "在新标签页打开" in links["extTitle"], links)
        check("清单外的写成红色断链", links["broken"] == "没了（链接已断）", links)
        check("同一篇里的锚点也能跳", links["anchorHref"].endswith("~登录名"),
              links["anchorHref"])
        check("四种写法都认：相对、清单原文、带 doc/ 前缀、只写文件名加锚点",
              links["forms"] == ["#/usage/approval.md", "#/usage/approval.md",
                                 "#/usage/approval.md", "#/usage/approval.md~注册申请"],
              links["forms"])
        check("只写文件名而重名的当作断链，不猜是哪一份",
              links["ambiguous"] == "broken", links["ambiguous"])
        anchors = page.evaluate("""() => {
            const box = document.createElement('div');
            box.innerHTML = MD.render('## 登录名\\n\\n正文', {anchors: true});
            const h = box.querySelector('h2');
            const off = document.createElement('div');
            off.innerHTML = MD.render('## 登录名', {});
            return {id: h ? h.id : '', plainId: off.querySelector('h2').id};
        }""")
        check("标题带锚点 id（不给 anchors 就不带）",
              anchors["id"] == "登录名" and anchors["plainId"] == "", anchors)
        imgs = page.evaluate("""() => {
            const ok = MD.render('![图](pic.png)', {image: t => 'api/docs/img?path=' + t});
            const no = MD.render('![图](pic.png)', {});
            const box = document.createElement('div');
            box.innerHTML = ok;
            return {src: (box.querySelector('img') || {}).getAttribute
                            ? box.querySelector('img').getAttribute('src') : '',
                    literal: no.indexOf('<img') < 0};
        }""")
        check("图片：给了地址才渲染，没给就留原样",
              imgs["src"] == "api/docs/img?path=pic.png" and imgs["literal"], imgs)

        # 真放一张图进去看能不能显示：文档与图都放在临时命名下，验完删掉
        pic = os.path.join(ROOT_DIR, "doc", "usage", "_probe-shot.png")
        shot_doc = os.path.join(ROOT_DIR, "doc", "usage", "_probe-shot.md")

        def tiny_png():
            def chunk(tag, data):
                return (struct.pack(">I", len(data)) + tag + data +
                        struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
            return (b"\x89PNG\r\n\x1a\n" +
                    chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
                    chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) +
                    chunk(b"IEND", b""))

        try:
            with open(pic, "wb") as fh:
                fh.write(tiny_png())
            with open(shot_doc, "w", encoding="utf-8") as fh:
                fh.write("# 图 - 正文里的一张图\n\n![示意](_probe-shot.png)\n")
            page.goto(BASE + "/docs#/usage/_probe-shot.md", wait_until="domcontentloaded")
            page.wait_for_selector("#docsArticle img.md-img", timeout=8000)
            page.wait_for_timeout(400)
            shot = page.evaluate("""() => {
                const img = document.querySelector('#docsArticle img.md-img');
                return {src: img.getAttribute('src'), w: img.naturalWidth,
                        h: img.naturalHeight};
            }""")
            check("文档里的图真的显示出来",
                  shot["w"] > 0 and "api/docs/img" in shot["src"], shot)
        finally:
            for one in (pic, shot_doc):
                if os.path.exists(one):
                    os.remove(one)
        stats = page.evaluate("""() => {
            const sizes = ['#docsBack', '#docsTheme'].map(sel => {
                const r = document.querySelector(sel).getBoundingClientRect();
                return [Math.round(r.width), Math.round(r.height)];
            });
            const svg = document.getElementById('docsTheme').querySelector('svg');
            const cs = svg ? getComputedStyle(svg) : null;
            return {sizes: sizes, icon: cs ? cs.width : ''};
        }""")
        check("右上角两颗按钮同尺寸", stats["sizes"][0] == stats["sizes"][1], stats)
        # "测量"要有意义：量的是图形本身在 20×20 里占的框，重心得落在正中
        centers = page.evaluate("""() => {
            // 太阳/月亮两颗里只量当时看得见的那一颗
            return Array.from(document.querySelectorAll('.docs-acts svg'))
                .filter(svg => svg.getBoundingClientRect().width > 0)
                .map(svg => {
                    const b = svg.getBBox();
                    return [Math.round((b.x + b.width / 2) * 100) / 100,
                            Math.round((b.y + b.height / 2) * 100) / 100];
                });
        }""")
        check("两颗图标的视觉重心都在格子中央",
              len(centers) == 2 and all(abs(c[0] - 10) <= 0.9 and abs(c[1] - 10) <= 0.9
                                        for c in centers), centers)

        # 侧栏顶上那个标志：与面板标题栏同一个（票券加对勾），灰度配色下跟正文色走
        logo = page.evaluate("""() => {
            const box = document.querySelector('.docs-logo');
            const svg = box && box.querySelector('svg');
            if (!svg) return null;
            const b = svg.getBBox();
            return {paths: svg.querySelectorAll('path').length,
                    w: Math.round(box.getBoundingClientRect().width),
                    bg: getComputedStyle(box).backgroundColor,
                    center: [Math.round((b.x + b.width / 2) * 100) / 100,
                             Math.round((b.y + b.height / 2) * 100) / 100]};
        }""")
        check("文档页顶上也是同一个标志，不是占位方块",
              logo is not None and logo["paths"] >= 2 and logo["w"] >= 12
              and logo["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), logo)
        check("标志的重心落在格子中央",
              logo is not None and abs(logo["center"][0] - 10) <= 0.9
              and abs(logo["center"][1] - 10) <= 0.9, logo)

        # 地址里的 ~锚点真的会滚到那一节，而且同一篇里换锚点不重取正文
        page.goto(BASE + "/docs#/usage/assistant.md~" +
                  urllib.request.quote("文档查询"), wait_until="domcontentloaded")
        page.wait_for_selector("#docsArticle h1", timeout=8000)
        page.wait_for_timeout(700)
        jump = page.evaluate("""() => {
            const want = document.getElementById('文档查询');
            window.__keepNode = document.getElementById('docsArticle').firstChild;
            if (!want) return null;
            const scrolled = Math.round(window.scrollY);
            const target = want.getBoundingClientRect().top + window.scrollY - 64;
            const limit = document.documentElement.scrollHeight - window.innerHeight;
            return {scrolled, expected: Math.round(Math.min(Math.max(0, target), limit))};
        }""")
        check("地址里的锚点会滚到那一节",
              jump is not None and jump["scrolled"] > 0
              and abs(jump["scrolled"] - jump["expected"]) <= 1, jump)
        page.evaluate("""() => {
            location.hash = '#/usage/assistant.md~' +
                            encodeURIComponent('模型设置');
        }""")
        page.wait_for_timeout(500)
        again = page.evaluate("""() => {
            const el = document.getElementById('模型设置');
            return {top: el ? Math.round(el.getBoundingClientRect().top) : -1,
                    scrolled: Math.round(window.scrollY),
                    kept: document.getElementById('docsArticle').firstChild ===
                          window.__keepNode,
                    busy: document.querySelector('.docs-body')
                          .classList.contains('is-busy')};
        }""")
        check("同一篇里换锚点也能跳，而且不重取正文",
              again["kept"] and not again["busy"] and 0 <= again["top"] <= 120
              and again["scrolled"] < jump["scrolled"], again)

        # 触屏：点一下不该留下系统那层蓝色方框，也不该把悬停底色粘在按钮上
        tctx, tpage = new_page(browser, 390, 844, touch=True, mobile=True, dpr=3)
        try:
            ui_login(tpage)
            tpage.goto(BASE + "/docs", wait_until="domcontentloaded")
            tpage.wait_for_timeout(900)
            media = tpage.evaluate("""() => ({
                noHover: matchMedia('(hover: none)').matches,
                tap: getComputedStyle(document.body).webkitTapHighlightColor})""")
            check("这一路是「没有悬停」的触屏", media["noHover"], media)
            check("系统那层点击高亮已经关掉（安卓会画成蓝方框）",
                  media["tap"] in ("transparent", "rgba(0, 0, 0, 0)"), media)
            tpage.tap("#docsTheme")
            tpage.wait_for_timeout(500)
            after = tpage.evaluate("""() => {
                const b = document.getElementById('docsTheme');
                return {bg: getComputedStyle(b).backgroundColor,
                        theme: document.documentElement.getAttribute('data-theme')};
            }""")
            check("点完深浅色换了，按钮上不留一块粘住的底色",
                  after["theme"] == "dark" and after["bg"] == "rgba(0, 0, 0, 0)", after)
            tpage.tap("#docsMenu")
            tpage.wait_for_timeout(400)
            check("触屏上抽屉还能开",
                  tpage.evaluate("() => document.getElementById('docsShell')"
                                 ".classList.contains('nav-open')"))
        finally:
            tctx.close()

        # 白名单：不认清单之外的路径
        try:
            bad = call(owner, "/api/docs/doc?path=" +
                       urllib.request.quote("../../etc/passwd"))
            check("只认清单里出现过的文档", bad.get("ok") is False, bad)
        except urllib.error.HTTPError as exc:
            check("只认清单里出现过的文档", exc.code == 404, exc.code)
        check("这一段没有 javascript 错误", not page._errors,
              "; ".join(page._errors)[:200])
    finally:
        try:
            page.evaluate("() => { localStorage.removeItem('xl.docs.theme');"
                          "localStorage.setItem('xl.panel.theme', 'auto'); }")
        except Exception:
            pass
        ctx.close()


def assistant_page(page):
    print("== 运维助手 ==")
    page.goto(BASE + "/panel#/assistant")
    page.wait_for_timeout(900)
    check("总览下面有这一页", page.locator('[data-page="assistant"]').is_visible())
    check("没配端点时说清楚该去配什么",
          "尚未配置模型" in page.inner_text("#assistantSub"),
          page.inner_text("#assistantSub"))

    # 新会话那一页：两行欢迎语 + 一行存档说明，不放示例问题；导出键灰着
    page.click("#assistantReset")
    page.wait_for_timeout(600)
    empty = page.evaluate("""() => {
        const box = document.getElementById('assistantEmpty');
        const exp = document.getElementById('asExport');
        return {shown: !box.hidden,
                lead: box.querySelector('.assistant-lead').innerText.trim(),
                sub: box.querySelector('.assistant-lead-sub').innerText.trim(),
                note: document.getElementById('asKeepNote').innerText.trim(),
                asks: box.querySelectorAll('.assistant-ask').length,
                expDisabled: exp.disabled, expTitle: exp.title};
    }""")
    check("新会话写着两行欢迎语",
          empty["shown"] and empty["lead"] == "你好，我是 CAEventManager 运维助手。"
          and empty["sub"] == "我可以帮你查数据和执行操作。关于平台的问题也可以问我。", empty)
    check("说明同一账号可在不同设备查看存档",
          empty["note"] == "对话会存档在账户上，可在设备间漫游", empty)
    check("新会话不放示例问题", empty["asks"] == 0, empty)
    check("新会话的导出键灰着，title 说明原因",
          empty["expDisabled"] and "还没有存档" in empty["expTitle"], empty)
    box = page.evaluate("""() => {
        const form = document.getElementById('assistantForm');
        const r = form.getBoundingClientRect();
        const send = document.getElementById('assistantSend').getBoundingClientRect();
        return {bottomGap: Math.round(window.innerHeight - r.bottom),
                insideSend: send.right <= r.right + 1 && send.left >= r.left,
                round: Math.round(send.width) === Math.round(send.height),
                expand: !!document.getElementById('asExpand'),
                gear: !!document.getElementById('asSettings')};
    }""")
    check("输入框常驻在页面底部", box["bottomGap"] <= 40, box)
    check("发送按钮嵌在框内，而且是圆的", box["insideSend"] and box["round"], box)
    arrow = page.evaluate("""() => {
        const send = document.getElementById('assistantSend').getBoundingClientRect();
        const path = document.querySelector('.as-ic-send path').getBoundingClientRect();
        return {dx: +(path.left + path.width / 2 - (send.left + send.width / 2)).toFixed(2),
                dy: +(path.top + path.height / 2 - (send.top + send.height / 2)).toFixed(2)};
    }""")
    check("发送按钮里的箭头在正中",
          abs(arrow["dx"]) <= 1 and abs(arrow["dy"]) <= 1, arrow)
    check("框里有设置与全屏编辑入口", box["expand"] and box["gear"], box)
    rows = page.evaluate("""() => {
        const t = document.getElementById('assistantText');
        return {h: Math.round(t.getBoundingClientRect().height),
                lh: parseFloat(getComputedStyle(t).lineHeight)};
    }""")
    check("初始是一行高（桌面上）", rows["h"] <= rows["lh"] + 12, rows)
    page.fill("#assistantText", "一\n二\n三\n四\n五\n六\n七\n八")
    page.evaluate("() => document.getElementById('assistantText')"
                  ".dispatchEvent(new Event('input'))")
    page.wait_for_timeout(200)
    grown = page.evaluate("""() => {
        const t = document.getElementById('assistantText');
        const cs = getComputedStyle(t);
        return {h: Math.round(t.getBoundingClientRect().height),
                lh: parseFloat(cs.lineHeight), scroll: cs.overflowY};
    }""")
    check("长到五行就封顶，再长在框里滚动",
          grown["h"] <= grown["lh"] * 5 + 12 and grown["scroll"] == "auto", grown)
    page.evaluate("() => { document.getElementById('assistantText').value = '';"
                  ".dispatchEvent".replace(".dispatchEvent", "") + "}")
    page.fill("#assistantText", "")
    page.evaluate("() => document.getElementById('assistantText')"
                  ".dispatchEvent(new Event('input'))")

    print("== 加载存档对话的中间态 ==")
    # 造一段存档：不需要模型密钥，直接存一次就行。
    # 顺手带上时间戳与"同一个工具调用两次"，用来量气泡下面的时间与合并后的动作提示
    now = int(time.time())
    saved = call(owner_opener(), "/api/panel/assistant/session/save",
                 {"id": 0, "title": "中间态测试",
                  "messages": [{"role": "user", "content": "你好", "ts": now},
                               {"role": "assistant", "content": "在", "ts": now,
                                "steps": [
                                    {"tool": "roster_entries", "ok": True,
                                     "detail": {"args": {"roster_id": "3", "q": "张"},
                                                "result": "6 条"}},
                                    {"tool": "roster_entries", "ok": True,
                                     "detail": {"args": {"roster_id": "3"},
                                                "result": "2 条"}},
                                    {"tool": "roster_add_person", "ok": True,
                                     "detail": {"args": {"sid": "2024001", "name": "张三"},
                                                "result": "完成"}},
                                    {"tool": "session_title", "ok": True,
                                     "detail": {"args": {"title": "报名核查"},
                                                "result": "完成"}},
                                    {"tool": "list_rosters", "ok": False,
                                     "detail": {"args": {},
                                                "result": "这一步没有权限"}}]}]})
    sid = (saved or {}).get("id")
    check("先存下一段对话", bool(sid), saved)

    # 采样器：切到助手页，之后每帧记一次"正在加载对话"有没有露出来
    probe = """(payload) => new Promise(resolve => {
        if (payload.last === null) localStorage.removeItem('as:lastSession');
        else localStorage.setItem('as:lastSession', String(payload.last));
        location.hash = '#/overview';
        setTimeout(() => {
            const seen = [];
            const t0 = performance.now();
            location.hash = '#/assistant';
            (function tick() {
                const el = document.getElementById('assistantLoading');
                if (el && !el.hidden) seen.push(Math.round(performance.now() - t0));
                if (performance.now() - t0 < 500) requestAnimationFrame(tick);
                else resolve(seen);
            })();
        }, 320);
    })"""
    if sid:
        # 读存档那一屏：把这条请求扣住，等"正在加载对话"确实画出来了再放行。
        # 不扣的话本机答得太快，偶尔一帧都采不到（这条断言以前是这么飘的）
        held = {}

        def hold_session(route):
            held["route"] = route

        page.route("**/api/panel/assistant/session?id=*", hold_session)
        page.evaluate("""(sid) => {
            localStorage.setItem('as:lastSession', String(sid));
            location.hash = '#/overview';
        }""", sid)
        page.wait_for_timeout(300)
        page.evaluate("() => { location.hash = '#/assistant'; }")
        shown = None
        try:
            page.wait_for_selector("#assistantLoading:not([hidden])", timeout=5000)
            shown = (page.locator("#assistantLoading").is_visible()
                     and page.locator("#assistantEmpty").is_hidden())
        except Exception:
            shown = False
        check("打开存档对话时先整块显示正在加载对话", bool(shown), shown)
        if held.get("route"):
            try:
                held["route"].continue_()
            except Exception:
                pass
        page.unroute("**/api/panel/assistant/session?id=*")
        page.wait_for_selector("#assistantLoading", state="hidden", timeout=8000)
        page.wait_for_timeout(200)
        check("读完之后历史在上面",
              "你好" in page.inner_text("#assistantLog"),
              page.inner_text("#assistantLog")[:80])
        seen_new = page.evaluate(probe, {"last": None})
        check("本来就没有对话时不闪加载态", seen_new == [], seen_new)
        page.wait_for_timeout(400)
        check("新对话直接就是那一页空白",
              page.locator("#assistantEmpty").is_visible()
              and page.locator("#assistantLoading").is_hidden())

    print("== 气泡下的时间与动作提示 ==")
    page.evaluate("""(sid) => {
        localStorage.setItem('as:lastSession', String(sid));
        location.hash = '#/overview';
    }""", sid)
    page.wait_for_timeout(300)
    page.evaluate("() => { location.hash = '#/assistant'; }")
    page.wait_for_timeout(700)
    stamps = page.evaluate("""() => {
        const log = document.getElementById('assistantLog');
        const times = Array.from(log.querySelectorAll('.as-time'));
        return {n: times.length, texts: times.map(t => t.textContent.trim())};
    }""")
    check("每条消息下面都有一个时间", stamps["n"] == 2, stamps)
    check("当天的时间只给时分秒",
          all(re.fullmatch(r"\d{2}:\d{2}:\d{2}", t) for t in stamps["texts"]),
          stamps["texts"])
    chips = page.evaluate("""() => Array.from(
        document.querySelectorAll('#assistantLog .as-step')).map(c => ({
            text: c.textContent.trim(), bad: c.classList.contains('bad'),
            write: c.classList.contains('is-write'),
            title: c.getAttribute('title') || ''}))""")
    check("同一个工具连调两次并成一条", "读取名单成员 ×2" in [c["text"] for c in chips], chips)
    check("写动作的提示用强调色分清",
          any(c["text"] == "写入名单" and c["write"] for c in chips), chips)
    check("工具名都换成了人话，没有一个英文名漏出来",
          not any(re.match(r"^[a-z_]+$", c["text"]) for c in chips), chips)
    check("改会话标题也是写动作，用强调色",
          any(c["text"] == "修改会话标题" and c["write"] for c in chips), chips)
    check("没做成的动作标红，不再挂一句“未获准”",
          any(c["text"] == "读取名单列表" and c["bad"] and not c["write"]
              and "未获准" not in c["text"] for c in chips), chips)
    check("红色那条用 title 说明没执行",
          any(c["bad"] and c["title"] == "没有执行" for c in chips), chips)
    check("不再用“看了…”这种说法",
          not any(t.startswith("看了") or t.startswith("查了")
                  for t in [c["text"] for c in chips]), chips)

    print("== 动作胶囊：不出界、悬停看摘要 ==")
    fit = page.evaluate("""() => {
        const log = document.getElementById('assistantLog');
        const steps = log.querySelector('.as-steps');
        const chips = Array.from(log.querySelectorAll('.as-step'));
        const bubble = steps.closest('.as-text').getBoundingClientRect();
        return {overflow: chips.some(c => c.getBoundingClientRect().right
                                         > bubble.right + 1),
                wrap: getComputedStyle(steps).flexWrap,
                inside: !!steps.closest('.as-text'),
                tips: chips.filter(c => c.hasAttribute('data-step-tip')).length};
    }""")
    check("胶囊在气泡里，不超出气泡右边缘，放不下就换行",
          not fit["overflow"] and fit["wrap"] == "wrap" and fit["inside"], fit)
    check("每颗胶囊都带着自己的摘要", fit["tips"] == 4, fit)
    check("胶囊与正文之间有一条分隔线，横穿整块", page.evaluate("""() => {
        const d = document.querySelector('#assistantLog .as-divider');
        const chips = document.querySelector('#assistantLog .as-steps');
        if (!d || !chips) return false;
        const cs = getComputedStyle(d);
        return parseFloat(cs.borderTopWidth) >= 1
            && d.getBoundingClientRect().width >= chips.getBoundingClientRect().width;
    }"""))
    page.hover('#assistantLog .as-step[data-step-tip="0"]')
    page.wait_for_timeout(500)
    tip = page.evaluate("""() => {
        const t = document.getElementById('asStepTip');
        return {open: !t.hidden, text: t.innerText,
                rows: t.querySelectorAll('.as-tip-row').length,
                blocks: t.querySelectorAll('.as-tip-block').length};
    }""")
    check("指上去出现这一步调了什么", tip["open"] and "读取名单成员" in tip["text"]
          and "roster_id" in tip["text"], tip)
    check("同一个工具调两次，摘要分两段", tip["blocks"] == 2, tip)
    page.mouse.move(6, 6)
    page.wait_for_timeout(400)
    check("移开就收起", page.locator("#asStepTip").is_hidden())
    # 触屏：点一下开、点外面关
    page.evaluate("""() => {
        const chip = document.querySelector('#assistantLog .as-step[data-step-tip="1"]');
        chip.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    }""")
    page.wait_for_timeout(300)
    check("点一下也能看", page.locator("#asStepTip").is_visible()
          and "写入名单" in page.inner_text("#asStepTip"), page.inner_text("#asStepTip")[:60])
    page.click(".assistant-page .page-head h1")     # 点页面别处（标题）也算"外面"
    page.wait_for_timeout(300)
    check("点外面收起", page.locator("#asStepTip").is_hidden())

    print("== 会话列表：整卡片高亮与边距 ==")
    # 再来一段对话，才有一张"不是当前这段"的卡片可以量悬停底色
    call(owner_opener(), "/api/panel/assistant/session/save",
         {"id": 0, "title": "另一段对话",
          "messages": [{"role": "user", "content": "再问一句"}]})
    page.click("#asSessionBar")
    page.wait_for_timeout(900)
    box = page.evaluate("""() => {
        const row = document.querySelector('#asDrawerBody .as-srow:not(.on)');
        const del = row.querySelector('[data-as-del]').getBoundingClientRect();
        const open = row.querySelector('.as-sopen').getBoundingClientRect();
        return {rowRight: Math.round(row.getBoundingClientRect().right),
                delRight: Math.round(del.right),
                rowPad: getComputedStyle(row).paddingRight,
                openLeft: Math.round(open.left)};
    }""")
    check("删除键与卡片右边留出边距",
          box["rowRight"] - box["delRight"] >= 2 and box["rowPad"].startswith("4"),
          box)

    def row_bg():
        return page.evaluate("""() => getComputedStyle(
            document.querySelector('#asDrawerBody .as-srow:not(.on)')).backgroundColor""")

    idle_bg = row_bg()
    drawer_bg = page.evaluate("""() => getComputedStyle(
        document.getElementById('asDrawer')).backgroundColor""")

    def rgb(text):
        return [int(n) for n in re.findall(r"\d+", text)[:3]]

    # 悬停在标题上、悬停在删除键上：整张卡片都应该亮起来，而且是同一种底色
    open_box = page.locator("#asDrawerBody .as-srow:not(.on) .as-sopen").first.bounding_box()
    page.mouse.move(open_box["x"] + 20, open_box["y"] + open_box["height"] / 2)
    page.wait_for_timeout(250)
    on_title = row_bg()
    del_box = page.locator('#asDrawerBody .as-srow:not(.on) [data-as-del]').first.bounding_box()
    page.mouse.move(del_box["x"] + del_box["width"] / 2,
                    del_box["y"] + del_box["height"] / 2)
    page.wait_for_timeout(250)
    on_del = row_bg()
    check("悬停标题时整张卡片高亮", on_title != idle_bg, (idle_bg, on_title))
    # 高亮要看得出来：与抽屉底色至少差一档（差得太近等于没有）
    delta = max(abs(a - b) for a, b in zip(rgb(on_title), rgb(drawer_bg)))
    check("高亮与抽屉底色拉得开（不是看不见的一档）",
          delta >= 8, (on_title, drawer_bg, delta))
    check("悬停按钮时卡片底色不变（高亮覆盖整个卡片）",
          on_del == on_title, (on_title, on_del))

    # 当前这一段本来就有底气色（强调色那一档）：悬停也得看得出来，而且不是同一档
    cur_idle = page.evaluate("""() => getComputedStyle(
        document.querySelector('#asDrawerBody .as-srow.on')).backgroundColor""")
    cur_box = page.locator("#asDrawerBody .as-srow.on .as-sopen").first.bounding_box()
    page.mouse.move(cur_box["x"] + 20, cur_box["y"] + cur_box["height"] / 2)
    page.wait_for_timeout(250)
    cur_hover = page.evaluate("""() => getComputedStyle(
        document.querySelector('#asDrawerBody .as-srow.on')).backgroundColor""")
    cur_delta = max(abs(a - b) for a, b in zip(rgb(cur_idle), rgb(cur_hover)))
    check("当前会话那张卡片悬停也有指示",
          cur_idle != cur_hover and cur_delta >= 8, (cur_idle, cur_hover, cur_delta))
    page.mouse.move(4, 4)
    page.wait_for_timeout(200)

    # 点当前这一段：抽屉收起来，但别把它从服务端重新读一遍
    reads = []
    page.on("request", lambda r: reads.append(r.url))
    reads.clear()
    page.click("#asDrawerBody .as-srow.on .as-sopen")
    page.wait_for_timeout(600)
    reloaded = [u for u in reads if "/assistant/session?id=" in u]
    check("点当前会话什么也不做，不重新读一遍",
          not reloaded
          and not page.locator("#asDrawer").is_visible(), (reloaded, reads[:4]))

    # 抽屉头上、叉的左边有个「新建会话」
    page.click("#asSessionBar")
    page.wait_for_timeout(700)
    head = page.evaluate("""() => {
        const neu = document.getElementById('asDrawerNew');
        const close = document.getElementById('asDrawerClose');
        if (!neu || !close) return {there: false};
        const n = neu.getBoundingClientRect(), c = close.getBoundingClientRect();
        return {there: true, leftOfClose: n.right <= c.left + 1,
                label: neu.getAttribute('aria-label'),
                round: Math.round(n.width) === Math.round(n.height)};
    }""")
    check("会话抽屉里有个新建会话按钮，排在叉的左边",
          head.get("there") and head.get("leftOfClose")
          and head.get("label") == "新建会话", head)
    page.click("#asDrawerNew")
    page.wait_for_timeout(700)
    check("点它开一段新对话",
          page.locator("#assistantEmpty").is_visible()
          and page.inner_text("#asSessionName") == "新会话"
          and not page.locator("#asDrawer").is_visible(),
          (page.inner_text("#asSessionName"),
           page.locator("#assistantEmpty").is_visible()))

    print("== 待批准的小红点 ==")
    dots = page.evaluate("""() => {
        const dot = document.getElementById('assistantDot');
        const cs = dot ? getComputedStyle(dot) : null;
        return {there: !!dot, hidden: dot ? dot.hidden : null,
                w: cs ? cs.width : "", bg: cs ? cs.backgroundColor : "",
                nav: !!document.querySelector('[data-nav="assistant"] #assistantDot')};
    }""")
    check("运维助手那一项里有个小红点，平时收着",
          dots["there"] and dots["nav"] and dots["hidden"], dots)
    check("红点很小、不带数字", dots["w"] in ("7px", "6px", "8px") and dots["bg"] != "", dots)

    print("== Markdown 渲染 ==")
    md_sample = "\n".join([
        "# 一级标题",
        "## 二级标题",
        "###### 六级标题",
        "",
        "**粗体** 与 *斜体* 与 ~~删除~~ 与 `code`，按键 <kbd>Ctrl</kbd>+<kbd>C</kbd>，"
        "水的分子式 H<sub>2</sub>O，平方 x<sup>2</sup>。",
        "",
        "转义：\\*不是斜体\\*、\\`不是代码\\`、\\\\ 是一个反斜杠。",
        "",
        "> 一层引用",
        "> > 二层引用",
        "",
        "- 甲",
        "  - 甲一",
        "    - 甲一一",
        "- 乙",
        "",
        "1. 第一",
        "2. 第二",
        "",
        "- [ ] 没做",
        "- [x] 做完了",
        "",
        "| 学号 | 姓名 |",
        "| --- | ---: |",
        "| 2024001 | 张三 |",
        "",
        "---",
        "",
        "```python",
        "print('hi')",
        "```",
    ])
    md_saved = call(owner_opener(), "/api/panel/assistant/session/save",
                    {"id": 0, "title": "Markdown 测试",
                     "messages": [{"role": "user", "content": "渲染一下", "ts": now},
                                  {"role": "assistant", "content": md_sample,
                                   "ts": now}]})
    md_sid = (md_saved or {}).get("id")
    page.evaluate("""(sid) => {
        localStorage.setItem('as:lastSession', String(sid));
        location.hash = '#/overview';
    }""", md_sid)
    page.wait_for_timeout(300)
    page.evaluate("() => { location.hash = '#/assistant'; }")
    page.wait_for_timeout(900)
    md = page.evaluate("""() => {
        const log = document.getElementById('assistantLog');
        const text = log.innerText;
        const css = el => el ? getComputedStyle(el) : null;
        const h1 = log.querySelector('h1.md-h1'), h2 = log.querySelector('h2.md-h2');
        const h6 = log.querySelector('h6.md-h6');
        const quotes = Array.from(log.querySelectorAll('blockquote'));
        const boxes = Array.from(log.querySelectorAll('.md-box'));
        const code = log.querySelector('.md-code');
        const table = log.querySelector('.md-table');
        const td = table ? table.querySelectorAll('tbody td') : [];
        return {
            text: text,
            h1: h1 ? h1.textContent.trim() : '',
            h2: h2 ? h2.textContent.trim() : '',
            h6: h6 ? h6.textContent.trim() : '',
            h1Size: h1 ? parseFloat(css(h1).fontSize) : 0,
            h6Size: h6 ? parseFloat(css(h6).fontSize) : 0,
            em: log.querySelector('em') ? log.querySelector('em').textContent : '',
            strong: log.querySelector('strong') ? log.querySelector('strong').textContent : '',
            del: log.querySelector('del') ? log.querySelector('del').textContent : '',
            kbd: log.querySelectorAll('kbd').length,
            sup: log.querySelectorAll('sup').length,
            sub: log.querySelectorAll('sub').length,
            quotes: quotes.length,
            nestedQuote: !!log.querySelector('blockquote blockquote'),
            deepList: !!log.querySelector('ul ul ul li'),
            boxes: boxes.length,
            checked: Array.from(log.querySelectorAll('.md-box')).filter(
                b => b.classList.contains('on')).length,
            table: !!table,
            th: table ? table.querySelectorAll('thead th').length : 0,
            td: td.length,
            rightAligned: td.length ? td[1].style.textAlign : '',
            hr: log.querySelectorAll('hr').length,
            lang: code ? code.querySelector('.md-code-lang').textContent.trim() : '',
            codeText: code ? code.querySelector('pre code').textContent : '',
            copyBtn: !!(code && code.querySelector('[data-copy-code]')),
            msgCopy: log.querySelectorAll('[data-copy-msg]').length,
            times: log.querySelectorAll('.as-time').length,
            rawAsterisk: text.indexOf('\\u0001') < 0 && text.indexOf('\\\\*') < 0,
        };
    }""")
    check("多级标题各自成级，字号拉得开",
          md["h1"] == "一级标题" and md["h2"] == "二级标题" and md["h6"] == "六级标题"
          and md["h1Size"] - md["h6Size"] >= 2, (md["h1Size"], md["h6Size"]))
    check("斜体、粗体、删除线都生效",
          md["em"] == "斜体" and md["strong"] == "粗体" and md["del"] == "删除", md)
    check("<kbd>、上下标生效",
          md["kbd"] == 2 and md["sup"] == 1 and md["sub"] == 1, md)
    check("反斜杠转义：星号与反引号变回字面量，且不残留反斜杠",
          "*不是斜体*" in md["text"] and "`不是代码`" in md["text"]
          and md["rawAsterisk"], md["text"][-160:])
    check("引用可以嵌套", md["quotes"] == 2 and md["nestedQuote"], md["quotes"])
    check("列表能嵌套到第三层", md["deepList"] and "甲一一" in md["text"], md["text"][:200])
    check("任务框：[ ] 空着、[x] 打勾", md["boxes"] == 2 and md["checked"] == 1,
          (md["boxes"], md["checked"]))
    check("表格有表头与单元格，右对齐照分隔行",
          md["table"] and md["th"] == 2 and md["td"] == 2
          and md["rightAligned"] == "right", md)
    check("分隔线生效", md["hr"] == 1, md["hr"])
    check("代码块带语言、内容原样、有复制键",
          md["lang"] == "python" and "print('hi')" in md["codeText"] and md["copyBtn"],
          (md["lang"], md["codeText"]))
    check("每段对话下面都有复制键，时间还在它右边",
          md["msgCopy"] == 2 and md["times"] == 2, (md["msgCopy"], md["times"]))

    # 复制：点代码块的键与消息上的键，都应该真的抄到剪贴板
    try:
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception:
        pass
    page.click(".md-code [data-copy-code]")
    page.wait_for_timeout(400)
    code_copied = page.evaluate("""() => navigator.clipboard.readText()
        .then(t => t).catch(() => '')""")
    check("代码块的复制键抄到的是那段代码", "print('hi')" in (code_copied or ""),
          code_copied)
    page.click('#assistantLog [data-copy-msg="1"]')
    page.wait_for_timeout(400)
    msg_copied = page.evaluate("""() => navigator.clipboard.readText()
        .then(t => t).catch(() => '')""")
    check("消息的复制键抄到的是 Markdown 原文",
          "# 一级标题" in (msg_copied or ""), (msg_copied or "")[:60])
    check("复制键一直看得见，不是只在悬停时才出现",
          float(page.evaluate("""() => getComputedStyle(
              document.querySelector('#assistantLog .as-copy')).opacity""")) > 0.3)

    print("== 上下文用量 ==")
    # 上限调小一点，百分比才不至于永远是 0（环上那一段弧要看得见）
    call(owner_opener(), "/api/panel/assistant/config", {"context": 8000})
    page.evaluate("() => { location.hash = '#/overview'; }")
    page.wait_for_timeout(300)
    page.evaluate("() => { location.hash = '#/assistant'; }")
    page.wait_for_timeout(900)
    ring = page.evaluate("""() => {
        const wrap = document.getElementById('asContextWrap');
        const btn = document.getElementById('asContext');
        const name = document.getElementById('asSessionBar').getBoundingClientRect();
        const w = wrap.getBoundingClientRect();
        const fill = document.getElementById('asCtxFill');
        return {shown: !wrap.hidden,
                sameLine: Math.abs((name.top + name.height / 2) - (w.top + w.height / 2)) <= 3,
                rightOfName: w.left >= name.right - 1,
                dash: fill.getAttribute('stroke-dasharray') || '',
                aria: btn.getAttribute('aria-label') || ''};
    }""")
    check("会话名同一行、右边有上下文环",
          ring["shown"] and ring["sameLine"] and ring["rightOfName"], ring)
    # 会话名那颗按钮只包住自己的字：悬停高亮不该一路铺到环旁边
    box = page.evaluate("""() => {
        const btn = document.getElementById('asSessionBar').getBoundingClientRect();
        const wrap = document.getElementById('asContextWrap').getBoundingClientRect();
        const send = document.getElementById('assistantSend').getBoundingClientRect();
        const form = document.getElementById('assistantForm').getBoundingClientRect();
        return {btnW: Math.round(btn.width), btnRight: Math.round(btn.right),
                gap: Math.round(wrap.left - btn.right),
                ringRight: Math.round(wrap.right), sendRight: Math.round(send.right),
                formRight: Math.round(form.right), ringH: Math.round(wrap.height)};
    }""")
    check("会话名按钮只包住自己的字（悬停不会铺到环左边）",
          box["btnW"] < 160 and box["gap"] > 20, box)
    check("环的右边沿与发送键对齐，且不贴输入框边缘",
          abs(box["ringRight"] - box["sendRight"]) <= 2
          and box["formRight"] - box["ringRight"] >= 8, box)
    check("环做得比按钮小一点", box["ringH"] <= 22, box)
    check("环按百分比画了弧，说明也写了",
          float(ring["dash"].split(" ")[0] or 0) > 1 and ring["aria"] == "上下文用量",
          ring)
    page.hover("#asContext")
    page.wait_for_timeout(700)
    pop = page.evaluate("""() => {
        const p = document.getElementById('asContextPanel');
        return {open: !p.hidden, text: p.innerText,
                rows: Array.from(p.querySelectorAll('.as-ctx-row dt'))
                        .map(e => e.textContent.trim()),
                percent: document.getElementById('asCtxPercent').textContent.trim(),
                figures: document.getElementById('asCtxFigures').textContent.trim(),
                segs: p.querySelectorAll('.as-ctx-seg').length};
    }""")
    check("指上去出现明细：已用多少与上限",
          pop["open"] and "上下文已用" in pop["text"] and pop["percent"].endswith("%")
          and "/" in pop["figures"], pop)
    check("分项是 系统提示词 / 工具 / 对话消息",
          pop["rows"] == ["系统提示词", "工具", "对话消息"] and pop["segs"] >= 2, pop)
    check("明细里的色点真的有颜色",
          page.evaluate("""() => Array.from(document.querySelectorAll('.as-ctx-swatch'))
              .every(s => getComputedStyle(s).backgroundColor !== 'rgba(0, 0, 0, 0)')"""))
    page.mouse.move(6, 6)
    page.wait_for_timeout(400)
    check("移开就收起", page.locator("#asContextPanel").is_hidden())

    print("== 回到最新 ==")
    # 先造一段够长的对话，否则会话区根本滚不动
    long_msgs = []
    for i in range(12):
        long_msgs.append({"role": "user", "content": "问题 %d：把会话撑长。" % i,
                          "ts": now})
        long_msgs.append({"role": "assistant", "ts": now,
                          "content": "回答 %d：一段够长的说明，用来把会话区撑出滚动条。" % i})
    long_sid = (call(owner_opener(), "/api/panel/assistant/session/save",
                     {"id": 0, "title": "长对话", "messages": long_msgs}) or {}).get("id")
    page.evaluate("""(sid) => {
        localStorage.setItem('as:lastSession', String(sid));
        location.hash = '#/overview';
    }""", long_sid)
    page.wait_for_timeout(300)
    page.evaluate("() => { location.hash = '#/assistant'; }")
    page.wait_for_timeout(900)
    check("在底部时不显示",
          page.locator("#asToBottom").is_hidden())
    page.evaluate("() => { document.getElementById('assistantScroll').scrollTop = 30; }")
    page.wait_for_timeout(400)
    check("往上滚了才出现", page.locator("#asToBottom").is_visible())
    page.click("#asToBottom")
    page.wait_for_timeout(700)
    bottom = page.evaluate("""() => {
        const box = document.getElementById('assistantScroll');
        return {far: Math.round(box.scrollHeight - box.scrollTop - box.clientHeight),
                hidden: document.getElementById('asToBottom').hidden};
    }""")
    check("点一下回到底部并自己收起来", bottom["far"] <= 4 and bottom["hidden"], bottom)
    gap = page.evaluate("""() => {
        document.getElementById('assistantScroll').scrollTop = 30;
        const box = document.getElementById('assistantScroll');
        const btn = document.getElementById('asToBottom').getBoundingClientRect();
        const bar = box.offsetWidth - box.clientWidth;   // 滚动条占的宽度
        return {bar: bar, clear: Math.round(box.getBoundingClientRect().right
                                           - btn.right)};
    }""")
    page.wait_for_timeout(300)
    check("回到最新让开滚动条那一条",
          gap["clear"] >= gap["bar"] + 2, gap)
    page.click("#asToBottom")
    page.wait_for_timeout(600)

    print("== 占用很小的时候百分比不显示成 0 ==")
    call(owner_opener(), "/api/panel/assistant/config", {"context": 10000000})
    page.evaluate("() => { location.hash = '#/overview'; }")
    page.wait_for_timeout(300)
    page.evaluate("() => { location.hash = '#/assistant'; }")
    page.wait_for_timeout(900)
    page.hover("#asContext")
    page.wait_for_timeout(700)
    tiny = page.evaluate("() => document.getElementById('asCtxPercent').textContent.trim()")
    check("不到 1% 时给两位小数", re.fullmatch(r"\d+\.\d{2}%", tiny) is not None, tiny)
    call(owner_opener(), "/api/panel/assistant/config", {"context": 8000})
    page.mouse.move(6, 6)
    page.wait_for_timeout(300)

    print("== 手机上的可读宽度 ==")
    wide = page.evaluate("""() => getComputedStyle(
        document.querySelector('#assistantLog .as-bot .as-body')).maxWidth""")
    page.set_viewport_size({"width": 420, "height": 820})
    page.wait_for_timeout(700)
    mobile = page.evaluate("""() => {
        const body = document.querySelector('#assistantLog .as-bot .as-body');
        const scroll = document.getElementById('assistantScroll');
        const name = document.getElementById('asSessionName');
        if (!body || !scroll) return {};
        return {maxWidth: getComputedStyle(body).maxWidth,
                area: scroll.clientWidth,
                bubble: body.getBoundingClientRect().width,
                ellipsis: getComputedStyle(name).textOverflow,
                nameOverflow: name.scrollWidth > name.clientWidth + 1};
    }""")
    check("桌面上气泡最多占 78%", wide in ("78%", "78.0%"), wide)
    check("手机上气泡吃满可读宽度",
          mobile and mobile["maxWidth"] == "100%"
          and mobile["bubble"] >= mobile["area"] * 0.9,
          (mobile.get("maxWidth"), mobile.get("bubble"), mobile.get("area")))
    check("会话名给环让位：过长截断",
          mobile and mobile["ellipsis"] == "ellipsis", mobile)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(400)

    print("== 写动作的批准条与会话名位置 ==")
    layout = page.evaluate("""() => {
        const bar = document.getElementById('asApproval');
        const form = document.getElementById('assistantForm');
        const chip = document.getElementById('asSessionBar');
        const page = document.querySelector('[data-page="assistant"]');
        const head = page.querySelector('.page-head');
        const wasHidden = bar.hidden;
        bar.hidden = false;                       // 量尺寸时先摆出来，量完还原
        const r = bar.getBoundingClientRect();
        const c = chip.getBoundingClientRect();   // 会话条也要在批准条露面时量
        bar.hidden = wasHidden;
        const f = form.getBoundingClientRect();
        return {hidden: wasHidden, text: bar.textContent.trim(),
                above: r.height > 0 && r.bottom <= f.top + 1,
                inComposer: form.contains(bar),
                chipInHead: head ? head.contains(chip) : false,
                chipAboveForm: c.bottom <= f.top + 1,
                chipAboveBar: r.height > 0 && c.bottom <= r.top + 1,
                yes: document.getElementById('asApprovalYes').textContent.trim(),
                no: document.getElementById('asApprovalNo').textContent.trim(),
                more: !!document.getElementById('asApprovalMore'),
                moreHidden: document.getElementById('asApprovalMore')
                            ? document.getElementById('asApprovalMore').hidden : null,
                argsHidden: document.getElementById('asApprovalArgs')
                            ? document.getElementById('asApprovalArgs').hidden : null,
                argsInBar: bar.contains(document.getElementById('asApprovalArgs'))};
    }""")
    check("批准条平时是收着的", layout["hidden"] and "批准" in layout["text"], layout)
    check("批准条在输入框上方、不在框里面",
          layout["above"] and not layout["inComposer"], layout)
    check("两个键是拒绝与批准",
          layout["no"] == "拒绝" and layout["yes"] == "批准", layout)
    check("会话名回到输入框上方，并且排在批准条上面",
          layout["chipAboveForm"] and layout["chipAboveBar"]
          and not layout["chipInHead"], layout)
    # 参数详情：键在条里、面板默认收起；这次没有参数，所以连键都不摆
    check("批准条里有详情键与参数面板，平时都收着",
          layout["more"] and layout["argsInBar"] and layout["argsHidden"]
          and layout["moreHidden"], layout)
    print("== 模型设置弹窗 ==")
    page.click("#asSettings")
    page.wait_for_timeout(350)
    check("设置是弹窗，不是内嵌卡片",
          page.locator("#overlay").is_visible()
          and "模型设置" in page.inner_text("#overlayTitle"))
    check("接口地址与模型已经填好默认值",
          page.input_value("#asBase").startswith("http")
          and page.input_value("#asModel") != "",
          (page.input_value("#asBase"), page.input_value("#asModel")))
    check("密钥框是密码型", page.get_attribute("#asKey", "type") == "password")
    adv = page.evaluate("""() => {
        const head = document.getElementById('asAdvToggle');
        const body = document.getElementById('asAdvBody');
        const ctx = document.getElementById('asCtx');
        return {there: !!head, hidden: body.hidden,
                inside: body.contains(ctx),
                label: head.textContent.trim(),
                placeholder: ctx.getAttribute('placeholder') || '',
                hint: !!body.querySelector('.hint')};
    }""")
    check("上下文上限收在「高级设置」里，点开才露出来",
          adv["there"] and adv["hidden"] and adv["inside"]
          and adv["label"] == "高级设置", adv)
    check("这个参数没有多余的解释文字", not adv["hint"], adv)
    page.click("#asAdvToggle")
    page.wait_for_timeout(300)
    check("点一下展开，再点收起",
          page.locator("#asAdvBody").is_visible()
          and page.get_attribute("#asAdvToggle", "aria-expanded") == "true")
    page.click("#asAdvToggle")
    page.wait_for_timeout(300)
    check("收起后又不占地方", page.locator("#asAdvBody").is_hidden())

    print("== 开关：和 micula 的一样是 40×20 ==")
    sw = switch_metrics(page)
    check("轨道 40×20", abs(sw["w"] - 40) < 1 and abs(sw["h"] - 20) < 1, sw)
    check("圆点直径 12", abs(sw["knob"] - 12) < 1, sw)
    check("文字与轨道一条中线上", abs(sw["textMid"] - sw["trackMid"]) <= 2, sw)
    was_on = knob_offset(page)
    page.click("#asResumeRow .track")
    page.wait_for_timeout(320)
    turned_off = knob_offset(page)
    check("关掉后圆点回到左边", abs(was_on - 20) < 1 and abs(turned_off) < 1,
          (was_on, turned_off))
    page.click("#asResumeRow .track")      # 复位，后面还要用这颗开关
    page.wait_for_timeout(320)
    check("再点一下走回右边", abs(knob_offset(page) - 20) < 1, knob_offset(page))

    page.fill("#asKey", "sk-not-a-real-key")
    page.click("#asSave")
    page.wait_for_timeout(800)
    check("保存后弹窗自己关掉", page.locator("#overlay").is_hidden())
    check("副标题改成当前模型", "当前模型" in page.inner_text("#assistantSub"),
          page.inner_text("#assistantSub"))
    page.click("#asSettings")
    page.wait_for_timeout(350)
    masked = page.evaluate("() => document.getElementById('asKey').value")
    check("再打开时密钥位置是一串星号，不是真东西",
          masked and "sk-not-a-real-key" not in masked and set(masked) == {"•"},
          masked[:12])
    page.click("#asSave")
    page.wait_for_timeout(700)
    page.click("#asSettings")
    page.wait_for_timeout(300)
    again = page.evaluate("() => document.getElementById('asKey').value")
    check("再保存一次也不会把密钥弄丢", again and set(again) == {"•"}, again[:12])
    # 清除密钥是危险操作：红键 + 先问一句
    check("清除密钥是红色危险键",
          page.evaluate("""() => document.getElementById('asClearKey')
              .classList.contains('danger')"""))
    page.click("#asClearKey")
    page.wait_for_timeout(400)
    check("点它先问一句",
          "清除密钥" in page.inner_text("#overlayTitle")
          and "不可用" in page.inner_text("#overlayBody"),
          (page.inner_text("#overlayTitle"), page.inner_text("#overlayBody")[:60]))
    page.click("#confirmGo")
    page.wait_for_timeout(700)
    check("清除密钥后回到未配置",
          "尚未配置模型" in page.inner_text("#assistantSub"),
          page.inner_text("#assistantSub"))
    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)

    print("== 全屏编辑（手机上才出现，桌面验行为）==")
    check("桌面上不给全屏按钮", not page.locator("#asExpand").is_visible())
    page.fill("#assistantText", "先把问题写在这里")
    page.evaluate("() => { document.getElementById('asExpand').style.display = 'grid'; }")
    page.click("#asExpand")
    page.wait_for_timeout(400)
    check("打开后带上了现有内容",
          page.input_value("#asFull") == "先把问题写在这里",
          page.input_value("#asFull"))
    page.fill("#asFull", "写到全屏里的一句话")
    page.wait_for_timeout(200)
    check("在全屏里改的内容同步回输入框",
          page.input_value("#assistantText") == "写到全屏里的一句话",
          page.input_value("#assistantText"))
    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)

    print("== 回答里的 Markdown ==")
    html = page.evaluate("""() => PX.md("**重点**：`pid 1`\\n- 甲\\n- 乙")""")
    check("列表与行内代码都认", "<ul>" in html and "<code>pid 1</code>" in html,
          html[:80])
    check("强调也认", "<strong>重点</strong>" in html, html[:80])
    check("原样的 HTML 被转义",
          "<img" not in page.evaluate("() => PX.md('<img src=x>')"))
    check("只放行 http(s) 链接",
          'href="https://' in page.evaluate("() => PX.md('[a](https://e.com)')")
          and "href" not in page.evaluate("() => PX.md('[a](javascript:alert(1))')"))

    # 助手实际会写的那一行：中文标点里夹着各种行内格式
    line_html = page.evaluate(r"""() => PX.md(
        "普通：**粗体**、*斜体*、***粗斜体***、~~删除~~、`代码`、" +
        "<kbd>Ctrl</kbd>+<kbd>S</kbd>、转义 \\*不该斜\\*、上标 x^2^ 与下标 H~2~O、" +
        "脚注[^1]、自动链接 <https://example.com>。")""")
    check("斜体在顿号后面也认",
          "<em>斜体</em>" in line_html and "*斜体*" not in line_html, line_html[:160])
    check("粗斜体两头都上",
          "<em><strong>粗斜体</strong></em>" in line_html
          or "<strong><em>粗斜体</em></strong>" in line_html, line_html[:200])
    check("插入符与波浪线写成上下标",
          "<sup>2</sup>" in line_html and "<sub>2</sub>" in line_html
          and "~~删除~~" not in line_html, line_html[:200])
    check("转义过的星号不变斜体，也不留反斜杠",
          "*不该斜*" in line_html and "\\*" not in line_html, line_html[:200])
    check("没有语言标注的代码块写 plain text",
          "plain text" in page.evaluate("() => PX.md('```\\nraw\\n```')"))

    print("== 回答里的链接 ==")
    # 存一段带链接的回答，看真渲染：站内文档开新标签页并带上那一节，站外照常，清单外断链
    now = int(time.time())
    link_sid = (call(owner_opener(), "/api/panel/assistant/session/save",
                     {"id": 0, "title": "链接测试",
                      "messages": [
                          {"role": "user", "content": "看文档", "ts": now},
                          {"role": "assistant", "ts": now,
                           "content": "见 [文档查询](usage/assistant.md#文档查询)、"
                                      "[仓库](https://github.com/Tianming-Wu/CAEventManager)、"
                                      "[别处](https://example.com)、[断链](nope.md)"}]})
                or {}).get("id")
    check("存下一段带链接的回答", bool(link_sid), link_sid)
    if link_sid:
        page.evaluate("""(sid) => {
            localStorage.setItem('as:lastSession', String(sid));
            location.hash = '#/overview';
        }""", link_sid)
        page.wait_for_timeout(300)
        page.evaluate("() => { location.hash = '#/assistant'; }")
        page.wait_for_selector("#assistantLog a", timeout=8000)
        page.wait_for_timeout(300)
        got = page.evaluate("""() => {
            const log = document.getElementById('assistantLog');
            const a = Array.from(log.querySelectorAll('a'));
            const broken = log.querySelector('.md-broken');
            return {hrefs: a.map(x => x.getAttribute('href')),
                    targets: a.map(x => x.getAttribute('target') || ''),
                    icons: a.filter(x => x.querySelector('.md-doc-ico')).length,
                    ext: a.filter(x => x.querySelector('.md-ext-ico')).length,
                    titles: a.map(x => x.getAttribute('title') || ''),
                    broken: broken ? broken.innerText.trim() : '',
                    text: log.innerText};
        }""")
        check("站内文档链接在新标签页打开，还带着那一节",
              bool(got["hrefs"]) and got["hrefs"][0].endswith(
                  "/docs#/usage/assistant.md~文档查询")
              and got["targets"][0] == "_blank", got)
        check("文档链接带文件图标",
              got["icons"] == 1, got)
        check("白名单里的站外链接可点，带开新窗口的图标与 title",
              "https://github.com/Tianming-Wu/CAEventManager" in got["hrefs"]
              and got["ext"] == 1
              and any("_blank" == t for t in got["targets"])
              and "在新标签页打开" in got["titles"], got)
        check("白名单外的地址只当文字",
              "https://example.com" not in got["hrefs"] and "别处" in got["text"], got)
        check("清单外的写成断链", got["broken"] == "断链（链接已断）", got)
        check("读回来的对话是可以导出的",
              not page.evaluate("() => document.getElementById('asExport').disabled"))


CRUMB_SAMPLER = r"""
window.__crumb = null;
window.__startCrumb = function (name) {
  window.__crumb = null;
  const out = [];
  const t0 = performance.now();
  function tick(now) {
    const page = document.querySelector('.page[data-page="' + name + '"]');
    const el = page && page.querySelector('.head-title .crumb-title');
    const sub = page && page.querySelector('.page-head .page-sub');
    const crumb = page && page.querySelector('.head-title .crumb-title .crumb');
    const nm = page && (page.querySelector('#rosterTitle, #signupTitle') || el);
    let row = {t: Math.round(now - t0), left: null, sub: null, name: null,
               crumb: null, shown: false};
    if (el && page && !page.hidden) {
      const box = el.getBoundingClientRect();
      row = {t: Math.round(now - t0),
             left: Math.round(box.left * 10) / 10,
             w: Math.round(box.width * 10) / 10,
             sub: sub ? Math.round(sub.getBoundingClientRect().left * 10) / 10 : null,
             name: nm ? Math.round(nm.getBoundingClientRect().left * 10) / 10 : null,
             crumb: crumb ? getComputedStyle(crumb).display !== 'none' : null,
             shown: true};
    }
    out.push(row);
    if (now - t0 < 900) requestAnimationFrame(tick);
    else window.__crumb = out;
  }
  requestAnimationFrame(tick);
};
"""


def crumb_motion(browser):
    """标题栏让位不许"抽"：逐帧量它横向走了多少，单帧不许超过 6px。

    用户报的现象：名单进详情再退出很顺，报名进编辑再退出"抽一下"。
    根子在动画撞上一屏新内容的排版——所以这里逐帧看，而不是看起止位置。
    """
    print("== 标题栏让位：进出详情都不许跳 ==")
    owner = owner_opener()
    rid = call(owner, "/api/panel/roster/import", {
        "name": "标题栏动画名单", "text": "学号,姓名\n20250011,甲一\n20250012,乙二"})["id"]
    sid = call(owner, "/api/panel/signup/create", {
        "title": "标题栏动画报名", "roster_id": rid})["id"]
    ctx, page = new_page(browser, 1440, 900)
    try:
        ui_login(page)

        def run(label, start, target, action, want_dir):
            if start:
                page.goto(BASE + "/panel" + start)
                page.wait_for_timeout(700)
            page.evaluate(CRUMB_SAMPLER)
            page.evaluate("(n) => window.__startCrumb(n)", target)
            if action == "open":
                page.click(target == "roster"
                          and '[data-open-roster="%d"]' % rid
                          or '[data-open-signup="%d"]' % sid)
            else:
                page.click('.page[data-page="%s"] .crumb-back'
                           % ("roster" if target == "rosters" else "signup"))
            page.wait_for_timeout(1000)
            rows = [s for s in page.evaluate("() => window.__crumb || []") if s["shown"]]
            if len(rows) < 10:
                check("%s：采到了这一段的移动" % label, False, "%d 帧" % len(rows))
                return
            steps = [round(b["left"] - a["left"], 1) for a, b in zip(rows, rows[1:])]
            travel = round(max(r["left"] for r in rows) - min(r["left"] for r in rows), 1)
            back = [s for s in steps if s * want_dir < 0]
            check("%s：标题确实挪了 36px" % label, travel >= 30, travel)
            # 一帧最多走全程的六成：掉帧（机器忙）会把几帧的路并成一帧，那不是产品的问题；
            # 真正的"抽"是一下跨过一大段或者跳回原处。判据因此看两件事：
            # 位移大**而且**这一帧的时间间隔正常——掉帧的那一步时间也会跟着长。
            spans = sorted(b["t"] - a["t"] for a, b in zip(rows, rows[1:]))
            typical = spans[len(spans) // 2] if spans else 16
            jumps = [(b["t"], round(b["left"] - a["left"], 1), b["t"] - a["t"])
                     for a, b in zip(rows, rows[1:])
                     if abs(b["left"] - a["left"]) > travel * 0.6
                     and (b["t"] - a["t"]) <= typical * 2]
            check("%s：没有一下子跨过一大段" % label, not jumps, jumps[:4])
            check("%s：一路往一个方向走" % label,
                  not [s for s in back if abs(s) >= 2],
                  "回头的帧：%s" % [s for s in back if abs(s) >= 2])
            # 副标题在同一行：它得跟着标题一起挪，否则两者会叠上或者中间空一块
            subs = [r["sub"] for r in rows if r["sub"] is not None]
            sub_travel = round(max(subs) - min(subs), 1) if subs else 0
            check("%s：副标题跟着一起挪" % label, sub_travel >= 30, sub_travel)
            # 动画开始之后，两者每一帧的位移都要一致（≤1.5px）。
            # 标题的宽度变了说明这一帧换字了（报名/名单的名字是数据到了才填的），跳过那一帧。
            pairs = [(r["left"], r["sub"], r["w"]) for r in rows if r["sub"] is not None]
            worst = 0.0
            started = False
            for (l0, s0, w0), (l1, s1, w1) in zip(pairs, pairs[1:]):
                if not started:
                    if abs(l1 - pairs[0][0]) < 0.5:
                        continue
                    started = True
                    continue
                if abs(w1 - w0) > 0.5:
                    continue
                worst = max(worst, abs((l1 - l0) - (s1 - s0)))
            check("%s：两者之间不留缝也不叠字" % label, worst <= 1.5,
                  "单帧位移差最大 %.1fpx" % worst)
            # 标题后半截（这一页的名字）必须和标题本身一起动，差不超过 1.5px：
            # 面包屑要是中途才长出来，名字会独自挪一大截，而标题盒子没动。
            name_jumps = []
            for a, b in zip(rows, rows[1:]):
                if a["name"] is None or b["name"] is None:
                    continue
                if abs((b["name"] - a["name"]) - (b["left"] - a["left"])) > 1.5:
                    name_jumps.append((b["t"], round(b["name"] - a["name"], 1),
                                       round(b["left"] - a["left"], 1)))
            check("%s：标题文字不跳" % label, not name_jumps, name_jumps[:4])
            # 进详情时，面包屑应当在第一帧就已经在那儿，而不是中途长出来
            marks = [r["crumb"] for r in rows if r["crumb"] is not None]
            if want_dir > 0 and marks:
                check("%s：面包屑一上来就在" % label, all(marks),
                      "中途缺了 %d 帧" % marks.count(False))

        run("名单 进", "#/rosters", "roster", "open", 1)
        run("名单 出", None, "rosters", "back", -1)
        run("报名 进", "#/signups", "signup", "open", 1)
        run("报名 出", None, "signups", "back", -1)
        check("这一段没有 javascript 错误", not page._errors,
              "; ".join(page._errors)[:200])
    finally:
        ctx.close()
        # 这两份是为了测动画造的：收干净，别让后面的用例以为列表本来就非空
        # 收尾：平台不给直接删，先归档再由超管彻底删除
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        drop_signup(owner, sid)
        drop_roster(owner, rid)


def switch_metrics(page):
    return page.evaluate("""() => {
        const label = document.getElementById('asResumeRow');
        const t = label.querySelector('.track');
        const r = t.getBoundingClientRect();
        const k = getComputedStyle(t, '::after');
        const txt = label.querySelector('.switch-text').getBoundingClientRect();
        const cs = getComputedStyle(label);
        return {w: +r.width.toFixed(1), h: +r.height.toFixed(1),
                knob: parseFloat(k.width), round: parseFloat(k.borderTopLeftRadius),
                textMid: (txt.top + txt.bottom) / 2,
                trackMid: (r.top + r.bottom) / 2, align: cs.alignItems};
    }""")


def knob_offset(page):
    """The knob's horizontal travel, in px (the hover scale does not touch it)."""
    return page.evaluate("""() => {
        const m = getComputedStyle(
            document.querySelector('#asResumeRow .track'), '::after').transform;
        if (!m || m === 'none') return 0;
        return Math.round(parseFloat(m.split(',')[4] || '0'));
    }""")


def assistant_drawer(browser):
    """会话列表是左侧抽屉：点开、选中、重命名、删除，换一页不会把它留在屏幕上。"""
    print("== 会话抽屉 ==")
    owner = owner_opener()
    made = call(owner, "/api/panel/assistant/session/save", {
        "id": 0, "title": "抽屉里的会话",
        "messages": [{"role": "user", "content": "抽屉里的一问"},
                     {"role": "assistant", "content": "抽屉里的一答"}]})
    sid = made["id"]
    ctx, page = new_page(browser, 1440, 900)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/assistant")
        page.wait_for_timeout(900)
        page.click("#asSessionBar")
        page.wait_for_timeout(400)
        opened = page.evaluate("""() => {
            const shell = document.getElementById('shell');
            const d = document.getElementById('asDrawer').getBoundingClientRect();
            const s = document.getElementById('scrim');
            return {open: shell.classList.contains('as-drawer-open'),
                    left: Math.round(d.left), width: Math.round(d.width),
                    top: Math.round(d.top), visible: d.width > 0,
                    scrim: !s.hidden && getComputedStyle(s).display !== 'none',
                    rows: document.querySelectorAll('#asDrawerBody .as-srow').length};
        }""")
        check("会话列表从左边拉出来", opened["open"] and opened["visible"], opened)
        check("贴着左上角、和侧栏一样窄", opened["left"] == 0 and opened["width"] <= 322,
              opened)
        check("外面压着一层遮罩", opened["scrim"], opened)
        check("存档的会话都在里面", opened["rows"] >= 1, opened)
        check("列表里认得出是哪一段",
              page.locator('#asDrawerBody .as-srow[data-sid="%d"]' % sid).count() == 1)

        print("== 选中一段会话 ==")
        page.click('#asDrawerBody .as-srow[data-sid="%d"] .as-sopen' % sid)
        page.wait_for_timeout(700)
        picked = page.evaluate("""() => ({
            open: document.getElementById('shell').classList.contains('as-drawer-open'),
            hash: location.hash,
            log: document.getElementById('assistantLog').innerText,
            name: document.getElementById('asSessionName').textContent.trim()})""")
        check("点一下就收回抽屉", not picked["open"], picked)
        check("落在这一段会话的地址上", picked["hash"] == "#/assistant/%d" % sid, picked)
        check("对话内容回来了", "抽屉里的一问" in picked["log"], picked["log"][:80])
        check("会话条上是它的名字", picked["name"] == "抽屉里的会话", picked["name"])

        print("== 重命名、再收起来 ==")
        page.click("#asSessionBar")
        page.wait_for_timeout(400)
        page.click('#asDrawerBody .as-srow[data-sid="%d"] [data-as-rename]' % sid)
        page.wait_for_timeout(350)
        check("重命名还是弹窗", page.locator("#overlay").is_visible())
        page.fill("#asRename", "改过的名字")
        page.click("#asRenameGo")
        page.wait_for_timeout(600)
        check("抽屉里立刻是新名字",
              page.locator('#asDrawerBody .as-srow[data-sid="%d"]' % sid).inner_text()
              .startswith("改过的名字"),
              page.locator('#asDrawerBody .as-srow[data-sid="%d"]' % sid).inner_text())
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        shut = page.evaluate("""() => {
            const shell = document.getElementById('shell');
            const d = document.getElementById('asDrawer').getBoundingClientRect();
            return {open: shell.classList.contains('as-drawer-open'),
                    onScreen: d.right > 1};
        }""")
        check("Esc 一按就收回去，而且真的移出屏幕", not shut["open"] and not shut["onScreen"],
              shut)

        print("== 换一页它不会留在屏幕上 ==")
        page.click("#asSessionBar")
        page.wait_for_timeout(400)
        page.evaluate("() => { location.hash = '#/overview'; }")
        page.wait_for_timeout(500)
        gone = page.evaluate("""() => ({
            open: document.getElementById('shell').classList.contains('as-drawer-open'),
            overview: !document.querySelector('.page[data-page=overview]').hidden})""")
        check("回到总览时抽屉已经收了", not gone["open"] and gone["overview"], gone)

        print("== 删除 ==")
        page.goto(BASE + "/panel#/assistant")
        page.wait_for_timeout(800)
        page.click("#asSessionBar")
        page.wait_for_timeout(400)
        page.click('#asDrawerBody .as-srow[data-sid="%d"] [data-as-del]' % sid)
        page.wait_for_timeout(350)
        check("删除先问一句", page.locator("#overlay").is_visible())
        body_text = page.inner_text("#overlayBody")
        row_title = page.inner_text('#asDrawerBody .as-srow[data-sid="%d"] .as-sopen' % sid)
        check("删除的那一句点名是哪段会话，并说明无法恢复",
              "确定要删除「" in body_text and "吗？" in body_text
              and "无法恢复" in body_text
              and row_title.split("\n")[0].strip() in body_text,
              (body_text, row_title[:40]))
        page.click("#asDelGo")
        page.wait_for_timeout(700)
        check("删掉的会话不在列表里了",
              page.locator('#asDrawerBody .as-srow[data-sid="%d"]' % sid).count() == 0)
        check("删完抽屉还开着", page.evaluate(
            "() => document.getElementById('shell').classList.contains('as-drawer-open')"))
        check("这一段自己没有 javascript 错误", not page._errors,
              "; ".join(page._errors)[:200])
    finally:
        ctx.close()


def title_row(page):
    print("== page titles sit at the same height everywhere ==")
    tops = {}
    # 运维助手是另一套骨架（固定标题 + 常驻输入框），标题更得跟别页对齐
    for route in ("overview", "events", "users", "all-events", "server", "account",
                  "assistant"):
        page.goto(BASE + "/panel#/" + route)
        page.wait_for_timeout(260)
        tops[route] = page.evaluate(
            "() => Math.round(document.querySelector('.page:not([hidden]) h1')"
            ".getBoundingClientRect().top)")
    heights = sorted(set(tops.values()))
    check("one title height for pages with and without a button",
          len(heights) == 1, tops)

    print("== 标题栏左上角的标志 ==")
    # 票券加对勾，两处（面板标题栏、使用文档页侧栏）共用同一个；不再是纯色方块占位
    mark = page.evaluate("""() => {
        const box = document.querySelector('.brand-mark');
        const svg = box && box.querySelector('svg');
        if (!svg) return null;
        const b = svg.getBBox();
        const cs = getComputedStyle(box);
        return {w: Math.round(box.getBoundingClientRect().width),
                center: [Math.round((b.x + b.width / 2) * 100) / 100,
                         Math.round((b.y + b.height / 2) * 100) / 100],
                bg: cs.backgroundColor,
                paths: svg.querySelectorAll('path').length};
    }""")
    check("标志是真图形，不再是占位方块",
          mark is not None and mark["paths"] >= 2
          and mark["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), mark)
    check("标志占满 18px 格子，重心落在正中",
          mark is not None and mark["w"] == 18
          and abs(mark["center"][0] - 10) <= 0.9
          and abs(mark["center"][1] - 10) <= 0.9, mark)
    check("标志的颜色跟着主题走", page.evaluate("""() => {
        const box = document.querySelector('.brand-mark');
        const root = document.documentElement;
        const was = root.getAttribute('data-theme');
        root.setAttribute('data-theme', 'light');
        const light = getComputedStyle(box).color;
        root.setAttribute('data-theme', 'dark');
        const dark = getComputedStyle(box).color;
        if (was === null) root.removeAttribute('data-theme');
        else root.setAttribute('data-theme', was);
        return light !== dark;
    }"""))

    print("== 标签页图标 ==")
    # 矢量优先、位图兜底、iOS 触屏各一份，都是从同一个标志渲染出来的
    icons = page.evaluate("""() => Array.from(document.querySelectorAll(
        'link[rel="icon"], link[rel="apple-touch-icon"]'))
        .map(l => l.getAttribute('href') || '')""")
    check("面板页带着矢量图标与两份位图",
          any(h.endswith("favicon.svg") for h in icons)
          and any(h.endswith("favicon.png") for h in icons)
          and any(h.endswith("apple-touch-icon.png") for h in icons), icons)
    check("图标不再是空占位（data:）", not any(h.startswith("data:") for h in icons), icons)
    fetched = page.request.get(BASE + "/assets/img/favicon.svg")
    check("矢量图标文件拿得到",
          fetched.status == 200 and "svg" in (fetched.headers.get("content-type") or ""),
          (fetched.status, fetched.headers.get("content-type")))
    login_icons = page.request.get(BASE + "/login")
    check("登录页也带同一套图标",
          "favicon.svg" in login_icons.text() and "apple-touch-icon" in login_icons.text(),
          login_icons.status)


def group_page(page):
    print("== the collapsible group ==")
    page.goto(BASE + "/panel#/overview")
    page.wait_for_timeout(220)
    sub = page.locator("#nav-approvals")
    was_open = sub.is_visible()
    page.click('[data-toggle="approvals"]')
    page.wait_for_timeout(260)
    check("the group toggles", sub.is_visible() != was_open)
    if not sub.is_visible():
        page.click('[data-toggle="approvals"]')
        page.wait_for_timeout(260)
    check("its children are visible",
          page.locator('[data-nav="approval-users"]').is_visible() and
          page.locator('[data-nav="approval-events"]').is_visible())
    page.click('[data-nav="approval-users"]')
    page.wait_for_timeout(320)
    check("a child navigates",
          page.locator('.page[data-page="approval-users"]').is_visible())
    check("the parent stays marked", page.evaluate(
        """() => document.querySelector('.nav-group[data-group="approvals"]')
                 .classList.contains('has-current')"""))


def write_pack_zip(path):
    """一份最小的屏幕包：入口页 + 一张样式表。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("screen.html", "<html><body>ui probe</body></html>")
        zf.writestr("pack.css", "body{margin:0}")
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())


def screens_page(page):
    print("== 展示屏幕 ==")
    page.goto(BASE + "/panel#/screens")
    page.wait_for_timeout(900)
    check("the page lists the built-in pack",
          page.locator('.pack-card').count() >= 1,
          page.locator('.pack-card').count())
    check("随发布走的那一份标着来源",
          "随发布" in page.inner_text("#screenPackList"),
          page.inner_text("#screenPackList"))
    check("内置的那一份删不掉",
          page.locator('.pack-card [data-pack-del="classic"]').count() == 0)
    check("站长看得到导入入口", page.locator("#screenPackAdd").is_visible())
    check("导入说明写出接受的文件类型",
          ".css" in page.inner_text("#screenPackRulesList"),
          page.inner_text("#screenPackRulesList"))
    check("导入说明写出入口页",
          "screen.html" in page.inner_text("#screenPackRulesList"))

    probe = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tmp", "pack-probe.zip"))
    os.makedirs(os.path.dirname(probe), exist_ok=True)
    write_pack_zip(probe)
    page.set_input_files("#screenPackFile", probe)
    page.wait_for_function(
        """() => (document.getElementById('screenPackList').textContent || '')
                 .indexOf('pack-probe') >= 0""", timeout=20000)
    page.wait_for_timeout(200)
    check("导入的包出现在清单里",
          "pack-probe" in page.inner_text("#screenPackList"))
    check("导入的那一份标为已导入",
          "已导入" in page.inner_text("#screenPackList"))
    check("导入的那一份可以删除",
          page.locator('[data-pack-del="pack-probe"]').count() == 1)

    page.click('[data-pack-del="pack-probe"]')
    page.wait_for_timeout(300)
    check("删除前先确认", page.locator("#overlay").is_visible())
    check("确认框说明了影响",
          "自带的展示页" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody"))
    page.click("#packDelGo")
    page.wait_for_function(
        """() => (document.getElementById('screenPackList').textContent || '')
                 .indexOf('pack-probe') < 0""", timeout=20000)
    check("删掉后从清单里消失",
          "pack-probe" not in page.inner_text("#screenPackList"))


def nav_keys_page(page):
    print("== 侧栏方向键 ==")
    page.goto(BASE + "/panel#/overview")
    page.wait_for_timeout(600)
    stops = page.evaluate("""() => [...document.querySelectorAll('#sidebar [data-nav], #sidebar [data-toggle]')]
        .filter(el => el.tabIndex === 0).length""")
    check("the whole sidebar is one tab stop", stops == 1, stops)
    page.focus('#sidebar [data-nav][tabindex="0"]')
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(120)
    first = page.evaluate(
        "() => document.activeElement.getAttribute('data-nav') || "
        "document.activeElement.getAttribute('data-toggle')")
    check("the down arrow moves to the next item", first == "assistant", first)
    page.keyboard.press("End")
    page.wait_for_timeout(120)
    last = page.evaluate("() => document.activeElement.getAttribute('data-nav')")
    check("End goes to the last entry", last == "account", last)


def head_align(page):
    """有按钮与没按钮的页面，标题区一样高，正文第一个元素从同一个高度开始。"""
    print("== 标题区高度：按钮不该把正文按下去 ==")
    routes = ["#/overview", "#/rosters", "#/write-approvals", "#/users",
              "#/all-events", "#/server", "#/audit", "#/account"]
    seen = {}
    for route in routes:
        page.goto(BASE + "/panel" + route, wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        got = page.evaluate("""() => {
            const sec = document.querySelector('section.page:not([hidden])');
            if (!sec) return null;
            const head = sec.querySelector('.page-head');
            let first = null;
            for (const el of sec.children) {
                if (el === head || el.hidden) continue;
                if (el.getBoundingClientRect().height < 1) continue;
                first = el;
                break;
            }
            return {head: head ? Math.round(head.getBoundingClientRect().height) : -1,
                    body: first ? Math.round(first.getBoundingClientRect().top) : -1,
                    acts: !!sec.querySelector('.page-head .page-actions')};
        }""")
        if got:
            seen[route] = got
    check("量到了几页", len(seen) == len(routes), sorted(seen))
    check("两种页面都有",
          sum(1 for v in seen.values() if v["acts"]) >= 2
          and sum(1 for v in seen.values() if not v["acts"]) >= 2,
          {k: v["acts"] for k, v in seen.items()})
    check("标题区高度处处一样", len({v["head"] for v in seen.values()}) == 1, seen)
    check("正文第一个元素从同一个高度开始",
          len({v["body"] for v in seen.values()}) == 1, seen)


def approval_queue_text(page):
    """注册申请那一页的两张表：读完了就不能还挂着"正在读取…"。"""
    print("== 注册申请：空表也要说清楚 ==")
    page.goto(BASE + "/panel#/approval-users")
    page.wait_for_timeout(900)
    got = page.evaluate("""() => ({
        pending: document.querySelector('#pendingTable tbody').innerText.trim(),
        decided: document.querySelector('#decidedTable tbody').innerText.trim(),
        sub: document.getElementById('approvalUsersSub').innerText.trim(),
    })""")
    check("待审核那张表不会一直显示正在读取",
          "正在读取" not in got["pending"], got["pending"][:80])
    check("最近处理那张表也不会", "正在读取" not in got["decided"],
          got["decided"][:80])
    check("空表说的是没有待处理的申请",
          got["pending"] != "" and ("没有待处理" in got["pending"]
                                    or "通过" in got["pending"]),
          got["pending"][:80])
    check("副标题说得出现在有几条", got["sub"] != "", got["sub"])


def import_page(browser):
    """用户管理 › 导入账号：读表、预览即编辑、下载、导入。

    这一段自己开一个上下文：它要点弹窗、要下载，和别段共用一个页面时，
    前一段留下的弹窗会压在上面（点什么都落在遮罩上）。
    """
    ctx, page = new_page(browser, 1280, 900)
    # 无头环境弹不出「另存为」：拿掉 picker，走"下载回落"这条真实分支
    ctx.add_init_script("delete window.showSaveFilePicker")
    try:
        ui_login(page)
        run_import_page(page)
    finally:
        ctx.close()


def run_import_page(page):
    print("== 导入账号：按钮在左边，进子页 ==")
    page.goto(BASE + "/panel#/users")
    page.wait_for_timeout(600)
    check("用户管理右上角有导入账号",
          page.locator("#importAccountBtn").is_visible(), None)
    where = page.evaluate("""() => {
        const pick = document.getElementById('importAccountBtn');
        const make = document.getElementById('newAccountBtn');
        if (!pick || !make) return null;
        return {pick: Math.round(pick.getBoundingClientRect().left),
                make: Math.round(make.getBoundingClientRect().left)};
    }""")
    check("导入账号在新建账号左边", bool(where) and where["pick"] < where["make"], where)

    page.click("#importAccountBtn")
    page.wait_for_timeout(700)
    check("进的是子页", page.url.endswith("#/import-accounts"), page.url)
    check("有返回键", page.locator("#importBack").is_visible())
    check("空表说得出下一步",
          "还没有内容" in page.inner_text("#importTable tbody"),
          page.inner_text("#importTable tbody")[:60])
    check("空表时导入键是灰的", page.locator("#importGo").is_disabled())

    print("== 手动录入加一行 ==")
    page.click("#importManualBtn")
    page.wait_for_function(
        "() => document.querySelectorAll('#importTable tbody tr[data-import-row]').length === 1",
        timeout=20000)
    page.wait_for_timeout(700)
    one = page.evaluate("""() => {
        const tr = document.querySelector('#importTable tbody tr[data-import-row]');
        const dot = tr && tr.querySelector('.warn-dot');
        const table = document.getElementById('importTable');
        return {name: tr && tr.querySelector('[data-import-field=name]').value,
                user: tr && tr.querySelector('[data-import-field=username]').value,
                pwd: tr && tr.querySelector('[data-import-field=password]').value,
                bad: !!(tr && tr.classList.contains('row-bad')),
                dot: !!dot,
                tip: dot ? (dot.getAttribute('data-tip') || '') : '',
                shown: table ? !table.classList.contains('warn-off') : false};
    }""")
    check("空行留得住（不是点一下又缩回去）", bool(one) and one["name"] == "", one)
    check("空行不替人补内容：登录名与密码都还是空的",
          one["user"] == "" and one["pwd"] == "", one)
    check("有问题的那一行标红", one["bad"], one)
    check("警示列露出来，原因挂在悬停说明上",
          one["shown"] and one["dot"] and "显示名" in one["tip"], one)
    page.fill("#importTable tbody tr:first-child [data-import-field=name]", "手填甲")
    page.wait_for_timeout(1500)
    focused = page.evaluate("""() => {
        const el = document.activeElement;
        return el && el.getAttribute ? (el.getAttribute('data-import-field') || el.id) : '';
    }""")
    check("打字期间焦点不会被重绘带走", focused == "name", focused)
    still = page.evaluate("""() => {
        const tr = document.querySelector('#importTable tbody tr[data-import-row]');
        return {user: tr.querySelector('[data-import-field=username]').value,
                pwd: tr.querySelector('[data-import-field=password]').value,
                bad: tr.classList.contains('row-bad'),
                fill: document.getElementById('importFillBtn').disabled};
    }""")
    check("填了显示名也不会自己长出登录名与密码",
          still["user"] == "" and still["pwd"] == "", still)
    check("还缺东西时这一行不能导入，自动填充键亮着",
          still["bad"] and still["fill"] is False, still)

    print("== 自动填充：只有点它才补 ==")
    page.evaluate("() => document.getElementById('importFillBtn').click()")
    try:
        page.wait_for_function(
            "() => document.getElementById('overlayTitle').innerText.trim() === '自动填充'",
            timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(200)
    dialog = page.evaluate("""() => ({
        title: document.getElementById('overlayTitle').innerText.trim(),
        rules: Array.from(document.querySelectorAll('#afRule option')).map(o => o.value),
        help: !!document.querySelector('#overlayBody .import-rule-help'),
        text: document.getElementById('overlayBody').innerText.slice(0, 80)})""")
    check("自动填充是弹窗，密码规则在弹窗里",
          dialog["title"] == "自动填充" and dialog["rules"][:2] == ["random", "sid"], dialog)
    check("使用说明在规则下面", dialog["help"], dialog)
    page.evaluate("() => document.getElementById('afGo').click()")
    page.wait_for_timeout(1600)
    filled = page.evaluate("""() => {
        const tr = document.querySelector('#importTable tbody tr[data-import-row]');
        return {user: tr.querySelector('[data-import-field=username]').value,
                pwd: tr.querySelector('[data-import-field=password]').value,
                bad: tr.classList.contains('row-bad'),
                go: document.getElementById('importGo').disabled,
                count: document.getElementById('importCount').innerText.trim()};
    }""")
    check("点了才补出登录名与密码",
          bool(filled["user"]) and len(filled["pwd"]) >= 8, filled)
    check("这一行现在能导入了", not filled["bad"] and filled["go"] is False, filled)
    check("计数写的是可导入几行", "可导入" in filled["count"], filled["count"])

    # 手动清空登录名：不能又被自动补上
    page.fill("#importTable tbody tr:first-child [data-import-field=username]", "")
    page.wait_for_timeout(1500)
    cleared = page.evaluate("""() => {
        const tr = document.querySelector('#importTable tbody tr[data-import-row]');
        return {user: tr.querySelector('[data-import-field=username]').value,
                bad: tr.classList.contains('row-bad')};
    }""")
    check("删掉的登录名就留空，不自己长回来",
          cleared["user"] == "" and cleared["bad"], cleared)
    page.click("#importBack")
    page.wait_for_timeout(500)
    check("有内容时离开会拦一下",
          page.locator("#importLeaveGo").count() == 1, page.url)
    page.click("#importLeaveGo")
    page.wait_for_timeout(600)
    check("确认之后才真的走", page.url.endswith("#/users"), page.url)
    page.click("#importAccountBtn")
    page.wait_for_timeout(700)

    print("== 读一张表：预览就是编辑页 ==")
    box = tempfile.mkdtemp(prefix="import-ui-")
    path = os.path.join(box, "accounts.csv")
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write("显示名,角色,初始密码,学号\n"
                 "界面甲,管理员,,2026011\n"
                 "界面乙,,GivenPass9,2026012\n")
    page.set_input_files("#importFile", path)
    page.wait_for_function(
        "() => document.querySelectorAll('#importTable tbody tr[data-import-row]').length >= 2",
        timeout=20000)
    page.wait_for_timeout(500)
    got = page.evaluate("""() => Array.from(
        document.querySelectorAll('#importTable tbody tr[data-import-row]')).map(tr => ({
            user: tr.querySelector('[data-import-field=username]').value,
            name: tr.querySelector('[data-import-field=name]').value,
            pwd: tr.querySelector('[data-import-field=password]').value,
            bad: tr.classList.contains('row-bad'),
        }))""")
    check("两行都读进来了", len(got) == 2, got)
    check("表里没登录名就留空", got[0]["user"] == "" and got[1]["user"] == "", got)
    check("表里给的密码照原样", got[1]["pwd"] == "GivenPass9", got[1])
    check("缺登录名的那两行还不能导入", got[0]["bad"] and got[1]["bad"], got)
    check("导入键是灰的", page.locator("#importGo").is_disabled())

    page.evaluate("() => document.getElementById('importFillBtn').click()")
    page.wait_for_timeout(600)
    page.evaluate("() => document.getElementById('afGo').click()")
    page.wait_for_timeout(1600)
    ready = page.evaluate("""() => Array.from(
        document.querySelectorAll('#importTable tbody tr[data-import-row]')).map(tr => ({
            user: tr.querySelector('[data-import-field=username]').value,
            pwd: tr.querySelector('[data-import-field=password]').value,
            bad: tr.classList.contains('row-bad'),
        }))""")
    check("自动填充补齐了登录名，没动表里给的密码",
          ready[0]["user"] != "" and ready[1]["user"] != ""
          and ready[1]["pwd"] == "GivenPass9", ready)
    check("两行都能导入了", not any(row["bad"] for row in ready), ready)
    check("导入键亮了", not page.locator("#importGo").is_disabled())
    check("没有问题时状态行是空的",
          page.inner_text("#importState").strip() == "",
          page.inner_text("#importState"))
    check("每一行都填满时，自动填充那颗键是灰的",
          page.locator("#importFillBtn").is_disabled())

    print("== 下载账号表 ==")
    with page.expect_download(timeout=20000) as grab:
        page.click("#importDownloadBtn")
    download = grab.value
    saved = os.path.join(box, "saved.csv")
    download.save_as(saved)
    text_csv = open(saved, encoding="utf-8-sig").read()
    check("下载的是一张表", download.suggested_filename.endswith(".csv"),
          download.suggested_filename)
    check("表里有初始密码", "GivenPass9" in text_csv, text_csv[:120])
    page.wait_for_timeout(600)

    print("== 改过之后导入要先问一句 ==")
    page.fill("#importTable tbody tr:nth-child(2) [data-import-field=name]", "界面乙改")
    page.wait_for_timeout(1400)
    # 程序化点击：这张表刚重绘过，鼠标点击会撞上"元素还在动"，测的又不是命中测试
    page.evaluate("() => document.getElementById('importGo').click()")
    page.wait_for_timeout(600)
    asked = page.locator("#overlayBody").count() and "下载" in page.inner_text("#overlayBody")
    check("没重新下载就先问一句", bool(asked),
          page.inner_text("#overlayBody")[:80] if page.locator("#overlayBody").count() else "")
    page.click("#confirmGo")
    page.wait_for_timeout(1500)
    made = []
    owner = owner_opener()
    for acc in call(owner, "/api/panel/accounts?limit=200")["accounts"]:
        if acc["name"] in ("界面甲", "界面乙改"):
            made.append(acc)
    check("两个账号都建出来了", len(made) == 2, [a["name"] for a in made])
    check("导入出来的是临时密码",
          all(a["passTemp"] is True for a in made),
          [(a["name"], a["passTemp"]) for a in made])

    print("== 返回用户管理要有让位动画 ==")
    check("用户管理的标题也在 head-title 里（动画要它）",
          page.locator('.page[data-page="users"] .head-title .crumb-title').count() == 1)
    page.evaluate(CRUMB_SAMPLER)
    page.evaluate("(n) => window.__startCrumb(n)", "users")
    page.click("#importBack")
    page.wait_for_timeout(1100)
    moved = [s for s in page.evaluate("() => window.__crumb || []") if s["shown"]]
    travel = (round(max(r["left"] for r in moved) - min(r["left"] for r in moved), 1)
              if moved else 0)
    check("标题从让位处滑回原位（不是闪回）", travel >= 30, travel)
    check("最后落在原位",
          bool(moved) and abs(moved[-1]["left"] - min(r["left"] for r in moved)) < 1,
          moved[-1] if moved else None)
    check("回到用户管理", page.url.endswith("#/users"), page.url)


def org_page(page):
    """组织管理：建组织、三个选人框互斥、归档与恢复、搜索、彻底删除。"""
    oid = "ui-org-%d" % random.randint(1000, 9999)
    oname = "界面测试组织"
    print("== 组织管理：入口与空状态 ==")
    page.goto(BASE + "/panel#/orgs")
    page.wait_for_timeout(700)
    nav = visible_nav(page)
    check("侧栏有组织管理", "orgs" in nav["items"], nav["items"])
    check("右上角有创建组织", page.locator("#orgNewBtn").is_visible())
    body = page.inner_text("#orgsTable tbody")
    check("空表说得出下一步", "还没有组织" in body, body[:60])

    print("== 创建组织：侧栏淡高亮、必填项在框下标出 ==")
    page.click("#orgNewBtn")
    page.wait_for_timeout(600)
    check("进的是组织子页", page.url.endswith("#/org-new"), page.url)
    check("有返回键", page.locator("#orgNewBack").is_visible())
    trail = page.evaluate("""() => {
        const a = document.querySelector('#sidebar [data-nav="orgs"]');
        return a ? {trail: a.hasAttribute('data-trail'),
                    current: a.getAttribute('aria-current')} : null;
    }""")
    check("子页里组织管理淡高亮",
          bool(trail) and trail["trail"] and not trail["current"], trail)
    caps = page.evaluate("""() => ['orgHeadPick', 'orgAdminPick', 'orgMemberPick'].map(id => {
        const cap = document.getElementById(id).querySelector('.cap');
        return {text: cap.textContent, lines: cap.getClientRects().length};
    })""")
    check("三个加号的字是 负责人 / 管理 / 成员",
          [c["text"] for c in caps] == ["负责人", "管理", "成员"], caps)
    check("这些字都不折行", all(c["lines"] == 1 for c in caps), caps)
    xs = page.evaluate("""() => ['orgHeadPick', 'orgAdminPick', 'orgMemberPick']
        .map(id => Math.round(document.getElementById(id).getBoundingClientRect().left))""")
    check("三排的加号左边对齐", len(set(xs)) == 1, xs)

    def complain():
        return page.evaluate("""() => {
            for (const id of ['orgNameErr', 'orgIdErr', 'orgHeadErr']) {
                const el = document.getElementById(id);
                if (el && !el.hidden && el.textContent) return el.textContent;
            }
            return '';
        }""")

    def page_errors():
            return page.locator('[data-page="org-new"] .err:not([hidden])').count()

    page.evaluate("() => document.getElementById('orgCreate').click()")
    check("没填名称就在框下标出来", complain() == "请填写组织名称", complain())
    check("名称那一格随之标红",
          page.get_attribute("#orgName", "aria-invalid") == "true")
    page.fill("#orgName", oname)
    check("填上名称后提示撤掉", complain() == "", complain())
    page.evaluate("() => document.getElementById('orgCreate').click()")
    check("没填标识也标在框下", complain() == "请填写组织标识", complain())

    print("== 组织标识：不合规的字符进不来，短信当场说 ==")
    page.click("#orgId")
    page.keyboard.type("ABC d@e1")
    typed = page.input_value("#orgId")
    check("大写转小写，空格与 @ 收不进来", typed == "abcde1", typed)
    page.fill("#orgId", "ab")
    page.wait_for_timeout(250)
    check("打字途中不评语", complain() == "", complain())
    page.evaluate("() => document.getElementById('orgId').blur()")
    page.wait_for_timeout(250)
    check("失焦后说明不足 3 位",
          complain().startswith("至少 3 位"), complain())
    page.fill("#orgId", oid)
    page.evaluate("() => document.getElementById('orgId').blur()")
    page.wait_for_timeout(250)
    check("补够了就不再提", complain() == "", complain())
    page.evaluate("() => document.getElementById('orgCreate').click()")
    check("没有负责人也会被拦下", complain() == "至少要有一位负责人", complain())
    check("页面上没有另起一行的报错条",
          page.locator("#orgCreateErr").count() == 0
          and page.locator('[data-page="org-new"] .err:not([hidden])').count() == 1,
          page.locator('[data-page="org-new"] .err:not([hidden])').count())

    print("== 负责人：多选，头像按账号取图 ==")
    page.evaluate("() => document.getElementById('orgHeadPick').click()")
    page.wait_for_selector(".pp-back", timeout=10000)
    page.wait_for_timeout(500)
    heads = page.evaluate("""() => {
        const out = [];
        for (const t of document.querySelectorAll('.pp-back .pp-tile')) {
            if (t.classList.contains('off')) continue;
            t.click();
            out.push(t.getAttribute('data-pp-id'));
            if (out.length === 2) break;
        }
        return out;
    }""")
    check("负责人可以选多位", len(heads) == 2, heads)
    page.evaluate("() => document.querySelector('.pp-back .pp-done').click()")
    page.wait_for_timeout(400)
    chips = page.evaluate("""() => Array.prototype.map.call(
        document.querySelectorAll('#orgHeadChips [data-org-unpick]'),
        b => b.getAttribute('data-org-unpick'))""")
    check("两位负责人都落到人像行里", sorted(chips) == sorted(heads), (chips, heads))
    xs2 = page.evaluate("""() => ['orgHeadPick', 'orgAdminPick', 'orgMemberPick']
        .map(id => Math.round(document.getElementById(id).getBoundingClientRect().left))""")
    check("有人之后加号仍与别的排对齐", len(set(xs2)) == 1, xs2)
    check("选到人之后必填提示撤掉", complain() == "", complain())
    faces = page.evaluate("""() => Array.prototype.map.call(
        document.querySelectorAll('#orgHeadChips img'),
        i => ({src: i.getAttribute('src'),
               ok: i.complete && i.naturalWidth > 0}))""")
    check("头像按账号取图，不是存了哪张图的名字",
          all("/api/panel/avatar/" in f["src"] for f in faces), faces)
    check("每一张头像都真的加载出来了", all(f["ok"] for f in faces), faces)

    print("== 三个选人框互斥：别人选过的人灰着 ==")
    page.evaluate("() => document.getElementById('orgAdminPick').click()")
    page.wait_for_selector(".pp-back", timeout=10000)
    page.wait_for_timeout(600)
    grey = page.evaluate("""(id) => {
        const t = document.querySelector('.pp-back [data-pp-id="' + id + '"]');
        if (!t) return null;
        const was = t.getAttribute('aria-pressed');
        t.click();
        return {total: document.querySelectorAll('.pp-back .pp-tile').length,
                off: t.classList.contains('off'),
                aria: t.getAttribute('aria-disabled'),
                now: t.getAttribute('aria-pressed'),
                was: was};
    }""", heads[0])
    check("已经当过负责人的人仍在名单里", bool(grey) and grey["total"] >= 2, grey)
    check("但是灰显", bool(grey) and grey["off"] and grey["aria"] == "true", grey)
    check("灰显的点不动", bool(grey) and grey["now"] == grey["was"], grey)
    page.evaluate("() => document.querySelector('.pp-back .pp-cancel').click()")
    page.wait_for_timeout(300)

    print("== 填了一半走人：先问一句 ==")
    page.evaluate("() => document.querySelector('#sidebar [data-nav=\"orgs\"]').click()")
    page.wait_for_selector("#orgLeaveGo", timeout=8000)
    check("离开前给出确认", page.url.endswith("#/org-new"), page.url)
    page.evaluate("() => document.querySelector('#overlayBody [data-close-overlay]').click()")
    page.wait_for_timeout(400)
    check("继续编辑就留在这一页", page.url.endswith("#/org-new"), page.url)
    check("人像行还在",
          page.locator("#orgHeadChips [data-org-unpick]").count() == 2)

    print("== 移出一个人：剩下的头像不重画 ==")
    page.evaluate("""() => {
        window.__orgChips = Array.prototype.slice.call(
            document.querySelectorAll('#orgHeadChips .invite-chip'));
        window.__orgImgs = Array.prototype.slice.call(
            document.querySelectorAll('#orgHeadChips img'));
    }""")
    page.evaluate("""(id) => document.querySelector(
        '#orgHeadChips [data-org-unpick="' + id + '"]').click()""", heads[1])
    page.wait_for_timeout(400)
    left = page.evaluate("""() => {
        const chips = Array.prototype.slice.call(
            document.querySelectorAll('#orgHeadChips .invite-chip'));
        const imgs = Array.prototype.slice.call(
            document.querySelectorAll('#orgHeadChips img'));
        return {n: chips.length,
                sameChips: chips.every(c => window.__orgChips.indexOf(c) >= 0),
                sameImgs: imgs.every(i => window.__orgImgs.indexOf(i) >= 0)};
    }""")
    check("移出后少一个人", left["n"] == 1, left)
    check("剩下的头像仍是原来那些节点，没有整行重画",
          left["sameChips"] and left["sameImgs"], left)

    print("== 建成之后回到列表 ==")
    page.evaluate("() => document.getElementById('orgCreate').click()")
    page.wait_for_function("() => location.hash === '#/orgs'", timeout=20000)
    page.wait_for_timeout(700)
    body = page.inner_text("#orgsTable tbody")
    check("新组织出现在列表里",
          oname in body and oid in body, body[:120])
    check("成员列只写人数", "负责人" not in body and "\t1\t" in body, body[:120])
    after = page.evaluate("""() => {
        const a = document.querySelector('#sidebar [data-nav="orgs"]');
        return {trail: a.hasAttribute('data-trail'), current: a.getAttribute('aria-current')};
    }""")
    check("回到顶层就换成实高亮",
          after["current"] == "page" and not after["trail"], after)

    print("== 搜索 ==")
    page.fill("#orgSearch", "zzz-没有这个")
    page.wait_for_timeout(300)
    body = page.inner_text("#orgsTable tbody")
    check("搜不到时说没有匹配", "没有匹配的组织" in body, body[:60])
    page.fill("#orgSearch", oid[-4:])
    page.wait_for_timeout(300)
    body = page.inner_text("#orgsTable tbody")
    check("按标识也能搜到", oname in body, body[:60])
    page.click("#orgSearchClear")
    page.wait_for_timeout(300)
    check("清空搜索后回到全部", oname in page.inner_text("#orgsTable tbody"))

    print("== 组织详情：这一页给平台侧，每一处改动都要提权 ==")
    check("列表里不再摆归档按钮",
          page.locator("#orgsTable [data-org-archive]").count() == 0)
    check("列表里没有我的身份这一列",
          "我的身份" not in page.inner_text("#orgsTable thead"),
          page.inner_text("#orgsTable thead"))
    page.evaluate("""(id) => document.querySelector(
        '#orgsTable [data-org-open="' + id + '"]').click()""", oid)
    page.wait_for_function("() => location.hash.indexOf('#/org/') === 0", timeout=20000)
    page.wait_for_timeout(800)
    check("进的是组织详情", page.inner_text("#orgTitle") == oname,
          page.inner_text("#orgTitle"))
    check("副标题不写标识也不写身份",
          page.inner_text("#orgSub").strip() == "", page.inner_text("#orgSub"))
    check("详情里组织管理也是淡高亮",
          page.evaluate("""() => document.querySelector('#sidebar [data-nav="orgs"]')
              .hasAttribute('data-trail')"""))
    check("成员表里有一个人",
          page.locator("#orgMemberTable tbody tr").count() == 1
          and "共 1 人" in page.inner_text("#orgMemberCount"),
          page.inner_text("#orgMemberCount"))
    check("标识写成一行说明",
          page.inner_text(".org-id-line").startswith("id:") and
          page.inner_text("#orgEditId") == oid, page.inner_text(".org-id-line"))
    check("标识没有输入框", page.locator("input#orgEditId").count() == 0)
    check("成员一栏是身份下拉",
          page.locator("#orgMemberTable [data-org-role-for]").count() == 1)

    page.fill("#orgEditName", oname + "二")
    page.wait_for_timeout(2000)
    check("静置两秒名字还在",
          page.input_value("#orgEditName") == oname + "二",
          page.input_value("#orgEditName"))
    page.fill("#orgEditNoteInput", "改过的简介")
    page.wait_for_timeout(600)
    check("填了简介之后名字还在",
          page.input_value("#orgEditName") == oname + "二",
          page.input_value("#orgEditName"))
    # 这一页的写操作一律走 apiSudo：会话已经有 sudo 时就不再弹窗，
    # 所以这里只看结果——名字改成了，说明请求确实发了出去并被接受
    page.evaluate("() => document.getElementById('orgSaveBtn').click()")
    sudo_if_asked(page, 8000)
    page.wait_for_timeout(1400)
    check("改完名字标题跟着变", page.inner_text("#orgTitle") == oname + "二",
          page.inner_text("#orgTitle"))

    print("== 添加成员与调整身份 ==")
    page.evaluate("() => document.getElementById('orgMemberAdd').click()")
    page.wait_for_selector(".pp-back", timeout=10000)
    page.wait_for_timeout(500)
    added = page.evaluate("""() => {
        const t = document.querySelector('.pp-back .pp-tile');
        if (!t) return null;
        t.click();
        return t.getAttribute('data-pp-id');
    }""")
    check("有账号可以加进来", bool(added), added)
    page.evaluate("() => document.querySelector('.pp-back .pp-done').click()")
    sudo_if_asked(page)
    page.wait_for_timeout(1400)
    check("加完变成两个人",
          page.locator("#orgMemberTable tbody tr").count() == 2
          and "共 2 人" in page.inner_text("#orgMemberCount"),
          page.inner_text("#orgMemberCount"))
    page.select_option('#orgMemberTable [data-org-role-for="%s"]' % added, "admin")
    sudo_if_asked(page)
    page.wait_for_timeout(1400)
    role = page.evaluate("""(id) => {
        const sel = document.querySelector(
            '#orgMemberTable [data-org-role-for="' + id + '"]');
        return sel ? sel.value : null;
    }""", added)
    check("身份改成管理之后就留在那儿", role == "admin", role)

    print("== 移出成员：先问一句，再提权 ==")
    page.evaluate("""(id) => document.querySelector(
        '#orgMemberTable [data-org-kick="' + id + '"]').click()""", added)
    page.wait_for_selector("#orgKickGo", timeout=8000)
    check("移出之前先确认", page.locator("#orgKickGo").is_visible())
    check("确认的时候人还没被移出",
          page.locator("#orgMemberTable tbody tr").count() == 2)
    page.evaluate("() => document.getElementById('orgKickGo').click()")
    sudo_if_asked(page)
    page.wait_for_timeout(1400)
    check("移出之后又回到一个人",
          page.locator("#orgMemberTable tbody tr").count() == 1
          and "共 1 人" in page.inner_text("#orgMemberCount"),
          page.inner_text("#orgMemberCount"))

    print("== 归档与恢复都在这一页 ==")
    page.evaluate("() => document.getElementById('orgArchiveBtn').click()")
    page.wait_for_selector("#orgArchiveGo", timeout=8000)
    page.evaluate("() => document.getElementById('orgArchiveGo').click()")
    sudo_if_asked(page)
    page.wait_for_timeout(1500)
    check("归档后副标题标着已归档", "已归档" in page.inner_text("#orgSub"),
          page.inner_text("#orgSub"))
    check("归档后不能再改", page.locator("#orgEditName").is_disabled())
    check("归档后成员身份只能看",
          page.locator("#orgMemberTable [data-org-role-for]").count() == 0)
    check("归档后出现恢复键", page.locator("#orgRestoreBtn").is_visible())
    page.evaluate("() => document.getElementById('orgRestoreBtn').click()")
    sudo_if_asked(page)
    page.wait_for_timeout(1500)
    check("恢复后又能改", page.locator("#orgEditName").is_enabled())

    print("== 我的组织 › 组织 › 组织管理（自己管自己，不必 sudo） ==")
    selfoid = "ui-self-%d" % random.randint(1000, 9999)
    page.evaluate("""(id) => fetch('/api/panel/me', {
            headers: {'X-Requested-With': 'CAEventManager'}
        }).then(r => r.json()).then(d => fetch('/api/panel/org/create', {
            method: 'POST',
            headers: {'X-Requested-With': 'CAEventManager',
                      'Content-Type': 'application/json'},
            body: JSON.stringify({id: id, name: '自管组织', heads: [d.me.id]})
        })).then(r => r.json())""", selfoid)
    page.goto(BASE + "/panel#/my-orgs")
    page.wait_for_timeout(1400)
    grid = page.inner_text("#myOrgGrid")
    check("我的组织里有自己负责的组织", "自管组织" in grid, grid[:80])
    page.evaluate("""(id) => document.querySelector(
        '#myOrgGrid [data-org-self="' + id + '"]').click()""", selfoid)
    page.wait_for_timeout(1400)
    check("点卡片进组织页", page.inner_text("#myOrgTitle") == "自管组织",
          page.inner_text("#myOrgTitle"))
    check("组织页写着我的身份与人数",
          "负责人" in page.inner_text("#myOrgSub"), page.inner_text("#myOrgSub"))
    check("负责人看得到 [组织管理]", page.locator("#myOrgManageBtn").is_visible())
    page.click("#myOrgManageBtn")
    page.wait_for_timeout(1400)
    check("进的是组织自己的管理页",
          page.url.endswith("#/my-org-manage/" + selfoid), page.url)
    check("管理页写着标识",
          page.inner_text("#orgManageId") == selfoid, page.inner_text("#orgManageId"))
    check("唯一负责人的 [移出] 是灰的",
          page.locator("#orgManageTable .badge-off").count() == 1)
    check("灰着的那颗点不出确认框",
          page.evaluate("""() => {
              document.querySelector('#orgManageTable .badge-off').click();
              return document.getElementById('overlay').hidden;
          }"""))
    page.fill("#orgManageName", "自管组织二")
    page.evaluate("() => document.getElementById('orgManageSave').click()")
    page.wait_for_timeout(1600)
    check("自己改名字不必提权就直接生效",
          page.inner_text("#orgManageCrumb") == "自管组织二",
          page.inner_text("#orgManageCrumb"))

    print("== 平台页给组织负责人指一条更轻的路 ==")
    page.goto(BASE + "/panel#/org/" + selfoid)
    page.wait_for_timeout(1400)
    check("平台页上出现提示条",
          page.locator("#orgSelfHint").is_visible()
          and "负责人" in page.inner_text("#orgSelfHintText"),
          page.inner_text("#orgSelfHintText"))
    page.evaluate("() => document.getElementById('orgSelfHintClose').click()")
    page.wait_for_timeout(250)
    check("提示条可以关掉", page.locator("#orgSelfHint").is_hidden())

    print("== 我的组织：一张卡片一个组织 ==")
    page.goto(BASE + "/panel#/my-orgs")
    page.wait_for_timeout(900)
    check("侧栏有我的组织", "my-orgs" in visible_nav(page)["items"])
    grid = page.inner_text("#myOrgGrid")
    check("我的组织里列出了参加的组织",
          page.locator("#myOrgGrid .org-card").count() >= 1, grid[:80])
    check("卡片上写着组织名", (oname + "二") in grid, grid[:120])
    check("卡片上写着我的身份", "负责人" in grid, grid[:120])
    check("卡片上写着成员人数",
          page.locator("#myOrgGrid .org-card-people").count()
          == page.locator("#myOrgGrid .org-card").count(), grid[:120])
    check("每张卡片都有一块图的位置",
          page.locator("#myOrgGrid .org-card-cover").count()
          == page.locator("#myOrgGrid .org-card").count())
    check("加入组织先摆着、还没有邀请码可用",
          page.locator("#orgJoinBtn").is_disabled())

    print("== 彻底删除：先归档，再提权 ==")
    page.goto(BASE + "/panel#/org/" + oid)
    page.wait_for_selector("#orgTitle", timeout=20000)
    page.wait_for_timeout(900)
    page.evaluate("() => document.getElementById('orgArchiveBtn').click()")
    page.wait_for_selector("#orgArchiveGo", timeout=8000)
    page.evaluate("() => document.getElementById('orgArchiveGo').click()")
    sudo_if_asked(page)
    page.wait_for_timeout(1500)
    page.evaluate("() => document.getElementById('orgDropBtn').click()")
    page.wait_for_selector("#orgDropGo", timeout=8000)
    page.evaluate("() => document.getElementById('orgDropGo').click()")
    sudo_if_asked(page)
    page.wait_for_function("() => location.hash === '#/orgs'", timeout=20000)
    page.wait_for_timeout(800)
    body = page.inner_text("#orgsTable tbody")
    check("彻底删除后回到列表且不在其中", oid not in body, body[:120])

    print("== 写错地址的详情页 ==")
    page.goto(BASE + "/panel#/org/" + selfoid)
    page.wait_for_timeout(1200)
    page.evaluate("() => { location.hash = '#/org/nwuca/x'; }")
    page.wait_for_timeout(1000)
    check("多一段的地址直接说没有这一页",
          page.locator("#missing").is_visible(), page.url)
    check("不把上一个组织的页面留在屏幕上",
          page.evaluate(
              "() => document.querySelector('.page[data-page=\"org\"]').hidden"))
    page.evaluate("() => document.getElementById('missingBack').click()")
    page.wait_for_timeout(900)
    check("回到首页", page.url.endswith("#/overview"), page.url)

    print("== 直接打开坏地址：没有上一页就不给返回键 ==")
    page.goto(BASE + "/panel#/org/nope/x")
    page.wait_for_timeout(400)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(1600)
    check("说的还是页面不存在", page.locator("#missing").is_visible(), page.url)
    check("没有上一页时只有回到首页",
          page.locator("#missingPrev").is_hidden()
          and page.locator("#missingBack").is_visible(),
          page.evaluate(
              "() => ({prev: !document.getElementById('missingPrev').hidden,"
              " back: !document.getElementById('missingBack').hidden})"))
    page.evaluate("() => document.getElementById('missingBack').click()")
    page.wait_for_timeout(800)

    print("== 新建活动这类向导也淡高亮上一层 ==")
    page.goto(BASE + "/panel#/new-event")
    page.wait_for_timeout(800)
    ev = page.evaluate("""() => {
        const a = document.querySelector('#sidebar [data-nav="events"]');
        return {trail: a.hasAttribute('data-trail'),
                current: a.getAttribute('aria-current')};
    }""")
    check("新建活动时我的活动淡高亮",
          ev["trail"] and not ev["current"], ev)
    page.goto(BASE + "/panel#/orgs")
    page.wait_for_timeout(500)


def field_keys(browser):
    """表单里上下方向键换框：单行文本才接管，别的输入框照旧用自己的方向键。"""
    print("== 表单里上下方向键换框 ==")
    ctx, page = new_page(browser, 1180, 860)
    try:
        page.goto(BASE + "/login", wait_until="domcontentloaded")
        page.wait_for_selector("#loginForm", timeout=20000)

        def active():
            return page.evaluate("() => (document.activeElement || {}).id || ''")

        page.click("#username")
        page.keyboard.type("someone")
        page.keyboard.press("ArrowDown")
        check("↓ 从登录名走到密码", active() == "password", active())
        page.keyboard.press("ArrowDown")
        check("↓ 再从密码走到提交键", active() == "go", active())
        page.keyboard.press("ArrowUp")
        check("↑ 回到密码", active() == "password", active())
        page.keyboard.press("ArrowUp")
        check("↑ 回到登录名", active() == "username", active())
        page.keyboard.press("ArrowUp")
        check("顶格的 ↑ 不会再往别处跑", active() == "username", active())
        page.keyboard.press("Shift+ArrowDown")
        check("带 Shift 的 ↓ 不换框", active() == "username", active())

        # 数字、多行文本、下拉、带候选的输入框都不该被接管：各放一个探针在页面上试
        page.evaluate("""() => {
            const box = document.createElement('div');
            box.setAttribute('data-page', 'field-probe');
            box.innerHTML =
                '<input id="kNum" type="number" value="1">' +
                '<textarea id="kArea"></textarea>' +
                '<select id="kSel"><option>a</option><option>b</option></select>' +
                '<input id="kList" type="search" list="kDl">' +
                '<datalist id="kDl"><option value="a"></option></datalist>' +
                '<input id="kText" type="text"><input id="kNext" type="text">' +
                '<button id="kPrimary" class="btn primary" type="button">保存</button>';
            document.body.appendChild(box);
        }""")
        page.focus("#kNum")
        page.keyboard.press("ArrowDown")
        check("数字框的 ↓ 留给它自己", active() == "kNum", active())
        page.focus("#kArea")
        page.keyboard.press("ArrowDown")
        check("多行文本的 ↓ 留给它自己", active() == "kArea", active())
        page.focus("#kSel")
        page.keyboard.press("ArrowDown")
        check("下拉框的 ↓ 留给它自己", active() == "kSel", active())
        page.focus("#kList")
        page.keyboard.press("ArrowDown")
        check("带候选列表的输入框不接管", active() == "kList", active())
        page.focus("#kText")
        page.keyboard.press("ArrowDown")
        check("普通单行输入框才跳", active() == "kNext", active())
        page.keyboard.press("ArrowDown")
        check("最后一格 ↓ 落到主按钮", active() == "kPrimary", active())
        page.keyboard.press("ArrowUp")
        check("主按钮上 ↑ 回到上一格", active() == "kNext", active())
        page.evaluate(
            "() => document.querySelector('[data-page=\"field-probe\"]').remove()")

        # 弹窗里的一行是弹窗自己，不往身后那一页里跑
        if WITH_ACCOUNTS:
            page.goto(BASE + "/login", wait_until="domcontentloaded")
            page.fill("#username", LOGIN)
            page.fill("#password", PASSWORD)
            page.click("#go")
            page.wait_for_url("**/panel", timeout=20000)
            page.wait_for_function(
                "() => document.documentElement.getAttribute('data-booted') === '1'",
                timeout=25000)
            page.goto(BASE + "/panel#/users")
            page.wait_for_timeout(700)
            page.click("#newAccountBtn")
            page.wait_for_timeout(400)
            page.focus("#naUser")
            page.keyboard.press("ArrowDown")
            check("弹窗里 ↓ 走到下一格", active() == "naName", active())
            page.keyboard.press("ArrowUp")
            check("弹窗里 ↑ 回到上一格", active() == "naUser", active())
            page.evaluate("() => document.getElementById('overlayClose').click()")
            page.wait_for_timeout(300)
    finally:
        ctx.close()


def search_page(page):
    print("== the account search box ==")
    page.goto(BASE + "/panel#/users")
    page.wait_for_timeout(320)
    check("a hidden login-shaped honeypot sits before it",
          page.locator(".hp input[name=username]").count() >= 1)
    # The native cross cannot be measured, so check that the rule which hides it
    # is actually shipped and that ours is a properly sized target.
    check("the native clear button is disabled in css", page.evaluate("""() => {
        for (const sheet of document.styleSheets) {
            let rules;
            try { rules = sheet.cssRules; } catch (e) { continue; }
            for (const rule of rules) {
                if (!rule.selectorText) continue;
                if (rule.selectorText.indexOf('::-webkit-search-cancel-button') >= 0 &&
                    /appearance:\s*none/.test(rule.cssText)) return true;
            }
        }
        return false;
    }"""))
    page.fill("#userSearch", "zzz-no-such-user")
    page.wait_for_timeout(200)
    clear = page.locator("#userSearchClear")
    check("the clear button appears once there is text", clear.is_visible())
    box = page.evaluate("""() => {
        const r = document.getElementById('userSearchClear').getBoundingClientRect();
        return {w: Math.round(r.width), h: Math.round(r.height)};
    }""")
    check("the circle is small under a mouse",
          12 <= box["w"] <= 22 and 12 <= box["h"] <= 22, box)
    check("触屏上那颗清空键更大，而且四边等距", page.evaluate("""() => {
        for (const sheet of document.styleSheets) {
            let rules;
            try { rules = sheet.cssRules; } catch (e) { continue; }
            for (const rule of rules) {
                if (!rule.conditionText || rule.conditionText.indexOf('coarse') < 0) continue;
                const text = rule.cssText || '';
                if (text.indexOf('.search-clear') < 0) continue;
                // 上下各 4px 定位、右边 4px：高度由框决定，四边留白才一样
                if (/width:\\s*34px/.test(text) && /top:\\s*4px/.test(text) &&
                    /bottom:\\s*4px/.test(text) && /right:\\s*4px/.test(text)) return true;
            }
        }
        return false;
    }"""))
    clear.click()
    page.wait_for_timeout(200)
    check("clicking it empties the box",
          page.evaluate("() => document.getElementById('userSearch').value") == "")
    check("the box is not autofilled", page.evaluate(
        "() => document.getElementById('userSearch').value") != "owner")

    print("== ESC 清空搜索框 ==")
    page.fill("#userSearch", "zzz-no-such-account")
    page.wait_for_timeout(500)
    check("先确认筛掉了东西",
          "没有匹配的账号" in page.inner_text("#accountsTable tbody"),
          page.inner_text("#accountsTable tbody")[:80])
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    check("ESC 把搜索框清空",
          page.evaluate("() => document.getElementById('userSearch').value") == "")
    check("清空之后名单自己回来了",
          page.locator("#accountsTable tbody tr").count() >= 1,
          page.locator("#accountsTable tbody tr").count())


def card_page(page):
    print("== 我的活动 is two mutually exclusive cards ==")
    page.goto(BASE + "/panel#/events")
    page.wait_for_timeout(420)
    cards = page.evaluate("""() => ({
        owned: document.querySelectorAll('#ownedEvents .event-card').length,
        joined: document.querySelectorAll('#joinedEvents .event-card').length,
        headings: Array.from(document.querySelectorAll(
            '.page[data-page="events"] .card-head h2')).map(h => h.textContent.trim()),
    })""")
    check("the page has both headings",
          cards["headings"] == ["我创建的活动", "我参与的活动"],
          cards["headings"])
    check("the creator sees their own activity on the left", cards["owned"] >= 1, cards)
    both = page.evaluate("""() => {
        const ids = (sel) => Array.from(document.querySelectorAll(sel + ' .event-token code'))
            .map(el => el.getAttribute('title'));
        const owned = ids('#ownedEvents');
        const joined = ids('#joinedEvents');
        return owned.filter(id => joined.indexOf(id) >= 0);
    }""")
    check("and no activity appears on both sides", not both, both)

    print("== the activity card ==")
    page.goto(BASE + "/panel#/events")
    page.wait_for_timeout(400)
    cols = page.evaluate("""() => {
        const grid = document.getElementById('ownedEvents');
        return getComputedStyle(grid).gridTemplateColumns.split(' ')
            .filter(Boolean).length;
    }""")
    check("three cards fit across a page", cols == 3, cols)
    check("the token is shortened, not wrapped",
          page.locator("#ownedEvents .event-token code").count() >= 1)
    label = page.evaluate("""() => {
        const box = document.querySelector('#ownedEvents .event-token');
        const tag = box.querySelector('.token-label');
        if (!tag) return null;
        const code = box.querySelector('code');
        const darker = (color) => color.match(/\\d+/g).slice(0, 3)
            .reduce((sum, v) => sum + Number(v), 0);
        return {text: tag.textContent.trim(),
                leftOfValue: tag.getBoundingClientRect().left <
                             code.getBoundingClientRect().left,
                labelInk: darker(getComputedStyle(tag).color),
                valueInk: darker(getComputedStyle(code).color)};
    }""")
    check("a token label sits on its left", label and label["leftOfValue"]
          and label["text"].lower() == "token", label)
    check("the label is darker than the value", label and
          label["labelInk"] < label["valueInk"], label)
    check("it keeps both ends and stars the middle", page.evaluate("""() => {
        const el = document.querySelector('#ownedEvents .event-token code');
        const full = el.title, shown = el.textContent;
        return shown.length < full.length && full.startsWith(shown.slice(0, 4))
               && full.endsWith(shown.slice(-5)) && shown.indexOf('****') > 0;
    }"""))
    per_card = page.evaluate("""() => Array.from(
        document.querySelectorAll('#ownedEvents .event-card')).map(card => Array.from(
            card.querySelectorAll('.event-actions a')).map(a => a.textContent.trim()))""")
    check("one 管理面板 button per card, nothing else",
          all(row == ["管理面板"] for row in per_card), per_card)

    print("== the card menu ==")
    check("no menu until asked", page.locator(".card-menu").count() == 0)
    page.click("#ownedEvents .card-menu-btn")
    page.wait_for_timeout(240)
    check("the three-dot button opens a menu",
          page.locator(".card-menu").is_visible())
    items = page.evaluate(
        "() => Array.from(document.querySelectorAll('.card-menu button, "
        ".card-menu .menu-link')).map(b => b.textContent.trim())")
    joined = " ".join(items)
    check("it holds the less-used actions",
          "活动设置" in joined and "保存为预设" in joined, items)
    check("nothing already on the card is repeated",
          "复制活动 token" not in items and "管理面板" not in items, items)
    # both entries do something now, so neither is greyed out
    check("nothing in the menu is a placeholder", page.evaluate(
        "() => Array.from(document.querySelectorAll('.card-menu button'))"
        ".filter(b => b.disabled).length") == 0)
    check("活动设置 opens the activity's own page", page.evaluate(
        "() => !!document.querySelector('.card-menu .menu-link[href]')"))
    # 行尾那枚"在新窗口打开"：贴右边、比字小，别挤在文字后面
    mark = page.evaluate("""() => {
        const link = document.querySelector('.card-menu .menu-link');
        const ext = link.querySelector('.menu-ext');
        const svg = ext && ext.querySelector('svg');
        if (!svg) return null;
        const row = link.getBoundingClientRect();
        const box = svg.getBoundingClientRect();
        return {gapRight: Math.round(row.right - box.right),
                icon: Math.round(box.width * 10) / 10,
                font: parseFloat(getComputedStyle(link).fontSize),
                text: link.innerText.trim()};
    }""")
    check("活动设置行尾那枚箭头贴右侧、比文字略小",
          mark is not None and mark["text"] == "活动设置"
          and mark["gapRight"] <= 14 and mark["icon"] < mark["font"], mark)
    check("the menu stays inside the window", page.evaluate("""() => {
        const m = document.querySelector('.card-menu').getBoundingClientRect();
        return m.left >= 0 && m.right <= window.innerWidth
            && m.top >= 0 && m.bottom <= window.innerHeight;
    }"""))
    page.keyboard.press("Escape")
    page.wait_for_timeout(220)
    check("escape closes it", page.locator(".card-menu").count() == 0)

    print("== copy ==")
    page.click("#ownedEvents .copy-btn")
    page.wait_for_timeout(320)
    check("the copy button reports back", page.evaluate(
        "() => document.querySelector('#ownedEvents .copy-btn').classList"
        ".contains('copied')"))

    print("== ESC 在输入框里不退弹窗 ==")
    page.click("#ownedEvents .card-menu-btn")
    page.wait_for_timeout(240)
    page.click(".card-menu [data-rename]")
    page.wait_for_selector("#renameTitle", timeout=8000)
    page.wait_for_timeout(200)
    check("重命名弹窗里的输入框是聚焦的",
          page.evaluate("() => document.activeElement.id") == "renameTitle")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("输入框聚焦时 ESC 不退弹窗",
          page.locator("#overlay").is_visible())
    page.evaluate("() => document.getElementById('overlayClose').focus()")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("焦点挪开之后 ESC 才关",
          not page.locator("#overlay").is_visible())


def profile_page(page):
    print("== the profile editor ==")
    page.goto(BASE + "/panel#/account")
    page.wait_for_timeout(400)
    check("the display name is prefilled",
          page.locator("#pfName").input_value().strip() != "")
    check("the login name is prefilled",
          page.locator("#pfUser").input_value().strip() != "")
    check("no leftover 'cannot edit' note",
          "还不能改" not in page.content())

    # start from a known state: a previous run may have left an avatar behind
    if page.evaluate("""() => document.getElementById('profileAvatar')
                              .querySelector('img') !== null"""):
        page.click("#avatarPick")
        page.wait_for_timeout(300)
        page.click("#overlay [data-avatar-clear]")
        page.wait_for_function(
            "() => document.querySelectorAll('#profileAvatar img').length === 0",
            timeout=20000)
        page.wait_for_timeout(250)

    print("== the avatar badge opens a dialog ==")
    check("no format line on the page itself",
          "512 KB" not in page.locator('.page[data-page="account"]').inner_text())
    badge = page.evaluate("""() => {
        const el = document.getElementById('avatarPick');
        const av = document.querySelector('.avatar-wrap .avatar').getBoundingClientRect();
        const b = el.getBoundingClientRect();
        return {overlaps: b.left < av.right && b.top < av.bottom,
                inside: b.right <= av.right + 12 && b.bottom <= av.bottom + 12,
                icon: el.innerHTML.indexOf('M10 5.2v9.6') >= 0 ? 'plus' : 'other'};
    }""")
    check("the badge sits on the avatar's corner",
          badge["overlaps"] and badge["inside"], badge)
    check("with no picture it shows a plus", badge["icon"] == "plus", badge)

    page.click("#avatarPick")
    page.wait_for_timeout(320)
    check("the dialog opens", page.locator("#overlay").is_visible())
    check("it offers a link and a file",
          page.locator("#overlay [data-avatar-url]").count() == 1 and
          page.locator("#overlay [data-avatar-file]").count() == 1)
    check("the format rule lives here now",
          "512 KB" in page.locator("#overlayBody").inner_text())

    print("== importing a file ==")
    png = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tmp", "avatar-probe.png"))
    os.makedirs(os.path.dirname(png), exist_ok=True)
    write_png(png)
    page.click("#overlay [data-avatar-file]")
    page.wait_for_timeout(150)
    page.set_input_files("#avatarFile", png)
    page.wait_for_function(
        "() => document.querySelectorAll('#profileAvatar img').length === 1",
        timeout=20000)
    page.wait_for_timeout(300)
    check("the picture appears", page.evaluate(
        "() => document.querySelectorAll('#profileAvatar img').length === 1"))
    check("the dialog closes itself", not page.locator("#overlay").is_visible())
    check("the sidebar card picks it up too", page.evaluate(
        "() => document.querySelectorAll('#sideAvatar img').length === 1"))
    check("and the sidebar image is scaled to its circle", page.evaluate("""() => {
        const el = document.getElementById('sideAvatar');
        const img = el.querySelector('img');
        const r = img.getBoundingClientRect();
        // the element has a 1px border, so the content box is 2px smaller
        return Math.abs(r.width - el.clientWidth) < 2 &&
               Math.abs(r.height - el.clientHeight) < 2 &&
               getComputedStyle(img).objectFit === 'cover';
    }"""))
    check("the badge becomes a pencil", page.evaluate(
        "() => document.getElementById('avatarPick').innerHTML"
        ".indexOf('M4.2 15.8h3.1') >= 0"))

    print("== an avatar by link ==")
    page.click("#avatarPick")
    page.wait_for_timeout(300)
    page.fill("#avatarUrl", "not-a-link")
    page.click("#overlay [data-avatar-url]")
    page.wait_for_timeout(300)
    check("a bad link is refused without closing", page.locator("#overlay").is_visible())
    page.fill("#avatarUrl", "https://example.com/avatar.png")
    page.click("#overlay [data-avatar-url]")
    page.wait_for_timeout(900)
    src = page.evaluate(
        "() => (document.querySelector('#profileAvatar img') || {}).src || ''")
    check("a link is stored as the image source",
          src == "https://example.com/avatar.png", src)

    print("== removing it ==")
    page.click("#avatarPick")
    page.wait_for_timeout(300)
    page.click("#overlay [data-avatar-clear]")
    page.wait_for_function(
        "() => document.querySelectorAll('#profileAvatar img').length === 0",
        timeout=20000)
    page.wait_for_timeout(200)
    check("the letter comes back", page.evaluate(
        "() => document.getElementById('profileAvatar').textContent.trim().length === 1"))
    check("and so does the plus", page.evaluate(
        "() => document.getElementById('avatarPick').innerHTML"
        ".indexOf('M10 5.2v9.6') >= 0"))

    print("== saving a display name ==")
    before = page.locator("#pfName").input_value()
    page.fill("#pfName", "测试改动")
    page.click("#pfSave")
    page.wait_for_timeout(700)
    check("the sidebar card follows the new name",
          page.locator("#sideName").inner_text().strip() == "测试改动",
          page.locator("#sideName").inner_text())
    page.fill("#pfName", before)
    page.click("#pfSave")
    page.wait_for_timeout(700)

    print("== an empty name is refused in place ==")
    page.fill("#pfName", "")
    page.click("#pfSave")
    page.wait_for_timeout(500)
    check("the reason is shown next to the button",
          page.locator("#pfErr").inner_text().strip() != "")
    page.fill("#pfName", before)
    page.click("#pfSave")
    page.wait_for_timeout(500)
    # the refusal above is logged by the browser as a 400 on purpose
    page._errors = []

    print("== API 令牌：建一张、看首尾、吊销 ==")
    page.goto(BASE + "/panel#/account")
    page.wait_for_timeout(700)
    check("账户页上有令牌卡片", page.locator("#tokenTable").count() == 1)
    seen = {}

    def watch_token_request(request):
        if request.url.endswith("/api/panel/token/create"):
            try:
                seen.update(json.loads(request.post_data or "{}"))
            except Exception:
                pass

    page.on("request", watch_token_request)
    page.click("#tokenNew")
    page.wait_for_timeout(350)
    # 权限那几行：方框与文字必须同一行同一高度（.field label 是块级的，容易把方框
    # 挤到单独一行），方框用平台自己的 18px 方框而不是浏览器默认的
    geo = page.evaluate("""() => Array.from(document.querySelectorAll('.check-row')).map(r => {
        const box = r.querySelector('input');
        const tn = Array.from(r.childNodes)
            .find(n => n.nodeType === 3 && n.textContent.trim());
        const range = document.createRange();
        range.selectNodeContents(tn);
        const b = box.getBoundingClientRect();
        const t = range.getBoundingClientRect();
        return {mid: Math.round((b.top + b.height / 2) - (t.top + t.height / 2)),
                w: Math.round(b.width), display: getComputedStyle(r).display};
    })""")
    check("权限行的方框与文字同一行同一高度",
          geo and all(abs(g["mid"]) <= 1 and g["display"] == "flex" for g in geo), geo)
    check("权限行用的是平台自己的方框",
          geo and all(g["w"] == 18 for g in geo), geo)
    check("令牌弹窗有描述这一行的提示",
          "令牌描述，仅显示用途" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:200])
    # 一项权限都不给：创建键点不动，并在左下角说清为什么
    boxes = page.locator(".tk-scope")
    for i in range(boxes.count()):
        boxes.nth(i).uncheck()
    page.wait_for_timeout(200)
    check("空权限时创建键是灰的",
          page.locator("#tkGo").is_disabled())
    check("左下角写着无法创建空权限令牌",
          page.inner_text("#tkScopeErr").strip() == "无法创建空权限令牌",
          page.inner_text("#tkScopeErr"))
    boxes.first.check()
    page.wait_for_timeout(200)
    check("勾回来就能建了",
          page.locator("#tkGo").is_enabled()
          and page.inner_text("#tkScopeErr").strip() == "")
    page.fill("#tkName", "界面测试")
    page.click("#tkGo")
    page.wait_for_timeout(900)
    page.remove_listener("request", watch_token_request)
    token = page.evaluate("() => { const el = document.getElementById('tkOnce');"
                          " return el ? el.textContent.trim() : ''; }")
    check("创建后明文显示一次", token.startswith("cae-") and len(token) > 40, token)
    check("明文是浏览器自己生成的：请求里只有摘要",
          len(seen.get("hash", "")) == 64 and "token" not in seen, sorted(seen))
    check("请求里带上了首尾几位",
          len(seen.get("head", "")) == 4 and len(seen.get("tail", "")) == 5, seen)
    page.click("[data-close-overlay]")
    page.wait_for_timeout(400)
    row_text = page.inner_text("#tokenTable")
    check("列表里只留首尾几位",
          "•••••" in row_text and token not in row_text, row_text[:120])
    check("权限也写在行里", "读取" in row_text, row_text[:120])
    page.click('[data-token-revoke]')
    page.wait_for_timeout(400)
    check("吊销前先确认", page.locator("#overlay").is_visible())
    page.click("#tkRevokeGo")
    page.wait_for_timeout(800)
    check("吊销后标为已吊销", "已吊销" in page.inner_text("#tokenTable"),
          page.inner_text("#tokenTable")[:120])

    print("== 通知上的小动作 ==")
    page.evaluate("""() => {
        window.__acted = false;
        PX.toast("试试带动作的通知", false, 9000, {
            icon: "open", title: "去看看", run: function () { window.__acted = true; }
        });
        const all = document.querySelectorAll('.toast');
        all[all.length - 1].setAttribute('data-probe', '1');
    }""")
    page.wait_for_timeout(300)
    check("通知里出现了动作按钮", page.locator(".toast .toast-act").count() >= 1)
    shape = page.evaluate("""() => {
        const toast = document.querySelector('.toast[data-probe]');
        const act = toast.querySelector('.toast-act').getBoundingClientRect();
        const x = toast.querySelector('.toast-x').getBoundingClientRect();
        const t = toast.getBoundingClientRect();
        return {act: Math.round(act.height), x: Math.round(x.height),
                toast: Math.round(t.height), sameLine: Math.abs(act.top - x.top) < 3};
    }""")
    check("动作按钮不会把通知撑高", shape["toast"] <= 46, shape)
    check("与关闭键同一行同高", shape["sameLine"] and shape["act"] == shape["x"], shape)
    page.click(".toast[data-probe] .toast-act")
    page.wait_for_timeout(400)
    check("点一下就执行", page.evaluate("() => window.__acted") is True)
    check("执行后这一条自己消失",
          page.evaluate("() => !document.querySelector('.toast[data-probe]')"))


def write_png(path):
    """A 4x4 amber png, written by hand so the test needs no fixtures."""
    import struct
    import zlib

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    head = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes((180, 83, 9)) * 4
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) +
                 chunk(b"IDAT", zlib.compress(row * 4)) + chunk(b"IEND", b""))


def temp_password_page(browser):
    """管理员开的号：临时密码登录 → 只落在重设密码那一页 → 改完才能进面板。"""
    print("== 临时密码：登录后被送去重设密码 ==")
    owner = owner_opener()
    username = "temp" + str(int(time.time()))[-6:]
    made = call(owner, "/api/panel/account/create",
                {"username": username, "name": "临时密码测试", "role": "admin"})
    check("管理员开号拿到临时密码",
          bool(made and made.get("password")), made)
    temp_pw = (made or {}).get("password") or ""

    ctx, page = new_page(browser, 1180, 860)
    try:
        page.goto(BASE + "/login", wait_until="domcontentloaded")
        page.fill("#username", username)
        page.fill("#password", temp_pw)
        page.click("#go")
        page.wait_for_url("**/reset", timeout=20000)
        page.wait_for_timeout(500)
        check("登录后直接落到重设密码页", "/reset" in page.url, page.url)
        fields = page.evaluate("""() => ({
            one: !!document.getElementById('newPass'),
            two: !!document.getElementById('newPass2'),
            old: !!document.getElementById('oldPass'),
            title: (document.querySelector('h1') || {}).innerText || ''})""")
        check("页面上只有新密码与再输一次，不要旧密码",
              fields["one"] and fields["two"] and not fields["old"], fields)
        check("标题写着重设密码", fields["title"].strip() == "重设密码", fields["title"])
        check("副标题说要先设置新密码",
              "设置新密码" in page.inner_text(".page-sub"), page.inner_text(".page-sub"))
        check("两个输入框各有各的提示语",
              page.get_attribute("#newPass", "placeholder") == "输入新密码"
              and page.get_attribute("#newPass2", "placeholder") == "两次输入需一致",
              (page.get_attribute("#newPass", "placeholder"),
               page.get_attribute("#newPass2", "placeholder")))
        check("页脚只留一个切换账号",
              page.inner_text(".auth-foot").strip() == "切换账号",
              page.inner_text(".auth-foot"))
        brand = page.evaluate("""() => {
            const m = document.querySelector('.auth-card .brand-mark');
            const n = document.querySelector('.auth-card .brand-name');
            if (!m || !n) return null;
            const a = m.getBoundingClientRect(), b = n.getBoundingClientRect();
            return {sameRow: Math.abs((a.top + a.height / 2) - (b.top + b.height / 2)) < 4,
                    beside: b.left >= a.right - 1};
        }""")
        check("图标与标题在同一行",
              bool(brand and brand["sameRow"] and brand["beside"]), brand)

        page.fill("#newPass", "abc12345")
        page.fill("#newPass2", "abc12399")
        page.click("#go")
        page.wait_for_timeout(400)
        check("两次不一样就当场说明",
              "不一致" in page.inner_text("#err"), page.inner_text("#err"))

        page.fill("#newPass", "newpass-8899")
        page.fill("#newPass2", "newpass-8899")
        page.click("#go")
        page.wait_for_url("**/panel**", timeout=20000)
        page.wait_for_function(
            "() => document.documentElement.getAttribute('data-booted') === '1'",
            timeout=25000)
        check("改完之后进得了面板", "#/reset" not in page.url, page.url)
        after = call(owner, "/api/panel/accounts?limit=200")
        row = [a for a in after["accounts"] if a["username"] == username]
        check("临时密码的标记撤掉了",
              bool(row) and row[0].get("passTemp") is False, row)

        # 改完之后那一页就不该再开得起来：手输地址也送回面板
        page.goto(BASE + "/reset", wait_until="domcontentloaded")
        page.wait_for_url("**/panel**", timeout=20000)
        check("状态正常之后重设页跳回面板", "/panel" in page.url, page.url)
        gone = page.request.post(
            BASE + "/api/panel/password/reset",
            data={"new": "another-pass-1", "confirm": "another-pass-1"},
            headers={"X-Requested-With": "CAEventManager"})
        check("重设接口也不给用", gone.status == 409, gone.status)
    finally:
        ctx.close()

    print("== 重设页开着的时候，别处改好了也要自己退出去 ==")
    username2 = "tmp2" + str(int(time.time()))[-5:]
    made2 = call(owner, "/api/panel/account/create",
                 {"username": username2, "name": "第二个临时号", "role": "admin"})
    check("再开一个临时号", bool(made2 and made2.get("password")), made2)
    ctx2, page2 = new_page(browser, 1180, 860)
    try:
        page2.goto(BASE + "/login", wait_until="domcontentloaded")
        page2.fill("#username", username2)
        page2.fill("#password", (made2 or {}).get("password") or "")
        page2.click("#go")
        page2.wait_for_url("**/reset", timeout=20000)
        page2.wait_for_timeout(600)
        done = page2.request.post(
            BASE + "/api/panel/password/reset",
            data={"new": "settled-pass-1", "confirm": "settled-pass-1"},
            headers={"X-Requested-With": "CAEventManager"})
        check("同一会话在别处改好了", done.status == 200, done.status)
        page2.wait_for_url("**/panel**", timeout=20000)
        check("开着的那一页自己退回面板", "/panel" in page2.url, page2.url)
    finally:
        ctx2.close()


def users_table(page):
    print("== a table header must not move when the body empties ==")
    page.goto(BASE + "/panel#/users")
    page.wait_for_timeout(420)
    full = page.evaluate(
        "() => Array.from(document.querySelectorAll('#accountsTable th'))"
        ".map(th => Math.round(th.getBoundingClientRect().width))")
    page.fill("#userSearch", "zzz-no-such-account")
    page.wait_for_timeout(320)
    empty = page.evaluate(
        "() => Array.from(document.querySelectorAll('#accountsTable th'))"
        ".map(th => Math.round(th.getBoundingClientRect().width))")
    check("the header is the same width either way", full == empty,
          "%s vs %s" % (full, empty))
    check("the empty state still explains itself",
          "没有匹配" in page.locator("#accountsTable tbody").inner_text())
    page.fill("#userSearch", "")
    page.wait_for_timeout(260)

    print("== 存档配额在账号弹窗里调 ==")
    page.goto(BASE + "/panel#/users")
    page.wait_for_timeout(500)
    page.click('#accountsTable tbody tr:first-child [data-acct]')
    page.wait_for_timeout(400)
    shown = page.inner_text("#overlayBody")
    check("账号弹窗说得出现在的配额", "存档配额" in shown, shown[:120])
    check("也说得出用了多少", "已用" in shown, shown[:120])
    page.click("[data-quota]")
    page.wait_for_timeout(350)
    check("调配额是一个带上限的输入框",
          page.locator("#qKb").is_visible()
          and page.get_attribute("#qKb", "type") == "number")
    check("并说清楚 0 表示用默认值", "默认值" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:200])
    page.fill("#qKb", "1024")
    page.click("#qGo")
    page.wait_for_timeout(900)
    check("保存后配额写进去了", "1 MB" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:160])
    print("== 恢复默认配额 ==")
    page.click("[data-quota]")
    page.wait_for_timeout(350)
    page.fill("#qKb", "0")
    page.click("#qGo")
    page.wait_for_timeout(900)
    check("填 0 就回到默认", "默认" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:160])

    print("== 新建账号：只填登录名、显示名与角色 ==")
    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)
    page.click("#newAccountBtn")
    page.wait_for_timeout(400)
    new = page.evaluate("""() => ({
        ids: Array.from(document.querySelectorAll(
            '#overlayBody input, #overlayBody select')).map(el => el.id),
        pw: document.querySelectorAll('#overlayBody input[type=password]').length,
        text: document.getElementById('overlayBody').innerText,
    })""")
    check("只有登录名、显示名与角色三项",
          new["ids"] == ["naUser", "naName", "naRole"], new["ids"])
    check("没有让人填密码的框", new["pw"] == 0, new["pw"])
    check("写明密码由平台生成", "平台生成" in new["text"], new["text"][:160])
    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)
    print("== 重置密码：新密码只显示一次 ==")
    # 上一段（调配额）的弹窗还开着：先关掉，不然它挡着表格
    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)
    # 重置密码要提权：先在页面会话里进 sudo，免得中途被密码框打断
    page.evaluate("""(pw) => PX.api("/sudo", {method: "POST", body: {password: pw}})""",
                  PASSWORD)
    page.wait_for_timeout(300)
    # 站长的密码不能在用户管理里改：挑一行不是站长的账号来试
    picked = page.evaluate("""() => {
        const rows = Array.from(document.querySelectorAll('#accountsTable tbody tr'));
        const row = rows.find(r => !r.querySelector('.tag.owner') && r.querySelector('[data-acct]'));
        if (!row) return '';
        row.querySelector('[data-acct]').click();
        return row.innerText.trim().split('\\n')[0];
    }""")
    check("找到一行不是站长的账号", bool(picked), picked)
    page.wait_for_timeout(400)
    check("账号弹窗里有重置密码", page.locator("[data-pwd]").count() == 1)
    page.click("[data-pwd]")
    page.wait_for_timeout(350)
    check("弹窗里没有让人填密码的框",
          page.locator("#pwdNew").count() == 0, page.inner_text("#overlayBody")[:160])
    check("说明密码由平台生成",
          "平台生成" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:200])
    check("并说明会话与令牌会失效",
          "令牌" in page.inner_text("#overlayBody"), page.inner_text("#overlayBody")[:200])
    page.click("#pwdGo")
    page.wait_for_timeout(1200)
    got = page.evaluate("""() => {
        const box = document.getElementById('pwdOut');
        return {value: box ? box.value : '', readOnly: box ? box.readOnly : false,
                note: document.getElementById('overlayBody').innerText};
    }""")
    check("结果页给出了一串新密码", len(got["value"] or "") >= 12, got["value"])
    check("那串只读，不能误改", got["readOnly"] is True)
    check("说清了只显示这一次", "一次" in (got["note"] or ""), (got["note"] or "")[:160])
    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)

    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)


def close_overlays(page, times=4):
    """弹窗是叠放的：一层层关掉，别把下面那层留在屏幕上挡点击。"""
    for _ in range(times):
        if page.evaluate("() => document.getElementById('overlay').hidden"):
            return
        close = page.locator("#overlayClose")
        if close.count():
            try:
                close.click(timeout=2000)
            except Exception:
                page.keyboard.press("Escape")
        else:
            page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    page.evaluate("() => { document.getElementById('overlay').hidden = true; }")


def events_table(page):
    print("== 活动管理：首列点开详情 ==")
    page.goto(BASE + "/panel#/all-events")
    page.wait_for_timeout(420)
    rows = page.evaluate("""() => Array.from(
        document.querySelectorAll('#allEventsTable tbody tr'))
        .map(tr => ({
            cells: tr.children.length,
            hasDetail: !!tr.querySelector('[data-event-detail]'),
            seq: (tr.querySelector('.num') || {}).textContent || ''
        }))""")
    check("每行都有打开详情的按钮", rows and all(r["hasDetail"] for r in rows), rows)
    check("活动号那一列有数字", rows and all(r["seq"].strip().isdigit() for r in rows), rows)
    # 表里不再塞按钮：高权限动作都在详情弹窗里
    buttons = page.locator("#allEventsTable tbody .btn").count()
    check("表里不塞操作按钮", buttons == 0, buttons)
    page.click("#allEventsTable tbody [data-event-detail]")
    page.wait_for_timeout(300)
    acts = page.evaluate("""() => Array.from(
        document.querySelectorAll('#overlayBody .detail-actions .btn'))
        .map(b => b.textContent.trim())""")
    check("详情里列出高权限动作", len(acts) >= 4, acts)
    check("详情里有转移负责人", any("转移负责人" in a for a in acts), acts)
    close_overlays(page)


def preset_favorite_flash(page):
    """在预设广场点收藏，整页不许先清空再填回来。"""
    print("== 收藏预设：页面不许闪白 ==")
    page.goto(BASE + "/panel#/templates")
    page.wait_for_timeout(900)
    before = page.evaluate(
        "() => document.querySelectorAll('#presetSquare .preset-card').length")
    check("广场里有预设卡片", before >= 1, before)
    page.evaluate("""() => {
        window.__sawEmpty = false;
        const box = document.getElementById('presetSquare');
        const obs = new MutationObserver(() => {
            if (!box.querySelector('.preset-card')) window.__sawEmpty = true;
        });
        obs.observe(box, {childList: true, subtree: true});
        window.__flashObs = obs;
    }""")
    page.click("#presetSquare .preset-star")
    page.wait_for_timeout(1600)
    state = page.evaluate("""() => ({
        empty: window.__sawEmpty,
        cards: document.querySelectorAll('#presetSquare .preset-card').length})""")
    page.evaluate("() => { if (window.__flashObs) window.__flashObs.disconnect(); }")
    check("收藏过程中没有出现空白", state["empty"] is False, state)
    check("收藏之后卡片还在", state["cards"] >= 1, state)
    # 复原：再点一次取消收藏
    page.click("#presetSquare .preset-star")
    page.wait_for_timeout(900)


def create_page(browser):
    print("== creating an activity from the console ==")
    ctx, page = new_page(browser, 1440, 900)
    try:
        ui_login(page)
        # 收藏的预设排在最前，星标只读
        owner = owner_opener()
        call(owner, "/api/panel/preset/favorite", {"id": "nwuca.salon", "on": True})
        page.goto(BASE + "/panel#/events")
        page.wait_for_timeout(500)
        page.click("#newEventBtn")
        page.wait_for_timeout(900)
        fav = page.evaluate("""() => {
            const tiles = Array.from(document.querySelectorAll('#kindGrid .kind-tile'));
            const starred = tiles.map(t => !!t.querySelector('.kind-fav'));
            const dot = document.querySelector('#kindGrid .kind-tile .kind-fav');
            return {count: tiles.length,
                    firstStarred: starred[0] === true,
                    oneOnly: starred.filter(Boolean).length === 1,
                    pointer: dot ? getComputedStyle(dot).pointerEvents : '',
                    inside: dot ? !!dot.closest('button.kind-tile') : false};
        }""")
        check("收藏的预设排在最前", fav["firstStarred"] and fav["oneOnly"], fav)
        check("星标只读（不接收点击）", fav["pointer"] == "none" and fav["inside"], fav)
        call(owner, "/api/panel/preset/favorite", {"id": "nwuca.salon", "on": False})
        page.goto(BASE + "/panel#/events")
        page.wait_for_timeout(420)
        check("the button says 创建活动",
              page.locator("#newEventBtn").inner_text().strip() == "创建活动")
        page.click("#newEventBtn")
        page.wait_for_timeout(700)
        check("a template picker appears", page.locator(".kind-tile").count() >= 1)
        check("the built-in template is offered", page.evaluate(
            "() => !!document.querySelector('[data-template=\\'nwuca.salon\\']')"))
        page.screenshot(path="tmp/ui/kind-picker.png")

        page.click("[data-template='nwuca.salon']")
        page.wait_for_timeout(600)
        check("choosing one renders its form", page.locator("#neTitle").is_visible())
        check("the schema drew its fields",
              page.locator("[data-kind-field]").count() >= 3,
              page.locator("[data-kind-field]").count())
        check("a required field is marked",
              page.locator(".kind-field .req, .field .req").count() >= 1)
        check("a required choice starts empty, so it has to be answered",
              page.evaluate(
                  "() => document.getElementById('kf_pool_mode').value") == "",
              page.evaluate("() => document.getElementById('kf_pool_mode').value"))
        page.screenshot(path="tmp/ui/kind-form.png")

        print("== refusals happen in place ==")
        page.fill("#neTitle", "")
        page.click("#kindCreate")
        page.wait_for_timeout(400)
        check("a missing name is refused", page.locator("#createErr").inner_text().strip() != "")

        print("== a real creation ==")
        page.fill("#neTitle", "界面创建的活动")
        page.select_option("#kf_pool_mode", "auto")
        with page.context.expect_page() as opened:
            page.click("#kindCreate")
        tab = opened.value
        tab.wait_for_url("**/admin", timeout=25000)
        check("新活动在另一个标签页打开", "/admin" in tab.url, tab.url)
        made = tab.url
        print("   created:", made)
        page.wait_for_timeout(700)
        check("主站留在「我的活动」", page.url.endswith("#/events"), page.url)
        tab.close()

        print("== the menu archives it again ==")
        page.goto(BASE + "/panel#/events")
        page.wait_for_timeout(600)
        newest = page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('#ownedEvents .event-card'));
            const card = cards[cards.length - 1];
            return card ? card.querySelector('.card-menu-btn').getAttribute('data-menu') : null;
        }""")
        check("the new activity is on the page", bool(newest), newest)
        if newest:
            page.click('[data-menu="' + newest + '"]')
            page.wait_for_timeout(300)
            check("the menu offers 归档", page.locator("[data-archive]").count() == 1)
            page.click("[data-archive]")
            page.wait_for_timeout(600)
            # 归档自己的活动不要密码：一句确认就够（密码留给真正不可逆的删除）
            check("归档只问一句，不弹密码框",
                  page.locator("#confirmGo").count() == 1
                  and page.locator("#sudoPass").count() == 0)
            entered = page.inner_text("#overlayBody")
            check("and names the action", "归档" in entered, entered[:60])
            check("and says what it will do", "确认归档？" in entered, entered[:120])
            page.click("#confirmGo")
            page.wait_for_timeout(900)
            left = page.evaluate("""() => Array.from(
                document.querySelectorAll('#ownedEvents .card-menu-btn'))
                .map(el => el.getAttribute('data-menu'))""")
            check("and the activity is gone from the list", newest not in left, left)

        sudo_page(page)
    finally:
        ctx.close()


def leave_guard_page(browser):
    """离开创建页：地址先退回原处再问，敲进去的东西一样都不能丢。"""
    print("== 未保存的创建页怎么走 ==")
    opener = owner_opener()
    made = []
    for i in range(2):
        user = "guard%d%s" % (i, TAG)
        call(opener, "/api/panel/register",
             {"username": user, "name": "守卫%d" % i, "password": "guard-pass-1", "note": ""})
        made.append(user)
    for a in call(opener, "/api/panel/accounts?limit=200")["accounts"]:
        if a["username"] in made and a["status"] == "pending":
            call(opener, "/api/panel/account/decide", {"id": a["id"], "action": "approve"})
    ctx, page = new_page(browser, 1280, 860)
    try:
        ui_login(page)

        print("== 选人弹窗：单选（转移负责人） ==")
        page.goto(BASE + "/panel#/all-events")
        page.wait_for_timeout(900)
        seq = page.evaluate("""() => {
            const btn = document.querySelector('#allEventsTable [data-event-detail]');
            return btn ? btn.getAttribute('data-event-detail') : null;
        }""")
        check("活动管理里有自己的一场活动", bool(seq), seq)
        if seq:
            page.click('[data-event-detail="%s"]' % seq)
            page.wait_for_timeout(500)
            pick = page.evaluate("""() => {
                const btns = Array.from(
                    document.querySelectorAll('#overlayBody [data-detail-act]'));
                const hit = btns.find(b => b.textContent.trim() === '转移负责人');
                return hit ? hit.getAttribute('data-detail-act') : null;
            }""")
            check("详情里有「转移负责人」", pick is not None, pick)
            page.click('[data-detail-act="%s"]' % pick)
            page.wait_for_selector(".pp-back .pp-tile", timeout=8000)
            page.wait_for_timeout(400)
            check("单选也有取消与完成两个键",
                  page.locator(".pp-cancel").count() == 1
                  and page.locator(".pp-done").count() == 1)
            check("没点人时「完成」是灰的、点不动",
                  page.locator(".pp-done").is_disabled())
            page.click(".pp-tile")
            page.wait_for_timeout(250)
            check("点一下只是选中，不会直接把事办了",
                  page.locator(".pp-back").count() == 1
                  and page.locator(".pp-tile.on").count() == 1)
            check("选过人之后「完成」才亮",
                  page.locator(".pp-done").is_enabled())
            page.locator(".pp-tile").nth(1).click()
            page.wait_for_timeout(250)
            check("点另一个人就换过去，先前那个自动让位",
                  page.locator(".pp-tile.on").count() == 1
                  and "on" in (page.locator(".pp-tile").nth(1).get_attribute("class") or ""))
            page.click(".pp-cancel")
            page.wait_for_timeout(300)
            check("取消之后既没换人，也没多问一句",
                  page.locator(".pp-back").count() == 0
                  and "换成" not in page.inner_text("#overlayBody"))
            page.click('[data-detail-act="%s"]' % pick)
            page.wait_for_selector(".pp-back .pp-tile", timeout=8000)
            page.wait_for_timeout(300)
            check("取消回来是干净的一张：没有残留的选人",
                  page.locator(".pp-tile.on").count() == 0)
            page.click(".pp-tile")
            page.click(".pp-done")
            page.wait_for_timeout(400)
            check("按「完成」才问一句要不要换", page.locator(".pp-back").count() == 0
                  and "换成" in page.inner_text("#overlayBody"),
                  page.inner_text("#overlayBody")[:80])
            page.click('#overlayBody [data-close-overlay="1"]')
            page.wait_for_timeout(300)
            # 取消不改变对象：确认框收掉，回到原来那张详情
            detail_back = page.evaluate("""() => ({
                open: !document.getElementById('overlay').hidden,
                body: document.getElementById('overlayBody').textContent})""")
            check("确认框里点取消也是一个字都没改，回到详情",
                  detail_back["open"] and "活动号" in detail_back["body"]
                  and "换成" not in detail_back["body"], detail_back["body"][:80])

        page.goto(BASE + "/panel#/new-event/nwuca.salon")
        page.wait_for_timeout(800)
        page.fill("#neTitle", "半天的活动")

        print("== 选人弹窗（邀请成员） ==")
        page.click("#invitePick")
        page.wait_for_selector(".pp-back .pp-find", timeout=8000)
        page.wait_for_timeout(600)
        check("一打开就有人可选，不用先打字",
              page.locator(".pp-tile").count() >= 2, page.locator(".pp-tile").count())
        shown = page.inner_text(".pp-card")
        check("只写显示名，不写登录名",
              "guard" not in shown and "user0" not in shown, shown[:120])
        check("搜索框没有标签，只有占位词",
              page.locator(".pp-card label").count() == 0
              and page.get_attribute(".pp-find", "placeholder") == "搜索用户")
        check("标题下面没有分隔线", page.evaluate(
            "() => getComputedStyle(document.querySelector('.pp-head'))"
            ".borderBottomWidth") == "0px")
        gap = page.evaluate("""() => {
            const a = document.querySelector('.pp-find').getBoundingClientRect();
            const b = document.querySelector('.pp-list').getBoundingClientRect();
            return Math.round(b.top - a.bottom);
        }""")
        check("搜索框与名单之间留着间距", gap >= 8, gap)
        wide = page.locator(".pp-list").bounding_box()
        check("名单是宽的一屏（三行、多列）",
              wide["width"] > 560 and wide["height"] >= 200,
              (wide["width"], wide["height"]))
        cols = page.evaluate(
            "() => getComputedStyle(document.querySelector('.pp-grid'))"
            ".gridTemplateColumns.split(' ').length")
        check("一屏不止三列", cols >= 5, cols)
        check("版面不会因为滚动条忽有忽无而左右挪",
              page.evaluate("() => getComputedStyle("
                            "document.querySelector('.content')).scrollbarGutter")
              .startswith("stable"))

        print("== 点窗外不关；ESC 看焦点 ==")
        page.mouse.click(60, 60)
        page.wait_for_timeout(300)
        check("点窗外关不掉这个弹窗", page.locator(".pp-back").count() == 1)
        page.fill(".pp-find", "守卫")
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        check("焦点在搜索框里：ESC 只清空，绝不退出",
              page.locator(".pp-back").count() == 1
              and page.locator(".pp-find").input_value() == "")
        check("清空之后名单自己回来了",
              page.locator(".pp-tile").count() >= 2, page.locator(".pp-tile").count())
        page.locator(".pp-tile").first.click()
        page.wait_for_timeout(250)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("焦点挪到方块上，ESC 才关", page.locator(".pp-back").count() == 0)
        page.click("#invitePick")
        page.wait_for_selector(".pp-back .pp-find", timeout=8000)
        page.wait_for_timeout(500)
        page.fill(".pp-find", "守卫")
        page.wait_for_timeout(400)
        check("筛完弹窗不变高变矮",
              abs(page.locator(".pp-list").bounding_box()["height"] - wide["height"]) < 1,
              (wide["height"], page.locator(".pp-list").bounding_box()["height"]))
        page.click(".pp-tile")
        page.wait_for_timeout(200)
        check("点过是选中，不是直接关掉", page.locator(".pp-tile.on").count() == 1)
        check("多选的「完成」一直是亮的", page.locator(".pp-done").is_enabled())
        page.click(".pp-cancel")
        page.wait_for_timeout(300)
        check("取消之后什么都没应用", page.locator(".pp-back").count() == 0
              and page.locator("#inviteChips .invite-chip").count() == 0)
        page.click("#invitePick")
        page.wait_for_selector(".pp-back .pp-tile", timeout=8000)
        page.wait_for_timeout(500)
        check("取消掉的那次选择也不留痕", page.locator(".pp-tile.on").count() == 0)
        page.fill(".pp-find", "守卫")
        page.wait_for_timeout(400)
        page.click(".pp-tile")
        page.click(".pp-done")
        page.wait_for_timeout(300)
        check("按「完成」才回到表单", page.locator(".pp-back").count() == 0)
        check("点过的人排到页面上", page.locator("#inviteChips .invite-chip").count() == 1)

        print("== 选过人之后再点别处 ==")
        page.goto(BASE + "/panel#/new-event/nwuca.salon")
        page.wait_for_timeout(800)
        page.fill("#neTitle", "半天的活动")
        page.click("#invitePick")
        page.wait_for_selector(".pp-back .pp-tile", timeout=8000)
        page.wait_for_timeout(400)
        check("再打开时上次选的人还亮着", page.locator(".pp-tile.on").count() == 1,
              page.locator(".pp-tile.on").count())
        page.click(".pp-tile:not(.on)")
        page.click(".pp-done")
        page.wait_for_timeout(300)
        check("再选一个就多一个人", page.locator("#inviteChips .invite-chip").count() == 2,
              page.locator("#inviteChips .invite-chip").count())
        page.click('[data-nav="overview"]')
        page.wait_for_timeout(600)
        check("先问一句，不先跳走",
              page.locator("#overlay").is_visible()
              and "离开创建活动" in page.inner_text("#overlayTitle"),
              page.inner_text("#overlayTitle"))
        check("地址还在表单那一页", page.url.endswith("#/new-event/nwuca.salon"), page.url)
        check("敲进去的名字还在",
              page.locator("#neTitle").input_value() == "半天的活动",
              page.locator("#neTitle").input_value())
        page.click('#overlayBody [data-close-overlay="1"]')
        page.wait_for_timeout(300)
        check("继续编辑：弹窗关上，原地不动",
              page.locator("#overlay").is_hidden()
              and page.url.endswith("#/new-event/nwuca.salon"), page.url)
        check("选的人也没丢", page.locator("#inviteChips .invite-chip").count() == 2,
              page.locator("#inviteChips .invite-chip").count())

        print("== 丢弃之后才真的走 ==")
        page.click('[data-nav="overview"]')
        page.wait_for_timeout(600)
        page.click("#draftDrop")
        page.wait_for_timeout(900)
        check("丢弃之后到了总览",
              page.url.endswith("#/overview") and page.locator("#overlay").is_hidden(),
              page.url)
        page.goto(BASE + "/panel#/new-event/nwuca.salon")
        page.wait_for_timeout(900)
        check("再回来是一张空表单，草稿没有留下",
              page.locator("#neTitle").input_value() != "半天的活动",
              page.locator("#neTitle").input_value())
        check("这一段没有 javascript 错误", not page._errors,
              "; ".join(page._errors)[:200])
    finally:
        ctx.close()


def module_refresh_page(browser):
    """停用/启用模块之后，主站的活动卡片自己更新，不用整页刷新。"""
    print("== 模块开关：卡片自己更新 ==")
    owner = owner_opener()
    made = call(owner, "/api/panel/events/create",
                {"template": "nwuca.salon", "title": "模块开关测试"})
    check("先建一场用来观察的活动", bool(made and made.get("event")), made)
    module_id = (made or {}).get("event", {}).get("kind") or "nwuca.lottery"
    ctx, page = new_page(browser, 1280, 860)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/modules")
        page.wait_for_timeout(800)
        # 停用（第一次要密码）
        page.click('#moduleTable [data-module-toggle="%s"]' % module_id)
        page.wait_for_selector("#sudoPass", timeout=8000)
        page.fill("#sudoPass", PASSWORD)
        page.click("#sudoEnter")
        page.wait_for_timeout(1600)
        check("停用之后模块表自己更新",
              page.locator('#moduleTable [data-module-toggle="%s"][data-on="1"]'
                           % module_id).count() == 1)
        page.click('[data-nav="events"]')
        page.wait_for_timeout(1200)
        check("活动卡片上出现「不可用」",
              page.locator("#ownedEvents .badge-off").count() >= 1,
              page.locator("#ownedEvents .badge-off").count())
        # 启用（已经提权，只多问一句）；全程不刷新页面
        page.click('[data-nav="modules"]')
        page.wait_for_timeout(800)
        page.click('#moduleTable [data-module-toggle="%s"]' % module_id)
        page.wait_for_timeout(500)
        if page.locator("#confirmGo").count():
            page.click("#confirmGo")
        page.wait_for_timeout(1400)
        page.click('[data-nav="events"]')
        page.wait_for_timeout(1200)
        check("不刷新页面，胶囊就自己消失了",
              page.locator("#ownedEvents .badge-off").count() == 0,
              page.locator("#ownedEvents .badge-off").count())
    finally:
        # 这一套件后面的用例还要用这个模块，无论成败都打开
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        call(owner, "/api/panel/module/toggle", {"id": module_id, "enabled": True})
        ctx.close()


def loading_states_page(browser):
    """列表的中间态：切栏的那一个 tick 里只能说「正在读取…」。"""
    print("== 列表的加载中间态 ==")
    ctx, page = new_page(browser, 1440, 900)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/roster-admin")
        page.wait_for_timeout(1200)
        # 同一个 tick 里点完就读：这时请求还没回来，不能已经摆出「还没有…」
        tick = page.evaluate("""() => {
            document.querySelector('#adminListScope [data-archived="1"]').click();
            return document.querySelector('#adminRosterTable tbody').textContent.trim();
        }""")
        check("切到已归档的那一 tick 是正在读取", tick == "正在读取…", tick)
        page.wait_for_timeout(900)
        body = page.inner_text("#adminRosterTable tbody").strip()
        check("读完之后才给结论", body and "正在读取" not in body, body[:60])
        back = page.evaluate("""() => {
            document.querySelector('#adminListScope [data-archived="0"]').click();
            return document.querySelector('#adminSignupTable tbody').textContent.trim();
        }""")
        check("切回当前的那一 tick 也是正在读取", back == "正在读取…", back)
        page.wait_for_timeout(900)
        check("读完才说有没有报名",
              "正在读取" not in page.inner_text("#adminSignupTable tbody"),
              page.inner_text("#adminSignupTable tbody")[:60])
    finally:
        ctx.close()


def roster_admin_page(browser):
    """名单管理：超管看全站的名单与报名，详情里能归档、转交与彻底删除。"""
    print("== 名单管理 ==")
    owner = owner_opener()
    made = call(owner, "/api/panel/roster/create", {"name": "名单管理测试"})
    rid = (made or {}).get("id")
    check("先造一份名单", bool(rid), made)
    ctx, page = new_page(browser, 1440, 900)
    try:
        ui_login(page)
        check("侧栏里有名单管理",
              page.locator('[data-nav="roster-admin"]').count() == 1)
        page.goto(BASE + "/panel#/roster-admin")
        page.wait_for_timeout(900)
        check("两个表都在",
              page.locator("#adminRosterTable").count() == 1
              and page.locator("#adminSignupTable").count() == 1)
        heads = page.eval_on_selector_all(
            "#adminRosterTable thead th", "els => els.map(e => e.textContent.trim())")
        check("名单表是 名单 / 拥有者 / 可见性 / 人数 / 更新时间 / 操作",
              heads == ["名单", "拥有者", "可见性", "人数", "更新时间", "操作"], heads)
        check("全站的名单在表里",
              "名单管理测试" in page.inner_text("#adminRosterTable tbody"),
              page.inner_text("#adminRosterTable tbody")[:120])
        page.click("[data-admin-roster]")
        page.wait_for_timeout(500)
        body = page.inner_text("#overlayBody")
        check("详情里有转移与归档",
              "转移拥有者" in body and "归档" in body, body[:140])
        check("没归档的名单不给彻底删除", "彻底删除" not in body, body[:140])
        check("也说得出拥有者是谁", "拥有者" in body, body[:80])
        # 点遮罩关掉：和活动详情一样，这类只读弹窗关掉不丢东西
        page.click("#overlay", position={"x": 20, "y": 20})
        page.wait_for_timeout(300)
        check("点外面能关掉详情", page.locator("#overlay").is_hidden())
        # 转移不挪动这份名单：做完详情还在（确认框与密码提示都叠在它上面）
        if WITH_ACCOUNTS:
            page.click("[data-admin-roster]")
            page.wait_for_timeout(400)
            page.click('[data-list-act="1"]')
            page.wait_for_timeout(600)
            page.click(".pp-list [data-pp-id]")
            page.wait_for_timeout(200)
            page.click(".pp-done")
            page.wait_for_timeout(600)
            if page.locator("#sudoPass").count():
                page.fill("#sudoPass", PASSWORD)
                page.click("#sudoEnter")
                page.wait_for_timeout(1600)
            elif page.locator("#confirmGo").count():
                page.click("#confirmGo")
                page.wait_for_timeout(1600)
            check("转移之后详情还开着", not page.locator("#overlay").is_hidden(),
                  page.inner_text("#overlayTitle"))
        # 归档：名单上还挂着报名时补问一句「一并归档？」，确认之后一起收
        call(owner, "/api/panel/signup/create",
             {"title": "链接到名单的报名", "roster_id": rid})
        if page.locator("#overlay").is_hidden():
            page.click("[data-admin-roster]")
            page.wait_for_timeout(400)
        page.click('[data-list-act="2"]')      # 打开 / 转移 / 归档
        page.wait_for_timeout(600)
        if page.locator("#sudoPass").count():
            page.fill("#sudoPass", PASSWORD)
            page.click("#sudoEnter")
            page.wait_for_timeout(1600)
        elif page.locator("#confirmGo").count():
            page.click("#confirmGo")
            page.wait_for_timeout(1200)
        check("名单上还挂着报名时补问一句",
              page.locator("#cascadeGo").count() == 1
              and "报名链接到这份名单" in page.inner_text("#overlayBody"),
              page.inner_text("#overlayBody")[:120])
        check("这句话给的是取消与全部归档",
              page.inner_text("#cascadeGo").strip() == "全部归档"
              and "取消" in page.inner_text("#overlayBody"), page.inner_text("#overlayBody")[:120])
        page.click("#cascadeGo")
        page.wait_for_timeout(1600)
        check("归档之后弹窗收掉了", page.locator("#overlay").is_hidden())
        check("当前那一栏里没有它了",
              "名单管理测试" not in page.inner_text("#adminRosterTable tbody"),
              page.inner_text("#adminRosterTable tbody")[:120])
        check("链接到它的报名也一起收走了",
              "链接到名单的报名" not in page.inner_text("#adminSignupTable tbody"),
              page.inner_text("#adminSignupTable tbody")[:120])
        # 已归档那一栏：名单与报名都在，超管能取消归档也能彻底删除
        page.click('#adminListScope [data-archived="1"]')
        page.wait_for_timeout(900)
        check("已归档那一栏里有名单",
              "名单管理测试" in page.inner_text("#adminRosterTable tbody"),
              page.inner_text("#adminRosterTable tbody")[:120])
        check("已归档那一栏里也有报名",
              "链接到名单的报名" in page.inner_text("#adminSignupTable tbody"),
              page.inner_text("#adminSignupTable tbody")[:120])
        check("那一栏的时间列是归档时间",
              page.inner_text("#adminRosterTable thead tr th:nth-child(5)") == "归档时间",
              page.inner_text("#adminRosterTable thead tr th:nth-child(5)"))
        page.click("[data-admin-roster]")
        page.wait_for_timeout(500)
        body = page.inner_text("#overlayBody")
        check("归档的详情里有取消归档与彻底删除",
              "取消归档" in body and "彻底删除" in body, body[:140])
        check("归档的详情里没有转移", "转移拥有者" not in body, body[:140])
        page.click('[data-list-act="1"]')      # 取消归档 / 彻底删除
        page.wait_for_timeout(600)
        if page.locator("#sudoPass").count():
            page.fill("#sudoPass", PASSWORD)
            page.click("#sudoEnter")
            page.wait_for_timeout(1800)
        elif page.locator("#confirmGo").count():
            page.click("#confirmGo")
            page.wait_for_timeout(1600)
        # 报名还指着它，所以这一步只得到一句说明，名单还在
        check("还有报名指着时不给彻底删除",
              "名单管理测试" in page.inner_text("#adminRosterTable tbody"),
              page.inner_text("#adminRosterTable tbody")[:120])
        if not page.locator("#overlay").is_hidden():
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        # 先把报名彻底删掉，再删名单
        page.click("[data-admin-signup]")
        page.wait_for_timeout(500)
        page.click('[data-list-act="1"]')      # 取消归档 / 彻底删除
        page.wait_for_timeout(600)
        if page.locator("#sudoPass").count():
            page.fill("#sudoPass", PASSWORD)
            page.click("#sudoEnter")
            page.wait_for_timeout(1800)
        elif page.locator("#confirmGo").count():
            page.click("#confirmGo")
            page.wait_for_timeout(1600)
        check("报名彻底删除之后那一栏里没有它了",
              "链接到名单的报名" not in page.inner_text("#adminSignupTable tbody"),
              page.inner_text("#adminSignupTable tbody")[:120])
        page.click("[data-admin-roster]")
        page.wait_for_timeout(500)
        page.click('[data-list-act="1"]')
        page.wait_for_timeout(600)
        if page.locator("#sudoPass").count():
            page.fill("#sudoPass", PASSWORD)
            page.click("#sudoEnter")
            page.wait_for_timeout(1800)
        elif page.locator("#confirmGo").count():
            page.click("#confirmGo")
            page.wait_for_timeout(1600)
        check("彻底删除之后弹窗收掉了", page.locator("#overlay").is_hidden())
        check("已归档那一栏里也没有名单了",
              "名单管理测试" not in page.inner_text("#adminRosterTable tbody"),
              page.inner_text("#adminRosterTable tbody")[:120])
    finally:
        ctx.close()
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        for flag in ("", "&archived=1"):
            for row in call(owner, "/api/panel/rosters?scope=all&limit=100" + flag)["rosters"]:
                if row["name"] == "名单管理测试":
                    drop_roster(owner, row["id"])


def module_install_page(browser):
    """模块管理：导入一个申请了高危能力的模块，装之前先把话说清楚。"""
    print("== 导入模块：高危提示在装之前 ==")
    probe_id = "nwuca.uiprobe"
    box = os.path.join(ROOT_DIR, "tmp", "t-module-import")
    shutil.rmtree(box, ignore_errors=True)
    os.makedirs(box, exist_ok=True)
    zip_path = os.path.join(box, "uiprobe.zip")
    ctx = page = None
    owner = owner_opener()
    try:
        with zipfile.ZipFile(zip_path, "w") as zf:
            # 压缩包里多包一层目录也算数
            zf.writestr("uiprobe/module.json", json.dumps({
                "id": probe_id, "name": "界面导入测试", "version": "2.0",
                "author": "panel_ui_test",
                "features": {"shortlink": {}, "super.anyshortlink": True}}))
            zf.writestr("uiprobe/doc.md", "# 界面导入测试\n")
        ctx, page = new_page(browser, 1440, 900)
        ui_login(page)
        page.goto(BASE + "/panel#/modules")
        page.wait_for_timeout(900)
        check("模块管理页上有导入模块",
              page.inner_text("#moduleInstallBtn").strip() == "导入模块")
        # 文件选择框只按扩展名筛一次：把 MIME 也写进 accept，弹出来的会是
        # 「自定义文件 (*.zip; *.zip)」那种重复的过滤器
        check("文件选择框只筛 .zip 一次",
              page.get_attribute("#moduleFile", "accept") == ".zip",
              page.get_attribute("#moduleFile", "accept"))
        page.set_input_files("#moduleFile", zip_path)
        page.wait_for_timeout(1200)
        body = page.inner_text("#overlayBody")
        check("装之前先把模块说清楚",
              "界面导入测试" in body and probe_id in body and "2.0" in body, body[:160])
        check("高危那句单独用红字点题", "模块申请以下高危权限" in body, body[:200])
        check("高危能力带着一句后果",
              "super.anyshortlink" in body and "短链接" in body, body[:260])
        check("普通能力也一并列出来", "申请的能力" in body and "shortlink" in body,
              body[:200])
        page.click('[data-close-overlay="1"]')
        page.wait_for_timeout(300)
        check("取消就什么都不装",
              probe_id not in page.inner_text("#moduleTable tbody"),
              page.inner_text("#moduleTable tbody")[:120])
        # 再来一次，这次真装：没提权时先要密码
        page.set_input_files("#moduleFile", zip_path)
        page.wait_for_timeout(1200)
        page.click("#moduleInstallGo")
        page.wait_for_timeout(700)
        check("装模块要再输一次密码", page.locator("#sudoPass").count() == 1)
        page.fill("#sudoPass", PASSWORD)
        page.click("#sudoEnter")
        page.wait_for_timeout(1800)
        row = '#moduleTable tbody'
        text = page.inner_text(row)
        check("装好之后列表里有它", probe_id in text, text[:200])
        check("标着已导入，也带着高危胶囊",
              "已导入" in text and page.locator("#moduleTable .badge-off").count() >= 1,
              text[:200])
        check("自己装的模块有卸载按钮",
              page.locator('[data-module-remove="%s"]' % probe_id).count() == 1)
        check("平台自带的模块没有卸载按钮",
              page.locator('[data-module-remove="nwuca.lottery"]').count() == 0)
    finally:
        if ctx is not None:
            ctx.close()
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        try:
            call(owner, "/api/panel/module/remove", {"id": probe_id})
        except Exception:
            pass
        shutil.rmtree(box, ignore_errors=True)


def archived_delete_page(browser):
    """归档活动的详情：彻底删除之后弹窗收掉，列表也刷新。"""
    print("== 归档活动：彻底删除之后弹窗收掉 ==")
    owner = owner_opener()
    made = call(owner, "/api/panel/events/create",
                {"template": "nwuca.salon", "title": "要彻底删掉的活动", "params": {}})
    seq = made["event"]["seq"]
    call(owner, "/api/panel/sudo", {"password": PASSWORD})
    archived = call(owner, "/api/panel/event/force_archive", {"seq": seq})
    check("先把它归档", bool(archived and archived.get("archived")), archived)
    ctx, page = new_page(browser, 1440, 900)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/all-events")
        page.wait_for_timeout(1200)
        page.click('#archivedTable [data-event-detail="%d"]' % seq)
        page.wait_for_timeout(400)
        check("归档表的详情打得开", not page.locator("#overlay").is_hidden())
        idx = page.evaluate("""() => {
            const btns = Array.from(
                document.querySelectorAll('#overlayBody [data-detail-act]'));
            const hit = btns.find(b => b.textContent.trim() === '彻底删除');
            return hit ? hit.getAttribute('data-detail-act') : null;
        }""")
        check("详情里有彻底删除", idx is not None, idx)
        page.click('[data-detail-act="%s"]' % idx)
        page.wait_for_timeout(600)
        # 这一页的动作都要提权：没进 sudo 时，密码提示本身就当确认用
        check("删之前先要一次密码", page.locator("#sudoPass").count() == 1)
        check("提示里说清是哪个动作",
              "彻底删除归档的活动" in page.inner_text("#overlayBody"),
              page.inner_text("#overlayBody")[:120])
        page.fill("#sudoPass", PASSWORD)
        page.click("#sudoEnter")
        page.wait_for_timeout(1800)
        check("删完之后详情收掉了", page.locator("#overlay").is_hidden())
        check("归档表里也没有了",
              "要彻底删掉的活动" not in page.inner_text("#archivedTable tbody"),
              page.inner_text("#archivedTable tbody")[:120])
    finally:
        ctx.close()


def account_delete_page(browser):
    """账号弹窗里的「彻底删除」：没停用就不出现，停用未满三天是灰的并说明原因。"""
    print("== 账号弹窗：彻底删除什么时候出现 ==")
    owner = owner_opener()
    user = "dropme" + TAG
    call(owner, "/api/panel/register",
         {"username": user, "name": "待删除的人", "password": "drop-pass-1", "note": ""})
    listed = call(owner, "/api/panel/accounts?limit=200")["accounts"]
    row = [a for a in listed if a["username"] == user][0]
    call(owner, "/api/panel/account/decide", {"id": row["id"], "action": "approve"})
    ctx, page = new_page(browser, 1280, 860)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/users")
        page.wait_for_timeout(900)
        page.click('[data-acct="%d"]' % row["id"])
        page.wait_for_timeout(400)
        check("正常状态的账号看不到删除",
              page.locator("[data-acct-del]").count() == 0
              and "彻底删除" not in page.inner_text("#overlayBody"),
              page.inner_text("#overlayBody")[:120])
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        # 停用它，再看一次：按钮在，但是灰的，悬停有说明
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        call(owner, "/api/panel/account/decide", {"id": row["id"], "action": "disable"})
        page.goto(BASE + "/panel#/users")
        page.wait_for_timeout(900)
        page.click('[data-acct="%d"]' % row["id"])
        page.wait_for_timeout(400)
        locked = page.locator(".btn.is-locked")
        check("停用之后按钮出现了", locked.count() == 1)
        check("但还点不动，并写着原因",
              "停用" in (locked.first.get_attribute("data-tip") or ""),
              locked.first.get_attribute("data-tip"))
        check("灰按钮里写的是彻底删除", locked.first.inner_text().strip() == "彻底删除",
              locked.first.inner_text())
    finally:
        ctx.close()
        call(owner, "/api/panel/sudo", {"password": PASSWORD})
        for a in call(owner, "/api/panel/accounts?limit=200")["accounts"]:
            if a["username"] == user:
                call(owner, "/api/panel/account/decide",
                     {"id": a["id"], "action": "enable"})


def brand_left(page):
    """The product name's left edge: sudo mode pushes it over, it never moves otherwise."""
    return page.evaluate(
        "() => Math.round(document.querySelector('.titlebar .brand')"
        ".getBoundingClientRect().left)")


def server_page(page):
    print("== 服务器管理 ==")
    page.goto(BASE + "/panel#/server")
    page.wait_for_timeout(700)
    body = page.inner_text('[data-page="server"]')
    check("运维操作那张占位卡没有了", "还没有接入" not in body, body[:120])
    check("服务卡片上有重启与安全模式两个按钮",
          page.locator("#serviceRestartBtn").count() == 1
          and page.locator("#safeModeBtn").count() == 1)
    page.wait_for_selector("#featureTable tbody tr", timeout=15000)
    feat = page.evaluate("""() => {
        const rows = Array.from(document.querySelectorAll('#featureTable tbody tr'));
        return rows.map(tr => ({
            text: tr.innerText.trim(),
            tone: tr.querySelector('.dot')
                ? (tr.querySelector('.dot').classList.contains('ok') ? 'ok' : 'bad')
                : 'none',
        }));
    }""")
    check("服务器特性卡片列出了自动图片缩放",
          any("自动图片缩放" in r["text"] for r in feat), feat)
    check("每条特性前面都有绿点或红点",
          bool(feat) and all(r["tone"] in ("ok", "bad") for r in feat), feat)
    check("放重启与安全模式的那张卡片叫操作",
          page.locator('[data-page="server"] .card', has_text="重启服务")
              .locator("h2").first.inner_text().strip() == "操作",
          page.locator('[data-page="server"] .card', has_text="重启服务")
              .locator("h2").first.inner_text())
    check("按钮写的是重启服务与进入安全模式",
          page.inner_text("#serviceRestartBtn").strip() == "重启服务"
          and page.inner_text("#safeModeBtn").strip() == "进入安全模式",
          (page.inner_text("#serviceRestartBtn"), page.inner_text("#safeModeBtn")))
    check("卡片上没有多余的角标", page.locator("#safeModeChip").count() == 0)
    check("服务信息里没有单独的安全模式一行",
          "安全模式" not in page.inner_text("#srvInfo"), page.inner_text("#srvInfo"))

    print("== 备份卡片 ==")
    page.wait_for_timeout(600)
    card = page.evaluate("""() => ({
        state: document.getElementById('backupState').innerText,
        enabled: document.getElementById('bkEnabled').checked,
        mode: document.getElementById('bkMode').value,
        at: document.getElementById('bkAt').value,
        daily: document.getElementById('bkDaily').value,
        dav: !!document.getElementById('bkDavEnabled'),
        dir: document.getElementById('backupDir').innerText,
        switchW: Math.round(document.querySelector('#bkEnabled')
                   .closest('.switch').getBoundingClientRect().width),
        cols: getComputedStyle(document.querySelector('.as-form-grid'))
                .gridTemplateColumns.split(' ').filter(Boolean).length,
    })""")
    check("备份卡片读到了设置",
          card["enabled"] is True and card["mode"] == "daily"
          and card["at"] == "03:30" and card["daily"] == "7", card)
    check("开关画得出来（不是零宽的隐形元素）", card["switchW"] >= 36, card)
    check("表单铺成多列，不是一条一行", card["cols"] >= 2, card)
    check("写明了备份放在哪", card["dir"].endswith("backups"), card["dir"])
    check("WebDAV 那一段折在里面", card["dav"])
    check("状态行说得出最近一次备份是什么时候",
          "最近一次备份" in card["state"] or "还没有备份" in card["state"], card["state"])
    labels = page.evaluate("""() => Array.from(
        document.querySelectorAll('[data-page="server"] .as-form-grid label'))
        .map(l => l.innerText.trim()).filter(t => t)""")
    print("== 备份卡片：折叠、状态点、只留几行 ==")
    folded = page.evaluate("""() => {
        const card = document.getElementById('backupCard');
        const box = document.getElementById('backupSettings');
        return {
            open: card.classList.contains('fold-open'),
            rows: box.getBoundingClientRect().height,
            toggleText: document.getElementById('backupFold').textContent.trim(),
        };
    }""")
    check("设置一开始是收起来的", not folded["open"] and folded["rows"] < 4, folded)
    check("收起来时那颗键写着设置", folded["toggleText"] == "设置", folded["toggleText"])
    page.click("#backupFold")
    page.wait_for_timeout(500)          # 等过渡走完
    opened = page.evaluate("""() => {
        const card = document.getElementById('backupCard');
        const box = document.getElementById('backupSettings');
        return {
            open: card.classList.contains('fold-open'),
            rows: box.getBoundingClientRect().height,
            toggleText: document.getElementById('backupFold').textContent.trim(),
        };
    }""")
    check("点一下展开，而且是长出来的",
          opened["open"] and opened["rows"] > 60, opened)
    check("展开后那颗键写着收起", opened["toggleText"] == "收起", opened["toggleText"])
    page.click("#backupFold")
    page.wait_for_timeout(500)
    check("再点一下收回去",
          page.evaluate("() => !document.getElementById('backupCard')"
                        ".classList.contains('fold-open')"))

    dots = page.evaluate("""() => Array.from(
        document.querySelectorAll('#backupTable tbody tr .dot'))
        .map(el => el.className)""")
    check("每一份备份前面有一颗状态点",
          bool(dots) and all((" ok" in (" " + d) or " bad" in (" " + d)) for d in dots),
          dots)
    check("标题旁边有打开文档的圆圈问号",
          page.locator('#backupCard .card-help[data-doc="ops/backup.md"]').count() == 1)
    rows_shown = page.locator("#backupTable tbody tr").count()
    check("备份列表最多五行", rows_shown <= 5, rows_shown)

    check("备份设置的字段名挨个对得上",
          all(t in labels for t in ("自动备份", "调度方式", "触发时刻",
                                    "留存天数", "留存周数", "留存月数", "总字节上限")),
          labels)
    check("WebDAV 一段的标题",
          page.inner_text("#bkDavBox summary").strip() == "WebDAV（异地副本）",
          page.inner_text("#bkDavBox summary"))
    modes = page.evaluate("""() => Array.from(
        document.querySelectorAll('#bkMode option')).map(o => o.textContent.trim())""")
    check("调度方式只有每天与手动", modes == ["每天", "手动"], modes)

    made = page.evaluate("""() => new Promise(resolve => {
        PX.api("/backup/run", { method: "POST", body: {} })
          .then(d => resolve({ok: true, name: d.name}))
          .catch(e => resolve({ok: false, error: e.message}));
    })""")
    check("接口层面能立刻做一份", made.get("ok"), made)
    page.reload(wait_until="domcontentloaded")
    # 备份清单是页面加载后再取的：等它出来，别用固定睡眠赌时间
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#backupTable tbody tr').length > 0",
            timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(300)
    backup_name = made.get("name") or ""
    new_row = page.locator("#backupTable tbody tr").filter(has_text=backup_name)
    check("列表中刚创建的备份显示清单可读",
          bool(backup_name) and new_row.locator(
              '.dot.ok[aria-label="备份清单可读取"]').count() == 1, made)

    print("== 备份进审计日志 ==")
    page.goto(BASE + "/panel#/audit")
    page.wait_for_timeout(900)
    audit = page.evaluate("""() => Array.from(
        document.querySelectorAll('#auditTable tbody tr'))
        .map(tr => tr.innerText.replace(/\\s+/g, ' '))""")
    check("审计里有服务自己记的备份",
          any("service (1)" in row and "backup" in row for row in audit), audit[:3])
    page.goto(BASE + "/panel#/server")
    page.wait_for_timeout(500)


def sudo_page(page):
    """The elevated mode: what it looks like, what it says, and how it ends."""
    print("== sudo mode ==")
    # the archive above left this session elevated; end it and start clean
    if page.locator("#sudoChip").is_visible():
        page.click("#sudoChip")
        page.wait_for_timeout(250)
        page.click("#sudoQuit")
        page.wait_for_timeout(800)
    # a throw-away activity, so the sensitive action has nothing real to break
    owner = owner_opener()
    spare = call(owner, "/api/panel/events/create", {"template": "nwuca.salon"})
    spare_id = spare["event"]["id"]
    page.reload()
    page.wait_for_function(
        "() => document.documentElement.getAttribute('data-booted') === '1'",
        timeout=25000)
    page.wait_for_timeout(500)
    check("nothing is elevated to begin with", not page.locator("#sudoChip").is_visible())
    rest_left = brand_left(page)
    card = '.page[data-page=events] [data-menu="%s"]' % spare_id
    check("the throw-away activity is on the page",
          page.locator(card).count() == 1, page.locator(card).count())

    page.click(card)
    page.wait_for_timeout(250)
    page.click(".card-menu [data-rotate]")
    page.wait_for_timeout(600)
    check("a sensitive action asks for the password",
          page.locator("#sudoPass").count() == 1)
    check("and says which action",
          "重置访问地址" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:60])
    desc_h = page.evaluate("() => { const el = document.querySelector('.sudo-prompt-desc');"
                           " return el ? Math.round(el.getBoundingClientRect().height) : 0; }")
    check("描述那块留了固定的高度", desc_h > 0, desc_h)
    check("the prompt has no header bar",
          page.evaluate("() => getComputedStyle("
                        "document.querySelector('.overlay-card .overlay-head'))"
                        ".display") == "none")
    page.click("#sudoCancel")
    page.wait_for_timeout(400)
    check("cancelling closes the dialog", page.locator("#overlay").is_hidden())
    check("cancelling does not elevate", not page.locator("#sudoChip").is_visible())
    check("and the name has not moved", brand_left(page) == rest_left)

    print("== 模块的停用/启用也要先提权 ==")
    page.goto(BASE + "/panel#/modules")
    page.wait_for_timeout(800)
    heads = page.eval_on_selector_all(
        "#moduleTable thead th", "els => els.map(e => e.textContent.trim())")
    check("表头是 模块 / 版本 / 作者 / 引用计数 / 操作",
          heads == ["模块", "版本", "作者", "引用计数", "操作"], heads)
    counts = page.eval_on_selector_all(
        "#moduleTable tbody tr td:nth-child(4)",
        "els => els.map(e => e.textContent.trim())")
    check("引用计数是单独一列，写的是数字",
          counts and all(v.isdigit() for v in counts), counts)
    page.click("#moduleTable [data-module-toggle]")
    page.wait_for_timeout(600)
    check("点停用先要一次密码", page.locator("#sudoPass").count() == 1)
    check("并且说清是哪个动作",
          "停用模块" in page.inner_text("#overlayBody"), page.inner_text("#overlayBody")[:120])
    check("确认框里只有一句确认，不铺陈说明",
          "。确认继续？" in page.inner_text("#overlayBody"),
          page.inner_text("#overlayBody")[:160])
    page.click("#sudoCancel")
    page.wait_for_timeout(400)
    check("取消之后模块照旧是开着的",
          page.locator("#moduleTable [data-on='0']").count() >= 1)
    page.goto(BASE + "/panel#/events")
    page.wait_for_timeout(500)

    page.click(card)
    page.wait_for_timeout(250)
    page.click(".card-menu [data-rotate]")
    page.wait_for_timeout(600)
    page.fill("#sudoPass", "wrong-password")
    page.click("#sudoEnter")
    page.wait_for_timeout(700)
    check("a wrong password is reported in place",
          page.locator("#sudoErr").inner_text().strip() != "",
          page.locator("#sudoErr").inner_text())
    page.fill("#sudoPass", PASSWORD)
    page.click("#sudoEnter")
    page.wait_for_function(
        "() => document.getElementById('shell').classList.contains('sudo')",
        timeout=8000)
    page.wait_for_timeout(600)
    check("the chip appears once elevated", page.locator("#sudoChip").is_visible())
    moved = brand_left(page) - rest_left
    check("the name is pushed right", moved >= 28, moved)
    check("the action went through this time",
          page.evaluate("() => document.getElementById('overlay').hidden"))

    page.reload()
    page.wait_for_function(
        "() => document.documentElement.getAttribute('data-booted') === '1'",
        timeout=25000)
    page.wait_for_timeout(500)
    check("the elevation survives a reload", page.locator("#sudoChip").is_visible())
    check("and the name is still pushed", brand_left(page) == rest_left + moved,
          (brand_left(page), rest_left + moved))

    page.click("#sudoChip")
    page.wait_for_timeout(320)
    check("the chip opens the sudo panel", page.locator(".sudo-pop").count() == 1)
    said = page.inner_text(".sudo-pop")
    check("it explains what the mode means",
          "敏感操作不会再次要求二次确认" in said, said)
    check("it warns that the next one asks again",
          "再次进入" in said, said)
    clock = page.inner_text("#sudoClock")
    check("it counts down in mm:ss", ":" in clock and "自动退出" in clock, clock)
    page.wait_for_timeout(1400)
    check("the countdown actually runs", page.inner_text("#sudoClock") != clock,
          (clock, page.inner_text("#sudoClock")))

    # while elevated, the next sensitive action must go straight through
    page.click("body")
    page.wait_for_timeout(250)
    rotated = [e for e in call(owner, "/api/panel/events")["events"]
               if e["seq"] == spare["event"]["seq"]][0]
    page.click('.page[data-page=events] [data-menu="%s"]' % rotated["id"])
    page.wait_for_timeout(250)
    check("no password prompt while elevated",
          page.locator("#sudoPass").count() == 0)
    page.click(".card-menu [data-archive]")
    page.wait_for_timeout(400)
    check("an elevated session still gets one light confirmation",
          page.locator("#confirmGo").count() == 1)
    check("and it does not ask for the password again",
          page.locator("#sudoPass").count() == 0)
    page.click("#confirmGo")
    page.wait_for_timeout(1000)
    check("the second action completes", page.locator("#overlay").is_hidden())
    left_now = page.evaluate("""() => Array.from(
        document.querySelectorAll('#ownedEvents .card-menu-btn'))
        .map(el => el.getAttribute('data-menu'))""")
    check("the throw-away activity is gone", rotated["id"] not in left_now, left_now)

    page.click("#sudoChip")
    page.wait_for_timeout(320)
    page.click("#sudoQuit")
    page.wait_for_timeout(900)
    check("退出 sudo 模式 closes the panel", page.locator(".sudo-pop").count() == 0)
    check("the chip fades out", not page.locator("#sudoChip").is_visible())
    check("the name slides back", brand_left(page) == rest_left,
          (brand_left(page), rest_left))

    page.reload()
    page.wait_for_function(
        "() => document.documentElement.getAttribute('data-booted') === '1'",
        timeout=25000)
    page.wait_for_timeout(500)
    check("leaving is remembered by the server",
          not page.locator("#sudoChip").is_visible())
    check("a clean session still works",
          page.locator(".page[data-page=events]").is_visible())

    # 进入 sudo 只能靠顶栏那个盾形标记：账户页上那张调试用的卡片已经去掉了
    page.goto(BASE + "/panel#/account")
    page.wait_for_timeout(500)
    check("账户页上没有 sudo 卡片",
          page.locator("#sudoBtn").count() == 0
          and "sudo 模式" not in page.inner_text('[data-page="account"]'),
          page.inner_text('[data-page="account"]')[:160])
    check("顶栏的标记还在", page.locator("#sudoChip").count() == 1)

    # put the instance back: the spare is archived, the token rotation stays on
    # the spare alone
    call(owner, "/api/panel/sudo", {"password": PASSWORD})
    gone = call(owner, "/api/panel/event/archive", {"seq": spare["event"]["seq"]})
    check("the spare activity is cleaned up", gone.get("ok") is True, gone)


def dialog_page(page):
    print("== 用户管理 opens a dialog per account ==")
    page.goto(BASE + "/panel#/users")
    page.wait_for_timeout(320)
    check("no approval card on the users page",
          page.locator("#reviewCard").count() == 0)
    manage = page.locator("#accountsTable button[data-acct]")
    check("each row has a 管理 button", manage.count() >= 1, manage.count())
    manage.first.click()
    page.wait_for_timeout(320)
    check("dialog opens", page.locator("#overlay").is_visible())
    page.keyboard.press("Escape")
    page.wait_for_timeout(260)
    check("escape closes it", not page.locator("#overlay").is_visible())


def audit_page(page):
    print("== audit timestamps ==")
    page.goto(BASE + "/panel#/audit")
    page.wait_for_timeout(500)
    stamps = page.evaluate("""() => Array.from(
        document.querySelectorAll('#auditTable tbody tr td:first-child'))
        .map(td => td.textContent.trim()).filter(Boolean)""")
    check("the log has rows", bool(stamps), stamps)
    check("times are precise to the second",
          bool(stamps) and all(t.count(":") == 2 for t in stamps), stamps[:3])
    actors = page.evaluate("""() => Array.from(
        document.querySelectorAll('#auditTable tbody tr td:nth-child(2)'))
        .map(td => td.textContent.trim()).filter(Boolean)""")
    check("操作人写成「名字 (编号)」",
          bool(actors) and all(re.search(r"\(\d+\)$", a) for a in actors), actors[:3])
    details = page.evaluate("""() => Array.from(
        document.querySelectorAll('#auditTable tbody tr td:nth-child(5)'))
        .map(td => td.textContent.trim()).filter(Boolean)""")
    check("说明是 key: value 的写法",
          all(":" in d for d in details), details[:3])
    widths = page.evaluate("""() => Array.from(
        document.querySelectorAll('#auditTable thead th'))
        .map(th => Math.round(th.getBoundingClientRect().width))""")
    check("时间那一列只占一个时间戳的宽度", widths and widths[0] <= 150, widths)
    check("多出来的宽度给了操作人", len(widths) >= 3 and widths[1] > widths[2], widths)
    gap = page.evaluate("""() => {
        const cell = document.querySelector('#auditTable tbody tr td:first-child');
        const next = document.querySelector('#auditTable tbody tr td:nth-child(2)');
        if (!cell || !next) return -1;
        const range = document.createRange();
        range.selectNodeContents(cell);
        return Math.round(next.getBoundingClientRect().left -
                          range.getBoundingClientRect().right);
    }""")
    check("日期不贴着操作人", gap >= 8, gap)

    print("== 离开再回来：日志是新取的 ==")
    made = call(owner_opener(), "/api/panel/roster/create", {"name": "审计刷新测试"})
    check("另开一场会话写了一条", bool(made and made.get("id")), made)
    page.click('[data-nav="overview"]')
    page.wait_for_timeout(500)
    page.click('[data-nav="audit"]')
    page.wait_for_timeout(900)
    first = page.evaluate("""() => {
        const tr = document.querySelector('#auditTable tbody tr');
        return tr ? tr.innerText.replace(/\\s+/g, ' ').trim() : '';
    }""")
    check("不用刷新页面，新写的那条就在最上面",
          "roster_create" in first and "审计刷新测试" in first, first)


def theme_page(page):
    print("== theme ==")
    page.goto(BASE + "/panel#/appearance")
    page.wait_for_timeout(260)
    page.evaluate("""() => {
        window.__vt = 0;
        const original = document.startViewTransition;
        if (!original) return;
        document.startViewTransition = function (cb) {
            window.__vt += 1;
            return original.call(this, cb);
        };
    }""")
    page.click('#themeSeg button[data-theme-pref="dark"]')
    page.wait_for_timeout(120)
    check("the switch cross-fades instead of cutting",
          page.evaluate("() => window.__vt") == 1,
          page.evaluate("() => window.__vt"))
    # 选中态必须和主题一起翻过来，不能等第二次点击
    chosen = page.eval_on_selector(
        '#themeSeg button[aria-checked="true"]', "el => el.dataset.themePref")
    check("the chosen theme is marked at once", chosen == "dark", chosen)
    page.wait_for_timeout(400)
    check("dark applied",
          page.evaluate("() => document.documentElement.dataset.theme") == "dark")
    check("dark really repaints", page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor") != "rgb(245, 242, 238)")
    page.click('#themeSeg button[data-theme-pref="light"]')
    page.wait_for_timeout(420)
    check("back to light",
          page.evaluate("() => document.documentElement.dataset.theme") == "light")

    print("== 滚动条：细滑块，槽透明 ==")
    bar = page.evaluate("""() => {
        const cs = getComputedStyle(document.documentElement);
        const ta = document.getElementById('assistantText');
        return {w: cs.scrollbarWidth, c: cs.scrollbarColor,
                ta: ta ? getComputedStyle(ta).scrollbarColor : null};
    }""")
    check("滑块细、槽透明（没有箭头和白底）",
          bar["w"] == "thin" and "rgba(0, 0, 0, 0)" in bar["c"], bar)
    check("输入框里也是同一套", bar["ta"] and "rgba(0, 0, 0, 0)" in bar["ta"], bar)


def layout_page(browser):
    print("== layout at six viewports ==")
    for w, h, dpr, touch, name in [
            (1600, 900, 1, False, "desktop"),
            (1280, 800, 1, False, "laptop"),
            (1024, 768, 1, True, "tablet-landscape"),
            (768, 1024, 1, True, "tablet-portrait"),
            (844, 390, 2, True, "phone-landscape"),
            (390, 844, 2, True, "phone-portrait")]:
        ctx, page = new_page(browser, w, h, touch, touch and w < 800, dpr)
        try:
            ui_login(page)
            over = page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth")
            check("%-16s no horizontal overflow" % name, over <= 1, "%dpx" % over)
            shell_h = page.evaluate(
                "() => Math.round(document.getElementById('shell')"
                ".getBoundingClientRect().height)")
            check("%-16s shell fits the viewport" % name, abs(shell_h - h) <= 1,
                  "%d vs %d" % (shell_h, h))
            if w <= 760:
                page.click("#menuBtn")
                page.wait_for_timeout(380)
                left = page.evaluate("() => Math.round(document.getElementById('sidebar')"
                                     ".getBoundingClientRect().left)")
                check("%-16s drawer opens" % name, left >= 0, "left=%d" % left)
            check("%-16s no script error" % name, not page._errors,
                  "; ".join(page._errors)[:120])
        finally:
            ctx.close()


def touch_page(browser):
    print("== tablet: the rail must still reach sub-items ==")
    ctx, page = new_page(browser, 1024, 768, touch=True)
    try:
        ui_login(page)
        check("labels hidden on the rail",
              not page.locator('[data-nav="overview"] .nav-text').is_visible())
        check("hover titles exist", page.evaluate(
            """() => document.querySelector('[data-nav="overview"]').title !== "" """))
        check("sub-items are not in the rail",
              not page.locator('[data-nav="approval-users"]').is_visible())
        page.click('[data-toggle="approvals"]')
        page.wait_for_timeout(440)
        check("tapping the group opens the pane", has_current(page))
        check("sub-items are reachable in the pane",
              page.locator('[data-nav="approval-users"]').is_visible())
        page.click('[data-nav="approval-users"]')
        page.wait_for_timeout(440)
        check("choosing one navigates",
              page.locator('.page[data-page="approval-users"]').is_visible())
        check("the pane closes afterwards", not has_current(page))
        small = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('.nav-item, .btn, .acct, .icon-btn').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return;
                if (r.height < 36) out.push((el.className || '') + ' ' + Math.round(r.height));
            });
            return out;
        }""")
        check("no control below 36px tall", not small, "; ".join(small[:4]))
    finally:
        ctx.close()

    print("== phone: drawer and the dialog sheet ==")
    ctx, page = new_page(browser, 390, 844, touch=True, mobile=True, dpr=2)
    try:
        ui_login(page)
        page.click("#menuBtn")
        page.wait_for_timeout(420)
        page.click('[data-toggle="approvals"]')
        page.wait_for_timeout(340)
        check("group expands inside the drawer",
              page.locator('[data-nav="approval-users"]').is_visible())
        page.click('[data-nav="approval-users"]')
        page.wait_for_timeout(440)
        check("drawer closes after choosing", not has_current(page))
        page.goto(BASE + "/panel#/users")
        page.wait_for_timeout(420)
        page.locator("#accountsTable button[data-acct]").first.click()
        page.wait_for_timeout(420)
        box = page.evaluate("""() => {
            const r = document.querySelector('.overlay-card').getBoundingClientRect();
            return {w: Math.round(r.width), vw: window.innerWidth};
        }""")
        check("the dialog is a full-height sheet", box["w"] >= box["vw"] - 1, box)
    finally:
        ctx.close()

    print("== phone: the drawer keeps its groups in place ==")
    ctx, page = new_page(browser, 390, 844, touch=True, mobile=True, dpr=2)
    try:
        ui_login(page)
        page.click("#menuBtn")
        page.wait_for_timeout(420)
        page.click('[data-toggle="lists"]')
        page.wait_for_timeout(420)
        geo = page.evaluate("""() => {
            const subs = [...document.querySelectorAll('#nav-lists .nav-sub-item')];
            const head = document.querySelector('#sidebar [data-toggle="lists"]')
                .getBoundingClientRect();
            const parent = document.getElementById('nav-lists').getBoundingClientRect();
            const side = document.getElementById('sidebar').getBoundingClientRect();
            const headText = document.querySelector('[data-toggle="lists"] .nav-text')
                .getBoundingClientRect();
            const subText = subs.length
                ? document.querySelector('#nav-lists .nav-sub-item .nav-text')
                    .getBoundingClientRect() : null;
            return {headBottom: Math.round(head.bottom),
                    tops: subs.map(s => Math.round(s.getBoundingClientRect().top)),
                    parentBottom: Math.round(parent.bottom),
                    lastBottom: subs.length
                        ? Math.round(subs[subs.length - 1].getBoundingClientRect().bottom) : 0,
                    // 子项文字与父项文字对齐（父项文字前面是图标），这是"属于谁"的视觉线索
                    headTextLeft: Math.round(headText.left - side.left),
                    subTextLeft: subText ? Math.round(subText.left - side.left) : -1};
        }""")
        check("子项各占一行，不叠在一起",
              len(set(geo["tops"])) == len(geo["tops"]) == 3, geo)
        check("子项接在分组标题下面", geo["tops"] and geo["tops"][0] >= geo["headBottom"] - 2,
              geo)
        check("父容器把子项包住，不会压到下一项",
              geo["parentBottom"] >= geo["lastBottom"] - 2, geo)
        check("子项文字与父项文字对齐",
              abs(geo["subTextLeft"] - geo["headTextLeft"]) <= 1, geo)
        closed = page.evaluate("""() => {
            const g = document.getElementById('nav-approvals').getBoundingClientRect();
            const first = document.querySelector('#nav-approvals .nav-sub-item')
                .getBoundingClientRect();
            return {h: Math.round(g.height), top: Math.round(first.top),
                    boxTop: Math.round(g.top)};
        }""")
        check("收起的分组高度为零，子项不外露",
              closed["h"] <= 1 and closed["top"] >= closed["boxTop"] + closed["h"] - 1, closed)
        # 抽屉里内容比可视高度长，1px 的分隔线会被 flex 压没：每条都必须真的画出来
        seps = page.evaluate("""() => [...document.querySelectorAll('#sidebar .nav-sep')]
            .filter(s => !s.hidden)
            .map(s => { const r = s.getBoundingClientRect();
                        return {h: Math.round(r.height), w: Math.round(r.width),
                                bg: getComputedStyle(s).backgroundColor}; })""")
        check("分隔线没有一条被压成零高",
              seps and all(s["h"] >= 1 and s["w"] > 10 for s in seps), seps)
        check("分隔线都不是透明的",
              all("rgba(0, 0, 0, 0)" not in s["bg"] for s in seps), seps)
    finally:
        ctx.close()

    print("== phone: tables scroll, actions stay reachable ==")
    # 名单列表得有至少一行，后面的"首列多宽"才有得量
    owner = owner_opener()
    phone_roster = call(owner, "/api/panel/roster/import", {
        "name": "手机表格测试名单", "text": "学号,姓名\n20250021,甲一"})["id"]
    ctx, page = new_page(browser, 390, 844, touch=True, mobile=True, dpr=2)
    try:
        ui_login(page)
        page.goto(BASE + "/panel#/users")
        page.wait_for_timeout(900)
        info = page.evaluate("""() => {
            const t = document.getElementById('accountsTable');
            const wrap = t.closest('.tblwrap');
            const cell = t.querySelector('td');
            const act = t.querySelector('td.actions');
            return {wrapW: wrap.clientWidth, wrapScroll: wrap.scrollWidth,
                    firstCell: Math.round(cell.getBoundingClientRect().width),
                    actRight: Math.round(act.getBoundingClientRect().right),
                    wrapRight: Math.round(wrap.getBoundingClientRect().right),
                    actLeft: Math.round(act.getBoundingClientRect().left),
                    colCount: t.querySelectorAll('thead th').length};
        }""")
        check("表格比容器宽：可以横向滚动",
              info["wrapScroll"] > info["wrapW"], info)
        check("首列不再被压成省略号", info["firstCell"] >= 60, info)
        check("窄屏不冻结操作列，滑过去就能碰到",
              info["actLeft"] > info["wrapRight"], info)
        page.goto(BASE + "/panel#/rosters")
        page.wait_for_timeout(900)
        narrow = page.evaluate("""() => {
            const t = document.getElementById('rosterTable');
            if (!t) return null;
            const wrap = t.closest('.tblwrap');
            const first = t.querySelector('tbody td');
            return {wrapW: wrap.clientWidth, wrapSW: wrap.scrollWidth,
                    firstW: first ? Math.round(first.getBoundingClientRect().width) : 0};
        }""")
        # 要么放得下，要么真的能滑出一段；只多出几个像素的"假滚动条"是这次的毛病
        check("名单列表要么放得下，要么真的能滑",
              narrow is None or narrow["wrapSW"] <= narrow["wrapW"] + 1
              or narrow["wrapSW"] - narrow["wrapW"] >= 24, narrow)
        check("首列没有宽到把后面几列全挡住",
              narrow is None or narrow["firstW"] < narrow["wrapW"] * 0.7, narrow)

        # 首列被内容撑得很大时，钉住它等于把屏幕让给一列：这时应该放它跟着滑
        page.evaluate("""() => {
            const t = document.getElementById('rosterTable');
            const cell = t && t.querySelector('tbody td');
            if (cell) cell.textContent = 'x'.repeat(120);
            window.dispatchEvent(new Event('resize'));
        }""")
        page.wait_for_timeout(500)
        wide = page.evaluate("""() => {
            const t = document.getElementById('rosterTable');
            const wrap = t.closest('.tblwrap');
            const cell = t.querySelector('tbody td');
            return {wide: wrap.classList.contains('wide-first'),
                    share: Math.round(cell.getBoundingClientRect().width /
                                      wrap.clientWidth * 100),
                    pos: getComputedStyle(cell).position};
        }""")
        check("首列宽过大半屏时不再钉住", wide["wide"] and wide["pos"] == "static", wide)
        check("确实是这样才放手（不是白放）", wide["share"] > 60, wide)
        page.goto(BASE + "/panel#/all-events")
        page.wait_for_timeout(900)
        btn = page.evaluate("""() => {
            const b = document.querySelector('#allEventsTable tbody [data-event-detail]');
            if (!b) return null;
            const wrap = b.closest('.tblwrap');
            const r = b.getBoundingClientRect();
            return {w: Math.round(r.width),
                    scroll: wrap.scrollWidth, box: wrap.clientWidth,
                    ths: Array.from(document.querySelectorAll(
                        '#allEventsTable thead th')).map(
                            th => Math.round(th.getBoundingClientRect().width))};
        }""")
        check("活动名那一列够宽，不是一条竖条",
              btn and btn["w"] > 40, btn)
        check("放不下就横滚，不靠冻结列挤",
              btn and btn["scroll"] > btn["box"], btn)
    finally:
        ctx.close()
        try:
            call(owner, "/api/panel/sudo", {"password": PASSWORD})
            drop_roster(owner, phone_roster)
        except Exception:
            pass

    print("== phone: the date pair uses the system pickers ==")
    ctx, page = new_page(browser, 390, 844, touch=True, mobile=True, dpr=2)
    try:
        ui_login(page)
        opener = owner_opener()
        roster = call(opener, "/api/panel/roster/import", {
            "name": "手机日期测试名单", "text": "学号,姓名\n20250001,甲一"})
        made = call(opener, "/api/panel/signup/create", {
            "title": "手机日期测试", "roster_id": (roster or {}).get("id") or 0})
        sid = (made or {}).get("id")
        if not sid:
            check("a signup to edit exists", False, made)
        else:
            page.goto(BASE + "/panel#/signup/%d" % sid)
            page.wait_for_timeout(1200)
            page.click("#signupTimeCard .switch")
            page.wait_for_timeout(300)
            shapes = page.evaluate("""() => {
                const box = s => {
                    const r = document.querySelector(s).getBoundingClientRect();
                    return {w: Math.round(r.width), h: Math.round(r.height)};
                };
                return {date: box('#signupOpensDatePick'), time: box('#signupOpensTimePick'),
                        textDate: box('#signupOpensDate'), cal: box('#signupOpensPick')};
            }""")
            check("原生日期控件成了可见的整行控件",
                  shapes["date"]["w"] > 80 and shapes["date"]["h"] > 20, shapes)
            check("原生时刻控件同上",
                  shapes["time"]["w"] > 60 and shapes["time"]["h"] > 20, shapes)
            check("电脑上那两个文本框在手机上让位",
                  shapes["textDate"]["w"] == 0, shapes)
            check("日历按钮在手机上隐去", shapes["cal"]["w"] == 0, shapes)
            page.fill("#signupOpensDatePick", "2026-12-01")
            page.dispatch_event("#signupOpensDatePick", "change")
            page.fill("#signupOpensTimePick", "19:30")
            page.dispatch_event("#signupOpensTimePick", "change")
            page.wait_for_timeout(250)
            got = page.evaluate("""() => ({
                date: document.getElementById('signupOpensDate').value,
                time: document.getElementById('signupOpensTime').value,
                clear: document.getElementById('signupOpensClear').disabled})""")
            check("选完写回日期文本框", got["date"] == "2026-12-01", got)
            check("选完写回时刻文本框", got["time"] == "19:30", got)
            check("清除按钮随之可用", got["clear"] is False, got)
            call(opener, "/api/panel/sudo", {"password": PASSWORD})
            drop_signup(opener, sid)
    finally:
        ctx.close()

    print("== landscape phone: short viewport ==")
    ctx, page = new_page(browser, 844, 390, touch=True)
    try:
        ui_login(page)
        check("the content scrolls, the page does not", page.evaluate(
            """() => { const c = document.getElementById('content');
                       return c.scrollHeight > c.clientHeight; }"""))
        nav_h = page.evaluate("() => Math.round(document.getElementById('sidebar')"
                              ".getBoundingClientRect().height)")
        check("the sidebar fits the short viewport", nav_h <= 390, nav_h)
    finally:
        ctx.close()


def role_page(browser):
    print("== role matrix ==")
    owner = owner_opener()
    # 改角色需要 sudo：这一节直接调接口，先把会话提上去
    call(owner, "/api/panel/sudo", {"password": PASSWORD})
    made = {}
    for name, role in (("admin", "admin"), ("super", "superadmin")):
        user = "%s%s" % (name, TAG)
        try:
            call(owner, "/api/panel/register", {
                "username": user, "name": "角色测试 " + name,
                "password": "role-pass-1", "note": "panel_ui_test"})
        except Exception:
            pass
        accounts = call(owner, "/api/panel/accounts")["accounts"]
        target = [a for a in accounts if a["username"] == user]
        if not target:
            check("%s account created" % role, False, user)
            continue
        aid = target[0]["id"]
        call(owner, "/api/panel/account/decide", {"id": aid, "action": "approve"})
        if role == "superadmin":
            call(owner, "/api/panel/account/role", {"id": aid, "role": "superadmin"})
        made[role] = user

    for role, hidden, shown in (
            ("admin", ("users", "all-events", "server", "audit"),
             ("overview", "events", "templates")),
            ("superadmin", ("server", "audit"),
             ("overview", "users", "all-events"))):
        user = made.get(role)
        if not user:
            continue
        ctx, page = new_page(browser, 1280, 800)
        try:
            ui_login(page, user, "role-pass-1")
            nav = visible_nav(page)
            for item in shown:
                check("%-10s sees %s" % (role, item), item in nav["items"], nav["items"])
            for item in hidden:
                check("%-10s does not see %s" % (role, item), item not in nav["items"],
                      nav["items"])
            if role == "admin":
                check("admin sees no approvals group",
                      "approvals" not in nav["groups"], nav["groups"])
                page.goto(BASE + "/panel#/overview")
                page.wait_for_timeout(700)
                check("普通管理员看不到账号总数",
                      not page.locator("#statAccountsBox").is_visible())
                check("也看不到待审核申请", not page.locator("#statPendingBox").is_visible())
                check("但看得到自己的名单与报名",
                      page.locator("#statRosters").is_visible()
                      and page.locator("#statSignups").is_visible())
                # 外部资源看得见，往里放东西是站长的事
                page.goto(BASE + "/panel#/screens")
                page.wait_for_timeout(600)
                check("admin can open 外部资源",
                      page.locator('[data-page="screens"]').is_visible())
                check("admin gets no import button",
                      not page.locator("#screenPackAdd").is_visible())
                check("admin is not offered the import rules",
                      not page.locator("#screenPackRules").is_visible())
                check("admin sees no delete button",
                      page.locator(".pack-card [data-pack-del]").count() == 0)
            if role == "superadmin":
                check("superadmin sees no device stats",
                      not page.locator("#hostStats").is_visible())
            page.goto(BASE + "/panel#/server")
            page.wait_for_timeout(520)
            check("%-10s sees the refusal" % role,
                  page.locator("#denied").is_visible())
            check("%-10s keeps the refused route in the url" % role,
                  page.evaluate("() => location.hash") == "#/server",
                  page.evaluate("() => location.hash"))
            check("%-10s gets no footer line" % role,
                  not page.locator("#siteFoot").is_visible())
            check("%-10s sees no normal page content" % role, page.evaluate(
                """() => Array.from(document.querySelectorAll('.page'))
                         .every(p => p.hidden)"""))
            # 只有标题与按钮，没有别的内容；[返回上一页] 有没有取决于背后有没有页面
            check("%-10s: 只有标题与按钮" % role, page.evaluate(
                """() => {
                    const box = document.getElementById('denied');
                    const lines = box.innerText.trim().split('\\n')
                        .filter(l => l.trim());
                    const back = document.getElementById('deniedBack');
                    return lines.length === (back.hidden ? 2 : 3);
                }"""),
                  page.evaluate(
                      "() => document.getElementById('denied').innerText"))
        finally:
            ctx.close()


def denied_flow(browser):
    """切换账号 -> login?s_return -> straight back to the page that was refused."""
    print("== 切换账号 takes you back where you were going ==")
    admin = "admin" + TAG
    ctx, page = new_page(browser, 1280, 800)
    try:
        ui_login(page, admin, "role-pass-1")
        page.goto(BASE + "/panel#/server")
        page.wait_for_timeout(520)
        check("an admin cannot open 服务器管理", page.locator("#denied").is_visible())

        box = page.evaluate("""() => {
            const c = document.getElementById('content').getBoundingClientRect();
            const d = document.getElementById('denied').getBoundingClientRect();
            const t = document.querySelector('.denied-title').getBoundingClientRect();
            return {cx: c.left + c.width / 2, cy: c.top + c.height / 2,
                    tx: t.left + t.width / 2, ty: t.top + t.height / 2,
                    inPanel: d.left >= c.left - 1 && d.right <= c.right + 1};
        }""")
        check("the message is centred in the panel",
              abs(box["tx"] - box["cx"]) < 6 and abs(box["ty"] - box["cy"]) < 80, box)

        page.click("#switchAccount")
        page.wait_for_timeout(320)
        check("切换账号 asks first", page.locator("#overlay").is_visible())
        page.click("#overlay [data-close-overlay]")
        page.wait_for_timeout(260)
        check("cancel keeps you signed in, still on the refusal",
              page.locator("#denied").is_visible() and
              page.locator("#overlay").is_hidden())

        page.click("#switchAccount")
        page.wait_for_timeout(300)
        page.click("#overlay [data-logout]")
        page.wait_for_url("**/login*", timeout=20000)
        check("confirming lands on the login page",
              "/login" in page.url, page.url)
        check("and carries the way back", "s_return=server" in page.url, page.url)

        page.fill("#username", LOGIN)
        page.fill("#password", PASSWORD)
        page.click("#go")
        # the return target lives in the fragment, which a glob will not match
        page.wait_for_function("() => location.hash === '#/server'", timeout=25000)
        page.wait_for_timeout(600)
        check("logging back in returns to the wanted page",
              page.evaluate("() => location.hash") == "#/server", page.url)
        check("which the owner is allowed to see",
              page.locator('.page[data-page="server"]').is_visible())
        check("the footer is back", page.locator("#siteFoot").is_visible())
    finally:
        ctx.close()


# --------------------------------------------------------------------------- #
MOUNT = "/u/laffey/xlottery"


def booted(page):
    page.wait_for_function(
        "() => document.documentElement.getAttribute('data-booted') === '1'",
        timeout=25000)
    page.wait_for_timeout(200)


def mount_page(browser):
    """挂载前缀下的地址：登录回来不该变成 …/panel/panel。"""
    print("== 面板挂在 /u/laffey/xlottery 下时地址是对的 ==")
    ctx, page = new_page(browser, 1280, 800)
    seen = []
    page.on("request", lambda r: seen.append(r.url))
    try:
        page.goto(BASE + MOUNT + "/panel", wait_until="domcontentloaded")
        page.wait_for_selector("#loginForm", timeout=20000)
        check("未登录访问面板会去登录页",
              page.url.startswith(BASE + MOUNT + "/login"), page.url)
        page.fill("#username", LOGIN)
        page.fill("#password", PASSWORD)
        page.click("#go")
        booted(page)
        check("登录后回到 …/panel", page.url == BASE + MOUNT + "/panel", page.url)
        check("没有把挂载前缀拼重",
              not [u for u in seen if "/panel/panel" in u],
              "; ".join(u for u in seen if "/panel/panel" in u)[:200])
        check("接口走的是挂载前缀下的地址",
              not [u for u in seen if "/panel/api/panel" in u],
              "; ".join(u for u in seen if "/panel/api/panel" in u)[:200])

        print("== 带结尾斜杠、地址被拼重的旧链接都能回到正主 ==")
        for path in ("/panel/", "/panel/panel", "/panel/panel#/overview"):
            page.goto(BASE + MOUNT + path, wait_until="domcontentloaded")
            seen[:] = []          # 只数这一跳之后的请求，导航本身不算
            booted(page)
            check("%s 回到 …/panel" % path,
                  page.url.split("#")[0] == BASE + MOUNT + "/panel", page.url)
            check("%s 之后没有再拼重" % path,
                  not [u for u in seen if "/panel/panel" in u],
                  "; ".join(u for u in seen if "/panel/panel" in u)[:200])

        print("== 被拒之后回来，落在原来那一页 ==")
        page.goto(BASE + MOUNT + "/login?s_return=overview", wait_until="domcontentloaded")
        booted(page)
        check("已登录的人从登录页直接进去",
              page.url == BASE + MOUNT + "/panel#/overview", page.url)
    finally:
        ctx.close()


# --------------------------------------------------------------------------- #
def sudo_expiry(browser):
    """The deadline is real: the last seconds blink, then the mode ends itself.

    A browser clock is installed first, because waiting ten minutes for a
    countdown is not a test anybody runs twice.
    """
    print("== sudo mode expires on its own ==")
    owner = owner_opener()
    spare = call(owner, "/api/panel/events/create", {"template": "nwuca.salon"})
    card = '.page[data-page=events] [data-menu="%s"]' % spare["event"]["id"]
    ctx, page = new_page(browser)
    try:
        page.clock.install()
        ui_login(page)
        page.goto(BASE + "/panel#/events")
        page.wait_for_timeout(400)
        rest_left = brand_left(page)
        page.click(card)
        page.wait_for_timeout(250)
        page.click(".card-menu [data-rotate]")
        page.wait_for_timeout(600)
        page.fill("#sudoPass", PASSWORD)
        page.click("#sudoEnter")
        page.wait_for_function(
            "() => document.getElementById('shell').classList.contains('sudo')",
            timeout=8000)
        page.wait_for_timeout(400)
        page.click("#sudoChip")
        page.wait_for_timeout(250)
        check("the countdown starts at ten minutes",
              "10:00" in page.inner_text("#sudoClock"), page.inner_text("#sudoClock"))
        check("nothing blinks yet",
              "blink" not in (page.get_attribute("#sudoClock", "class") or ""))

        page.clock.fast_forward(598000)
        page.wait_for_timeout(60)
        check("the last seconds blink",
              "blink" in (page.get_attribute("#sudoClock", "class") or ""),
              page.inner_text("#sudoClock"))
        check("and the mode is still on", page.locator("#sudoChip").is_visible())

        page.clock.fast_forward(4000)
        page.wait_for_timeout(800)
        check("then it ends by itself", not page.locator("#sudoChip").is_visible())
        check("the panel closes with it", page.locator(".sudo-pop").count() == 0)
        check("and the name slides back", brand_left(page) == rest_left,
              (brand_left(page), rest_left))
        # the rotation gave the spare a new token, so find its card again
        rotated = [e for e in call(owner, "/api/panel/events")["events"]
                   if e["seq"] == spare["event"]["seq"]][0]
        card = '.page[data-page=events] [data-menu="%s"]' % rotated["id"]
        page.click(card)
        page.wait_for_timeout(250)
        page.click(".card-menu [data-rotate]")
        page.wait_for_timeout(600)
        check("the password is asked for again", page.locator("#sudoPass").count() == 1)
        page.click("#sudoCancel")
        page.wait_for_timeout(200)
    finally:
        ctx.close()
    # put the instance back
    call(owner, "/api/panel/sudo", {"password": PASSWORD})
    call(owner, "/api/panel/event/archive", {"seq": spare["event"]["seq"]})


def main():
    started = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            ctx, page = new_page(browser)
            try:
                # 只跑一部分时，先把这个壳子立起来（登录 + 首屏），后面各组都靠它
                if ONLY:
                    print("== 只跑：%s ==" % ", ".join(ONLY))
                    if WITH_ACCOUNTS and [g for g in ONLY if g in NEEDS_ACCOUNTS]:
                        seed_accounts()
                    ui_login(page)
                    # 运维助手先跑：它要求干净的落地状态（抽屉收着、没有弹窗），
                    # 侧栏那几段会动导航与焦点，放在后面
                    if want("assistant"):
                        assistant_page(page)
                        assistant_drawer(browser)
                    if want("docs"):
                        docs_page(browser)
                    if want("shell"):
                        routes_page(page)
                        title_row(page)
                        group_page(page)
                        nav_keys_page(page)
                        crumb_motion(browser)
                        field_keys(browser)
                        head_align(page)
                else:
                    if WITH_ACCOUNTS:
                        register_page(page)
                    login_page(page)
                    shell_page(page)
                    routes_page(page)
                    title_row(page)
                    group_page(page)
                    nav_keys_page(page)
                    crumb_motion(browser)
                    field_keys(browser)
                    head_align(page)
                if want("docs") and not ONLY:
                    docs_page(browser)
                    docs_links(browser)
                if want("assistant") and not ONLY:
                    assistant_page(page)
                    assistant_drawer(browser)
                if want("ops"):
                    screens_page(page)
                if want("data"):
                    roster_page(page)
                    signup_page(page, browser)
                    print_page(browser)
                    xlsx_page(browser)
                    qr_formats(browser)
                if want("ops"):
                    notice_page(page)
                    card_page(page)
                    create_page(browser)
                    leave_guard_page(browser)
                if want("mods"):
                    module_refresh_page(browser)
                    loading_states_page(browser)
                if want("admin"):
                    roster_admin_page(browser)
                if want("mods"):
                    module_install_page(browser)
                if want("admin"):
                    archived_delete_page(browser)
                    account_delete_page(browser)
                    # profile_page edits the signed-in account, so it only runs on a
                    # throw-away instance
                    if WITH_ACCOUNTS:
                        profile_page(page)
                    users_table(page)
                    import_page(browser)
                    org_page(page)
                    approval_queue_text(page)
                    if WITH_ACCOUNTS:
                        temp_password_page(browser)
                if want("ops"):
                    preset_favorite_flash(page)
                    events_table(page)
                    dialog_page(page)
                    theme_page(page)
                    audit_page(page)
                    server_page(page)
                    search_page(page)
                if not ONLY:
                    check("no javascript error on the owner's session", not page._errors,
                          "; ".join(page._errors)[:200])
            finally:
                ctx.close()

            if want("shell"):
                layout_page(browser)
                touch_page(browser)
            if want("ops"):
                mount_page(browser)
                sudo_expiry(browser)
            if want("admin") and WITH_ACCOUNTS:
                role_page(browser)
                denied_flow(browser)
        finally:
            browser.close()

    print()
    print("用时 %.1f 秒%s" % (time.time() - started,
                             ("（只跑了 %s）" % ", ".join(ONLY)) if ONLY else ""))
    if problems:
        print("FAILED %d check(s):" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("PANEL UI TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
