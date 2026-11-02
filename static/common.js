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
    var init = {
      method: options.method || "GET",
      headers: { "X-Requested-With": "lottery" },
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
      if (s) out.push([s, list[i]]);
    }
    out.sort(function (a, b) {
      if (b[0] !== a[0]) return b[0] - a[0];
      return (a[1].sid || "").localeCompare(b[1].sid || "");
    });
    return out.slice(0, limit || 10).map(function (p) { return p[1]; });
  }

  var KIND_LABEL = { checkin: "签到", task1: "任务一", task2: "任务二", bonus: "速度奖励" };

  window.LT = {
    PREFIX: PREFIX,
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
    KIND_LABEL: KIND_LABEL
  };
})();
