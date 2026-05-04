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
      syncButton.textContent = "同步";
      const syncState = syncResultMap.get(user.id);
      if (syncState?.status === "loading") {
        syncButton.disabled = true;
        syncButton.textContent = "同步中…";
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

  const loadUsers = async ({ clearStatus = true } = {}) => {
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

  const closeModal = () => {
    if (modalSaving) {
      return;
    }
    modalNode.classList.add("hidden");
  };

  const openBanModal = (user) => {
    banningUser = user;
    banSaving = false;
    banModalConfirmButton.disabled = false;
    setBanModalAlert("");
    banReasonInput.value = "";
    banModalTextNode.textContent = `确定要封禁用户 "${user.name || "未命名用户"}" 吗？`;
    banModalNode.classList.remove("hidden");
    banReasonInput.focus();
  };

  const closeBanModal = (force = false) => {
    if (banSaving && !force) {
      return;
    }
    banModalNode.classList.add("hidden");
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
    setBanModalAlert("正在封禁...", "warning");

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
    modalNode.classList.remove("hidden");
    fieldUserId.focus();
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
        body: JSON.stringify(payload),
        action: isEdit ? "更新" : "创建",
        expectedStatus: isEdit ? 200 : 201,
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
      await api.apiRequest(`/webui/api/users/${targetUser.id}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        action: "删除",
        expectedStatus: 204,
      });
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

  const toggleBan = async (user, ban, reason) => {
    var actionText = ban ? "封禁" : "解封";
    setStatus(actionText + "中...", "warning");

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
      var lines = [actionText + "成功，用户 " + user.name];
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
            lines.push(serverId + "." + serverName + "：失败，" + (item.reason || "未知错误"));
          }
        }
      }
      setStatus(lines.join("\n"), "success");
      await loadUsers({ clearStatus: false });
    } catch (error) {
      var message = error instanceof Error ? error.message : actionText + "失败";
      setStatus(message, "error");
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
    currentPage = 1;
    void loadUsers();
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

  searchInput.addEventListener("input", () => {
    currentPage = 1;
    void loadUsers();
  });

  perPageSelect.addEventListener("change", () => {
    currentPerPage = Number(perPageSelect.value || 10);
    currentPage = 1;
    void loadUsers();
  });

  prevPageButton.addEventListener("click", () => {
    if (currentPage <= 1) {
      return;
    }
    currentPage -= 1;
    void loadUsers({ clearStatus: false });
  });

  nextPageButton.addEventListener("click", () => {
    if (currentMeta.total_pages > 0 && currentPage >= currentMeta.total_pages) {
      return;
    }
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

  void loadUsers();
})();
