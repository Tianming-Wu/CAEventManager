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

  function renderUnlock() {
    S.unlocked = $("#unlockToggle").checked;
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
    var list = q ? LT.queryStudents(S.students, q, 999, searchSpec())
                 : S.students.slice();
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

  /* 正在打 `#` 词的时候，把手上的标签列出来（点一下就能补全），
     不要求谁记住语法 */
  function renderTagHint() {
    var box = $("#tagHint");
    if (!box) return;
    var q = S.q.trim();
    var words = q.split(/\s+/);
    var last = words[words.length - 1] || "";
    var typing = last.charAt(0) === "#";
    if (!typing) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    var parsed = LT.parseQuery(q);
    var known = parsed.unknown.length === 0;
    var buttons = (LT.tagsFromSpec(searchSpec()) || LT.TAGS).map(function (tag) {
      return '<button type="button" data-tag="' + LT.esc(tag.label.split(" ")[0]) + '">' +
        LT.esc(tag.label) + "</button>";
    }).join("");
    box.innerHTML = (known ? "按身份、状态筛：" : "没有这个标签。可用的有：") + buttons;
    box.hidden = false;
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
    var q = S.rosterQ.trim();
    var list = q ? LT.queryStudents(S.students, q, 999, searchSpec())
                 : S.students.slice();
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
      if (typeof p === "string") return { name: p, count: 1, mode: "ticket", batch: false };
      return {
        name: (p && p.name) || "",
        count: Number(p && p.count) || 0,
        mode: p && p.mode === "person" ? "person" : "ticket",
        batch: !!(p && p.batch)
      };
    }).filter(function (p) { return p.name; });
  }

  // 「逐个」 draws one winner per click; 「一次抽完」 draws the whole remaining
  // quota of that prize in one go. Stored with the prize, so it survives a
  // reload and both operators see the same choice.
  function paceSwitch(index, oneShot) {
    return '<div class="seg" role="group">' +
      '<button type="button" class="seg-btn' + (oneShot ? "" : " on") +
      '" data-pace="step" data-i="' + index + '">逐个</button>' +
      '<button type="button" class="seg-btn' + (oneShot ? " on" : "") +
      '" data-pace="batch" data-i="' + index + '">一次抽完</button></div>';
  }

  function renderPrizeList() {
    var body = $("#prizeTable tbody");
    if (!body) return;
    var prizes = S.prizes || [];
    if (!prizes.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">还没有配置奖品</td></tr>';
      return;
    }
    body.innerHTML = prizes.map(function (p, i) {
      var done = p.remaining <= 0;
      var blocked = p.mode === "ticket" && !S.config.finalized;
      var label = p.mode === "person" ? "均等" : "按票";
      var why = done ? "已抽完" : (blocked ? "需先结算奖票" : "");
      var act = p.batch ? "抽完" : "抽";
      return "<tr" + (done ? ' class="done-row"' : "") + ">" +
        "<td><b>" + LT.esc(p.name) + "</b></td>" +
        '<td style="color:var(--muted)">' + label + "</td>" +
        "<td>" + paceSwitch(i, p.batch) + "</td>" +
        '<td class="right left-n">' + p.drawn + "</td>" +
        '<td class="right left-n">' + p.remaining + " / " + p.count + "</td>" +
        '<td class="right"><button class="btn draw-btn ' + (done ? "" : "primary") +
        '" data-draw="' + i + '"' + (done || blocked ? " disabled" : "") +
        ' title="' + why + '">' + act + "</button></td></tr>";
    }).join("");
  }

  function setPace(index, oneShot) {
    var prizes = S.prizes || [];
    if (!prizes[index] || !!prizes[index].batch === oneShot) return;
    var payload = prizes.map(function (p, i) {
      return {
        name: p.name,
        count: p.count,
        mode: p.mode,
        batch: i === index ? oneShot : !!p.batch
      };
    });
    LT.api("/api/admin/settings", { method: "POST", body: { prizes: payload } })
      .then(function () { return load(); })
      .catch(bad);
  }

  function renderPrizeEditor() {
    var box = $("#prizeEditor");
    if (!box) return;
    var prizes = normalizePrizes(S.config.prizes);
    box.innerHTML = prizes.map(function (p, i) {
      return '<div class="prize-row" data-i="' + i + '" data-batch="' + (p.batch ? "1" : "0") + '">' +
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
    if (!S.unlocked) {
      ok("归档前请先在右上角打开「解锁修改」");
      return;
    }
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
            // 一次性确认：这一次请求带上密码，不打开任何提权窗口
            askOncePassword("归档活动").then(function (password) {
              LT.panelApi("/api/panel/event/archive",
                { method: "POST", body: { seq: S.seq, password: password } })
                .then(function () {
                  ok("已归档，正在回到总览");
                  setTimeout(function () { location.href = LT.panelLoginUrl(); }, 1200);
                })
                .catch(bad);
            }).catch(function () {});
            return;
          }
          bad(e);
        });
    });
  }

  /* 一次性确认：只验这一次请求的密码，不开提权窗口 */
  function askOncePassword(what) {
    return new Promise(function (resolve, reject) {
      var back = LT.modal("<h2>需要确认</h2>"
        + '<p class="page-sub">' + LT.esc(what) + '：请输入登录密码。</p>'
        + '<div class="field"><label for="oncePw">登录密码</label>'
        + '<input id="oncePw" type="password" autocomplete="current-password"></div>'
        + '<div class="row" style="margin-top:16px;justify-content:flex-end">'
        + '<button class="btn" id="onceCancel" type="button">取消</button>'
        + '<button class="btn primary" id="onceGo" type="button">确认</button></div>');
      var box = $("#oncePw");
      if (box) box.focus();
      $("#onceCancel").addEventListener("click", function () {
        back.close();
        reject(new Error("cancelled"));
      });
      $("#onceGo").addEventListener("click", function () {
        var pw = ($("#oncePw").value || "");
        back.close();
        resolve(pw);
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
      renderPrizeEditor();
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
    renderPrizeList();
    renderUnlock();
    renderHits();
    renderTable();
    renderFullTable();
    renderDraws();
    renderLog();
    if (S.tab === "config") renderSettings();
    if (S.tab === "tickets") renderPlan();
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
    refreshHits();
    renderAll();
  }

  function load() {
    return LT.api("/api/admin/state").then(applyState).catch(function (e) {
      if (e.status === 401) { location.href = LT.panelLoginUrl(); return; }
      bad(e);
    });
  }

  function loadPlan() {
    return LT.api("/api/admin/plan").then(function (d) {
      S.plan = d;
      renderPlan();
    }).catch(bad);
  }

  // 模块在 module.json 的 features.search 里说清楚"哪份清单、哪个字段是姓名"，
  // 搜索的算法留在平台：这里只是把那份声明递给 LT
  function searchSpec() {
    var perms = S.features || {};
    return perms.search || null;
  }

  function refreshHits() {
    renderTagHint();
    if (!S.q.trim()) { S.hits = []; return; }
    S.hits = LT.queryStudents(S.students, S.q, 8, searchSpec());
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
    // 点提示里的标签＝把它补进查询（省得记写法）
    var hintBox = $("#tagHint");
    if (hintBox) {
      hintBox.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-tag]");
        if (!btn) return;
        var words = S.q.trim().split(/\s+/).filter(Boolean);
        if (words.length && words[words.length - 1].charAt(0) === "#") words.pop();
        words.push(btn.getAttribute("data-tag"));
        S.q = words.join(" ") + " ";
        q.value = S.q;
        S.sel = 0;
        refreshHits();
        renderHits();
        renderTable();
        q.focus();
      });
    }
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
      var pc = t.getAttribute && t.getAttribute("data-pace");
      if (pc) { setPace(Number(t.getAttribute("data-i")), pc === "batch"); return; }
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

    renderTabs();

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
  // The bar comes from the platform; a kind only decides which pages it wants.
  function renderTabs() {
    var nav = $("#tabs");
    LT.api("/api/admin/console").then(function (d) {
      S.pages = d.pages || [];
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
    if (name === "ops") { $("#q").focus(); }
    if (name === "tickets") loadPlan();
    if (name === "config") renderSettings();
    if (name === "settings") loadMembers();
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
        if (S.tab === "tickets") loadPlan();
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
        renderPrizeEditor();
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
      var el = document.activeElement;
      var editing = el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.id !== "q";
      if (editing) return;
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
      var el = document.activeElement;
      var editing = el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.id !== "q";
      if (editing) return;
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
