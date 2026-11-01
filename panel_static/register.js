/* Registration form. */
(function () {
  "use strict";
  var form = document.getElementById("regForm");
  var go = document.getElementById("go");
  var err = document.getElementById("err");
  var done = document.getElementById("done");

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.textContent = "";
    go.disabled = true;
    PX.api("/register", {
      method: "POST",
      body: {
        username: document.getElementById("username").value.trim(),
        name: document.getElementById("name").value.trim(),
        password: document.getElementById("password").value,
        note: document.getElementById("note").value.trim()
      }
    }).then(function (d) {
      form.querySelectorAll("input,textarea,button").forEach(function (el) {
        el.disabled = true;
      });
      done.style.display = "block";
      done.textContent = "已提交，等待超级管理员审核。通过后即可用该账号登录。";
    }).catch(function (e) {
      err.textContent = e.message || "注册失败";
      go.disabled = false;
    });
  });
})();
