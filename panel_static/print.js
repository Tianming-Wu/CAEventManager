/* 打印页：报名表（空白）与已报名单。
   数据走面板自己的接口，所以权限、会话、审计一处都没绕开：
   看不到这份报名的人打不开它的报名表。 */
(function () {
  "use strict";

  var PX = window.PX;
  var paper = document.getElementById("paper");
  var hint = document.getElementById("countHint");
  var rowsField = document.getElementById("rowsField");
  var rowsInput = document.getElementById("blankRows");

  var query = {};
  (location.search.replace(/^\?/, "") || "").split("&").forEach(function (pair) {
    if (!pair) return;
    var kv = pair.split("=");
    query[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || "").replace(/\+/g, " "));
  });

  var KIND = query.kind || "signup";
  var ID = Number(query.id) || 0;
  var S = {
    mode: query.mode === "list" ? "list" : "blank",
    signup: null,
    entries: [],
    total: 0
  };

  function esc(text) {
    return PX.esc(text === null || text === undefined ? "" : text);
  }

  function whenText(ts) {
    return ts ? PX.whenExact(ts) : "";
  }

  /* ---------- 表头 ---------- */
  function qrSvg(url) {
    if (!url || !window.qrcode) return "";
    try {
      var qr = window.qrcode(0, "M");
      qr.addData(url);
      qr.make();
      return qr.createSvgTag({ cellSize: 3, margin: 4 });
    } catch (e) {
      return "";
    }
  }

  function headHtml(signup) {
    var meta = [];
    if (signup.rosterName) meta.push(["写入名单", signup.rosterName]);
    if (signup.opensAt || signup.closesAt) {
      var from = signup.opensAt ? whenText(signup.opensAt) : "不限";
      var to = signup.closesAt ? whenText(signup.closesAt) : "不限";
      meta.push(["报名时间", from + " 至 " + to]);
    }
    if (signup.capacity) {
      meta.push(["名额", signup.capacity + " 人（已报 " + (signup.entries || 0) + " 人）"]);
    }
    if (signup.filterRosterName) {
      meta.push(["限报名单", signup.filterRosterName]);
    }
    var qr = qrSvg(signup.url);
    return '<header class="sheet-head">' +
      '<div class="sheet-title"><h1>' + esc(signup.title || "报名") + "</h1>" +
      (signup.note ? '<p class="sheet-note">' + esc(signup.note) + "</p>" : "") +
      "</div>" +
      (qr ? '<div class="sheet-qr">' + qr + '<span class="sheet-url">' +
            esc(signup.url) + "</span></div>" : "") +
      "</header>" +
      (meta.length ? '<dl class="sheet-meta">' + meta.map(function (pair) {
        return "<div><dt>" + esc(pair[0]) + "</dt><dd>" + esc(pair[1]) + "</dd></div>";
      }).join("") + "</dl>" : "");
  }

  /* ---------- 列 ---------- */
  function columns(signup) {
    var collect = signup.collect || [];
    var cols = [{ key: "sid", label: "学号", cls: "mono" },
                { key: "name", label: "姓名" }];
    if (collect.indexOf("dept") >= 0) cols.push({ key: "dept", label: "院系" });
    if (collect.indexOf("grp") >= 0) cols.push({ key: "grp", label: "班级" });
    (signup.fields || []).forEach(function (field) {
      cols.push({ key: field.key, label: field.label, from: "answers" });
    });
    if (S.mode === "list") {
      cols.push({ key: "ts", label: "报名时间", cls: "mono w-time", kind: "time" });
    }
    cols.push({ key: "sign", label: "签名", cls: "w-sig" });
    return cols;
  }

  function cellValue(row, col) {
    if (col.kind === "time") return whenText(row.ts);
    if (col.from === "answers") {
      var value = (row.answers || {})[col.key];
      return value === undefined || value === null ? "" : String(value);
    }
    return row[col.key] || "";
  }

  function tableHtml(signup, rows) {
    var cols = columns(signup);
    var head = '<thead><tr><th class="w-no">序号</th>' + cols.map(function (col) {
      return '<th' + (col.cls ? ' class="' + col.cls + '"' : "") + ">" +
        esc(col.label) + "</th>";
    }).join("") + "</tr></thead>";
    var body = rows.map(function (row, index) {
      return "<tr><td class=\"no\">" + (index + 1) + "</td>" +
        cols.map(function (col) {
          if (col.key === "sign") return "<td></td>";
          var value = cellValue(row, col);
          return "<td" + (col.cls ? ' class="' + col.cls + '"' : "") + ">" +
            esc(value) + "</td>";
        }).join("") + "</tr>";
    }).join("");
    return '<table class="sheet-table' + (S.mode === "blank" ? " blank" : "") + '">' +
      head + "<tbody>" + body + "</tbody></table>";
  }

  /* ---------- 两种布局 ---------- */
  function draw() {
    var signup = S.signup;
    if (!signup) return;
    rowsField.hidden = S.mode !== "blank";
    var stamp = "打印于 " + whenText(Date.now() / 1000);
    var html = headHtml(signup);
    if (S.mode === "blank") {
      var count = Math.max(0, Math.min(40, Number(rowsInput.value) || 0));
      html += tableHtml(signup, new Array(count).fill({}));
      html += '<footer class="sheet-foot"><span>共 ' + count + " 个空位</span>" +
        "<span>CAEventManager · " + esc(stamp) + "</span></footer>";
      hint.textContent = "空白表：" + count + " 行，留给现场手写";
    } else {
      html += tableHtml(signup, S.entries);
      if (!S.entries.length) {
        html += '<p class="sheet-empty">还没有人报名。</p>';
      }
      html += '<footer class="sheet-foot"><span>共 ' + S.total + " 人" +
        (S.entries.length < S.total ? "（这里列出前 " + S.entries.length + " 人）" : "") +
        "</span><span>CAEventManager · " + esc(stamp) + "</span></footer>";
      hint.textContent = "已报名单：" + S.total + " 人";
    }
    paper.innerHTML = html;
    document.title = (signup.title || "报名") + " · 报名表";
  }

  /* ---------- 数据 ---------- */
  function load() {
    if (KIND !== "signup" || !ID) {
      paper.innerHTML = '<p class="hint">缺少报名编号，请从「我的报名」里打开。</p>';
      return Promise.resolve();
    }
    return PX.api("/signup?id=" + ID).then(function (data) {
      S.signup = data.signup;
      if (S.mode !== "list") return null;
      // 一次取够：打印件要在一张纸上看全，分页留给下一个人
      return PX.api("/signup/entries?id=" + ID + "&limit=200").then(function (page) {
        S.entries = page.entries || [];
        S.total = page.total || 0;
      });
    }).then(draw).catch(function (err) {
      if (err && err.status === 401) {
        var back = encodeURIComponent(location.pathname + location.search);
        paper.innerHTML = '<p class="sheet-empty">登录之后才能打印。' +
          '<a href="' + PX.esc(PX.PREFIX + "/login?s_return=" + back) +
          '">去登录</a></p>';
        return;
      }
      paper.innerHTML = '<p class="sheet-empty">' +
        PX.esc((err && err.message) || "读不出这份报名") + "</p>";
    });
  }

  /* ---------- 交互 ---------- */
  document.getElementById("printGo").addEventListener("click", function () {
    window.print();
  });
  document.getElementById("backBtn").addEventListener("click", function () {
    location.href = PX.PREFIX + "/panel#/signup/" + ID;
  });
  document.getElementById("modeSeg").addEventListener("click", function (e) {
    var btn = e.target.closest("[data-mode]");
    if (!btn) return;
    var mode = btn.getAttribute("data-mode");
    if (mode === S.mode) return;
    S.mode = mode;
    Array.prototype.forEach.call(this.querySelectorAll("[data-mode]"), function (item) {
      item.setAttribute("aria-pressed",
        item.getAttribute("data-mode") === mode ? "true" : "false");
    });
    // 换布局要重取数据：空白表不碰提交记录
    if (mode === "list" && !S.entries.length) {
      paper.innerHTML = '<p class="hint">正在读取报名记录…</p>';
      PX.api("/signup/entries?id=" + ID + "&limit=200").then(function (page) {
        S.entries = page.entries || [];
        S.total = page.total || 0;
        draw();
      }).catch(function (err) {
        paper.innerHTML = '<p class="sheet-empty">' +
          PX.esc((err && err.message) || "读不出报名记录") + "</p>";
      });
      return;
    }
    draw();
  });
  rowsInput.addEventListener("input", function () {
    if (S.mode === "blank") draw();
  });

  load();
})();
