/* Shared helpers for all three pages. No build step, no modules. */
(function () {
  "use strict";

  // The lab platform publishes this app under /u/<user>/<service>/ and strips
  // that prefix before forwarding, so every request must be prefix-relative.
  function prefix() {
    var p = location.pathname.replace(/\/(admin|screen|login|board|checkin)(\/|$)/, "/");
    p = p.replace(/\/+$/, "");
    return p;
  }

  var PREFIX = prefix();

  // What this browser has seen, per object.  A write carries it so the server can
  // tell a real disagreement from two people working on different things.
  var revs = {};
  var watchers = [];

  /* 服务端版本：重新部署之后，这台机器上的页面还在跑旧脚本。
     第一次收到只记下来，之后变了才换页面；投影页正在抽奖时由页面自己延后。 */
  var bootVersion = "";
  var outdatedHandler = null;

  function version(v) {
    if (!v) return;
    if (!bootVersion) { bootVersion = v; return; }
    if (bootVersion === v) return;
    bootVersion = v;
    if (outdatedHandler) {
      try { if (outdatedHandler(v) === true) return; } catch (e) { /* 退回默认 */ }
    }
    location.reload();
  }

  function outdated(fn) { outdatedHandler = fn; }

  // The activity's own address has the panel one level above it.  The console
  // has no login of its own any more: it sends people to the panel and comes
  // back here, which is what makes one account work on both surfaces.
  var PANEL = PREFIX.replace(/\/[^/]+$/, "");

  function panelLoginUrl(back) {
    var target = back === undefined ? location.pathname : back;
    if (!target) return PANEL + "/login";
    return PANEL + "/login?s_return=" + encodeURIComponent(target);
  }

  function api(path, options) {
    options = options || {};
    var headers = { "X-Requested-With": "lottery" };
    if (Object.keys(revs).length) headers["X-Rev-Map"] = JSON.stringify(revs);
    var init = {
      method: options.method || "GET",
      headers: headers,
      credentials: "same-origin",
      cache: "no-store"
    };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    return fetch(PREFIX + path, init).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { ok: false, error: text || "响应无法解析" }; }
        if (data && data.revs) revs = data.revs;
        if (data && data.version) version(data.version);
        if (!res.ok || data.ok === false) {
          var err = new Error(data && data.error ? data.error : "HTTP " + res.status);
          err.status = res.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function code(n) { return n === null || n === undefined ? "---" : String(1000 + n).slice(1); }

  function fmtTime(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  function fmtDateTime(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " + fmtTime(ts);
  }
  function ago(ts) {
    if (!ts) return "";
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return Math.floor(s) + " 秒前";
    if (s < 3600) return Math.floor(s / 60) + " 分钟前";
    if (s < 86400) return Math.floor(s / 3600) + " 小时前";
    return Math.floor(s / 86400) + " 天前";
  }

  var toastEl = null, toastTimer = null;
  function toast(msg, bad, ms) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "toast";
      document.body.appendChild(toastEl);
    }
    toastEl.className = "toast" + (bad ? " bad" : "");
    toastEl.innerHTML = msg;
    void toastEl.offsetWidth;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, ms || 2600);
  }

  /* ---- modal confirm (replaces window.confirm, which is ugly on a projector) */
  function confirmBox(opts) {
    return new Promise(function (resolve) {
      var back = document.createElement("div");
      back.className = "modal-back";
      back.innerHTML =
        '<div class="modal"><h2>' + esc(opts.title || "确认") + "</h2>" +
        "<p>" + (opts.html || esc(opts.text || "")) + "</p>" +
        '<div class="row" style="justify-content:flex-end;margin-top:16px">' +
        '<button class="btn" data-act="no">取消</button>' +
        '<button class="btn ' + (opts.danger ? "danger" : "primary") + '" data-act="yes">' +
        esc(opts.okText || "确认") + "</button></div></div>";
      document.body.appendChild(back);
      function done(v) { back.remove(); document.removeEventListener("keydown", onKey); resolve(v); }
      function onKey(e) {
        if (e.key === "Escape") done(false);
        if (e.key === "Enter") done(true);
      }
      back.addEventListener("click", function (e) {
        var act = e.target.getAttribute && e.target.getAttribute("data-act");
        if (act === "yes") done(true);
        else if (act === "no" || e.target === back) done(false);
      });
      document.addEventListener("keydown", onKey);
      var yes = back.querySelector('[data-act="yes"]');
      if (yes) yes.focus();
    });
  }

  /* ---- generic modal: returns the backdrop element, .close() to dismiss ---- */
  function modal(html, opts) {
    opts = opts || {};
    var back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = '<div class="modal">' + html + "</div>";
    document.body.appendChild(back);
    function close() {
      back.remove();
      document.removeEventListener("keydown", onKey);
    }
    function onKey(e) {
      if (e.key === "Escape" && opts.dismissable !== false) close();
    }
    back.addEventListener("click", function (e) {
      if (e.target === back && opts.dismissable !== false) close();
    });
    document.addEventListener("keydown", onKey);
    back.close = close;
    var focus = back.querySelector("[autofocus]");
    if (focus) focus.focus();
    return back;
  }

  /* ---- search scoring: mirrors model._search_score on the server ---- */
  function score(stu, q) {
    var qc = q.replace(/\s+/g, "");
    if (!q && !qc) return 0;
    var sid = (stu.sid || "").toLowerCase();
    var name = stu.name || "";
    var py = (stu.py || "").toLowerCase();
    var pyi = (stu.pyi || "").toLowerCase();
    if (sid === qc || py === qc) return 1000;
    if (name === q) return 990;
    if (sid.indexOf(qc) === 0) return 900;
    if (py.indexOf(qc) === 0) return 860;
    if (pyi === qc) return 850;
    if (name.indexOf(q) === 0) return 820;
    if (pyi.indexOf(qc) === 0) return 780;
    if (name.indexOf(q) >= 0) return 700;
    if (py.indexOf(qc) >= 0) return 600;
    if (pyi.indexOf(qc) >= 0) return 500;
    if (sid.indexOf(qc) >= 0) return 400;
    return 0;
  }

  function search(list, q, limit) {
    q = (q || "").trim().toLowerCase();
    if (!q) return [];
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var s = score(list[i], q);
      if (!s) s = flexScore(list[i], q);
      if (s) out.push([s, list[i]]);
    }
    out.sort(function (a, b) {
      if (b[0] !== a[0]) return b[0] - a[0];
      return (a[1].sid || "").localeCompare(b[1].sid || "");
    });
    return out.slice(0, limit || 10).map(function (p) { return p[1]; });
  }

  /* ------------------------------------------------------------------ 混合搜索
     目标只有一个：录入时快到极限。所以查询是"空格分隔的与条件"，`#标签` 筛身份与状态，
     其余词按姓名 / 学号 / 拼音匹配，全部在前端算完（名单量级 60~200 人）。

     拼音这一步是 JECh 式的：把姓名拆成逐字音节（曾萍静 = zeng/ping/jing），
     查询按字母消费——每个音节只输入前缀也行，可以跳字也行：

       zengpingjing  zpg  zengpj  zenpj  zpj   ← 都命中

     这比"整串前缀"宽容得多，也比"随意子序列"严格：两个字母不能都从同一个音节里取。 */
  var TAGS = [
    { keys: ["w", "worker", "staff", "工作人员"], label: "#w 工作人员",
      test: function (s) { return s.role === "staff"; } },
    { keys: ["v", "volunteer", "志愿者"], label: "#v 志愿者",
      test: function (s) { return s.role === "volunteer"; } },
    { keys: ["p", "participant", "参加人员"], label: "#p 参加人员",
      test: function (s) { return !s.role || s.role === "participant"; } },
    { keys: ["已签到", "in"], label: "#已签到",
      test: function (s) { return !!s.marks.checkin; } },
    { keys: ["未签到", "out"], label: "#未签到",
      test: function (s) { return !s.marks.checkin; } },
    { keys: ["已完成", "done"], label: "#已完成",
      test: function (s) { return (s.tasks || 0) >= 2; } },
    { keys: ["未完成", "todo"], label: "#未完成",
      test: function (s) { return (s.tasks || 0) < 2; } },
    { keys: ["有票", "ticket"], label: "#有票",
      test: function (s) { return ticketCount(s) > 0; } },
    { keys: ["无票", "zeroticket"], label: "#无票",
      test: function (s) { return ticketCount(s) === 0; } },
    { keys: ["越序", "override"], label: "#越序",
      test: function (s) { return !!s.override; } }
  ];

  function ticketCount(stu) {
    var t = stu.tickets;
    if (!t) return 0;
    return typeof t.length === "number" ? t.length : Object.keys(t).length;
  }

  function findTag(word) {
    var key = (word || "").toLowerCase();
    for (var i = 0; i < TAGS.length; i++) {
      if (TAGS[i].keys.indexOf(key) >= 0 || TAGS[i].keys.indexOf(word) >= 0) {
        return TAGS[i];
      }
    }
    return null;
  }

  // {tags: [{tag, negate, raw}], terms: ["xyh", "#..."], hint: 未完成的标签词}
  function parseQuery(text) {
    var out = { tags: [], terms: [], unknown: [] };
    (text || "").trim().split(/\s+/).forEach(function (word) {
      if (!word) return;
      if (word.charAt(0) !== "#") {
        out.terms.push(word.toLowerCase());
        return;
      }
      var body = word.slice(1);
      var negate = body.charAt(0) === "-";
      if (negate) body = body.slice(1);
      if (!body) return;                 // 只打了个 "#" 或 "#-"：还不算标签
      var tag = findTag(body);
      if (tag) out.tags.push({ tag: tag, negate: negate });
      else out.unknown.push(word);
    });
    return out;
  }

  /* 拼音：音节前缀 + 可跳字。返回匹配到的分数（0 = 不匹配）。 */
  function pinyinScore(syllables, query) {
    if (!syllables || !syllables.length || !query) return 0;
    var n = syllables.length;
    var m = query.length;
    var seen = {};
    var whole = true;         // 整句都是连续前缀（更强）
    var step = function (i, j, k) {
      if (j === m) return true;
      if (i >= n) return false;
      var key = i + ":" + j + ":" + k;
      if (seen[key] !== undefined) return seen[key];
      var ok = false;
      // 1) 继续在当前音节里吃一个字母
      if (k < syllables[i].length && syllables[i].charAt(k) === query.charAt(j)) {
        ok = step(i, j + 1, k + 1);
      }
      // 2) 跳到下一个音节（这一跳意味着"跳字"，强度降一档）
      if (!ok && k === 0 && step(i + 1, j, 0)) {
        whole = false;
        ok = true;
      }
      // 3) 当前音节还没吃完也可以跳过去，从下一个音节的第一个字母重新开始
      if (!ok && k > 0 && step(i + 1, j, 0)) {
        whole = false;
        ok = true;
      }
      seen[key] = ok;
      return ok;
    };
    if (!step(0, 0, 0)) return 0;
    return whole ? 320 : 300;
  }

  /* 一个词对一个学生的匹配分：姓名与学号直接比，拼音按上面的规则。 */
  function termScore(stu, term) {
    var sid = (stu.sid || "").toLowerCase();
    var name = stu.name || "";
    var py = (stu.py || "").toLowerCase();
    var pyi = (stu.pyi || "").toLowerCase();
    if (!term) return 0;
    if (sid === term || py === term) return 1000;
    if (name === term) return 980;
    if (sid.indexOf(term) === 0) return 900;
    if (py.indexOf(term) === 0) return 860;
    if (pyi === term) return 850;
    if (name.indexOf(term) >= 0) return 800;
    if (pyi.indexOf(term) === 0) return 780;
    if (py.indexOf(term) >= 0) return 600;
    if (sid.indexOf(term) >= 0) return 400;
    var pyScore = pinyinScore(stu.pys || [], term);
    if (pyScore) return pyScore;
    // 首字母子序列也算：zpg 之于 z-p-j
    if (pyi && isSubsequence(term, pyi)) return 260;
    return 0;
  }

  function isSubsequence(needle, hay) {
    var i = 0;
    for (var j = 0; j < hay.length && i < needle.length; j++) {
      if (hay.charAt(j) === needle.charAt(i)) i++;
    }
    return i === needle.length;
  }

  function flexScore(stu, term) {
    return termScore(stu, term);
  }

  /* 解析 + 求交：标签在本地过滤，词逐个匹配，取最弱的那一项当总分。 */
  function queryStudents(list, text, limit) {
    var parsed = parseQuery(text);
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var stu = list[i];
      var ok = true;
      for (var t = 0; t < parsed.tags.length; t++) {
        var item = parsed.tags[t];
        if (!!item.tag.test(stu) === item.negate) { ok = false; break; }
      }
      if (!ok) continue;
      if (parsed.unknown.length) continue;      // 写错的标签当成"没有结果"
      var weakest = 0;
      for (var k = 0; k < parsed.terms.length; k++) {
        var s = termScore(stu, parsed.terms[k]);
        if (!s) { ok = false; break; }
        weakest = weakest ? Math.min(weakest, s) : s;
      }
      if (!ok) continue;
      out.push([weakest || 1, stu]);
    }
    out.sort(function (a, b) {
      if (b[0] !== a[0]) return b[0] - a[0];
      return (a[1].sid || "").localeCompare(b[1].sid || "");
    });
    return out.slice(0, limit || 999).map(function (p) { return p[1]; });
  }

  // The console hears about other people's writes instead of waiting for a poll.
  // The public pages use the same bus through ``/api/stream``, which only speaks
  // about the activity being watched; ``onState`` reports whether the stream is
  // live, so a page can fall back to polling when it is not.
  function watch(handler, options) {
    options = options || {};
    watchers.push(handler);
    var state = options.onState || function () {};
    var path = options.stream || "/api/admin/stream";
    if (typeof window.EventSource !== "function") {
      state(false);
      return;
    }
    var source = null;
    var open = function () {
      if (source) return;
      source = new EventSource(PREFIX + path);
      source.onopen = function () { state(true); };
      source.onmessage = function (event) {
        var data;
        try { data = JSON.parse(event.data); } catch (e) { return; }
        if (!data || !data.revs) return;
        if (data.version) version(data.version);
        var before = revs;
        revs = data.revs;
        watchers.forEach(function (fn) {
          try { fn(data.revs, before); } catch (e) { /* one bad watcher */ }
        });
      };
      source.onerror = function () {
        state(false);
        if (source && source.readyState === 2) {
          source = null;
          setTimeout(open, 4000);
        }
      };
    };
    open();
  }

  function conflictMsg(err) {
    if (err && err.data && err.data.conflict) {
      return "另一位工作人员刚刚改过这一项，本次更改没有生效";
    }
    return "";
  }

  var KIND_LABEL = { checkin: "签到", task1: "任务一", task2: "任务二", bonus: "速度奖励" };

  window.LT = {
    PREFIX: PREFIX,
    watch: watch,
    version: version,
    outdated: outdated,
    revs: function () { return revs; },
    conflictMsg: conflictMsg,
    PANEL: PANEL,
    panelLoginUrl: panelLoginUrl,
    api: api,
    esc: esc,
    pad: pad,
    code: code,
    fmtTime: fmtTime,
    fmtDateTime: fmtDateTime,
    ago: ago,
    toast: toast,
    confirm: confirmBox,
    modal: modal,
    search: search,
    score: score,
    parseQuery: parseQuery,
    queryStudents: queryStudents,
    termScore: termScore,
    TAGS: TAGS,
    KIND_LABEL: KIND_LABEL
  };
})();
