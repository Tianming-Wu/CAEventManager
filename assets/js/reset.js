/* 临时密码的账号：登录后落在这里，只做一件事——给自己设一个新密码。 */
(function () {
  "use strict";
  var form = document.getElementById("resetForm");
  var go = document.getElementById("go");
  var err = document.getElementById("err");

  // 谁在重设：把名字写在副标题里，免得在别人的机器上改错账号
  PX.api("/me").then(function (d) {
    var me = d && d.me;
    if (!me) { location.href = "login"; return; }
    var who = document.getElementById("resetWho");
    if (who) {
      who.textContent = (me.name || me.username) +
        " 还在用临时密码，先给自己设一个新密码。";
    }
  }).catch(function () { /* 读不到就算了，副标题照旧 */ });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.textContent = "";
    var one = document.getElementById("newPass").value;
    var two = document.getElementById("newPass2").value;
    if (one.length < 8) { err.textContent = "新密码至少 8 位"; return; }
    if (one !== two) { err.textContent = "两次输入的密码不一致"; return; }
    go.disabled = true;
    PX.api("/password/reset", { method: "POST", body: { new: one, confirm: two } })
      .then(function () {
        location.href = "panel";
      })
      .catch(function (e2) {
        err.textContent = e2.message || "没改成，稍后再试";
        go.disabled = false;
      });
  });
})();
