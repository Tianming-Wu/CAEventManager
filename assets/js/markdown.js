/* 一小套 Markdown：面板里助手回答、文档页面都用这一份。
   只认下面这些语法，规矩是"先转义、再套样式"——模型或文档里写出来的 HTML 不会变成 DOM。
   认的标签只有 <kbd> <sup> <sub>，它们的内容照样转义。

      window.MD.render(text, opts)  ->  HTML 片段
      window.MD.esc(text)           ->  转义后的纯文本
      window.MD.resolveLink(...)    ->  一条链接该渲染成什么（文档页与面板共用一份判定）
*/
window.MD = (function () {
function esc(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* ---------- Markdown ----------
   助手回答常带标题、列表、表格与代码，原样显示很难读。这里只认一小撮语法，
   规矩仍是"先转义、再套样式"：除了下面这些，别的都当纯文本——模型吐出来的 HTML
   不会变成 DOM。认的标签只有 <kbd> <sup> <sub>，它们的内容照样转义。

   链接与图片不自己判：给不给得出来源由调用方决定（opts.link / opts.image），
   判不出来就留成纯文本——这里不认识协议，也不该认识。 */

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
  // 图片：只看调用方给不给得出来源（文档页只认本地的图，且在白名单里）
  out = out.replace(/!\[([^\]]*)\]\(([^\s)]+)\)/g, function (all, alt, target) {
    var src = OPTS && typeof OPTS.image === "function" ? OPTS.image(target) : null;
    if (!src) return hold(all);                 // 给不出来：原样留着，别让下面的链接规则啃掉
    return '<img class="md-img" src="' + escAttr(src) + '" alt="' + escAttr(alt) +
      '" loading="lazy">';
  });
  // 链接：交给调用方判——站内文档、外链、还是当普通文字 / 断链。
  // 没人接手时只认外链，别的路径留成文字，不给死链接
  out = out.replace(/\[([^\]]+)\]\(([^\s)]+)\)/g, function (all, label, target) {
    var hit = OPTS && typeof OPTS.link === "function"
      ? OPTS.link(target) : resolveLink(target, {});
    if (!hit) return label;                     // 不认的链接：只留文字
    if (hit.broken) {
      return '<span class="md-broken">' + label + "（链接已断）</span>";
    }
    return '<a class="' + (hit.doc ? "md-doc" : "md-link") + '" href="' +
      escAttr(hit.href) + '"' + (hit.target ? " " + hit.target : "") +
      (hit.ext ? ' title="在新标签页打开"' : "") +
      (hit.doc ? ' data-doc-link="' + escAttr(hit.doc) + '"' : "") + ">" +
      (hit.doc ? MD_DOC_ICON : (hit.ext ? MD_EXT_ICON : "")) +
      "<span>" + label + "</span></a>";
  });
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
      // 标题带锚点：别的文章可以直接跳到这一节（#/path~slug）
      var anchor = OPTS && OPTS.anchors ? ' id="' + escAttr(slug(m[2])) + '"' : "";
      out.push("<h" + lv + ' class="md-h' + lv + '"' + anchor + ">" +
               mdInline(m[2]) + "</h" + lv + ">");
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

/* 渲染时的上下文：站内文档清单（`usage/approval.md` 这种键）、当前文档所在目录、
   以及"这一篇的地址怎么拼"。没有清单就把 @ 链接当普通文字，不硬造链接。 */
var OPTS = null;

var MD_DOC_ICON = '<svg class="md-doc-ico" viewBox="0 0 20 20" aria-hidden="true" ' +
  'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" ' +
  'stroke-linejoin="round"><path d="M4.6 3.4h6.2a1.6 1.6 0 0 1 1.6 1.6v10.4a1.2 1.2 0 0 1-1.2 ' +
  '1.2H4.6a1.2 1.2 0 0 1-1.2-1.2V4.6a1.2 1.2 0 0 1 1.2-1.2Z"/><path d="M7 7.2h3.2M7 10h3.2' +
  'M7 12.8h2"/></svg>';

/* 站外地址：开新标签页，所以带上那枚"开新窗口"的小箭头（与侧栏的「使用文档」同一形状） */
var MD_EXT_ICON = '<svg class="md-ext-ico" viewBox="0 0 20 20" aria-hidden="true" ' +
  'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" ' +
  'stroke-linejoin="round"><path d="M9 3.8H5.4a1.6 1.6 0 0 0-1.6 1.6v9.2a1.6 1.6 0 0 0 ' +
  '1.6 1.6h9.2a1.6 1.6 0 0 0 1.6-1.6V11"/><path d="M10 10l6.2-6.2M11.8 3.8h4.4v4.4"/></svg>';

function escAttr(text) {
  return String(text === null || text === undefined ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* 文档里的相对路径归一成清单里的键（形如 `usage/rosters.md`，不带 doc/ 前缀）：
   `a/../b`、`./x` 都算干净；写成 `doc/usage/x.md` 时按文档根算，不接当前目录 */
function resolveDocPath(target, base) {
  var raw = String(target || "").trim();
  if (!raw) return "";
  if (/^\/?doc\//.test(raw)) {
    raw = raw.replace(/^\/?doc\//, "");
    base = "";
  }
  var full = raw.charAt(0) === "/" ? raw.slice(1) : (base ? base + "/" + raw : raw);
  var out = [];
  full.split("/").forEach(function (part) {
    if (!part || part === ".") return;
    if (part === "..") { out.pop(); return; }
    out.push(part);
  });
  return out.join("/");
}

/* 键对上清单：先按整条路径对；只写了文件名（`assistant.md`）时按文件名对一次，
   重名（各模块的 doc.md）就当作对不上，别猜是哪一份 */
function findDoc(key, docs) {
  if (docs.indexOf(key) >= 0) return key;
  if (key.indexOf("/") >= 0) return "";
  var hits = docs.filter(function (p) {
    return p.slice(-(key.length + 1)) === "/" + key;
  });
  return hits.length === 1 ? hits[0] : "";
}

/* 站外地址要不要放行：调用方给一个判定函数，或者一串正则（字符串也行）。
   不给就是都放行——我们自己的文档不必过白名单，模型写出来的地址要过 */
function externalAllowed(url, allow) {
  if (!allow) return true;
  if (typeof allow === "function") return !!allow(url);
  var list = allow || [];
  for (var i = 0; i < list.length; i++) {
    try {
      if (list[i] instanceof RegExp ? list[i].test(url) : new RegExp(list[i]).test(url)) {
        return true;
      }
    } catch (e) { /* 写错的正则当没写 */ }
  }
  return false;
}

/* 一条链接该渲染成什么——判定只有这一处：
     {href, target?, doc?, ext?}  可点的链接（doc 是站内文档，ext 是站外地址，两者都开新标签页）
     {broken: true}         指向了不存在的文档（渲染成红色断链）
     null                   不认：只留文字，不给死链接
   opts: {docs:[键], base:"usage", href(path, anchor), target, external,
          allowExternal(地址 → 布尔 或 正则串数组), selfAnchor(anchor)} */
function resolveLink(target, opts) {
  opts = opts || {};
  var t = String(target || "").trim();
  if (!t) return null;
  if (/^https?:\/\//i.test(t) || /^mailto:/i.test(t)) {
    if (opts.external === false) return null;
    if (!externalAllowed(t, opts.allowExternal)) return null;
    return {href: t, target: 'target="_blank" rel="noopener noreferrer"',
            ext: /^https?:\/\//i.test(t)};
  }
  if (t.charAt(0) === "#") {
    return opts.selfAnchor ? {href: opts.selfAnchor(t.slice(1))} : null;
  }
  if (!opts.docs || !opts.docs.length) return null;
  var cut = t.indexOf("#");
  var anchor = cut < 0 ? "" : t.slice(cut + 1);
  var path = resolveDocPath(cut < 0 ? t : t.slice(0, cut), opts.base);
  var key = findDoc(/\.md$/i.test(path) ? path : path + ".md", opts.docs);
  if (!key) return {broken: true};
  return {href: typeof opts.href === "function" ? opts.href(key, anchor) : "#/" + key,
          doc: key, target: opts.target || ""};
}

/* 标题的锚点：与 GitHub 的做法一致一点——去标点、空格换连字符、转小写。
   中文标题照原样留着，地址里会被 encodeURIComponent 编掉。 */
function slug(text) {
  return String(text || "").trim().toLowerCase()
    .replace(/[\s]+/g, "-")
    .replace(/[!"#$%&'()*+,./:;<=>?@\[\\\]^`{|}~，。、；：！？（）《》【】“”‘’…—]/g, "")
    .replace(/-+/g, "-");
}

function md(text, opts) {
  var prev = OPTS;
  OPTS = opts || null;
  try {
    return mdBlocks(String(text === null || text === undefined ? "" : text)
      .replace(/\r\n?/g, "\n").split("\n"));
  } finally {
    OPTS = prev;
  }
}
  return {render: md, esc: esc, resolveLink: resolveLink,
          resolveDocPath: resolveDocPath, slug: slug};
})();