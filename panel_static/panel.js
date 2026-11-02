/* Master panel shell: navigation, pages, and the account menu.
 *
 * Everything that is visible but not backed by a real endpoint is marked
 * UNWIRED(<feature>) so a later pass can grep for it:
 *
 *     grep -rn "UNWIRED" panel_static/ tpl/ app.py
 *
 * The marker means "this is a placeholder on purpose, do not assume it works".
 */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) {
    return Array.prototype.slice.call((r || document).querySelectorAll(s));
  };

  var ROLE_RANK = { admin: 0, superadmin: 1, owner: 2 };

  // min: the cheapest role that may open the page
  var ROUTES = {
    "overview": { min: "admin" },
    "events": { min: "admin" },
    "templates": { min: "admin" },
    "users": { min: "superadmin" },
    "all-events": { min: "superadmin" },
    "approval-users": { min: "superadmin", group: "approvals" },
    "approval-events": { min: "superadmin", group: "approvals" },
    "new-event": { min: "admin" },
    "server": { min: "owner" },
    "audit": { min: "owner" },
    "account": { min: "admin" },
    "appearance": { min: "admin" }
  };
  // old links stay working
  var ALIAS = { "approvals": "approval-users" };
  var FALLBACK_ROUTE = "overview";

  var S = {
    me: null,
    accounts: [],
    events: [],
    audit: [],
    host: null,
    invites: [],          // UNWIRED(invites): 邀请还没有后端，永远是空数组
    route: null,
    hostTimer: null,
    userFilter: "",
    openGroups: {},
    denied: null,
    kinds: [],
    kindId: null,
    kindFilter: "",
    invited: []
  };

  var RAIL_QUERY = "(max-width: 1100px)";
  var AVATAR_HINT = "PNG / JPEG / WebP，不超过 512 KB";
  var ICON_PLUS = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<path d="M10 5.2v9.6M5.2 10h9.6"/></svg>';
  var ICON_PENCIL = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<path d="M4.2 15.8h3.1l7.5-7.5a1.6 1.6 0 0 0-2.3-2.3l-7.5 7.5v2.3Z"/>' +
    '<path d="M11.5 7l2.3 2.3"/></svg>';

  /* ---------------------------------------------------------------- helpers */
  function rank(role) {
    return ROLE_RANK[role] === undefined ? -1 : ROLE_RANK[role];
  }
  function myRank() { return S.me ? rank(S.me.role) : -1; }
  function allowed(route) {
    var r = ROUTES[route];
    return !!r && myRank() >= rank(r.min);
  }
  function isOwner() { return !!S.me && S.me.role === "owner"; }
  function canAudit() { return allowed("audit"); }
  function canReview() { return allowed("approval-users"); }

  // The login name is for signing in; what the console shows is the display
  // name, so showing someone the interface does not hand them the account name.
  function displayName(me) {
    if (!me) return "";
    return me.name || me.roleLabel || "";
  }
  function initialOf(me) {
    var s = displayName(me) || "?";
    return s.trim().charAt(0);
  }

  // The avatar endpoint is session-guarded, so a plain <img> is enough - the
  // cookie rides along.  ?v= busts the cache when the picture changes.
  function avatarUrl(acc) {
    if (!acc || !acc.avatar) return "";
    // a stored link points straight at its host; a stored file comes from us
    if (/^https?:\/\//i.test(acc.avatar)) return acc.avatar;
    return PX.PREFIX + "/api/panel/avatar/" + acc.id +
      "?v=" + Math.round(acc.avatarAt || 0);
  }

  function paintAvatar(el, acc, fallbackText) {
    if (!el) return;
    var url = avatarUrl(acc);
    if (url) {
      el.innerHTML = '<img alt="" src="' + PX.esc(url) + '">';
    } else {
      el.textContent = fallbackText;
    }
  }

  // The panel reports which side of an event this account is on.  The two lists
  // are mutually exclusive: an event you created is not one you "joined".
  function ownedEvents() {
    return S.events.filter(function (e) { return e.role === "owner"; });
  }
  function joinedEvents() {
    return S.events.filter(function (e) { return e.role === "member"; });
  }
  function myEvents() { return ownedEvents().concat(joinedEvents()); }
  function pendingAccounts() {
    return S.accounts.filter(function (a) { return a.status === "pending"; });
  }

  function pct(n) { return (n === null || n === undefined) ? "–" : Math.round(n) + "%"; }
  function eventTitle(e) { return e.title || "未命名活动"; }

  // A long token identifies the card, it is not something to retype: keep the
  // ends, star the middle, and put the real value one click away.
  function shortToken(id) {
    if (!id) return "";
    if (id.length <= 16) return id;
    return id.slice(0, 4) + "****" + id.slice(-5);
  }

  var ICON_COPY = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<rect x="6.6" y="6.6" width="9.2" height="9.2" rx="1.8"/>' +
    '<path d="M13.4 6.6V5.2a1.6 1.6 0 0 0-1.6-1.6H5.2a1.6 1.6 0 0 0-1.6 1.6v6.6a1.6 1.6 0 0 0 1.6 1.6h1.4"/></svg>';
  var ICON_DOTS = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<circle cx="10" cy="4.3" r="1.35" fill="currentColor" stroke="none"/>' +
    '<circle cx="10" cy="10" r="1.35" fill="currentColor" stroke="none"/>' +
    '<circle cx="10" cy="15.7" r="1.35" fill="currentColor" stroke="none"/></svg>';

  function eventCard(e) {
    var meta = [];
    if (e.createdAt) meta.push("建于 " + PX.when(e.createdAt));
    return '<div class="event-card">' +
      '<div class="event-card-head">' +
      '<div class="event-titlewrap">' +
      '<div class="event-title">' + PX.esc(eventTitle(e)) + "</div>" +
      '<div class="event-id">#' + PX.esc(String(e.seq)) + "</div></div>" +
      '<button class="card-menu-btn" type="button" data-menu="' + PX.esc(e.id) + '" ' +
      'aria-haspopup="menu" aria-label="更多操作">' + ICON_DOTS + "</button>" +
      "</div>" +
      '<div class="event-token"><span class="token-label">token</span>' +
      '<code title="' + PX.esc(e.id) + '">' +
      PX.esc(shortToken(e.id)) + "</code>" +
      '<button class="copy-btn" type="button" data-copy="' + PX.esc(e.id) + '" ' +
      'aria-label="复制活动 token">' + ICON_COPY + "</button></div>" +
      '<div class="event-meta">' + PX.esc(meta.join(" · ")) + "</div>" +
      '<div class="event-actions">' +
      '<a class="btn primary" href="' + PX.esc(e.admin) + '" target="_blank" ' +
      'rel="noopener">管理面板</a></div></div>';
  }

  function emptyRow(cols, text) {
    return '<tr><td colspan="' + cols + '" class="empty">' + PX.esc(text) + "</td></tr>";
  }

  function roleTag(a) {
    var cls = a.role === "owner" ? "owner" : (a.role === "superadmin" ? "superadmin" : "");
    return '<span class="tag ' + cls + '">' + PX.esc(a.roleLabel) + "</span>";
  }
  function statusTag(a) {
    var cls = a.status === "active" ? "" : a.status;
    return '<span class="tag ' + cls + '">' + PX.esc(a.statusLabel) + "</span>";
  }

  /* ---------------------------------------------------------------- overlay */
  var overlayKind = null;
  var overlayOpener = null;

  function openOverlay(kind, title, html) {
    overlayKind = kind;
    overlayOpener = document.activeElement;
    $("#overlayTitle").textContent = title;
    $("#overlayBody").innerHTML = html;
    $("#overlay").hidden = false;
    document.body.style.overflow = "hidden";
    var close = $("#overlayClose");
    if (close) close.focus();
  }

  function closeOverlay() {
    if ($("#overlay").hidden) return;
    $("#overlay").hidden = true;
    $("#overlayBody").innerHTML = "";
    document.body.style.overflow = "";
    overlayKind = null;
    if (overlayOpener && overlayOpener.focus) overlayOpener.focus();
    overlayOpener = null;
  }

  $("#overlayClose").addEventListener("click", closeOverlay);
  $("#overlay").addEventListener("click", function (e) {
    if (e.target === $("#overlay")) closeOverlay();   // click the scrim
  });

  /* ------------------------------------------------------------ card menu */
  var cardMenuEl = null;

  function closeCardMenu() {
    if (cardMenuEl) {
      cardMenuEl.remove();
      cardMenuEl = null;
    }
  }

  function openCardMenu(anchor, id) {
    closeCardMenu();
    var e = S.events.filter(function (x) { return x.id === id; })[0];
    if (!e) return;
    var menu = document.createElement("div");
    menu.className = "card-menu";
    menu.setAttribute("role", "menu");
    // Anything already on the card face - the token's copy button, the admin
    // link - is not repeated here; this menu is for the less common actions.
    menu.innerHTML =
      (e.role === "owner"
        ? '<button type="button" role="menuitem" data-rename="' + e.seq + '">' +
          "重命名活动</button>"
        : "") +
      '<button type="button" role="menuitem" data-copy="' + PX.esc(e.url) + '">' +
      "复制访问链接</button>" +
      // UNWIRED(templates): saving a template has no backend yet
      '<button type="button" role="menuitem" disabled ' +
      'title="模板功能还没有接入">' +
      "保存为模板</button>" +
      '<div class="menu-sep"></div>' +
      // UNWIRED(event-settings): there is no settings page yet
      '<button type="button" role="menuitem" disabled ' +
      'title="活动设置还没有接入">' +
      "活动设置</button>";
    document.body.appendChild(menu);

    var rect = anchor.getBoundingClientRect();
    var left = Math.min(Math.max(8, rect.right - menu.offsetWidth),
                        window.innerWidth - menu.offsetWidth - 8);
    var top = rect.bottom + 6;
    if (top + menu.offsetHeight > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menu.offsetHeight - 6);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    cardMenuEl = menu;
    var first = menu.querySelector("button:not([disabled])");
    if (first) first.focus();
  }

  function openRename(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    if (!e) return;
    closeCardMenu();
    openOverlay("rename", "重命名活动",
      '<div class="field"><label for="renameTitle">活动名称</label>' +
      '<input id="renameTitle" type="text" maxlength="60" value="' +
      PX.esc(e.title) + '"></div>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" data-rename-save="' + e.seq + '">' +
      "保存</button></div>");
    var box = $("#renameTitle");
    if (box) {
      box.focus();
      box.select();
    }
  }

  function saveRename(seq) {
    var box = $("#renameTitle");
    var title = box ? box.value.trim() : "";
    PX.api("/event/title", { method: "POST", body: { seq: Number(seq), title: title } })
      .then(function () {
        closeOverlay();
        PX.toast("已重命名");
        return reloadAll();
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function copyText(text, button) {
    var done = function () {
      PX.toast("已复制");
      if (button) {
        button.classList.add("copied");
        setTimeout(function () { button.classList.remove("copied"); }, 1200);
      }
    };
    var fallback = function () {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy"); } catch (e) { /* nothing else to try */ }
      area.remove();
      done();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else {
      fallback();
    }
  }

  /* -------------------------------------------------------- role visibility */
  function applyRoleVisibility() {
    $$("[data-min-role]").forEach(function (el) {
      el.hidden = myRank() < rank(el.getAttribute("data-min-role"));
    });
    $("#hostStats").hidden = !isOwner() || !S.host;
  }

  // The rail shows icons only, so every entry needs a hover title.
  function labelNav() {
    $$(".nav-item").forEach(function (a) {
      var t = a.querySelector(".nav-text");
      if (t && !a.getAttribute("title")) a.setAttribute("title", t.textContent.trim());
    });
    var acct = $(".nav-account");
    if (acct && !acct.getAttribute("title")) acct.setAttribute("title", "账户信息");
  }

  // One place shows who you are: the card at the bottom of the sidebar.  The
  // title bar is deliberately empty on that side, free for whatever comes next.
  function renderIdentity() {
    if (!S.me) return;
    $("#sideName").textContent = displayName(S.me);
    paintAvatar($("#sideAvatar"), S.me, initialOf(S.me));
    var tag = $("#sideRole");
    tag.textContent = S.me.roleLabel;
    tag.className = "tag " + (S.me.role === "owner" ? "owner"
                              : (S.me.role === "superadmin" ? "superadmin" : ""));
    tag.hidden = false;
  }

  /* -------------------------------------------------------------- nav groups */
  function groupEl(name) {
    return $('.nav-group[data-group="' + name + '"]');
  }

  function setGroup(name, open) {
    var g = groupEl(name);
    if (!g) return;
    S.openGroups[name] = !!open;
    // the list is clipped, not hidden, so the height can animate; inert keeps
    // the clipped links out of the tab order while it is shut
    g.querySelector(".nav-sub").inert = !open;
    g.querySelector(".nav-group-head").setAttribute("aria-expanded", open ? "true" : "false");
    g.classList.toggle("open", !!open);
  }

  function toggleGroup(name) {
    setGroup(name, !S.openGroups[name]);
  }

  // Clicking the header of a collapsible item either reveals its children
  // in place, or - when the rail is too narrow to show them - opens the pane
  // first, so the children are never unreachable.
  function onGroupHead(name) {
    var rail = window.matchMedia(RAIL_QUERY).matches;
    if (rail && !$("#shell").classList.contains("nav-open")) {
      openPane();
      setGroup(name, true);
      return;
    }
    toggleGroup(name);
  }

  function syncGroups() {
    $$(".nav-group").forEach(function (g) {
      var name = g.getAttribute("data-group");
      setGroup(name, !!S.openGroups[name]);
    });
  }

  /* -------------------------------------------------------------- pane/scrim */
  function openPane() {
    $("#shell").classList.add("nav-open");
    $("#scrim").hidden = false;
    $("#menuBtn").setAttribute("aria-expanded", "true");
  }
  function closePane() {
    $("#shell").classList.remove("nav-open");
    $("#scrim").hidden = true;
    $("#menuBtn").setAttribute("aria-expanded", "false");
  }

  /* ----------------------------------------------------------------- router */
  function routeFromHash() {
    var h = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
    if (ALIAS[h]) h = ALIAS[h];
    if (ROUTES[h]) {
      S.kindId = null;
      return h;
    }
    var cut = h.indexOf("/");
    if (cut > 0) {
      var head = h.slice(0, cut);
      var tail = decodeURIComponent(h.slice(cut + 1)).replace(/\/$/, "");
      if (head === "new-event" && ROUTES[head]) {
        S.kindId = tail || null;
        return head;
      }
    }
    return null;
  }

  function go(route, replace) {
    var target = "#/" + route;
    if (location.hash === target) {
      onRoute();
      return;
    }
    if (replace) location.replace(target);
    else location.hash = target;
  }

  function showDenied(route) {
    S.route = null;
    S.denied = route;
    $$(".page").forEach(function (p) { p.hidden = true; });
    $("#denied").hidden = false;
    $("#siteFoot").hidden = true;
    $$("[data-nav]").forEach(function (a) { a.removeAttribute("aria-current"); });
    $$(".nav-group").forEach(function (g) { g.classList.remove("has-current"); });
    closePane();
    var el = $("#content");
    if (el) el.scrollTop = 0;
  }

  function onRoute() {
    var wanted = routeFromHash() || FALLBACK_ROUTE;
    if (!allowed(wanted)) {
      // Reachable by typing the hash; say so instead of silently moving them.
      showDenied(wanted);
      return;
    }
    S.route = wanted;
    S.denied = null;
    $("#denied").hidden = true;
    $("#siteFoot").hidden = false;

    // a child route keeps its group expanded
    var parent = ROUTES[wanted].group;
    if (parent) S.openGroups[parent] = true;
    syncGroups();

    $$(".page").forEach(function (p) {
      p.hidden = p.getAttribute("data-page") !== wanted;
    });
    $$("[data-nav]").forEach(function (a) {
      if (a.getAttribute("data-nav") === wanted) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    $$(".nav-group").forEach(function (g) {
      var on = !!ROUTES[wanted] && ROUTES[wanted].group === g.getAttribute("data-group");
      g.classList.toggle("has-current", on);
    });

    closePane();
    $("#content").scrollTop = 0;
    draw();
    if (wanted === "server" || wanted === "overview") refreshHost();
    if (wanted === "audit" && canAudit() && !S.audit.length) loadAudit().then(draw);
  }

  /* ------------------------------------------------------------------ pages */
  function draw() {
    switch (S.route) {
      case "overview": return drawOverview();
      case "events": return drawEvents();
      case "templates": return drawTemplates();
      case "new-event": return drawNewEvent();
      case "users": return drawUsers();
      case "all-events": return drawAllEvents();
      case "approval-users": return drawApprovalUsers();
      case "approval-events": return drawApprovalEvents();
      case "server": return drawServer();
      case "audit": return drawAudit();
      case "account": return drawAccount();
      case "appearance": return drawAppearance();
    }
  }

  function drawOverview() {
    var mine = myEvents();
    var pending = pendingAccounts();

    $("#overviewSub").textContent = "欢迎回来，" +
      ((S.me && (S.me.name || S.me.username)) || "");

    $("#statEvents").textContent = mine.length;
    $("#statAccounts").textContent = S.accounts.length || "–";
    $("#statInvites").textContent = S.invites.length;

    var showPending = canReview();
    $("#statPendingBox").hidden = !showPending;
    $("#statPending").textContent = pending.length;

    $("#overviewEvents").innerHTML = mine.length
      ? mine.slice(0, 4).map(function (e) { return eventCard(e); }).join("")
      : '<div class="empty">还没有活动。</div>';

    var card = $("#overviewAuditCard");
    if (canAudit() && S.audit.length) {
      card.hidden = false;
      $("#overviewAudit").innerHTML = S.audit.slice(0, 6).map(function (r) {
        return '<li><span class="t">' + PX.when(r.ts) + "</span>" +
          '<span class="w">' + PX.esc(r.actor || "system") + " · " +
          PX.esc(r.action) + (r.target ? " · " + PX.esc(r.target) : "") + "</span></li>";
      }).join("");
    } else {
      card.hidden = true;
    }
  }

  function drawEvents() {
    // UNWIRED(invites): 收到邀请后这里置顶显示；现在没有后端，永远走空分支。
    var stack = $("#inviteStack");
    if (S.invites.length) {
      stack.hidden = false;
      $("#inviteList").innerHTML = S.invites.map(function (e) {
        return eventCard(e, '<div class="event-meta">邀请你加入</div>');
      }).join("");
    } else {
      stack.hidden = true;
    }

    var owned = ownedEvents();
    $("#ownedEvents").innerHTML = owned.length
      ? owned.map(function (e) { return eventCard(e); }).join("")
      : '<div class="empty">还没有创建过活动</div>';

    var joined = joinedEvents();
    $("#joinedEvents").innerHTML = joined.length
      ? joined.map(function (e) { return eventCard(e); }).join("")
      : '<div class="empty">还没有参与别人创建的活动</div>';
  }

  function drawTemplates() {
    // UNWIRED(templates): 模板是计划中的功能，这里只列出打算做成可配置的项目。
    var planned = [
      ["任务链是否强制", "不完成任务链就抽不了奖，还是只做提示。"],
      ["是否允许重复中奖", "同一个人能否中多个等级的奖。"],
      ["奖票池模式", "按到场人数自动伸缩，还是固定数量。"],
      ["特等奖范围", "只算参加人员，还是把工作人员与志愿者也算进来。"]
    ];
    $("#templateTiles").innerHTML = planned.map(function (p) {
      return '<div class="tile"><b>' + PX.esc(p[0]) + "</b>" + PX.esc(p[1]) +
        '<span class="tag">未接入</span></div>';
    }).join("");
  }

  function loadKinds() {
    if (S.kinds.length) return Promise.resolve();
    return PX.api("/templates").then(function (d) { S.kinds = d.kinds || []; })
      .catch(function () { S.kinds = []; });
  }

  function kindById(id) {
    return S.kinds.filter(function (k) { return k.id === id; })[0];
  }

  function drawNewEvent() {
    if (!S.kinds.length) {
      // a direct link carries the kind in the hash, so the list may not be here
      // yet; load it and draw again
      loadKinds().then(drawNewEvent);
      return;
    }
    var kind = S.kindId ? kindById(S.kindId) : null;
    if (S.kindId && !kind) {
      go("new-event", true);      // unknown kind: back to the picker
      return;
    }
    $("#kindPicker").hidden = !!kind;
    $("#kindForm").hidden = !kind;
    if (kind) drawKindForm(kind);
    else $("#kindGrid").innerHTML = renderKindTiles();
  }

  function renderKindTiles() {
    var q = S.kindFilter.trim().toLowerCase();
    var rows = S.kinds.filter(function (k) {
      if (!q) return true;
      return (k.name + " " + k.id + " " + (k.description || ""))
        .toLowerCase().indexOf(q) >= 0;
    });
    if (!rows.length) {
      return '<div class="empty">' + (S.kinds.length
        ? "\u6ca1\u6709\u5339\u914d\u7684\u6d3b\u52a8\u5f62\u5f0f"
        : "\u8fd8\u6ca1\u6709\u53ef\u7528\u7684\u6d3b\u52a8\u5f62\u5f0f") + "</div>";
    }
    return rows.map(function (k) {
      var meta = [(k.builtin ? "\u5185\u7f6e" : "\u7528\u6237\u5bfc\u5165"),
                  "v" + k.version, k.author].filter(Boolean).join(" \u00b7 ");
      return '<button class="kind-tile' + (k.ok ? "" : " is-off") + '" type="button" ' +
        (k.ok ? 'data-kind="' + PX.esc(k.id) + '"' : "disabled") + ">" +
        '<span class="kind-name">' + PX.esc(k.name) + "</span>" +
        '<span class="kind-meta">' + PX.esc(meta) + "</span>" +
        (k.description ? '<span class="kind-desc">' + PX.esc(k.description) + "</span>" : "") +
        (k.ok ? "" : '<span class="tag disabled">' + PX.esc(k.reason || "\u4e0d\u53ef\u7528") + "</span>") +
        "</button>";
    }).join("");
  }

  function drawKindForm(kind) {
    $("#kindFormTitle").textContent = kind.name;
    $("#kindFormSub").textContent = [kind.id, "v" + kind.version,
                                     kind.builtin ? "\u5185\u7f6e" : "\u7528\u6237\u5bfc\u5165",
                                     kind.author, kind.homepage]
      .filter(Boolean).join(" \u00b7 ");
    $("#kindFields").innerHTML = kind.fields.map(function (f) {
      var id = "kf_" + f.key;
      var when = f.visibleWhen || f.visible_when;
      var wrapAttr = when
        ? ' data-show-when="' + PX.esc(when.key) + '" data-show-is="' +
          PX.esc(String(when.equals)) + '"'
        : "";
      var attr = ' data-kind-field="' + PX.esc(f.key) + '"';
      var head = '<label for="' + id + '">' + PX.esc(f.label) +
        (f.required ? ' <span class="req">\u5fc5\u586b</span>' : "") + "</label>";
      var help = f.help ? '<p class="hint">' + PX.esc(f.help) + "</p>" : "";
      var box = function (inner) {
        return '<div class="field kind-field"' + wrapAttr + ">" + inner + "</div>";
      };
      if (f.type === "bool") {
        return '<div class="field kind-field"' + wrapAttr + '><label class="check">' +
          '<input id="' + id + '" type="checkbox"' + attr +
          (f.default ? " checked" : "") + "><span>" + PX.esc(f.label) + "</span></label>" +
          help + "</div>";
      }
      if (f.type === "select") {
        var opts = (f.options || []).map(function (o) {
          var sel = (!f.required && String(f.default) === String(o.value)) ? " selected" : "";
          return '<option value="' + PX.esc(o.value) + '"' + sel + ">" +
            PX.esc(o.label || o.value) + "</option>";
        }).join("");
        return box(head + '<select id="' + id + '"' + attr +
          (f.required ? " required" : "") + ">" +
          (f.required ? '<option value="">\u8bf7\u9009\u62e9</option>' : "") + opts +
          "</select>" + help);
      }
      if (f.type === "int") {
        var hasDef = f.default !== undefined && f.default !== null;
        return box(head + '<input id="' + id + '" type="number"' +
          attr + (hasDef ? ' value="' + PX.esc(String(f.default)) + '"' : "") +
          (f.min !== undefined && f.min !== null ? ' min="' + f.min + '"' : "") +
          (f.max !== undefined && f.max !== null ? ' max="' + f.max + '"' : "") +
          ">" + help);
      }
      var def = f.default;
      return box(head + '<input id="' + id + '" type="text"' +
        attr + (def !== undefined && def !== null && def !== ""
          ? ' value="' + PX.esc(String(def)) + '"' : "") + ">" + help);
    }).join("");
    $("#createErr").textContent = "";
    renderInviteChips();
    bindShowWhen($("#kindFields"));
  }

  // A field can say what it depends on; the whole row disappears when the
  // choice it depends on is not the one it applies to.
  function bindShowWhen(scope) {
    var rows = $$("[data-show-when]", scope);
    if (!rows.length) return;
    var sync = function () {
      rows.forEach(function (row) {
        var key = row.getAttribute("data-show-when");
        var want = row.getAttribute("data-show-is");
        var driver = $('[data-kind-field="' + key + '"]', scope);
        var value = driver ? String(driver.value) : "";
        row.hidden = value !== want;
      });
    };
    rows.forEach(function (row) {
      var driver = $('[data-kind-field="' + row.getAttribute("data-show-when") + '"]', scope);
      if (driver) {
        driver.addEventListener("change", sync);
        driver.addEventListener("input", sync);
      }
    });
    sync();
  }

  function collectKindParams() {
    var out = {};
    $$("[data-kind-field]").forEach(function (el) {
      var key = el.getAttribute("data-kind-field");
      if (el.type === "checkbox") out[key] = el.checked;
      else if (el.value !== "") out[key] = el.value;
    });
    return out;
  }

  function renderInviteChips() {
    $("#inviteChips").innerHTML = S.invited.map(function (id) {
      var a = S.accounts.filter(function (x) { return x.id === id; })[0];
      if (!a) return "";
      return '<span class="invite-chip">' + PX.esc(displayName(a)) +
        '<button type="button" data-uninvite="' + a.id + '" aria-label="\u79fb\u9664">' +
        "\u00d7</button></span>";
    }).join("");
    $("#inviteHint").textContent = S.invited.length
      ? "\u5df2\u9009 " + S.invited.length + " \u4eba\uff0c\u521b\u5efa\u540e\u53d1\u51fa\u9080\u8bf7"
      : "";
  }

  function openInvitePicker() {
    var rows = S.accounts.filter(function (a) { return a.id !== (S.me && S.me.id); });
    var html = rows.length
      ? '<div class="pick-grid">' + rows.map(function (a) {
        var on = S.invited.indexOf(a.id) >= 0;
        var url = avatarUrl(a);
        return '<button type="button" class="pick-tile' + (on ? " on" : "") +
          '" data-invite="' + a.id + '">' +
          '<span class="avatar">' + (url
            ? '<img alt="" src="' + PX.esc(url) + '">'
            : PX.esc(initialOf(a))) + "</span>" +
          '<span class="pick-name">' + PX.esc(displayName(a)) + "</span>" +
          '<span class="tag">' + PX.esc(a.roleLabel) + "</span></button>";
      }).join("") + "</div>"
      : '<div class="empty">\u9664\u4e86\u4f60\u81ea\u5df1\uff0c\u8fd8\u6ca1\u6709\u522b\u7684\u8d26\u53f7</div>';
    // UNWIRED(groups): 分组一键邀请要等用户分组做出来
    openOverlay("invite", "\u5feb\u901f\u9080\u8bf7", html +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn primary" type="button" data-close-overlay="1">\u5b8c\u6210</button>' +
      "</div>");
  }

  function toggleInvite(id) {
    var at = S.invited.indexOf(id);
    if (at >= 0) S.invited.splice(at, 1);
    else S.invited.push(id);
    renderInviteChips();
    var tile = $('[data-invite="' + id + '"]');
    if (tile) tile.classList.toggle("on", at < 0);
  }

  function createEvent() {
    var err = $("#createErr");
    err.textContent = "";
    var body = {
      kind: S.kindId,
      title: $("#neTitle").value.trim(),
      note: $("#neNote").value.trim(),
      params: collectKindParams(),
      members: S.invited.slice()
    };
    if (!body.title) {
      err.textContent = "\u8bf7\u586b\u5199\u6d3b\u52a8\u540d\u79f0";
      $("#neTitle").focus();
      return;
    }
    var btn = $("#kindCreate");
    btn.disabled = true;
    PX.api("/events/create", { method: "POST", body: body })
      .then(function (d) {
        PX.toast("\u5df2\u521b\u5efa");
        location.href = d.event.admin;
      })
      .catch(function (e) {
        err.textContent = e.message;
        btn.disabled = false;
      });
  }

  function drawUsers() {
    renderAccountsTable();
  }

  function canDecide(acc) {
    if (myRank() < rank("superadmin")) return false;
    if (acc.role === "owner") return false;
    if (isOwner()) return true;
    return acc.role === "admin";
  }

  function renderAccountsTable() {
    var q = S.userFilter.trim().toLowerCase();
    var rows = S.accounts.filter(function (a) {
      if (!q) return true;
      return (a.username || "").toLowerCase().indexOf(q) >= 0 ||
        (a.name || "").toLowerCase().indexOf(q) >= 0;
    });

    $("#accountsTable tbody").innerHTML = rows.length ? rows.map(function (a) {
      return "<tr>" +
        "<td><b>" + PX.esc(a.username) + "</b>" +
        (S.me && a.id === S.me.id ? ' <span class="tag">我</span>' : "") + "</td>" +
        "<td>" + PX.esc(a.name || "—") + "</td>" +
        "<td>" + roleTag(a) + "</td>" +
        "<td>" + statusTag(a) + "</td>" +
        '<td class="mono">' + PX.when(a.lastLogin) + "</td>" +
        '<td class="actions">' +
        '<button class="btn sm" data-acct="' + a.id + '">管理</button>' +
        "</td></tr>";
    }).join("") : emptyRow(6, "没有匹配的账号");
  }

  function openAccountOverlay(id) {
    var a = S.accounts.filter(function (x) { return x.id === id; })[0];
    if (!a) return;

    var rows = [
      ["用户名", a.username],
      ["称呼", a.name || "—"],
      ["角色", a.roleLabel],
      ["状态", a.statusLabel],
      ["注册时间", PX.when(a.createdAt)],
      ["最近登录", PX.when(a.lastLogin)]
    ];
    if (a.decidedBy) rows.push(["上次处理", a.decidedBy + " · " + PX.when(a.decidedAt)]);
    if (a.note) rows.push(["申请说明", a.note]);

    var btns = [];
    if (canDecide(a)) {
      if (a.status === "pending") {
        btns.push('<button class="btn primary" data-decide="approve" data-id="' + a.id + '">通过</button>');
        btns.push('<button class="btn danger" data-decide="reject" data-id="' + a.id + '">拒绝</button>');
      }
      if (a.status === "active") {
        btns.push('<button class="btn danger" data-decide="disable" data-id="' + a.id + '">停用</button>');
      }
      if (a.status === "disabled" || a.status === "rejected") {
        btns.push('<button class="btn" data-decide="enable" data-id="' + a.id + '">启用</button>');
      }
    }
    if (isOwner() && a.role !== "owner") {
      btns.push(a.role === "superadmin"
        ? '<button class="btn ghost" data-role="admin" data-id="' + a.id + '">降为管理员</button>'
        : '<button class="btn ghost" data-role="superadmin" data-id="' + a.id + '">升为超级管理员</button>');
    }

    openOverlay("account", a.username,
      '<dl class="kv">' + rows.map(function (r) {
        return "<dt>" + PX.esc(r[0]) + "</dt><dd>" + PX.esc(r[1]) + "</dd>";
      }).join("") + "</dl>" +
      (btns.length
        ? '<div class="row" style="margin-top:16px">' + btns.join("") + "</div>"
        : '<p class="page-sub" style="margin-top:16px">这个账号没有可以执行的操作。</p>'));
    // afterMutation() re-opens this dialog with fresh data
    $("#overlayBody").setAttribute("data-acct", String(a.id));
  }

  function drawAllEvents() {
    $("#allEventsTable tbody").innerHTML = S.events.length ? S.events.map(function (e) {
      return "<tr>" +
        "<td><b>" + PX.esc(eventTitle(e)) + "</b></td>" +
        '<td class="mono" style="font-size:.78rem" title="' + PX.esc(e.id) + '">' +
        PX.esc(shortToken(e.id)) + "</td>" +
        "<td>" + PX.esc(e.ownerName || "—") + "</td>" +
        '<td class="mono">' + PX.when(e.createdAt) + "</td>" +
        '<td class="actions">' +
        // the public board, the projector page and the console are all reachable
        // from inside an event; this list only needs the way in
        '<a class="btn sm primary" href="' + PX.esc(e.admin) + '" target="_blank" ' +
        'rel="noopener">管理</a>' +
        "</td></tr>";
    }).join("") : emptyRow(5, "还没有活动");
  }

  function drawApprovalUsers() {
    var pending = pendingAccounts();
    $("#approvalUsersSub").textContent = pending.length
      ? "有 " + pending.length + " 个注册申请等待处理"
      : "没有待处理的注册申请";

    $("#pendingTable tbody").innerHTML = pending.length ? pending.map(function (a) {
      return "<tr>" +
        "<td><b>" + PX.esc(a.username) + "</b></td>" +
        "<td>" + PX.esc(a.name || "—") + "</td>" +
        '<td class="wrap">' + PX.esc(a.note || "—") + "</td>" +
        '<td class="mono">' + PX.when(a.createdAt) + "</td>" +
        '<td class="actions">' +
        '<button class="btn sm primary" data-decide="approve" data-id="' + a.id + '">通过</button> ' +
        '<button class="btn sm danger" data-decide="reject" data-id="' + a.id + '">拒绝</button>' +
        "</td></tr>";
    }).join("") : emptyRow(5, "没有待处理的申请");

    var decided = S.accounts.filter(function (a) {
      return a.decidedAt && a.status !== "pending";
    }).sort(function (a, b) { return b.decidedAt - a.decidedAt; }).slice(0, 20);

    $("#decidedTable tbody").innerHTML = decided.length ? decided.map(function (a) {
      return "<tr>" +
        "<td><b>" + PX.esc(a.username) + "</b></td>" +
        "<td>" + PX.esc(a.name || "—") + "</td>" +
        "<td>" + statusTag(a) + "</td>" +
        "<td>" + PX.esc(a.decidedBy || "—") + "</td>" +
        '<td class="mono">' + PX.when(a.decidedAt) + "</td></tr>";
    }).join("") : emptyRow(5, "还没有处理记录");
  }

  function drawApprovalEvents() {
    // UNWIRED(event-approvals): 活动申请队列不存在，默认自动通过。
  }

  function drawServer() {
    var h = S.host;
    $("#srvCpu").textContent = h ? pct(h.cpu) : "–";
    $("#srvMem").textContent = h ? pct(h.mem) : "–";
    $("#srvUptime").textContent = h && h.uptime ? humanUptime(h.uptime) : "–";
    $("#srvVersion").textContent = h && h.version ? h.version : "–";

    var rows = [];
    if (h) {
      rows.push(["主机", h.host || "—"]);
      rows.push(["内存", (h.mem !== null && h.mem !== undefined)
        ? Math.round(h.mem) + "%" +
          (h.memTotalMB ? "（共 " + Math.round(h.memTotalMB) + " MB）" : "")
        : "—"]);
      rows.push(["进程", "pid " + (h.pid || "—")]);
      rows.push(["Python", h.python || "—"]);
      rows.push(["数据目录", h.dataDir || "—"]);
      rows.push(["活动数", String(S.events.length)]);
      rows.push(["账号数", String(S.accounts.length)]);
    } else {
      rows.push(["状态", "这台机器读不到设备占用（非 Linux 或 /proc 不可用）"]);
    }
    $("#srvInfo").innerHTML = rows.map(function (r) {
      return "<dt>" + PX.esc(r[0]) + "</dt><dd>" + PX.esc(r[1]) + "</dd>";
    }).join("");
  }

  function humanUptime(sec) {
    sec = Math.max(0, Math.floor(sec));
    var d = Math.floor(sec / 86400);
    var h = Math.floor((sec % 86400) / 3600);
    var m = Math.floor((sec % 3600) / 60);
    if (d) return d + " 天 " + h + " 小时";
    if (h) return h + " 小时 " + m + " 分";
    return m + " 分";
  }

  function drawAudit() {
    $("#auditTable tbody").innerHTML = S.audit.length ? S.audit.map(function (r) {
      return '<tr><td class="mono">' + PX.whenExact(r.ts) + "</td>" +
        "<td>" + PX.esc(r.actor || "") + "</td>" +
        "<td>" + PX.esc(r.action) + "</td>" +
        "<td>" + PX.esc(r.target || "") + "</td>" +
        '<td class="wrap">' + PX.esc(r.detail || "") + "</td></tr>";
    }).join("") : emptyRow(5, "还没有记录");
  }

  function drawAccount() {
    var me = S.me;
    if (!me) return;

    paintAvatar($("#profileAvatar"), me, initialOf(me));
    var badge = $("#avatarPick");
    badge.innerHTML = me.avatar ? ICON_PENCIL : ICON_PLUS;
    badge.title = me.avatar ? "更换头像" : "添加头像";

    $("#pfName").value = me.name || "";
    var user = $("#pfUser");
    user.value = me.username;
    var ownerLocked = me.role === "owner";
    user.readOnly = ownerLocked;
    user.classList.toggle("readonly", ownerLocked);
    $("#pfUserHint").textContent = ownerLocked
      ? "站长的登录名由 config.json 的 owner_username 决定"
      : "用于登录，3~24 位，只能用字母数字和 . _ -";

    var rows = [
      ["角色", me.roleLabel],
      ["状态", me.statusLabel],
      ["注册时间", PX.when(me.createdAt)],
      ["最近登录", PX.when(me.lastLogin)]
    ];
    $("#accountInfo").innerHTML = rows.map(function (r) {
      return "<dt>" + PX.esc(r[0]) + "</dt><dd>" + PX.esc(r[1]) + "</dd>";
    }).join("");
  }

  function applyMe(me) {
    if (!me) return;
    S.me = me;
    var mine = S.accounts.filter(function (a) { return a.id === me.id; })[0];
    if (mine) {
      for (var key in me) {
        if (Object.prototype.hasOwnProperty.call(me, key)) mine[key] = me[key];
      }
    }
    renderIdentity();
    drawAccount();
  }

  function saveProfile() {
    var err = $("#pfErr");
    err.textContent = "";
    PX.api("/profile", {
      method: "POST",
      body: { name: $("#pfName").value, username: $("#pfUser").value }
    }).then(function (d) {
      applyMe(d.me);
      PX.toast("已保存");
      return reloadAll();
    }).catch(function (e) {
      err.textContent = e.message;
    });
  }

  function sendAvatar(body, doneMessage) {
    PX.api("/avatar", { method: "POST", body: body })
      .then(function (d) {
        applyMe(d.me);
        closeOverlay();
        PX.toast(doneMessage);
        return reloadAll();
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function openAvatarDialog() {
    var has = !!(S.me && S.me.avatar);
    openOverlay("avatar", has ? "更换头像" : "添加头像",
      '<div class="avatar-rows">' +
      '<div class="field"><label for="avatarUrl">图片链接</label>' +
      '<div class="avatar-row">' +
      '<input id="avatarUrl" type="url" placeholder="https://…" ' +
      'autocomplete="off" spellcheck="false">' +
      '<button class="btn primary" type="button" data-avatar-url="1">使用</button>' +
      "</div></div>" +
      '<div class="avatar-or">或者</div>' +
      '<div class="avatar-row">' +
      '<button class="btn" type="button" data-avatar-file="1">从本机导入图片</button>' +
      (has ? '<button class="btn danger" type="button" data-avatar-clear="1">' +
        "移除头像</button>" : "") +
      "</div>" +
      '<p class="hint">' + AVATAR_HINT + "</p></div>");
    var box = $("#avatarUrl");
    if (box) box.focus();
  }

  function drawAppearance() {
    var pref = document.documentElement.getAttribute("data-theme-pref") || "auto";
    $$("#themeSeg button").forEach(function (b) {
      b.setAttribute("aria-checked",
        b.getAttribute("data-theme-pref") === pref ? "true" : "false");
    });
  }

  /* ------------------------------------------------------------------ host */
  function applyHost(h) {
    S.host = h;
    $("#hostStats").hidden = !isOwner() || !h;
    if (!h) {
      if (S.route === "server") drawServer();
      return;
    }
    $("#cpuNum").textContent = pct(h.cpu);
    $("#memNum").textContent = pct(h.mem);
    $("#cpuBar").style.width = Math.max(0, Math.min(100, h.cpu || 0)) + "%";
    $("#memBar").style.width = Math.max(0, Math.min(100, h.mem || 0)) + "%";
    if (S.route === "server") drawServer();
  }

  function refreshHost() {
    if (!isOwner()) return;
    PX.api("/host")
      .then(function (d) { applyHost(d && d.host ? d.host : null); })
      .catch(function () { applyHost(null); });
  }

  function startHostPolling() {
    if (!isOwner() || S.hostTimer) return;
    refreshHost();
    S.hostTimer = setInterval(function () {
      if (document.hidden) return;
      refreshHost();
    }, 5000);
  }

  /* ------------------------------------------------------------------ data */
  function loadAccounts() {
    return PX.api("/accounts").then(function (d) {
      S.me = d.me;
      S.accounts = d.accounts || [];
      renderIdentity();
      applyRoleVisibility();
      syncPendingBadge();
    });
  }

  function loadEvents() {
    return PX.api("/events").then(function (d) { S.events = d.events || []; })
      .catch(function () { S.events = []; });
  }

  function loadAudit() {
    if (!canAudit()) return Promise.resolve();
    return PX.api("/audit").then(function (d) { S.audit = d.audit || []; })
      .catch(function () { S.audit = []; });
  }

  function syncPendingBadge() {
    var n = pendingAccounts().length;
    var badge = $("#pendingBadge");
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.hidden = !canReview() || n === 0;
    // UNWIRED(invites): 收到邀请后这里显示角标
    $("#inviteBadge").hidden = S.invites.length === 0;
  }

  function reloadAll() {
    return loadAccounts().then(function () {
      return Promise.all([loadEvents(), loadAudit()]);
    }).then(function () {
      applyRoleVisibility();
      draw();
    });
  }

  /* --------------------------------------------------------------- actions */
  function decide(id, action) {
    PX.api("/account/decide", { method: "POST", body: { id: id, action: action } })
      .then(function () {
        PX.toast("已" + ({
          approve: "通过", reject: "拒绝", disable: "停用", enable: "启用"
        })[action]);
        return reloadAll().then(afterMutation);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function changeRole(id, role) {
    PX.api("/account/role", { method: "POST", body: { id: id, role: role } })
      .then(function () { PX.toast("角色已更新"); return reloadAll().then(afterMutation); })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  // keep an open account dialog in sync with the data behind it
  function afterMutation() {
    if ($("#overlay").hidden || overlayKind !== "account") return;
    var id = Number($("#overlayBody").getAttribute("data-acct"));
    if (id) openAccountOverlay(id);
    else closeOverlay();
  }

  function setTheme(pref) {
    try { localStorage.setItem("xl.panel.theme", pref); } catch (e) { /* private mode */ }
    var dark = pref === "dark" ||
      (pref === "auto" && window.matchMedia &&
       window.matchMedia("(prefers-color-scheme: dark)").matches);
    var root = document.documentElement;
    var next = dark ? "dark" : "light";
    var changed = root.getAttribute("data-theme") !== next;

    var apply = function () {
      root.setAttribute("data-theme", next);
      root.setAttribute("data-theme-pref", pref);
    };

    // Cross-fade two snapshots rather than animating each property: measured
    // frame by frame, transitioning colour made headings jump back to a mid grey
    // and run twice, which reads as a flicker.
    if (changed && !reducedMotion() && document.startViewTransition) {
      try {
        document.startViewTransition(apply);
      } catch (err) {
        apply();
      }
    } else {
      apply();
    }
    drawAppearance();
  }

  function reducedMotion() {
    return !!(window.matchMedia &&
              window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  /* -------------------------------------------------------------- listeners */
  // Everything is read from the closest ancestor: a tap usually lands on the
  // icon or the label inside the control, never on the control itself.
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;

    var dec = t.closest("[data-decide]");
    if (dec) return decide(Number(dec.getAttribute("data-id")),
                           dec.getAttribute("data-decide"));

    var role = t.closest("[data-role]");
    if (role) return changeRole(Number(role.getAttribute("data-id")),
                                role.getAttribute("data-role"));

    var acct = t.closest("[data-acct]");
    if (acct) return openAccountOverlay(Number(acct.getAttribute("data-acct")));

    var toggle = t.closest("[data-toggle]");
    if (toggle) return onGroupHead(toggle.getAttribute("data-toggle"));

    var kindTile = t.closest("[data-kind]");
    if (kindTile) return go("new-event/" + kindTile.getAttribute("data-kind"));

    var invite = t.closest("[data-invite]");
    if (invite) return toggleInvite(Number(invite.getAttribute("data-invite")));

    var uninvite = t.closest("[data-uninvite]");
    if (uninvite) return toggleInvite(Number(uninvite.getAttribute("data-uninvite")));

    var renameBtn = t.closest("[data-rename]");
    if (renameBtn) return openRename(renameBtn.getAttribute("data-rename"));

    var renameSave = t.closest("[data-rename-save]");
    if (renameSave) return saveRename(renameSave.getAttribute("data-rename-save"));

    var menuBtn = t.closest("[data-menu]");
    if (menuBtn) {
      e.stopPropagation();
      return openCardMenu(menuBtn, menuBtn.getAttribute("data-menu"));
    }

    var copyBtn = t.closest("[data-copy]");
    if (copyBtn) {
      e.stopPropagation();
      closeCardMenu();
      return copyText(copyBtn.getAttribute("data-copy"), copyBtn);
    }

    var openLink = t.closest("[data-open-link]");
    if (openLink) {
      closeCardMenu();
      window.open(openLink.getAttribute("data-open-link"), "_blank", "noopener");
      return;
    }

    if (t.closest("[data-avatar-file]")) return $("#avatarFile").click();
    if (t.closest("[data-avatar-clear]")) {
      return sendAvatar({ clear: true }, "头像已移除");
    }
    if (t.closest("[data-avatar-url]")) {
      var box = $("#avatarUrl");
      var link = box ? box.value.trim() : "";
      if (!/^https?:\/\//i.test(link)) {
        PX.toast("链接需要是 http 或 https 开头", true);
        return;
      }
      return sendAvatar({ url: link }, "头像已更新");
    }

    var logoutBtn = t.closest("[data-logout]");
    if (logoutBtn) return logout(logoutBtn.getAttribute("data-logout"));

    if (t.closest("[data-close-overlay]")) return closeOverlay();

    var goEl = t.closest("[data-go]");
    if (goEl) return go(goEl.getAttribute("data-go"));

    var theme = t.closest("#themeSeg [data-theme-pref]");
    if (theme) return setTheme(theme.getAttribute("data-theme-pref"));
  });

  $("#menuBtn").addEventListener("click", function () {
    if ($("#shell").classList.contains("nav-open")) closePane();
    else openPane();
  });
  $("#scrim").addEventListener("click", closePane);
  $("#sidebar").addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-nav]")) closePane();
  });

  document.addEventListener("click", function (e) {
    // The opener is also on the document, so exclude it: otherwise the menu is
    // closed by the same click that opened it.
    if (cardMenuEl && !(e.target.closest &&
                        (e.target.closest(".card-menu") ||
                         e.target.closest("[data-menu]")))) {
      closeCardMenu();
    }
  });

  function logout(returnRoute) {
    var back = returnRoute ? "?s_return=" + encodeURIComponent(returnRoute) : "";
    var leave = function () { location.href = "login" + back; };
    PX.api("/logout", { method: "POST" }).then(leave).catch(leave);
  }

  function confirmSwitchAccount() {
    var route = S.denied || "";
    openOverlay("switch", "切换账号",
      '<p class="page-sub">退出当前账号后回到登录页，登录成功会直接来到这里</p>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" data-logout="' + PX.esc(route) + '">' +
      "退出并切换账号</button></div>");
  }

  $("#logoutBtn").addEventListener("click", function () { logout(""); });
  $("#switchAccount").addEventListener("click", confirmSwitchAccount);

  function bindLiveSearch(boxSel, clearSel, onChange) {
    var box = $(boxSel);
    var clear = $(clearSel);
    if (!box || !clear) return;
    var sync = function () { clear.hidden = !box.value; };
    box.addEventListener("input", function () { onChange(box.value); sync(); });
    clear.addEventListener("click", function () {
      box.value = "";
      onChange("");
      sync();
      box.focus();
    });
    PX.guardAutofill(box, function () { box.value = ""; onChange(""); sync(); });
    sync();
  }

  function syncClearButton() {
    var box = $("#userSearch");
    var clear = $("#userSearchClear");
    if (box && clear) clear.hidden = !box.value;
  }

  function bindUserSearch() {
    var box = $("#userSearch");
    var clear = $("#userSearchClear");
    box.addEventListener("input", function (e) {
      S.userFilter = e.target.value || "";
      syncClearButton();
      renderAccountsTable();
    });
    clear.addEventListener("click", function () {
      box.value = "";
      S.userFilter = "";
      syncClearButton();
      renderAccountsTable();
      box.focus();
    });
    PX.guardAutofill(box, function () {
      S.userFilter = "";
      syncClearButton();
      renderAccountsTable();
    });
    syncClearButton();
  }

  $("#hostRefresh").addEventListener("click", function () {
    refreshHost();
    PX.toast("已刷新");
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

  bindUserSearch();

  $("#pfSave").addEventListener("click", saveProfile);
  $("#avatarPick").addEventListener("click", openAvatarDialog);
  $("#avatarFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 512 * 1024) {
      PX.toast("图片超过 512 KB", true);
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      sendAvatar({ data: String(reader.result) }, "头像已更新");
    };
    reader.onerror = function () { PX.toast("读不了这个文件", true); };
    reader.readAsDataURL(file);
  });

  function newEvent() {
    S.invited = [];
    loadKinds().then(function () { go("new-event"); });
  }
  $("#newEventBtn").addEventListener("click", newEvent);
  // 活动管理 is a superadmin view; creating from there uses the same flow
  $("#createEventBtn").addEventListener("click", newEvent);

  $("#kindBack").addEventListener("click", function () {
    S.invited = [];
    go("new-event");
  });
  $("#pickerBack").addEventListener("click", function () { go("events"); });
  $("#kindCreate").addEventListener("click", createEvent);
  $("#invitePick").addEventListener("click", openInvitePicker);
  bindLiveSearch("#kindSearch", "#kindSearchClear", function (value) {
    S.kindFilter = value;
    if (!$("#kindPicker").hidden) $("#kindGrid").innerHTML = renderKindTiles();
  });

  window.addEventListener("hashchange", onRoute);

  window.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      if (cardMenuEl) return closeCardMenu();
      if (!$("#overlay").hidden) return closeOverlay();
      closePane();
    }
    // keep Tab inside the dialog while it is open
    if (e.key === "Tab" && !$("#overlay").hidden) {
      var focusable = $$('button, [href], input, select, textarea', $("#overlay"))
        .filter(function (el) { return !el.disabled && el.offsetParent !== null; });
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onScheme = function () {
      if (document.documentElement.getAttribute("data-theme-pref") === "auto") {
        setTheme("auto");
      }
    };
    if (mq.addEventListener) mq.addEventListener("change", onScheme);
    else if (mq.addListener) mq.addListener(onScheme);
  }

  /* ------------------------------------------------------------------- boot */
  function boot() {
    PX.api("/me").then(function (d) {
      if (!d.authed) {
        location.href = "login";
        return;
      }
      S.me = d.me;
      renderIdentity();
      applyRoleVisibility();
      labelNav();
      return reloadAll().then(function () {
        startHostPolling();
        onRoute();
        // a real readiness signal: tests used to poll the placeholder text
        document.documentElement.setAttribute("data-booted", "1");
      });
    }).catch(function (e) {
      if (e.status === 401) location.href = "login";
      else PX.toast(PX.esc(e.message || "加载失败"), true);
    });
  }

  boot();
})();
