/* 模块自己的页签脚本：平台在活动后台启动后按 pages 声明依次载入，
 * 然后调用 MODULE.pages.<页签>.mount(ctx)。ctx 里是平台给的那几件东西。
 *
 * 这一段是「奖票」页：把可抽的票、保底与速度奖励算出来的结果画出来，
 * 以及结算、取消结算、重置这三颗按钮。
 * 数据来自 /api/admin/plan，口算在模块的 model.py 里。
 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;

  function renderPlan() {
    var p = S.plan;
    if (!p) return;
    var sum = p.summary || {};
    var chips = [
      '<span class="chip green"><b>' + sum.present + "</b> 人可参与</span>",
      '<span class="chip">保底 <b>' + sum.guaranteed + "</b> 张</span>",
      '<span class="chip gold">速度奖励 <b>' + sum.bonus_pool + "</b> 张</span>",
      '<span class="chip">总计 <b>' + sum.pool_size + "</b> 张全部发完</span>",
      '<span class="chip">β = <b>' + sum.beta + "</b></span>"
    ];
    chips.push('<span class="chip">' + (sum.pool_mode === "fixed"
      ? "固定池 " + sum.fixed_pool_size + " 张"
      : "自动池 = " + sum.present + "×3 + " + sum.bonus_constant) + "</span>");
    if (sum.present) {
      chips.push('<span class="chip blue">最多一人 <b>+' + sum.max_bonus + "</b> 张</span>");
    }
    if (sum.finalized) chips.push('<span class="chip gold live">已结算</span>');
    if (!sum.allow_bonus) chips.push('<span class="chip">速度奖励已关闭</span>');
    lib.$("#planSummary").innerHTML = chips.join("");

    lib.$("#planTable tbody").innerHTML = (p.rows || []).map(function (r) {
      var stu = S.byId[r.id] || {};
      return "<tr>" +
        '<td class="num right">' + r.pos + "</td>" +
        "<td><b>" + LT.esc(r.name) + "</b> <span style='color:var(--dim);font-size:.8em'>" + LT.esc(r.sid) + "</span></td>" +
        '<td class="num right">' + r.tasks + "</td>" +
        '<td class="num right">' + r.mean_rank.toFixed(3) + "</td>" +
        '<td class="num right">' + r.weight.toFixed(2) + "</td>" +
        '<td class="num right">' + r.expected.toFixed(2) + "</td>" +
        '<td class="num right">' + (r.bonus ? '<b style="color:var(--gold)">+' + r.bonus + "</b>" : '<span style="color:var(--dim)">0</span>') + "</td>" +
        '<td class="num right">' + (stu.ticket_count || 0) + "</td></tr>";
    }).join("") || '<tr><td colspan="8" class="empty">还没有人签到</td></tr>';

    lib.$("#planNote").textContent = sum.finalized
      ? "已结算于 " + LT.fmtDateTime((S.config || {}).finalized_at)
      : "预览，点击「结算」才会真正发票";
    renderButtons();
  }

  /* 结算、取消结算、重置：状态跟着计划与解锁开关走 */
  function renderButtons() {
    var sum = (S.plan && S.plan.summary) || {};
    var fin = lib.$("#finalizeBtn");
    if (fin) fin.disabled = !!sum.finalized;
    var un = lib.$("#unfinalizeBtn");
    if (un) un.disabled = !sum.finalized || !S.unlocked;
    var reset = lib.$("#resetTicketsBtn");
    if (reset) reset.disabled = !S.unlocked;
  }

  function loadPlan() {
    return LT.api("/api/admin/plan").then(function (d) {
      S.plan = d;
      renderPlan();
    }).catch(lib.fail);
  }

  function finalize() {
    var sum = (S.plan && S.plan.summary) || {};
    LT.confirm({
      title: "结算奖票？",
      okText: "结算",
      html: "将为 <b>" + sum.present + "</b> 位到场同学按速度分配 <b style='color:var(--gold)'>" +
        sum.bonus_pool + "</b> 张剩余奖票。<br>结算后不能继续标记（需要先取消结算）。"
    }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/finalize", { method: "POST" }).then(function (d) {
        lib.ok("结算完成，发放了 <b>" + d.result.assigned + "</b> 张奖励票");
        return lib.reloadState().then(loadPlan);
      }).catch(lib.bad);
    });
  }

  function unfinalize() {
    LT.confirm({
      title: "取消结算", danger: true, okText: "取消结算",
      html: "会收回所有「速度奖励」奖票，之后可以继续标记。<br>确认？"
    }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/unfinalize", { method: "POST" })
        .then(function () {
          lib.ok("已取消结算");
          return lib.reloadState().then(loadPlan);
        })
        .catch(lib.bad);
    });
  }

  function resetTickets() {
    LT.confirm({
      title: "重置全部奖票", danger: true, okText: "重置",
      html: "会收回所有人的奖票号码并取消结算，标记本身保留。<br>" +
        "重新标记/签到时会重新发号，号码可能变化。<br>确认？"
    }).then(function (yes) {
      if (!yes) return;
      LT.api("/api/admin/reset_tickets", { method: "POST" })
        .then(function () {
          lib.ok("已重置奖票");
          return lib.reloadState().then(loadPlan);
        })
        .catch(lib.bad);
    });
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    lib.$("#finalizeBtn").addEventListener("click", finalize);
    lib.$("#unfinalizeBtn").addEventListener("click", unfinalize);
    lib.$("#resetTicketsBtn").addEventListener("click", resetTickets);
  }

  window.MODULE.pages.tickets = {
    // 平台启动后调一次，给它那几件共用件与当前状态
    mount: function (page) {
      LT = page.LT;
      ctx = page;
      S = page.state || {};
      lib = window.MODULE.lib;
      lib.bind(page);
      bindOnce();
    },
    // 平台每次拉到新状态就调一次；只重画自己这一段
    refresh: function (state, tab) {
      S = state || S;
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      if (ctx && ctx.tab === "tickets") renderPlan();
      else renderButtons();
    },
    // 切到这个页签时调一次：重新算一遍再画
    show: function (tab) {
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      return loadPlan();
    },
  };
})();
