(function () {
  "use strict";

  const api = window.NextBotWebUIApi;
  if (!api) {
    console.error("NextBotWebUIApi 未加载");
    return;
  }

  const state = {
    shops: [],
    selectedShopId: null,
    selectedShopDetail: null,
    tiers: [],
    servers: [],
    editingShopId: null,
    editingItemId: null,
    pendingDeleteShop: null, // { id, name, itemCount }
    pendingDeleteItem: null, // { id, name }
    pendingImport: null,     // { fileName, payload, shopCount, itemCount, exportedAt }
    // H-3：写入飞行 guard，禁止双击重复请求（特别覆盖 import replace_all 这条破坏性路径）。
    submittingShop: false,
    submittingItem: false,
    deletingShop: false,
    deletingItem: false,
    importing: false,
  };

  const els = {};

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
    // M-16：记住打开 modal 前的焦点元素，关闭时恢复，保障键盘 / 屏幕阅读器流。
    modal._previousFocus = document.activeElement;
    modal.classList.remove("hidden");
  }

  function hideModal(modal) {
    if (!modal) return;
    modal.classList.add("hidden");
    // M-16：恢复打开前的焦点，避免焦点跳到 <body>。
    const prev = modal._previousFocus;
    modal._previousFocus = null;
    if (prev && typeof prev.focus === "function") {
      try { prev.focus(); } catch (_e) { /* ignore */ }
    }
  }

  function focusFirstField(modal) {
    if (!modal) return;
    const focusable = modal.querySelector(
      "input:not([type='hidden']):not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])"
    );
    if (focusable && typeof focusable.focus === "function") {
      try { focusable.focus(); } catch (_e) { /* ignore */ }
    }
  }

  function isAnySubmissionInFlight() {
    return state.submittingShop
      || state.submittingItem
      || state.deletingShop
      || state.deletingItem
      || state.importing;
  }

  async function callApi(url, opts = {}) {
    return api.apiRequest(url, opts);
  }

  // ---------- Data load ----------

  async function loadMeta() {
    try {
      const tierRes = await callApi("/webui/api/shops/meta/tiers", { action: "加载" });
      state.tiers = api.unwrapData(tierRes) || [];
    } catch (err) { state.tiers = []; }
    try {
      const srvRes = await callApi("/webui/api/shops/meta/servers", { action: "加载" });
      state.servers = api.unwrapData(srvRes) || [];
    } catch (err) { state.servers = []; }
  }

  async function loadShops() {
    // M-13：已选 shop 时与 list 并行拉取详情，省一次串行 RTT。
    const targetShopId = state.selectedShopId;
    const listPromise = callApi("/webui/api/shops", { action: "加载" });
    const detailPromise = targetShopId !== null
      ? callApi("/webui/api/shops/" + targetShopId, { action: "加载" })
      : Promise.resolve(null);
    let listRes, detailRes, listErr, detailErr;
    try { listRes = await listPromise; } catch (e) { listErr = e; }
    try { detailRes = await detailPromise; } catch (e) { detailErr = e; }
    if (listErr) {
      showAlert(els.alert, listErr.message || "加载失败", "error");
      return;
    }
    state.shops = api.unwrapData(listRes) || [];
    renderShopList();
    if (targetShopId !== null) {
      const exists = state.shops.find((s) => s.id === targetShopId);
      if (!exists) {
        state.selectedShopId = null;
        state.selectedShopDetail = null;
        renderShopDetail();
      } else if (detailErr) {
        // Detail load failed (selected shop may have been deleted concurrently);
        // surface the error and clear detail panel.
        state.selectedShopDetail = null;
        renderShopDetail();
        showAlert(els.alert, detailErr.message || "加载失败", "error");
      } else {
        state.selectedShopDetail = detailRes ? api.unwrapData(detailRes) : null;
        renderShopDetail();
      }
    }
  }

  async function loadShopDetail(shopId) {
    try {
      const res = await callApi("/webui/api/shops/" + shopId, { action: "加载" });
      state.selectedShopDetail = api.unwrapData(res);
      renderShopDetail();
    } catch (err) {
      showAlert(els.alert, err.message || "加载失败", "error");
    }
  }

  // ---------- Render ----------

  function renderShopList() {
    clearChildren(els.shopList);
    if (state.shops.length === 0) {
      els.shopListEmpty.classList.remove("hidden");
      return;
    }
    els.shopListEmpty.classList.add("hidden");
    state.shops.forEach((shop) => {
      const card = document.createElement("div");
      card.className = "shop-card" + (shop.id === state.selectedShopId ? " is-active" : "");

      const body = document.createElement("div");
      body.className = "shop-card-body";

      const title = document.createElement("div");
      title.className = "shop-card-title";
      const titleText = document.createElement("span");
      titleText.textContent = shop.name;
      title.appendChild(titleText);
      const status = document.createElement("span");
      status.className = "status-badge " + (shop.enabled ? "is-on" : "is-off");
      status.textContent = shop.enabled ? "上架" : "下架";
      title.appendChild(status);
      body.appendChild(title);

      if (shop.description) {
        const desc = document.createElement("div");
        desc.className = "shop-card-desc";
        desc.textContent = shop.description;
        body.appendChild(desc);
      }

      const meta = document.createElement("div");
      meta.className = "shop-card-meta";
      meta.textContent = "ID " + shop.id + "  ·  " + (shop.item_count || 0) + " 件商品  ·  排序 " + shop.sort_order;
      body.appendChild(meta);

      card.appendChild(body);

      const actions = document.createElement("div");
      actions.className = "shop-card-actions";
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "btn shop-card-edit-btn";
      editBtn.textContent = "编辑";
      editBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openShopModal(shop);
      });
      actions.appendChild(editBtn);
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "btn shop-card-edit-btn action-btn-danger";
      deleteBtn.textContent = "删除";
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openShopDeleteModal(shop);
      });
      actions.appendChild(deleteBtn);
      card.appendChild(actions);

      card.addEventListener("click", () => {
        state.selectedShopId = shop.id;
        renderShopList();
        loadShopDetail(shop.id);
      });

      els.shopList.appendChild(card);
    });
  }

  function renderShopDetail() {
    const detail = state.selectedShopDetail;
    if (!detail) {
      els.detailHead.classList.add("hidden");
      els.itemTableWrap.classList.add("hidden");
      els.itemEmpty.classList.add("hidden");
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

    els.detailSubtitle.textContent = "ID " + detail.id + "  ·  " + (detail.item_count || 0) + " 件商品  ·  排序 " + detail.sort_order;
    els.detailDesc.textContent = detail.description || "";
    els.detailDesc.style.display = detail.description ? "block" : "none";

    clearChildren(els.itemTbody);
    const items = Array.isArray(detail.items) ? detail.items : [];
    if (items.length === 0) {
      els.itemTableWrap.classList.add("hidden");
      els.itemEmpty.classList.remove("hidden");
    } else {
      els.itemEmpty.classList.add("hidden");
      els.itemTableWrap.classList.remove("hidden");
      items.forEach((it, idx) => els.itemTbody.appendChild(renderItemRow(it, idx + 1)));
    }
  }

  function renderItemRow(it, displayIndex) {
    const tr = document.createElement("tr");

    const tdIdx = document.createElement("td");
    tdIdx.className = "col-index";
    tdIdx.textContent = "#" + displayIndex;
    tr.appendChild(tdIdx);

    const tdKind = document.createElement("td");
    tdKind.className = "col-kind";
    const kind = document.createElement("span");
    kind.className = "kind-badge " + (it.kind === "item" ? "kind-item" : "kind-command");
    kind.textContent = it.kind === "item" ? "物品" : "指令";
    tdKind.appendChild(kind);
    tr.appendChild(tdKind);

    const tdName = document.createElement("td");
    tdName.className = "item-name-cell";
    const name = document.createElement("p");
    name.className = "item-name";
    name.textContent = it.name || "未命名";
    tdName.appendChild(name);
    if (it.description) {
      const desc = document.createElement("div");
      desc.className = "item-desc";
      desc.textContent = it.description;
      desc.title = it.description;
      tdName.appendChild(desc);
    }
    tr.appendChild(tdName);

    const tdPrice = document.createElement("td");
    tdPrice.className = "col-price";
    tdPrice.textContent = it.price + " 金币";
    tr.appendChild(tdPrice);

    const tdDetail = document.createElement("td");
    tdDetail.className = "item-detail-cell";
    if (it.kind === "item") {
      const line = document.createElement("div");
      line.className = "item-detail-line";
      const baseSpan = document.createElement("span");
      baseSpan.textContent = "物品 ID " + it.item_id + "  ·  前缀 " + it.prefix_id + "  ·  数量 ×" + it.quantity + "  ·  进度 " + (it.min_tier_label || it.min_tier);
      line.appendChild(baseSpan);
      if (it.actual_value !== null && it.actual_value !== undefined) {
        const av = document.createElement("span");
        av.className = "flag-chip flag-on";
        av.textContent = "实际单价 " + it.actual_value;
        line.appendChild(av);
      }
      if (it.is_mystery) {
        const m = document.createElement("span");
        m.className = "flag-chip flag-on";
        m.textContent = "盲盒";
        line.appendChild(m);
      }
      tdDetail.appendChild(line);
    } else {
      const targetLine = document.createElement("div");
      targetLine.className = "item-detail-line";
      const targetSpan = document.createElement("span");
      targetSpan.textContent = "目标：" + (it.target_server_label || "全部服务器");
      targetLine.appendChild(targetSpan);
      if (it.show_command) {
        const flag = document.createElement("span");
        flag.className = "flag-chip flag-on";
        flag.textContent = "展示命令";
        targetLine.appendChild(flag);
      }
      if (it.require_online) {
        const flag = document.createElement("span");
        flag.className = "flag-chip flag-on";
        flag.textContent = "要求在线";
        targetLine.appendChild(flag);
      }
      tdDetail.appendChild(targetLine);
      if (it.command_template) {
        const cmd = document.createElement("div");
        cmd.className = "item-detail-line";
        const cmdPreview = document.createElement("span");
        cmdPreview.className = "command-preview";
        cmdPreview.textContent = it.command_template;
        cmdPreview.title = it.command_template;
        cmd.appendChild(cmdPreview);
        tdDetail.appendChild(cmd);
      }
    }
    tr.appendChild(tdDetail);

    const tdStatus = document.createElement("td");
    tdStatus.className = "col-status";
    const stat = document.createElement("span");
    stat.className = "status-badge " + (it.enabled ? "is-on" : "is-off");
    stat.textContent = it.enabled ? "上架" : "下架";
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
    editBtn.addEventListener("click", () => openItemModal(it));
    wrap.appendChild(editBtn);
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn action-btn action-btn-danger";
    deleteBtn.textContent = "删除";
    deleteBtn.addEventListener("click", () => openItemDeleteModal(it));
    wrap.appendChild(deleteBtn);
    tdAct.appendChild(wrap);
    tr.appendChild(tdAct);

    return tr;
  }

  // ---------- Shop modal ----------

  function openShopModal(shop) {
    state.editingShopId = shop ? shop.id : null;
    hideAlert(els.shopModalAlert);
    els.shopModalTitle.textContent = shop ? "编辑商店" : "新建商店";
    els.shopFieldName.value = shop ? shop.name : "";
    els.shopFieldDescription.value = shop ? (shop.description || "") : "";
    els.shopFieldSortOrder.value = shop ? shop.sort_order : 0;
    els.shopFieldEnabled.checked = shop ? !!shop.enabled : true;
    showModal(els.shopModal);
    setTimeout(() => {
      try { els.shopFieldName.focus(); } catch (_e) { /* ignore */ }
    }, 30);
  }

  function closeShopModal() {
    hideModal(els.shopModal);
    state.editingShopId = null;
  }

  async function submitShopModal(ev) {
    ev.preventDefault();
    // H-3：飞行 guard，避免双击产生重复 create / update。
    if (state.submittingShop) return;
    hideAlert(els.shopModalAlert);
    const payload = {
      name: els.shopFieldName.value.trim(),
      description: els.shopFieldDescription.value.trim(),
      sort_order: Number(els.shopFieldSortOrder.value || 0),
      enabled: els.shopFieldEnabled.checked,
    };
    state.submittingShop = true;
    if (els.shopModalSave) els.shopModalSave.disabled = true;
    try {
      if (state.editingShopId === null) {
        await callApi("/webui/api/shops", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          action: "新建",
        });
      } else {
        await callApi("/webui/api/shops/" + state.editingShopId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          action: "保存",
        });
      }
      closeShopModal();
      await loadShops();
    } catch (err) {
      showAlert(els.shopModalAlert, err.message || "保存失败", "error");
    } finally {
      state.submittingShop = false;
      if (els.shopModalSave) els.shopModalSave.disabled = false;
    }
  }

  // ---------- Shop delete confirm modal ----------

  function openShopDeleteModal(shop) {
    // Direct call from a list-card "删除" button passes a shop object;
    // calls from the edit modal fall back to editingShopId.
    let target = shop || null;
    if (!target) {
      if (state.editingShopId === null) return;
      target = state.shops.find((s) => s.id === state.editingShopId) || null;
    }
    if (!target) return;
    // M-15：在确认 modal 内展示级联删除的商品数量，避免 admin 误删大店。
    const itemCount = Number(target.item_count || 0);
    state.pendingDeleteShop = { id: target.id, name: target.name || "", itemCount };
    els.shopDeleteName.textContent = state.pendingDeleteShop.name || ("ID " + state.pendingDeleteShop.id);
    if (els.shopDeleteItemCount) els.shopDeleteItemCount.textContent = String(itemCount);
    hideAlert(els.shopDeleteAlert);
    showModal(els.shopDeleteModal);
  }

  function closeShopDeleteModal() {
    hideModal(els.shopDeleteModal);
    state.pendingDeleteShop = null;
  }

  async function confirmDeleteShop() {
    if (!state.pendingDeleteShop) return;
    // H-3：飞行 guard，避免双击产生重复 DELETE。
    if (state.deletingShop) return;
    const id = state.pendingDeleteShop.id;
    state.deletingShop = true;
    if (els.shopDeleteConfirm) els.shopDeleteConfirm.disabled = true;
    try {
      await callApi("/webui/api/shops/" + id, { method: "DELETE", action: "删除" });
      const wasSelected = state.selectedShopId === id;
      closeShopDeleteModal();
      closeShopModal();
      if (wasSelected) {
        state.selectedShopId = null;
        state.selectedShopDetail = null;
      }
      await loadShops();
      renderShopDetail();
    } catch (err) {
      showAlert(els.shopDeleteAlert, err.message || "删除失败", "error");
    } finally {
      state.deletingShop = false;
      if (els.shopDeleteConfirm) els.shopDeleteConfirm.disabled = false;
    }
  }

  // ---------- Item modal ----------

  function fillTierOptions() {
    clearChildren(els.itemFieldMinTier);
    state.tiers.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.key;
      opt.textContent = t.label;
      els.itemFieldMinTier.appendChild(opt);
    });
  }

  function fillServerOptions() {
    clearChildren(els.itemFieldTargetServer);
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "全部服务器";
    els.itemFieldTargetServer.appendChild(all);
    state.servers.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = String(s.id);
      opt.textContent = s.id + ". " + s.name;
      els.itemFieldTargetServer.appendChild(opt);
    });
  }

  function applyKindVisibility() {
    const kind = els.itemFieldKind.value;
    if (kind === "item") {
      els.itemKindItemFields.classList.remove("hidden");
      els.itemKindCommandFields.classList.add("hidden");
    } else {
      els.itemKindItemFields.classList.add("hidden");
      els.itemKindCommandFields.classList.remove("hidden");
    }
  }

  // L-1：用户切换 kind 时，把对端字段重置回初始值，避免「保存的字段是哪一组」的视觉困惑。
  // 注意：openItemModal 用 applyKindVisibility 仅切显示，不应触发该重置。
  function handleKindUserChange() {
    const kind = els.itemFieldKind.value;
    if (kind === "item") {
      els.itemFieldTargetServer.value = "";
      els.itemFieldCommandTemplate.value = "";
      els.itemFieldShowCommand.checked = false;
      els.itemFieldRequireOnline.checked = false;
    } else {
      els.itemFieldItemId.value = 1;
      els.itemFieldPrefixId.value = 0;
      els.itemFieldQuantity.value = 1;
      els.itemFieldMinTier.value = "none";
      els.itemFieldActualValue.value = "";
      els.itemFieldIsMystery.checked = false;
    }
    applyKindVisibility();
  }

  function openItemModal(item) {
    if (state.selectedShopId === null) return;
    state.editingItemId = item ? item.id : null;
    hideAlert(els.itemModalAlert);
    els.itemModalTitle.textContent = item ? "编辑商品" : "新建商品";
    els.itemFieldName.value = item ? item.name : "";
    els.itemFieldDescription.value = item ? (item.description || "") : "";
    els.itemFieldKind.value = item ? item.kind : "item";
    els.itemFieldPrice.value = item ? item.price : 0;
    els.itemFieldSortOrder.value = item ? item.sort_order : 0;
    els.itemFieldEnabled.checked = item ? !!item.enabled : true;
    els.itemFieldItemId.value = item ? (item.item_id || 1) : 1;
    els.itemFieldPrefixId.value = item ? (item.prefix_id || 0) : 0;
    els.itemFieldQuantity.value = item ? (item.quantity || 1) : 1;
    els.itemFieldMinTier.value = item ? (item.min_tier || "none") : "none";
    els.itemFieldActualValue.value = (item && item.actual_value !== null && item.actual_value !== undefined)
      ? String(item.actual_value) : "";
    els.itemFieldIsMystery.checked = item ? !!item.is_mystery : false;
    els.itemFieldTargetServer.value = (item && item.target_server_id !== null && item.target_server_id !== undefined)
      ? String(item.target_server_id) : "";
    els.itemFieldCommandTemplate.value = item ? (item.command_template || "") : "";
    els.itemFieldShowCommand.checked = item ? !!item.show_command : false;
    els.itemFieldRequireOnline.checked = item ? !!item.require_online : false;
    applyKindVisibility();
    showModal(els.itemModal);
    setTimeout(() => {
      try { els.itemFieldName.focus(); } catch (_e) { /* ignore */ }
    }, 30);
  }

  function closeItemModal() {
    hideModal(els.itemModal);
    state.editingItemId = null;
  }

  async function submitItemModal(ev) {
    ev.preventDefault();
    // H-3：飞行 guard，避免双击产生重复 create / update。
    if (state.submittingItem) return;
    hideAlert(els.itemModalAlert);
    if (state.selectedShopId === null) return;
    const kind = els.itemFieldKind.value;
    const payload = {
      name: els.itemFieldName.value.trim(),
      description: els.itemFieldDescription.value.trim(),
      kind: kind,
      price: Number(els.itemFieldPrice.value || 0),
      sort_order: Number(els.itemFieldSortOrder.value || 0),
      enabled: els.itemFieldEnabled.checked,
    };
    if (kind === "item") {
      payload.item_id = Number(els.itemFieldItemId.value || 0);
      payload.prefix_id = Number(els.itemFieldPrefixId.value || 0);
      payload.quantity = Number(els.itemFieldQuantity.value || 1);
      payload.min_tier = els.itemFieldMinTier.value || "none";
      const av = els.itemFieldActualValue.value.trim();
      payload.actual_value = av === "" ? null : Number(av);
      payload.is_mystery = els.itemFieldIsMystery.checked;
    } else {
      const raw = els.itemFieldTargetServer.value;
      payload.target_server_id = raw ? Number(raw) : null;
      payload.command_template = els.itemFieldCommandTemplate.value;
      payload.show_command = els.itemFieldShowCommand.checked;
      payload.require_online = els.itemFieldRequireOnline.checked;
    }
    state.submittingItem = true;
    if (els.itemModalSave) els.itemModalSave.disabled = true;
    try {
      if (state.editingItemId === null) {
        await callApi("/webui/api/shops/" + state.selectedShopId + "/items", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          action: "新建",
        });
      } else {
        await callApi(
          "/webui/api/shops/" + state.selectedShopId + "/items/" + state.editingItemId,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            action: "保存",
          },
        );
      }
      closeItemModal();
      await loadShopDetail(state.selectedShopId);
      await loadShops();
    } catch (err) {
      showAlert(els.itemModalAlert, err.message || "保存失败", "error");
    } finally {
      state.submittingItem = false;
      if (els.itemModalSave) els.itemModalSave.disabled = false;
    }
  }

  // ---------- Item delete confirm modal ----------

  function openItemDeleteModal(item) {
    // Direct call from a row "删除" button passes an item object;
    // calls from the edit modal fall back to editingItemId.
    if (state.selectedShopId === null) return;
    let target = item || null;
    if (!target) {
      if (state.editingItemId === null) return;
      const items = state.selectedShopDetail && Array.isArray(state.selectedShopDetail.items)
        ? state.selectedShopDetail.items : [];
      target = items.find((it) => it.id === state.editingItemId) || null;
    }
    if (!target) return;
    state.pendingDeleteItem = { id: target.id, name: target.name || "" };
    els.itemDeleteName.textContent = state.pendingDeleteItem.name || ("ID " + state.pendingDeleteItem.id);
    hideAlert(els.itemDeleteAlert);
    showModal(els.itemDeleteModal);
  }

  function closeItemDeleteModal() {
    hideModal(els.itemDeleteModal);
    state.pendingDeleteItem = null;
  }

  async function confirmDeleteItem() {
    if (!state.pendingDeleteItem || state.selectedShopId === null) return;
    // H-3：飞行 guard，避免双击产生重复 DELETE。
    if (state.deletingItem) return;
    const id = state.pendingDeleteItem.id;
    state.deletingItem = true;
    if (els.itemDeleteConfirm) els.itemDeleteConfirm.disabled = true;
    try {
      await callApi(
        "/webui/api/shops/" + state.selectedShopId + "/items/" + id,
        { method: "DELETE", action: "删除" },
      );
      closeItemDeleteModal();
      closeItemModal();
      await loadShopDetail(state.selectedShopId);
      await loadShops();
    } catch (err) {
      showAlert(els.itemDeleteAlert, err.message || "删除失败", "error");
    } finally {
      state.deletingItem = false;
      if (els.itemDeleteConfirm) els.itemDeleteConfirm.disabled = false;
    }
  }

  // ---------- Export ----------

  async function handleExport() {
    hideAlert(els.alert);
    try {
      const res = await callApi("/webui/api/shops/export", { action: "导出" });
      const data = api.unwrapData(res);
      const json = JSON.stringify(data, null, 2);
      const blob = new Blob([json], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const stamp = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `nextbot-shops-${stamp}.json`;
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
    // Reset the input value so the same file can be reselected if user closes the modal.
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
      if (parsed.kind !== "shops") {
        showAlert(els.alert, api.buildActionFailureMessage("导入", "kind 必须为 shops"), "error");
        return;
      }
      if (parsed.version !== 1) {
        showAlert(els.alert, api.buildActionFailureMessage("导入", "version 必须为 1"), "error");
        return;
      }
      const shops = Array.isArray(parsed.shops) ? parsed.shops : [];
      const itemCount = shops.reduce(
        (sum, s) => sum + ((s && Array.isArray(s.items)) ? s.items.length : 0), 0,
      );
      state.pendingImport = {
        fileName: file.name,
        payload: parsed,
        shopCount: shops.length,
        itemCount,
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
    hideAlert(els.shopImportAlert);
    clearChildren(els.shopImportSummary);

    const lines = [
      ["文件", state.pendingImport.fileName],
      ["商店数", String(state.pendingImport.shopCount)],
      ["商品总数", String(state.pendingImport.itemCount)],
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
      els.shopImportSummary.appendChild(row);
    });

    // Reset radio to merge default
    const defaultRadio = document.querySelector('input[name="shop-import-mode"][value="merge"]');
    if (defaultRadio) defaultRadio.checked = true;
    // H-2：每次打开 modal 时清空 REPLACE 确认输入。
    if (els.shopImportReplaceConfirm) els.shopImportReplaceConfirm.value = "";
    refreshImportReplaceWarn();
    showModal(els.shopImportModal);
  }

  function refreshImportReplaceWarn() {
    const isReplace = document.querySelector('input[name="shop-import-mode"]:checked')?.value === "replace_all";
    if (isReplace) {
      els.shopImportReplaceWarn.classList.remove("hidden");
      // H-2：replace_all 必须额外输入 "REPLACE" 才能启用「导入」按钮。
      if (els.shopImportReplaceConfirmRow) els.shopImportReplaceConfirmRow.classList.remove("hidden");
    } else {
      els.shopImportReplaceWarn.classList.add("hidden");
      if (els.shopImportReplaceConfirmRow) els.shopImportReplaceConfirmRow.classList.add("hidden");
      // 切回 merge 时清空 REPLACE 输入，避免下次切到 replace_all 误启用。
      if (els.shopImportReplaceConfirm) els.shopImportReplaceConfirm.value = "";
    }
    refreshImportConfirmEnabled();
  }

  function refreshImportConfirmEnabled() {
    if (!els.shopImportConfirm) return;
    if (state.importing) {
      els.shopImportConfirm.disabled = true;
      return;
    }
    const isReplace = document.querySelector('input[name="shop-import-mode"]:checked')?.value === "replace_all";
    if (isReplace) {
      const typed = els.shopImportReplaceConfirm ? els.shopImportReplaceConfirm.value : "";
      els.shopImportConfirm.disabled = typed.trim() !== "REPLACE";
    } else {
      els.shopImportConfirm.disabled = false;
    }
  }

  async function confirmImport() {
    if (!state.pendingImport) return;
    // H-3：飞行 guard，特别保护 replace_all 这条破坏性路径不被双击。
    if (state.importing) return;
    const mode = document.querySelector('input[name="shop-import-mode"]:checked')?.value || "merge";
    // H-2：replace_all 强制输入 "REPLACE" 才能继续，防止误点。
    if (mode === "replace_all") {
      const typed = els.shopImportReplaceConfirm ? els.shopImportReplaceConfirm.value.trim() : "";
      if (typed !== "REPLACE") {
        showAlert(
          els.shopImportAlert,
          api.buildActionFailureMessage("导入", "请输入 REPLACE 以确认全量替换"),
          "error",
        );
        return;
      }
    }
    hideAlert(els.shopImportAlert);
    state.importing = true;
    refreshImportConfirmEnabled();
    try {
      await callApi(
        "/webui/api/shops/import?mode=" + encodeURIComponent(mode),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(state.pendingImport.payload),
          action: "导入",
        },
      );
      hideModal(els.shopImportModal);
      state.pendingImport = null;
      showAlert(els.alert, "导入成功", "success");
      await loadShops();
    } catch (err) {
      showAlert(els.shopImportAlert, err.message || "导入失败", "error");
    } finally {
      state.importing = false;
      refreshImportConfirmEnabled();
    }
  }

  // ---------- Bind ----------

  function bindEls() {
    els.alert = $("shop-alert");

    els.shopList = $("shop-list");
    els.shopListEmpty = $("shop-list-empty");
    els.shopReloadBtn = $("shop-reload-btn");
    els.shopCreateBtn = $("shop-create-btn");

    els.detailHead = $("shop-detail-head");
    els.detailTitle = $("shop-detail-title");
    els.detailSubtitle = $("shop-detail-subtitle");
    els.detailDesc = $("shop-detail-desc");
    els.detailPlaceholder = $("shop-detail-placeholder");
    els.itemCreateBtn = $("shop-item-create-btn");

    els.itemTableWrap = $("shop-item-table-wrap");
    els.itemTbody = $("shop-item-tbody");
    els.itemEmpty = $("shop-item-empty");

    els.shopModal = $("shop-modal");
    els.shopModalTitle = $("shop-modal-title");
    els.shopModalAlert = $("shop-modal-alert");
    els.shopModalForm = $("shop-modal-form");
    els.shopModalSave = $("shop-modal-save");
    els.shopFieldName = $("shop-field-name");
    els.shopFieldDescription = $("shop-field-description");
    els.shopFieldSortOrder = $("shop-field-sort-order");
    els.shopFieldEnabled = $("shop-field-enabled");

    els.shopDeleteModal = $("shop-delete-modal");
    els.shopDeleteAlert = $("shop-delete-alert");
    els.shopDeleteName = $("shop-delete-name");
    els.shopDeleteItemCount = $("shop-delete-item-count");
    els.shopDeleteConfirm = $("shop-delete-confirm");

    els.itemModal = $("item-modal");
    els.itemModalTitle = $("item-modal-title");
    els.itemModalAlert = $("item-modal-alert");
    els.itemModalForm = $("item-modal-form");
    els.itemModalSave = $("item-modal-save");
    els.itemFieldName = $("item-field-name");
    els.itemFieldDescription = $("item-field-description");
    els.itemFieldKind = $("item-field-kind");
    els.itemFieldPrice = $("item-field-price");
    els.itemFieldSortOrder = $("item-field-sort-order");
    els.itemFieldEnabled = $("item-field-enabled");
    els.itemKindItemFields = $("item-kind-item-fields");
    els.itemKindCommandFields = $("item-kind-command-fields");
    els.itemFieldItemId = $("item-field-item-id");
    els.itemFieldPrefixId = $("item-field-prefix-id");
    els.itemFieldQuantity = $("item-field-quantity");
    els.itemFieldMinTier = $("item-field-min-tier");
    els.itemFieldActualValue = $("item-field-actual-value");
    els.itemFieldIsMystery = $("item-field-is-mystery");
    els.itemFieldTargetServer = $("item-field-target-server");
    els.itemFieldCommandTemplate = $("item-field-command-template");
    els.itemFieldShowCommand = $("item-field-show-command");
    els.itemFieldRequireOnline = $("item-field-require-online");

    els.itemDeleteModal = $("item-delete-modal");
    els.itemDeleteAlert = $("item-delete-alert");
    els.itemDeleteName = $("item-delete-name");
    els.itemDeleteConfirm = $("item-delete-confirm");

    els.shopExportBtn = $("shop-export-btn");
    els.shopImportBtn = $("shop-import-btn");
    els.shopImportFile = $("shop-import-file");
    els.shopImportModal = $("shop-import-modal");
    els.shopImportAlert = $("shop-import-alert");
    els.shopImportSummary = $("shop-import-summary");
    els.shopImportReplaceWarn = $("shop-import-replace-warn");
    els.shopImportReplaceConfirmRow = $("shop-import-replace-confirm-row");
    els.shopImportReplaceConfirm = $("shop-import-replace-confirm");
    els.shopImportConfirm = $("shop-import-confirm");
  }

  function bindEvents() {
    els.shopReloadBtn.addEventListener("click", loadShops);
    els.shopCreateBtn.addEventListener("click", () => openShopModal(null));
    els.shopModalForm.addEventListener("submit", submitShopModal);
    els.shopDeleteConfirm.addEventListener("click", confirmDeleteShop);

    els.itemCreateBtn.addEventListener("click", () => openItemModal(null));
    els.itemModalForm.addEventListener("submit", submitItemModal);
    els.itemDeleteConfirm.addEventListener("click", confirmDeleteItem);
    // L-1：用户切换 kind 触发对端字段重置 + 显隐切换。
    els.itemFieldKind.addEventListener("change", handleKindUserChange);

    els.shopExportBtn.addEventListener("click", handleExport);
    els.shopImportBtn.addEventListener("click", () => els.shopImportFile.click());
    els.shopImportFile.addEventListener("change", handleImportFileChosen);
    els.shopImportConfirm.addEventListener("click", confirmImport);
    document.querySelectorAll('input[name="shop-import-mode"]').forEach((r) => {
      r.addEventListener("change", refreshImportReplaceWarn);
    });
    // H-2：REPLACE 确认输入变化时刷新「导入」按钮的 disabled 状态。
    if (els.shopImportReplaceConfirm) {
      els.shopImportReplaceConfirm.addEventListener("input", refreshImportConfirmEnabled);
    }

    // Generic close handlers (data-modal-close="<id>").
    // M-17：飞行中（submit / delete / import）禁止通过 mask / 关闭按钮关闭 modal，
    // 避免用户误以为已取消但后端事务仍在执行。
    document.querySelectorAll("[data-modal-close]").forEach((el) => {
      el.addEventListener("click", () => {
        if (isAnySubmissionInFlight()) return;
        const targetId = el.getAttribute("data-modal-close");
        const target = document.getElementById(targetId);
        if (target) hideModal(target);
      });
    });

    // Esc closes any visible modal.
    // M-17：与上面一致，飞行中拦截 ESC。
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (isAnySubmissionInFlight()) return;
      document.querySelectorAll(".modal:not(.hidden)").forEach((m) => hideModal(m));
    });
  }

  async function init() {
    bindEls();
    bindEvents();
    await loadMeta();
    fillTierOptions();
    fillServerOptions();
    await loadShops();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
