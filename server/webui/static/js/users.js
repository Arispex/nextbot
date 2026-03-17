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

  const modalNode = document.getElementById("user-modal");
  const modalTitleNode = document.getElementById("user-modal-title");
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

  const fieldUserId = document.getElementById("field-user-id");
  const fieldName = document.getElementById("field-name");
  const fieldCoins = document.getElementById("field-coins");
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
      fieldUserId &&
      fieldName &&
      fieldCoins &&
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
  let modalMode = "create";
  let editingUserDbId = null;
  let modalSaving = false;
  let deletingUser = null;
  let deleteSaving = false;

  const syncResultMap = new Map();

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
    permissions: normalizePermissionsText(item?.permissions || ""),
    group: String(item?.group || ""),
    created_at: String(item?.created_at || ""),
  });

  const ensureGroupOptions = () => {
    if (!groupOptions.length) {
      groupOptions = ["guest", "default"];
    }
  };

  const renderGroupSelectOptions = (selectedGroup = "") => {
    ensureGroupOptions();
    fieldGroup.innerHTML = "";
    const options = [...groupOptions];
    if (selectedGroup && !options.includes(selectedGroup)) {
      options.push(selectedGroup);
    }
    options.sort((a, b) => a.localeCompare(b));

    for (const group of options) {
      const option = document.createElement("option");
      option.value = group;
      option.textContent = group;
      fieldGroup.appendChild(option);
    }
    if (selectedGroup && options.includes(selectedGroup)) {
      fieldGroup.value = selectedGroup;
    } else if (groupOptions.includes("default")) {
      fieldGroup.value = "default";
    } else if (options.length > 0) {
      fieldGroup.value = options[0];
    }
  };

  const getFilteredUsers = () => {
    const keyword = String(searchInput.value || "").trim().toLowerCase();
    if (!keyword) {
      return [...userStates];
    }
    return userStates.filter((user) => {
      const text = [
        String(user.id),
        user.user_id,
        user.name,
        user.group,
        user.permissions,
      ].join(" ").toLowerCase();
      return text.includes(keyword);
    });
  };

  const renderTable = () => {
    tableBodyNode.innerHTML = "";
    loadingNode.classList.add("hidden");

    const filteredUsers = getFilteredUsers().sort((a, b) => a.id - b.id);

    if (!userStates.length) {
      emptyNode.textContent = "暂无用户数据。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    if (!filteredUsers.length) {
      emptyNode.textContent = "没有匹配的用户。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const user of filteredUsers) {
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

      const groupCell = document.createElement("td");
      groupCell.className = "group-cell";
      groupCell.textContent = user.group;

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
      editButton.addEventListener("click", () => {
        openModal("edit", user);
      });

      const syncButton = document.createElement("button");
      syncButton.type = "button";
      syncButton.className = "btn action-btn action-btn-sync";
      syncButton.textContent = "同步白名单";
      const syncState = syncResultMap.get(user.id);
      if (syncState?.status === "loading") {
        syncButton.disabled = true;
        syncButton.textContent = "正在同步";
      }
      syncButton.addEventListener("click", () => {
        void syncWhitelist(user);
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
      actions.appendChild(deleteButton);
      actionCell.appendChild(actions);

      row.appendChild(idCell);
      row.appendChild(userIdCell);
      row.appendChild(nameCell);
      row.appendChild(coinsCell);
      row.appendChild(groupCell);
      row.appendChild(permissionCell);
      row.appendChild(createdCell);
      row.appendChild(actionCell);
      tableBodyNode.appendChild(row);
    }
  };

  const loadUsers = async ({ clearStatus = true } = {}) => {
    if (clearStatus) {
      setStatus("");
    }
    loadingNode.classList.remove("hidden");
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");

    try {
      const payload = await api.apiRequest("/webui/api/users", {
        method: "GET",
        headers: { Accept: "application/json" },
        errorPrefix: "加载失败",
      });
      const users = api.unwrapData(payload);
      const meta = api.unwrapMeta(payload);
      const groups = Array.isArray(meta.groups) ? meta.groups : [];
      if (!Array.isArray(users)) {
        throw new Error("加载失败，返回数据格式错误");
      }

      userStates = users.map(normalizeUser);
      groupOptions = [...new Set(groups.map((item) => String(item || "").trim()).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b));
      ensureGroupOptions();

      const validIds = new Set(userStates.map((item) => item.id));
      for (const key of [...syncResultMap.keys()]) {
        if (!validIds.has(key)) {
          syncResultMap.delete(key);
        }
      }

      renderTable();
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      loadingNode.classList.add("hidden");
      emptyNode.classList.remove("hidden");
      emptyNode.textContent = message;
      tableWrapNode.classList.add("hidden");
      return false;
    }
  };

  const closeModal = () => {
    if (modalSaving) {
      return;
    }
    modalNode.classList.add("hidden");
  };

  const openDeleteModal = (user) => {
    deletingUser = user;
    deleteSaving = false;
    deleteModalConfirmButton.disabled = false;
    setDeleteModalAlert("");
    deleteModalTextNode.textContent = `确定要删除用户 “${user.name || "未命名用户"}” 吗？此操作无法撤销。`;
    deleteModalNode.classList.remove("hidden");
  };

  const closeDeleteModal = (force = false) => {
    if (deleteSaving && !force) {
      return;
    }
    deleteModalNode.classList.add("hidden");
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
      modalSaveButton.textContent = "保存修改";
      fieldUserId.value = user.user_id;
      fieldName.value = user.name;
      fieldCoins.value = String(user.coins);
      renderGroupSelectOptions(user.group);
      fieldPermissions.value = user.permissions || "";
    } else {
      modalTitleNode.textContent = "创建用户";
      modalSaveButton.textContent = "创建用户";
      fieldUserId.value = "";
      fieldName.value = "";
      fieldCoins.value = "0";
      renderGroupSelectOptions("default");
      fieldPermissions.value = "";
    }

    updatePermissionPreview();
    modalNode.classList.remove("hidden");
    fieldUserId.focus();
  };

  const buildPayloadFromModal = () => {
    const userId = String(fieldUserId.value || "").trim();
    const name = String(fieldName.value || "").trim();
    const coinsText = String(fieldCoins.value || "").trim();
    const group = String(fieldGroup.value || "").trim();
    const permissions = normalizePermissionsText(fieldPermissions.value || "");

    if (!userId) {
      throw new Error("用户 ID 不能为空");
    }
    if (!USER_ID_PATTERN.test(userId)) {
      throw new Error("用户 ID 必须是 5-20 位数字");
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

    if (!group) {
      throw new Error("身份组不能为空");
    }

    return {
      user_id: userId,
      name,
      coins: coinsNumber,
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
    setModalAlert("正在保存...", "info");

    try {
      const url = isEdit ? `/webui/api/users/${editingUserDbId}` : "/webui/api/users";
      const method = isEdit ? "PUT" : "POST";

      await api.apiRequest(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ data: payload }),
        errorPrefix: isEdit ? "更新失败" : "创建失败",
      });

      modalNode.classList.add("hidden");
      const reloaded = await loadUsers({ clearStatus: false });
      if (reloaded) {
        setStatus(isEdit ? "更新成功" : "创建成功", "success");
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
    setDeleteModalAlert(`正在删除用户 #${targetUser.id}...`, "warning");

    setStatus(`正在删除用户 #${targetUser.id}...`, "warning");
    try {
      const payload = await api.apiRequest(`/webui/api/users/${targetUser.id}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        errorPrefix: "删除失败",
      });
      api.unwrapData(payload);
      syncResultMap.delete(targetUser.id);
      closeDeleteModal(true);
      const reloaded = await loadUsers({ clearStatus: false });
      if (reloaded) {
        setStatus("删除成功", "success");
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

  const syncWhitelist = async (user) => {
    syncResultMap.set(user.id, {
      status: "loading",
      successCount: 0,
      failedCount: 0,
    });
    renderTable();
    setStatus(`正在同步用户 ${user.name} 的白名单...`, "warning");

    try {
      const payload = await api.apiRequest(`/webui/api/users/${user.id}/sync-whitelist`, {
        method: "POST",
        headers: { Accept: "application/json" },
        errorPrefix: "同步失败",
      });
      const result = api.unwrapData(payload);

      const userName = String(result.name || user.name);
      const syncResults = Array.isArray(result.results) ? result.results : [];
      const lines = [`用户 ${userName} 白名单同步结果：`];
      let successCount = 0;
      let failedCount = 0;

      if (!syncResults.length) {
        lines.push(String(result.message || "同步失败，暂无可同步的服务器"));
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
            const reason = String(item?.reason || "未知错误");
            lines.push(`${serverId}.${serverName}：同步失败，${reason}`);
          }
        }
      }

      syncResultMap.set(user.id, {
        status: failedCount > 0 || !syncResults.length ? "failed" : "success",
        successCount,
        failedCount,
      });
      setStatus(lines.join("\n"), failedCount > 0 || !syncResults.length ? "error" : "success");
      renderTable();
    } catch (error) {
      const message = error instanceof Error ? error.message : "同步失败";
      syncResultMap.set(user.id, {
        status: "failed",
        successCount: 0,
        failedCount: 0,
      });
      setStatus(message, "error");
      renderTable();
    }
  };

  reloadButton.addEventListener("click", () => {
    void loadUsers();
  });

  addUserButton.addEventListener("click", () => {
    openModal("create");
  });

  searchInput.addEventListener("input", () => {
    renderTable();
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

  void loadUsers();
})();
