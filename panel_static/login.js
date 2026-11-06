/* Login form. */
(function () {
  "use strict";

  // ?s_return=... - sent here when a page was refused.  Two shapes are allowed,
  // and nothing else: a panel route id (a page was refused inside the panel) or
  // an absolute path on this host (the activity console, which signs in here
  // now).  Neither can point at another site.
  //
  // Everything is built from the mount prefix: the login page itself can sit at
  // <mount>/panel/login (a trailing slash on the console address gets there), and
  // a relative "panel" would then resolve to <mount>/panel/panel.
  function returnTarget() {
    var m = /[?&]s_return=([^&]*)/.exec(location.search || "");
    if (!m) return "";
    var value = "";
    try { value = decodeURIComponent(m[1]); } catch (e) { return ""; }
    if (/^[a-z][a-z-]{0,30}$/.test(value)) return PX.PREFIX + "/panel#/" + value;
    // a single leading slash: no scheme, no host, no protocol-relative "//"
    if (/^\/(?!\/)[\w\-./%~]*$/.test(value)) return value;
    return "";
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
      location.href = returnTarget() || PX.PREFIX + "/panel";
    }).catch(function (e) {
      err.textContent = e.message || "登录失败";
      go.disabled = false;
      document.getElementById("password").select();
    });
  });
})();
