/* Panel helpers: same relative-URL discipline as the event pages. */
(function () {
  "use strict";

  // "/<mount>/panel" -> "/<mount>", "/" -> ""
  function prefix() {
    var p = location.pathname.replace(/\/(panel|login|register)?\/?$/, "/");
    return p.replace(/\/+$/, "");
  }
  var PREFIX = prefix();

  // The revision this page last saw.  A write that carries it is refused if
  // another session moved on in the meantime (first write wins).
  var revs = {};
  var watchers = [];

  function api(path, options) {
    options = options || {};
    var headers = { "X-Requested-With": "CAEventManager" };
    // what this session has seen, per object; the server compares only the
    // object the request touches, so unrelated writes never block each other
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
    return fetch(PREFIX + "/api/panel" + path, init).then(function (res) {
      return res.text().then(function (text) {
        var data;
        try { data = text ? JSON.parse(text) : {}; } catch (e) {
          data = { ok: false, error: text || "响应无法解析" };
        }
        if (data && data.revs) revs = data.revs;
        if (!res.ok || data.ok === false) {
          var err = new Error((data && data.error) || ("HTTP " + res.status));
          err.status = res.status;
          // the whole answer, for callers that act on a flag rather than text
          err.data = data || {};
          // the session is not elevated: the caller asks for the password and
          // repeats the same request
          err.sudo = !!(data && data.sudoRequired);
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

  // Audit rows are evidence: minutes are not precise enough to tell two actions
  // apart, so those get seconds.
  function whenExact(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  // Browsers happily treat a lone search box as a login field and autofill the
  // saved account name into it.  Anything that appears without a keystroke is
  // not the operator's input, so drop it.
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
    return scrub;
  }

  /* ---- notices: the top-right corner, for things that happen elsewhere ---- */
  var noticeBox = null;

  function notice(text, kind) {
    if (!noticeBox) {
      noticeBox = document.createElement("div");
      noticeBox.className = "notice-stack";
      noticeBox.setAttribute("role", "status");
      noticeBox.setAttribute("aria-live", "polite");
      document.body.appendChild(noticeBox);
    }
    var item = document.createElement("div");
    item.className = "notice" + (kind ? " " + kind : "");
    item.textContent = text;
    noticeBox.appendChild(item);
    void item.offsetWidth;
    item.classList.add("show");
    setTimeout(function () {
      item.classList.remove("show");
      setTimeout(function () { item.remove(); }, 220);
    }, 5200);
  }

  /* ---- the stream: someone else changed something -------------------------- */
  function watch(handler) {
    watchers.push(handler);
    if (typeof window.EventSource !== "function") return;
    var source = null;
    var open = function () {
      if (source) return;
      source = new EventSource(PREFIX + "/api/panel/stream");
      source.onmessage = function (event) {
        var data;
        try { data = JSON.parse(event.data); } catch (e) { return; }
        if (!data || !data.revs) return;
        var before = revs;
        revs = data.revs;
        watchers.forEach(function (fn) {
          try { fn(data.revs, before); } catch (e) { /* one bad watcher */ }
        });
      };
      source.onerror = function () {
        // the browser retries by itself; reconnect with a clean slate
        if (source && source.readyState === 2) {
          source = null;
          setTimeout(open, 4000);
        }
      };
    };
    open();
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
    watch: watch,
    notice: notice,
    revs: function () { return revs; },
    esc: esc,
    when: when,
    whenExact: whenExact,
    guardAutofill: guardAutofill,
    pad: pad,
    toast: toast
  };
})();
