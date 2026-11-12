/* 「提问」页：把观众的提问记下来，答过的标一下。 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;

  function render() {
    lib.ready();
    var list = lib.questions();
    var tbody = lib.$("#questionTable tbody");
    if (!tbody) return;
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">还没有提问</td></tr>';
    } else {
      tbody.innerHTML = list.map(function (q) {
        var done = !!q.answered_at;
        return '<tr' + (done ? ' class="muted-row"' : "") + ">" +
          "<td>" + LT.esc(q.who || "匿名") + "</td>" +
          '<td style="white-space:normal">' + LT.esc(q.text) + "</td>" +
          '<td class="num">' + LT.fmtTime(q.asked_at) + "</td>" +
          "<td>" + (done ? "已回答" : "待回答") + "</td>" +
          '<td class="right">' +
          (done ? "" : '<button class="btn sm green" data-answer="' + q.id + '">已回答</button> ') +
          '<button class="btn sm danger" data-drop-question="' + q.id + '"' +
          (S.unlocked ? "" : " disabled") + ">删除</button>" +
          "</td></tr>";
      }).join("");
    }
    var open = list.filter(function (q) { return !q.answered_at; }).length;
    var chip = lib.$("#questionCount");
    if (chip) chip.textContent = list.length + " 条，待回答 " + open;
  }

  function ask() {
    var who = lib.$("#askWho").value.trim();
    var text = lib.$("#askText").value.trim();
    if (!text) { lib.fail("先填提问内容"); return; }
    lib.act("ask", { who: who, text: text }).then(function () {
      lib.$("#askText").value = "";
      lib.ok("已记下");
      return lib.reloadState();
    }).catch(lib.fail);
  }

  function answer(id) {
    lib.act("answer", { id: id }).then(function () {
      lib.ok("已标记");
      return lib.reloadState();
    }).catch(lib.fail);
  }

  function drop(id) {
    LT.confirm({
      title: "删除这条提问？", danger: true, okText: "删除",
      html: "删掉之后就不能再标记回答了。"
    }).then(function (yes) {
      if (!yes) return;
      lib.act("drop_question", { id: id }).then(function () {
        lib.ok("已删除");
        return lib.reloadState();
      }).catch(lib.fail);
    });
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    lib.$("#askBtn").addEventListener("click", ask);
    lib.$("#askText").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); ask(); }
    });
    lib.$("#questionTable").addEventListener("click", function (e) {
      var a = e.target.closest("[data-answer]");
      if (a) { answer(Number(a.getAttribute("data-answer"))); return; }
      var d = e.target.closest("[data-drop-question]");
      if (d) drop(Number(d.getAttribute("data-drop-question")));
    });
  }

  window.MODULE.pages.questions = {
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
      if (ctx && ctx.tab === "questions") render();
    },
    show: function (tab) {
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      render();
    },
    count: function (state) {
      var d = (state && state.data) || {};
      return (d.questions || []).filter(function (q) {
        return !q.answered_at;
      }).length;
    },
  };
})();
