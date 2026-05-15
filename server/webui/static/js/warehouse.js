(function () {
  "use strict";

  const api = window.NextBotWebUIApi;
  if (!api) {
    console.error("NextBotWebUIApi 未加载");
    return;
  }

  // H-4：数值字段上限，与后端 _ITEM_ID_MAX / _QUANTITY_MAX / _VALUE_MAX 对齐。
  const NUMERIC_LIMITS = {
    itemId: { min: 1, max: 999999, label: "物品 ID" },
    prefixId: { min: 0, max: 999999, label: "前缀 ID" },
    quantity: { min: 1, max: 9999, label: "数量" },
    value: { min: 0, max: 1000000000, label: "单价" },
  };
  // H-4：严格整数正则（拒绝小数 / 科学计数法 / 多余空白）。
  const INT_RE = /^-?\d+$/;

  const state = {
    user: null,           // { user_id, user_name }
    capacity: 100,
    used: 0,
    slots: new Map(),     // slot_index -> { item_id, prefix_id, quantity, min_tier, min_tier_label, value }
    tiers: [],            // [{ key, label }]
    editingSlot: null,    // current slot_index in modal
    modalReturnTarget: null,  // M-5：modal 打开时记录的触发元素，关闭时 restore focus
    deleteReturnTarget: null,
    dropdownActiveIndex: -1,  // M-4：dropdown 键盘导航高亮项
    dropdownUsers: [],        // M-4：dropdown 当前展示用户列表
    isSubmitting: false,      // M-3 / M-6：保存 / 删除提交中标记
    focusTimerId: null,       // M-5：modal focus setTimeout 句柄，关闭时清理
  };

  const TIER_RANK = new Map();   // tier key -> rank index for tier-chip styling

  const itemNameMap = new Map();
  const prefixNameMap = new Map();

  // H-3：模块级 AbortController，切换用户 / 重载时取消上一次未完成请求。
  let loadAbortController = null;
  let searchAbortController = null;

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
    // L-4：错误用 role="alert" + aria-live="assertive" 立即播报；成功用 role="status" + polite。
    if (kind === "error") {
      node.setAttribute("role", "alert");
      node.setAttribute("aria-live", "assertive");
    } else {
      node.setAttribute("role", "status");
      node.setAttribute("aria-live", "polite");
    }
  }

  function hideAlert(node) {
    if (!node) return;
    node.classList.add("hidden");
    const msg = node.querySelector(".alert-message");
    if (msg) msg.textContent = "";
  }

  function showModal(modal) {
    if (!modal) return;
    modal.classList.remove("hidden");
  }

  function hideModal(modal) {
    if (!modal) return;
    modal.classList.add("hidden");
  }

  // ---------- Data load ----------

  async function loadDicts() {
    try {
      const [itemRes, prefixRes] = await Promise.all([
        fetch("/assets/dicts/item.json"),
        fetch("/assets/dicts/prefix.json"),
      ]);
      if (itemRes.ok) {
        const list = await itemRes.json();
        if (Array.isArray(list)) list.forEach(function (e) {
          const id = Number(e && e.id || 0);
          const name = String(e && e.name || "").trim();
          if (id > 0 && name) itemNameMap.set(id, name);
        });
      } else {
        // M-9：HTTP 非 2xx 时也输出 console warning，避免静默退化到 "ID:N" 显示。
        console.warn("加载物品字典失败，HTTP " + itemRes.status);
      }
      if (prefixRes.ok) {
        const list = await prefixRes.json();
        if (Array.isArray(list)) list.forEach(function (e) {
          const id = Number(e && e.id || 0);
          const name = String(e && e.name || "").trim();
          if (id > 0 && name) prefixNameMap.set(id, name);
        });
      } else {
        console.warn("加载前缀字典失败，HTTP " + prefixRes.status);
      }
    } catch (e) {
      // M-9：网络异常 / JSON 解析失败时输出 console warning。
      console.warn("加载字典异常", e);
    }
  }

  async function loadTiers() {
    try {
      const payload = await api.apiRequest("/webui/api/warehouse/tiers", {
        method: "GET",
        headers: { "Accept": "application/json" },
        action: "加载",
        expectedStatus: 200,
      });
      const data = api.unwrapData(payload);
      state.tiers = Array.isArray(data) ? data : [];
      // Rank index for tier-chip color (skip "none", first real boss = rank 0)
      TIER_RANK.clear();
      let rank = 0;
      state.tiers.forEach(function (t) {
        if (t.key === "none") {
          TIER_RANK.set(t.key, -1);
        } else {
          TIER_RANK.set(t.key, rank);
          rank += 1;
        }
      });
    } catch (err) {
      showAlert(els.alert, err && err.message ? err.message : "加载失败", "error");
    }
  }

  function populateTierSelect(selectedKey) {
    if (!els.fieldMinTier) return;
    clearChildren(els.fieldMinTier);
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择进度";
    placeholder.disabled = true;
    if (!selectedKey) placeholder.selected = true;
    els.fieldMinTier.appendChild(placeholder);
    state.tiers.forEach(function (t) {
      const opt = document.createElement("option");
      opt.value = t.key;
      opt.textContent = t.label;
      if (selectedKey && t.key === selectedKey) opt.selected = true;
      els.fieldMinTier.appendChild(opt);
    });
  }

  // M-10：把 unwrapData 失败归类为 "加载失败，返回数据格式错误"，并保留 raw payload 给 console 调试。
  function safeUnwrapData(payload, contextLabel) {
    try {
      return api.unwrapData(payload);
    } catch (err) {
      console.warn(contextLabel + " 返回数据格式错误", payload);
      throw new Error("返回数据格式错误");
    }
  }

  async function loadWarehouse(userId) {
    hideAlert(els.alert);
    if (!userId) {
      showAlert(els.alert, "加载失败，请输入用户 QQ 或用户名", "error");
      return;
    }

    // H-3：abort 上一次未完成的 load 请求，避免快速切换用户时旧响应覆盖新响应。
    if (loadAbortController) {
      try { loadAbortController.abort(); } catch (_e) { /* ignore */ }
    }
    loadAbortController = new AbortController();
    const signal = loadAbortController.signal;

    try {
      const payload = await api.apiRequest(
        "/webui/api/warehouse?user_id=" + encodeURIComponent(userId),
        {
          method: "GET",
          headers: { "Accept": "application/json" },
          action: "加载",
          expectedStatus: 200,
          signal,
        }
      );
      const data = safeUnwrapData(payload, "加载仓库");
      state.user = { user_id: data.user_id, user_name: data.user_name };
      state.capacity = Number(data.capacity || 100);
      state.used = Number(data.used || 0);
      state.slots.clear();
      (data.slots || []).forEach(function (s) {
        state.slots.set(Number(s.slot_index), s);
      });
      renderSummary();
      renderGrid();
    } catch (err) {
      // H-3：abort 错误是用户主动操作（切换用户 / reload），不报错也不重置 UI。
      if (signal.aborted) return;
      showAlert(els.alert, err && err.message ? err.message : "加载失败", "error");
      hideAll();
    }
  }

  function hideAll() {
    state.user = null;
    els.summary.classList.add("hidden");
    els.gridCard.classList.add("hidden");
    els.empty.classList.remove("hidden");
  }

  function renderSummary() {
    if (!state.user) return hideAll();
    els.summary.classList.remove("hidden");
    els.summaryName.textContent = state.user.user_name || "(无用户名)";
    els.summaryQq.textContent = state.user.user_id;
    els.summaryUsed.textContent = String(state.used);
    els.summaryCapacity.textContent = String(state.capacity);
    els.empty.classList.add("hidden");
  }

  function renderGrid() {
    if (!state.user) return;
    els.gridCard.classList.remove("hidden");
    clearChildren(els.grid);
    for (let i = 1; i <= state.capacity; i++) {
      els.grid.appendChild(renderSlot(i, state.slots.get(i)));
    }
  }

  function renderSlot(slotIndex, slot) {
    const occupied = !!slot;
    const cell = document.createElement("div");
    cell.className = "wh-slot" + (occupied ? " is-occupied" : "");
    // M-4 / M-5：标记 slot_index，便于 H-3 partial update 通过 data 属性定位。
    cell.dataset.slotIndex = String(slotIndex);
    cell.addEventListener("click", function () { openModal(slotIndex, slot, cell); });

    const idEl = document.createElement("div");
    idEl.className = "wh-slot-id";
    idEl.textContent = "#" + slotIndex;
    cell.appendChild(idEl);

    const iconWrap = document.createElement("div");
    iconWrap.className = "wh-slot-icon";
    if (occupied) {
      const img = document.createElement("img");
      // M-8：防御性强转 integer，即便未来后端契约放宽也不会拼出穿越路径。
      const safeItemId = Math.max(0, Number(slot.item_id) | 0);
      img.src = "/assets/items/Item_" + safeItemId + ".png";
      img.alt = String(safeItemId);
      img.addEventListener("error", function () { img.style.display = "none"; });
      iconWrap.appendChild(img);
    } else {
      const empty = document.createElement("div");
      empty.className = "wh-slot-empty-icon";
      empty.textContent = "+";
      iconWrap.appendChild(empty);
    }
    cell.appendChild(iconWrap);

    if (!occupied) return cell;

    if (Number(slot.quantity || 0) > 1) {
      const stack = document.createElement("div");
      stack.className = "wh-slot-stack";
      stack.textContent = "×" + slot.quantity;
      cell.appendChild(stack);
    }

    const itemName = itemNameMap.get(Number(slot.item_id)) || ("ID:" + slot.item_id);
    const prefixId = Number(slot.prefix_id || 0);
    const prefixName = prefixId > 0 ? (prefixNameMap.get(prefixId) || "前缀 ID:" + prefixId) : "";

    if (prefixName) {
      const prefixEl = document.createElement("div");
      prefixEl.className = "wh-slot-prefix";
      prefixEl.textContent = prefixName;
      cell.appendChild(prefixEl);
    }

    const nameEl = document.createElement("div");
    nameEl.className = "wh-slot-name";
    nameEl.textContent = itemName;
    nameEl.title = (prefixName ? prefixName + " " : "") + itemName + " · " + (slot.min_tier_label || slot.min_tier || "");
    cell.appendChild(nameEl);

    if (slot.min_tier_label && slot.min_tier !== "none") {
      const tierEl = document.createElement("div");
      const rank = TIER_RANK.has(String(slot.min_tier)) ? TIER_RANK.get(String(slot.min_tier)) : -1;
      tierEl.className = "tier-chip" + (rank >= 0 ? " tier-" + rank : " tier-none");
      tierEl.textContent = slot.min_tier_label;
      cell.appendChild(tierEl);
    }

    if (Number(slot.value || 0) > 0) {
      const valueEl = document.createElement("div");
      valueEl.className = "wh-slot-value";
      // L-5：补单位与 form label "单价（金币 / 件）" 对齐。
      valueEl.textContent = "💰 " + slot.value + "/件";
      cell.appendChild(valueEl);
    }

    return cell;
  }

  // H-3：partial update — 单格 PUT / DELETE 成功后只重建该格 cell，不重拉整个仓库。
  function replaceSlotCell(slotIndex) {
    if (!els.grid) return;
    const oldCell = els.grid.querySelector('[data-slot-index="' + slotIndex + '"]');
    if (!oldCell) return;
    const newCell = renderSlot(slotIndex, state.slots.get(slotIndex));
    els.grid.replaceChild(newCell, oldCell);
  }

  // ---------- Modal ----------

  function openModal(slotIndex, slot, triggerEl) {
    state.editingSlot = slotIndex;
    state.modalReturnTarget = triggerEl || document.activeElement;
    hideAlert(els.modalAlert);
    els.modalTitle.textContent = slot ? "编辑物品" : "添加物品";
    els.fieldSlot.value = "#" + slotIndex;
    els.fieldItemId.value = slot ? String(slot.item_id) : "";
    els.fieldPrefixId.value = slot ? String(slot.prefix_id) : "0";
    els.fieldQuantity.value = slot ? String(slot.quantity) : "1";
    els.fieldValue.value = slot ? String(slot.value || 0) : "0";
    populateTierSelect(slot ? slot.min_tier : "");
    if (slot) {
      els.modalDelete.classList.remove("hidden");
    } else {
      els.modalDelete.classList.add("hidden");
    }
    showModal(els.modal);
    // M-5：用 rAF 替代 setTimeout(30) 并保留句柄；modal 已关闭时不再 focus。
    if (state.focusTimerId !== null) {
      cancelAnimationFrame(state.focusTimerId);
      state.focusTimerId = null;
    }
    state.focusTimerId = requestAnimationFrame(function () {
      state.focusTimerId = null;
      if (!els.modal.classList.contains("hidden") && els.fieldItemId) {
        els.fieldItemId.focus();
      }
    });
  }

  function closeModal() {
    state.editingSlot = null;
    if (state.focusTimerId !== null) {
      cancelAnimationFrame(state.focusTimerId);
      state.focusTimerId = null;
    }
    hideModal(els.modal);
    hideAlert(els.modalAlert);
    // M-5：恢复焦点到打开 modal 的触发元素。
    const restoreTarget = state.modalReturnTarget;
    state.modalReturnTarget = null;
    if (restoreTarget && typeof restoreTarget.focus === "function" && document.contains(restoreTarget)) {
      try { restoreTarget.focus(); } catch (_e) { /* ignore */ }
    }
  }

  // M-3：toggle 保存 / 删除按钮 + form 输入的禁用状态。
  function setSavePending(pending) {
    state.isSubmitting = !!pending;
    if (els.modalSave) els.modalSave.disabled = !!pending;
    if (els.modalDelete) els.modalDelete.disabled = !!pending;
    if (els.modalForm) {
      Array.prototype.forEach.call(els.modalForm.querySelectorAll("input, select"), function (n) {
        if (n.readOnly) return;
        n.disabled = !!pending;
      });
    }
    // M-6：提交中禁用 mask 点击 + 顶部 close 按钮。
    document.querySelectorAll('[data-modal-close="wh-modal"]').forEach(function (n) {
      if (pending) {
        n.setAttribute("data-disabled", "true");
      } else {
        n.removeAttribute("data-disabled");
      }
    });
  }

  function setDeletePending(pending) {
    state.isSubmitting = !!pending;
    if (els.deleteConfirm) els.deleteConfirm.disabled = !!pending;
    document.querySelectorAll('[data-modal-close="wh-delete-modal"]').forEach(function (n) {
      if (pending) {
        n.setAttribute("data-disabled", "true");
      } else {
        n.removeAttribute("data-disabled");
      }
    });
  }

  // H-4：严格整数解析；返回 { ok, value, message }。
  function parseStrictInt(raw, key) {
    const limit = NUMERIC_LIMITS[key];
    const trimmed = String(raw == null ? "" : raw).trim();
    if (trimmed === "") {
      return { ok: false, message: limit.label + "不能为空" };
    }
    if (!INT_RE.test(trimmed)) {
      return { ok: false, message: limit.label + "必须为整数" };
    }
    const n = Number(trimmed);
    if (!Number.isInteger(n)) {
      return { ok: false, message: limit.label + "必须为整数" };
    }
    if (n < limit.min) {
      return { ok: false, message: limit.label + "不能小于 " + limit.min };
    }
    if (n > limit.max) {
      return { ok: false, message: limit.label + "不能大于 " + limit.max };
    }
    return { ok: true, value: n };
  }

  async function saveModal(ev) {
    ev.preventDefault();
    if (!state.user || !state.editingSlot) return;
    if (state.isSubmitting) return;   // M-3：阻止重复提交
    hideAlert(els.modalAlert);

    const itemRes = parseStrictInt(els.fieldItemId.value, "itemId");
    if (!itemRes.ok) return showAlert(els.modalAlert, "保存失败，" + itemRes.message, "error");
    const prefixRes = parseStrictInt(els.fieldPrefixId.value, "prefixId");
    if (!prefixRes.ok) return showAlert(els.modalAlert, "保存失败，" + prefixRes.message, "error");
    const quantityRes = parseStrictInt(els.fieldQuantity.value, "quantity");
    if (!quantityRes.ok) return showAlert(els.modalAlert, "保存失败，" + quantityRes.message, "error");
    const valueRes = parseStrictInt(els.fieldValue.value, "value");
    if (!valueRes.ok) return showAlert(els.modalAlert, "保存失败，" + valueRes.message, "error");
    const minTier = els.fieldMinTier.value;
    if (!minTier) return showAlert(els.modalAlert, "保存失败，请选择最低进度", "error");

    setSavePending(true);
    try {
      const payload = await api.apiRequest(
        "/webui/api/warehouse/" + encodeURIComponent(state.user.user_id) + "/" + state.editingSlot,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            item_id: itemRes.value,
            prefix_id: prefixRes.value,
            quantity: quantityRes.value,
            value: valueRes.value,
            min_tier: minTier,
          }),
          action: "保存",
          expectedStatus: 200,
        }
      );
      const data = safeUnwrapData(payload, "保存仓库");
      const slotIndex = state.editingSlot;
      // H-3：本地 state + 单格 DOM 更新，避免全量重绘 100 格。
      const wasOccupied = state.slots.has(slotIndex);
      state.slots.set(slotIndex, {
        slot_index: slotIndex,
        item_id: data.item_id,
        prefix_id: data.prefix_id,
        quantity: data.quantity,
        value: data.value,
        min_tier: data.min_tier,
        min_tier_label: data.min_tier_label,
      });
      if (!wasOccupied) state.used += 1;
      replaceSlotCell(slotIndex);
      renderSummary();
      closeModal();
      // M-2 / M-11：toast 主句不含对象名 / 位置标识。
      showAlert(els.alert, "保存成功", "success");
    } catch (err) {
      showAlert(els.modalAlert, err && err.message ? err.message : "保存失败", "error");
    } finally {
      setSavePending(false);
    }
  }

  function openDeleteModal() {
    if (!state.user || !state.editingSlot) return;
    state.deleteReturnTarget = document.activeElement;
    hideAlert(els.deleteAlert);
    els.deleteSlot.textContent = "#" + state.editingSlot;
    showModal(els.deleteModal);
    // M-5：把焦点移到 confirm 按钮，方便 Enter 确认 / Esc 取消。
    requestAnimationFrame(function () {
      if (!els.deleteModal.classList.contains("hidden") && els.deleteConfirm) {
        els.deleteConfirm.focus();
      }
    });
  }

  function closeDeleteModal() {
    hideModal(els.deleteModal);
    const restoreTarget = state.deleteReturnTarget;
    state.deleteReturnTarget = null;
    if (restoreTarget && typeof restoreTarget.focus === "function" && document.contains(restoreTarget)) {
      try { restoreTarget.focus(); } catch (_e) { /* ignore */ }
    }
  }

  async function confirmDelete() {
    if (!state.user || !state.editingSlot) return;
    if (state.isSubmitting) return;
    hideAlert(els.deleteAlert);
    setDeletePending(true);
    try {
      await api.apiRequest(
        "/webui/api/warehouse/" + encodeURIComponent(state.user.user_id) + "/" + state.editingSlot,
        {
          method: "DELETE",
          headers: { "Accept": "application/json" },
          action: "删除",
          expectedStatus: 200,
        }
      );
      const slotIndex = state.editingSlot;
      // H-3：本地 state + 单格 DOM 更新。
      const wasOccupied = state.slots.has(slotIndex);
      state.slots.delete(slotIndex);
      if (wasOccupied && state.used > 0) state.used -= 1;
      closeDeleteModal();
      closeModal();
      replaceSlotCell(slotIndex);
      renderSummary();
      // M-2：toast 主句不含对象名 / 位置标识。
      showAlert(els.alert, "删除成功", "success");
    } catch (err) {
      showAlert(els.deleteAlert, err && err.message ? err.message : "删除失败", "error");
    } finally {
      setDeletePending(false);
    }
  }

  // ---------- Search dropdown ----------

  let searchTimer = null;
  let lastSearchKeyword = "";

  function showDropdown() {
    els.searchDropdown.classList.remove("hidden");
    if (els.searchInput) els.searchInput.setAttribute("aria-expanded", "true");
  }
  function hideDropdown() {
    els.searchDropdown.classList.add("hidden");
    state.dropdownActiveIndex = -1;
    state.dropdownUsers = [];
    if (els.searchInput) {
      els.searchInput.setAttribute("aria-expanded", "false");
      els.searchInput.removeAttribute("aria-activedescendant");
    }
  }

  function renderDropdownMessage(text) {
    if (!els.searchDropdown) return;
    clearChildren(els.searchDropdown);
    state.dropdownUsers = [];
    state.dropdownActiveIndex = -1;
    const msg = document.createElement("div");
    msg.className = "search-dropdown-empty";
    msg.textContent = text;
    els.searchDropdown.appendChild(msg);
    showDropdown();
  }

  function renderDropdownResults(users) {
    if (!els.searchDropdown) return;
    clearChildren(els.searchDropdown);
    if (!users.length) {
      renderDropdownMessage("无匹配用户");
      return;
    }
    state.dropdownUsers = users.slice();
    state.dropdownActiveIndex = -1;
    users.forEach(function (u, idx) {
      const item = document.createElement("div");
      item.className = "search-dropdown-item";
      // M-4：a11y — listbox option
      item.setAttribute("role", "option");
      item.id = "wh-search-option-" + idx;
      item.setAttribute("aria-selected", "false");
      item.addEventListener("click", function () {
        selectDropdownUser(u);
      });
      item.addEventListener("mouseenter", function () {
        setDropdownActive(idx);
      });

      const nameSpan = document.createElement("span");
      nameSpan.className = "name";
      nameSpan.textContent = String(u.name || "(无用户名)");
      item.appendChild(nameSpan);

      const qqSpan = document.createElement("span");
      qqSpan.className = "qq";
      qqSpan.textContent = String(u.user_id || "");
      item.appendChild(qqSpan);

      els.searchDropdown.appendChild(item);
    });
    els.searchDropdown.setAttribute("role", "listbox");
    showDropdown();
  }

  function selectDropdownUser(u) {
    if (!u) return;
    els.searchInput.value = String(u.name || "");
    hideDropdown();
    loadWarehouse(String(u.user_id));
  }

  function setDropdownActive(idx) {
    if (!els.searchDropdown) return;
    const items = els.searchDropdown.querySelectorAll(".search-dropdown-item");
    items.forEach(function (n, i) {
      if (i === idx) {
        n.classList.add("is-active");
        n.setAttribute("aria-selected", "true");
        if (els.searchInput) els.searchInput.setAttribute("aria-activedescendant", n.id);
      } else {
        n.classList.remove("is-active");
        n.setAttribute("aria-selected", "false");
      }
    });
    state.dropdownActiveIndex = idx;
  }

  async function searchUsers(keyword) {
    if (lastSearchKeyword === keyword) return;
    lastSearchKeyword = keyword;

    // H-3：abort 上一次未完成的搜索，避免 fast-typing 时旧响应覆盖新响应。
    if (searchAbortController) {
      try { searchAbortController.abort(); } catch (_e) { /* ignore */ }
    }
    searchAbortController = new AbortController();
    const signal = searchAbortController.signal;

    try {
      const url = "/webui/api/users?per_page=20" + (keyword ? "&q=" + encodeURIComponent(keyword) : "");
      const payload = await api.apiRequest(url, {
        method: "GET",
        headers: { "Accept": "application/json" },
        action: "搜索",
        expectedStatus: 200,
        signal,
      });
      const users = safeUnwrapData(payload, "搜索用户") || [];
      const current = (els.searchInput.value || "").trim().toLowerCase();
      if (current === keyword) {
        renderDropdownResults(Array.isArray(users) ? users.slice(0, 20) : []);
      }
    } catch (err) {
      if (signal.aborted) return;
      renderDropdownMessage(err && err.message ? err.message : "搜索失败");
    }
  }

  // ---------- Bind ----------

  function bindElements() {
    els.searchInput = $("wh-search-input");
    els.searchDropdown = $("wh-search-dropdown");
    els.searchWrap = els.searchInput ? els.searchInput.closest(".search-input-wrap") : null;
    els.reloadBtn = $("wh-reload-btn");
    els.summary = $("wh-summary");
    els.summaryName = $("wh-summary-name");
    els.summaryQq = $("wh-summary-qq");
    els.summaryUsed = $("wh-summary-used");
    els.summaryCapacity = $("wh-summary-capacity");
    els.gridCard = $("wh-grid-card");
    els.grid = $("wh-grid");
    els.empty = $("wh-empty");
    els.alert = $("wh-alert");
    els.modal = $("wh-modal");
    els.modalTitle = $("wh-modal-title");
    els.modalAlert = $("wh-modal-alert");
    els.modalForm = $("wh-modal-form");
    els.modalDelete = $("wh-modal-delete");
    els.modalSave = $("wh-modal-save");
    els.fieldSlot = $("wh-field-slot");
    els.fieldItemId = $("wh-field-item-id");
    els.fieldPrefixId = $("wh-field-prefix-id");
    els.fieldQuantity = $("wh-field-quantity");
    els.fieldValue = $("wh-field-value");
    els.fieldMinTier = $("wh-field-min-tier");
    els.deleteModal = $("wh-delete-modal");
    els.deleteAlert = $("wh-delete-alert");
    els.deleteSlot = $("wh-delete-slot");
    els.deleteConfirm = $("wh-delete-confirm");
  }

  function bindEvents() {
    els.searchInput.addEventListener("input", function () {
      const keyword = (els.searchInput.value || "").trim().toLowerCase();
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(function () { searchUsers(keyword); }, 200);
    });
    els.searchInput.addEventListener("focus", function () {
      const keyword = (els.searchInput.value || "").trim().toLowerCase();
      lastSearchKeyword = "__force__";
      searchUsers(keyword);
    });
    // M-4：dropdown 键盘导航（↑/↓/Enter/Esc）。
    els.searchInput.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        hideDropdown();
        return;
      }
      const count = state.dropdownUsers.length;
      if (count === 0) return;
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        setDropdownActive((state.dropdownActiveIndex + 1) % count);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        setDropdownActive((state.dropdownActiveIndex - 1 + count) % count);
      } else if (ev.key === "Enter") {
        if (state.dropdownActiveIndex >= 0 && state.dropdownActiveIndex < count) {
          ev.preventDefault();
          selectDropdownUser(state.dropdownUsers[state.dropdownActiveIndex]);
        }
      }
    });
    document.addEventListener("click", function (ev) {
      if (els.searchWrap && !els.searchWrap.contains(ev.target)) hideDropdown();
    });

    els.reloadBtn.addEventListener("click", function () {
      if (state.user && state.user.user_id) {
        loadWarehouse(state.user.user_id);
      }
    });

    els.modalForm.addEventListener("submit", saveModal);
    els.modalDelete.addEventListener("click", openDeleteModal);
    els.deleteConfirm.addEventListener("click", confirmDelete);

    document.querySelectorAll("[data-modal-close]").forEach(function (el) {
      el.addEventListener("click", function () {
        // M-6：提交中 mask / close 按钮被标记为 disabled，忽略点击避免丢失错误提示。
        if (el.getAttribute("data-disabled") === "true") return;
        const targetId = el.getAttribute("data-modal-close");
        if (targetId === "wh-modal") {
          closeModal();
        } else if (targetId === "wh-delete-modal") {
          closeDeleteModal();
        } else {
          const target = document.getElementById(targetId);
          if (target) target.classList.add("hidden");
        }
      });
    });

    // M-5 / M-7：集中 ESC dispatcher，只关最上层 modal，且走 closeModal / closeDeleteModal 以保证 state 清理 + focus 恢复。
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      if (state.isSubmitting) return;   // M-6：提交中拒绝 ESC 关闭
      // 优先级：delete > edit
      if (els.deleteModal && !els.deleteModal.classList.contains("hidden")) {
        closeDeleteModal();
      } else if (els.modal && !els.modal.classList.contains("hidden")) {
        closeModal();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", async function () {
    bindElements();
    bindEvents();
    await Promise.all([loadDicts(), loadTiers()]);

    try {
      const params = new URLSearchParams(window.location.search);
      const presetUserId = (params.get("user_id") || "").trim();
      if (presetUserId) {
        els.searchInput.value = presetUserId;
        await loadWarehouse(presetUserId);
        if (state.user && state.user.user_name) {
          els.searchInput.value = state.user.user_name;
        }
      }
    } catch (e) { /* ignore */ }
  });
})();
