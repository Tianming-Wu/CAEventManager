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
    if (e.target === $("#overlay")) closeOverlay();   // click the scrim
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
      PX.notice("另一个会话已经改过这里，本次更改没有生效", "warn");
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
          "重命名活动</button>"
        : "") +
      '<button type="button" role="menuitem" data-copy="' + PX.esc(e.url) + '">' +
      "复制访问链接</button>" +
      '<button type="button" role="menuitem" data-save-preset="' + e.seq + '">' +
      "保存为预设</button>" +
      '<div class="menu-sep"></div>' +
      '<a role="menuitem" class="menu-link" href="' + PX.esc(e.admin) +
      '" target="_blank" rel="noopener">活动设置 ↗</a>' +
      (e.role === "owner"
        ? '<div class="menu-sep"></div>' +
          '<button type="button" role="menuitem" data-rotate="' + e.seq + '">' +
          "重置访问地址</button>" +
          '<button type="button" role="menuitem" class="danger" ' +
          'data-archive="' + e.seq + '">存档活动</button>'
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

  function rotateEvent(seq) {
    var e = S.events.filter(function (x) { return String(x.seq) === String(seq); })[0];
    closeCardMenu();
    confirmHighRisk("重置访问地址",
      "会生成一个新的 token，之前所有指向该活动的链接立即失效。"
      + (e ? "<br>活动：" + PX.esc(eventTitle(e)) : ""),
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
    confirmHighRisk("存档活动",
      "存档后这个活动不再出现在列表里，它的链接也会失效。数据保留，不会被删除。"
      + (e ? "<br>存档：" + PX.esc(eventTitle(e)) : ""),
      "存档", true,
      function () {
        apiSudo("/event/archive", { method: "POST", body: { seq: Number(seq) } }, "存档活动")
          .then(function () {
            PX.toast("已存档");
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
    var wanted = routeFromHash() || FALLBACK_ROUTE;
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
    syncSudo();
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
      case "modules": return drawModules();
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
    reader.onerror = function () { PX.toast("读不了这个文件", true); };
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
      return "<tr>" +
        "<td><b>" + PX.esc(eventTitle(e)) + "</b></td>" +
        "<td>" + PX.esc(e.ownerName || "—") + "</td>" +
        '<td class="mono">' + PX.whenExact(e.archivedAt) + "</td>" +
        '<td class="mono">' + PX.esc(shortToken(e.id)) + "</td></tr>";
    }).join("") : emptyRow(4, S.archivedTotal === 0
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
    return PX.api("/accounts?limit=50").then(function (d) {
      S.me = d.me || S.me;
      S.accounts = d.accounts || [];
      S.accountsTotal = d.total || S.accounts.length;
      renderIdentity();
      applyRoleVisibility();
      syncPendingBadge();
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
      return Promise.all([loadEvents(), loadInvites(), loadAudit()]);
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
    if (SUDO.pop && e.target.closest &&
        !e.target.closest(".sudo-pop") && !e.target.closest("#sudoChip")) {
      closeSudoPop();
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
    loadTemplates().then(function () { go("new-event"); });
  }
  $("#newEventBtn").addEventListener("click", newEvent);
  $("#presetNewBtn").addEventListener("click", function () { presetDialog(null); });
  $("#archivedMore").addEventListener("click", function () { loadArchived(false); });
  $("#presetImportBtn").addEventListener("click", function () {
    $("#presetImportFile").click();
  });
  $("#presetImportFile").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) importPreset(file);
  });
  // 活动管理 is a superadmin view; creating from there uses the same flow
  $("#createEventBtn").addEventListener("click", newEvent);

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
      // another session's writes come back through the stream
      PX.watch(function (revs, before) {
        if (!before || before.panel === undefined) return;
        if (revs.panel === before.panel) return;
        scheduleReload();
      });
      // a reload keeps an elevation that has not run out yet
      setSudo(d.sudoUntil, d.now);
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
