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
    "all-events": { min: "superadmin" },
    "approval-users": { min: "superadmin", group: "approvals" },
    "approval-events": { min: "superadmin", group: "approvals" },
    "new-event": { min: "admin" },
    "modules": { min: "superadmin" },
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
    templates: [],
    archived: [],
    archivedTotal: null,
    archivedLoading: false,
    currentFields: [],
    templateId: null,
    templateFilter: "",
    invited: [],
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

  // 允许点遮罩关闭的弹窗（都是没有输入、关掉不丢东西的）
  var OVERLAY_OUTSIDE_CLOSE = { presets: true, info: true };

  function openOverlay(kind, title, html) {
    overlayKind = kind;
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
  }

  function closeOverlay() {
    if ($("#overlay").hidden) return;
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
    // the account page offers the same switch, with the same countdown
    var state = $("#sudoState");
    if (state) {
      state.textContent = left > 0
        ? "已进入，将于 " + clockText(left) + " 后退出"
        : "未进入";
      state.classList.toggle("on", left > 0);
    }
    var button = $("#sudoBtn");
    if (button) button.textContent = left > 0 ? "退出 sudo 模式" : "进入 sudo 模式";
  }

  function sudoChipEl() { return $("#sudoChip"); }

  $("#sudoChip").addEventListener("click", function () {
    if (SUDO.pop) closeSudoPop();
    else openSudoPop();
  });

  // The account page offers the switch directly, so the mode can be entered on
  // purpose instead of only as the answer to a refused action.
  $("#sudoBtn").addEventListener("click", function () {
    if (sudoActive()) return quitSudo();
    askSudoPassword().then(function () {
      PX.toast("已进入 sudo 模式");
    }).catch(fail);
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

  function askSudoPassword(what) {
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
        '<label class="sudo-prompt-label" for="sudoPass">当前账号密码</label>' +
        '<input id="sudoPass" type="password" autocomplete="current-password" ' +
        'maxlength="128">' +
        '<p class="err sudo-prompt-err" id="sudoErr"></p>' +
        '<div class="sudo-prompt-foot">' +
        '<button class="btn" type="button" id="sudoCancel">取消</button>' +
        '<button class="btn primary" type="button" id="sudoEnter">进入 sudo 模式</button>' +
        "</div></div>");
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
  function apiSudo(path, options, what) {
    return PX.api(path, options).catch(function (e) {
      if (!e.sudo) throw e;
      return askSudoPassword(what).then(function () {
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
      '" target="_blank" rel="noopener">' + ICON_GEAR + "活动设置 ↗</a>" +
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
  function confirmHighRisk(title, note, label, danger, run) {
    if (!sudoActive()) return run();
    openOverlay("confirm", title,
      '<p class="page-sub">' + note + "</p>"
      + '<div class="row" style="margin-top:18px;justify-content:flex-end">'
      + '<button class="btn" type="button" data-close-overlay="1">取消</button>'
      + '<button class="btn ' + (danger ? "danger" : "primary") + '" type="button" '
      + 'id="confirmGo">' + label + "</button></div>");
    $("#confirmGo").addEventListener("click", function () {
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
      function () {
        apiSudo("/event/reclaim", { method: "POST", body: { seq: Number(seq) } },
          "回收站内资源")
          .then(function (d) {
            PX.toast("已回收 " + ((d && d.shortlinks) || 0) + " 条短链");
            return reloadAll();
          })
          .catch(fail);
      });
  }

  /* 已归档活动的三个提权动作：下载完整数据、取消归档、彻底删除 */
  /* 别人的活动：强制归档也是破坏性操作，只在活动管理页出现 */
  function forceArchive(seq) {
    confirmHighRisk("强制归档", "确认继续？", "强制归档", true, function () {
      apiSudo("/event/force_archive", { method: "POST", body: { seq: Number(seq) } },
        "强制归档活动")
        .then(function () {
          PX.toast("已归档");
          return reloadAll();
        })
        .catch(fail);
    });
  }

  function archivedDownload(seq) {
    var w = window.open((PX.panelRoot || "") + "/api/panel/event/download?seq=" + seq,
      "_blank");
    if (w) PX.toast("正在准备下载");
  }

  function archivedRestore(seq) {
    confirmHighRisk("取消归档", "确认取消归档？",
      "取消归档", false, function () {
        apiSudo("/event/unarchive", { method: "POST", body: { seq: Number(seq) } },
          "取消归档")
          .then(function () {
            PX.toast("已取消归档");
            return reloadAll();
          })
          .catch(fail);
      });
  }

  function archivedDelete(seq) {
    confirmHighRisk("彻底删除归档的活动",
      "删除后不可恢复。确认继续？",
      "彻底删除", true,
      function () {
        apiSudo("/event/delete", { method: "POST", body: { seq: Number(seq) } },
          "彻底删除归档的活动")
          .then(function () {
            PX.toast("已删除");
            return loadArchived(true);
          })
          .catch(function (e) {
            if (e && e.status === 404) {
              // 别处已经删掉了：刷新一遍列表，别让人对着一条已经不存在的记录再点
              PX.toast("这场活动已经不在了", true);
              loadArchived(true);
              return;
            }
            fail(e);
          });
      });
  }

  function rotateEvent(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    closeCardMenu();
    confirmHighRisk("重置访问地址",
      "确认重新生成？" + (e ? "<br>活动：" + PX.esc(eventTitle(e)) : ""),
      "重新生成", false,
      function () {
        apiSudo("/event/rotate", { method: "POST", body: { seq: Number(seq) } }, "重置访问地址")
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
    confirmHighRisk("归档活动",
      "确认归档？"
      + (e ? "<br>存档：" + PX.esc(eventTitle(e)) : ""),
      "归档", true,
      function () {
        apiSudo("/event/archive", { method: "POST", body: { seq: Number(seq) } }, "归档活动")
          .then(function () {
            PX.toast("已归档");
            return reloadAll();
          })
          .catch(fail);
      });
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
      '<span class="notice-meta"><span class="notice-level">服务</span>' +
      mute + "</span>" +
      '<span class="notice-title">' + PX.esc(it.title) + "</span>" +
      (it.body ? '<span class="notice-body">' + PX.esc(it.body) + "</span>" : "") +
      "</div>";
  }

  function loadNotices() {
    return PX.api("/notices")
      .then(function (d) {
        S.notices = d.notices || [];
        S.noticeUnread = d.unread || 0;
        drawBell();
        drawNoticeList();
        maybeShowAlert();
      })
      .catch(function () { /* 通知读不到不该影响主流程 */ });
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

  function drawNoticeList() {
    var box = $("#noticeList");
    if (!box) return;
    var foot = $("#noticeFoot");
    if (foot) foot.hidden = !S.notices.some(function (n) { return n.unread; });
    var svc = servicesShown();
    if (!S.notices.length && !svc.length) {
      box.innerHTML = '<p class="notice-empty">还没有通知</p>';
      return;
    }
    box.innerHTML = svc.map(drawServiceRow).join("") +
      S.notices.map(function (n) {
      return '<button class="notice-item ' + PX.esc(n.level) +
        (n.unread ? " unread" : "") + '" type="button" data-notice="' + n.id +
        '" data-object="' + PX.esc(n.object || "") + '">' +
        '<span class="notice-meta"><span class="notice-level">' +
        PX.esc(n.levelLabel) + "</span>" + PX.esc(PX.whenExact(n.ts)) + "</span>" +
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
    }
    var parts = ["/rosters?scope=" + (S.rosterScope || "mine"),
                 "offset=" + (more ? S.rosters.length : 0),
                 "limit=" + ROSTER_PAGE];
    if (S.rosterFilter) parts.push("q=" + encodeURIComponent(S.rosterFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.rosters = (more ? S.rosters : []).concat(d.rosters || []);
        S.rosterTotal = d.total || 0;
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
    }).join("") : emptyRow(5, S.rosterTotal === 0
      ? "还没有名单"
      : "正在读取…");

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
    }
    var parts = ["/roster/entries?id=" + S.roster.id,
                 "offset=" + (more ? S.rosterEntries.length : 0),
                 "limit=" + ENTRY_PAGE];
    if (S.entryFilter) parts.push("q=" + encodeURIComponent(S.entryFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.rosterEntries = (more ? S.rosterEntries : []).concat(d.entries || []);
        S.entryTotal = d.total || 0;
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
          ? '<input class="search" id="rosterGrantWho" type="text" ' +
            'placeholder="登录名" autocomplete="off" style="max-width:220px">' +
            '<button class="btn sm" type="button" id="rosterGrantBtn">授予写入权限</button>'
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

  function rosterGrant(who) {
    PX.api("/roster/grant", { method: "POST",
      body: { id: S.roster.id, username: who } })
      .then(function () {
        PX.toast("已授予写入权限");
        return loadRoster(S.roster.id).then(drawRoster);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
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
            e.personId + '">移出</button>'
          : '<span class="hint">只读</span>') + "</td></tr>";
    }).join("") : emptyRow(8, S.entryTotal === 0
      ? "还没有成员"
      : "没有匹配的成员");
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
    return PX.api("/screen_packs")
      .then(function (d) {
        S.screenPacks = d.packs || [];
        S.packManage = !!d.canManage;
        S.packLimits = d.limits || null;
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
    }).join("") : '<p class="hint">暂无可用屏幕</p>';
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
    }
    var parts = ["/signups?offset=" + (more ? S.signups.length : 0),
                 "limit=" + SIGNUP_PAGE];
    if (S.signupFilter) parts.push("q=" + encodeURIComponent(S.signupFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.signups = (more ? S.signups : []).concat(d.signups || []);
        S.signupTotal = d.total || 0;
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
    }).join("") : emptyRow(5, S.signupTotal === 0 ? "还没有报名" : "正在读取…");
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
    }
    var parts = ["/signup/entries?id=" + S.signup.id,
                 "offset=" + (more ? S.signupEntries.length : 0),
                 "limit=" + SIGNUP_ENTRY_PAGE];
    if (S.signupEntryFilter) parts.push("q=" + encodeURIComponent(S.signupEntryFilter));
    return PX.api(parts.join("&"))
      .then(function (d) {
        S.signupEntries = (more ? S.signupEntries : []).concat(d.entries || []);
        S.signupEntryTotal = d.total || 0;
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
    }).join("") : emptyRow(6, S.signupEntryTotal === 0 ? "还没有人报名" : "没有匹配的记录");
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
    return PX.api("/list_approvals")
      .then(function (d) {
        S.approvals = d.pending || [];
        S.myAsks = d.asks || [];
        S.approvalsCount = d.count || 0;
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
      crumb.setAttribute("href", parent.href);
      crumb.textContent = parent.label;
      return;
    }
    if (S.explicitBack) {
      crumb.setAttribute("href", "#" + S.explicitBack);
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
      : emptyRow(4, "暂无待审批的申请");
    $("#myAsksTable tbody").innerHTML = S.myAsks.length
      ? S.myAsks.map(function (a) {
          return "<tr>" +
            "<td><b>" + PX.esc(a.name) + "</b></td>" +
            "<td>" + PX.esc(a.ownerName || "—") + "</td>" +
            '<td class="mono">' + PX.whenExact(a.askedAt) + "</td>" +
            '<td class="actions"><span class="hint">等待确认</span></td></tr>';
        }).join("")
      : emptyRow(4, "暂无等待确认的申请");
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
      (row ? '<button class="btn danger" type="button" id="rosterDelete">删除名单</button>' : "") +
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
    var del = $("#rosterDelete");
    if (del) {
      del.addEventListener("click", function () {
        if (!window.confirm("删除「" + r.name + "」？名单里的人会一起删掉，人员记录保留。")) return;
        PX.api("/roster/delete", { method: "POST", body: { id: r.id } })
          .then(function () {
            closeOverlay();
            PX.toast("已删除");
            S.roster = null;
            location.hash = "#/rosters";
          })
          .catch(function (e) { PX.toast(PX.esc(e.message), true); });
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
    $("#denied").hidden = false;
    $("#siteFoot").hidden = true;
    $$("[data-nav]").forEach(function (a) { a.removeAttribute("aria-current"); });
    $$(".nav-group").forEach(function (g) { g.classList.remove("has-current"); });
    closePane();
    var el = $("#content");
    if (el) el.scrollTop = 0;
  }

  var guardBypass = false;

  function onRoute() {
    var prevRoute = S.route;                 // 上一页有没有返回键，全靠它
    var wanted = routeFromHash() || FALLBACK_ROUTE;
    // 换一页就把会话抽屉收回去：返回键回到别处时，它不该还浮在上面
    closeAssistantDrawer();
    if (!guardBypass && S.route === "new-event" && wanted !== "new-event" && S.dirty) {
      // put the address back where the form is, then ask what to do with it
      var here = "#/" + S.route + (S.templateId ? "/" + encodeURIComponent(S.templateId) : "");
      location.replace(here);
      askDraftChoice(wanted);
      return;
    }
    if (!allowed(wanted)) {
      // Reachable by typing the hash; say so instead of silently moving them.
      showDenied(wanted);
      return;
    }
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
    $("#siteFoot").hidden = (wanted === "assistant");
    // 助手页不滚整页：标题与输入框固定，只有中间的对话滚
    var main = $("#content");
    if (main) main.classList.toggle("is-chat", wanted === "assistant");

    // a child route keeps its group expanded
    var parent = ROUTES[wanted].group;
    if (parent) S.openGroups[parent] = true;
    syncGroups();

    var prevHadBack = routeHasBack(prevRoute);
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
    var current = document.querySelector('#sidebar [data-nav][aria-current="page"]');
    if (current) {
      $$('#sidebar [data-nav], #sidebar [data-toggle]').forEach(function (el) {
        el.tabIndex = -1;
      });
      current.tabIndex = 0;
    }

    closePane();
    $("#content").scrollTop = 0;
    draw();
    syncCrumb(wanted);
    animateCrumb(wanted, prevHadBack);
    scheduleFitTables();
    loadNotices();
    loadService();
    syncSudo();
    if (wanted === "server" || wanted === "overview") refreshHost();
    if (wanted === "audit" && canAudit() && !S.audit.length) loadAudit().then(draw);
    if (wanted === "rosters") {
      S.rosterScope = S.rosterScope || "joined";
      loadRosters(false).then(drawRosters);
    }
    if (wanted === "signups") {
      loadSignups(false).then(drawSignups);
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
    if (wanted === "screens") loadScreenPacks().then(drawScreens);
    if (wanted === "account") loadTokens().then(drawTokens);
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
      case "all-events": return drawAllEvents();
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
      : '<div class="empty">还没有创建过活动</div>';

    var joined = joinedEvents();
    $("#joinedEvents").innerHTML = joined.length
      ? joined.map(function (e) { return eventCard(e); }).join("")
      : '<div class="empty">还没有参与别人创建的活动</div>';
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
      : '<div class="empty">' + PX.esc(empty) + "</div>";
  }

  function drawTemplates() {
    // importing brings content into the installation: only the station master
    var importBtn = $("#presetImportBtn");
    if (importBtn) importBtn.hidden = !isOwner();
    return PX.api("/templates").then(function (d) {
      S.templates = d.presets || [];
      S.visibilityOptions = d.visibility || [];
      var fav = S.templates.filter(function (p) { return p.fav; });
      var mine = S.templates.filter(function (p) { return p.mine && !p.builtin; });
      // the square is everything shared, whether or not it is also starred
      var square = S.templates.filter(function (p) {
        return p.builtin || p.visibility === "public" || p.visibility === "org";
      });
      presetShelf("#presetFavorites", fav, "fav", "还没有收藏的预设");
      presetShelf("#presetMine", mine, "mine", "还没有创建的预设");
      presetShelf("#presetSquare", square, "square", "还没有可用的预设");
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
    PX.api("/preset/favorite", { method: "POST", body: { id: id, on: on } })
      .then(function () { return loadTemplates().then(drawTemplates); })
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
    return PX.api("/modules").then(function (d) {
      S.modules = d.modules || [];
      $("#moduleTable tbody").innerHTML = S.modules.map(function (m) {
        return "<tr" + (m.enabled ? "" : ' class="muted-row"') + ">" +
          "<td><b>" + PX.esc(m.name) + "</b><br>" +
          '<span class="mono">' + PX.esc(m.id) + "</span></td>" +
          '<td class="mono">' + PX.esc(m.version) + "</td>" +
          "<td>" + PX.esc(m.author || "—") + "</td>" +
          '<td class="actions">' +
          '<button class="btn sm' + (m.enabled ? " danger" : "") +
          '" type="button" data-module-toggle="' + PX.esc(m.id) +
          '" data-on="' + (m.enabled ? "0" : "1") + '">' +
          (m.enabled ? "禁用" : "启用") + "</button></td></tr>";
      }).join("") || emptyRow(4, "没有已安装的模块");
    }).catch(function (e) {
      if (e.status === 403) return showDenied("modules");
      PX.toast(PX.esc(e.message), true);
    });
  }

  function toggleModule(id, on) {
    PX.api("/module/toggle", { method: "POST", body: { id: id, enabled: on } })
      .then(function () {
        PX.toast(on ? "已启用" : "已禁用");
        return loadTemplates(true).then(drawModules);
      })
      .catch(function (e) { PX.toast(PX.esc(e.message), true); });
  }

  function loadTemplates(force) {
    if (S.templates.length && !force) return Promise.resolve();
    return PX.api("/templates").then(function (d) {
      S.templates = d.presets || d.templates || [];
      S.visibilityOptions = d.visibility || [];
    }).catch(function () { S.templates = []; });
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

  function markDirty() { S.dirty = true; }

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
        S.invited = (payload.members || []).filter(function (id) {
          return S.accounts.some(function (a) { return a.id === id; });
        });
        renderInviteChips();
        var rows = $$("[data-show-when]", $("#kindFields"));
        rows.forEach(function (row) {
          var driver = $('[data-kind-field="' + row.getAttribute("data-show-when") + '"]');
          row.hidden = !driver || String(driver.value) !== row.getAttribute("data-show-is");
        });
        PX.toast("已恢复上次的草稿");
        return true;
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
    $("#kindForm").addEventListener("input", markDirty);
    $("#kindForm").addEventListener("change", markDirty);
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
    PX.api("/events/create", { method: "POST", body: body })
      .then(function (d) {
        dropDraft();          // the draft became an activity
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
    }).join("") : emptyRow(6, S.userFilter.trim()
      ? "没有匹配的账号" : "还没有别的账号");
    var more = $("#usersMore");
    if (more) {
      var left = Math.max(0, (S.accountsTotal || 0) - S.accounts.length);
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "再显示 " + Math.min(50, left) + " 个" : "加载更多";
    }
    var count = $("#usersCount");
    if (count) count.textContent = S.accountsTotal ? "共 " + S.accountsTotal + " 个账号" : "";
  }

  function loadAccountsPage(reset) {
    if (S.accountsLoading) return Promise.resolve();
    if (reset) {
      S.accounts = [];
      S.accountsTotal = 0;
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
      ["用户名", a.username],
      ["称呼", a.name || "—"],
      ["角色", a.roleLabel],
      ["状态", a.statusLabel],
      ["存档配额", quotaText(a)],
      ["注册时间", PX.when(a.createdAt)],
      ["最近登录", PX.when(a.lastLogin)]
    ];
    if (a.banReason) rows.push(["封禁原因", a.banReason]);
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
        drawArchived();
      });
  }

  function drawArchived() {
    var body = $("#archivedTable tbody");
    if (!body) return;
    body.innerHTML = S.archived.length ? S.archived.map(function (e) {
      // 已归档的活动已经归平台管：下载、取消归档、彻底删除都在这一行的操作列里
      return "<tr>" +
        "<td><b>" + PX.esc(eventTitle(e)) + "</b></td>" +
        "<td>" + PX.esc(e.ownerName || "—") + "</td>" +
        '<td class="mono">' + PX.whenExact(e.archivedAt) + "</td>" +
        '<td class="mono">' + PX.esc(shortToken(e.id)) + "</td>" +
        '<td class="actions">' +
        '<button class="btn sm" type="button" data-arch-download="' + e.seq + '">下载</button>' +
        '<button class="btn sm" type="button" data-arch-restore="' + e.seq + '">取消归档</button>' +
        '<button class="btn sm danger" type="button" data-arch-delete="' + e.seq +
        '">彻底删除</button></td></tr>' ;
    }).join("") : emptyRow(5, S.archivedTotal === 0
      ? "还没有归档的活动"
      : "正在读取…");
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
      return "<tr>" +
        "<td><b>" + PX.esc(eventTitle(e)) + "</b></td>" +
        '<td class="mono" style="font-size:.78rem" title="' + PX.esc(e.id) + '">' +
        PX.esc(shortToken(e.id)) + "</td>" +
        "<td>" + PX.esc(e.ownerName || "—") + "</td>" +
        '<td class="mono">' + PX.when(e.createdAt) + "</td>" +
        '<td class="actions">' +
        '<button class="btn sm" type="button" data-reclaim="' + e.seq + '">' +
        ICON_RECLAIM + '回收资源</button>' +
        '<button class="btn sm danger" type="button" data-force-archive="' + e.seq +
        '">强制归档</button></td>' +
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
    var u = S.users;
    $("#srvUsers").textContent = u ? (u.online + " / " + u.registered) : "–";

    var rows = [];
    if (h) {
      rows.push(["主机", h.host || "—"]);
      rows.push(["版本", h.version || "—"]);
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
        "<td>" + PX.esc(r.actor || "") + "</td>" +
        "<td>" + PX.esc(r.action) + "</td>" +
        "<td>" + PX.esc(r.target || "") + "</td>" +
        '<td class="wrap">' + PX.esc(r.detail || "") + "</td></tr>";
    }).join("") : emptyRow(5, S.auditFilter ? "没有匹配的记录" : "还没有记录");
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
    return PX.api("/tokens")
      .then(function (d) {
        S.tokens = d.tokens || [];
        S.tokenMeta = { scopes: d.scopes || [], labels: d.scopeLabels || {} };
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
    }).join("") : emptyRow(6, "还没有令牌");
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
      '<p class="hint">取一个以后能认出用途的名字</p></div>' +
      '<div class="field"><label>权限</label>' + rows +
      '<p class="hint">账号本身的权限仍然生效，令牌只能在它之下再收窄</p></div>' +
      '<div class="field"><label for="tkDays">有效期</label>' +
      '<select id="tkDays">' +
      '<option value="0">永不过期</option>' +
      '<option value="30">30 天</option>' +
      '<option value="90">90 天</option>' +
      '<option value="365">一年</option></select></div>' +
      '<div class="row" style="margin-top:18px;justify-content:flex-end">' +
      '<button class="btn" type="button" data-close-overlay="1">取消</button>' +
      '<button class="btn primary" type="button" id="tkGo">创建</button></div>');
    $("#tkName").focus();
    $("#tkGo").addEventListener("click", createToken);
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

  function drawAppearance() {
    var pref = document.documentElement.getAttribute("data-theme-pref") || "auto";
    $$("#themeSeg button").forEach(function (b) {
      b.setAttribute("aria-checked",
        b.getAttribute("data-theme-pref") === pref ? "true" : "false");
    });
  }

  /* ---------------------------------------------------------------- 运维助手 --- */
  /* 对话按会话存进数据库（有配额），地址里带着会话 id；
     模型配置在账号上，密钥只写不读——画面上永远只有"已配置"。 */
  var ASSISTANT_ASKS = [
    "我最近的活动为什么没人报名？帮我看看名单与报名的情况",
    "平台现在有多少人、签到了几个？",
    "帮我查一下某个学号在不在某份名单里",
    "最近谁改过东西？有没有异常"
  ];

  var STEP_LABEL = {
    list_events: "看了活动列表",
    event_board: "看了活动数据",
    list_rosters: "看了名单列表",
    roster_entries: "看了名单成员",
    list_signups: "看了报名列表",
    signup_entries: "看了报名记录",
    search_people: "查了人员库",
    recent_audit: "看了审计日志",
    host_stats: "看了服务器状态"
  };

  function stepLabel(step) {
    var label = STEP_LABEL[step.tool] || step.tool;
    return step.ok ? label : label + "（未获准）";
  }

  function loadAssistant() {
    return PX.api("/assistant")
      .then(function (d) {
        S.assistantCfg = d.config || { ready: false };
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
      '<p class="hint">端点需兼容 OpenAI 的 <code>/chat/completions</code>；' +
      '地址与模型留空时使用默认值</p>' +
      '<label class="switch" id="asResumeRow" style="margin-top:14px">' +
      '<input type="checkbox" id="asResume"' +
      ((cfg.resume === false) ? "" : " checked") + '>' +
      '<span class="track"></span>' +
      '<span class="switch-text">进入时接着上次的会话</span></label>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" id="asClearKey" type="button">清除密钥</button>' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn primary" id="asSave" type="button">保存</button></div>');
    showKeyMask(cfg);
    $("#asSave").addEventListener("click", saveAssistantConfig);
    $("#asModels").addEventListener("click", fetchAssistantModels);
    $("#asModelList").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-model]");
      if (btn) pickAssistantModel(btn.getAttribute("data-model"));
    });
    $("#asClearKey").addEventListener("click", function () {
      PX.api("/assistant/config", { method: "POST", body: { clear: true } })
        .then(function (d) {
          S.assistantCfg = d.config || S.assistantCfg;
          showKeyMask(S.assistantCfg);
          drawAssistant();
          PX.toast("密钥已清除");
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
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
        if (!title) return;
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
    return PX.api("/assistant/session/save", {
      method: "POST",
      body: { id: S.assistantSessionId || 0, title: currentTitle(), messages: msgs }
    }).then(function (d) {
      var fresh = !S.assistantSessionId;
      S.assistantSessionId = d.id;
      S.assistantUsed = d.used;
      S.assistantQuota = d.quota;
      try { localStorage.setItem("as:lastSession", String(d.id)); } catch (e) {}
      if (fresh) setSessionInHash(d.id);
      setSessionName(currentTitle());
      drawAssistantQuota();
      if (fresh) autoTitleSession(d.id);
    }).catch(function (e) {
      // 满了就说清楚，别默默丢掉用户的对话
      if (e.status === 413) PX.toast(PX.esc(e.message), true);
    });
  }

  function drawAssistantQuota() {
    var sub = $("#assistantSub");
    if (!sub) return;
    var cfg = S.assistantCfg || {};
    if (!cfg.ready) {
      sub.textContent = "还没有密钥：点页面右上角的模型设置按钮填写，地址与模型有默认值";
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
    if (location.hash !== want) location.replace(want);
  }

  function openAssistantSession(id) {
    S.assistantSessionId = id;
    S.assistantMsgs = [];
    drawAssistant();
    if (!id) {
      drawAssistantQuota();
      setSessionName("新会话");
      return Promise.resolve();
    }
    return PX.api("/assistant/session?id=" + id).then(function (r) {
      S.assistantMsgs = (r.session && r.session.messages) || [];
      setSessionName((r.session && r.session.title) || "会话");
      try { localStorage.setItem("as:lastSession", String(id)); } catch (e) {}
      drawAssistant();
      drawAssistantQuota();
    }).catch(function () {
      S.assistantSessionId = 0;
      drawAssistantQuota();
    });
  }

  function setSessionName(text) {
    var el = $("#asSessionName");
    if (el) el.textContent = text || "新会话";
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
      drawAssistantDrawer(d);
      shell.classList.add("as-drawer-open");
      syncScrim();
      $("#asDrawerClose").focus();
    }).catch(function (e) { PX.toast(PX.esc(e.message), true); });
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
    S.assistantQuota = d.quota;
    S.assistantUsed = d.used;
    var rows = d.sessions || [];
    S.assistantList = rows;
    var body = $("#asDrawerBody");
    if (body) {
      body.innerHTML = rows.length ? '<div class="as-slist">' + rows.map(function (s) {
        // 属性都带 as- 前缀：页面里那套 data-rename / data-del 是活动的，别撞上
        return '<div class="as-srow' +
          (s.id === S.assistantSessionId ? " on" : "") + '" data-sid="' + s.id + '">' +
          '<button class="as-sopen" type="button" data-as-open="' + s.id + '">' +
          PX.esc(s.title) + '<span>' + PX.when(s.updatedAt) + " · " +
          Math.max(1, Math.round(s.size / 1024)) + " KB</span></button>" +
          '<button class="as-sicon" type="button" data-as-rename="' + s.id +
          '" title="重命名" aria-label="重命名">' + ICON_PENCIL + "</button>" +
          '<button class="as-sicon danger" type="button" data-as-del="' + s.id +
          '" title="删除" aria-label="删除">' + ICON_TRASH + "</button>" +
          "</div>";
      }).join("") + "</div>"
        : '<p class="page-sub">还没有会话。发一条消息就会存下来。</p>';
    }
    var foot = $("#asDrawerFoot");
    if (foot) {
      foot.textContent = "存档 " + Math.round(d.used / 1024) + " / " +
        Math.round(d.quota / 1024) + " KB";
    }
    drawAssistantQuota();
  }

  function assistantDrawerClick(e) {
    var open = e.target.closest("[data-as-open]");
    if (open) {
      var id = Number(open.getAttribute("data-as-open"));
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
      '<input id="asRename" type="text" maxlength="60" value="' +
      PX.esc(row.title || "") + '"></div>' +
      '<div class="row" style="margin-top:16px;justify-content:flex-end">' +
      '<button class="btn" data-close-overlay="1" type="button">取消</button>' +
      '<button class="btn primary" id="asRenameGo" type="button">保存</button></div>');
    $("#asRenameGo").addEventListener("click", function () {
      var name = ($("#asRename").value || "").trim();
      if (!name) { PX.toast("请填一个名称", true); return; }
      PX.api("/assistant/session/rename", { method: "POST", body: { id: id, title: name } })
        .then(function () {
          closeOverlay();
          if (S.assistantSessionId === id) setSessionName(name);
          refreshAssistantDrawer();
          PX.toast("已重命名");
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
    $("#asRename").focus();
  }

  function deleteSession(id) {
    openOverlay("as-del", "删除会话",
      '<p class="page-sub">删掉之后这段对话就找不回来了。</p>' +
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
          refreshAssistantDrawer();
          PX.toast("已删除");
        })
        .catch(function (e) { PX.toast(PX.esc(e.message), true); });
    });
  }

  function exportAssistantSession() {
    if (!S.assistantSessionId) {
      PX.toast("这段对话还没有存档", true);
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
      sub.textContent = "还没有密钥：点页面右上角的模型设置按钮填写，地址与模型有默认值";
    }
    var asks = $("#assistantAsks");
    if (asks && !asks.children.length) {
      asks.innerHTML = ASSISTANT_ASKS.map(function (q) {
        return '<button class="assistant-ask" type="button">' + PX.esc(q) + "</button>";
      }).join("");
    }
    var empty = $("#assistantEmpty");
    if (empty) empty.hidden = (S.assistantMsgs || []).length > 0;
    var keep = $("#asKeepNote");
    if (keep) {
      keep.textContent = ((S.assistantCfg || {}).resume === false)
        ? "这段对话不存档，刷新后即消失。"
        : "对话会存档在账户上，下次回来接着聊。";
    }
    drawAssistantLog();
    growAssistantBox();
  }

  function drawAssistantLog() {
    var log = $("#assistantLog");
    if (!log) return;
    log.innerHTML = (S.assistantMsgs || []).map(function (m) {
      if (m.role === "user") {
        return '<div class="as-msg as-user"><div class="as-text">' +
          PX.esc(m.content).replace(/\n/g, "<br>") + "</div></div>";
      }
      var steps = (m.steps || []).map(function (s) {
        return '<span class="as-step' + (s.ok ? "" : " bad") + '">' +
          PX.esc(stepLabel(s)) + "</span>";
      }).join("");
      return '<div class="as-msg as-bot">' +
        (steps ? '<div class="as-steps">' + steps + "</div>" : "") +
        '<div class="as-text md">' + PX.md(m.content || "") + "</div></div>";
    }).join("");
    scrollAssistant();
  }

  function scrollAssistant() {
    var box = $("#assistantScroll");
    if (box) box.scrollTop = box.scrollHeight;
  }

  /* 正在思考：动态的三个点，写在对话流末尾，回答落地时移除 */
  function showThinking() {
    var log = $("#assistantLog");
    if (!log || $("#asThinking")) return;
    var el = document.createElement("div");
    el.className = "as-msg as-bot";
    el.id = "asThinking";
    el.innerHTML = '<span class="as-thinking">正在思考<i></i><i></i><i></i></span>';
    log.appendChild(el);
    scrollAssistant();
  }

  function hideThinking() {
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
    S.assistantMsgs.push({ role: "user", content: question });
    drawAssistant();
    var box = $("#assistantText");
    box.value = "";
    growAssistantBox();
    setAssistantRunning(true);
    showThinking();
    var controller = window.AbortController ? new AbortController() : null;
    S.assistantAbort = controller;
    PX.api("/assistant/chat", {
      method: "POST",
      body: { messages: S.assistantMsgs },
      signal: controller ? controller.signal : undefined
    }).then(function (d) {
      S.assistantMsgs.push({ role: "assistant", content: d.reply || "",
                             steps: d.steps || [] });
    }).catch(function (e) {
      if (e && (e.name === "AbortError" || /abort/i.test(String(e.message)))) {
        S.assistantMsgs.push({ role: "assistant", content: "（已停止）", steps: [] });
      } else {
        S.assistantMsgs.push({ role: "assistant", content: "出错了：" + e.message,
                               steps: [] });
      }
    }).then(function () {
      S.assistantAbort = null;
      hideThinking();
      setAssistantRunning(false);
      drawAssistant();
      saveAssistantSession();
    });
  }

  function stopAssistant() {
    if (S.assistantAbort) {
      try { S.assistantAbort.abort(); } catch (e) { /* 已经结束了 */ }
      S.assistantAbort = null;
    }
  }

  function saveAssistantConfig() {
    var body = { base: $("#asBase").value.trim(), model: $("#asModel").value.trim(),
                 resume: !!($("#asResume") && $("#asResume").checked) };
    // 框里还是那串星号 = 没动过密钥，别把已有的覆盖成空
    if (!keyFieldMasked()) body.key = $("#asKey").value;
    PX.api("/assistant/config", { method: "POST", body: body })
      .then(function (d) {
        S.assistantCfg = d.config || S.assistantCfg;
        closeOverlay();
        drawAssistant();
        PX.toast(d.config && d.config.ready ? "已保存" : "已保存，还差密钥", !d.config.ready);
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
        applyHost(d && d.host ? d.host : null);
      })
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
    // 账号清单是超管才看得到的东西：普通管理员打开面板时不去问它
    if (!isSuper()) {
      S.accounts = [];
      S.accountsTotal = 0;
      renderIdentity();
      applyRoleVisibility();
      syncPendingBadge();
      return Promise.resolve();
    }
    return PX.api("/accounts?limit=50").then(function (d) {
      S.me = d.me || S.me;
      S.accounts = d.accounts || [];
      S.accountsTotal = d.total || S.accounts.length;
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
    return PX.api("/events").then(function (d) { S.events = d.events || []; })
      .catch(function () { S.events = []; });
  }

  function loadAudit(q) {
    if (!canAudit()) return Promise.resolve();
    S.auditFilter = (q === undefined) ? (S.auditFilter || "") : q;
    var query = "?limit=60" +
      (S.auditFilter ? "&q=" + encodeURIComponent(S.auditFilter) : "");
    return PX.api("/audit" + query).then(function (d) {
      S.audit = d.audit || [];
      S.auditTotal = d.total || 0;
    }).catch(function () { S.audit = []; S.auditTotal = 0; });
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
    var dark = pref === "dark" ||
      (pref === "auto" && window.matchMedia &&
       window.matchMedia("(prefers-color-scheme: dark)").matches);
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
    if (changed && !reducedMotion() && document.startViewTransition) {
      try {
        document.startViewTransition(apply);
        return;
      } catch (err) {
        // fall through to the direct path
      }
    }
    apply();
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

    var quota = t.closest("[data-quota]");
    if (quota) return quotaDialog(Number(quota.getAttribute("data-quota")));

    var toggle = t.closest("[data-toggle]");
    if (toggle) return onGroupHead(toggle.getAttribute("data-toggle"));

    var tplTile = t.closest("[data-template]");
    if (tplTile) return go("new-event/" + tplTile.getAttribute("data-template"));

    var invite = t.closest("[data-invite]");
    if (invite) return toggleInvite(Number(invite.getAttribute("data-invite")));

    var uninvite = t.closest("[data-uninvite]");
    if (uninvite) return toggleInvite(Number(uninvite.getAttribute("data-uninvite")));

    var banBtn = t.closest("[data-ban]");
    if (banBtn) return confirmBan(Number(banBtn.getAttribute("data-ban")));

    var banYes = t.closest("[data-ban-confirm]");
    if (banYes) return doBan(banYes.getAttribute("data-ban-confirm"));

    var rotateBtn = t.closest("[data-rotate]");
    if (rotateBtn) return rotateEvent(rotateBtn.getAttribute("data-rotate"));

    var archiveBtn = t.closest("[data-archive]");
    if (archiveBtn) return archiveEvent(archiveBtn.getAttribute("data-archive"));
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
    if (SUDO.pop && e.target.closest &&
        !e.target.closest(".sudo-pop") && !e.target.closest("#sudoChip")) {
      closeSudoPop();
    }
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
  bindLiveSearch("#auditSearch", "#auditSearchClear", function (value) {
    loadAudit(value).then(drawAudit);
  });
  $("#usersMore").addEventListener("click", function () { loadAccountsPage(false); });
  $("#auditMore").addEventListener("click", loadMoreAudit);

  $("#pfSave").addEventListener("click", saveProfile);
  // 运维助手：输入框常驻底部，设置与全屏编辑都在框里
  $("#asSettings").addEventListener("click", assistantConfigDialog);
  $("#asExpand").addEventListener("click", assistantFullscreenDialog);
  $("#assistantReset").addEventListener("click", function () {
    location.hash = "#/assistant";      // 新会话：点开就是初始页，发消息才落库
    openAssistantSession(0);
  });
  $("#asSessionBar").addEventListener("click", openAssistantDrawer);
  $("#asDrawerClose").addEventListener("click", closeAssistantDrawer);
  $("#asDrawerBody").addEventListener("click", assistantDrawerClick);
  $("#asExport").addEventListener("click", exportAssistantSession);
  $("#assistantAsks").addEventListener("click", function (e) {
    var btn = e.target.closest(".assistant-ask");
    if (btn) sendAssistant(btn.textContent);
  });
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
  $("#createEventBtn").addEventListener("click", newEvent);

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
  $("#signupBack").addEventListener("click", function () {
    location.hash = "#/signups";
  });
  $("#rosterBack").addEventListener("click", function () {
    location.hash = "#/rosters";
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
    if (t.id === "rosterGrantBtn") {
      var who = $("#rosterGrantWho").value.trim();
      if (!who) { PX.toast("请填写登录名", true); return; }
      rosterGrant(who);
      return;
    }
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
      rosterEntryAction("drop", Number(drop));
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
      if (qrMenuEl) return closeQrMenu();
      if (cardMenuEl) return closeCardMenu();
      if (!$("#overlay").hidden) return closeOverlay();
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
    bindUpdateBar();
    PX.api("/me").then(function (d) {
      if (!d.authed) {
        location.href = PX.PREFIX + "/login";
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
