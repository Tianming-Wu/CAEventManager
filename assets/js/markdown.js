/* 一小套 Markdown：面板里助手回答、文档页面都用这一份。
   只认下面这些语法，规矩是"先转义、再套样式"——模型或文档里写出来的 HTML 不会变成 DOM。
   认的标签只有 <kbd> <sup> <sub>，它们的内容照样转义。

      window.MD.render(text)  ->  HTML 片段
      window.MD.esc(text)     ->  转义后的纯文本
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
  return {render: md, esc: esc};
})();