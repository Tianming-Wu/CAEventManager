/* Big screen: QR (rotating check-in code during the sign-in phase, static board
   link during the draw phase), live stats, persistent winners strip, name wall,
   and the draw animation. */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var data = null;
  var lastSeq = null;
  var qrUrl = null;
  var rollTimer = null;
  var hideTimer = null;
  var tokenTimer = null;
  var MAX_WINNERS = 14;   // keep the strip from eating the name wall
  var forceSignin = null; // null = follow the server phase; true/false = local override (Q)
  var etag = "";          // last board version, so a re-read can be a 304
  var streamed = false;   // the event stream is live, so polling is a recheck

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

  // ------------------------------------------------------------------- QR ---
  function drawQR(url, caption) {
    document.querySelector("#qr").setAttribute("data-url", url);
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      $("#qr").innerHTML =
        qr.createSvgTag({ cellSize: 6, margin: 2, scalable: true }) +
        '<div class="cap">' + caption + "</div>";
    } catch (e) {
      $("#qr").innerHTML = '<div class="cap" style="color:#f87171">二维码生成失败</div>';
    }
  }

  function renderStaticQR() {
    var full = boardUrl() + "/";
    if (full === qrUrl) return;
    qrUrl = full;
    drawQR(full, "扫码查看奖票");
  }

  function renderCheckinQR() {
    var fetchToken = function () {
      fetch(LT.PREFIX + "/api/screen/token", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var url = boardUrl() + "/checkin?t=" + d.token;
          if (url !== qrUrl) {
            qrUrl = url;
            drawQR(url, "扫码签到 · 每 10 秒刷新");
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
  // The audience only needs two things from this screen: how to get in, and who
  // won. Task counters and ticket totals are admin information, not signage.
  function renderStats() {
    var st = data.stats || {};
    var half = '<span class="sstat-of">/' + (st.participants || st.total) + "</span>";
    var cards = phase() === "signin"
      ? [["green", st.checkin + half, "已签到"]]
      : [["green", st.checkin + half, "到场"],
         ["gold", st.live_tickets + '<span class="sstat-of">/' + st.pool_size + "</span>", "奖票"]];
    $("#stats").innerHTML = cards.map(function (c) {
      return '<div class="sstat ' + c[0] + '"><b>' + c[1] + "</b><span>" + c[2] + "</span></div>";
    }).join("");
    $("#subtitle").textContent = (data.config && data.config.subtitle) || "扫码查看奖票";
    $("#title").textContent = (data.config && data.config.title) || "抽奖";
    document.title = ((data.config && data.config.title) || "抽奖") + " · 大屏";
  }

  function renderWinners() {
    var el = $("#winnersStrip");
    var list = (data.winners || []).slice().reverse();  // oldest first, reads left to right
    if (!list.length) { el.innerHTML = ""; return; }
    var shown = list.slice(-MAX_WINNERS);
    var hidden = list.length - shown.length;
    var html = '<span class="wstrip-label">中奖名单</span>';
    html += shown.map(function (w) {
      var person = w.mode === "person";
      return '<div class="wstrip-item' + (person ? " person" : "") + '">' +
        '<span class="pz">' + LT.esc(w.prize || "奖品") + "</span>" +
        '<span class="nm">' + LT.esc(w.name || "?") + "</span>" +
        '<span class="cd">' + (person ? "到场均等" : "#" + LT.code(w.code)) + "</span></div>";
    }).join("");
    if (hidden > 0) html += '<span class="wstrip-more">…等共 ' + list.length + " 个</span>";
    el.innerHTML = html;
  }

  function renderWall() {
    var wall = $("#wall");
    var on = !!(data.config && data.config.screen_show_wall);
    document.querySelector(".screen").classList.toggle("no-wall", !on);
    if (!on) {
      wall.innerHTML = "";
      return;
    }
    var showSid = !!(data.config && data.config.show_sid);
    wall.innerHTML = (data.students || []).map(function (s) {
      var codes = s.codes || [];
      var right = codes.length
        ? codes.map(function (c) { return LT.code(c.code); }).join(" ")
        : (s.progress ? "已签到" : (showSid ? s.sid : "—"));
      return '<div class="w p' + (s.progress || 0) + '">' +
        '<div class="n">' + LT.esc(s.name) + "</div>" +
        '<div class="c">' + LT.esc(right) + "</div></div>";
    }).join("");
  }

  // --------------------------------------------------------------- layout ---
  function applyLayout() {
    var signin = phase() === "signin";
    var el = document.querySelector(".screen");
    el.classList.toggle("mode-signin", signin);
    el.classList.toggle("mode-draw", !signin);
  }

  // ---------------------------------------------------------------- draws ---
  function playDraw(draw) {
    var overlay = $("#overlay");
    var codeEl = $("#dCode");
    var winnerEl = $("#dWinner");
    var person = draw.mode === "person";
    var pool = Math.max(1, (data.stats && data.stats.max_code) || (data.stats && data.stats.pool_size) || 200);
    var names = (data.students || []).filter(function (s) { return s.progress > 0; })
      .map(function (s) { return s.name; });
    var sidHtml = data.config.show_sid && draw.sid
      ? ' <span class="sid2">' + LT.esc(draw.sid) + "</span>" : "";

    clearInterval(rollTimer);
    clearTimeout(hideTimer);
    overlay.classList.add("show");
    codeEl.style.display = "";
    $("#dPrize").textContent = draw.prize || (person ? "特等奖" : "抽奖");
    codeEl.classList.toggle("names", person);
    codeEl.textContent = person ? (names[0] || "…") : LT.code(draw.code);
    codeEl.classList.add("rolling");
    winnerEl.classList.remove("show");
    winnerEl.innerHTML = "";
    $("#dHint").textContent = person
      ? "在到场 " + names.length + " 人中均等抽取…"
      : "抽取中…";

    var startedAt = Date.now();
    rollTimer = setInterval(function () {
      if (person) {
        codeEl.textContent = names[Math.floor(Math.random() * Math.max(1, names.length))] || "…";
      } else {
        codeEl.textContent = LT.code(1 + Math.floor(Math.random() * pool));
      }
      if (Date.now() - startedAt > 2600) {
        clearInterval(rollTimer);
        codeEl.classList.remove("rolling");
        if (person) {
          codeEl.textContent = draw.name || "";
          winnerEl.innerHTML = "<span class='nm2'>特等奖</span>" + sidHtml;
        } else {
          codeEl.textContent = LT.code(draw.code);
          winnerEl.innerHTML = '<span class="nm2">' + LT.esc(draw.name || "") + "</span>" + sidHtml;
        }
        winnerEl.classList.add("show");
        $("#dHint").textContent = "请上台领奖 · 点击任意处关闭";
        hideTimer = setTimeout(closeOverlay, 15000);
      }
    }, 55);
  }

  function closeOverlay() {
    clearInterval(rollTimer);
    clearTimeout(hideTimer);
    $("#overlay").classList.remove("show");
    $("#dCode").style.display = "";
  }

  // A prize drawn 「一次抽完」 arrives as one batch: roll once, then show the
  // whole list together instead of playing one animation per winner.
  function playBatch(batch) {
    var overlay = $("#overlay");
    var codeEl = $("#dCode");
    var winnerEl = $("#dWinner");
    var list = (batch.winners || []).slice();
    if (!list.length) return;
    var person = batch.mode === "person";
    var showSid = !!(data.config && data.config.show_sid);
    var names = (data.students || []).filter(function (s) { return s.progress > 0; })
      .map(function (s) { return s.name; });
    var pool = Math.max(1, (data.stats && data.stats.max_code) || (data.stats && data.stats.pool_size) || 200);
    if (!person) {
      // ticket numbers ascending: the audience reads the list like a roster
      list.sort(function (a, b) { return (a.code || 0) - (b.code || 0); });
    }

    clearInterval(rollTimer);
    clearTimeout(hideTimer);
    overlay.classList.add("show");
    codeEl.style.display = "";
    codeEl.classList.toggle("names", person);
    codeEl.classList.add("rolling");
    codeEl.textContent = person ? (names[0] || "…") : LT.code(1 + Math.floor(Math.random() * pool));
    winnerEl.classList.remove("show");
    winnerEl.innerHTML = "";
    $("#dPrize").textContent = (batch.prize || "抽奖") + " · " + list.length + " 人";
    $("#dHint").textContent = "抽取中…";

    var startedAt = Date.now();
    rollTimer = setInterval(function () {
      codeEl.textContent = person
        ? (names[Math.floor(Math.random() * Math.max(1, names.length))] || "…")
        : LT.code(1 + Math.floor(Math.random() * pool));
      if (Date.now() - startedAt > 1700) {
        clearInterval(rollTimer);
        codeEl.classList.remove("rolling");
        codeEl.style.display = "none";
        winnerEl.innerHTML = '<div class="draw-reveal">' + list.map(function (w) {
          return '<div class="it">' +
            (w.mode === "person" ? "" : '<span class="cd">' + LT.code(w.code) + "</span>") +
            '<span class="nm">' + LT.esc(w.name || "?") + "</span>" +
            (showSid && w.sid ? '<span class="sd">' + LT.esc(w.sid) + "</span>" : "") +
            "</div>";
        }).join("") + "</div>";
        winnerEl.classList.add("show");
        $("#dHint").textContent = "请上台领奖 · 点击任意处关闭";
        hideTimer = setTimeout(closeOverlay, 20000);
      }
    }, 60);
  }

  // ---------------------------------------------------------------- render --
  function render() {
    renderStats();
    renderWinners();
    applyLayout();
    renderQR();
    renderWall();
    var d = data.lastDraw;
    if (d && lastSeq !== null && d.seq !== lastSeq) {
      var batch = data.lastBatch;
      if (batch && batch.winners && batch.winners.length > 1) playBatch(batch);
      else playDraw(d);
    }
    if (d) lastSeq = d.seq;
    else if (lastSeq === null) lastSeq = 0;
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
        // 服务端换了版本：抽奖动画中间不能换页面，等这一段放完再说
        LT.version(d.version);
      })
      .catch(function () {});
  }

  // The screen is driven by the server's event stream; the 1.5 s poll stays as
  // the fallback for when that stream cannot be opened or keeps dropping.
  function boot() {
    $("#overlay").addEventListener("click", closeOverlay);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeOverlay();
      // Q flips the layout locally without touching the server phase.
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
    // 部署换了版本：抽奖动画正在播就先记着，放完再换页面
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
      // with the stream live, one read every 30 s is enough to notice a missed
      // event; with it down the page is back to polling
      if (streamed && ticks % 20) return;
      load();
    }, 1500);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
