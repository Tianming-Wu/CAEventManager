/* Login form. */
(function () {
  "use strict";
  var form = document.getElementById("loginForm");
  var go = document.getElementById("go");
  var err = document.getElementById("err");

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.textContent = "";
    go.disabled = true;
    PX.api("/login", {
      method: "POST",
      body: {
        username: document.getElementById("username").value.trim(),
        password: document.getElementById("password").value
      }
    }).then(function () {
      location.href = "panel";
    }).catch(function (e) {
      err.textContent = e.message || "登录失败";
      go.disabled = false;
      document.getElementById("password").select();
    });
  });
})();
