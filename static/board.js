/* Public board: read-only, no login, refreshes itself. */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var data = null;
  var filter = "";

  function statCard(cls, value, label) {
    return '<div class="stat ' + cls + '"><b>' + value + "</b><span>" + label + "</span></div>";
  }

  function renderStats(d) {
    var st = d.stats || {};
    var html = [
      statCard("green", st.checkin + "/" + st.total, "已签到"),
      statCard("blue", st.task1, "完成任务一"),
      statCard("blue", st.task2, "完成任务二"),
      statCard("gold", st.live_tickets, "已发奖票")
    ];
    if (st.finalized) html.push(statCard("gold", "已结算", "速度奖励已发放"));
    else html.push(statCard("", st.bonus_pool, "待分配奖票"));
    $("#stats").innerHTML = html.join("");
  }

  function renderWinners(d) {
    var list = d.winners || [];
    if (!list.length) { $("#winnersBox").classList.add("hidden"); return; }
    $("#winnersBox").classList.remove("hidden");
    $("#winners").innerHTML = list.map(function (w) {
      var tail = w.mode === "person"
        ? '<span class="code" style="font-family:inherit;font-size:.85em">到场均等</span>'
        : '<span class="code">#' + LT.code(w.code) + "</span>";
      return '<div class="winner"><span class="prize">' + LT.esc(w.prize || "奖品") + "</span>" +
        '<span class="who">' + LT.esc(w.name || "?") + "</span>" +
        '<span style="color:var(--dim);font-size:.8em">' + (data.config.show_sid ? LT.esc(w.sid || "") : "") + "</span>" +
        tail + "</div>";
    }).join("");
  }

  function matched(s) {
    if (!filter) return true;
    return LT.score(s, filter) > 0;
  }

  function renderCards() {
    var d = data;
    // staff and volunteers have no tickets, so they do not belong on this board
    var students = (d.students || []).filter(function (x) {
      return (x.role || "participant") === "participant";
    });
    var showSid = !!(d.config && d.config.show_sid);
    var showTimes = !!(d.config && d.config.show_times);
    var list = students.filter(matched);
    $("#countNote").textContent = filter ? "（匹配 " + list.length + " 人）" : "（" + students.length + " 人）";

    $("#cards").innerHTML = list.map(function (s) {
      var codes = s.codes || [];
      var cls = "card";
      if (!s.progress) cls += " notin";
      else if (!codes.length) cls += " pending";
      var chips = codes.length
        ? codes.map(function (t) {
            return '<span class="tkt' + (t.kind === "bonus" ? " bonus" : "") + '">' +
              LT.code(t.code) + "</span>";
          }).join("")
        : '<span style="color:var(--dim);font-size:.78rem">暂无奖票</span>';
      var meta = [];
      if (showTimes && s.times) {
        if (s.times.checkin) meta.push('<span>签到 ' + LT.fmtTime(s.times.checkin) + "</span>");
        if (s.times.task1) meta.push('<span>任务一 ' + LT.fmtTime(s.times.task1) + "</span>");
        if (s.times.task2) meta.push('<span>任务二 ' + LT.fmtTime(s.times.task2) + "</span>");
      }
      return '<div class="' + cls + '">' +
        '<div class="top"><span class="nm">' + LT.esc(s.name) + "</span>" +
        '<span class="sub">' + LT.esc(s.grp || (showSid ? s.sid : "")) + "</span></div>" +
        '<div class="tks">' + chips + "</div>" +
        (meta.length ? '<div class="meta">' + meta.join("") + "</div>" : "") +
        "</div>";
    }).join("") || '<p class="empty">没有匹配的人</p>';
  }

  function render() {
    if (!data) return;
    var cfg = data.config || {};
    $("#title").textContent = cfg.title || "抽奖";
    $("#subtitle").textContent = cfg.subtitle || "";
    document.title = (cfg.title || "抽奖") + " · 奖票公示";
    renderStats(data);
    renderWinners(data);
    renderCards();
  }

  function load() {
    return fetch(LT.PREFIX + "/api/board", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { data = d; render(); })
      .catch(function () {});
  }

  function boot() {
    var find = $("#find");
    find.addEventListener("input", function (e) {
      filter = e.target.value.trim().toLowerCase();
      renderCards();
    });
    // same autofill scrub as the console: drop injected text before it filters
    var touched = false;
    var scrub = function () {
      if (touched || !find.value) return;
      find.value = "";
      filter = "";
      renderCards();
    };
    find.addEventListener("keydown", function () { touched = true; });
    find.addEventListener("focus", scrub);
    find.addEventListener("input", function (e) { if (e.isTrusted) touched = true; });
    [300, 1200, 2600].forEach(function (ms) { setTimeout(scrub, ms); });
    window.addEventListener("load", scrub);
    load();
    setInterval(function () { if (!document.hidden) load(); }, 5000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
