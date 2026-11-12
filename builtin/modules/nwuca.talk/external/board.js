/* 公示页：议程与提问。数据来自 /api/board 的 present 段（模块自己给的）。 */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var data = null;
  var etag = "";
  var streamed = false;

  function renderStats(d) {
    var st = (d.present && d.present.stats) || {};
    var chips = [
      ["green", st.done + "/" + st.sections, "小节完成"],
      ["blue", st.open_questions, "待回答"],
    ];
    $("#stats").innerHTML = chips.map(function (c) {
      return '<div class="stat ' + c[0] + '"><b>' + c[1] + "</b><span>" + c[2] + "</span></div>";
    }).join("");
  }

  function renderAgenda(d) {
    var list = (d.present && d.present.agenda) || [];
    var cur = d.present && d.present.current;
    var head = cur
      ? '<p class="note" style="margin:0 0 8px">现在讲：<b>' + LT.esc(cur.title) +
        "</b>（计划 " + cur.minutes + " 分钟）</p>"
      : "";
    $("#agenda").innerHTML = head + (list.map(function (s) {
      var cls = s.state === "now" ? " now" : (s.state === "done" ? " done" : "");
      var tail = s.state === "now"
        ? '<span class="cd">进行中</span>'
        : (s.state === "done" ? '<span class="cd">已结束</span>'
                              : '<span class="cd">' + s.minutes + " 分钟</span>");
      return '<div class="arow' + cls + '">' +
        '<span class="pos">' + s.pos + "</span>" +
        '<span class="nm">' + LT.esc(s.title) + "</span>" + tail + "</div>";
    }).join("") || '<p class="empty">议程还没有排</p>');
  }

  function renderQuestions(d) {
    var box = $("#questionsBox");
    var list = (d.present && d.present.questions) || [];
    if (!list.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    $("#questionNote").textContent = "（" + list.length + " 条）";
    $("#questions").innerHTML = list.map(function (q) {
      return '<div class="qrow' + (q.answered ? " done" : "") + '">' +
        '<span class="who">' + LT.esc(q.who || "匿名") + "</span>" +
        '<span class="txt">' + LT.esc(q.text) + "</span>" +
        (q.answered ? '<span class="ok">已回答</span>' : "") + "</div>";
    }).join("");
  }

  function render() {
    if (!data || !data.present) return;
    var cfg = data.config || {};
    $("#title").textContent = cfg.title || "";
    $("#subtitle").textContent = cfg.subtitle || "议程与提问";
    document.title = (cfg.title || "活动") + " · 议程";
    renderStats(data);
    renderAgenda(data);
    renderQuestions(data);
  }

  function load() {
    var headers = {};
    if (etag) headers["If-None-Match"] = etag;
    return fetch(LT.PREFIX + "/api/board", { cache: "no-store", headers: headers })
      .then(function (r) {
        if (r.status === 304) return null;
        var tag = r.headers.get("ETag");
        if (tag) etag = tag;
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        data = d;
        render();
        LT.version(d.version);
      })
      .catch(function () {});
  }

  function boot() {
    LT.watch(function () {
      if (!document.hidden) load();
    }, {
      stream: "/api/stream",
      onState: function (live) {
        streamed = live;
        document.documentElement.dataset.stream = live ? "live" : "poll";
      }
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) load();
    });
    load();
    var ticks = 0;
    setInterval(function () {
      if (document.hidden) return;
      ticks += 1;
      if (streamed && ticks % 20) return;
      load();
    }, 1500);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
