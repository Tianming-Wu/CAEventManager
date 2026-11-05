/* Public 报名 page: one token in the address, no account, no session.
   Everything it needs comes from `api/state`; submitting posts one JSON body. */
(function () {
  "use strict";

  var BASE = location.pathname.replace(/\/+$/, "");
  var data = null;
  var pending = null;   // 上一次提交的正文，用户确认"更新信息"时原样再发一次

  var $ = function (s) { return document.querySelector(s); };

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function when(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" +
      ("0" + d.getDate()).slice(-2) + " " +
      ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  function showError(text) {
    $("#err").textContent = text || "";
  }

  /* 主题只动几个变量，页面结构与组件不随主题变化 */
  var PRESETS = {
    plain: { bg: "#ffffff", text: "#1c1917", muted: "#8b837b", line: "#ddd7cf",
             surface: "#ffffff", page: "#f5f2ee" },
    warm: { bg: "#fff8f0", text: "#3b2a1a", muted: "#9a8570", line: "#ecdcc8",
            surface: "#fffdfa", page: "#f7efe3" },
    cool: { bg: "#f3f7fb", text: "#1b2733", muted: "#6d8398", line: "#d5e2ee",
            surface: "#ffffff", page: "#eaf1f8" },
    dark: { bg: "#1b1a18", text: "#f3efe9", muted: "#a49b90", line: "#3a3632",
            surface: "#20201e", page: "#0f0e0d" }
  };

  function applyTheme() {
    var theme = (data && data.signup && data.signup.theme) || {};
    var preset = PRESETS[theme.preset] || PRESETS.plain;
    var card = $("#card");
    if (!card) return;
    card.style.background = preset.bg;
    card.style.borderColor = preset.line;
    card.style.boxShadow = "0 10px 30px rgba(40, 28, 12, .10)";
    document.body.style.background = preset.page;
    document.body.style.color = preset.text;
    var title = $("#title");
    if (title) title.style.color = preset.text;
    ["#note", "#status"].forEach(function (sel) {
      var el = $(sel);
      if (el) el.style.color = preset.muted;
    });
    Array.prototype.forEach.call(card.querySelectorAll(".field label, .hint"), function (el) {
      el.style.color = preset.muted;
    });
    Array.prototype.forEach.call(card.querySelectorAll("input, select, textarea"), function (el) {
      el.style.background = preset.surface;
      el.style.color = preset.text;
      el.style.borderColor = preset.line;
    });
    var go = $("#go");
    if (go) {
      go.style.background = theme.accent || "";
      go.style.color = "#fff";
    }
    card.style.setProperty("--preview-bg", preset.bg);
    card.style.setProperty("--preview-text", preset.text);
    card.style.setProperty("--preview-muted", preset.muted);
    card.style.setProperty("--preview-line", preset.line);
    card.style.setProperty("--preview-surface", preset.surface);
    if (theme.accent) card.style.setProperty("--preview-accent", theme.accent);
  }

  function fieldHtml(field) {
    var id = "f_" + field.key;
    var label = '<label for="' + id + '">' + esc(field.label) +
      (field.required ? ' <span class="req">*</span>' : "") + "</label>";
    if (field.type === "textarea") {
      return '<div class="field">' + label + '<textarea id="' + id + '" rows="3" ' +
        'maxlength="500"></textarea></div>';
    }
    if (field.type === "select") {
      return '<div class="field">' + label + '<select id="' + id + '">' +
        '<option value="">请选择</option>' +
        field.options.map(function (o) {
          return '<option value="' + esc(o) + '">' + esc(o) + "</option>";
        }).join("") + "</select></div>";
    }
    var type = field.type === "number" ? "number" : (field.type === "date" ? "date" : "text");
    return '<div class="field">' + label + '<input id="' + id + '" type="' + type +
      '" autocomplete="off"></div>';
  }

  function renderForm() {
    var s = data.signup;
    var fields = s.fields || [];
    var collect = s.collect || [];
    // 已经有字段写进院系/班级时，不再另外给一个同名输入框
    var takesDept = fields.some(function (f) { return f.into === "person.dept"; });
    var takesGrp = fields.some(function (f) { return f.into === "person.grp"; });
    var builtin = "";
    if (collect.indexOf("dept") >= 0 && !takesDept) {
      builtin += '<div class="field"><label for="dept">院系</label>' +
        '<input id="dept" type="text" autocomplete="off" maxlength="40"></div>';
    }
    if (collect.indexOf("grp") >= 0 && !takesGrp) {
      builtin += '<div class="field"><label for="grp">班级</label>' +
        '<input id="grp" type="text" autocomplete="off" maxlength="40"></div>';
    }
    $("#body").innerHTML =
      '<div class="field"><label for="sid">学号 <span class="req">*</span></label>' +
      '<input id="sid" type="text" autocomplete="off" maxlength="32"></div>' +
      '<div class="field"><label for="name">姓名 <span class="req">*</span></label>' +
      '<input id="name" type="text" autocomplete="off" maxlength="32"></div>' +
      (data.limited
        ? '<p class="hint">本次报名仅限名单内成员，学号与姓名需与名单记录一致。</p>'
        : "") +
      builtin + fields.map(fieldHtml).join("") +
      '<button class="btn primary block" type="button" id="go">提交报名</button>';
    $("#go").addEventListener("click", function () { submit(false); });
    ["#sid", "#name", "#dept", "#grp"].forEach(function (sel) {
      var el = $(sel);
      if (el) {
        el.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); submit(false); }
        });
      }
    });
    applyTheme();
  }

  function collectBody() {
    var s = data.signup;
    var body = {
      sid: ($("#sid").value || "").trim(),
      name: ($("#name").value || "").trim(),
      dept: $("#dept") ? $("#dept").value.trim() : "",
      grp: $("#grp") ? $("#grp").value.trim() : "",
      answers: {}
    };
    (s.fields || []).forEach(function (f) {
      var el = $("#f_" + f.key);
      body.answers[f.key] = el ? el.value : "";
    });
    return body;
  }

  function post(body) {
    return fetch(BASE + "/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "signup" },
      cache: "no-store",
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.text().then(function (text) {
        var out = {};
        try { out = text ? JSON.parse(text) : {}; } catch (e) { out = {}; }
        if (!r.ok || out.ok === false) {
          throw new Error(out.error || "提交失败");
        }
        return out;
      });
    });
  }

  function submit(update) {
    var body = collectBody();
    if (!body.sid || !body.name) {
      showError("学号与姓名均为必填");
      return;
    }
    if (update) body.update = true;
    $("#go").disabled = true;
    showError("");
    post(body).then(handle).catch(function (e) {
      showError(e.message || "提交失败");
      $("#go").disabled = false;
    });
  }

  function handle(out) {
    if (out.state === "same") {
      $("#body").innerHTML = '<div class="ci-done" style="text-align:left">' +
        "<h2 style='margin:0 0 8px'>您已经报过名</h2>" +
        "<p class='hint'>" + when(out.ts) + " 提交的记录已收到，无需重复提交。</p></div>";
      return;
    }
    if (out.state === "differs") {
      // 允许自己改：问一次，确认后带上 update 再发一遍
      var body = collectBody();
      $("#body").innerHTML = '<div style="text-align:left">' +
        "<h2 style='margin:0 0 8px'>您已经报过名，但信息不一致</h2>" +
        "<p class='hint'>您希望更新信息吗？更新后以本次填写为准。</p>" +
        '<button class="btn primary block" type="button" id="doUpdate">更新信息</button>' +
        '<button class="btn block" type="button" id="doCancel" ' +
        'style="margin-top:8px">保持原样</button></div>';
      $("#doUpdate").addEventListener("click", function () {
        $("#doUpdate").disabled = true;
        body.update = true;
        post(body).then(handle).catch(function (e) {
          showError(e.message || "提交失败");
          $("#doUpdate").disabled = false;
        });
      });
      $("#doCancel").addEventListener("click", function () {
        renderState();
      });
      applyTheme();
      return;
    }
    var title = out.state === "updated" ? "信息已更新" : "报名成功";
    var tail = out.state === "updated"
      ? "已按本次填写覆盖此前的记录。"
      : "已记录，当前共 " + out.count + " 人报名。";
    $("#body").innerHTML = '<div class="ci-done" style="text-align:left">' +
      "<h2 style='margin:0 0 8px'>" + title + "</h2>" +
      "<p class='hint'>" + tail + "</p></div>";
    applyTheme();
  }

  function renderState() {
    var s = data.signup;
    $("#title").textContent = s.title || "报名";
    document.title = (s.title || "报名") + " · CAEventManager";
    $("#note").textContent = s.note || "";
    var bits = [s.statusLabel];
    if (s.capacity) bits.push("已报 " + s.entries + " / " + s.capacity);
    else if (s.entries) bits.push("已报 " + s.entries + " 人");
    if (s.opensAt) bits.push("开始 " + when(s.opensAt));
    if (s.closesAt) bits.push("截止 " + when(s.closesAt));
    $("#status").textContent = bits.join(" · ");
    if (s.status === "open") {
      renderForm();
    } else {
      var why = {
        upcoming: ["本次报名尚未开始", s.opensAt ? "开始时间：" + when(s.opensAt) : ""],
        ended: ["本次报名已结束", s.closesAt ? "结束时间：" + when(s.closesAt) : ""],
        disabled: ["本次报名已关闭", "如需报名，请联系活动负责人"],
        full: ["名额已满", s.capacity ? "上限 " + s.capacity + " 人" : ""]
      }[s.status] || ["本次报名不接受提交", ""];
      $("#body").innerHTML =
        '<div class="blocked"><b>' + esc(why[0]) + "</b>" +
        (why[1] ? "<span>" + esc(why[1]) + "</span>" : "") + "</div>";
    }
    applyTheme();
  }

  function boot() {
    fetch(BASE + "/api/state", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (out) {
        if (!out || out.ok === false) throw new Error((out && out.error) || "报名不存在");
        data = out;
        renderState();
      })
      .catch(function (e) {
        $("#body").innerHTML = '<p class="hint">' + esc(e.message || "载入失败") + "</p>";
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
