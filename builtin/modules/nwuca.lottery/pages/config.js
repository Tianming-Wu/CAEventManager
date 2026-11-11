/* 「奖票设置」页：活动的基本信息、奖票池与算法、奖品、公示页二维码。
 * 平台在活动后台启动后按 pages 声明载入本文件，然后调 MODULE.pages.config.mount()。
 */
window.MODULE = window.MODULE || { pages: {} };

(function () {
  var LT, ctx, S, lib, bound = false;
  /* 操作员正在改的表单不被别的会话的重画盖掉：改过就只由「保存」重置 */
  var formTouched = false;

  function normalizePrizes(list) {
    return (list || []).map(function (p) {
      if (typeof p === "string") return { name: p, count: 1, mode: "ticket", batch: false };
      return {
        name: (p && p.name) || "",
        count: Number(p && p.count) || 0,
        mode: p && p.mode === "person" ? "person" : "ticket",
        batch: !!(p && p.batch)
      };
    }).filter(function (p) { return p.name; });
  }

  // 展示屏幕：可选的服务端清单，来自「外部资源 › 展示屏幕」
  function renderScreenPackChoices(current) {
    var box = lib.$("#setScreenPack");
    if (!box) return;
    var packs = S.screenPacks || [];
    box.innerHTML = '<option value="">活动自带</option>' + packs.map(function (id) {
      return '<option value="' + LT.esc(id) + '">' + LT.esc(id) + "</option>";
    }).join("");
    // 引用的那份要是已经被删掉，仍然把它列出来，别让选择悄悄地变回"自带"
    if (current && packs.indexOf(current) < 0) {
      box.insertAdjacentHTML("beforeend",
        '<option value="' + LT.esc(current) + '">' + LT.esc(current) + "（已不存在）</option>");
    }
    box.value = current || "";
  }

  function updatePoolPreview() {
    var st = lib.stats(S);
    var auto = (S.config.pool_mode || "auto") !== "fixed";
    lib.$("#poolPreview").innerHTML =
      "当前：到场 <b>" + st.checkin + "</b> 人 → 奖票池 <b>" + st.pool_size + "</b> 张" +
      "（保底 " + st.guaranteed + " + 速度奖励 " + st.bonus_pool + "）" +
      (auto ? "。<br>之后每多签到一人，池子自动 +3。" : "。<br>固定模式下池子不随人数变化。");
    var mode = S.config.pool_mode || "auto";
    lib.$("#setPool").disabled = auto;
    // a field that does not apply is hidden, not merely disabled
    lib.$("#poolExprField").hidden = mode !== "expr";
  }

  function renderQR() {
    var url = (S.publicBaseUrl || S.config.public_base_url || "").trim();
    var holder = lib.$("#qrHolder");
    if (!holder) return;
    if (!url) {
      holder.innerHTML = '<p class="note">先填写公示页地址。</p>';
      return;
    }
    var full = url.replace(/\/+$/, "") + "/";
    try {
      var qr = qrcode(0, "M");
      qr.addData(full);
      qr.make();
      holder.innerHTML =
        '<div style="background:#fff;padding:10px;border-radius:10px;width:190px">' +
        qr.createSvgTag({ cellSize: 5, margin: 2, scalable: true }) + "</div>" +
        '<div><p class="note" style="word-break:break-all">' + LT.esc(full) + "</p>" +
        '<a class="btn sm" href="' + LT.esc(full) + '" target="_blank" rel="noopener">打开公示页 ↗</a></div>';
    } catch (e) {
      holder.innerHTML = '<p class="note danger-text">二维码生成失败：' + LT.esc(e.message) + "</p>";
    }
  }

  function renderPrizeEditor() {
    var box = lib.$("#prizeEditor");
    if (!box) return;
    var prizes = normalizePrizes(S.config.prizes);
    box.innerHTML = prizes.map(function (p, i) {
      return '<div class="prize-row" data-i="' + i + '" data-batch="' + (p.batch ? "1" : "0") + '">' +
        '<input class="p-name" type="text" value="' + LT.esc(p.name) + '" placeholder="奖品名称">' +
        '<input class="p-count" type="number" min="0" max="999" value="' + p.count + '">' +
        '<select class="p-mode">' +
        '<option value="ticket"' + (p.mode === "ticket" ? " selected" : "") + ">按票</option>" +
        '<option value="person"' + (p.mode === "person" ? " selected" : "") + ">均等</option>" +
        "</select>" +
        '<button class="btn sm danger" data-prize-del="' + i + '">删除</button>' +
        "</div>";
    }).join("") || '<p class="note">还没有配置奖品</p>';
  }

  function render() {
    if (!S) return;
    var c = S.config || {};
    if (!formTouched) {
      lib.$("#setTitle").value = c.title || "";
      lib.$("#setSubtitle").value = c.subtitle || "";
      lib.$("#setBaseUrl").value = S.publicBaseUrl || c.public_base_url || "";
      lib.$("#setShowTimes").checked = !!c.show_times;
      lib.$("#setShowSid").checked = !!c.show_sid;
      lib.$("#setScreenWall").checked = !!c.screen_show_wall;
      renderScreenPackChoices(c.screen_pack || "");
      lib.$("#setPoolMode").value = c.pool_mode || "auto";
      lib.$("#setPool").value = c.pool_size;
      lib.$("#setPoolExpr").value = c.pool_expr || "";
      lib.$("#setBonusConstant").value = c.bonus_constant;
      lib.$("#setBeta").value = c.beta;
      lib.$("#setAllowBonus").checked = c.allow_bonus !== false;
      lib.$("#setUniqueInside").checked = c.draw_unique_inside !== false;
      lib.$("#setUniqueAcross").checked = c.draw_unique_across === true;
      renderPrizeEditor();
    }
    lib.$("#secretUrl").textContent = (S.publicBaseUrl || "") + "/";
    updatePoolPreview();
    renderQR();
  }

  function collectSettings() {
    return {
      // title is owned by the master panel now; sending it could overwrite it
      subtitle: lib.$("#setSubtitle").value.trim(),
      public_base_url: lib.$("#setBaseUrl").value.trim(),
      show_times: lib.$("#setShowTimes").checked,
      show_sid: lib.$("#setShowSid").checked,
      screen_show_wall: lib.$("#setScreenWall").checked,
      screen_pack: lib.$("#setScreenPack").value,
      pool_mode: lib.$("#setPoolMode").value,
      pool_size: Number(lib.$("#setPool").value),
      pool_expr: lib.$("#setPoolExpr").value.trim(),
      bonus_constant: Number(lib.$("#setBonusConstant").value),
      beta: Number(lib.$("#setBeta").value),
      allow_bonus: lib.$("#setAllowBonus").checked,
      draw_unique_inside: lib.$("#setUniqueInside").checked,
      draw_unique_across: lib.$("#setUniqueAcross").checked
    };
  }

  function saveSettings() {
    LT.api("/api/admin/settings", { method: "POST", body: collectSettings() })
      .then(function (d) {
        S.config = d.config;
        formTouched = false;   // what was typed is now what the server holds
        return lib.reloadState();
      })
      .then(function () {
        lib.$("#settingsNote").textContent = "已保存";
        lib.ok("设置已保存");
      })
      .catch(lib.bad);
  }

  function collectPrizes() {
    var rows = lib.$$("#prizeEditor .prize-row");
    if (!rows.length) return [];
    return rows.map(function (row) {
      return {
        name: lib.$(".p-name", row).value.trim(),
        count: Math.max(0, Number(lib.$(".p-count", row).value) || 0),
        mode: lib.$(".p-mode", row).value,
        batch: row.getAttribute("data-batch") === "1"
      };
    }).filter(function (p) { return p.name; });
  }

  function savePrizes() {
    LT.api("/api/admin/settings", { method: "POST", body: { prizes: collectPrizes() } })
      .then(function (d) {
        S.config = d.config;
        return lib.reloadState();
      })
      .then(function () {
        lib.$("#prizeNote").textContent = "已保存";
        lib.ok("奖品已保存");
        pageRefreshDraw();
      })
      .catch(lib.bad);
  }

  /* 奖品改了，抽奖页的奖品表要跟着变；两页都在模块里，直接叫它 */
  function pageRefreshDraw() {
    var draw = window.MODULE.pages.draw;
    if (draw && draw.refresh) draw.refresh(S);
  }

  function addPrizeRow() {
    S.config.prizes = collectPrizes().concat([{ name: "", count: 1, mode: "ticket", batch: false }]);
    renderPrizeEditor();
    pageRefreshDraw();
    var inputs = lib.$$("#prizeEditor .p-name");
    if (inputs.length) inputs[inputs.length - 1].focus();
  }

  function deletePrizeRow(index) {
    var kept = collectPrizes();
    kept.splice(index, 1);
    S.config.prizes = kept;
    renderPrizeEditor();
    pageRefreshDraw();
  }

  function bindOnce() {
    if (bound) return;
    bound = true;
    lib.$("#saveSettingsBtn").addEventListener("click", saveSettings);
    lib.$("#savePrizesBtn").addEventListener("click", savePrizes);
    lib.$("#prizeAddBtn").addEventListener("click", addPrizeRow);
    lib.$("#setPoolMode").addEventListener("change", function () {
      S.config.pool_mode = lib.$("#setPoolMode").value;
      updatePoolPreview();
    });
    lib.$("#setBonusConstant").addEventListener("input", updatePoolPreview);
    // 在这里敲过、点过的东西就比别的会话拉回来的状态重要
    var tab = lib.$("#tab-config");
    if (tab) {
      ["input", "change"].forEach(function (evt) {
        tab.addEventListener(evt, function (e) {
          if (e.target && e.target.tagName !== "BUTTON") formTouched = true;
        });
      });
    }
    document.addEventListener("click", function (e) {
      var del = e.target.getAttribute && e.target.getAttribute("data-prize-del");
      if (del === null || del === undefined) return;
      deletePrizeRow(Number(del));
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
