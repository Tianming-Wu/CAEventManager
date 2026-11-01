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
import random
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931").rstrip("/")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""
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
    call(opener, "/api/panel/login", {"username": "owner", "password": PASSWORD})
    return opener


def call(opener, path, body=None):
    """Talk to the panel API directly; used to set up state, not to test it."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method="POST" if data else "GET",
        headers={"X-Requested-With": "xlottery", "Content-Type": "application/json"})
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


def ui_login(page, user, password):
    page.goto(BASE + "/login", wait_until="domcontentloaded")
    page.fill("#username", user)
    page.fill("#password", password)
    page.click("#go")
    page.wait_for_url("**/panel", timeout=20000)
    page.wait_for_selector(".shell", timeout=20000)
    # .shell is static markup; wait for the account to land, not for a stopwatch
    page.wait_for_function(
        "() => document.getElementById('acctName').textContent.trim().length > 0",
        timeout=25000)
    page.wait_for_timeout(250)


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
    page.fill("#username", "owner")
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
    body = page.content()
    check("console rendered", "总览" in body or "我的活动" in body, page.title())
    check("no 501 page", "Unsupported method" not in body)
    # the rejected attempt above logs a 401 in the console on purpose; from here
    # on any console error is a real one.
    page._errors = []


def shell_page(page):
    print("== the shell ==")
    check("account name shown", page.locator("#acctName").inner_text().strip() != "")
    check("account role shown", "站长" in page.locator("#acctRole").inner_text())
    check("avatar shows one letter",
          len(page.locator("#acctAvatar").inner_text().strip()) == 1)
    nav = visible_nav(page)
    for item in ("overview", "events", "templates", "users", "all-events",
                 "server", "audit", "account", "appearance"):
        check("nav has %s" % item, item in nav["items"], nav["items"])
    check("nav has the approvals group", "approvals" in nav["groups"], nav["groups"])

    print("== the account card is invisible until hovered ==")
    rest = page.evaluate("""() => {
        const cs = getComputedStyle(document.getElementById('acctBtn'));
        return {bg: cs.backgroundColor};
    }""")
    page.hover("#acctBtn")
    page.wait_for_timeout(240)
    hover = page.evaluate("""() => {
        const cs = getComputedStyle(document.getElementById('acctBtn'));
        return {bg: cs.backgroundColor, radius: cs.borderTopLeftRadius};
    }""")
    check("resting card has no fill",
          rest["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), rest)
    check("hovered card shows a rounded fill",
          hover["bg"] not in ("rgba(0, 0, 0, 0)", "transparent") and
          hover["radius"] not in ("0px", ""), hover)


def routes_page(page):
    print("== every route renders its own page ==")
    for route, expect in [
            ("overview", "总览"), ("events", "我的活动"), ("templates", "模板管理"),
            ("users", "用户管理"), ("all-events", "活动管理"),
            ("approval-users", "注册申请"), ("approval-events", "活动申请"),
            ("server", "服务器管理"), ("audit", "审计日志"),
            ("account", "账户信息"), ("appearance", "界面设置")]:
        page.goto(BASE + "/panel#/" + route)
        page.wait_for_timeout(230)
        title = page.locator('.page[data-page="%s"] h1' % route).inner_text()
        check("%-16s -> %s" % (route, title), expect in title, title)


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


def theme_page(page):
    print("== theme ==")
    page.goto(BASE + "/panel#/appearance")
    page.wait_for_timeout(260)
    page.click('#themeSeg button[data-theme-pref="dark"]')
    page.wait_for_timeout(320)
    check("dark applied",
          page.evaluate("() => document.documentElement.dataset.theme") == "dark")
    check("dark really repaints", page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor") != "rgb(245, 242, 238)")
    page.click('#themeSeg button[data-theme-pref="light"]')
    page.wait_for_timeout(260)
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
            ui_login(page, "owner", PASSWORD)
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
        ui_login(page, "owner", PASSWORD)
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
        ui_login(page, "owner", PASSWORD)
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
        ui_login(page, "owner", PASSWORD)
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
            page.wait_for_timeout(440)
            check("%-10s is bounced off a forbidden route" % role,
                  page.evaluate("() => location.hash") != "#/server",
                  page.evaluate("() => location.hash"))
        finally:
            ctx.close()


# --------------------------------------------------------------------------- #
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
                group_page(page)
                dialog_page(page)
                theme_page(page)
                check("no javascript error on the owner's session", not page._errors,
                      "; ".join(page._errors)[:200])
            finally:
                ctx.close()

            layout_page(browser)
            touch_page(browser)
            if WITH_ACCOUNTS:
                role_page(browser)
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
