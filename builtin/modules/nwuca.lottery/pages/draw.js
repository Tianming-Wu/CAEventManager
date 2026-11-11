/* 「抽奖」页的脚本：奖品列表、抽取节奏、抽奖记录。
 * 平台在活动后台启动后按 pages 声明载入本文件，然后调 MODULE.pages.draw.mount()。
 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S;

  // 模块脚本不共享平台的内部件
  function $(sel) { return document.querySelector(sel); }
  function ok(html) { LT.toast(html, false); }
  function fail(e) { LT.toast(LT.esc((e && e.message) || "出错了"), true); }

  function normalizePrizes(list) {
    return (list || []).map(function (p) {
      if (typeof p === "string") return { name: p, count: 1, mode: "ticket", batch: false };
      return {
        name: (p && p.name) || "",
        count: Number(p && p.count) || 0,
        mode: p && p.mode === "person" ? "person" : "ticket",
        batch: !!(p && p.batch)
      };
    }).filter(function (p) { return p.name; });
  }

  function paceSwitch(index, oneShot) {
    return '<div class="seg" role="group">' +
      '<button type="button" class="seg-btn' + (oneShot ? "" : " on") +
      '" data-pace="step" data-i="' + index + '">逐个</button>' +
      '<button type="button" class="seg-btn' + (oneShot ? " on" : "") +
      '" data-pace="batch" data-i="' + index + '">一次抽完</button></div>';
  }

  function renderPrizeList() {
    var body = $("#prizeTable tbody");
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
      .then(function () { return (ctx && ctx.reload) ? ctx.reload() : null; })
      .catch(fail);
  }

  function renderPrizeEditor() {
    var box = $("#prizeEditor");
    if (!box) return;
    var prizes = normalizePrizes(S.config.prizes);
    box.innerHTML = prizes.map(function (p, i) {
      return '<div class="prize-row" data-i="' + i + '" data-batch="' + (p.batch ? "1" : "0") + '">' +
        '<input class="p-name" type="text" value="' + LT.esc(p.name) + '" placeholder="奖品名称">' +
        '<input class="p-count" type="number" min="0" max="999" value="' + p.count + '">' +
        '<select class="p-mode">' +
        '<option value="ticket"' + (p.mode === "ticket" ? " selected" : "") + ">按票</option>" +
        '<option value="person"' + (p.mode === "person" ? " selected" : "") + ">均等</option>" +
        "</select>" +
        '<button class="btn sm danger" data-prize-del="' + i + '">删除</button>' +
        "</div>";
    }).join("") || '<p class="note">还没有配置奖品</p>';
  }

  function renderDraws() {
    $("#drawTable tbody").innerHTML = S.draws.map(function (d) {
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

  window.MODULE.pages.draw = {
    mount: function (page) { LT = page.LT; ctx = page; S = page.state || {}; },
    refresh: function (state, tab) {
      S = state || S;
      if (ctx && tab) ctx.tab = tab;
      renderPrizeList();
      renderPrizeEditor();
      renderDraws();
    },
    // 平台点「逐个 / 一次抽完」这两颗按钮时调过来
    setPace: function (index, oneShot) {
      return setPace(index, oneShot);
    },
    show: function (tab) {
      if (ctx && tab) ctx.tab = tab;
      renderPrizeList();
      renderPrizeEditor();
      renderDraws();
    },
  };
})();
