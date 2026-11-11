/* 「名单」页：导入名单、手动加人、全部名单（筛选、排序、编辑）。
 * 平台在活动后台启动后按 pages 声明载入本文件，然后调 MODULE.pages.roster.mount()。
 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;

  function ensureState() {
    if (!S) return;
    if (typeof S.rosterQ !== "string") S.rosterQ = "";
    if (!S.sortKey) S.sortKey = "sid";
    if (S.sortDir !== -1) S.sortDir = 1;
  }

  /* 整份名单按搜索框与表头排一遍：搜索算法在平台，排序在模块 */
  function rosterRows() {
    var q = (S.rosterQ || "").trim();
    var list = q ? LT.queryStudents(S.students, q, 999, lib.searchSpec())
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
    if (!S) return;
    var tbody = lib.$("#fullTable tbody");
    if (!tbody) return;
    var rows = rosterRows();
    lib.$$("#fullTable th[data-sort]").forEach(function (th) {
      th.classList.toggle("sorted", th.getAttribute("data-sort") === S.sortKey);
      th.setAttribute("data-dir", th.getAttribute("data-sort") === S.sortKey
        ? (S.sortDir > 0 ? "asc" : "desc") : "");
    });
    tbody.innerHTML = rows.map(function (s) {
      return "<tr>" +
        '<td class="num">' + LT.esc(s.sid) + "</td>" +
        '<td><b>' + LT.esc(s.name) + "</b></td>" +
        '<td style="color:var(--muted)">' + LT.esc(s.grp || "") + "</td>" +
        '<td style="color:var(--muted)">' + LT.esc(lib.roleLabel(s)) + "</td>" +
        lib.timeCell(s.marks.checkin) + lib.timeCell(s.marks.task1) + lib.timeCell(s.marks.task2) +
        "<td>" + (lib.ticketChips(s) || "—") + "</td>" +
        '<td class="right">' +
        '<button class="btn sm" data-edit="' + s.id + '">编辑</button> ' +
        '<button class="btn sm ' + (s.next ? "green" : "") + '" data-row="' + s.id + '">选中</button>' +
        "</td></tr>";
    }).join("") || '<tr><td colspan="9" class="empty">没有匹配的学生</td></tr>';
    lib.$("#rosterCount").textContent = rows.length === S.students.length
      ? S.students.length + " 人"
      : rows.length + " / " + S.students.length + " 人";
  }

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
      ["participant", "staff", "volunteer"].map(function (r) {
        return '<option value="' + r + '"' + ((s.role || "participant") === r ? " selected" : "") + ">" +
          lib.roleLabel({ role: r }) + "</option>";
      }).join("") + "</select></div>" +
      '<div class="row" style="justify-content:space-between;margin-top:14px">' +
      '<button class="btn danger" id="edDel"' + (S.unlocked ? "" : ' disabled title="先解锁"') + ">删除此人</button>" +
      '<div class="row"><button class="btn" id="edCancel">取消</button>' +
      '<button class="btn primary" id="edSave">保存</button></div></div>'
    );
    lib.$("#edCancel", m).onclick = function () { m.close(); };
    lib.$("#edSave", m).onclick = function () {
      LT.api("/api/admin/student", {
        method: "POST",
        body: {
          id: id,
          name: lib.$("#edName", m).value.trim(),
          grp: lib.$("#edGrp", m).value.trim(),
          note: lib.$("#edNote", m).value.trim(),
          role: lib.$("#edRole", m).value
        }
      }).then(function () { m.close(); return lib.reloadState(); })
        .then(function () { lib.ok("已保存"); })
        .catch(lib.bad);
    };
    lib.$("#edDel", m).onclick = function () {
      LT.confirm({
        title: "删除学生",
        danger: true,
        okText: "删除",
        html: "会同时删除 <b>" + LT.esc(s.name) + "</b> 的标记与奖票，且不能恢复。确认？"
      }).then(function (yes) {
        if (!yes) return;
        LT.api("/api/admin/student_delete", { method: "POST", body: { id: id } })
          .then(function () { m.close(); return lib.reloadState(); })
          .then(function () { lib.ok("已删除"); })
          .catch(lib.bad);
      });
    };
  }

  function importRoster() {
    var text = lib.$("#importText").value;
    if (!text.trim()) { lib.fail("先粘贴或选择名单内容"); return; }
    LT.api("/api/admin/import", { method: "POST", body: { text: text } })
      .then(function (d) {
        var r = d.result;
        lib.$("#importNote").textContent = "新增 " + r.added + " 人，更新 " + r.updated + " 人" +
          (r.skipped ? "，跳过 " + r.skipped + " 行" : "");
        return lib.reloadState().then(function () {
          lib.ok("导入完成：新增 <b>" + r.added + "</b>，更新 <b>" + r.updated + "</b>");
        });
      })
      .catch(lib.bad);
  }

  function addStudent() {
    var sid = lib.$("#addSid").value.trim();
    var name = lib.$("#addName").value.trim();
    if (!sid || !name) { lib.fail("学号和姓名都要填"); return; }
    LT.api("/api/admin/student", {
      method: "POST",
      body: { sid: sid, name: name, grp: lib.$("#addGrp").value.trim() }
    }).then(function () {
      lib.$("#addSid").value = "";
      lib.$("#addName").value = "";
      return lib.reloadState();
    }).then(function () {
      lib.ok("已添加 <b>" + LT.esc(name) + "</b>");
    }).catch(lib.bad);
  }

  /* 「选中」把这个人交给操作台的搜索框：两页都在模块里，直接过去 */
  function selectInOps(id) {
    var s = S.byId[id];
    if (!s) return;
    if (ctx && ctx.showTab) ctx.showTab("ops");
    var ops = window.MODULE.pages.ops;
    if (ops && ops.select) ops.select(s.name);
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    lib.$("#importBtn").addEventListener("click", importRoster);
    lib.$("#addBtn").addEventListener("click", addStudent);
    lib.$("#pickFileBtn").addEventListener("click", function () { lib.$("#importFile").click(); });
    lib.$("#importFile").addEventListener("change", function (e) {
      var f = e.target.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () { lib.$("#importText").value = reader.result; };
      reader.readAsText(f, "utf-8");
    });
    lib.$("#rosterFilter").addEventListener("input", function (e) {
      S.rosterQ = e.target.value;
      renderFullTable();
    });
    lib.$("#fullTable").addEventListener("click", function (e) {
      var th = e.target.closest("th[data-sort]");
      if (th) {
        var key = th.getAttribute("data-sort");
        if (S.sortKey === key) S.sortDir = -S.sortDir;
        else { S.sortKey = key; S.sortDir = 1; }
        renderFullTable();
        return;
      }
      var ed = e.target.closest("[data-edit]");
      if (ed) { editStudent(Number(ed.getAttribute("data-edit"))); return; }
      var row = e.target.closest("[data-row]");
      if (row) selectInOps(Number(row.getAttribute("data-row")));
    });
  }

  window.MODULE.pages.roster = {
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
      if (ctx && ctx.tab === "roster") renderFullTable();
    },
    show: function (tab) {
      if (ctx && tab) ctx.tab = tab;
      ensureState();
      renderFullTable();
    },
  };
})();
