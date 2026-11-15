/* 使用文档页：树状目录 + 正文。
   这一页自己一套骨架，不加载面板的脚本；接口只有 /api/docs/tree 与 /api/docs/doc。
   深浅色存在本机（键名与面板分开），第一次进来时沿用面板那一次的选择。 */
(function () {
  "use strict";

  var DOCS_THEME = "xl.docs.theme";
  var PANEL_THEME = "xl.panel.theme";

  var S = {
    groups: [],
    path: "",
    open: {},
    loading: false
  };

  function $(sel) { return document.querySelector(sel); }

  function esc(text) { return window.MD.esc(text); }

  function store(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { /* 隐私模式：存不下就只影响下次 */ }
  }

  function read(key) {
    try { return localStorage.getItem(key) || ""; } catch (e) { return ""; }
  }

  /* ---------------- 深浅色 ---------------- */
  function prefersDark() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
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
    applyTheme(prefersDark());       // 面板也没选过：跟着系统
  }

  function toggleTheme() {
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    applyTheme(!dark);
    store(DOCS_THEME, !dark ? "dark" : "light");
  }

  /* ---------------- 目录树 ---------------- */
  function drawTree() {
    var box = $("#docsTree");
    if (!box) return;
    box.innerHTML = S.groups.map(function (group) {
      var open = S.open[group.key] !== false;
      var docs = group.docs.map(function (doc) {
        return '<a class="tree-doc' + (doc.path === S.path ? " on" : "") +
          '" href="#' + encodeURI(doc.path) + '" data-doc="' + esc(doc.path) +
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
    var raw = (location.hash || "").replace(/^#/, "");
    try { return decodeURI(raw); } catch (e) { return raw; }
  }

  function setHash(path) {
    var want = "#" + encodeURI(path);
    if (location.hash !== want) location.hash = want;
  }

  /* ---------------- 正文 ---------------- */
  function drawCrumb() {
    var el = $("#docsCrumb");
    if (!el) return;
    if (!S.path) { el.textContent = ""; return; }
    for (var i = 0; i < S.groups.length; i++) {
      var docs = S.groups[i].docs;
      for (var j = 0; j < docs.length; j++) {
        if (docs[j].path === S.path) {
          el.textContent = S.groups[i].label + " › " + docs[j].title;
          return;
        }
      }
    }
    el.textContent = S.path;
  }

  function openDoc(path) {
    var article = $("#docsArticle");
    var loading = $("#docsLoading");
    var empty = $("#docsEmpty");
    S.path = path || "";
    drawTree();
    drawCrumb();
    closeNav();
    if (!S.path) {
      article.innerHTML = "";
      if (loading) loading.hidden = true;
      if (empty) empty.hidden = false;
      return Promise.resolve();
    }
    if (empty) empty.hidden = true;
    if (loading) loading.hidden = false;
    return fetch("api/docs/doc?path=" + encodeURIComponent(S.path), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "CAEventManager" }
    }).then(function (res) {
      if (res.status === 401) { location.href = "login"; return null; }
      return res.json();
    }).then(function (data) {
      if (loading) loading.hidden = true;
      if (!data) return;
      if (data.ok === false) {
        article.innerHTML = '<p class="docs-empty">' + esc(data.error || "读不到这篇") +
          "</p>";
        return;
      }
      article.innerHTML = window.MD.render(data.text || "");
      document.title = (data.title || data.path) + " · 使用文档";
      var box = document.querySelector(".docs-body");
      if (box) box.scrollTop = 0;
      window.scrollTo(0, 0);
    }).catch(function (e) {
      if (loading) loading.hidden = true;
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
        S.open[key] = S.open[key] === false;      // 取反
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
      var doc = t.closest("[data-doc]");
      if (doc) {
        // 走 hash，浏览器的前进后退照常可用
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
      var want = pathFromHash();
      var exists = false;
      S.groups.forEach(function (g) {
        g.docs.forEach(function (d) { if (d.path === want) exists = true; });
      });
      if (!exists) {
        want = S.groups.length && S.groups[0].docs.length ? S.groups[0].docs[0].path : "";
      }
      drawTree();
      if (want) {
        S.path = want;
        if (location.hash !== "#" + encodeURI(want)) {
          // 第一次进来：地址里补上这一篇，方便直接分享
          try { history.replaceState(null, "", "#" + encodeURI(want)); } catch (err) {}
        }
        return openDoc(want);
      }
      openDoc("");
    }).catch(function (e) {
      var tree = $("#docsTree");
      if (tree) tree.innerHTML = '<p class="docs-empty">目录读不到：' + esc(e.message) + "</p>";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
