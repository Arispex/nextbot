(() => {
  const reloadButton = document.getElementById("reload-btn");
  const addServerButton = document.getElementById("add-server-btn");
  const searchInput = document.getElementById("server-search");

  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const loadingNode = document.getElementById("loading");
  const emptyNode = document.getElementById("empty");
  const tableWrapNode = document.getElementById("table-wrap");
  const tableBodyNode = document.getElementById("server-table-body");
  const paginationNode = document.getElementById("server-pagination");
  const paginationInfoNode = document.getElementById("server-pagination-info");
  const perPageSelect = document.getElementById("server-per-page");
  const prevPageButton = document.getElementById("server-prev-btn");
  const nextPageButton = document.getElementById("server-next-btn");

  const modalNode = document.getElementById("server-modal");
  const modalTitleNode = document.getElementById("server-modal-title");
  const modalAlertNode = document.getElementById("modal-alert");
  const modalAlertMessageNode = document.getElementById("modal-alert-message");
  const modalCloseButton = document.getElementById("modal-close-btn");
  const modalCancelButton = document.getElementById("modal-cancel-btn");
  const modalSaveButton = document.getElementById("modal-save-btn");
  const modalTokenToggleButton = document.getElementById("modal-token-toggle");
  const deleteModalNode = document.getElementById("delete-modal");
  const deleteModalTextNode = document.getElementById("delete-modal-text");
  const deleteModalAlertNode = document.getElementById("delete-modal-alert");
  const deleteModalAlertMessageNode = document.getElementById("delete-modal-alert-message");
  const deleteModalCloseButton = document.getElementById("delete-modal-close-btn");
  const deleteModalCancelButton = document.getElementById("delete-modal-cancel-btn");
  const deleteModalConfirmButton = document.getElementById("delete-modal-confirm-btn");

  const nameInput = document.getElementById("field-name");
  const ipInput = document.getElementById("field-ip");
  const gamePortInput = document.getElementById("field-game-port");
  const restapiPortInput = document.getElementById("field-restapi-port");
  const tokenInput = document.getElementById("field-token");

  const requiredNodesReady = Boolean(
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
      modalTokenToggleButton &&
      deleteModalNode &&
      deleteModalTextNode &&
      deleteModalAlertNode &&
      deleteModalAlertMessageNode &&
      deleteModalCloseButton &&
      deleteModalCancelButton &&
      deleteModalConfirmButton &&
      nameInput &&
      ipInput &&
      gamePortInput &&
      restapiPortInput &&
      tokenInput
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;
  const NAME_PATTERN = /^[A-Za-z0-9\u4e00-\u9fff ._-]{1,32}$/u;
  const SHOW_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"></path>
      <circle cx="12" cy="12" r="3"></circle>
    </svg>
  `;
  const HIDE_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-11-7-11-7a21.76 21.76 0 0 1 5.06-5.94"></path>
      <path d="M9.9 4.24A10.93 10.93 0 0 1 12 4c7 0 11 7 11 7a21.86 21.86 0 0 1-3.12 4.36"></path>
      <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"></path>
      <path d="M1 1l22 22"></path>
    </svg>
  `;

  let serverStates = [];
  let modalMode = "create";
  let editingServerId = null;
  let modalTokenVisible = false;
  let modalSaving = false;
  let deletingServer = null;
  let deleteSaving = false;
  let currentPage = 1;
  let currentPerPage = Number(perPageSelect.value || 20);
  let currentMeta = { total: 0, page: 1, per_page: currentPerPage, total_pages: 0 };

  const visibleTokenIds = new Set();
  const testResultMap = new Map();

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

  const normalizeServer = (item) => ({
    id: Number(item?.id || 0),
    name: String(item?.name || ""),
    ip: String(item?.ip || ""),
    game_port: String(item?.game_port || ""),
    restapi_port: String(item?.restapi_port || ""),
    token: String(item?.token || ""),
  });

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
    const end = Math.min(total, start + Math.max(serverStates.length - 1, 0));
    paginationInfoNode.textContent = `第 ${page} / ${Math.max(totalPages, 1)} 页，共 ${total} 条，当前显示 ${start}-${end}`;
    prevPageButton.disabled = page <= 1;
    nextPageButton.disabled = totalPages <= 0 || page >= totalPages;
  };

  const formatMaskedToken = (token) => {
    const length = Math.max(8, Math.min(16, String(token).length || 8));
    return "•".repeat(length);
  };

  const setTokenButtonIcon = (button, visible) => {
    button.innerHTML = visible ? HIDE_ICON_SVG : SHOW_ICON_SVG;
    button.title = visible ? "隐藏 Token" : "显示 Token";
    button.setAttribute("aria-label", button.title);
  };

  const buildResultBadge = (serverId) => {
    const badge = document.createElement("span");
    badge.className = "result-badge";

    const result = testResultMap.get(serverId);
    if (!result || result.status === "idle") {
      badge.textContent = "未测试";
      return badge;
    }

    if (result.status === "loading") {
      badge.classList.add("warning");
      badge.textContent = "正在测试";
      return badge;
    }

    if (result.status === "success") {
      badge.classList.add("success");
      badge.textContent = "连通成功";
      if (result.reason) {
        badge.title = result.reason;
      }
      return badge;
    }

    badge.classList.add("danger");
    badge.textContent = "连通失败";
    if (result.reason) {
      badge.title = result.reason;
    }
    return badge;
  };

  const renderTable = () => {
    tableBodyNode.innerHTML = "";
    loadingNode.classList.add("hidden");

    if (!serverStates.length) {
      emptyNode.textContent = currentMeta.total > 0 ? "当前页暂无数据。" : "暂无服务器配置。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      updatePagination();
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const server of serverStates) {
      const row = document.createElement("tr");
      row.dataset.serverId = String(server.id);

      const idCell = document.createElement("td");
      idCell.className = "id-cell";
      idCell.textContent = String(server.id);

      const nameCell = document.createElement("td");
      nameCell.className = "name-cell";
      const nameText = document.createElement("p");
      nameText.className = "name-text";
      nameText.textContent = server.name;
      nameCell.appendChild(nameText);

      const hostCell = document.createElement("td");
      hostCell.className = "host-cell";
      hostCell.textContent = server.ip;

      const gamePortCell = document.createElement("td");
      gamePortCell.className = "port-cell";
      gamePortCell.textContent = server.game_port;

      const restPortCell = document.createElement("td");
      restPortCell.className = "port-cell";
      restPortCell.textContent = server.restapi_port;

      const tokenCell = document.createElement("td");
      tokenCell.className = "token-cell";
      const tokenWrap = document.createElement("div");
      tokenWrap.className = "token-wrap";
      const tokenText = document.createElement("span");
      tokenText.className = "token-text";
      const tokenVisible = visibleTokenIds.has(server.id);
      tokenText.textContent = tokenVisible ? server.token : formatMaskedToken(server.token);
      tokenText.title = tokenVisible ? server.token : "已隐藏";

      const tokenToggleButton = document.createElement("button");
      tokenToggleButton.type = "button";
      tokenToggleButton.className = "btn token-toggle-btn";
      setTokenButtonIcon(tokenToggleButton, tokenVisible);
      tokenToggleButton.addEventListener("click", () => {
        if (visibleTokenIds.has(server.id)) {
          visibleTokenIds.delete(server.id);
        } else {
          visibleTokenIds.add(server.id);
        }
        renderTable();
      });

      tokenWrap.appendChild(tokenText);
      tokenWrap.appendChild(tokenToggleButton);
      tokenCell.appendChild(tokenWrap);

      const resultCell = document.createElement("td");
      resultCell.appendChild(buildResultBadge(server.id));

      const actionCell = document.createElement("td");
      actionCell.className = "actions-cell";
      const actions = document.createElement("div");
      actions.className = "row-actions";

      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.className = "btn action-btn";
      editButton.textContent = "编辑";
      editButton.addEventListener("click", () => {
        openModal("edit", server);
      });

      const testButton = document.createElement("button");
      testButton.type = "button";
      testButton.className = "btn action-btn";
      testButton.textContent = "测试";
      const resultState = testResultMap.get(server.id);
      if (resultState?.status === "loading") {
        testButton.disabled = true;
        testButton.textContent = "正在测试";
      }
      testButton.addEventListener("click", () => {
        void testServerConnectivity(server.id);
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "btn action-btn action-btn-danger";
      deleteButton.textContent = "删除";
      deleteButton.addEventListener("click", () => {
        openDeleteModal(server);
      });

      actions.appendChild(editButton);
      actions.appendChild(testButton);
      actions.appendChild(deleteButton);
      actionCell.appendChild(actions);

      row.appendChild(idCell);
      row.appendChild(nameCell);
      row.appendChild(hostCell);
      row.appendChild(gamePortCell);
      row.appendChild(restPortCell);
      row.appendChild(tokenCell);
      row.appendChild(resultCell);
      row.appendChild(actionCell);
      tableBodyNode.appendChild(row);
    }

    updatePagination();
  };

  const loadServers = async ({ clearStatus = true } = {}) => {
    if (clearStatus) {
      setStatus("");
    }
    loadingNode.classList.remove("hidden");
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");
    paginationNode.classList.add("hidden");

    try {
      const payload = await api.apiRequest(
        `/webui/api/servers?page=${encodeURIComponent(String(currentPage))}&per_page=${encodeURIComponent(String(currentPerPage))}&q=${encodeURIComponent(String(searchInput.value || "").trim())}`,
        {
          method: "GET",
          headers: { Accept: "application/json" },
          action: "加载",
          expectedStatus: 200,
        }
      );
      const servers = api.unwrapData(payload);
      const meta = api.unwrapMeta(payload);
      if (!Array.isArray(servers)) {
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
      serverStates = servers.map(normalizeServer);
      const validIds = new Set(serverStates.map((item) => item.id));
      for (const key of [...visibleTokenIds]) {
        if (!validIds.has(key)) {
          visibleTokenIds.delete(key);
        }
      }
      for (const key of [...testResultMap.keys()]) {
        if (!validIds.has(key)) {
          testResultMap.delete(key);
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

  const openModal = (mode, server = null) => {
    modalMode = mode;
    editingServerId = mode === "edit" && server ? server.id : null;
    modalSaving = false;
    modalTokenVisible = false;
    tokenInput.type = "password";
    setTokenButtonIcon(modalTokenToggleButton, false);
    setModalAlert("");

    if (mode === "edit" && server) {
      modalTitleNode.textContent = "编辑服务器";
      modalSaveButton.textContent = "保存修改";
      nameInput.value = server.name;
      ipInput.value = server.ip;
      gamePortInput.value = server.game_port;
      restapiPortInput.value = server.restapi_port;
      tokenInput.value = server.token;
    } else {
      modalTitleNode.textContent = "创建服务器";
      modalSaveButton.textContent = "创建服务器";
      nameInput.value = "";
      ipInput.value = "";
      gamePortInput.value = "";
      restapiPortInput.value = "";
      tokenInput.value = "";
    }

    modalNode.classList.remove("hidden");
    nameInput.focus();
  };

  const closeModal = (force = false) => {
    if (modalSaving && !force) {
      return;
    }
    modalNode.classList.add("hidden");
  };

  const openDeleteModal = (server) => {
    deletingServer = server;
    deleteSaving = false;
    deleteModalConfirmButton.disabled = false;
    setDeleteModalAlert("");
    deleteModalTextNode.textContent = `确定要删除服务器 “${server.name}” 吗？此操作无法撤销。`;
    deleteModalNode.classList.remove("hidden");
  };

  const closeDeleteModal = (force = false) => {
    if (deleteSaving && !force) {
      return;
    }
    deleteModalNode.classList.add("hidden");
    if (force || !deleteSaving) {
      deletingServer = null;
    }
  };

  const parsePort = (fieldName, rawValue) => {
    const text = String(rawValue || "").trim();
    if (!text) {
      throw new Error(`${fieldName}不能为空`);
    }
    const parsed = Number(text);
    if (!Number.isInteger(parsed)) {
      throw new Error(`${fieldName}必须是整数`);
    }
    if (parsed < 1 || parsed > 65535) {
      throw new Error(`${fieldName}范围必须在 1-65535`);
    }
    return String(parsed);
  };

  const buildPayloadFromModal = () => {
    const name = String(nameInput.value || "").trim();
    const ip = String(ipInput.value || "").trim();
    const token = String(tokenInput.value || "").trim();
    const gamePort = parsePort("游戏端口", gamePortInput.value);
    const restapiPort = parsePort("REST API 端口", restapiPortInput.value);

    if (!name) {
      throw new Error("服务器名称不能为空");
    }
    if (!NAME_PATTERN.test(name)) {
      throw new Error("服务器名称格式错误，仅允许中英文、数字、空格和 -_.，长度 1-32");
    }
    if (!ip) {
      throw new Error("地址不能为空");
    }
    if (!token) {
      throw new Error("Token 不能为空");
    }
    if (token.length < 1 || token.length > 128) {
      throw new Error("Token 长度必须在 1-128 之间");
    }

    return {
      name,
      ip,
      game_port: gamePort,
      restapi_port: restapiPort,
      token,
    };
  };

  const saveServer = async () => {
    if (modalSaving) {
      return;
    }

    const isEdit = modalMode === "edit" && typeof editingServerId === "number";

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
      const url = isEdit ? `/webui/api/servers/${editingServerId}` : "/webui/api/servers";
      const method = isEdit ? "PATCH" : "POST";

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

      closeModal(true);
      currentPage = 1;
      const reloaded = await loadServers({ clearStatus: false });
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

  const confirmDeleteServer = async () => {
    if (!deletingServer || deleteSaving) {
      return;
    }
    const targetServer = deletingServer;
    deleteSaving = true;
    deleteModalConfirmButton.disabled = true;
    setDeleteModalAlert(`正在删除服务器 #${targetServer.id}...`, "warning");
    setStatus(`正在删除服务器 #${targetServer.id}...`, "warning");

    try {
      await api.apiRequest(`/webui/api/servers/${targetServer.id}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        action: "删除",
        expectedStatus: 204,
      });

      visibleTokenIds.delete(targetServer.id);
      testResultMap.delete(targetServer.id);
      closeDeleteModal(true);
      const reloaded = await loadServers({ clearStatus: false });
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

  const testServerConnectivity = async (serverId) => {
    testResultMap.set(serverId, { status: "loading", reason: "正在测试" });
    renderTable();
    setStatus(`正在测试服务器 #${serverId} 连通性...`, "warning");

    try {
      const payload = await api.apiRequest(`/webui/api/servers/${serverId}/test`, {
        method: "POST",
        headers: { Accept: "application/json" },
        action: "测试",
        expectedStatus: 200,
      });
      const result = api.unwrapData(payload);
      const reachable = Boolean(result.reachable);
      const reason = String(result.reason || "");
      const message = reachable ? "测试成功" : api.buildActionFailureMessage("测试", reason);
      testResultMap.set(serverId, {
        status: reachable ? "success" : "failed",
        reason,
      });
      setStatus(message, reachable ? "success" : "error");
      renderTable();
    } catch (error) {
      const message = error instanceof Error ? error.message : "测试失败";
      testResultMap.set(serverId, {
        status: "failed",
        reason: message,
      });
      setStatus(message, "error");
      renderTable();
    }
  };

  reloadButton?.addEventListener("click", () => {
    currentPage = 1;
    void loadServers();
  });

  addServerButton?.addEventListener("click", () => {
    openModal("create");
  });

  searchInput?.addEventListener("input", () => {
    currentPage = 1;
    void loadServers();
  });

  perPageSelect.addEventListener("change", () => {
    currentPerPage = Number(perPageSelect.value || 20);
    currentPage = 1;
    void loadServers();
  });

  prevPageButton.addEventListener("click", () => {
    if (currentPage <= 1) {
      return;
    }
    currentPage -= 1;
    void loadServers({ clearStatus: false });
  });

  nextPageButton.addEventListener("click", () => {
    if (currentMeta.total_pages > 0 && currentPage >= currentMeta.total_pages) {
      return;
    }
    currentPage += 1;
    void loadServers({ clearStatus: false });
  });

  modalCloseButton.addEventListener("click", closeModal);
  modalCancelButton.addEventListener("click", closeModal);
  modalSaveButton.addEventListener("click", () => {
    void saveServer();
  });

  modalTokenToggleButton.addEventListener("click", () => {
    modalTokenVisible = !modalTokenVisible;
    tokenInput.type = modalTokenVisible ? "text" : "password";
    setTokenButtonIcon(modalTokenToggleButton, modalTokenVisible);
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
    void confirmDeleteServer();
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

  void loadServers();
})();
