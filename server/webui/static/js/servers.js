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

  const normalizeServer = (item) => {
    return {
      id: Number(item?.id || 0),
      name: String(item?.name || ""),
      ip: String(item?.ip || ""),
      game_port: String(item?.game_port || ""),
      restapi_port: String(item?.restapi_port || ""),
      token: String(item?.token || ""),
    };
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

  const getFilteredServers = () => {
    const keyword = String(searchInput?.value || "").trim().toLowerCase();
    if (!keyword) {
      return [...serverStates];
    }

    return serverStates.filter((server) => {
      const text = [
        String(server.id),
        server.name,
        server.ip,
        server.game_port,
        server.restapi_port,
      ]
        .join(" ")
        .toLowerCase();
      return text.includes(keyword);
    });
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
      badge.textContent = "测试中";
      return badge;
    }

    if (result.status === "success") {
      badge.classList.add("success");
      badge.textContent = "连通成功";
      if (result.message) {
        badge.title = result.message;
      }
      return badge;
    }

    badge.classList.add("danger");
    badge.textContent = "连通失败";
    if (result.message) {
      badge.title = result.message;
    }
    return badge;
  };

  const renderTable = () => {
    tableBodyNode.innerHTML = "";
    loadingNode.classList.add("hidden");

    const filteredServers = getFilteredServers().sort((a, b) => a.id - b.id);

    if (!serverStates.length) {
      emptyNode.textContent = "暂无服务器配置。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    if (!filteredServers.length) {
      emptyNode.textContent = "没有匹配的服务器。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const server of filteredServers) {
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
        testButton.textContent = "测试中";
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
  };

  const loadServers = async () => {
    setStatus("");
    loadingNode.classList.remove("hidden");
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");

    try {
      const response = await fetch("/webui/api/servers", {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      const payload = await parseJsonSafe(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload, `加载失败（HTTP ${response.status}）`));
      }
      if (!payload || payload.ok !== true || !Array.isArray(payload.servers)) {
        throw new Error("加载失败，返回数据格式错误");
      }

      serverStates = payload.servers.map(normalizeServer);
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
      setStatus(`已加载 ${serverStates.length} 台服务器`, "success");
      renderTable();
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      loadingNode.classList.add("hidden");
      emptyNode.classList.remove("hidden");
      emptyNode.textContent = "加载失败，请点击刷新重试。";
      tableWrapNode.classList.add("hidden");
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
      modalTitleNode.textContent = `编辑服务器 #${server.id}`;
      modalSaveButton.textContent = "保存修改";
      nameInput.value = server.name;
      ipInput.value = server.ip;
      gamePortInput.value = server.game_port;
      restapiPortInput.value = server.restapi_port;
      tokenInput.value = server.token;
    } else {
      modalTitleNode.textContent = "新增服务器";
      modalSaveButton.textContent = "新增服务器";
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
    deleteModalTextNode.textContent = `确认删除服务器 #${server.id}（${server.name}）吗？该操作无法撤销。`;
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
    if (token.length < 6 || token.length > 128) {
      throw new Error("Token 长度必须在 6-128 之间");
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
      const isEdit = modalMode === "edit" && typeof editingServerId === "number";
      const url = isEdit
        ? `/webui/api/servers/${editingServerId}`
        : "/webui/api/servers";
      const method = isEdit ? "PUT" : "POST";

      const response = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
      const result = await parseJsonSafe(response);
      if (!response.ok || !result || result.ok !== true) {
        throw new Error(readErrorMessage(result, "保存失败"));
      }

      setStatus(isEdit ? "服务器已更新" : "服务器已新增", "success");
      closeModal(true);
      await loadServers();
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存失败";
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
      const response = await fetch(`/webui/api/servers/${targetServer.id}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      const result = await parseJsonSafe(response);
      if (!response.ok || !result || result.ok !== true) {
        throw new Error(readErrorMessage(result, "删除失败"));
      }

      visibleTokenIds.delete(targetServer.id);
      testResultMap.delete(targetServer.id);
      closeDeleteModal(true);
      setStatus("删除成功", "success");
      await loadServers();
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
    testResultMap.set(serverId, { status: "loading", message: "测试中" });
    renderTable();
    setStatus(`正在测试服务器 #${serverId} 连通性...`, "warning");

    try {
      const response = await fetch(`/webui/api/servers/${serverId}/test`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const result = await parseJsonSafe(response);
      if (!response.ok || !result || result.ok !== true || !result.data) {
        throw new Error(readErrorMessage(result, "测试失败"));
      }

      const reachable = Boolean(result.data.reachable);
      const message = String(result.data.message || "");
      testResultMap.set(serverId, {
        status: reachable ? "success" : "failed",
        message,
      });
      setStatus(message, reachable ? "success" : "error");
      renderTable();
    } catch (error) {
      const message = error instanceof Error ? error.message : "测试失败";
      testResultMap.set(serverId, {
        status: "failed",
        message,
      });
      setStatus(message, "error");
      renderTable();
    }
  };

  reloadButton?.addEventListener("click", () => {
    void loadServers();
  });

  addServerButton?.addEventListener("click", () => {
    openModal("create");
  });

  searchInput?.addEventListener("input", () => {
    renderTable();
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
