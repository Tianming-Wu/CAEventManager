/* 使用文档页：树状目录 + 正文。
   这一页自己一套骨架，不加载面板的脚本；接口只有 /api/docs/tree 与 /api/docs/doc。
   深浅色存在本机（键名与面板分开），第一次进来时沿用面板那一次的选择。

   地址里省掉 doc/ 前缀：docs#/usage/approval.md；正文里的 [审批](@usage/approval)
   走的也是这一套键。 */
(function () {
  "use strict";

  var DOCS_THEME = "xl.docs.theme";
  var PANEL_THEME = "xl.panel.theme";

  var S = {
    groups: [],
    docs: [],          // 站内链接认的键：usage/approval.md 这种
    path: "",
    open: {},          // 用户点过的分组：true 展开 / false 收起
    booted: false,
    busyTimer: null
  };

  function $(sel) { return document.querySelector(sel); }

  function esc(text) { return window.MD.esc(text); }

  function store(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { /* 隐私模式：存不下只影响下次 */ }
  }

  function read(key) {
    try { return localStorage.getItem(key) || ""; } catch (e) { return ""; }
  }

  function shortPath(path) {
    return String(path || "").replace(/^doc\//, "").replace(/^\/+/, "");
  }

  function dirOf(path) {
    var at = String(path || "").lastIndexOf("/");
    return at < 0 ? "" : path.slice(0, at);
  }

  /* ---------------- 深浅色 ---------------- */
  function prefersDark() {
    return !!(window.matchMedia &&
              window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function applyTheme(dark) {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    var btn = $("#docsTheme");
    if (btn) btn.setAttribute("aria-pressed", dark ? "true" : "false");
  }

  function initTheme() {
    var mine = read(DOCS_THEME);
    if (mine === "dark" || mine === "light") {
      applyTheme(mine === "dark");
      return;
    }
    // 第一次进来：沿用面板那一次的选择（面板也是存在本机的）
    var theirs = read(PANEL_THEME);
    if (theirs === "dark" || theirs === "light") {
      applyTheme(theirs === "dark");
      store(DOCS_THEME, theirs);
      return;
    }
    applyTheme(prefersDark());
  }

  function toggleTheme() {
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    applyTheme(!dark);
    store(DOCS_THEME, !dark ? "dark" : "light");
  }

  /* ---------------- 目录树 ---------------- */
  function groupOpen(group) {
    if (group.key in S.open) return S.open[group.key];
    // 没点过：只把当前这一篇所在的那一组打开，其余收着
    return group.docs.some(function (d) { return shortPath(d.path) === S.path; });
  }

  function drawTree() {
    var box = $("#docsTree");
    if (!box) return;
    box.innerHTML = S.groups.map(function (group) {
      var open = groupOpen(group);
      var docs = group.docs.map(function (doc) {
        var path = shortPath(doc.path);
        return '<a class="tree-doc' + (path === S.path ? " on" : "") +
          '" href="#/' + encodeURI(path) + '" data-doc="' + esc(path) +
          '" title="' + esc(doc.title) + '">' + esc(doc.title) + "</a>";
      }).join("");
      return '<div class="tree-group' + (open ? " open" : "") + '">' +
        '<button class="tree-head' + (open ? " open" : "") + '" type="button" ' +
        'data-group="' + esc(group.key) + '" aria-expanded="' + (open ? "true" : "false") +
        '">' +
        '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M7.6 5.2L12.4 10l-4.8 4.8"/></svg>' +
        esc(group.label) +
        '<span class="tree-count">' + group.docs.length + "</span></button>" +
        '<div class="tree-docs">' + docs + "</div></div>";
    }).join("");
  }

  function pathFromHash() {
    var raw = (location.hash || "").replace(/^#\/?/, "");
    try { return shortPath(decodeURI(raw)); } catch (e) { return shortPath(raw); }
  }

  function setHash(path) {
    var want = "#/" + encodeURI(path);
    if (location.hash === want) return;
    location.hash = want;
  }

  /* ---------------- 正文 ---------------- */
  function drawCrumb() {
    var el = $("#docsCrumb");
    if (!el) return;
    if (!S.path) { el.textContent = ""; return; }
    for (var i = 0; i < S.groups.length; i++) {
      var docs = S.groups[i].docs;
      for (var j = 0; j < docs.length; j++) {
        if (shortPath(docs[j].path) === S.path) {
          el.textContent = S.groups[i].label + " › " + docs[j].title;
          return;
        }
      }
    }
    el.textContent = S.path;
  }

  /* 加载态不占任何位置：慢的时候才把正文压暗、右上角转个圈 */
  function setBusy(on) {
    var body = $(".docs-body");
    if (!body) return;
    if (S.busyTimer) { clearTimeout(S.busyTimer); S.busyTimer = null; }
    if (!on) {
      body.classList.remove("is-busy");
      return;
    }
    S.busyTimer = setTimeout(function () {
      body.classList.add("is-busy");
      S.busyTimer = null;
    }, 260);
  }

  function stampDate(epoch) {
    if (!epoch) return "";
    var d = new Date(epoch * 1000);
    function two(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "/" + two(d.getMonth() + 1) + "/" + two(d.getDate());
  }

  /* 正文第一行如果是大标题，就把它提出来当页标题，下面写"上次更新" */
  function splitTitle(text) {
    var body = String(text || "").replace(/^\uFEFF/, "");
    var lines = body.split("\n");
    var i = 0;
    while (i < lines.length && !lines[i].trim()) i++;
    var m = i < lines.length ? lines[i].match(/^ {0,3}#\s+(.+?)\s*#*\s*$/) : null;
    if (!m) return {title: "", body: body};
    return {title: m[1], body: lines.slice(i + 1).join("\n")};
  }

  function openDoc(path) {
    var article = $("#docsArticle");
    var empty = $("#docsEmpty");
    S.path = path || "";
    drawTree();
    drawCrumb();
    closeNav();
    if (!S.path) {
      article.innerHTML = "";
      if (empty) empty.hidden = false;
      return Promise.resolve();
    }
    if (empty) empty.hidden = true;
    setBusy(true);
    return fetch("api/docs/doc?path=" + encodeURIComponent(S.path), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "CAEventManager" }
    }).then(function (res) {
      if (res.status === 401) { location.href = "login"; return null; }
      return res.json();
    }).then(function (data) {
      setBusy(false);
      if (!data) return;
      if (data.ok === false) {
        article.innerHTML = '<p class="docs-empty">' + esc(data.error || "读不到这篇") +
          "</p>";
        return;
      }
      var parts = splitTitle(data.text);
      var title = parts.title || data.title || S.path;
      var meta = data.updated ? "上次更新 " + stampDate(data.updated) : "";
      var body = window.MD.render(parts.body, {
        docs: S.docs,
        base: dirOf(S.path),
        href: function (p) { return "#/" + p; }
      });
      article.innerHTML = "<h1>" + esc(title) + "</h1>" +
        (meta ? '<p class="docs-meta">' + esc(meta) + "</p>" : "") + body;
      document.title = title + " · 使用文档";
      window.scrollTo(0, 0);
    }).catch(function (e) {
      setBusy(false);
      article.innerHTML = '<p class="docs-empty">读不到这篇：' + esc(e.message) + "</p>";
    });
  }

  /* ---------------- 抽屉（窄屏） ---------------- */
  function openNav() {
    $("#docsShell").classList.add("nav-open");
    $("#docsScrim").hidden = false;
  }

  function closeNav() {
    var shell = $("#docsShell");
    if (!shell || !shell.classList.contains("nav-open")) return;
    shell.classList.remove("nav-open");
    $("#docsScrim").hidden = true;
  }

  /* ---------------- 复制代码 ---------------- */
  function copyText(text, button) {
    var done = function () {
      if (!button) return;
      button.classList.add("copied");
      setTimeout(function () { button.classList.remove("copied"); }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(done);
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); } catch (e) { /* 没别的办法 */ }
    area.remove();
    done();
  }

  /* ---------------- 起步 ---------------- */
  function boot() {
    initTheme();
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var head = t.closest("[data-group]");
      if (head) {
        var key = head.getAttribute("data-group");
        S.open[key] = !head.classList.contains("open");
        drawTree();
        return;
      }
      var code = t.closest("[data-copy-code]");
      if (code) {
        var block = code.closest(".md-code");
        var pre = block ? block.querySelector("pre code") || block.querySelector("pre") : null;
        copyText(pre ? pre.textContent : "", code);
        return;
      }
      var docLink = t.closest("[data-doc-link]");
      if (docLink) {
        e.preventDefault();
        closeNav();
        setHash(docLink.getAttribute("data-doc-link"));
        return;
      }
      var doc = t.closest("[data-doc]");
      if (doc) {
        e.preventDefault();
        closeNav();
        setHash(doc.getAttribute("data-doc"));
        return;
      }
      if (t.closest("#docsScrim")) closeNav();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeNav();
    });
    var menu = $("#docsMenu");
    if (menu) menu.addEventListener("click", openNav);
    var theme = $("#docsTheme");
    if (theme) theme.addEventListener("click", toggleTheme);
    window.addEventListener("hashchange", function () { openDoc(pathFromHash()); });

    fetch("api/docs/tree", {
      credentials: "same-origin",
      headers: { "X-Requested-With": "CAEventManager" }
    }).then(function (res) {
      if (res.status === 401) { location.href = "login"; return null; }
      return res.json();
    }).then(function (data) {
      if (!data) return;
      S.groups = (data && data.groups) || [];
      S.docs = [];
      S.groups.forEach(function (g) {
        g.docs.forEach(function (d) { S.docs.push(shortPath(d.path)); });
      });
      var want = pathFromHash();
      if (S.docs.indexOf(want) < 0) want = S.docs.length ? S.docs[0] : "";
      if (!want) {
        drawTree();
        openDoc("");
        return;
      }
      S.path = want;
      if (location.hash !== "#/" + encodeURI(want)) {
        try { history.replaceState(null, "", "#/" + encodeURI(want)); } catch (err) {}
      }
      return openDoc(want);
    }).catch(function (e) {
      var tree = $("#docsTree");
      if (tree) {
        tree.innerHTML = '<p class="docs-empty">目录读不到：' + esc(e.message) + "</p>";
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
