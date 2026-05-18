(() => {
  const reloadButton = document.getElementById("reload-btn");
  const addUserButton = document.getElementById("add-user-btn");
  const searchInput = document.getElementById("user-search");

  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const loadingNode = document.getElementById("loading");
  const emptyNode = document.getElementById("empty");
  const tableWrapNode = document.getElementById("table-wrap");
  const tableBodyNode = document.getElementById("user-table-body");
  const paginationNode = document.getElementById("user-pagination");
  const paginationInfoNode = document.getElementById("user-pagination-info");
  const perPageSelect = document.getElementById("user-per-page");
  const prevPageButton = document.getElementById("user-prev-btn");
  const nextPageButton = document.getElementById("user-next-btn");

  const modalNode = document.getElementById("user-modal");
  const modalTitleNode = document.getElementById("user-modal-title");
  const modalAlertNode = document.getElementById("modal-alert");
  const modalAlertMessageNode = document.getElementById("modal-alert-message");
  const modalCloseButton = document.getElementById("modal-close-btn");
  const modalCancelButton = document.getElementById("modal-cancel-btn");
  const modalSaveButton = document.getElementById("modal-save-btn");
  const banModalNode = document.getElementById("ban-modal");
  const banModalTextNode = document.getElementById("ban-modal-text");
  const banModalAlertNode = document.getElementById("ban-modal-alert");
  const banModalAlertMessageNode = document.getElementById("ban-modal-alert-message");
  const banModalCloseButton = document.getElementById("ban-modal-close-btn");
  const banModalCancelButton = document.getElementById("ban-modal-cancel-btn");
  const banModalConfirmButton = document.getElementById("ban-modal-confirm-btn");
  const banReasonInput = document.getElementById("ban-reason-input");

  const deleteModalNode = document.getElementById("delete-modal");
  const deleteModalTextNode = document.getElementById("delete-modal-text");
  const deleteModalAlertNode = document.getElementById("delete-modal-alert");
  const deleteModalAlertMessageNode = document.getElementById("delete-modal-alert-message");
  const deleteModalCloseButton = document.getElementById("delete-modal-close-btn");
  const deleteModalCancelButton = document.getElementById("delete-modal-cancel-btn");
  const deleteModalConfirmButton = document.getElementById("delete-modal-confirm-btn");

  const fieldUserId = document.getElementById("field-user-id");
  const fieldName = document.getElementById("field-name");
  const fieldCoins = document.getElementById("field-coins");
  const fieldSignTotal = document.getElementById("field-sign-total");
  const fieldSignStreak = document.getElementById("field-sign-streak");
  const fieldGroup = document.getElementById("field-group");
  const fieldPermissions = document.getElementById("field-permissions");
  const permissionPreviewNode = document.getElementById("permission-preview-list");

  const requiredNodesReady = Boolean(
    reloadButton &&
      addUserButton &&
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
      banModalNode &&
      banModalTextNode &&
      banModalAlertNode &&
      banModalAlertMessageNode &&
      banModalCloseButton &&
      banModalCancelButton &&
      banModalConfirmButton &&
      banReasonInput &&
      deleteModalNode &&
      deleteModalTextNode &&
      deleteModalAlertNode &&
      deleteModalAlertMessageNode &&
      deleteModalCloseButton &&
      deleteModalCancelButton &&
      deleteModalConfirmButton &&
      fieldUserId &&
      fieldName &&
      fieldCoins &&
      fieldSignTotal &&
      fieldSignStreak &&
      fieldGroup &&
      fieldPermissions &&
      permissionPreviewNode
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;
  const USER_ID_PATTERN = /^\d{5,20}$/;
  const USER_NAME_PATTERN = /^[A-Za-z0-9\u4e00-\u9fff]+$/u;
  const MAX_USER_NAME_LENGTH = 16;

  let userStates = [];
  let groupOptions = [];
  let groupOptionsLoaded = false;
  let modalMode = "create";
  let editingUserDbId = null;
  let modalSaving = false;
  let deletingUser = null;
  let deleteSaving = false;
  let banningUser = null;
  let banSaving = false;
  let currentPage = 1;
  let currentPerPage = Number(perPageSelect.value || 10);
  let currentMeta = { total: 0, page: 1, per_page: currentPerPage, total_pages: 0 };
  let reloadInFlight = false;

  const syncResultMap = new Map();

  // M-5 / M-6 / M-7：搜索 debounce + AbortController + beforeunload 清理
  let searchDebounceTimer = null;
  let searchAbortController = null;
  const SEARCH_DEBOUNCE_MS = 300;

  // M-10 / M-11 / M-12：modal stack + focus trap + body scroll lock（参考 commands.js）
  const modalStack = [];
  const modalCloseRegistry = new WeakMap();
  const modalPreviousFocus = new WeakMap();
  const modalTrapHandlers = new WeakMap();
  let bodyOverflowBeforeModal = null;

  const getFocusableInModal = (modalNode) => {
    if (!modalNode) return [];
    return Array.from(
      modalNode.querySelectorAll(
        'a[href]:not([disabled]), area[href]:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [contenteditable]:not([contenteditable="false"]), [tabindex]:not([tabindex="-1"]):not([disabled]), details:not([disabled]) > summary:not([disabled])'
      )
    ).filter((el) => !el.classList.contains("hidden"));
  };

  const buildTrapFocusHandler = (modalNode) => (event) => {
    if (event.key !== "Tab") return;
    const focusables = getFocusableInModal(modalNode);
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

  const pushModalToStack = (modalNode) => {
    if (!modalNode) return;
    const idx = modalStack.lastIndexOf(modalNode);
    if (idx >= 0) modalStack.splice(idx, 1);
    modalStack.push(modalNode);
  };

  const popModalFromStack = (modalNode) => {
    if (!modalNode) return;
    const idx = modalStack.lastIndexOf(modalNode);
    if (idx >= 0) modalStack.splice(idx, 1);
  };

  const registerModalCloser = (modalNode, closer) => {
    if (!modalNode || typeof closer !== "function") return;
    modalCloseRegistry.set(modalNode, closer);
  };

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

  const openModalWithFocus = (modalNode) => {
    if (!modalNode) return;
    if (!modalNode.classList.contains("hidden")) return;
    modalPreviousFocus.set(modalNode, document.activeElement);
    modalNode.classList.remove("hidden");
    const handler = buildTrapFocusHandler(modalNode);
    modalTrapHandlers.set(modalNode, handler);
    modalNode.addEventListener("keydown", handler);
    pushModalToStack(modalNode);
    lockBodyScroll();
    setTimeout(() => {
      const focusables = getFocusableInModal(modalNode);
      const preferred = focusables.find(
        (el) => !el.classList.contains("modal-close-btn")
      ) || focusables[0];
      if (preferred && typeof preferred.focus === "function") {
        preferred.focus();
      }
    }, 0);
  };

  const closeModalAndRestoreFocus = (modalNode) => {
    if (!modalNode) return;
    modalNode.classList.add("hidden");
    const handler = modalTrapHandlers.get(modalNode);
    if (handler) {
      modalNode.removeEventListener("keydown", handler);
      modalTrapHandlers.delete(modalNode);
    }
    const previousFocus = modalPreviousFocus.get(modalNode);
    modalPreviousFocus.delete(modalNode);
    popModalFromStack(modalNode);
    unlockBodyScroll();
    if (previousFocus && document.contains(previousFocus) && typeof previousFocus.focus === "function") {
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

  const setBanModalAlert = (message = "", type = "info") => {
    const text = String(message || "").trim();
    if (!text) {
      banModalAlertNode.className = "alert hidden modal-alert";
      banModalAlertMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    banModalAlertNode.className = `alert ${normalizedType} modal-alert`;
    banModalAlertMessageNode.textContent = text;
  };

  const normalizePermissionsText = (raw) => {
    const text = String(raw || "").trim();
    if (!text) {
      return "";
    }
    const values = [...new Set(text.split(",").map((item) => item.trim()).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b));
    return values.join(",");
  };

  const permissionsToArray = (raw) => {
    const text = normalizePermissionsText(raw);
    if (!text) {
      return [];
    }
    return text.split(",").filter(Boolean);
  };

  const renderPermissionBadges = (container, rawPermissions) => {
    container.innerHTML = "";
    const permissions = permissionsToArray(rawPermissions);
    if (!permissions.length) {
      const noneBadge = document.createElement("span");
      noneBadge.className = "permission-badge none";
      noneBadge.textContent = "无";
      container.appendChild(noneBadge);
      return;
    }
    for (const permission of permissions) {
      const badge = document.createElement("span");
      badge.className = "permission-badge";
      badge.textContent = permission;
      container.appendChild(badge);
    }
  };

  const normalizeUser = (item) => ({
    id: Number(item?.id || 0),
    user_id: String(item?.user_id || ""),
    name: String(item?.name || ""),
    coins: Number(item?.coins || 0),
    sign_total: Number(item?.sign_total || 0),
    sign_streak: Number(item?.sign_streak || 0),
    permissions: normalizePermissionsText(item?.permissions || ""),
    group: String(item?.group || ""),
    is_banned: Boolean(item?.is_banned),
    banned_at: String(item?.banned_at || ""),
    ban_reason: String(item?.ban_reason || ""),
    created_at: String(item?.created_at || ""),
  });

  const ensureGroupOptions = () => {
    const baseOptions = [...new Set([...groupOptions, "guest", "default"])]
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
    groupOptions = baseOptions;
  };

  const renderGroupSelectOptions = (selectedGroup = "") => {
    ensureGroupOptions();
    fieldGroup.innerHTML = "";
    const options = [...groupOptions];
    if (selectedGroup && !options.includes(selectedGroup)) {
      options.push(selectedGroup);
      options.sort((a, b) => a.localeCompare(b));
    }

    for (const group of options) {
      const option = document.createElement("option");
      option.value = group;
      option.textContent = group;
      fieldGroup.appendChild(option);
    }
    if (selectedGroup && options.includes(selectedGroup)) {
      fieldGroup.value = selectedGroup;
    } else if (options.includes("default")) {
      fieldGroup.value = "default";
    } else if (options.length > 0) {
      fieldGroup.value = options[0];
    }
  };

  const loadGroupOptions = async () => {
    if (groupOptionsLoaded) {
      return;
    }

    const payload = await api.apiRequest("/webui/api/groups/options", {
      method: "GET",
      headers: { Accept: "application/json" },
      action: "加载",
      expectedStatus: 200,
    });
    const groups = api.unwrapData(payload);
    if (!Array.isArray(groups)) {
      throw new Error("加载失败，返回数据格式错误");
    }
    groupOptions = [...new Set(groups.map((item) => String(item || "").trim()).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b));
    groupOptionsLoaded = true;
    ensureGroupOptions();
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
    const end = Math.min(total, start + Math.max(userStates.length - 1, 0));
    paginationInfoNode.textContent = `第 ${page} / ${Math.max(totalPages, 1)} 页，共 ${total} 条，当前显示 ${start}-${end}`;
    prevPageButton.disabled = page <= 1;
    nextPageButton.disabled = totalPages <= 0 || page >= totalPages;
  };

  const renderTable = () => {
    tableBodyNode.innerHTML = "";
    loadingNode.classList.add("hidden");

    if (!userStates.length) {
      emptyNode.textContent = currentMeta.total > 0 ? "当前页暂无数据。" : "暂无用户数据。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      updatePagination();
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const user of userStates) {
      const row = document.createElement("tr");
      row.dataset.userId = String(user.id);

      const idCell = document.createElement("td");
      idCell.className = "id-cell";
      idCell.textContent = String(user.id);

      const userIdCell = document.createElement("td");
      userIdCell.className = "user-id-cell";
      userIdCell.textContent = user.user_id;

      const nameCell = document.createElement("td");
      nameCell.className = "name-cell";
      const nameText = document.createElement("p");
      nameText.className = "name-text";
      nameText.textContent = user.name;
      nameCell.appendChild(nameText);

      const coinsCell = document.createElement("td");
      coinsCell.className = "coins-cell";
      coinsCell.textContent = Number(user.coins).toLocaleString("zh-CN");

      const signTotalCell = document.createElement("td");
      signTotalCell.className = "sign-total-cell";
      signTotalCell.textContent = String(user.sign_total);

      const signStreakCell = document.createElement("td");
      signStreakCell.className = "sign-streak-cell";
      signStreakCell.textContent = String(user.sign_streak);

      const groupCell = document.createElement("td");
      groupCell.className = "group-cell";
      groupCell.textContent = user.group;

      const banCell = document.createElement("td");
      banCell.className = "ban-cell";
      if (user.is_banned) {
        var banBadge = document.createElement("span");
        banBadge.className = "permission-badge";
        banBadge.style.cssText = "color: var(--danger); border-color: color-mix(in srgb, var(--danger) 35%, var(--line-strong));";
        banBadge.textContent = "已封禁";
        banBadge.title = (user.ban_reason || "") + (user.banned_at ? "\n" + user.banned_at : "");
        banCell.appendChild(banBadge);
      } else {
        var normalBadge = document.createElement("span");
        normalBadge.className = "permission-badge none";
        normalBadge.textContent = "正常";
        banCell.appendChild(normalBadge);
      }

      const permissionCell = document.createElement("td");
      permissionCell.className = "permission-cell";
      const permissionList = document.createElement("div");
      permissionList.className = "permission-list";
      renderPermissionBadges(permissionList, user.permissions);
      permissionCell.appendChild(permissionList);

      const createdCell = document.createElement("td");
      createdCell.className = "created-cell";
      createdCell.textContent = user.created_at || "-";

      const actionCell = document.createElement("td");
      actionCell.className = "actions-cell";
      const actions = document.createElement("div");
      actions.className = "row-actions";

      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.className = "btn action-btn";
      editButton.textContent = "编辑";
      editButton.addEventListener("click", async () => {
        try {
          await loadGroupOptions();
          openModal("edit", user);
        } catch (error) {
          const message = error instanceof Error ? error.message : "加载失败";
          setStatus(message, "error");
        }
      });

      const syncButton = document.createElement("button");
      syncButton.type = "button";
      syncButton.className = "btn action-btn action-btn-sync";
      // M-13：sync 按钮 disable / textContent 与 syncResultMap 状态局部对齐
      syncButton.dataset.role = "sync";
      const syncState = syncResultMap.get(user.id);
      if (syncState?.status === "loading") {
        syncButton.disabled = true;
        syncButton.textContent = "同步中…";
      } else {
        syncButton.textContent = "同步";
      }
      syncButton.addEventListener("click", () => {
        void syncWhitelist(user);
      });

      const banButton = document.createElement("button");
      banButton.type = "button";
      if (user.is_banned) {
        banButton.className = "btn action-btn";
        banButton.textContent = "解封";
        banButton.addEventListener("click", () => {
          void toggleBan(user, false);
        });
      } else {
        banButton.className = "btn action-btn action-btn-danger";
        banButton.textContent = "封禁";
        banButton.addEventListener("click", () => {
          openBanModal(user);
        });
      }

      const warehouseButton = document.createElement("button");
      warehouseButton.type = "button";
      warehouseButton.className = "btn action-btn";
      warehouseButton.textContent = "仓库";
      warehouseButton.addEventListener("click", () => {
        window.location.href = "/webui/warehouse?user_id=" + encodeURIComponent(user.user_id);
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "btn action-btn action-btn-danger";
      deleteButton.textContent = "删除";
      deleteButton.addEventListener("click", () => {
        openDeleteModal(user);
      });

      actions.appendChild(editButton);
      actions.appendChild(syncButton);
      actions.appendChild(warehouseButton);
      actions.appendChild(banButton);
      actions.appendChild(deleteButton);
      actionCell.appendChild(actions);

      row.appendChild(idCell);
      row.appendChild(userIdCell);
      row.appendChild(nameCell);
      row.appendChild(coinsCell);
      row.appendChild(signTotalCell);
      row.appendChild(signStreakCell);
      row.appendChild(groupCell);
      row.appendChild(banCell);
      row.appendChild(permissionCell);
      row.appendChild(createdCell);
      row.appendChild(actionCell);
      tableBodyNode.appendChild(row);
    }

    updatePagination();
  };

  // M-8 / M-13：局部更新某 user 行的 sync 按钮，避免 sync 状态切换触发全表重渲染
  const updateSyncButtonForUser = (userId) => {
    const row = tableBodyNode.querySelector(`tr[data-user-id="${CSS.escape(String(userId))}"]`);
    if (!row) return;
    const button = row.querySelector('button[data-role="sync"]');
    if (!button) return;
    const state = syncResultMap.get(userId);
    if (state?.status === "loading") {
      button.disabled = true;
      button.textContent = "同步中…";
    } else {
      button.disabled = false;
      button.textContent = "同步";
    }
  };

  // M-9：用返回 user 局部更新 userStates 中对应一行，避免 ban/unban 后全表重拉
  const updateUserStateById = (updatedUser) => {
    if (!updatedUser || typeof updatedUser !== "object") return false;
    const normalized = normalizeUser(updatedUser);
    const idx = userStates.findIndex((item) => item.id === normalized.id);
    if (idx < 0) return false;
    userStates[idx] = normalized;
    return true;
  };

  const loadUsers = async ({ clearStatus = true, signal = null } = {}) => {
    if (clearStatus) {
      setStatus("");
    }
    loadingNode.classList.remove("hidden");
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");
    paginationNode.classList.add("hidden");

    try {
      const payload = await api.apiRequest(
        `/webui/api/users?page=${encodeURIComponent(String(currentPage))}&per_page=${encodeURIComponent(String(currentPerPage))}&q=${encodeURIComponent(String(searchInput.value || "").trim())}`,
        {
          method: "GET",
          headers: { Accept: "application/json" },
          action: "加载",
          expectedStatus: 200,
          signal,
        }
      );

      const users = api.unwrapData(payload);
      const meta = api.unwrapMeta(payload);
      if (!Array.isArray(users)) {
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
      userStates = users.map(normalizeUser);

      const validIds = new Set(userStates.map((item) => item.id));
      for (const key of [...syncResultMap.keys()]) {
        if (!validIds.has(key)) {
          syncResultMap.delete(key);
        }
      }

      renderTable();
      return true;
    } catch (error) {
      // M-5 / M-6：abort 是用户主动操作，不报错
      if (signal && signal.aborted) {
        return false;
      }
      if (error && (error.name === "AbortError" || error.code === "AbortError")) {
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

  // M-5 / M-6 / L-7：取消 pending search debounce + abort 在飞 search 请求
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

  // M-5：搜索 debounce + AbortController 取消在飞请求
  const triggerSearchDebounced = () => {
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
    }
    searchDebounceTimer = setTimeout(() => {
      if (searchAbortController) {
        searchAbortController.abort();
      }
      searchAbortController = new AbortController();
      currentPage = 1;
      void loadUsers({ signal: searchAbortController.signal });
    }, SEARCH_DEBOUNCE_MS);
  };

  const closeModal = () => {
    if (modalSaving) {
      return;
    }
    closeModalAndRestoreFocus(modalNode);
  };

  const openBanModal = (user) => {
    banningUser = user;
    banSaving = false;
    banModalConfirmButton.disabled = false;
    setBanModalAlert("");
    banReasonInput.value = "";
    banModalTextNode.textContent = `确定封禁用户「${user.name || "未命名用户"}」吗？`;
    // M-10 / M-11 / M-12：modal stack + focus trap + body scroll lock
    openModalWithFocus(banModalNode);
  };

  const closeBanModal = (force = false) => {
    if (banSaving && !force) {
      return;
    }
    closeModalAndRestoreFocus(banModalNode);
    if (force || !banSaving) {
      banningUser = null;
    }
  };

  const confirmBanUser = async () => {
    if (!banningUser || banSaving) {
      return;
    }
    var reason = banReasonInput.value.trim();
    if (!reason) {
      setBanModalAlert("请输入封禁原因", "error");
      banReasonInput.focus();
      return;
    }
    var targetUser = banningUser;
    banSaving = true;
    banModalConfirmButton.disabled = true;
    // M-14：进行时文案统一去对象名 + unicode 省略号
    setBanModalAlert("封禁中…", "warning");

    try {
      await toggleBan(targetUser, true, reason);
      closeBanModal(true);
    } catch (error) {
      var message = error instanceof Error ? error.message : "封禁失败";
      setBanModalAlert(message, "error");
      banSaving = false;
      banModalConfirmButton.disabled = false;
    }
  };

  const openDeleteModal = (user) => {
    deletingUser = user;
    deleteSaving = false;
    deleteModalConfirmButton.disabled = false;
    setDeleteModalAlert("");
    deleteModalTextNode.textContent = `确定删除用户「${user.name || "未命名用户"}」吗？此操作不可恢复。`;
    // M-10 / M-11 / M-12：modal stack + focus trap + body scroll lock
    openModalWithFocus(deleteModalNode);
  };

  const closeDeleteModal = (force = false) => {
    if (deleteSaving && !force) {
      return;
    }
    closeModalAndRestoreFocus(deleteModalNode);
    if (force || !deleteSaving) {
      deletingUser = null;
    }
  };

  const updatePermissionPreview = () => {
    renderPermissionBadges(permissionPreviewNode, fieldPermissions.value);
  };

  const openModal = (mode, user = null) => {
    modalMode = mode;
    editingUserDbId = mode === "edit" && user ? user.id : null;
    modalSaving = false;
    setModalAlert("");

    if (mode === "edit" && user) {
      modalTitleNode.textContent = "编辑用户";
      modalSaveButton.textContent = "保存";
      fieldUserId.value = user.user_id;
      fieldName.value = user.name;
      fieldCoins.value = String(user.coins);
      fieldSignTotal.value = String(user.sign_total);
      fieldSignStreak.value = String(user.sign_streak);
      renderGroupSelectOptions(user.group);
      fieldPermissions.value = user.permissions || "";
    } else {
      modalTitleNode.textContent = "创建用户";
      modalSaveButton.textContent = "创建";
      fieldUserId.value = "";
      fieldName.value = "";
      fieldCoins.value = "0";
      fieldSignTotal.value = "0";
      fieldSignStreak.value = "0";
      renderGroupSelectOptions("default");
      fieldPermissions.value = "";
    }

    updatePermissionPreview();
    // M-10 / M-11 / M-12：modal stack + focus trap + body scroll lock；首焦点由 openModalWithFocus 处理
    openModalWithFocus(modalNode);
  };

  const buildPayloadFromModal = () => {
    const userId = String(fieldUserId.value || "").trim();
    const name = String(fieldName.value || "").trim();
    const coinsText = String(fieldCoins.value || "").trim();
    const signTotalText = String(fieldSignTotal.value || "0").trim();
    const signStreakText = String(fieldSignStreak.value || "0").trim();
    const group = String(fieldGroup.value || "").trim();
    const permissions = normalizePermissionsText(fieldPermissions.value || "");

    if (!userId) {
      throw new Error("用户 QQ 不能为空");
    }
    if (!USER_ID_PATTERN.test(userId)) {
      throw new Error("用户 QQ 必须是 5-20 位数字");
    }
    if (!name) {
      throw new Error("用户名称不能为空");
    }
    if (name.length > MAX_USER_NAME_LENGTH) {
      throw new Error(`用户名称过长，最多 ${MAX_USER_NAME_LENGTH} 个字符`);
    }
    if (/^\d+$/.test(name)) {
      throw new Error("用户名称不能为纯数字");
    }
    if (!USER_NAME_PATTERN.test(name)) {
      throw new Error("用户名称不能包含符号，只能使用中文、英文和数字");
    }
    if (!coinsText) {
      throw new Error("金币不能为空");
    }

    const coinsNumber = Number(coinsText);
    if (!Number.isInteger(coinsNumber)) {
      throw new Error("金币必须是整数");
    }
    if (coinsNumber < 0) {
      throw new Error("金币必须是非负整数");
    }

    const signTotalNumber = Number(signTotalText);
    if (!Number.isInteger(signTotalNumber) || signTotalNumber < 0) {
      throw new Error("累计签到必须是非负整数");
    }

    const signStreakNumber = Number(signStreakText);
    if (!Number.isInteger(signStreakNumber) || signStreakNumber < 0) {
      throw new Error("连续签到必须是非负整数");
    }

    if (!group) {
      throw new Error("身份组不能为空");
    }

    return {
      user_id: userId,
      name,
      coins: coinsNumber,
      sign_total: signTotalNumber,
      sign_streak: signStreakNumber,
      group,
      permissions,
    };
  };

  const saveUser = async () => {
    if (modalSaving) {
      return;
    }

    const isEdit = modalMode === "edit" && typeof editingUserDbId === "number";

    let payload;
    try {
      payload = buildPayloadFromModal();
    } catch (error) {
      const message = error instanceof Error ? error.message : "表单校验失败";
      setModalAlert(`${isEdit ? "更新失败" : "创建失败"}，${message}`, "error");
      return;
    }

    modalSaving = true;
    modalSaveButton.disabled = true;
    // M-14：进行时文案统一去对象名 + unicode 省略号
    setModalAlert("保存中…", "info");

    try {
      const url = isEdit ? `/webui/api/users/${editingUserDbId}` : "/webui/api/users";
      const method = isEdit ? "PUT" : "POST";

      const responsePayload = await api.apiRequest(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
        action: isEdit ? "更新" : "创建",
        expectedStatus: isEdit ? 200 : 201,
      });

      closeModalAndRestoreFocus(modalNode);
      const reloaded = await loadUsers({ clearStatus: false });
      if (reloaded) {
        // L-8：mirror delete/ban/unban 的 server_results 展示；create + 改名 update 都附带 server_results
        const result = api.unwrapData(responsePayload) || {};
        const serverResults = Array.isArray(result.server_results) ? result.server_results : [];
        const lines = [isEdit ? "更新成功" : "创建成功"];
        if (serverResults.length) {
          lines.push("服务器白名单：");
          for (let i = 0; i < serverResults.length; i++) {
            const item = serverResults[i];
            const serverId = String(item.server_id || "?");
            const serverName = String(item.server_name || "未知服务器");
            if (item.success) {
              const extra = item.reason ? "（" + item.reason + "）" : "";
              lines.push(serverId + "." + serverName + "：成功" + extra);
            } else {
              // L-9：原样透传后端 reason，detail 空时不拼"未知错误"
              const failReason = String(item.reason || "");
              if (failReason) {
                lines.push(serverId + "." + serverName + "：失败，" + failReason);
              } else {
                lines.push(serverId + "." + serverName + "：失败");
              }
            }
          }
        }
        setStatus(lines.join("\n"), "success");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : isEdit ? "更新失败" : "创建失败";
      setModalAlert(message, "error");
    } finally {
      modalSaving = false;
      modalSaveButton.disabled = false;
    }
  };

  const confirmDeleteUser = async () => {
    if (!deletingUser || deleteSaving) {
      return;
    }
    const targetUser = deletingUser;
    deleteSaving = true;
    deleteModalConfirmButton.disabled = true;
    // M-14：进行时文案统一去对象名 + unicode 省略号
    setDeleteModalAlert("删除中…", "warning");

    setStatus("删除中…", "warning");
    try {
      const payload = await api.apiRequest(`/webui/api/users/${targetUser.id}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        action: "删除",
        expectedStatus: 200,
      });
      const result = api.unwrapData(payload) || {};

      syncResultMap.delete(targetUser.id);
      closeDeleteModal(true);
      const reloaded = await loadUsers({ clearStatus: false });
      if (reloaded) {
        // L-8：toast 文案去对象名（CLAUDE.md 规范），mirror ban/unban 的 server_results 展示
        var serverResults = Array.isArray(result.server_results) ? result.server_results : [];
        var lines = ["删除成功"];
        if (serverResults.length) {
          lines.push("服务器白名单：");
          for (var i = 0; i < serverResults.length; i++) {
            var item = serverResults[i];
            var serverId = String(item.server_id || "?");
            var serverName = String(item.server_name || "未知服务器");
            if (item.success) {
              var extra = item.reason ? "（" + item.reason + "）" : "";
              lines.push(serverId + "." + serverName + "：成功" + extra);
            } else {
              // L-9：原样透传后端 reason，detail 空时不拼"未知错误"
              var failReason = String(item.reason || "");
              if (failReason) {
                lines.push(serverId + "." + serverName + "：失败，" + failReason);
              } else {
                lines.push(serverId + "." + serverName + "：失败");
              }
            }
          }
        }
        setStatus(lines.join("\n"), "success");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除失败";
      setDeleteModalAlert(message, "error");
      setStatus(message, "error");
    } finally {
      deleteSaving = false;
      deleteModalConfirmButton.disabled = false;
    }
  };

  const toggleBan = async (user, ban, reason) => {
    var actionText = ban ? "封禁" : "解封";
    // M-14：进行时文案统一 unicode 省略号
    setStatus(actionText + "中…", "warning");

    try {
      var options = {
        method: "POST",
        headers: { Accept: "application/json" },
        action: actionText,
        expectedStatus: 200,
      };
      if (ban) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify({ reason: reason });
      }
      var endpoint = ban ? "ban" : "unban";
      var payload = await api.apiRequest("/webui/api/users/" + user.id + "/" + endpoint, options);
      var result = api.unwrapData(payload);

      var serverResults = Array.isArray(result.server_results) ? result.server_results : [];
      // L-8：toast 文案去对象名（CLAUDE.md 规范）
      var lines = [actionText + "成功"];
      if (serverResults.length) {
        lines.push("服务器黑名单：");
        for (var i = 0; i < serverResults.length; i++) {
          var item = serverResults[i];
          var serverId = String(item.server_id || "?");
          var serverName = String(item.server_name || "未知服务器");
          if (item.success) {
            var extra = item.reason ? "（" + item.reason + "）" : "";
            lines.push(serverId + "." + serverName + "：成功" + extra);
          } else {
            // L-9：原样透传后端 reason，detail 空时不拼"未知错误"
            var failReason = String(item.reason || "");
            if (failReason) {
              lines.push(serverId + "." + serverName + "：失败，" + failReason);
            } else {
              lines.push(serverId + "." + serverName + "：失败");
            }
          }
        }
      }
      setStatus(lines.join("\n"), "success");

      // M-9：用返回 user 局部更新对应行，避免触发 loadUsers 全表重拉
      if (result && result.user && updateUserStateById(result.user)) {
        renderTable();
      } else {
        await loadUsers({ clearStatus: false });
      }
    } catch (error) {
      var message = error instanceof Error ? error.message : actionText + "失败";
      setStatus(message, "error");
    }
  };

  const syncWhitelist = async (user) => {
    // L-1：同步开始前重置该 user 的 entry，避免上一轮 failed 状态污染本轮显示
    syncResultMap.set(user.id, {
      status: "loading",
      successCount: 0,
      failedCount: 0,
    });
    // M-8：局部更新 sync 按钮，避免全表重渲染
    updateSyncButtonForUser(user.id);
    // M-14：进行时文案去对象名 + unicode 省略号
    setStatus("同步中…", "warning");

    try {
      const payload = await api.apiRequest(`/webui/api/users/${user.id}/sync-whitelist`, {
        method: "POST",
        headers: { Accept: "application/json" },
        action: "同步",
        expectedStatus: 200,
      });
      const result = api.unwrapData(payload);

      const userName = String(result.name || user.name);
      const syncResults = Array.isArray(result.results) ? result.results : [];
      const lines = [`用户 ${userName} 白名单同步结果：`];
      let successCount = 0;
      let failedCount = 0;

      if (!syncResults.length) {
        lines.push("同步失败，暂无可同步的服务器");
      } else {
        for (const item of syncResults) {
          const serverId = String(item?.server_id ?? "?");
          const serverName = String(item?.server_name || "未知服务器");
          const success = Boolean(item?.success);
          if (success) {
            successCount += 1;
            lines.push(`${serverId}.${serverName}：同步成功`);
          } else {
            failedCount += 1;
            // L-9：原样透传后端 reason，detail 空时不拼"未知错误"
            const reason = String(item?.reason || "");
            if (reason) {
              lines.push(`${serverId}.${serverName}：同步失败，${reason}`);
            } else {
              lines.push(`${serverId}.${serverName}：同步失败`);
            }
          }
        }
      }

      const hasFailure = failedCount > 0 || !syncResults.length;
      if (hasFailure) {
        syncResultMap.set(user.id, {
          status: "failed",
          successCount,
          failedCount,
        });
      } else {
        // L-1：完全成功后清理 entry，避免常驻 Map 占用内存
        syncResultMap.delete(user.id);
      }
      setStatus(lines.join("\n"), hasFailure ? "error" : "success");
      // M-8：仅局部更新 sync 按钮
      updateSyncButtonForUser(user.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "同步失败";
      syncResultMap.set(user.id, {
        status: "failed",
        successCount: 0,
        failedCount: 0,
      });
      setStatus(message, "error");
      updateSyncButtonForUser(user.id);
    }
  };

  reloadButton.addEventListener("click", async () => {
    // L-7：reload 按钮 loading 状态 + 取消 pending search，避免点击连发 / debounce 风暴
    if (reloadInFlight) return;
    cancelPendingSearch();
    reloadInFlight = true;
    reloadButton.disabled = true;
    try {
      currentPage = 1;
      await loadUsers();
    } finally {
      reloadInFlight = false;
      reloadButton.disabled = false;
    }
  });

  addUserButton.addEventListener("click", async () => {
    try {
      await loadGroupOptions();
      openModal("create");
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
    }
  });

  // M-5：搜索 input 走 300ms debounce + AbortController
  searchInput.addEventListener("input", () => {
    triggerSearchDebounced();
  });

  perPageSelect.addEventListener("change", () => {
    cancelPendingSearch();
    currentPerPage = Number(perPageSelect.value || 10);
    currentPage = 1;
    void loadUsers();
  });

  prevPageButton.addEventListener("click", () => {
    if (currentPage <= 1) {
      return;
    }
    cancelPendingSearch();
    currentPage -= 1;
    void loadUsers({ clearStatus: false });
  });

  nextPageButton.addEventListener("click", () => {
    if (currentMeta.total_pages > 0 && currentPage >= currentMeta.total_pages) {
      return;
    }
    cancelPendingSearch();
    currentPage += 1;
    void loadUsers({ clearStatus: false });
  });

  fieldPermissions.addEventListener("input", () => {
    updatePermissionPreview();
  });

  modalCloseButton.addEventListener("click", closeModal);
  modalCancelButton.addEventListener("click", closeModal);
  modalSaveButton.addEventListener("click", () => {
    void saveUser();
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

  deleteModalCloseButton.addEventListener("click", () => {
    closeDeleteModal();
  });
  deleteModalCancelButton.addEventListener("click", () => {
    closeDeleteModal();
  });
  deleteModalConfirmButton.addEventListener("click", () => {
    void confirmDeleteUser();
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

  banModalCloseButton.addEventListener("click", () => {
    closeBanModal();
  });
  banModalCancelButton.addEventListener("click", () => {
    closeBanModal();
  });
  banModalConfirmButton.addEventListener("click", () => {
    void confirmBanUser();
  });
  banModalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.banModalClose === "1") {
      closeBanModal();
    }
  });

  // M-10：3 个 modal 注册到统一 ESC dispatcher，仅栈顶 modal 响应
  registerModalCloser(modalNode, () => closeModal());
  registerModalCloser(deleteModalNode, () => closeDeleteModal());
  registerModalCloser(banModalNode, () => closeBanModal());

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

  // M-7：beforeunload 时 abort 在飞请求 + 清理 debounce timer
  window.addEventListener("beforeunload", () => {
    cancelPendingSearch();
  });

  void loadUsers();
})();
