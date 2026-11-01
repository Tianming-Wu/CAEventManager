/* Login page. */
(function () {
  "use strict";
  var form = document.getElementById("loginForm");
  var user = document.getElementById("user");
  var pass = document.getElementById("pass");
  var err = document.getElementById("err");
  var go = document.getElementById("go");

  var remembered = localStorage.getItem("lt_user");
  if (remembered) user.value = remembered;

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.textContent = "";
    go.disabled = true;
    LT.api("/api/login", {
      method: "POST",
      body: {
        user: user.value.trim(),
        pass: pass.value,
        cookie_path: LT.PREFIX + "/"
      }
    })
      .then(function () {
        localStorage.setItem("lt_user", user.value.trim());
        location.href = LT.PREFIX + "/admin";
      })
      .catch(function (e) {
        err.textContent = e.message || "登录失败";
        pass.select();
        go.disabled = false;
      });
  });
})();
