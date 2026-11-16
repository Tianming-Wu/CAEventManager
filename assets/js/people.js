/* 选人：面板与活动后台共用的一份。
 *
 * 两个表面各有一套运行库（主站 PX、活动族 LT），也各有自己的接口前缀，所以这里
 * 不碰任何一边的 api，只要求调用方把 search() 交进来，结果形如
 *   {id, uid, label, avatar, avatarAt}
 * 命名规则在服务端（panel.person_view）：有显示名用显示名，没有就用账号编号。
 *
 *   PeoplePicker.open({
 *     title: "邀请成员",
 *     mode: "multi",            // single：点一下就定；multi：点完再按「完成」
 *     chosen: [person, ...],    // 打开时已经选中的
 *     exclude: [id, ...],       // 不该出现在名单里的（例如自己）
 *     placeholder: "搜索用户",
 *     empty: "没有匹配的账号",
 *     avatarUrl: function (person) { return "…/avatar/1?v=2" },
 *     search: function (q) { return Promise<people[]> },
 *     apply: function (people) {}   // 单选当场回调，多选按「完成」回调
 *   });
 */
(function () {
  // 同一时刻只开一个：再点一次「邀请成员」不该叠出第二个
  var current = null;

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function labelOf(p) {
    if (!p) return "";
    return p.label || p.name || (p.uid ? "账号 #" + p.uid : "账号");
  }

  function open(opts) {
    opts = opts || {};
    var multi = opts.mode !== "single";
    if (current) current.close();          // 只留一层

    var chosen = {};
    (opts.chosen || []).forEach(function (p) {
      if (p) chosen[p.id] = p;
    });
    var hidden = {};
    (opts.exclude || []).forEach(function (id) {
      if (id) hidden[id] = true;
    });
    var results = [];
    var seq = 0;                // 只认最后一次搜索的回答：慢的那个不许盖住新的

    // 头像：有图用图（远程链接直接用），没有就用显示名的第一个字
    function avatarHtml(p) {
      var label = labelOf(p);
      var url = "";
      if (p && p.avatar) {
        url = /^https?:\/\//i.test(p.avatar)
          ? p.avatar
          : (opts.avatarUrl ? opts.avatarUrl(p) : "");
      }
      if (url) return '<span class="pp-avatar"><img alt="" src="' + esc(url) + '"></span>';
      return '<span class="pp-avatar">' + esc(label.trim().charAt(0) || "?") + "</span>";
    }

    function tileHtml(p, on) {
      return '<button type="button" class="pp-tile' + (on ? " on" : "") + '"' +
        ' data-pp-id="' + esc(p.id) + '" title="' + esc(labelOf(p)) + '"' +
        ' aria-pressed="' + (on ? "true" : "false") + '">' +
        avatarHtml(p) +
        '<span class="pp-name">' + esc(labelOf(p)) + "</span></button>";
    }

    var back = document.createElement("div");
    back.className = "pp-back";
    back.innerHTML =
      '<div class="pp-card" role="dialog" aria-modal="true">' +
      '<div class="pp-head">' +
      "<h2>" + esc(opts.title || "选择成员") + "</h2>" +
      '<button type="button" class="pp-x" aria-label="关闭">' +
      '<svg viewBox="0 0 20 20" aria-hidden="true">' +
      '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/></svg></button></div>' +
      '<input type="search" class="pp-find" autocomplete="off"' +
      ' placeholder="' + esc(opts.placeholder || "搜索用户") + '"' +
      ' aria-label="' + esc(opts.placeholder || "搜索用户") + '">' +
      '<div class="pp-list"></div>' +
      '<div class="pp-foot">' +
      '<button type="button" class="pp-cancel">取消</button>' +
      '<button type="button" class="pp-done"' + (multi ? "" : " disabled") +
      ">完成</button></div>" +
      "</div>";
    document.body.appendChild(back);

    var list = back.querySelector(".pp-list");
    var box = back.querySelector(".pp-find");
    var doneBtn = back.querySelector(".pp-done");
    var self = { close: close };
    current = self;

    function close() {
      if (current === self) current = null;
      document.removeEventListener("keydown", onKey, true);
      document.body.classList.remove("pp-open");
      if (back.parentNode) back.parentNode.removeChild(back);
    }

    // 单人选择时"没点人"就没有可交的东西，[完成] 先淡着
    function syncDone() {
      if (!doneBtn) return;
      doneBtn.disabled = !multi && !Object.keys(chosen).length;
    }

    // ESC 的规矩（与其它弹窗一致）：有输入框正被聚焦就永不退出——搜索框里按 ESC
    // 只当"清空"用（单活动时代快速名单的老习惯）；焦点不在输入框上，ESC 就是取消。
    function focusInTextField() {
      var el = document.activeElement;
      if (!el || !back.contains(el)) return false;
      var tag = (el.tagName || "").toLowerCase();
      return tag === "input" || tag === "textarea" || tag === "select";
    }

    function onKey(e) {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (focusInTextField()) {
        if (box.value) {
          box.value = "";
          search();
        }
        return;
      }
      close();                 // 与 [取消] 一样：什么都不改
    }

    function draw() {
      var rows = results.filter(function (p) { return !hidden[p.id]; });
      if (!rows.length) {
        list.innerHTML = '<p class="pp-empty">' +
          esc(opts.empty || "没有匹配的账号") + "</p>";
        return;
      }
      list.innerHTML = '<div class="pp-grid">' + rows.map(function (p) {
        return tileHtml(p, !!chosen[p.id]);
      }).join("") + "</div>";
    }

    function search() {
      var mine = ++seq;
      var q = box.value.trim();
      Promise.resolve(opts.search ? opts.search(q) : []).then(function (rows) {
        if (mine !== seq) return;         // 有更新的搜索在路上，这个回答作废
        results = rows || [];
        draw();
      }).catch(function () {
        if (mine !== seq) return;
        results = [];
        list.innerHTML = '<p class="pp-empty">读不到账号清单</p>';
      });
    }

    function apply(people) {
      close();
      if (typeof opts.apply === "function") opts.apply(people);
    }

    list.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-pp-id]") : null;
      if (!btn) return;
      var id = btn.getAttribute("data-pp-id");
      var person = chosen[id] || null;
      for (var i = 0; i < results.length && !person; i++) {
        if (String(results[i].id) === id) person = results[i];
      }
      if (!person) return;
      if (!multi) {
        // 单选：点一个只是"选它"，先前那个自动让位；按 [完成] 才算数
        chosen = {};
        chosen[id] = person;
        Array.prototype.forEach.call(
          list.querySelectorAll("[data-pp-id]"), function (el) {
            var on = el.getAttribute("data-pp-id") === id;
            el.classList.toggle("on", on);
            el.setAttribute("aria-pressed", on ? "true" : "false");
          });
        syncDone();
        return;
      }
      if (chosen[id]) delete chosen[id];
      else chosen[id] = person;
      btn.classList.toggle("on", !!chosen[id]);
      btn.setAttribute("aria-pressed", chosen[id] ? "true" : "false");
      syncDone();
    });

    // 点窗外不关：这一层里攒着人选，误触就白选了。关掉只有三个明路——[取消]、×、Esc
    back.querySelector(".pp-x").addEventListener("click", close);
    back.querySelector(".pp-cancel").addEventListener("click", close);
    doneBtn.addEventListener("click", function () {
      // 只有 [完成] 才把这一次的选择交出去；[取消]、Esc、点窗外都什么都不做
      apply(Object.keys(chosen).map(function (id) { return chosen[id]; }));
    });

    var timer = null;
    box.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(search, 160);
    });

    document.addEventListener("keydown", onKey, true);
    document.body.classList.add("pp-open");
    syncDone();
    search();
    box.focus();
    return self;
  }

  window.PeoplePicker = { open: open };
})();
