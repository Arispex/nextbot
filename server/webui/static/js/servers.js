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

  const pluginConfigModalNode = document.getElementById("plugin-config-modal");
  const pluginConfigModalTitleNode = document.getElementById("plugin-config-modal-title");
  const pluginConfigModalAlertNode = document.getElementById("plugin-config-modal-alert");
  const pluginConfigModalAlertMessageNode = document.getElementById("plugin-config-modal-alert-message");
  const pluginConfigModalBodyNode = document.getElementById("plugin-config-modal-body");
  const pluginConfigModalCloseButton = document.getElementById("plugin-config-modal-close-btn");
  const pluginConfigModalCancelButton = document.getElementById("plugin-config-modal-cancel-btn");
  const pluginConfigModalSaveButton = document.getElementById("plugin-config-modal-save-btn");

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
      pluginConfigModalNode &&
      pluginConfigModalTitleNode &&
      pluginConfigModalAlertNode &&
      pluginConfigModalAlertMessageNode &&
      pluginConfigModalBodyNode &&
      pluginConfigModalCloseButton &&
      pluginConfigModalCancelButton &&
      pluginConfigModalSaveButton &&
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

  const PLUGIN_CONFIG_SCHEMA = [
    {
      section: "NextBot 服务",
      items: [
        { path: "nextbot.baseUrl", label: "NextBot 服务地址", type: "text", placeholder: "https://example.com" },
        { path: "nextbot.token", label: "NextBot Token", type: "password" },
      ],
    },
    {
      section: "服务器标识",
      items: [
        { path: "serverName", label: "服务器名称", type: "text", placeholder: "我的服务器" },
      ],
    },
    {
      section: "白名单",
      items: [
        { path: "whitelist.enabled", label: "启用白名单", type: "bool" },
        { path: "whitelist.caseSensitive", label: "区分大小写", type: "bool" },
        { path: "whitelist.denyMessage", label: "白名单拒绝提示", type: "text" },
      ],
    },
    {
      section: "登入确认",
      items: [
        { path: "loginConfirmation.enabled", label: "启用登入确认", type: "bool" },
        { path: "loginConfirmation.autoLogin", label: "自动登入", type: "bool" },
        { path: "loginConfirmation.detectUuid", label: "检测 UUID 变化", type: "bool" },
        { path: "loginConfirmation.detectIp", label: "检测 IP 变化", type: "bool" },
        { path: "loginConfirmation.emptyUuidMessage", label: "UUID 为空提示", type: "text" },
        { path: "loginConfirmation.changeDetectedMessage", label: "信息变化提示", type: "text" },
        { path: "loginConfirmation.deviceMismatchMessage", label: "设备不匹配提示", type: "text" },
        { path: "loginConfirmation.pendingExistsMessage", label: "待确认重复提示", type: "text" },
      ],
    },
    {
      section: "启动同步",
      items: [
        { path: "sync.whitelist", label: "同步白名单", type: "bool" },
        { path: "sync.blacklist", label: "同步黑名单", type: "bool" },
      ],
    },
    {
      section: "玩家事件推送",
      items: [
        { path: "playerEvents.enabled", label: "启用玩家事件推送（总开关）", type: "bool" },
        { path: "playerEvents.online", label: "推送上线事件", type: "bool" },
        { path: "playerEvents.offline", label: "推送下线事件", type: "bool" },
        { path: "playerEvents.message", label: "同步聊天消息", type: "bool" },
        { path: "playerEvents.bossSummon", label: "推送 Boss 召唤事件", type: "bool" },
      ],
    },
  ];

  let pluginConfigServerId = null;
  let pluginConfigServerName = "";
  let pluginConfigOriginal = {};
  let pluginConfigInputs = new Map();
  let pluginConfigLoading = false;
  let pluginConfigSaving = false;
  let pluginConfigVerifying = false;
  let pluginConfigVerifyButton = null;

  let serverStates = [];
  let modalMode = "create";
  let editingServerId = null;
  let modalTokenVisible = false;
  let modalSaving = false;
  let deletingServer = null;
  let deleteSaving = false;
  let currentPage = 1;
  let currentPerPage = Number(perPageSelect.value || 10);
  let currentMeta = { total: 0, page: 1, per_page: currentPerPage, total_pages: 0 };

  // H-1：visibleTokenIds 从 Set 升级为 Map<server_id, fullToken>
  // - 表格默认仅展示 mask token；点眼睛图标按需调用 GET /token 端点临时取明文
  // - 10s 后自动隐藏，避免 DOM 长期持有明文
  // - renderTable 时若 Map 内有 server_id 则展示对应明文，否则展示 mask
  const visibleTokenIds = new Map();
  const revealTimers = new Map();
  const REVEAL_TIMEOUT_MS = 10_000;
  const testResultMap = new Map();

  // B-1 / B-2：搜索 + 翻页 debounce / abort 状态
  let searchDebounceTimer = null;
  let searchAbortController = null;
  const SEARCH_DEBOUNCE_MS = 300;

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
      badge.textContent = "测试中…";
      return badge;
    }

    if (result.status === "success") {
      badge.classList.add("success");
      badge.textContent = "成功";
      if (result.reason) {
        badge.title = result.reason;
      }
      return badge;
    }

    badge.classList.add("danger");
    badge.textContent = "失败";
    if (result.reason) {
      badge.title = result.reason;
    }
    return badge;
  };

  // H-1：按需调用 reveal-token 端点临时获取明文，10s 后自动隐藏
  const clearRevealTimer = (serverId) => {
    const timerId = revealTimers.get(serverId);
    if (timerId) {
      clearTimeout(timerId);
      revealTimers.delete(serverId);
    }
  };

  const hideRevealedToken = (serverId) => {
    visibleTokenIds.delete(serverId);
    clearRevealTimer(serverId);
    renderTable();
  };

  const toggleRevealToken = async (serverId) => {
    if (visibleTokenIds.has(serverId)) {
      hideRevealedToken(serverId);
      return;
    }
    try {
      const payload = await api.apiRequest(`/webui/api/servers/${serverId}/token`, {
        method: "GET",
        headers: { Accept: "application/json" },
        action: "显示",
        expectedStatus: 200,
      });
      const data = api.unwrapData(payload);
      const fullToken = String(data?.token || "");
      if (!fullToken) {
        setStatus("显示失败，未返回 Token", "error");
        return;
      }
      visibleTokenIds.set(serverId, fullToken);
      renderTable();
      const timerId = setTimeout(() => hideRevealedToken(serverId), REVEAL_TIMEOUT_MS);
      revealTimers.set(serverId, timerId);
    } catch (error) {
      const message = error instanceof Error ? error.message : "显示失败";
      setStatus(message, "error");
    }
  };

  const renderTable = () => {
    tableBodyNode.innerHTML = "";
    loadingNode.classList.add("hidden");

    if (!serverStates.length) {
      // H-4：空态文案统一去尾句号，并与 HTML 初始态保持一致
      emptyNode.textContent = currentMeta.total > 0 ? "当前页暂无数据" : "暂无服务器配置";
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
      // H-1：默认仅展示 server.token（来自后端的 mask 串如 "****abcd"）；
      // 仅在 visibleTokenIds Map 内有该 server_id 时展示完整明文。
      const fullToken = visibleTokenIds.get(server.id);
      const tokenVisible = typeof fullToken === "string" && fullToken.length > 0;
      tokenText.textContent = tokenVisible ? fullToken : (server.token || formatMaskedToken(""));
      tokenText.title = tokenVisible ? fullToken : "点击眼睛图标查看完整 Token";

      const tokenToggleButton = document.createElement("button");
      tokenToggleButton.type = "button";
      tokenToggleButton.className = "btn token-toggle-btn";
      setTokenButtonIcon(tokenToggleButton, tokenVisible);
      tokenToggleButton.addEventListener("click", () => {
        void toggleRevealToken(server.id);
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
        testButton.textContent = "测试中…";
      }
      testButton.addEventListener("click", () => {
        void testServerConnectivity(server.id);
      });

      const pluginConfigButton = document.createElement("button");
      pluginConfigButton.type = "button";
      pluginConfigButton.className = "btn action-btn";
      pluginConfigButton.textContent = "配置";
      pluginConfigButton.addEventListener("click", () => {
        void openPluginConfigModal(server);
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
      actions.appendChild(pluginConfigButton);
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

  const loadServers = async ({ clearStatus = true, signal } = {}) => {
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
          signal,  // B-1 / B-2：透传 abort signal
        }
      );
      const servers = api.unwrapData(payload);
      const meta = api.unwrapMeta(payload);
      if (!Array.isArray(servers)) {
        // C-2-E：开发者视角文案 → 用户视角
        throw new Error("加载失败，响应数据格式异常");
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
      for (const key of [...visibleTokenIds.keys()]) {
        if (!validIds.has(key)) {
          // H-1：清理已不在当前页的 reveal 状态 + 定时器（这里不重渲，下一行 renderTable 统一渲）
          visibleTokenIds.delete(key);
          clearRevealTimer(key);
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
      // B-1：abort 错误是用户主动操作（搜索 / 翻页变更），不报错
      // api.js 把底层 AbortError 包成 ApiRequestError，故通过 signal.aborted 判断
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
      // C-2-D：不再把错误文案塞 emptyNode；保留默认空态，由 status bar 显示完整原因
      emptyNode.textContent = "暂无服务器配置";
      tableWrapNode.classList.add("hidden");
      paginationNode.classList.add("hidden");
      return false;
    }
  };

  // B-1 / B-2：搜索 / 翻页前取消 pending debounce + 上一次 fetch，避免 race
  const cancelPendingLoad = () => {
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = null;
    }
    if (searchAbortController) {
      searchAbortController.abort();
      searchAbortController = null;
    }
  };

  const loadServersWithAbort = (options = {}) => {
    cancelPendingLoad();
    searchAbortController = new AbortController();
    return loadServers({ ...options, signal: searchAbortController.signal });
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
      modalSaveButton.textContent = "保存";
      nameInput.value = server.name;
      ipInput.value = server.ip;
      gamePortInput.value = server.game_port;
      restapiPortInput.value = server.restapi_port;
      // H-1：editor 不回填完整 token，input 留空 + placeholder 提示
      // buildPayloadFromModal 在 edit 模式遇到空串 token 时返回空，后端 update 路由
      // 检测到 mask / 空串后会跳过 token 赋值（保留原值）
      tokenInput.value = "";
      tokenInput.placeholder = "留空表示保留原 Token";
    } else {
      modalTitleNode.textContent = "创建服务器";
      modalSaveButton.textContent = "创建";
      nameInput.value = "";
      ipInput.value = "";
      gamePortInput.value = "";
      restapiPortInput.value = "";
      tokenInput.value = "";
      tokenInput.placeholder = "请输入 Token";
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
    deleteModalTextNode.textContent = `确定删除服务器「${server.name}」吗？此操作不可恢复。`;
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

  const buildPayloadFromModal = (isEditMode = false) => {
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
    // H-1：edit 模式允许空 token（= 保留原值）；create 模式仍要求非空
    if (!isEditMode && !token) {
      throw new Error("Token 不能为空");
    }
    if (token && (token.length < 1 || token.length > 128)) {
      throw new Error("Token 长度必须在 1-128 之间");
    }

    return {
      name,
      ip,
      game_port: gamePort,
      restapi_port: restapiPort,
      token,  // edit 模式可能为空串；后端识别后跳过 token 赋值
    };
  };

  const saveServer = async () => {
    if (modalSaving) {
      return;
    }

    const isEdit = modalMode === "edit" && typeof editingServerId === "number";

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
    // H-3：ASCII ... → 中文 …
    setModalAlert("正在保存…", "info");

    try {
      const url = isEdit ? `/webui/api/servers/${editingServerId}` : "/webui/api/servers";
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

      closeModal(true);
      currentPage = 1;
      // B-1：reload 取消 pending search debounce，避免 race
      const reloaded = await loadServersWithAbort({ clearStatus: false });
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
    // H-3：去对象名 + 全角省略号
    setDeleteModalAlert("正在删除…", "warning");
    setStatus("正在删除…", "warning");

    try {
      await api.apiRequest(`/webui/api/servers/${targetServer.id}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        action: "删除",
        expectedStatus: 204,
      });

      // H-1：清理已删除 server 的 reveal 状态 + 定时器
      visibleTokenIds.delete(targetServer.id);
      clearRevealTimer(targetServer.id);
      testResultMap.delete(targetServer.id);
      closeDeleteModal(true);
      // B-1：reload 取消 pending search debounce，避免 race
      const reloaded = await loadServersWithAbort({ clearStatus: false });
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

  const setPluginConfigModalAlert = (message = "", type = "info") => {
    const text = String(message || "").trim();
    if (!text) {
      pluginConfigModalAlertNode.className = "alert hidden modal-alert";
      pluginConfigModalAlertMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    pluginConfigModalAlertNode.className = `alert ${normalizedType} modal-alert`;
    pluginConfigModalAlertMessageNode.textContent = text;
  };

  const getByPath = (obj, path) => {
    if (!obj || typeof obj !== "object") {
      return undefined;
    }
    const parts = String(path).split(".");
    let current = obj;
    for (const part of parts) {
      if (current === null || current === undefined || typeof current !== "object") {
        return undefined;
      }
      current = current[part];
    }
    return current;
  };

  const renderPluginConfigForm = (config) => {
    pluginConfigModalBodyNode.replaceChildren();
    pluginConfigInputs = new Map();

    for (const section of PLUGIN_CONFIG_SCHEMA) {
      const visibleItems = section.items.filter(
        (item) => getByPath(config, item.path) !== undefined,
      );
      if (!visibleItems.length) {
        continue;
      }

      const group = document.createElement("div");
      group.className = "form-section";

      const heading = document.createElement("h4");
      heading.className = "form-section-title";
      heading.textContent = section.section;
      group.appendChild(heading);

      for (const item of visibleItems) {
        const value = getByPath(config, item.path);
        const formItem = document.createElement("label");
        formItem.className = item.type === "bool" ? "form-item form-item-bool" : "form-item";

        const labelText = document.createElement("span");
        labelText.className = "form-label";
        labelText.textContent = item.label;
        formItem.appendChild(labelText);

        let input;
        if (item.type === "bool") {
          input = document.createElement("input");
          input.type = "checkbox";
          input.className = "form-checkbox";
          input.checked = Boolean(value);
        } else {
          input = document.createElement("input");
          input.className = "input";
          input.type = item.type === "password" ? "password" : "text";
          // H-1 / F-A-4：password 字段不回填明文，仅提示用户「留空表示保留原值」
          if (item.type === "password") {
            input.value = "";
            input.placeholder = "留空表示保留原值";
          } else {
            if (item.placeholder) {
              input.placeholder = item.placeholder;
            }
            input.value = String(value ?? "");
          }
        }

        formItem.appendChild(input);
        group.appendChild(formItem);
        pluginConfigInputs.set(item.path, { input, type: item.type });
      }

      if (section.section === "NextBot 服务") {
        const actions = document.createElement("div");
        actions.className = "form-section-actions";

        const verifyButton = document.createElement("button");
        verifyButton.type = "button";
        verifyButton.className = "btn";
        verifyButton.textContent = "验证连通性";
        verifyButton.addEventListener("click", () => {
          void verifyNextBotConnection(verifyButton);
        });
        pluginConfigVerifyButton = verifyButton;

        const hint = document.createElement("span");
        hint.className = "form-section-hint";
        hint.textContent = "验证前会自动保存地址和 Token 改动";

        actions.appendChild(verifyButton);
        actions.appendChild(hint);
        group.appendChild(actions);
      }

      pluginConfigModalBodyNode.appendChild(group);
    }

    if (!pluginConfigInputs.size) {
      const emptyNote = document.createElement("p");
      emptyNote.className = "confirm-modal-text";
      // C-2-M：晦涩的"未返回可编辑的配置字段" → 用户视角
      emptyNote.textContent = "当前没有可编辑的配置";
      pluginConfigModalBodyNode.appendChild(emptyNote);
    }
  };

  const openPluginConfigModal = async (server) => {
    pluginConfigServerId = server.id;
    pluginConfigServerName = server.name;
    pluginConfigOriginal = {};
    pluginConfigInputs = new Map();
    pluginConfigLoading = true;
    pluginConfigSaving = false;
    pluginConfigModalSaveButton.disabled = true;
    pluginConfigModalTitleNode.textContent = "编辑插件配置";
    // C-2-K：ASCII ... → 中文 …；同时改 createElement + textContent，与同模块风格一致
    pluginConfigModalBodyNode.replaceChildren();
    const loadingPlaceholder = document.createElement("p");
    loadingPlaceholder.className = "confirm-modal-text";
    loadingPlaceholder.textContent = "加载中…";
    pluginConfigModalBodyNode.appendChild(loadingPlaceholder);
    setPluginConfigModalAlert("");
    pluginConfigModalNode.classList.remove("hidden");

    try {
      const payload = await api.apiRequest(
        `/webui/api/servers/${server.id}/plugin-config`,
        {
          method: "GET",
          headers: { Accept: "application/json" },
          action: "读取",
          expectedStatus: 200,
        },
      );
      const config = api.unwrapData(payload);
      if (!config || typeof config !== "object") {
        // C-2-E：开发者视角 → 用户视角
        throw new Error("读取失败，响应数据格式异常");
      }
      pluginConfigOriginal = config;
      renderPluginConfigForm(config);
      pluginConfigModalSaveButton.disabled = false;
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取失败";
      pluginConfigModalBodyNode.replaceChildren();
      setPluginConfigModalAlert(message, "error");
    } finally {
      pluginConfigLoading = false;
    }
  };

  const closePluginConfigModal = (force = false) => {
    if (pluginConfigSaving && !force) {
      return;
    }
    pluginConfigModalNode.classList.add("hidden");
    // H-1：清空 password input 残留值，避免明文 token 滞留 DOM
    pluginConfigModalBodyNode.querySelectorAll('input[type="password"]').forEach((inp) => {
      inp.value = "";
    });
    pluginConfigServerId = null;
    pluginConfigServerName = "";
    pluginConfigOriginal = {};
    pluginConfigInputs = new Map();
    pluginConfigVerifyButton = null;
    pluginConfigVerifying = false;
  };

  const collectPluginConfigDiff = () => {
    const diff = {};
    for (const [path, { input, type }] of pluginConfigInputs.entries()) {
      const original = getByPath(pluginConfigOriginal, path);
      let current;
      if (type === "bool") {
        current = Boolean(input.checked);
      } else {
        current = String(input.value ?? "");
      }
      if (type === "bool") {
        if (Boolean(original) !== current) {
          diff[path] = current;
        }
      } else if (type === "password") {
        // H-1 / F-A-4：password 字段空串 = 保留原值，不进入 diff
        if (current && current !== String(original ?? "")) {
          diff[path] = current;
        }
      } else {
        const originalText = original === undefined || original === null ? "" : String(original);
        if (originalText !== current) {
          diff[path] = current;
        }
      }
    }
    return diff;
  };

  const savePluginConfig = async () => {
    if (pluginConfigSaving || pluginConfigLoading || pluginConfigServerId === null) {
      return;
    }
    if (!pluginConfigInputs.size) {
      // C-2-L：业务术语 → 用户视角
      setPluginConfigModalAlert("没有可保存的修改", "warning");
      return;
    }

    const diff = collectPluginConfigDiff();
    if (!Object.keys(diff).length) {
      // C-2-L：业务术语 → 用户视角
      setPluginConfigModalAlert("没有可保存的修改", "info");
      return;
    }

    pluginConfigSaving = true;
    pluginConfigModalSaveButton.disabled = true;
    // H-3：ASCII ... → 中文 …
    setPluginConfigModalAlert("正在保存…", "info");

    try {
      await api.apiRequest(
        `/webui/api/servers/${pluginConfigServerId}/plugin-config`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify(diff),
          action: "保存",
          expectedStatus: 200,
        },
      );
      closePluginConfigModal(true);
      setStatus("保存成功", "success");
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存失败";
      setPluginConfigModalAlert(message, "error");
    } finally {
      pluginConfigSaving = false;
      pluginConfigModalSaveButton.disabled = false;
    }
  };

  const setByPath = (obj, path, value) => {
    const parts = String(path).split(".");
    let current = obj;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const key = parts[i];
      if (current[key] === null || current[key] === undefined || typeof current[key] !== "object") {
        current[key] = {};
      }
      current = current[key];
    }
    current[parts[parts.length - 1]] = value;
  };

  const verifyNextBotConnection = async (button) => {
    if (
      pluginConfigVerifying
      || pluginConfigSaving
      || pluginConfigLoading
      || pluginConfigServerId === null
    ) {
      return;
    }

    const watchedPaths = ["nextbot.baseUrl", "nextbot.token"];
    const diff = {};
    for (const path of watchedPaths) {
      const entry = pluginConfigInputs.get(path);
      if (!entry) {
        continue;
      }
      const current = String(entry.input.value ?? "");
      const original = getByPath(pluginConfigOriginal, path);
      const originalText = original === undefined || original === null ? "" : String(original);
      // H-1：password 字段空串 = 保留原值，不进 diff
      if (entry.type === "password") {
        if (current && current !== originalText) {
          diff[path] = current;
        }
      } else if (originalText !== current) {
        diff[path] = current;
      }
    }

    pluginConfigVerifying = true;
    const originalLabel = button.textContent;
    button.disabled = true;
    // H-3：与其它进行时文案保持 ellipsis 风格一致
    button.textContent = "正在验证…";
    pluginConfigModalSaveButton.disabled = true;
    setPluginConfigModalAlert("");

    try {
      if (Object.keys(diff).length) {
        // H-3：去对象名（"地址和 Token" 是当前对象）+ 全角省略号
        setPluginConfigModalAlert("正在保存…", "info");
        await api.apiRequest(
          `/webui/api/servers/${pluginConfigServerId}/plugin-config`,
          {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              Accept: "application/json",
            },
            body: JSON.stringify(diff),
            action: "保存",
            expectedStatus: 200,
          },
        );
        for (const [path, value] of Object.entries(diff)) {
          setByPath(pluginConfigOriginal, path, value);
        }
      }

      // H-3：简化 + 全角省略号
      setPluginConfigModalAlert("正在验证…", "info");
      const payload = await api.apiRequest(
        `/webui/api/servers/${pluginConfigServerId}/plugin-config/verify-nextbot`,
        {
          method: "POST",
          headers: { Accept: "application/json" },
          action: "验证",
          expectedStatus: 200,
          // B-7：verify-nextbot 包含外部 HTTP 链路，默认 15s 偏紧 → 30s
          timeoutMs: 30_000,
        },
      );
      const data = api.unwrapData(payload) || {};
      const probeStatus = String(data.probeStatus || "");
      const message = String(data.message || "").trim();
      const httpStatus = data.httpStatus;
      const suffix = Number.isInteger(httpStatus) ? `（HTTP ${httpStatus}）` : "";
      // H-2：不再把后端 enum probeStatus 直接拼到用户文案；改为「动作 + 结果」格式
      const verbResult = probeStatus === "Ok"
        ? "验证成功"
        : probeStatus === "Skipped"
          ? "验证已跳过"
          : "验证失败";
      const tone = probeStatus === "Ok"
        ? "success"
        : probeStatus === "Skipped"
          ? "info"
          : "error";
      const detail = message ? `，${message}${suffix}` : suffix;
      setPluginConfigModalAlert(`${verbResult}${detail}`, tone);
    } catch (error) {
      const message = error instanceof Error ? error.message : "验证失败";
      setPluginConfigModalAlert(message, "error");
    } finally {
      pluginConfigVerifying = false;
      button.disabled = false;
      button.textContent = originalLabel;
      pluginConfigModalSaveButton.disabled = false;
    }
  };

  const testServerConnectivity = async (serverId) => {
    testResultMap.set(serverId, { status: "loading", reason: "正在测试" });
    renderTable();
    // H-3：去对象名 + 全角省略号
    setStatus("正在测试…", "warning");

    try {
      const payload = await api.apiRequest(`/webui/api/servers/${serverId}/test`, {
        method: "POST",
        headers: { Accept: "application/json" },
        action: "测试",
        expectedStatus: 200,
        // B-7：test 端点后端默认 10s（B-4 已调），前端给 30s cap 留余量
        timeoutMs: 30_000,
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
    // B-1：reload 取消 pending debounce / fetch，确保拿到最新结果
    currentPage = 1;
    void loadServersWithAbort();
  });

  addServerButton?.addEventListener("click", () => {
    openModal("create");
  });

  searchInput?.addEventListener("input", () => {
    // B-1：搜索 debounce 300ms + AbortController（commands R1 prior art）
    cancelPendingLoad();
    searchDebounceTimer = setTimeout(() => {
      searchDebounceTimer = null;
      currentPage = 1;
      void loadServersWithAbort();
    }, SEARCH_DEBOUNCE_MS);
  });

  perPageSelect.addEventListener("change", () => {
    // B-2：翻页 / per-page 同样取消 pending，防 race
    currentPerPage = Number(perPageSelect.value || 10);
    currentPage = 1;
    void loadServersWithAbort();
  });

  prevPageButton.addEventListener("click", () => {
    if (currentPage <= 1) {
      return;
    }
    currentPage -= 1;
    void loadServersWithAbort({ clearStatus: false });
  });

  nextPageButton.addEventListener("click", () => {
    if (currentMeta.total_pages > 0 && currentPage >= currentMeta.total_pages) {
      return;
    }
    currentPage += 1;
    void loadServersWithAbort({ clearStatus: false });
  });

  // commands R2 B-7 prior art：不能直接绑定 closeModal，否则 MouseEvent 会作为
  // force 参数传入（!MouseEvent === false），绕过 modalSaving guard
  modalCloseButton.addEventListener("click", () => closeModal());
  modalCancelButton.addEventListener("click", () => closeModal());
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

  pluginConfigModalCloseButton.addEventListener("click", () => {
    closePluginConfigModal();
  });
  pluginConfigModalCancelButton.addEventListener("click", () => {
    closePluginConfigModal();
  });
  pluginConfigModalSaveButton.addEventListener("click", () => {
    void savePluginConfig();
  });
  pluginConfigModalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.pluginConfigModalClose === "1") {
      closePluginConfigModal();
    }
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

  // 初始加载也走 abort 通道，确保后续操作能取消未完成请求
  void loadServersWithAbort();
})();
