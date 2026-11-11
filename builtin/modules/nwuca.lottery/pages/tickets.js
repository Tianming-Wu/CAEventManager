/* 模块自己的页签脚本：平台在活动后台启动后按 pages 声明依次载入，
 * 然后调用 MODULE.pages.<页签>.mount(ctx)。ctx 里是平台给的那几件东西。
 *
 * 这一段是「奖票」页：把可抽的票、保底与速度奖励算出来的结果画出来。
 * 数据来自 /api/admin/plan，口算在模块的 model.py 里。
 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S;

  // 模块脚本不共享平台的内部件：要什么自己取
  function $(sel) { return document.querySelector(sel); }
  function fail(e) { LT.toast(LT.esc((e && e.message) || "出错了"), true); }

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
    $("#planSummary").innerHTML = chips.join("");

    $("#planTable tbody").innerHTML = (p.rows || []).map(function (r) {
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

    $("#planNote").textContent = sum.finalized
      ? "已结算于 " + LT.fmtDateTime(S.config.finalized_at)
      : "预览，点击「结算」才会真正发票";
    $("#finalizeBtn").disabled = !!sum.finalized;
  }

    function loadPlan() {
    return LT.api("/api/admin/plan").then(function (d) {
      S.plan = d;
      renderPlan();
      // 平台自己也有依赖这份计划的按钮状态，通知它一声
      document.dispatchEvent(new Event("module-plan"));
    }).catch(fail);
  }

  window.MODULE.pages.tickets = {
    // 平台启动后调一次，给它那几件共用件与当前状态
    mount: function (page) {
      LT = page.LT;
      ctx = page;
      S = page.state || {};
    },
    // 平台每次拉到新状态就调一次；只重画自己这一段
    refresh: function (state, tab) {
      S = state || S;
      if (ctx && tab) ctx.tab = tab;
      if (ctx && ctx.tab === "tickets") renderPlan();
    },
    // 切到这个页签时调一次：重新算一遍再画
    show: function (tab) {
      if (ctx && tab) ctx.tab = tab;
      return loadPlan();
    },
  };
})();
