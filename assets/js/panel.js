/* Master panel shell: navigation, pages, and the account menu.
 *
 * Everything that is visible but not backed by a real endpoint is marked
 * UNWIRED(<feature>) so a later pass can grep for it:
 *
 *     grep -rn "UNWIRED" assets/ pages/ app.py
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
    "assistant": { min: "admin" },
    "events": { min: "admin" },
    "rosters": { min: "admin", group: "lists" },
    "roster": { min: "admin", group: "lists",
                parent: { href: "#/rosters", label: "我的名单" } },
    "signups": { min: "admin", group: "lists" },
    "signup": { min: "admin", group: "lists",
                parent: { href: "#/signups", label: "我的报名" } },
    "write-approvals": { min: "admin", group: "lists" },
    "screens": { min: "admin", group: "resources" },
    "templates": { min: "admin" },
    "users": { min: "superadmin" },
    // 批量建号只给站长：这条路上能把人设成超级管理员，超管不能自己开这道门
    "import-accounts": { min: "owner", parent: { href: "#/users", label: "用户管理" } },
    "all-events": { min: "superadmin" },
    "roster-admin": { min: "superadmin" },
    "orgs": { min: "superadmin" },
    // 我参加的组织：成员自己看的那一页
    "my-orgs": { min: "admin" },
    "my-org": { min: "admin", parent: { href: "#/my-orgs", label: "我的组织" } },
    "my-org-manage": { min: "admin", parent: { href: "#/my-orgs", label: "我的组织" } },
    // 组织详情对管理员也开：能不能看由服务端按成员关系判，看不到就是 404
    "org": { min: "admin", parent: { href: "#/orgs", label: "组织管理" } },
    "org-new": { min: "superadmin", parent: { href: "#/orgs", label: "组织管理" } },
    "approval-users": { min: "superadmin", group: "approvals" },
    "approval-events": { min: "superadmin", group: "approvals" },
    "new-event": { min: "admin", trail: "events" },
    // 模块管理是站长自己的页面：装模块、停模块都是整台机器的事
    "modules": { min: "owner" },
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
    // 每一份列表取回来了没有：没取回来的空列表只能说"正在读取…"，
    // 不能说"还没有…"（见 rowsOrEmpty）
    listLoaded: {},
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
    templates: [],
    archived: [],
    archivedTotal: null,
    archivedLoading: false,
    // 名单管理（超管）：全站的名单与报名
    adminRosters: [],
    adminSignups: [],
    adminArchived: false,
    currentFields: [],
    templateId: null,
    templateFilter: "",
    invited: [],
    // 选进来的人：id -> {id, uid, label, avatar, avatarAt}（/people 的形状）
    people: {},
    // 名单：列表与当前打开的那一份
    rosters: [],
    rosterTotal: null,
    rosterScope: "joined",
    rosterFilter: "",
    rosterId: 0,
    roster: null,
    rosterEntries: [],
    entryTotal: null,
    entryFilter: "",
    rosterImportName: "",
    signups: [],
    signupTotal: null,
    signupFilter: "",
    signup: null,
    signupEntries: [],
    signupEntryTotal: null,
    signupEntryFilter: "",
    signupDirty: false,     // 表单被改过：后台刷新不要覆盖它
    history: [],            // 表单快照栈：Ctrl+Z / Ctrl+Shift+Z
    historyPos: -1,
    screenPacks: [],
    packManage: false,     // 站长/超级管理员才能导入与删除
    packLimits: null,      // 导入的限制，由服务端给出
    users: null,           // 在线/注册，随 /host 一起回来
    tokens: [],            // 我的 API 令牌
    tokenMeta: null,       // 可选的权限与它们的说明
    assistantCfg: null,    // 这个账号的模型配置（不含密钥）
    assistantMsgs: [],     // 本次对话，只在这里，不进库
    assistantPending: null,   // 助手要改数据、等着用户点批准的那一步
    assistantAbort: null,  // 正在等回答时的中止器：这颗按钮此刻是"停止"
    overviewCounts: {},    // 总览上的"我的名单 / 我的报名"数
    updateTo: "",          // 服务端换了版本，等一个能安全刷新的时机
    typed: false,          // 这一页里敲过字：别在人家填表的时候刷新页面
    notices: [],
    noticeUnread: 0,
    service: [],           // 服务有效性提示：服务端推出来的，不进库
    svcMuted: {},          // 手动关掉的常驻提示，记在这台设备上
    alertOpen: false,
    approvals: [],
    myAsks: [],
    approvalsCount: 0,
    backTo: null,         // 从别的页面跳进审批中心时，返回哪里
    explicitBack: null,   // 地址里真的写了 ?back=
  };

  var RAIL_QUERY = "(max-width: 1100px)";
  var AVATAR_HINT = "PNG / JPEG / WebP，不超过 512 KB";
  var ICON_PLUS = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<path d="M10 5.2v9.6M5.2 10h9.6"/></svg>';
  var ICON_PENCIL = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<path d="M4.2 15.8h3.1l7.5-7.5a1.6 1.6 0 0 0-2.3-2.3l-7.5 7.5v2.3Z"/>' +
    '<path d="M11.5 7l2.3 2.3"/></svg>';
  var ICON_GEAR = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8.183 5.503 L8.745 5.315 L9.03 3.876 L10.97 3.876 L11.255 5.315 L11.817 5.503 L11.895 5.536 L12.425 5.8 L13.644 4.984 L15.016 6.356 L14.2 7.575 L14.464 8.105 L14.497 8.183 L14.685 8.745 L16.124 9.03 L16.124 10.97 L14.685 11.255 L14.497 11.817 L14.464 11.895 L14.2 12.425 L15.016 13.644 L13.644 15.016 L12.425 14.2 L11.895 14.464 L11.817 14.497 L11.255 14.685 L10.97 16.124 L9.03 16.124 L8.745 14.685 L8.183 14.497 L8.105 14.464 L7.575 14.2 L6.356 15.016 L4.984 13.644 L5.8 12.425 L5.536 11.895 L5.503 11.817 L5.315 11.255 L3.876 10.97 L3.876 9.03 L5.315 8.745 L5.503 8.183 L5.536 8.105 L5.8 7.575 L4.984 6.356 L6.356 4.984 L7.575 5.8 L8.105 5.536 Z"/><circle cx="10" cy="10" r="1.95"/></svg>';
  var ICON_REFRESH = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M16.2 10a6.2 6.2 0 1 1-1.9-4.4"/><path d="M16.4 3.6v3.2h-3.2"/></svg>';
  var ICON_LINK = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8.4 11.6a3.4 3.4 0 0 0 4.8 0l2.1-2.1a3.4 3.4 0 0 0-4.8-4.8l-1 1"/><path d="M11.6 8.4a3.4 3.4 0 0 0-4.8 0l-2.1 2.1a3.4 3.4 0 0 0 4.8 4.8l1-1"/></svg>';
  var ICON_RECLAIM = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4.2 10a5.8 5.8 0 0 1 10-4"/><path d="M14.2 3.6v2.8h-2.8"/><path d="M15.8 10a5.8 5.8 0 0 1-10 4"/><path d="M5.8 16.4v-2.8h2.8"/></svg>';
  var ICON_ARCHIVE = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.4 6.4h13.2v9a1.6 1.6 0 0 1-1.6 1.6H5a1.6 1.6 0 0 1-1.6-1.6v-9Z"/><path d="M2.6 3.4h14.8v3H2.6zM8 10h4"/></svg>';
  var ICON_TRASH = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 5.6h12M8 3.4h4M6.2 5.6l.7 9.4a1.6 1.6 0 0 0 1.6 1.5h3a1.6 1.6 0 0 0 1.6-1.5l.7-9.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var ICON_EXT = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3.8H5.4a1.6 1.6 0 0 0-1.6 1.6v9.2a1.6 1.6 0 0 0 1.6 1.6h9.2a1.6 1.6 0 0 0 1.6-1.6V11"/><path d="M10 10l6.2-6.2M11.8 3.8h4.4v4.4"/></svg>';
  var ICON_COPY = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="7.3" y="7.3" width="8.4" height="8.4" rx="1.6"/><path d="M12.7 4.3H5.9a1.6 1.6 0 0 0-1.6 1.6v6.8"/></svg>';
  var ICON_CHECK = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.8 10.6l3.3 3.3 7.1-7.8"/></svg>';

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
  function isSuper() { return !!S.me && rank(S.me.role) >= rank("superadmin"); }
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

  /* 焦点在不在输入类控件上：弹窗的 ESC 退不退，就看这一条。
     输入框聚焦时 ESC 一律不退（搜索框还留着"清空"），焦点挪开才关。 */
  function typingIn(scope) {
    var el = document.activeElement;
    if (!el || !scope || !scope.contains(el)) return false;
    var tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
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
  /* 圆里一个感叹号：卡片标题旁的「不可用」用它（图标表里登记过的那一个） */
  var ICON_ALERT = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<circle cx="10" cy="10" r="7.1"/>' +
    '<path d="M10 6.1v4.8M10 13.5v.2" stroke-linecap="round"/></svg>';

  function eventCard(e) {
    var meta = [];
    if (e.createdAt) meta.push("建于 " + PX.when(e.createdAt));
    // 依赖的模块被停用：标题右边一枚红色胶囊，原因放在悬停/点击的提示里，
    // 不进卡片正文——多一行字会把卡片撑高，三列排布就参差了
    var badge = e.stopped
      ? '<button class="badge-off" type="button" data-tip="依赖模块被禁用"' +
        ' aria-label="不可用：依赖模块被禁用">' + ICON_ALERT + "不可用</button>"
      : "";
    return '<div class="event-card">' +
      '<div class="event-card-head">' +
      '<div class="event-titlewrap">' +
      '<div class="event-title-row">' +
      '<div class="event-title">' + PX.esc(eventTitle(e)) + "</div>" + badge +
      "</div>" +
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
      // 模块停着的时候这颗按钮必然打不开：不做成主按钮，别催人点
      '<a class="btn' + (e.stopped ? "" : " primary") + '" href="' + PX.esc(e.admin) +
      '" target="_blank" rel="noopener">管理面板</a></div></div>';
  }

  /* 小提示：鼠标悬停就出现，触屏点一下也能看；点别处、按 Esc 就收 */
  var cardTipEl = null;
  var cardTipAnchor = null;
  var cardTipPinned = false;

  function closeCardTip() {
    if (!cardTipEl) return;
    if (cardTipEl.parentNode) cardTipEl.parentNode.removeChild(cardTipEl);
    cardTipEl = null;
    cardTipAnchor = null;
    cardTipPinned = false;
  }

  function showCardTip(anchor, text) {
    closeCardTip();
    var tip = document.createElement("div");
    tip.className = "card-tip";
    tip.setAttribute("role", "tooltip");
    tip.textContent = text;
    document.body.appendChild(tip);
    var r = anchor.getBoundingClientRect();
    var left = Math.min(Math.max(8, r.left + r.width / 2 - tip.offsetWidth / 2),
                        window.innerWidth - tip.offsetWidth - 8);
    var top = r.top - tip.offsetHeight - 8;
    if (top < 8) top = r.bottom + 8;
    tip.style.left = Math.round(left) + "px";
    tip.style.top = Math.round(top) + "px";
    cardTipEl = tip;
    cardTipAnchor = anchor;
  }

  function emptyRow(cols, text) {
    return '<tr><td colspan="' + cols + '" class="empty">' + PX.esc(text) + "</td></tr>";
  }

  /* 列表的两种空状态：正在读的时候说"正在读取"，读完了确实是空的才说"还没有…"。
     结论性的字（还没有 / 没有匹配）只能在数据回来之后出现——不然切一栏就闪一下
     "还没有名单"，看上去像东西没了。 */
  function loaded(key) { return !!S.listLoaded[key]; }
  function rowsOrEmpty(cols, key, text) {
    return emptyRow(cols, loaded(key) ? text : "正在读取…");
  }
  function markLoading(key) { S.listLoaded[key] = false; }
  function markLoaded(key) { S.listLoaded[key] = true; }

  function roleTag(a) {
    var cls = a.role === "owner" ? "owner" : (a.role === "superadmin" ? "superadmin" : "");
    return '<span class="tag ' + cls + '">' + PX.esc(a.roleLabel) + "</span>";
  }
  function statusTag(a) {
    var cls = a.status === "active" ? "" : a.status;
    var out = '<span class="tag ' + cls + '">' + PX.esc(a.statusLabel) + "</span>";
    // 临时密码：还没自己改过密码，登录后只能先去重设
    if (a.passTemp) {
      out += ' <span class="tag warn" title="还在用临时密码，登录后要先重设">临时密码</span>';
    }
    return out;
  }

  /* ---------------------------------------------------------------- overlay */
  var overlayKind = null;
  var overlayOpener = null;
  var overlayAfter = null;

  // 允许点遮罩关闭的弹窗（都是没有输入、关掉不丢东西的）
  var OVERLAY_OUTSIDE_CLOSE = { presets: true, info: true, "event-detail": true,
                                "admin-roster": true, "admin-signup": true };

  /* 叠放：sudo 提示这类"盖在当前弹窗上的小窗"关掉之后要回到原来那一层，
     不能把下面的详情一起关掉 */
  var OVERLAY_STACK = [];

  function openOverlay(kind, title, html, after, stack) {
    // 只有明确要叠（stack=true）的弹窗才压住下面那层：sudo 提示、以及从详情里点开的确认框。
    // 其它的照旧替换，免得一层层弹窗堆着关不掉
    if (stack && !$("#overlay").hidden && overlayKind) {
      OVERLAY_STACK.push({ kind: overlayKind, title: $("#overlayTitle").textContent,
                           html: $("#overlayBody").innerHTML,
                           after: overlayAfter });
    }
    overlayKind = kind;
    overlayAfter = after;
    var card = $("#overlay").firstElementChild;
    if (card) {
      // the password prompt is its own thing: narrow, and with the title
      // inside the body under the mark instead of a header bar with an x
      card.classList.toggle("narrow", kind === "sudo");
      if (kind === "sudo") card.setAttribute("aria-label", title);
      else card.removeAttribute("aria-label");
    }
    overlayOpener = document.activeElement;
    $("#overlayTitle").textContent = title;
    // the body is shared between dialogs: nothing may survive from the last one
    var body = $("#overlayBody");
    body.removeAttribute("data-acct-body");
    body.innerHTML = html;
    $("#overlay").hidden = false;
    document.body.style.overflow = "hidden";
    var close = $("#overlayClose");
    if (close && kind !== "sudo") close.focus();
    if (typeof after === "function") after(body);
  }

  function closeAllOverlays() {
    OVERLAY_STACK.length = 0;
    closeOverlay();
  }

  function closeOverlay() {
    if ($("#overlay").hidden) return;
    if (OVERLAY_STACK.length) {
      // 回到上一层弹窗，而不是把它一起关掉
      var back = OVERLAY_STACK.pop();
      $("#overlay").hidden = true;
      $("#overlayBody").innerHTML = "";
      openOverlay(back.kind, back.title, back.html, back.after);
      return;
    }
    var asked = overlayKind === "sudo";
    $("#overlay").hidden = true;
    $("#overlayBody").innerHTML = "";
    document.body.style.overflow = "";
    overlayKind = null;
    // the x, the scrim and Escape close the dialog before asking anything;
    // a pending password prompt must be settled or its caller waits forever
    if (asked) abortSudoAsk();
    if (overlayOpener && overlayOpener.focus) overlayOpener.focus();
    overlayOpener = null;
  }

  $("#overlayClose").addEventListener("click", closeOverlay);
  $("#overlay").addEventListener("click", function (e) {
    // 点遮罩默认什么都不做：多数弹窗里有正在编辑的内容，关掉就是丢东西。
    // 需要"点外面关闭"的弹窗自己带 data-outside-close。
    if (e.target !== $("#overlay")) return;
    if (overlayKind && OVERLAY_OUTSIDE_CLOSE[overlayKind]) closeOverlay();
  });

  /* ------------------------------------------------------------ sudo mode */
  /* Re-entering the password raises this one session for a few minutes.  The
   * deadline is stored on the session row, so a second device signed in as the
   * same person is not elevated, and signing out ends the elevation with the
   * session.  Sensitive endpoints answer 403 + sudoRequired; apiSudo below
   * turns that into a password prompt and repeats the very same request. */
  var SUDO = { deadline: 0, timer: null, pop: null, asker: null };

  // One source of truth for the mark: copy it out of the title bar rather than
  // keeping a second copy of the paths here.
  function shieldMarkup() {
    var chip = $("#sudoChip");
    var svg = chip && chip.querySelector("svg");
    return svg ? svg.outerHTML : "";
  }

  function sudoLeft() {
    return SUDO.deadline ? Math.max(0, SUDO.deadline - Date.now() / 1000) : 0;
  }
  function sudoActive() { return sudoLeft() > 0; }

  function clockText(sec) {
    sec = Math.max(0, Math.ceil(sec));
    return PX.pad(Math.floor(sec / 60)) + ":" + PX.pad(sec % 60);
  }

  // The server sends an absolute deadline; Date.now() on this device may not
  // agree with the server's clock, so the remaining time is applied locally.
  function setSudo(until, serverNow) {
    var local = Date.now() / 1000;
    var base = (serverNow === undefined || serverNow === null) ? local : serverNow;
    SUDO.deadline = until ? local + Math.max(0, until - base) : 0;
    if (SUDO.deadline > local) startSudoClock();
    else stopSudoClock();
    syncSudo();
  }

  function startSudoClock() {
    if (SUDO.timer) return;
    SUDO.timer = setInterval(function () {
      // running out goes through the same door as 退出: the server is told, so
      // a device whose clock jumped cannot leave an elevation behind it
      if (!sudoActive()) return quitSudo();
      paintSudoClock();
    }, 1000);
  }
  function stopSudoClock() {
    if (SUDO.timer) {
      clearInterval(SUDO.timer);
      SUDO.timer = null;
    }
  }

  function paintSudoClock() {
    var left = sudoLeft();
    var el = $("#sudoClock");
    if (el) {
      el.textContent = "sudo 模式将于 " + clockText(left) + " 后自动退出。";
      // the last three seconds blink, then the mode ends on its own
      el.classList.toggle("blink", left <= 3);
    }
  }

  function sudoChipEl() { return $("#sudoChip"); }

  $("#sudoChip").addEventListener("click", function () {
    if (SUDO.pop) closeSudoPop();
    else openSudoPop();
  });

  // The mark sits in the title bar; the class on the shell grows it into place,
  // which pushes the product name over instead of covering it.
  function syncSudo() {
    $("#shell").classList.toggle("sudo", sudoActive());
    if (!sudoActive()) closeSudoPop();
    paintSudoClock();
  }

  function openSudoPop() {
    closeSudoPop();
    var pop = document.createElement("div");
    pop.className = "sudo-pop";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "sudo 模式");
    pop.innerHTML =
      '<div class="sudo-pop-head">' + shieldMarkup() + "<h3>sudo 模式</h3></div>" +
      '<p class="sudo-pop-note">您正处于 sudo 模式，期间敏感操作不会再次要求二次确认。</p>' +
      '<p class="sudo-pop-note">再次进入 sudo 模式将会要求您输入密码。</p>' +
      '<p class="sudo-pop-clock" id="sudoClock">—</p>' +
      '<div class="sudo-pop-foot">' +
      '<button class="btn sm danger" type="button" id="sudoQuit">退出 sudo 模式</button>' +
      "</div>";
    document.body.appendChild(pop);

    var chip = sudoChipEl();
    var r = chip.getBoundingClientRect();
    var left = Math.min(Math.max(8, r.left), window.innerWidth - pop.offsetWidth - 8);
    var top = r.bottom + 8;
    if (top + pop.offsetHeight > window.innerHeight - 8) {
      top = Math.max(8, r.top - pop.offsetHeight - 8);
    }
    pop.style.left = left + "px";
    pop.style.top = top + "px";
    SUDO.pop = pop;
    chip.setAttribute("aria-expanded", "true");
    $("#sudoQuit").addEventListener("click", quitSudo);
    paintSudoClock();
  }

  function closeSudoPop() {
    if (SUDO.pop) {
      SUDO.pop.remove();
      SUDO.pop = null;
    }
    var chip = $("#sudoChip");
    if (chip) chip.setAttribute("aria-expanded", "false");
  }

  function quitSudo() {
    // Dropping the elevation locally is the half that always works; the server
    // is told as well so the record and the deadline agree with the screen.
    PX.api("/sudo/exit", { method: "POST" }).catch(function () { }).then(exitSudo);
  }

  function exitSudo() {
    stopSudoClock();
    SUDO.deadline = 0;
    closeSudoPop();
    paintSudoClock();
    var shell = $("#shell");
    // one last blink, then the chip fades and the title slides back
    shell.classList.add("sudo-out");
    setTimeout(function () {
      shell.classList.remove("sudo-out");
      shell.classList.remove("sudo");
    }, 320);
  }

  function askSudoPassword(what, note) {
    if (SUDO.asker) return SUDO.asker.promise;
    var cancel = null;
    var promise = new Promise(function (resolve, reject) {
      // which action opened the dialog is the only thing worth naming here
      var action = what ? "「" + PX.esc(what) + "」" : "";
      openOverlay("sudo", "sudo 模式",
        '<div class="sudo-prompt">' +
        '<span class="sudo-prompt-mark">' + shieldMarkup() + "</span>" +
        '<h2 class="sudo-prompt-title">sudo 模式</h2>' +
        '<p class="sudo-prompt-note">您正在进入 sudo 模式，十分钟内敏感操作将不需要认证</p>' +
        (action ? '<p class="sudo-prompt-action">' + action + "</p>" : "") +
        '<p class="sudo-prompt-desc">' + (note || "") + "</p>" +
        '<label class="sudo-prompt-label" for="sudoPass">当前账号密码</label>' +
        '<input id="sudoPass" type="password" autocomplete="current-password" ' +
        'maxlength="128">' +
        '<p class="err sudo-prompt-err" id="sudoErr"></p>' +
        '<div class="sudo-prompt-foot">' +
        '<button class="btn" type="button" id="sudoCancel">取消</button>' +
        '<button class="btn primary" type="button" id="sudoEnter">进入 sudo 模式</button>' +
        "</div></div>", null, true);
      var box = $("#sudoPass");
      if (box) {
        box.focus();
        // a fresh keystroke is the answer to whatever the last complaint was
        box.addEventListener("input", function () {
          var errEl = $("#sudoErr");
          if (errEl) errEl.textContent = "";
        });
      }

      var settle = function (ok, err) {
        SUDO.asker = null;
        if (ok) resolve(true);
        else reject(err);
      };
      // closeOverlay calls this: the x, the scrim and Escape all mean "no"
      cancel = function () {
        var err = new Error("已取消");
        err.silent = true;
        settle(false, err);
      };

      var submit = function () {
        var errEl = $("#sudoErr");
        var go = $("#sudoEnter");
        var pw = box ? box.value : "";
        if (!pw) {
          if (errEl) errEl.textContent = "请输入密码";
          return;
        }
        if (go) go.disabled = true;
        PX.api("/sudo", { method: "POST", body: { password: pw } })
          .then(function (d) {
            settle(true);        // first, so closing does not cancel it
            closeOverlay();
            setSudo(d.sudoUntil, d.now);
          })
          .catch(function (e) {
            if (go) go.disabled = false;
            if (errEl) errEl.textContent = e.message || "密码错误";
            if (box) {
              box.focus();
              box.select();
            }
          });
      };

      $("#sudoCancel").addEventListener("click", function () {
        cancel();
        closeOverlay();
      });
      $("#sudoEnter").addEventListener("click", submit);
      if (box) {
        box.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            submit();
          }
        });
      }
    });
    SUDO.asker = { promise: promise, cancel: cancel };
    return promise;
  }

  function abortSudoAsk() {
    var a = SUDO.asker;
    if (!a) return;
    SUDO.asker = null;
    a.cancel();
  }

  // Same contract as PX.api, except a "needs the password again" refusal is
  // answered with the password prompt and the request is repeated once.
  function apiSudo(path, options, what, note) {
    return PX.api(path, options).catch(function (e) {
      if (!e.sudo) throw e;
      return askSudoPassword(what, note).then(function () {
        return PX.api(path, options);
      });
    });
  }

  // Errors that only mean "the operator changed their mind" stay silent.
  function fail(e) {
    if (e && e.silent) return;
    if (e && e.data && e.data.conflict) {
      PX.notice("另一个会话刚刚改过这一项，本次更改没有生效", "warn");
      reloadAll().catch(function () {});
      return;
    }
    PX.toast(PX.esc((e && e.message) || "操作失败"), true);
  }

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
          ICON_PENCIL + "重命名活动</button>"
        : "") +
      '<button type="button" role="menuitem" data-copy="' + PX.esc(e.url) + '">' +
      ICON_COPY + "复制访问链接</button>" +
      '<button type="button" role="menuitem" data-save-preset="' + e.seq + '">' +
      ICON_STAR + "保存为预设</button>" +
      '<div class="menu-sep"></div>' +
      '<a role="menuitem" class="menu-link" href="' + PX.esc(e.admin) +
      '" target="_blank" rel="noopener">' + ICON_GEAR + "活动设置" +
      '<span class="menu-ext">' + ICON_EXT + "</span></a>" +
      (e.role === "owner"
        ? '<div class="menu-sep"></div>' +
          '<button type="button" role="menuitem" data-rotate="' + e.seq + '">' +
          ICON_REFRESH + "重置访问地址</button>" +
          '<button type="button" role="menuitem" class="danger" ' +
          'data-archive="' + e.seq + '">' + ICON_ARCHIVE + "归档</button>"
        : "");
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

  function confirmBan(id) {
    var a = S.accounts.filter(function (x) { return x.id === id; })[0];
    if (!a) return;
    openOverlay("ban", "封禁 " + displayName(a),
      '<div class="field"><label for="banReason">原因</label>'
      + '<input id="banReason" type="text" maxlength="120" autocomplete="off"'
      + ' placeholder="对方登录时看得到这句话"></div>'
      + '<div class="row" style="margin-top:16px;justify-content:flex-end">'
      + '<button class="btn" type="button" data-close-overlay="1">取消</button>'
      + '<button class="btn danger" type="button" data-ban-confirm="' + a.id + '">'
      + "封禁</button></div>");
    var box = $("#banReason");
    if (box) box.focus();
  }

  function doBan(id) {
    var box = $("#banReason");
    apiSudo("/account/decide", {
      method: "POST",
      body: { id: Number(id), action: "ban", reason: box ? box.value.trim() : "" }
    }, "封禁账号").then(function () {
      closeOverlay();
      PX.toast("已封禁");
      return reloadAll();
    }).catch(fail);
  }

  // High-risk actions keep one light confirmation even while elevated: the
  // password proves who is acting, not that this particular click was meant.
  // While not elevated the password prompt is that confirmation, so this is
  // skipped - typing it is already deliberate, and cancelling it leaves no
  // trace because nothing ever reached the server.
  // opts.noSudo：这个动作不要密码（归档、转移自己的东西就是这种），没有密码提示
  // 可以充当确认，所以这一句确认总要有。opts.keepUnder：确认框叠在详情上面，
  // 取消只回到详情。
  function confirmHighRisk(title, note, label, danger, run, opts) {
    if (!(opts && opts.noSudo) && !sudoActive()) return run(note);
    openOverlay("confirm", title,
      '<p class="page-sub">' + note + "</p>"
      + '<div class="row" style="margin-top:18px;justify-content:flex-end">'
      + '<button class="btn" type="button" data-close-overlay="1">取消</button>'
      + '<button class="btn ' + (danger ? "danger" : "primary") + '" type="button" '
      + 'id="confirmGo">' + label + "</button></div>", null,
      !!(opts && opts.keepUnder));
    $("#confirmGo").addEventListener("click", function () {
      // 从详情弹窗点开的动作要把详情留着（取消 sudo 之后还得回到那儿）；
      // 其它的确认框收掉整摞，回到页面
      if (opts && opts.keepUnder) closeOverlay();
      else closeAllOverlays();
      run();
    });
  }

  /* 归档：把名单或报名收起来。自己的东西只要一句确认（不设密码，超管能放回来）；
     替别人做是提权操作，那一步照样要密码。名单上还挂着报名时先问一句「一并归档？」，
     确认之后一起收，不拿「先去处理那几个报名」去拦人 */
  function archiveList(kind, id, onDone) {
    var what = kind === "roster" ? "名单" : "报名";
    function send(cascade) {
      return apiSudo("/" + kind + "/archive",
                     { method: "POST", body: { id: Number(id), cascade: !!cascade } },
                     "归档" + what);
    }
    send(false).then(function (d) { onDone(d || {}); }).catch(function (e) {
      var linked = e && e.data && e.data.linkedSignups;
      if (!linked) { fail(e); return; }
      askCascadeArchive(what, linked, function () {
        send(true).then(function (d) { onDone(d || {}); }).catch(fail);
      });
    });
  }

  /* 「还有 N 个报名链接到这份名单。一并归档？」——取消就什么都不做 */
  function askCascadeArchive(what, count, run) {
    openOverlay("confirm", "归档" + what,
      '<p class="page-sub">还有 ' + count + " 个报名链接到这份" + what +
      "。一并归档？</p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="cascadeGo">全部归档</button></div>',
      null, true);
    $("#cascadeGo").addEventListener("click", function () {
      closeOverlay();
      run();
    });
  }

  /* 站内资源回收：把这场活动占着的东西（目前是短链）由超管显式收回来 */
  function reclaimEvent(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    closeCardMenu();
    confirmHighRisk("回收站内资源",
      "确认回收？" + (e ? "<br>活动：" + PX.esc(eventTitle(e)) : ""),
      "回收", true,
      function (note) {
        apiSudo("/event/reclaim", { method: "POST", body: { seq: Number(seq) } },
          "回收站内资源", note)
          .then(function (d) {
            // 回收不动活动本身，详情照旧留着
            PX.toast("已回收 " + ((d && d.shortlinks) || 0) + " 条短链");
            return reloadAll();
          })
          .catch(fail);
      }, { keepUnder: true });
  }

  /* 已归档活动的三个提权动作：下载完整数据、取消归档、彻底删除 */
  /* 活动详情弹窗：活动管理页里点活动名打开，高权限动作都在这里 */
  function openEventDetail(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0] ||
            S.archived.filter(function (x) { return String(x.seq) === String(seq); })[0];
    if (!e) return;
    var archived = !!e.archivedAt;
    var rows = [
      ["活动号", String(e.seq)],
      ["token", e.id],
      ["负责人", e.ownerMissing ? "账号已删除" : (e.ownerName || "—")],
      ["模块", e.kind || "—"],
      [archived ? "归档时间" : "创建时间",
       archived ? PX.whenExact(e.archivedAt) : PX.when(e.createdAt)]
    ];
    var acts = [];
    if (!archived) {
      acts.push({ text: "打开活动设置", icon: ICON_EXT, kind: "ghost", primary: true,
                  run: function () { window.open(e.admin, "_blank"); } });
    }
    if (archived) {
      acts.push({ text: "下载完整数据", kind: "", run: function () { archivedDownload(seq); } });
      acts.push({ text: "取消归档", kind: "", run: function () { archivedRestore(seq); } });
      acts.push({ text: "彻底删除", kind: "danger", run: function () { archivedDelete(seq); } });
    } else {
      acts.push({ text: "下载数据", kind: "", run: function () { archivedDownload(seq); } });
      acts.push({ text: "转移负责人", kind: "", run: function () { transferOwner(seq); } });
      acts.push({ text: "回收站内资源", kind: "", run: function () { reclaimEvent(seq); } });
      acts.push({ text: "强制归档", kind: "danger", run: function () { forceArchive(seq); } });
    }
    // 「打开活动设置」是无框的链接式按钮，和别的按钮挤在一行会把它们推歪：单独一行
    var head = acts.filter(function (a) { return a.primary; });
    var rest = acts.filter(function (a) { return !a.primary; });
    function buttons(list, offset) {
      return list.map(function (a, i) {
        // 带图标的动作（打开活动设置、打开）把图标放在文字后面，尺寸交给 CSS
        return '<button class="btn ' + a.kind + (a.icon ? " btn-icon" : "") +
          '" type="button" data-detail-act="' + (i + offset) + '">' +
          PX.esc(a.text) + (a.icon || "") + "</button>";
      }).join("");
    }
    var html = '<dl class="kv">' + rows.map(function (r) {
      return "<dt>" + PX.esc(r[0]) + "</dt><dd>" + PX.esc(r[1]) + "</dd>";
    }).join("") + "</dl>" +
      (head.length ? '<div class="detail-actions detail-actions-top">' +
        buttons(head, 0) + "</div>" : "") +
      '<div class="detail-actions">' + buttons(rest, head.length) + "</div>";
    openOverlay("event-detail", eventTitle(e), html, function (card) {
      card.querySelectorAll("[data-detail-act]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var act = acts[Number(btn.getAttribute("data-detail-act"))];
          // 只有把对象挪走或抹掉的动作（归档、强制归档、取消归档、彻底删除）做完才收弹窗；
          // 转移、回收、下载、打开都不动它，详情留在原处
          if (act) act.run();
        });
      });
    });
  }

  /* 转移负责人：负责人自己能做；负责人不在时由超管提权代做。
     这里选人是单选——点谁就是谁，再问一句才真的换过去。 */
  function transferOwner(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    pickPeople({
      title: "转移负责人" + (e ? "：" + eventTitle(e) : ""),
      mode: "single",
      scene: "transfer",
      seq: Number(seq),
      exclude: [S.me && S.me.id],
      empty: "没有匹配的账号",
      apply: function (people) {
        var who = people[0];
        if (!who) return;
        // 转移不算把活动挪走：确认框叠在详情上面，点取消只回到详情，
        // 换完之后详情也照旧留着（它这会儿显示的负责人是旧的，刷新页面才会变）。
        // 这一步的确认是动作本身要的：负责人自己转不用密码，没有那道密码提示
        openOverlay("event-transfer", "转移负责人" + (e ? "：" + eventTitle(e) : ""),
          '<p class="page-sub">把这场的负责人换成 ' + PX.esc(who.label) + "？" +
          "换过之后你不再是它的负责人。</p>" +
          '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
          '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
          '<button class="btn danger" type="button" id="transferGo">转移</button></div>',
          null, true);
        $("#transferGo").addEventListener("click", function () {
          closeOverlay();
          apiSudo("/event/transfer",
            { method: "POST", body: { seq: Number(seq), uid: who.uid } }, "转移负责人")
            .then(function () {
              PX.toast("已转移给 " + who.label);
              return reloadAll();
            })
            .catch(fail);
        });
      }
    });
  }

  /* 别人的活动：强制归档也是破坏性操作，只在活动管理页出现 */
  function forceArchive(seq) {
    confirmHighRisk("强制归档", "确认继续？", "强制归档", true, function (note) {
      apiSudo("/event/force_archive", { method: "POST", body: { seq: Number(seq) } },
        "强制归档活动", note)
        .then(function () {
          // 归档把活动挪到「已归档的活动」那张表：详情留着就没对象了
          closeAllOverlays();
          PX.toast("已归档");
          return reloadAll();
        })
        .catch(fail);
    }, { keepUnder: true });
  }

  function archivedDownload(seq) {
    var w = window.open((PX.panelRoot || "") + "/api/panel/event/download?seq=" + seq,
      "_blank");
    if (w) PX.toast("正在准备下载");
  }

  function archivedRestore(seq) {
    var e = (S.archived || []).filter(function (x) {
      return String(x.seq) === String(seq);
    })[0] || {};
    if (e.ownerMissing) {
      // 负责人账号已经被彻底删了：取消归档要顺带指定一个新的负责人。
      // 归档的活动不拦着删人，所以这种活动一定存在，得有个说法
      return pickPeople({
        title: "取消归档：先指定负责人",
        mode: "single",
        apply: function (people) {
          var who = people[0];
          if (!who) return;
          confirmHighRisk("取消归档",
            "这场活动的负责人账号已不存在。取消归档并交给 " + PX.esc(who.label) + "？",
            "取消归档", false, function (note) {
              apiSudo("/event/unarchive",
                { method: "POST", body: { seq: Number(seq), owner: who.uid } },
                "取消归档", note)
                .then(function () {
                  closeAllOverlays();
                  PX.toast("已取消归档，负责人是 " + who.label);
                  return reloadAll();
                })
                .catch(fail);
            }, { keepUnder: true });
        }
      });
    }
    confirmHighRisk("取消归档", "确认取消归档？",
      "取消归档", false, function (note) {
        apiSudo("/event/unarchive", { method: "POST", body: { seq: Number(seq) } },
          "取消归档", note)
          .then(function () {
            closeAllOverlays();
            PX.toast("已取消归档");
            return reloadAll();
          })
          .catch(fail);
      }, { keepUnder: true });
  }

  function archivedDelete(seq) {
    confirmHighRisk("彻底删除归档的活动",
      "删除后不可恢复。确认继续？",
      "彻底删除", true,
      function (note) {
        apiSudo("/event/delete", { method: "POST", body: { seq: Number(seq) } },
          "彻底删除归档的活动", note)
          .then(function () {
            closeAllOverlays();
            PX.toast("已删除");
            return loadArchived(true);
          })
          .catch(function (e) {
            if (e && e.status === 404) {
              // 别处已经删掉了：收掉详情、刷新列表，别让人对着一条不存在的记录再点
              closeAllOverlays();
              PX.toast("这场活动已被删除", true);
              return loadArchived(true);
            }
            fail(e);
          });
      }, { keepUnder: true });
  }

  function rotateEvent(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    closeCardMenu();
    confirmHighRisk("重置访问地址",
      "确认重新生成？" + (e ? "<br>活动：" + PX.esc(eventTitle(e)) : ""),
      "重新生成", false,
      function (note) {
        apiSudo("/event/rotate", { method: "POST", body: { seq: Number(seq) } },
          "重置访问地址", note)
          .then(function () {
            PX.toast("已生成新的访问地址");
            return reloadAll();
          })
          .catch(fail);
      });
  }

  function archiveEvent(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    closeCardMenu();
    // 归档自己的活动不要密码（收起来的东西超管能放回来），一句确认就够；
    // 真正不可逆的是「彻底删除」，那一步才提权
    confirmHighRisk("归档活动",
      "确认归档？"
      + (e ? "<br>存档：" + PX.esc(eventTitle(e)) : ""),
      "归档", true,
      function () {
        apiSudo("/event/archive", { method: "POST", body: { seq: Number(seq) } },
          "归档活动")
          .then(function () {
            PX.toast("已归档");
            return reloadAll();
          })
          .catch(fail);
      }, { noSudo: true });
  }

  function decideInvite(seq, action) {
    PX.api("/invite", { method: "POST", body: { seq: Number(seq), action: action } })
      .then(function () {
        PX.toast(action === "accept" ? "已加入活动" : "已拒绝邀请");
        return reloadAll();
      })
      .catch(fail);
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
  /* 角色过滤之后，空的节会留下两条相邻的分隔线（看起来更粗）与一段空白：
     规则是——节里没有任何可见项，就连同它后面的分隔线一起去掉。 */
  function tidyNavSeparators() {
    var scroll = document.querySelector("#sidebar .nav-scroll");
    if (!scroll) return;
    var kids = Array.prototype.slice.call(scroll.children);
    var seenItem = false;
    var pending = [];
    kids.forEach(function (el) {
      var visible = el.offsetParent !== null;
      if (el.classList.contains("nav-sep")) {
        if (visible) pending.push(el);
        return;
      }
      if (!visible) return;
      // 这一段里出现了可见项：之前攒下的分隔线才有意义
      pending.forEach(function (sep) { sep.hidden = !seenItem; });
      pending = [];
      seenItem = true;
    });
    // 末尾的分隔线后面什么都没有：藏掉
    pending.forEach(function (sep) { sep.hidden = true; });
    // 再收一遍：连续两条可见分隔线只留一条
    var lastWasSep = false;
    Array.prototype.forEach.call(scroll.children, function (el) {
      if (el.offsetParent === null) return;
      if (el.classList.contains("nav-sep")) {
        if (lastWasSep) el.hidden = true;
        lastWasSep = true;
        return;
      }
      lastWasSep = false;
    });
  }

  function applyRoleVisibility() {
    $$("[data-min-role]").forEach(function (el) {
      el.hidden = myRank() < rank(el.getAttribute("data-min-role"));
    });
    $("#hostStats").hidden = !isOwner() || !S.host;
    // 批量建号只给站长：超管连这颗键都看不见
    if ($("#importAccountBtn")) $("#importAccountBtn").hidden = !isOwner();
  }

  // The rail shows icons only, so every entry needs a hover title.
  /* 卡片标题右边的圆圈问号：新标签页打开对应的文档 */
  var DOCS_HREF = PX.PREFIX + "/docs#/";

  /* 滚动条只在指针靠近容器右边时浮现：进到右缘 28px 以内才算 */
  var NEAR_BAND = 28;
  var nearTimers = new WeakMap();

  function watchNearScroll(el) {
    if (!el || nearTimers.has(el)) return;
    nearTimers.set(el, null);
    el.addEventListener("pointermove", function (ev) {
      if (ev.pointerType === "touch") return;
      var rect = el.getBoundingClientRect();
      var near = rect.right - ev.clientX <= NEAR_BAND;
      el.classList.toggle("near-scroll", near);
      clearTimeout(nearTimers.get(el));
      if (near) {
        nearTimers.set(el, setTimeout(function () {
          el.classList.remove("near-scroll");
        }, 1200));
      }
    }, { passive: true });
    el.addEventListener("pointerleave", function () {
      clearTimeout(nearTimers.get(el));
      el.classList.remove("near-scroll");
    }, { passive: true });
  }

  function watchScrollbars() {
    [".content", ".nav-scroll"].forEach(function (sel) { watchNearScroll($(sel)); });
    $$(".tblwrap").forEach(watchNearScroll);
  }

  /* 侧栏还有没有"往上的内容 / 往下的内容"：靠它决定哪一侧加淡出 */
  function syncNavFade() {
    var box = $(".nav-scroll");
    if (!box) return;
    var room = box.scrollHeight - box.clientHeight;
    box.classList.toggle("fade-top", box.scrollTop > 2);
    box.classList.toggle("fade-bottom", room > 2 && box.scrollTop < room - 2);
  }

  function watchNavFade() {
    var box = $(".nav-scroll");
    if (!box) return;
    box.addEventListener("scroll", syncNavFade, { passive: true });
    window.addEventListener("resize", syncNavFade);
    if (window.ResizeObserver) {
      new ResizeObserver(syncNavFade).observe(box);
    }
    syncNavFade();
  }

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
  // 遮罩是侧栏和会话抽屉共用的：两个都收干净了才拿掉
  function syncScrim() {
    var shell = $("#shell");
    $("#scrim").hidden = !(shell.classList.contains("nav-open") ||
                           shell.classList.contains("as-drawer-open"));
  }
  function openPane() {
    $("#shell").classList.add("nav-open");
    $("#menuBtn").setAttribute("aria-expanded", "true");
    syncScrim();
  }
  function closePane() {
    $("#shell").classList.remove("nav-open");
    $("#menuBtn").setAttribute("aria-expanded", "false");
    syncScrim();
  }

  /* 侧栏用方向键走：整栏只占一个 Tab 位，上下键在可见项之间移动
     （展开的分组子项也在序列里），Home/End 到首尾。 */
  function bindSidebarKeys() {
    var bar = $("#sidebar");
    if (!bar) return;

    function items() {
      return Array.prototype.filter.call(
        bar.querySelectorAll('[data-nav], [data-toggle]'),
        function (el) {
          if (el.closest("[inert]")) return false;
          var parent = el.closest(".nav-sub");
          if (parent && parent.hidden) return false;
          return el.offsetParent !== null;
        });
    }

    function rove(target) {
      var list = items();
      if (!list.length) return;
      list.forEach(function (el) { el.tabIndex = -1; });
      (target || list[0]).tabIndex = 0;
    }

    bar.addEventListener("keydown", function (e) {
      var key = e.key;
      if (["ArrowDown", "ArrowUp", "Home", "End"].indexOf(key) < 0) return;
      var list = items();
      if (!list.length) return;
      var here = document.activeElement;
      var idx = list.indexOf(here);
      var next;
      if (key === "Home") next = 0;
      else if (key === "End") next = list.length - 1;
      else if (idx < 0) next = 0;
      else next = (idx + (key === "ArrowDown" ? 1 : -1) + list.length) % list.length;
      e.preventDefault();
      list[next].focus();
    });

    // 焦点落进侧栏时，把唯一的 Tab 位挪到它身上
    bar.addEventListener("focusin", function (e) {
      if (e.target.getAttribute && (e.target.hasAttribute("data-nav") ||
                                    e.target.hasAttribute("data-toggle"))) {
        rove(e.target);
      }
    });
    rove(null);
  }

  /* ------------------------------------------------------------------ 通知 --- */
  /* 右上角一个铃铛：有未读就亮红点，点开是列表。紧急级未读一上线就全屏弹出，
   * 只能点"已读 / 关闭"结束——点周围不消失。 */
  /* 服务有效性：服务端按一次读数推出来的提示（占用偏高、盘快写不进去了）。
     只读、不进库、不发指令；条件消失，提示跟着消失。 */
  var SVC_MUTE_KEY = "xl.svc.muted";

  function loadSvcMuted() {
    try {
      (localStorage.getItem(SVC_MUTE_KEY) || "").split(",").forEach(function (id) {
        if (id) S.svcMuted[id] = true;
      });
    } catch (e) { /* 隐私模式：只在这一次里记住 */ }
  }

  function servicesShown() {
    return (S.service || []).filter(function (it) { return !S.svcMuted[it.id]; });
  }

  /* 常驻的那几条可以单独关掉；紧急级不给关——写不进去这件事不该被静音 */
  function muteService(id) {
    S.svcMuted[id] = true;
    try {
      localStorage.setItem(SVC_MUTE_KEY, Object.keys(S.svcMuted).join(","));
    } catch (e) { /* 同上 */ }
    drawBell();
    drawNoticeList();
  }

  function loadService() {
    return PX.api("/service")
      .then(function (d) {
        S.service = (d && d.items) || [];
        drawBell();
        drawNoticeList();
      })
      .catch(function () { /* 读不到就不提示，不自己造一条 */ });
  }

  function drawServiceRow(it) {
    var mute = it.sticky && it.level !== "critical"
      ? '<button class="notice-mute" type="button" data-svc-mute="' + PX.esc(it.id) +
        '">不再提示</button>'
      : "";
    return '<div class="notice-item svc ' + PX.esc(it.level || "info") + '">' +
      '<span class="notice-meta">' + noticeIcon(it.level) +
      "<span>服务</span>" + mute + "</span>" +
      '<span class="notice-title">' + PX.esc(it.title) + "</span>" +
      (it.body ? '<span class="notice-body">' + PX.esc(it.body) + "</span>" : "") +
      "</div>";
  }

  function loadNotices() {
    markLoading("notices");
    return PX.api("/notices")
      .then(function (d) {
        S.notices = d.notices || [];
        S.noticeUnread = d.unread || 0;
        markLoaded("notices");
        drawBell();
        drawNoticeList();
        popVersionNotice();
        maybeShowAlert();
      })
      .catch(function () { /* 通知读不到不该影响主流程 */ });
  }

  /* 版本更新那条：第一次看到时弹一条，几秒后自己收起；
     通知本身留在通知中心里，之后再打开页面不会再弹。 */
  var NOTICE_SEEN = "xl.notices.popped";

  function seenNotices() {
    try {
      return JSON.parse(localStorage.getItem(NOTICE_SEEN) || "[]") || [];
    } catch (e) {
      return [];
    }
  }

  function popVersionNotice() {
    var seen = seenNotices();
    var fresh = (S.notices || []).filter(function (n) {
      return n.object === "version" && seen.indexOf(n.id) < 0;
    });
    if (!fresh.length) return;
    fresh.forEach(function (n) {
      PX.toast(PX.esc(n.title));
      seen.push(n.id);
    });
    try {
      localStorage.setItem(NOTICE_SEEN, JSON.stringify(seen.slice(-50)));
    } catch (e) { /* 存不下就下次再弹，不影响别的 */ }
  }

  function drawBell() {
    var dot = $("#bellDot");
    var svc = servicesShown().length;
    /* 红点只表示"有未读通知"：服务提示是当下的状态，读完通知也还在，
       拿它挡着红点会让人以为没读完。它换成铃铛本身着色。 */
    if (dot) dot.hidden = !S.noticeUnread;
    var btn = $("#bellBtn");
    if (btn) {
      btn.classList.toggle("has-svc", svc > 0);
      var bits = [];
      if (S.noticeUnread) bits.push(S.noticeUnread + " 条未读");
      if (svc) bits.push(svc + " 条服务提示");
      btn.setAttribute("aria-label",
        bits.length ? "通知（" + bits.join("，") + "）" : "通知");
    }
  }

  /* 通知的等级：一枚小图标就够了，不必再写一枚"通知/提示"胶囊——谁都知道那是通知 */
  var NOTICE_ICON = {
    info: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.6"/>' +
          '<path d="M10 9.2v4.4M10 6.6v.1"/></svg>',
    warn: '<svg viewBox="0 0 20 20" aria-hidden="true">' +
          '<path d="M10 3.2l7.4 12.8H2.6z"/><path d="M10 8.2v3.4M10 14v.1"/></svg>',
    bad: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.6"/>' +
         '<path d="M10 6.2v4.8M10 13.8v.1"/></svg>',
    success: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.6"/>' +
             '<path d="M6.6 10.2l2.4 2.3 4.4-4.6"/></svg>'
  };

  function noticeIcon(level) {
    var svg = NOTICE_ICON[level] || NOTICE_ICON.info;
    return '<span class="notice-ico ' + PX.esc(level || "info") +
      '" aria-hidden="true">' + svg + "</span>";
  }

  function drawNoticeList() {
    var box = $("#noticeList");
    if (!box) return;
    var foot = $("#noticeFoot");
    if (foot) foot.hidden = !S.notices.some(function (n) { return n.unread; });
    var svc = servicesShown();
    if (!S.notices.length && !svc.length) {
      box.innerHTML = '<p class="notice-empty">' +
        (loaded("notices") ? "还没有通知" : "正在读取…") + "</p>";
      return;
    }
    box.innerHTML = svc.map(drawServiceRow).join("") +
      S.notices.map(function (n) {
      return '<button class="notice-item ' + PX.esc(n.level) +
        (n.unread ? " unread" : "") + '" type="button" data-notice="' + n.id +
        '" data-object="' + PX.esc(n.object || "") + '">' +
        '<span class="notice-meta">' + noticeIcon(n.level) +
        "<span>" + PX.esc(PX.whenExact(n.ts)) + "</span></span>" +
        '<span class="notice-title">' + PX.esc(n.title) + "</span>" +
        (n.body ? '<span class="notice-body">' + PX.esc(n.body) + "</span>" : "") +
        "</button>";
    }).join("");
  }

  function markNotices(ids) {
    PX.api("/notices/read", { method: "POST", body: { ids: ids || [] } })
      .then(function () { return loadNotices(); })
      .catch(function () {});
  }

  /* 紧急级：一次弹一条，关掉一条再看下一条 */
  function maybeShowAlert() {
    if (S.alertOpen) return;
    var next = null;
    for (var i = 0; i < S.notices.length; i += 1) {
      if (S.notices[i].level === "critical" && S.notices[i].unread) {
        next = S.notices[i];
        break;
      }
    }
    var back = $("#alertBack");
    if (!back) return;
    if (!next) {
      back.hidden = true;
      S.alertOpen = false;
      return;
    }
    S.alertOpen = next.id;
    $("#alertTitle").textContent = next.title;
    $("#alertBody").textContent = next.body || "";
    back.hidden = false;
    var ok = $("#alertRead");
    if (ok) ok.focus();
  }

  function closeAlert(mark) {
    var id = S.alertOpen;
    S.alertOpen = false;
    $("#alertBack").hidden = true;
    if (mark && id) markNotices([id]);
  }

  function noticePanelOpen() {
    var panel = $("#noticePanel");
    return !!panel && panel.classList.contains("open");
  }

  /* 收起时不是 display:none，而是往顶栏底下缩回去（见 CSS）：
     这样才有滑入/滑出的过程，也不再需要 hidden 来挡点击 */
  function openNoticePanel(open) {
    var panel = $("#noticePanel");
    var btn = $("#bellBtn");
    if (!panel) return;
    var show = open === undefined ? !noticePanelOpen() : !!open;
    panel.classList.toggle("open", show);
    panel.setAttribute("aria-hidden", show ? "false" : "true");
    if (btn) btn.setAttribute("aria-expanded", show ? "true" : "false");
    if (show) { loadNotices(); loadService(); }
  }

  /* --------------------------------------------------------------- 名单 --- */
  /* 名单与活动并列：名单是人的集合，活动是名单的一次使用。名单存在自己的库里，
   * 这里只管"谁看得见、谁能改"和成员本身；报名（让名单自己长出来的入口）另起一块。
   * 看得见 ≠ 改得动：别人的名单可以打开，但按钮只在创建者那边出现。 */
  var ROSTER_PAGE = 30;
  var ENTRY_PAGE = 100;

  function loadRosters(more) {
    if (more) {
      if (S.rosterTotal !== null && S.rosters.length >= S.rosterTotal) {
        return Promise.resolve();
      }
    } else {
      S.rosters = [];
      S.rosterTotal = null;
      markLoading("rosters");
    }
    var parts = ["/rosters?scope=" + (S.rosterScope || "mine"),
                 "offset=" + (more ? S.rosters.length : 0),
                 "limit=" + ROSTER_PAGE];
    if (S.rosterFilter) parts.push("q=" + encodeURIComponent(S.rosterFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.rosters = (more ? S.rosters : []).concat(d.rosters || []);
        S.rosterTotal = d.total || 0;
        markLoaded("rosters");
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function drawRosters() {
    var body = $("#rosterTable tbody");
    if (!body) return;
    var rows = S.rosters || [];
    body.innerHTML = rows.length ? rows.map(function (r) {
      return "<tr>" +
        '<td><a href="#/roster/' + r.id + '"><b>' + PX.esc(r.name) + "</b></a>" +
        (r.note ? '<div class="hint">' + PX.esc(r.note) + "</div>" : "") +
        (r.mine ? "" : '<div class="hint">由 ' + PX.esc(r.ownerName || "—") +
          " 创建 · 当前权限：" + PX.esc(r.levelLabel) + "</div>") +
        "</td>" +
        '<td class="right num">' + r.members + "</td>" +
        "<td>" + PX.esc(r.visibilityLabel) + "</td>" +
        '<td class="mono">' + PX.when(r.updatedAt) + "</td>" +
        '<td class="actions"><button class="btn sm" type="button" data-open-roster="' +
        r.id + '">打开</button></td></tr>';
    }).join("") : rowsOrEmpty(5, "rosters", "还没有名单");

    var count = $("#rosterCount");
    if (count) {
      count.textContent = S.rosterTotal === null ? "" : "共 " + S.rosterTotal + " 份";
    }
    var more = $("#rosterMore");
    if (more) {
      var left = (S.rosterTotal === null) ? 0 : S.rosterTotal - rows.length;
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "再显示 " + Math.min(ROSTER_PAGE, left) + " 份" : "";
    }
    var box = $("#rosterScopeBox");
    if (box) {
      Array.prototype.forEach.call(box.querySelectorAll("[data-scope]"), function (b) {
        var on = b.getAttribute("data-scope") === (S.rosterScope || "joined");
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
    }
  }

  function loadRoster(id) {
    return PX.api("/roster?id=" + id)
      .then(function (d) {
        S.roster = d.roster;
      })
      .catch(function (e) {
        PX.toast(PX.esc(e.message), true);
        S.roster = null;
      });
  }

  function loadEntries(more) {
    if (!S.roster) return Promise.resolve();
    if (more) {
      if (S.entryTotal !== null && S.rosterEntries.length >= S.entryTotal) {
        return Promise.resolve();
      }
    } else {
      S.rosterEntries = [];
      S.entryTotal = null;
      markLoading("rosterEntries");
    }
    var parts = ["/roster/entries?id=" + S.roster.id,
                 "offset=" + (more ? S.rosterEntries.length : 0),
                 "limit=" + ENTRY_PAGE];
    if (S.entryFilter) parts.push("q=" + encodeURIComponent(S.entryFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.rosterEntries = (more ? S.rosterEntries : []).concat(d.entries || []);
        S.entryTotal = d.total || 0;
        markLoaded("rosterEntries");
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function drawRoster() {
    var r = S.roster;
    var head = $("#rosterTitle");
    if (!r) {
      if (head) head.textContent = "名单";
      var sub = $("#rosterSub");
      if (sub) sub.textContent = "这份名单打不开";
      if ($("#rosterEntryTable tbody")) $("#rosterEntryTable tbody").innerHTML = "";
      return;
    }
    head.textContent = r.name;
    $("#rosterSub").textContent = (r.note ? r.note + " · " : "") +
      (r.owner ? "拥有者：你" : "拥有者 " + (r.ownerName || "—")) +
      " · " + r.visibilityLabel +
      (r.owner ? "" : " · 当前权限：" + r.levelLabel) +
      (r.visibility === "org" ? "（组织尚未建立，暂按公开处理）" : "");
    document.title = r.name + " · CAEventManager";
    $("#rosterReadOnly").hidden = !!r.editable;
    var addBtn = $("#rosterAddBtn");
    if (addBtn) addBtn.hidden = !r.editable;
    $("#rosterMergeBtn").hidden = !r.editable;
    $("#rosterEditBtn").hidden = !r.owner;
    $("#rosterMemberCount").textContent = (S.entryTotal === null ? "" : "共 " + S.entryTotal + " 人") +
      (S.entryFilter ? "（已筛选）" : "");
    var note = $("#rosterAskNote");
    if (note) {
      note.textContent = r.askNote || "";
      note.hidden = !r.askNote;
    }
    drawSharing();
    drawEntries();
  }

  /* 写入权限：拥有者可以邀请与移除，看得见的人可以申请。
   * 名单设置里的"写入权限模式"决定申请是立刻生效还是要拥有者点头。 */
  function drawSharing() {
    var box = $("#rosterShareBody");
    var hint = $("#rosterShareHint");
    var r = S.roster;
    if (!box || !r) return;
    if (hint) {
      hint.textContent = r.owner ? "模式：" + r.grantModeLabel : "";
    }
    var html = '<div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:10px">' +
      '<span class="tag owner">拥有者 ' + PX.esc(r.ownerName || "—") + "</span>";
    (r.editors || []).forEach(function (e) {
      html += '<span class="tag">' + PX.esc(e.name || ("#" + e.accountId)) +
        (r.owner ? ' <button class="tag-x" type="button" data-revoke="' +
          e.accountId + '" aria-label="移除写入权限">×</button>' : "") + "</span>";
    });
    if (!(r.editors || []).length) {
      html += '<span class="hint">除拥有者外暂无其他账号</span>';
    }
    html += "</div>";
    if (r.editable) {
      html += '<div class="row" style="gap:8px">' +
        (r.owner
          ? '<button class="btn sm" type="button" id="rosterGrantBtn">授予写入权限</button>'
          : "") +
        '<a class="btn sm" href="#/write-approvals?back=' +
        encodeURIComponent("/roster/" + r.id) + '">写入审批</a></div>';
    } else if (r.canAsk) {
      html += '<div class="row"><button class="btn primary" type="button" ' +
        'id="rosterAskBtn">申请写入权限</button>' +
        '<span class="hint">' +
        (r.askNote ? "重新申请需拥有者确认"
          : (r.grantMode === "auto" ? "申请后立即生效" : "需要拥有者确认后才生效")) +
        "</span></div>";
    } else if (r.level === "pending") {
      html += '<p class="note">已提交申请，等待确认。</p>';
    }
    if (r.canApprove && (r.pending || []).length) {
      html += '<div style="margin-top:12px"><p class="hint">待确认的申请</p>' +
        r.pending.map(function (e) {
          return '<div class="row" style="gap:8px;margin-top:6px">' +
            "<span>" + PX.esc(e.name || ("#" + e.accountId)) + "</span>" +
            '<button class="btn sm primary" type="button" data-ask-ok="' +
            e.accountId + '">同意</button>' +
            '<button class="btn sm" type="button" data-ask-no="' +
            e.accountId + '">拒绝</button></div>';
        }).join("") + "</div>";
    }
    box.innerHTML = html;
  }

  function rosterGrant(people) {
    if (!people || !people.length) return;
    PX.api("/roster/grant", {
      method: "POST",
      body: { id: S.roster.id, uids: people.map(function (p) { return p.uid; }) }
    })
      .then(function () {
        PX.toast("已授予写入权限");
        return loadRoster(S.roster.id).then(drawRoster);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  /* 授予写入权限：与邀请成员同一个弹窗，多选，一次给几个人都行 */
  function rosterGrantPicker() {
    var r = S.roster || {};
    pickPeople({
      title: "授予写入权限" + (r.name ? "：" + r.name : ""),
      mode: "multi",
      scene: "roster",
      rosterId: r.id,
      exclude: [S.me && S.me.id],
      empty: "没有匹配的账号",
      apply: rosterGrant
    });
  }

  function rosterAsk() {
    PX.api("/roster/ask", { method: "POST", body: { id: S.roster.id } })
      .then(function (d) {
        PX.toast(d.level === "write" ? "已获得写入权限" : "已提交申请，等待确认");
        loadApprovals();
        return loadRoster(S.roster.id).then(function () {
          return loadEntries(false);
        }).then(drawRoster);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function rosterDecide(accountId, action) {
    PX.api("/roster/grant/decide", { method: "POST",
      body: { id: S.roster.id, accountId: accountId, action: action } })
      .then(function () {
        PX.toast(action === "approve" ? "已同意" : "已拒绝");
        return loadRoster(S.roster.id).then(drawRoster);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function rosterRevoke(accountId) {
    PX.api("/roster/grant/revoke", { method: "POST",
      body: { id: S.roster.id, accountId: accountId } })
      .then(function () {
        PX.toast("已移除写入权限");
        return loadRoster(S.roster.id).then(drawRoster);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function drawEntries() {
    var body = $("#rosterEntryTable tbody");
    if (!body) return;
    var rows = S.rosterEntries || [];
    var editable = !!(S.roster && S.roster.editable);
    body.innerHTML = rows.length ? rows.map(function (e) {
      var gone = e.state === "left";
      return "<tr" + (gone ? ' class="is-muted"' : "") + ">" +
        '<td class="mono">' + PX.esc(e.sid) + "</td>" +
        "<td><b>" + PX.esc(e.name) + "</b>" +
        (e.note ? '<div class="hint">' + PX.esc(e.note) + "</div>" : "") + "</td>" +
        "<td>" + PX.esc(e.dept || "—") + "</td>" +
        "<td>" + PX.esc(e.grp || "—") + "</td>" +
        "<td>" + PX.esc(e.tag || "—") + "</td>" +
        "<td>" + PX.esc(e.sourceLabel) + "</td>" +
        "<td>" + PX.esc(e.stateLabel) + "</td>" +
        '<td class="actions">' + (editable
          ? '<button class="btn sm" type="button" data-entry-state="' + e.personId +
            '" data-entry-to="' + (gone ? "active" : "left") + '">' +
            (gone ? "恢复" : "标为退出") + "</button>" +
            '<button class="btn sm danger" type="button" data-entry-drop="' +
            e.personId + '" data-entry-name="' + PX.esc(e.name) + '">移出</button>'
          : '<span class="hint">只读</span>') + "</td></tr>";
    }).join("") : rowsOrEmpty(8, "rosterEntries",
      S.entryTotal === 0 ? "还没有成员" : "没有匹配的成员");
    var more = $("#rosterEntryMore");
    if (more) {
      var left = (S.entryTotal === null) ? 0 : S.entryTotal - rows.length;
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "再显示 " + Math.min(ENTRY_PAGE, left) + " 人" : "";
    }
    var count = $("#rosterMemberCount");
    if (count && S.roster) {
      count.textContent = (S.entryTotal === null ? "" : "共 " + S.entryTotal + " 人") +
        (S.entryFilter ? "（已筛选）" : "");
    }
  }

  /* ------------------------------------------------------------------ 屏幕包 --- */
  function loadScreenPacks() {
    markLoading("screenPacks");
    return PX.api("/screen_packs")
      .then(function (d) {
        S.screenPacks = d.packs || [];
        S.packManage = !!d.canManage;
        S.packLimits = d.limits || null;
        markLoaded("screenPacks");
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function packStamp(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + PX.pad(d.getMonth() + 1) + "-" + PX.pad(d.getDate());
  }

  // 导入的限制全部来自服务端（packs.py 里的静态配置），这里只负责摆放
  function drawScreenRules() {
    var card = $("#screenPackRules");
    var box = $("#screenPackRulesList");
    if (!card || !box) return;
    card.hidden = !S.packManage;
    if (!S.packManage) return;
    var l = S.packLimits || {};
    var ext = (l.ext || []).map(function (e) {
      return "<code>" + PX.esc(e) + "</code>";
    }).join(" ");
    function nb(text) {
      return '<span class="nb">' + PX.esc(text) + "</span>";
    }
    var rules = [
      "压缩包内需有 <code>" + PX.esc(l.page || "screen.html") + "</code> 作为入口页；" +
        "包内其它文件按 <code>screen/&lt;文件名&gt;</code> 相对引用。",
      "接受的文件类型：" + ext + "；其余文件会被丢弃。",
      "压缩包不超过 " + nb(l.zipText || "") + "；解开后单个文件不超过 " +
        nb(l.fileText || "") + "，总量不超过 " + nb(l.totalText || "") +
        "，最多 " + Number(l.countMax || 0) + " 个文件。",
      "标识取自文件名（小写）；与已有标识重复时自动追加序号，不会覆盖已有屏幕。",
      "删除后，仍引用该项的活动会回到自带的展示页。"
    ];
    box.innerHTML = rules.map(function (t) { return "<li>" + t + "</li>"; }).join("");
  }

  function drawScreens() {
    var box = $("#screenPackList");
    if (!box) return;
    var hint = $("#screenPackHint");
    var add = $("#screenPackAdd");
    var packs = S.screenPacks || [];
    if (add) add.hidden = !S.packManage;
    if (hint) hint.textContent = packs.length ? "共 " + packs.length + " 项" : "";
    box.innerHTML = packs.length ? packs.map(function (pk) {
      var meta = (pk.files || 0) + " 个文件";
      if (pk.sizeText) meta += " · " + pk.sizeText;
      if (pk.at) meta += " · " + packStamp(pk.at);
      return '<div class="pack-card" role="listitem">' +
        '<span class="pack-name mono">' + PX.esc(pk.id) + "</span>" +
        '<span class="pack-src">' + PX.esc(pk.source) + " · " + PX.esc(meta) + "</span>" +
        (S.packManage && !pk.builtin
          ? '<button class="btn sm pack-del" type="button" data-pack-del="' +
            PX.esc(pk.id) + '">删除</button>'
          : "") +
        "</div>";
    }).join("") : '<p class="hint">' +
      (loaded("screenPacks") ? "还没有可用的屏幕" : "正在读取…") + "</p>";
    drawScreenRules();
  }

  function uploadScreenPack(file) {
    if (!file) return;
    var l = S.packLimits || {};
    if (l.zipMax && file.size > l.zipMax) {
      PX.toast("压缩包超过 " + PX.esc(l.zipText || "上限"), true);
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      PX.api("/screen_pack/upload", {
        method: "POST",
        body: { filename: file.name, data: String(reader.result || "") }
      }).then(function (d) {
        var dropped = (d.skipped || []).length;
        PX.toast("已导入：" + PX.esc(d.pack ? d.pack.id : "") +
                 (dropped ? "（" + dropped + " 个文件未接受）" : ""));
        return loadScreenPacks().then(drawScreens);
      }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
    };
    reader.onerror = function () { PX.toast("无法读取该文件", true); };
    reader.readAsDataURL(file);
  }

  function deleteScreenPack(id) {
    openOverlay("pack-del", "删除屏幕",
      '<p class="page-sub">删除后，仍引用这项的活动会回到自带的展示页。</p>' +
      '<p class="page-sub"><b>' + PX.esc(id) + "</b></p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn danger" type="button" id="packDelGo">删除</button></div>');
    $("#packDelGo").addEventListener("click", function () {
      closeOverlay();
      PX.api("/screen_pack/delete", { method: "POST", body: { id: id } })
        .then(function () {
          PX.toast("已删除");
          return loadScreenPacks().then(drawScreens);
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
  }

  /* ------------------------------------------------------------------ 报名 --- */
  /* 一个报名 = 一个公开链接 + 时间窗 + 往哪份名单里写。管理侧只管设置与记录；
   * 公开页在 /join/<token>，不经过面板登录。 */
  var SIGNUP_PAGE = 30;
  var SIGNUP_ENTRY_PAGE = 100;

  function loadSignups(more) {
    if (more) {
      if (S.signupTotal !== null && S.signups.length >= S.signupTotal) {
        return Promise.resolve();
      }
    } else {
      S.signups = [];
      S.signupTotal = null;
      markLoading("signups");
    }
    var parts = ["/signups?offset=" + (more ? S.signups.length : 0),
                 "limit=" + SIGNUP_PAGE];
    if (S.signupFilter) parts.push("q=" + encodeURIComponent(S.signupFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.signups = (more ? S.signups : []).concat(d.signups || []);
        S.signupTotal = d.total || 0;
        markLoaded("signups");
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function drawSignups() {
    var body = $("#signupTable tbody");
    if (!body) return;
    var rows = S.signups || [];
    body.innerHTML = rows.length ? rows.map(function (r) {
      return "<tr>" +
        '<td><a href="#/signup/' + r.id + '"><b>' + PX.esc(r.title) + "</b></a>" +
        (r.note ? '<div class="hint">' + PX.esc(r.note) + "</div>" : "") + "</td>" +
        "<td>" + PX.esc(r.rosterName || "—") +
        (r.filterRosterName ? '<div class="hint">限 ' + PX.esc(r.filterRosterName) +
          " 内成员</div>" : "") + "</td>" +
        '<td><span class="tag' + (r.status === "open" ? " pending" : "") + '">' +
        PX.esc(r.statusLabel) + "</span></td>" +
        '<td class="right num">' + r.entries +
        (r.capacity ? " / " + r.capacity : "") + "</td>" +
        '<td class="actions"><button class="btn sm" type="button" data-open-signup="' +
        r.id + '">编辑</button></td></tr>';
    }).join("") : rowsOrEmpty(5, "signups", "还没有报名");
    var count = $("#signupCount");
    if (count) count.textContent = S.signupTotal === null ? "" : "共 " + S.signupTotal + " 个";
    var more = $("#signupMore");
    if (more) {
      var left = (S.signupTotal === null) ? 0 : S.signupTotal - rows.length;
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "再显示 " + Math.min(SIGNUP_PAGE, left) + " 个" : "";
    }
  }

  function loadSignup(id) {
    return PX.api("/signup?id=" + id)
      .then(function (d) {
        S.signup = d.signup;
        S.signupDirty = false;
        S.history = [];
        S.historyPos = -1;
      })
      .catch(function (e) {
        PX.toast(PX.esc(e.message), true);
        S.signup = null;
      });
  }

  function loadSignupEntries(more) {
    if (!S.signup) return Promise.resolve();
    if (more) {
      if (S.signupEntryTotal !== null && S.signupEntries.length >= S.signupEntryTotal) {
        return Promise.resolve();
      }
    } else {
      S.signupEntries = [];
      S.signupEntryTotal = null;
      markLoading("signupEntries");
    }
    var parts = ["/signup/entries?id=" + S.signup.id,
                 "offset=" + (more ? S.signupEntries.length : 0),
                 "limit=" + SIGNUP_ENTRY_PAGE];
    if (S.signupEntryFilter) parts.push("q=" + encodeURIComponent(S.signupEntryFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.signupEntries = (more ? S.signupEntries : []).concat(d.entries || []);
        S.signupEntryTotal = d.total || 0;
        markLoaded("signupEntries");
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function toLocalInput(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + PX.pad(d.getMonth() + 1) + "-" + PX.pad(d.getDate()) +
      "T" + PX.pad(d.getHours()) + ":" + PX.pad(d.getMinutes());
  }

  /* 时间与日期是两个文本框：原生控件的占位符跟着浏览器语言走，中英混排很怪，
     所以显示交给我们自己的占位符，选择器由一个日历按钮调出来。
     解析尽量宽：2026-12-01 / 2026/12/01 / 2026.12.01 / 20261201，
     带时间时再认 12:31 或 1231，也认直接粘进来的 20261201T12:31。 */
  function parseStamp(text) {
    var raw = String(text || "").trim().replace(/\s+/g, " ");
    if (!raw) return { empty: true };
    var m = raw.match(
      /^(\d{4})[-/.\u5e74]?(\d{1,2})[-/.\u6708]?(\d{1,2})\u65e5?(?:[ T,]*?(\d{1,2})(?::?(\d{2}))?(?::?(\d{2}))?)?$/);
    if (!m) return { error: "日期请写成 2026-12-01 这样的格式" };
    var year = Number(m[1]);
    var month = Number(m[2]);
    var day = Number(m[3]);
    var hour = m[4] === undefined ? 0 : Number(m[4]);
    var minute = m[5] === undefined ? 0 : Number(m[5]);
    if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59) {
      return { error: "日期或时刻超出范围" };
    }
    var probe = new Date(year, month - 1, day);
    if (probe.getMonth() !== month - 1 || probe.getDate() !== day) {
      return { error: "这个月没有这一天" };
    }
    var stamp = new Date(year, month - 1, day, hour, minute).getTime();
    if (isNaN(stamp)) return { error: "日期或时刻不合法" };
    return {
      ts: Math.round(stamp / 1000),
      date: year + "-" + PX.pad(month) + "-" + PX.pad(day),
      time: PX.pad(hour) + ":" + PX.pad(minute),
      hasTime: m[4] !== undefined
    };
  }

  function parsePair(dateSel, timeSel) {
    var text = ($(dateSel).value || "").trim();
    if (!text) return { ts: 0 };
    var parsed = parseStamp(text);
    if (parsed.error) return { error: parsed.error };
    var timeText = ($(timeSel).value || "").trim();
    if (timeText) {
      var time = parseStamp(parsed.date + " " + timeText);
      if (time.error) return { error: "时刻请写成 12:31 这样的格式" };
      return { ts: time.ts, date: parsed.date, time: time.time };
    }
    // 日期里自带时刻就用它，否则落到 00:00
    return { ts: parsed.ts, date: parsed.date, time: parsed.time, inferredTime: !parsed.hasTime };
  }

  function pairValue(dateSel, timeSel) {
    return parsePair(dateSel, timeSel).ts || 0;
  }

  /* 保存时把两个框改写成库里的显示格式，用户看得见实际存的是什么 */
  function normalizePair(dateSel, timeSel, clearSel, ts) {
    if (!ts) { setPair(dateSel, timeSel, clearSel, 0); return; }
    setPair(dateSel, timeSel, clearSel, ts);
  }

  function pairOf(dateSel) {
    for (var i = 0; i < DT_PAIRS.length; i++) {
      if (DT_PAIRS[i].date === dateSel) return DT_PAIRS[i];
    }
    return null;
  }

  function syncNativePair(pair, ts) {
    if (!pair) return;
    var full = toLocalInput(ts);
    $(pair.datePick).value = full ? full.slice(0, 10) : "";
    $(pair.timePick).value = full ? full.slice(11, 16) : "";
  }

  function setPair(dateSel, timeSel, clearSel, ts) {
    var full = toLocalInput(ts);
    $(dateSel).value = full ? full.slice(0, 10) : "";
    $(timeSel).value = full ? full.slice(11, 16) : "";
    if (clearSel) $(clearSel).disabled = !full;
    syncNativePair(pairOf(dateSel), ts);
  }

  function clearPair(dateSel, timeSel, clearSel) {
    $(dateSel).value = "";
    $(timeSel).value = "";
    if (clearSel) $(clearSel).disabled = true;
    syncNativePair(pairOf(dateSel), 0);
    S.signupDirty = true;
    pushHistory();
  }

  function fieldRowHtml(field, index) {
    var f = field || { label: "", type: "text", into: "", required: false };
    var types = [["text", "单行文本"], ["textarea", "多行文本"], ["number", "数字"],
                 ["select", "单选"], ["date", "日期"]];
    var intos = [["", "只留在本次报名"], ["person.dept", "写入院系"], ["person.grp", "写入班级"],
                 ["roster.tag", "写成名单分组"], ["roster.note", "写成名单备注"]];
    return '<div class="prize-row field-row" data-fi="' + index + '">' +
      '<input class="f-label" type="text" maxlength="40" placeholder="字段名称" value="' +
      PX.esc(f.label) + '">' +
      '<select class="f-type">' + types.map(function (t) {
        return '<option value="' + t[0] + '"' + (f.type === t[0] ? " selected" : "") +
          ">" + t[1] + "</option>";
      }).join("") + "</select>" +
      '<select class="f-into">' + intos.map(function (t) {
        return '<option value="' + t[0] + '"' + (f.into === t[0] ? " selected" : "") +
          ">" + t[1] + "</option>";
      }).join("") + "</select>" +
      '<label class="f-req"><input type="checkbox" class="f-required"' +
      (f.required ? " checked" : "") + "> 必填</label>" +
      '<button class="btn sm danger" type="button" data-field-del="' + index + '">删除</button>' +
      "</div>";
  }

  function drawFieldEditor() {
    var box = $("#fieldEditor");
    if (!box || !S.signup) return;
    var fields = S.signup.fields || [];
    box.innerHTML = fields.length
      ? fields.map(fieldRowHtml).join("")
      : '<p class="hint">还没有自定义字段</p>';
  }

  // ---- 主题：报名页只换这几个值，管理侧的预览也跟着变 ----
  var THEMES = {
    plain: { bg: "#ffffff", text: "#1c1917", muted: "#8b837b", line: "#ddd7cf",
             surface: "#ffffff" },
    warm: { bg: "#fff8f0", text: "#3b2a1a", muted: "#9a8570", line: "#ecdcc8",
            surface: "#fffdfa" },
    cool: { bg: "#f3f7fb", text: "#1b2733", muted: "#6d8398", line: "#d5e2ee",
            surface: "#ffffff" },
    dark: { bg: "#1b1a18", text: "#f3efe9", muted: "#a49b90", line: "#3a3632",
            surface: "#20201e" }
  };

  function drawThemePreview() {
    var page = $("#signupPreview");
    if (!page) return;
    var preset = THEMES[$("#signupTheme").value] || THEMES.plain;
    var accent = $("#signupAccent").value || "#b45309";
    page.style.setProperty("--preview-bg", preset.bg);
    page.style.setProperty("--preview-text", preset.text);
    page.style.setProperty("--preview-muted", preset.muted);
    page.style.setProperty("--preview-line", preset.line);
    page.style.setProperty("--preview-surface", preset.surface);
    page.style.setProperty("--preview-accent", accent);
    page.style.background = preset.bg;
    $("#pvTitle").textContent = $("#signupName").value.trim() || "报名";
    $("#pvTitle").style.color = preset.text;
    $("#pvNote").textContent = $("#signupNote").value.trim() || "报名说明";
    var builtin = [];
    if ($("#collectDept").checked) builtin.push("院系");
    if ($("#collectGrp").checked) builtin.push("班级");
    var extra = collectFields().map(function (f) { return f.label; });
    $("#pvExtra").textContent = builtin.concat(extra).join("、") || "没有任何附加字段";
    $("#pvExtra").style.display = (builtin.length + extra.length) ? "" : "none";
    $("#pvBtn").textContent = "提交报名";
  }

  function drawQr(url) {
    var holder = $("#signupQr");
    if (!holder) return;
    holder.innerHTML = "";
    if (!url) return;
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      holder.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 1, scalable: true });
      // 库会在最外层塞一块 fill="white" 的 rect，压着码的边看着像黑框；白底由容器给
      var bg = holder.querySelector('svg > rect');
      if (bg) bg.parentNode.removeChild(bg);
    } catch (e) {
      holder.innerHTML = '<span class="hint">二维码生成失败</span>';
    }
  }

  function toggleCardState(cardId, on) {
    var card = $(cardId);
    if (!card) return;
    card.classList.toggle("is-off", !on);
    Array.prototype.forEach.call(
      card.querySelectorAll(".field input, .field select"), function (el) {
        el.disabled = !on;
      });
  }

  function drawSignup() {
    var r = S.signup;
    var head = $("#signupTitle");
    if (!head) return;
    if (!r) {
      head.textContent = "报名";
      return;
    }
    head.textContent = r.title;
    // 只有拥有者能归档这份报名
    var archiveBtn = $("#signupArchiveBtn");
    if (archiveBtn) archiveBtn.hidden = !r.owner;
    $("#signupSub").textContent = r.statusLabel + " · 已报 " + r.entries +
      (r.capacity ? " / " + r.capacity : "") + " 人 · 写入名单 " +
      (r.rosterName || "—") +
      (r.filterRosterName ? " · 限 " + r.filterRosterName + " 内成员" : "");
    document.title = r.title + " · CAEventManager";

    $("#signupUrl").textContent = r.url || "—";
    $("#signupOpenBtn").setAttribute("href", r.url || "#");
    drawQr(r.url);

    // 后台的刷新只更新只读的那部分；正在填的表单由操作者说了算
    if (S.signupDirty) {
      $("#signupEntryCount").textContent = S.signupEntryTotal === null
        ? "" : "共 " + S.signupEntryTotal + " 条";
      drawSignupEntries();
      return;
    }

    $("#signupName").value = r.title || "";
    $("#signupNote").value = r.note || "";
    $("#signupState").value = r.state || "open";

    var theme = r.theme || {};
    $("#signupTheme").value = theme.preset || "plain";
    $("#signupAccent").value = theme.accent || "#b45309";

    // 时间：有任一端就当作"启用了时间限制"
    var hasTime = !!(r.opensAt || r.closesAt);
    $("#signupTimeOn").checked = hasTime;
    setPair("#signupOpensDate", "#signupOpensTime", "#signupOpensClear", r.opensAt);
    setPair("#signupClosesDate", "#signupClosesTime", "#signupClosesClear", r.closesAt);
    $("#signupCapOn").checked = !!r.capacity;
    $("#signupCap").value = r.capacity || 1;
    $("#signupSelfEdit").value = r.selfEdit || "off";
    var collect = r.collect || [];
    $("#collectDept").checked = collect.indexOf("dept") >= 0;
    $("#collectGrp").checked = collect.indexOf("grp") >= 0;

    toggleCardState("#signupCapCard", !!r.capacity);
    toggleCardState("#signupTimeCard", hasTime);

    $("#signupEntryCount").textContent = S.signupEntryTotal === null
      ? "" : "共 " + S.signupEntryTotal + " 条";
    drawFieldEditor();
    drawSignupEntries();
    drawThemePreview();
    if (!S.signupDirty && !S.history.length) pushHistory();   // 基线：刚打开时的样子
  }

  function drawSignupEntries() {
    var body = $("#signupEntryTable tbody");
    if (!body || !S.signup) return;
    var fields = S.signup.fields || [];
    var rows = S.signupEntries || [];
    body.innerHTML = rows.length ? rows.map(function (e) {
      var extra = fields.map(function (f) {
        var value = (e.answers || {})[f.key] || "";
        return value ? f.label + "：" + value : "";
      }).filter(Boolean).join(" · ");
      return "<tr>" +
        '<td class="mono">' + PX.esc(e.sid) + "</td>" +
        "<td><b>" + PX.esc(e.name) + "</b></td>" +
        "<td>" + PX.esc(e.dept || "—") + "</td>" +
        "<td>" + PX.esc(e.grp || "—") + "</td>" +
        '<td class="mono">' + PX.whenExact(e.ts) + "</td>" +
        '<td class="wrap">' + (extra ? PX.esc(extra) : "—") + "</td></tr>";
    }).join("") : rowsOrEmpty(6, "signupEntries", S.signupEntryTotal === 0 ? "还没有人报名" : "没有匹配的记录");
    var more = $("#signupEntryMore");
    if (more) {
      var left = (S.signupEntryTotal === null) ? 0 : S.signupEntryTotal - rows.length;
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "再显示 " + Math.min(SIGNUP_ENTRY_PAGE, left) + " 条" : "";
    }
    var count = $("#signupEntryCount");
    if (count) {
      count.textContent = S.signupEntryTotal === null ? "" : "共 " + S.signupEntryTotal + " 条";
    }
  }

  function collectFields() {
    var rows = $$("#fieldEditor .field-row");
    return rows.map(function (row) {
      return {
        label: $(".f-label", row).value.trim(),
        type: $(".f-type", row).value,
        into: $(".f-into", row).value,
        required: $(".f-required", row).checked
      };
    }).filter(function (f) { return f.label; });
  }

  function saveSignup() {
    if (!S.signup) return;
    var timeOn = $("#signupTimeOn").checked;
    var openPair = timeOn ? parsePair("#signupOpensDate", "#signupOpensTime") : { ts: 0 };
    var closePair = timeOn ? parsePair("#signupClosesDate", "#signupClosesTime") : { ts: 0 };
    if (openPair.error || closePair.error) {
      PX.toast(openPair.error || closePair.error, true);
      return;
    }
    var opens = openPair.ts || 0;
    var closes = closePair.ts || 0;
    if (opens && closes && opens >= closes) {
      PX.toast("开始时间必须早于结束时间", true);
      return;
    }
    // 粘进来的 20261201T12:31、2026/12/01 都在这里被改写成本系统的显示格式
    if (timeOn) {
      if (openPair.ts) normalizePair("#signupOpensDate", "#signupOpensTime",
                                     "#signupOpensClear", openPair.ts);
      if (closePair.ts) normalizePair("#signupClosesDate", "#signupClosesTime",
                                      "#signupClosesClear", closePair.ts);
    }
    var capOn = $("#signupCapOn").checked;
    var collect = [];
    if ($("#collectDept").checked) collect.push("dept");
    if ($("#collectGrp").checked) collect.push("grp");
    var body = {
      id: S.signup.id,
      title: $("#signupName").value.trim(),
      note: $("#signupNote").value.trim(),
      state: $("#signupState").value,
      opens_at: opens,
      closes_at: closes,
      capacity: capOn ? (Number($("#signupCap").value) || 0) : 0,
      self_edit: $("#signupSelfEdit").value,
      collect: collect,
      theme: { preset: $("#signupTheme").value, accent: $("#signupAccent").value },
      fields: collectFields()
    };
    PX.api("/signup/update", { method: "POST", body: body })
      .then(function (d) {
        var changed = !(d && d.changed === false);
        var url = (S.signup && S.signup.url) || "";
        PX.toast(changed ? "已保存" : "没有改动", false, 3600,
                 changed && url ? {
                   icon: "open", title: "打开报名页",
                   run: function () { window.open(url, "_blank", "noopener"); }
                 } : null);
        S.signupDirty = false;
        return loadSignup(S.signup.id).then(drawSignup);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function signupDialog() {
    var options = (S.rosters || []).map(function (r) {
      return '<option value="' + r.id + '">' + PX.esc(r.name) + "</option>";
    }).join("");
    openOverlay("signup", "创建报名",
      '<div class="field"><label for="signupNewName">名称</label>' +
      '<input id="signupNewName" type="text" maxlength="60" ' +
      'placeholder="例如 秋日祭现场报名"></div>' +
      '<div class="field"><label for="signupNewRoster">写入哪份名单</label>' +
      '<select id="signupNewRoster">' + options + "</select>" +
      '<p class="hint">报名成功的人直接进入这份名单</p></div>' +
      '<div class="field"><label for="signupNewFilter">限制范围</label>' +
      '<select id="signupNewFilter"><option value="0">不限制，任何人可报名</option>' +
      options + "</select>" +
      '<p class="hint">选定名单后，只有该名单内的成员可以报名，学号与姓名需与记录一致</p></div>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="signupNewGo">创建</button></div>');
    $("#signupNewName").focus();
    $("#signupNewGo").addEventListener("click", function () {
      var title = $("#signupNewName").value.trim();
      if (!title) { PX.toast("请填写报名名称", true); return; }
      PX.api("/signup/create", {
        method: "POST",
        body: {
          title: title,
          roster_id: Number($("#signupNewRoster").value) || 0,
          filter_roster_id: Number($("#signupNewFilter").value) || 0
        }
      }).then(function (d) {
        closeOverlay();
        PX.toast("已创建报名");
        location.hash = "#/signup/" + d.id;
      }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
  }

  /* ------------------------------------------------------------------ 撤销 --- */
  /* 报名设置是一张表单：切换开关、选主题、加删字段这些动作浏览器不会替你撤销，
   * 所以自己留一份快照栈。粒度是"一次改变"，不是每个按键；保存成功后清空。 */
  var HISTORY_MAX = 40;

  function signupFormState() {
    if (!S.signup) return null;
    return {
      name: $("#signupName").value,
      note: $("#signupNote").value,
      state: $("#signupState").value,
      theme: $("#signupTheme").value,
      accent: $("#signupAccent").value,
      timeOn: $("#signupTimeOn").checked,
      opensDate: $("#signupOpensDate").value,
      opensTime: $("#signupOpensTime").value,
      closesDate: $("#signupClosesDate").value,
      closesTime: $("#signupClosesTime").value,
      capOn: $("#signupCapOn").checked,
      cap: $("#signupCap").value,
      selfEdit: $("#signupSelfEdit").value,
      collectDept: $("#collectDept").checked,
      collectGrp: $("#collectGrp").checked,
      fields: collectFields()
    };
  }

  function applySignupFormState(st) {
    if (!st || !S.signup) return;
    $("#signupName").value = st.name;
    $("#signupNote").value = st.note;
    $("#signupState").value = st.state;
    $("#signupTheme").value = st.theme;
    $("#signupAccent").value = st.accent;
    $("#signupTimeOn").checked = st.timeOn;
    $("#signupOpensDate").value = st.opensDate;
    $("#signupOpensTime").value = st.opensTime;
    $("#signupClosesDate").value = st.closesDate;
    $("#signupClosesTime").value = st.closesTime;
    $("#signupOpensClear").disabled = !(st.opensDate || st.opensTime);
    $("#signupClosesClear").disabled = !(st.closesDate || st.closesTime);
    // 撤销/重做是直接改文本框，手机上看的那两个原生控件要跟着走
    DT_PAIRS.forEach(function (pair) {
      syncNativePair(pair, pairValue(pair.date, pair.time));
    });
    $("#signupCapOn").checked = st.capOn;
    $("#signupCap").value = st.cap;
    $("#signupSelfEdit").value = st.selfEdit;
    $("#collectDept").checked = st.collectDept;
    $("#collectGrp").checked = st.collectGrp;
    S.signup.fields = st.fields;
    toggleCardState("#signupCapCard", !!st.capOn);
    toggleCardState("#signupTimeCard", !!st.timeOn);
    drawFieldEditor();
    drawThemePreview();
    S.signupDirty = true;
  }

  function pushHistory() {
    var st = signupFormState();
    if (!st) return;
    var top = S.history[S.historyPos];
    if (top && JSON.stringify(top) === JSON.stringify(st)) return;
    S.history = S.history.slice(0, S.historyPos + 1);
    S.history.push(st);
    if (S.history.length > HISTORY_MAX) S.history.shift();
    S.historyPos = S.history.length - 1;
  }

  function undoSignup() {
    if (S.historyPos <= 0) return;
    S.historyPos -= 1;
    applySignupFormState(S.history[S.historyPos]);
    PX.toast("已撤销");
  }

  function redoSignup() {
    if (S.historyPos >= S.history.length - 1) return;
    S.historyPos += 1;
    applySignupFormState(S.history[S.historyPos]);
    PX.toast("已恢复");
  }

  /* ------------------------------------------------------------------ 写入审批 --- */
  /* 一个统一入口：别人申请写你的名单，都在这里。普通账号也用得上，所以它和
   * 超管那份「活跃审批」是两条独立的路由，鉴权各自管各自的。
   * 从名单页跳过来时会带上 ?back=，那就给一个返回键；直接点侧栏进来则没有。 */
  function loadApprovals() {
    markLoading("approvals");
    markLoading("myAsks");
    return PX.api("/list_approvals")
      .then(function (d) {
        S.approvals = d.pending || [];
        S.myAsks = d.asks || [];
        S.approvalsCount = d.count || 0;
        markLoaded("approvals");
        markLoaded("myAsks");
        renderApprovalBadge();
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function renderApprovalBadge() {
    var badge = $("#writeBadge");
    if (!badge) return;
    badge.textContent = S.approvalsCount;
    badge.hidden = !S.approvalsCount;
  }

  /* 有没有返回键只看路由表里有没有 parent：
     有上一级的页面（详情页、挂在某一节下面的汇总页）永远有返回键；
     ?back= 只决定"返回到哪里"，不决定"有没有返回键"。 */
  function routeParent(route) {
    var r = route ? ROUTES[route] : null;
    return (r && r.parent) || null;
  }

  /* 顶层页也可以临时有返回键：地址里带 ?back= 时（从别处跳过来），给它一个回去的路。
     这时返回键的目的地是那一页，面包屑写「返回」。 */
  function routeHasBack(route) {
    if (routeParent(route)) return true;
    return !!(route && S.explicitBack);
  }

  /* 侧栏那一项：当前页在它里面时淡一档亮着，表示"路径经过这里"。
     有上一级的按上一级算；新建活动这类向导没有上一级，就明写一封 trail。 */
  function routeTrail(route) {
    var r = route ? ROUTES[route] : null;
    if (!r) return "";
    if (r.trail) return r.trail;
    var parent = r.parent;
    if (!parent) return "";
    var name = String(parent.href || "").replace(/^#\//, "");
    return ROUTES[name] ? name : "";
  }

  /* 切换页面时给标题与返回键放一次入场动画：
     上一页与这一页都有（或都没有）返回键时不动，否则标题让位、按钮淡入放大。

     让位是 padding-left 的布局动画（副标题同一行，跟着一起挪），所以**什么时候开始**
     很要紧：一屏新表单/表格的排版要几十毫秒，动画撞进去会被冻住再跳过去——看起来
     就是"抽一下"。于是先按上一页的样子摆好，等这一页画完再放开：
     `PX.afterApi` 报"回答落地"，据此把放开推到最后一屏之后（另有兜底）。
  */
  var CRUMB = null;

  function stopCrumb() {
    if (!CRUMB) return;
    clearTimeout(CRUMB.timer);
    if (CRUMB.idle && window.cancelIdleCallback) window.cancelIdleCallback(CRUMB.idle);
    CRUMB = null;
  }

  function playCrumb() {
    var c = CRUMB;
    if (!c) return;
    CRUMB = null;
    clearTimeout(c.timer);
    if (c.idle && window.cancelIdleCallback) window.cancelIdleCallback(c.idle);
    c.head.classList.toggle("has-back", c.want);
    c.head.classList.toggle("back-only", c.backOnly);
  }

  /* 又一个回答落地：把还没放的让位往后挪，等主线程真正空下来。
     requestIdleCallback 在这一帧的排版、绘制之后才回调——一屏新内容的排版正卡在那段里。 */
  function nudgeCrumb() {
    if (!CRUMB) return;
    clearTimeout(CRUMB.timer);
    if (CRUMB.idle && window.cancelIdleCallback) window.cancelIdleCallback(CRUMB.idle);
    if (window.requestIdleCallback) {
      CRUMB.idle = window.requestIdleCallback(playCrumb, {timeout: 150});
    } else {
      CRUMB.timer = setTimeout(playCrumb, 48);
    }
  }

  function animateCrumb(wanted, prevHadBack) {
    var page = document.querySelector('.page[data-page="' + wanted + '"]');
    var head = page ? page.querySelector(".head-title") : null;
    if (!head) return;
    var want = routeHasBack(wanted);
    var backOnly = want && !!S.explicitBack && !routeParent(wanted);
    stopCrumb();
    if (prevHadBack === want || reducedMotion()) {
      head.classList.toggle("has-back", want);
      head.classList.toggle("back-only", backOnly);
      return;
    }
    // 先摆成上一页的样子（这一页的标题可能还留着上次的类），再等放开
    var root = document.documentElement;
    root.classList.add("no-crumb-anim");
    head.classList.toggle("has-back", !!prevHadBack);
    head.classList.toggle("back-only", backOnly);
    void head.offsetWidth;
    root.classList.remove("no-crumb-anim");
    CRUMB = {head: head, want: want, backOnly: backOnly, idle: 0,
             timer: setTimeout(playCrumb, 160)};
  }

  /* 手机上表格要横滚时，首列默认钉在左边，操作列钉在右边。但首列自己就占了
     大半屏幕时（名字很长），钉住它等于把屏幕让给一列，别的列永远滑不进来——
     这种情况给 .tblwrap 打上 .wide-first，让首列跟着一起滑。 */
  var fitTimer = 0;

  function fitTables() {
    fitTimer = 0;
    var wraps = document.querySelectorAll(".tblwrap");
    for (var i = 0; i < wraps.length; i++) {
      var wrap = wraps[i];
      var cell = wrap.querySelector("table tbody tr td:first-child") ||
                 wrap.querySelector("table thead th:first-child");
      var wide = false;
      if (cell && wrap.scrollWidth > wrap.clientWidth + 1) {
        wide = cell.getBoundingClientRect().width > wrap.clientWidth * 0.6;
      }
      wrap.classList.toggle("wide-first", wide);
    }
  }

  function scheduleFitTables() {
    if (fitTimer) return;
    fitTimer = setTimeout(fitTables, 80);
  }

  function syncBackButton() {
    var back = $("#approvalsBack");
    if (back) back.setAttribute("data-back", S.backTo || "");
  }

  /* 当前页面的面包屑：上一段来自路由表，当前段就是这一页的标题 */
  function syncCrumb(wanted) {
    var page = document.querySelector('.page[data-page="' + wanted + '"]');
    if (!page) return;
    var head = page.querySelector(".head-title");
    // 有没有上一级，切到这一页就定下来：这段文字不该等"让位"那一动画
    if (head) head.classList.toggle("has-crumb", !!routeParent(wanted));
    var crumb = page.querySelector(".crumb-title .crumb");
    if (!crumb) return;
    var parent = routeParent(wanted);
    if (parent) {
      crumb.setAttribute("data-href", parent.href);
      crumb.textContent = parent.label;
      return;
    }
    if (S.explicitBack) {
      crumb.setAttribute("data-href", "#" + S.explicitBack);
      crumb.textContent = "返回";
    }
  }

  function drawApprovals() {
    syncBackButton();
    $("#approvalsCount").textContent = S.approvalsCount
      ? "共 " + S.approvalsCount + " 条" : "";
    $("#approvalsTable tbody").innerHTML = S.approvals.length
      ? S.approvals.map(function (a) {
          return "<tr>" +
            "<td><b>" + PX.esc(a.name) + "</b></td>" +
            "<td>" + PX.esc(a.accountName || ("#" + a.accountId)) + "</td>" +
            '<td class="mono">' + PX.whenExact(a.askedAt) + "</td>" +
            '<td class="actions">' +
            '<button class="btn sm primary" type="button" data-approve="' +
            a.id + '" data-approve-acct="' + a.accountId + '">同意</button>' +
            '<button class="btn sm" type="button" data-reject="' + a.id +
            '" data-approve-acct="' + a.accountId + '">拒绝</button></td></tr>';
        }).join("")
      : rowsOrEmpty(4, "approvals", "没有待处理的申请");
    $("#myAsksTable tbody").innerHTML = S.myAsks.length
      ? S.myAsks.map(function (a) {
          return "<tr>" +
            "<td><b>" + PX.esc(a.name) + "</b></td>" +
            "<td>" + PX.esc(a.ownerName || "—") + "</td>" +
            '<td class="mono">' + PX.whenExact(a.askedAt) + "</td>" +
            '<td class="actions"><span class="hint">等待确认</span></td></tr>';
        }).join("")
      : rowsOrEmpty(4, "myAsks", "还没有等待确认的申请");
  }

  function decideApproval(rid, accountId, action) {
    PX.api("/roster/grant/decide", {
      method: "POST",
      body: { id: rid, accountId: accountId, action: action }
    }).then(function () {
      PX.toast(action === "approve" ? "已同意" : "已拒绝");
      return loadApprovals().then(drawApprovals);
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function rosterDialog(row) {
    var r = row || {};
    openOverlay("roster", row ? "名单设置" : "新建名单",
      '<div class="field"><label for="rosterName">名称</label>' +
      '<input id="rosterName" type="text" maxlength="60" value="' +
      PX.esc(r.name || "") + '" placeholder="例如 2026 秋 动漫社成员"></div>' +
      '<div class="field"><label for="rosterNote">说明</label>' +
      '<input id="rosterNote" type="text" maxlength="200" value="' +
      PX.esc(r.note || "") + '" placeholder="可选"></div>' +
      '<div class="field"><label for="rosterVis">可见性</label>' +
      '<select id="rosterVis">' +
      '<option value="private"' + ((r.visibility || "private") === "private" ? " selected" : "") +
      ">私有（仅拥有者可见）</option>" +
      '<option value="public"' + (r.visibility === "public" ? " selected" : "") +
      ">公开（所有登录账号可见）</option>" +
      '<option value="org"' + (r.visibility === "org" ? " selected" : "") +
      ">组织公开（组织建立前等同公开）</option></select></div>" +
      (row && row.owner === false ? "" :
        '<div class="field"><label for="rosterGrantMode">他人申请写入时</label>' +
        '<select id="rosterGrantMode">' +
        '<option value="auto"' + ((r.grantMode || "auto") === "auto" ? " selected" : "") +
        ">申请即生效</option>" +
        '<option value="confirm"' + (r.grantMode === "confirm" ? " selected" : "") +
        ">需要我确认</option></select></div>" +
        '<div class="field"><label for="rosterApproveMode">审批人</label>' +
        '<select id="rosterApproveMode">' +
        '<option value="owner"' + ((r.approveMode || "owner") === "owner" ? " selected" : "") +
        ">仅拥有者</option>" +
        '<option value="editors"' + (r.approveMode === "editors" ? " selected" : "") +
        ">拥有者与可写成员</option></select></div>") +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      // 只有拥有者能归档（挂个写入权的人连按钮都不该看到）
      (row && row.owner !== false
        ? '<button class="btn danger" type="button" id="rosterArchive">归档名单</button>'
        : "") +
      '<button class="btn primary" type="button" id="rosterSave">保存</button></div>');
    $("#rosterName").focus();
    $("#rosterSave").addEventListener("click", function () {
      var payload = {
        name: $("#rosterName").value.trim(),
        note: $("#rosterNote").value.trim(),
        visibility: $("#rosterVis").value
      };
      var mode = $("#rosterGrantMode");
      if (mode) payload.grant_mode = mode.value;
      var approver = $("#rosterApproveMode");
      if (approver) payload.approve_mode = approver.value;
      if (!payload.name) { PX.toast("请填写名单名称", true); return; }
      var req;
      if (row) {
        payload.id = row.id;
        req = PX.api("/roster/update", { method: "POST", body: payload });
      } else {
        req = PX.api("/roster/create", { method: "POST", body: payload })
          .then(function (d) { return d.id; });
      }
      req.then(function (out) {
        closeOverlay();
        if (out && out.changed === false) {
          PX.toast("没有改动");
          return loadRoster(row.id).then(drawRoster);
        }
        PX.toast(row ? "已保存" : "已创建名单");
        if (row) {
          return loadRoster(row.id).then(drawRoster);
        }
        location.hash = "#/roster/" + out;
      }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
    var del = $("#rosterArchive");
    if (del) {
      del.addEventListener("click", function () {
        // 归档是拥有者能做的最重的一步：在拥有者眼里它就等于删除——退出列表之后
        // 自己再也看不到它；数据一条不动，真要找回来得请超级管理员
        confirmHighRisk("归档名单",
          "归档之后这份名单从你的列表里消失，也不能再改；人员和条目原样留着，"
          + "要恢复请联系超级管理员。确认继续？", "归档", true,
          function () {
            archiveList("roster", r.id, function () {
              closeAllOverlays();
              PX.toast("已归档，需要时联系超级管理员");
              S.roster = null;
              location.hash = "#/rosters";
            });
          }, { noSudo: true, keepUnder: true });
      });
    }
  }

  /* 导入有两条路：列表页新建一份，或名单里合并进这份。走的是同一个接口。 */
  function rosterImportDialog() {
    openOverlay("roster-import", "导入名单",
      '<div class="field"><label for="rosterImportNameInput">名单名称</label>' +
      '<input id="rosterImportNameInput" type="text" maxlength="60" ' +
      'placeholder="例如 2026 秋 动漫社成员"></div>' +
      '<p class="note" style="margin-top:10px">支持 CSV 与 TSV。首行为表头时可识别' +
      '学号、姓名、院系、班级、分组、备注；无表头时按「学号,姓名,班级」读取。</p>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="rosterImportPick">选择文件</button></div>');
    $("#rosterImportNameInput").focus();
    $("#rosterImportPick").addEventListener("click", function () {
      var name = $("#rosterImportNameInput").value.trim();
      if (!name) { PX.toast("请填写名单名称", true); return; }
      S.rosterImportName = name;
      closeOverlay();
      $("#rosterImportFile").click();
    });
  }

  function importRoster(file, rosterId) {
    // xlsx 按原始字节发过去（zip 不能当文本读），CSV / TSV 照旧当文本
    var isXlsx = /\.xlsx$/i.test(file.name || "");
    var done = function (d) {
      PX.toast("导入完成：新增 " + d.added + " 人，名单内已有 " + d.unchanged +
               " 人" + (d.failed ? "，无法读取 " + d.failed + " 行" : ""),
               false, 6000, {
                 icon: "open", title: "打开名单",
                 run: function () { location.hash = "#/roster/" + d.id; }
               });
      if (rosterId) return loadRoster(rosterId).then(function () {
        return loadEntries(false);
      }).then(drawRoster);
      location.hash = "#/roster/" + d.id;
    };
    var fail = function (e) { PX.toast(PX.esc(e.message), true); };

    if (isXlsx) {
      var url = "/roster/import.xlsx?id=" + (rosterId || 0);
      if (!rosterId) {
        url += "&name=" + encodeURIComponent(S.rosterImportName ||
          (file.name || "").replace(/\.[^.]+$/, "") || "导入的名单");
      }
      PX.api(url, { method: "POST", blob: file }).then(done).catch(fail);
      return;
    }

    var reader = new FileReader();
    reader.onload = function () {
      var text = String(reader.result || "");
      if (!text.trim()) { PX.toast("文件为空", true); return; }
      var body = { text: text };
      if (rosterId) {
        body.id = rosterId;
      } else {
        body.name = S.rosterImportName ||
          (file.name || "").replace(/\.[^.]+$/, "") || "导入的名单";
      }
      PX.api("/roster/import", { method: "POST", body: body }).then(done).catch(fail);
    };
    reader.onerror = function () { PX.toast("无法读取该文件", true); };
    reader.readAsText(file, "utf-8");
  }

  function addRosterEntryDialog() {
    if (!S.roster) return;
    openOverlay("roster-entry", "手动录入",
      '<div class="field"><label for="rosterAddSid">学号</label>' +
      '<input id="rosterAddSid" type="text" maxlength="32" autocomplete="off"></div>' +
      '<div class="field"><label for="rosterAddName">姓名</label>' +
      '<input id="rosterAddName" type="text" maxlength="32" autocomplete="off"></div>' +
      '<div class="field"><label for="rosterAddDept">院系</label>' +
      '<input id="rosterAddDept" type="text" maxlength="40" autocomplete="off"></div>' +
      '<div class="field"><label for="rosterAddGrp">班级</label>' +
      '<input id="rosterAddGrp" type="text" maxlength="40" autocomplete="off"></div>' +
      '<div class="field"><label for="rosterAddTag">分组</label>' +
      '<input id="rosterAddTag" type="text" maxlength="40" autocomplete="off"></div>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="rosterAddGo">加入名单</button></div>');
    $("#rosterAddSid").focus();
    $("#rosterAddGo").addEventListener("click", addRosterEntry);
    ["#rosterAddSid", "#rosterAddName", "#rosterAddDept", "#rosterAddGrp",
     "#rosterAddTag"].forEach(function (sel) {
      $(sel).addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); addRosterEntry(); }
      });
    });
  }

  function addRosterEntry() {
    if (!S.roster) return;
    var sid = $("#rosterAddSid").value.trim();
    var name = $("#rosterAddName").value.trim();
    if (!sid || !name) { PX.toast("学号与姓名均为必填", true); return; }
    PX.api("/roster/entry", {
      method: "POST",
      body: {
        id: S.roster.id, sid: sid, name: name,
        dept: $("#rosterAddDept").value.trim(),
        grp: $("#rosterAddGrp").value.trim(),
        tag: $("#rosterAddTag").value.trim()
      }
    }).then(function () {
      closeOverlay();
      PX.toast("已加入名单");
      return loadRoster(S.roster.id).then(function () {
        return loadEntries(false);
      }).then(drawRoster);
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function rosterEntryAction(what, personId, state) {
    if (!S.roster) return;
    var req = what === "drop"
      ? PX.api("/roster/entry/drop", { method: "POST", body: { id: S.roster.id, personId: personId } })
      : PX.api("/roster/entry/state", {
          method: "POST",
          body: { id: S.roster.id, personId: personId, state: state }
        });
    req.then(function () {
      return loadRoster(S.roster.id).then(function () {
        return loadEntries(false);
      }).then(drawRoster);
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  /* ----------------------------------------------------------------- router */
  function hashQuery() {
    var raw = (location.hash || "").replace(/^#\/?/, "");
    var at = raw.indexOf("?");
    var out = {};
    if (at < 0) return out;
    raw.slice(at + 1).split("&").forEach(function (pair) {
      if (!pair) return;
      var kv = pair.split("=");
      out[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || "");
    });
    return out;
  }

  function routeFromHash() {
    var h = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
    if (ALIAS[h]) h = ALIAS[h];
    if (ROUTES[h]) {
      S.templateId = null;
      return h;
    }
    var cut = h.indexOf("/");
    if (cut > 0) {
      var head = h.slice(0, cut);
      var tail = decodeURIComponent(h.slice(cut + 1)).replace(/\/$/, "");
      // 结尾多一个斜杠不算"更深的页面"：回到它自己
      if (!tail && ROUTES[head]) return head;
      if (head === "new-event" && ROUTES[head]) {
        S.templateId = tail || null;
        return head;
      }
      if (head === "roster" && ROUTES[head]) {
        S.rosterId = Number(tail) || 0;
        return head;
      }
      if (head === "signup" && ROUTES[head]) {
        S.signupId = Number(tail) || 0;
        return head;
      }
      if (head === "org" && ROUTES[head]) {
        // 组织标识只有一段：多出来的部分不是"更深的页面"，是地址写错了
        if (tail.indexOf("/") >= 0) return null;
        S.orgId = tail;
        return head;
      }
      if ((head === "my-org" || head === "my-org-manage") && ROUTES[head]) {
        if (tail.indexOf("/") >= 0) return null;
        S.orgId = tail;
        return head;
      }
      // 会话 id 也进地址（#/assistant/<id>），换一段会话就是换一个地址
      if (head === "assistant" && ROUTES[head]) return head;
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
    $("#missing").hidden = true;
    $("#denied").hidden = false;
    $("#deniedBack").hidden = !canGoBack();
    $("#siteFoot").hidden = true;
    $$("[data-nav]").forEach(function (a) {
      a.removeAttribute("aria-current");
      a.removeAttribute("data-trail");
    });
    $$(".nav-group").forEach(function (g) { g.classList.remove("has-current"); });
    closePane();
    var el = $("#content");
    if (el) el.scrollTop = 0;
  }

  var guardBypass = false;

  /* 这一页背后还有没有"上一个页面"：数的是自己切换过几次页。
     直接打开一个地址（哪怕地址是错的）时这里是 0——浏览器历史里确实有上一站，
     但那多半是别的站点，退回去就离开了面板，所以只给"回到首页"。 */
  var navDepth = 0;

  function canGoBack() { return navDepth > 0; }

  /* 地址里那一串（#/ 之后、? 之前）是什么 */
  function hashRoute() {
    return String(location.hash || "").replace(/^#\/?/, "").split("?")[0].replace(/\/$/, "");
  }

  /* 认不出来的页面：整页换成"页面不存在"，手上的数据一并丢掉，
     免得半页旧内容配一条错的标题留在屏幕上 */
  function showMissing() {
    S.route = null;
    S.orgId = "";
    S.orgDetail = null;
    S.rosterId = 0;
    S.signupId = 0;
    S.templateId = null;
    $$(".page").forEach(function (p) { p.hidden = true; });
    $("#denied").hidden = true;
    $("#missing").hidden = false;
    $("#missingPrev").hidden = !canGoBack();
    $("#siteFoot").hidden = true;
    $$("[data-nav]").forEach(function (a) {
      a.removeAttribute("aria-current");
      a.removeAttribute("data-trail");
    });
    $$(".nav-group").forEach(function (g) { g.classList.remove("has-current"); });
    closePane();
    var el = $("#content");
    if (el) el.scrollTop = 0;
  }

  function onRoute() {
    var prevRoute = S.route;                 // 上一页有没有返回键，全靠它
    // routeFromHash() 会重算 templateId，所以先把它留在手里：离开表单时地址要退回原样
    var prevTemplate = S.templateId;
    var parsed = routeFromHash();
    if (!parsed && hashRoute()) {
      // 地址里写着一个认不出来的页面：说"没有这一页"，不要把上一页的数据留在屏幕上
      showMissing();
      return;
    }
    var wanted = parsed || FALLBACK_ROUTE;
    // 换一页就把会话抽屉收回去：返回键回到别处时，它不该还浮在上面
    closeAssistantDrawer();
    // 弹窗同理：换一页了，上一页的详情不该还压在新页面上（它属于那个对象，不是这一页的）
    if (wanted !== S.route) closeAllOverlays();
    if (!importBypass && S.route === "import-accounts" && wanted !== "import-accounts" &&
      S.import && (S.import.rows || []).length) {
    try { history.replaceState(null, "", "#/import-accounts"); } catch (e) { /* 老浏览器 */ }
    askImportLeave(wanted);
    return;
  }
    if (!orgBypass && S.route === "org-new" && wanted !== "org-new" && orgHasContent()) {
      // 同一套办法：地址先退回去，问清楚再走
      try { history.replaceState(null, "", "#/org-new"); } catch (e) { /* 老浏览器 */ }
      askOrgLeave(wanted);
      return;
    }
    if (!guardBypass && S.route === "new-event" && wanted !== "new-event" && S.dirty) {
      // 地址退回表单那一页，但不许重画：location.replace 会再触发一次 hashchange，
      // 表单会按预设重填一遍，填过的内容和脏标记当场都没了（"继续编辑"也就无从恢复）
      var here = "#/" + S.route +
        (prevTemplate ? "/" + encodeURIComponent(prevTemplate) : "");
      try {
        history.replaceState(null, "", here);
      } catch (e) { /* 极老的浏览器：地址留在原处，内容还在 */ }
      askDraftChoice(wanted);
      return;
    }
    if (!allowed(wanted)) {
      // Reachable by typing the hash; say so instead of silently moving them.
      showDenied(wanted);
      return;
    }
    // 走了就把这张表单忘掉：下次进来该是干净的一张（草稿另存，回来时再填）
    if (prevRoute === "new-event" && wanted !== "new-event") forgetKindForm();
    // 换过页了：历史里就有了上一层，404 那边才敢给 [返回上一页]。
    // 第一次落笔不算——刚从地址栏进来时，背后没有属于自己的页面。
    if (prevRoute && wanted !== prevRoute) navDepth += 1;
    S.route = wanted;
    S.typed = false;         // 换了一页，"这一页敲过字"重新算
    // 助手页把输入框钉在底部，页脚就不跟它抢这点高度了
    var foot = $("#siteFoot");
    if (foot) foot.hidden = (wanted === "assistant");
    // 返回键属于"这一页有没有上一级"：先定下来，动画与渲染都靠它
    var parent = routeParent(wanted);
    S.explicitBack = hashQuery().back || null;
    S.backTo = S.explicitBack || (parent ? parent.href.replace(/^#/, "") : null);
    S.denied = null;
    $("#denied").hidden = true;
    $("#missing").hidden = true;
    $("#siteFoot").hidden = (wanted === "assistant");
    // 助手页不滚整页：标题与输入框固定，只有中间的对话滚
    var main = $("#content");
    if (main) main.classList.toggle("is-chat", wanted === "assistant");

    // a child route keeps its group expanded
    var parent = ROUTES[wanted].group;
    if (parent) S.openGroups[parent] = true;
    syncGroups();

    var trail = routeTrail(wanted);
    var prevHadBack = routeHasBack(prevRoute);
    $$(".page").forEach(function (p) {
      p.hidden = p.getAttribute("data-page") !== wanted;
    });
    $$("[data-nav]").forEach(function (a) {
      var name = a.getAttribute("data-nav");
      if (name === wanted) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
      // 这一页在上层页的里面：淡一档亮着，点它回到那一层
      if (trail && name === trail && name !== wanted) a.setAttribute("data-trail", "1");
      else a.removeAttribute("data-trail");
    });
    $$(".nav-group").forEach(function (g) {
      var on = !!ROUTES[wanted] && ROUTES[wanted].group === g.getAttribute("data-group");
      g.classList.toggle("has-current", on);
    });
    var current = document.querySelector('#sidebar [data-nav][aria-current="page"]');
    if (current) {
      $$('#sidebar [data-nav], #sidebar [data-toggle]').forEach(function (el) {
        el.tabIndex = -1;
      });
      current.tabIndex = 0;
    }

    closePane();
    $("#content").scrollTop = 0;
    syncNavFade();
    draw();
    syncCrumb(wanted);
    animateCrumb(wanted, prevHadBack);
    scheduleFitTables();
    loadNotices();
    loadService();
    syncSudo();
    if (wanted === "server" || wanted === "overview") refreshHost();
    // 备份那一段只在进页面时问一次：放在重画里问，回答落地又会触发重画
    if (wanted === "server") loadBackup();
    /* 进页就问一次服务端。实时通知只负责"快"，长连接的可靠性不足以当真相：
       流要是断过一段，页面上的数字与列表就会停在旧的那一版，而人看不出来。
       所以每一页进来都重新取一遍自己要显示的东西——代价是一次小请求。 */
    var onEnter = {
      overview: function () {
        return Promise.all([loadEvents(), loadInvites(), loadOverviewCounts()])
          .then(draw);
      },
      events: function () { return loadEvents().then(draw); },
      "all-events": function () {
        return Promise.all([loadEvents(), loadArchived(true)]).then(draw);
      },
      orgs: function () { return loadOrgs().then(drawOrgs); },
      org: function () {
        S.orgEditDirty = false;
        return loadOrg().then(drawOrg);
      },
      "my-orgs": function () { return loadMyOrgs().then(drawMyOrgs); },
      "my-org": function () {
        S.orgEditDirty = false;
        return loadOrg().then(drawOrgSelf);
      },
      "my-org-manage": function () {
        S.orgEditDirty = false;
        return loadOrg().then(drawOrgManage);
      },
      "org-new": function () {
        // 每次进来都从空白开始：上一份没提交的人选留在屏幕上没有意义
        resetOrgForm();
        drawOrgNew();
      },
      "roster-admin": function () {
        // 进来先看「当前」那一栏：归档是少数情况，不该是默认视角
        S.adminArchived = false;
        S.adminRosters = null;
        S.adminSignups = null;
        markLoading("adminRosters");
        markLoading("adminSignups");
        return loadAdminLists().then(draw);
      },
      users: function () { return loadAccounts().then(draw); },
      "import-accounts": function () {
        // 每次进来都从空表开始：上一批的密码留在屏幕上没有意义
        resetImport();
        return loadImportRules().then(drawImport);
      },
      "approval-users": function () { return loadAccounts().then(draw); },
      audit: function () { return loadAudit().then(draw); },
      templates: function () { return loadTemplates(true).then(draw); },
      rosters: function () {
        S.rosterScope = S.rosterScope || "joined";
        return loadRosters(false).then(drawRosters);
      },
      signups: function () { return loadSignups(false).then(drawSignups); },
      screens: function () { return loadScreenPacks().then(drawScreens); },
      account: function () { return loadTokens().then(drawTokens); }
    };
    if (onEnter[wanted]) onEnter[wanted]();
    if ((wanted === "org" || wanted === "my-org" || wanted === "my-org-manage")
        && !S.orgId) {
      location.hash = "#/" + (wanted === "org" ? "orgs" : "my-orgs");
      return;
    }
    if (wanted === "signup") {
      if (!S.signupId) {
        location.hash = "#/signups";
        return;
      }
      loadSignup(S.signupId)
        .then(function () { return loadSignupEntries(false); })
        .then(drawSignup);
    }
    if (wanted === "assistant") {
      loadAssistant();
      resumeAssistant(prevRoute);
    }
    if (wanted === "write-approvals") {
      syncBackButton();
      loadApprovals().then(drawApprovals);
    }
    if (wanted === "roster") {
      if (!S.rosterId) {
        location.hash = "#/rosters";
        return;
      }
      loadRoster(S.rosterId)
        .then(function () { return loadEntries(false); })
        .then(drawRoster);
    }
  }

  /* ------------------------------------------------------------------ pages */
  function draw() {
    switch (S.route) {
      case "overview": return drawOverview();
      case "events": return drawEvents();
      case "rosters": return drawRosters();
      case "roster": return drawRoster();
      case "signups": return drawSignups();
      case "signup": return drawSignup();
      case "screens": return drawScreens();
      case "write-approvals": return drawApprovals();
      case "templates": return drawTemplates();
      case "new-event": return drawNewEvent();
      case "users": return drawUsers();
      case "import-accounts": return drawImport();
      case "all-events": return drawAllEvents();
      case "roster-admin": return drawRosterAdmin();
      case "orgs": return drawOrgs();
      case "my-orgs": return drawMyOrgs();
      case "my-org": return drawOrgSelf();
      case "my-org-manage": return drawOrgManage();
      case "org": return drawOrg();
      case "org-new": return drawOrgNew();
      case "approval-users": return drawApprovalUsers();
      case "approval-events": return drawApprovalEvents();
      case "modules": return drawModules();
      case "server": return drawServer();
      case "audit": return drawAudit();
      case "account": return drawAccount();
      case "assistant": return drawAssistant();
      case "appearance": return drawAppearance();
    }
  }

  function drawOverview() {
    var mine = myEvents();
    var pending = pendingAccounts();

    $("#overviewSub").textContent = "欢迎回来，" +
      ((S.me && (S.me.name || S.me.username)) || "");

    $("#statEvents").textContent = mine.length;
    var counts = S.overviewCounts || {};
    $("#statRosters").textContent =
      (counts.rosters === null || counts.rosters === undefined) ? "–" : counts.rosters;
    $("#statSignups").textContent =
      (counts.signups === null || counts.signups === undefined) ? "–" : counts.signups;
    $("#statInvites").textContent = S.invites.length;

    var showPending = canReview();
    $("#statPendingBox").hidden = !showPending;
    $("#statPending").textContent = pending.length;

    // 账号总数是整台机器的事，只给能看到账号清单的人
    var showAccounts = isSuper();
    $("#statAccountsBox").hidden = !showAccounts;
    if (showAccounts) {
      $("#statAccounts").textContent = S.accountsTotal || "–";
    }

    $("#overviewEvents").innerHTML = mine.length
      ? mine.slice(0, 4).map(function (e) { return eventCard(e); }).join("")
      : (loaded("events") ? '<div class="empty">还没有活动</div>'
                         : '<div class="empty">正在读取…</div>');

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
    var stack = $("#inviteStack");
    if (S.invites.length) {
      stack.hidden = false;
      $("#inviteList").innerHTML = S.invites.map(function (inv) {
        return '<div class="invite-request">' +
          '<div class="invite-text"><b>' + PX.esc(inv.title || "未命名活动") + "</b>" +
          '<span class="page-sub">' +
          PX.esc(inv.invitedBy ? inv.invitedBy + " 邀请你加入" : "邀请你加入") +
          "</span></div>" +
          '<div class="page-actions">' +
          '<button class="btn sm" type="button" data-request="decline" data-seq="' +
          inv.seq + '">拒绝</button>' +
          '<button class="btn sm primary" type="button" data-request="accept" data-seq="' +
          inv.seq + '">接受</button></div></div>';
      }).join("");
    } else {
      stack.hidden = true;
      $("#inviteList").innerHTML = "";
    }

    var owned = ownedEvents();
    $("#ownedEvents").innerHTML = owned.length
      ? owned.map(function (e) { return eventCard(e); }).join("")
      : (loaded("events") ? '<div class="empty">还没有创建过活动</div>'
                         : '<div class="empty">正在读取…</div>');

    var joined = joinedEvents();
    $("#joinedEvents").innerHTML = joined.length
      ? joined.map(function (e) { return eventCard(e); }).join("")
      : (loaded("events") ? '<div class="empty">还没有参与别人创建的活动</div>'
                         : '<div class="empty">正在读取…</div>');
  }

  // A preset is a saved snapshot of one activity's setup: module, parameters and
  // a suggested name.  Three shelves: what I starred, what I made, what is out
  // there for everyone.
  var ICON_STAR = '<svg viewBox="0 0 20 20" aria-hidden="true">' +
    '<path d="M10 2.9l2.2 4.5 5 .7-3.6 3.5.9 4.9-4.5-2.4-4.5 2.4.9-4.9L2.8 8.1l5-.7L10 2.9Z"/>' +
    "</svg>";

  function presetCard(p, shelf) {
    var tags = ['<span class="tag">' + PX.esc(p.moduleName) + "</span>"];
    if (p.builtin) tags.push('<span class="tag">内置</span>');
    if (shelf !== "mine" && p.ownerName) {
      tags.push('<span class="tag">' + PX.esc(p.ownerName) + "</span>");
    }
    var actions = p.ok
      ? '<button class="btn sm primary" type="button" data-preset-use="' +
        PX.esc(p.id) + '">创建活动</button>'
      : '<span class="tag disabled">' + PX.esc(p.reason || "不可用") + "</span>";
    if (p.mine && !p.builtin) {
      actions += '<button class="btn sm" type="button" data-preset-edit="' +
        PX.esc(p.id) + '">编辑</button>' +
        '<button class="btn sm danger" type="button" data-preset-drop="' +
        PX.esc(p.id) + '">删除</button>';
    }
    return '<div class="preset-card' + (p.ok ? "" : " is-off") + '">' +
      '<button class="preset-star' + (p.fav ? " on" : "") + '" type="button" ' +
        'data-preset-fav="' + PX.esc(p.id) + '" data-on="' + (p.fav ? "0" : "1") + '" ' +
        'aria-pressed="' + (p.fav ? "true" : "false") + '" ' +
        'title="' + (p.fav ? "取消收藏" : "收藏") + '">' + ICON_STAR + "</button>" +
      '<div class="preset-head">' +
        '<span class="preset-name">' + PX.esc(p.name) + "</span>" +
        '<span class="tag vis-' + PX.esc(p.visibility) + '">' +
          PX.esc(p.visibilityLabel) + "</span>" +
      "</div>" +
      '<div class="preset-tags">' + tags.join("") + "</div>" +
      (p.note ? '<p class="preset-note">' + PX.esc(p.note) + "</p>" : "") +
      '<div class="preset-actions">' + actions + "</div></div>";
  }

  function presetShelf(id, rows, shelf, empty) {
    var el = $(id);
    if (!el) return;
    el.innerHTML = rows.length
      ? rows.map(function (p) { return presetCard(p, shelf); }).join("")
      : '<div class="empty">' +
        PX.esc(loaded("templates") ? empty : "正在读取…") + "</div>";
  }

  /* 三栏：收藏 / 我创建的 / 预设广场。数据已经在手上就直接画，
     没有才摆"正在读取…"——收藏一下再整页闪白是这条路上最容易犯的错。 */
  function renderShelves() {
    var fav = S.templates.filter(function (p) { return p.fav; });
    var mine = S.templates.filter(function (p) { return p.mine && !p.builtin; });
    // the square is everything shared, whether or not it is also starred
    var square = S.templates.filter(function (p) {
      return p.builtin || p.visibility === "public" || p.visibility === "org";
    });
    presetShelf("#presetFavorites", fav, "fav", "还没有收藏的预设");
    presetShelf("#presetMine", mine, "mine", "还没有创建的预设");
    presetShelf("#presetSquare", square, "square", "还没有可用的预设");
  }

  function drawTemplates() {
    // importing brings content into the installation: only the station master
    var importBtn = $("#presetImportBtn");
    if (importBtn) importBtn.hidden = !isOwner();
    if (S.templates.length) {
      renderShelves();                 // 手上有一份就先画着，别先清空
    } else {
      markLoading("templates");
      presetShelf("#presetFavorites", [], "fav", "还没有收藏的预设");
      presetShelf("#presetMine", [], "mine", "还没有创建的预设");
      presetShelf("#presetSquare", [], "square", "还没有可用的预设");
    }
    return PX.api("/templates").then(function (d) {
      S.templates = d.presets || [];
      S.visibilityOptions = d.visibility || [];
      markLoaded("templates");
      renderShelves();
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function presetDialog(preset) {
    var p = preset || {};
    var options = S.visibilityOptions.length ? S.visibilityOptions : [
      { value: "private", label: "私有" },
      { value: "public", label: "公开" },
      { value: "org", label: "组织公开" }
    ];
    openOverlay("preset", preset ? "编辑预设" : "新建预设",
      '<div class="field"><label for="presetName">名称</label>' +
      '<input id="presetName" type="text" maxlength="40" value="' +
      PX.esc(p.name || "") + '"></div>' +
      '<div class="field"><label for="presetDesc">能力说明</label>' +
      '<textarea id="presetDesc" rows="3" maxlength="600">' +
      PX.esc(p.description || "") + "</textarea></div>" +
      '<div class="field"><label for="presetNote">备注</label>' +
      '<input id="presetNote" type="text" maxlength="200" value="' +
      PX.esc(p.note || "") + '"></div>' +
      '<div class="field"><label for="presetVis">可见性</label>' +
      '<select id="presetVis">' + options.map(function (o) {
        return '<option value="' + PX.esc(o.value) + '"' +
          ((p.visibility || "private") === o.value ? " selected" : "") + ">" +
          PX.esc(o.label) + "</option>";
      }).join("") + "</select></div>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="presetSave">保存</button></div>');
    $("#presetName").focus();
    $("#presetSave").addEventListener("click", function () {
      var payload = {
        id: p.id || "",
        name: $("#presetName").value.trim(),
        note: $("#presetNote").value.trim(),
        description: $("#presetDesc").value.trim(),
        visibility: $("#presetVis").value,
        module: p.module || (S.templates[0] && S.templates[0].module) || "",
        params: p.params || {},
        title: p.title || ""
      };
      PX.api("/preset", { method: "POST", body: payload })
        .then(function () {
          closeOverlay();
          PX.toast("已保存预设");
          return loadTemplates().then(drawTemplates);
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
  }

  /* Importing a preset file.  Its dependency decides whether it can be used at
   * all: a preset whose module is not installed is still imported, but only
   * after the operator has been told what that means. */
  function importPreset(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var raw;
      try {
        raw = JSON.parse(String(reader.result));
      } catch (e) {
        PX.toast("不是合法的 JSON", true);
        return;
      }
      sendPreset(raw, false);
    };
    reader.onerror = function () { PX.toast("无法读取该文件", true); };
    reader.readAsText(file, "utf-8");
  }

  function sendPreset(raw, force) {
    PX.api("/preset/import", {
      method: "POST",
      body: { preset: raw, force: force, visibility: "private" }
    }).then(function (d) {
      PX.toast(d.missing ? "已导入（不可用）" : "已导入预设");
      return loadTemplates(true).then(drawTemplates);
    }).catch(function (e) {
      if (!(e.data && e.data.dependencyMissing)) {
        PX.toast(PX.esc(e.message), true);
        return;
      }
      openOverlay("preset-import", "导入预设",
        '<p class="page-sub">' + PX.esc(e.message) + "</p>" +
        '<p class="page-sub">预设导入后将不可用。仍然导入吗？</p>' +
        '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
        '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
        '<button class="btn primary" type="button" id="presetForce">仍然导入</button>' +
        "</div>");
      $("#presetForce").addEventListener("click", function () {
        closeOverlay();
        sendPreset(raw, true);
      });
    });
  }

  function presetFavorite(id, on) {
    // 先就地把这颗星翻过来，再让服务端确认：整页重画一次，但不经过空白态
    PX.api("/preset/favorite", { method: "POST", body: { id: id, on: on } })
      .then(drawTemplates)
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function presetDelete(id) {
    var p = S.templates.filter(function (x) { return x.id === id; })[0] || {};
    openOverlay("preset-del", "删除预设",
      '<p class="page-sub">「' + PX.esc(p.name || id) +
      "」会被删掉，已经用它建出来的活动不受影响</p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn danger" type="button" id="presetDropYes">删除</button></div>');
    $("#presetDropYes").addEventListener("click", function () {
      PX.api("/preset/delete", { method: "POST", body: { id: id } })
        .then(function () {
          closeOverlay();
          PX.toast("已删除");
          return loadTemplates().then(drawTemplates);
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
  }

  // Snapshot an activity as it is set up right now.
  function saveAsPreset(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    closeCardMenu();
    openOverlay("preset-save", "保存为预设",
      '<p class="page-sub">把「' + PX.esc(e ? eventTitle(e) : "") +
      "」现在的设定存成一份预设，下次建活动可以直接套用</p>" +
      '<div class="field" style="margin-top:14px"><label for="presetSaveName">名称</label>' +
      '<input id="presetSaveName" type="text" maxlength="40" value="' +
      PX.esc(e ? eventTitle(e) : "") + '"></div>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="presetSaveYes">保存</button></div>');
    $("#presetSaveName").focus();
    $("#presetSaveYes").addEventListener("click", function () {
      PX.api("/event/preset", {
        method: "POST",
        body: { seq: Number(seq), name: $("#presetSaveName").value.trim(),
                visibility: "private" }
      }).then(function (d) {
        closeOverlay();
        PX.toast("已保存预设「" + PX.esc(d.name) + "」");
        return loadTemplates();
      }).catch(function (err) { PX.toast(PX.esc(err.message), true); });
    });
  }

  function drawModules() {
    markLoading("modules");
    $("#moduleTable tbody").innerHTML = rowsOrEmpty(5, "modules", "");
    return PX.api("/modules").then(function (d) {
      S.modules = d.modules || [];
      markLoaded("modules");
      $("#moduleTable tbody").innerHTML = S.modules.map(function (m) {
        // 申请了平台保留的高危能力：挂一枚胶囊，悬停/点一下能看到申请了哪几条。
        // 模块只有装与没装两种状态，装的时候说清楚，不在装好之后做逐条授权
        var tag = "";
        if ((m.superFeatures || []).length) {
          var names = m.superFeatures.map(function (f) { return f.name; }).join("、");
          tag = '<button class="badge-off" type="button" data-tip="申请了高危权限：' +
            PX.esc(names) + '" aria-label="申请了高危权限">' + ICON_ALERT + "高危</button>";
        }
        var removeBtn = m.builtin ? "" :
          '<button class="btn sm" type="button" data-module-remove="' +
          PX.esc(m.id) + '">卸载</button> ';
        return "<tr" + (m.enabled ? "" : ' class="muted-row"') + ">" +
          "<td><b>" + PX.esc(m.name) + "</b> " +
          (m.enabled ? "" : '<span class="tag">已停用</span>') + tag + "<br>" +
          '<span class="mono">' + PX.esc(m.id) + "</span>" +
          (m.builtin ? "" : ' <span class="tag">已导入</span>') + "</td>" +
          '<td class="mono">' + PX.esc(m.version) + "</td>" +
          "<td>" + PX.esc(m.author || "—") + "</td>" +
          '<td class="mono">' + (m.events || 0) + "</td>" +
          '<td class="actions">' + removeBtn +
          '<button class="btn sm' + (m.enabled ? " danger" : "") +
          '" type="button" data-module-toggle="' + PX.esc(m.id) +
          '" data-on="' + (m.enabled ? "0" : "1") + '">' +
          (m.enabled ? "停用" : "启用") + "</button></td></tr>";
      }).join("") || rowsOrEmpty(5, "modules", "还没有装模块");
    }).catch(function (e) {
      if (e.status === 403) return showDenied("modules");
      PX.toast(PX.esc(e.message), true);
    });
  }

  /* 导入模块：先把文件送到服务端校验并解到暂存目录，**装之前**把模块申请的能力摆出来
     （高危的那几条另起一句红字），点 [安装] 才真的落盘。装模块等于让一段别人写的代码
     在这个进程里跑，所以这一步要把话说清楚 */
  function uploadModule(file) {
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      PX.api("/module/upload", {
        method: "POST",
        body: { filename: file.name, data: String(reader.result || "") }
      }).then(installModuleDialog).catch(function (e) {
        PX.toast(PX.esc(e.message), true);
      });
    };
    reader.onerror = function () { PX.toast("无法读取该文件", true); };
    reader.readAsDataURL(file);
  }

  function installModuleDialog(info) {
    if (!info) return;
    var feats = info.features || [];
    var risky = info.superFeatures || [];
    var rows = function (list, withNote) {
      return '<ul class="cap-list">' + list.map(function (f) {
        var name = withNote ? f.name : f;
        return "<li><b class=\"mono\">" + PX.esc(name) + "</b>" +
          (withNote && f.note ? "<br>" + PX.esc(f.note) : "") + "</li>";
      }).join("") + "</ul>";
    };
    var plain = feats.filter(function (n) {
      return !(info.superFeatures || []).some(function (f) { return f.name === n; });
    });
    openOverlay("module-install", "导入模块",
      '<dl class="kv">' +
      "<dt>模块</dt><dd><b>" + PX.esc(info.name) + "</b></dd>" +
      '<dt>标识</dt><dd class="mono">' + PX.esc(info.id) + "</dd>" +
      '<dt>版本</dt><dd class="mono">' + PX.esc(info.version) + "</dd>" +
      "<dt>作者</dt><dd>" + PX.esc(info.author || "—") + "</dd>" +
      "<dt>体积</dt><dd>" + PX.esc(info.files + " 个文件 · " + info.sizeText) + "</dd>" +
      "</dl>" +
      (plain.length ? "<p class=\"page-sub\" style=\"margin-top:14px\">申请的能力</p>" +
        rows(plain, false) : "") +
      (risky.length
        ? '<p class="super-risk" style="margin-top:14px">模块申请以下高危权限</p>' +
          rows(risky, true)
        : "") +
      '<p class="hint" style="margin-top:14px">模块的代码会在服务进程里运行。' +
      (risky.length ? "上面那几条能力让它能碰到平台管的东西之外。" : "") +
      "装好之后随时可以停用或卸载。</p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="moduleInstallGo">安装</button></div>');
    $("#moduleInstallGo").addEventListener("click", function () {
      closeOverlay();
      apiSudo("/module/install",
              { method: "POST", body: { stage: info.stage, version: info.version } },
              "导入模块")
        .then(function (d) {
          PX.toast("已装上 " + ((d && d.id) || info.id));
          return drawModules();
        })
        .catch(fail);
    });
  }

  function removeModule(id, name) {
    confirmHighRisk("卸载 " + (name || id),
      "模块的目录会被删掉，用它建的活动不会自动清理。确认继续？", "卸载", true,
      function () {
        apiSudo("/module/remove", { method: "POST", body: { id: id } }, "卸载模块")
          .then(function () {
            PX.toast("已卸载");
            return drawModules();
          })
          .catch(fail);
      });
  }

  /* 停用会连带影响使用这个模块的活动，所以照别的破坏性动作来：
     先提权；确认框里只写一句确认，不铺开说明（见 ui-naming 的二次确认写法） */
  function toggleModule(id, on) {
    var label = on ? "启用模块" : "停用模块";
    var note = on
      ? "启用后，使用该模块的活动恢复可用。"
      : "停用后，使用该模块的活动一并不可用。确认继续？";
    confirmHighRisk(label, note, on ? "启用" : "停用", !on, function (why) {
      apiSudo("/module/toggle", { method: "POST", body: { id: id, enabled: on } },
              label, why)
        .then(function () {
          PX.toast(on ? "已启用" : "已停用");
          // 活动列表跟着刷新：卡片上的「不可用」标记由它决定，别等实时通知
          return Promise.all([loadTemplates(true), loadEvents()])
            .then(function () { drawModules(); });
        })
        .catch(fail);
    });
  }

  function loadTemplates(force) {
    if (S.templates.length && !force) return Promise.resolve();
    markLoading("templates");
    return PX.api("/templates").then(function (d) {
      S.templates = d.presets || d.templates || [];
      S.visibilityOptions = d.visibility || [];
      markLoaded("templates");
    }).catch(function () { S.templates = []; markLoaded("templates"); });
  }

  /* ------------------------------------------------------------- drafts */
  /* Leaving the create form with typing in it asks what to do.  A draft is the
   * same payload the create endpoint takes, saved on the account so it is there
   * on another machine too. */
  function draftPayload() {
    return {
      title: $("#neTitle").value,
      note: $("#neNote").value,
      params: collectKindParams(),
      members: (S.invited || []).slice()
    };
  }

  var importBypass = false;

  function askImportLeave(into) {
    openOverlay("import-leave", "离开导入账号？",
      '<p class="page-sub">账号尚未导入。' +
      "离开页面将清除导入表及其中的初始密码。</p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">继续编辑</button>' +
      '<button class="btn danger" type="button" id="importLeaveGo">离开</button></div>');
    $("#importLeaveGo").addEventListener("click", function () {
      closeOverlay();
      importBypass = true;
      S.import = null;
      go(into);
      setTimeout(function () { importBypass = false; }, 80);
    });
  }

  var orgBypass = false;

  /* 创建组织填了一半就走去别处：先说清这一页的内容没有留存 */
  function askOrgLeave(into) {
    openOverlay("org-leave", "离开创建组织？",
      '<p class="page-sub">已填写的内容不会保留。</p>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">继续编辑</button>' +
      '<button class="btn danger" type="button" id="orgLeaveGo">离开</button></div>');
    $("#orgLeaveGo").addEventListener("click", function () {
      closeOverlay();
      orgBypass = true;
      resetOrgForm();
      go(into);
      setTimeout(function () { orgBypass = false; }, 80);
    });
  }

  function markDirty() { S.dirty = true; }

  /* 离开创建页时把这张表单清空：不然下次进来还挂着上一次的名字与人像。
     已经存成草稿的内容不走这里——回来时 restoreDraft 会把它填回去。 */
  function forgetKindForm() {
    S.invited = [];
    S.people = {};
    S.currentFields = [];
    var title = $("#neTitle");
    var note = $("#neNote");
    if (title) title.value = "";
    if (note) note.value = "";
    renderInviteChips();
  }

  var reloadTimer = null;
  function scheduleReload() {
    if (reloadTimer) return;
    reloadTimer = setTimeout(function () {
      reloadTimer = null;
      if (S.dirty) return;          // do not throw away what is being typed
      reloadAll().catch(function () {});
    }, 400);
  }

  function saveDraft() {
    var payload = draftPayload();
    S.dirty = false;
    return PX.api("/draft", {
      method: "POST",
      body: { key: S.templateId || "picker", payload: payload }
    }).then(function () {
      PX.toast("已保存草稿");
    }).catch(function (e) {
      S.dirty = true;
      PX.toast(PX.esc(e.message || "草稿没存上"), true);
    });
  }

  function dropDraft() {
    S.dirty = false;
    return PX.api("/draft", {
      method: "POST",
      body: { key: S.templateId || "picker", payload: null }
    }).catch(function () { /* a draft that will not drop is not worth a toast */ });
  }

  function restoreDraft(tpl) {
    return PX.api("/draft?key=" + encodeURIComponent(S.templateId || "picker"))
      .then(function (d) {
        if (!d.draft) return false;
        var payload;
        try { payload = JSON.parse(d.draft.payload); } catch (e) { return false; }
        if (!payload || typeof payload !== "object") return false;
        if (payload.title) $("#neTitle").value = payload.title;
        if (payload.note) $("#neNote").value = payload.note;
        Object.keys(payload.params || {}).forEach(function (key) {
          var el = $('[data-kind-field="' + key + '"]');
          if (!el) return;
          if (el.type === "checkbox") el.checked = !!payload.params[key];
          else el.value = payload.params[key];
        });
        return loadPeople(payload.members || []).then(function (map) {
          S.people = map;
          S.invited = Object.keys(map).map(Number);
          renderInviteChips();
          var rows = $$("[data-show-when]", $("#kindFields"));
          rows.forEach(function (row) {
            var driver = $('[data-kind-field="' + row.getAttribute("data-show-when") + '"]');
            row.hidden = !driver || String(driver.value) !== row.getAttribute("data-show-is");
          });
          PX.toast("已恢复上次的草稿");
          return true;
        });
      })
      .catch(function () { return false; });
  }

  // The three answers when the form has typing in it and the page is leaving.
  function askDraftChoice(into) {
    openOverlay("draft", "离开创建活动？",
      '<p class="page-sub">还没有创建。填过的内容可以留成草稿，下次进来接着改</p>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">继续编辑</button>' +
      '<button class="btn danger" type="button" id="draftDrop">丢弃</button>' +
      '<button class="btn primary" type="button" id="draftKeep">保存草稿</button></div>');
    var leave = function () {
      guardBypass = true;
      S.dirty = false;
      go(into);
      setTimeout(function () { guardBypass = false; }, 80);
    };
    $("#draftDrop").addEventListener("click", function () {
      closeOverlay();
      dropDraft().then(leave);
    });
    $("#draftKeep").addEventListener("click", function () {
      closeOverlay();
      saveDraft().then(leave);
    });
  }

  function templateById(id) {
    return S.templates.filter(function (t) { return t.id === id; })[0];
  }

  function drawNewEvent() {
    if (!S.templates.length) {
      // a direct link carries the template in the hash, so the list may not be
      // here yet; load it and draw again
      loadTemplates().then(drawNewEvent);
      return;
    }
    var tpl = S.templateId ? templateById(S.templateId) : null;
    if (S.templateId && !tpl) {
      go("new-event", true);      // unknown template: back to the picker
      return;
    }
    $("#kindPicker").hidden = !!tpl;
    $("#kindForm").hidden = !tpl;
    if (tpl) drawKindForm(tpl);
    else $("#kindGrid").innerHTML = renderKindTiles();
  }

  // What this preset can do, next to the form that narrows it further.
  function renderPresetInfo(tpl) {
    var box = $("#presetInfo");
    if (!box) return;
    var rows = [];
    rows.push('<div class="preset-info-name">' + PX.esc(tpl.name) + "</div>");
    var tags = ['<span class="tag">' + PX.esc(tpl.moduleName || tpl.module) + "</span>"];
    if (tpl.builtin) tags.push('<span class="tag">内置</span>');
    if (tpl.visibilityLabel) {
      tags.push('<span class="tag vis-' + PX.esc(tpl.visibility) + '">' +
        PX.esc(tpl.visibilityLabel) + "</span>");
    }
    rows.push('<div class="preset-tags">' + tags.join("") + "</div>");
    if (tpl.description) {
      rows.push('<p class="preset-info-text">' + PX.esc(tpl.description) + "</p>");
    }
    if (tpl.note) {
      rows.push('<p class="preset-info-note">' + PX.esc(tpl.note) + "</p>");
    }
    if (tpl.moduleVersion) {
      rows.push('<p class="preset-info-meta">模块版本 ' +
        PX.esc(tpl.moduleVersion) + "</p>");
    }
    box.innerHTML = rows.join("");
  }

  // Required fields that were left empty: mark them and put the cursor in the
  // first one, instead of letting the server say no afterwards.
  function markMissing(fields) {
    var missing = [];
    $$(".is-missing").forEach(function (el) { el.classList.remove("is-missing"); });
    var title = $("#neTitle");
    if (!title.value.trim()) {
      title.classList.add("is-missing");
      missing.push(title);
    }
    fields.forEach(function (f) {
      if (!f.required) return;
      var el = $('[data-kind-field="' + f.key + '"]');
      if (!el || el.closest("[hidden]")) return;
      var empty = el.type === "checkbox" ? false : !String(el.value || "").trim();
      if (empty) {
        el.classList.add("is-missing");
        missing.push(el);
      }
    });
    if (missing.length) {
      missing[0].focus();
      missing[0].scrollIntoView({ block: "center" });
      return missing.length;
    }
    return 0;
  }

  function renderKindTiles() {
    var q = S.templateFilter.trim().toLowerCase();
    var rows = S.templates.filter(function (t) {
      if (!q) return true;
      return (t.name + " " + t.id + " " + (t.description || "") + " " + t.moduleName)
        .toLowerCase().indexOf(q) >= 0;
    });
    // 收藏的排前面（收藏本身在「预设管理」里改，这里只按它排序、只读显示那颗星）
    rows = rows.slice().sort(function (a, b) {
      return (b.fav ? 1 : 0) - (a.fav ? 1 : 0);
    });
    if (!rows.length) {
      return '<div class="empty">' + (S.templates.length
        ? "\u6ca1\u6709\u5339\u914d\u7684\u6a21\u677f"
        : "\u8fd8\u6ca1\u6709\u53ef\u7528\u7684\u6a21\u677f") + "</div>";
    }
    return rows.map(function (t) {
      var meta = [t.moduleName, t.builtin ? "\u5185\u7f6e" : "\u7528\u6237\u5bfc\u5165"]
        .filter(Boolean).join(" \u00b7 ");
      return '<button class="kind-tile' + (t.ok ? "" : " is-off") + '" type="button" ' +
        (t.ok ? 'data-template="' + PX.esc(t.id) + '"' : "disabled") + ">" +
        (t.fav ? '<span class="kind-fav" aria-label="已收藏" title="已收藏">' +
                 ICON_STAR + "</span>" : "") +
        '<span class="kind-name">' + PX.esc(t.name) + "</span>" +
        '<span class="kind-meta">' + PX.esc(meta) + "</span>" +
        (t.description ? '<span class="kind-desc">' + PX.esc(t.description) + "</span>" : "") +
        (t.ok ? "" : '<span class="tag disabled">' + PX.esc(t.reason || "\u4e0d\u53ef\u7528") + "</span>") +
        "</button>";
    }).join("");
  }

  function drawKindForm(tpl) {
    var kind = tpl;                       // the module supplies the fields
    $("#kindFormTitle").textContent = tpl.name;
    // the header already carries the template name; the version is enough here
    $("#kindFormSub").textContent = "v" + (tpl.moduleVersion || tpl.version || "");
    $("#kindFormSub").title = [tpl.moduleName, tpl.id, tpl.module]
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
          (((tpl.params && f.key in tpl.params) ? tpl.params[f.key] : f.default)
            ? " checked" : "") +
          "><span>" + PX.esc(f.label) + "</span></label>" +
          help + "</div>";
      }
      if (f.type === "select") {
        var opts = (f.options || []).map(function (o) {
          var preset = (tpl.params && f.key in tpl.params) ? tpl.params[f.key] : f.default;
          var sel = (!f.required && String(preset) === String(o.value)) ? " selected" : "";
          return '<option value="' + PX.esc(o.value) + '"' + sel + ">" +
            PX.esc(o.label || o.value) + "</option>";
        }).join("");
        return box(head + '<select id="' + id + '"' + attr +
          (f.required ? " required" : "") + ">" +
          (f.required ? '<option value="">\u8bf7\u9009\u62e9</option>' : "") + opts +
          "</select>" + help);
      }
      if (f.type === "int") {
        var preset = (tpl.params && f.key in tpl.params) ? tpl.params[f.key] : f.default;
        var hasDef = preset !== undefined && preset !== null;
        return box(head + '<input id="' + id + '" type="number"' +
          attr + (hasDef ? ' value="' + PX.esc(String(preset)) + '"' : "") +
          (f.min !== undefined && f.min !== null ? ' min="' + f.min + '"' : "") +
          (f.max !== undefined && f.max !== null ? ' max="' + f.max + '"' : "") +
          ">" + help);
      }
      var def = (tpl.params && f.key in tpl.params) ? tpl.params[f.key] : f.default;
      return box(head + '<input id="' + id + '" type="text"' +
        attr + (def !== undefined && def !== null && def !== ""
          ? ' value="' + PX.esc(String(def)) + '"' : "") + ">" + help);
    }).join("");
    $("#createErr").textContent = "";
    if (!$("#neTitle").value) $("#neTitle").value = tpl.title || "";
    S.currentFields = kind.fields || [];
    renderPresetInfo(tpl);
    renderInviteChips();
    bindShowWhen($("#kindFields"));
    S.dirty = false;
    restoreDraft(tpl);
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

  // Importing a config only fills the form; anything it does not mention keeps
  // whatever is already there, and nothing is submitted until the button.
  function importConfig(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var data;
      try {
        data = JSON.parse(String(reader.result));
      } catch (e) {
        $("#createErr").textContent = "\u8fd9\u4e2a\u6587\u4ef6\u4e0d\u662f\u5408\u6cd5\u7684 JSON";
        return;
      }
      if (!data || typeof data !== "object" || Array.isArray(data)) {
        $("#createErr").textContent = "\u914d\u7f6e\u6587\u4ef6\u5e94\u8be5\u662f\u4e00\u4e2a\u5bf9\u8c61";
        return;
      }
      var filled = 0;
      if (typeof data.title === "string" && data.title.trim()) {
        $("#neTitle").value = data.title.trim();
        filled += 1;
      }
      if (typeof data.note === "string") {
        $("#neNote").value = data.note;
        filled += 1;
      }
      var params = data.params && typeof data.params === "object" ? data.params : data;
      $$("[data-kind-field]").forEach(function (el) {
        var key = el.getAttribute("data-kind-field");
        if (!(key in params)) return;
        var value = params[key];
        if (el.type === "checkbox") {
          el.checked = !!value;
        } else if (value !== null && value !== undefined && value !== "") {
          el.value = String(value);
        } else {
          return;
        }
        filled += 1;
        el.dispatchEvent(new Event("change", { bubbles: true }));
      });
      $("#createErr").textContent = filled
        ? ""
        : "\u8fd9\u4e2a\u6587\u4ef6\u91cc\u6ca1\u6709\u80fd\u7528\u7684\u5b57\u6bb5";
      if (filled) PX.toast("\u5df2\u586b\u5165 " + filled + " \u9879\uff0c\u63d0\u4ea4\u524d\u53ef\u4ee5\u7ee7\u7eed\u6539");
    };
    reader.onerror = function () {
      $("#createErr").textContent = "\u8bfb\u4e0d\u4e86\u8fd9\u4e2a\u6587\u4ef6";
    };
    reader.readAsText(file);
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

  /* 选过的人排成一行：圆头像在上一行，名字在下面，不加底色 */
  function renderInviteChips() {
    var html = S.invited.map(function (id) {
      var a = S.people[id];
      if (!a) return "";
      return '<span class="invite-chip">' +
        personFaceHtml(a) +
        '<span class="nm">' + PX.esc(a.label || "") + "</span>" +
        '<button type="button" data-uninvite="' + a.id + '" aria-label="移出">×</button>' +
        "</span>";
    }).join("");
    $("#inviteChips").innerHTML = html;
    // 只说明这一步的后果，不报数：谁被选中，上面的人像已经写着
    $("#inviteHint").textContent = S.invited.length ? "创建后发出邀请" : "";
  }

  /* 选人：面板这边只把接口接上，弹窗本身是 assets/js/people.js（活动后台共用同一份）。
     scene 决定服务端怎么判"能不能查人"：转移负责人、名单授权、邀请成员各有一套。 */
  function pickPeople(opts) {
    var fixed = [];
    if (opts.scene) fixed.push("scene=" + encodeURIComponent(opts.scene));
    if (opts.seq) fixed.push("seq=" + encodeURIComponent(opts.seq));
    if (opts.rosterId) fixed.push("id=" + encodeURIComponent(opts.rosterId));
    return window.PeoplePicker.open({
      title: opts.title,
      mode: opts.mode || "multi",
      chosen: opts.chosen || [],
      exclude: opts.exclude || [],
      disabled: opts.disabled || [],
      empty: opts.empty || "没有匹配的账号",
      avatarUrl: function (p) { return personAvatarUrl(p); },
      apply: opts.apply,
      search: function (q) {
        var qs = fixed.slice();
        if (q) qs.push("q=" + encodeURIComponent(q));
        return PX.api("/people" + (qs.length ? "?" + qs.join("&") : ""))
          .then(function (d) { return d.people || []; });
      }
    });
  }

  function openInvitePicker() {
    // 与「活动设置 › 邀请成员」是同一个弹窗：先看人再点，点错了再点一次取消。
    // 选过谁留在页面上的人像行里；这里只管这一次的勾选。
    pickPeople({
      title: "邀请成员",
      mode: "multi",
      chosen: S.invited.map(function (id) { return S.people[id]; }).filter(Boolean),
      exclude: [S.me && S.me.id],
      apply: function (people) {
        S.invited = people.map(function (p) { return p.id; });
        S.people = {};
        people.forEach(function (p) { S.people[p.id] = p; });
        // 选人发生在弹窗里，表单收不到 input 事件：脏标记要在这里单独记一笔
        markDirty();
        renderInviteChips();
      }
    });
  }

  function removeInvite(id) {
    S.invited = S.invited.filter(function (x) { return x !== id; });
    delete S.people[id];
    markDirty();
    renderInviteChips();
  }

  /* 草稿里只存了账号 id：拿回来时把显示名与头像补上（/people 给的就是这些） */
  function loadPeople(ids) {
    var keep = (ids || []).filter(function (x) { return x; });
    if (!keep.length) return Promise.resolve({});
    return PX.api("/people?ids=" + keep.join(","))
      .then(function (d) {
        var map = {};
        (d.people || []).forEach(function (p) { map[p.id] = p; });
        return map;
      })
      .catch(function () { return {}; });
  }

  function createEvent() {
    var err = $("#createErr");
    S.dirty = false;
    err.textContent = "";
    var body = {
      template: S.templateId,
      title: $("#neTitle").value.trim(),
      note: $("#neNote").value.trim(),
      params: collectKindParams(),
      members: S.invited.slice()
    };
    var gaps = markMissing(S.currentFields || []);
    if (gaps) {
      err.textContent = "\u8fd8\u6709 " + gaps + " \u9879\u5fc5\u586b\u6ca1\u586b";
      return;
    }
    var btn = $("#kindCreate");
    btn.disabled = true;
    // 新标签页要在这个点击里同步开出来：等接口回来再开会被浏览器当成弹窗拦掉
    var tab = window.open("about:blank", "_blank");
    PX.api("/events/create", { method: "POST", body: body })
      .then(function (d) {
        dropDraft();          // the draft became an activity
        if (tab && !tab.closed) {
          tab.location.href = d.event.admin;
        } else {
          // 被拦了也要让人进得去，至少别丢地址
          PX.toast("活动已创建，新标签页被拦了：去「我的活动」里打开它");
        }
        PX.toast("已创建，活动后台在新标签页打开了");
        go("events");          // 主站回到「我的活动」
      })
      .catch(function (e) {
        if (tab && !tab.closed) tab.close();
        err.textContent = e.message;
        btn.disabled = false;
      });
  }

  /* ------------------------------------------------------------------ 组织 --- */
  /* 组织管的是归属：谁属于哪个组织、在里面是什么身份。活动暂不挂在组织上；
   * 组织里的身份也不带来平台权力——不改角色、不重置密码、不删账号。 */
  function orgById(id) {
    return (S.orgs || []).filter(function (o) { return o.id === id; })[0] || null;
  }

  function resetOrgForm() {
    S.orgForm = { heads: [], admins: [], members: [] };
    S.orgPeople = {};
    ["orgName", "orgId", "orgNote"].forEach(function (id) {
      var el = $("#" + id);
      if (el) {
        el.value = "";
        el.removeAttribute("aria-invalid");
      }
    });
    ["#orgHeadChips", "#orgAdminChips", "#orgMemberChips"].forEach(function (sel) {
      var box = $(sel);
      if (box) box.innerHTML = "";
    });
    orgClearErrors();
  }

  /* 填了一半就走，先问一句：这一页的内容没有别的地方存着 */
  function orgHasContent() {
    var f = S.orgForm || {};
    var text = ["orgName", "orgId", "orgNote"].some(function (id) {
      var el = $("#" + id);
      return el && el.value.trim();
    });
    return !!text || ["heads", "admins", "members"].some(function (k) {
      return (f[k] || []).length > 0;
    });
  }

  function loadOrgs() {
    markLoading("orgs");
    return PX.api("/orgs").then(function (d) {
      S.orgs = d.orgs || [];
      markLoaded("orgs");
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function orgRows() {
    var q = (S.orgFilter || "").trim().toLowerCase();
    var rows = S.orgs || [];
    if (!q) return rows;
    return rows.filter(function (o) {
      return (o.name || "").toLowerCase().indexOf(q) >= 0 ||
             (o.id || "").toLowerCase().indexOf(q) >= 0;
    });
  }

  function drawOrgs() {
    var body = $("#orgsTable tbody");
    if (!body) return;
    var rows = orgRows();
    var total = (S.orgs || []).length;
    var countEl = $("#orgsCount");
    if (countEl) {
      countEl.textContent = total ? "共 " + total + " 个组织" +
        (rows.length !== total ? "，匹配 " + rows.length + " 个" : "") : "";
    }
    body.innerHTML = rows.length ? rows.map(function (o) {
      // 这一列只写人数：谁在里面、各是什么身份，点进去看
      return "<tr>" +
        '<td><button class="link-btn" type="button" data-org-open="' + PX.esc(o.id) +
        '">' + PX.esc(o.name) + "</button></td>" +
        '<td class="mono">' + PX.esc(o.id) + "</td>" +
        '<td class="mono">' + o.members + "</td>" +
        "<td>" + (o.archived ? '<span class="tag warn">已归档</span>' : "—") + "</td>" +
        '<td class="actions"><button class="btn sm" data-org-open="' + PX.esc(o.id) +
        '">管理</button></td></tr>';
    }).join("") : rowsOrEmpty(5, "orgs",
      (S.orgFilter || "").trim() ? "没有匹配的组织" : "还没有组织");
  }

  /* ------------------------------------------------------------ 我的组织 --- */
  /* 我参加的组织：一张卡片一个组织。卡片上先留出背景图的位置，
     等外观那一块做完，横幅就摆进同一个框里。 */
  function loadMyOrgs() {
    markLoading("myOrgs");
    return PX.api("/org/mine").then(function (d) {
      S.myOrgs = d.orgs || [];
      markLoaded("myOrgs");
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function drawMyOrgs() {
    var grid = $("#myOrgGrid");
    if (!grid) return;
    var rows = S.myOrgs || [];
    var count = $("#myOrgsCount");
    if (count) count.textContent = rows.length ? "共 " + rows.length + " 个" : "";
    grid.innerHTML = rows.length ? rows.map(function (o) {
      var src = orgCoverSrc(o);
      return '<article class="org-card" role="listitem">' +
        '<button class="org-card-open" type="button" data-org-self="' + PX.esc(o.id) +
        '" aria-label="打开 ' + PX.esc(o.name) + '">' +
        '<span class="org-card-cover' + (src ? "" : " no-cover") + '">' +
        (src ? '<img alt="" loading="lazy" src="' + PX.esc(src) + '"' +
          (orgCoverSrcset(o) ? ' srcset="' + PX.esc(orgCoverSrcset(o)) + '"' +
            ' sizes="(max-width: 560px) 100vw, (max-width: 980px) 50vw, 33vw"' : "") +
          ">" : "") +
        (o.myRoleLabel
          ? '<span class="org-card-role"><span class="tag">' + PX.esc(o.myRoleLabel) +
            "</span></span>"
          : "") +
        '<span class="org-card-people" title="成员人数">' +
        '<svg viewBox="0 0 20 20" aria-hidden="true">' +
        '<circle cx="7.4" cy="6.6" r="2.7"/>' +
        '<path d="M2 16.2a5.4 5.4 0 0 1 10.8 0M12.5 4.7a2.5 2.5 0 1 1 .4 4.8' +
        'M13.3 11.5a4.8 4.8 0 0 1 4.7 4.7"/></svg>' +
        "<span>" + o.members + "</span></span>" +
        "</span>" +
        '<span class="org-card-name">' + PX.esc(o.name) + "</span>" +
        "</button></article>";
    }).join("") : '<p class="hint">' +
      (loaded("myOrgs") ? "还没有参加组织" : "正在读取…") + "</p>";
  }

  /* 外观那张图：外链直接用，站内那份按账号去取 */
  function orgCoverSrc(o, width) {
    if (!o || !o.cover) return "";
    if (/^https?:\/\//i.test(o.cover)) return o.cover;
    return PX.PREFIX + "/api/panel/org_cover/" + encodeURIComponent(o.id) + "?v=" +
      Math.round(o.coverAt || 0) + (width ? "&w=" + width : "");
  }

  /* 卡片上的图按屏宽挑一档：小图不必等整张原图下来 */
  function orgCoverSrcset(o) {
    if (!o || !o.cover || /^https?:\/\//i.test(o.cover)) return "";
    var widths = o.coverWidths || [];
    if (!widths.length) return "";
    return widths.map(function (w) {
      return orgCoverSrc(o, w) + " " + w + "w";
    }).join(", ");
  }

  /* 上传前在浏览器里画几档小图：这台机器只有标准库，解不了 JPEG/WebP，
     缩图这件事只能由上传方做。原图照旧整张留着，需要时能取到全分辨率。 */
  var COVER_WIDTHS = [480, 960, 1600];

  function coverVariants(file, done) {
    var img = new Image();
    var url = URL.createObjectURL(file);
    var finished = false;

    function end(list) {
      if (finished) return;
      finished = true;
      URL.revokeObjectURL(url);
      done(list);
    }

    img.onload = function () {
      var jobs = COVER_WIDTHS.filter(function (w) { return w < img.naturalWidth; });
      if (!jobs.length || !window.HTMLCanvasElement) {
        end([]);
        return;
      }
      var out = [];
      var left = jobs.length;
      jobs.forEach(function (w) {
        var canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = Math.max(1, Math.round(img.naturalHeight * w / img.naturalWidth));
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
          if (!blob) {
            if (--left === 0) end(out);
            return;
          }
          var reader = new FileReader();
          reader.onload = function () {
            out.push({ w: w, data: String(reader.result || "") });
            if (--left === 0) end(out);
          };
          reader.onerror = function () { if (--left === 0) end(out); };
          reader.readAsDataURL(blob);
        }, "image/webp", 0.82);
      });
    };
    img.onerror = function () { end([]); };
    img.src = url;
  }

  function coverSizeText(bytes) {
    if (!bytes) return "";
    if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
    return Math.round(bytes / 1024) + " KB";
  }

  /* 一页里可能有两处外观（平台侧、组织自己的管理页）：传进来的是那一组元素的 id 前缀 */
  function drawCoverUi(prefix, o) {
    var box = $("#" + prefix + "Preview");
    var size = $("#" + prefix + "Size");
    var clear = $("#" + prefix + "Clear");
    if (!box) return;
    var src = orgCoverSrc(o);
    box.innerHTML = src
      ? '<img alt="" src="' + PX.esc(src) + '">'
      : '<span class="org-cover-hint">还没有图片</span>';
    box.classList.toggle("empty", !src);
    if (size) {
      size.textContent = o && o.coverBytes
        ? "站内图片 " + coverSizeText(o.coverBytes) : "";
    }
    if (clear) clear.hidden = !src;
  }

  function askCoverLink(scope) {
    var d = S.orgDetail;
    if (!d) return;
    openOverlay("org-cover", "使用图片链接",
      '<p class="page-sub">填图床地址，直接用那张图，不占站内空间。</p>' +
      '<div class="field"><label for="orgCoverUrl">图片地址</label>' +
      '<input id="orgCoverUrl" type="text" maxlength="500" autocomplete="off"' +
      ' placeholder="https://…"></div>' +
      '<p class="err" id="orgCoverErr" hidden></p>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="orgCoverGo">保存</button></div>');
    $("#orgCoverGo").addEventListener("click", function () {
      var url = $("#orgCoverUrl").value.trim();
      var err = $("#orgCoverErr");
      if (!/^https?:\/\/\S+$/i.test(url)) {
        err.textContent = "请填写以 http 或 https 开头的图片地址";
        err.hidden = false;
        return;
      }
      closeOverlay();
      orgWrite(scope, "/org/cover",
        { id: d.org.id, url: url, scope: scope }, "更换组织图片")
        .then(function () { PX.toast("已更新"); return afterOrgChange(); })
        .catch(fail);
    });
  }

  function uploadCover(scope, file) {
    var d = S.orgDetail;
    if (!d || !file) return;
    if (file.size > 2 * 1024 * 1024) {
      PX.toast("图片超过 2 MB", true);
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      coverVariants(file, function (variants) {
        orgWrite(scope, "/org/cover",
          { id: d.org.id, data: String(reader.result || ""), variants: variants,
            scope: scope }, "上传组织图片").then(function () {
          PX.toast("已上传");
          return afterOrgChange();
        }).catch(fail);
      });
    };
    reader.onerror = function () { PX.toast("无法读取该文件", true); };
    reader.readAsDataURL(file);
  }

  function clearCover(scope) {
    var d = S.orgDetail;
    if (!d) return;
    orgWrite(scope, "/org/cover",
      { id: d.org.id, clear: true, scope: scope }, "清除组织图片")
      .then(function () { PX.toast("已清除"); return afterOrgChange(); })
      .catch(fail);
  }

  /* 选过的人排成一行：与创建活动里的成员行同一套样子。
     头像要按账号去取：名单里给的是存了哪张图，不是能直接当 src 的地址。 */
  function personAvatarUrl(p) {
    if (!p || !p.avatar) return "";
    if (/^https?:\/\//i.test(p.avatar)) return p.avatar;
    return PX.PREFIX + "/api/panel/avatar/" + p.id + "?v=" +
      Math.round(p.avatarAt || 0);
  }

  function personFaceHtml(p) {
    var url = personAvatarUrl(p);
    return '<span class="avatar">' + (url
      ? '<img alt="" src="' + PX.esc(url) + '">'
      : PX.esc((p.label || "").charAt(0) || "?")) + "</span>";
  }

  function orgChipHtml(p) {
    return '<span class="invite-chip" data-chip-id="' + PX.esc(p.id) + '">' +
      personFaceHtml(p) +
      '<span class="nm">' + PX.esc(p.label || "") + "</span>" +
      '<button type="button" data-org-unpick="' + PX.esc(p.id) +
      '" aria-label="移出">×</button></span>';
  }

  var ORG_CHIP_BOX = { heads: "#orgHeadChips", admins: "#orgAdminChips",
                       members: "#orgMemberChips" };

  /* 人像行按名字对齐：已有的不动，缺的补在后面，不在名单里的拿掉。
     整块重画会让这一行里每张头像都重新加载一遍——删一个人，剩下的全都闪一下。 */
  function syncOrgChips(key) {
    var box = $(ORG_CHIP_BOX[key]);
    if (!box) return;
    var ids = S.orgForm[key].map(String);
    Array.prototype.forEach.call(box.querySelectorAll("[data-chip-id]"),
      function (el) {
        if (ids.indexOf(el.getAttribute("data-chip-id")) < 0) box.removeChild(el);
      });
    var have = {};
    Array.prototype.forEach.call(box.querySelectorAll("[data-chip-id]"),
      function (el) { have[el.getAttribute("data-chip-id")] = true; });
    ids.forEach(function (id) {
      var p = S.orgPeople[id];
      if (have[id] || !p) return;
      box.insertAdjacentHTML("beforeend", orgChipHtml(p));
    });
  }

  function drawOrgNew() {
    if (!S.orgForm) resetOrgForm();
    Object.keys(ORG_CHIP_BOX).forEach(syncOrgChips);
  }

  function unpickOrgPerson(id) {
    var f = S.orgForm;
    if (!f) return;
    var hit = "";
    Object.keys(ORG_CHIP_BOX).forEach(function (key) {
      if (f[key].map(String).indexOf(String(id)) >= 0) hit = key;
      f[key] = f[key].filter(function (x) { return String(x) !== String(id); });
    });
    if (hit) syncOrgChips(hit);
  }

  /* 三个框互斥：一个人只能出现在其中一个里面。别的框已经选过的人在这个框里
   * 灰着——人看得见，知道他在，也点不动。 */
  function pickOrgPeople(key, title, mode) {
    var f = S.orgForm;
    var others = [];
    ["heads", "admins", "members"].forEach(function (k) {
      if (k !== key) others = others.concat(f[k]);
    });
    pickPeople({
      title: title,
      mode: mode,
      chosen: f[key].map(function (id) { return S.orgPeople[id]; }).filter(Boolean),
      disabled: others,
      apply: function (people) {
        f[key] = people.map(function (p) { return p.id; });
        people.forEach(function (p) { S.orgPeople[p.id] = p; });
        syncOrgChips(key);
        orgFormChanged();
      }
    });
  }

  /* 表单里的错都写在出问题的那一格下面：不另起一行讲，也不弹窗 */
  function orgFieldError(which, text) {
    var box = { name: "#orgNameErr", id: "#orgIdErr", heads: "#orgHeadErr" }[which];
    var el = box ? $(box) : null;
    if (el) {
      el.textContent = text || "";
      el.hidden = !text;
    }
    var input = which === "heads" ? null : $("#org" + (which === "name" ? "Name" : "Id"));
    if (input) {
      if (text) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
    }
  }

  function orgClearErrors() {
    orgFieldError("name", "");
    orgFieldError("id", "");
    orgFieldError("heads", "");
  }

  /* 组织标识只收小写字母、数字、连字符与下划线：敲不进去的字当场就不收。
     收字是即时的，评语不是——长度这类判断等这一格失焦或提交时再说，
     打字途中不弹红字。 */
  function sanitizeOrgId() {
    var box = $("#orgId");
    if (!box) return;
    var before = box.value;
    var caret = box.selectionStart === undefined ? before.length : box.selectionStart;
    var kept = 0;
    var clean = "";
    for (var i = 0; i < before.length && clean.length < 24; i++) {
      var ch = before.charAt(i).toLowerCase();
      if (!/[a-z0-9_-]/.test(ch)) continue;
      clean += ch;
      if (i < caret) kept += 1;
    }
    if (clean !== before) {
      box.value = clean;
      try { box.setSelectionRange(kept, kept); } catch (e) { /* 老浏览器 */ }
    }
    // 动过这一格就把上一次的评语收起来，等失焦再重新判
    orgFieldError("id", "");
  }

  /* 失焦时才说话：填了就判长度，空着不催——空着到提交时再讲 */
  function checkOrgId() {
    var v = (($("#orgId") || {}).value || "").trim();
    if (!v) return orgFieldError("id", "");
    orgFieldError("id", v.length < 3
      ? "至少 3 位，只接受小写字母、数字、连字符与下划线" : "");
  }

  function checkOrgName() {
    var v = (($("#orgName") || {}).value || "").trim();
    if (v) orgFieldError("name", "");
  }

  function orgFormChanged() {
    sanitizeOrgId();
    if (S.orgForm && S.orgForm.heads.length) orgFieldError("heads", "");
  }

  function createOrg() {
    var f = S.orgForm || { heads: [], admins: [], members: [] };
    var name = $("#orgName").value.trim();
    var id = $("#orgId").value.trim().toLowerCase();
    orgClearErrors();
    if (!name) {
      orgFieldError("name", "请填写组织名称");
      $("#orgName").focus();
      return;
    }
    if (id.length < 3) {
      orgFieldError("id", id ? "至少 3 位" : "请填写组织标识");
      $("#orgId").focus();
      return;
    }
    if (!f.heads.length) {
      orgFieldError("heads", "至少要有一位负责人");
      return;
    }
    $("#orgCreate").disabled = true;
    PX.api("/org/create", {
      method: "POST",
      body: { id: id, name: name, note: $("#orgNote").value.trim(),
              heads: f.heads, admins: f.admins, members: f.members }
    }).then(function () {
      $("#orgCreate").disabled = false;
      // 刚建成功，不必再用"内容会丢掉"拦一次；表单在下次进来时清空
      orgBypass = true;
      PX.toast("已创建组织");
      go("orgs");
      setTimeout(function () { orgBypass = false; }, 80);
    }).catch(function (e) {
      $("#orgCreate").disabled = false;
      var msg = (e && e.message) || "创建失败";
      if (msg.indexOf("标识") >= 0) orgFieldError("id", msg);
      else if (msg.indexOf("名称") >= 0) orgFieldError("name", msg);
      else if (msg.indexOf("负责人") >= 0) orgFieldError("heads", msg);
      else PX.toast(PX.esc(msg), true);
    });
  }

  /* ------------------------------------------------------------ 组织详情 --- */
  /* 这一页是给平台侧看的：站长与超级管理员在这里动别人的组织，因此每一处改动都要提权
     （移出成员还要再问一句）。组织自己的负责人管自己的组织是另一页，在「我的组织」下面。 */
  var ORG_ROLE_CHOICES = [["head", "负责人"], ["admin", "管理"], ["member", "成员"]];

  function orgRoleSelect(member) {
    return '<select class="cell-input cell-select" data-org-role-for="' +
      member.accountId + '" aria-label="身份">' +
      ORG_ROLE_CHOICES.map(function (c) {
        return '<option value="' + c[0] + '"' +
          (c[0] === member.role ? " selected" : "") + ">" + c[1] + "</option>";
      }).join("") + "</select>";
  }

  function loadOrg() {
    // 换了一个组织就先把手上的放下：别在新的一页上先闪一下上一个组织
    if (S.orgDetail && S.orgDetail.org.id !== S.orgId) S.orgDetail = null;
    markLoading("orgDetail");
    return PX.api("/org?id=" + encodeURIComponent(S.orgId || "")).then(function (d) {
      S.orgDetail = { org: d.org, members: d.members || [] };
      markLoaded("orgDetail");
    }).catch(function (e) {
      S.orgDetail = null;
      markLoaded("orgDetail");
      PX.toast(PX.esc(e.message), true);
    });
  }

  function drawOrg() {
    var d = S.orgDetail;
    var tb = $("#orgMemberTable tbody");
    if (!d) {
      $("#orgTitle").textContent = loaded("orgDetail") ? "组织不存在" : "正在读取…";
      $("#orgSub").textContent = "";
      if (tb) tb.innerHTML = rowsOrEmpty(4, "orgDetail", "组织不存在");
      $("#orgSelfHint").hidden = true;
      return;
    }
    var o = d.org;
    var frozen = !!o.archived;
    var manage = !frozen && isSuper();
    $("#orgTitle").textContent = o.name;
    $("#orgSub").textContent = frozen ? "已归档" : "";
    $("#orgEditId").textContent = o.id;
    $("#orgEditNote").textContent = frozen ? "已归档，先恢复再改" : "";
    // 用户改过的两个格子不再被后台重画写回旧值（提醒、通知每来一次就会重画这一页）
    if (!S.orgEditDirty) {
      $("#orgEditName").value = o.name;
      $("#orgEditNoteInput").value = o.note || "";
    }
    $("#orgEditName").disabled = !manage;
    $("#orgEditNoteInput").disabled = !manage;
    $("#orgSaveBtn").hidden = !manage;
    $("#orgMemberAdd").hidden = !manage;
    $("#orgMemberCount").textContent = d.members.length
      ? "共 " + d.members.length + " 人" : "";
    if (tb) tb.innerHTML = orgMemberRows(d, manage, "orgDetail");
    drawCoverUi("orgCover", o);
    // 自己正好是这个组织的负责人/管理时，指一条不必提权的路
    var mine = o.myRole || "";
    var hint = $("#orgSelfHint");
    hint.hidden = !(mine === "head" || mine === "admin") || frozen;
    if (!hint.hidden) {
      $("#orgSelfHintText").textContent = "你是该组织" + o.myRoleLabel +
        "，可以前往「我的组织」页面进行操作";
    }
    // 归档、恢复只给超管以上；彻底删除只给站长，并且只在归档之后
    $("#orgArchiveBtn").hidden = frozen || !isSuper();
    $("#orgRestoreBtn").hidden = !frozen || !isSuper();
    $("#orgDropBtn").hidden = !(frozen && isOwner());
    $("#orgStateNote").textContent = frozen
      ? "已归档：成员关系折起来存放，恢复时展开"
      : "归档后不能改成员，恢复时展开成员关系";
  }

  /* 成员表：平台侧与组织自己的管理页共用一份。负责人的人数决定谁能被移出——
     最后一个负责人的 [移出] 直接灰着，点不出来，鼠标停上去说明原因。 */
  function orgMemberRows(d, manage, loadKey) {
    if (!d.members.length) {
      return rowsOrEmpty(4, loadKey,
        (S.orgFilter || "").trim() ? "没有匹配的成员" : "还没有成员");
    }
    var heads = d.members.filter(function (m) { return m.role === "head"; }).length;
    return d.members.map(function (m) {
      var person = { id: m.accountId, avatar: m.avatar, avatarAt: m.avatarAt,
                     label: m.name || ("账号 #" + (m.uid || 0)) };
      var last = m.role === "head" && heads <= 1;
      var act = "";
      if (manage) {
        act = last
          ? '<button class="badge-off" type="button" data-tip="至少要留一位负责人"' +
            ' aria-label="不能移出：至少要留一位负责人">移出</button>'
          : '<button class="btn sm danger" data-org-kick="' + m.accountId +
            '">移出</button>';
      }
      return "<tr>" +
        '<td><span class="person-cell">' + personFaceHtml(person) +
        '<span class="nm">' + PX.esc(person.label) + "</span></span></td>" +
        "<td>" + (manage ? orgRoleSelect(m)
          : '<span class="tag">' + PX.esc(m.roleLabel) + "</span>") + "</td>" +
        '<td class="mono">' + PX.when(m.joinedAt) + "</td>" +
        '<td class="actions">' + act + "</td></tr>";
    }).join("");
  }

  function afterOrgChange() {
    var jobs = [loadOrgs(), loadMyOrgs()];
    if (S.route === "org") jobs.push(loadOrg());
    if (S.route === "my-org" || S.route === "my-org-manage") jobs.push(loadOrg());
    return Promise.all(jobs).then(function () {
      drawOrgs();
      drawMyOrgs();
      if (S.route === "org") drawOrg();
      if (S.route === "my-org") drawOrgSelf();
      if (S.route === "my-org-manage") drawOrgManage();
    });
  }

  /* 两处改组织：平台侧要 sudo，组织自己的设置页只问一句。scope 由页面决定。 */
  function orgWrite(scope, path, body, what) {
    if (scope === "platform") return apiSudo(path, { method: "POST", body: body }, what);
    return PX.api(path, { method: "POST", body: body });
  }

  function saveOrg(scope, nameSel, noteSel, errSel) {
    var d = S.orgDetail;
    if (!d) return;
    var err = $(errSel);
    var name = $(nameSel).value.trim();
    err.hidden = true;
    if (!name) {
      err.textContent = "请填写组织名称";
      err.hidden = false;
      $(nameSel).setAttribute("aria-invalid", "true");
      $(nameSel).focus();
      return;
    }
    $(nameSel).removeAttribute("aria-invalid");
    $("#orgSaveBtn").disabled = true;
    $("#orgManageSave").disabled = true;
    orgWrite(scope, "/org/update",
      { id: d.org.id, name: name, note: $(noteSel).value.trim(), scope: scope },
      "修改组织").then(function () {
      S.orgEditDirty = false;
      PX.toast("已保存");
      return afterOrgChange();
    }).catch(fail).then(function () {
      $("#orgSaveBtn").disabled = false;
      $("#orgManageSave").disabled = false;
    });
  }

  function addOrgMembers(scope) {
    var d = S.orgDetail;
    if (!d) return;
    pickPeople({
      title: "添加成员",
      mode: "multi",
      exclude: d.members.map(function (m) { return m.accountId; }),
      empty: "没有可添加的账号",
      apply: function (people) {
        if (!people.length) return;
        Promise.all(people.map(function (p) {
          return orgWrite(scope, "/org/member", {
            org: d.org.id, account: p.id, action: "add", role: "member", scope: scope
          }, "添加组织成员");
        })).then(function () {
          PX.toast("已添加 " + people.length + " 位成员");
          return afterOrgChange();
        }).catch(fail);
      }
    });
  }

  function setOrgMemberRole(scope, account, role) {
    var d = S.orgDetail;
    if (!d) return;
    orgWrite(scope, "/org/member", {
      org: d.org.id, account: account, action: "add", role: role, scope: scope
    }, "调整组织内身份").then(function () { return afterOrgChange(); }).catch(function (e) {
      fail(e);
      return afterOrgChange();          // 没改成：把下拉退回服务端那一版
    });
  }

  /* 移出成员要先问一句：这一下会把人从组织里拿掉 */
  function kickOrgMember(scope, account) {
    var d = S.orgDetail;
    if (!d) return;
    var who = d.members.filter(function (m) {
      return Number(m.accountId) === Number(account);
    })[0];
    var label = (who && who.name) || ("账号 #" + (who ? who.uid : account));
    openOverlay("org-kick", "移出成员",
      '<p class="page-sub">把 ' + PX.esc(label) + " 移出「" + PX.esc(d.org.name) +
      "」？后续可以重新添加。</p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn danger" type="button" id="orgKickGo">移出</button></div>');
    $("#orgKickGo").addEventListener("click", function () {
      closeOverlay();
      orgWrite(scope, "/org/member", {
        org: d.org.id, account: account, action: "remove", scope: scope
      }, "移出组织成员").then(function () {
        PX.toast("已移出");
        return afterOrgChange();
      }).catch(fail);
    });
  }

  /* ---------------------------------------------- 我的组织 › 组织（成员看） --- */
  function drawOrgSelf() {
    var d = S.orgDetail;
    var tb = $("#myOrgMemberTable tbody");
    if (!d) {
      $("#myOrgTitle").textContent = loaded("orgDetail") ? "组织不存在" : "正在读取…";
      $("#myOrgSub").textContent = "";
      $("#myOrgManageBtn").hidden = true;
      if (tb) tb.innerHTML = rowsOrEmpty(3, "orgDetail", "组织不存在");
      return;
    }
    var o = d.org;
    $("#myOrgTitle").textContent = o.name;
    $("#myOrgSub").textContent = (o.myRoleLabel ? "我的身份 " + o.myRoleLabel + " · " : "") +
      d.members.length + " 位成员";
    $("#myOrgCardTitle").textContent = o.name;
    $("#myOrgCount").textContent = d.members.length + " 位成员";
    $("#myOrgCardId").textContent = o.id;
    $("#myOrgNote").textContent = o.note || "还没有简介";
    // 没有图就什么都不摆：不留一个空框
    var src = orgCoverSrc(o, 960);
    var col = $("#myOrgCoverCol");
    col.hidden = !src;
    $("#myOrgCover").innerHTML = src
      ? '<img alt="" src="' + PX.esc(src) + '">' : "";
    $("#myOrgMemberCount").textContent = "共 " + d.members.length + " 人";
    var canManage = !o.archived && (o.myRole === "head" || o.myRole === "admin");
    $("#myOrgManageBtn").hidden = !canManage;
    if (tb) {
      tb.innerHTML = d.members.length ? d.members.map(function (m) {
        var person = { id: m.accountId, avatar: m.avatar, avatarAt: m.avatarAt,
                       label: m.name || ("账号 #" + (m.uid || 0)) };
        return "<tr>" +
          '<td><span class="person-cell">' + personFaceHtml(person) +
          '<span class="nm">' + PX.esc(person.label) + "</span></span></td>" +
          '<td><span class="tag">' + PX.esc(m.roleLabel) + "</span></td>" +
          '<td class="mono">' + PX.when(m.joinedAt) + "</td></tr>";
      }).join("") : rowsOrEmpty(3, "orgDetail", "还没有成员");
    }
  }

  /* ------------------------------ 我的组织 › 组织 › 组织管理（负责人/管理） --- */
  function drawOrgManage() {
    var d = S.orgDetail;
    if (!d) {
      $("#orgManageCrumb").textContent = loaded("orgDetail") ? "组织不存在" : "正在读取…";
      $("#orgManageTable tbody").innerHTML = rowsOrEmpty(4, "orgDetail", "组织不存在");
      return;
    }
    var o = d.org;
    $("#orgManageCrumb").textContent = o.name;
    $("#orgManageCrumb").setAttribute("data-href", "#/my-org/" + encodeURIComponent(o.id));
    $("#orgManageId").textContent = o.id;
    if (!S.orgEditDirty) {
      $("#orgManageName").value = o.name;
      $("#orgManageNote").value = o.note || "";
    }
    $("#orgManageCount").textContent = d.members.length
      ? "共 " + d.members.length + " 人" : "";
    $("#orgManageTable tbody").innerHTML = orgMemberRows(d, !o.archived, "orgDetail");
    drawCoverUi("manCover", o);
  }

function archiveOrg(id) {
    var o = orgById(id) || (S.orgDetail && S.orgDetail.org.id === id
      ? S.orgDetail.org : null);
    openOverlay("org-archive", "归档组织",
      '<p class="page-sub">成员关系折起来存放，明细行删除；之后可以恢复。</p>' +
      '<p class="page-sub"><b>' + PX.esc(o ? o.name : id) + "</b></p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="orgArchiveGo">归档</button></div>');
    $("#orgArchiveGo").addEventListener("click", function () {
      closeOverlay();
      apiSudo("/org/archive", { method: "POST", body: { id: id } }, "归档组织")
        .then(function () { PX.toast("已归档"); return afterOrgChange(); })
        .catch(fail);
    });
  }

  function restoreOrg(id) {
    apiSudo("/org/restore", { method: "POST", body: { id: id } }, "恢复组织")
      .then(function () { PX.toast("已恢复"); return afterOrgChange(); })
      .catch(fail);
  }

  /* 彻底删除只在归档之后：先把明细折掉，剩下那一行才谈得上"删干净" */
  function dropOrg(id) {
    var o = orgById(id) || (S.orgDetail && S.orgDetail.org.id === id
      ? S.orgDetail.org : null);
    openOverlay("org-drop", "彻底删除组织",
      '<p class="page-sub">删除后不能恢复，成员的归属记录一并消失。</p>' +
      '<p class="page-sub"><b>' + PX.esc(o ? o.name : id) + "</b></p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn danger" type="button" id="orgDropGo">彻底删除</button></div>');
    $("#orgDropGo").addEventListener("click", function () {
      closeOverlay();
      apiSudo("/org/delete", { method: "POST", body: { id: id } }, "彻底删除组织")
        .then(function () {
          PX.toast("已删除");
          // 这一页说的组织已经没有了：回列表，别把空的详情页留在屏幕上
          if (S.route === "org" && S.orgId === id) {
            S.orgId = "";
            S.orgDetail = null;
            go("orgs");
            return null;
          }
          return afterOrgChange();
        })
        .catch(fail);
    });
  }

  function drawUsers() {
    renderAccountsTable();
  }

  /* ------------------------------------------------------- 批量导入账号 --- */
  /* 预览就是编辑页：读进来的每一行都能就地改，改完点[导入]。
     账号表下载过一次之后又改了内容，[导入] 会先问一句要不要重新下一份——
     因为初始密码只在这一页上看得到，关掉就没了。 */
  function resetImport() {
    markLoading("importRows");
    S.import = { rows: [], counts: null, rules: [], cap: null, warnings: [],
                 rule: { id: "random", template: "" },
                 // 预览不替人补内容：要补得先点 [自动填充]，而且只补那一次
                 autofill: { username: true, password: true },
                 revision: 0, downloaded: -1, busy: false };
    var help = $("#importRuleHelp");
    if (help) help.setAttribute("href", PX.PREFIX + "/docs#/usage/account-import.md");
  }

  function loadImportRules() {
    return PX.api("/account/import/rules").then(function (d) {
      S.import.rules = d.rules || [];
      S.import.cap = d.cap || null;
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function importRulePayload() {
    if (!S.import) return { id: "random" };
    var payload = { id: S.import.rule.id || "random" };
    if (payload.id === "custom") payload.template = S.import.rule.template || "";
    return payload;
  }

  function importPreview(rows, rule, autofill) {
    S.import.busy = true;
    drawImport();
    return PX.api("/account/import/preview", {
      method: "POST",
      body: { rows: rows, rule: rule || importRulePayload(),
              autofill: autofill || {} }
    }).then(function (d) {
      S.import.rows = (d.rows || []).map(function (r) { return pickImportRow(r); });
      S.import.counts = d.counts || null;
      S.import.cap = d.cap || S.import.cap;
      S.import.warnings = d.warnings || [];
      S.import.revision += 1;
      S.import.busy = false;
      drawImport();
      return d;
    }).catch(function (e) {
      S.import.busy = false;
      PX.toast(PX.esc(e.message), true);
      drawImport();
    });
  }

  function pickImportRow(row) {
    return {
      index: row.index, username: row.username || "", name: row.name || "",
      role: row.role || "admin", password: row.password || "",
      org: row.org || "", note: row.note || "", extras: row.extras || {},
      generated: !!row.generated, autoUsername: !!row.autoUsername,
      problems: row.problems || [], status: row.status || "ready"
    };
  }

  /* 警示格：正常行是空的；有问题的那一行在最右边给一颗圆圈感叹号，
     悬停看原因（电脑），点一下弹一条通知（触屏）。整列只在有人出问题时才出现。 */
  var ICON_WARN = '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" ' +
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round">' +
    '<circle cx="10" cy="10" r="7.4"/><path d="M10 6.4v4.4"/><path d="M10 13.6v.1"/></svg>';

  function importWarnCell(row) {
    var why = (row.problems || []).join("；");
    if (!why) return "";
    return '<button class="badge-off warn-dot" type="button" data-tip="' + PX.esc(why) +
      '" aria-label="不能导入：' + PX.esc(why) + '">' + ICON_WARN + "</button>";
  }

  function importRowHtml(row) {
    var bad = row.status !== "ready";
    var field = function (name, extra) {
      return '<input class="cell-input' + (extra || "") + '" data-import-field="' + name +
        '" data-row="' + row.index + '" value="' + PX.esc(row[name] || "") + '"' +
        (name === "username" || name === "password" ? ' spellcheck="false"' : "") + ">";
    };
    return '<tr data-import-row="' + row.index + '"' + (bad ? ' class="row-bad"' : "") + ">" +
      '<td class="mono">' + row.index + "</td>" +
      "<td>" + field("username", " mono") + "</td>" +
      "<td>" + field("name") + "</td>" +
      '<td><select class="cell-input cell-select" data-import-field="role" data-row="' +
      row.index + '">' +
      '<option value="admin"' + (row.role === "admin" ? " selected" : "") + ">管理员</option>" +
      '<option value="superadmin"' + (row.role === "superadmin" ? " selected" : "") +
      ">超级管理员</option></select></td>" +
      "<td>" + field("password", " mono") + "</td>" +
      '<td><input class="cell-input" data-import-field="org" data-row="' + row.index +
      '" value="' + PX.esc(row.org || "") + '" disabled></td>' +
      "<td>" + field("note") + "</td>" +
      '<td class="import-warn">' + importWarnCell(row) + "</td></tr>";
  }

  /* 表格里已经有这一批行了：只改变化的那几处，不重建。
     重建会在"正在打字的格子被移走"时抛异常（innerHTML 设置失败），
     而且焦点与光标位置也跟着丢——打字的人每停半秒就被打断一次。 */
  function patchImportRows(body, rows) {
    var trs = body.querySelectorAll("tr[data-import-row]");
    if (!rows.length || trs.length !== rows.length) return false;
    for (var i = 0; i < rows.length; i++) {
      var tr = trs[i];
      var row = rows[i];
      if (Number(tr.getAttribute("data-import-row")) !== row.index) return false;
      tr.classList.toggle("row-bad", row.status !== "ready");
      var warn = tr.querySelector("td.import-warn");
      if (warn) warn.innerHTML = importWarnCell(row);
      var fields = tr.querySelectorAll("[data-import-field]");
      for (var j = 0; j < fields.length; j++) {
        var el = fields[j];
        if (el === document.activeElement) continue;      // 正在打字的格子不动它
        var want = row[el.getAttribute("data-import-field")] || "";
        if (el.value !== want) el.value = want;
      }
    }
    return true;
  }

  function drawImport() {
    var state = S.import;
    if (!state) return;
    syncCrumb("import-accounts");
    var counts = state.counts || { total: 0, ready: 0, blocked: 0, generated: 0 };
    if ($("#importCount")) {
      $("#importCount").textContent = counts.total
        ? "共 " + counts.total + " 行，可导入 " + counts.ready + " 行"
        : "";
    }
    var body = $("#importTable tbody");
    var table = $("#importTable");
    var anyProblem = (state.rows || []).some(function (r) { return r.status !== "ready"; });
    if (table) table.classList.toggle("warn-off", !anyProblem);
    if (body && !patchImportRows(body, state.rows)) {
      body.innerHTML = state.rows.length
        ? state.rows.map(importRowHtml).join("")
        : emptyRow(7, "还没有内容：选一个表格，或者点 [手动录入]");
    }

    var why = "";
    if (counts.blocked) why = "有 " + counts.blocked + " 行无法导入";
    else if (S.import.cap && S.import.cap.max > 0 && counts.ready > S.import.cap.left) {
      why = "超出账号上限：当前可再创建 " + S.import.cap.left + " 个";
    }
    if ($("#importGo")) {
      $("#importGo").disabled = !!why || !state.rows.length || state.busy;
      $("#importGo").title = why || "创建导入表中的账号";
    }
    if ($("#importFillBtn")) {
      // 只有真的还有空格子时这颗键才有意义
      var blanks = state.rows.filter(function (r) {
        return !r.username || !r.password;
      }).length;
      $("#importFillBtn").disabled = !blanks || state.busy;
      $("#importFillBtn").title = blanks ? "有 " + blanks + " 行缺登录名或初始密码"
                                         : "所有行均已填写登录名和初始密码";
    }
    if ($("#importState")) $("#importState").textContent = why;
    if ($("#importWarn")) {
      var warns = state.warnings || [];
      $("#importWarn").hidden = !warns.length;
      $("#importWarn").textContent = warns.join("；");
    }
    if ($("#importDownloadBtn")) $("#importDownloadBtn").disabled = !state.rows.length;
  }

  // 表格里的就地编辑：先把值写回本地（下载与导入都用最新的），
  // 再等手停下来重新算一遍预览——冲突、上限、密码都要服务端算
  var importTimer = null;
  function importCellChanged(input, immediate) {
    if (!S.import) return;
    var field = input.getAttribute("data-import-field");
    var index = Number(input.getAttribute("data-row"));
    var row = (S.import.rows || []).filter(function (r) { return r.index === index; })[0];
    if (!row) return;
    row[field] = input.value;
    if (field === "username") row.autoUsername = false;
    if (field === "password") row.generated = false;
    // 内容动过了：上一份下载的账号表就过期了
    S.import.revision += 1;
    if (importTimer) clearTimeout(importTimer);
    var run = function () {
      importTimer = null;
      importPreview(S.import.rows, importRulePayload());
    };
    if (immediate) run();
    else importTimer = setTimeout(run, 450);
  }

  function addImportBlankRow() {
    if (!S.import) return;
    var next = (S.import.rows || []).length + 1;
    S.import.rows = (S.import.rows || []).concat([{
      index: next, username: "", name: "", role: "admin", password: "",
      org: "", note: "", extras: {}, generated: false, autoUsername: true,
      problems: [], status: "ready"
    }]);
    importPreview(S.import.rows, importRulePayload());
  }

  function readImportFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var text = String(reader.result || "");
      var base64 = text.indexOf("base64,") >= 0 ? text.split("base64,")[1] : "";
      PX.api("/account/import/preview", {
        method: "POST",
        body: { filename: file.name, data: base64, rule: importRulePayload() }
      }).then(function (d) {
        S.import.rows = (d.rows || []).map(pickImportRow);
        S.import.counts = d.counts || null;
        S.import.cap = d.cap || S.import.cap;
        S.import.warnings = d.warnings || [];
        S.import.revision += 1;
        drawImport();
      }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
    };
    reader.readAsDataURL(file);
  }

  function importTableCsv() {
    var head = ["登录名", "显示名", "角色", "初始密码", "组织", "备注"];
    var lines = [head.join(",")];
    (S.import.rows || []).forEach(function (row) {
      lines.push([row.username, row.name, row.role === "superadmin" ? "超级管理员" : "管理员",
                  row.password, row.org, row.note].map(function (cell) {
        var text = String(cell == null ? "" : cell);
        return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
      }).join(","));
    });
    return lines.join("\n") + "\n";
  }

  function downloadImportTable() {
    if (!S.import) return;
    var rows = S.import.rows || [];
    if (!rows.length) return;
    var text = "\ufeff" + importTableCsv();
    var now = new Date();
    var name = "accounts-" + now.getFullYear() + PX.pad(now.getMonth() + 1) +
      PX.pad(now.getDate()) + ".csv";
    var mark = function () {
      S.import.downloaded = S.import.revision;
      drawImport();
      PX.toast("账号表已下载");
      PX.api("/account/import/downloaded", {
        method: "POST", body: { rows: rows.length }
      }).catch(function () { /* 记账失败不影响下载 */ });
    };
    var fallback = function () {
      var blob = new Blob([text], { type: "text/csv;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
      mark();
    };
    // 能弹「另存为」就弹：位置由人来选。取消（AbortError）不算下载过，
    // 导入之前还会再问一次
    if (window.showSaveFilePicker) {
      window.showSaveFilePicker({
        suggestedName: name,
        types: [{description: "CSV", accept: {"text/csv": [".csv"]}}]
      }).then(function (handle) {
        return handle.createWritable().then(function (writable) {
          return writable.write(text).then(function () { return writable.close(); });
        });
      }).then(mark).catch(function (e) {
        if (e && (e.name === "AbortError" || e.name === "NotAllowedError")) return;
        fallback();
      });
      return;
    }
    fallback();
  }

  /* 自动填充：只填空着的格子，填什么由这一次的选择决定 */
  function autofillDialog() {
    if (!S.import) return;
    if (!(S.import.rows || []).length) {
      PX.toast("先读一张表或手动录入", true);
      return;
    }
    var opts = S.import.autofill || {username: true, password: true};
    var rule = S.import.rule || {id: "random", template: ""};
    var choices = (S.import.rules || []).map(function (r) {
      return '<option value="' + PX.esc(r.id) + '"' +
        (r.id === rule.id ? " selected" : "") + ">" + PX.esc(r.label) + "</option>";
    }).join("");
    openOverlay("import-autofill", "自动填充",
      '<p class="page-sub">仅填充空白的登录名或初始密码</p>' +
      '<div class="as-form-grid" style="margin-top:12px">' +
      '<div class="field"><label for="afUser">补齐登录名（按姓名拼音）</label>' +
      '<label class="switch" style="margin-top:6px"><input type="checkbox" id="afUser"' +
      (opts.username ? " checked" : "") + '><span class="track"></span></label></div>' +
      '<div class="field"><label for="afPass">补齐初始密码</label>' +
      '<label class="switch" style="margin-top:6px"><input type="checkbox" id="afPass"' +
      (opts.password ? " checked" : "") + '><span class="track"></span></label></div>' +
      "</div>" +
      '<div class="field" style="margin-top:10px"><label for="afRule">密码规则</label>' +
      '<select id="afRule">' + choices + "</select></div>" +
      '<div class="field" id="afTplRow" style="margin-top:8px"' +
      (rule.id === "custom" ? "" : " hidden") + '>' +
      '<label for="afTpl">自定义规则</label>' +
      '<input id="afTpl" type="text" autocomplete="off" spellcheck="false" ' +
      'placeholder="{{学号:6}}" value="' + PX.esc(rule.template || "") + '"></div>' +
      '<p style="margin-top:8px"><a class="link-ico import-rule-help" href="' +
      PX.PREFIX + '/docs#/usage/account-import.md" target="_blank" rel="noopener">' +
      '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" ' +
      'stroke-width="1.5" stroke-linecap="round"><circle cx="10" cy="10" r="7.2"/>' +
      '<path d="M8.2 7.9a1.9 1.9 0 0 1 3.7.6c0 1.2-1.9 1.5-1.9 2.8"/>' +
      '<path d="M10 14.2v.1"/></svg><span>使用说明</span></a></p>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="afGo">自动填充</button></div>');
    if ($("#afRule")) {
      $("#afRule").addEventListener("change", function () {
        var row = $("#afTplRow");
        if (row) row.hidden = this.value !== "custom";
      });
    }
    $("#afGo").addEventListener("click", function () {
      S.import.autofill = {username: !!($("#afUser") && $("#afUser").checked),
                           password: !!($("#afPass") && $("#afPass").checked)};
      S.import.rule = {id: $("#afRule") ? $("#afRule").value : "random",
                       template: ($("#afTpl") && $("#afTpl").value) || ""};
      closeOverlay();
      // 只在这一次请求里补：之后重算（含把某一格删空）不会再补回来
      importPreview(S.import.rows, importRulePayload(), S.import.autofill);
    });
  }

  function submitImport() {
    if (!S.import) return;
    var state = S.import;
    var rows = state.rows || [];
    if (!rows.length) return;
    var go = function () {
      PX.api("/account/import", { method: "POST", body: { rows: rows } })
        .then(function (d) {
          PX.toast("已导入 " + d.created + " 个账号");
          resetImport();
          return loadImportRules().then(drawImport);
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    };
    if (state.downloaded !== state.revision) {
      confirmHighRisk("尚未下载账号表", "请先下载当前账号表。" +
                      "导入后将无法再次查看未下载的更改", "仍然导入", false, go, { noSudo: true });
      return;
    }
    go();
  }

  function canDecide(acc) {
    if (myRank() < rank("superadmin")) return false;
    if (acc.role === "owner") return false;
    if (isOwner()) return true;
    return acc.role === "admin";
  }

  function renderAccountsTable() {
    var rows = S.accounts;
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
    }).join("") : rowsOrEmpty(6, "accounts", S.userFilter.trim()
      ? "没有匹配的账号" : "还没有别的账号");
    var more = $("#usersMore");
    if (more) {
      var left = Math.max(0, (S.accountsTotal || 0) - S.accounts.length);
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "再显示 " + Math.min(50, left) + " 个" : "加载更多";
    }
    var count = $("#usersCount");
    if (count) {
      var text = S.accountsTotal ? "共 " + S.accountsTotal + " 个账号" : "";
      // 上限写在配置文件里：到了之后注册口会拒绝，这里让人提前看见还剩多少
      if (S.maxAccounts && !S.userFilter.trim()) {
        text += (text ? "，" : "") + "上限 " + S.maxAccounts +
          ((S.accountsAll || 0) >= S.maxAccounts ? "（已满）" : "");
      }
      count.textContent = text;
    }
  }

  function loadAccountsPage(reset) {
    if (S.accountsLoading) return Promise.resolve();
    if (reset) {
      S.accounts = [];
      S.accountsTotal = 0;
      markLoading("accounts");
    } else if (S.accountsTotal !== null && S.accounts.length >= S.accountsTotal) {
      return Promise.resolve();
    }
    S.accountsLoading = true;
    var q = S.userFilter.trim();
    return PX.api("/accounts?offset=" + S.accounts.length + "&limit=50" +
                  (q ? "&q=" + encodeURIComponent(q) : ""))
      .then(function (d) {
        S.me = d.me || S.me;
        S.accounts = S.accounts.concat(d.accounts || []);
        S.accountsTotal = d.total || 0;
        S.accountsAll = d.allTotal || 0;
        S.maxAccounts = d.maxAccounts || 0;
        markLoaded("accounts");
        if (d.quotaDefault) S.quotaDefault = d.quotaDefault;
        if (d.quotaMax) S.quotaMax = d.quotaMax;
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); })
      .then(function () {
        S.accountsLoading = false;
        renderIdentity();
        applyRoleVisibility();
        syncPendingBadge();
        renderAccountsTable();
      });
  }

  function openAccountOverlay(id) {
    var a = S.accounts.filter(function (x) { return x.id === id; })[0];
    if (!a) return;

    var rows = [
      ["编号", "#" + (a.uid || 0)],
      ["用户名", a.username],
      ["显示名", a.name || "—"],
      ["角色", a.roleLabel],
      ["状态", a.statusLabel],
      ["存档配额", quotaText(a)],
      ["注册时间", PX.when(a.createdAt)],
      ["最近登录", PX.when(a.lastLogin)]
    ];
    if (a.banReason) rows.push(["封禁原因", a.banReason]);
    if (a.passTemp) rows.push(["密码", "临时密码（对方登录后要先重设）"]);
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
      if (a.status === "banned") {
        btns.push('<button class="btn" data-decide="unban" data-id="' + a.id + '">解除封禁</button>');
      } else {
        btns.push('<button class="btn danger" data-ban="' + a.id + '">封禁</button>');
      }
    }
    if (isOwner() && a.role !== "owner") {
      btns.push(a.role === "superadmin"
        ? '<button class="btn ghost" data-role="admin" data-id="' + a.id + '">降为管理员</button>'
        : '<button class="btn ghost" data-role="superadmin" data-id="' + a.id + '">升为超级管理员</button>');
    }
    if (isSuper()) {
      btns.push('<button class="btn ghost" data-quota="' + a.id + '">调整存档配额</button>');
    }
    // 重置密码：超管只能重置普通管理员；站长的密码只能他自己在账户信息里改
    var canReset = isSuper() && a.role !== "owner"
      && (isOwner() || a.role === "admin");
    if (canReset) {
      btns.push('<button class="btn ghost" data-pwd="' + a.id + '">重置密码</button>');
    }
    // 彻底删除只给"已停用、而且真的能删"的账号：
    // 没停用就根本不出现；停用了还删不掉就做成灰按钮，悬停说出缺什么
    var canDelete = isSuper() && a.role !== "owner";
    if (canDelete && a.deleteBlocked === "") {
      btns.push('<button class="btn danger" data-acct-del="' + a.id + '">彻底删除</button>');
    } else if (canDelete && a.status === "disabled") {
      btns.push('<span class="btn danger is-locked" data-tip="' +
                PX.esc(a.deleteBlocked || "") + '" aria-disabled="true">彻底删除</span>');
    }

    openOverlay("account", a.username,
      '<dl class="kv">' + rows.map(function (r) {
        return "<dt>" + PX.esc(r[0]) + "</dt><dd>" + PX.esc(r[1]) + "</dd>";
      }).join("") + "</dl>" +
      (btns.length
        ? '<div class="row" style="margin-top:16px">' + btns.join("") + "</div>"
        : '<p class="page-sub" style="margin-top:16px">这个账号没有可以执行的操作。</p>'));
    // afterMutation() re-opens this dialog with fresh data
    $("#overlayBody").setAttribute("data-acct-body", String(a.id));
  }

  // Archived activities are history: they load a page at a time, on request.
  function loadArchived(reset) {
    if (!isSuper()) return Promise.resolve();
    if (S.archivedLoading) return Promise.resolve();
    if (reset) {
      S.archived = [];
      S.archivedTotal = null;
      markLoading("archived");
    } else if (S.archivedTotal !== null && S.archived.length >= S.archivedTotal) {
      return Promise.resolve();
    }
    S.archivedLoading = true;
    return PX.api("/events?archived=1&offset=" + S.archived.length + "&limit=20")
      .then(function (d) {
        S.archived = S.archived.concat(d.events || []);
        S.archivedTotal = d.total || 0;
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); })
      .then(function () {
        S.archivedLoading = false;
        markLoaded("archived");
        drawArchived();
      });
  }

  /* 活动管理两张表共用的首列：名字点开详情，模块停着的在旁边挂「不可用」胶囊 */
  function eventNameCell(e) {
    return '<span class="event-name">' +
      '<button class="link-btn" type="button" data-event-detail="' + e.seq + '">' +
      "<b>" + PX.esc(eventTitle(e)) + "</b></button>" +
      (e.stopped
        ? '<button class="badge-off" type="button" data-tip="依赖模块被禁用"' +
          ' aria-label="不可用：依赖模块被禁用">' + ICON_ALERT + "不可用</button>"
        : "") +
      "</span>";
  }

  function drawArchived() {
    var body = $("#archivedTable tbody");
    if (!body) return;
    body.innerHTML = S.archived.length ? S.archived.map(function (e) {
      // 已归档的活动已经归平台管：下载、取消归档、彻底删除都在这一行的操作列里
      return "<tr>" +
        "<td>" + eventNameCell(e) + "</td>" +
        '<td class="num mono">' + e.seq + "</td>" +
        (e.ownerMissing
          ? '<td class="dim">账号已删除</td>'
          : "<td>" + PX.esc(e.ownerName || "—") + "</td>") +
        '<td class="mono">' + PX.whenExact(e.archivedAt) + "</td>" +
        '<td class="mono">' + PX.esc(shortToken(e.id)) + "</td>" +
        '<td class="actions"><button class="detail-btn" type="button" ' +
        'data-event-detail="' + e.seq + '">详情</button></td>' + "</tr>";
    }).join("") : rowsOrEmpty(6, "archived", "还没有归档的活动");
    var more = $("#archivedMore");
    if (more) {
      var left = (S.archivedTotal === null) ? 0 : S.archivedTotal - S.archived.length;
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "再显示 " + Math.min(20, left) + " 条" : "加载更多";
    }
    var count = $("#archivedCount");
    if (count) {
      count.textContent = S.archivedTotal === null
        ? "" : "共 " + S.archivedTotal + " 场";
    }
  }

  function drawAllEvents() {
    if (isSuper() && S.archivedTotal === null && !S.archivedLoading) {
      loadArchived(true);
    }
    $("#allEventsTable tbody").innerHTML = S.events.length ? S.events.map(function (e) {
      // 首列点开详情；高权限动作都在详情里，表里不塞按钮
      return "<tr>" +
        "<td>" + eventNameCell(e) + "</td>" +
        '<td class="num mono">' + e.seq + "</td>" +
        "<td>" + PX.esc(e.ownerName || "—") + "</td>" +
        '<td class="mono">' + PX.when(e.createdAt) + "</td>" +
        '<td class="mono" style="font-size:.78rem" title="' + PX.esc(e.id) + '">' +
        PX.esc(shortToken(e.id)) + "</td>" +
        '<td class="actions"><button class="detail-btn" type="button" ' +
        'data-event-detail="' + e.seq + '">详情</button></td>' + "</tr>";
    }).join("") : rowsOrEmpty(6, "events", "当前没有活动");
  }

  /* 名单管理：服务上的全部名单与报名，超管及以上才看得到。
     做法与「活动管理」一样：表格只列事实，归档、转移与彻底删除都在详情弹窗里 */
  function loadAdminLists() {
    if (!isSuper()) return Promise.resolve();
    var tail = "&limit=100" + (S.adminArchived ? "&archived=1" : "");
    markLoading("adminRosters");
    markLoading("adminSignups");
    return Promise.all([
      PX.api("/rosters?scope=all" + tail),
      PX.api("/signups?scope=all" + tail)
    ]).then(function (res) {
      S.adminRosters = res[0].rosters || [];
      S.adminSignups = res[1].signups || [];
      markLoaded("adminRosters");
      markLoaded("adminSignups");
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function drawRosterAdmin() {
    var archived = !!S.adminArchived;
    var scopeBox = $("#adminListScope");
    if (scopeBox) {
      Array.prototype.forEach.call(scopeBox.querySelectorAll("[data-archived]"), function (b) {
        var on = b.getAttribute("data-archived") === (archived ? "1" : "0");
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
    }
    var hint = $("#adminListHint");
    if (hint) {
      hint.textContent = archived
        ? "归档之后谁都打不开也改不动，只有这里能取消归档或彻底删除"
        : "";
    }
    // 归档那一栏里，最后的时间列显示的是归档时间
    ["#adminRosterTable", "#adminSignupTable"].forEach(function (sel) {
      var head = document.querySelector(sel + " thead th:nth-child(5)");
      if (head) head.textContent = archived ? "归档时间" : "更新时间";
    });

    var rosters = S.adminRosters || [];
    $("#adminRosterTable tbody").innerHTML = rosters.length ? rosters.map(function (r) {
      return "<tr>" +
        '<td><button class="link-btn" type="button" data-admin-roster="' + r.id + '">' +
        "<b>" + PX.esc(r.name) + "</b></button></td>" +
        "<td>" + PX.esc(r.ownerName || "—") + "</td>" +
        "<td>" + PX.esc(r.visibilityLabel || "") + "</td>" +
        '<td class="num mono">' + (r.members || 0) + "</td>" +
        '<td class="mono">' + PX.when(archived ? r.archivedAt
          : (r.updatedAt || r.createdAt)) + "</td>" +
        '<td class="actions"><button class="detail-btn" type="button" ' +
        'data-admin-roster="' + r.id + '">详情</button></td>' + "</tr>";
    }).join("") : rowsOrEmpty(6, "adminRosters",
      archived ? "还没有归档的名单" : "当前没有名单");

    var signups = S.adminSignups || [];
    $("#adminSignupTable tbody").innerHTML = signups.length ? signups.map(function (s) {
      return "<tr>" +
        '<td><button class="link-btn" type="button" data-admin-signup="' + s.id + '">' +
        "<b>" + PX.esc(s.title) + "</b></button></td>" +
        "<td>" + PX.esc(s.ownerName || "—") + "</td>" +
        "<td>" + PX.esc(s.statusLabel || "") + "</td>" +
        '<td class="num mono">' + (s.entries || 0) + "</td>" +
        '<td class="mono">' + PX.when(archived ? s.archivedAt
          : (s.updatedAt || s.createdAt)) + "</td>" +
        '<td class="actions"><button class="detail-btn" type="button" ' +
        'data-admin-signup="' + s.id + '">详情</button></td>' + "</tr>";
    }).join("") : rowsOrEmpty(6, "adminSignups",
      archived ? "还没有归档的报名" : "当前没有报名");
  }

  /* 名单 / 报名的详情：超管在这里归档、换人或者彻底删掉别人名下的东西（都要提权）。
     归档与彻底删除会给对象换位置，做完就收掉详情；转移不挪动它，详情留着 */
  function openAdminListDetail(kind, id) {
    var list = kind === "roster" ? (S.adminRosters || []) : (S.adminSignups || []);
    var row = list.filter(function (x) { return String(x.id) === String(id); })[0];
    if (!row) return;
    var isArchived = !!S.adminArchived;
    var title = kind === "roster" ? row.name : row.title;
    var rows = kind === "roster"
      ? [["拥有者", row.ownerName || "—"],
         ["可见性", row.visibilityLabel || ""],
         ["人数", String(row.members || 0)],
         ["创建时间", PX.when(row.createdAt)],
         [isArchived ? "归档时间" : "更新时间",
          PX.when(isArchived ? row.archivedAt : (row.updatedAt || row.createdAt))]]
      : [["拥有者", row.ownerName || "—"],
         ["状态", row.statusLabel || ""],
         ["已报名", String(row.entries || 0)],
         ["创建时间", PX.when(row.createdAt)],
         [isArchived ? "归档时间" : "更新时间",
          PX.when(isArchived ? row.archivedAt : (row.updatedAt || row.createdAt))]];
    var acts = [];
    if (!isArchived) {
      acts.push({
        text: "打开", icon: ICON_EXT, kind: "ghost", primary: true,
        run: function () {
          var url = String(location.href).split("#")[0] +
            (kind === "roster" ? "#/roster/" + row.id : "#/signup/" + row.id);
          window.open(url, "_blank");
        }
      });
      acts.push({ text: "转移拥有者", kind: "", run: function () {
        transferListOwner(kind, row);
      } });
      acts.push({ text: "归档", kind: "", run: function () {
        archiveAdminList(kind, row);
      } });
    } else {
      acts.push({ text: "取消归档", kind: "", run: function () {
        unarchiveAdminList(kind, row);
      } });
      acts.push({ text: "彻底删除", kind: "danger", run: function () {
        deleteAdminList(kind, row);
      } });
    }
    var head = acts.filter(function (a) { return a.primary; });
    var rest = acts.filter(function (a) { return !a.primary; });
    var html = '<dl class="kv">' + rows.map(function (r) {
      return "<dt>" + PX.esc(r[0]) + "</dt><dd>" + PX.esc(r[1]) + "</dd>";
    }).join("") + "</dl>" +
      (head.length ? '<div class="detail-actions detail-actions-top">' +
        '<button class="btn ghost" type="button" data-list-act="0">' +
        head[0].text + "</button></div>" : "") +
      '<div class="detail-actions">' + rest.map(function (a, i) {
        return '<button class="btn ' + a.kind + '" type="button" data-list-act="' +
          (i + head.length) + '">' + PX.esc(a.text) + "</button>";
      }).join("") + "</div>";
    openOverlay(kind === "roster" ? "admin-roster" : "admin-signup", title, html,
      function (card) {
        card.querySelectorAll("[data-list-act]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var act = acts[Number(btn.getAttribute("data-list-act"))];
            if (act) act.run();
          });
        });
      });
  }

  function transferListOwner(kind, row) {
    var title = kind === "roster" ? row.name : row.title;
    pickPeople({
      title: "转移拥有者" + (title ? "：" + title : ""),
      mode: "single",
      exclude: [S.me && S.me.id],
      apply: function (people) {
        var who = people[0];
        if (!who) return;
        // 转移不挪动这份名单或报名：确认框叠在详情上面，取消回到详情，转完也留着
        confirmHighRisk("转移拥有者",
          "把它交给 " + PX.esc(who.label) + "？原来的拥有者会保留写入权限。确认继续？",
          "转移", false, function (note) {
            apiSudo("/" + kind + "/transfer",
              { method: "POST", body: { id: row.id, uid: who.uid } }, "转移拥有者", note)
              .then(function () {
                PX.toast("已交给 " + who.label);
                return loadAdminLists().then(drawRosterAdmin);
              })
              .catch(fail);
          }, { keepUnder: true });
      }
    });
  }

  /* 归档：把东西从列表里退下去，数据一条不动。做完之后它就不在这张表里了，所以收掉详情。
     自己的东西只要一句确认；替别人做是提权操作，没提权时先要密码。
     名单上还挂着报名时，会再问一句「一并归档？」 */
  function archiveAdminList(kind, row) {
    var what = kind === "roster" ? "名单" : "报名";
    var mine = S.me && row.ownerId === S.me.id;
    confirmHighRisk("归档" + what,
      "归档之后谁都打不开也改不动，只有超级管理员能取消归档或彻底删除。确认继续？",
      "归档", true, function () {
        archiveList(kind, row.id, function () {
          closeAllOverlays();
          PX.toast("已归档");
          loadAdminLists().then(drawRosterAdmin);
        });
      }, { noSudo: mine, keepUnder: true });
  }

  function unarchiveAdminList(kind, row) {
    var what = kind === "roster" ? "名单" : "报名";
    confirmHighRisk("取消归档" + what,
      "取消归档之后它回到普通列表里，拥有者又能打开它。确认继续？",
      "取消归档", false, function (note) {
        apiSudo("/" + kind + "/unarchive", { method: "POST", body: { id: row.id } },
                "取消归档" + what, note)
          .then(function () {
            closeAllOverlays();
            PX.toast("已取消归档");
            return loadAdminLists().then(drawRosterAdmin);
          })
          .catch(fail);
      }, { keepUnder: true });
  }

  function deleteAdminList(kind, row) {
    var what = kind === "roster" ? "名单" : "报名";
    confirmHighRisk("彻底删除" + what,
      "删除后无法恢复。确认继续？", "删除", true, function (note) {
        apiSudo("/" + kind + "/delete", { method: "POST", body: { id: row.id } },
                "删除" + what, note)
          .then(function () {
            // 删掉了就没有对象了：整摞收掉，回到表里
            closeAllOverlays();
            PX.toast("已删除");
            return loadAdminLists().then(drawRosterAdmin);
          })
          .catch(fail);
      }, { keepUnder: true });
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
    }).join("") : rowsOrEmpty(5, "accounts", "没有待处理的申请");

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
    }).join("") : rowsOrEmpty(5, "accounts", "还没有处理记录");
  }

  function drawApprovalEvents() {
    // UNWIRED(event-approvals): 活动申请队列不存在，默认自动通过。
  }

  /* 安全模式：服务信息里一行状态，下面一张卡管进出 */
  function safeModeOn() {
    return !!(S.hostMeta && S.hostMeta.safeMode);
  }

  function drawSafeMode() {
    var btn = $("#safeModeBtn");
    var note = $("#safeModeNote");
    if (!btn) return;
    var on = safeModeOn();
    // 只改文字那一截：整颗按钮的 textContent 会把图标一起抹掉
    var label = btn.querySelector(".btn-label");
    if (label) label.textContent = on ? "退出安全模式" : "进入安全模式";
    else btn.textContent = on ? "退出安全模式" : "进入安全模式";
    btn.className = "btn " + (on ? "" : "danger");
    if (note) {
      note.textContent = (S.hostMeta && S.hostMeta.supervised === false)
        ? "服务管理未运行，无法自动重启。详见服务文档"
        : "";
    }
  }

  /* 重启要等它回来：服务起来之前 /host 会连不上，连上了再看状态对不对 */
  function waitForRestart(want) {
    var started = Date.now();
    var probe = function () {
      PX.api("/host").then(function (d) {
        S.users = (d && d.users) || null;
        S.hostMeta = { safeMode: !!(d && d.safeMode), supervised: d && d.supervised };
        applyHost(d && d.host ? d.host : null);
        if (want === null || S.hostMeta.safeMode === want) {
          location.reload();
          return;
        }
        if (Date.now() - started > 45000) {
          PX.toast("服务还没按预期回来，去服务器上看一眼", true, 6000);
          return;
        }
        setTimeout(probe, 1200);
      }).catch(function () {
        if (Date.now() - started > 45000) return;
        setTimeout(probe, 700);
      });
    };
    setTimeout(probe, 500);
  }

  function restartService() {
    confirmHighRisk("重启服务？",
      "正在进行的操作会短暂断开，几秒后恢复。",
      "重启", false, function (note) {
        apiSudo("/service/restart", { method: "POST" }, "重启", note)
          .then(function (d) {
            if (d.restarting) {
              PX.toast("服务重启中…");
              waitForRestart(null);
            } else {
              PX.toast("服务管理未运行，请到服务器上重启");
            }
          })
          .catch(fail);
      });
  }

  function toggleSafeMode() {
    var want = !safeModeOn();
    var title = want ? "进入安全模式？" : "退出安全模式？";
    var note = want
      ? "活动页会立刻显示维护提示，所有人暂时打不开；数据原样不动，面板照常可用。<br>修好之后再退出安全模式。"
      : "活动页会恢复开放，服务重启一次。";
    var label = want ? "进入安全模式" : "退出安全模式";
    confirmHighRisk(title, note, label, want, function (note) {
      apiSudo("/service/safe_mode", { method: "POST", body: { on: want } }, label, note)
        .then(function (d) {
          S.hostMeta = { safeMode: !!d.safeMode, supervised: d.supervised };
          drawSafeMode();
          if (d.restarting) {
            PX.toast(want ? "正在进入安全模式，服务重启中…" : "正在退出安全模式，服务重启中…");
            waitForRestart(want);
          } else {
            PX.toast("配置已改，重启服务之后生效");
          }
        })
        .catch(fail);
    });
  }

  function drawServer() {
    var h = S.host;
  drawFeatures();
  drawSafeMode();
    $("#srvCpu").textContent = h ? pct(h.cpu) : "–";
    $("#srvMem").textContent = h ? pct(h.mem) : "–";
    $("#srvUptime").textContent = h && h.uptime ? humanUptime(h.uptime) : "–";
    var u = S.users;
    $("#srvUsers").textContent = u ? (u.online + " / " + u.registered) : "–";

    var rows = [];
    if (h) {
      rows.push(["主机", h.host || "—"]);
      rows.push(["版本", (h.version || "—") + (safeModeOn() ? "（安全模式）" : "")]);
      rows.push(["内存", (h.mem !== null && h.mem !== undefined)
        ? Math.round(h.mem) + "%" +
          (h.memTotalMB ? "（共 " + Math.round(h.memTotalMB) + " MB）" : "")
        : "—"]);
      // 进程与解释器并成一行：这里只回答"现在跑的是什么"
      rows.push(["进程", "pid " + (h.pid || "—") +
        (h.python ? "（Python " + h.python + "）" : "")]);
      rows.push(["数据目录", h.dataDir || "—"]);
      rows.push(["账号数", String(S.accounts.length)]);
    } else {
      rows.push(["状态", "这台机器读不到设备占用（非 Linux 或 /proc 不可用）"
                         + (safeModeOn() ? "（安全模式）" : "")]);
    }
    $("#srvInfo").innerHTML = rows.map(function (r) {
      return "<dt>" + PX.esc(r[0]) + "</dt><dd>" + PX.esc(r[1]) + "</dd>";
    }).join("");
  }

  /* ---------------------------------------------------------------- 备份 --- */
  /* 设置写 data/backup_settings.json，包放 backups/；恢复只走命令行（要停服务） */
  function loadBackup() {
    return PX.api("/backup").then(function (d) {
      S.backup = d || null;
      markLoaded("backup");
      drawBackup();
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  var BACKUP_KIND = { manual: "手动", auto: "自动", "pre-restore": "恢复前" };
  // 备份列表只摆最近几份：这里不操作备份数据，摆一长串只是占高度
  var BACKUP_ROWS = 5;

  function drawBackup() {
    var d = S.backup;
    if (!d) return;
    /* 后台每隔一会儿就会问一次服务端，回来的多半跟手上这份一模一样。
       一模一样就别再写一遍 DOM：写一遍会把正在展开的那段收回去、把滚到一半的
       列表顶回顶部，下拉框也会在弹出的一瞬被重设一次。 */
    var rows = (d.backups || []).slice(0, BACKUP_ROWS);
    var sig = JSON.stringify([d.settings, d.dir, d.due, rows, d.hints]);
    if (sig === S.backupSig) return;
    S.backupSig = sig;
    var cfg = d.settings || {};
    var schedule = cfg.schedule || {};
    var ret = cfg.retention || {};
    var dav = cfg.webdav || {};
    if ($("#bkEnabled")) $("#bkEnabled").checked = !!cfg.enabled;
    var modeSel = $("#bkMode");
    if (modeSel) {
      var wantMode = schedule.mode || "daily";
      modeSel.value = wantMode;
      Array.prototype.forEach.call(modeSel.options, function (opt) {
        opt.selected = opt.value === wantMode;
      });
    }
    if ($("#bkAt")) $("#bkAt").value = schedule.at || "03:30";
    if ($("#bkDaily")) $("#bkDaily").value = ret.daily === undefined ? 7 : ret.daily;
    if ($("#bkWeekly")) $("#bkWeekly").value = ret.weekly === undefined ? 4 : ret.weekly;
    if ($("#bkMonthly")) $("#bkMonthly").value = ret.monthly === undefined ? 6 : ret.monthly;
    if ($("#bkBytes")) $("#bkBytes").value = ret.maxBytes || 0;
    if ($("#bkDavEnabled")) $("#bkDavEnabled").checked = !!dav.enabled;
    if ($("#bkDavUrl")) $("#bkDavUrl").value = dav.url || "";
    if ($("#bkDavPath")) $("#bkDavPath").value = dav.path || "/";
    if ($("#bkDavUser")) $("#bkDavUser").value = dav.username || "";
    if ($("#bkDavPass")) {
      $("#bkDavPass").value = "";
      $("#bkDavPass").placeholder = dav.passwordSet ? "已保存（修改时重填）" : "应用密码";
    }
    if ($("#bkDavVerify")) $("#bkDavVerify").checked = dav.verify !== false;
    if ($("#bkDavPrune")) $("#bkDavPrune").checked = dav.prune !== false;
    if ($("#backupDir")) $("#backupDir").textContent = d.dir || "backups/";

    var due = d.due || {};
    var last = due.last ? PX.when(due.last) : "";
    if ($("#backupState")) {
      var line = last ? "最近一次备份：" + last : "还没有备份";
      if (!cfg.enabled) line = "自动备份已关闭；" + line;
      else if (due.reason) line = due.reason + "；" + line;
      $("#backupState").textContent = line;
    }
    var hints = $("#backupHints");
    if (hints) {
      var items = d.hints || [];
      hints.hidden = !items.length;
      hints.innerHTML = items.map(function (h) {
        return "<dt>" + PX.esc(h.level) + "</dt><dd>" + PX.esc(h.text) + "</dd>";
      }).join("");
    }
    var body = $("#backupTable tbody");
    if (body) {
      body.innerHTML = rows.length ? rows.map(function (b) {
        var tone = b.manifestReadable ? "ok" : "bad";
        var why = b.manifestReadable ? "备份清单可读取" : "无法读取备份清单";
        return "<tr><td><span class=\"dot " + tone + "\" data-tip=\"" +
          PX.esc(why) + "\" aria-label=\"" + PX.esc(why) + "\"></span>" +
          '<span class="mono">' + PX.esc(b.name) + "</span></td>" +
          "<td>" + PX.esc(PX.when(b.created)) + "</td>" +
          "<td class=\"mono\">" + PX.esc(fmtBytes(b.bytes)) + "</td>" +
          "<td>" + PX.esc(BACKUP_KIND[b.kind] || b.kind || "—") + "</td></tr>";
      }).join("") : rowsOrEmpty(4, "backup", "还没有备份");
    }
  }

  function saveBackup() {
    S.backupSig = null;          // 存完要让下一次 draw 重新套一遍
    var wear = function (sel) { return ($(sel) && $(sel).value || "").trim(); };
    var body = {
      enabled: !!($("#bkEnabled") && $("#bkEnabled").checked),
      schedule: { mode: wear("#bkMode") || "daily", at: wear("#bkAt") || "03:30" },
      retention: {
        daily: wear("#bkDaily") || 0,
        weekly: wear("#bkWeekly") || 0,
        monthly: wear("#bkMonthly") || 0,
        maxBytes: wear("#bkBytes") || 0
      },
      webdav: {
        enabled: !!($("#bkDavEnabled") && $("#bkDavEnabled").checked),
        url: wear("#bkDavUrl"),
        path: wear("#bkDavPath") || "/",
        username: wear("#bkDavUser"),
        verify: !!($("#bkDavVerify") && $("#bkDavVerify").checked),
        prune: !!($("#bkDavPrune") && $("#bkDavPrune").checked)
      }
    };
    var pass = $("#bkDavPass") ? $("#bkDavPass").value : "";
    if (pass) body.webdav.password = pass;      // 留空表示不改
    // 改这里要重输一次密码：它决定备份放哪、凭据是什么
    apiSudo("/backup/settings", { method: "POST", body: body },
            "修改备份设置", "备份的位置、留存与 WebDAV 凭据都会按新的来")
      .then(function () {
        PX.toast("已保存");
        if ($("#bkDavPass")) $("#bkDavPass").value = "";
        loadBackup();
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function runBackup() {
    var btn = $("#backupRun");
    if (btn) btn.disabled = true;
    PX.api("/backup/run", { method: "POST", body: {} })
      .then(function (d) {
        PX.toast("已备份 " + fmtBytes(d.bytes));
        return loadBackup();
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); })
      .then(function () { if (btn) btn.disabled = false; });
  }

  function testBackupDav() {
    var note = $("#bkDavNote");
    if (note) note.textContent = "正在试…";
    PX.api("/backup/webdav_test", { method: "POST", body: {} })
      .then(function (d) {
        if (note) note.textContent = "连得上，能写：" + (d.where || "");
      })
      .catch(function (e) {
        if (note) note.textContent = "没成：" + e.message;
      });
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

  /* 审计里的 target 只是一个 id，光看数字没意义：按动作前缀补上对象类型 */
  function auditTarget(a) {
    var t = (a.target || "").trim();
    if (!t) return "";
    var action = (a.action || "");
    var kind = action.indexOf("roster_") === 0 ? "roster"
      : action.indexOf("signup_") === 0 ? "signup"
      : action.indexOf("preset") === 0 ? "preset"
      : action.indexOf("event") === 0 ? "event"
      : action.indexOf("account") === 0 ? "account"
      : action.indexOf("module") === 0 ? "module" : "";
    return kind ? kind + "@" + t : t;
  }

  function drawAudit() {
    var count = $("#auditCount");
    if (count) {
      count.textContent = S.auditTotal
        ? (S.auditFilter ? "匹配 " + S.auditTotal + " 条" : "共 " + S.auditTotal + " 条操作")
        : "没有记录";
    }
    var more = $("#auditMore");
    if (more) more.hidden = S.audit.length >= (S.auditTotal || 0);
    $("#auditTable tbody").innerHTML = S.audit.length ? S.audit.map(function (r) {
      return '<tr><td class="mono">' + PX.whenExact(r.ts) + "</td>" +
        // 操作人：库里存编号，这里显示名字，编号淡一档跟在后面
        "<td>" + PX.esc(r.actorName || r.actorLabel || r.actor || "") +
        (r.actorId ? ' <span class="dim">(' + PX.esc(r.actorId) + ")</span>" : "") +
        "</td>" +
        // 动作名可以很长（event_member_reinvite）：装不下时靠悬停看全
        '<td title="' + PX.esc(r.action || "") + '">' + PX.esc(r.action) + "</td>" +
        "<td>" + PX.esc(r.target || "") + "</td>" +
        '<td class="wrap mono" title="' + PX.esc(r.detail || "") + '">' +
        PX.esc(r.detail || "") + "</td></tr>";
    }).join("") : rowsOrEmpty(5, "audit", S.auditFilter ? "没有匹配的记录" : "还没有记录");
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
      : "用于登录，3~24 位，只能用字母、数字、_ 和 -";

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

  /* ---------------------------------------------------------------- API 令牌 --- */
  /* 明文只在创建那一刻出现一次：之后服务端只有哈希与前四位/后五位。
     所以"复制"这一步要做得顺手，错过就只能重建一张。 */
  function loadTokens() {
    markLoading("tokens");
    return PX.api("/tokens")
      .then(function (d) {
        S.tokens = d.tokens || [];
        S.tokenMeta = { scopes: d.scopes || [], labels: d.scopeLabels || {} };
        markLoaded("tokens");
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function drawTokens() {
    var body = $("#tokenTable tbody");
    if (!body) return;
    var rows = S.tokens || [];
    var hint = $("#tokenHint");
    if (hint) hint.textContent = rows.length ? "共 " + rows.length + " 张" : "";
    body.innerHTML = rows.length ? rows.map(function (t) {
      var live = !t.revokedAt && t.state === "可用";
      return "<tr>" +
        "<td><b>" + PX.esc(t.name) + "</b></td>" +
        '<td class="mono">' + PX.esc(t.masked) + "</td>" +
        '<td class="wrap">' + (t.scopeLabels || []).map(function (s) {
          return '<span class="tag">' + PX.esc(s) + "</span>";
        }).join(" ") + "</td>" +
        '<td class="mono">' + PX.when(t.createdAt) + "</td>" +
        '<td class="mono">' + (t.lastUsedAt ? PX.when(t.lastUsedAt) : "从未") + "</td>" +
        '<td class="actions">' +
        (live
          ? '<button class="btn sm danger" type="button" data-token-revoke="' +
            t.id + '">吊销</button>'
          : '<span class="tag">' + PX.esc(t.state) + "</span>") +
        "</td></tr>";
    }).join("") : rowsOrEmpty(6, "tokens", "还没有令牌");
  }

  function tokenDialog() {
    var meta = S.tokenMeta || { scopes: [], labels: {} };
    var rows = meta.scopes.map(function (s) {
      return '<label class="check-row"><input type="checkbox" class="tk-scope" value="' +
        PX.esc(s) + '"' + (s === "read" ? " checked" : "") + "> " +
        PX.esc(meta.labels[s] || s) +
        '<span class="hint">' + PX.esc(TOKEN_SCOPE_HINT[s] || "") + "</span></label>";
    }).join("");
    openOverlay("token", "新建 API 令牌",
      '<div class="field"><label for="tkName">名称</label>' +
      '<input id="tkName" type="text" maxlength="40" placeholder="比如：群机器人">' +
      '<p class="hint">令牌描述，仅显示用途</p></div>' +
      '<div class="field"><label>权限</label>' + rows + "</div>" +
      '<div class="field"><label for="tkDays">有效期</label>' +
      '<select id="tkDays">' +
      '<option value="0">永不过期</option>' +
      '<option value="30">30 天</option>' +
      '<option value="90">90 天</option>' +
      '<option value="365">一年</option></select></div>' +
      '<div class="row" style="margin-top:18px;justify-content:space-between">' +
      '<span class="err" id="tkScopeErr"></span>' +
      '<span class="row">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="tkGo">创建</button></span></div>');
    $("#tkName").focus();
    $("#tkGo").addEventListener("click", createToken);
    // 一项权限都不给，这张令牌连自己是谁都问不出来：直接不让建
    function syncScope() {
      var picked = document.querySelectorAll(".tk-scope:checked").length > 0;
      $("#tkGo").disabled = !picked;
      $("#tkScopeErr").textContent = picked ? "" : "无法创建空权限令牌";
    }
    document.querySelectorAll(".tk-scope").forEach(function (box) {
      box.addEventListener("change", syncScope);
    });
    syncScope();
  }

  var TOKEN_SCOPE_HINT = {
    read: "列表、详情、导出这类只读操作",
    write: "名单、报名、活动设置的日常改动",
    admin: "账号、活动生命周期、外部资源这类平台级操作"
  };

  // 令牌在浏览器里生成：明文不经过网络，服务端只收到它的 sha256 与首尾几位。
  function makeTokenText() {
    var bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    var raw = "";
    for (var i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
    var b64 = btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    return "cae-" + b64;
  }

  function sha256Hex(text) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))
      .then(function (buffer) {
        return Array.prototype.map.call(new Uint8Array(buffer), function (b) {
          return ("0" + b.toString(16)).slice(-2);
        }).join("");
      });
  }

  function browserCanMint() {
    return !!(window.crypto && crypto.getRandomValues && crypto.subtle && window.TextEncoder);
  }

  function createToken() {
    var name = ($("#tkName").value || "").trim();
    if (!name) { PX.toast("请给这张令牌起个名字", true); return; }
    var scopes = Array.prototype.map.call(
      document.querySelectorAll(".tk-scope:checked"), function (el) { return el.value; });
    if (!scopes.length) { PX.toast("至少给一项权限", true); return; }
    var body = { name: name, scopes: scopes, days: Number($("#tkDays").value) || 0 };
    var plain = "";
    if (browserCanMint()) {
      plain = makeTokenText();
      var secret = plain.slice(4);          // cae- 之后的部分
      body.hash = "";
      body.head = secret.slice(0, 4);
      body.tail = secret.slice(-5);
      sha256Hex(plain).then(function (digest) {
        body.hash = digest;
        sendTokenCreate(body, plain);
      }).catch(function () {
        // 极少数环境算不出摘要：退回服务端生成，但要说清楚
        PX.toast("本机算不出摘要，改由服务端生成", true);
        delete body.hash;
        delete body.head;
        delete body.tail;
        sendTokenCreate(body, "");
      });
      return;
    }
    sendTokenCreate(body, "");
  }

  function sendTokenCreate(body, plain) {
    PX.api("/token/create", { method: "POST", body: body })
      .then(function (d) {
        closeOverlay();
        tokenOnceDialog(plain || d.token || "", d.item);
        return loadTokens().then(drawTokens);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  // 明文只有这一次机会：给一个能直接选中的框 + 复制按钮
  function tokenOnceDialog(plain, item) {
    openOverlay("token-once", "令牌已创建",
      '<p class="page-sub">这张令牌只会显示这一次。关掉之后，任何地方都只能看到首尾几位。</p>' +
      (plain
        ? '<div class="token-once"><code id="tkOnce">' + PX.esc(plain) + "</code></div>"
        : '<div class="unwired-note">这一次是由服务端生成的，但明文没能带回来。' +
          "请吊销这一张重新建一次。</div>") +
      '<p class="hint">' + PX.esc((item && item.name) || "") +
      " · " + PX.esc(((item && item.scopeLabels) || []).join("、")) +
      " · 用法：请求头 <code>Authorization: Bearer …</code></p>" +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      (plain ? '<button class="btn" type="button" id="tkCopy">复制</button>' : "") +
      '<button class="btn primary" type="button" data-close-overlay="1">我已保存</button></div>');
    if (plain) {
      $("#tkCopy").addEventListener("click", function () {
        copyTokenText(plain);
      });
    }
    var box = $("#tkOnce");
    if (box && window.getSelection) {
      var range = document.createRange();
      range.selectNodeContents(box);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }

  function revokeToken(id) {
    var t = (S.tokens || []).filter(function (x) { return x.id === id; })[0];
    openOverlay("token-revoke", "吊销令牌",
      '<p class="page-sub">吊销后立刻失效，正在用它的一方会被拒绝。这个动作不能撤销。</p>' +
      (t ? '<p class="page-sub"><b>' + PX.esc(t.name) + "</b> · " +
           PX.esc(t.masked) + "</p>" : "") +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn danger" type="button" id="tkRevokeGo">吊销</button></div>');
    $("#tkRevokeGo").addEventListener("click", function () {
      closeOverlay();
      PX.api("/token/revoke", { method: "POST", body: { id: id } })
        .then(function (d) {
          S.tokens = d.tokens || [];
          drawTokens();
          PX.toast("已吊销");
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
  }

  function copyTokenText(text) {
    // 复用页面里已有的复制助手：它会自己弹一条 toast，并给按钮加 .copied 反馈
    copyText(text, $("#tkCopy"));
  }

  function applyMe(me) {    if (!me) return;
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

  function systemDark() {
    return !!(window.matchMedia &&
              window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  /* 顶栏那枚按钮：图标由 panel.css 按 data-theme-pref / data-theme 直接给，
     这里补的是说明文字，说明它现在是哪一档 */
  function drawThemeBtn() {
    var btn = $("#themeBtn");
    if (!btn) return;
    var pref = document.documentElement.getAttribute("data-theme-pref") || "auto";
    var name = pref === "auto" ? "跟随系统" : (pref === "dark" ? "深色" : "浅色");
    btn.setAttribute("title", "主题：" + name);
    btn.setAttribute("aria-label", "主题：" + name);
  }

  function drawAppearance() {
    var pref = document.documentElement.getAttribute("data-theme-pref") || "auto";
    $$("#themeSeg button").forEach(function (b) {
      b.setAttribute("aria-checked",
        b.getAttribute("data-theme-pref") === pref ? "true" : "false");
    });
    if ($("#themeSeg")) $("#themeSeg").setAttribute("data-active", pref);
    drawThemeBtn();
  }

  /* ---------------------------------------------------------------- 运维助手 --- */
  /* 对话按会话存进数据库（有配额），地址里带着会话 id；
     模型配置在账号上，密钥只写不读——画面上永远只有"已配置"。 */

  /* 动作的正式名称由服务端给（policy.ACTION_LABELS）：批准条、过程标签共用一份。
     下面这份只是断网或旧服务端时的兜底，别在两边各写一套说法 */
  var STEP_LABEL = {
    list_events: "读取活动列表",
    event_board: "读取活动数据",
    list_rosters: "读取名单列表",
    roster_entries: "读取名单成员",
    list_signups: "读取报名列表",
    signup_entries: "读取报名记录",
    search_people: "检索人员",
    recent_audit: "读取审计日志",
    host_stats: "读取服务器状态",
    list_docs: "读取文档清单",
    read_doc: "读取文档",
    roster_add_person: "写入名单",
    roster_remove_person: "移出名单",
    signup_window: "修改报名设置",
    event_rename: "修改活动名称",
    session_title: "修改会话标题"
  };

  /* 助手回答里的链接：站内文档（[审批](usage/approval.md)）在新标签页打开那一篇，
     后面接 #标题 就落在那一节；站外只认白名单里的站点（服务端给的 patterns），
     名单外的一律当文字；别的路径也不给死链 */
  function assistantMdOpts() {
    var docs = S.assistantDocs || [];
    var allow = S.assistantLinks || [];
    return {
      link: function (target) {
        return window.MD.resolveLink(target, {
          docs: docs,
          base: "",
          allowExternal: allow,
          target: 'target="_blank" rel="noopener"',
          href: function (path, anchor) {
            return PX.PREFIX + "/docs#/" + path + (anchor ? "~" + anchor : "");
          }
        });
      }
    };
  }

  function toolLabel(name) {
    var fromServer = S.assistantLabels || {};
    return fromServer[name] || STEP_LABEL[name] || name;
  }

  /* 会真的改数据的动作：标签用强调色，跟只读的那些分开 */
  var WRITE_TOOLS = {
    roster_add_person: 1, roster_remove_person: 1, signup_window: 1, event_rename: 1,
    session_title: 1
  };

  /* 一步一行会刷出一串重复标签：同一轮里连调两次名单就并成「读取名单成员 ×2」。
     每个标签背后还留着这几次调用的摘要（悬停时看） */
  var STEP_TIPS = [];          // [{tool, calls: [{args, result, ok}]}]

  function stepChips(steps) {
    var order = [], group = {};
    (steps || []).forEach(function (s) {
      if (!s || !s.tool) return;
      if (!group[s.tool]) {
        group[s.tool] = { tool: s.tool, ok: true, n: 0, calls: [] };
        order.push(s.tool);
      }
      group[s.tool].n += 1;
      if (!s.ok) group[s.tool].ok = false;
      group[s.tool].calls.push({ args: (s.detail && s.detail.args) || {},
                                 result: (s.detail && s.detail.result) || "",
                                 ok: !!s.ok });
    });
    return order.map(function (name) {
      var g = group[name];
      var label = toolLabel(g.tool);
      if (g.n > 1) label += " ×" + g.n;
      STEP_TIPS.push({ tool: g.tool, calls: g.calls });
      return { label: label, ok: g.ok, write: !!WRITE_TOOLS[g.tool],
               tip: STEP_TIPS.length - 1 };
    });
  }

  /* 胶囊的悬停摘要：这一步调了什么、拿到了几条。指上去出来，触屏点一下，点外面收 */
  function stepTipHtml(item) {
    var head = '<div class="as-tip-head">' + PX.esc(toolLabel(item.tool)) + "</div>";
    var blocks = item.calls.slice(0, 4).map(function (call) {
      var keys = Object.keys(call.args || {});
      var lines = keys.slice(0, 6).map(function (k) {
        return '<div class="as-tip-row"><span>' + PX.esc(k) + "</span><b>" +
          PX.esc(String(call.args[k])) + "</b></div>";
      });
      if (call.result) {
        lines.push('<div class="as-tip-row"><span>结果</span><b>' +
                   PX.esc(call.result) + "</b></div>");
      }
      if (!lines.length) lines.push('<div class="as-tip-row"><span>无参数</span></div>');
      return '<div class="as-tip-block">' + lines.join("") + "</div>";
    });
    if (item.calls.length > 4) {
      blocks.push('<div class="as-tip-more">还有 ' + (item.calls.length - 4) +
                  " 次</div>");
    }
    return head + blocks.join("");
  }

  function openStepTip(chip) {
    var idx = Number(chip.getAttribute("data-step-tip"));
    var item = STEP_TIPS[idx];
    var tip = $("#asStepTip");
    if (!item || !tip) return;
    tip.innerHTML = stepTipHtml(item);
    tip.hidden = false;
    var box = chip.getBoundingClientRect();
    var size = tip.getBoundingClientRect();
    var left = Math.min(Math.max(8, box.left), window.innerWidth - size.width - 8);
    var top = box.top - size.height - 8;
    if (top < 8) top = box.bottom + 8;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
    S.stepTipFor = idx;
  }

  function closeStepTip() {
    var tip = $("#asStepTip");
    if (tip) tip.hidden = true;
    S.stepTipFor = null;
  }

  /* 时间戳：当天只给时分秒；同年补日期；跨年再补年份 */
  function messageTime(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000), now = new Date();
    if (isNaN(d.getTime())) return "";
    var hm = PX.pad(d.getHours()) + ":" + PX.pad(d.getMinutes()) + ":" +
      PX.pad(d.getSeconds());
    var day = PX.pad(d.getMonth() + 1) + "/" + PX.pad(d.getDate());
    if (d.getFullYear() !== now.getFullYear()) return d.getFullYear() + "/" + day + " " + hm;
    if (d.getMonth() !== now.getMonth() || d.getDate() !== now.getDate()) return day + " " + hm;
    return hm;
  }

  function nowStamp() {
    return Math.round(Date.now() / 1000);
  }

  function loadAssistant() {
    return PX.api("/assistant")
      .then(function (d) {
        S.assistantCfg = d.config || { ready: false };
        S.assistantLabels = d.labels || {};
        S.assistantDocs = d.docs || [];
        S.assistantLinks = d.links || [];
        adoptPendingServer(d.pending || []);
        drawAssistant();
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  /* 密钥框里放的是"假内容"：一串星号，不是真密钥。
     这样既看得出"已经配过"，又不会把真东西塞进 DOM 里让人读走。
     保存时如果框里还是这串星号，就当没改过；要换密钥就直接覆盖，要删就点「清除密钥」。 */
  var KEY_MASK = "••••••••••••••••••";

  function keyFieldMasked() {
    var box = $("#asKey");
    return !!box && box.value === KEY_MASK;
  }

  function showKeyMask(cfg) {
    var box = $("#asKey");
    if (!box) return;
    if (cfg.hasKey) {
      box.value = KEY_MASK;
      box.dataset.masked = "1";
      box.placeholder = "";
    } else {
      box.value = "";
      delete box.dataset.masked;
      box.placeholder = "sk-…";
    }
  }

  // 模型设置放在弹窗里：输入框常驻底部，原来那张内嵌卡片会把对话挤走
  function assistantConfigDialog() {
    var cfg = S.assistantCfg || {};
    openOverlay("assistant-cfg", "模型设置",
      '<div class="field"><label for="asBase">接口地址</label>' +
      '<input id="asBase" type="text" autocomplete="off" spellcheck="false" value="' +
      PX.esc(cfg.base || "") + '"></div>' +
      '<div class="field"><label for="asModel">模型</label>' +
      '<div class="row" style="gap:8px;align-items:center;flex-wrap:nowrap">' +
      '<input id="asModel" type="text" autocomplete="off" spellcheck="false" ' +
      'style="max-width:300px" value="' + PX.esc(cfg.model || "") + '">' +
      '<button class="btn sm" id="asModels" type="button">获取模型</button></div>' +
      '<div class="as-models" id="asModelList" hidden></div></div>' +
      '<div class="field"><label for="asKey">密钥</label>' +
      '<input id="asKey" type="password" autocomplete="off" spellcheck="false">' +
      '<p class="hint">留空表示保持现状；清除请点下面的按钮</p></div>' +
      '<p class="hint">端点需兼容 OpenAI 的 <code>/chat/completions</code></p>' +
      '<div class="as-adv">' +
      '<button class="as-adv-head" id="asAdvToggle" type="button" aria-expanded="false">' +
      '<svg viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M6 8.2l4 4 4-4"/></svg><span>高级设置</span></button>' +
      '<div class="as-adv-body" id="asAdvBody" hidden>' +
      '<div class="field"><label for="asCtx">上下文上限</label>' +
      '<input id="asCtx" type="text" inputmode="numeric" style="max-width:160px" ' +
      'placeholder="由模型决定" value="' +
      PX.esc(String(cfg.storedContext || "")) + '"></div></div></div>' +
      '<label class="switch" id="asResumeRow" style="margin-top:14px">' +
      '<input type="checkbox" id="asResume"' +
      ((cfg.resume === false) ? "" : " checked") + '>' +
      '<span class="track"></span>' +
      '<span class="switch-text">进入时继续上次的会话</span></label>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn danger" id="asClearKey" type="button">清除密钥</button>' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn primary" id="asSave" type="button">保存</button></div>');
    showKeyMask(cfg);
    $("#asSave").addEventListener("click", saveAssistantConfig);
    $("#asAdvToggle").addEventListener("click", function () {
      var body = $("#asAdvBody");
      var open = body.hidden;
      body.hidden = !open;
      this.setAttribute("aria-expanded", open ? "true" : "false");
      this.classList.toggle("open", open);
    });
    $("#asModels").addEventListener("click", fetchAssistantModels);
    $("#asModelList").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-model]");
      if (btn) pickAssistantModel(btn.getAttribute("data-model"));
    });
    $("#asClearKey").addEventListener("click", function () {
      // 清掉之后助手就不能用了，要重填：先问一句，不用密码
      confirmHighRisk("清除密钥", "清除密钥之后助手将不可用，直到再次填入有效密钥。",
                      "清除", true, function () {
        PX.api("/assistant/config", { method: "POST", body: { clear: true } })
          .then(function (d) {
            S.assistantCfg = d.config || S.assistantCfg;
            showKeyMask(S.assistantCfg);
            drawAssistant();
            drawContext(null);
            PX.toast("密钥已清除");
          })
          .catch(function (e) { PX.toast(PX.esc(e.message), true); });
      }, { noSudo: true, keepUnder: true });
    });
    // 星号是假的：一聚焦就清掉，让人能直接覆盖
    $("#asKey").addEventListener("focus", function () {
      if (keyFieldMasked()) this.value = "";
    });
    $("#asKey").addEventListener("blur", function () {
      if (!this.value && (S.assistantCfg || {}).hasKey) this.value = KEY_MASK;
    });
  }

  // 全屏编辑：打进来时带上现有的内容，改的同时就写回去，退出不用再同步一次
  function assistantFullscreenDialog() {
    var box = $("#assistantText");
    openOverlay("assistant-full", "写长一点",
      '<textarea class="as-full" id="asFull" maxlength="2000"></textarea>' +
      '<div class="row" style="margin-top:14px;justify-content:flex-end">' +
      '<button class="btn primary" data-close-overlay="1" type="button">完成</button></div>');
    var full = $("#asFull");
    full.value = box.value;
    full.focus();
    full.selectionStart = full.selectionEnd = full.value.length;
    full.addEventListener("input", function () {
      box.value = full.value;
      growAssistantBox();
    });
  }

  // 对话存档：每轮回答落地后存一次（改动过才存），刷新回来接着聊
  function currentTitle() {
    var first = (S.assistantMsgs || []).filter(function (m) {
      return m.role === "user";
    })[0];
    return first ? String(first.content).slice(0, 40) : "未命名会话";
  }

  // 新会话存下之后，请模型起个短名字；起不出来就留着第一句话
  function autoTitleSession(id) {
    var first = (S.assistantMsgs || []).filter(function (m) {
      return m.role === "user";
    })[0];
    if (!first) return;
    PX.api("/assistant/title", { method: "POST", body: { text: first.content } })
      .then(function (d) {
        var title = (d.title || "").trim();
        // 这中间人自己改过名字了：别拿模型起的名字盖掉他
        if (!title || S.assistantTitleManual) return;
        if (S.assistantSessionId === id) setSessionName(title);
        return PX.api("/assistant/session/rename",
                      { method: "POST", body: { id: id, title: title } })
          .then(refreshAssistantDrawer);
      })
      .catch(function () { /* 名字只是好看：安静地算了 */ });
  }

  function saveAssistantSession() {
    var msgs = S.assistantMsgs || [];
    if (!msgs.length) return Promise.resolve();
    // 名字只在新会话时说一次：之后存对话不再动它，人手改的名字才留得住
    var fresh = !S.assistantSessionId;
    return PX.api("/assistant/session/save", {
      method: "POST",
      body: { id: S.assistantSessionId || 0,
              title: fresh ? currentTitle() : "", messages: msgs }
    }).then(function (d) {
      S.assistantSessionId = d.id;
      S.assistantUsed = d.used;
      S.assistantQuota = d.quota;
      try { localStorage.setItem("as:lastSession", String(d.id)); } catch (e) {}
      setSessionInHash(d.id);
      if (fresh) {
        setSessionName(currentTitle());
        S.assistantTitlePending = true;   // 这一段是新存下来的：该起个短名字
      }
      // 起过一次就不再起
      if (S.assistantTitlePending && !S.assistantTitleManual) {
        S.assistantTitlePending = false;
        autoTitleSession(d.id);
      }
      drawAssistantQuota();
    }).catch(function (e) {
      // 满了就说清楚，别默默丢掉用户的对话
      if (e.status === 413) PX.toast(PX.esc(e.message), true);
    });
  }

  /* 发第一句话之前先把会话存下来。
     两个理由：挂起的写动作是按会话 id 记的（没 id 就点不到那张卡片上的小红点），
     助手要给自己这段对话改名也得有个 id 可改。存不下来就照常聊，只是刷新之后找不回来 */
  function ensureAssistantSession() {
    if (S.assistantSessionId) return Promise.resolve();
    var msgs = S.assistantMsgs || [];
    if (!msgs.length) return Promise.resolve();
    return PX.api("/assistant/session/save", {
      method: "POST",
      body: { id: 0, title: currentTitle(), messages: msgs }
    }).then(function (d) {
      S.assistantSessionId = d.id;
      S.assistantUsed = d.used;
      S.assistantQuota = d.quota;
      S.assistantTitlePending = true;   // 短名字等这一轮答完再起
      try { localStorage.setItem("as:lastSession", String(d.id)); } catch (e) {}
      setSessionInHash(d.id);
      setSessionName(currentTitle());
      drawAssistantQuota();
    }).catch(function () { /* 存不下也照常聊 */ });
  }

  function drawAssistantQuota() {
    var sub = $("#assistantSub");
    if (!sub) return;
    var cfg = S.assistantCfg || {};
    if (!cfg.ready) {
      sub.textContent = "尚未配置模型，请在右上角打开「模型设置」";
      return;
    }
    var bits = ["当前模型：" + (cfg.model || "—")];
    if (S.assistantUsed !== undefined && S.assistantQuota) {
      bits.push("存档 " + Math.round(S.assistantUsed / 1024) + " / " +
                Math.round(S.assistantQuota / 1024) + " KB");
    }
    sub.textContent = bits.join(" · ");
  }

  // 会话：地址里带着 id（#/assistant/<id>），所以开几个标签页就各是各的会话
  function sessionIdFromHash() {
    var m = (location.hash || "").match(/^#\/assistant\/(\d+)/);
    return m ? Number(m[1]) : 0;
  }

  function setSessionInHash(id) {
    if (!id) return;
    var want = "#/assistant/" + id;
    if (location.hash === want) return;
    // 只把地址换成带 id 的那一版，不再走一遍 onRoute：刚存下来的这一段没必要重读
    try {
      history.replaceState(null, "", want);
    } catch (e) {
      location.replace(want);
    }
  }

  function openAssistantSession(id) {
    S.assistantSessionId = id;
    S.assistantMsgs = [];
    S.assistantTitleManual = false;   // 换了一段对话："名字是人改的"这个标记重新算
    S.assistantTitlePending = false;
    if (!id) {
      // 新的对话：没有要等的东西，直接就是那一页空白，中间不闪任何东西
      markLoaded("assistant");
      hideApproval();
      drawAssistant();
      drawAssistantQuota();
      setSessionName("新会话");
      drawContext(null);
      return Promise.resolve();
    }
    // 读一段存档对话：等的时候整块居中写"正在加载对话"，
    // 别先把"新对话"那一页摆出来再换成历史
    markLoading("assistant");
    hideApproval();          // 上一条待批准的请求不属于这一段对话
    setSessionName("正在加载…");
    drawAssistant();
    return PX.api("/assistant/session?id=" + id).then(function (r) {
      S.assistantMsgs = (r.session && r.session.messages) || [];
      setSessionName((r.session && r.session.title) || "会话");
      try { localStorage.setItem("as:lastSession", String(id)); } catch (e) {}
      markLoaded("assistant");
      drawAssistant();
      drawAssistantQuota();
      loadContext();
      syncToBottom();
      restorePending(id);
    }).catch(function () {
      S.assistantSessionId = 0;
      markLoaded("assistant");
      setSessionName("新会话");
      drawAssistant();
      drawAssistantQuota();
    });
  }

  /* 这一段对话还挂着一个等批准的动作（刷新过、或刚从别的页面回来）：
     把那条提示捡回来。令牌在服务端还等着，五分钟内有效 */
  function restorePending(id) {
    if (!id || S.assistantPending) return Promise.resolve();
    return PX.api("/assistant/pending?session=" + id).then(function (d) {
      if (S.assistantSessionId !== id) return;         // 人已经换到别处去了
      if (d && d.pending) { showApproval(d.pending); return; }
      // 服务端说没有了（过时、或已经批过）：把这个点点掉
      if (S.asPending) delete S.asPending[id];
      drawAssistantPending();
    }).catch(function () {});
  }

  function setSessionName(text) {
    var el = $("#asSessionName");
    if (el) el.textContent = text || "新会话";
    if (el) el.title = text || "新会话";   // 截断了也能悬停看全
  }

  /* ---- 上下文用量：会话名右边那条小环 ----
     数字是估算的（服务端按字数估 token），用途是"大概还剩多少"；
     指上去或点一下看明细：系统提示词 / 工具 / 对话消息。 */
  var CTX_RADIUS = 5.5;
  var CTX_CIRC = 2 * Math.PI * CTX_RADIUS;

  /* 占用很小的时候别一律显示 0%：<1% 两位小数、<10% 一位小数、其余取整 */
  function fmtPercent(pct) {
    if (pct <= 0) return "0%";
    if (pct < 1) return (Math.round(pct * 100) / 100).toFixed(2) + "%";
    if (pct < 10) return (Math.round(pct * 10) / 10).toFixed(1) + "%";
    return Math.round(pct) + "%";
  }

  function fmtTokens(n) {
    n = Number(n) || 0;
    function one(v) {
      return v >= 100 ? String(Math.round(v)) : String(Math.round(v * 10) / 10);
    }
    if (n < 1000) return String(n);
    // 正好是整千/整百万就不带"~"：1000000 是 1M，999999 才是 ~1M
    if (n < 1e6) return (n % 1e3 === 0 ? "" : "~") + one(n / 1e3) + "K";
    return (n % 1e6 === 0 ? "" : "~") + one(n / 1e6) + "M";
  }

  function drawContext(stats) {
    var wrap = $("#asContextWrap");
    var fill = $("#asCtxFill");
    if (!wrap || !fill) return;
    S.assistantCtx = stats || null;
    if (!stats || !stats.window) { wrap.hidden = true; return; }
    wrap.hidden = false;
    var pct = Math.max(0, Math.min(100, Number(stats.percent) || 0));
    fill.setAttribute("stroke-dasharray",
      (CTX_CIRC * pct / 100).toFixed(2) + " " + CTX_CIRC.toFixed(2));
    wrap.classList.toggle("warn", pct >= 90);
    $("#asCtxPercent").textContent = fmtPercent(pct);
    $("#asCtxFigures").textContent = fmtTokens(stats.used) + " / " +
      fmtTokens(stats.window);
    var parts = stats.parts || {};
    var total = (parts.system || 0) + (parts.tools || 0) + (parts.messages || 0);
    var rows = [["system", "系统提示词"], ["tools", "工具"], ["messages", "对话消息"]];
    $("#asCtxBar").innerHTML = total ? rows.map(function (r) {
      var w = pct * (parts[r[0]] || 0) / total;
      return w <= 0 ? "" : '<span class="as-ctx-seg ' + r[0] +
        '" style="width:' + w.toFixed(2) + '%"></span>';
    }).join("") : '<span class="as-ctx-seg messages" style="width:' + pct +
      '%"></span>';
    $("#asCtxRows").innerHTML = rows.map(function (r) {
      return '<div class="as-ctx-row"><dt><span class="as-ctx-swatch ' + r[0] +
        '"></span>' + r[1] + "</dt><dd>" + fmtTokens(parts[r[0]] || 0) +
        "</dd></div>";
    }).join("");
  }

  function loadContext() {
    if (!S.assistantSessionId) { drawContext(null); return Promise.resolve(); }
    return PX.api("/assistant/context?session=" + S.assistantSessionId)
      .then(drawContext)
      .catch(function () { /* 环只是参考，取不到就不显示 */ });
  }

  function showContextPanel(on) {
    var panel = $("#asContextPanel");
    var btn = $("#asContext");
    if (!panel || !btn) return;
    if (on) {
      panel.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      loadContext();
      return;
    }
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  }

  // 从侧栏进来时：恢复上次打开的那段（记在本机），或者直接开一段新的。
  // 已经在这一页里换会话（比如点了新建）就照地址来，不要再把上一段捞回来。
  function resumeAssistant(prevRoute) {
    var id = sessionIdFromHash();
    if (id) return openAssistantSession(id);
    var resume = (S.assistantCfg || {}).resume !== false && prevRoute !== "assistant";
    if (!resume) return openAssistantSession(0);
    var last = "";
    try { last = localStorage.getItem("as:lastSession") || ""; } catch (e) {}
    if (last) return openAssistantSession(Number(last));
    return openAssistantSession(0);
  }

  // 会话列表：输入框上方那条点开，从左边拉出抽屉（和主侧栏一样）
  function openAssistantDrawer() {
    var shell = $("#shell");
    if (shell.classList.contains("as-drawer-open")) return;
    PX.api("/assistant/sessions").then(function (d) {
      adoptDrawerPending(d.sessions || []);
      drawAssistantDrawer(d);
      shell.classList.add("as-drawer-open");
      syncScrim();
      $("#asDrawerClose").focus();
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  // 列表里那几行的 `pending` 是服务端说了算；还没存过的对话（id 0）不在列表里，留着
  function adoptDrawerPending(rows) {
    var keep = {};
    Object.keys(S.asPending || {}).forEach(function (k) {
      if (!Number(k)) keep[k] = true;
    });
    (rows || []).forEach(function (s) { if (s.pending) keep[s.id] = true; });
    S.asPending = keep;
    syncPendingDot();
  }

  function closeAssistantDrawer() {
    $("#shell").classList.remove("as-drawer-open");
    syncScrim();
  }

  function assistantDrawerOpen() {
    return $("#shell").classList.contains("as-drawer-open");
  }

  // 抽屉开着的时候，重命名、删除之后把列表重新画一遍
  function refreshAssistantDrawer() {
    if (!assistantDrawerOpen()) return Promise.resolve();
    return PX.api("/assistant/sessions").then(drawAssistantDrawer)
      .catch(function () {});
  }

  function drawAssistantDrawer(d) {
    d = d || {};
    // 也用来"只重画一遍"（小红点变了），那时手上没有新数据，沿用上一次的
    if (d.quota !== undefined) S.assistantQuota = d.quota;
    if (d.used !== undefined) S.assistantUsed = d.used;
    var rows = d.sessions || S.assistantList || [];
    S.assistantList = rows;
    var body = $("#asDrawerBody");
    if (body) {
      body.innerHTML = rows.length ? '<div class="as-slist">' + rows.map(function (s) {
        // 属性都带 as- 前缀：页面里那套 data-rename / data-del 是活动的，别撞上
        var wait = hasSessionPendingOf(s.id);
        return '<div class="as-srow' +
          (s.id === S.assistantSessionId ? " on" : "") + '" data-sid="' + s.id + '">' +
          (wait ? '<span class="as-wait" title="有待批准的操作"></span>' : "") +
          '<button class="as-sopen" type="button" data-as-open="' + s.id + '">' +
          PX.esc(s.title) + '<span>' + PX.when(s.updatedAt) + " · " +
          Math.max(1, Math.round(s.size / 1024)) + " KB</span></button>" +
          '<button class="as-sicon" type="button" data-as-rename="' + s.id +
          '" title="重命名" aria-label="重命名">' + ICON_PENCIL + "</button>" +
          '<button class="as-sicon danger" type="button" data-as-del="' + s.id +
          '" title="删除" aria-label="删除">' + ICON_TRASH + "</button>" +
          "</div>";
      }).join("") + "</div>"
        : '<p class="page-sub">无会话</p>';
    }
    var foot = $("#asDrawerFoot");
    if (foot) {
      foot.textContent = "存档 " + Math.round(S.assistantUsed / 1024) + " / " +
        Math.round(S.assistantQuota / 1024) + " KB";
    }
    drawAssistantQuota();
  }

  function hasSessionPendingOf(id) {
    if (!S.asPending) return false;
    return !!(S.asPending[Number(id) || 0] || S.asPending[id]);
  }

  function assistantDrawerClick(e) {
    var open = e.target.closest("[data-as-open]");
    if (open) {
      var id = Number(open.getAttribute("data-as-open"));
      // 点的就是手头这一段：什么都不做，别把它从服务端重新读一遍
      if (id === S.assistantSessionId) {
        closeAssistantDrawer();
        return;
      }
      var want = "#/assistant/" + id;
      closeAssistantDrawer();
      // 换一页会走 onRoute；同一条会话就把地址省了，直接打开
      if (location.hash === want) openAssistantSession(id);
      else location.hash = want;
      return;
    }
    var rename = e.target.closest("[data-as-rename]");
    if (rename) {
      renameSession(Number(rename.getAttribute("data-as-rename")));
      return;
    }
    var del = e.target.closest("[data-as-del]");
    if (del) deleteSession(Number(del.getAttribute("data-as-del")));
  }

  function renameSession(id) {
    var row = ((S.assistantList || []).filter(function (s) { return s.id === id; })[0]) || {};
    openOverlay("as-rename", "重命名会话",
      '<div class="field"><label for="asRename">名称</label>' +
      '<input id="asRename" type="text" maxlength="24" value="' +
      PX.esc(row.title || "") + '" placeholder="最多 24 个字"></div>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn primary" id="asRenameGo" type="button">保存</button></div>');
    $("#asRenameGo").addEventListener("click", function () {
      var name = ($("#asRename").value || "").trim();
      if (!name) { PX.toast("请填一个名称", true); return; }
      PX.api("/assistant/session/rename", { method: "POST", body: { id: id, title: name } })
        .then(function () {
          closeOverlay();
          if (S.assistantSessionId === id) {
            S.assistantTitleManual = true;   // 名字是人定的，之后谁也别改它
            setSessionName(name);
          }
          refreshAssistantDrawer();
          PX.toast("已重命名");
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
    $("#asRename").focus();
  }

  function deleteSession(id) {
    var row = ((S.assistantList || []).filter(function (s) { return s.id === id; })[0]) || {};
    var name = (row.title || "").trim() || "这段会话";
    openOverlay("as-del", "删除会话",
      '<p class="page-sub">确定要删除「' + PX.esc(name) + '」吗？</p>' +
      '<p class="page-sub">会话删除后将无法恢复。</p>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn danger" id="asDelGo" type="button">删除</button></div>');
    $("#asDelGo").addEventListener("click", function () {
      closeOverlay();
      PX.api("/assistant/session/delete", { method: "POST", body: { id: id } })
        .then(function (d) {
          S.assistantUsed = d.used;
          S.assistantQuota = d.quota;
          if (S.assistantSessionId === id) {
            S.assistantSessionId = 0;
            S.assistantMsgs = [];
            setSessionName("新会话");
            location.replace("#/assistant");
            drawAssistant();
          }
          drawAssistantQuota();
          // 这段对话没了，它挂着的那个动作也就没地方点了
          if (S.asPending) delete S.asPending[id];
          syncPendingDot();
          refreshAssistantDrawer();
          PX.toast("已删除");
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
  }

  function exportAssistantSession() {
    if (!S.assistantSessionId) {
      PX.toast("新会话还没有存档", true);
      return;
    }
    window.open(PX.PREFIX + "/api/panel/assistant/session/export?id=" +
                S.assistantSessionId, "_blank", "noopener");
  }

  function drawAssistant() {
    var cfg = S.assistantCfg || { ready: false };
    drawAssistantQuota();
    var sub = $("#assistantSub");
    if (sub && !S.assistantQuota && !cfg.ready) {
      sub.textContent = "尚未配置模型，请在右上角打开「模型设置」";
    }
    // 新会话没有存档可导：按钮灰掉，说明写在 title 上
    var exp = $("#asExport");
    if (exp) {
      exp.disabled = !S.assistantSessionId;
      var label = exp.disabled ? "新会话还没有存档，暂时不能导出" : "导出这段对话";
      exp.title = label;
      exp.setAttribute("aria-label", label);
    }
    // 正在读存档对话时，中间的"正在加载对话"顶掉"新对话"那一页：
    // 不然会先看见欢迎语与几个问法，再被历史消息替换掉
    var busy = !loaded("assistant");
    var loadingView = $("#assistantLoading");
    if (loadingView) loadingView.hidden = !busy;
    var empty = $("#assistantEmpty");
    if (empty) empty.hidden = busy || (S.assistantMsgs || []).length > 0;
    drawAssistantLog();
    growAssistantBox();
  }

  function drawAssistantLog() {
    var log = $("#assistantLog");
    if (!log) return;
    STEP_TIPS = [];
    closeStepTip();
    log.innerHTML = (S.assistantMsgs || []).map(function (m, idx) {
      var when = messageTime(m.ts);
      // 时间那一行：[复制] [时间]。抄的只是这一段话本身，不含上面那几枚动作标签
      var foot = '<div class="as-foot">' +
        '<button class="as-copy" type="button" data-copy-msg="' + idx +
        '" title="复制这段话" aria-label="复制这段话">' +
        '<span class="i-copy">' + ICON_COPY + "</span>" +
        '<span class="i-check">' + ICON_CHECK + "</span></button>" +
        (when ? '<span class="as-time">' + PX.esc(when) + "</span>" : "") + "</div>";
      if (m.role === "user") {
        return '<div class="as-msg as-user"><div class="as-body">' +
          '<div class="as-text">' +
          PX.esc(m.content).replace(/\n/g, "<br>") + "</div>" + foot + "</div></div>";
      }
      var steps = stepChips(m.steps).map(function (s) {
        var tip = ' data-step-tip="' + s.tip + '"';
        if (!s.ok) {
          return '<span class="as-step bad" title="没有执行"' + tip + ">" +
            PX.esc(s.label) + "</span>";
        }
        return '<span class="as-step' + (s.write ? " is-write" : "") + '"' + tip +
          ">" + PX.esc(s.label) + "</span>";
      }).join("");
      // 胶囊放进气泡里：它不许超过气泡的右边缘，放进去就自然被约束住了；
      // 有胶囊时下面压一条分隔线，把"做过什么"和"说了什么"分开
      return '<div class="as-msg as-bot"><div class="as-body">' +
        '<div class="as-text md">' +
        (steps ? '<div class="as-steps">' + steps + "</div>" +
                 '<div class="as-divider"></div>' : "") +
        PX.md(m.content || "", assistantMdOpts()) + "</div>" + foot + "</div></div>";
    }).join("");
    if (thinkingOn) paintThinking();   // 重画之后把"正在…"那一行补回去
    scrollAssistant();
  }

  function scrollAssistant() {
    var box = $("#assistantScroll");
    if (box) box.scrollTop = box.scrollHeight;
    syncToBottom();
  }

  /* 「回到最新」：离开底部时才出现，点了滑回最下面 */
  function syncToBottom() {
    var btn = $("#asToBottom");
    var box = $("#assistantScroll");
    if (!btn || !box) return;
    var far = box.scrollHeight - box.scrollTop - box.clientHeight;
    btn.hidden = far <= 40;
  }

  /* 正在思考：动态的三个点，写在对话流末尾，回答落地时移除。
     文字会随着服务端的进度换：思考 → 执行某个工具 → 生成回答。
     这一行不在 S.assistantMsgs 里，所以整段重画之后要自己补回去——写动作会触发
     一次数据重载，重载会把对话重画，不补就出现"什么都不显示"的空档 */
  var thinkingOn = false;
  var thinkingText = "正在思考";

  function paintThinking() {
    var log = $("#assistantLog");
    if (!log) return;
    var old = $("#asThinking");
    if (old) old.remove();
    if (!thinkingOn) return;
    var el = document.createElement("div");
    el.className = "as-msg as-bot";
    el.id = "asThinking";
    el.innerHTML = '<span class="as-thinking">' +
      '<span class="as-thinking-label">' + PX.esc(thinkingText) +
      "</span><i></i><i></i><i></i></span>";
    log.appendChild(el);
    scrollAssistant();
  }

  function showThinking() {
    thinkingOn = true;
    thinkingText = "正在思考";
    paintThinking();
  }

  /* 这一轮走到哪一步：think / tools / write，说给用户听 */
  var PHASE_TEXT = { think: "正在思考" };

  function phaseText(phase, label) {
    if (phase === "tools") {
      return label ? "正在执行「" + toolLabel(label) + "」" : "正在执行";
    }
    return PHASE_TEXT[phase] || "";
  }

  function setThinkingLabel(text) {
    if (!text || text === thinkingText) return;
    thinkingText = text;
    var el = $("#asThinking");
    if (!el) return;                 // 眼下没有那一行：下次重画会按新文字带上
    var label = el.querySelector(".as-thinking-label");
    if (label) {
      label.textContent = text;
      scrollAssistant();
    }
  }

  /* 一轮问答是一次请求，中间没有推送：等的时候隔一会儿问一句进度，
     好让"正在思考"能换成"正在执行某个工具""正在生成回答" */
  function startAssistantPulse() {
    stopAssistantPulse();
    var tick = function () {
      PX.api("/assistant/progress?session=" + (S.assistantSessionId || 0))
        .then(function (d) {
          var text = phaseText(d.phase, d.label);
          if (text) setThinkingLabel(text);
        })
        .catch(function () { /* 问不到就一直是"正在思考" */ });
    };
    S.assistantPulse = setInterval(tick, 500);
    tick();
  }

  function stopAssistantPulse() {
    if (S.assistantPulse) {
      clearInterval(S.assistantPulse);
      S.assistantPulse = null;
    }
  }

  function hideThinking() {
    thinkingOn = false;
    var el = $("#asThinking");
    if (el) el.remove();
  }

  /* 输入框按内容长高：一行起，五行封顶，再多就在框里滚。
     手机上一开始就是两行。 */
  function assistantLines() {
    return window.innerWidth <= 760 ? 2 : 1;
  }

  function growAssistantBox() {
    var box = $("#assistantText");
    if (!box) return;
    var line = 24;                       // 与 CSS 的 line-height 对齐
    var pad = 8;                         // 上下内边距
    var maxLines = 5;
    box.style.height = "auto";
    var need = box.scrollHeight - pad;   // 去掉内边距后的内容高度
    var lines = Math.min(maxLines, Math.max(assistantLines(), Math.ceil(need / line) || 1));
    box.style.height = (lines * line + pad) + "px";
    box.style.overflowY = (need > maxLines * line) ? "auto" : "hidden";
  }

  function setAssistantRunning(running) {
    var btn = $("#assistantSend");
    if (!btn) return;
    btn.classList.toggle("is-running", !!running);
    btn.title = running ? "停止" : "发送";
    btn.setAttribute("aria-label", running ? "停止" : "发送");
  }

  function sendAssistant(text) {
    var question = String(text || "").trim();
    if (!question) return;
    var cfg = S.assistantCfg || {};
    if (!cfg.ready) {
      PX.toast("先在设置里填一把密钥", true);
      assistantConfigDialog();
      return;
    }
    S.assistantMsgs = S.assistantMsgs || [];
    S.assistantMsgs.push({ role: "user", content: question, ts: nowStamp() });
    drawAssistant();
    var box = $("#assistantText");
    box.value = "";
    growAssistantBox();
    // 先把这段对话存下来再问：会话 id 一早就有了，写动作与改名都挂在它上面
    ensureAssistantSession().then(function () {
      runAssistantTurn("/assistant/chat", { messages: S.assistantMsgs,
                                            session: S.assistantSessionId || 0 });
    });
  }

  /* 一轮问答：问一句、或者对某个写动作点批准/拒绝，走的是同一条收尾。
     服务端会带 pending 回来（助手要改数据），那时先不动记录，等人在输入框上方那一条上点头。 */
  function runAssistantTurn(path, body) {
    setAssistantRunning(true);
    hideApproval(true);            // 这一轮开始时，本会话原来挂着的那个作废
    showThinking();
    startAssistantPulse();
    var controller = window.AbortController ? new AbortController() : null;
    S.assistantAbort = controller;
    S.assistantStream = { text: "", stick: nearBottom(), frame: null };
    var out = null;
    var payload = path === "/assistant/chat"
      ? { messages: body.messages, session: body.session || 0 }
      : { token: body.token, allow: !!body.allow };
    fetch(PX.PREFIX + "/api/panel/assistant/stream", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json",
                 "X-Requested-With": "CAEventManager" },
      body: JSON.stringify(payload),
      signal: controller ? controller.signal : undefined
    }).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return readStream(res, function (ev) {
        if (ev.type === "delta") {
          if (!S.assistantStream) return;
          S.assistantStream.text += ev.text || "";
          hideThinking();          // 有字出来了：状态行让位给正在写的这段
          scheduleStreamView();
        } else if (ev.type === "reset") {
          if (S.assistantStream) S.assistantStream.text = "";
          scheduleStreamView();
        } else if (ev.type === "phase") {
          setThinkingLabel(phaseText(ev.phase, ev.label));
        } else if (ev.type === "done") {
          out = ev;
        } else if (ev.type === "error") {
          throw new Error(ev.error || "出错了");
        }
      });
    }).catch(function (e) {
      var abort = e && (e.name === "AbortError" || /abort/i.test(String(e.message)));
      var partial = (S.assistantStream && S.assistantStream.text) || "";
      if (abort) {
        S.assistantMsgs.push({ role: "assistant",
                               content: partial ? partial + "\n\n（已停止）" : "（已停止）",
                               steps: [], ts: nowStamp() });
      } else {
        S.assistantMsgs.push({ role: "assistant", content: "出错了：" + e.message,
                               steps: [], ts: nowStamp() });
      }
    }).then(function () {
      S.assistantAbort = null;
      stopAssistantPulse();
      hideThinking();
      setAssistantRunning(false);
      dropStreamView();
      var steps = (out && out.steps) || [];
      if (out && out.pending) {
        showApproval(out.pending);
        if (out.reply) {
          S.assistantMsgs.push({ role: "assistant", content: out.reply,
                                 steps: steps, ts: nowStamp() });
        }
      } else if (out) {
        S.assistantMsgs.push({ role: "assistant", content: out.reply || "",
                               steps: steps, ts: nowStamp() });
      }
      drawAssistant();
      // 助手刚刚给这段对话改了名：把这一条上的名字换成新的
      var titled = steps.some(function (s) { return s.tool === "session_title" && s.ok; });
      if (titled) refreshSessionName();
      saveAssistantSession();
      loadContext();
    });
  }

  /* ---- 逐字输出：SSE 的分块读、增量渲染、跟着内容的滚动 ---- */
  function readStream(res, onEvent) {
    function handle(block) {
      (block || "").split("\n").forEach(function (line) {
        if (line.indexOf("data:") !== 0) return;
        var data = line.slice(5).trim();
        if (!data || data === "[DONE]") return;
        try { onEvent(JSON.parse(data)); } catch (e) { /* 半个包就丢掉 */ }
      });
    }
    if (!res.body || !res.body.getReader) {
      return res.text().then(function (all) { handle(all); });
    }
    var reader = res.body.getReader();
    var decoder = window.TextDecoder ? new TextDecoder() : null;
    var buffer = "";
    function pump() {
      return reader.read().then(function (chunk) {
        if (chunk.done) {
          if (buffer.trim()) handle(buffer);
          return;
        }
        buffer += decoder ? decoder.decode(chunk.value, { stream: true })
                          : "";
        var cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          handle(buffer.slice(0, cut));
          buffer = buffer.slice(cut + 2);
        }
        return pump();
      });
    }
    return pump();
  }

  function nearBottom() {
    var box = $("#assistantScroll");
    if (!box) return true;
    return box.scrollHeight - box.scrollTop - box.clientHeight <= 48;
  }

  /* 正在写的那一段：单独一个气泡，内容一变就重画（每帧最多一次） */
  function scheduleStreamView() {
    var state = S.assistantStream;
    if (!state || state.frame) return;
    state.frame = window.requestAnimationFrame(function () {
      state.frame = null;
      paintStreamView();
    });
  }

  function paintStreamView() {
    var state = S.assistantStream;
    var log = $("#assistantLog");
    if (!state || !log) return;
    var el = $("#asStream");
    if (!el) {
      el = document.createElement("div");
      el.className = "as-msg as-bot";
      el.id = "asStream";
      el.innerHTML = '<div class="as-body"><div class="as-text md"></div></div>';
      log.appendChild(el);
    }
    var box = el.querySelector(".as-text");
    box.innerHTML = PX.md(state.text, assistantMdOpts());
    attachCaret(box);
    followStream(el);
  }

  /* 光标要落在文字末尾：一直往下钻到最后那个元素里再挂，
     直接 append 在 markdown 后面会被块级元素顶到下一行 */
  function attachCaret(box) {
    var caret = document.createElement("span");
    caret.className = "as-caret";
    var node = box.lastElementChild;
    while (node && node.lastElementChild && node.tagName !== "PRE") {
      node = node.lastElementChild;
    }
    (node || box).appendChild(caret);
  }

  /* 滚动：贴着底部时跟着走；这一段比一屏还长就停在它的顶部，
     让人从头读，而不是一路被拽到底。用户自己往上翻过就不再跟。 */
  function followStream(el) {
    var state = S.assistantStream;
    var box = $("#assistantScroll");
    if (!state || !box || !state.stick) return;
    var boxRect = box.getBoundingClientRect();
    var elRect = el.getBoundingClientRect();
    var topInBox = elRect.top - boxRect.top + box.scrollTop;
    var room = box.clientHeight - 32;
    var want = elRect.height > room
      ? topInBox - 12
      : box.scrollHeight - box.clientHeight;
    want = Math.max(0, Math.min(want, box.scrollHeight - box.clientHeight));
    if (Math.abs(box.scrollTop - want) > 0.5) {
      S.streamScrollOwn = true;
      box.scrollTop = want;
    }
  }

  function dropStreamView() {
    var el = $("#asStream");
    if (el) el.remove();
    if (S.assistantStream && S.assistantStream.frame) {
      window.cancelAnimationFrame(S.assistantStream.frame);
    }
    S.assistantStream = null;
  }

  /* 会话标题被助手改过之后，从服务端取一次名字换上（它只动这一处，别整段重读） */
  function refreshSessionName() {
    if (!S.assistantSessionId) return Promise.resolve();
    return PX.api("/assistant/session?id=" + S.assistantSessionId).then(function (r) {
      var title = (r.session && r.session.title) || "";
      if (!title) return;
      S.assistantTitleManual = true;   // 名字是这一次改的，别再让自动起名盖回去
      setSessionName(title);
      refreshAssistantDrawer();
    }).catch(function () { /* 名字只是好看，取不到就算了 */ });
  }

  /* 输入框上方那一条：「助手要执行「写入名单」：把 张三（2024001）加进名单 #3」。
     右边 [详情] 展开这次调用的参数（JSON），再点一次收起 */
  function showApproval(pending) {
    S.assistantPending = pending || null;
    var bar = $("#asApproval");
    if (!bar) return;
    if (!pending) { bar.hidden = true; return; }
    $("#asApprovalText").textContent = "助手要执行「" + (pending.label || "改动") + "」：" +
      (pending.summary || "");
    showApprovalArgs(pending.args, false);
    bar.hidden = false;
    markSessionPending(true);
    $("#asApprovalYes").focus();
  }

  function showApprovalArgs(args, open) {
    var box = $("#asApprovalArgs");
    var more = $("#asApprovalMore");
    if (!box || !more) return;
    var text = "";
    if (args && typeof args === "object" && Object.keys(args).length) {
      try { text = JSON.stringify(args, null, 2); } catch (e) { text = ""; }
    }
    var shown = !!(open && text);
    box.textContent = text;
    box.hidden = !shown;
    more.hidden = !text;             // 没有参数就别摆一个按不动的键
    // 键面就写「详情 / 收起」：不做按下态，按下去的样式在这条上不好看也不好认
    more.textContent = shown ? "收起" : "详情";
    more.setAttribute("aria-expanded", shown ? "true" : "false");
  }

  function toggleApprovalArgs() {
    var box = $("#asApprovalArgs");
    if (!box || !box.textContent) return;
    showApprovalArgs(S.assistantPending ? S.assistantPending.args : null, !!box.hidden);
  }

  /* 收起那一条。`done` 为真表示这一次已经点了批准或拒绝（这份挂起没了）；
     换会话时只是把它收起来，那份挂起还在服务端等着，所以小红点要留着 */
  function hideApproval(done) {
    S.assistantPending = null;
    var bar = $("#asApproval");
    if (bar) bar.hidden = true;
    showApprovalArgs(null, false);   // 参数面板收回去，别带到下一次
    if (done) markSessionPending(false);
  }

  /* ---- 挂起的写动作：侧栏与会话卡片上的小红点 ----
     服务端是权威（换会话、刷新页面之后由它告诉我们哪几段还挂着），
     这里只是把它画出来；本机点下去的那一刻先自己记一笔，省一趟请求 */
  function markSessionPending(on) {
    S.asPending = S.asPending || {};
    var sid = S.assistantSessionId || 0;
    if (on) S.asPending[sid] = true;
    else delete S.asPending[sid];
    drawAssistantPending();
  }

  function adoptPendingServer(ids) {
    S.asPending = {};
    (ids || []).forEach(function (id) {
      S.asPending[Number(id) || 0] = true;
    });
    drawAssistantPending();
  }

  function hasSessionPending() {
    return !!(S.asPending && Object.keys(S.asPending).length);
  }

  function drawAssistantPending() {
    syncPendingDot();
    if (assistantDrawerOpen() && S.assistantList) drawAssistantDrawer({});
  }

  function syncPendingDot() {
    var dot = $("#assistantDot");
    if (dot) dot.hidden = !hasSessionPending();
  }

  function answerApproval(allow) {
    var pending = S.assistantPending;
    if (!pending) return;
    hideApproval(true);
    runAssistantTurn("/assistant/approve", { token: pending.token, allow: !!allow });
  }

  function stopAssistant() {
    if (S.assistantAbort) {
      try { S.assistantAbort.abort(); } catch (e) { /* 已经结束了 */ }
      S.assistantAbort = null;
    }
  }

  function saveAssistantConfig() {
    var ctxBox = $("#asCtx");
    var raw = ctxBox ? ctxBox.value.replace(/[^0-9]/g, "") : "";
    var ctx = raw ? parseInt(raw, 10) : 0;      // 空着就是"由模型决定"
    var body = { base: $("#asBase").value.trim(), model: $("#asModel").value.trim(),
                 context: ctx,
                 resume: !!($("#asResume") && $("#asResume").checked) };
    // 框里还是那串星号 = 没动过密钥，别把已有的覆盖成空
    if (!keyFieldMasked()) body.key = $("#asKey").value;
    PX.api("/assistant/config", { method: "POST", body: body })
      .then(function (d) {
        S.assistantCfg = d.config || S.assistantCfg;
        closeOverlay();
        drawAssistant();
        loadContext();                 // 上下文上限改了，环的分母跟着变
        // 服务端没回 config 时别去读它的字段：这里以前会抛一句 "reading 'ready'"
        var ready = !!(d.config && d.config.ready);
        PX.toast(ready ? "已保存" : "已保存，还差密钥", !ready);
      }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  // 从服务端问一遍有哪些模型可选（DeepSeek 的 GET /models），列在弹窗里自己画
  function fetchAssistantModels() {
    var btn = $("#asModels");
    btn.disabled = true;
    btn.textContent = "获取中…";
    var body = { base: $("#asBase").value.trim() };
    if (!keyFieldMasked() && $("#asKey").value) body.key = $("#asKey").value;
    PX.api("/assistant/models", { method: "POST", body: body })
      .then(function (d) {
        var models = d.models || [];
        if (!models.length) { PX.toast("没有拿到模型", true); return; }
        var box = $("#asModelList");
        box.innerHTML = models.map(function (m) {
          return '<button type="button" data-model="' + PX.esc(m.id) + '">' +
            PX.esc(m.id) + (m.name && m.name !== m.id
              ? '<span>' + PX.esc(m.name) + "</span>" : "") + "</button>";
        }).join("");
        box.hidden = false;
        PX.toast("已拉取 " + models.length + " 个模型");
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); })
      .then(function () {
        btn.disabled = false;
        btn.textContent = "获取模型";
      });
  }

  function pickAssistantModel(id) {
    var box = $("#asModel");
    if (box && id) box.value = id;
    var list = $("#asModelList");
    if (list) list.hidden = true;
  }

  /* ------------------------------------------------------------------- host */
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
      .then(function (d) {
        S.users = (d && d.users) || null;
        S.features = (d && d.features) || null;
        S.hostMeta = { safeMode: !!(d && d.safeMode), supervised: d && d.supervised };
        applyHost(d && d.host ? d.host : null);
        drawFeatures();
      })
      .catch(function () { applyHost(null); });
  }

  /* 服务器特性：额外依赖装上了才有的能力。绿点可用，红点不可用，
     第二列说清是哪一个版本，或者缺了什么、缺了会怎样。 */
  function drawFeatures() {
    var body = $("#featureTable tbody");
    if (!body) return;
    var rows = S.features;
    if (!rows) {
      body.innerHTML = rowsOrEmpty(2, "features", "没有可选特性");
      return;
    }
    var sig = JSON.stringify(rows);
    if (sig === S.featureSig) return;          // 每五秒问一次，没变就不动 DOM
    S.featureSig = sig;
    markLoaded("features");
    body.innerHTML = rows.length ? rows.map(function (f) {
      return "<tr>" +
        '<td><span class="dot ' + (f.ok ? "ok" : "bad") + '" aria-hidden="true"></span>' +
        PX.esc(f.name || "") + "</td>" +
        '<td class="note">' + (f.ok ? "" : "不可用 · ") +
        PX.esc(f.detail || "") + "</td></tr>";
    }).join("") : rowsOrEmpty(2, "features", "没有可选特性");
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
    // 账号清单是超管才看得到的东西：普通管理员打开面板时不去问它
    if (!isSuper()) {
      S.accounts = [];
      S.accountsTotal = 0;
      renderIdentity();
      applyRoleVisibility();
      syncPendingBadge();
      return Promise.resolve();
    }
    markLoading("accounts");
    return PX.api("/accounts?limit=50").then(function (d) {
      S.me = d.me || S.me;
      S.accounts = d.accounts || [];
      S.accountsTotal = d.total || S.accounts.length;
      markLoaded("accounts");
      renderIdentity();
      applyRoleVisibility();
      syncPendingBadge();
    });
  }

  // 总览上的两个"我的"数：名单与报名各问一次总量，不拉列表
  function loadOverviewCounts() {
    return Promise.all([
      PX.api("/rosters?scope=joined&limit=1").then(function (d) { return d.total || 0; })
        .catch(function () { return null; }),
      PX.api("/signups?scope=joined&limit=1").then(function (d) { return d.total || 0; })
        .catch(function () { return null; })
    ]).then(function (counts) {
      S.overviewCounts = { rosters: counts[0], signups: counts[1] };
    });
  }

  function loadInvites() {
    return PX.api("/invites").then(function (d) {
      S.invites = d.invites || [];
      // the badge lives in the sidebar and is painted with the identity, which
      // happens before this list is known
      $("#inviteBadge").hidden = S.invites.length === 0;
    }).catch(function () { S.invites = []; });
  }

  function loadEvents() {
    markLoading("events");
    return PX.api("/events").then(function (d) {
      S.events = d.events || [];
      markLoaded("events");
    }).catch(function () { S.events = []; markLoaded("events"); });
  }

  function loadAudit(q) {
    if (!canAudit()) return Promise.resolve();
    S.auditFilter = (q === undefined) ? (S.auditFilter || "") : q;
    var query = "?limit=60" +
      (S.auditFilter ? "&q=" + encodeURIComponent(S.auditFilter) : "");
    markLoading("audit");
    return PX.api("/audit" + query).then(function (d) {
      S.audit = d.audit || [];
      S.auditTotal = d.total || 0;
      markLoaded("audit");
    }).catch(function () { S.audit = []; S.auditTotal = 0; markLoaded("audit"); });
  }

  function loadMoreAudit() {
    if (!canAudit() || S.auditLoading) return Promise.resolve();
    if (S.audit.length >= (S.auditTotal || 0)) return Promise.resolve();
    S.auditLoading = true;
    var query = "?limit=60&offset=" + S.audit.length +
      (S.auditFilter ? "&q=" + encodeURIComponent(S.auditFilter) : "");
    return PX.api("/audit" + query).then(function (d) {
      S.audit = S.audit.concat(d.audit || []);
      S.auditTotal = d.total || 0;
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); })
      .then(function () {
        S.auditLoading = false;
        drawAudit();
      });
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
      return Promise.all([loadEvents(), loadInvites(), loadAudit(),
                          loadOverviewCounts()]);
    }).then(function () {
      applyRoleVisibility();
      draw();
    });
  }

  /* --------------------------------------------------------------- actions */
  function decide(id, action) {
    // stopping someone's access is the decision that needs the password again
    var call = action === "disable" ? apiSudo : PX.api;
    call("/account/decide", { method: "POST", body: { id: id, action: action } },
         "停用账号")
      .then(function () {
        PX.toast("已" + ({
          approve: "通过", reject: "拒绝", disable: "停用", enable: "启用"
        })[action]);
        return reloadAll().then(afterMutation);
      })
      .catch(fail);
  }

  function changeRole(id, role) {
    apiSudo("/account/role", { method: "POST", body: { id: id, role: role } }, "修改角色")
      .then(function () { PX.toast("角色已更新"); return reloadAll().then(afterMutation); })
      .catch(fail);
  }

  // 对话存档配额：按账号给，0 表示用默认值
  function sizeText(bytes) {
    var kb = Math.round((bytes || 0) / 1024);
    return kb >= 1024 ? (Math.round(kb / 10.24) / 100) + " MB" : kb + " KB";
  }

  function quotaText(a) {
    var cap = a.chatQuota > 0 ? a.chatQuota : (S.quotaDefault || 0);
    return (a.chatQuota > 0 ? "" : "默认 ") + sizeText(cap) +
      " · 已用 " + sizeText(a.chatUsed);
  }

  /* 彻底删除账号：不可逆。先问一句，再走提权（没提权时密码就是那道确认） */
  function deleteAccount(id) {
    var a = S.accounts.filter(function (x) { return x.id === id; })[0];
    if (!a) return;
    openOverlay("confirm", "彻底删除 " + displayName(a) + "？",
      '<p class="page-sub">删除后无法恢复。确认继续？</p>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn danger" type="button" id="acctDropGo">删除</button></div>');
    $("#acctDropGo").addEventListener("click", function () {
      closeAllOverlays();
      apiSudo("/account/delete", { method: "POST", body: { id: a.id } }, "彻底删除账号")
        .then(function (d) {
          PX.toast(d && d.eventsOrphaned
            ? "账号已删除，" + d.eventsOrphaned + " 场已归档的活动等取消归档时再指定负责人"
            : "账号已删除");
          return reloadAll();
        })
        .catch(fail);
    });
  }

  /* 新建账号：管理员只给登录名与显示名，密码由系统生成（临时密码） */
  function newAccountDialog() {
    var roleField = isOwner()
      ? '<div class="field"><label for="naRole">角色</label>' +
        '<select id="naRole"><option value="admin">管理员</option>' +
        '<option value="superadmin">超级管理员</option></select></div>'
      : "";
    openOverlay("account-new", "新建账号",
      '<div class="field"><label for="naUser">登录名</label>' +
      '<input id="naUser" type="text" autocomplete="off" autocapitalize="off" ' +
      'spellcheck="false" placeholder="3~24 位，字母数字和 . _ -"></div>' +
      '<div class="field"><label for="naName">显示名</label>' +
      '<input id="naName" type="text" autocomplete="off" placeholder="例如 李老师"></div>' +
      roleField +
      '<p class="hint">密码由平台生成，管理员不设定内容；该密码仅显示一次，' +
      "账号首次登录时须自行设置新密码</p>" +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn primary" id="naGo" type="button">创建</button></div>');
    $("#naUser").focus();
    $("#naGo").addEventListener("click", function () {
      var body = {
        username: ($("#naUser").value || "").trim(),
        name: ($("#naName").value || "").trim()
      };
      if ($("#naRole")) body.role = $("#naRole").value;
      PX.api("/account/create", { method: "POST", body: body })
        .then(function (d) {
          showCreatedAccount(body.username, d);
        })
        .catch(fail);
    });
  }

  /* 开号的结果：临时密码只出现这一次，交代清楚对方第一次登录要做什么 */
  function showCreatedAccount(username, d) {
    openOverlay("account-new-done", "账号已创建",
      '<p class="page-sub">登录名与以下临时密码交由 ' + PX.esc(username) +
      " 使用。该账号首次登录时须自行设置新密码，此密码随即失效</p>" +
      '<div class="row" style="gap:8px;margin-top:12px;align-items:center">' +
      '<input id="pwdOut" type="text" readonly value="' + PX.esc(d.password || "") + '" ' +
      'style="flex:1;font-family:var(--mono)">' +
      '<button class="btn" id="pwdCopy" type="button">复制</button></div>' +
      '<p class="hint" style="margin-top:10px">只显示这一次，关掉弹窗之后就看不到了；' +
      "丢了就在账号弹窗里点 [重置密码] 再生成一串。</p>" +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn primary" data-close-overlay="1" type="button">我记下了</button></div>');
    $("#pwdOut").select();
    $("#pwdCopy").addEventListener("click", function () {
      copyInto($("#pwdOut"), $("#pwdCopy"));
    });
    reloadAll().catch(function () {});
  }

  function copyInto(box, button) {
    box.select();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(box.value).then(function () {
        if (button) button.textContent = "已复制";
      }).catch(function () { PX.toast("复制不了，手动选中吧", true); });
    } else {
      PX.toast("复制不了，手动选中吧", true);
    }
  }

  /* 重置别人的密码：留空由服务端生成一串临时口令，只显示这一次 */
  function passwordDialog(id) {
    var a = S.accounts.filter(function (x) { return x.id === id; })[0];
    if (!a) return;
    openOverlay("as-pwd", "重置 " + displayName(a) + " 的密码",
      '<p class="page-sub">密码由平台生成，管理员不设定内容。' +
      "重置后该账号的登录会话与 API 令牌同时失效，对方需用新密码登录并另行设置自己的密码</p>" +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn primary" id="pwdGo" type="button">重置</button></div>');
    $("#pwdGo").addEventListener("click", function () {
      apiSudo("/account/password", { method: "POST", body: { id: a.id } },
              "重置账号密码")
        .then(function (d) {
          showNewPassword(a, d);
        })
        .catch(fail);
    });
  }

  /* 新密码只在这里出现一次：一个只读框 + 一颗复制键，关掉就没了 */
  function showNewPassword(a, d) {
    openOverlay("as-pwd-done", "密码已重置",
      '<p class="page-sub">以下临时密码交由 ' + PX.esc(displayName(a)) +
      " 使用。该账号首次登录时须自行设置新密码</p>" +
      '<div class="row" style="gap:8px;margin-top:12px;align-items:center">' +
      '<input id="pwdOut" type="text" readonly value="' + PX.esc(d.password || "") + '" ' +
      'style="flex:1;font-family:var(--mono)">' +
      '<button class="btn" id="pwdCopy" type="button">复制</button></div>' +
      '<p class="hint" style="margin-top:10px">' +
      "已失效的登录会话 " + (d.sessions || 0) + " 个、API 令牌 " + (d.tokens || 0) +
      " 张。临时密码仅在此处显示一次</p>" +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn primary" data-close-overlay="1" type="button">我已记录</button></div>');
    $("#pwdOut").select();
    $("#pwdCopy").addEventListener("click", function () {
      copyInto($("#pwdOut"), $("#pwdCopy"));
    });
    loadAccountsPage(true).then(function () {
      renderAccountsTable();
      afterMutation();
    }).catch(function () {});
  }

  function quotaDialog(id) {
    var a = S.accounts.filter(function (x) { return x.id === id; })[0];
    if (!a) return;
    var def = S.quotaDefault || 512 * 1024;
    var cap = a.chatQuota > 0 ? a.chatQuota : def;
    var maxKb = Math.max(1, Math.floor((S.quotaMax || def) / 1024));
    openOverlay("as-quota", "调整存档配额",
      '<div class="field"><label for="qKb">上限（KB）</label>' +
      '<input id="qKb" type="number" min="0" max="' + maxKb + '" value="' +
      Math.round(cap / 1024) + '"></div>' +
      '<p class="hint">填 0 表示用默认值（' + Math.round(def / 1024) + ' KB）；' +
      '当前已用 ' + sizeText(a.chatUsed) + "</p>" +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn primary" id="qGo" type="button">保存</button></div>');
    $("#qKb").focus();
    $("#qKb").select();
    $("#qGo").addEventListener("click", function () {
      var kb = Math.max(0, Math.round(Number($("#qKb").value) || 0));
      apiSudo("/account/quota", { method: "POST", body: { id: a.id, quota: kb * 1024 } },
              "调整存档配额")
        .then(function () {
          PX.toast("配额已更新");
          closeOverlay();
          return reloadAll().then(function () { openAccountOverlay(a.id); });
        })
        .catch(fail);
    });
  }

  // keep an open account dialog in sync with the data behind it
  function afterMutation() {
    if ($("#overlay").hidden || overlayKind !== "account") return;
    var id = Number($("#overlayBody").getAttribute("data-acct-body"));
    if (id) openAccountOverlay(id);
    else closeOverlay();
  }

  function setTheme(pref) {
    try { localStorage.setItem("xl.panel.theme", pref); } catch (e) { /* private mode */ }
    var dark = pref === "dark" || (pref === "auto" && systemDark());
    var root = document.documentElement;
    var next = dark ? "dark" : "light";
    var changed = root.getAttribute("data-theme") !== next;

    var apply = function () {
      root.setAttribute("data-theme", next);
      root.setAttribute("data-theme-pref", pref);
      // 选中态要和主题在同一次 DOM 变更里翻过来：startViewTransition 的回调是异步的，
      // 在外面读完再画就会画到旧值上，看起来要再点一次才生效
      drawAppearance();
    };

    // Cross-fade two snapshots rather than animating each property: measured
    // frame by frame, transitioning colour made headings jump back to a mid grey
    // and run twice, which reads as a flicker.
    //
    // 转场会把页面换成两张快照，快照里是"点下去之前"和"点下去之后"两张静止的图。
    // 只把状态翻过来是不够的：指示块的位移会发生在快照底下，屏幕上只剩一次跳变。
    // 所以拍新快照之前先关掉块自己的过渡，让它直接落在终点，位移由转场那一段负责搬。
    var seg = $("#themeSeg");
    if (changed && !reducedMotion() && document.startViewTransition) {
      var vt;
      if (seg) seg.classList.add("seg-snap");
      try {
        vt = document.startViewTransition(apply);
      } catch (err) {
        // fall through to the direct path
        if (seg) seg.classList.remove("seg-snap");
        apply();
        return;
      }
      if (vt.finished && vt.finished.then) {
        var snapDone = function () { if (seg) seg.classList.remove("seg-snap"); };
        vt.finished.then(snapDone, snapDone);
      } else if (seg) {
        seg.classList.remove("seg-snap");
      }
      return;
    }
    apply();
  }

  function reducedMotion() {
    return !!(window.matchMedia &&
              window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  /* 顶栏按钮按一个三格的环走：跟随系统 → 与系统相反的值 → 系统的值 → 跟随系统。
     这么排是为了第一下点击一定换到一个不一样的外观；Shift 反向走同一个环。 */
  function cycleTheme(back) {
    var dark = systemDark();
    var ring = ["auto", dark ? "light" : "dark", dark ? "dark" : "light"];
    var pref = document.documentElement.getAttribute("data-theme-pref") || "auto";
    var at = ring.indexOf(pref);
    if (at < 0) at = 0;
    var step = back ? -1 : 1;
    setTheme(ring[(at + step + ring.length) % ring.length]);
  }

  /* -------------------------------------------------------------- listeners */
  // Everything is read from the closest ancestor: a tap usually lands on the
  // icon or the label inside the control, never on the control itself.
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;

    // 页内跳转的链接不上 href：浏览器会在左下角弹一串地址（鼠标一过就闪），
    // 那是给外链用的提示，页内切页不需要。Ctrl/⌘ 点仍按"新开一个"处理。
    var navLink = t.closest("[data-href]");
    if (navLink) {
      var where = navLink.getAttribute("data-href");
      e.preventDefault();
      if (e.metaKey || e.ctrlKey || e.shiftKey) {
        window.open(location.pathname + where, "_blank", "noopener");
        return;
      }
      if (location.hash !== where) location.hash = where;
      return;
    }

    var dec = t.closest("[data-decide]");
    if (dec) return decide(Number(dec.getAttribute("data-id")),
                           dec.getAttribute("data-decide"));

    var role = t.closest("[data-role]");
    if (role) return changeRole(Number(role.getAttribute("data-id")),
                                role.getAttribute("data-role"));

    var acct = t.closest("[data-acct]");
    if (acct) return openAccountOverlay(Number(acct.getAttribute("data-acct")));

    var quota = t.closest("[data-quota]");
    if (quota) return quotaDialog(Number(quota.getAttribute("data-quota")));

    var pwd = t.closest("[data-pwd]");
    if (pwd) return passwordDialog(Number(pwd.getAttribute("data-pwd")));

    var dropAcct = t.closest("[data-acct-del]");
    if (dropAcct) return deleteAccount(Number(dropAcct.getAttribute("data-acct-del")));

    var toggle = t.closest("[data-toggle]");
    if (toggle) return onGroupHead(toggle.getAttribute("data-toggle"));

    var tplTile = t.closest("[data-template]");
    if (tplTile) return go("new-event/" + tplTile.getAttribute("data-template"));

    var uninvite = t.closest("[data-uninvite]");
    if (uninvite) return removeInvite(Number(uninvite.getAttribute("data-uninvite")));

    var orgUnpick = t.closest("[data-org-unpick]");
    if (orgUnpick) return unpickOrgPerson(orgUnpick.getAttribute("data-org-unpick"));

    var orgOpen = t.closest("[data-org-open]");
    if (orgOpen) return go("org/" + encodeURIComponent(orgOpen.getAttribute("data-org-open")));

    var orgKick = t.closest("[data-org-kick]");
    if (orgKick) {
      return kickOrgMember(S.route === "org" ? "platform" : "self",
                           Number(orgKick.getAttribute("data-org-kick")));
    }

    var orgSelf = t.closest("[data-org-self]");
    if (orgSelf) {
      return go("my-org/" + encodeURIComponent(orgSelf.getAttribute("data-org-self")));
    }

    var orgArchive = t.closest("[data-org-archive]");
    if (orgArchive) return archiveOrg(orgArchive.getAttribute("data-org-archive"));

    var orgRestore = t.closest("[data-org-restore]");
    if (orgRestore) return restoreOrg(orgRestore.getAttribute("data-org-restore"));

    var orgDrop = t.closest("[data-org-drop]");
    if (orgDrop) return dropOrg(orgDrop.getAttribute("data-org-drop"));

    var banBtn = t.closest("[data-ban]");
    if (banBtn) return confirmBan(Number(banBtn.getAttribute("data-ban")));

    var banYes = t.closest("[data-ban-confirm]");
    if (banYes) return doBan(banYes.getAttribute("data-ban-confirm"));

    var rotateBtn = t.closest("[data-rotate]");
    if (rotateBtn) return rotateEvent(rotateBtn.getAttribute("data-rotate"));

    var archiveBtn = t.closest("[data-archive]");
    if (archiveBtn) return archiveEvent(archiveBtn.getAttribute("data-archive"));
    var detailBtn = t.closest("[data-event-detail]");
    if (detailBtn) return openEventDetail(detailBtn.getAttribute("data-event-detail"));

    var adminRoster = t.closest("[data-admin-roster]");
    if (adminRoster) {
      return openAdminListDetail("roster", adminRoster.getAttribute("data-admin-roster"));
    }
    var adminSignup = t.closest("[data-admin-signup]");
    if (adminSignup) {
      return openAdminListDetail("signup", adminSignup.getAttribute("data-admin-signup"));
    }
    var forceBtn = t.closest("[data-force-archive]");
    if (forceBtn) return forceArchive(forceBtn.getAttribute("data-force-archive"));
    var reclaimBtn = t.closest("[data-reclaim]");
    if (reclaimBtn) return reclaimEvent(reclaimBtn.getAttribute("data-reclaim"));
    var dlBtn = t.closest("[data-arch-download]");
    if (dlBtn) return archivedDownload(dlBtn.getAttribute("data-arch-download"));
    var restoreBtn = t.closest("[data-arch-restore]");
    if (restoreBtn) return archivedRestore(restoreBtn.getAttribute("data-arch-restore"));
    var delBtn = t.closest("[data-arch-delete]");
    if (delBtn) return archivedDelete(delBtn.getAttribute("data-arch-delete"));

    var requestBtn = t.closest("[data-request]");
    if (requestBtn) {
      return decideInvite(requestBtn.getAttribute("data-seq"),
                          requestBtn.getAttribute("data-request"));
    }

    var modToggle = t.closest("[data-module-toggle]");
    if (modToggle) {
      return toggleModule(modToggle.getAttribute("data-module-toggle"),
                          modToggle.getAttribute("data-on") === "1");
    }

    var modRemove = t.closest("[data-module-remove]");
    if (modRemove) {
      var rid = modRemove.getAttribute("data-module-remove");
      var row = (S.modules || []).filter(function (x) { return x.id === rid; })[0] || {};
      return removeModule(rid, row.name);
    }

    var offTip = t.closest("[data-tip]");
    if (offTip) {
      // 悬停看过的那一次不算数：点一下先"钉住"（触屏没有悬停，全靠这一下），
      // 再点一下才收
      if (cardTipEl && cardTipAnchor === offTip && cardTipPinned) return closeCardTip();
      showCardTip(offTip, offTip.getAttribute("data-tip") || "");
      cardTipPinned = true;
      return;
    }

    var wantPreset = t.closest("[data-save-preset]");
    if (wantPreset) {
      return saveAsPreset(wantPreset.getAttribute("data-save-preset"));
    }

    var usePreset = t.closest("[data-preset-use]");
    if (usePreset) {
      return go("new-event/" + usePreset.getAttribute("data-preset-use"));
    }

    var favPreset = t.closest("[data-preset-fav]");
    if (favPreset) {
      return presetFavorite(favPreset.getAttribute("data-preset-fav"),
                            favPreset.getAttribute("data-on") === "1");
    }

    var editPreset = t.closest("[data-preset-edit]");
    if (editPreset) {
      var found = S.templates.filter(function (x) {
        return x.id === editPreset.getAttribute("data-preset-edit");
      })[0];
      return presetDialog(found || null);
    }

    var dropPreset = t.closest("[data-preset-drop]");
    if (dropPreset) {
      return presetDelete(dropPreset.getAttribute("data-preset-drop"));
    }

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

    // 回答里代码块右上角那颗：抄的是同一段 <pre><code> 的文字，不用把代码再塞进属性里
    var codeBtn = t.closest("[data-copy-code]");
    if (codeBtn) {
      e.stopPropagation();
      var block = codeBtn.closest(".md-code");
      var pre = block ? block.querySelector("pre code") || block.querySelector("pre") : null;
      return copyText(pre ? pre.textContent : "", codeBtn);
    }

    // 每一段对话下面那颗：抄原文（助手的回答抄 Markdown 源，粘出去还能用）
    var msgBtn = t.closest("[data-copy-msg]");
    if (msgBtn) {
      e.stopPropagation();
      var idx = Number(msgBtn.getAttribute("data-copy-msg"));
      var msg = (S.assistantMsgs || [])[idx];
      return copyText(msg ? String(msg.content || "") : "", msgBtn);
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

    var help = t.closest("[data-doc]");
    if (help) {
      window.open(DOCS_HREF + help.getAttribute("data-doc"), "_blank",
                  "noopener");
      return;
    }

    var fold = t.closest("[data-fold-toggle]");
    if (fold) {
      var card = fold.closest(".card");
      if (card) {
        var open = !card.classList.contains("fold-open");
        card.classList.toggle("fold-open", open);
        fold.setAttribute("aria-expanded", open ? "true" : "false");
        if (fold.getAttribute("data-fold-toggle") === "1" ||
            fold.hasAttribute("data-fold-label")) {
          var label = fold.getAttribute("data-fold-label") || fold.textContent;
          fold.setAttribute("data-fold-label", label);
          fold.textContent = open ? "收起" : label;
        }
      }
      return;
    }

    var goEl = t.closest("[data-go]");
    if (goEl) return go(goEl.getAttribute("data-go"));

    var theme = t.closest("#themeSeg [data-theme-pref]");
    if (theme) return setTheme(theme.getAttribute("data-theme-pref"));

    if (t.closest("#themeBtn")) return cycleTheme(!!e.shiftKey);
  });

  $("#menuBtn").addEventListener("click", function () {
    if ($("#shell").classList.contains("nav-open")) closePane();
    else openPane();
  });
  $("#scrim").addEventListener("click", function () {
    closeAssistantDrawer();
    closePane();
  });
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
    // 卡片上的小提示也是同一个规矩：点别处就收，别让它停在屏幕上
    if (cardTipEl && !(e.target.closest &&
                       (e.target.closest("[data-tip]") ||
                        e.target.closest(".card-tip")))) {
      closeCardTip();
    }
    if (SUDO.pop && e.target.closest &&
        !e.target.closest(".sudo-pop") && !e.target.closest("#sudoChip")) {
      closeSudoPop();
    }
  });

  /* 悬停看原因；触屏没有悬停，所以在点击里再给一次机会 */
  document.addEventListener("mouseover", function (e) {
    var badge = e.target.closest && e.target.closest("[data-tip]");
    if (!badge) return;
    if (cardTipEl && cardTipAnchor === badge) return;
    showCardTip(badge, badge.getAttribute("data-tip") || "");
  });
  document.addEventListener("mouseout", function (e) {
    var badge = e.target.closest && e.target.closest("[data-tip]");
    if (!badge || cardTipPinned) return;
    if (cardTipAnchor === badge) closeCardTip();
  });

  function logout(returnRoute) {
    var back = returnRoute ? "?s_return=" + encodeURIComponent(returnRoute) : "";
    var leave = function () { location.href = PX.PREFIX + "/login" + back; };
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
  $("#missingBack").addEventListener("click", function () { go(FALLBACK_ROUTE); });

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
    var timer = null;
    var search = function (value) {
      S.userFilter = value || "";
      syncClearButton();
      clearTimeout(timer);
      timer = setTimeout(function () { loadAccountsPage(true); }, 220);
    };
    box.addEventListener("input", function (e) { search(e.target.value); });
    clear.addEventListener("click", function () {
      box.value = "";
      search("");
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

  $("#safeModeBtn").addEventListener("click", toggleSafeMode);
  $("#serviceRestartBtn").addEventListener("click", restartService);
  $("#newAccountBtn").addEventListener("click", newAccountDialog);
  $("#orgNewBtn").addEventListener("click", function () {
    resetOrgForm();
    location.hash = "#/org-new";
  });
  $("#orgNewBack").addEventListener("click", function () { location.hash = "#/orgs"; });
  $("#orgCreate").addEventListener("click", createOrg);
  $("#orgName").addEventListener("input", checkOrgName);
  $("#orgName").addEventListener("blur", checkOrgName);
  $("#orgId").addEventListener("input", sanitizeOrgId);
  $("#orgId").addEventListener("blur", checkOrgId);
  $("#orgHeadPick").addEventListener("click", function () {
    pickOrgPeople("heads", "选择负责人", "multi");
  });
  $("#orgAdminPick").addEventListener("click", function () {
    pickOrgPeople("admins", "选择管理", "multi");
  });
  $("#orgMemberPick").addEventListener("click", function () {
    pickOrgPeople("members", "选择成员", "multi");
  });
  bindLiveSearch("#orgSearch", "#orgSearchClear", function (value) {
    S.orgFilter = value || "";
    drawOrgs();
  });
  $("#orgBack").addEventListener("click", function () { location.hash = "#/orgs"; });
  $("#orgSaveBtn").addEventListener("click", function () {
    saveOrg("platform", "#orgEditName", "#orgEditNoteInput", "#orgEditNameErr");
  });
  $("#orgManageSave").addEventListener("click", function () {
    saveOrg("self", "#orgManageName", "#orgManageNote", "#orgManageNameErr");
  });
  ["#orgEditName", "#orgEditNoteInput", "#orgManageName", "#orgManageNote"]
    .forEach(function (sel) {
      var el = $(sel);
      if (el) el.addEventListener("input", function () { S.orgEditDirty = true; });
    });
  $("#orgMemberAdd").addEventListener("click", function () { addOrgMembers("platform"); });
  $("#orgManageAdd").addEventListener("click", function () { addOrgMembers("self"); });
  $("#myOrgBack").addEventListener("click", function () { location.hash = "#/my-orgs"; });
  $("#orgManageBack").addEventListener("click", function () {
    if (S.orgDetail) location.hash = "#/my-org/" + encodeURIComponent(S.orgDetail.org.id);
    else location.hash = "#/my-orgs";
  });
  $("#myOrgManageBtn").addEventListener("click", function () {
    if (S.orgDetail) {
      location.hash = "#/my-org-manage/" + encodeURIComponent(S.orgDetail.org.id);
    }
  });
  $("#orgSelfHintGo").addEventListener("click", function () {
    if (S.orgDetail) {
      location.hash = "#/my-org-manage/" + encodeURIComponent(S.orgDetail.org.id);
    }
  });
  ["orgCover", "manCover"].forEach(function (prefix) {
    var scope = prefix === "orgCover" ? "platform" : "self";
    var link = $("#" + prefix + "Link");
    var pick = $("#" + prefix + "Pick");
    var file = $("#" + prefix + "File");
    var clear = $("#" + prefix + "Clear");
    if (link) link.addEventListener("click", function () { askCoverLink(scope); });
    if (pick && file) {
      pick.addEventListener("click", function () { file.value = ""; file.click(); });
      file.addEventListener("change", function () {
        if (file.files && file.files[0]) uploadCover(scope, file.files[0]);
      });
    }
    if (clear) clear.addEventListener("click", function () { clearCover(scope); });
  });
  $("#missingPrev").addEventListener("click", function () {
    if (canGoBack()) window.history.back();
    else go(FALLBACK_ROUTE);
  });
  $("#deniedBack").addEventListener("click", function () {
    if (canGoBack()) window.history.back();
    else go(FALLBACK_ROUTE);
  });
  $("#orgSelfHintClose").addEventListener("click", function () {
    $("#orgSelfHint").hidden = true;
  });
  $("#orgArchiveBtn").addEventListener("click", function () {
    if (S.orgDetail) archiveOrg(S.orgDetail.org.id);
  });
  $("#orgRestoreBtn").addEventListener("click", function () {
    if (S.orgDetail) restoreOrg(S.orgDetail.org.id);
  });
  $("#orgDropBtn").addEventListener("click", function () {
    if (S.orgDetail) dropOrg(S.orgDetail.org.id);
  });
  ["#orgMemberTable", "#orgManageTable"].forEach(function (id) {
    $(id + " tbody").addEventListener("change", function (e) {
      var sel = e.target.closest ? e.target.closest("[data-org-role-for]") : null;
      if (!sel) return;
      setOrgMemberRole(id === "#orgMemberTable" ? "platform" : "self",
                       Number(sel.getAttribute("data-org-role-for")), sel.value);
    });
  });
  $("#backupSave").addEventListener("click", saveBackup);
  $("#backupRun").addEventListener("click", runBackup);
  $("#bkDavTest").addEventListener("click", testBackupDav);

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
  watchNavFade();
  watchScrollbars();
  bindLiveSearch("#auditSearch", "#auditSearchClear", function (value) {
    loadAudit(value).then(drawAudit);
  });
  $("#usersMore").addEventListener("click", function () { loadAccountsPage(false); });
  $("#auditMore").addEventListener("click", loadMoreAudit);

  $("#pfSave").addEventListener("click", saveProfile);
  // 运维助手：输入框常驻底部，设置与全屏编辑都在框里
  $("#asSettings").addEventListener("click", assistantConfigDialog);
  $("#asExpand").addEventListener("click", assistantFullscreenDialog);
  /* 开一段新对话：页面右上角那颗按钮与会话抽屉里的「新建」走同一条路 */
  function startNewSession() {
    closeAssistantDrawer();
    location.hash = "#/assistant";      // 新会话：点开就是初始页，发消息才落库
    openAssistantSession(0);
  }

  $("#assistantReset").addEventListener("click", startNewSession);
  $("#asDrawerNew").addEventListener("click", startNewSession);
  $("#asApprovalYes").addEventListener("click", function () { answerApproval(true); });
  $("#asApprovalNo").addEventListener("click", function () { answerApproval(false); });
  $("#asApprovalMore").addEventListener("click", toggleApprovalArgs);
  $("#asSessionBar").addEventListener("click", openAssistantDrawer);
  $("#assistantScroll").addEventListener("scroll", function () {
    if (S.streamScrollOwn) {
      S.streamScrollOwn = false;      // 这一跳是我们自己滚的，不算用户的意思
    } else if (S.assistantStream) {
      S.assistantStream.stick = nearBottom();
    }
    syncToBottom();
  });
  $("#asToBottom").addEventListener("click", function () {
    var box = $("#assistantScroll");
    if (!box) return;
    if (box.scrollTo) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
    else box.scrollTop = box.scrollHeight;
    setTimeout(syncToBottom, 320);
  });

  /* 上下文环：桌面指上去就出明细，触屏点一下（再点或点外面收起） */
  function ctxHasHover() {
    return !!(window.matchMedia && window.matchMedia("(hover: hover)").matches);
  }
  $("#asContext").addEventListener("click", function (e) {
    e.stopPropagation();
    var panel = $("#asContextPanel");
    var open = panel && !panel.hidden;
    if (open && ctxHasHover()) return;      // 悬停已经开着，点它不再收
    showContextPanel(!open);
  });
  $("#asContextWrap").addEventListener("mouseenter", function () {
    if (ctxHasHover()) showContextPanel(true);
  });
  /* 动作胶囊：指上去看这一步调了什么；触屏点一下开关，点外面收起 */
  $("#assistantLog").addEventListener("mouseover", function (e) {
    var chip = e.target.closest ? e.target.closest("[data-step-tip]") : null;
    if (chip && ctxHasHover()) openStepTip(chip);
  });
  $("#assistantLog").addEventListener("mouseout", function (e) {
    var chip = e.target.closest ? e.target.closest("[data-step-tip]") : null;
    if (chip && ctxHasHover()) closeStepTip();
  });
  $("#assistantLog").addEventListener("click", function (e) {
    var chip = e.target.closest ? e.target.closest("[data-step-tip]") : null;
    if (!chip) return;
    e.stopPropagation();
    var idx = Number(chip.getAttribute("data-step-tip"));
    if (S.stepTipFor === idx) closeStepTip();
    else openStepTip(chip);
  });
  document.addEventListener("pointerdown", function (e) {
    var tip = $("#asStepTip");
    if (!tip || tip.hidden) return;
    if (e.target && e.target.closest &&
        (e.target.closest("[data-step-tip]") || e.target.closest("#asStepTip"))) return;
    closeStepTip();
  });
  $("#asContextWrap").addEventListener("mouseleave", function () {
    if (ctxHasHover()) showContextPanel(false);
  });
  document.addEventListener("pointerdown", function (e) {
    var panel = $("#asContextPanel");
    if (!panel || panel.hidden) return;
    if (e.target && e.target.closest && e.target.closest("#asContextWrap")) return;
    showContextPanel(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    showContextPanel(false);
  });
  $("#asDrawerClose").addEventListener("click", closeAssistantDrawer);
  $("#asDrawerBody").addEventListener("click", assistantDrawerClick);
  $("#asExport").addEventListener("click", exportAssistantSession);
  $("#assistantForm").addEventListener("submit", function (e) {
    e.preventDefault();
    // 正在等回答时这颗按钮是"停止"
    if (S.assistantAbort) { stopAssistant(); return; }
    var box = $("#assistantText");
    var text = box.value;
    box.value = "";
    growAssistantBox();
    sendAssistant(text);
  });
  $("#assistantText").addEventListener("keydown", function (e) {
    // 触屏上 Enter 属于输入法（换行），不能当作发送：手机上就多点一下发送键
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    if (window.matchMedia && window.matchMedia("(pointer: coarse)").matches) return;
    e.preventDefault();
    $("#assistantForm").dispatchEvent(new Event("submit", { cancelable: true }));
  });
  $("#assistantText").addEventListener("input", growAssistantBox);
  window.addEventListener("resize", growAssistantBox);
  window.addEventListener("resize", scheduleFitTables);
  $("#tokenNew").addEventListener("click", tokenDialog);
  $("#tokenTable").addEventListener("click", function (e) {
    var btn = e.target.closest("[data-token-revoke]");
    if (btn) revokeToken(Number(btn.getAttribute("data-token-revoke")));
  });
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
    reader.onerror = function () { PX.toast("无法读取该文件", true); };
    reader.readAsDataURL(file);
  });

  function newEvent() {
    S.invited = [];
    loadTemplates().then(function () { go("new-event"); });
  }
  $("#newEventBtn").addEventListener("click", newEvent);
  $("#presetNewBtn").addEventListener("click", function () { presetDialog(null); });
  $("#archivedMore").addEventListener("click", function () { loadArchived(false); });
  $("#presetImportBtn").addEventListener("click", function () {
    $("#presetImportFile").click();
  });
  $("#screenPackAdd").addEventListener("click", function () {
    $("#screenPackFile").click();
  });
  $("#screenPackFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) uploadScreenPack(file);
  });
  $("#moduleInstallBtn").addEventListener("click", function () {
    $("#moduleFile").click();
  });
  $("#moduleFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) uploadModule(file);
  });
  $("#screenPackList").addEventListener("click", function (e) {
    var btn = e.target.closest("[data-pack-del]");
    if (btn) deleteScreenPack(btn.getAttribute("data-pack-del"));
  });
  $("#presetImportFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) importPreset(file);
  });
  // 活动管理 is a superadmin view; creating from there uses the same flow

  /* ---- 名单 ---- */
  $("#rosterNewBtn").addEventListener("click", function () { rosterDialog(null); });
  $("#rosterImportBtn").addEventListener("click", rosterImportDialog);
  $("#rosterImportFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) importRoster(file, 0);
  });
  $("#rosterMergeBtn").addEventListener("click", function () {
    if (S.roster) $("#rosterMergeFile").click();
  });
  $("#rosterMergeFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file && S.roster) importRoster(file, S.roster.id);
  });
  $("#rosterExportBtn").addEventListener("click", function () {
    if (!S.roster) return;
    location.href = PX.PREFIX + "/api/panel/roster/export.csv?id=" + S.roster.id;
  });
  $("#rosterXlsxBtn").addEventListener("click", function () {
    if (!S.roster) return;
    location.href = PX.PREFIX + "/api/panel/roster/export.xlsx?id=" + S.roster.id;
  });
  // 打印名单：一张简单的表，另开一张纸
  $("#rosterPrintBtn").addEventListener("click", function () {
    if (!S.roster) return;
    window.open(PX.PREFIX + "/print?kind=roster&id=" + S.roster.id, "_blank", "noopener");
  });
  $("#rosterEditBtn").addEventListener("click", function () {
    if (S.roster) rosterDialog(S.roster);
  });
  $("#rosterAddBtn").addEventListener("click", addRosterEntryDialog);
  $("#rosterMore").addEventListener("click", function () {
    loadRosters(true).then(drawRosters);
  });
  $("#rosterEntryMore").addEventListener("click", function () {
    loadEntries(true).then(drawEntries);
  });
  var scopeBox = $("#rosterScopeBox");
  if (scopeBox) {
    scopeBox.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-scope]") : null;
      if (!btn) return;
      var next = btn.getAttribute("data-scope");
      if (next === S.rosterScope) return;
      S.rosterScope = next;
      drawRosters();
      loadRosters(false).then(drawRosters);
    });
  }
  var adminScopeBox = $("#adminListScope");
  if (adminScopeBox) {
    adminScopeBox.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-archived]") : null;
      if (!btn) return;
      var next = btn.getAttribute("data-archived") === "1";
      if (next === !!S.adminArchived) return;
      S.adminArchived = next;
      S.adminRosters = null;
      S.adminSignups = null;
      markLoading("adminRosters");
      markLoading("adminSignups");
      drawRosterAdmin();
      loadAdminLists().then(drawRosterAdmin);
    });
  }
  $("#rosterTable").addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-open-roster]") : null;
    if (!btn) return;
    location.hash = "#/roster/" + btn.getAttribute("data-open-roster");
  });
  /* ---- 报名 ---- */
  $("#signupNewBtn").addEventListener("click", function () {
    if (!(S.rosters || []).length) {
      loadRosters(false).then(function () { signupDialog(); });
      return;
    }
    signupDialog();
  });
  $("#signupTable").addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-open-signup]") : null;
    if (!btn) return;
    location.hash = "#/signup/" + btn.getAttribute("data-open-signup");
  });
  $("#signupMore").addEventListener("click", function () {
    loadSignups(true).then(drawSignups);
  });
  (function bindSignupForm() {
    var form = document.querySelector('.page[data-page="signup"]');
    if (!form) return;
    form.addEventListener("input", function (e) {
      if (e.target && e.target.tagName !== "BUTTON") S.signupDirty = true;
    });
    // change 是"这次改完了"的信号，用它落一版快照
    form.addEventListener("change", function (e) {
      if (e.target && e.target.tagName !== "BUTTON") {
        S.signupDirty = true;
        pushHistory();
      }
    });
    form.addEventListener("blur", function (e) {
      var t = e.target;
      if (!t) return;
      if (t.classList && t.classList.contains("dt-date")) {
        var parsed = parseStamp(t.value);
        if (parsed.error) return;            // 让用户自己改，别把输入吃掉
        if (parsed.date) {
          t.value = parsed.date;
          // 只有日期里自带时刻时才写时刻框；空的时刻留给保存时补 00:00，
          // 否则会和用户接下来的输入打架
          var timeBox = $("#" + t.id.replace("Date", "Time"));
          if (parsed.hasTime && timeBox) timeBox.value = parsed.time;
          S.signupDirty = true;
          pushHistory();
        }
      }
      if (t.type === "text" || t.type === "number" || t.tagName === "TEXTAREA") {
        pushHistory();
      }
    }, true);
  })();

  /* 一对「日期 + 时刻」。电脑上用两个文本框配日历按钮；手机上这两个文本框被隐藏，
     换成同样两个原生控件的可见版本（点一下就是系统日历/时间轮）。
     保存时读的始终是文本框，原生控件只负责把值写进去，所以两边的格式不会打架。 */
  var DT_PAIRS = [
    { pick: "#signupOpensPick", datePick: "#signupOpensDatePick",
      timePick: "#signupOpensTimePick", date: "#signupOpensDate",
      time: "#signupOpensTime", clear: "#signupOpensClear" },
    { pick: "#signupClosesPick", datePick: "#signupClosesDatePick",
      timePick: "#signupClosesTimePick", date: "#signupClosesDate",
      time: "#signupClosesTime", clear: "#signupClosesClear" }
  ];

  // 原生控件选完：写回文本框，标记改动，让 Ctrl+Z 也能回退
  function nativePicked(pair) {
    var date = $(pair.datePick).value || "";
    var time = $(pair.timePick).value || "";
    if (!date && !time) return;
    $(pair.date).value = date;
    $(pair.time).value = time || ($(pair.date).value ? "00:00" : "");
    $(pair.clear).disabled = false;
    S.signupDirty = true;
    pushHistory();
  }

  function wirePicker(pair) {
    $(pair.pick).addEventListener("click", function () {
      var native = $(pair.datePick);
      native.value = $(pair.date).value || "";
      // showPicker 把选择器锚在这个控件上：它就在按钮中心，看起来是"贴着按钮弹出"
      if (native.showPicker) {
        try { native.showPicker(); return; } catch (e) { /* 老浏览器退回点击 */ }
      }
      native.focus();
      native.click();
    });
    $(pair.datePick).addEventListener("change", function () {
      if (this.value) {
        $(pair.date).value = this.value;
        if (!$(pair.time).value) $(pair.time).value = "00:00";
        $(pair.clear).disabled = false;
        syncNativePair(pair, pairValue(pair.date, pair.time));
        S.signupDirty = true;
        pushHistory();
      }
    });
    $(pair.timePick).addEventListener("change", function () {
      nativePicked(pair);
    });
  }
  DT_PAIRS.forEach(wirePicker);

  $("#signupOpensClear").addEventListener("click", function () {
    clearPair("#signupOpensDate", "#signupOpensTime", "#signupOpensClear");
    drawThemePreview();
  });
  $("#signupClosesClear").addEventListener("click", function () {
    clearPair("#signupClosesDate", "#signupClosesTime", "#signupClosesClear");
  });
  // 清空按钮的状态跟着两个输入框走
  ["#signupOpensDate", "#signupOpensTime", "#signupClosesDate", "#signupClosesTime"]
    .forEach(function (sel) {
      $(sel).addEventListener("input", function () {
        $("#signupOpensClear").disabled =
          !($("#signupOpensDate").value || $("#signupOpensTime").value);
        $("#signupClosesClear").disabled =
          !($("#signupClosesDate").value || $("#signupClosesTime").value);
      });
    });
  $("#signupSaveBtn").addEventListener("click", saveSignup);
  $("#signupArchiveBtn").addEventListener("click", function () {
    // 拥有者能做的最重的一步：报名从列表里退下去、公开链接随之失效，
    // 报名记录一条不动。真要抹掉得由超级管理员在「名单管理」里做
    if (!S.signup) return;
    confirmHighRisk("归档报名",
      "归档之后这份报名从你的列表里消失，公开链接随即失效；已收到的报名原样留着，"
      + "要恢复请联系超级管理员。确认继续？", "归档", true,
      function () {
        archiveList("signup", S.signup.id, function () {
          PX.toast("已归档，需要时联系超级管理员");
          S.signup = null;
          location.hash = "#/signups";
        });
      }, { noSudo: true });
  });
  $("#signupBack").addEventListener("click", function () {
    location.hash = "#/signups";
  });
  $("#rosterBack").addEventListener("click", function () {
    location.hash = "#/rosters";
  });

  /* ---- 导入账号 ---- */
  $("#importBack").addEventListener("click", function () {
    location.hash = "#/users";
  });
  $("#importAccountBtn").addEventListener("click", function () {
    location.hash = "#/import-accounts";
  });
  $("#importPickBtn").addEventListener("click", function () {
    var picker = $("#importFile");
    picker.value = "";
    picker.click();
  });
  $("#importFile").addEventListener("change", function () {
    var file = this.files && this.files[0];
    if (file) readImportFile(file);
  });
  $("#importManualBtn").addEventListener("click", addImportBlankRow);
  $("#importFillBtn").addEventListener("click", autofillDialog);
  $("#importDownloadBtn").addEventListener("click", downloadImportTable);
  $("#importGo").addEventListener("click", submitImport);
  $("#importTable").addEventListener("input", function (e) {
    var input = e.target;
    if (input && input.getAttribute && input.getAttribute("data-import-field")) {
      importCellChanged(input, false);
    }
  });
  $("#importTable").addEventListener("change", function (e) {
    var input = e.target;
    if (input && input.getAttribute && input.getAttribute("data-import-field")) {
      importCellChanged(input, true);
    }
  });

  $("#signupCopyBtn").addEventListener("click", function () {
    var text = $("#signupUrl").textContent || "";
    var done = function () { PX.toast("链接已复制"); };
    var failed = function () { PX.toast("复制失败，请手动选择", true); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () {
        // 退回老办法：选中文本再复制
        try {
          var range = document.createRange();
          range.selectNodeContents($("#signupUrl"));
          var sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          document.execCommand("copy");
          done();
        } catch (e) { failed(); }
      });
      return;
    }
    try {
      var r2 = document.createRange();
      r2.selectNodeContents($("#signupUrl"));
      var s2 = window.getSelection();
      s2.removeAllRanges();
      s2.addRange(r2);
      document.execCommand("copy");
      done();
    } catch (e) { failed(); }
  });
  $("#signupQrSave").addEventListener("click", function () {
    var url = (S.signup && S.signup.url) || "";
    if (!url) { PX.toast("还没有可用的报名链接", true); return; }
    // 下载件单独生成：留出四个模块的白边（网页里那圈只有一格），
    // 并且给出明确的像素尺寸，导出的 PNG 才不会糊
    var svgTag;
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      svgTag = qr.createSvgTag({ cellSize: 8, margin: 32 });
    } catch (e) {
      PX.toast("二维码生成失败", true);
      return;
    }
    var img = new Image();
    img.onload = function () {
      var size = 720;
      var canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, size, size);
      ctx.drawImage(img, 0, 0, size, size);
      var link = document.createElement("a");
      link.href = canvas.toDataURL("image/png");
      link.download = (($("#signupName").value.trim() || "signup") + "-二维码.png");
      link.click();
    };
    img.onerror = function () { PX.toast("导出二维码失败", true); };
    img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(svgTag)));
  });

  /* ---------------- 二维码的其它格式 ----------------
     网页里那颗是 SVG（缩放随便），下载件另做：
     - PNG 1:1：一格一像素、不留白边、只有黑白两色，图里一个杂色都没有
     - PNG 打印：1024 见方、四周留四个模块的白边
     - SVG：矢量，任何尺寸都清楚
     都用二维码自己的矩阵画（isDark），不经过 SVG 再栅格化，所以才是"1:1"。 */
  var qrMenuEl = null;

  function closeQrMenu() {
    if (!qrMenuEl) return;
    qrMenuEl.remove();
    qrMenuEl = null;
    var more = $("#signupQrMore");
    if (more) more.setAttribute("aria-expanded", "false");
  }

  function qrModel() {
    var url = (S.signup && S.signup.url) || "";
    if (!url) {
      PX.toast("还没有可用的报名链接", true);
      return null;
    }
    try {
      var qr = qrcode(0, "M");
      qr.addData(url);
      qr.make();
      return qr;
    } catch (e) {
      PX.toast("二维码生成失败", true);
      return null;
    }
  }

  // 一格一像素地画：cell 个像素表示一个模块，margin 是留白的模块数
  function qrCanvas(qr, cell, margin) {
    var count = qr.getModuleCount();
    var side = (count + margin * 2) * cell;
    var canvas = document.createElement("canvas");
    canvas.width = side;
    canvas.height = side;
    var ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, side, side);
    ctx.fillStyle = "#000000";
    for (var row = 0; row < count; row++) {
      for (var col = 0; col < count; col++) {
        if (qr.isDark(row, col)) {
          ctx.fillRect((col + margin) * cell, (row + margin) * cell, cell, cell);
        }
      }
    }
    return canvas;
  }

  /* 一格一像素的 1 位灰度 PNG：一个模块一个像素，没有留白，只有黑白两色。
     canvas 只能给 8 位 RGBA，所以这一份自己拼：每行一个过滤字节 + 位图，
     位图里 1 = 白、0 = 黑。压缩用 CompressionStream（deflate），外面套 PNG 要的
     zlib 头与 adler32。这样出来的文件比 canvas 那份小一个数量级，也没有 alpha。 */
  var _crcTable = null;

  function crc32(bytes) {
    if (!_crcTable) {
      _crcTable = new Uint32Array(256);
      for (var n = 0; n < 256; n++) {
        var c = n;
        for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
        _crcTable[n] = c >>> 0;
      }
    }
    var crc = 0xFFFFFFFF;
    for (var i = 0; i < bytes.length; i++) {
      crc = _crcTable[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8);
    }
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }

  function adler32(bytes) {
    var a = 1, b = 0;
    for (var i = 0; i < bytes.length; i++) {
      a = (a + bytes[i]) % 65521;
      b = (b + a) % 65521;
    }
    return ((b << 16) | a) >>> 0;
  }

  function u32(value) {
    return [(value >>> 24) & 255, (value >>> 16) & 255, (value >>> 8) & 255,
            value & 255];
  }

  function pngChunk(type, data) {
    var body = [];
    for (var i = 0; i < type.length; i++) body.push(type.charCodeAt(i));
    var payload = body.concat(data);
    return u32(data.length).concat(payload, u32(crc32(payload)));
  }

  // "deflate" 在 Compression Streams 里给的就是 zlib（RFC1950），正是 PNG 要的
  function deflateZlib(bytes) {
    if (!window.CompressionStream) return Promise.reject(new Error("浏览器不支持压缩"));
    var stream = new Blob([new Uint8Array(bytes)]).stream()
      .pipeThrough(new CompressionStream("deflate"));
    return new Response(stream).arrayBuffer().then(function (buf) {
      return Array.prototype.slice.call(new Uint8Array(buf));
    });
  }

  // 1 位灰度的 PNG 字节：side 见方，dark(row, col) 决定那一像素
  function qrMonoPng(qr) {
    var count = qr.getModuleCount();
    var rowBytes = Math.ceil(count / 8);
    var raw = [];
    for (var row = 0; row < count; row++) {
      raw.push(0);                              // 过滤类型：无
      for (var b = 0; b < rowBytes; b++) {
        var byte = 0;
        for (var bit = 0; bit < 8; bit++) {
          var col = b * 8 + bit;
          var white = col >= count || !qr.isDark(row, col);
          byte = (byte << 1) | (white ? 1 : 0);
        }
        raw.push(byte);
      }
    }
    return deflateZlib(raw).then(function (idat) {
      var ihdr = u32(count).concat(u32(count), [1, 0, 0, 0, 0]);   // 1 位、灰度
      return [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]
        .concat(pngChunk("IHDR", ihdr), pngChunk("IDAT", idat),
                pngChunk("IEND", []));
    });
  }

  function qrBaseName() {
    var box = $("#signupName");
    return ((box && box.value.trim()) || (S.signup && S.signup.title) || "signup");
  }

  function saveUrl(name, href) {
    var link = document.createElement("a");
    link.href = href;
    link.download = name;
    link.click();
  }

  function qrDownload(kind) {
    var qr = qrModel();
    if (!qr) return;
    var base = qrBaseName();
    if (kind === "png-1to1") {
      var count = qr.getModuleCount();
      qrMonoPng(qr).then(function (bytes) {
        saveUrl(base + "-二维码-" + count + "px.png",
                "data:image/png;base64," +
                btoa(String.fromCharCode.apply(null, bytes)));
        PX.toast("已下载 " + count + "×" + count + " 的 1:1 二维码");
      }).catch(function () {
        // 老浏览器没有 CompressionStream：退回 canvas（像素仍然是纯黑白）
        saveUrl(base + "-二维码-" + count + "px.png",
                qrCanvas(qr, 1, 0).toDataURL("image/png"));
        PX.toast("已下载 " + count + "×" + count + " 的 1:1 二维码");
      });
      return;
    }
    if (kind === "png-print") {
      var side = 1024;
      var cell = Math.max(1, Math.floor(side / (qr.getModuleCount() + 8)));
      saveUrl(base + "-二维码-打印.png",
              qrCanvas(qr, cell, 4).toDataURL("image/png"));
      PX.toast("已下载打印用的二维码");
      return;
    }
    if (kind === "svg") {
      var tag = qr.createSvgTag({ cellSize: 8, margin: 0, scalable: true });
      // 库自带的白底 rect 在矢量件里多余：白底交给用它的人
      tag = tag.replace(/<rect[^>]*fill="white"[^>]*\/>/i, "");
      saveUrl(base + "-二维码.svg",
              "data:image/svg+xml;charset=utf-8," + encodeURIComponent(tag));
      PX.toast("已下载 SVG");
    }
  }

  function openQrMenu(anchor) {
    if (qrMenuEl) { closeQrMenu(); return; }
    var menu = document.createElement("div");
    menu.className = "card-menu";
    menu.setAttribute("role", "menu");
    menu.innerHTML =
      '<button type="button" role="menuitem" data-qr="png-1to1">PNG · 1 格 1 像素，无留白</button>' +
      '<button type="button" role="menuitem" data-qr="png-print">PNG · 1024，带留白</button>' +
      '<button type="button" role="menuitem" data-qr="svg">SVG · 矢量</button>';
    document.body.appendChild(menu);
    var rect = anchor.getBoundingClientRect();
    var left = Math.min(Math.max(8, rect.left),
                        window.innerWidth - menu.offsetWidth - 8);
    var top = rect.bottom + 6;
    if (top + menu.offsetHeight > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menu.offsetHeight - 6);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    qrMenuEl = menu;
    anchor.setAttribute("aria-expanded", "true");
    menu.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-qr]");
      if (!btn) return;
      closeQrMenu();
      qrDownload(btn.getAttribute("data-qr"));
    });
    var first = menu.querySelector("button");
    if (first) first.focus();
  }

  $("#signupQrMore").addEventListener("click", function (e) {
    e.stopPropagation();
    openQrMenu(this);
  });
  document.addEventListener("click", function (e) {
    if (qrMenuEl && !qrMenuEl.contains(e.target) &&
        e.target !== $("#signupQrMore") && !e.target.closest("#signupQrMore")) {
      closeQrMenu();
    }
  });

  ["#signupTheme", "#signupAccent", "#signupName", "#signupNote",
   "#collectDept", "#collectGrp"].forEach(function (sel) {
    var el = $(sel);
    if (!el) return;
    el.addEventListener("input", drawThemePreview);
    el.addEventListener("change", drawThemePreview);
  });
  $("#signupTimeOn").addEventListener("change", function () {
    toggleCardState("#signupTimeCard", this.checked);
  });
  $("#signupCapOn").addEventListener("change", function () {
    toggleCardState("#signupCapCard", this.checked);
    if (this.checked && !Number($("#signupCap").value)) $("#signupCap").value = 1;
  });
  $("#signupExportBtn").addEventListener("click", function () {
    if (!S.signup) return;
    location.href = PX.PREFIX + "/api/panel/signup/export.csv?id=" + S.signup.id;
  });
  $("#signupXlsxBtn").addEventListener("click", function () {
    if (!S.signup) return;
    location.href = PX.PREFIX + "/api/panel/signup/export.xlsx?id=" + S.signup.id;
  });
  // 打印报名表：另开一张纸（空白表 / 已报名单），不挤在面板里
  $("#signupPrintBtn").addEventListener("click", function () {
    if (!S.signup) return;
    window.open(PX.PREFIX + "/print?kind=signup&id=" + S.signup.id, "_blank", "noopener");
  });
  $("#signupEntryMore").addEventListener("click", function () {
    loadSignupEntries(true).then(drawSignupEntries);
  });
  $("#fieldAddBtn").addEventListener("click", function () {
    if (!S.signup) return;
    var fields = collectFields().concat([{ label: "", type: "text", into: "", required: false }]);
    S.signup.fields = fields;
    drawFieldEditor();
    drawThemePreview();
    var inputs = $$("#fieldEditor .f-label");
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  $("#fieldEditor").addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-field-del]") : null;
    if (!btn) return;
    var kept = collectFields();
    kept.splice(Number(btn.getAttribute("data-field-del")), 1);
    S.signup.fields = kept;
    drawFieldEditor();
    drawThemePreview();
  });
  bindLiveSearch("#signupSearch", "#signupSearchClear", function (value) {
    S.signupFilter = value;
    loadSignups(false).then(drawSignups);
  });
  bindLiveSearch("#signupEntrySearch", "#signupEntrySearchClear", function (value) {
    S.signupEntryFilter = value;
    loadSignupEntries(false).then(drawSignupEntries);
  });

  $("#bellBtn").addEventListener("click", function () {
    openNoticePanel();
  });
  loadSvcMuted();
  loadService();
  setInterval(loadService, 30000);
  document.addEventListener("keydown", function (e) {
    // 紧急弹窗可以用 ESC 收起，等同于点"关闭"（不标已读）
    if (e.key === "Escape" && $("#alertBack") && !$("#alertBack").hidden) {
      e.preventDefault();
      e.stopPropagation();
      closeAlert(false);
    }
    // 没有 href 的页内链接要自己认键盘：Enter / 空格跟点击一样
    if (e.key === "Enter" || e.key === " ") {
      var keyLink = e.target && e.target.closest ? e.target.closest("[data-href]") : null;
      if (keyLink && e.target === keyLink) {
        e.preventDefault();
        var where = keyLink.getAttribute("data-href");
        if (location.hash !== where) location.hash = where;
      }
    }
  }, true);
  $("#noticeReadAll").addEventListener("click", function () { markNotices([]); });
  $("#noticeList").addEventListener("click", function (e) {
    var muteBtn = e.target.closest ? e.target.closest("[data-svc-mute]") : null;
    if (muteBtn) {
      muteService(muteBtn.getAttribute("data-svc-mute"));
      return;
    }
    var item = e.target.closest ? e.target.closest("[data-notice]") : null;
    if (!item) return;
    var id = Number(item.getAttribute("data-notice"));
    var obj = item.getAttribute("data-object") || "";
    markNotices([id]);
    if (obj.indexOf("roster:") === 0) {
      openNoticePanel(false);
      location.hash = "#/roster/" + obj.split(":")[1];
    }
  });
  $("#alertRead").addEventListener("click", function () { closeAlert(true); });
  $("#alertClose").addEventListener("click", function () { closeAlert(false); });
  // 点空白处收起列表，但紧急弹窗不在此列
  document.addEventListener("click", function (e) {
    var panel = $("#noticePanel");
    if (!panel || !noticePanelOpen()) return;
    if (panel.contains(e.target) || e.target.closest("#bellBtn")) return;
    openNoticePanel(false);
  });

  $("#approvalsBack").addEventListener("click", function () {
    var to = this.getAttribute("data-back") || "";
    if (to) location.hash = "#" + to;
  });
  $("#approvalsTable").addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-approve],[data-reject]") : null;
    if (!t) return;
    var acct = Number(t.getAttribute("data-approve-acct"));
    if (t.getAttribute("data-approve")) {
      decideApproval(Number(t.getAttribute("data-approve")), acct, "approve");
      return;
    }
    decideApproval(Number(t.getAttribute("data-reject")), acct, "reject");
  });
  $("#rosterShareBody").addEventListener("click", function (e) {
    var t = e.target.closest ?
      e.target.closest("[data-revoke],[data-ask-ok],[data-ask-no],#rosterAskBtn,#rosterGrantBtn") : null;
    if (!t) return;
    if (t.id === "rosterAskBtn") { rosterAsk(); return; }
    if (t.id === "rosterGrantBtn") { rosterGrantPicker(); return; }
    var ok = t.getAttribute("data-ask-ok");
    if (ok) { rosterDecide(Number(ok), "approve"); return; }
    var no = t.getAttribute("data-ask-no");
    if (no) { rosterDecide(Number(no), "reject"); return; }
    var rev = t.getAttribute("data-revoke");
    if (rev) { rosterRevoke(Number(rev)); return; }
  });
  $("#rosterEntryTable").addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-entry-drop],[data-entry-state]") : null;
    if (!t) return;
    var drop = t.getAttribute("data-entry-drop");
    if (drop) {
      // 移出是把这一条删掉（分组、标签、备注一起没了），不能点错就到对面去；
      // 自己名单里的动作，只要一句确认，不用密码
      var who = t.getAttribute("data-entry-name") || "这个人";
      confirmHighRisk("移出名单",
                      "把 " + PX.esc(who) + " 移出「" +
                      PX.esc((S.roster && S.roster.name) || "这份名单") +
                      "」？分组、标签与备注会一起丢掉，只想留个记录就用「标为退出」。",
                      "移出", true,
                      function () { rosterEntryAction("drop", Number(drop)); },
                      {noSudo: true});
      return;
    }
    rosterEntryAction("state", Number(t.getAttribute("data-entry-state")),
                      t.getAttribute("data-entry-to"));
  });
  bindLiveSearch("#rosterSearch", "#rosterSearchClear", function (value) {
    S.rosterFilter = value;
    loadRosters(false).then(drawRosters);
  });
  bindLiveSearch("#rosterEntrySearch", "#rosterEntrySearchClear", function (value) {
    S.entryFilter = value;
    loadEntries(false).then(function () { drawRoster(); });
  });

  $("#kindBack").addEventListener("click", function () {
    if (S.dirty) return askDraftChoice("new-event");
    S.invited = [];
    go("new-event");
  });
  $("#pickerBack").addEventListener("click", function () {
    if (S.dirty) return askDraftChoice("events");
    go("events");
  });
  $("#kindCreate").addEventListener("click", createEvent);
  // 表单是整块重画的，脏标记交给容器一次性代理；点选成员在弹窗里发生，另外记一笔
  $("#kindForm").addEventListener("input", markDirty);
  $("#kindForm").addEventListener("change", markDirty);
  $("#kindImport").addEventListener("click", function () {
    $("#kindImportFile").click();
  });
  $("#kindImportFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) importConfig(file);
  });
  $("#invitePick").addEventListener("click", openInvitePicker);
  bindLiveSearch("#kindSearch", "#kindSearchClear", function (value) {
    S.templateFilter = value;
    if (!$("#kindPicker").hidden) $("#kindGrid").innerHTML = renderKindTiles();
  });

  window.addEventListener("hashchange", onRoute);

  window.addEventListener("beforeunload", function (e) {
    if (S.route === "new-event" && S.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  window.addEventListener("keydown", function (e) {
    var onSignup = S.route === "signup";
    var mod = e.ctrlKey || e.metaKey;
    if (mod && !e.altKey) {
      var key = (e.key || "").toLowerCase();
      // Ctrl+S 默认会弹"保存网页"，换成保存这张表单
      if (key === "s" && onSignup && S.signup) {
        e.preventDefault();
        saveSignup();
        return;
      }
      // 撤销 / 恢复：光标在文本输入框里时交给浏览器（逐字撤销是它更熟的语义），
      // 其余情况（刚拨过开关、刚选过下拉）撤销的是整张表单的状态
      var typing = (function (el) {
        if (!el) return false;
        // 日期/时刻这两格是表单控件，不是随手的文字：撤销应该回退整张表单
        if (el.classList && (el.classList.contains("dt-date") ||
                             el.classList.contains("dt-time"))) return false;
        var tag = (el.tagName || "").toLowerCase();
        return tag === "textarea" ||
          (tag === "input" && ["text", "number", "search", "email"].indexOf(el.type) >= 0);
      })(document.activeElement);
      if (onSignup && $("#overlay").hidden && !typing) {
        if (key === "z") {
          e.preventDefault();
          if (e.shiftKey) redoSignup();
          else undoSignup();
          return;
        }
        if (key === "y") {
          e.preventDefault();
          redoSignup();
          return;
        }
      }
    }
    if (e.key === "Escape") {
      if (cardTipEl) return closeCardTip();
      if (qrMenuEl) return closeQrMenu();
      if (cardMenuEl) return closeCardMenu();
      if (!$("#overlay").hidden) {
        // 弹窗里有输入框正被聚焦时不退：ESC 不是会误触的键，但填了一半的东西
        // 不该因为按了一下 ESC 就没了（搜索框的 ESC 留给"清空"）
        if (typingIn($("#overlay"))) return;
        return closeOverlay();
      }
      if (assistantDrawerOpen()) return closeAssistantDrawer();
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

  /* 重新部署之后，这个页面上的脚本已经是旧的了。能自己换掉就换，不能就先说一声：
     这一页里已经敲过字（表单、搜索框都算）、或者开着弹窗，都算“不能说走就走”。 */
  function canSwapNow() {
    if (S.typed || S.signupDirty || S.dirty) return false;
    var overlay = $("#overlay");
    if (overlay && !overlay.hidden) return false;
    return true;
  }

  function showUpdateBar(version) {
    var bar = $("#updateBar");
    var text = $("#updateText");
    if (!bar || !text) return;
    text.textContent = "服务已更新到 v" + version + "，刷新后使用新版本";
    bar.hidden = false;
  }

  function serverUpdated(version) {
    if (S.updateTo === version) return;
    S.updateTo = version;
    if (canSwapNow()) {
      // 自己换的页面，换完要说一声：不然人只会觉得"刚才好像闪了一下"
      try { sessionStorage.setItem("px:updatedTo", version); } catch (e) { /* 无痕模式 */ }
      location.reload();
      return;
    }
    showUpdateBar(version);
  }

  // 刷新之后把上一页留下的版本号取出来，说明这次是自更新而不是人手刷的
  function announceUpdate() {
    var version = "";
    try {
      version = sessionStorage.getItem("px:updatedTo") || "";
      if (version) sessionStorage.removeItem("px:updatedTo");
    } catch (e) {
      return;
    }
    if (!version) return;
    PX.toast("无感更新完成：v" + PX.esc(version), false, 3600);
  }

  function bindUpdateBar() {
    var go = $("#updateGo");
    if (go) {
      go.addEventListener("click", function () {
        S.signupDirty = false;
        S.dirty = false;
        S.typed = false;
        location.reload();
      });
    }
    // 只要人真的在什么地方敲了字，就别在他打字的时候把页面换掉
    document.addEventListener("input", function (e) {
      if (e.isTrusted) S.typed = true;
    }, true);
    PX.onUpdate(serverUpdated);
  }

  /* ------------------------------------------------------------------- boot */
  function boot() {
    drawThemeBtn();
    bindUpdateBar();
    PX.api("/me").then(function (d) {
      if (!d.authed) {
        location.href = PX.PREFIX + "/login";
        return;
      }
      // 临时密码：服务端也会把页面拦到 /reset，这里补一道，免得脚本绕过
      if (d.mustReset) {
        location.replace(PX.PREFIX + "/reset");
        return;
      }
      S.me = d.me;
      renderIdentity();
      applyRoleVisibility();
      labelNav();
      tidyNavSeparators();
      bindSidebarKeys();
      PX.afterApi(nudgeCrumb);
      PX.afterApi(scheduleFitTables);
      // another session's writes come back through the stream
      PX.watch(function (revs, before) {
        if (!before || before.panel === undefined) return;
        if (revs.panel === before.panel) return;
        scheduleReload();
        loadNotices();
      });
      // a reload keeps an elevation that has not run out yet
      setSudo(d.sudoUntil, d.now);
      // 刷新之后侧栏那个小红点还得在：服务端把挂着的对话一起带过来
      adoptPendingServer(d.assistantPending || []);
      return reloadAll().then(function () {
        loadNotices();
        loadApprovals();
        startHostPolling();
        onRoute();
        announceUpdate();
        // a real readiness signal: tests used to poll the placeholder text
        document.documentElement.setAttribute("data-booted", "1");
      });
    }).catch(function (e) {
      if (e.status === 401) location.href = PX.PREFIX + "/login";
      else PX.toast(PX.esc(e.message || "加载失败"), true);
    });
  }

  boot();
})();