/* Admin console. Vanilla JS, no build step. */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var KINDS = ["checkin", "task1", "task2"];
  var PREV = { checkin: null, task1: "checkin", task2: "task1" };

  var S = {
    students: [],
    byId: {},
    config: {},
    audit: [],
    draws: [],
    unlocked: false,
    members: [],
    seat: {},
    unlockUntil: 0,
    plan: null,
    tab: "ops"
  };

  // ------------------------------------------------------------------ utils
  /* ---- 模块页面的脚本 ----
     平台按 module.json 的 pages 声明依次载入 pages/<id>.js，然后调 mount/refresh/show。
     平台自己的代码不直接碰模块的函数，只走这两个入口。 */
  function modulePage(id) {
    return (window.MODULE && window.MODULE.pages && window.MODULE.pages[id]) || null;
  }

  function pageRefresh(id) {
    var page = modulePage(id);
    if (page && page.refresh) page.refresh(S, S.tab);
  }

  function pageShow(id) {
    var page = modulePage(id);
    if (page && page.show) return page.show(S.tab);
    return undefined;
  }

  /* 一个文件一个 <script>：先载模块的共用件，再载各页自己的 */
  function loadScripts(names) {
    var base = (window.LT && LT.PREFIX) || "";
    return Promise.all((names || []).map(function (name) {
      return new Promise(function (resolve) {
        var el = document.createElement("script");
        el.src = base + "/module/" + encodeURIComponent(name) + ".js";
        el.onload = function () { resolve(true); };
        el.onerror = function () { resolve(false); };   // 没有脚本的页签是正常的
        document.head.appendChild(el);
      });
    }));
  }

  function loadModuleScripts(scripts, pages) {
    var list = (pages || []).filter(function (p) { return p && p.id; });
    return loadScripts(scripts).then(function () {
      return loadScripts(list.map(function (p) { return p.id; }));
    }).then(function () {
      list.forEach(function (p) {
        var page = modulePage(p.id);
        if (page && page.mount) {
          page.mount({ LT: LT, state: S, tab: S.tab, page: p,
                       reload: load, showTab: showTab });
        }
      });
      // 脚本是异步来的：载入的时候可能已经切到某个页签了，补画一次
      if (S.tab) pageShow(S.tab);
    });
  }

  // Mirrors model.effective_pool(): in auto mode the pool follows the room.
  function poolSize(st) {
    if ((S.config.pool_mode || "auto") === "fixed") return Number(S.config.pool_size) || 0;
    return st.checkin * 3 + (Number(S.config.bonus_constant) || 0);
  }

  function isParticipant(stu) { return (stu.role || "participant") === "participant"; }

  function computeStats() {
    var st = {
      total: S.students.length, participants: 0, staff: 0, staff_present: 0,
      checkin: 0, task1: 0, task2: 0,
      live_tickets: 0, max_code: 0, finalized: !!S.config.finalized
    };
    S.students.forEach(function (s) {
      if (isParticipant(s)) {
        st.participants++;
        KINDS.forEach(function (k) { if (s.marks[k]) st[k]++; });
      } else {
        st.staff++;
        if (s.marks.checkin) st.staff_present++;
      }
      (s.tickets || []).forEach(function (t) {
        st.live_tickets++;
        if (t.code > st.max_code) st.max_code = t.code;
      });
    });
    st.guaranteed = st.checkin + st.task1 + st.task2;
    st.pool_mode = S.config.pool_mode || "auto";
    st.bonus_constant = Number(S.config.bonus_constant) || 0;
    st.pool_size = poolSize(st);
    st.bonus_pool = Math.max(0, st.pool_size - st.guaranteed);
    st.remaining = st.pool_size - st.live_tickets;
    return st;
  }

  // ------------------------------------------------------------------ toasts
  function ok(html) { LT.toast(html, false); }
  function bad(err) {
    // a refused write is a disagreement with another operator, not a failure to
    // hide: say so, then show the state the server actually has
    var conflict = LT.conflictMsg(err);
    if (conflict) {
      LT.toast(conflict, true, 5200);
      load().catch(function () {});
      return;
    }
    LT.toast(LT.esc(err && err.message ? err.message : String(err)), true, 4200);
  }

  // ------------------------------------------------------------------ render
  function renderHead() {
    var st = computeStats();
    var html = [
      '<span class="chip green"><b>' + st.checkin + "</b>/" + st.participants + " 已签到</span>",
      '<span class="chip blue"><b>' + st.task1 + "</b> 任务一</span>",
      '<span class="chip blue"><b>' + st.task2 + "</b> 任务二</span>",
      '<span class="chip gold"><b>' + st.live_tickets + "</b>/" + st.pool_size + " 奖票已发</span>"
    ];
    if (st.staff) html.push('<span class="chip">工作人员 <b>' + st.staff_present + "</b>/" + st.staff + "</span>");
    if (st.finalized) html.push('<span class="chip gold live">已结算</span>');
    if (S.unlocked) html.push('<span class="chip" style="border-color:#6b2020;color:#ffb4b4">修改已解锁</span>');
    $("#headStats").innerHTML = html.join("");
    $("#brandTitle").textContent = S.config.title || "抽奖";
    var badge = $("#nRoster");
    if (badge) badge.textContent = st.total;
    document.title = "后台 · " + (S.config.title || "抽奖");
  }

  /* 结算/重置这两颗按钮是平台动作，状态由平台自己管 */
  function renderDrawButtons() {
    var sum = (S.plan && S.plan.summary) || {};
    var fin = $("#finalizeBtn");
    if (fin) fin.disabled = !!sum.finalized;
    var un = $("#unfinalizeBtn");
    if (un) un.disabled = !sum.finalized || !S.unlocked;
    var reset = $("#resetTicketsBtn");
    if (reset) reset.disabled = !S.unlocked;
  }

  function renderUnlock() {

    var on = S.unlocked && S.unlockUntil * 1000 > Date.now();
    $("#unlockToggle").checked = on;
    $("#unlockWrap").classList.toggle("on", on);
    var label = $("#unlockLabel");
    if (on) {
      var left = Math.max(0, Math.round(S.unlockUntil - Date.now() / 1000));
      label.textContent = "解锁中 " + left + "s";
    } else {
      label.textContent = "解锁修改";
    }
  }

  function renderLog() {
    $("#logTable tbody").innerHTML = S.audit.map(function (a) {
      return "<tr><td class='num'>" + LT.fmtDateTime(a.ts) + "</td>" +
        "<td>" + LT.esc(a.action) + "</td>" +
        "<td style='white-space:normal'>" + LT.esc(a.detail || "") + "</td></tr>";
    }).join("");
  }

  function renderPhase() {
    var p = S.config.screen_phase || "signin";
    var el = $("#phaseNow");
    if (!el) return;
    el.textContent = "当前：" + (p === "draw" ? "抽奖阶段" : "签到阶段");
    el.className = "chip " + (p === "draw" ? "gold" : "green");
    $("#phaseSigninBtn").classList.toggle("primary", p !== "draw");
    $("#phaseDrawBtn").classList.toggle("primary", p === "draw");
  }

  function setPhase(phase) {
    LT.api("/api/admin/phase", { method: "POST", body: { phase: phase } })
      .then(function (d) {
        S.config.screen_phase = d.phase;
        renderPhase();
        ok(d.phase === "draw" ? "大屏已切到抽奖阶段" : "大屏已切到签到阶段（二维码每 10 秒刷新）");
      })
      .catch(bad);
  }

  /* ---- 短链：活动的平台资产，模块申请 features.shortlink 之后才有入口 ---- */
  var SHORT = { links: [], suggest: [], limit: 0, timer: null };

  function shortlinkSlot() {
    return (S.features && S.features.shortlink) || null;
  }

  function fmtLeft(link) {
    if (!link.expiresAt) return "不限";
    var left = Math.max(0, link.expiresAt - Date.now() / 1000);
    if (left <= 0) return "已过期";
    return Math.floor(left / 60) + " 分钟";
  }

  function drawShortlinks() {
    var body = $("#shortlinkTable tbody");
    if (!body) return;
    $("#shortlinkLimit").textContent =
      SHORT.limit ? "本活动最多 " + SHORT.limit + " 条" : "";
    if (!SHORT.links.length) {
      body.innerHTML = '<tr><td colspan="5" style="color:var(--muted)">还没有短链</td></tr>';
      return;
    }
    body.innerHTML = SHORT.links.map(function (l) {
      var state = l.valid ? (l.maxHits ? l.hits + " / " + l.maxHits : l.hits + " 次")
        : l.revoked ? "已删除" : l.expired ? "已过期" : "次数用尽";
      return "<tr>" +
        '<td><span class="mono">/s/' + LT.esc(l.code) + "</span></td>" +
        "<td>" + LT.esc(l.target || "/（公示页）") + "</td>" +
        "<td>" + LT.esc(fmtLeft(l)) + "</td>" +
        "<td>" + LT.esc(state) + "</td>" +
        '<td class="actions"><button class="btn sm ghost" data-shortline-del="' +
        LT.esc(l.code) + '">删除</button></td>' +
        "</tr>";
    }).join("");
  }

  function loadShortlinks() {
    if (!shortlinkSlot()) {
      var panel = $("#shortlinkTable") && $("#shortlinkTable").closest(".panel");
      if (panel) panel.classList.add("hidden");
      return Promise.resolve();
    }
    return LT.panelApi("/api/panel/shortlink?seq=" + S.seq)
      .then(function (d) {
        SHORT.links = d.links || [];
        SHORT.limit = d.limit || 0;
        var hints = $("#shortlinkSuggest");
        if (hints && !hints.children.length) {
          SHORT.suggest = d.suggest || [];
          hints.innerHTML = SHORT.suggest.map(function (t) {
            return '<option value="' + LT.esc(t.key) + '">' + LT.esc(t.label) + "</option>";
          }).join("");
        }
        drawShortlinks();
      })
      .catch(function () { /* 没权限或模块没申请：入口本来就不该出现 */ });
  }

  function mintShortlink() {
    var target = ($("#shortlinkTarget").value || "").trim();
    var minutes = Number($("#shortlinkMinutes").value || 0);
    var hits = Number($("#shortlinkHits").value || 0);
    LT.panelApi("/api/panel/shortlink", {
      method: "POST",
      body: { seq: S.seq, target: target, minutes: minutes, max_hits: hits },
    })
      .then(function (d) {
        ok("已生成短链 " + (d.link && d.link.code ? "/s/" + d.link.code : ""));
        return loadShortlinks();
      })
      .catch(bad);
  }

  function revokeShortlink(code) {
    LT.panelApi("/api/panel/shortlink/delete", { method: "POST", body: { code: code } })
      .then(function () { ok("短链已删除"); return loadShortlinks(); })
      .catch(bad);
  }

  // A reload arrives after every write and every other session's change.  While
  // the operator is editing the settings form it must not overwrite what they
  // typed: the fields are only re-read from the server once they save.
  var formTouched = false;

  function archiveEvent() {
    if (!S.seq) return;
    LT.confirm("归档活动",
      "归档后这场活动不再出现在列表里，链接与短链同时失效；数据会被打包，只留元数据。"
      + "归档之后活动不再保证能打开。",
      { danger: true, label: "归档" }).then(function (yes) {
      if (!yes) return;
      LT.panelApi("/api/panel/event/archive", { method: "POST", body: { seq: S.seq } })
        .then(function () {
          ok("已归档，正在回到总览");
          setTimeout(function () { location.href = LT.panelLoginUrl(); }, 1200);
        })
        .catch(function (e) {
          if (e && e.sudo) {
            // 主站提权：输一次密码，十分钟内有效——归档是主站动作，走这一套
            askPanelPassword().then(function () { archiveEvent(); }).catch(function () {});
            return;
          }
          bad(e);
        });
    });
  }


  /* 提权：活动级的动作要回面板去输一次密码；面板的 sudo 是会话级的，这里借用同一次登录 */
  function askPanelPassword() {
    return new Promise(function (resolve, reject) {
      LT.modal("<h2>需要确认</h2><p class=\"page-sub\">这个操作要重新输入一次密码。</p>"
        + '<div class="field"><label for="sudoPw">登录密码</label>'
        + '<input id="sudoPw" type="password" autocomplete="off"></div>'
        + '<div class="row" style="margin-top:16px;justify-content:flex-end">'
        + '<button class="btn" data-close="1">取消</button>'
        + '<button class="btn primary" id="sudoGo">确认</button></div>');
      $("#sudoGo").addEventListener("click", function () {
        LT.panelApi("/api/panel/sudo", {
          method: "POST", body: { password: $("#sudoPw").value || "" } })
          .then(function () { LT.closeModal(); resolve(true); })
          .catch(function (e) { PX_ERROR(e); reject(e); });
      });
    });
  }

  function PX_ERROR(e) {
    LT.toast(LT.esc((e && e.message) || "密码不对"), true);
  }

  /* 危险区域：负责人能点，其他成员看到的是减淡的红色、点不动 */
  function renderDangerZone() {
    var btn = $("#archiveEventBtn");
    if (!btn) return;
    var seat = (S.seat && S.seat.role) || "";
    var owner = seat === "owner";
    btn.disabled = !owner;
    btn.classList.toggle("danger", true);
    btn.classList.toggle("dim", !owner);
    var note = $("#archiveEventNote");
    if (note) note.textContent = owner ? "" : "只有该活动的负责人能归档";
  }

  function renderSettings() {
    loadShortlinks();
    renderDangerZone();
    var c = S.config;
    if (!formTouched) {
      $("#setTitle").value = c.title || "";
      $("#setSubtitle").value = c.subtitle || "";
      $("#setBaseUrl").value = S.publicBaseUrl || c.public_base_url || "";
      $("#setShowTimes").checked = !!c.show_times;
      $("#setShowSid").checked = !!c.show_sid;
      $("#setScreenWall").checked = !!c.screen_show_wall;
      renderScreenPackChoices(c.screen_pack || "");
      $("#setPoolMode").value = c.pool_mode || "auto";
      $("#setPool").value = c.pool_size;
      $("#setPoolExpr").value = c.pool_expr || "";
      $("#setBonusConstant").value = c.bonus_constant;
      $("#setBeta").value = c.beta;
      $("#setAllowBonus").checked = c.allow_bonus !== false;
      $("#setUniqueInside").checked = c.draw_unique_inside !== false;
      $("#setUniqueAcross").checked = c.draw_unique_across === true;
      pageRefresh("draw");
    }
    $("#exportLink").href = LT.PREFIX + "/api/admin/export.csv";
    $("#secretUrl").textContent = S.publicBaseUrl + "/";
    updatePoolPreview();
    renderQR();
  }

  // 展示屏幕：可选的服务端清单，来自「外部资源 › 展示屏幕」
  function renderScreenPackChoices(current) {
    var box = $("#setScreenPack");
    if (!box) return;
    var packs = S.screenPacks || [];
    box.innerHTML = '<option value="">活动自带</option>' + packs.map(function (id) {
      return '<option value="' + LT.esc(id) + '">' + LT.esc(id) + "</option>";
    }).join("");
    // 引用的那份要是已经被删掉，仍然把它列出来，别让选择悄悄地变回"自带"
    if (current && packs.indexOf(current) < 0) {
      box.insertAdjacentHTML("beforeend",
        '<option value="' + LT.esc(current) + '">' + LT.esc(current) + "（已不存在）</option>");
    }
    box.value = current || "";
  }

  function updatePoolPreview() {
    var st = computeStats();
    var auto = (S.config.pool_mode || "auto") !== "fixed";
    $("#poolPreview").innerHTML =
      "当前：到场 <b>" + st.checkin + "</b> 人 → 奖票池 <b>" + st.pool_size + "</b> 张" +
      "（保底 " + st.guaranteed + " + 速度奖励 " + st.bonus_pool + "）" +
      (auto ? "。<br>之后每多签到一人，池子自动 +3。" : "。<br>固定模式下池子不随人数变化。");
    var mode = S.config.pool_mode || "auto";
    $("#setPool").disabled = auto;
    // a field that does not apply is hidden, not merely disabled
    $("#poolExprField").hidden = mode !== "expr";
  }

  function renderQR() {
    var url = (S.publicBaseUrl || S.config.public_base_url || "").trim();
    var holder = $("#qrHolder");
    if (!url) {
      holder.innerHTML = '<p class="note">先填写公示页地址。</p>';
      return;
    }
    var full = url.replace(/\/+$/, "") + "/";
    try {
      var qr = qrcode(0, "M");
      qr.addData(full);
      qr.make();
      holder.innerHTML =
        '<div style="background:#fff;padding:10px;border-radius:10px;width:190px">' +
        qr.createSvgTag({ cellSize: 5, margin: 2, scalable: true }) + "</div>" +
        '<div><p class="note" style="word-break:break-all">' + LT.esc(full) + "</p>" +
        '<a class="btn sm" href="' + LT.esc(full) + '" target="_blank" rel="noopener">打开公示页 ↗</a></div>';
    } catch (e) {
      holder.innerHTML = '<p class="note danger-text">二维码生成失败：' + LT.esc(e.message) + "</p>";
    }
  }

  function renderAll() {
    renderHead();
    renderPhase();
    pageRefresh("draw");
    renderUnlock();
    pageRefresh("ops");
    pageRefresh("roster");
    pageShow("draw");
    renderLog();
    if (S.tab === "config") renderSettings();
    if (S.tab === "tickets") pageRefresh("tickets");
  }

  // ------------------------------------------------------------------ data
  function applyState(d) {
    S.features = d.features || {};
    S.seq = d.seq || 0;
    S.config = d.config || {};
    S.prizes = d.prizes || [];
    S.screenPacks = d.screenPacks || [];
    S.roster = d.roster || null;
    S.publicBaseUrl = (d.publicBaseUrl || S.config.public_base_url || "").replace(/\/+$/, "");
    S.students = d.students || [];
    S.byId = {};
    S.students.forEach(function (s) { S.byId[s.id] = s; });
    S.audit = d.audit || [];
    S.features = d.features || {};
    S.draws = d.draws || [];
    S.unlocked = !!d.unlocked;
    S.unlockUntil = d.unlockUntil || 0;
    renderAll();
  }

  function load() {
    return LT.api("/api/admin/state").then(applyState).catch(function (e) {
      if (e.status === 401) { location.href = LT.panelLoginUrl(); return; }
      bad(e);
    });
  }


  // ------------------------------------------------------------------ events
  function bindGlobal() {
    document.addEventListener("click", function (e) {
      var t = e.target;
      var dp = t.getAttribute && t.getAttribute("data-draw");
      if (dp) { drawPrize(Number(dp)); return; }
      var pc = t.getAttribute && t.getAttribute("data-pace");
      if (pc) {
      var dp = modulePage("draw");
      if (dp && dp.setPace) dp.setPace(Number(t.getAttribute("data-i")), pc === "batch");
      return;
    }
      var pd = t.getAttribute && t.getAttribute("data-prize-del");
      if (pd) {
        var kept = collectPrizes();
        kept.splice(Number(pd), 1);
        S.config.prizes = kept;
        pageRefresh("draw");
        return;
      }
      var vd = t.getAttribute && t.getAttribute("data-void");
      if (vd) { voidDraw(Number(vd)); return; }
    });

    $("#tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-tab]");
      if (btn) showTab(btn.getAttribute("data-tab"));
    });

    renderTabs();

    $("#unlockToggle").addEventListener("change", function (e) {
      var on = e.target.checked;
      if (on) {
        LT.confirm({
          title: "打开解锁修改？",
          danger: true,
          okText: "打开",
          html: "解锁后可以：撤销标记、修改完成时间、删除学生、作废抽奖、重置奖票。<br>" +
            "30 秒无操作自动上锁。"
        }).then(function (yes) {
          if (!yes) { e.target.checked = false; return; }
          setUnlock(true);
        });
      } else {
        setUnlock(false);
      }
    });

    $("#logoutBtn").addEventListener("click", function () {
      // one session for both surfaces, so this signs out of the panel too
      LT.api("/api/logout", { method: "POST" }).then(function () {
        location.href = LT.PANEL + "/login";
      }).catch(bad);
    });

    $("#archiveEventBtn").addEventListener("click", archiveEvent);
    $("#shortlinkBtn").addEventListener("click", mintShortlink);
  $("#shortlinkTable").addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-shortline-del]") : null;
    if (btn) revokeShortlink(btn.getAttribute("data-shortline-del"));
  });

  $("#downloadDataBtn").addEventListener("click", function () {
    if (!S.seq) return;
    window.open((LT.panelRoot || "") + "/api/panel/event/download?seq=" + S.seq, "_blank");
  });

    $("#logRefreshBtn").addEventListener("click", function () { load().then(function () { ok("已刷新"); }); });

  }

  function setUnlock(on) {
    LT.api("/api/admin/unlock", { method: "POST", body: { on: on } }).then(function (d) {
      S.unlocked = d.unlocked;
      S.unlockUntil = d.unlockUntil;
      renderUnlock();
      pageRefresh("ops");
      pageRefresh("tickets");
      pageRefresh("draw");
      ok(on ? "已解锁" : "已上锁");
    }).catch(bad);
  }

  // ------------------------------------------------------------------ tabs
  // The bar comes from the platform; a kind only decides which pages it wants.
  function renderTabs() {
    var nav = $("#tabs");
    LT.api("/api/admin/console").then(function (d) {
      S.pages = d.pages || [];
      // 模块页面的脚本：载入之后调 mount，平台这边只留页签条与调度
      loadModuleScripts(d.scripts, S.pages);
      nav.innerHTML = S.pages.map(function (page) {
        var extra = page.id === "roster"
          ? '<span class="badge-n" id="nRoster">0</span>' : "";
        return '<button data-tab="' + page.id + '">' + LT.esc(page.title) +
          extra + "</button>";
      }).join("");
      var first = S.pages[0];
      nav.querySelectorAll("button[data-tab]").forEach(function (btn) {
        if (first && btn.getAttribute("data-tab") === first.id) {
          btn.classList.add("active");
        }
      });
      var badge = $("#nRoster");
      if (badge) badge.textContent = (S.students || []).length;
      if (first) showTab(first.id);
    }).catch(function () {
      nav.textContent = "";
    });
  }

  function showTab(name) {
    S.tab = name;
    $$("#tabs button").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-tab") === name); });
    $$("main > section").forEach(function (s) { s.classList.toggle("hidden", s.id !== "tab-" + name); });
    if (name === "config") renderSettings();
    if (name === "settings") loadMembers();
    if (name === "log") load();
    // 模块页自己画：它的 show 收得到现在是哪个页签
    pageShow(name);
    return name;
  }

  // ------------------------------------------------------------------ tickets
  function finalize() {
    var sum = (S.plan && S.plan.summary) || {};
    LT.confirm({
      title: "结算奖票？",
      okText: "结算",
      html: "将为 <b>" + sum.present + "</b> 位到场同学按速度分配 <b style='color:var(--gold)'>" +
        sum.bonus_pool + "</b> 张剩余奖票。<br>结算后不能继续标记（需要先取消结算）。"
    }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/finalize", { method: "POST" }).then(function (d) {
        ok("结算完成，发放了 <b>" + d.result.assigned + "</b> 张奖励票");
        return load().then(function () { pageShow("tickets"); });
      }).catch(bad);
    });
  }

  function unfinalize() {
    LT.confirm({
      title: "取消结算", danger: true, okText: "取消结算",
      html: "会收回所有「速度奖励」奖票，之后可以继续标记。<br>确认？"
    }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/unfinalize", { method: "POST" })
        .then(function () { ok("已取消结算"); return load().then(function () { pageShow("tickets"); }); })
        .catch(bad);
    });
  }

  function resetTickets() {
    LT.confirm({
      title: "重置全部奖票", danger: true, okText: "重置",
      html: "会收回所有人的奖票号码并取消结算，标记本身保留。<br>" +
        "重新标记/签到时会重新发号，号码可能变化。<br>确认？"
    }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/reset_tickets", { method: "POST" })
        .then(function () { ok("已重置奖票"); return load().then(function () { pageShow("tickets"); }); })
        .catch(bad);
    });
  }

  // ------------------------------------------------------------------ draw
  function winnerRow(w) {
    var tail = w.mode === "person"
      ? '<span class="code" style="font-family:inherit">均等</span>'
      : '<span class="code">#' + LT.code(w.code) + "</span>";
    return '<div class="winner"><span class="prize">' + LT.esc(w.prize) + "</span>" +
      '<span class="who">' + LT.esc(w.name) +
      " <span style='color:var(--dim)'>" + LT.esc(w.sid) + "</span></span>" + tail + "</div>";
  }

  function renderDrawResult(list, stopped) {
    var box = $("#drawResult");
    if (!box) return;
    var html;
    if (list.length > 1) {
      html = '<div class="winner-head">本次抽出 ' + list.length + " 人</div>" +
        '<div class="winner-grid">' + list.map(function (w) {
          return '<span class="winner-cell">' + LT.esc(w.name) +
            (w.mode === "person" ? "" : ' <span class="tkt">' + LT.code(w.code) + "</span>") +
            "</span>";
        }).join("") + "</div>";
    } else {
      html = winnerRow(list[0]);
    }
    if (stopped) {
      html += '<p class="note" style="margin-top:8px">只抽出 ' + list.length +
        " 人：" + LT.esc(stopped) + "</p>";
    }
    box.innerHTML = html;
  }

  function drawPrize(index) {
    var p = (S.prizes || [])[index];
    if (!p) return;
    var body = { prize: p.name, mode: p.mode };
    if (p.batch) body.batch = true;
    LT.api("/api/admin/draw", { method: "POST", body: body })
      .then(function (d) {
        renderDrawResult(d.draws || [d.draw], d.stopped);
        return load();
      })
      .catch(bad);
  }

  function voidDraw(id) {
    LT.confirm({ title: "作废这条抽奖记录？", danger: true, okText: "作废" }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/draw_void", { method: "POST", body: { id: id } })
        .then(function () { ok("已作废"); return load(); }).catch(bad);
    });
  }

  // ------------------------------------------------------------------ settings
  function collectSettings() {
    return {
      // title is owned by the master panel now; sending it could overwrite it
      subtitle: $("#setSubtitle").value.trim(),
      public_base_url: $("#setBaseUrl").value.trim(),
      show_times: $("#setShowTimes").checked,
      show_sid: $("#setShowSid").checked,
      screen_show_wall: $("#setScreenWall").checked,
      screen_pack: $("#setScreenPack").value,
      pool_mode: $("#setPoolMode").value,
      pool_size: Number($("#setPool").value),
      pool_expr: $("#setPoolExpr").value.trim(),
      bonus_constant: Number($("#setBonusConstant").value),
      beta: Number($("#setBeta").value),
      allow_bonus: $("#setAllowBonus").checked,
      draw_unique_inside: $("#setUniqueInside").checked,
      draw_unique_across: $("#setUniqueAcross").checked
    };
  }

  function saveSettings() {
    var body = collectSettings();
    LT.api("/api/admin/settings", { method: "POST", body: body })
      .then(function (d) {
        S.config = d.config;
        formTouched = false;   // what was typed is now what the server holds
        return load();
      })
      .then(function () {
        $("#settingsNote").textContent = "已保存";
        ok("设置已保存");
        if (S.tab === "tickets") pageShow("tickets");
      })
      .catch(bad);
  }

  function collectPrizes() {
    var rows = $$("#prizeEditor .prize-row");
    if (!rows.length) return [];
    return rows.map(function (row) {
      return {
        name: $(".p-name", row).value.trim(),
        count: Math.max(0, Number($(".p-count", row).value) || 0),
        mode: $(".p-mode", row).value,
        batch: row.getAttribute("data-batch") === "1"
      };
    }).filter(function (p) { return p.name; });
  }

  function savePrizes() {
    LT.api("/api/admin/settings", { method: "POST", body: { prizes: collectPrizes() } })
      .then(function (d) {
        S.config = d.config;
        return load();
      })
      .then(function () {
        $("#prizeNote").textContent = "已保存";
        pageRefresh("draw");
      })
      .catch(bad);
  }

  // ------------------------------------------------------------ 活动设置
  function loadRosterSource() {
    return LT.api("/api/admin/rosters")
      .then(function (d) {
        S.rosterChoices = d.rosters || [];
        var pick = $("#rosterPick");
        if (!pick) return;
        var bound = (S.roster || {}).id || d.bound || 0;
        pick.innerHTML = '<option value="0">不绑定</option>' +
          S.rosterChoices.map(function (r) {
            return '<option value="' + r.id + '">' + LT.esc(r.name) +
              "（" + r.members + " 人）</option>";
          }).join("");
        pick.value = String(bound);
        renderRosterSource();
      })
      .catch(function () { /* 名单里的东西看不到就算了，不挡住控制台 */ });
  }

  function renderRosterSource() {
    var info = $("#rosterInfo");
    var pick = $("#rosterPick");
    if (!info || !pick) return;
    var r = S.roster || {};
    var bound = Number(pick.value) || 0;
    var name = "";
    (S.rosterChoices || []).forEach(function (x) {
      if (x.id === bound) name = x.name;
    });
    var rows = [
      ["当前绑定", bound ? (name || r.name || "—") : "未绑定"],
      ["上次同步", r.syncedAt ? LT.fmtDateTime(r.syncedAt) : "—"],
      ["已不在名单", r.detached ? r.detached + " 人" : "—"]
    ];
    info.innerHTML = rows.map(function (kv) {
      return "<dt>" + kv[0] + "</dt><dd>" + LT.esc(String(kv[1])) + "</dd>";
    }).join("");
    $("#rosterSyncBtn").disabled = !bound;
  }

  function bindRoster(rosterId) {
    LT.api("/api/admin/roster_bind", { method: "POST",
      body: { roster_id: rosterId } })
      .then(function () {
        ok(rosterId ? "已绑定名单" : "已取消绑定");
        return load();
      })
      .then(loadRosterSource)
      .catch(bad);
  }

  function syncRoster() {
    LT.api("/api/admin/roster_sync", { method: "POST" })
      .then(function (d) {
        var r = d.result || {};
        var msg = "新增 " + r.added + " 人，更新 " + r.updated + " 人";
        if (r.restored) msg += "，恢复 " + r.restored + " 人";
        if ((r.missing || []).length) {
          msg += "；" + r.missing.length + " 人已不在名单";
        }
        $("#rosterNote").textContent = msg;
        ok(msg);
        return load();
      })
      .then(loadRosterSource)
      .catch(bad);
  }

  function loadMembers() {
    LT.api("/api/admin/members").then(function (d) {
      S.members = d.members || [];
      S.seat = d.me || {};
      renderDangerZone();
      renderMembers();
      renderEventInfo();
      loadRosterSource();
    }).catch(function (e) {
      if (e.status === 401) { location.href = LT.panelLoginUrl(); return; }
      if (e.status === 403) {
        $("#memberTable tbody").innerHTML =
          '<tr><td colspan="5" class="empty">只有该活动的负责人能管理成员</td></tr>';
        return;
      }
      bad(e);
    });
  }

  function renderMembers() {
    var body = $("#memberTable tbody");
    if (!S.members || !S.members.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">还没有成员</td></tr>';
      return;
    }
    body.innerHTML = S.members.map(function (m) {
      var gone = m.state === "revoked" || m.state === "declined";
      var actions;
      if (m.state === "active") {
        actions = m.accountId === (S.seat || {}).accountId
          ? '<span class="note">你自己</span>'
          : '<button class="btn sm danger" data-member="revoke" data-acct="' +
            m.accountId + '">移出</button>';
      } else if (m.state === "pending") {
        actions = '<button class="btn sm" data-member="cancel" data-acct="' +
          m.accountId + '">取消邀请</button>';
      } else {
        actions = '<button class="btn sm" data-member="invite" data-acct="' +
          m.accountId + '">重新邀请</button>';
      }
      return '<tr' + (gone ? ' class="muted-row"' : "") + '>' +
        '<td><b>' + LT.esc(m.name || m.username) + '</b>' +
        '<br><span class="note">' + LT.esc(m.username) + '</span></td>' +
        '<td>' + LT.esc(m.roleLabel) + '</td>' +
        '<td>' + LT.esc(m.stateLabel) +
        (m.state === "pending" && m.invitedBy
          ? '<br><span class="note">' + LT.esc(m.invitedBy) + ' 邀请</span>' : '') +
        (gone && m.stateAt
          ? '<br><span class="note">' + LT.fmtDateTime(m.stateAt) + '</span>' : '') +
        '</td>' +
        '<td class="mono">' + (m.addedAt ? LT.fmtDateTime(m.addedAt) : '—') + '</td>' +
        '<td class="actions">' + actions + '</td></tr>';
    }).join('');
  }

  function renderEventInfo() {
    var cfg = S.config || {};
    var live = (S.members || []).filter(function (m) {
      return m.state === "active";
    }).length;
    var name = $("#eventName");
    if (name && document.activeElement !== name) name.value = cfg.title || "";
    var rows = [
      ["在场成员", live + " 人"],
      ["访问地址", S.publicBaseUrl || ""]
    ];
    $("#eventInfo").innerHTML = rows.map(function (r) {
      return "<dt>" + LT.esc(r[0]) + "</dt><dd>" + LT.esc(r[1]) + "</dd>";
    }).join("");
  }

  function saveEventName() {
    var box = $("#eventName");
    var note = $("#eventNameNote");
    var title = box ? box.value.trim() : "";
    if (!title) {
      if (note) note.textContent = "名称不能为空";
      return;
    }
    LT.api("/api/admin/event_info", { method: "POST", body: { title: title } })
      .then(function (d) {
        S.config.title = d.title;
        if (note) note.textContent = "已保存";
        renderHead();
        renderEventInfo();
        load().catch(function () {});
      })
      .catch(function (e) {
        if (note) note.textContent = LT.conflictMsg(e) || (e && e.message) || "保存失败";
      });
  }

  function memberAction(accountId, action) {
    var who = (S.members || []).filter(function (m) {
      return m.accountId === accountId;
    })[0] || {};
    var name = who.name || who.username || "这个人";
    var ask = {
      invite: { title: "邀请 " + name + "？",
                html: "对方会在自己的面板里收到邀请，确认之后才能进这个活动后台。",
                ok: "邀请", danger: false },
      revoke: { title: "移出 " + name + "？",
                html: "席位会变成「已移出」，姓名和加入时间留在名单里，随时可以重新邀请。",
                ok: "移出", danger: true },
      cancel: { title: "取消发给 " + name + " 的邀请？",
                html: "对方还没确认，取消之后这条邀请就作废了。",
                ok: "取消邀请", danger: true }
    }[action];
    if (!ask) return;
    LT.confirm({ title: ask.title, html: ask.html, okText: ask.ok, danger: ask.danger })
      .then(function (yes) {
        if (!yes) return;
        LT.api("/api/admin/member", {
          method: "POST", body: { accountId: accountId, action: action }
        }).then(function () {
          ok({ invite: "已发出邀请", revoke: "已移出", cancel: "已取消邀请" }[action]);
          loadMembers();
        }).catch(bad);
      });
  }

  function openInviteSearch() {
    var back = LT.modal(
      '<h2>邀请成员</h2>' +
      '<div class="field"><label>按登录名或称呼搜索</label>' +
      '<input id="invQ" type="text" autocomplete="off" autofocus></div>' +
      '<div id="invHits" class="pick-list"></div>' +
      '<div class="row" style="justify-content:flex-end;margin-top:12px">' +
      '<button class="btn" id="invDone">关闭</button></div>');
    $("#invDone").addEventListener("click", back.close);
    var box = $("#invQ");
    var timer = null;
    box.addEventListener("input", function () {
      clearTimeout(timer);
      var q = box.value.trim();
      if (!q) {
        $("#invHits").innerHTML = "";
        return;
      }
      timer = setTimeout(function () {
        LT.api("/api/admin/members/search?q=" + encodeURIComponent(q))
          .then(function (d) {
            var hits = d.accounts || [];
            $("#invHits").innerHTML = hits.length ? hits.map(function (a) {
              return '<button class="pick" type="button" data-pick="' + a.id + '">' +
                "<b>" + LT.esc(a.name || a.username) + "</b>" +
                '<span class="note">' + LT.esc(a.username) + " · " +
                LT.esc(a.roleLabel) + "</span></button>";
            }).join("") : '<p class="note">没有匹配的账号</p>';
          }).catch(bad);
      }, 220);
    });
    box.focus();
    $("#invHits").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-pick]");
      if (!btn) return;
      back.close();
      memberAction(Number(btn.getAttribute("data-pick")), "invite");
    });
  }

  function bindSettings() {
    $("#saveSettingsBtn").addEventListener("click", saveSettings);
    $("#setPoolMode").addEventListener("change", function () {
      S.config.pool_mode = $("#setPoolMode").value;
      updatePoolPreview();
    });
    $("#setBonusConstant").addEventListener("input", updatePoolPreview);
    $("#savePrizesBtn").addEventListener("click", savePrizes);
    // Anything typed or toggled here outranks a reload from another session.
    var tab = $("#tab-config");
    if (tab) {
      ["input", "change"].forEach(function (evt) {
        tab.addEventListener(evt, function (e) {
          if (e.target && e.target.tagName !== "BUTTON") formTouched = true;
        });
      });
    }
  }

  // ------------------------------------------------------------------ boot
  /* 正在往表单里打字就别重画；搜索框随时都是焦点，不算在内 */
  function isTyping() {
    var el = document.activeElement;
    if (!el || !/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return false;
    return el.type !== "search";
  }

  /* 服务有效性：服务端按一次读数推出来的提示（占用偏高、盘快写不进去了）。
     只读、不进库；条件消失，提示跟着消失。 */
  function drawService(items) {
    var box = $("#svcChips");
    if (!box) return;
    box.innerHTML = (items || []).map(function (it) {
      return '<span class="chip svc ' + LT.esc(it.level || "info") + '" title="' +
        LT.esc(it.body || "") + '">' + LT.esc(it.title) + "</span>";
    }).join("");
  }

  function loadService() {
    return LT.api("/api/service")
      .then(function (d) { drawService(d && d.items); })
      .catch(function () { /* 读不到就不提示，不自己造一条 */ });
  }

  function boot() {
    bindGlobal();
    bindSettings();

    // 模块算完计划会发这个事件：平台据此更新结算/重置按钮
    document.addEventListener("module-plan", renderDrawButtons);

    $("#finalizeBtn").addEventListener("click", finalize);
    $("#unfinalizeBtn").addEventListener("click", unfinalize);
    $("#resetTicketsBtn").addEventListener("click", resetTickets);
    $("#phaseSigninBtn").addEventListener("click", function () { setPhase("signin"); });
    $("#phaseDrawBtn").addEventListener("click", function () { setPhase("draw"); });
    $("#shortlinkBtn").addEventListener("click", mintShortlink);
    $("#memberInviteBtn").addEventListener("click", openInviteSearch);
    $("#eventNameSave").addEventListener("click", saveEventName);
    $("#rosterSyncBtn").addEventListener("click", syncRoster);
    $("#rosterPick").addEventListener("change", function () {
      bindRoster(Number(this.value) || 0);
    });
    $("#memberTable").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-member]");
      if (!btn) return;
      memberAction(Number(btn.getAttribute("data-acct")),
                   btn.getAttribute("data-member"));
    });

    $("#prizeAddBtn").addEventListener("click", function () {
      var list = collectPrizes().concat([{ name: "", count: 1, mode: "ticket", batch: false }]);
      S.config.prizes = list;
      pageRefresh("draw");
      var inputs = $$("#prizeEditor .p-name");
      if (inputs.length) inputs[inputs.length - 1].focus();
    });

    load().then(function () {
      if (S.config.finalized) showTab("draw");
    });

    // who is signed in: the console has no account of its own any more
    LT.api("/api/me").then(function (d) {
      var who = $("#whoami");
      if (who && d.authed) who.textContent = d.user + (d.role === "owner" ? " · 负责人" : "");
    }).catch(function () { /* the state call reports a dead session first */ });

    // another operator's writes arrive through the stream; the poll stays as a
    // fallback for a stream that cannot connect
    LT.watch(function (revs, before) {
      if (!before || JSON.stringify(revs) === JSON.stringify(before)) return;
      if (document.hidden) return;
      if (isTyping()) return;
      clearTimeout(S.remoteTimer);
      S.remoteTimer = setTimeout(function () { load().catch(function () {}); }, 300);
    });

    // 服务降级提示：进来先读一次，之后半分钟对一次
    loadService();
    setInterval(loadService, 30000);

    // keep the unlock countdown honest, and quietly resync every 15s
    setInterval(renderUnlock, 1000);
    setInterval(function () {
      if (document.hidden || document.querySelector(".modal-back")) return;
      if (isTyping()) return;
      LT.api("/api/admin/state").then(function (d) {
        applyState(d);
      }).catch(function (e) { if (e.status === 401) location.href = LT.panelLoginUrl(); });
    }, 5000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
