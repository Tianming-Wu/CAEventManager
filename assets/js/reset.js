/* 临时密码的账号：登录后落在这里，只做一件事——设置新密码。
   状态一变（别处已经设过、或者被停用），这一页自己退回主站，停在上面也做不了任何操作。 */
(function () {
  "use strict";
  var form = document.getElementById("resetForm");
  var go = document.getElementById("go");
  var err = document.getElementById("err");

  function leave(d) {
    if (!d || !d.authed) { location.replace(PX.PREFIX + "/login"); return; }
    if (!d.mustReset) { location.replace(PX.PREFIX + "/panel"); }
  }

  // 一直盯着状态：账号状态一旦正常，这一页立刻失效
  var watch = function () {
    PX.api("/me").then(leave).catch(function () { /* 断网就等下一轮 */ });
  };
  watch();
  setInterval(watch, 5000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) watch();
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.textContent = "";
    var one = document.getElementById("newPass").value;
    var two = document.getElementById("newPass2").value;
    if (one.length < 8) { err.textContent = "新密码至少 8 位"; return; }
    if (one !== two) { err.textContent = "两次输入的密码不一致"; return; }
    go.disabled = true;
    PX.api("/password/reset", { method: "POST", body: { new: one, confirm: two } })
      .then(function () { location.replace(PX.PREFIX + "/panel"); })
      .catch(function (e2) {
        if (e2 && e2.status === 409) { location.replace(PX.PREFIX + "/panel"); return; }
        err.textContent = e2.message || "未能保存，请稍后重试";
        go.disabled = false;
      });
  });
})();
