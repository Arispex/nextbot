(() => {
  const reloadButton = document.getElementById("reload-btn");
  const saveButton = document.getElementById("save-btn");
  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const onebotWsUrlsInput = document.getElementById("field-onebot-ws-urls");

  const onebotWsUrlsPreview = document.getElementById("preview-onebot-ws-urls");
  const onebotAccessTokenInput = document.getElementById("field-onebot-access-token");
  const ownerIdInput = document.getElementById("field-owner-id");
  const ownerIdPreview = document.getElementById("preview-owner-id");
  const groupIdInput = document.getElementById("field-group-id");
  const groupIdPreview = document.getElementById("preview-group-id");
  const webServerHostInput = document.getElementById("field-web-server-host");
  const webServerPortInput = document.getElementById("field-web-server-port");
  const webServerPublicBaseUrlInput = document.getElementById("field-web-server-public-base-url");
  const commandDisabledModeInput = document.getElementById("field-command-disabled-mode");
  const commandDisabledMessageInput = document.getElementById("field-command-disabled-message");
  const tokenToggleButton = document.getElementById("token-toggle-btn");

  const requiredNodesReady = Boolean(
    reloadButton &&
    saveButton &&
    statusNode &&
    statusMessageNode &&
    onebotWsUrlsInput &&
    onebotWsUrlsPreview &&
    onebotAccessTokenInput &&
    ownerIdInput &&
    ownerIdPreview &&
    groupIdInput &&
    groupIdPreview &&
    webServerHostInput &&
    webServerPortInput &&
    webServerPublicBaseUrlInput &&
    commandDisabledModeInput &&
    commandDisabledMessageInput &&
    tokenToggleButton
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;

  const QQ_ID_PATTERN = /^\d{5,20}$/;
  const FIELD_LABELS = {
    onebot_ws_urls: "OneBot WebSocket 地址",
    onebot_access_token: "OneBot 访问令牌",
    owner_id: "管理员 QQ",
    group_id: "允许群号",
    web_server_host: "Web 服务监听地址",
    web_server_port: "Web 服务端口",
    web_server_public_base_url: "Web 服务对外地址",
    command_disabled_mode: "命令关闭模式",
    command_disabled_message: "命令关闭提示语",
  };
  const MODE_LABELS = {
    reply: "回复提示",
    silent: "静默拦截",
  };
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

  let tokenVisible = false;

  const setStatus = (message, type = "info") => {
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

  const setTokenButtonIcon = (visible) => {
    tokenToggleButton.innerHTML = visible ? HIDE_ICON_SVG : SHOW_ICON_SVG;
    tokenToggleButton.title = visible ? "隐藏 Token" : "显示 Token";
    tokenToggleButton.setAttribute("aria-label", tokenToggleButton.title);
    onebotAccessTokenInput.type = visible ? "text" : "password";
  };

  const parseCommaListField = (fieldLabel, rawText) => {
    const text = String(rawText || "").trim();
    if (!text) {
      throw new Error(`${fieldLabel} 不能为空`);
    }
    const values = text
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (!values.length) {
      throw new Error(`${fieldLabel} 不能为空`);
    }
    return [...new Set(values)];
  };

  const parseCommaListLoose = (rawText) => {
    const text = String(rawText || "").trim();
    if (!text) {
      return [];
    }
    return [...new Set(
      text
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    )];
  };

  const renderTagPreview = (container, values) => {
    container.innerHTML = "";
    if (!Array.isArray(values) || values.length === 0) {
      const badge = document.createElement("span");
      badge.className = "tag-badge none";
      badge.textContent = "无";
      container.appendChild(badge);
      return;
    }

    for (const value of values) {
      const badge = document.createElement("span");
      badge.className = "tag-badge";
      badge.textContent = value;
      badge.title = value;
      container.appendChild(badge);
    }
  };

  const updateArrayPreviews = () => {
    renderTagPreview(onebotWsUrlsPreview, parseCommaListLoose(onebotWsUrlsInput.value));
    renderTagPreview(ownerIdPreview, parseCommaListLoose(ownerIdInput.value));
    renderTagPreview(groupIdPreview, parseCommaListLoose(groupIdInput.value));
  };

  const validateWsUrls = (values) => {
    for (const value of values) {
      if (!value) {
        throw new Error(`${FIELD_LABELS.onebot_ws_urls} 不能包含空项`);
      }
      let parsed;
      try {
        parsed = new URL(value);
      } catch (_error) {
        throw new Error(`${FIELD_LABELS.onebot_ws_urls} 必须是 ws/wss URL`);
      }
      if (!["ws:", "wss:"].includes(parsed.protocol)) {
        throw new Error(`${FIELD_LABELS.onebot_ws_urls} 必须是 ws/wss URL`);
      }
    }
  };

  const validateQqIdList = (fieldLabel, values) => {
    for (const value of values) {
      if (!QQ_ID_PATTERN.test(value)) {
        throw new Error(`${fieldLabel} 仅支持 5-20 位数字`);
      }
    }
  };

  const assertSingleLineValue = (fieldLabel, rawValue) => {
    const text = String(rawValue ?? "");
    if (text.includes("\r") || text.includes("\n")) {
      throw new Error(`${fieldLabel} 不能包含换行`);
    }
    return text.trim();
  };

  const buildPayload = () => {
    const onebotWsUrls = parseCommaListField(FIELD_LABELS.onebot_ws_urls, onebotWsUrlsInput.value);
    validateWsUrls(onebotWsUrls);

    const ownerId = parseCommaListField(FIELD_LABELS.owner_id, ownerIdInput.value);
    validateQqIdList(FIELD_LABELS.owner_id, ownerId);

    const groupId = parseCommaListField(FIELD_LABELS.group_id, groupIdInput.value);
    validateQqIdList(FIELD_LABELS.group_id, groupId);

    const onebotAccessToken = assertSingleLineValue(
      FIELD_LABELS.onebot_access_token,
      onebotAccessTokenInput.value
    );
    if (!onebotAccessToken) {
      throw new Error(`${FIELD_LABELS.onebot_access_token} 不能为空`);
    }

    const webServerHost = assertSingleLineValue(
      FIELD_LABELS.web_server_host,
      webServerHostInput.value
    );
    if (!webServerHost) {
      throw new Error(`${FIELD_LABELS.web_server_host} 不能为空`);
    }

    const webServerPortText = String(webServerPortInput.value || "").trim();
    if (!webServerPortText) {
      throw new Error(`${FIELD_LABELS.web_server_port} 不能为空`);
    }
    const webServerPort = Number(webServerPortText);
    if (!Number.isInteger(webServerPort) || webServerPort < 1 || webServerPort > 65535) {
      throw new Error(`${FIELD_LABELS.web_server_port} 范围必须在 1-65535`);
    }

    const baseUrl = assertSingleLineValue(
      FIELD_LABELS.web_server_public_base_url,
      webServerPublicBaseUrlInput.value
    );
    if (!baseUrl) {
      throw new Error(`${FIELD_LABELS.web_server_public_base_url} 不能为空`);
    }
    let parsedBaseUrl;
    try {
      parsedBaseUrl = new URL(baseUrl);
    } catch (_error) {
      throw new Error(`${FIELD_LABELS.web_server_public_base_url} 必须是 http/https URL`);
    }
    if (!["http:", "https:"].includes(parsedBaseUrl.protocol)) {
      throw new Error(`${FIELD_LABELS.web_server_public_base_url} 必须是 http/https URL`);
    }

    const commandDisabledMode = assertSingleLineValue(
      FIELD_LABELS.command_disabled_mode,
      commandDisabledModeInput.value
    ).toLowerCase();
    if (!["reply", "silent"].includes(commandDisabledMode)) {
      throw new Error(
        `${FIELD_LABELS.command_disabled_mode} 仅支持 ${MODE_LABELS.reply} 或 ${MODE_LABELS.silent}`
      );
    }

    const commandDisabledMessage = assertSingleLineValue(
      FIELD_LABELS.command_disabled_message,
      commandDisabledMessageInput.value
    );
    if (!commandDisabledMessage) {
      throw new Error(`${FIELD_LABELS.command_disabled_message} 不能为空`);
    }

    return {
      onebot_ws_urls: onebotWsUrls,
      onebot_access_token: onebotAccessToken,
      owner_id: ownerId,
      group_id: groupId,
      web_server_host: webServerHost,
      web_server_port: webServerPort,
      web_server_public_base_url: parsedBaseUrl.toString().replace(/\/$/, ""),
      command_disabled_mode: commandDisabledMode,
      command_disabled_message: commandDisabledMessage,
    };
  };

  const fillForm = (data) => {
    onebotWsUrlsInput.value = Array.isArray(data.onebot_ws_urls)
      ? data.onebot_ws_urls.join(", ")
      : "";
    onebotAccessTokenInput.value = String(data.onebot_access_token ?? "");
    ownerIdInput.value = Array.isArray(data.owner_id) ? data.owner_id.join(", ") : "";
    groupIdInput.value = Array.isArray(data.group_id) ? data.group_id.join(", ") : "";
    webServerHostInput.value = String(data.web_server_host ?? "");
    webServerPortInput.value = String(data.web_server_port ?? "");
    webServerPublicBaseUrlInput.value = String(data.web_server_public_base_url ?? "");
    commandDisabledModeInput.value = String(data.command_disabled_mode ?? "reply");
    commandDisabledMessageInput.value = String(data.command_disabled_message ?? "");
    updateArrayPreviews();
  };

  const loadSettings = async () => {
    setStatus("");
    try {
      const payload = await api.apiRequest("/webui/api/settings", {
        method: "GET",
        headers: { Accept: "application/json" },
        action: "加载",
        expectedStatus: 200,
      });
      fillForm(api.unwrapData(payload));
      setStatus("");
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
    }
  };

  const saveSettings = async () => {
    let data;
    try {
      data = buildPayload();
    } catch (error) {
      const message = error instanceof Error ? error.message : "表单校验失败";
      setStatus(`保存失败，${message}`, "error");
      return;
    }

    saveButton.disabled = true;
    setStatus("正在保存并重启...", "warning");
    try {
      await api.apiRequest("/webui/api/settings", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(data),
        action: "保存",
        expectedStatus: 200,
      });

      setStatus("保存成功，正在重启程序", "success");
      setTimeout(() => {
        window.location.reload();
      }, 3000);
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存失败";
      setStatus(message, "error");
      saveButton.disabled = false;
    }
  };

  reloadButton.addEventListener("click", () => {
    void loadSettings();
  });

  saveButton.addEventListener("click", () => {
    void saveSettings();
  });

  tokenToggleButton.addEventListener("click", () => {
    tokenVisible = !tokenVisible;
    setTokenButtonIcon(tokenVisible);
  });

  onebotWsUrlsInput.addEventListener("input", updateArrayPreviews);
  ownerIdInput.addEventListener("input", updateArrayPreviews);
  groupIdInput.addEventListener("input", updateArrayPreviews);

  setTokenButtonIcon(false);
  updateArrayPreviews();
  void loadSettings();
})();
