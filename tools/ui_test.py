#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser-driven UI test (Playwright, drives the locally installed Edge).

    python tools/ui_test.py http://127.0.0.1:8931 PASSWORD [shotdir]

The test is deterministic: it asks the API which students are in which state and
drives the UI against those, so it can run against freshly seeded data any
number of times.  tools/run_ui_test.ps1 reseeds and calls this.

Screenshots land in tmp/ui/ for visual review; any JS console error fails it.
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import _baseurl  # noqa: E402

BASE = _baseurl.api_base(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8931")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""
# the console rides on a panel account; the owner is the one that always exists
OWNER = os.environ.get("LOTTERY_OWNER") or "owner"
SHOTS = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp", "ui"
)
os.makedirs(SHOTS, exist_ok=True)

problems = []
step_errors = []


def _is_dark(rgb):
    """Rough luminance test on a computed rgb()/rgba() string."""
    try:
        nums = [int(float(x)) for x in rgb.replace("rgba", "").replace("rgb", "")
                .strip("() ").split(",")[:3]]
    except ValueError:
        return False
    return sum(nums) / 3.0 < 110


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        problems.append("%s %s" % (name, detail))


def shot(page, name, full=False):
    page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=full)


# Requests that are *meant* to fail (negative tests) still show up as console
# errors, because browsers log every failed fetch.  Ignore only those exact URLs.
EXPECTED_4XX = ("/api/checkin/submit",)


def wire(page, label):
    def on_console(msg):
        if msg.type == "error":
            text = msg.text
            if "401 (Unauthorized)" in text:
                return
            loc = getattr(msg, "location", None) or {}
            url = (loc.get("url") or "") if isinstance(loc, dict) else getattr(loc, "url", "")
            if "404" in text and any(part in url for part in EXPECTED_4XX):
                return
            problems.append("[%s] console: %s" % (label, text))
            print("  ERR   [%s] %s" % (label, text))

    def on_error(exc):
        problems.append("[%s] pageerror: %s" % (label, exc))
        print("  ERR   [%s] pageerror: %s" % (label, exc))

    page.on("console", on_console)
    page.on("pageerror", on_error)


def state(page):
    return page.evaluate(
        "fetch('api/admin/state',{headers:{'X-Requested-With':'lottery'},cache:'no-store'})"
        ".then(r=>r.json())"
    )


def hit_state(page):
    return page.evaluate(
        """() => {
        const sel = document.querySelector('.hit.sel');
        return {
          hits: document.querySelectorAll('.hit').length,
          sel: !!sel,
          done: document.querySelectorAll('.hit.sel .step.done').length,
          tickets: document.querySelectorAll('.hit.sel .tkt').length,
          text: sel ? sel.innerText.replace(/\\n/g, ' | ') : '',
          sid: sel ? sel.querySelector('.sid').innerText : null,
          toast: (document.querySelector('.toast') || {}).innerText || ''
        };
      }"""
    )


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
            print("browser: msedge")
        except Exception as exc:  # noqa: BLE001
            print("msedge unavailable (%s), using bundled chromium" % exc)
            browser = p.chromium.launch(headless=True)

        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = ctx.new_page()
        wire(page, "admin")

        # ------------------------------------------------------------ login
        print("== login ==")
        # The console signs in through the panel now: same account, same cookie.
        page.goto(BASE + "/admin", wait_until="networkidle")
        check("an unauthenticated console goes to the panel login",
              "/login" in page.url, page.url)
        shot(page, "00-login")
        page.fill("#username", OWNER)
        page.fill("#password", PASSWORD)
        page.click("#go")
        page.wait_for_url("**/admin", timeout=15000)
        page.wait_for_selector("#rosterTable tbody tr", timeout=15000)
        page.wait_for_timeout(400)
        check("logged in", "/admin" in page.url)
        check("it came back to the console it asked for", page.url.endswith("/admin"),
              page.url)
        who = page.inner_text("#whoami") or ""
        check("the console shows who is signed in", bool(who), who)
        check("and which side of the activity they are on", "负责人" in who, who)
        check("roster table rendered", page.locator("#rosterTable tbody tr").count() > 40,
              page.locator("#rosterTable tbody tr").count())
        shot(page, "01-admin-ops")

        st = state(page)
        students = st["students"]
        check("console shows the crew count",
              "工作人员" in page.inner_text("#headStats"),
              page.inner_text("#headStats"))
        crew = [x for x in students if (x.get("role") or "participant") != "participant"]
        check("seed has staff and volunteers", len(crew) == 8, len(crew))
        fresh = next((s for s in students if not s["marks"]["checkin"]), None)
        partial = next((s for s in students
                        if s["marks"]["checkin"] and s["marks"]["task1"] and not s["marks"]["task2"]), None)
        check("found a not-checked-in student", fresh is not None)
        check("found a half-finished student", partial is not None)

        # ------------------------------------------------- keyboard marking
        print("== keyboard: search + Enter + Shift+n ==")
        page.fill("#q", fresh["pyi"] or fresh["py"])
        page.wait_for_timeout(300)
        before = hit_state(page)
        check("search produced hits", before["hits"] >= 1, before["hits"])
        check("selection highlighted", before["sel"] is True)
        marked_sid = before["sid"]
        shot(page, "02-search")

        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        after = hit_state(page)
        check("Enter completed one step", after["done"] == before["done"] + 1,
              "%s -> %s" % (before["done"], after["done"]))
        check("a ticket number appeared", after["tickets"] == before["tickets"] + 1,
              "%s -> %s" % (before["tickets"], after["tickets"]))
        check("toast confirms the action", "完成" in after["toast"], after["toast"])
        shot(page, "03-after-mark")

        page.keyboard.press("Shift+Digit2")
        page.wait_for_timeout(500)
        page.keyboard.press("Shift+Digit3")
        page.wait_for_timeout(600)
        done3 = hit_state(page)
        check("Shift+2 / Shift+3 finished the chain", done3["done"] == before["done"] + 3,
              done3["done"])
        check("three ticket numbers visible", done3["tickets"] == before["tickets"] + 3,
              done3["tickets"])
        shot(page, "04-chain-done")

        # ------------------------------------------------------- undo lock
        print("== 混合搜索：拼音片段 + #标签 ==")
        # 匹配器是纯函数，直接拿一份固定的名单量它（不依赖这台机器上的演示数据）
        result = page.evaluate("""() => {
            const list = [
              {name: "曾萍静", sid: "2024001", py: "zengpingjing", pyi: "zpj",
               pys: ["zeng", "ping", "jing"], role: "participant",
               marks: {checkin: 1}, tickets: [1], tasks: 2},
              {name: "周子墨", sid: "2024002", py: "zhouzimo", pyi: "zzm",
               pys: ["zhou", "zi", "mo"], role: "staff",
               marks: {}, tickets: [], tasks: 0},
              {name: "高勇", sid: "2024003", py: "gaoyong", pyi: "gy",
               pys: ["gao", "yong"], role: "volunteer",
               marks: {checkin: 1}, tickets: [], tasks: 1}
            ];
            const names = (q) => LT.queryStudents(list, q).map(s => s.name);
            return {
              full: names("zengpingjing"),
              initials: names("zpj"),
              firstWholeRestInitial: names("zengpj"),
              midWhole: names("zengpingj"),
              skipMiddle: names("zengj"),
              sid: names("2024002"),
              hanzi: names("周子墨"),
              staff: names("#staff"),
              staffCn: names("#工作人员"),
              negStaff: names("w") .length ? names("#-std") : [],
              notStaff: names("#-工作人员"),
              staffWithTerm: names("#w zzm"),
              unknownTag: names("#nosuchtag"),
              andTerms: names("gao 2024003"),
              parsedUnknown: LT.parseQuery("#").unknown.length
            };
        }""")
        check("整拼命中", result["full"] == ["曾萍静"], result["full"])
        check("首字母命中", result["initials"] == ["曾萍静"], result["initials"])
        check("第一段整拼 + 后面只打首字母",
              result["firstWholeRestInitial"] == ["曾萍静"],
              result["firstWholeRestInitial"])
        check("中间音节可以只打前缀", result["midWhole"] == ["曾萍静"],
              result["midWhole"])
        check("中间音节可以整段跳过", result["skipMiddle"] == ["曾萍静"],
              result["skipMiddle"])
        check("学号片段命中", result["sid"] == ["周子墨"], result["sid"])
        check("直接打汉字也命中", result["hanzi"] == ["周子墨"], result["hanzi"])
        check("标签按身份筛（英文）", result["staff"] == ["周子墨"], result["staff"])
        check("标签按身份筛（中文）", result["staffCn"] == ["周子墨"],
              result["staffCn"])
        check("标签取反", sorted(result["notStaff"]) == ["曾萍静", "高勇"],
              result["notStaff"])
        check("标签与词求交", result["staffWithTerm"] == ["周子墨"],
              result["staffWithTerm"])
        check("多个词求交", result["andTerms"] == ["高勇"], result["andTerms"])
        check("写错的标签不命中任何人", result["unknownTag"] == [],
              result["unknownTag"])
        check("只打 # 不算写错标签", result["parsedUnknown"] == 0,
              result["parsedUnknown"])

        page.fill("#q", "#")
        page.wait_for_timeout(240)
        hint = page.evaluate("""() => {
            const h = document.getElementById('tagHint');
            return h && !h.hidden ? h.innerText : '';
        }""")
        check("打 # 就把可用的标签列出来",
              "工作人员" in hint and "未签到" in hint, hint[:60])
        page.fill("#q", "")
        page.wait_for_timeout(200)

        print("== undo requires the unlock switch ==")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        row = page.locator("#rosterTable tbody tr").filter(has_text=marked_sid).first
        undo_btn = row.locator("[data-undo]").first
        check("undo button disabled while locked", undo_btn.is_disabled())
        page.fill("#q", marked_sid)
        page.wait_for_timeout(350)
        page.keyboard.press("Control+Shift+KeyZ")
        page.wait_for_timeout(500)
        locked_try = hit_state(page)
        check("Ctrl+Shift+Z does nothing while locked", locked_try["done"] == done3["done"],
              "%s (toast: %s)" % (locked_try["done"], locked_try["toast"]))
        check("toast tells the operator to unlock", "解锁" in locked_try["toast"],
              locked_try["toast"])

        page.click("#unlockWrap")
        page.wait_for_selector(".modal-back", timeout=5000)
        shot(page, "05-unlock-confirm")
        page.click('.modal-back [data-act="yes"]')
        page.wait_for_timeout(700)
        check("unlock switch turned on", page.locator("#unlockWrap.on").count() == 1)
        row = page.locator("#rosterTable tbody tr").filter(has_text=marked_sid).first
        check("undo button enabled", not row.locator("[data-undo]").first.is_disabled())

        page.fill("#q", marked_sid)
        page.wait_for_timeout(350)
        page.keyboard.press("Control+Shift+KeyZ")
        page.wait_for_timeout(600)
        if page.locator(".modal-back").count():
            page.click('.modal-back [data-act="yes"]')
            page.wait_for_timeout(700)
        undone = hit_state(page)
        check("undo removed one step", undone["done"] == done3["done"] - 1,
              "%s -> %s" % (done3["done"], undone["done"]))
        check("toast reports the undo", "撤销" in undone["toast"], undone["toast"])
        shot(page, "06-after-undo")

        # redo so later stages see a complete record, then re-lock
        page.keyboard.press("Shift+Digit3")
        page.wait_for_timeout(600)
        page.click("#unlockWrap")
        page.wait_for_timeout(500)
        check("re-locked", page.locator("#unlockWrap.on").count() == 0)

        # ------------------------------------------------ autofill scrub ----
        # The guard only scrubs before the operator's first keystroke (that is
        # when browsers inject the saved account name), so this needs a page
        # that nobody has typed into yet.
        print("== autofill guard ==")
        fresh = ctx.new_page()
        wire(fresh, "autofill")
        fresh.goto(BASE + "/admin", wait_until="networkidle")
        fresh.wait_for_selector("#q", timeout=10000)
        fresh.wait_for_timeout(800)
        fresh.evaluate("() => { document.querySelector('#q').blur();"
                       " document.querySelector('#q').value = 'laffey'; }")
        fresh.click("#q")
        fresh.wait_for_timeout(400)
        check("injected value is scrubbed on focus",
              fresh.evaluate("() => document.querySelector('#q').value") == "",
              fresh.evaluate("() => document.querySelector('#q').value"))
        # the honeypot must come *before* the search box: browsers fill the
        # first login-shaped input they meet
        order = fresh.evaluate(
            """() => {
            const inputs = Array.from(document.querySelectorAll('input'));
            const q = document.querySelector('#q');
            const firstUser = inputs.find(i => i.getAttribute('autocomplete') === 'username');
            const firstPw = inputs.find(i => i.type === 'password');
            return {
              qIndex: inputs.indexOf(q),
              userIndex: firstUser ? inputs.indexOf(firstUser) : -1,
              pwIndex: firstPw ? inputs.indexOf(firstPw) : -1,
              qName: q.getAttribute('name'),
              qAutocomplete: q.getAttribute('autocomplete')
            };
        }""")
        check("a decoy account field precedes the search box",
              -1 < order["userIndex"] < order["qIndex"], order)
        check("a decoy password field precedes the search box",
              -1 < order["pwIndex"] < order["qIndex"], order)
        check("search box is not login-shaped", order["qAutocomplete"] == "off"
              and "filter" in (order["qName"] or ""), order)
        fresh.fill("#q", "2026")
        fresh.wait_for_timeout(300)
        check("real typing still works",
              fresh.evaluate("() => document.querySelector('#q').value") == "2026")
        fresh.close()

        # ------------------------------------------------------------- roster
        print("== roster tab ==")
        page.keyboard.press("Escape")
        page.click('#tabs button[data-tab="roster"]')
        page.wait_for_timeout(500)
        rows = page.locator("#fullTable tbody tr").count()
        check("full roster listed", rows == len(students), "%s vs %s" % (rows, len(students)))
        check("roster no longer shows pinyin",
              " / " not in page.locator("#fullTable tbody tr").first.inner_text())

        # filter + sort are the two things the operator missed at the event
        page.fill("#rosterFilter", students[0]["sid"][:6])
        page.wait_for_timeout(400)
        check("roster filter narrows the list",
              page.locator("#fullTable tbody tr").count() < rows,
              page.locator("#fullTable tbody tr").count())
        page.fill("#rosterFilter", "")
        page.wait_for_timeout(300)
        check("clearing the filter restores the list",
              page.locator("#fullTable tbody tr").count() == rows)
        page.click('#fullTable th[data-sort="name"]')
        page.wait_for_timeout(300)
        first_asc = page.locator("#fullTable tbody tr td:nth-child(2)").first.inner_text()
        page.click('#fullTable th[data-sort="name"]')
        page.wait_for_timeout(300)
        first_desc = page.locator("#fullTable tbody tr td:nth-child(2)").first.inner_text()
        check("clicking a header sorts, clicking again reverses",
              first_asc != first_desc, (first_asc, first_desc))
        page.click('#fullTable th[data-sort="sid"]')
        page.fill("#addSid", "2099999")
        page.fill("#addName", "测试新增")
        page.click("#addBtn")
        page.wait_for_timeout(900)
        check("added a student through the UI",
              page.locator("#fullTable tbody tr").count() == rows + 1)
        shot(page, "07-roster")

        # import paste box should report a summary
        page.fill("#importText", "学号,姓名\n2099999,测试新增\n2099998,测试第二个")
        page.click("#importBtn")
        page.wait_for_timeout(1000)
        note = page.locator("#importNote").inner_text()
        check("import reports a summary", "新增" in note, note)
        shot(page, "08-roster-import")

        # ------------------------------------------------------------ tickets
        print("== tickets tab ==")
        page.click('#tabs button[data-tab="tickets"]')
        page.wait_for_selector("#planTable tbody tr", timeout=10000)
        page.wait_for_timeout(600)
        st = state(page)
        present = st["stats"]["checkin"]
        check("plan lists every present student",
              page.locator("#planTable tbody tr").count() == present,
              "%s vs %s" % (page.locator("#planTable tbody tr").count(), present))
        shot(page, "09-plan", full=True)

        # ------------------------------------------------------------- screen
        print("== big screen: sign-in phase ==")
        screen = ctx.new_page()
        wire(screen, "screen")
        screen.set_viewport_size({"width": 1920, "height": 1080})
        screen.goto(BASE + "/screen", wait_until="networkidle")
        screen.wait_for_timeout(1600)
        check("the screen listens to the event stream",
              screen.get_attribute("html", "data-stream") == "live",
              screen.get_attribute("html", "data-stream"))
        check("name wall is hidden by default",
              screen.locator(".wall .w").count() == 0,
              screen.locator(".wall .w").count())
        stats_txt = screen.inner_text("#stats")
        check("sign-in screen shows only the door count", "已签到" in stats_txt
              and "任务" not in stats_txt and "奖票" not in stats_txt, stats_txt)
        check("sign-in screen shows a single stat", screen.locator(".sstat").count() == 1,
              screen.locator(".sstat").count())

        # The token rotates every 10s, so comparing against a freshly fetched one
        # is inherently racy.  Instead read the URL the QR actually encodes and
        # prove the server accepts it right now.
        qr = screen.evaluate(
            """async () => {
            const b = await fetch('api/board', {cache:'no-store'}).then(r => r.json());
            return {
              phase: b.config.screen_phase,
              base: (b.config.public_base_url || '').replace(/\\/+$/, ''),
              url: document.querySelector('#qr').getAttribute('data-url') || ''
            };
        }""")
        check("screen starts in the sign-in phase", qr["phase"] == "signin", qr["phase"])
        check("QR encodes the check-in page", qr["url"].startswith(qr["base"] + "/checkin?t="), qr)
        check("QR carries a token", len(qr["url"].split("t=")[-1]) >= 8, qr["url"])
        # the QR carries the *published* base url; point it at the test server
        local_url = BASE + qr["url"][len(qr["base"]):]
        scanned = ctx.request.get(local_url)
        check("the scanned URL is accepted by the server", scanned.ok, scanned.status)
        check("scanning hands out a presence cookie",
              "laffey_lot_presence" in (scanned.headers.get("set-cookie") or ""),
              scanned.headers.get("set-cookie"))

        layout = screen.evaluate(
            """() => {
            const el = document.querySelector('.screen');
            const r = document.querySelector('#qr svg').getBoundingClientRect();
            return {cls: el.className, w: Math.round(r.width),
                    cx: Math.round(r.x + r.width / 2), vw: innerWidth};
        }""")
        check("big-QR layout active", "mode-signin" in layout["cls"], layout["cls"])
        check("big QR is centred", abs(layout["cx"] - layout["vw"] / 2) < 40, layout)
        check("big QR is large", layout["w"] >= 320, layout["w"])
        shot(screen, "10-screen-signin")

        # ------------------------------------------------- self check-in flow
        print("== self check-in (phone) ==")
        st = state(page)
        queued = [x for x in st["students"]
                  if not x["marks"]["checkin"] and (x.get("role") or "participant") == "participant"]
        check("there is someone left to check in", len(queued) >= 1, len(queued))
        who = queued[0]
        token = page.evaluate(
            "fetch('api/screen/token',{cache:'no-store'}).then(r=>r.json()).then(d=>d.token)")

        phone0 = browser.new_context(viewport={"width": 390, "height": 844},
                                     device_scale_factor=2, is_mobile=True, has_touch=True,
                                     locale="zh-CN")
        ci = phone0.new_page()
        wire(ci, "checkin")
        ci.goto(BASE + "/checkin", wait_until="networkidle")
        ci.wait_for_timeout(900)
        check("direct visit asks for a scan",
              "请扫描现场二维码" in ci.inner_text("#ciBody"), ci.inner_text("#ciBody")[:80])
        shot(ci, "21-checkin-blocked")

        ci.goto(BASE + "/checkin?t=" + token, wait_until="networkidle")
        ci.wait_for_selector("#ciForm", timeout=8000)
        check("a fresh scan unlocks the form", ci.locator("#ciForm").count() == 1)
        shot(ci, "22-checkin-form")
        ci.fill("#sid", who["sid"])
        ci.fill("#name", "错误的名字")
        ci.click("#ciGo")
        ci.wait_for_timeout(900)
        check("wrong name is refused", "不符" in ci.inner_text("#ciErr"), ci.inner_text("#ciErr"))
        ci.fill("#name", who["name"])
        ci.click("#ciGo")
        ci.wait_for_selector(".ci-done", timeout=8000)
        done = ci.inner_text(".ci-done")
        check("check-in succeeds", who["name"] in done, done[:120])
        check("door position shown", "位到场" in done, done[:120])
        check("own ticket shown", ci.locator(".ci-done .tkt").count() >= 1,
              ci.locator(".ci-done .tkt").count())
        shot(ci, "23-checkin-done")

        st = state(page)
        updated = [x for x in st["students"] if x["id"] == who["id"]][0]
        check("server recorded the self check-in", bool(updated["marks"]["checkin"]),
              updated["marks"])

        # ---------------------------------------------------- switch to draw
        print("== big screen: draw phase ==")
        page.click('#tabs button[data-tab="draw"]')
        page.wait_for_timeout(300)
        page.click("#phaseDrawBtn")
        page.wait_for_timeout(2600)
        after = screen.evaluate(
            """async () => {
            const b = await fetch('api/board', {cache:'no-store'}).then(r => r.json());
            const base = (b.config.public_base_url || '').replace(/\\/+$/, '');
            const want = base + '/';
            const host = document.createElement('div');
            const q = qrcode(0, 'M');
            q.addData(want);
            q.make();
            host.innerHTML = q.createSvgTag({cellSize: 6, margin: 2, scalable: true});
            const el = document.querySelector('.screen');
            const r = document.querySelector('#qr svg').getBoundingClientRect();
            return {
              phase: b.config.screen_phase,
              cls: el.className,
              match: document.querySelector('#qr').getAttribute('data-url') === want,
              w: Math.round(r.width),
              right: Math.round(innerWidth - (r.x + r.width))
            };
        }""")
        check("phase switched to draw", after["phase"] == "draw", after["phase"])
        check("compact layout active", "mode-draw" in after["cls"], after["cls"])
        draw_txt = screen.inner_text("#stats")
        check("draw screen drops the task counters", "任务" not in draw_txt, draw_txt)
        check("name wall stays hidden", screen.locator(".wall .w").count() == 0)

        # the toggle must bring it back.  The screen polls, so wait for what the
        # setting is supposed to cause instead of guessing at a delay.
        page.click('#tabs button[data-tab="config"]')
        page.wait_for_timeout(500)
        page.click("label:has(#setScreenWall)")
        page.click("#saveSettingsBtn")
        walled = True
        try:
            screen.wait_for_function(
                "n => document.querySelectorAll('.wall .w').length === n",
                arg=st["stats"]["total"], timeout=15000)
        except Exception:
            walled = False
        check("enabling the toggle renders the wall", walled,
              screen.locator(".wall .w").count())
        page.click("label:has(#setScreenWall)")
        page.click("#saveSettingsBtn")
        hidden = True
        try:
            screen.wait_for_function(
                "() => document.querySelectorAll('.wall .w').length === 0",
                timeout=15000)
        except Exception:
            hidden = False
        check("disabling it hides the wall again", hidden,
              screen.locator(".wall .w").count())
        page.click('#tabs button[data-tab="draw"]')
        page.wait_for_timeout(400)
        check("QR becomes the static public link", after["match"] is True, after)
        check("QR shrinks", after["w"] < layout["w"], after["w"])
        check("QR sits near the top-right corner", after["right"] < 120, after)
        shot(screen, "10b-screen-draw")

        # ----------------------------------------------------- finalize + draw
        print("== finalize ==")
        page.click('#tabs button[data-tab="tickets"]')   # phase switch left us on 抽奖
        page.wait_for_timeout(700)
        page.click("#finalizeBtn")
        page.wait_for_selector(".modal-back", timeout=5000)
        shot(page, "11-finalize-confirm")
        page.click('.modal-back [data-act="yes"]')
        page.wait_for_timeout(1800)
        check("finalize button disabled afterwards", page.locator("#finalizeBtn").is_disabled())
        st = state(page)
        check("server says finalized", st["stats"]["finalized"] is True)
        check("bonus tickets are live now",
              st["stats"]["live_tickets"] == st["stats"]["pool_size"] - st["stats"]["remaining"])
        shot(page, "12-plan-finalized", full=True)

        print("== draw ==")
        page.click('#tabs button[data-tab="draw"]')
        page.wait_for_timeout(600)
        rows = page.locator("#prizeTable tbody tr")
        check("prize list comes from the plan's counts", rows.count() >= 4, rows.count())
        first = page.locator("#prizeTable tbody tr").filter(has_text="一等奖").first
        check("row shows its quota", "2 / 2" in first.inner_text(), first.inner_text())
        first.locator("[data-draw]").click()
        page.wait_for_timeout(1500)
        check("draw result banner shown", page.locator("#drawResult .winner").count() == 1)
        check("draw recorded in the history table",
              page.locator("#drawTable tbody tr").count() == 1)
        again = page.locator("#prizeTable tbody tr").filter(has_text="一等奖").first
        check("remaining count decreases", "1 / 2" in again.inner_text(), again.inner_text())
        shot(page, "13-draw")

        screen.wait_for_timeout(1000)
        check("screen started the draw animation", screen.locator("#overlay.show").count() == 1)
        shot(screen, "14-screen-rolling")
        screen.wait_for_timeout(2800)
        check("screen revealed the winner", screen.locator("#dWinner.show").count() == 1)
        shot(screen, "15-screen-winner")

        check("winners stay listed on screen",
              screen.locator(".winners-strip .wstrip-item").count() == 1,
              screen.locator(".winners-strip .wstrip-item").count())
        shot(screen, "14b-screen-compact")

        # ------------------------------------------------------ person draw
        print("== 特等奖 (person draw) ==")
        top = page.locator("#prizeTable tbody tr").filter(has_text="特等奖").first
        top.locator("[data-draw]").click()
        page.wait_for_timeout(1500)
        banner = page.locator("#drawResult .winner")
        check("person draw shows a result", banner.count() == 1)
        check("person draw banner names the prize", "特等奖" in banner.inner_text(),
              banner.inner_text())
        rows = page.locator("#drawTable tbody tr")
        check("person draw recorded", rows.count() == 2, rows.count())
        first_row = rows.first.inner_text()
        check("history marks the person draw", "人级均等" in first_row, first_row)
        check("exhausted prize locks its button",
              page.locator("#prizeTable tbody tr").filter(has_text="特等奖")
                  .first.locator("[data-draw]").is_disabled())
        shot(page, "13b-person-draw")

        screen.wait_for_timeout(1400)
        check("screen plays the person draw animation",
              screen.locator("#overlay.show").count() == 1)
        screen.wait_for_timeout(2800)
        check("person draw reveals in name-roll mode",
              screen.locator("#dCode.names").count() == 1)
        shot(screen, "15b-screen-person")

        # draw a prize the screen will ignore (already-played guard) then close
        screen.keyboard.press("Escape")
        screen.wait_for_timeout(600)
        check("overlay can be dismissed", screen.locator("#overlay.show").count() == 0)
        won = page.locator("#drawTable tbody tr").count()
        check("every winner stays listed on screen",
              screen.locator(".winners-strip .wstrip-item").count() == won,
              screen.locator(".winners-strip .wstrip-item").count())
        check("person winner is styled apart",
              screen.locator(".winners-strip .wstrip-item.person").count() == 1)
        shot(screen, "15c-screen-both-winners")

        # --------------------------------------------------- 一次抽完 (batch)
        print("== 一次抽完 ==")
        row = page.locator("#prizeTable tbody tr").filter(has_text="二等奖").first
        left = row.locator('[data-pace="batch"]')
        check("每个奖品都有逐个/一次抽完的切换", left.count() == 1)
        left.click()
        page.wait_for_timeout(1000)
        row = page.locator("#prizeTable tbody tr").filter(has_text="二等奖").first
        cls = row.locator('[data-pace="batch"]').get_attribute("class") or ""
        check("切到一次抽完后开关留在那一档", "on" in cls, cls)
        check("按钮改成抽完",
              row.locator("[data-draw]").inner_text().strip() == "抽完",
              row.locator("[data-draw]").inner_text())
        row.locator("[data-draw]").click()
        page.wait_for_timeout(1800)
        cells = page.locator("#drawResult .winner-grid .winner-cell")
        check("一次抽完把该奖品名额抽满", cells.count() == 5, cells.count())
        check("结果按整批展示",
              "5 人" in page.locator("#drawResult .winner-head").inner_text(),
              page.locator("#drawResult .winner-head").inner_text())
        check("抽奖记录一次记满整批",
              page.locator("#drawTable tbody tr").count() == won + 5,
              page.locator("#drawTable tbody tr").count())
        check("抽完的奖品锁定按钮",
              page.locator("#prizeTable tbody tr").filter(has_text="二等奖")
                  .first.locator("[data-draw]").is_disabled())
        shot(page, "13c-batch-draw")

        screen.wait_for_timeout(2200)
        check("大屏为整批起一次动画",
              screen.locator("#overlay.show").count() == 1)
        screen.wait_for_timeout(2200)
        check("大屏一次展示整批得主",
              screen.locator("#overlay.show .draw-reveal .it").count() == 5,
              screen.locator("#overlay.show .draw-reveal .it").count())
        shot(screen, "15d-screen-batch")
        screen.keyboard.press("Escape")
        screen.wait_for_timeout(500)
        won = page.locator("#drawTable tbody tr").count()

        # -------------------------------------------------------- public board
        print("== public board ==")
        board = ctx.new_page()
        wire(board, "board")
        board.goto(BASE + "/", wait_until="networkidle")
        board.wait_for_timeout(1200)
        st = state(page)
        check("board lists every participant",
              board.locator(".card").count() == st["stats"]["participants"],
              "%s vs %s" % (board.locator(".card").count(), st["stats"]["participants"]))
        check("board hides staff and volunteers",
              board.locator(".card").count() < st["stats"]["total"])
        check("board lists every winner", board.locator("#winnersBox .winner").count() == won,
              board.locator("#winnersBox .winner").count())
        shot(board, "16-board", full=True)

        board.fill("#find", "zh")
        board.wait_for_timeout(400)
        check("board search filters", board.locator(".card").count() < st["stats"]["total"])
        shot(board, "17-board-search")

        phone = browser.new_context(viewport={"width": 390, "height": 844},
                                    device_scale_factor=2, is_mobile=True, has_touch=True,
                                    locale="zh-CN")
        pboard = phone.new_page()
        wire(pboard, "phone")
        pboard.goto(BASE + "/", wait_until="networkidle")
        pboard.wait_for_timeout(1200)
        check("phone layout renders",
              pboard.locator(".card").count() == st["stats"]["participants"],
              pboard.locator(".card").count())
        shot(pboard, "18-board-phone")

        # ------------------------------------------------------------ settings
        print("== 奖票设置 / 活动设置 / 日志 ==")
        page.click('#tabs button[data-tab="config"]')
        page.wait_for_timeout(800)
        check("the module's own page still draws the QR",
              page.locator("#qrHolder svg").count() == 1)
        options = page.eval_on_selector_all(
            "#setScreenPack option", "els => els.map(e => e.textContent.trim())")
        check("展示屏幕有一个「活动自带」的选项",
              options and options[0] == "活动自带", options)
        shot(page, "19-config", full=True)

        # 活动设置 is the platform's page: members, and the activity itself
        page.click('#tabs button[data-tab="settings"]')
        page.wait_for_selector("#memberTable tbody tr", timeout=15000)
        page.wait_for_timeout(400)
        rows = page.eval_on_selector_all(
            "#memberTable tbody tr",
            "r => r.map(x => x.innerText.replace(/\\s+/g, ' ').trim())")
        check("the roster lists the owner as 负责人",
              len(rows) == 1 and "负责人" in rows[0], rows)
        check("the activity card names the activity",
              page.inner_text("#eventInfo").strip() != "", page.inner_text("#eventInfo"))
        page.click("#memberInviteBtn")
        page.wait_for_selector("#invQ", timeout=8000)
        page.fill("#invQ", "no-such-account")
        page.wait_for_timeout(600)
        check("a search with no match says so",
              "没有匹配的账号" in page.inner_text("#invHits"),
              page.inner_text("#invHits"))
        page.click("#invDone")
        page.wait_for_timeout(300)
        shot(page, "20-settings", full=True)

        # 名单来源：绑定一份名单，同步一次，人应该出现在这场的名单里
        check("the settings page offers a roster to bind",
              page.locator("#rosterPick").count() == 1)
        bound = page.evaluate("""async () => {
          const post = (p, b) => fetch(p, {method: 'POST',
            headers: {'X-Requested-With': 'CAEventManager',
                      'Content-Type': 'application/json'},
            body: JSON.stringify(b)}).then(r => r.json());
          const made = await post('/api/panel/roster/create', {name: '活动绑定测试'});
          await post('/api/panel/roster/import',
                     {id: made.id, text: '学号,姓名\\n20260001,绑定甲\\n20260002,绑定乙\\n'});
          const r = await fetch('api/admin/roster_bind', {method: 'POST',
            headers: {'X-Requested-With': 'lottery', 'Content-Type': 'application/json'},
            body: JSON.stringify({roster_id: made.id})}).then(x => x.json());
          const s = await fetch('api/admin/roster_sync', {method: 'POST',
            headers: {'X-Requested-With': 'lottery'}}).then(x => x.json());
          return {id: made.id, bind: r, sync: s};
        }""")
        check("the binding and the sync go through",
              bound and bound["bind"].get("ok") and
              bound["sync"].get("result", {}).get("added") == 2, bound)
        page.reload(wait_until="networkidle")
        page.wait_for_selector('#tabs button[data-tab="roster"]', timeout=15000)
        page.wait_for_timeout(900)
        page.click('#tabs button[data-tab="roster"]')
        page.wait_for_timeout(900)
        table = page.inner_text("#rosterTable")
        check("the synced people are on the activity roster",
              "20260001" in table and "绑定甲" in table, table[:120])
        page.click('#tabs button[data-tab="settings"]')
        page.wait_for_timeout(1200)
        info = page.inner_text("#rosterInfo")
        check("the settings card names the bound roster",
              "活动绑定测试" in info and "上次同步" in info, info)

        page.click('#tabs button[data-tab="log"]')
        page.wait_for_timeout(800)
        check("audit log filled", page.locator("#logTable tbody tr").count() > 5,
              page.locator("#logTable tbody tr").count())
        shot(page, "21-log")

        # -------------------------------------------------- dark scheme -----
        print("== dark scheme ==")
        dark = browser.new_context(viewport={"width": 1440, "height": 900},
                                   color_scheme="dark", locale="zh-CN")
        dpage = dark.new_page()
        wire(dpage, "dark")
        dpage.goto(BASE + "/", wait_until="networkidle")
        dpage.wait_for_timeout(900)
        bg = dpage.evaluate("() => getComputedStyle(document.body).backgroundColor")
        check("dark scheme is applied from the device setting", _is_dark(bg), bg)
        shot(dpage, "24-dark-board")

        light = browser.new_context(viewport={"width": 1440, "height": 900},
                                    color_scheme="light", locale="zh-CN")
        lpage = light.new_page()
        lpage.goto(BASE + "/", wait_until="networkidle")
        lpage.wait_for_timeout(700)
        lbg = lpage.evaluate("() => getComputedStyle(document.body).backgroundColor")
        check("light is the default scheme", not _is_dark(lbg), lbg)
        shot(lpage, "25-light-board")

        # ------------------------------------------------------- csv download
        print("== csv export ==")
        resp = ctx.request.get(BASE + "/api/admin/export.csv")
        check("csv export works", resp.ok and "学号" in resp.text())

        browser.close()

    print()
    if problems:
        print("UI PROBLEMS (%d):" % len(problems))
        for item in problems:
            print("  -", item)
        return 1
    print("UI TEST PASSED - screenshots in", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
