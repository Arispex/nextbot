(() => {
  const reloadButton = document.getElementById("reload-btn");
  const searchInput = document.getElementById("command-search");

  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const loadingNode = document.getElementById("loading");
  const emptyNode = document.getElementById("empty");
  const tableWrapNode = document.getElementById("table-wrap");
  const tableBodyNode = document.getElementById("command-table-body");

  const modalNode = document.getElementById("param-modal");
  const modalBodyNode = document.getElementById("param-modal-body");
  const modalTitleNode = document.getElementById("param-modal-title");
  const modalAlertNode = document.getElementById("param-modal-alert");
  const modalAlertMessageNode = document.getElementById("param-modal-alert-message");
  const modalCloseButton = document.getElementById("modal-close-btn");
  const modalCancelButton = document.getElementById("modal-cancel-btn");
  const modalSaveButton = document.getElementById("modal-save-btn");

  let commandStates = [];
  let activeModalCommandKey = "";
  let modalSaving = false;

  const requiredNodesReady = Boolean(
    statusNode &&
      statusMessageNode &&
      loadingNode &&
      emptyNode &&
      tableWrapNode &&
      tableBodyNode
  );

  const setStatus = (message, type = "") => {
    if (!statusNode || !statusMessageNode) return;
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
    if (!modalAlertNode || !modalAlertMessageNode) return;
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

  const getErrorMessage = (data, fallbackMessage) => {
    if (data && typeof data === "object") {
      if (Array.isArray(data.errors) && data.errors.length) {
        const firstError = data.errors[0];
        if (firstError && typeof firstError === "object" && firstError.message) {
          return String(firstError.message);
        }
      }
      if (data.message) {
        return String(data.message);
      }
    }
    return fallbackMessage;
  };

  const setModalSavingState = (saving) => {
    modalSaving = Boolean(saving);
    if (modalSaveButton) {
      modalSaveButton.disabled = modalSaving;
    }
    if (modalCancelButton) {
      modalCancelButton.disabled = modalSaving;
    }
    if (modalCloseButton) {
      modalCloseButton.disabled = modalSaving;
    }
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

  const getFilteredCommands = () => {
    const keyword = String(searchInput?.value || "").trim().toLowerCase();
    if (!keyword) return [...commandStates];

    return commandStates.filter((item) => {
      const text = [
        String(item.display_name || "").toLowerCase(),
        String(item.description || "").toLowerCase(),
        String(item.usage || "").toLowerCase(),
        String(item.permission || "").toLowerCase(),
      ].join(" ");
      return text.includes(keyword);
    });
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

  const renderTable = () => {
    if (!tableBodyNode || !loadingNode || !emptyNode || !tableWrapNode) return;

    tableBodyNode.innerHTML = "";
    loadingNode.classList.add("hidden");

    const filteredCommands = getFilteredCommands();

    if (!commandStates.length) {
      emptyNode.textContent = "暂无可配置命令。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    if (!filteredCommands.length) {
      emptyNode.textContent = "没有匹配的命令。";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    filteredCommands.sort((a, b) =>
      String(a.display_name || "").localeCompare(String(b.display_name || ""))
    );

    for (const command of filteredCommands) {
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
        setStatus("保存中...");

        try {
          const { reloaded } = await saveSingleCommand({
            commandKey: command.command_key,
            enabled: nextEnabled,
          });
          if (reloaded) {
            setStatus("保存成功，已立即生效", "success");
          } else {
            setStatus("保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态", "warning");
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

      const schema = command.param_schema && typeof command.param_schema === "object"
        ? command.param_schema
        : {};
      const paramNames = Object.keys(schema);

      const actionCell = document.createElement("td");
      if (paramNames.length) {
        const actionButton = document.createElement("button");
        actionButton.type = "button";
        actionButton.className = "btn action-btn";
        actionButton.textContent = "编辑参数";
        actionButton.addEventListener("click", () => {
          openParamModal(command.command_key);
        });
        actionCell.appendChild(actionButton);
      }

      row.appendChild(commandCell);
      row.appendChild(descriptionCell);
      row.appendChild(usageCell);
      row.appendChild(permissionCell);
      row.appendChild(statusCell);
      row.appendChild(actionCell);
      tableBodyNode.appendChild(row);
    }
  };

  const openParamModal = (commandKey) => {
    if (!modalNode || !modalBodyNode || !modalTitleNode) return;

    const command = getCommandByKey(commandKey);
    if (!command) return;

    activeModalCommandKey = commandKey;
    setModalAlert("修改参数后会立即保存并生效。", "info");

    const schema = command.param_schema && typeof command.param_schema === "object"
      ? command.param_schema
      : {};
    const paramNames = Object.keys(schema);

    modalTitleNode.textContent = "编辑参数";
    modalBodyNode.innerHTML = "";

    if (!paramNames.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "当前命令没有可配置参数。";
      modalBodyNode.appendChild(empty);
      setModalAlert("当前命令没有可配置参数。", "warning");
      modalNode.classList.remove("hidden");
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

    modalNode.classList.remove("hidden");
  };

  const closeParamModal = () => {
    if (!modalNode || !modalBodyNode || modalSaving) return;
    modalNode.classList.add("hidden");
    modalBodyNode.innerHTML = "";
    activeModalCommandKey = "";
    setModalAlert("");
  };

  const saveModalParams = async () => {
    if (!modalBodyNode || !activeModalCommandKey || modalSaving) return;

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
        setModalAlert(`${paramLabel}: 参数定义无效`, "error");
        return;
      }

      let rawValue;
      if (schema.type === "bool") {
        rawValue = Boolean(inputNode.checked);
      } else if (inputNode.dataset.enumSelect === "1" && Array.isArray(schema.enum)) {
        const enumIndex = Number.parseInt(String(inputNode.value), 10);
        if (!Number.isInteger(enumIndex) || enumIndex < 0 || enumIndex >= schema.enum.length) {
          setModalAlert(`${paramLabel}: 选项无效`, "error");
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
        setModalAlert(`${paramLabel}: ${message}`, "error");
        if (typeof inputNode.focus === "function") {
          inputNode.focus();
        }
        return;
      }
    }

    setModalSavingState(true);
    setModalAlert("保存中...", "info");

    try {
      const { reloaded } = await saveSingleCommand({
        commandKey: command.command_key,
        params: nextValues,
      });
      command.param_values = nextValues;
      if (reloaded) {
        setStatus("参数保存成功，已立即生效", "success");
      } else {
        setStatus("参数保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态", "warning");
      }
      setModalSavingState(false);
      closeParamModal();
      return;
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存失败";
      setModalAlert(message, "error");
    } finally {
      setModalSavingState(false);
    }
  };

  const loadCommands = async ({ clearStatus = true } = {}) => {
    if (!requiredNodesReady) {
      if (loadingNode) {
        loadingNode.classList.add("hidden");
      }
      if (statusNode) {
        setStatus("页面资源版本不一致，请刷新页面或重启机器人", "error");
      }
      return false;
    }

    if (loadingNode) {
      loadingNode.classList.remove("hidden");
    }
    if (tableWrapNode) {
      tableWrapNode.classList.add("hidden");
    }
    if (clearStatus) {
      setStatus("");
    }

    try {
      const response = await fetch("/webui/api/commands", {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
      });

      if (!response.ok) {
        throw new Error(`加载失败 (${response.status})`);
      }

      const data = await response.json();
      const commands = Array.isArray(data.commands) ? data.commands : [];
      commandStates = cloneValue(commands);
      for (const command of commandStates) {
        ensureCommandParamValues(command);
      }

      renderTable();
      return true;
    } catch (error) {
      renderTable();
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      return false;
    }
  };

  const saveSingleCommand = async ({ commandKey, enabled, params }) => {
    const payload = {
      command_key: commandKey,
    };

    if (enabled !== undefined) {
      payload.enabled = Boolean(enabled);
    }
    if (params !== undefined) {
      payload.params = cloneValue(params || {});
    }

    const response = await fetch("/webui/api/commands/batch", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ commands: [payload] }),
    });

    let data = null;
    try {
      data = await response.json();
    } catch (_error) {
      data = null;
    }

    if (!response.ok || !data?.ok) {
      throw new Error(getErrorMessage(data, `保存失败 (${response.status})`));
    }

    const reloaded = await loadCommands({ clearStatus: false });
    return { reloaded };
  };

  reloadButton?.addEventListener("click", () => {
    loadCommands();
  });

  searchInput?.addEventListener("input", () => {
    renderTable();
  });

  modalSaveButton?.addEventListener("click", () => {
    saveModalParams();
  });

  modalCancelButton?.addEventListener("click", () => {
    closeParamModal();
  });

  modalCloseButton?.addEventListener("click", () => {
    closeParamModal();
  });

  modalNode?.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.dataset.modalClose === "1") {
      closeParamModal();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modalNode && !modalNode.classList.contains("hidden")) {
      closeParamModal();
    }
  });

  loadCommands();
})();
