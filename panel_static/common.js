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

  /* 服务端版本：重新部署之后，页面上的脚本还是旧的。
     第一次收到版本号只记下来，之后变了才算“换过版本”，交给页面自己决定怎么换。 */
  var bootVersion = "";
  var updateHandler = null;

  function noteVersion(version) {
    if (!version) return;
    if (!bootVersion) { bootVersion = version; return; }
    if (bootVersion === version) return;
    bootVersion = version;
    if (updateHandler) {
      try { updateHandler(version); return; } catch (e) { /* 交给默认处理 */ }
    }
    document.dispatchEvent(new CustomEvent("px:server-updated", { detail: version }));
  }

  function onUpdate(fn) { updateHandler = fn; }

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
        if (data && data.version) noteVersion(data.version);
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
        if (data.version) noteVersion(data.version);
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

  /* 通知从右上角落下：底部弹一条颜色接近页面的条，人一低头就错过了。
     多条时叠成一列，点一下（或按 Esc）随身带走，错误留得久一点。 */
  var toastStack = null;
  function toastBox() {
    if (!toastStack || !toastStack.isConnected) {
      toastStack = document.createElement("div");
      toastStack.className = "toast-stack";
      toastStack.setAttribute("role", "status");
      toastStack.setAttribute("aria-live", "polite");
      document.body.appendChild(toastStack);
    }
    return toastStack;
  }

  function toast(msg, bad, ms) {
    var stack = toastBox();
    var el = document.createElement("div");
    el.className = "toast" + (bad ? " bad" : "");
    el.innerHTML =
      '<span class="toast-mark" aria-hidden="true">' + (bad ? "!" : "✓") + "</span>" +
      '<span class="toast-text">' + msg + "</span>" +
      '<button class="toast-x" type="button" aria-label="关闭通知">' +
      '<svg viewBox="0 0 12 12" aria-hidden="true">' +
      '<path d="M3 3l6 6M9 3l-6 6"/></svg></button>';
    stack.appendChild(el);
    void el.offsetWidth;
    el.classList.add("show");
    var close = function () {
      el.classList.remove("show");
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 200);
    };
    var x = el.querySelector(".toast-x");
    ["pointerdown", "click"].forEach(function (evt) {
      x.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        close();
      });
    });
    setTimeout(close, ms || (bad ? 4200 : 2600));
    // 只留最近四条，别把屏幕堆满
    while (stack.children.length > 4) stack.removeChild(stack.firstChild);
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
    toast: toast,
    onUpdate: onUpdate,
    version: function () { return bootVersion; }
  };
})();
