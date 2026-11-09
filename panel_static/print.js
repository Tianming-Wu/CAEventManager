/* 打印页：名单 / 报名单。

   纸上只有一张表与页码。标题与二维码是选项（默认不开）。
   数据走面板自己的接口：谁能看这份名单/报名，谁才能打印它。

   分页在页面里先算好：每页是一个固定高度的 .sheet-page，JS 逐行放进去，
   放不下就换页，最后统一填「第 N / M 页」。这样屏幕上看到的就是印出来的样子。 */
(function () {
  "use strict";

  var PX = window.PX;
  var paper = document.getElementById("paper");
  var hint = document.getElementById("countHint");
  var rowsField = document.getElementById("rowsField");
  var rowsInput = document.getElementById("blankRows");
  var titleBox = document.getElementById("optTitle");
  var qrBox = document.getElementById("optQr");
  var qrLabel = document.getElementById("optQrLabel");
  var modeSeg = document.getElementById("modeSeg");

  var query = {};
  (location.search.replace(/^\?/, "") || "").split("&").forEach(function (pair) {
    if (!pair) return;
    var kv = pair.split("=");
    query[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || "").replace(/\+/g, " "));
  });

  var KIND = query.kind === "roster" ? "roster" : "signup";
  var ID = Number(query.id) || 0;
  var S = {
    // 报名默认打"已报名单"：手写报名表不是这套系统的用法，留着只是备选
    mode: query.mode === "blank" ? "blank" : "list",
    signup: null,
    roster: null,
    entries: [],
    total: 0
  };

  function whenText(ts) {
    return ts ? PX.whenExact(ts) : "";
  }

  function qrSvg(url) {
    if (!url || !window.qrcode) return "";
    try {
      var qr = window.qrcode(0, "M");
      qr.addData(url);
      qr.make();
      return qr.createSvgTag({ cellSize: 3, margin: 2 });
    } catch (e) {
      return "";
    }
  }

  /* ---------- 列 ---------- */
  function columns() {
    if (KIND === "roster") {
      var cols = [
        { key: "sid", label: "学号", cls: "mono w-sid" },
        { key: "name", label: "姓名" },
        { key: "dept", label: "院系" },
        { key: "grp", label: "班级" }
      ];
      if (S.entries.some(function (row) { return row.tag; })) {
        cols.push({ key: "tag", label: "分组" });
      }
      if (S.entries.some(function (row) { return row.state !== "active"; })) {
        cols.push({ key: "state", label: "状态", cls: "w-state" });
      }
      return cols;
    }
    var signup = S.signup || {};
    var collect = signup.collect || [];
    var list = [{ key: "sid", label: "学号", cls: "mono w-sid" },
                { key: "name", label: "姓名" }];
    if (collect.indexOf("dept") >= 0) list.push({ key: "dept", label: "院系" });
    if (collect.indexOf("grp") >= 0) list.push({ key: "grp", label: "班级" });
    (signup.fields || []).forEach(function (field) {
      list.push({ key: field.key, label: field.label, from: "answers" });
    });
    return list;
  }

  function cellValue(row, col) {
    if (col.from === "answers") {
      var value = (row.answers || {})[col.key];
      return value === undefined || value === null ? "" : String(value);
    }
    if (col.key === "state") return row.stateLabel || "";
    return row[col.key] || "";
  }

  /* ---------- 一页一页地放 ---------- */
  function rowsOf() {
    if (KIND === "roster") return S.entries;
    if (S.mode === "blank") {
      var count = Math.max(0, Math.min(60, Number(rowsInput.value) || 0));
      return new Array(count).fill({});
    }
    return S.entries;
  }

  function capHtml() {
    var wantTitle = titleBox.checked;
    var wantQr = KIND === "signup" && qrBox.checked;
    if (!wantTitle && !wantQr) return "";
    var title = KIND === "roster"
      ? (S.roster && S.roster.name) || ""
      : (S.signup && S.signup.title) || "";
    var qr = wantQr ? qrSvg((S.signup || {}).url) : "";
    return '<div class="sheet-cap">' +
      (wantTitle ? '<div class="cap-title">' + esc(title) + "</div>" : '<div class="cap-title"></div>') +
      (qr ? '<div class="cap-qr">' + qr + "</div>" : "") +
      "</div>";
  }

  function esc(text) {
    return PX.esc(text === null || text === undefined ? "" : text);
  }

  function layout() {
    var cols = columns();
    var rows = rowsOf();
    var blank = KIND === "signup" && S.mode === "blank";
    paper.innerHTML = "";
    if (!rows.length) {
      var empty = document.createElement("div");
      empty.className = "sheet-page";
      empty.innerHTML = '<div class="page-body"><p class="sheet-note">' +
        (KIND === "roster" ? "这份名单还没有成员。" : "还没有人报名。") +
        "</p></div>";
      paper.appendChild(empty);
      hint.textContent = "没有可打印的内容";
      return;
    }
    var head = "<thead><tr><th class=\"w-no\">序号</th>" + cols.map(function (col) {
      return "<th" + (col.cls ? ' class="' + col.cls + '"' : "") + ">" +
        esc(col.label) + "</th>";
    }).join("") + "</tr></thead>";

    var pages = [];
    var page = null;
    var tbody = null;
    var body = null;

    function newPage() {
      page = document.createElement("div");
      page.className = "sheet-page";
      page.innerHTML = '<div class="page-body">' +
        (pages.length === 0 ? capHtml() : "") +
        '<table class="sheet-table' + (blank ? " blank" : "") + '">' + head +
        "</table></div>" +
        '<div class="page-foot"><span class="page-no"></span></div>';
      paper.appendChild(page);
      body = page.querySelector(".page-body");
      tbody = document.createElement("tbody");
      page.querySelector("table").appendChild(tbody);
      pages.push(page);
    }

    newPage();
    rows.forEach(function (row, index) {
      var tr = document.createElement("tr");
      tr.innerHTML = '<td class="no">' + (index + 1) + "</td>" +
        cols.map(function (col) {
          var value = cellValue(row, col);
          return "<td" + (col.cls ? ' class="' + col.cls + '"' : "") + ">" +
            esc(value) + "</td>";
        }).join("");
      tbody.appendChild(tr);
      // 放不下就把这一行挪到下一页：overflow:hidden 的 page-body 会撑高
      if (body.scrollHeight > body.clientHeight + 1 && tbody.children.length > 1) {
        tbody.removeChild(tr);
        newPage();
        tbody.appendChild(tr);
      }
    });

    var total = pages.length;
    pages.forEach(function (item, index) {
      item.querySelector(".page-no").textContent =
        "第 " + (index + 1) + " / " + total + " 页";
    });
    hint.textContent = (KIND === "roster" ? "名单：" + rows.length + " 人"
                       : blank ? "空白表：" + rows.length + " 行"
                       : "已报名单：" + rows.length + " 人") +
      " · 共 " + total + " 页";
  }

  /* ---------- 取数据 ---------- */
  function loadAll(entriesPath, wanted) {
    var all = [];
    var total = null;
    function step() {
      var url = entriesPath + "&offset=" + all.length + "&limit=200";
      return PX.api(url).then(function (page) {
        all = all.concat(page.entries || []);
        total = page.total || 0;
        if (all.length < total && all.length < 2000) return step();
        return {entries: all, total: total};
      });
    }
    return step();
  }

  function load() {
    if (!ID) {
      paper.innerHTML = '<p class="sheet-note">缺少编号，请从面板里打开。</p>';
      return Promise.resolve();
    }
    if (KIND === "roster") {
      return PX.api("/roster?id=" + ID).then(function (data) {
        S.roster = data.roster;
        document.title = (S.roster && S.roster.name ? S.roster.name : "名单");
        return loadAll("/roster/entries?id=" + ID, 0);
      }).then(function (page) {
        S.entries = page.entries || [];
        S.total = page.total || 0;
        layout();
      }).catch(fail);
    }
    return PX.api("/signup?id=" + ID).then(function (data) {
      S.signup = data.signup;
      document.title = S.signup.title || "报名";
      if (S.mode !== "list") return null;
      return PX.api("/signup/entries?id=" + ID + "&limit=200").then(function (page) {
        S.entries = page.entries || [];
        S.total = page.total || 0;
      });
    }).then(layout).catch(fail);
  }

  function fail(err) {
    if (err && err.status === 401) {
      var back = encodeURIComponent(location.pathname + location.search);
      paper.innerHTML = '<p class="sheet-note">登录之后才能打印。' +
        '<a href="' + PX.esc(PX.PREFIX + "/login?s_return=" + back) +
        '">去登录</a></p>';
      return;
    }
    paper.innerHTML = '<p class="sheet-note">' +
      PX.esc((err && err.message) || "读不出要打印的内容") + "</p>";
  }

  /* ---------- 交互 ---------- */
  document.getElementById("printGo").addEventListener("click", function () {
    window.print();
  });
  document.getElementById("backBtn").addEventListener("click", function () {
    location.href = PX.PREFIX + "/panel#" +
      (KIND === "roster" ? "/roster/" : "/signup/") + ID;
  });
  if (modeSeg) {
    modeSeg.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-mode]");
      if (!btn) return;
      var mode = btn.getAttribute("data-mode");
      if (mode === S.mode) return;
      S.mode = mode;
      Array.prototype.forEach.call(modeSeg.querySelectorAll("[data-mode]"), function (el) {
        el.setAttribute("aria-pressed",
          el.getAttribute("data-mode") === mode ? "true" : "false");
      });
      rowsField.hidden = mode !== "blank";
      layout();
    });
  }
  rowsInput.addEventListener("input", layout);
  titleBox.addEventListener("change", layout);
  if (qrBox) qrBox.addEventListener("change", layout);

  // 名单没有空白表这一说
  if (KIND === "roster") {
    if (modeSeg) modeSeg.hidden = true;
    rowsField.hidden = true;
    if (qrLabel) qrLabel.hidden = true;
  } else {
    rowsField.hidden = S.mode !== "blank";
    Array.prototype.forEach.call(
      (modeSeg || document).querySelectorAll("[data-mode]"), function (el) {
        el.setAttribute("aria-pressed",
          el.getAttribute("data-mode") === S.mode ? "true" : "false");
      });
  }

  load();
})();
