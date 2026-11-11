/* 模块自己的共用件：平台在页签脚本之前载入这一份，几页都要用的画法与请求放这里。
 * 各页 mount 时调 MODULE.lib.bind() 把平台给的那几件东西递进来，之后直接用。
 */
window.MODULE = window.MODULE || { pages: {} };

window.MODULE.lib = (function () {
  var LT = null;
  var S = null;
  var reload = null;

  var KINDS = ["checkin", "task1", "task2"];
  var PREV = { checkin: null, task1: "checkin", task2: "task1" };
  var ROLE_TAG = { staff: "工作人员", volunteer: "志愿者" };

  function bind(page) {
    if (!page) return;
    LT = page.LT || LT;
    S = page.state || S;
    reload = page.reload || reload;
    ready();
  }

  /* 状态还没拉到的时候先给个空壳：脚本比第一次状态快的时候会先画一轮 */
  function ready() {
    if (!S) return null;
    if (!S.students) S.students = [];
    if (!S.config) S.config = {};
    if (!S.features) S.features = {};
    if (!S.prizes) S.prizes = [];
    if (!S.draws) S.draws = [];
    if (!S.screenPacks) S.screenPacks = [];
    if (typeof S.publicBaseUrl !== "string") S.publicBaseUrl = "";
    return S;
  }

  function state() { return S; }

  /* 清单按 id 建个索引：清单换了引用就重建，各页不用自己记 */
  var INDEX_OF = null, INDEX = {};
  function byId(state) {
    var list = ((state || S || {}).students) || [];
    if (list !== INDEX_OF) {
      INDEX_OF = list;
      INDEX = {};
      list.forEach(function (s) { INDEX[s.id] = s; });
    }
    return INDEX;
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function ok(html) { LT.toast(html, false); }

  /* 传字符串就是一句话，传错误对象就是它的 message */
  function fail(err) {
    var text = typeof err === "string" ? err : (err && err.message) || "出错了";
    LT.toast(LT.esc(text), true, 4200);
  }

  /* 被另一个操作员的写拒了不是"出错了"：说清为什么会拒，再按服务器真正的状态重画 */
  function bad(err) {
    var conflict = LT.conflictMsg(err);
    if (conflict) {
      LT.toast(conflict, true, 5200);
      if (reload) {
        var p = reload();
        if (p && p.catch) p.catch(function () {});
      }
      return;
    }
    fail(err);
  }

  function reloadState() { return reload ? reload() : null; }

  function kindLabel(k) { return LT.KIND_LABEL[k] || k; }

  function isParticipant(stu) { return (stu.role || "participant") === "participant"; }

  function roleLabel(stu) {
    return ROLE_TAG[stu.role] || "参加人员";
  }

  function canMark(stu, kind) {
    if (stu.marks[kind]) return false;
    if (S.config.finalized) return false;
    var need = PREV[kind];
    return !need || !!stu.marks[need];
  }

  function ticketChips(stu) {
    var list = (stu.tickets || []).slice().sort(function (a, b) { return a.code - b.code; });
    return list.map(function (t) {
      return '<span class="tkt' + (t.kind === "bonus" ? " bonus" : "") + '" title="' +
        (LT.KIND_LABEL[t.kind] || t.kind) + '">' + LT.code(t.code) + "</span>";
    }).join(" ");
  }

  function roleTag(stu) {
    var label = ROLE_TAG[stu.role];
    return label ? ' <span class="role-tag">' + label + "</span>" : "";
  }

  function timeCell(ts) {
    if (!ts) return '<td class="num" style="color:var(--dim)">—</td>';
    return '<td class="num" title="' + LT.fmtDateTime(ts) + '">' + LT.fmtTime(ts) + "</td>";
  }

  function stepBadges(stu) {
    if (!isParticipant(stu)) {
      return stu.marks.checkin
        ? '<span class="step done">到场 ✓</span>'
        : '<span class="step next">到场</span>';
    }
    return KINDS.map(function (k) {
      if (stu.marks[k]) {
        return '<span class="step done">' + kindLabel(k) + " ✓</span>";
      }
      if (stu.next === k && !S.config.finalized) {
        return '<span class="step next">' + kindLabel(k) + "</span>";
      }
      return '<span class="step">' + kindLabel(k) + "</span>";
    }).join("");
  }

  function nextLabel(stu) {
    if (S.config.finalized) return '<span class="next none">已结算</span>';
    if (!stu.next) return '<span class="next none">已完成</span>';
    return '<span class="next">下一步 ' + kindLabel(stu.next) + "</span>";
  }

  function actionButtons(stu, compact) {
    if (!isParticipant(stu)) {
      var shown = !!stu.marks.checkin;
      var html = '<button class="btn sm ' + (shown ? "" : "primary") + '" data-mark="checkin" data-id="' +
        stu.id + '"' + (shown ? " disabled" : "") + ">" + (shown ? "到场 ✓" : "到场") + "</button>";
      if (shown) {
        html += ' <button class="btn sm danger" data-undo="checkin" data-id="' + stu.id + '"' +
          (S.unlocked ? "" : " disabled") + ">撤销</button>";
      }
      return html;
    }
    var btns = [];
    KINDS.forEach(function (k, idx) {
      var can = canMark(stu, k);
      if (stu.marks[k]) {
        btns.push('<button class="btn sm danger" data-undo="' + k + '" data-id="' + stu.id + '"' +
          (S.unlocked ? "" : " disabled title=\"先打开解锁修改\"") + ">撤 " + kindLabel(k) + "…</button>");
      } else {
        btns.push('<button class="btn sm ' + (stu.next === k ? "green" : "") + '" data-mark="' + k +
          '" data-id="' + stu.id + '"' + (can ? "" : " disabled") + ">" +
          (compact ? (idx + 1) : "") + kindLabel(k) + "</button>");
      }
    });
    return btns.join(" ");
  }

  /* 模块在 module.json 里声明了「哪份清单、哪个字段是姓名」，搜索算法留在平台 */
  function searchSpec() {
    var perms = (S && S.features) || {};
    return perms.search || null;
  }

  /* 自动模式下奖票池跟着到场人数走 */
  function poolSize(state, st) {
    var cfg = (state && state.config) || {};
    if ((cfg.pool_mode || "auto") === "fixed") return Number(cfg.pool_size) || 0;
    return st.checkin * 3 + (Number(cfg.bonus_constant) || 0);
  }

  function stats(state) {
    state = state || S;
    var list = (state && state.students) || [];
    var cfg = (state && state.config) || {};
    var st = {
      total: list.length, participants: 0, staff: 0, staff_present: 0,
      checkin: 0, task1: 0, task2: 0,
      live_tickets: 0, max_code: 0, finalized: !!cfg.finalized
    };
    list.forEach(function (s) {
      if (isParticipant(s)) {
        st.participants++;
        KINDS.forEach(function (k) { if (s.marks[k]) st[k]++; });
      } else {
        st.staff++;
        if (s.marks.checkin) st.staff_present++;
      }
      (s.tickets || []).forEach(function (t) {
        st.live_tickets++;
        if (t.code > st.max_code) st.max_code = t.code;
      });
    });
    st.guaranteed = st.checkin + st.task1 + st.task2;
    st.pool_mode = cfg.pool_mode || "auto";
    st.bonus_constant = Number(cfg.bonus_constant) || 0;
    st.pool_size = poolSize(state, st);
    st.bonus_pool = Math.max(0, st.pool_size - st.guaranteed);
    st.remaining = st.pool_size - st.live_tickets;
    return st;
  }

  /* 顶栏的状态条由模块填：平台只管那块地方，不认识上面写的是什么 */
  function headHtml(state) {
    state = state || S;
    if (!state) return "";
    var st = stats(state);
    var html = [
      '<span class="chip green"><b>' + st.checkin + "</b>/" + st.participants + " 已签到</span>",
      '<span class="chip blue"><b>' + st.task1 + "</b> 任务一</span>",
      '<span class="chip blue"><b>' + st.task2 + "</b> 任务二</span>",
      '<span class="chip gold"><b>' + st.live_tickets + "</b>/" + st.pool_size + " 奖票已发</span>"
    ];
    if (st.staff) {
      html.push('<span class="chip">工作人员 <b>' + st.staff_present + "</b>/" + st.staff + "</span>");
    }
    if (st.finalized) html.push('<span class="chip gold live">已结算</span>');
    if (state && state.unlocked) {
      html.push('<span class="chip" style="border-color:#6b2020;color:#ffb4b4">修改已解锁</span>');
    }
    return html.join("");
  }

  window.MODULE.head = {
    render: function (state) { return headHtml(state); }
  };

  /* 已经结算过就直接落到抽奖页：接着要做的多半是抽奖 */
  window.MODULE.landing = function (state) {
    return state && state.config && state.config.finalized ? "draw" : "";
  };

  /* 解锁之后能动什么：平台只认识那个开关，这句话由模块说 */
  window.MODULE.unlockNote = "解锁后可以：撤销标记、修改完成时间、删除学生、作废抽奖、重置奖票。";

  return {
    bind: bind, ready: ready, state: state, reloadState: reloadState, byId: byId,
    $: $, $$: $$, ok: ok, fail: fail, bad: bad,
    KINDS: KINDS, PREV: PREV, ROLE_TAG: ROLE_TAG,
    kindLabel: kindLabel, isParticipant: isParticipant, roleLabel: roleLabel,
    canMark: canMark, ticketChips: ticketChips, roleTag: roleTag, timeCell: timeCell,
    stepBadges: stepBadges, nextLabel: nextLabel, actionButtons: actionButtons,
    searchSpec: searchSpec, stats: stats, poolSize: poolSize
  };
})();
