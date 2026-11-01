/* Panel helpers: same relative-URL discipline as the event pages. */
(function () {
  "use strict";

  // "/u/laffey/xlottery/panel" -> "/u/laffey/xlottery", "/" -> ""
  function prefix() {
    var p = location.pathname.replace(/\/(panel|login|register)?\/?$/, "/");
    return p.replace(/\/+$/, "");
  }
  var PREFIX = prefix();

  function api(path, options) {
    options = options || {};
    var init = {
      method: options.method || "GET",
      headers: { "X-Requested-With": "xlottery" },
      credentials: "same-origin",
      cache: "no-store"
    };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    return fetch(PREFIX + "/api/panel" + path, init).then(function (res) {
      return res.text().then(function (text) {
        var data;
        try { data = text ? JSON.parse(text) : {}; } catch (e) {
          data = { ok: false, error: text || "响应无法解析" };
        }
        if (!res.ok || data.ok === false) {
          var err = new Error((data && data.error) || ("HTTP " + res.status));
          err.status = res.status;
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

  function when(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  var toastEl = null, toastTimer = null;
  function toast(msg, bad) {
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
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 2600);
  }

  window.PX = {
    PREFIX: PREFIX,
    api: api,
    esc: esc,
    when: when,
    pad: pad,
    toast: toast
  };
})();
