/* Login form. */
(function () {
  "use strict";

  // ?s_return=<route> - sent here when a page was refused. Only a route id is
  // accepted, so the parameter can never point somewhere outside the panel.
  function returnRoute() {
    var m = /[?&]s_return=([^&]*)/.exec(location.search || "");
    if (!m) return "";
    var value = "";
    try { value = decodeURIComponent(m[1]); } catch (e) { return ""; }
    return /^[a-z][a-z-]{0,30}$/.test(value) ? value : "";
  }

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
      var back = returnRoute();
      location.href = back ? "panel#/" + back : "panel";
    }).catch(function (e) {
      err.textContent = e.message || "登录失败";
      go.disabled = false;
      document.getElementById("password").select();
    });
  });
})();
