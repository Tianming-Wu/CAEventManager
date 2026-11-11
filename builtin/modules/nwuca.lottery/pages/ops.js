/* 「操作台」页：搜索、键盘标记、名单表格。
 * 平台在活动后台启动后按 pages 声明载入本文件，然后调 MODULE.pages.ops.mount()。
 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;

  function ensureState() {
    if (!S) return;
    if (typeof S.q !== "string") S.q = "";
    if (!S.hits) S.hits = [];
    if (typeof S.sel !== "number") S.sel = 0;
    if (!S.filter) S.filter = "all";
  }

  function filtered() {
    var q = (S.q || "").trim();
    var list = q ? LT.queryStudents(S.students, q, 999, lib.searchSpec())
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

  function renderTable() {
    var tbody = lib.$("#rosterTable tbody");
    if (!tbody) return;
    var rows = filtered();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">没有符合条件的学生</td></tr>';
    } else {
      tbody.innerHTML = rows.map(function (s) {
        return '<tr class="' + (s.tasks === 2 ? "done" : "") + '">' +
          "<td><b>" + LT.esc(s.name) + "</b>" + lib.roleTag(s) + "</td>" +
          '<td class="num">' + LT.esc(s.sid) + "</td>" +
          '<td style="color:var(--muted)">' + LT.esc(s.grp || "") + "</td>" +
          lib.timeCell(s.marks.checkin) + lib.timeCell(s.marks.task1) + lib.timeCell(s.marks.task2) +
          "<td>" + (lib.ticketChips(s) || '<span style="color:var(--dim)">—</span>') + "</td>" +
          '<td class="right">' + lib.actionButtons(s, false) + "</td></tr>";
      }).join("");
    }
    lib.$("#rosterNote").textContent = "显示 " + rows.length + " / " + S.students.length + " 人";
  }

  /* 正在打 `#` 词的时候，把手上的标签列出来（点一下就能补全），不要求谁记住语法 */
  function renderTagHint() {
    var box = lib.$("#tagHint");
    if (!box) return;
    var q = (S.q || "").trim();
    var words = q.split(/\s+/);
    var last = words[words.length - 1] || "";
    if (last.charAt(0) !== "#") {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    var parsed = LT.parseQuery(q);
    var known = parsed.unknown.length === 0;
    var buttons = (LT.tagsFromSpec(lib.searchSpec()) || LT.TAGS).map(function (tag) {
      return '<button type="button" data-tag="' + LT.esc(tag.label.split(" ")[0]) + '">' +
        LT.esc(tag.label) + "</button>";
    }).join("");
    box.innerHTML = (known ? "按身份、状态筛：" : "没有这个标签。可用的有：") + buttons;
    box.hidden = false;
  }

  function renderHits() {
    var box = lib.$("#hits");
    if (!box) return;
    if (!(S.q || "").trim()) {
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
        '<div class="who"><span class="nm">' + LT.esc(s.name) + "</span>" + lib.roleTag(s) +
        '<span class="sid">' + LT.esc(s.sid) + "</span>" +
        '<div class="steps">' + lib.stepBadges(s) + "</div></div>" +
        '<div class="side">' + lib.ticketChips(s) + lib.nextLabel(s) + "</div></div>";
    }).join("");
  }

  function refreshHits() {
    renderTagHint();
    if (!(S.q || "").trim()) { S.hits = []; return; }
    S.hits = LT.queryStudents(S.students, S.q, 8, lib.searchSpec());
    if (S.sel >= S.hits.length) S.sel = Math.max(0, S.hits.length - 1);
  }

  function renderAll() {
    refreshHits();
    renderHits();
    renderTable();
  }

  function mark(studentId, kind) {
    var stu = S.byId[studentId];
    if (!stu) return;
    if (stu.marks[kind]) {
      lib.ok("<b>" + LT.esc(stu.name) + "</b> 的" + lib.kindLabel(kind) + "早就完成了");
      return;
    }
    if (S.config.finalized) { lib.fail("已结算奖票，如需继续操作请先取消结算（需解锁）"); return; }
    var need = lib.PREV[kind];
    if (need && !stu.marks[need]) { lib.fail("需要先完成「" + lib.kindLabel(need) + "」"); return; }

    LT.api("/api/admin/mark", { method: "POST", body: { student_id: studentId, kind: kind } })
      .then(function (d) {
        var code = d.result && d.result.code;
        var suffix = d.result && d.result.already
          ? "（之前已完成）"
          : ' · 奖票 <b>#' + LT.code(code) + "</b>";
        lib.ok("<b>" + LT.esc(stu.name) + "</b> " + lib.kindLabel(kind) + " 完成" + suffix);
        var p = lib.reloadState();
        if (p && p.then) p.then(refocus, function () {});
        else refocus();
      })
      .catch(function (e) {
        lib.bad(e);
        if (e.status === 409) lib.reloadState();
      });
  }

  function undo(studentId, kind) {
    var stu = S.byId[studentId];
    if (!stu) return;
    if (!S.unlocked) { lib.fail("撤销需要先打开右上角的「解锁修改」开关"); return; }
    var deps = kind === "checkin"
      ? ["task1", "task2"].filter(function (k) { return stu.marks[k]; })
      : (kind === "task1" && stu.marks.task2 ? ["task2"] : []);

    var run = function (cascade) {
      LT.api("/api/admin/undo", {
        method: "POST",
        body: { student_id: studentId, kind: kind, cascade: cascade }
      })
        .then(function (d) {
          lib.ok("<b>" + LT.esc(stu.name) + "</b> 已撤销 " +
            (d.result.released || []).map(lib.kindLabel).join("、"));
          return lib.reloadState();
        })
        .catch(function (e) {
          lib.bad(e);
          if (e.status === 403) lib.reloadState();
        });
    };

    if (deps.length) {
      LT.confirm({
        title: "级联撤销",
        danger: true,
        okText: "一起撤销",
        html: "撤销 <b>" + lib.kindLabel(kind) + "</b> 会同时撤销后续已完成的 <b>" +
          deps.map(lib.kindLabel).join("、") + "</b>，并收回对应奖票。<br>确认要撤销 <b>" +
          LT.esc(stu.name) + "</b> 的这些记录吗？"
      }).then(function (yes) { if (yes) run(true); });
    } else {
      run(false);
    }
  }

  function resetInput() {
    lib.$("#q").value = "";
    S.q = "";
    S.hits = [];
    S.sel = 0;
    renderHits();
    renderTable();
  }

  function selectedHit() { return S.hits[S.sel] || null; }

  /* 鼠标点过之后键盘还能接着用：操作台上搜索框一直拿着焦点，除非正在别处输入 */
  function refocus() {
    if (!ctx || ctx.tab !== "ops") return;
    var el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.id !== "q") return;
    if (document.querySelector(".modal-back")) return;
    var q = lib.$("#q");
    if (q) q.focus();
  }

  /* 浏览器会把孤零零的搜索框当登录框，把存下来的账号名填进去。
     没有按键就出现的值不是操作员打的，清掉再走。 */
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
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    var q = lib.$("#q");

    q.addEventListener("input", function () {
      S.q = q.value;
      S.sel = 0;
      renderAll();
    });

    // 点提示里的标签＝把它补进查询（省得记写法）
    var hintBox = lib.$("#tagHint");
    if (hintBox) {
      hintBox.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-tag]");
        if (!btn) return;
        var words = (S.q || "").trim().split(/\s+/).filter(Boolean);
        if (words.length && words[words.length - 1].charAt(0) === "#") words.pop();
        words.push(btn.getAttribute("data-tag"));
        S.q = words.join(" ") + " ";
        q.value = S.q;
        S.sel = 0;
        renderAll();
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
      // 撤销：Ctrl+Shift+Z。Alt 组合被显卡叠加层与厂商工具占着，不能用
      if (e.ctrlKey && e.shiftKey && e.code === "KeyZ") {
        e.preventDefault();
        var s = selectedHit();
        if (!s) return;
        var done = lib.KINDS.filter(function (k) { return s.marks[k]; });
        if (!done.length) { lib.ok("没有可撤销的步骤"); return; }
        undo(s.id, done[done.length - 1]);
        return;
      }
      // 指定步骤：Shift+1/2/3（用 e.code，键盘布局与 Shift 符号都改不动它）
      var digit = { Digit1: 0, Digit2: 1, Digit3: 2 }[e.code];
      if (e.shiftKey && !e.ctrlKey && !e.altKey && digit !== undefined) {
        e.preventDefault();
        var stu = selectedHit();
        if (stu) mark(stu.id, lib.KINDS[digit]);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        var target = selectedHit();
        if (!target) {
          var cands = filtered();
          if (cands.length === 1) target = cands[0];
        }
        if (!target) { lib.fail("没有选中学生"); return; }
        if (!target.next) { lib.ok("<b>" + LT.esc(target.name) + "</b> 已经全部完成了"); return; }
        mark(target.id, target.next);
      }
    });

    // 右键清空：比用鼠标选掉整段再删快
    q.addEventListener("contextmenu", function (e) {
      if (!q.value) return;
      e.preventDefault();
      resetInput();
    });

    guardAutofill(q, function () {
      S.q = "";
      S.hits = [];
      S.sel = 0;
      renderHits();
      renderTable();
    });

    lib.$("#hits").addEventListener("click", function (e) {
      var hit = e.target.closest(".hit");
      if (!hit) return;
      S.sel = Number(hit.getAttribute("data-i"));
      renderHits();
      var stu = S.byId[Number(hit.getAttribute("data-id"))];
      if (stu && stu.next) mark(stu.id, stu.next);
    });

    lib.$("#filters").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-filter]");
      if (!btn) return;
      S.filter = btn.getAttribute("data-filter");
      lib.$$("#filters button").forEach(function (b) { b.classList.toggle("primary", b === btn); });
      renderTable();
    });
    lib.$$("#filters button").forEach(function (b) {
      if (b.getAttribute("data-filter") === "all") b.classList.add("primary");
    });

    lib.$("#refreshBtn").addEventListener("click", function () {
      var p = lib.reloadState();
      if (p && p.then) p.then(function () { lib.ok("已刷新"); });
      else lib.ok("已刷新");
    });

    // 表格与命中列表里的按钮都是现画的：在文档上接一次
    document.addEventListener("click", function (e) {
      var t = e.target;
      var mk = t.getAttribute && t.getAttribute("data-mark");
      if (mk) { mark(Number(t.getAttribute("data-id")), mk); refocus(); return; }
      var un = t.getAttribute && t.getAttribute("data-undo");
      if (un) { undo(Number(t.getAttribute("data-id")), un); refocus(); }
    });

    // 「/」随时回到搜索框
    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || document.activeElement === q) return;
      if (document.querySelector(".modal-back")) return;
      e.preventDefault();
      if (ctx && ctx.showTab) ctx.showTab("ops");
      q.focus();
    });
  }

  window.MODULE.pages.ops = {
    mount: function (page) {
      LT = page.LT;
      ctx = page;
      S = page.state || {};
      lib = window.MODULE.lib;
      lib.bind(page);
      ensureState();
      bindOnce();
    },
    refresh: function (state, tab) {
      S = state || S;
      if (ctx && tab) ctx.tab = tab;
      ensureState();
      if (ctx && ctx.tab === "ops") renderAll();
    },
    show: function (tab) {
      if (ctx && tab) ctx.tab = tab;
      ensureState();
      renderAll();
      var q = lib.$("#q");
      if (q) q.focus();
    },
    /* 名单页点「选中」时叫过来：把人填进搜索框，接着走键盘 */
    select: function (name) {
      ensureState();
      var q = lib.$("#q");
      if (q) q.value = name;
      S.q = name || "";
      S.sel = 0;
      renderAll();
      if (q) q.focus();
    },
  };
})();
