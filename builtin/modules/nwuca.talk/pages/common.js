/* 讲座模块的共用件：平台在页签脚本之前先载入这一份。
   各页 mount 时调 MODULE.lib.bind(page) 把平台给的 LT 与状态递进来。 */
window.MODULE = window.MODULE || { pages: {} };

window.MODULE.lib = (function () {
  var LT = null;
  var S = null;
  var reload = null;

  function bind(page) {
    if (!page) return;
    LT = page.LT || LT;
    S = page.state || S;
    reload = page.reload || reload;
    ready();
  }

  /* 状态还没到的时候先给个空壳 */
  function ready() {
    if (!S) return null;
    if (!S.data) S.data = {};
    if (!S.data.agenda) S.data.agenda = [];
    if (!S.data.questions) S.data.questions = [];
    if (!S.config) S.config = {};
    return S;
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function ok(html) { LT.toast(html, false); }

  function fail(err) {
    var text = typeof err === "string" ? err : (err && err.message) || "出错了";
    LT.toast(LT.esc(text), true, 4200);
  }

  /* 写操作一律走平台的动作接口：鉴权、解锁、审计都在那边 */
  function act(name, body) {
    return LT.api("/api/do/" + name, { method: "POST", body: body || {} });
  }

  function reloadState() { return reload ? reload() : null; }

  function data() { return (S && S.data) || {}; }

  function agenda() { return data().agenda || []; }
  function questions() { return data().questions || []; }

  function current() {
    var list = agenda();
    for (var i = 0; i < list.length; i++) {
      if (list[i].started_at && !list[i].ended_at) return list[i];
    }
    return null;
  }

  function stateLabel(s) {
    if (s.ended_at) return "已结束";
    if (s.started_at) return "进行中";
    return "未开始";
  }

  function mmss(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return m + ":" + (s < 10 ? "0" + s : s);
  }

  return {
    bind: bind, ready: ready, reloadState: reloadState,
    $: $, $$: $$, ok: ok, fail: fail, act: act,
    data: data, agenda: agenda, questions: questions, current: current,
    stateLabel: stateLabel, mmss: mmss,
  };
})();

/* 顶栏那条状态：平台只提供位置 */
window.MODULE.head = {
  render: function (state) {
    var d = (state && state.data) || {};
    var list = d.agenda || [];
    var qs = d.questions || [];
    var done = 0;
    var cur = null;
    for (var i = 0; i < list.length; i++) {
      if (list[i].ended_at) done++;
      if (list[i].started_at && !list[i].ended_at) cur = list[i];
    }
    var open = 0;
    for (var j = 0; j < qs.length; j++) {
      if (!qs[j].answered_at) open++;
    }
    var html = [
      '<span class="chip green"><b>' + done + "</b>/" + list.length + " 小节完成</span>",
      cur ? '<span class="chip gold">正在讲 <b>' + LT.esc(cur.title) + "</b></span>" : "",
      '<span class="chip blue"><b>' + open + "</b> 条待回答</span>",
    ];
    return html.join("");
  },
};

/* 进来先落在议程这一页 */
window.MODULE.landing = function () { return "agenda"; };
