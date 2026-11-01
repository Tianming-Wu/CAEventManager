/* Self check-in. One thumb, two fields, one button. */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var body = $("#ciBody");
  var state = { verified: false, enabled: true, count: 0, total: 0 };
  var busy = false;

  function renderBlocked(reason) {
    body.innerHTML =
      '<div class="ci-card ci-notice">' +
      "<h2>请扫描现场二维码</h2>" +
      (reason ? '<p class="note">' + LT.esc(reason) + "</p>" : "") +
      "</div>";
  }

  function ticketChips(tickets) {
    if (!tickets || !tickets.length) return "";
    return tickets.map(function (t) {
      return '<span class="tkt' + (t.kind === "bonus" ? " bonus" : "") + '">' +
        LT.code(t.code) + "</span>";
    }).join(" ");
  }

  function renderForm() {
    body.innerHTML =
      '<form class="ci-card" id="ciForm" autocomplete="off">' +
      '<label class="ci-label" for="sid">学号</label>' +
      '<input id="sid" class="ci-input" type="text" inputmode="numeric" maxlength="20" ' +
      'autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="2026123456">' +
      '<label class="ci-label" for="name">姓名</label>' +
      '<input id="name" class="ci-input" type="text" autocomplete="off" ' +
      'spellcheck="false" placeholder="张三">' +
      '<div class="ci-err" id="ciErr"></div>' +
      '<button class="btn primary ci-btn" type="submit" id="ciGo">签到</button>' +
      (state.total ? '<div class="ci-count">已签到 ' + state.count + " / " + state.total + " 人</div>" : "") +
      "</form>";
    $("#ciForm").addEventListener("submit", submit);
    $("#sid").focus();
  }

  function renderDone(data, already) {
    if ((data.role || "participant") !== "participant") {
      body.innerHTML =
        '<div class="ci-card ci-done">' +
        "<h2>" + (already ? "已记录" : "已记录") + "</h2>" +
        '<div class="ci-name">' + LT.esc(data.name) + "</div>" +
        '<div class="ci-meta">' + LT.esc(data.sid) +
        (data.grp ? " · " + LT.esc(data.grp) : "") + "</div>" +
        '<div class="ci-count" id="ciAuto">5 秒后跳转奖票公示</div>' +
        "</div>";
      redirectLater();
      return;
    }
    var marks = data.marks || {};
    var steps = [["checkin", "签到"], ["task1", "任务一"], ["task2", "任务二"]]
      .map(function (kv) {
        return '<span class="step ' + (marks[kv[0]] ? "done" : "") + '">' + kv[1] + "</span>";
      }).join("");

    body.innerHTML =
      '<div class="ci-card ci-done">' +
      "<h2>" + (already ? "已签到" : "签到成功") + "</h2>" +
      '<div class="ci-name">' + LT.esc(data.name) + "</div>" +
      '<div class="ci-meta">' + LT.esc(data.sid) + (data.grp ? " · " + LT.esc(data.grp) : "") + "</div>" +
      (data.position ? '<div class="ci-pos">第 <b>' + data.position + "</b> 位到场</div>" : "") +
      '<div class="ci-tickets-label">奖票</div>' +
      '<div class="ci-tickets">' + (ticketChips(data.tickets) || '<span class="note">暂无</span>') + "</div>" +
      '<div class="ci-steps">' + steps + "</div>" +
      '<div class="ci-count" id="ciAuto">5 秒后跳转奖票公示</div>' +
      "</div>";
    redirectLater();
  }

  function redirectLater() {
    var left = 5;
    var timer = setInterval(function () {
      left -= 1;
      var el = $("#ciAuto");
      if (left <= 0) {
        clearInterval(timer);
        location.href = LT.PREFIX + "/";
      } else if (el) {
        el.textContent = left + " 秒后跳转奖票公示";
      }
    }, 1000);
  }

  function submit(e) {
    e.preventDefault();
    if (busy) return;
    var sid = $("#sid").value.trim();
    var name = $("#name").value.trim();
    var err = $("#ciErr");
    if (!sid || !name) {
      err.textContent = "学号和姓名都要填";
      return;
    }
    busy = true;
    $("#ciGo").disabled = true;
    err.textContent = "";
    LT.api("/api/checkin/submit", { method: "POST", body: { sid: sid, name: name } })
      .then(function (d) { renderDone(d, d.already); })
      .catch(function (ex) {
        busy = false;
        var btn = $("#ciGo");
        if (btn) btn.disabled = false;
        err.textContent = ex.message || "签到失败";
      });
  }

  function load() {
    return fetch(LT.PREFIX + "/api/checkin/status", { cache: "no-store", credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.verified = !!d.verified;
        state.enabled = d.enabled !== false;
        state.count = d.checkin_count || 0;
        state.total = d.total || 0;
        if (d.title) {
          $("#ciTitle").textContent = d.title;
          document.title = "签到 · " + d.title;
        }
        if (!state.enabled) return renderBlocked("本活动未开启自助签到");
        if (!state.verified) return renderBlocked("二维码已过期");
        renderForm();
      })
      .catch(function () { renderBlocked("网络异常"); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
