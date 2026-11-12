/* 「议程」页：按顺序推进小节，记下开始与结束时间。 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;

  function render() {
    lib.ready();
    var list = lib.agenda();
    var tbody = lib.$("#agendaTable tbody");
    if (!tbody) return;
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">议程还是空的，先加一节</td></tr>';
    } else {
      tbody.innerHTML = list.map(function (s) {
        var st = lib.stateLabel(s);
        var cls = s.ended_at ? "muted-row" : (s.started_at ? "done" : "");
        var act = s.ended_at
          ? ""
          : (s.started_at
             ? '<span class="note">进行中</span>'
             : '<button class="btn sm primary" data-start="' + s.id + '">开始</button>');
        act += ' <button class="btn sm danger" data-drop-section="' + s.id + '"' +
          (S.unlocked ? "" : " disabled") + ">删除</button>";
        return '<tr' + (cls ? ' class="' + cls + '"' : "") + ">" +
          '<td class="num">' + s.pos + "</td>" +
          "<td><b>" + LT.esc(s.title) + "</b></td>" +
          '<td class="num">' + s.minutes + " 分</td>" +
          "<td>" + st + "</td>" +
          '<td class="right">' + act + "</td></tr>";
      }).join("");
    }
    var cur = lib.current();
    var chip = lib.$("#agendaNow");
    if (chip) {
      chip.textContent = cur ? "正在进行：" + cur.title : "没有在讲的小节";
      chip.className = "chip" + (cur ? " gold live" : "");
    }
    var endBtn = lib.$("#endSectionBtn");
    if (endBtn) endBtn.disabled = !cur;
  }

  function start(id) {
    lib.act("start_section", { id: id }).then(function () {
      lib.ok("已开始");
      return lib.reloadState();
    }).catch(lib.fail);
  }

  function endCurrent() {
    lib.act("end_section", {}).then(function () {
      lib.ok("已结束");
      return lib.reloadState();
    }).catch(lib.fail);
  }

  function addSection() {
    var title = lib.$("#secTitle").value.trim();
    var minutes = Number(lib.$("#secMinutes").value || 0) || 15;
    if (!title) { lib.fail("先填小节标题"); return; }
    lib.act("add_section", { title: title, minutes: minutes }).then(function () {
      lib.$("#secTitle").value = "";
      lib.ok("已添加");
      return lib.reloadState();
    }).catch(lib.fail);
  }

  function dropSection(id) {
    LT.confirm({
      title: "删除这一节？", danger: true, okText: "删除",
      html: "删掉之后议程里就没有它了，已经记下的开始与结束时间一并消失。"
    }).then(function (yes) {
      if (!yes) return;
      lib.act("drop_section", { id: id }).then(function () {
        lib.ok("已删除");
        return lib.reloadState();
      }).catch(lib.fail);
    });
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    lib.$("#secAddBtn").addEventListener("click", addSection);
    lib.$("#endSectionBtn").addEventListener("click", endCurrent);
    lib.$("#agendaTable").addEventListener("click", function (e) {
      var s = e.target.closest("[data-start]");
      if (s) { start(Number(s.getAttribute("data-start"))); return; }
      var d = e.target.closest("[data-drop-section]");
      if (d) dropSection(Number(d.getAttribute("data-drop-section")));
    });
  }

  window.MODULE.pages.agenda = {
    mount: function (page) {
      LT = page.LT;
      ctx = page;
      S = page.state || {};
      lib = window.MODULE.lib;
      lib.bind(page);
      bindOnce();
    },
    refresh: function (state, tab) {
      S = state || S;
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      if (ctx && ctx.tab === "agenda") render();
    },
    show: function (tab) {
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      render();
    },
    count: function (state) {
      var d = (state && state.data) || {};
      return ((d.agenda || []).length) || 0;
    },
  };
})();
