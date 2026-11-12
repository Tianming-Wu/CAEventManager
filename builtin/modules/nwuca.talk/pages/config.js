/* 「设置」页：这一套玩法只有一件事可调——公示页要不要显示提问。 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;
  var touched = false;

  function render() {
    lib.ready();
    if (touched) return;
    var box = lib.$("#setShowQuestions");
    if (box) box.checked = S.config.talk_show_questions !== false;
  }

  function save() {
    var on = lib.$("#setShowQuestions").checked;
    LT.api("/api/admin/settings", { method: "POST", body: { talk_show_questions: on } })
      .then(function () {
        touched = false;
        lib.$("#talkNote").textContent = "已保存";
        lib.ok("已保存");
        return lib.reloadState();
      })
      .catch(lib.fail);
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    lib.$("#saveTalkBtn").addEventListener("click", save);
    lib.$("#setShowQuestions").addEventListener("change", function () {
      touched = true;
    });
  }

  window.MODULE.pages.config = {
    mount: function (page) {
      LT = page.LT;
      ctx = page;
      S = page.state || {};
      lib = window.MODULE.lib;
      lib.bind(page);
      bindOnce();
    },
    refresh: function (state, tab) {
      S = state || S;
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      if (ctx && ctx.tab === "config") render();
    },
    show: function (tab) {
      lib.ready();
      if (ctx && tab) ctx.tab = tab;
      render();
    },
  };
})();
