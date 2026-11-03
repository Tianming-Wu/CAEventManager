/* Public 报名 page: one token in the address, no account, no session.
   Everything it needs comes from `api/state`; submitting posts one JSON body. */
(function () {
  "use strict";

  var BASE = location.pathname.replace(/\/+$/, "");
  var data = null;

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
    // 已经有字段写进院系/班级时，不再另外给一个同名输入框
    var takesDept = fields.some(function (f) { return f.into === "person.dept"; });
    var takesGrp = fields.some(function (f) { return f.into === "person.grp"; });
    $("#body").innerHTML =
      '<div class="field"><label for="sid">学号 <span class="req">*</span></label>' +
      '<input id="sid" type="text" autocomplete="off" maxlength="32"></div>' +
      '<div class="field"><label for="name">姓名 <span class="req">*</span></label>' +
      '<input id="name" type="text" autocomplete="off" maxlength="32"></div>' +
      (data.limited
        ? '<p class="hint">本次报名仅限名单内成员，学号与姓名需与名单记录一致。</p>'
        : (takesDept ? "" :
            '<div class="field"><label for="dept">院系</label>' +
            '<input id="dept" type="text" autocomplete="off" maxlength="40"></div>') +
          (takesGrp ? "" :
            '<div class="field"><label for="grp">班级</label>' +
            '<input id="grp" type="text" autocomplete="off" maxlength="40"></div>')) +
      fields.map(fieldHtml).join("") +
      '<button class="btn primary block" type="button" id="go">提交报名</button>';
    $("#go").addEventListener("click", submit);
    ["#sid", "#name", "#dept", "#grp"].forEach(function (sel) {
      var el = $(sel);
      if (el) {
        el.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); submit(); }
        });
      }
    });
  }

  function submit() {
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
    if (!body.sid || !body.name) {
      showError("学号与姓名均为必填");
      return;
    }
    $("#go").disabled = true;
    showError("");
    fetch(BASE + "/api/submit", {
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
    }).then(function (out) {
      $("#body").innerHTML = '<div class="ci-done" style="text-align:left">' +
        "<h2 style='margin:0 0 8px'>" + (out.duplicate ? "此前已报名" : "报名成功") + "</h2>" +
        "<p class='hint'>" + esc(body.name) + "（" + esc(body.sid) + "）" +
        (out.duplicate ? " 已在 " + when(out.ts) + " 报过名，无需重复提交。"
                       : " 已记录，当前共 " + out.count + " 人报名。") +
        "</p></div>";
    }).catch(function (e) {
      showError(e.message || "提交失败");
      $("#go").disabled = false;
    });
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
        upcoming: "本次报名尚未开始",
        ended: "本次报名已结束",
        closed: "本次报名已关闭",
        full: "名额已满"
      }[s.status] || "本次报名不接受提交";
      $("#body").innerHTML = '<p class="hint">' + esc(why) + "</p>";
    }
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
