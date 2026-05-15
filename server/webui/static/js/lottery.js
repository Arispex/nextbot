(function () {
  "use strict";

  const api = window.NextBotWebUIApi;
  if (!api) {
    console.error("NextBotWebUIApi 未加载");
    return;
  }

  const state = {
    pools: [],
    selectedPoolId: null,
    selectedPoolDetail: null,
    tiers: [],
    servers: [],
    editingPoolId: null,
    editingPrizeId: null,
    editingPrizeOriginalKind: null,
    pendingDeletePool: null,
    pendingDeletePrize: null,
    pendingImport: null,
    pendingKindSwitch: null,
  };

  const els = {};

  // M-8 / L-6：AbortController 状态，避免快速切换 pool 时请求乱序、离开页面 fetch 悬挂。
  let poolsAbortController = null;
  let detailAbortController = null;

  // L-7：previousFocus 用于 modal 关闭后恢复键盘焦点。
  let previousFocus = null;

  // H-1：replace_all 高危操作要求用户精确键入此短语。
  const REPLACE_ALL_CONFIRM_PHRASE = "全量替换";

  function $(id) { return document.getElementById(id); }

  function clearChildren(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function showAlert(node, text, kind) {
    if (!node) return;
    const msg = node.querySelector(".alert-message") || node;
    msg.textContent = text;
    node.classList.remove("hidden", "success", "error");
    node.classList.add(kind === "error" ? "error" : "success");
  }

  function hideAlert(node) {
    if (!node) return;
    node.classList.add("hidden");
    const msg = node.querySelector(".alert-message");
    if (msg) msg.textContent = "";
  }

  function showModal(modal) {
    if (!modal) return;
    // L-7：保存当前焦点，关闭 modal 时恢复（仅在 modal 未已打开时保存）。
    if (modal.classList.contains("hidden")) {
      previousFocus = document.activeElement;
    }
    modal.classList.remove("hidden");
  }
  function hideModal(modal) {
    if (!modal) return;
    const wasOpen = !modal.classList.contains("hidden");
    modal.classList.add("hidden");
    // L-7：仅当本次确实关闭了一个可见的 modal 时才恢复焦点。
    // 嵌套 modal 已经被 hideOpenModals 全部 hide，本回调只恢复一次。
    if (wasOpen && previousFocus && typeof previousFocus.focus === "function" &&
        !document.querySelector(".modal:not(.hidden)")) {
      try { previousFocus.focus(); } catch (_e) { /* ignore */ }
      previousFocus = null;
    }
  }

  async function callApi(url, opts = {}) { return api.apiRequest(url, opts); }

  function formatProbabilityPct(n) {
    const v = Number(n) || 0;
    const r2 = Math.round(v * 100) / 100;
    const r1 = Math.round(v * 10) / 10;
    return r2 === r1 ? r1.toFixed(1) : r2.toFixed(2);
  }

  // ---------- Probability resolution ----------

  function resolveProbabilities(prizes) {
    const enabled = prizes.filter((p) => p.enabled);
    const setSum = enabled.reduce((acc, p) => acc + (p.weight !== null && p.weight !== undefined ? Number(p.weight) : 0), 0);
    const clampedSum = Math.max(0, Math.min(100, setSum));
    const remaining = Math.max(0, 100 - clampedSum);
    const unset = enabled.filter((p) => p.weight === null || p.weight === undefined);
    const perUnset = unset.length > 0 ? remaining / unset.length : 0;
    const map = new Map();
    enabled.forEach((p) => {
      const prob = (p.weight !== null && p.weight !== undefined) ? Math.max(0, Math.min(100, Number(p.weight))) : perUnset;
      map.set(p.id, prob);
    });
    const missPct = unset.length > 0 ? 0 : remaining;
    // L-1：当存在 unset prize 但剩余概率为 0 时，提示用户调低其他奖品。
    const unsetUnderflow = unset.length > 0 && remaining <= 0;
    return { map, missPct, unsetUnderflow, setSum };
  }

  // ---------- Data load ----------

  async function loadMeta() {
    try {
      const tierRes = await callApi("/webui/api/lottery/meta/tiers", { action: "加载进度选项" });
      state.tiers = api.unwrapData(tierRes) || [];
    } catch { state.tiers = []; }
    try {
      const srvRes = await callApi("/webui/api/lottery/meta/servers", { action: "加载服务器列表" });
      state.servers = api.unwrapData(srvRes) || [];
    } catch { state.servers = []; }
  }

  async function loadPools() {
    // M-8：abort 上一次 in-flight 请求，避免快速点击刷新时 race。
    if (poolsAbortController) {
      try { poolsAbortController.abort(); } catch (_e) { /* ignore */ }
    }
    poolsAbortController = new AbortController();
    const signal = poolsAbortController.signal;
    try {
      const res = await callApi("/webui/api/lottery", {
        action: "加载奖池列表",
        signal,
      });
      if (signal.aborted) return;
      state.pools = api.unwrapData(res) || [];
      renderPoolList();
      if (state.selectedPoolId !== null) {
        const exists = state.pools.find((p) => p.id === state.selectedPoolId);
        if (!exists) {
          state.selectedPoolId = null;
          state.selectedPoolDetail = null;
          renderPoolDetail();
        } else {
          await loadPoolDetail(state.selectedPoolId);
        }
      }
    } catch (err) {
      if (signal.aborted || (err && err.name === "AbortError")) return;
      // M-7：加载失败时清空选中态，避免后续 race 拿到旧 pool 引用。
      state.selectedPoolId = null;
      state.selectedPoolDetail = null;
      renderPoolDetail();
      showAlert(els.alert, err.message || "加载失败", "error");
    }
  }

  async function loadPoolDetail(poolId) {
    // M-8：abort 上一次 detail 请求，确保 A → B → A 顺序点击时不会渲染旧 pool。
    if (detailAbortController) {
      try { detailAbortController.abort(); } catch (_e) { /* ignore */ }
    }
    detailAbortController = new AbortController();
    const signal = detailAbortController.signal;
    try {
      const res = await callApi("/webui/api/lottery/" + poolId, {
        action: "加载奖池详情",
        signal,
      });
      if (signal.aborted) return;
      // M-7：用户在 await 期间切到其它 pool 时，本次结果作废。
      if (state.selectedPoolId !== poolId) return;
      state.selectedPoolDetail = api.unwrapData(res);
      renderPoolDetail();
    } catch (err) {
      if (signal.aborted || (err && err.name === "AbortError")) return;
      // M-7：detail 加载失败（典型场景：奖池被别的管理员删了 → 404），
      // 重置选中态让 UI 回到空态，而不是停留在旧数据上。
      if (state.selectedPoolId === poolId) {
        state.selectedPoolId = null;
        state.selectedPoolDetail = null;
        renderPoolDetail();
      }
      showAlert(els.alert, err.message || "加载失败", "error");
    }
  }

  // ---------- Render: pool list ----------

  function renderPoolList() {
    clearChildren(els.poolList);
    if (state.pools.length === 0) {
      els.poolListEmpty.classList.remove("hidden");
      return;
    }
    els.poolListEmpty.classList.add("hidden");
    state.pools.forEach((pool) => {
      const card = document.createElement("div");
      card.className = "lottery-card" + (pool.id === state.selectedPoolId ? " is-active" : "");

      const body = document.createElement("div");
      body.className = "lottery-card-body";

      const title = document.createElement("div");
      title.className = "lottery-card-title";
      const titleText = document.createElement("span");
      titleText.textContent = pool.name;
      title.appendChild(titleText);
      const status = document.createElement("span");
      status.className = "status-badge " + (pool.enabled ? "is-on" : "is-off");
      status.textContent = pool.enabled ? "上架" : "下架";
      title.appendChild(status);
      body.appendChild(title);

      if (pool.description) {
        const desc = document.createElement("div");
        desc.className = "lottery-card-desc";
        desc.textContent = pool.description;
        body.appendChild(desc);
      }

      const meta = document.createElement("div");
      meta.className = "lottery-card-meta";
      meta.textContent = "ID " + pool.id + "  ·  " + (pool.prize_count || 0) + " 件奖品  ·  💰 " + (pool.cost_per_draw || 0) + " / 次  ·  排序 " + pool.sort_order;
      body.appendChild(meta);
      card.appendChild(body);

      const actions = document.createElement("div");
      actions.className = "lottery-card-actions";
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "btn lottery-card-edit-btn";
      editBtn.textContent = "编辑";
      editBtn.addEventListener("click", (e) => { e.stopPropagation(); openPoolModal(pool); });
      actions.appendChild(editBtn);
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "btn lottery-card-edit-btn action-btn-danger";
      deleteBtn.textContent = "删除";
      deleteBtn.addEventListener("click", (e) => { e.stopPropagation(); openPoolDeleteModal(pool); });
      actions.appendChild(deleteBtn);
      card.appendChild(actions);

      card.addEventListener("click", () => {
        state.selectedPoolId = pool.id;
        renderPoolList();
        loadPoolDetail(pool.id);
      });
      els.poolList.appendChild(card);
    });
  }

  // ---------- Render: pool detail ----------

  function renderPoolDetail() {
    const detail = state.selectedPoolDetail;
    if (!detail) {
      els.detailHead.classList.add("hidden");
      els.prizeTableWrap.classList.add("hidden");
      els.prizeEmpty.classList.add("hidden");
      els.detailPlaceholder.classList.remove("hidden");
      return;
    }
    els.detailPlaceholder.classList.add("hidden");
    els.detailHead.classList.remove("hidden");

    clearChildren(els.detailTitle);
    const titleText = document.createElement("span");
    titleText.textContent = detail.name;
    els.detailTitle.appendChild(titleText);
    const status = document.createElement("span");
    status.className = "status-badge " + (detail.enabled ? "is-on" : "is-off");
    status.textContent = detail.enabled ? "上架" : "下架";
    els.detailTitle.appendChild(status);

    els.detailSubtitle.textContent = "ID " + detail.id + "  ·  " + (detail.prize_count || 0) + " 件奖品  ·  💰 " + (detail.cost_per_draw || 0) + " / 次  ·  排序 " + detail.sort_order;
    els.detailDesc.textContent = detail.description || "";
    els.detailDesc.style.display = detail.description ? "block" : "none";

    clearChildren(els.prizeTbody);
    const prizes = Array.isArray(detail.prizes) ? detail.prizes : [];
    if (prizes.length === 0) {
      els.prizeTableWrap.classList.add("hidden");
      els.prizeEmpty.classList.remove("hidden");
    } else {
      els.prizeEmpty.classList.add("hidden");
      els.prizeTableWrap.classList.remove("hidden");
      const probs = resolveProbabilities(prizes);
      prizes.forEach((p, idx) => els.prizeTbody.appendChild(
        renderPrizeRow(p, idx + 1, probs.map.get(p.id) || 0, probs.unsetUnderflow)
      ));
    }
  }

  function renderPrizeRow(prize, displayIndex, probabilityPct, unsetUnderflow) {
    const tr = document.createElement("tr");

    const tdIdx = document.createElement("td");
    tdIdx.className = "col-index";
    tdIdx.textContent = String(prize.id);
    tr.appendChild(tdIdx);

    const tdKind = document.createElement("td");
    tdKind.className = "col-kind";
    const kind = document.createElement("span");
    if (prize.kind === "item") {
      kind.className = "kind-badge kind-item"; kind.textContent = "物品";
    } else if (prize.kind === "command") {
      kind.className = "kind-badge kind-command"; kind.textContent = "指令";
    } else {
      const positive = Number(prize.coin_amount || 0) >= 0;
      kind.className = "kind-badge " + (positive ? "kind-coin-pos" : "kind-coin-neg");
      kind.textContent = positive ? "金币奖励" : "金币扣除";
    }
    tdKind.appendChild(kind);
    tr.appendChild(tdKind);

    const tdName = document.createElement("td");
    tdName.className = "item-name-cell";
    const name = document.createElement("p");
    name.className = "item-name";
    name.textContent = prize.name || "未命名";
    tdName.appendChild(name);
    if (prize.description) {
      const desc = document.createElement("div");
      desc.className = "item-desc";
      desc.textContent = prize.description;
      desc.title = prize.description;
      tdName.appendChild(desc);
    }
    tr.appendChild(tdName);

    const tdProb = document.createElement("td");
    tdProb.className = "col-price";
    const probChip = document.createElement("span");
    const isDefault = prize.weight === null || prize.weight === undefined;
    probChip.className = "weight-chip" + (isDefault ? " is-default" : "");
    // L-1：unset 且剩余概率为 0 时，提示用户调低其他奖品而不是误以为禁用。
    let suffix;
    if (isDefault) {
      suffix = unsetUnderflow ? "（剩余 0，请下调其他奖品）" : "（默认）";
    } else {
      suffix = "";
    }
    probChip.textContent = formatProbabilityPct(probabilityPct) + "%" + suffix;
    tdProb.appendChild(probChip);
    tr.appendChild(tdProb);

    const tdDetail = document.createElement("td");
    tdDetail.className = "item-detail-cell";
    if (prize.kind === "item") {
      const line = document.createElement("div");
      line.className = "item-detail-line";
      const baseSpan = document.createElement("span");
      baseSpan.textContent = "物品 ID " + prize.item_id + "  ·  前缀 " + prize.prefix_id + "  ·  数量 ×" + prize.quantity + "  ·  进度 " + (prize.min_tier_label || prize.min_tier);
      line.appendChild(baseSpan);
      if (prize.actual_value !== null && prize.actual_value !== undefined) {
        const av = document.createElement("span");
        av.className = "flag-chip flag-on";
        av.textContent = "实际单价 " + prize.actual_value;
        line.appendChild(av);
      }
      if (prize.is_mystery) {
        const m = document.createElement("span");
        m.className = "flag-chip flag-on";
        m.textContent = "盲盒";
        line.appendChild(m);
      }
      tdDetail.appendChild(line);
    } else if (prize.kind === "command") {
      const targetLine = document.createElement("div");
      targetLine.className = "item-detail-line";
      const targetSpan = document.createElement("span");
      targetSpan.textContent = "目标：" + (prize.target_server_label || "全部服务器");
      targetLine.appendChild(targetSpan);
      if (prize.show_command) {
        const f = document.createElement("span");
        f.className = "flag-chip flag-on"; f.textContent = "展示命令";
        targetLine.appendChild(f);
      }
      if (prize.require_online) {
        const f = document.createElement("span");
        f.className = "flag-chip flag-on"; f.textContent = "要求在线";
        targetLine.appendChild(f);
      }
      tdDetail.appendChild(targetLine);
      if (prize.command_template) {
        const cmd = document.createElement("div");
        cmd.className = "item-detail-line";
        const cmdPreview = document.createElement("span");
        cmdPreview.className = "command-preview";
        cmdPreview.textContent = prize.command_template;
        cmdPreview.title = prize.command_template;
        cmd.appendChild(cmdPreview);
        tdDetail.appendChild(cmd);
      }
    } else {  // coin
      const line = document.createElement("div");
      line.className = "item-detail-line";
      const amt = Number(prize.coin_amount || 0);
      line.textContent = (amt >= 0 ? "奖励 +" : "扣除 ") + amt + " 金币";
      tdDetail.appendChild(line);
    }
    tr.appendChild(tdDetail);

    const tdStatus = document.createElement("td");
    tdStatus.className = "col-status";
    const stat = document.createElement("span");
    stat.className = "status-badge " + (prize.enabled ? "is-on" : "is-off");
    stat.textContent = prize.enabled ? "上架" : "下架";
    tdStatus.appendChild(stat);
    tr.appendChild(tdStatus);

    const tdAct = document.createElement("td");
    tdAct.className = "col-actions";
    const wrap = document.createElement("div");
    wrap.className = "row-actions";
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn action-btn";
    editBtn.textContent = "编辑";
    editBtn.addEventListener("click", () => openPrizeModal(prize));
    wrap.appendChild(editBtn);
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn action-btn action-btn-danger";
    deleteBtn.textContent = "删除";
    deleteBtn.addEventListener("click", () => openPrizeDeleteModal(prize));
    wrap.appendChild(deleteBtn);
    tdAct.appendChild(wrap);
    tr.appendChild(tdAct);
    return tr;
  }

  // ---------- Pool modal ----------

  function openPoolModal(pool) {
    state.editingPoolId = pool ? pool.id : null;
    hideAlert(els.poolModalAlert);
    els.poolModalTitle.textContent = pool ? "编辑奖池" : "新建奖池";
    els.poolFieldName.value = pool ? pool.name : "";
    els.poolFieldDescription.value = pool ? (pool.description || "") : "";
    els.poolFieldCost.value = pool ? pool.cost_per_draw : 0;
    els.poolFieldSortOrder.value = pool ? pool.sort_order : 0;
    els.poolFieldEnabled.checked = pool ? !!pool.enabled : true;
    showModal(els.poolModal);
    setTimeout(() => els.poolFieldName.focus(), 30);
  }

  function closePoolModal() {
    hideModal(els.poolModal);
    state.editingPoolId = null;
  }

  async function submitPoolModal(ev) {
    ev.preventDefault();
    hideAlert(els.poolModalAlert);
    const payload = {
      name: els.poolFieldName.value.trim(),
      description: els.poolFieldDescription.value.trim(),
      cost_per_draw: Number(els.poolFieldCost.value || 0),
      sort_order: Number(els.poolFieldSortOrder.value || 0),
      enabled: els.poolFieldEnabled.checked,
    };
    const isCreate = state.editingPoolId === null;
    try {
      if (isCreate) {
        await callApi("/webui/api/lottery", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          action: "新建奖池",
        });
      } else {
        await callApi("/webui/api/lottery/" + state.editingPoolId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          action: "保存奖池",
        });
      }
      closePoolModal();
      await loadPools();
      showAlert(els.alert, isCreate ? "新建成功" : "保存成功", "success");
    } catch (err) {
      showAlert(els.poolModalAlert, err.message || "保存失败", "error");
    }
  }

  function openPoolDeleteModal(pool) {
    // Direct call from a list-card "删除" button passes a pool object;
    // calls from inside the edit modal fall back to editingPoolId.
    let target = pool || null;
    if (!target) {
      if (state.editingPoolId === null) return;
      target = state.pools.find((p) => p.id === state.editingPoolId) || null;
    }
    if (!target) return;
    state.pendingDeletePool = { id: target.id, name: target.name || "" };
    els.poolDeleteName.textContent = state.pendingDeletePool.name || ("ID " + state.pendingDeletePool.id);
    hideAlert(els.poolDeleteAlert);
    showModal(els.poolDeleteModal);
  }

  async function confirmDeletePool() {
    if (!state.pendingDeletePool) return;
    const id = state.pendingDeletePool.id;
    try {
      await callApi("/webui/api/lottery/" + id, { method: "DELETE", action: "删除奖池" });
      const wasSelected = state.selectedPoolId === id;
      hideModal(els.poolDeleteModal);
      state.pendingDeletePool = null;
      closePoolModal();
      if (wasSelected) {
        state.selectedPoolId = null;
        state.selectedPoolDetail = null;
      }
      await loadPools();
      renderPoolDetail();
      showAlert(els.alert, "删除成功", "success");
    } catch (err) {
      showAlert(els.poolDeleteAlert, err.message || "删除失败", "error");
    }
  }

  // ---------- Prize modal ----------

  function fillTierOptions() {
    clearChildren(els.prizeFieldMinTier);
    state.tiers.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.key; opt.textContent = t.label;
      els.prizeFieldMinTier.appendChild(opt);
    });
  }

  function fillServerOptions() {
    clearChildren(els.prizeFieldTargetServer);
    const all = document.createElement("option");
    all.value = ""; all.textContent = "全部服务器";
    els.prizeFieldTargetServer.appendChild(all);
    state.servers.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = String(s.id); opt.textContent = s.id + ". " + s.name;
      els.prizeFieldTargetServer.appendChild(opt);
    });
  }

  function applyKindVisibility() {
    const kind = els.prizeFieldKind.value;
    els.prizeKindItemFields.classList.toggle("hidden", kind !== "item");
    els.prizeKindCommandFields.classList.toggle("hidden", kind !== "command");
    els.prizeKindCoinFields.classList.toggle("hidden", kind !== "coin");
  }

  // M-10：切到非当前 kind 时清空另两组字段，避免上一 kind 脏数据写入 DB。
  // 仅在 handleKindChange（用户主动切换）时调用，不在 openPrizeModal 初次填充时调用。
  function resetFieldsForOtherKinds(currentKind) {
    if (currentKind !== "item") {
      els.prizeFieldItemId.value = 1;
      els.prizeFieldPrefixId.value = 0;
      els.prizeFieldQuantity.value = 1;
      els.prizeFieldMinTier.value = "none";
      els.prizeFieldActualValue.value = "";
      els.prizeFieldIsMystery.checked = false;
    }
    if (currentKind !== "command") {
      els.prizeFieldTargetServer.value = "";
      els.prizeFieldCommandTemplate.value = "";
      els.prizeFieldShowCommand.checked = false;
      els.prizeFieldRequireOnline.checked = false;
    }
    if (currentKind !== "coin") {
      els.prizeFieldCoinAmount.value = "";
    }
  }

  // H-6：kind 切换确认。
  async function handleKindChange(ev) {
    const newKind = els.prizeFieldKind.value;
    // 仅在编辑已有 prize 且 kind 发生变化时确认。
    if (state.editingPrizeId !== null && state.editingPrizeOriginalKind &&
        state.editingPrizeOriginalKind !== newKind) {
      const prevLabel = kindLabel(state.editingPrizeOriginalKind);
      // 先 revert 视觉值，避免 dialog 弹出期间显示新值（保留原 sync confirm 的视觉语义）。
      els.prizeFieldKind.value = state.editingPrizeOriginalKind;
      const ok = await window.webuiConfirm(
        `切换类型会清空「${prevLabel}」配置，确定继续？`,
        { title: "切换奖品类型", confirmText: "继续", danger: true }
      );
      if (!ok) {
        return;
      }
      // 用户确认后，应用新 kind 并把 originalKind 设为新值，避免反复弹窗。
      els.prizeFieldKind.value = newKind;
      state.editingPrizeOriginalKind = newKind;
    }
    // M-10：用户切 kind 时清空另两组字段。
    resetFieldsForOtherKinds(els.prizeFieldKind.value);
    applyKindVisibility();
  }

  function kindLabel(kind) {
    if (kind === "item") return "物品";
    if (kind === "command") return "指令";
    if (kind === "coin") return "金币";
    return kind;
  }

  function openPrizeModal(prize) {
    if (state.selectedPoolId === null) return;
    state.editingPrizeId = prize ? prize.id : null;
    // H-6：保存编辑前的 kind，用于 kind 切换二次确认。
    state.editingPrizeOriginalKind = prize ? (prize.kind || null) : null;
    hideAlert(els.prizeModalAlert);
    els.prizeModalTitle.textContent = prize ? "编辑奖品" : "新建奖品";
    els.prizeFieldName.value = prize ? prize.name : "";
    els.prizeFieldDescription.value = prize ? (prize.description || "") : "";
    els.prizeFieldKind.value = prize ? prize.kind : "item";
    els.prizeFieldWeight.value = (prize && prize.weight !== null && prize.weight !== undefined) ? String(prize.weight) : "";
    els.prizeFieldSortOrder.value = prize ? prize.sort_order : 0;
    els.prizeFieldEnabled.checked = prize ? !!prize.enabled : true;
    els.prizeFieldItemId.value = prize ? (prize.item_id || 1) : 1;
    els.prizeFieldPrefixId.value = prize ? (prize.prefix_id || 0) : 0;
    els.prizeFieldQuantity.value = prize ? (prize.quantity || 1) : 1;
    els.prizeFieldMinTier.value = prize ? (prize.min_tier || "none") : "none";
    els.prizeFieldActualValue.value = (prize && prize.actual_value !== null && prize.actual_value !== undefined) ? String(prize.actual_value) : "";
    els.prizeFieldIsMystery.checked = prize ? !!prize.is_mystery : false;
    els.prizeFieldTargetServer.value = (prize && prize.target_server_id !== null && prize.target_server_id !== undefined) ? String(prize.target_server_id) : "";
    els.prizeFieldCommandTemplate.value = prize ? (prize.command_template || "") : "";
    els.prizeFieldShowCommand.checked = prize ? !!prize.show_command : false;
    els.prizeFieldRequireOnline.checked = prize ? !!prize.require_online : false;
    els.prizeFieldCoinAmount.value = prize ? String(prize.coin_amount || 0) : "";
    applyKindVisibility();
    showModal(els.prizeModal);
    setTimeout(() => els.prizeFieldName.focus(), 30);
  }

  function closePrizeModal() {
    hideModal(els.prizeModal);
    state.editingPrizeId = null;
    state.editingPrizeOriginalKind = null;
  }

  async function submitPrizeModal(ev) {
    ev.preventDefault();
    hideAlert(els.prizeModalAlert);
    if (state.selectedPoolId === null) return;
    const kind = els.prizeFieldKind.value;
    const wRaw = els.prizeFieldWeight.value.trim();
    let weightValue = null;
    if (wRaw !== "") {
      const num = Number(wRaw);
      if (!Number.isFinite(num)) {
        showAlert(els.prizeModalAlert, "概率必须为有限数值", "error");
        return;
      }
      // M-9：钳制到 4 位小数，与后端 round(weight, 4) 一致。
      weightValue = Math.round(num * 10000) / 10000;
    }
    const payload = {
      name: els.prizeFieldName.value.trim(),
      description: els.prizeFieldDescription.value.trim(),
      kind: kind,
      sort_order: Number(els.prizeFieldSortOrder.value || 0),
      enabled: els.prizeFieldEnabled.checked,
      weight: weightValue,
    };
    if (kind === "item") {
      payload.item_id = Number(els.prizeFieldItemId.value || 0);
      payload.prefix_id = Number(els.prizeFieldPrefixId.value || 0);
      payload.quantity = Number(els.prizeFieldQuantity.value || 1);
      payload.min_tier = els.prizeFieldMinTier.value || "none";
      const av = els.prizeFieldActualValue.value.trim();
      payload.actual_value = av === "" ? null : Number(av);
      payload.is_mystery = els.prizeFieldIsMystery.checked;
    } else if (kind === "command") {
      const raw = els.prizeFieldTargetServer.value;
      payload.target_server_id = raw ? Number(raw) : null;
      payload.command_template = els.prizeFieldCommandTemplate.value;
      payload.show_command = els.prizeFieldShowCommand.checked;
      payload.require_online = els.prizeFieldRequireOnline.checked;
    } else {  // coin
      const c = els.prizeFieldCoinAmount.value.trim();
      payload.coin_amount = c === "" ? 0 : Number(c);
    }
    const isCreate = state.editingPrizeId === null;
    try {
      if (isCreate) {
        await callApi("/webui/api/lottery/" + state.selectedPoolId + "/prizes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          action: "新建奖品",
        });
      } else {
        await callApi("/webui/api/lottery/" + state.selectedPoolId + "/prizes/" + state.editingPrizeId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          action: "保存奖品",
        });
      }
      closePrizeModal();
      await loadPoolDetail(state.selectedPoolId);
      await loadPools();
      showAlert(els.alert, isCreate ? "新建成功" : "保存成功", "success");
    } catch (err) {
      showAlert(els.prizeModalAlert, err.message || "保存失败", "error");
    }
  }

  function openPrizeDeleteModal(prize) {
    // Direct call from a row "删除" button passes a prize object;
    // calls from the edit modal fall back to editingPrizeId.
    if (state.selectedPoolId === null) return;
    let target = prize || null;
    if (!target) {
      if (state.editingPrizeId === null) return;
      const prizes = state.selectedPoolDetail && Array.isArray(state.selectedPoolDetail.prizes)
        ? state.selectedPoolDetail.prizes : [];
      target = prizes.find((p) => p.id === state.editingPrizeId) || null;
    }
    if (!target) return;
    state.pendingDeletePrize = { id: target.id, name: target.name || "" };
    els.prizeDeleteName.textContent = state.pendingDeletePrize.name || ("ID " + state.pendingDeletePrize.id);
    hideAlert(els.prizeDeleteAlert);
    showModal(els.prizeDeleteModal);
  }

  async function confirmDeletePrize() {
    if (!state.pendingDeletePrize || state.selectedPoolId === null) return;
    const id = state.pendingDeletePrize.id;
    try {
      await callApi("/webui/api/lottery/" + state.selectedPoolId + "/prizes/" + id, {
        method: "DELETE", action: "删除奖品",
      });
      hideModal(els.prizeDeleteModal);
      state.pendingDeletePrize = null;
      closePrizeModal();
      await loadPoolDetail(state.selectedPoolId);
      await loadPools();
      showAlert(els.alert, "删除成功", "success");
    } catch (err) {
      showAlert(els.prizeDeleteAlert, err.message || "删除失败", "error");
    }
  }

  // ---------- Export ----------

  async function handleExport() {
    hideAlert(els.alert);
    try {
      const res = await callApi("/webui/api/lottery/export", { action: "导出" });
      const data = api.unwrapData(res);
      const json = JSON.stringify(data, null, 2);
      const blob = new Blob([json], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const stamp = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `nextbot-lottery-${stamp}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showAlert(els.alert, "导出成功", "success");
    } catch (err) {
      showAlert(els.alert, err.message || "导出失败", "error");
    }
  }

  // ---------- Import ----------

  function handleImportFileChosen(ev) {
    const input = ev.target;
    const file = input && input.files && input.files[0];
    if (input) input.value = "";
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      let parsed;
      try {
        parsed = JSON.parse(String(reader.result || ""));
      } catch (_e) {
        showAlert(els.alert, api.buildActionFailureMessage("导入", "文件不是有效的 JSON"), "error");
        return;
      }
      if (!parsed || typeof parsed !== "object") {
        showAlert(els.alert, api.buildActionFailureMessage("导入", "文件内容不是 JSON 对象"), "error");
        return;
      }
      if (parsed.kind !== "lottery_pools") {
        showAlert(els.alert, api.buildActionFailureMessage("导入", "kind 必须为 lottery_pools"), "error");
        return;
      }
      if (parsed.version !== 1) {
        showAlert(els.alert, api.buildActionFailureMessage("导入", "version 必须为 1"), "error");
        return;
      }
      const pools = Array.isArray(parsed.pools) ? parsed.pools : [];
      const prizeCount = pools.reduce(
        (sum, p) => sum + ((p && Array.isArray(p.prizes)) ? p.prizes.length : 0), 0,
      );
      state.pendingImport = {
        fileName: file.name,
        payload: parsed,
        poolCount: pools.length,
        prizeCount,
        exportedAt: typeof parsed.exported_at === "string" ? parsed.exported_at : "",
      };
      openImportModal();
    };
    reader.onerror = () => {
      showAlert(els.alert, api.buildActionFailureMessage("导入", "无法读取文件"), "error");
    };
    reader.readAsText(file, "utf-8");
  }

  function openImportModal() {
    if (!state.pendingImport) return;
    hideAlert(els.lotteryImportAlert);
    clearChildren(els.lotteryImportSummary);

    const lines = [
      ["文件", state.pendingImport.fileName],
      ["奖池数", String(state.pendingImport.poolCount)],
      ["奖品总数", String(state.pendingImport.prizeCount)],
    ];
    if (state.pendingImport.exportedAt) {
      lines.push(["导出时间", state.pendingImport.exportedAt]);
    }
    lines.forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "form-item form-item-full";
      const labelEl = document.createElement("span");
      labelEl.className = "form-label";
      labelEl.textContent = label;
      const valueEl = document.createElement("span");
      valueEl.textContent = value;
      row.appendChild(labelEl);
      row.appendChild(valueEl);
      els.lotteryImportSummary.appendChild(row);
    });

    const defaultRadio = document.querySelector('input[name="lottery-import-mode"][value="merge"]');
    if (defaultRadio) defaultRadio.checked = true;
    refreshImportReplaceWarn();
    showModal(els.lotteryImportModal);
  }

  function refreshImportReplaceWarn() {
    const isReplace = document.querySelector('input[name="lottery-import-mode"]:checked')?.value === "replace_all";
    if (isReplace) {
      els.lotteryImportReplaceWarn.classList.remove("hidden");
      if (els.lotteryImportConfirmField) {
        els.lotteryImportConfirmField.classList.remove("hidden");
      }
    } else {
      els.lotteryImportReplaceWarn.classList.add("hidden");
      if (els.lotteryImportConfirmField) {
        els.lotteryImportConfirmField.classList.add("hidden");
      }
      if (els.lotteryImportConfirmInput) {
        els.lotteryImportConfirmInput.value = "";
      }
    }
    refreshImportConfirmButton();
  }

  // H-1：根据模式与 confirm 输入框控制「导入」按钮启用状态。
  function refreshImportConfirmButton() {
    if (!els.lotteryImportConfirm) return;
    const mode = document.querySelector('input[name="lottery-import-mode"]:checked')?.value || "merge";
    if (mode === "replace_all") {
      const phrase = els.lotteryImportConfirmInput
        ? els.lotteryImportConfirmInput.value.trim() : "";
      els.lotteryImportConfirm.disabled = (phrase !== REPLACE_ALL_CONFIRM_PHRASE);
    } else {
      els.lotteryImportConfirm.disabled = false;
    }
  }

  async function confirmImport() {
    if (!state.pendingImport) return;
    const mode = document.querySelector('input[name="lottery-import-mode"]:checked')?.value || "merge";
    hideAlert(els.lotteryImportAlert);
    // H-1：replace_all 必须本地校验 confirm 字段，且作为 payload 字段透传到后端。
    let confirmPhrase = "";
    if (mode === "replace_all") {
      confirmPhrase = els.lotteryImportConfirmInput
        ? els.lotteryImportConfirmInput.value.trim() : "";
      if (confirmPhrase !== REPLACE_ALL_CONFIRM_PHRASE) {
        showAlert(els.lotteryImportAlert,
          `请在确认输入框中精确键入「${REPLACE_ALL_CONFIRM_PHRASE}」`, "error");
        return;
      }
    }
    const payloadToSend = mode === "replace_all"
      ? Object.assign({}, state.pendingImport.payload, { confirm: confirmPhrase })
      : state.pendingImport.payload;
    try {
      await callApi(
        "/webui/api/lottery/import?mode=" + encodeURIComponent(mode),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payloadToSend),
          action: "导入",
        },
      );
      hideModal(els.lotteryImportModal);
      state.pendingImport = null;
      if (els.lotteryImportConfirmInput) els.lotteryImportConfirmInput.value = "";
      showAlert(els.alert, "导入成功", "success");
      await loadPools();
    } catch (err) {
      showAlert(els.lotteryImportAlert, err.message || "导入失败", "error");
    }
  }

  // ---------- Bind ----------

  function bindEls() {
    els.alert = $("lottery-alert");
    els.poolList = $("pool-list");
    els.poolListEmpty = $("lottery-list-empty");
    els.reloadBtn = $("lottery-reload-btn");
    els.poolCreateBtn = $("pool-create-btn");
    els.detailHead = $("pool-detail-head");
    els.detailTitle = $("pool-detail-title");
    els.detailSubtitle = $("pool-detail-subtitle");
    els.detailDesc = $("pool-detail-desc");
    els.detailPlaceholder = $("lottery-detail-placeholder");
    els.prizeCreateBtn = $("prize-create-btn");
    els.prizeTableWrap = $("lottery-prize-table-wrap");
    els.prizeTbody = $("prize-tbody");
    els.prizeEmpty = $("lottery-prize-empty");

    els.poolModal = $("pool-modal");
    els.poolModalTitle = $("pool-modal-title");
    els.poolModalAlert = $("pool-modal-alert");
    els.poolModalForm = $("pool-modal-form");
    els.poolFieldName = $("pool-field-name");
    els.poolFieldDescription = $("pool-field-description");
    els.poolFieldCost = $("pool-field-cost");
    els.poolFieldSortOrder = $("pool-field-sort-order");
    els.poolFieldEnabled = $("pool-field-enabled");

    els.poolDeleteModal = $("pool-delete-modal");
    els.poolDeleteAlert = $("pool-delete-alert");
    els.poolDeleteName = $("pool-delete-name");
    els.poolDeleteConfirm = $("pool-delete-confirm");

    els.prizeModal = $("prize-modal");
    els.prizeModalTitle = $("prize-modal-title");
    els.prizeModalAlert = $("prize-modal-alert");
    els.prizeModalForm = $("prize-modal-form");
    els.prizeFieldName = $("prize-field-name");
    els.prizeFieldDescription = $("prize-field-description");
    els.prizeFieldKind = $("prize-field-kind");
    els.prizeFieldWeight = $("prize-field-weight");
    els.prizeFieldSortOrder = $("prize-field-sort-order");
    els.prizeFieldEnabled = $("prize-field-enabled");
    els.prizeKindItemFields = $("prize-kind-item-fields");
    els.prizeKindCommandFields = $("prize-kind-command-fields");
    els.prizeKindCoinFields = $("prize-kind-coin-fields");
    els.prizeFieldItemId = $("prize-field-item-id");
    els.prizeFieldPrefixId = $("prize-field-prefix-id");
    els.prizeFieldQuantity = $("prize-field-quantity");
    els.prizeFieldMinTier = $("prize-field-min-tier");
    els.prizeFieldActualValue = $("prize-field-actual-value");
    els.prizeFieldIsMystery = $("prize-field-is-mystery");
    els.prizeFieldTargetServer = $("prize-field-target-server");
    els.prizeFieldCommandTemplate = $("prize-field-command-template");
    els.prizeFieldShowCommand = $("prize-field-show-command");
    els.prizeFieldRequireOnline = $("prize-field-require-online");
    els.prizeFieldCoinAmount = $("prize-field-coin-amount");

    els.prizeDeleteModal = $("prize-delete-modal");
    els.prizeDeleteAlert = $("prize-delete-alert");
    els.prizeDeleteName = $("prize-delete-name");
    els.prizeDeleteConfirm = $("prize-delete-confirm");

    els.lotteryExportBtn = $("lottery-export-btn");
    els.lotteryImportBtn = $("lottery-import-btn");
    els.lotteryImportFile = $("lottery-import-file");
    els.lotteryImportModal = $("lottery-import-modal");
    els.lotteryImportAlert = $("lottery-import-alert");
    els.lotteryImportSummary = $("lottery-import-summary");
    els.lotteryImportReplaceWarn = $("lottery-import-replace-warn");
    els.lotteryImportConfirm = $("lottery-import-confirm");
    els.lotteryImportConfirmField = $("lottery-import-confirm-field");
    els.lotteryImportConfirmInput = $("lottery-import-confirm-input");
  }

  function bindEvents() {
    els.reloadBtn.addEventListener("click", loadPools);
    els.poolCreateBtn.addEventListener("click", () => openPoolModal(null));
    els.poolModalForm.addEventListener("submit", submitPoolModal);
    els.poolDeleteConfirm.addEventListener("click", confirmDeletePool);

    els.prizeCreateBtn.addEventListener("click", () => openPrizeModal(null));
    els.prizeModalForm.addEventListener("submit", submitPrizeModal);
    els.prizeDeleteConfirm.addEventListener("click", confirmDeletePrize);
    // H-6：kind change 走自定义 handler，必要时弹确认。
    els.prizeFieldKind.addEventListener("change", handleKindChange);

    els.lotteryExportBtn.addEventListener("click", handleExport);
    els.lotteryImportBtn.addEventListener("click", () => els.lotteryImportFile.click());
    els.lotteryImportFile.addEventListener("change", handleImportFileChosen);
    els.lotteryImportConfirm.addEventListener("click", confirmImport);
    document.querySelectorAll('input[name="lottery-import-mode"]').forEach((r) => {
      r.addEventListener("change", refreshImportReplaceWarn);
    });
    // H-1：confirm 输入框实时更新「导入」按钮启用状态。
    if (els.lotteryImportConfirmInput) {
      els.lotteryImportConfirmInput.addEventListener("input", refreshImportConfirmButton);
    }

    // M-11：通用 modal 关闭 dispatcher，复用 hideModal 以触发 previousFocus 恢复 + 清理 pending state。
    document.querySelectorAll("[data-modal-close]").forEach((el) => {
      el.addEventListener("click", () => {
        const targetId = el.getAttribute("data-modal-close");
        const target = document.getElementById(targetId);
        if (!target) return;
        hideModal(target);
        clearPendingForModal(targetId);
      });
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      const openModals = document.querySelectorAll(".modal:not(.hidden)");
      if (openModals.length === 0) return;
      openModals.forEach((m) => {
        hideModal(m);
        if (m.id) clearPendingForModal(m.id);
      });
    });

    // L-6：页面卸载时 abort 在飞请求，避免悬挂 fetch。
    window.addEventListener("beforeunload", () => {
      if (poolsAbortController) {
        try { poolsAbortController.abort(); } catch (_e) { /* ignore */ }
      }
      if (detailAbortController) {
        try { detailAbortController.abort(); } catch (_e) { /* ignore */ }
      }
    });
  }

  // M-11：modal 关闭时按 id 清理对应 pending state，避免 ESC / 取消时 state 残留。
  function clearPendingForModal(modalId) {
    if (modalId === "pool-delete-modal") {
      state.pendingDeletePool = null;
    } else if (modalId === "prize-delete-modal") {
      state.pendingDeletePrize = null;
    } else if (modalId === "lottery-import-modal") {
      state.pendingImport = null;
      if (els.lotteryImportConfirmInput) els.lotteryImportConfirmInput.value = "";
    } else if (modalId === "prize-modal") {
      state.editingPrizeId = null;
      state.editingPrizeOriginalKind = null;
    } else if (modalId === "pool-modal") {
      state.editingPoolId = null;
    }
  }

  async function init() {
    bindEls();
    bindEvents();
    await loadMeta();
    fillTierOptions();
    fillServerOptions();
    await loadPools();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
