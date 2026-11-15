/* Panel helpers: same relative-URL discipline as the event pages. */
(function () {
  "use strict";

  // "/<mount>/panel" -> "/<mount>", "/" -> ""
  // <mount>/panel/panel（旧地址把挂载前缀拼重过）也还原成 <mount>
  function prefix() {
    var p = location.pathname.replace(/(?:\/(?:panel|login|register|print))+\/?$/, "/");
    return p.replace(/\/+$/, "");
  }
  var PREFIX = prefix();

  /* 搜索框里按 ESC 就是清空（单活动时代快速名单的老习惯，单子是这么用的）。
     清完补一次 input 事件，各处已经接好的筛选会自己重跑一遍。 */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var el = e.target;
    if (!el || !el.matches || !el.matches('input[type="search"]')) return;
    if (!el.value) return;
    e.stopPropagation();
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });

  // The revision this page last saw.  A write that carries it is refused if
  // another session moved on in the meantime (first write wins).
  var revs = {};
  var watchers = [];

  /* 服务端版本：重新部署之后，页面上的脚本还是旧的。
     第一次收到版本号只记下来，之后变了才算“换过版本”，交给页面自己决定怎么换。 */
  var bootVersion = "";
  var updateHandler = null;

  function noteVersion(version) {
    if (!version) return;
    if (!bootVersion) { bootVersion = version; return; }
    if (bootVersion === version) return;
    bootVersion = version;
    if (updateHandler) {
      try { updateHandler(version); return; } catch (e) { /* 交给默认处理 */ }
    }
    document.dispatchEvent(new CustomEvent("px:server-updated", { detail: version }));
  }

  function onUpdate(fn) { updateHandler = fn; }

  /* 每个回答落地的信号。一次页面切换往往带好几个并发请求，
     面板用它判断"内容还在往下灌，先别放那一小段入场动画"——
     动画撞在一屏新内容的排版上，只会被冻住再跳过去（看起来就是"抽一下"）。 */
  var apiHooks = [];
  function afterApi(fn) { apiHooks.push(fn); }
  function noteApi() {
    for (var i = 0; i < apiHooks.length; i++) {
      try { apiHooks[i](); } catch (e) { /* 一个不许连累别的 */ }
    }
  }

  function api(path, options) {
    options = options || {};
    var headers = { "X-Requested-With": "CAEventManager" };
    // what this session has seen, per object; the server compares only the
    // object the request touches, so unrelated writes never block each other
    if (Object.keys(revs).length) headers["X-Rev-Map"] = JSON.stringify(revs);
    var init = {
      method: options.method || "GET",
      headers: headers,
      credentials: "same-origin",
      cache: "no-store"
    };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    // 二进制上送（xlsx 导入）：原样把 Blob 当请求体，别经过 JSON
    if (options.blob) {
      init.headers["Content-Type"] = options.type || "application/octet-stream";
      init.body = options.blob;
    }
    // 长请求要能被"停止"打断：调用方传一个 AbortController 的 signal 就行
    if (options.signal) init.signal = options.signal;
    return fetch(PREFIX + "/api/panel" + path, init).then(function (res) {
      return res.text().then(function (text) {
        var data;
        try { data = text ? JSON.parse(text) : {}; } catch (e) {
          data = { ok: false, error: text || "响应无法解析" };
        }
        if (data && data.revs) revs = data.revs;
        if (data && data.version) noteVersion(data.version);
        noteApi();
        if (!res.ok || data.ok === false) {
          var err = new Error((data && data.error) || ("HTTP " + res.status));
          err.status = res.status;
          // the whole answer, for callers that act on a flag rather than text
          err.data = data || {};
          // the session is not elevated: the caller asks for the password and
          // repeats the same request
          err.sudo = !!(data && data.sudoRequired);
          throw err;
        }
        return data;
      });
    });
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* ---------- Markdown ----------
     助手回答常带标题、列表、表格与代码，原样显示很难读。这里只认一小撮语法，
     规矩仍是"先转义、再套样式"：除了下面这些，别的都当纯文本——模型吐出来的 HTML
     不会变成 DOM。认的标签只有 <kbd> <sup> <sub>，它们的内容照样转义。

     链接与图片这一版**不渲染**：要做成能点的东西，得先把协议白名单、外链提示、
     图片要不要走代理想清楚（ROADMAP 四e 记着）。 */

  var MD_INLINE_TAGS = { kbd: 1, sup: 1, sub: 1 };

  /* 行内：先把反斜杠转义与行内代码挑出来藏好，剩下的照常转义，再上样式，
     最后把藏起来的那几段放回去——这样 \* 不会被当成强调，代码里的星号也不会 */
  function mdInline(text) {
    var holds = [];
    function hold(html) {
      holds.push(html);
      return "\u0001" + (holds.length - 1) + "\u0001";
    }
    var src = String(text === null || text === undefined ? "" : text);
    // 行内代码：开头的反引号不能是被转义的（\` 是字面量，不是代码段的开始）
    src = src.replace(/(^|[^\\])(`+)([\s\S]*?)\2/g, function (all, lead, tick, code) {
      return lead + hold("<code>" + esc(code) + "</code>");
    });
    src = src.replace(/\\([\\`*_{}\[\]()#+\-.!>~|$%&])/g, function (all, ch) {
      return hold(esc(ch));
    });
    var out = esc(src);
    // 三个认得的标签：只认名字，内容早转义过了
    out = out.replace(/&lt;(kbd|sup|sub)&gt;([\s\S]*?)&lt;\/\1&gt;/g,
      function (all, tag, inner) {
        return MD_INLINE_TAGS[tag] ? "<" + tag + ">" + inner + "</" + tag + ">" : all;
      });
    // 粗体先于斜体：**x** 里那两颗星不会再被当成斜体
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
             .replace(/([^_\w])__([^_]+)__(?!\w)/g, "$1<strong>$2</strong>");
    // 斜体只看两端是不是星号，不要求前后有空格或西文标点——
    // 中文里「*斜体*、」这种后面紧跟顿号的写法，以前整条都匹配不上
    out = out.replace(/\*([^*\s](?:[^*]*[^*\s])?)\*/g, "<em>$1</em>");
    // 下划线式的斜体要认词边界，否则 roster_add_person 这种标识符会被拆开
    out = out.replace(/(^|[^\w])_([^_\s](?:[^_]*[^_\s])?)_(?!\w)/g, "$1<em>$2</em>");
    // 删除线先于下标：~~x~~ 不能被当成 ~x~ 再套一层
    out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    // 上下标：模型习惯写 x^2^ 与 H~2~O
    out = out.replace(/\^([^\^\s]+)\^/g, "<sup>$1</sup>");
    out = out.replace(/~([^~\s]+)~/g, "<sub>$1</sub>");
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return out.replace(/\u0001(\d+)\u0001/g, function (all, i) {
      return holds[Number(i)] || "";
    });
  }

  var MD_HR = /^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/;
  var MD_ATX = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  var MD_QUOTE = /^ {0,3}>/;
  var MD_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  var MD_FENCE_OPEN = /^ {0,3}(```+|~~~+)\s*([\w+#.-]*)\s*$/;
  var MD_FENCE_CLOSE = /^ {0,3}(```+|~~~+)\s*$/;
  var MD_TABLE_SEP = /^ {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;

  function mdIndent(line) {
    return (String(line).match(/^[ \t]*/)[0] || "").replace(/\t/g, "    ").length;
  }

  /* 代码块：带一行小标题写语言，右边一颗复制键（点击的活儿在 panel.js 里）。
     没标语言就写 plain text——中文在这条等宽小字里会掉进中文字体，观感不齐 */
  function mdCode(lang, code) {
    var cls = lang ? ' class="language-' + esc(lang) + '"' : "";
    return '<div class="md-code">' +
      '<div class="md-code-head"><span class="md-code-lang">' +
      (lang ? esc(lang) : "plain text") + "</span>" +
      '<button class="md-copy" type="button" data-copy-code="1" ' +
      'aria-label="复制这段代码">复制</button></div>' +
      "<pre><code" + cls + ">" + esc(code) + "</code></pre></div>";
  }

  function mdCells(line) {
    var text = String(line).trim().replace(/^\|/, "").replace(/\|$/, "");
    var cells = [], cur = "";
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (ch === "\\" && text[i + 1] === "|") { cur += "|"; i++; continue; }
      if (ch === "|") { cells.push(cur); cur = ""; continue; }
      cur += ch;
    }
    cells.push(cur);
    return cells.map(function (c) { return c.trim(); });
  }

  function mdTable(rows, aligns) {
    function row(tag, cells) {
      return "<tr>" + cells.map(function (cell, idx) {
        var al = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : "";
        return "<" + tag + al + ">" + mdInline(cell) + "</" + tag + ">";
      }).join("") + "</tr>";
    }
    var body = rows.slice(1).map(function (r) { return row("td", r); }).join("");
    return '<div class="md-table-wrap"><table class="md-table"><thead>' +
      row("th", rows[0] || []) + "</thead><tbody>" + body + "</tbody></table></div>";
  }

  /* 列表：缩进更深的接到上一项里当子列表，所以嵌套、混排都能出来 */
  function mdList(lines, start, indent) {
    var items = [], i = start, ordered = null;
    while (i < lines.length) {
      var m = lines[i].match(MD_ITEM);
      if (!m) break;
      var ind = mdIndent(lines[i]);
      if (ind < indent) break;
      var isOrdered = /^\d/.test(m[2]);
      if (ordered === null) ordered = isOrdered;
      else if (isOrdered !== ordered) break;
      if (ind > indent) {
        if (!items.length) break;
        var sub = mdList(lines, i, ind);
        items[items.length - 1].children += sub.html;
        i = sub.next;
        continue;
      }
      var text = m[3];
      var task = text.match(/^\[([ xX])\]\s+(.*)$/);
      items.push({ text: task ? task[2] : text, task: task ? task[1] : null,
                   children: "" });
      i++;
      // 续行：比标记更深、又不是新条目的那些，接到这一项的文字后面
      while (i < lines.length && !/^\s*$/.test(lines[i]) && !MD_ITEM.test(lines[i])) {
        if (mdIndent(lines[i]) <= indent) break;
        items[items.length - 1].text += " " + lines[i].trim();
        i++;
      }
    }
    var tag = ordered ? "ol" : "ul";
    var hasTask = items.some(function (it) { return it.task !== null; });
    var html = "<" + tag + (hasTask ? ' class="md-tasks"' : "") + ">" +
      items.map(function (it) {
        var box = "";
        if (it.task !== null) {
          var on = String(it.task).toLowerCase() === "x";
          box = '<span class="md-box' + (on ? " on" : "") + '" aria-hidden="true">' +
            (on ? "✓" : "") + "</span>";
        }
        return "<li" + (it.task !== null ? ' class="md-task"' : "") + ">" +
          box + mdInline(it.text) + it.children + "</li>";
      }).join("") + "</" + tag + ">";
    return { html: html, next: i };
  }

  function mdBlocks(lines) {
    var out = [], i = 0, line, m;
    while (i < lines.length) {
      line = lines[i];
      if (/^\s*$/.test(line)) { i++; continue; }

      m = line.match(MD_FENCE_OPEN);
      if (m) {
        var code = [];
        i++;
        while (i < lines.length && !MD_FENCE_CLOSE.test(lines[i])) {
          code.push(lines[i]);
          i++;
        }
        i++;                                   // 跳过收尾的那一行
        out.push(mdCode(m[2], code.join("\n")));
        continue;
      }

      if (MD_HR.test(line)) { out.push("<hr>"); i++; continue; }

      m = line.match(MD_ATX);
      if (m) {
        var lv = m[1].length;
        out.push("<h" + lv + ' class="md-h' + lv + '">' + mdInline(m[2]) +
                 "</h" + lv + ">");
        i++;
        continue;
      }

      if (MD_QUOTE.test(line)) {
        var inner = [];
        while (i < lines.length) {
          if (MD_QUOTE.test(lines[i])) {
            inner.push(lines[i].replace(/^ {0,3}> ?/, ""));
            i++;
            continue;
          }
          // 引用块里的续行（空行之后的普通行到此为止）
          if (!inner.length || /^\s*$/.test(lines[i]) || MD_ITEM.test(lines[i]) ||
              MD_ATX.test(lines[i]) || MD_HR.test(lines[i])) break;
          inner.push(lines[i]);
          i++;
        }
        out.push("<blockquote>" + mdBlocks(inner) + "</blockquote>");
        continue;
      }

      if (MD_ITEM.test(line)) {
        var got = mdList(lines, i, mdIndent(line));
        if (got.html) out.push(got.html);
        i = got.next;
        continue;
      }

      // 表格：这一行有竖线，下一行是 |---|---| 那种分隔行
      if (line.indexOf("|") >= 0 && i + 1 < lines.length &&
          MD_TABLE_SEP.test(lines[i + 1])) {
        var aligns = mdCells(lines[i + 1]).map(function (c) {
          var left = /^:/.test(c), right = /:$/.test(c);
          return left && right ? "center" : right ? "right" : left ? "left" : "";
        });
        var rows = [mdCells(line)];
        i += 2;
        while (i < lines.length && lines[i].indexOf("|") >= 0 &&
               !/^\s*$/.test(lines[i])) {
          rows.push(mdCells(lines[i]));
          i++;
        }
        out.push(mdTable(rows, aligns));
        continue;
      }

      // 剩下的：一段话。段落内的换行照原样断，模型很少自己折行
      var para = [line.trim()];
      i++;
      while (i < lines.length && !/^\s*$/.test(lines[i]) &&
             !MD_ITEM.test(lines[i]) && !MD_QUOTE.test(lines[i]) &&
             !MD_HR.test(lines[i]) && !MD_ATX.test(lines[i]) &&
             !MD_FENCE_OPEN.test(lines[i])) {
        para.push(lines[i].trim());
        i++;
      }
      out.push("<p>" + mdInline(para.join("\n")).replace(/\n/g, "<br>") + "</p>");
    }
    return out.join("\n");
  }

  function md(text) {
    return mdBlocks(String(text === null || text === undefined ? "" : text)
      .replace(/\r\n?/g, "\n").split("\n"));
  }

  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function when(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  // Audit rows are evidence: minutes are not precise enough to tell two actions
  // apart, so those get seconds.
  function whenExact(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  // Browsers happily treat a lone search box as a login field and autofill the
  // saved account name into it.  Anything that appears without a keystroke is
  // not the operator's input, so drop it.
  function guardAutofill(input, onClear) {
    var touched = false;
    var scrub = function () {
      if (touched || !input.value) return;
      input.value = "";
      if (onClear) onClear();
    };
    input.addEventListener("keydown", function () { touched = true; });
    input.addEventListener("focus", scrub);
    input.addEventListener("mousedown", function () { setTimeout(scrub, 0); });
    input.addEventListener("input", function (e) {
      if (e.isTrusted) touched = true;
    });
    [300, 1200, 2600].forEach(function (ms) { setTimeout(scrub, ms); });
    window.addEventListener("load", scrub);
    return scrub;
  }

  /* ---- notices: the top-right corner, for things that happen elsewhere ---- */
  var noticeBox = null;

  function notice(text, kind) {
    if (!noticeBox) {
      noticeBox = document.createElement("div");
      noticeBox.className = "notice-stack";
      noticeBox.setAttribute("role", "status");
      noticeBox.setAttribute("aria-live", "polite");
      document.body.appendChild(noticeBox);
    }
    var item = document.createElement("div");
    item.className = "notice" + (kind ? " " + kind : "");
    item.textContent = text;
    noticeBox.appendChild(item);
    void item.offsetWidth;
    item.classList.add("show");
    setTimeout(function () {
      item.classList.remove("show");
      setTimeout(function () { item.remove(); }, 220);
    }, 5200);
  }

  /* ---- the stream: someone else changed something -------------------------- */
  function watch(handler) {
    watchers.push(handler);
    if (typeof window.EventSource !== "function") return;
    var source = null;
    var open = function () {
      if (source) return;
      source = new EventSource(PREFIX + "/api/panel/stream");
      source.onmessage = function (event) {
        var data;
        try { data = JSON.parse(event.data); } catch (e) { return; }
        if (!data || !data.revs) return;
        if (data.version) noteVersion(data.version);
        var before = revs;
        revs = data.revs;
        watchers.forEach(function (fn) {
          try { fn(data.revs, before); } catch (e) { /* one bad watcher */ }
        });
      };
      source.onerror = function () {
        // the browser retries by itself; reconnect with a clean slate
        if (source && source.readyState === 2) {
          source = null;
          setTimeout(open, 4000);
        }
      };
    };
    open();
  }

  /* 通知从右上角落下：底部弹一条颜色接近页面的条，人一低头就错过了。
     多条时叠成一列，点一下（或按 Esc）随身带走，错误留得久一点。 */
  var toastStack = null;
  function toastBox() {
    if (!toastStack || !toastStack.isConnected) {
      toastStack = document.createElement("div");
      toastStack.className = "toast-stack";
      toastStack.setAttribute("role", "status");
      toastStack.setAttribute("aria-live", "polite");
      document.body.appendChild(toastStack);
    }
    return toastStack;
  }

  /* action 可选：{icon, title, run}。图标是一个小方块按钮，放在关闭键左边同一行，
     所以不会把这条通知撑高——它只是替人省下"再去别处点一次"。 */
  var TOAST_ICONS = {
    open: "M7 3.4v1.6M3.4 7H1.8M6.2 6.2L2.4 10M2.4 6.4v3.2h3.2",   // 跳过去
    reload: "M2.6 6a3.4 3.4 0 1 1 1 2.4M2.6 3.4V6h2.6",            // 重新载入
    copy: "M3 1.8h4.4v4.4M5 4.2H1.8v4.4H6V6.2"
  };

  function toast(msg, bad, ms, action) {
    var stack = toastBox();
    var el = document.createElement("div");
    el.className = "toast" + (bad ? " bad" : "");
    var actHtml = "";
    if (action && TOAST_ICONS[action.icon]) {
      actHtml = '<button class="toast-act" type="button" title="' +
        esc(action.title || "执行") + '" aria-label="' + esc(action.title || "执行") + '">' +
        '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="' +
        TOAST_ICONS[action.icon] + '"/></svg></button>';
    }
    el.innerHTML =
      '<span class="toast-mark" aria-hidden="true">' + (bad ? "!" : "✓") + "</span>" +
      '<span class="toast-text">' + msg + "</span>" +
      actHtml +
      '<button class="toast-x" type="button" aria-label="关闭通知">' +
      '<svg viewBox="0 0 12 12" aria-hidden="true">' +
      '<path d="M3 3l6 6M9 3l-6 6"/></svg></button>';
    stack.appendChild(el);
    void el.offsetWidth;
    el.classList.add("show");
    var close = function () {
      el.classList.remove("show");
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 200);
    };
    var x = el.querySelector(".toast-x");
    ["pointerdown", "click"].forEach(function (evt) {
      x.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        close();
      });
    });
    var act = el.querySelector(".toast-act");
    if (act) {
      act.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        close();
        try { action.run(); } catch (err) { /* 动作失败不影响通知消失 */ }
      });
    }
    setTimeout(close, ms || (bad ? 4200 : 2600));
    // 只留最近四条，别把屏幕堆满
    while (stack.children.length > 4) stack.removeChild(stack.firstChild);
  }

  window.PX = {
    PREFIX: PREFIX,
    api: api,
    watch: watch,
    notice: notice,
    esc: esc,
    when: when,
    whenExact: whenExact,
    guardAutofill: guardAutofill,
    pad: pad,
    toast: toast,
    md: md,
    onUpdate: onUpdate,
    afterApi: afterApi
  };
})();
