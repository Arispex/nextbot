(() => {
  const reloadButton = document.getElementById("reload-btn");
  const searchInput = document.getElementById("command-search");

  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const loadingNode = document.getElementById("loading");
  const emptyNode = document.getElementById("empty");
  const tableWrapNode = document.getElementById("table-wrap");
  const tableBodyNode = document.getElementById("command-table-body");
  const paginationNode = document.getElementById("command-pagination");
  const paginationInfoNode = document.getElementById("command-pagination-info");
  const perPageSelect = document.getElementById("command-per-page");
  const prevPageButton = document.getElementById("command-prev-btn");
  const nextPageButton = document.getElementById("command-next-btn");

  const modalNode = document.getElementById("param-modal");
  const modalBodyNode = document.getElementById("param-modal-body");
  const modalTitleNode = document.getElementById("param-modal-title");
  const modalAlertNode = document.getElementById("param-modal-alert");
  const modalAlertMessageNode = document.getElementById("param-modal-alert-message");
  const modalCloseButton = document.getElementById("modal-close-btn");
  const modalCancelButton = document.getElementById("modal-cancel-btn");
  const modalSaveButton = document.getElementById("modal-save-btn");

  const aliasModalNode = document.getElementById("alias-modal");
  const aliasModalTitleNode = document.getElementById("alias-modal-title");
  const aliasModalAlertNode = document.getElementById("alias-modal-alert");
  const aliasModalAlertMessageNode = document.getElementById("alias-modal-alert-message");
  const aliasInput = document.getElementById("alias-input");
  const aliasCloseButton = document.getElementById("alias-modal-close-btn");
  const aliasCancelButton = document.getElementById("alias-cancel-btn");
  const aliasSaveButton = document.getElementById("alias-save-btn");
  const restartButton = document.getElementById("restart-btn");

  let commandStates = [];
  let activeModalCommandKey = "";
  let activeAliasCommandKey = "";
  let modalSaving = false;
  let aliasSaving = false;
  let currentPage = 1;
  let currentPerPage = Number(perPageSelect?.value || 10);
  let currentMeta = { total: 0, page: 1, per_page: currentPerPage, total_pages: 0 };

  // P1-Race: search input debounce + AbortController 状态。
  let searchDebounceTimer = null;
  let searchAbortController = null;

  // P2-A: modal focus 管理。记录每个 modal 打开前的 activeElement，关闭时恢复。
  const modalPreviousFocus = new WeakMap();
  // P2-A: focus trap keydown 监听 handler，按 modal 缓存以便正确 removeEventListener。
  const modalTrapHandlers = new WeakMap();

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
      modalBodyNode &&
      modalTitleNode &&
      modalAlertNode &&
      modalAlertMessageNode &&
      modalCloseButton &&
      modalCancelButton &&
      modalSaveButton
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;
  const apiReady = Boolean(
    api &&
      typeof api.apiRequest === "function" &&
      typeof api.unwrapData === "function" &&
      typeof api.unwrapMeta === "function"
  );

  const setStatus = (message, type = "") => {
    const text = String(message || "").trim();
    if (!text) {
      statusMessageNode.textContent = "";
      statusNode.className = "alert hidden";
      return;
    }

    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";

    statusMessageNode.textContent = text;
    statusNode.className = `alert ${normalizedType}`;
  };

  const setModalAlert = (message = "", type = "info") => {
    const text = String(message || "").trim();
    if (!text) {
      modalAlertNode.className = "alert info modal-alert hidden";
      modalAlertMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    modalAlertMessageNode.textContent = text;
    modalAlertNode.className = `alert ${normalizedType} modal-alert`;
  };

  const cloneValue = (value) => JSON.parse(JSON.stringify(value));

  // P2-A: 收集 modal 内可聚焦元素，跳过 disabled / tabindex="-1"。
  const getFocusableInModal = (modalNode) => {
    if (!modalNode) return [];
    return Array.from(
      modalNode.querySelectorAll(
        'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    ).filter((el) => !el.classList.contains("hidden"));
  };

  // P2-A: 构造 modal Tab 循环 handler，绑定到 keydown。
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

  // P2-A: 打开 modal 时记录上一个焦点、自动聚焦首个可交互元素、安装 focus trap。
  const openModalWithFocus = (modalNode) => {
    if (!modalNode) return;
    modalPreviousFocus.set(modalNode, document.activeElement);
    modalNode.classList.remove("hidden");
    const handler = buildTrapFocusHandler(modalNode);
    modalTrapHandlers.set(modalNode, handler);
    modalNode.addEventListener("keydown", handler);
    // 使用 setTimeout 让浏览器先完成布局，再聚焦首个交互元素。
    setTimeout(() => {
      const focusables = getFocusableInModal(modalNode);
      // 跳过 close button (✕) 直接聚焦表单首元素，更符合用户预期。
      const preferred = focusables.find(
        (el) => !el.classList.contains("modal-close-btn")
      ) || focusables[0];
      if (preferred && typeof preferred.focus === "function") {
        preferred.focus();
      }
    }, 0);
  };

  // P2-A: 关闭 modal 时卸载 trap、把焦点返还给打开前的元素。
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
    if (previousFocus && document.contains(previousFocus) && typeof previousFocus.focus === "function") {
      try {
        previousFocus.focus({ preventScroll: true });
      } catch (_error) {
        previousFocus.focus();
      }
    }
  };

  const setModalSavingState = (saving) => {
    modalSaving = Boolean(saving);
    modalSaveButton.disabled = modalSaving;
    modalCancelButton.disabled = modalSaving;
    modalCloseButton.disabled = modalSaving;
  };

  const coerceByType = (type, raw, fromInput = false) => {
    if (type === "bool") {
      if (typeof raw === "boolean") return raw;
      if (typeof raw === "string") {
        const text = raw.trim().toLowerCase();
        if (["true", "1", "yes", "on"].includes(text)) return true;
        if (["false", "0", "no", "off", ""].includes(text)) return false;
      }
      return Boolean(raw);
    }

    if (type === "int") {
      const text = String(raw ?? "").trim();
      if (!text) {
        throw new Error("需要整数");
      }
      const parsed = Number(text);
      if (!Number.isInteger(parsed)) {
        throw new Error("需要整数");
      }
      return parsed;
    }

    if (type === "float") {
      const text = String(raw ?? "").trim();
      if (!text) {
        throw new Error("需要数字");
      }
      const parsed = Number(text);
      if (!Number.isFinite(parsed)) {
        throw new Error("需要数字");
      }
      return parsed;
    }

    const text = String(raw ?? "");
    if (!fromInput) return text;
    return text;
  };

  const normalizeWithSchema = (schema, raw, fromInput = false) => {
    const type = String(schema?.type || "string");
    const value = coerceByType(type, raw, fromInput);

    if (schema?.required && type === "string" && !String(value).trim()) {
      throw new Error("不能为空");
    }

    if ((type === "int" || type === "float") && value !== null && value !== undefined) {
      if (schema?.min !== undefined && Number(value) < Number(schema.min)) {
        throw new Error(`不能小于 ${schema.min}`);
      }
      if (schema?.max !== undefined && Number(value) > Number(schema.max)) {
        throw new Error(`不能大于 ${schema.max}`);
      }
    }

    if (Array.isArray(schema?.enum) && schema.enum.length > 0) {
      let matched = false;
      for (const enumValue of schema.enum) {
        try {
          const normalizedEnum = coerceByType(type, enumValue, false);
          if (Object.is(normalizedEnum, value)) {
            matched = true;
            break;
          }
        } catch (_error) {
          // Ignore invalid enum item.
        }
      }
      if (!matched) {
        throw new Error("不在可选范围内");
      }
    }

    return value;
  };

  const ensureCommandParamValues = (command) => {
    const schema = command?.param_schema && typeof command.param_schema === "object"
      ? command.param_schema
      : {};

    const rawValues = command?.param_values && typeof command.param_values === "object"
      ? command.param_values
      : {};

    const normalized = {};
    for (const paramName of Object.keys(schema)) {
      const definition = schema[paramName] || {};
      const fallback = definition.default;
      const rawValue = Object.prototype.hasOwnProperty.call(rawValues, paramName)
        ? rawValues[paramName]
        : fallback;

      try {
        normalized[paramName] = normalizeWithSchema(definition, rawValue, false);
      } catch (_error) {
        try {
          normalized[paramName] = normalizeWithSchema(definition, fallback, false);
        } catch (_error2) {
          normalized[paramName] = fallback;
        }
      }
    }

    command.param_values = normalized;
  };

  const getCommandByKey = (commandKey) => {
    return commandStates.find((item) => item.command_key === commandKey) || null;
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
    const end = Math.min(total, start + Math.max(commandStates.length - 1, 0));
    paginationInfoNode.textContent = `第 ${page} / ${Math.max(totalPages, 1)} 页，共 ${total} 条，当前显示 ${start}-${end}`;
    prevPageButton.disabled = page <= 1;
    nextPageButton.disabled = totalPages <= 0 || page >= totalPages;
  };

  const buildPermissionNode = (permission) => {
    const badge = document.createElement("span");
    badge.className = "badge";
    if (!permission) {
      badge.classList.add("none");
      badge.textContent = "无";
      return badge;
    }
    badge.textContent = permission;
    return badge;
  };

  // P2-Loading: 同步 aria-busy 状态，跟随 hidden class 切换，对齐 dashboard R1+R2 规范。
  const setLoadingVisible = (visible) => {
    if (visible) {
      loadingNode.classList.remove("hidden");
      loadingNode.setAttribute("aria-busy", "true");
    } else {
      loadingNode.classList.add("hidden");
      loadingNode.setAttribute("aria-busy", "false");
    }
  };

  const renderTable = () => {
    tableBodyNode.replaceChildren();
    setLoadingVisible(false);

    if (!commandStates.length) {
      emptyNode.textContent = currentMeta.total > 0 ? "当前页暂无数据" : "暂无可配置命令";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      updatePagination();
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const command of commandStates) {
      const row = document.createElement("tr");
      row.dataset.commandKey = command.command_key;

      const commandCell = document.createElement("td");
      const commandMain = document.createElement("div");
      commandMain.className = "command-main";

      const nameNode = document.createElement("p");
      nameNode.className = "command-name";
      nameNode.textContent = command.display_name || command.command_key;

      commandMain.appendChild(nameNode);
      commandCell.appendChild(commandMain);

      const descriptionCell = document.createElement("td");
      descriptionCell.className = "description-cell";
      const descriptionNode = document.createElement("div");
      descriptionNode.className = "command-desc";
      descriptionNode.textContent = command.description || "暂无介绍";
      descriptionCell.appendChild(descriptionNode);

      const usageCell = document.createElement("td");
      usageCell.className = "usage-cell";
      const usageNode = document.createElement("div");
      usageNode.className = "command-desc";
      usageNode.textContent = command.usage || "未填写用法";
      usageCell.appendChild(usageNode);

      const permissionCell = document.createElement("td");
      permissionCell.appendChild(buildPermissionNode(command.permission));

      const statusCell = document.createElement("td");
      const switchNode = document.createElement("label");
      switchNode.className = "switch";

      const enabledInput = document.createElement("input");
      enabledInput.type = "checkbox";
      enabledInput.checked = Boolean(command.enabled);

      const switchTrack = document.createElement("span");
      switchTrack.className = "switch-track";

      const switchText = document.createElement("span");
      switchText.textContent = enabledInput.checked ? "启用" : "关闭";

      enabledInput.addEventListener("change", async () => {
        const nextEnabled = Boolean(enabledInput.checked);
        const previousEnabled = !nextEnabled;

        command.enabled = nextEnabled;
        switchText.textContent = nextEnabled ? "启用" : "关闭";
        enabledInput.disabled = true;
        setStatus("正在保存…", "info");

        try {
          const { reloaded } = await saveSingleCommand({
            commandKey: command.command_key,
            enabled: nextEnabled,
          });
          if (reloaded) {
            setStatus("保存成功", "success");
          } else {
            setStatus("保存成功，已立即生效；刷新失败，请手动刷新页面", "warning");
          }
        } catch (error) {
          command.enabled = previousEnabled;
          enabledInput.checked = previousEnabled;
          switchText.textContent = previousEnabled ? "启用" : "关闭";
          const message = error instanceof Error ? error.message : "保存失败";
          setStatus(message, "error");
        } finally {
          enabledInput.disabled = false;
        }
      });

      switchNode.appendChild(enabledInput);
      switchNode.appendChild(switchTrack);
      switchNode.appendChild(switchText);
      statusCell.appendChild(switchNode);

      const adminCell = document.createElement("td");
      const categoryText = String(command.category || "").trim() || "未分类";
      adminCell.textContent = categoryText;

      const schema = command.param_schema && typeof command.param_schema === "object"
        ? command.param_schema
        : {};
      const paramNames = Object.keys(schema);

      const aliasesCell = document.createElement("td");
      const aliasesList = Array.isArray(command.aliases) ? command.aliases : [];
      const aliasesNode = document.createElement("div");
      aliasesNode.className = "command-desc";
      aliasesNode.textContent = aliasesList.length ? aliasesList.join(", ") : "-";
      aliasesCell.appendChild(aliasesNode);

      const actionCell = document.createElement("td");
      const actionWrap = document.createElement("div");
      actionWrap.className = "action-wrap";
      if (paramNames.length) {
        const actionButton = document.createElement("button");
        actionButton.type = "button";
        actionButton.className = "btn action-btn";
        actionButton.textContent = "参数";
        actionButton.addEventListener("click", () => {
          openParamModal(command.command_key);
        });
        actionWrap.appendChild(actionButton);
      }
      const aliasButton = document.createElement("button");
      aliasButton.type = "button";
      aliasButton.className = "btn action-btn";
      aliasButton.textContent = "别名";
      aliasButton.addEventListener("click", () => {
        openAliasModal(command.command_key);
      });
      actionWrap.appendChild(aliasButton);
      actionCell.appendChild(actionWrap);

      row.appendChild(commandCell);
      row.appendChild(descriptionCell);
      row.appendChild(usageCell);
      row.appendChild(permissionCell);
      row.appendChild(aliasesCell);
      row.appendChild(statusCell);
      row.appendChild(adminCell);
      row.appendChild(actionCell);
      tableBodyNode.appendChild(row);
    }

    updatePagination();
  };

  const openParamModal = (commandKey) => {
    const command = getCommandByKey(commandKey);
    if (!command) return;

    activeModalCommandKey = commandKey;
    setModalAlert("");

    const schema = command.param_schema && typeof command.param_schema === "object"
      ? command.param_schema
      : {};
    const paramNames = Object.keys(schema);

    modalTitleNode.textContent = "编辑参数";
    modalBodyNode.replaceChildren();

    if (!paramNames.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "当前命令没有可配置参数";
      modalBodyNode.appendChild(empty);
      openModalWithFocus(modalNode);
      return;
    }

    for (const paramName of paramNames) {
      const definition = schema[paramName] || {};
      const currentValue = command.param_values?.[paramName];

      const item = document.createElement("section");
      item.className = "param-item";

      const head = document.createElement("div");
      head.className = "param-head";

      const label = document.createElement("p");
      label.className = "param-label";
      label.textContent = definition.label || paramName;
      head.appendChild(label);

      if (definition.description) {
        const desc = document.createElement("p");
        desc.className = "param-desc";
        desc.textContent = definition.description;
        head.appendChild(desc);
      }

      item.appendChild(head);

      let inputNode;
      if (definition.type === "bool") {
        const boolWrap = document.createElement("label");
        boolWrap.className = "param-bool-control";

        inputNode = document.createElement("input");
        inputNode.type = "checkbox";
        inputNode.className = "bool-input";
        inputNode.checked = Boolean(currentValue);

        const boolTrack = document.createElement("span");
        boolTrack.className = "param-bool-track";

        boolWrap.appendChild(inputNode);
        boolWrap.appendChild(boolTrack);
        item.appendChild(boolWrap);
      } else if (Array.isArray(definition.enum) && definition.enum.length) {
        inputNode = document.createElement("select");
        inputNode.className = "select";

        let selectedIndex = 0;
        for (let i = 0; i < definition.enum.length; i += 1) {
          const enumValue = definition.enum[i];
          const option = document.createElement("option");
          option.value = String(i);
          option.textContent = String(enumValue);
          inputNode.appendChild(option);

          if (Object.is(enumValue, currentValue) || String(enumValue) === String(currentValue)) {
            selectedIndex = i;
          }
        }

        inputNode.value = String(selectedIndex);
        inputNode.dataset.enumSelect = "1";
      } else {
        inputNode = document.createElement("input");
        inputNode.className = "input";

        if (definition.type === "int" || definition.type === "float") {
          inputNode.type = "number";
          inputNode.step = definition.type === "float" ? "any" : "1";
          if (definition.min !== undefined) {
            inputNode.min = String(definition.min);
          }
          if (definition.max !== undefined) {
            inputNode.max = String(definition.max);
          }
        } else {
          inputNode.type = "text";
        }

        inputNode.value = String(currentValue ?? "");
      }

      inputNode.dataset.role = "param-input";
      inputNode.dataset.paramName = paramName;
      inputNode.dataset.paramLabel = definition.label || paramName;
      inputNode.dataset.paramSchema = JSON.stringify(definition);

      if (definition.type !== "bool") {
        item.appendChild(inputNode);
      }
      modalBodyNode.appendChild(item);
    }

    openModalWithFocus(modalNode);
  };

  const closeParamModal = (force = false) => {
    if (modalSaving && !force) return;
    closeModalAndRestoreFocus(modalNode);
    modalBodyNode.replaceChildren();
    activeModalCommandKey = "";
    setModalAlert("");
  };

  const saveModalParams = async () => {
    if (!activeModalCommandKey || modalSaving) return;

    const command = getCommandByKey(activeModalCommandKey);
    if (!command) {
      closeParamModal();
      return;
    }

    const nextValues = {};
    const inputNodes = modalBodyNode.querySelectorAll("[data-role='param-input']");

    for (const inputNode of inputNodes) {
      const paramName = inputNode.dataset.paramName;
      const schemaRaw = inputNode.dataset.paramSchema;
      const paramLabel = inputNode.dataset.paramLabel || paramName || "参数";
      if (!paramName || !schemaRaw) {
        continue;
      }

      let schema;
      try {
        schema = JSON.parse(schemaRaw);
      } catch (_error) {
        setModalAlert(`${paramLabel}：参数定义无效`, "error");
        return;
      }

      let rawValue;
      if (schema.type === "bool") {
        rawValue = Boolean(inputNode.checked);
      } else if (inputNode.dataset.enumSelect === "1" && Array.isArray(schema.enum)) {
        const enumIndex = Number.parseInt(String(inputNode.value), 10);
        if (!Number.isInteger(enumIndex) || enumIndex < 0 || enumIndex >= schema.enum.length) {
          setModalAlert(`${paramLabel}：选项无效`, "error");
          return;
        }
        rawValue = schema.enum[enumIndex];
      } else {
        rawValue = inputNode.value;
      }

      try {
        nextValues[paramName] = normalizeWithSchema(schema, rawValue, true);
      } catch (error) {
        const message = error instanceof Error ? error.message : "参数格式错误";
        setModalAlert(`${paramLabel}：${message}`, "error");
        if (typeof inputNode.focus === "function") {
          inputNode.focus();
        }
        return;
      }
    }

    setModalSavingState(true);
    setModalAlert("正在保存…", "info");

    try {
      const { reloaded } = await saveSingleCommand({
        commandKey: command.command_key,
        paramValues: nextValues,
      });
      command.param_values = nextValues;
      if (reloaded) {
        setStatus("保存成功", "success");
      } else {
        setStatus("保存成功，已立即生效；刷新失败，请手动刷新页面", "warning");
      }
      closeParamModal(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存失败";
      setModalAlert(message, "error");
    } finally {
      setModalSavingState(false);
    }
  };

  const loadCommands = async ({ clearStatus = true, signal } = {}) => {
    if (!apiReady) {
      setLoadingVisible(false);
      setStatus("页面资源版本不一致，请刷新页面或重启机器人", "error");
      return false;
    }

    if (clearStatus) {
      setStatus("");
    }

    setLoadingVisible(true);
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");
    paginationNode.classList.add("hidden");

    try {
      const payload = await api.apiRequest(
        `/webui/api/commands?page=${encodeURIComponent(String(currentPage))}&per_page=${encodeURIComponent(String(currentPerPage))}&q=${encodeURIComponent(String(searchInput.value || "").trim())}`,
        {
          method: "GET",
          headers: {
            Accept: "application/json",
          },
          action: "加载",
          expectedStatus: 200,
          signal,
        }
      );
      // P1-Race: 若请求在 await 期间被 abort，直接静默返回，不渲染过期结果。
      if (signal && signal.aborted) {
        return false;
      }
      const commands = api.unwrapData(payload);
      const meta = api.unwrapMeta(payload);
      if (!Array.isArray(commands)) {
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
      commandStates = cloneValue(commands);
      for (const command of commandStates) {
        ensureCommandParamValues(command);
      }

      renderTable();
      return true;
    } catch (error) {
      // P1-Race: AbortError 是预期路径，不展示错误。
      if (signal && signal.aborted) {
        return false;
      }
      if (error && (error.name === "AbortError" || error.code === "ABORT_ERR")) {
        return false;
      }
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      setLoadingVisible(false);
      emptyNode.classList.remove("hidden");
      emptyNode.textContent = message;
      tableWrapNode.classList.add("hidden");
      paginationNode.classList.add("hidden");
      return false;
    }
  };

  const saveSingleCommand = async ({ commandKey, enabled, paramValues }) => {
    const data = {};

    if (enabled !== undefined) {
      data.enabled = Boolean(enabled);
    }
    if (paramValues !== undefined) {
      data.param_values = cloneValue(paramValues || {});
    }

    await api.apiRequest(`/webui/api/commands/${encodeURIComponent(commandKey)}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(data),
      action: "保存",
      expectedStatus: 200,
    });

    const reloaded = await loadCommands({ clearStatus: false });
    return { reloaded };
  };

  reloadButton.addEventListener("click", () => {
    currentPage = 1;
    void loadCommands();
  });

  // P1-Race: 搜索输入加 300ms debounce + AbortController 取消在飞请求，避免请求风暴 + 结果 race。
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
      void loadCommands({ signal: searchAbortController.signal });
    }, 300);
  });

  perPageSelect.addEventListener("change", () => {
    currentPerPage = Number(perPageSelect.value || 10);
    currentPage = 1;
    void loadCommands();
  });

  prevPageButton.addEventListener("click", () => {
    if (currentPage <= 1) {
      return;
    }
    currentPage -= 1;
    void loadCommands({ clearStatus: false });
  });

  nextPageButton.addEventListener("click", () => {
    if (currentMeta.total_pages > 0 && currentPage >= currentMeta.total_pages) {
      return;
    }
    currentPage += 1;
    void loadCommands({ clearStatus: false });
  });

  modalSaveButton.addEventListener("click", () => {
    void saveModalParams();
  });

  modalCancelButton.addEventListener("click", () => {
    closeParamModal();
  });

  modalCloseButton.addEventListener("click", () => {
    closeParamModal();
  });

  modalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.dataset.modalClose === "1") {
      closeParamModal();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modalNode.classList.contains("hidden")) {
      closeParamModal();
    }
  });

  // ── Alias Modal ──

  const setAliasAlert = (message, type = "") => {
    if (!aliasModalAlertNode || !aliasModalAlertMessageNode) return;
    const text = String(message || "").trim();
    if (!text) {
      aliasModalAlertNode.classList.add("hidden");
      aliasModalAlertMessageNode.textContent = "";
      return;
    }
    aliasModalAlertMessageNode.textContent = text;
    aliasModalAlertNode.className = `alert ${type || "info"} modal-alert`;
  };

  const openAliasModal = (commandKey) => {
    const command = getCommandByKey(commandKey);
    if (!command || !aliasModalNode) return;

    activeAliasCommandKey = commandKey;
    setAliasAlert("");
    const aliases = Array.isArray(command.aliases) ? command.aliases : [];
    aliasInput.value = aliases.join(", ");
    aliasModalTitleNode.textContent = "编辑别名";
    openModalWithFocus(aliasModalNode);
  };

  const closeAliasModal = (force = false) => {
    // P3 一致性：与 param modal 对齐，saving 中阻止关闭。
    if (aliasSaving && !force) return;
    if (aliasModalNode) closeModalAndRestoreFocus(aliasModalNode);
    activeAliasCommandKey = "";
  };

  const saveAliases = async () => {
    if (aliasSaving || !activeAliasCommandKey) return;

    const raw = String(aliasInput.value || "").trim();
    const aliases = raw ? raw.split(",").map(s => s.trim()).filter(Boolean) : [];

    aliasSaving = true;
    aliasSaveButton.disabled = true;
    setAliasAlert("正在保存…", "info");

    try {
      const response = await api.apiRequest(
        `/webui/api/commands/${encodeURIComponent(activeAliasCommandKey)}/aliases`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ aliases }),
          action: "保存",
          expectedStatus: 200,
        }
      );
      const result = api.unwrapData(response);
      if (!result) throw new Error("保存失败");
      // 标记 saving 已结束以放行 closeAliasModal 的 saving-guard。
      aliasSaving = false;
      closeAliasModal(true);
      setStatus("保存成功，需要重启后生效", "success");
      await loadCommands({ clearStatus: false });
    } catch (error) {
      let message = error instanceof Error ? error.message : "保存失败";
      if (error && error.details && Array.isArray(error.details) && error.details.length > 0) {
        message = error.details[0].message || message;
      }
      setAliasAlert(message, "error");
    } finally {
      aliasSaving = false;
      aliasSaveButton.disabled = false;
    }
  };

  if (aliasSaveButton) aliasSaveButton.addEventListener("click", saveAliases);
  if (aliasCancelButton) aliasCancelButton.addEventListener("click", closeAliasModal);
  if (aliasCloseButton) aliasCloseButton.addEventListener("click", closeAliasModal);
  if (aliasModalNode) {
    aliasModalNode.addEventListener("click", (event) => {
      const target = event.target;
      if (target instanceof HTMLElement && target.dataset.aliasModalClose === "1") {
        closeAliasModal();
      }
    });
  }

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && aliasModalNode && !aliasModalNode.classList.contains("hidden")) {
      closeAliasModal();
    }
  });

  // ── Restart Button (warm-canvas confirm modal, no native confirm) ──

  if (restartButton) {
    const restartModal = document.getElementById("restart-confirm-modal");
    const restartConfirmBtn = document.getElementById("restart-confirm-confirm-btn");
    const restartCancelBtn = document.getElementById("restart-confirm-cancel-btn");
    const restartCloseBtn = document.getElementById("restart-confirm-close-btn");
    const restartMask = restartModal?.querySelector("[data-restart-confirm-close]");

    // P2-A: 复用 openModalWithFocus / closeModalAndRestoreFocus 保持 focus 行为一致。
    const closeRestartModal = () => {
      if (!restartModal) return;
      closeModalAndRestoreFocus(restartModal);
    };
    const openRestartModal = () => {
      if (!restartModal) return;
      openModalWithFocus(restartModal);
    };

    restartCancelBtn?.addEventListener("click", closeRestartModal);
    restartCloseBtn?.addEventListener("click", closeRestartModal);
    restartMask?.addEventListener("click", closeRestartModal);

    // P2-ESC: restart-confirm-modal 补 ESC 关闭，与 param / alias modal 一致。
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!restartModal || restartModal.classList.contains("hidden")) return;
      closeRestartModal();
    });

    restartButton.addEventListener("click", () => {
      if (!restartModal) return;
      openRestartModal();
    });

    restartConfirmBtn?.addEventListener("click", async () => {
      closeRestartModal();
      restartButton.disabled = true;
      setStatus("正在重启…", "info");
      try {
        // R2-T-6：restart 端点会触发进程 execv，HTTP 响应可能在 execv 前发出但 TCP 关闭时序不定，
        // 给前端 60s timeout 余量，避免在罕见慢回包场景下被默认 15s cap 误判超时。
        await api.apiRequest("/webui/api/restart", { method: "POST", action: "重启", timeoutMs: 60000 });
        setStatus("重启中，页面即将自动刷新…", "success");
        setTimeout(() => location.reload(), 3000);
      } catch (error) {
        const message = error instanceof Error ? error.message : "重启失败";
        setStatus(message, "error");
        restartButton.disabled = false;
      }
    });
  }

  void loadCommands();
})();
