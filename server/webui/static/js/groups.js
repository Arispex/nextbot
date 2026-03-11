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

  const GROUP_NAME_PATTERN = /^[A-Za-z0-9\u4e00-\u9fff._-]{1,32}$/u;
  const ITEM_PATTERN = /^[^\s,]{1,64}$/u;

  const DEFAULT_BUILTIN_GROUPS = ["guest", "default"];

  let groupStates = [];
  let builtinGroups = new Set(DEFAULT_BUILTIN_GROUPS);
  let modalMode = "create";
  let editingGroupName = "";
  let modalSaving = false;
  let deletingGroup = null;
  let deleteSaving = false;

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

  const parseJsonSafe = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const readErrorMessage = (payload, fallback) => {
    if (payload && typeof payload.message === "string" && payload.message.trim()) {
      return payload.message.trim();
    }
    return fallback;
  };

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
        throw new Error(`${fieldLabel}项格式错误，不能包含空白或逗号，且长度 1-64`);
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
    container.innerHTML = "";
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

  const getFilteredGroups = () => {
    const keyword = String(searchInput.value || "").trim().toLowerCase();
    if (!keyword) {
      return [...groupStates];
    }
    return groupStates.filter((group) => {
      const text = [
        group.name,
        group.permissions,
        group.inherits,
      ].join(" ").toLowerCase();
      return text.includes(keyword);
    });
  };

  const renderTypeBadge = (builtin) => {
    const badge = document.createElement("span");
    badge.className = `group-type-badge ${builtin ? "builtin" : "normal"}`;
    badge.textContent = builtin ? "内置" : "普通";
    return badge;
  };

  const renderTable = () => {
    tableBodyNode.innerHTML = "";
    loadingNode.classList.add("hidden");

    const filteredGroups = getFilteredGroups().sort((a, b) => a.name.localeCompare(b.name));

    if (!groupStates.length) {
      emptyNode.textContent = "暂无身份组数据。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    if (!filteredGroups.length) {
      emptyNode.textContent = "没有匹配的身份组。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const group of filteredGroups) {
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
  };

  const loadGroups = async ({ clearStatus = true } = {}) => {
    if (clearStatus) {
      setStatus("");
    }
    loadingNode.classList.remove("hidden");
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");

    try {
      const response = await fetch("/webui/api/groups", {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      const payload = await parseJsonSafe(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload, `加载失败（HTTP ${response.status}）`));
      }
      if (!payload || payload.ok !== true || !Array.isArray(payload.groups)) {
        throw new Error("加载失败，返回数据格式错误");
      }

      const builtinValues = Array.isArray(payload.builtin_groups)
        ? payload.builtin_groups.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      builtinGroups = new Set(builtinValues.length ? builtinValues : DEFAULT_BUILTIN_GROUPS);

      groupStates = payload.groups.map(normalizeGroup).map((group) => ({
        ...group,
        builtin: group.builtin || builtinGroups.has(group.name),
      }));

      renderTable();
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      loadingNode.classList.add("hidden");
      emptyNode.classList.remove("hidden");
      emptyNode.textContent = "加载失败，请点击刷新重试。";
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

  const openDeleteModal = (group) => {
    deletingGroup = group;
    deleteSaving = false;
    deleteModalConfirmButton.disabled = false;
    setDeleteModalAlert("");
    deleteModalTextNode.textContent = `确定要删除身份组 “${group.name}” 吗？此操作无法撤销。`;
    deleteModalNode.classList.remove("hidden");
  };

  const closeDeleteModal = (force = false) => {
    if (deleteSaving && !force) {
      return;
    }
    deleteModalNode.classList.add("hidden");
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
      modalSaveButton.textContent = "保存修改";
      fieldName.value = group.name;
      fieldName.readOnly = true;
      fieldPermissions.value = group.permissions || "";
      fieldInherits.value = group.inherits || "";
    } else {
      modalTitleNode.textContent = "创建身份组";
      modalSaveButton.textContent = "创建身份组";
      fieldName.value = "";
      fieldName.readOnly = false;
      fieldPermissions.value = "";
      fieldInherits.value = "";
    }

    updatePreview();
    modalNode.classList.remove("hidden");
    if (modalMode === "create") {
      fieldName.focus();
    } else {
      fieldPermissions.focus();
    }
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

  const saveGroup = async () => {
    if (modalSaving) {
      return;
    }

    let payload;
    try {
      payload = buildPayloadFromModal();
    } catch (error) {
      const message = error instanceof Error ? error.message : "表单校验失败";
      setModalAlert(message, "error");
      return;
    }

    modalSaving = true;
    modalSaveButton.disabled = true;
    setModalAlert("正在保存...", "info");

    try {
      const isEdit = modalMode === "edit" && editingGroupName;
      const url = isEdit
        ? `/webui/api/groups/${encodeURIComponent(editingGroupName)}`
        : "/webui/api/groups";
      const method = isEdit ? "PUT" : "POST";
      const requestPayload = isEdit
        ? {
            permissions: payload.permissions,
            inherits: payload.inherits,
          }
        : {
            name: payload.name,
            permissions: payload.permissions,
            inherits: payload.inherits,
          };

      const response = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(requestPayload),
      });
      const result = await parseJsonSafe(response);
      if (!response.ok || !result || result.ok !== true) {
        throw new Error(readErrorMessage(result, "保存失败"));
      }

      modalNode.classList.add("hidden");
      const reloaded = await loadGroups({ clearStatus: false });
      if (reloaded) {
        setStatus(isEdit ? "身份组已更新" : "身份组已创建", "success");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存失败";
      setModalAlert(message, "error");
    } finally {
      modalSaving = false;
      modalSaveButton.disabled = false;
    }
  };

  const confirmDeleteGroup = async () => {
    if (!deletingGroup || deleteSaving) {
      return;
    }
    const targetGroup = deletingGroup;
    deleteSaving = true;
    deleteModalConfirmButton.disabled = true;
    setDeleteModalAlert(`正在删除身份组 ${targetGroup.name}...`, "warning");

    setStatus(`正在删除身份组 ${targetGroup.name}...`, "warning");
    try {
      const response = await fetch(`/webui/api/groups/${encodeURIComponent(targetGroup.name)}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      const result = await parseJsonSafe(response);
      if (!response.ok || !result || result.ok !== true) {
        throw new Error(readErrorMessage(result, "删除失败"));
      }
      closeDeleteModal(true);
      const reloaded = await loadGroups({ clearStatus: false });
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

  reloadButton.addEventListener("click", () => {
    void loadGroups();
  });

  addGroupButton.addEventListener("click", () => {
    openModal("create");
  });

  searchInput.addEventListener("input", () => {
    renderTable();
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

  void loadGroups();
})();
