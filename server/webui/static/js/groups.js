(() => {
  const reloadButton = document.getElementById("reload-btn");
  const addGroupButton = document.getElementById("add-group-btn");
  const searchInput = document.getElementById("group-search");

  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const loadingNode = document.getElementById("loading");
  const emptyNode = document.getElementById("empty");
  const tableWrapNode = document.getElementById("table-wrap");
  const tableBodyNode = document.getElementById("group-table-body");
  const paginationNode = document.getElementById("group-pagination");
  const paginationInfoNode = document.getElementById("group-pagination-info");
  const perPageSelect = document.getElementById("group-per-page");
  const prevPageButton = document.getElementById("group-prev-btn");
  const nextPageButton = document.getElementById("group-next-btn");

  const modalNode = document.getElementById("group-modal");
  const modalTitleNode = document.getElementById("group-modal-title");
  const modalAlertNode = document.getElementById("modal-alert");
  const modalAlertMessageNode = document.getElementById("modal-alert-message");
  const modalCloseButton = document.getElementById("modal-close-btn");
  const modalCancelButton = document.getElementById("modal-cancel-btn");
  const modalSaveButton = document.getElementById("modal-save-btn");
  const deleteModalNode = document.getElementById("delete-modal");
  const deleteModalTextNode = document.getElementById("delete-modal-text");
  const deleteModalAlertNode = document.getElementById("delete-modal-alert");
  const deleteModalAlertMessageNode = document.getElementById("delete-modal-alert-message");
  const deleteModalCloseButton = document.getElementById("delete-modal-close-btn");
  const deleteModalCancelButton = document.getElementById("delete-modal-cancel-btn");
  const deleteModalConfirmButton = document.getElementById("delete-modal-confirm-btn");

  const fieldName = document.getElementById("field-name");
  const fieldPermissions = document.getElementById("field-permissions");
  const fieldInherits = document.getElementById("field-inherits");
  const permissionPreviewNode = document.getElementById("permission-preview-list");
  const inheritPreviewNode = document.getElementById("inherit-preview-list");

  const requiredNodesReady = Boolean(
    reloadButton &&
      addGroupButton &&
      searchInput &&
      statusNode &&
      statusMessageNode &&
      loadingNode &&
      emptyNode &&
      tableWrapNode &&
      tableBodyNode &&
      paginationNode &&
      paginationInfoNode &&
      perPageSelect &&
      prevPageButton &&
      nextPageButton &&
      modalNode &&
      modalTitleNode &&
      modalAlertNode &&
      modalAlertMessageNode &&
      modalCloseButton &&
      modalCancelButton &&
      modalSaveButton &&
      deleteModalNode &&
      deleteModalTextNode &&
      deleteModalAlertNode &&
      deleteModalAlertMessageNode &&
      deleteModalCloseButton &&
      deleteModalCancelButton &&
      deleteModalConfirmButton &&
      fieldName &&
      fieldPermissions &&
      fieldInherits &&
      permissionPreviewNode &&
      inheritPreviewNode
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;
  // M-U-5: apiReady=false 时禁用所有交互控件，避免 api.apiRequest is not a function 整页崩溃。
  // 与 commands.js B-9 同形态。
  const apiReady = Boolean(
    api &&
      typeof api.apiRequest === "function" &&
      typeof api.unwrapData === "function" &&
      typeof api.unwrapMeta === "function"
  );

  const GROUP_NAME_PATTERN = /^[A-Za-z0-9一-鿿._-]{1,32}$/u;
  const ITEM_PATTERN = /^[^\s,]{1,256}$/u;

  let groupStates = [];
  let modalMode = "create";
  let editingGroupName = "";
  let modalSaving = false;
  let deletingGroup = null;
  let deleteSaving = false;
  let currentPage = 1;
  let currentPerPage = Number(perPageSelect.value || 10);
  let currentMeta = { total: 0, page: 1, per_page: currentPerPage, total_pages: 0 };

  // M-P-3 / M-P-4: search input debounce + AbortController，复用 commands.js / servers.js 模式。
  let searchDebounceTimer = null;
  let searchAbortController = null;

  // M-U-2 / M-U-3: modal focus 管理：记录每个 modal 打开前的 activeElement，关闭时恢复。
  const modalPreviousFocus = new WeakMap();
  const modalTrapHandlers = new WeakMap();

  // M-U-1: modal stack —— 同一时刻按打开顺序追踪 modal，ESC 仅作用于栈顶。
  const modalStack = [];
  const modalCloseRegistry = new WeakMap();
  const pushModalToStack = (node) => {
    if (!node) return;
    const idx = modalStack.lastIndexOf(node);
    if (idx >= 0) modalStack.splice(idx, 1);
    modalStack.push(node);
  };
  const popModalFromStack = (node) => {
    if (!node) return;
    const idx = modalStack.lastIndexOf(node);
    if (idx >= 0) modalStack.splice(idx, 1);
  };
  const registerModalCloser = (node, closer) => {
    if (!node || typeof closer !== "function") return;
    modalCloseRegistry.set(node, closer);
  };

  // M-U-4: 记录打开第一个 modal 前 body 的 inline overflow，关闭最后一个 modal 时恢复。
  let bodyOverflowBeforeModal = null;
  const lockBodyScroll = () => {
    if (modalStack.length !== 1) return;
    bodyOverflowBeforeModal = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  };
  const unlockBodyScroll = () => {
    if (modalStack.length > 0) return;
    document.body.style.overflow = bodyOverflowBeforeModal ?? "";
    bodyOverflowBeforeModal = null;
  };

  const setStatus = (message, type = "") => {
    const text = String(message || "").trim();
    if (!text) {
      statusNode.className = "alert hidden";
      statusMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    statusNode.className = `alert ${normalizedType}`;
    statusMessageNode.textContent = text;
  };

  const setModalAlert = (message = "", type = "info") => {
    const text = String(message || "").trim();
    if (!text) {
      modalAlertNode.className = "alert hidden modal-alert";
      modalAlertMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    modalAlertNode.className = `alert ${normalizedType} modal-alert`;
    modalAlertMessageNode.textContent = text;
  };

  const setDeleteModalAlert = (message = "", type = "info") => {
    const text = String(message || "").trim();
    if (!text) {
      deleteModalAlertNode.className = "alert hidden modal-alert";
      deleteModalAlertMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    deleteModalAlertNode.className = `alert ${normalizedType} modal-alert`;
    deleteModalAlertMessageNode.textContent = text;
  };

  // M-U-5: api 加载失败时，禁用所有交互入口避免后续 click 抛 TypeError。
  if (!apiReady) {
    setStatus("页面资源版本不一致，请刷新页面或重启机器人", "error");
    loadingNode.classList.add("hidden");
    reloadButton.disabled = true;
    addGroupButton.disabled = true;
    searchInput.disabled = true;
    perPageSelect.disabled = true;
    prevPageButton.disabled = true;
    nextPageButton.disabled = true;
    return;
  }

  const normalizeCsv = (raw, { fieldLabel }) => {
    const text = String(raw || "").trim();
    if (!text) {
      return "";
    }

    const values = [];
    for (const token of text.split(",")) {
      const value = token.trim();
      if (!value) {
        continue;
      }
      if (!ITEM_PATTERN.test(value)) {
        throw new Error(`${fieldLabel}项格式错误，不能包含空白或逗号，且长度 1-256`);
      }
      values.push(value);
    }

    return [...new Set(values)].sort((a, b) => a.localeCompare(b)).join(",");
  };

  const csvToArray = (raw) => {
    const text = String(raw || "").trim();
    if (!text) {
      return [];
    }
    return text.split(",").map((item) => item.trim()).filter(Boolean);
  };

  const renderTagBadges = (container, raw, noneText = "无") => {
    // 用 replaceChildren() 而非 innerHTML="" 清空，避免触发 XSS 风险扫描噪声。
    container.replaceChildren();
    const values = csvToArray(raw);
    if (!values.length) {
      const badge = document.createElement("span");
      badge.className = "tag-badge none";
      badge.textContent = noneText;
      container.appendChild(badge);
      return;
    }
    for (const value of values) {
      const badge = document.createElement("span");
      badge.className = "tag-badge";
      badge.textContent = value;
      container.appendChild(badge);
    }
  };

  const normalizeGroup = (item) => ({
    name: String(item?.name || ""),
    permissions: normalizeCsv(String(item?.permissions || ""), { fieldLabel: "权限" }),
    inherits: normalizeCsv(String(item?.inherits || ""), { fieldLabel: "继承" }),
    user_count: Number(item?.user_count || 0),
    builtin: Boolean(item?.builtin),
  });

  const renderTypeBadge = (builtin) => {
    const badge = document.createElement("span");
    badge.className = `group-type-badge ${builtin ? "builtin" : "normal"}`;
    badge.textContent = builtin ? "内置" : "普通";
    return badge;
  };

  const updatePagination = () => {
    const total = Number(currentMeta.total || 0);
    const page = Number(currentMeta.page || 1);
    const perPage = Number(currentMeta.per_page || currentPerPage);
    const totalPages = Number(currentMeta.total_pages || 0);

    perPageSelect.value = String(perPage);
    if (total <= 0) {
      paginationNode.classList.add("hidden");
      paginationInfoNode.textContent = "";
      prevPageButton.disabled = true;
      nextPageButton.disabled = true;
      return;
    }

    paginationNode.classList.remove("hidden");
    const start = (page - 1) * perPage + 1;
    const end = Math.min(total, start + Math.max(groupStates.length - 1, 0));
    paginationInfoNode.textContent = `第 ${page} / ${Math.max(totalPages, 1)} 页，共 ${total} 条，当前显示 ${start}-${end}`;
    prevPageButton.disabled = page <= 1;
    nextPageButton.disabled = totalPages <= 0 || page >= totalPages;
  };

  const renderTable = () => {
    tableBodyNode.replaceChildren();
    loadingNode.classList.add("hidden");

    if (!groupStates.length) {
      emptyNode.textContent = currentMeta.total > 0 ? "当前页暂无数据。" : "暂无身份组数据。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      updatePagination();
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const group of groupStates) {
      const row = document.createElement("tr");
      row.dataset.groupName = group.name;

      const nameCell = document.createElement("td");
      nameCell.className = "name-cell";
      const nameText = document.createElement("p");
      nameText.className = "name-text";
      nameText.textContent = group.name;
      nameCell.appendChild(nameText);

      const permissionCell = document.createElement("td");
      permissionCell.className = "permission-cell";
      const permissionList = document.createElement("div");
      permissionList.className = "tag-list";
      renderTagBadges(permissionList, group.permissions);
      permissionCell.appendChild(permissionList);

      const inheritCell = document.createElement("td");
      inheritCell.className = "inherit-cell";
      const inheritList = document.createElement("div");
      inheritList.className = "tag-list";
      renderTagBadges(inheritList, group.inherits);
      inheritCell.appendChild(inheritList);

      const userCountCell = document.createElement("td");
      userCountCell.className = "user-count-cell";
      userCountCell.textContent = Number(group.user_count || 0).toLocaleString("zh-CN");

      const typeCell = document.createElement("td");
      typeCell.className = "type-cell";
      typeCell.appendChild(renderTypeBadge(group.builtin));

      const actionCell = document.createElement("td");
      actionCell.className = "actions-cell";
      const actions = document.createElement("div");
      actions.className = "row-actions";

      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.className = "btn action-btn";
      editButton.textContent = "编辑";
      // L-U-3: 内置组编辑会立即生效到全局默认权限，hover 提示风险。
      if (group.builtin) {
        editButton.title = "编辑会立即影响全局默认权限，请谨慎";
      }
      editButton.addEventListener("click", () => {
        openModal("edit", group);
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "btn action-btn action-btn-danger";
      deleteButton.textContent = "删除";
      if (group.builtin) {
        deleteButton.disabled = true;
        deleteButton.title = "内置身份组不可删除";
      } else {
        deleteButton.addEventListener("click", () => {
          openDeleteModal(group);
        });
      }

      actions.appendChild(editButton);
      actions.appendChild(deleteButton);
      actionCell.appendChild(actions);

      row.appendChild(nameCell);
      row.appendChild(permissionCell);
      row.appendChild(inheritCell);
      row.appendChild(userCountCell);
      row.appendChild(typeCell);
      row.appendChild(actionCell);
      tableBodyNode.appendChild(row);
    }

    updatePagination();
  };

  const loadGroups = async ({ clearStatus = true, signal } = {}) => {
    if (clearStatus) {
      setStatus("");
    }
    loadingNode.classList.remove("hidden");
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");
    paginationNode.classList.add("hidden");

    try {
      const payload = await api.apiRequest(
        `/webui/api/groups?page=${encodeURIComponent(String(currentPage))}&per_page=${encodeURIComponent(String(currentPerPage))}&q=${encodeURIComponent(String(searchInput.value || "").trim())}`,
        {
          method: "GET",
          headers: { Accept: "application/json" },
          action: "加载",
          expectedStatus: 200,
          signal,
        }
      );
      // M-P-3: 若请求在 await 期间被 abort，直接静默返回不渲染过期结果。
      if (signal && signal.aborted) {
        return false;
      }
      const groups = api.unwrapData(payload);
      const meta = api.unwrapMeta(payload);
      if (!Array.isArray(groups)) {
        throw new Error("加载失败，返回数据格式错误");
      }

      currentMeta = {
        total: Number(meta.total || 0),
        page: Number(meta.page || currentPage),
        per_page: Number(meta.per_page || currentPerPage),
        total_pages: Number(meta.total_pages || 0),
      };
      currentPage = currentMeta.page;
      currentPerPage = currentMeta.per_page;
      groupStates = groups.map(normalizeGroup);

      renderTable();
      return true;
    } catch (error) {
      // M-P-3: AbortError 是预期路径，不展示错误。
      if (signal && signal.aborted) {
        return false;
      }
      if (error && (error.name === "AbortError" || error.code === "ABORT_ERR")) {
        return false;
      }
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      loadingNode.classList.add("hidden");
      emptyNode.classList.remove("hidden");
      emptyNode.textContent = message;
      tableWrapNode.classList.add("hidden");
      paginationNode.classList.add("hidden");
      return false;
    }
  };

  // M-P-3 / M-P-4: 取消 pending search debounce + abort 在飞 search 请求，
  // 供 reload / 分页 / per-page change 切换前调用。
  const cancelPendingSearch = () => {
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = null;
    }
    if (searchAbortController) {
      searchAbortController.abort();
      searchAbortController = null;
    }
  };

  // M-U-2: 收集 modal 内可聚焦元素，跳过 disabled / tabindex="-1"。
  const getFocusableInModal = (node) => {
    if (!node) return [];
    return Array.from(
      node.querySelectorAll(
        'a[href]:not([disabled]), area[href]:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [contenteditable]:not([contenteditable="false"]), [tabindex]:not([tabindex="-1"]):not([disabled])'
      )
    ).filter((el) => !el.classList.contains("hidden"));
  };

  // M-U-2: 构造 modal Tab 循环 handler。
  const buildTrapFocusHandler = (node) => (event) => {
    if (event.key !== "Tab") return;
    const focusables = getFocusableInModal(node);
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  // M-U-2 / M-U-3 / M-U-4: 打开 modal —— 记录 previousFocus、装载 focus trap、
  // 入栈、锁滚动；可选 focusTarget 指定初始焦点元素，默认聚焦首个可交互元素（跳过 close）。
  const openModalWithFocus = (node, { focusTarget } = {}) => {
    if (!node) return;
    if (!node.classList.contains("hidden")) return;
    modalPreviousFocus.set(node, document.activeElement);
    node.classList.remove("hidden");
    const handler = buildTrapFocusHandler(node);
    modalTrapHandlers.set(node, handler);
    node.addEventListener("keydown", handler);
    pushModalToStack(node);
    lockBodyScroll();
    setTimeout(() => {
      if (focusTarget && typeof focusTarget.focus === "function") {
        try {
          focusTarget.focus();
        } catch (_error) {
          focusTarget.focus();
        }
        return;
      }
      const focusables = getFocusableInModal(node);
      const preferred = focusables.find(
        (el) => !el.classList.contains("modal-close-btn")
      ) || focusables[0];
      if (preferred && typeof preferred.focus === "function") {
        preferred.focus();
      }
    }, 0);
  };

  // M-U-3 / M-U-4: 关闭 modal —— 卸载 trap、出栈、解锁滚动、恢复焦点；
  // previousFocus 已离开 DOM 时 fallback 到 reloadButton / main landmark。
  const closeModalAndRestoreFocus = (node) => {
    if (!node) return;
    node.classList.add("hidden");
    const handler = modalTrapHandlers.get(node);
    if (handler) {
      node.removeEventListener("keydown", handler);
      modalTrapHandlers.delete(node);
    }
    const previousFocus = modalPreviousFocus.get(node);
    modalPreviousFocus.delete(node);
    popModalFromStack(node);
    unlockBodyScroll();
    if (
      previousFocus &&
      document.contains(previousFocus) &&
      typeof previousFocus.focus === "function"
    ) {
      try {
        previousFocus.focus({ preventScroll: true });
      } catch (_error) {
        previousFocus.focus();
      }
      return;
    }
    const fallback = reloadButton || document.querySelector("main, [role=main]");
    if (fallback && typeof fallback.focus === "function") {
      const nativelyFocusable = ["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(
        fallback.tagName
      );
      if (!nativelyFocusable && !fallback.hasAttribute("tabindex")) {
        fallback.setAttribute("tabindex", "-1");
      }
      try {
        fallback.focus({ preventScroll: true });
      } catch (_error) {
        fallback.focus();
      }
    }
  };

  const closeModal = () => {
    if (modalSaving) {
      return;
    }
    closeModalAndRestoreFocus(modalNode);
  };

  const openDeleteModal = (group) => {
    deletingGroup = group;
    deleteSaving = false;
    deleteModalConfirmButton.disabled = false;
    setDeleteModalAlert("");
    // M-U-6: 进入新的 delete 流程时清空顶部 status，避免上次失败错误叠加在新弹窗上。
    setStatus("");
    // H-3: 提示用户删除会把 N 个成员回退到 default 组。
    // user_count 从 list 接口已返回的 group 数据直接读取，避免额外请求。
    const userCount = Number(group?.user_count || 0);
    const baseText = `确定删除身份组「${group.name}」吗？`;
    const affectedText = userCount > 0
      ? `当前有 ${userCount} 个用户将回退到 default 组。`
      : "";
    deleteModalTextNode.textContent = `${baseText}${affectedText}此操作不可恢复。`;
    openModalWithFocus(deleteModalNode, { focusTarget: deleteModalCancelButton });
  };

  const closeDeleteModal = (force = false) => {
    if (deleteSaving && !force) {
      return;
    }
    closeModalAndRestoreFocus(deleteModalNode);
    if (force || !deleteSaving) {
      deletingGroup = null;
    }
  };

  const updatePreview = () => {
    renderTagBadges(permissionPreviewNode, fieldPermissions.value);
    renderTagBadges(inheritPreviewNode, fieldInherits.value);
  };

  const openModal = (mode, group = null) => {
    modalMode = mode;
    modalSaving = false;
    editingGroupName = mode === "edit" && group ? group.name : "";
    setModalAlert("");

    if (mode === "edit" && group) {
      modalTitleNode.textContent = "编辑身份组";
      modalSaveButton.textContent = "保存";
      fieldName.value = group.name;
      fieldName.readOnly = true;
      fieldPermissions.value = group.permissions || "";
      fieldInherits.value = group.inherits || "";
    } else {
      modalTitleNode.textContent = "创建身份组";
      modalSaveButton.textContent = "创建";
      fieldName.value = "";
      fieldName.readOnly = false;
      fieldPermissions.value = "";
      fieldInherits.value = "";
    }

    updatePreview();
    const initialFocus = modalMode === "create" ? fieldName : fieldPermissions;
    openModalWithFocus(modalNode, { focusTarget: initialFocus });
  };

  const buildPayloadFromModal = () => {
    const name = String(fieldName.value || "").trim();
    const targetName = modalMode === "edit" ? editingGroupName : name;

    if (modalMode === "create") {
      if (!name) {
        throw new Error("身份组名称不能为空");
      }
      if (!GROUP_NAME_PATTERN.test(name)) {
        throw new Error("身份组名称格式错误，仅允许中文、英文、数字和 ._-，长度 1-32");
      }
    }

    const permissions = normalizeCsv(fieldPermissions.value, { fieldLabel: "权限" });
    const inherits = normalizeCsv(fieldInherits.value, { fieldLabel: "继承" });

    const inheritsValues = new Set(csvToArray(inherits));
    if (targetName && inheritsValues.has(targetName)) {
      throw new Error("继承列表不能包含自身");
    }

    return {
      name,
      permissions,
      inherits,
    };
  };

  const setModalSavingState = (saving) => {
    modalSaving = Boolean(saving);
    modalSaveButton.disabled = modalSaving;
    modalCancelButton.disabled = modalSaving;
    modalCloseButton.disabled = modalSaving;
  };

  const setDeleteSavingState = (saving) => {
    deleteSaving = Boolean(saving);
    deleteModalConfirmButton.disabled = deleteSaving;
    deleteModalCancelButton.disabled = deleteSaving;
    deleteModalCloseButton.disabled = deleteSaving;
  };

  const saveGroup = async () => {
    if (modalSaving) {
      return;
    }

    const isEdit = modalMode === "edit" && editingGroupName;

    let payload;
    try {
      payload = buildPayloadFromModal();
    } catch (error) {
      const message = error instanceof Error ? error.message : "表单校验失败";
      // 注：apiRequest catch 块 error.message 已含 "{action}失败，" 前缀，这里
      // 是表单校验本地抛错，需手动加前缀以保持文案一致。
      setModalAlert(`${isEdit ? "更新失败" : "创建失败"}，${message}`, "error");
      return;
    }

    setModalSavingState(true);
    // M-C-1: 使用中文省略号 …（U+2026），与 servers / commands prior art 对齐。
    setModalAlert("正在保存…", "info");

    try {
      const url = isEdit
        ? `/webui/api/groups/${encodeURIComponent(editingGroupName)}`
        : "/webui/api/groups";
      const method = isEdit ? "PUT" : "POST";
      const requestPayload = isEdit
        ? { permissions: payload.permissions, inherits: payload.inherits }
        : { name: payload.name, permissions: payload.permissions, inherits: payload.inherits };

      await api.apiRequest(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(requestPayload),
        action: isEdit ? "更新" : "创建",
        expectedStatus: isEdit ? 200 : 201,
      });

      setModalSavingState(false);
      closeModalAndRestoreFocus(modalNode);
      const reloaded = await loadGroups({ clearStatus: false });
      if (reloaded) {
        setStatus(isEdit ? "更新成功" : "创建成功", "success");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : (isEdit ? "更新失败" : "创建失败");
      setModalAlert(message, "error");
      setModalSavingState(false);
    }
  };

  const confirmDeleteGroup = async () => {
    if (!deletingGroup || deleteSaving) {
      return;
    }
    const targetGroup = deletingGroup;
    setDeleteSavingState(true);
    // M-U-7 / M-C-2: 删除中文案去对象名 + 用中文省略号，与 servers.js prior art 一致。
    setDeleteModalAlert("正在删除…", "warning");
    setStatus("正在删除…", "warning");
    try {
      await api.apiRequest(`/webui/api/groups/${encodeURIComponent(targetGroup.name)}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        action: "删除",
        expectedStatus: 204,
      });
      setDeleteSavingState(false);
      closeDeleteModal(true);
      const reloaded = await loadGroups({ clearStatus: false });
      if (reloaded) {
        setStatus("删除成功", "success");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除失败";
      setDeleteModalAlert(message, "error");
      setStatus(message, "error");
      setDeleteSavingState(false);
    }
  };

  reloadButton.addEventListener("click", () => {
    cancelPendingSearch();
    currentPage = 1;
    void loadGroups();
  });

  addGroupButton.addEventListener("click", () => {
    openModal("create");
  });

  // M-P-3: 搜索输入加 300ms debounce + AbortController 取消在飞请求，避免请求风暴 + 结果 race。
  searchInput.addEventListener("input", () => {
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
    }
    searchDebounceTimer = setTimeout(() => {
      if (searchAbortController) {
        searchAbortController.abort();
      }
      searchAbortController = new AbortController();
      currentPage = 1;
      void loadGroups({ signal: searchAbortController.signal });
    }, 300);
  });

  perPageSelect.addEventListener("change", () => {
    cancelPendingSearch();
    currentPerPage = Number(perPageSelect.value || 10);
    currentPage = 1;
    void loadGroups();
  });

  prevPageButton.addEventListener("click", () => {
    if (currentPage <= 1) {
      return;
    }
    cancelPendingSearch();
    currentPage -= 1;
    void loadGroups({ clearStatus: false });
  });

  nextPageButton.addEventListener("click", () => {
    if (currentMeta.total_pages > 0 && currentPage >= currentMeta.total_pages) {
      return;
    }
    cancelPendingSearch();
    currentPage += 1;
    void loadGroups({ clearStatus: false });
  });

  // L-P-1: 卸载时 abort 在飞请求 + 清理 debounce timer，与 commands.js B-2 对齐。
  window.addEventListener("beforeunload", () => {
    cancelPendingSearch();
  });

  // M-U-1: 统一 ESC dispatcher —— 仅作用于栈顶 modal。
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (modalStack.length === 0) return;
    const top = modalStack[modalStack.length - 1];
    if (!top || top.classList.contains("hidden")) return;
    const closer = modalCloseRegistry.get(top);
    if (typeof closer === "function") {
      closer();
    }
  });

  fieldPermissions.addEventListener("input", updatePreview);
  fieldInherits.addEventListener("input", updatePreview);

  modalCloseButton.addEventListener("click", closeModal);
  modalCancelButton.addEventListener("click", closeModal);
  modalSaveButton.addEventListener("click", () => {
    void saveGroup();
  });

  modalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.modalClose === "1") {
      closeModal();
    }
  });

  // M-U-1: 注册 group-modal 的 ESC closer。
  registerModalCloser(modalNode, () => closeModal());

  deleteModalCloseButton.addEventListener("click", () => {
    closeDeleteModal();
  });
  deleteModalCancelButton.addEventListener("click", () => {
    closeDeleteModal();
  });
  deleteModalConfirmButton.addEventListener("click", () => {
    void confirmDeleteGroup();
  });

  deleteModalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.deleteModalClose === "1") {
      closeDeleteModal();
    }
  });

  // M-U-1: 注册 delete-modal 的 ESC closer。
  registerModalCloser(deleteModalNode, () => closeDeleteModal());

  void loadGroups();
})();
