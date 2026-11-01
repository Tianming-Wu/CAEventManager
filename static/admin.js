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
    unlockUntil: 0,
    q: "",
    hits: [],
    sel: 0,
    rosterQ: "",
    sortKey: "sid",
    sortDir: 1,
    filter: "all",
    plan: null,
    tab: "ops"
  };

  // ------------------------------------------------------------------ utils
  function kindLabel(k) { return LT.KIND_LABEL[k] || k; }

  function canMark(stu, kind) {
    if (stu.marks[kind]) return false;
    if (S.config.finalized) return false;
    var need = PREV[kind];
    return !need || !!stu.marks[need];
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

  function ticketChips(stu) {
    var list = (stu.tickets || []).slice().sort(function (a, b) { return a.code - b.code; });
    return list.map(function (t) {
      return '<span class="tkt' + (t.kind === "bonus" ? " bonus" : "") + '" title="' +
        (LT.KIND_LABEL[t.kind] || t.kind) + '">' + LT.code(t.code) + "</span>";
    }).join(" ");
  }

  var ROLE_TAG = { staff: "工作人员", volunteer: "志愿者" };

  function roleTag(stu) {
    var label = ROLE_TAG[stu.role];
    return label ? ' <span class="role-tag">' + label + "</span>" : "";
  }

  function stepBadges(stu) {
    if (!isParticipant(stu)) {
      return stu.marks.checkin
        ? '<span class="step done">到场 ✓</span>'
        : '<span class="step next">到场</span>';
    }
    return KINDS.map(function (k) {
      if (stu.marks[k]) {
        return '<span class="step done">' + kindLabel(k) + " ✓</span>";
      }
      if (stu.next === k && !S.config.finalized) {
        return '<span class="step next">' + kindLabel(k) + "</span>";
      }
      return '<span class="step">' + kindLabel(k) + "</span>";
    }).join("");
  }

  function nextLabel(stu) {
    if (S.config.finalized) return '<span class="next none">已结算</span>';
    if (!stu.next) return '<span class="next none">已完成</span>';
    return '<span class="next">下一步 ' + kindLabel(stu.next) + "</span>";
  }

  // ------------------------------------------------------------------ toasts
  function ok(html) { LT.toast(html, false); }
  function bad(err) { LT.toast(LT.esc(err && err.message ? err.message : String(err)), true, 4200); }

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
    $("#nRoster").textContent = st.total;
    document.title = "后台 · " + (S.config.title || "抽奖");
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

  function filtered() {
    var q = S.q.trim();
    var list = S.students;
    if (q) {
      list = LT.search(S.students, q, 999);
    }
    return list.filter(function (s) {
      switch (S.filter) {
        case "in": return !!s.marks.checkin;
        case "out": return !s.marks.checkin;
        case "todo": return s.marks.checkin && (s.tasks < 2);
        case "done": return s.tasks === 2;
        case "zero": return Object.keys(s.tickets || {}).length === 0;
        default: return true;
      }
    });
  }

  function renderHits() {
    var box = $("#hits");
    if (!S.q.trim()) {
      box.innerHTML = S.students.length
        ? '<div class="empty">输入姓名、拼音或学号</div>'
        : '<div class="empty">名单还是空的 —— 去「名单」页导入。</div>';
      return;
    }
    if (!S.hits.length) {
      box.innerHTML = '<div class="empty">没有匹配的学生</div>';
      return;
    }
    box.innerHTML = S.hits.map(function (s, i) {
      return '<div class="hit' + (i === S.sel ? " sel" : "") + '" data-id="' + s.id + '" data-i="' + i + '">' +
        '<div class="who"><span class="nm">' + LT.esc(s.name) + "</span>" + roleTag(s) +
        '<span class="sid">' + LT.esc(s.sid) + "</span>" +
        '<div class="steps">' + stepBadges(s) + "</div></div>" +
        '<div class="side">' + ticketChips(s) + nextLabel(s) + "</div></div>";
    }).join("");
  }

  function actionButtons(stu, compact) {
    if (!isParticipant(stu)) {
      var shown = !!stu.marks.checkin;
      var html = '<button class="btn sm ' + (shown ? "" : "primary") + '" data-mark="checkin" data-id="' +
        stu.id + '"' + (shown ? " disabled" : "") + ">" + (shown ? "到场 ✓" : "到场") + "</button>";
      if (shown) {
        html += ' <button class="btn sm danger" data-undo="checkin" data-id="' + stu.id + '"' +
          (S.unlocked ? "" : " disabled") + ">撤销</button>";
      }
      return html;
    }
    var btns = [];
    KINDS.forEach(function (k, idx) {
      var can = canMark(stu, k);
      if (stu.marks[k]) {
        btns.push('<button class="btn sm danger" data-undo="' + k + '" data-id="' + stu.id + '"' +
          (S.unlocked ? "" : " disabled title=\"先打开解锁修改\"") + ">撤 " + kindLabel(k) + "…</button>");
      } else {
        btns.push('<button class="btn sm ' + (stu.next === k ? "green" : "") + '" data-mark="' + k +
          '" data-id="' + stu.id + '"' + (can ? "" : " disabled") + ">" +
          (compact ? (idx + 1) : "") + kindLabel(k) + "</button>");
      }
    });
    return btns.join(" ");
  }

  function timeCell(ts, kind) {
    if (!ts) return '<td class="num" style="color:var(--dim)">—</td>';
    return '<td class="num" title="' + LT.fmtDateTime(ts) + '">' + LT.fmtTime(ts) + "</td>";
  }

  function renderTable() {
    var rows = filtered();
    var tbody = $("#rosterTable tbody");
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">没有符合条件的学生</td></tr>';
    } else {
      tbody.innerHTML = rows.map(function (s) {
        return '<tr class="' + (s.tasks === 2 ? "done" : "") + '">' +
          '<td><b>' + LT.esc(s.name) + "</b>" + roleTag(s) + "</td>" +
          '<td class="num">' + LT.esc(s.sid) + "</td>" +
          '<td style="color:var(--muted)">' + LT.esc(s.grp || "") + "</td>" +
          timeCell(s.marks.checkin) + timeCell(s.marks.task1) + timeCell(s.marks.task2) +
          "<td>" + (ticketChips(s) || '<span style="color:var(--dim)">—</span>') + "</td>" +
          '<td class="right">' + actionButtons(s, false) + "</td></tr>";
      }).join("");
    }
    $("#rosterNote").textContent = "显示 " + rows.length + " / " + S.students.length + " 人";
  }

  function rosterRows() {
    var q = S.rosterQ.trim().toLowerCase();
    var list = q ? LT.search(S.students, q, 999) : S.students.slice();
    var key = S.sortKey;
    var dir = S.sortDir;
    var rank = { participant: 0, staff: 1, volunteer: 2 };
    list.sort(function (a, b) {
      var av, bv;
      if (key === "checkin" || key === "task1" || key === "task2") {
        av = a.marks[key] || 0;
        bv = b.marks[key] || 0;
      } else if (key === "tickets") {
        av = (a.tickets || []).length;
        bv = (b.tickets || []).length;
      } else if (key === "name") {
        av = a.py || a.name;
        bv = b.py || b.name;
      } else if (key === "role") {
        av = rank[a.role || "participant"] || 0;
        bv = rank[b.role || "participant"] || 0;
      } else {
        av = a[key] || "";
        bv = b[key] || "";
      }
      if (av < bv) return -dir;
      if (av > bv) return dir;
      return (a.sid || "").localeCompare(b.sid || "");
    });
    return list;
  }

  function renderFullTable() {
    var tbody = $("#fullTable tbody");
    var rows = rosterRows();
    $$("#fullTable th[data-sort]").forEach(function (th) {
      th.classList.toggle("sorted", th.getAttribute("data-sort") === S.sortKey);
      th.setAttribute("data-dir", th.getAttribute("data-sort") === S.sortKey
        ? (S.sortDir > 0 ? "asc" : "desc") : "");
    });
    tbody.innerHTML = rows.map(function (s) {
      return "<tr>" +
        '<td class="num">' + LT.esc(s.sid) + "</td>" +
        '<td><b>' + LT.esc(s.name) + "</b></td>" +
        '<td style="color:var(--muted)">' + LT.esc(s.grp || "") + "</td>" +
        '<td style="color:var(--muted)">' + (ROLE_TAG[s.role] || "参加人员") + "</td>" +
        timeCell(s.marks.checkin) + timeCell(s.marks.task1) + timeCell(s.marks.task2) +
        "<td>" + (ticketChips(s) || "—") + "</td>" +
        '<td class="right">' +
        '<button class="btn sm" data-edit="' + s.id + '">编辑</button> ' +
        '<button class="btn sm ' + (s.next ? "green" : "") + '" data-row="' + s.id + '">选中</button>' +
        "</td></tr>";
    }).join("") || '<tr><td colspan="9" class="empty">没有匹配的学生</td></tr>';
    $("#rosterCount").textContent = rows.length === S.students.length
      ? S.students.length + " 人"
      : rows.length + " / " + S.students.length + " 人";
  }

  function renderLog() {
    $("#logTable tbody").innerHTML = S.audit.map(function (a) {
      return "<tr><td class='num'>" + LT.fmtDateTime(a.ts) + "</td>" +
        "<td>" + LT.esc(a.action) + "</td>" +
        "<td style='white-space:normal'>" + LT.esc(a.detail || "") + "</td></tr>";
    }).join("");
  }

  // Mirrors model.normalize_prizes(): a stored prize may still be a bare name.
  function normalizePrizes(list) {
    return (list || []).map(function (p) {
      if (typeof p === "string") return { name: p, count: 1, mode: "ticket" };
      return {
        name: (p && p.name) || "",
        count: Number(p && p.count) || 0,
        mode: p && p.mode === "person" ? "person" : "ticket"
      };
    }).filter(function (p) { return p.name; });
  }

  function renderPrizeList() {
    var body = $("#prizeTable tbody");
    if (!body) return;
    var prizes = S.prizes || [];
    if (!prizes.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">还没有配置奖品</td></tr>';
      return;
    }
    body.innerHTML = prizes.map(function (p, i) {
      var done = p.remaining <= 0;
      var blocked = p.mode === "ticket" && !S.config.finalized;
      var label = p.mode === "person" ? "均等" : "按票";
      var why = done ? "已抽完" : (blocked ? "需先结算奖票" : "");
      return "<tr" + (done ? ' class="done-row"' : "") + ">" +
        "<td><b>" + LT.esc(p.name) + "</b></td>" +
        '<td style="color:var(--muted)">' + label + "</td>" +
        '<td class="right left-n">' + p.drawn + "</td>" +
        '<td class="right left-n">' + p.remaining + " / " + p.count + "</td>" +
        '<td class="right"><button class="btn draw-btn ' + (done ? "" : "primary") +
        '" data-draw="' + i + '"' + (done || blocked ? " disabled" : "") +
        ' title="' + why + '">抽</button></td></tr>';
    }).join("");
  }

  function renderPrizeEditor() {
    var box = $("#prizeEditor");
    if (!box) return;
    var prizes = normalizePrizes(S.config.prizes);
    box.innerHTML = prizes.map(function (p, i) {
      return '<div class="prize-row" data-i="' + i + '">' +
        '<input class="p-name" type="text" value="' + LT.esc(p.name) + '" placeholder="奖品名称">' +
        '<input class="p-count" type="number" min="0" max="999" value="' + p.count + '">' +
        '<select class="p-mode">' +
        '<option value="ticket"' + (p.mode === "ticket" ? " selected" : "") + ">按票</option>" +
        '<option value="person"' + (p.mode === "person" ? " selected" : "") + ">均等</option>" +
        "</select>" +
        '<button class="btn sm danger" data-prize-del="' + i + '">删除</button>' +
        "</div>";
    }).join("") || '<p class="note">还没有配置奖品</p>';
  }

  function renderDraws() {
    $("#drawTable tbody").innerHTML = S.draws.map(function (d) {
      var voided = !!d.voided_at;
      var person = d.mode === "person";
      return "<tr" + (voided ? ' style="opacity:.4"' : "") + ">" +
        '<td class="num right">' + d.seq + "</td>" +
        "<td>" + LT.esc(d.prize || "") + "</td>" +
        "<td>" + (person
          ? '<span class="chip" style="padding:1px 7px;font-size:.75rem">人级均等</span>'
          : '<span class="chip" style="padding:1px 7px;font-size:.75rem">按票</span>') + "</td>" +
        '<td class="num">' + (person ? '<span style="color:var(--dim)">—</span>'
          : '<span class="tkt">' + LT.code(d.code) + "</span>") + "</td>" +
        "<td><b>" + LT.esc(d.name || "?") + "</b> <span style='color:var(--dim)'>" + LT.esc(d.sid || "") + "</span></td>" +
        '<td class="num">' + LT.fmtDateTime(d.ts) + "</td>" +
        '<td class="right">' + (voided
          ? '<span class="note">已作废</span>'
          : '<button class="btn sm danger" data-void="' + d.id + '"' + (S.unlocked ? "" : " disabled") + ">作废</button>") +
        "</td></tr>";
    }).join("") || '<tr><td colspan="7" class="empty">还没有抽奖记录</td></tr>';
  }

  function renderPlan() {
    var p = S.plan;
    if (!p) return;
    var sum = p.summary || {};
    var chips = [
      '<span class="chip green"><b>' + sum.present + "</b> 人可参与</span>",
      '<span class="chip">保底 <b>' + sum.guaranteed + "</b> 张</span>",
      '<span class="chip gold">速度奖励 <b>' + sum.bonus_pool + "</b> 张</span>",
      '<span class="chip">总计 <b>' + sum.pool_size + "</b> 张全部发完</span>",
      '<span class="chip">β = <b>' + sum.beta + "</b></span>"
    ];
    chips.push('<span class="chip">' + (sum.pool_mode === "fixed"
      ? "固定池 " + sum.fixed_pool_size + " 张"
      : "自动池 = " + sum.present + "×3 + " + sum.bonus_constant) + "</span>");
    if (sum.present) {
      chips.push('<span class="chip blue">最多一人 <b>+' + sum.max_bonus + "</b> 张</span>");
    }
    if (sum.finalized) chips.push('<span class="chip gold live">已结算</span>');
    if (!sum.allow_bonus) chips.push('<span class="chip">速度奖励已关闭</span>');
    $("#planSummary").innerHTML = chips.join("");

    $("#planTable tbody").innerHTML = (p.rows || []).map(function (r) {
      var stu = S.byId[r.id] || {};
      return "<tr>" +
        '<td class="num right">' + r.pos + "</td>" +
        "<td><b>" + LT.esc(r.name) + "</b> <span style='color:var(--dim);font-size:.8em'>" + LT.esc(r.sid) + "</span></td>" +
        '<td class="num right">' + r.tasks + "</td>" +
        '<td class="num right">' + r.mean_rank.toFixed(3) + "</td>" +
        '<td class="num right">' + r.weight.toFixed(2) + "</td>" +
        '<td class="num right">' + r.expected.toFixed(2) + "</td>" +
        '<td class="num right">' + (r.bonus ? '<b style="color:var(--gold)">+' + r.bonus + "</b>" : '<span style="color:var(--dim)">0</span>') + "</td>" +
        '<td class="num right">' + (stu.ticket_count || 0) + "</td></tr>";
    }).join("") || '<tr><td colspan="8" class="empty">还没有人签到</td></tr>';

    $("#planNote").textContent = sum.finalized
      ? "已结算于 " + LT.fmtDateTime(S.config.finalized_at)
      : "预览，点击「结算」才会真正发票";
    $("#finalizeBtn").disabled = !!sum.finalized;
    $("#unfinalizeBtn").disabled = !sum.finalized || !S.unlocked;
    $("#resetTicketsBtn").disabled = !S.unlocked;
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

  function mintShortlink() {
    LT.api("/api/admin/shortlink", { method: "POST", body: { target: "screen", minutes: 60 } })
      .then(function (d) {
        $("#shortlinkBox").innerHTML =
          '<div class="winner" style="flex-direction:column;align-items:flex-start;gap:6px">' +
          '<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">' +
          '<span class="prize">短链接</span>' +
          '<span class="mono" style="font-size:1.15rem;font-weight:700">' + LT.esc(d.url) + "</span>" +
          "</div>" +
          '<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">' +
          '<span class="prize">只输数字</span>' +
          '<span class="mono" style="font-size:1.5rem;font-weight:700;letter-spacing:.15em">' +
          LT.esc(d.code) + "</span>" +
          '<span class="note">在投屏电脑打开上面的网址，或直接打开任意页面后把地址换成 /s/' +
          LT.esc(d.code) + "</span>" +
          "</div>" +
          '<span class="note" id="shortlinkLeft">有效期 ' + d.minutes + " 分钟</span>" +
          "</div>";
        $("#shortlinkNote").textContent = "已生成，指向大屏页";
        var left = d.minutes * 60;
        clearInterval(mintShortlink.timer);
        mintShortlink.timer = setInterval(function () {
          left -= 1;
          var el = $("#shortlinkLeft");
          if (left <= 0) {
            clearInterval(mintShortlink.timer);
            if (el) el.textContent = "已过期";
            return;
          }
          if (el) el.textContent = "还剩 " + Math.floor(left / 60) + ":" + LT.pad(left % 60);
        }, 1000);
      })
      .catch(bad);
  }

  function renderSettings() {
    var c = S.config;
    $("#setTitle").value = c.title || "";
    $("#setSubtitle").value = c.subtitle || "";
    $("#setBaseUrl").value = S.publicBaseUrl || c.public_base_url || "";
    $("#setShowTimes").checked = !!c.show_times;
    $("#setShowSid").checked = !!c.show_sid;
    $("#setScreenWall").checked = !!c.screen_show_wall;
    $("#setPoolMode").value = c.pool_mode || "auto";
    $("#setPool").value = c.pool_size;
    $("#setBonusConstant").value = c.bonus_constant;
    $("#setBeta").value = c.beta;
    $("#setAllowBonus").checked = c.allow_bonus !== false;
    renderPrizeEditor();
    $("#setUser").value = c.admin_user || "";
    $("#exportLink").href = LT.PREFIX + "/api/admin/export.csv";
    $("#exportZipLink").href = LT.PREFIX + "/api/admin/export.zip";
    $("#secretUrl").textContent = S.publicBaseUrl + "/";
    updatePoolPreview();
    renderQR();
  }

  function updatePoolPreview() {
    var st = computeStats();
    var auto = (S.config.pool_mode || "auto") !== "fixed";
    $("#poolPreview").innerHTML =
      "当前：到场 <b>" + st.checkin + "</b> 人 → 奖票池 <b>" + st.pool_size + "</b> 张" +
      "（保底 " + st.guaranteed + " + 速度奖励 " + st.bonus_pool + "）" +
      (auto ? "。<br>之后每多签到一人，池子自动 +3。" : "。<br>固定模式下池子不随人数变化。");
    $("#setPool").disabled = auto;
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
    renderPrizeList();
    renderUnlock();
    renderHits();
    renderTable();
    renderFullTable();
    renderDraws();
    renderLog();
    if (S.tab === "settings") renderSettings();
    if (S.tab === "tickets") renderPlan();
  }

  // ------------------------------------------------------------------ data
  function applyState(d) {
    S.config = d.config || {};
    S.prizes = d.prizes || [];
    S.publicBaseUrl = (d.publicBaseUrl || S.config.public_base_url || "").replace(/\/+$/, "");
    S.students = d.students || [];
    S.byId = {};
    S.students.forEach(function (s) { S.byId[s.id] = s; });
    S.audit = d.audit || [];
    S.draws = d.draws || [];
    S.unlocked = !!d.unlocked;
    S.unlockUntil = d.unlockUntil || 0;
    refreshHits();
    renderAll();
  }

  function load() {
    return LT.api("/api/admin/state").then(applyState).catch(function (e) {
      if (e.status === 401) { location.href = LT.PREFIX + "/login"; return; }
      bad(e);
    });
  }

  function loadPlan() {
    return LT.api("/api/admin/plan").then(function (d) {
      S.plan = d;
      renderPlan();
    }).catch(bad);
  }

  function refreshHits() {
    if (!S.q.trim()) { S.hits = []; return; }
    S.hits = LT.search(S.students, S.q, 8);
    if (S.sel >= S.hits.length) S.sel = Math.max(0, S.hits.length - 1);
  }

  function patchStudent(stu) {
    if (!stu) return;
    var i = S.students.findIndex(function (s) { return s.id === stu.id; });
    if (i >= 0) S.students[i] = stu; else S.students.push(stu);
    S.byId[stu.id] = stu;
    // the hit list holds its own references; without this the search result row
    // would keep showing the pre-action state until the next poll.
    for (var j = 0; j < S.hits.length; j++) {
      if (S.hits[j].id === stu.id) S.hits[j] = stu;
    }
  }

  // ------------------------------------------------------------------ actions
  function mark(studentId, kind) {
    var stu = S.byId[studentId];
    if (!stu) return;
    if (stu.marks[kind]) { ok("<b>" + LT.esc(stu.name) + "</b> 的" + kindLabel(kind) + "早就完成了"); return; }
    if (S.config.finalized) { bad("已结算奖票，如需继续操作请先取消结算（需解锁）"); return; }
    var need = PREV[kind];
    if (need && !stu.marks[need]) { bad("需要先完成「" + kindLabel(need) + "」"); return; }

    LT.api("/api/admin/mark", { method: "POST", body: { student_id: studentId, kind: kind } })
      .then(function (d) {
        patchStudent(d.student);
        renderAll();
        var code = d.result && d.result.code;
        var suffix = d.result && d.result.already
          ? "（之前已完成）"
          : ' · 奖票 <b>#' + LT.code(code) + "</b>";
        ok("<b>" + LT.esc(stu.name) + "</b> " + kindLabel(kind) + " 完成" + suffix);
      })
      .catch(function (e) {
        bad(e);
        if (e.status === 409) load();
      });
  }

  function undo(studentId, kind) {
    var stu = S.byId[studentId];
    if (!stu) return;
    if (!S.unlocked) { bad("撤销需要先打开右上角的「解锁修改」开关"); return; }
    var deps = kind === "checkin"
      ? ["task1", "task2"].filter(function (k) { return stu.marks[k]; })
      : (kind === "task1" && stu.marks.task2 ? ["task2"] : []);

    var run = function (cascade) {
      LT.api("/api/admin/undo", {
        method: "POST",
        body: { student_id: studentId, kind: kind, cascade: cascade }
      })
        .then(function (d) {
          S.unlockUntil = d.unlockUntil || S.unlockUntil;
          patchStudent(d.student);
          renderAll();
          ok("<b>" + LT.esc(stu.name) + "</b> 已撤销 " +
            (d.result.released || []).map(kindLabel).join("、"));
        })
        .catch(function (e) { bad(e); if (e.status === 403) { S.unlocked = false; renderUnlock(); } });
    };

    if (deps.length) {
      LT.confirm({
        title: "级联撤销",
        danger: true,
        okText: "一起撤销",
        html: "撤销 <b>" + kindLabel(kind) + "</b> 会同时撤销后续已完成的 <b>" +
          deps.map(kindLabel).join("、") + "</b>，并收回对应奖票。<br>确认要撤销 <b>" + LT.esc(stu.name) + "</b> 的这些记录吗？"
      }).then(function (yes) { if (yes) run(true); });
    } else {
      run(false);
    }
  }

  function resetInput() {
    $("#q").value = "";
    S.q = "";
    S.hits = [];
    S.sel = 0;
    renderHits();
    renderTable();
  }

  function selectedHit() {
    return S.hits[S.sel] || null;
  }

  // Keep the keyboard fast-path usable after a mouse click: on the ops tab the
  // search box should hold focus unless the operator is typing somewhere else.
  function refocus() {
    if (S.tab !== "ops") return;
    var el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.id !== "q") return;
    if (document.querySelector(".modal-back")) return;
    $("#q").focus();
  }

  // ------------------------------------------------------------------ events
  // Browsers happily treat a lone search box as a login field and autofill the
  // saved account name into it.  Anything that appears without a keystroke is
  // not the operator's input, so drop it before it confuses the roster filter.
  function guardAutofill(input, onClear) {
    var touched = false;
    var scrub = function () {
      if (touched || !input.value) return;
      input.value = "";
      if (onClear) onClear();
    };
    input.addEventListener("keydown", function () { touched = true; });
    input.addEventListener("focus", scrub);
    input.addEventListener("mousedown", function () { setTimeout(scrub, 0); });
    input.addEventListener("input", function (e) {
      if (e.isTrusted) touched = true;
    });
    [300, 1200, 2600].forEach(function (ms) { setTimeout(scrub, ms); });
    window.addEventListener("load", scrub);
    return scrub;
  }

  function bindSearch() {
    var q = $("#q");
    q.addEventListener("input", function () {
      S.q = q.value;
      S.sel = 0;
      refreshHits();
      renderHits();
      renderTable();
    });
    q.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (S.hits.length) { S.sel = (S.sel + 1) % S.hits.length; renderHits(); }
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (S.hits.length) { S.sel = (S.sel - 1 + S.hits.length) % S.hits.length; renderHits(); }
        return;
      }
      if (e.key === "Escape") {
        if (q.value) { e.preventDefault(); resetInput(); }
        return;
      }
      // Undo: Ctrl+Shift+Z.  Nothing here uses Alt - Alt+Z opens the NVIDIA
      // overlay on machines with the NVIDIA app, and other vendor tools grab
      // Alt+digits too, so those combos are off limits.
      if (e.ctrlKey && e.shiftKey && e.code === "KeyZ") {
        e.preventDefault();
        var s = selectedHit();
        if (!s) return;
        var done = KINDS.filter(function (k) { return s.marks[k]; });
        if (!done.length) { ok("没有可撤销的步骤"); return; }
        undo(s.id, done[done.length - 1]);
        return;
      }
      // Step override: Shift+1/2/3 (e.code so keyboard layout and shift
      // symbols cannot break it).
      var digit = { Digit1: 0, Digit2: 1, Digit3: 2 }[e.code];
      if (e.shiftKey && !e.ctrlKey && !e.altKey && digit !== undefined) {
        e.preventDefault();
        var stu = selectedHit();
        if (!stu) return;
        mark(stu.id, KINDS[digit]);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        var target = selectedHit();
        if (!target) {
          var cands = filtered();
          if (cands.length === 1) { target = cands[0]; }
        }
        if (!target) { bad("没有选中学生"); return; }
        if (!target.next) { ok("<b>" + LT.esc(target.name) + "</b> 已经全部完成了"); return; }
        mark(target.id, target.next);
      }
    });

    // right-click clears the box: faster than selecting the text with the mouse
    q.addEventListener("contextmenu", function (e) {
      if (!q.value) return;
      e.preventDefault();
      S.q = "";
      S.hits = [];
      S.sel = 0;
      q.value = "";
      renderHits();
      renderTable();
    });

    guardAutofill(q, function () {
      S.q = "";
      S.hits = [];
      S.sel = 0;
      renderHits();
      renderTable();
    });

    $("#hits").addEventListener("click", function (e) {
      var hit = e.target.closest(".hit");
      if (!hit) return;
      S.sel = Number(hit.getAttribute("data-i"));
      renderHits();
      var stu = S.byId[Number(hit.getAttribute("data-id"))];
      if (stu && stu.next) mark(stu.id, stu.next);
    });
  }

  function bindGlobal() {
    document.addEventListener("click", function (e) {
      var t = e.target;
      var mk = t.getAttribute && t.getAttribute("data-mark");
      if (mk) { mark(Number(t.getAttribute("data-id")), mk); refocus(); return; }
      var un = t.getAttribute && t.getAttribute("data-undo");
      if (un) { undo(Number(t.getAttribute("data-id")), un); refocus(); return; }
      var dp = t.getAttribute && t.getAttribute("data-draw");
      if (dp) { drawPrize(Number(dp)); return; }
      var pd = t.getAttribute && t.getAttribute("data-prize-del");
      if (pd) {
        var kept = collectPrizes();
        kept.splice(Number(pd), 1);
        S.config.prizes = kept;
        renderPrizeEditor();
        return;
      }
      var vd = t.getAttribute && t.getAttribute("data-void");
      if (vd) { voidDraw(Number(vd)); return; }
      var ed = t.getAttribute && t.getAttribute("data-edit");
      if (ed) { editStudent(Number(ed)); return; }
      var row = t.getAttribute && t.getAttribute("data-row");
      if (row) {
        var s = S.byId[Number(row)];
        showTab("ops");
        $("#q").value = s.name;
        S.q = s.name;
        S.sel = 0;
        refreshHits();
        renderHits();
        renderTable();
        $("#q").focus();
        return;
      }
    });

    $("#tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-tab]");
      if (btn) showTab(btn.getAttribute("data-tab"));
    });

    $("#filters").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-filter]");
      if (!btn) return;
      S.filter = btn.getAttribute("data-filter");
      $$("#filters button").forEach(function (b) { b.classList.toggle("primary", b === btn); });
      renderTable();
    });

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
      LT.api("/api/logout", { method: "POST" }).then(function () {
        location.href = LT.PREFIX + "/login";
      }).catch(bad);
    });

    $("#refreshBtn").addEventListener("click", function () { load().then(function () { ok("已刷新"); }); });

    $("#rosterFilter").addEventListener("input", function (e) {
      S.rosterQ = e.target.value;
      renderFullTable();
    });
    $("#fullTable").addEventListener("click", function (e) {
      var th = e.target.closest("th[data-sort]");
      if (!th) return;
      var key = th.getAttribute("data-sort");
      if (S.sortKey === key) S.sortDir = -S.sortDir;
      else { S.sortKey = key; S.sortDir = 1; }
      renderFullTable();
    });
    $("#logRefreshBtn").addEventListener("click", function () { load().then(function () { ok("已刷新"); }); });

    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== $("#q") && !document.querySelector(".modal-back")) {
        e.preventDefault();
        showTab("ops");
        $("#q").focus();
      }
    });
  }

  function setUnlock(on) {
    LT.api("/api/admin/unlock", { method: "POST", body: { on: on } }).then(function (d) {
      S.unlocked = d.unlocked;
      S.unlockUntil = d.unlockUntil;
      renderUnlock();
      renderTable();
      renderPlan();
      renderDraws();
      ok(on ? "已解锁" : "已上锁");
    }).catch(bad);
  }

  // ------------------------------------------------------------------ tabs
  function showTab(name) {
    S.tab = name;
    $$("#tabs button").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-tab") === name); });
    $$("main > section").forEach(function (s) { s.classList.toggle("hidden", s.id !== "tab-" + name); });
    if (name === "ops") { $("#q").focus(); }
    if (name === "tickets") loadPlan();
    if (name === "settings") renderSettings();
    if (name === "log") load();
    return name;
  }

  // ------------------------------------------------------------------ roster ops
  function editStudent(id) {
    var s = S.byId[id];
    if (!s) return;
    var m = LT.modal(
      '<h2>编辑 ' + LT.esc(s.name) + "</h2>" +
      '<div class="field"><label>姓名</label><input id="edName" type="text" value="' + LT.esc(s.name) + '"></div>' +
      '<div class="field"><label>学号</label><input id="edSid" type="text" value="' + LT.esc(s.sid) + '" disabled></div>' +
      '<div class="field"><label>班级</label><input id="edGrp" type="text" value="' + LT.esc(s.grp || "") + '"></div>' +

      '<div class="field"><label>备注</label><input id="edNote" type="text" value="' + LT.esc(s.note || "") + '"></div>' +
      '<div class="field"><label>身份</label><select id="edRole">' +
      ['participant', 'staff', 'volunteer'].map(function (r) {
        return '<option value="' + r + '"' + ((s.role || "participant") === r ? " selected" : "") + ">" +
          ({ participant: "参加人员", staff: "工作人员", volunteer: "志愿者" })[r] + "</option>";
      }).join("") + "</select></div>" +
      '<div class="row" style="justify-content:space-between;margin-top:14px">' +
      '<button class="btn danger" id="edDel"' + (S.unlocked ? "" : ' disabled title="先解锁"') + ">删除此人</button>" +
      '<div class="row"><button class="btn" id="edCancel">取消</button>' +
      '<button class="btn primary" id="edSave">保存</button></div></div>'
    );
    $("#edCancel", m).onclick = function () { m.close(); };
    $("#edSave", m).onclick = function () {
      LT.api("/api/admin/student", {
        method: "POST",
        body: {
          id: id,
          name: $("#edName", m).value.trim(),
          grp: $("#edGrp", m).value.trim(),
          note: $("#edNote", m).value.trim(),
          role: $("#edRole", m).value
        }
      }).then(function () { m.close(); return load(); }).then(function () { ok("已保存"); }).catch(bad);
    };
    $("#edDel", m).onclick = function () {
      LT.confirm({
        title: "删除学生",
        danger: true,
        okText: "删除",
        html: "会同时删除 <b>" + LT.esc(s.name) + "</b> 的标记与奖票，且不能恢复。确认？"
      }).then(function (yes) {
        if (!yes) return;
        LT.api("/api/admin/student_delete", { method: "POST", body: { id: id } })
          .then(function () { m.close(); return load(); })
          .then(function () { ok("已删除"); })
          .catch(bad);
      });
    };
  }

  function importRoster() {
    var text = $("#importText").value;
    if (!text.trim()) { bad("先粘贴或选择名单内容"); return; }
    LT.api("/api/admin/import", { method: "POST", body: { text: text } })
      .then(function (d) {
        var r = d.result;
        $("#importNote").textContent = "新增 " + r.added + " 人，更新 " + r.updated + " 人" +
          (r.skipped ? "，跳过 " + r.skipped + " 行" : "");
        return load().then(function () { ok("导入完成：新增 <b>" + r.added + "</b>，更新 <b>" + r.updated + "</b>"); });
      })
      .catch(bad);
  }

  function addStudent() {
    var sid = $("#addSid").value.trim();
    var name = $("#addName").value.trim();
    if (!sid || !name) { bad("学号和姓名都要填"); return; }
    LT.api("/api/admin/student", {
      method: "POST",
      body: { sid: sid, name: name, grp: $("#addGrp").value.trim() }
    }).then(function () {
      $("#addSid").value = ""; $("#addName").value = "";
      return load();
    }).then(function () { ok("已添加 <b>" + LT.esc(name) + "</b>"); }).catch(bad);
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
        return load().then(loadPlan);
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
        .then(function () { ok("已取消结算"); return load().then(loadPlan); })
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
        .then(function () { ok("已重置奖票"); return load().then(loadPlan); })
        .catch(bad);
    });
  }

  // ------------------------------------------------------------------ draw
  function drawPrize(index) {
    var p = (S.prizes || [])[index];
    if (!p) return;
    LT.api("/api/admin/draw", { method: "POST", body: { prize: p.name, mode: p.mode } })
      .then(function (d) {
        var w = d.draw;
        var tail = w.mode === "person"
          ? '<span class="code" style="font-family:inherit">均等</span>'
          : '<span class="code">#' + LT.code(w.code) + "</span>";
        $("#drawResult").innerHTML =
          '<div class="winner"><span class="prize">' + LT.esc(w.prize) + "</span>" +
          '<span class="who">' + LT.esc(w.name) +
          " <span style='color:var(--dim)'>" + LT.esc(w.sid) + "</span></span>" + tail + "</div>";
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
      title: $("#setTitle").value.trim(),
      subtitle: $("#setSubtitle").value.trim(),
      public_base_url: $("#setBaseUrl").value.trim(),
      show_times: $("#setShowTimes").checked,
      show_sid: $("#setShowSid").checked,
      screen_show_wall: $("#setScreenWall").checked,
      pool_mode: $("#setPoolMode").value,
      pool_size: Number($("#setPool").value),
      bonus_constant: Number($("#setBonusConstant").value),
      beta: Number($("#setBeta").value),
      allow_bonus: $("#setAllowBonus").checked
    };
  }

  function saveSettings() {
    LT.api("/api/admin/settings", { method: "POST", body: collectSettings() })
      .then(function (d) {
        S.config = d.config;
        return load();
      })
      .then(function () {
        $("#settingsNote").textContent = "已保存";
        ok("设置已保存");
        if (S.tab === "tickets") loadPlan();
      })
      .catch(bad);
  }

  function rotateSecret() {
    LT.confirm({
      title: "重新生成访问 id？",
      danger: true,
      okText: "重新生成",
      html: "所有已经发出去的链接和二维码都会失效，需要用新的地址重新分发。<br>" +
        "奖票、名单、签到记录不受影响。<br>确认？"
    }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/rotate_secret", { method: "POST" })
        .then(function (d) {
          S.publicBaseUrl = d.publicBaseUrl;
          S.unlocked = false;
          renderUnlock();
          renderSettings();
          ok("已生成新地址，请重新打开：<br><b style='word-break:break-all'>" +
            LT.esc(d.publicBaseUrl) + "/admin</b>", false, 12000);
        })
        .catch(bad);
    });
  }

  function collectPrizes() {
    var rows = $$("#prizeEditor .prize-row");
    if (!rows.length) return [];
    return rows.map(function (row) {
      return {
        name: $(".p-name", row).value.trim(),
        count: Math.max(0, Number($(".p-count", row).value) || 0),
        mode: $(".p-mode", row).value
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
        renderPrizeEditor();
      })
      .catch(bad);
  }

  function bindSettings() {
    $("#saveSettingsBtn").addEventListener("click", saveSettings);
    $("#rotateSecretBtn").addEventListener("click", rotateSecret);
    $("#setPoolMode").addEventListener("change", function () {
      S.config.pool_mode = $("#setPoolMode").value;
      updatePoolPreview();
    });
    $("#setBonusConstant").addEventListener("input", updatePoolPreview);
    $("#savePrizesBtn").addEventListener("click", savePrizes);
    $("#saveUserBtn").addEventListener("click", function () {
      LT.api("/api/admin/settings", { method: "POST", body: { admin_user: $("#setUser").value.trim() } })
        .then(function (d) { S.config = d.config; ok("账号已保存"); })
        .catch(bad);
    });
    $("#pwBtn").addEventListener("click", function () {
      LT.api("/api/admin/password", {
        method: "POST",
        body: { old: $("#pwOld").value, new: $("#pwNew").value }
      }).then(function () {
        $("#pwOld").value = ""; $("#pwNew").value = "";
        $("#pwNote").textContent = "已修改";
        ok("密码已修改");
      }).catch(function (e) { $("#pwNote").textContent = e.message; });
    });
  }

  // ------------------------------------------------------------------ boot
  function boot() {
    bindSearch();
    bindGlobal();
    bindSettings();

    $("#importBtn").addEventListener("click", importRoster);
    $("#addBtn").addEventListener("click", addStudent);
    $("#pickFileBtn").addEventListener("click", function () { $("#importFile").click(); });
    $("#importFile").addEventListener("change", function (e) {
      var f = e.target.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () { $("#importText").value = reader.result; };
      reader.readAsText(f, "utf-8");
    });
    $("#finalizeBtn").addEventListener("click", finalize);
    $("#unfinalizeBtn").addEventListener("click", unfinalize);
    $("#resetTicketsBtn").addEventListener("click", resetTickets);
    $("#phaseSigninBtn").addEventListener("click", function () { setPhase("signin"); });
    $("#phaseDrawBtn").addEventListener("click", function () { setPhase("draw"); });
    $("#shortlinkBtn").addEventListener("click", mintShortlink);
    $("#prizeAddBtn").addEventListener("click", function () {
      var list = collectPrizes().concat([{ name: "", count: 1, mode: "ticket" }]);
      S.config.prizes = list;
      renderPrizeEditor();
      var inputs = $$("#prizeEditor .p-name");
      if (inputs.length) inputs[inputs.length - 1].focus();
    });

    $$("#filters button").forEach(function (b) {
      if (b.getAttribute("data-filter") === "all") b.classList.add("primary");
    });

    load().then(function () {
      $("#q").focus();
      if (S.config.finalized) showTab("draw");
    });

    // keep the unlock countdown honest, and quietly resync every 15s
    setInterval(renderUnlock, 1000);
    setInterval(function () {
      if (document.hidden || document.querySelector(".modal-back")) return;
      var el = document.activeElement;
      var editing = el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.id !== "q";
      if (editing) return;
      LT.api("/api/admin/state").then(function (d) {
        applyState(d);
      }).catch(function (e) { if (e.status === 401) location.href = LT.PREFIX + "/login"; });
    }, 5000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
