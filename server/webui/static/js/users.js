(() => {
  const reloadButton = document.getElementById("reload-btn");
  const globalSyncButton = document.getElementById("global-sync-btn");
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

  const changeNameModalNode = document.getElementById("change-name-modal");
  const changeNameModalAlertNode = document.getElementById("change-name-modal-alert");
  const changeNameModalAlertMessageNode = document.getElementById("change-name-modal-alert-message");
  const changeNameModalCloseButton = document.getElementById("change-name-modal-close-btn");
  const changeNameModalCancelButton = document.getElementById("change-name-modal-cancel-btn");
  const changeNameModalConfirmButton = document.getElementById("change-name-modal-confirm-btn");
  const changeNameTargetNameNode = document.getElementById("change-name-target-name");
  const changeNameTargetQqNode = document.getElementById("change-name-target-qq");
  const changeNameInput = document.getElementById("change-name-input");

  const changePasswordModalNode = document.getElementById("change-password-modal");
  const changePasswordModalAlertNode = document.getElementById("change-password-modal-alert");
  const changePasswordModalAlertMessageNode = document.getElementById("change-password-modal-alert-message");
  const changePasswordModalCloseButton = document.getElementById("change-password-modal-close-btn");
  const changePasswordModalCancelButton = document.getElementById("change-password-modal-cancel-btn");
  const changePasswordModalConfirmButton = document.getElementById("change-password-modal-confirm-btn");
  const changePasswordTargetNameNode = document.getElementById("change-password-target-name");
  const changePasswordTargetQqNode = document.getElementById("change-password-target-qq");
  const changePasswordInput = document.getElementById("change-password-input");
  const changePasswordConfirmInput = document.getElementById("change-password-confirm");
  const changePasswordGenerateButton = document.getElementById("change-password-generate-btn");

  const fieldUserId = document.getElementById("field-user-id");
  const fieldName = document.getElementById("field-name");
  const fieldNameEditHint = document.getElementById("field-name-edit-hint");
  const fieldCoins = document.getElementById("field-coins");
  const fieldSignTotal = document.getElementById("field-sign-total");
  const fieldSignStreak = document.getElementById("field-sign-streak");
  const fieldGroup = document.getElementById("field-group");
  const fieldPermissions = document.getElementById("field-permissions");
  const fieldPassword = document.getElementById("field-password");
  const fieldPasswordConfirm = document.getElementById("field-password-confirm");
  const fieldPasswordGenerate = document.getElementById("field-password-generate");
  const permissionPreviewNode = document.getElementById("permission-preview-list");

  const requiredNodesReady = Boolean(
    reloadButton &&
      globalSyncButton &&
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
      changePasswordModalNode &&
      changePasswordModalAlertNode &&
      changePasswordModalAlertMessageNode &&
      changePasswordModalCloseButton &&
      changePasswordModalCancelButton &&
      changePasswordModalConfirmButton &&
      changePasswordTargetNameNode &&
      changePasswordTargetQqNode &&
      changePasswordInput &&
      changePasswordConfirmInput &&
      changePasswordGenerateButton &&
      changeNameModalNode &&
      changeNameModalAlertNode &&
      changeNameModalAlertMessageNode &&
      changeNameModalCloseButton &&
      changeNameModalCancelButton &&
      changeNameModalConfirmButton &&
      changeNameTargetNameNode &&
      changeNameTargetQqNode &&
      changeNameInput &&
      fieldUserId &&
      fieldName &&
      fieldCoins &&
      fieldSignTotal &&
      fieldSignStreak &&
      fieldGroup &&
      fieldPermissions &&
      fieldPassword &&
      fieldPasswordConfirm &&
      fieldPasswordGenerate &&
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
  let changePasswordUser = null;
  let changePasswordSaving = false;
  let changePasswordRevealTimer = null;
  let changeNameUser = null;
  let changeNameSaving = false;
  let currentPage = 1;
  let currentPerPage = Number(perPageSelect.value || 10);
  let currentMeta = { total: 0, page: 1, per_page: currentPerPage, total_pages: 0 };
  let reloadInFlight = false;
  let globalSyncInFlight = false;

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

  // 统一渲染 sync orchestrator 返回的 per-server outcomes 为 toast 多行文案。
  // outcomes 元素结构：{server_id, server_name, ok, status, detail}
  const renderSyncOutcomes = (outcomes) => {
    if (!Array.isArray(outcomes) || outcomes.length === 0) {
      return [];
    }
    const lines = ["同步服务器结果："];
    for (const o of outcomes) {
      const serverId = String(o?.server_id ?? "?");
      const serverName = String(o?.server_name || "未知服务器");
      if (o?.ok) {
        if (o.status === "skipped") {
          lines.push(`${serverId}.${serverName}：同步成功，无需同步`);
        } else {
          lines.push(`${serverId}.${serverName}：同步成功`);
        }
      } else {
        const reason = String(o?.detail || "");
        if (reason) {
          lines.push(`${serverId}.${serverName}：同步失败，${reason}`);
        } else {
          lines.push(`${serverId}.${serverName}：同步失败`);
        }
      }
    }
    return lines;
  };

  const allSyncOk = (outcomes) =>
    Array.isArray(outcomes) &&
    outcomes.length > 0 &&
    outcomes.every((o) => Boolean(o?.ok));

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

      const changeNameButton = document.createElement("button");
      changeNameButton.type = "button";
      changeNameButton.className = "btn action-btn";
      changeNameButton.textContent = "修改用户名";
      changeNameButton.title = "修改用户名";
      changeNameButton.dataset.action = "change-name";
      changeNameButton.dataset.userId = String(user.id);
      changeNameButton.addEventListener("click", () => {
        openChangeNameModal(user);
      });

      const changePasswordButton = document.createElement("button");
      changePasswordButton.type = "button";
      changePasswordButton.className = "btn action-btn";
      changePasswordButton.textContent = "修改密码";
      changePasswordButton.title = "修改密码";
      changePasswordButton.addEventListener("click", () => {
        openChangePasswordModal(user);
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "btn action-btn action-btn-danger";
      deleteButton.textContent = "删除";
      deleteButton.addEventListener("click", () => {
        openDeleteModal(user);
      });

      actions.appendChild(editButton);
      actions.appendChild(warehouseButton);
      actions.appendChild(banButton);
      actions.appendChild(changeNameButton);
      actions.appendChild(changePasswordButton);
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
    // 关 modal 时清掉 reveal timer、把 input type 切回 password、并清空 DOM 中的 plaintext，
    // 防止 cancel 后明文残留在 input.value 内存里
    resetPasswordInputType();
    fieldPassword.value = "";
    fieldPasswordConfirm.value = "";
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

  const setChangePasswordModalAlert = (message = "", type = "info") => {
    const text = String(message || "").trim();
    if (!text) {
      changePasswordModalAlertNode.className = "alert hidden modal-alert";
      changePasswordModalAlertMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    changePasswordModalAlertNode.className = `alert ${normalizedType} modal-alert`;
    changePasswordModalAlertMessageNode.textContent = text;
  };

  // QQ 中间打码：仅保留首尾 2 位，防止 plaintext QQ 全文显示
  const maskQq = (qq) => {
    const text = String(qq || "");
    if (text.length < 4) return text;
    return text.slice(0, 2) + "***" + text.slice(-2);
  };

  const resetChangePasswordInputType = () => {
    if (changePasswordRevealTimer) {
      clearTimeout(changePasswordRevealTimer);
      changePasswordRevealTimer = null;
    }
    changePasswordInput.type = "password";
    changePasswordConfirmInput.type = "password";
  };

  const generateChangePassword = () => {
    const buf = new Uint8Array(PASSWORD_GENERATED_LENGTH);
    crypto.getRandomValues(buf);
    let pwd = "";
    for (let i = 0; i < buf.length; i++) {
      pwd += PASSWORD_CHARSET[buf[i] % PASSWORD_CHARSET.length];
    }
    changePasswordInput.value = pwd;
    changePasswordConfirmInput.value = pwd;
    if (changePasswordRevealTimer) {
      clearTimeout(changePasswordRevealTimer);
    }
    changePasswordInput.type = "text";
    changePasswordConfirmInput.type = "text";
    changePasswordRevealTimer = setTimeout(() => {
      changePasswordInput.type = "password";
      changePasswordConfirmInput.type = "password";
      changePasswordRevealTimer = null;
    }, PASSWORD_REVEAL_MS);
  };

  const openChangePasswordModal = (user) => {
    changePasswordUser = user;
    changePasswordSaving = false;
    changePasswordModalConfirmButton.disabled = false;
    setChangePasswordModalAlert("");
    changePasswordTargetNameNode.textContent = user.name || "未命名用户";
    changePasswordTargetQqNode.textContent = user.user_id ? `（QQ：${maskQq(user.user_id)}）` : "";
    changePasswordInput.value = "";
    changePasswordConfirmInput.value = "";
    resetChangePasswordInputType();
    openModalWithFocus(changePasswordModalNode);
  };

  const closeChangePasswordModal = (force = false) => {
    if (changePasswordSaving && !force) {
      return;
    }
    // 关 modal 时清空 input 并切回 password type，防止 plaintext 残留在 DOM
    resetChangePasswordInputType();
    changePasswordInput.value = "";
    changePasswordConfirmInput.value = "";
    closeModalAndRestoreFocus(changePasswordModalNode);
    if (force || !changePasswordSaving) {
      changePasswordUser = null;
    }
  };

  const setChangeNameModalAlert = (message = "", type = "info") => {
    const text = String(message || "").trim();
    if (!text) {
      changeNameModalAlertNode.className = "alert hidden modal-alert";
      changeNameModalAlertMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    changeNameModalAlertNode.className = `alert ${normalizedType} modal-alert`;
    changeNameModalAlertMessageNode.textContent = text;
  };

  const openChangeNameModal = (user) => {
    changeNameUser = user;
    changeNameSaving = false;
    changeNameModalConfirmButton.disabled = false;
    setChangeNameModalAlert("");
    changeNameTargetNameNode.textContent = user.name || "未命名用户";
    changeNameTargetQqNode.textContent = user.user_id ? `（QQ：${maskQq(user.user_id)}）` : "";
    changeNameInput.value = user.name || "";
    openModalWithFocus(changeNameModalNode);
  };

  const closeChangeNameModal = (force = false) => {
    if (changeNameSaving && !force) {
      return;
    }
    changeNameInput.value = "";
    setChangeNameModalAlert("");
    closeModalAndRestoreFocus(changeNameModalNode);
    if (force || !changeNameSaving) {
      changeNameUser = null;
    }
  };

  const confirmChangeName = async () => {
    if (!changeNameUser || changeNameSaving) {
      return;
    }
    const targetUser = changeNameUser;
    const newName = String(changeNameInput.value || "").trim();

    if (!newName) {
      setChangeNameModalAlert("修改失败，用户名不能为空", "error");
      changeNameInput.focus();
      return;
    }
    if (newName.length > MAX_USER_NAME_LENGTH) {
      setChangeNameModalAlert(`修改失败，用户名称过长，最多 ${MAX_USER_NAME_LENGTH} 个字符`, "error");
      changeNameInput.focus();
      return;
    }
    if (/^\d+$/.test(newName)) {
      setChangeNameModalAlert("修改失败，用户名称不能为纯数字", "error");
      changeNameInput.focus();
      return;
    }
    if (!USER_NAME_PATTERN.test(newName)) {
      setChangeNameModalAlert("修改失败，用户名称不能包含符号，只能使用中文、英文和数字", "error");
      changeNameInput.focus();
      return;
    }

    changeNameSaving = true;
    changeNameModalConfirmButton.disabled = true;
    setChangeNameModalAlert("保存中…", "info");

    try {
      const responsePayload = await api.apiRequest(
        `/webui/api/users/${targetUser.id}/change-name`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({ name: newName }),
          action: "修改",
          expectedStatus: 200,
        }
      );

      const result = api.unwrapData(responsePayload) || {};
      const syncOutcomes = Array.isArray(result.sync_outcomes)
        ? result.sync_outcomes
        : [];

      const lines = ["修改成功", ...renderSyncOutcomes(syncOutcomes)];
      const toastType =
        syncOutcomes.length && !allSyncOk(syncOutcomes) ? "warning" : "success";

      closeChangeNameModal(true);
      setStatus(lines.join("\n"), toastType);

      // 用返回 user 局部更新对应行，避免触发 loadUsers 全表重拉
      if (result && result.user && updateUserStateById(result.user)) {
        renderTable();
      } else {
        await loadUsers({ clearStatus: false });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "修改失败";
      setChangeNameModalAlert(message, "error");
    } finally {
      changeNameSaving = false;
      changeNameModalConfirmButton.disabled = false;
    }
  };

  const confirmChangePassword = async () => {
    if (!changePasswordUser || changePasswordSaving) {
      return;
    }
    const targetUser = changePasswordUser;
    const pwd = String(changePasswordInput.value || "");
    const pwdConfirm = String(changePasswordConfirmInput.value || "");

    if (!pwd) {
      setChangePasswordModalAlert("修改失败，密码不能为空", "error");
      changePasswordInput.focus();
      return;
    }
    if (pwd.length < 8) {
      setChangePasswordModalAlert("修改失败，密码长度至少 8 位", "error");
      changePasswordInput.focus();
      return;
    }
    if (pwd !== pwdConfirm) {
      setChangePasswordModalAlert("修改失败，两次输入的密码不一致", "error");
      changePasswordConfirmInput.focus();
      return;
    }

    changePasswordSaving = true;
    changePasswordModalConfirmButton.disabled = true;
    setChangePasswordModalAlert("保存中…", "info");

    const body = { password: pwd };
    try {
      const responsePayload = await api.apiRequest(
        `/webui/api/users/${targetUser.id}/change-password`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify(body),
          action: "修改",
          expectedStatus: 200,
        }
      );

      // 提交成功后立即清空 plaintext，避免在 DOM / 引用中残留
      changePasswordInput.value = "";
      changePasswordConfirmInput.value = "";
      resetChangePasswordInputType();
      body.password = "";

      const result = api.unwrapData(responsePayload) || {};
      const syncOutcomes = Array.isArray(result.sync_outcomes)
        ? result.sync_outcomes
        : [];
      const lines = ["修改成功", ...renderSyncOutcomes(syncOutcomes)];
      const toastType =
        syncOutcomes.length && !allSyncOk(syncOutcomes) ? "warning" : "success";

      closeChangePasswordModal(true);
      setStatus(lines.join("\n"), toastType);

      // 局部更新 user 行（user 字段如 name 可能已变，但本接口只改密码；用 returned user 做一次同步是稳妥的）
      if (result && result.user && updateUserStateById(result.user)) {
        renderTable();
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "修改失败";
      setChangePasswordModalAlert(message, "error");
    } finally {
      changePasswordSaving = false;
      changePasswordModalConfirmButton.disabled = false;
      // 失败路径也释放 body 上的 plaintext 引用（GC 兜底）
      if (body && "password" in body) {
        body.password = "";
      }
    }
  };

  const updatePermissionPreview = () => {
    renderPermissionBadges(permissionPreviewNode, fieldPermissions.value);
  };

  // 生成按钮：浏览器 crypto 安全 RNG 生成 16 位 [A-Za-z0-9]，自动填两个 input，
  // 临时把 type 切到 text 让 admin 一眼看到密码，3 秒后切回 password。
  const PASSWORD_CHARSET =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const PASSWORD_GENERATED_LENGTH = 16;
  const PASSWORD_REVEAL_MS = 3000;
  let passwordRevealTimer = null;

  const resetPasswordInputType = () => {
    if (passwordRevealTimer) {
      clearTimeout(passwordRevealTimer);
      passwordRevealTimer = null;
    }
    fieldPassword.type = "password";
    fieldPasswordConfirm.type = "password";
  };

  const generatePassword = () => {
    const buf = new Uint8Array(PASSWORD_GENERATED_LENGTH);
    crypto.getRandomValues(buf);
    let pwd = "";
    for (let i = 0; i < buf.length; i++) {
      pwd += PASSWORD_CHARSET[buf[i] % PASSWORD_CHARSET.length];
    }
    fieldPassword.value = pwd;
    fieldPasswordConfirm.value = pwd;
    // 临时显示明文，让 admin 看到生成的密码
    if (passwordRevealTimer) {
      clearTimeout(passwordRevealTimer);
    }
    fieldPassword.type = "text";
    fieldPasswordConfirm.type = "text";
    passwordRevealTimer = setTimeout(() => {
      fieldPassword.type = "password";
      fieldPasswordConfirm.type = "password";
      passwordRevealTimer = null;
    }, PASSWORD_REVEAL_MS);
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
      // 改名拎到独立 dialog，编辑 dialog 中 name 字段仅展示（readonly）
      fieldName.readOnly = true;
      if (fieldNameEditHint) {
        fieldNameEditHint.hidden = false;
      }
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
      // 创建模式 name 可编辑
      fieldName.readOnly = false;
      if (fieldNameEditHint) {
        fieldNameEditHint.hidden = true;
      }
      fieldCoins.value = "0";
      fieldSignTotal.value = "0";
      fieldSignStreak.value = "0";
      renderGroupSelectOptions("default");
      fieldPermissions.value = "";
    }

    // 创建模式显示密码区，编辑模式隐藏；进入 modal 前永远清空两个 password 输入
    fieldPassword.value = "";
    fieldPasswordConfirm.value = "";
    // 进入 modal 前重置 input type 为 password（前一次"生成"可能临时切到 text）
    resetPasswordInputType();
    const isCreate = mode !== "edit";
    document.querySelectorAll("[data-create-only]").forEach((el) => {
      if (el instanceof HTMLElement) {
        el.style.display = isCreate ? "" : "none";
      }
    });

    updatePermissionPreview();
    // M-10 / M-11 / M-12：modal stack + focus trap + body scroll lock；首焦点由 openModalWithFocus 处理
    openModalWithFocus(modalNode);
  };

  const buildPayloadFromModal = (isEdit) => {
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

    const payload = {
      user_id: userId,
      name,
      coins: coinsNumber,
      sign_total: signTotalNumber,
      sign_streak: signStreakNumber,
      group,
      permissions,
    };

    // 创建模式必须带密码；编辑模式 payload 不带 password（后端 update 路径也不接收）
    if (!isEdit) {
      const pwd = String(fieldPassword.value || "");
      const pwdConfirm = String(fieldPasswordConfirm.value || "");
      if (!pwd) {
        throw new Error("密码不能为空");
      }
      if (pwd.length < 8) {
        throw new Error("密码长度至少 8 位");
      }
      if (pwd !== pwdConfirm) {
        throw new Error("两次输入的密码不一致");
      }
      payload.password = pwd;
    }

    return payload;
  };

  const saveUser = async () => {
    if (modalSaving) {
      return;
    }

    const isEdit = modalMode === "edit" && typeof editingUserDbId === "number";

    let payload;
    try {
      payload = buildPayloadFromModal(isEdit);
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

      // 提交成功后立即清空密码输入，避免 plaintext 在 DOM 残留
      fieldPassword.value = "";
      fieldPasswordConfirm.value = "";
      // 清掉可能存在的 reveal timer，并把 type 切回 password
      resetPasswordInputType();
      // 释放 payload 上的 plaintext 引用（GC 兜底）
      if (payload && "password" in payload) {
        payload.password = "";
      }
      closeModalAndRestoreFocus(modalNode);
      const reloaded = await loadUsers({ clearStatus: false });
      if (reloaded) {
        // create 路径：返回 sync_outcomes；update 路径无 sync_outcomes（编辑不触发 sync）
        const result = api.unwrapData(responsePayload) || {};
        const syncOutcomes = Array.isArray(result.sync_outcomes)
          ? result.sync_outcomes
          : [];
        const lines = [
          isEdit ? "更新成功" : "创建成功",
          ...renderSyncOutcomes(syncOutcomes),
        ];
        const toastType =
          syncOutcomes.length && !allSyncOk(syncOutcomes) ? "warning" : "success";
        setStatus(lines.join("\n"), toastType);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : isEdit ? "更新失败" : "创建失败";
      setModalAlert(message, "error");
    } finally {
      modalSaving = false;
      modalSaveButton.disabled = false;
      // 无论成功 / 失败都释放 payload 上的 plaintext 引用（成功路径已清，失败路径补一次）
      if (payload && "password" in payload) {
        payload.password = "";
      }
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

      closeDeleteModal(true);
      const reloaded = await loadUsers({ clearStatus: false });
      if (reloaded) {
        const syncOutcomes = Array.isArray(result.sync_outcomes)
          ? result.sync_outcomes
          : [];
        const lines = ["删除成功", ...renderSyncOutcomes(syncOutcomes)];
        const toastType =
          syncOutcomes.length && !allSyncOk(syncOutcomes) ? "warning" : "success";
        setStatus(lines.join("\n"), toastType);
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

      var syncOutcomes = Array.isArray(result.sync_outcomes)
        ? result.sync_outcomes
        : [];
      var lines = [actionText + "成功"].concat(renderSyncOutcomes(syncOutcomes));
      var toastType =
        syncOutcomes.length && !allSyncOk(syncOutcomes) ? "warning" : "success";
      setStatus(lines.join("\n"), toastType);

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

  const triggerGlobalSync = async () => {
    if (globalSyncInFlight) {
      return;
    }
    globalSyncInFlight = true;
    const originalText = globalSyncButton.textContent;
    globalSyncButton.disabled = true;
    globalSyncButton.textContent = "同步中…";
    setStatus("同步中…", "warning");

    try {
      const payload = await api.apiRequest("/webui/api/sync/trigger", {
        method: "POST",
        headers: { Accept: "application/json" },
        action: "同步",
        expectedStatus: 200,
      });
      const result = api.unwrapData(payload) || {};
      const syncOutcomes = Array.isArray(result.sync_outcomes)
        ? result.sync_outcomes
        : [];
      const lines = renderSyncOutcomes(syncOutcomes);
      if (lines.length === 0) {
        setStatus("同步成功，暂无服务器", "success");
      } else {
        const toastType = allSyncOk(syncOutcomes) ? "success" : "warning";
        setStatus(lines.join("\n"), toastType);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知错误";
      setStatus(`同步失败，${message}`, "error");
    } finally {
      globalSyncInFlight = false;
      globalSyncButton.disabled = false;
      globalSyncButton.textContent = originalText;
    }
  };

  globalSyncButton.addEventListener("click", () => {
    void triggerGlobalSync();
  });

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
  fieldPasswordGenerate.addEventListener("click", () => {
    generatePassword();
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

  changePasswordModalCloseButton.addEventListener("click", () => {
    closeChangePasswordModal();
  });
  changePasswordModalCancelButton.addEventListener("click", () => {
    closeChangePasswordModal();
  });
  changePasswordModalConfirmButton.addEventListener("click", () => {
    void confirmChangePassword();
  });
  changePasswordGenerateButton.addEventListener("click", () => {
    generateChangePassword();
  });
  changePasswordModalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.changePasswordModalClose === "1") {
      closeChangePasswordModal();
    }
  });

  changeNameModalCloseButton.addEventListener("click", () => {
    closeChangeNameModal();
  });
  changeNameModalCancelButton.addEventListener("click", () => {
    closeChangeNameModal();
  });
  changeNameModalConfirmButton.addEventListener("click", () => {
    void confirmChangeName();
  });
  changeNameModalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.changeNameModalClose === "1") {
      closeChangeNameModal();
    }
  });

  // M-10：modal 注册到统一 ESC dispatcher，仅栈顶 modal 响应
  registerModalCloser(modalNode, () => closeModal());
  registerModalCloser(deleteModalNode, () => closeDeleteModal());
  registerModalCloser(banModalNode, () => closeBanModal());
  registerModalCloser(changePasswordModalNode, () => closeChangePasswordModal());
  registerModalCloser(changeNameModalNode, () => closeChangeNameModal());

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
