#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser test for the panel's auth pages (the pages real people actually see).

    python tools/panel_ui_test.py <base-url> <owner-password> [--register]

``tools/panel_test.py`` talks to ``/api/panel/*`` directly, so it stayed green
while both auth pages were posting to ``/api/panel/api/login`` - the page asked
"login", the server answered "not logged in".  This suite drives the pages
themselves, which is the only thing that catches that.

The registration half creates a pending account, so it is opt-in: run it against
a development instance, not production.
"""
import random
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931").rstrip("/")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""
DO_REGISTER = "--register" in sys.argv

problems = []


def check(name, cond, detail=""):
    print("  %-5s %s %s" % ("PASS" if cond else "FAIL", name, "" if cond else detail))
    if not cond:
        problems.append(name)


def login_flow(page):
    print("== root defaults to login ==")
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#loginForm", timeout=20000)
    check("login form is the default view", page.locator("#loginForm").count() == 1,
          page.title())
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
    check("console rendered", "主面板" in body or "活动" in body, page.title())
    check("no 501 page", "Unsupported method" not in body)
    print("   landed on:", page.url)

    print("== sign out ==")
    code = page.evaluate("""async () => {
        const r = await fetch('api/panel/logout', {method: 'POST',
            headers: {'X-Requested-With': 'xlottery'}});
        return r.status;
    }""")
    check("logout accepted", code == 200, code)


def guard_check(browser):
    """A visitor with no cookie lands on the login form, not on the console."""
    fresh = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = fresh.new_page()
        page.goto(BASE + "/panel", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_selector("#loginForm", timeout=20000)
        check("signed-out /panel shows the login form",
              page.locator("#loginForm").count() == 1, page.url)
        check("signed-out /panel does not render the console",
              "主面板" not in page.content(), page.url)
    finally:
        fresh.close()


def register_flow(page):
    user = "uitest%d" % random.randint(1000, 9999)
    print("== registration page (%s) ==" % user)
    page.goto(BASE + "/register", wait_until="domcontentloaded")
    page.wait_for_selector("#regForm", timeout=20000)
    check("register form present", page.locator("#regForm").count() == 1, page.title())
    check("links back to login", page.locator('a[href="./"]').count() == 1)

    page.fill("#username", user)
    page.fill("#name", "浏览器测试")
    page.fill("#password", "browser-pass-1")
    page.fill("#note", "自动化测试")
    page.click("#go")
    page.wait_for_function(
        "document.getElementById('done').style.display === 'block' ||"
        " document.getElementById('err').textContent.trim().length > 0",
        timeout=15000)
    done = page.locator("#done").inner_text().strip()
    err = page.locator("#err").inner_text().strip()
    check("submission accepted", done != "" and err == "", "%r / %r" % (done, err))
    check("form is locked after submitting", page.locator("#go").is_disabled())
    print("   page said:", done or err)


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        register_flow(page) if DO_REGISTER else None
        login_flow(page)
        guard_check(browser)
        check("no javascript error on any page", not errors, "; ".join(errors))
    finally:
        browser.close()

print()
if problems:
    print("FAILED %d check(s):" % len(problems))
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("PANEL UI TEST PASSED")
