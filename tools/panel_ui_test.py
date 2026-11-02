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


def routes_page(page):
    print("== every route renders its own page ==")
    for route, expect in [
            ("overview", "总览"), ("events", "我的活动"), ("templates", "预设管理"),
            ("users", "用户管理"), ("all-events", "活动管理"),
            ("approval-users", "注册申请"), ("approval-events", "活动申请"),
            ("modules", "模块管理"), ("server", "服务器管理"), ("audit", "审计日志"),
            ("account", "账户信息"), ("appearance", "界面设置")]:
        page.goto(BASE + "/panel#/" + route)
        page.wait_for_timeout(230)
        title = page.locator('.page[data-page="%s"] h1' % route).inner_text()
        check("%-16s -> %s" % (route, title), expect in title, title)


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
    page.wait_for_timeout(400)
    check("dark applied",
          page.evaluate("() => document.documentElement.dataset.theme") == "dark")
    check("dark really repaints", page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor") != "rgb(245, 242, 238)")
    page.click('#themeSeg button[data-theme-pref="light"]')
    page.wait_for_timeout(420)
    check("back to light",
          page.evaluate("() => document.documentElement.dataset.theme") == "light")


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
