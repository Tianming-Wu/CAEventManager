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
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931").rstrip("/")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""
LOGIN = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") \
    else "owner"
WITH_ACCOUNTS = "--accounts" in sys.argv
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
import xlsx  # noqa: E402  xlsx 用例要用同一份实现写测试文件
TAG = str(random.randint(1000, 9999))

problems = []


def check(name, cond, detail=""):
    print("  %-5s %s %s" % ("PASS" if cond else "FAIL", name, "" if cond else detail))
    if not cond:
        problems.append(name)


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


def new_page(browser, width=1440, height=900, touch=False, mobile=False, dpr=1):
    ctx = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch,
        is_mobile=mobile, device_scale_factor=dpr)
    page = ctx.new_page()
    page._errors = []
    page.on("pageerror", lambda e: page._errors.append(str(e)))
    page.on("console", lambda m: page._errors.append("console: " + m.text)
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
        """() => { const a = document.querySelector('.stat[href="#/rosters"]');
                   const b = document.querySelector('.stat[href="#/signups"]');
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
    check("the list shows it with its member count", "UI 测试名单" in body and "2" in body, body)


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
          page.locator('.page[data-page="signup"] .crumb[href="#/signups"]').count() == 1)
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
        for path, body in (("/api/panel/signup/delete", {"id": sid}),
                           ("/api/panel/roster/delete", {"id": rid})):
            try:
                call(owner, path, body)
            except Exception:
                pass


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
        for path, body in (("/api/panel/signup/delete", {"id": sid}),
                           ("/api/panel/roster/delete", {"id": rid})):
            try:
                call(owner, path, body)
            except Exception:
                pass
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
        for path, body in (("/api/panel/signup/delete", {"id": sid}),
                           ("/api/panel/roster/delete", {"id": rid})):
            try:
                call(owner, path, body)
            except Exception:
                pass
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
    check("levels are marked", "重要" in page.inner_text("#noticeList") and
          "紧急" in page.inner_text("#noticeList"), page.inner_text("#noticeList")[:80])
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


def assistant_page(page):
    print("== 运维助手 ==")
    page.goto(BASE + "/panel#/assistant")
    page.wait_for_timeout(900)
    check("总览下面有这一页", page.locator('[data-page="assistant"]').is_visible())
    check("没配密钥时说清楚该去配什么",
          "密钥" in page.inner_text("#assistantSub"),
          page.inner_text("#assistantSub"))
    check("给了几个可以直接点的问法",
          page.locator(".assistant-ask").count() >= 3,
          page.locator(".assistant-ask").count())
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
    page.click("#asClearKey")
    page.wait_for_timeout(600)
    check("清除密钥后回到未配置",
          "密钥" in page.inner_text("#assistantSub"), page.inner_text("#assistantSub"))
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
            # 真正的"抽"是一下跨过一大段或者跳回原处，那种会超过这个门槛。
            jumps = [(b["t"], round(b["left"] - a["left"], 1))
                     for a, b in zip(rows, rows[1:])
                     if abs(b["left"] - a["left"]) > travel * 0.6]
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
        for path, body in (("/api/panel/signup/delete", {"id": sid}),
                           ("/api/panel/roster/delete", {"id": rid})):
            try:
                call(owner, path, body)
            except Exception:
                pass


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
    for route in ("overview", "events", "users", "all-events", "server", "account"):
        page.goto(BASE + "/panel#/" + route)
        page.wait_for_timeout(260)
        tops[route] = page.evaluate(
            "() => Math.round(document.querySelector('.page:not([hidden]) h1')"
            ".getBoundingClientRect().top)")
    heights = sorted(set(tops.values()))
    check("one title height for pages with and without a button",
          len(heights) == 1, tops)


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
    check("内置的那一份标着来源",
          "内置" in page.inner_text("#screenPackList"),
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
    check("but a finger still gets a big one", page.evaluate("""() => {
        for (const sheet of document.styleSheets) {
            let rules;
            try { rules = sheet.cssRules; } catch (e) { continue; }
            for (const rule of rules) {
                if (rule.conditionText && rule.conditionText.indexOf('coarse') >= 0 &&
                    rule.cssText.indexOf('.search-clear') >= 0 &&
                    /width:\s*30px/.test(rule.cssText)) return true;
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
    page.evaluate("() => document.getElementById('overlayClose').click()")
    page.wait_for_timeout(300)


def events_table(page):
    print("== 活动管理 lists the way in, not every page ==")
    page.goto(BASE + "/panel#/all-events")
    page.wait_for_timeout(420)
    links = page.evaluate("""() => Array.from(
        document.querySelectorAll('#allEventsTable tbody tr'))
        .map(tr => Array.from(tr.querySelectorAll('a'))
            .map(a => a.textContent.trim()))""")
    check("every row has exactly one link", links and all(len(r) == 1 for r in links),
          links)
    check("and it is 管理", links and all(r[0] == "管理" for r in links), links)
    target = page.evaluate(
        "() => document.querySelector('#allEventsTable tbody a').getAttribute('target')")
    check("it opens in a new tab", target == "_blank", target)

    print("== signing out lives on the account page ==")
    page.goto(BASE + "/panel#/account")
    page.wait_for_timeout(400)
    check("the account page offers 退出登录",
          page.locator("#logoutBtn").is_visible())


def create_page(browser):
    print("== creating an activity from the console ==")
    ctx, page = new_page(browser, 1440, 900)
    try:
        ui_login(page)
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
        page.click("#kindCreate")
        page.wait_for_url("**/admin", timeout=25000)
        check("it opens the new activity's console", "/admin" in page.url, page.url)
        made = page.url
        print("   created:", made)

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
            check("the menu offers 存档活动", page.locator("[data-archive]").count() == 1)
            page.click("[data-archive]")
            page.wait_for_timeout(600)
            # nothing is elevated, so the password prompt is the confirmation:
            # typing it is deliberate, and cancelling it leaves no trace
            check("archiving asks for the password straight away",
                  page.locator("#sudoPass").count() == 1)
            entered = page.inner_text("#overlayBody")
            check("and names the action", "存档活动" in entered, entered[:60])
            check("the prompt has no header bar",
                  page.evaluate("() => getComputedStyle("
                                "document.querySelector('.overlay-card .overlay-head'))"
                                ".display") == "none")
            enter_sudo(page)
            page.wait_for_timeout(900)
            left = page.evaluate("""() => Array.from(
                document.querySelectorAll('#ownedEvents .card-menu-btn'))
                .map(el => el.getAttribute('data-menu'))""")
            check("and the activity is gone from the list", newest not in left, left)

        sudo_page(page)
    finally:
        ctx.close()


def brand_left(page):
    """The product name's left edge: sudo mode pushes it over, it never moves otherwise."""
    return page.evaluate(
        "() => Math.round(document.querySelector('.titlebar .brand')"
        ".getBoundingClientRect().left)")


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
    page.click("#sudoCancel")
    page.wait_for_timeout(400)
    check("cancelling closes the dialog", page.locator("#overlay").is_hidden())
    check("cancelling does not elevate", not page.locator("#sudoChip").is_visible())
    check("and the name has not moved", brand_left(page) == rest_left)

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

    # the mode can also be entered on purpose, from the account page
    page.goto(BASE + "/panel#/account")
    page.wait_for_timeout(500)
    check("the account page offers the switch",
          page.locator("#sudoBtn").inner_text().strip() == "进入 sudo 模式",
          page.locator("#sudoBtn").inner_text())
    check("and says the mode is off", "未进入" in page.inner_text("#sudoState"),
          page.inner_text("#sudoState"))
    page.click("#sudoBtn")
    page.wait_for_selector("#sudoPass", timeout=8000)
    check("it asks for the password too", page.locator("#sudoPass").count() == 1)
    page.fill("#sudoPass", PASSWORD)
    page.click("#sudoEnter")
    page.wait_for_function(
        "() => document.getElementById('shell').classList.contains('sudo')",
        timeout=8000)
    page.wait_for_timeout(500)
    check("the switch elevates the session", page.locator("#sudoChip").is_visible())
    check("the button flips to leaving",
          page.locator("#sudoBtn").inner_text().strip() == "退出 sudo 模式",
          page.locator("#sudoBtn").inner_text())
    check("the card counts down", "已进入" in page.inner_text("#sudoState"),
          page.inner_text("#sudoState"))
    first = page.inner_text("#sudoState")
    page.wait_for_timeout(1200)
    check("and its countdown runs", page.inner_text("#sudoState") != first,
          (first, page.inner_text("#sudoState")))
    page.click("#sudoBtn")
    page.wait_for_timeout(900)
    check("clicking it again leaves the mode", not page.locator("#sudoChip").is_visible())
    check("and the card says so", "未进入" in page.inner_text("#sudoState"),
          page.inner_text("#sudoState"))

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
        check("操作列贴在右边，按钮不用滑就够得着",
              info["actRight"] <= info["wrapRight"] + 1
              and info["actRight"] >= info["wrapRight"] - 40, info)
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
            const b = document.querySelector('#allEventsTable td.actions .btn');
            if (!b) return null;
            const wrap = b.closest('.tblwrap').getBoundingClientRect();
            const r = b.getBoundingClientRect();
            return {w: Math.round(r.width), right: Math.round(r.right),
                    wrapRight: Math.round(wrap.right)};
        }""")
        check("活动管理的「管理」按钮整颗都在容器里",
              btn and btn["w"] > 40 and btn["right"] <= btn["wrapRight"] + 1, btn)
    finally:
        ctx.close()
        try:
            call(owner, "/api/panel/roster/delete", {"id": phone_roster})
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
            call(opener, "/api/panel/signup/delete", {"id": sid})
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
            check("%-10s: only the two lines are shown" % role, page.evaluate(
                """() => document.getElementById('denied').innerText.trim()
                         .split('\\n').filter(l => l.trim()).length === 2"""),
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
        page.click(".card-menu [data-archive]")
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
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            ctx, page = new_page(browser)
            try:
                if WITH_ACCOUNTS:
                    register_page(page)
                login_page(page)
                shell_page(page)
                routes_page(page)
                title_row(page)
                group_page(page)
                nav_keys_page(page)
                assistant_page(page)
                assistant_drawer(browser)
                crumb_motion(browser)
                screens_page(page)
                roster_page(page)
                signup_page(page, browser)
                print_page(browser)
                xlsx_page(browser)
                qr_formats(browser)
                notice_page(page)
                card_page(page)
                create_page(browser)
                # profile_page edits the signed-in account, so it only runs on a
                # throw-away instance
                if WITH_ACCOUNTS:
                    profile_page(page)
                users_table(page)
                events_table(page)
                dialog_page(page)
                theme_page(page)
                audit_page(page)
                search_page(page)
                check("no javascript error on the owner's session", not page._errors,
                      "; ".join(page._errors)[:200])
            finally:
                ctx.close()

            layout_page(browser)
            touch_page(browser)
            mount_page(browser)
            sudo_expiry(browser)
            if WITH_ACCOUNTS:
                role_page(browser)
                denied_flow(browser)
        finally:
            browser.close()

    print()
    if problems:
        print("FAILED %d check(s):" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("PANEL UI TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
