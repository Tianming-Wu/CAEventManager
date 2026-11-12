/* 大屏外壳：二维码、阶段版式、以及模块填进来的几块内容。

   平台只负责"怎么画"：数字条、名单条、名字墙、中区卡片、抽奖那种揭示动画。
   "画什么"全在 `present().screen` 里——平台不认识奖票、名次这些词，换一个模块
   照样能用这块屏。哪一块没给就不画哪一块。 */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var data = null;
  var lastRevealKey = null;
  var qrUrl = null;
  var rollTimer = null;
  var hideTimer = null;
  var tokenTimer = null;
  var MAX_STRIP = 14;     // 名单条最多显示几条，剩下的折成一句"…等共 N 个"
  var forceSignin = null; // null = 跟服务端；true/false = 按 Q 本地翻版式
  var etag = "";          // 上次拿到的版本，读回来可以是 304
  var streamed = false;   // 事件流活着，轮询只是兜底

  function block() {
    return (data && data.present && data.present.screen) || {};
  }

  function boardUrl() {
    var cfg = (data && data.config) || {};
    var configured = (cfg.public_base_url || "").trim();
    if (configured) return configured.replace(/\/+$/, "");
    return location.origin + (LT.PREFIX || "");
  }

  function phase() {
    if (forceSignin !== null) return forceSignin ? "signin" : "draw";
    var p = data && data.config && data.config.screen_phase;
    return p === "draw" ? "draw" : "signin";
  }

  function caption(which, fallback) {
    var qr = block().qr || {};
    return qr[which] || fallback;
  }

  // ------------------------------------------------------------------- QR ---
  function drawQR(url, text) {
    document.querySelector("#qr").setAttribute("data-url", url);
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      $("#qr").innerHTML =
        qr.createSvgTag({ cellSize: 6, margin: 2, scalable: true }) +
        '<div class="cap">' + text + "</div>";
    } catch (e) {
      $("#qr").innerHTML = '<div class="cap" style="color:#f87171">二维码生成失败</div>';
    }
  }

  function renderStaticQR() {
    var full = boardUrl() + "/";
    if (full === qrUrl) return;
    qrUrl = full;
    drawQR(full, caption("draw", "扫码查看"));
  }

  function renderCheckinQR() {
    var fetchToken = function () {
      fetch(LT.PREFIX + "/api/screen/token", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var url = boardUrl() + "/checkin?t=" + d.token;
          if (url !== qrUrl) {
            qrUrl = url;
            drawQR(url, caption("signin", "扫码签到 · 每 10 秒刷新"));
          }
        })
        .catch(function () {});
    };
    fetchToken();
    clearInterval(tokenTimer);
    tokenTimer = setInterval(fetchToken, 3000);
  }

  function renderQR() {
    clearInterval(tokenTimer);
    tokenTimer = null;
    if (phase() === "signin") {
      renderCheckinQR();
    } else {
      qrUrl = null;
      renderStaticQR();
    }
  }

  // ---------------------------------------------------------------- blocks --
  function esc(s) { return LT.esc(s === null || s === undefined ? "" : String(s)); }

  function renderHead() {
    var cfg = (data && data.config) || {};
    var title = cfg.title || "";
    $("#subtitle").textContent = cfg.subtitle || caption("subtitle", "");
    $("#title").textContent = title;
    document.title = title ? title + " · 大屏" : "大屏";
  }

  /* 数字条：给了 phase 的只在那个阶段出现，没给的阶段无关 */
  function renderBanner() {
    var now = phase();
    var items = (block().banner || []).filter(function (it) {
      return !it.phase || it.phase === now;
    });
    $("#stats").innerHTML = items.map(function (it) {
      var of = it.of ? '<span class="sstat-of">' + esc(it.of) + "</span>" : "";
      return '<div class="sstat ' + esc(it.tone || "") + '"><b>' +
        esc(it.value) + of + "</b><span>" + esc(it.label) + "</span></div>";
    }).join("");
  }

  function renderStrip() {
    var el = $("#winnersStrip");
    var strip = block().strip || {};
    var list = (strip.items || []).slice().reverse();   // 最早的排在最左
    if (!list.length) { el.innerHTML = ""; return; }
    var shown = list.slice(-MAX_STRIP);
    var hidden = list.length - shown.length;
    var html = strip.label ? '<span class="wstrip-label">' + esc(strip.label) + "</span>" : "";
    html += shown.map(function (it) {
      var sub = it.note ? '<span class="cd">' + esc(it.note) + "</span>" : "";
      return '<div class="wstrip-item' + (it.kind ? " " + esc(it.kind) : "") + '">' +
        (it.title ? '<span class="pz">' + esc(it.title) + "</span>" : "") +
        '<span class="nm">' + esc(it.name) + "</span>" + sub + "</div>";
    }).join("");
    if (hidden > 0) {
      html += '<span class="wstrip-more">…等共 ' + list.length + " 个</span>";
    }
    el.innerHTML = html;
  }

  function renderWall() {
    var wall = $("#wall");
    var spec = block().wall || {};
    document.querySelector(".screen").classList.toggle("no-wall", !spec.on);
    if (!spec.on) {
      wall.innerHTML = "";
      return;
    }
    wall.innerHTML = (spec.items || []).map(function (it) {
      return '<div class="w' + (it.level ? " p" + esc(it.level) : "") + '">' +
        '<div class="n">' + esc(it.name) + "</div>" +
        '<div class="c">' + esc(it.note) + "</div></div>";
    }).join("");
  }

  function renderCards() {
    var box = $("#cards");
    if (!box) return;
    box.innerHTML = (block().cards || []).map(function (it) {
      return '<div class="scard"><div class="t">' + esc(it.title) + "</div>" +
        '<div class="b">' + esc(it.body) + "</div></div>";
    }).join("");
  }

  // --------------------------------------------------------------- layout ---
  function applyLayout() {
    var signin = phase() === "signin";
    var el = document.querySelector(".screen");
    el.classList.toggle("mode-signin", signin);
    el.classList.toggle("mode-draw", !signin);
  }

  // --------------------------------------------------------------- reveal ---
  /* 揭示：模块说"现在有东西要放"，平台负责滚一遍再定格。
     candidates 是滚动时依次闪过的字，value 是最后落定的那个。 */
  function playReveal(reveal) {
    var overlay = $("#overlay");
    var codeEl = $("#dCode");
    var winnerEl = $("#dWinner");
    var cands = (reveal.candidates || []).slice();
    var many = (reveal.items || []).length > 1;
    if (!cands.length) cands = [reveal.value || "…"];

    clearInterval(rollTimer);
    clearTimeout(hideTimer);
    overlay.classList.add("show");
    codeEl.style.display = "";
    codeEl.classList.toggle("names", !!reveal.names);
    codeEl.classList.add("rolling");
    codeEl.textContent = cands[0];
    $("#dPrize").textContent = reveal.title || "";
    winnerEl.classList.remove("show");
    winnerEl.innerHTML = "";
    $("#dHint").textContent = reveal.hint || "";

    var pick = function () { return cands[Math.floor(Math.random() * cands.length)]; };
    var startedAt = Date.now();
    var span = many ? 1700 : 2600;
    var step = many ? 60 : 55;
    rollTimer = setInterval(function () {
      codeEl.textContent = pick();
      if (Date.now() - startedAt <= span) return;
      clearInterval(rollTimer);
      codeEl.classList.remove("rolling");
      if (many) {
        codeEl.style.display = "none";
        winnerEl.innerHTML = '<div class="draw-reveal">' + reveal.items.map(function (it) {
          return '<div class="it">' +
            (it.value ? '<span class="cd">' + esc(it.value) + "</span>" : "") +
            '<span class="nm">' + esc(it.name) + "</span>" +
            (it.note ? '<span class="sd">' + esc(it.note) + "</span>" : "") +
            "</div>";
        }).join("") + "</div>";
      } else {
        codeEl.textContent = reveal.value || "";
        winnerEl.innerHTML =
          (reveal.note ? '<span class="nm2">' + esc(reveal.note) + "</span>" : "") +
          (reveal.sub ? ' <span class="sid2">' + esc(reveal.sub) + "</span>" : "");
      }
      winnerEl.classList.add("show");
      hideTimer = setTimeout(closeOverlay, reveal.holdMs || (many ? 20000 : 15000));
    }, step);
  }

  function closeOverlay() {
    clearInterval(rollTimer);
    clearTimeout(hideTimer);
    $("#overlay").classList.remove("show");
    $("#dCode").style.display = "";
  }

  // ---------------------------------------------------------------- render --
  function render() {
    if (!data || !data.present) return;
    renderHead();
    renderBanner();
    renderStrip();
    applyLayout();
    renderQR();
    renderWall();
    renderCards();
    var reveal = block().reveal;
    if (reveal && reveal.key && lastRevealKey !== null && reveal.key !== lastRevealKey) {
      playReveal(reveal);
    }
    if (reveal && reveal.key) lastRevealKey = reveal.key;
    else if (lastRevealKey === null) lastRevealKey = "";
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
        // 服务端换了版本：揭示动画中间不能换页面，等这一段放完再说
        LT.version(d.version);
      })
      .catch(function () {});
  }

  function boot() {
    $("#overlay").addEventListener("click", closeOverlay);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeOverlay();
      // Q 只翻本地版式，不动服务端的阶段
      if (e.key === "q" || e.key === "Q") {
        forceSignin = phase() !== "signin";
        if (data) { applyLayout(); renderQR(); }
      }
    });
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
    // 部署换了版本：动画正在放就先记着，放完再换页面
    var staleVersion = "";
    var swapWhenIdle = function () {
      if (!staleVersion) return;
      if ($("#overlay").classList.contains("show")) return;
      location.reload();
    };
    LT.outdated(function (version) {
      staleVersion = version;
      swapWhenIdle();
      return true;      // 由这一页自己决定什么时候换
    });
    setInterval(swapWhenIdle, 1500);
    load();
    var ticks = 0;
    setInterval(function () {
      if (document.hidden) return;
      ticks += 1;
      // 事件流活着时半分钟对一次就够；断了就退回轮询
      if (streamed && ticks % 20) return;
      load();
    }, 1500);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
