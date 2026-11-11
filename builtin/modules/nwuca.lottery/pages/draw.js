/* 「抽奖」页的脚本：奖品列表、抽取节奏、抽奖记录、大屏阶段。
 * 平台在活动后台启动后按 pages 声明载入本文件，然后调 MODULE.pages.draw.mount()。
 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;

  function paceSwitch(index, oneShot) {
    return '<div class="seg" role="group">' +
      '<button type="button" class="seg-btn' + (oneShot ? "" : " on") +
      '" data-pace="step" data-i="' + index + '">逐个</button>' +
      '<button type="button" class="seg-btn' + (oneShot ? " on" : "") +
      '" data-pace="batch" data-i="' + index + '">一次抽完</button></div>';
  }

  function renderPrizeList() {
    var body = lib.$("#prizeTable tbody");
    if (!body) return;
    var prizes = S.prizes || [];
    if (!prizes.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">还没有配置奖品</td></tr>';
      return;
    }
    body.innerHTML = prizes.map(function (p, i) {
      var done = p.remaining <= 0;
      var blocked = p.mode === "ticket" && !S.config.finalized;
      var label = p.mode === "person" ? "均等" : "按票";
      var why = done ? "已抽完" : (blocked ? "需先结算奖票" : "");
      var act = p.batch ? "抽完" : "抽";
      return "<tr" + (done ? ' class="done-row"' : "") + ">" +
        "<td><b>" + LT.esc(p.name) + "</b></td>" +
        '<td style="color:var(--muted)">' + label + "</td>" +
        "<td>" + paceSwitch(i, p.batch) + "</td>" +
        '<td class="right left-n">' + p.drawn + "</td>" +
        '<td class="right left-n">' + p.remaining + " / " + p.count + "</td>" +
        '<td class="right"><button class="btn draw-btn ' + (done ? "" : "primary") +
        '" data-draw="' + i + '"' + (done || blocked ? " disabled" : "") +
        ' title="' + why + '">' + act + "</button></td></tr>";
    }).join("");
  }

  function setPace(index, oneShot) {
    var prizes = S.prizes || [];
    if (!prizes[index] || !!prizes[index].batch === oneShot) return;
    var payload = prizes.map(function (p, i) {
      return {
        name: p.name,
        count: p.count,
        mode: p.mode,
        batch: i === index ? oneShot : !!p.batch
      };
    });
    LT.api("/api/admin/settings", { method: "POST", body: { prizes: payload } })
      .then(function () { return lib.reloadState(); })
      .catch(lib.fail);
  }

  function renderDraws() {
    lib.$("#drawTable tbody").innerHTML = (S.draws || []).map(function (d) {
      var voided = !!d.voided_at;
      var person = d.mode === "person";
      return "<tr" + (voided ? ' style="opacity:.4"' : "") + ">" +
        '<td class="num right">' + d.seq + "</td>" +
        "<td>" + LT.esc(d.prize || "") + "</td>" +
        "<td>" + (person
          ? '<span class="chip" style="padding:1px 7px;font-size:.75rem">人级均等</span>'
          : '<span class="chip" style="padding:1px 7px;font-size:.75rem">按票</span>') + "</td>" +
        '<td class="num">' + (person ? '<span style="color:var(--dim)">—</span>'
          : '<span class="tkt">' + LT.code(d.code) + "</span>") + "</td>" +
        "<td><b>" + LT.esc(d.name || "?") + "</b> <span style='color:var(--dim)'>" + LT.esc(d.sid || "") + "</span></td>" +
        '<td class="num">' + LT.fmtDateTime(d.ts) + "</td>" +
        '<td class="right">' + (voided
          ? '<span class="note">已作废</span>'
          : '<button class="btn sm danger" data-void="' + d.id + '"' + (S.unlocked ? "" : " disabled") + ">作废</button>") +
        "</td></tr>";
    }).join("") || '<tr><td colspan="7" class="empty">还没有抽奖记录</td></tr>';
  }

  /* 大屏现在放的是签到还是抽奖 */
  function renderPhase() {
    var p = (S.config || {}).screen_phase || "signin";
    var el = lib.$("#phaseNow");
    if (!el) return;
    el.textContent = "当前：" + (p === "draw" ? "抽奖阶段" : "签到阶段");
    el.className = "chip " + (p === "draw" ? "gold" : "green");
    lib.$("#phaseSigninBtn").classList.toggle("primary", p !== "draw");
    lib.$("#phaseDrawBtn").classList.toggle("primary", p === "draw");
  }

  function setPhase(phase) {
    LT.api("/api/admin/phase", { method: "POST", body: { phase: phase } })
      .then(function (d) {
        S.config.screen_phase = d.phase;
        renderPhase();
        lib.ok(d.phase === "draw" ? "大屏已切到抽奖阶段" : "大屏已切到签到阶段（二维码每 10 秒刷新）");
      })
      .catch(lib.bad);
  }

  function winnerRow(w) {
    var tail = w.mode === "person"
      ? '<span class="code" style="font-family:inherit">均等</span>'
      : '<span class="code">#' + LT.code(w.code) + "</span>";
    return '<div class="winner"><span class="prize">' + LT.esc(w.prize) + "</span>" +
      '<span class="who">' + LT.esc(w.name) +
      " <span style='color:var(--dim)'>" + LT.esc(w.sid) + "</span></span>" + tail + "</div>";
  }

  function renderDrawResult(list, stopped) {
    var box = lib.$("#drawResult");
    if (!box) return;
    var html;
    if (list.length > 1) {
      html = '<div class="winner-head">本次抽出 ' + list.length + " 人</div>" +
        '<div class="winner-grid">' + list.map(function (w) {
          return '<span class="winner-cell">' + LT.esc(w.name) +
            (w.mode === "person" ? "" : ' <span class="tkt">' + LT.code(w.code) + "</span>") +
            "</span>";
        }).join("") + "</div>";
    } else {
      html = winnerRow(list[0]);
    }
    if (stopped) {
      html += '<p class="note" style="margin-top:8px">只抽出 ' + list.length +
        " 人：" + LT.esc(stopped) + "</p>";
    }
    box.innerHTML = html;
  }

  function drawPrize(index) {
    var p = (S.prizes || [])[index];
    if (!p) return;
    var body = { prize: p.name, mode: p.mode };
    if (p.batch) body.batch = true;
    LT.api("/api/admin/draw", { method: "POST", body: body })
      .then(function (d) {
        renderDrawResult(d.draws || [d.draw], d.stopped);
        return lib.reloadState();
      })
      .catch(lib.bad);
  }

  function voidDraw(id) {
    LT.confirm({ title: "作废这条抽奖记录？", danger: true, okText: "作废" }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/draw_void", { method: "POST", body: { id: id } })
        .then(function () {
          lib.ok("已作废");
          return lib.reloadState();
        })
        .catch(lib.bad);
    });
  }

  function renderAll() {
    renderPhase();
    renderPrizeList();
    renderDraws();
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    lib.$("#phaseSigninBtn").addEventListener("click", function () { setPhase("signin"); });
    lib.$("#phaseDrawBtn").addEventListener("click", function () { setPhase("draw"); });
    // 奖品表与抽奖记录都是现画的：在文档上接一次
    document.addEventListener("click", function (e) {
      var t = e.target;
      var dp = t.getAttribute && t.getAttribute("data-draw");
      if (dp) { drawPrize(Number(dp)); return; }
      var pc = t.getAttribute && t.getAttribute("data-pace");
      if (pc) { setPace(Number(t.getAttribute("data-i")), pc === "batch"); return; }
      var vd = t.getAttribute && t.getAttribute("data-void");
      if (vd) voidDraw(Number(vd));
    });
  }

  window.MODULE.pages.draw = {
    mount: function (page) {
      LT = page.LT;
      ctx = page;
      S = page.state || {};
      lib = window.MODULE.lib;
      lib.bind(page);
      bindOnce();
    },
    refresh: function (state, tab) {
      S = state || S;
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      renderAll();
    },
    show: function (tab) {
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      renderAll();
    },
  };
})();
