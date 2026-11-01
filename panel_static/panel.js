/* Master panel: pending approvals, account administration, password change. */
(function () {
  "use strict";

  var S = { me: null, accounts: [] };
  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };

  function isOwner() { return S.me && S.me.role === "owner"; }
  function isStaff() { return S.me && (S.me.role === "owner" || S.me.role === "superadmin"); }

  // superadmin manages admins; owner manages everyone below owner
  function canDecide(acc) {
    if (!isStaff()) return false;
    if (acc.role === "owner") return false;
    if (isOwner()) return true;
    return acc.role === "admin";
  }

  function tag(acc) {
    var roleClass = acc.role === "owner" ? "owner" : (acc.role === "superadmin" ? "superadmin" : "");
    return '<span class="tag ' + roleClass + '">' + PX.esc(acc.roleLabel) + "</span>";
  }

  function statusTag(acc) {
    var cls = acc.status === "active" ? "" : acc.status;
    return '<span class="tag ' + cls + '">' + PX.esc(acc.statusLabel) + "</span>";
  }

  function renderMe() {
    $("#who").innerHTML = "<b>" + PX.esc(S.me.name || S.me.username) + "</b>" +
      " · " + PX.esc(S.me.roleLabel);
    $("#accountsHint").textContent = isOwner()
      ? "站长可调整超级管理员与管理员。"
      : (isStaff() ? "超级管理员可审核与停用普通管理员。" : "你可以查看自己的账号信息。");
  }

  function renderPending() {
    var pending = S.accounts.filter(function (a) { return a.status === "pending"; });
    var panel = $("#pendingPanel");
    if (!isStaff()) {
      panel.style.display = "none";
      return;
    }
    panel.style.display = pending.length ? "block" : "none";
    $("#pendingTable tbody").innerHTML = pending.map(function (a) {
      return "<tr>" +
        "<td><b>" + PX.esc(a.username) + "</b></td>" +
        "<td>" + PX.esc(a.name) + "</td>" +
        '<td style="white-space:normal;max-width:320px">' + PX.esc(a.note || "—") + "</td>" +
        '<td class="mono">' + PX.when(a.createdAt) + "</td>" +
        '<td class="actions">' +
        '<button class="btn sm primary" data-decide="approve" data-id="' + a.id + '">通过</button> ' +
        '<button class="btn sm danger" data-decide="reject" data-id="' + a.id + '">拒绝</button>' +
        "</td></tr>";
    }).join("");
  }

  function renderAccounts() {
    $("#accountsTable tbody").innerHTML = S.accounts.map(function (a) {
      var btns = [];
      if (canDecide(a)) {
        if (a.status === "active") btns.push('<button class="btn sm danger" data-decide="disable" data-id="' + a.id + '">停用</button>');
        if (a.status === "disabled") btns.push('<button class="btn sm" data-decide="enable" data-id="' + a.id + '">启用</button>');
        if (a.status === "pending") btns.push('<button class="btn sm primary" data-decide="approve" data-id="' + a.id + '">通过</button>');
        if (a.status === "rejected") btns.push('<button class="btn sm" data-decide="enable" data-id="' + a.id + '">恢复</button>');
      }
      if (isOwner() && a.role !== "owner") {
        btns.push(a.role === "superadmin"
          ? '<button class="btn sm ghost" data-role="admin" data-id="' + a.id + '">降为管理员</button>'
          : '<button class="btn sm ghost" data-role="superadmin" data-id="' + a.id + '">升为超级管理员</button>');
      }
      return "<tr>" +
        "<td><b>" + PX.esc(a.username) + "</b>" + (a.id === S.me.id ? ' <span class="tag">我</span>' : "") + "</td>" +
        "<td>" + PX.esc(a.name) + "</td>" +
        "<td>" + tag(a) + "</td>" +
        "<td>" + statusTag(a) + "</td>" +
        '<td class="mono">' + PX.when(a.lastLogin) + "</td>" +
        '<td class="actions">' + (btns.join(" ") || "—") + "</td></tr>";
    }).join("");
  }

  function renderEvents(rows) {
    $("#eventsTable tbody").innerHTML = rows.map(function (e) {
      return "<tr>" +
        '<td class="mono" style="font-size:.78rem">' + PX.esc(e.id) + "</td>" +
        "<td>" + PX.esc(e.title || "—") + "</td>" +
        '<td class="mono">' + PX.when(e.createdAt) + "</td>" +
        '<td class="actions">' +
        '<a class="btn sm" href="' + PX.esc(e.url) + '" target="_blank" rel="noopener">公示</a> ' +
        '<a class="btn sm" href="' + PX.esc(e.screen) + '" target="_blank" rel="noopener">大屏</a> ' +
        '<a class="btn sm primary" href="' + PX.esc(e.admin) + '" target="_blank" rel="noopener">后台</a>' +
        "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="empty">还没有活动</td></tr>';
  }

  function renderAudit(rows) {
    if (!isStaff()) return;
    $("#auditPanel").style.display = "block";
    $("#auditTable tbody").innerHTML = rows.map(function (r) {
      return '<tr><td class="mono">' + PX.when(r.ts) + "</td>" +
        "<td>" + PX.esc(r.actor || "") + "</td>" +
        "<td>" + PX.esc(r.action) + "</td>" +
        "<td>" + PX.esc(r.target || "") + "</td>" +
        '<td style="white-space:normal">' + PX.esc(r.detail || "") + "</td></tr>";
    }).join("");
  }

  function loadEvents() {
    return PX.api("/events").then(function (d) { renderEvents(d.events); })
      .catch(function () {});
  }

  function load() {
    return PX.api("/accounts").then(function (d) {
      S.me = d.me;
      S.accounts = d.accounts;
      renderMe();
      renderPending();
      renderAccounts();
      if (isStaff()) {
        return PX.api("/audit").then(function (a) { renderAudit(a.audit); });
      }
    }).catch(function (e) {
      if (e.status === 401) location.href = "login";
      else PX.toast(PX.esc(e.message), true);
    });
  }

  function decide(id, action) {
    PX.api("/account/decide", { method: "POST", body: { id: id, action: action } })
      .then(function () {
        PX.toast("已" + ({ approve: "通过", reject: "拒绝", disable: "停用", enable: "启用" })[action]);
        return load();
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function changeRole(id, role) {
    PX.api("/account/role", { method: "POST", body: { id: id, role: role } })
      .then(function () { PX.toast("角色已更新"); return load(); })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  document.addEventListener("click", function (e) {
    var t = e.target;
    var dec = t.getAttribute && t.getAttribute("data-decide");
    if (dec) return decide(Number(t.getAttribute("data-id")), dec);
    var role = t.getAttribute && t.getAttribute("data-role");
    if (role) return changeRole(Number(t.getAttribute("data-id")), role);
  });

  $("#logoutBtn").addEventListener("click", function () {
    PX.api("/logout", { method: "POST" }).then(function () {
      location.href = "login";
    }).catch(function () { location.href = "login"; });
  });

  $("#pwBtn").addEventListener("click", function () {
    var err = $("#pwErr");
    err.textContent = "";
    PX.api("/password", {
      method: "POST",
      body: { old: $("#pwOld").value, new: $("#pwNew").value }
    }).then(function () {
      $("#pwOld").value = "";
      $("#pwNew").value = "";
      PX.toast("密码已修改");
    }).catch(function (e) { err.textContent = e.message; });
  });

  loadEvents();
  load();
})();
