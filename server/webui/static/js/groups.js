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
  const GROUP_NAME_PATTERN = /^[A-Za-z0-9\u4e00-\u9fff._-]{1,32}$/u;
  const ITEM_PATTERN = /^[^\s,]{1,256}$/u;

  let groupStates = [];
  let modalMode = "create";
  let editingGroupName = "";
  let modalSaving = false;
  let deletingGroup = null;
  let deleteSaving = false;
  let currentPage = 1;
  let currentPerPage = Number(perPageSelect.value || 20);
  let currentMeta = { total: 0, page: 1, per_page: currentPerPage, total_pages: 0 };

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
    tableBodyNode.innerHTML = "";
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

  const loadGroups = async ({ clearStatus = true } = {}) => {
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
        }
      );
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

    const isEdit = modalMode === "edit" && editingGroupName;

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
      const url = isEdit
        ? `/webui/api/groups/${encodeURIComponent(editingGroupName)}`
        : "/webui/api/groups";
      const method = isEdit ? "PATCH" : "POST";
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

      modalNode.classList.add("hidden");
      const reloaded = await loadGroups({ clearStatus: false });
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
      await api.apiRequest(`/webui/api/groups/${encodeURIComponent(targetGroup.name)}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        action: "删除",
        expectedStatus: 204,
      });
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
    currentPage = 1;
    void loadGroups();
  });

  addGroupButton.addEventListener("click", () => {
    openModal("create");
  });

  searchInput.addEventListener("input", () => {
    currentPage = 1;
    void loadGroups();
  });

  perPageSelect.addEventListener("change", () => {
    currentPerPage = Number(perPageSelect.value || 20);
    currentPage = 1;
    void loadGroups();
  });

  prevPageButton.addEventListener("click", () => {
    if (currentPage <= 1) {
      return;
    }
    currentPage -= 1;
    void loadGroups({ clearStatus: false });
  });

  nextPageButton.addEventListener("click", () => {
    if (currentMeta.total_pages > 0 && currentPage >= currentMeta.total_pages) {
      return;
    }
    currentPage += 1;
    void loadGroups({ clearStatus: false });
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
