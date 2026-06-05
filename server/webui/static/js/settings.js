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
  const loginNotifyAllGroupsInput = document.getElementById("field-login-notify-all-groups");
  const playerNotifyModeInput = document.getElementById("field-player-notify-mode");
  const playerNotifyGroupIdInput = document.getElementById("field-player-notify-group-id");
  const playerNotifyOnlineTemplateInput = document.getElementById("field-player-notify-online-template");
  const playerNotifyOfflineTemplateInput = document.getElementById("field-player-notify-offline-template");
  const chatSyncModeInput = document.getElementById("field-chat-sync-mode");
  const chatSyncGroupIdInput = document.getElementById("field-chat-sync-group-id");
  const chatSyncTemplateInput = document.getElementById("field-chat-sync-template");
  const bossNotifyModeInput = document.getElementById("field-boss-notify-mode");
  const bossNotifyGroupIdInput = document.getElementById("field-boss-notify-group-id");
  const bossNotifyTemplateInput = document.getElementById("field-boss-notify-template");
  const groupWelcomeEnabledInput = document.getElementById("field-group-welcome-enabled");
  const groupWelcomeTemplateInput = document.getElementById("field-group-welcome-template");
  const groupFarewellEnabledInput = document.getElementById("field-group-farewell-enabled");
  const groupFarewellTemplateInput = document.getElementById("field-group-farewell-template");
  const groupAutoBanOnLeaveEnabledInput = document.getElementById("field-group-auto-ban-on-leave-enabled");
  const groupAutoBanOnLeaveNotifyInput = document.getElementById("field-group-auto-ban-on-leave-notify");
  const dbBackupEnabledInput = document.getElementById("field-db-backup-enabled");
  const dbBackupIntervalHoursInput = document.getElementById("field-db-backup-interval-hours");
  const dbBackupRetentionInput = document.getElementById("field-db-backup-retention");
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
    loginNotifyAllGroupsInput &&
    playerNotifyModeInput &&
    playerNotifyGroupIdInput &&
    playerNotifyOnlineTemplateInput &&
    playerNotifyOfflineTemplateInput &&
    chatSyncModeInput &&
    chatSyncGroupIdInput &&
    chatSyncTemplateInput &&
    bossNotifyModeInput &&
    bossNotifyGroupIdInput &&
    bossNotifyTemplateInput &&
    groupWelcomeEnabledInput &&
    groupWelcomeTemplateInput &&
    groupFarewellEnabledInput &&
    groupFarewellTemplateInput &&
    groupAutoBanOnLeaveEnabledInput &&
    groupAutoBanOnLeaveNotifyInput &&
    dbBackupEnabledInput &&
    dbBackupIntervalHoursInput &&
    dbBackupRetentionInput &&
    tokenToggleButton
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;

  const QQ_ID_PATTERN = /^\d{5,20}$/;
  // M-9：监听地址格式白名单（IPv4 / IPv6-in-brackets / hostname / 0.0.0.0 / localhost）。
  const WEB_HOST_PATTERN =
    /^(0\.0\.0\.0|127\.0\.0\.1|localhost|\d{1,3}(\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)*)$/;
  // CRIT-1：mask token 与后端约定（`****` + 末 4 位），用于识别"保留原值"语义。
  const TOKEN_MASK_PREFIX = "****";
  const TOKEN_REVEAL_HIDE_MS = 10000;
  // H-4：保存后 reload 探活轮询参数。
  const RESTART_POLL_INITIAL_DELAY_MS = 1500;
  const RESTART_POLL_INTERVAL_MS = 500;
  const RESTART_POLL_MAX_ATTEMPTS = 15;
  // H-3：CSRF 防护所需的 `X-Requested-With` 头已由 api.js 在所有非 GET 请求中默认注入，
  // 此处不再重复维护。
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
    login_notify_all_groups: "登入通知范围",
    player_notify_mode: "上下线通知范围",
    player_notify_group_id: "上下线通知群号",
    player_notify_online_template: "上线消息模板",
    player_notify_offline_template: "下线消息模板",
    chat_sync_mode: "消息同步范围",
    chat_sync_group_id: "消息同步群号",
    chat_sync_template: "消息同步模板",
    boss_notify_mode: "Boss 召唤通知范围",
    boss_notify_group_id: "Boss 召唤通知群号",
    boss_notify_template: "Boss 召唤消息模板",
    group_welcome_enabled: "入群欢迎启用",
    group_welcome_template: "入群欢迎模板",
    group_farewell_enabled: "退群送别启用",
    group_farewell_template: "退群送别模板",
    group_auto_ban_on_leave_enabled: "退群自动封禁",
    group_auto_ban_on_leave_notify: "退群封禁通知",
    db_backup_enabled: "数据库自动备份",
    db_backup_interval_hours: "备份间隔",
    db_backup_retention: "备份保留数量",
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
  // CRIT-1：缓存通过 reveal 端点拉取到的明文 token，仅在显示状态下临时持有。
  let revealedToken = "";
  let tokenRevealTimer = 0;
  // M-7：reload 并发抑制；进行中的 fetch 在下一次 reload 时被 abort。
  let loadAbortController = null;
  // M-8：表单脏标记，用于 beforeunload 提示。
  let isDirty = false;

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

  const clearTokenRevealTimer = () => {
    if (tokenRevealTimer) {
      clearTimeout(tokenRevealTimer);
      tokenRevealTimer = 0;
    }
  };

  // CRIT-1：自动隐藏 token，并清空缓存避免明文滞留在 DOM。
  const hideToken = () => {
    clearTokenRevealTimer();
    revealedToken = "";
    tokenVisible = false;
    onebotAccessTokenInput.value = "";
    setTokenButtonIcon(false);
  };

  const parseCommaListField = (fieldLabel, rawText) => {
    const text = String(rawText || "").trim();
    if (!text) {
      // M-5：中文字段名 + 中文谓词之间不加空格。
      throw new Error(`${fieldLabel}不能为空`);
    }
    const values = text
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (!values.length) {
      throw new Error(`${fieldLabel}不能为空`);
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
        throw new Error(`${FIELD_LABELS.onebot_ws_urls}不能包含空项`);
      }
      let parsed;
      try {
        parsed = new URL(value);
      } catch (_error) {
        throw new Error(`${FIELD_LABELS.onebot_ws_urls}必须是 ws/wss URL`);
      }
      if (!["ws:", "wss:"].includes(parsed.protocol)) {
        throw new Error(`${FIELD_LABELS.onebot_ws_urls}必须是 ws/wss URL`);
      }
    }
  };

  const validateQqIdList = (fieldLabel, values) => {
    for (const value of values) {
      if (!QQ_ID_PATTERN.test(value)) {
        throw new Error(`${fieldLabel}仅支持 5-20 位数字`);
      }
    }
  };

  const assertSingleLineValue = (fieldLabel, rawValue) => {
    const text = String(rawValue ?? "");
    if (text.includes("\r") || text.includes("\n")) {
      throw new Error(`${fieldLabel}不能包含换行`);
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

    // CRIT-1：token input 留空表示保留原值；当前显示的 mask 串也视为保留原值。
    // 后端在收到空串 / mask 串时会从 snapshot 复用现有 token。
    const onebotAccessTokenRaw = assertSingleLineValue(
      FIELD_LABELS.onebot_access_token,
      onebotAccessTokenInput.value
    );
    const onebotAccessToken =
      !onebotAccessTokenRaw || onebotAccessTokenRaw.startsWith(TOKEN_MASK_PREFIX)
        ? ""
        : onebotAccessTokenRaw;

    const webServerHost = assertSingleLineValue(
      FIELD_LABELS.web_server_host,
      webServerHostInput.value
    );
    if (!webServerHost) {
      throw new Error(`${FIELD_LABELS.web_server_host}不能为空`);
    }
    // M-9：格式校验，防 `;`、空格、`&` 等非法字符落进 .env 导致下次启动失败。
    if (!WEB_HOST_PATTERN.test(webServerHost)) {
      throw new Error(`${FIELD_LABELS.web_server_host}格式无效`);
    }

    const webServerPortText = String(webServerPortInput.value || "").trim();
    if (!webServerPortText) {
      throw new Error(`${FIELD_LABELS.web_server_port}不能为空`);
    }
    const webServerPort = Number(webServerPortText);
    if (!Number.isInteger(webServerPort) || webServerPort < 1 || webServerPort > 65535) {
      throw new Error(`${FIELD_LABELS.web_server_port}范围必须在 1-65535`);
    }

    const baseUrl = assertSingleLineValue(
      FIELD_LABELS.web_server_public_base_url,
      webServerPublicBaseUrlInput.value
    );
    if (!baseUrl) {
      throw new Error(`${FIELD_LABELS.web_server_public_base_url}不能为空`);
    }
    let parsedBaseUrl;
    try {
      parsedBaseUrl = new URL(baseUrl);
    } catch (_error) {
      throw new Error(`${FIELD_LABELS.web_server_public_base_url}必须是 http/https URL`);
    }
    if (!["http:", "https:"].includes(parsedBaseUrl.protocol)) {
      throw new Error(`${FIELD_LABELS.web_server_public_base_url}必须是 http/https URL`);
    }

    const commandDisabledMode = assertSingleLineValue(
      FIELD_LABELS.command_disabled_mode,
      commandDisabledModeInput.value
    ).toLowerCase();
    if (!["reply", "silent"].includes(commandDisabledMode)) {
      throw new Error(
        `${FIELD_LABELS.command_disabled_mode}仅支持 ${MODE_LABELS.reply} 或 ${MODE_LABELS.silent}`
      );
    }

    const commandDisabledMessage = assertSingleLineValue(
      FIELD_LABELS.command_disabled_message,
      commandDisabledMessageInput.value
    );
    if (!commandDisabledMessage) {
      throw new Error(`${FIELD_LABELS.command_disabled_message}不能为空`);
    }

    const dbBackupIntervalText = String(dbBackupIntervalHoursInput.value || "").trim();
    if (!dbBackupIntervalText) {
      throw new Error(`${FIELD_LABELS.db_backup_interval_hours}不能为空`);
    }
    const dbBackupIntervalHours = Number(dbBackupIntervalText);
    if (
      !Number.isInteger(dbBackupIntervalHours) ||
      dbBackupIntervalHours < 1 ||
      dbBackupIntervalHours > 8760
    ) {
      throw new Error(`${FIELD_LABELS.db_backup_interval_hours}范围必须在 1-8760`);
    }

    const dbBackupRetentionText = String(dbBackupRetentionInput.value || "").trim();
    if (!dbBackupRetentionText) {
      throw new Error(`${FIELD_LABELS.db_backup_retention}不能为空`);
    }
    const dbBackupRetention = Number(dbBackupRetentionText);
    if (
      !Number.isInteger(dbBackupRetention) ||
      dbBackupRetention < 1 ||
      dbBackupRetention > 1000
    ) {
      throw new Error(`${FIELD_LABELS.db_backup_retention}范围必须在 1-1000`);
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
      login_notify_all_groups: loginNotifyAllGroupsInput.value === "true",
      player_notify_mode: playerNotifyModeInput.value,
      player_notify_group_id: playerNotifyGroupIdInput.value.trim(),
      player_notify_online_template: playerNotifyOnlineTemplateInput.value,
      player_notify_offline_template: playerNotifyOfflineTemplateInput.value,
      chat_sync_mode: chatSyncModeInput.value,
      chat_sync_group_id: chatSyncGroupIdInput.value.trim(),
      chat_sync_template: chatSyncTemplateInput.value,
      boss_notify_mode: bossNotifyModeInput.value,
      boss_notify_group_id: bossNotifyGroupIdInput.value.trim(),
      boss_notify_template: bossNotifyTemplateInput.value,
      group_welcome_enabled: groupWelcomeEnabledInput.value === "true",
      group_welcome_template: groupWelcomeTemplateInput.value,
      group_farewell_enabled: groupFarewellEnabledInput.value === "true",
      group_farewell_template: groupFarewellTemplateInput.value,
      group_auto_ban_on_leave_enabled: groupAutoBanOnLeaveEnabledInput.value === "true",
      group_auto_ban_on_leave_notify: groupAutoBanOnLeaveNotifyInput.value === "true",
      db_backup_enabled: dbBackupEnabledInput.value === "true",
      db_backup_interval_hours: dbBackupIntervalHours,
      db_backup_retention: dbBackupRetention,
    };
  };

  const fillForm = (data) => {
    onebotWsUrlsInput.value = Array.isArray(data.onebot_ws_urls)
      ? data.onebot_ws_urls.join(", ")
      : "";
    // CRIT-1：返回的 token 是 mask 形式，不回填进 input；用户须点眼睛图标显式拉取。
    onebotAccessTokenInput.value = "";
    onebotAccessTokenInput.placeholder = "留空保留原 Token";
    revealedToken = "";
    tokenVisible = false;
    clearTokenRevealTimer();
    setTokenButtonIcon(false);
    ownerIdInput.value = Array.isArray(data.owner_id) ? data.owner_id.join(", ") : "";
    groupIdInput.value = Array.isArray(data.group_id) ? data.group_id.join(", ") : "";
    webServerHostInput.value = String(data.web_server_host ?? "");
    webServerPortInput.value = String(data.web_server_port ?? "");
    webServerPublicBaseUrlInput.value = String(data.web_server_public_base_url ?? "");
    commandDisabledModeInput.value = String(data.command_disabled_mode ?? "reply");
    commandDisabledMessageInput.value = String(data.command_disabled_message ?? "");
    loginNotifyAllGroupsInput.value = data.login_notify_all_groups ? "true" : "false";
    playerNotifyModeInput.value = String(data.player_notify_mode ?? "all");
    playerNotifyGroupIdInput.value = String(data.player_notify_group_id ?? "");
    playerNotifyOnlineTemplateInput.value = String(
      data.player_notify_online_template ?? "[{server}]{player} 上线了",
    );
    playerNotifyOfflineTemplateInput.value = String(
      data.player_notify_offline_template ?? "[{server}]{player} 下线了",
    );
    chatSyncModeInput.value = String(data.chat_sync_mode ?? "all");
    chatSyncGroupIdInput.value = String(data.chat_sync_group_id ?? "");
    chatSyncTemplateInput.value = String(
      data.chat_sync_template ?? "[{server}]{player}：{message}",
    );
    bossNotifyModeInput.value = String(data.boss_notify_mode ?? "all");
    bossNotifyGroupIdInput.value = String(data.boss_notify_group_id ?? "");
    bossNotifyTemplateInput.value = String(
      data.boss_notify_template ?? "[{server}]{player} 召唤了 {boss}",
    );
    groupWelcomeEnabledInput.value = data.group_welcome_enabled ? "true" : "false";
    groupWelcomeTemplateInput.value = String(
      data.group_welcome_template ?? "{at} 欢迎加入本群！\n请先阅读群公告~",
    );
    groupFarewellEnabledInput.value = data.group_farewell_enabled ? "true" : "false";
    groupFarewellTemplateInput.value = String(
      data.group_farewell_template ?? "{nickname}（{user_id}）离开了本群",
    );
    groupAutoBanOnLeaveEnabledInput.value = data.group_auto_ban_on_leave_enabled ? "true" : "false";
    groupAutoBanOnLeaveNotifyInput.value = data.group_auto_ban_on_leave_notify ? "true" : "false";
    dbBackupEnabledInput.value =
      data.db_backup_enabled === undefined || data.db_backup_enabled ? "true" : "false";
    dbBackupIntervalHoursInput.value = String(data.db_backup_interval_hours ?? 24);
    dbBackupRetentionInput.value = String(data.db_backup_retention ?? 30);
    updateArrayPreviews();
  };

  const loadSettings = async () => {
    // M-7：取消进行中的请求，避免并发响应覆盖。
    if (loadAbortController) {
      loadAbortController.abort();
    }
    const controller = new AbortController();
    loadAbortController = controller;
    // M-6：进入加载态。
    setStatus("加载中…", "info");
    reloadButton.disabled = true;
    try {
      const payload = await api.apiRequest("/webui/api/settings", {
        method: "GET",
        headers: { Accept: "application/json" },
        action: "加载",
        expectedStatus: 200,
        signal: controller.signal,
      });
      fillForm(api.unwrapData(payload));
      setStatus("");
      // M-8：成功加载视为表单干净。
      isDirty = false;
    } catch (error) {
      // 主动 abort 引发的错误不向用户报错。
      if (error && error.name === "AbortError") {
        return;
      }
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
    } finally {
      if (loadAbortController === controller) {
        loadAbortController = null;
      }
      reloadButton.disabled = false;
    }
  };

  // CRIT-1：点击眼睛图标时按需拉取明文 token；10s 后自动隐藏并清空。
  const revealToken = async () => {
    try {
      const payload = await api.apiRequest("/webui/api/settings/onebot-token", {
        method: "GET",
        headers: { Accept: "application/json" },
        action: "加载",
        expectedStatus: 200,
      });
      const data = api.unwrapData(payload);
      revealedToken = String((data && data.token) || "");
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      return false;
    }
    onebotAccessTokenInput.value = revealedToken;
    tokenVisible = true;
    setTokenButtonIcon(true);
    clearTokenRevealTimer();
    tokenRevealTimer = window.setTimeout(() => {
      tokenRevealTimer = 0;
      hideToken();
    }, TOKEN_REVEAL_HIDE_MS);
    return true;
  };

  // H-4：探活 /webui/api/settings。返回 200 或 401 均视为新进程已上线：
  // 401 是 auth 中间件对失效 session/token 的响应（API 路由不会 302），
  // 此时立即 reload，浏览器随后请求 HTML 路由会被自然 302 到登录页。
  // 仅网络错误 / abort 才视为未恢复。
  const probeRestartReady = async (signal) => {
    try {
      const response = await fetch("/webui/api/settings", {
        method: "GET",
        headers: { Accept: "application/json" },
        signal,
      });
      return response.status === 200 || response.status === 401;
    } catch (_error) {
      return false;
    }
  };

  const waitForRestart = async (signal) => {
    await new Promise((resolve, reject) => {
      const timer = window.setTimeout(resolve, RESTART_POLL_INITIAL_DELAY_MS);
      const onAbort = () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    });

    for (let attempt = 0; attempt < RESTART_POLL_MAX_ATTEMPTS; attempt += 1) {
      if (signal.aborted) {
        return false;
      }
      // eslint-disable-next-line no-await-in-loop
      const ready = await probeRestartReady(signal);
      if (ready) {
        return true;
      }
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve, reject) => {
        const timer = window.setTimeout(resolve, RESTART_POLL_INTERVAL_MS);
        const onAbort = () => {
          window.clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        };
        if (signal.aborted) {
          onAbort();
          return;
        }
        signal.addEventListener("abort", onAbort, { once: true });
      });
    }
    return false;
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
    setStatus("正在保存…", "warning");
    try {
      await api.apiRequest("/webui/api/settings", {
        method: "PUT",
        body: JSON.stringify(data),
        action: "保存",
        expectedStatus: 200,
      });

      // M-4：成功文案严格遵守"动作+结果"，不再拼接"正在重启程序"。
      setStatus("保存成功", "success");
      // M-8：保存成功后表单视为干净。
      isDirty = false;

      // H-4：等首次响应可用后再 reload，避免冷启动慢时撞到 connection refused。
      const pollController = new AbortController();
      const onUnload = () => pollController.abort();
      window.addEventListener("beforeunload", onUnload, { once: true });
      try {
        // 进入 poll 前把状态切到 info，避免 success toast 长时间挂着让用户
        // 误以为流程已结束；同时显式告知接下来"重启 → 可能要重新登录"是预期行为。
        setStatus("正在重启，等待服务恢复…", "info");
        const ready = await waitForRestart(pollController.signal);
        if (ready) {
          window.location.reload();
          return;
        }
        setStatus("重启超时，请手动刷新页面", "warning");
        saveButton.disabled = false;
      } catch (_error) {
        // 主动 abort（用户已离开页面）不需要再展示文案。
      } finally {
        window.removeEventListener("beforeunload", onUnload);
      }
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
    if (tokenVisible) {
      hideToken();
      return;
    }
    void revealToken();
  });

  onebotWsUrlsInput.addEventListener("input", updateArrayPreviews);
  ownerIdInput.addEventListener("input", updateArrayPreviews);
  groupIdInput.addEventListener("input", updateArrayPreviews);

  // M-8：所有输入控件都标记表单为脏，并在 beforeunload 时提示用户。
  const dirtyTargets = [
    onebotWsUrlsInput,
    onebotAccessTokenInput,
    ownerIdInput,
    groupIdInput,
    webServerHostInput,
    webServerPortInput,
    webServerPublicBaseUrlInput,
    commandDisabledModeInput,
    commandDisabledMessageInput,
    loginNotifyAllGroupsInput,
    playerNotifyModeInput,
    playerNotifyGroupIdInput,
    playerNotifyOnlineTemplateInput,
    playerNotifyOfflineTemplateInput,
    chatSyncModeInput,
    chatSyncGroupIdInput,
    chatSyncTemplateInput,
    bossNotifyModeInput,
    bossNotifyGroupIdInput,
    bossNotifyTemplateInput,
    groupWelcomeEnabledInput,
    groupWelcomeTemplateInput,
    groupFarewellEnabledInput,
    groupFarewellTemplateInput,
    groupAutoBanOnLeaveEnabledInput,
    groupAutoBanOnLeaveNotifyInput,
    dbBackupEnabledInput,
    dbBackupIntervalHoursInput,
    dbBackupRetentionInput,
  ];
  const markDirty = () => {
    isDirty = true;
  };
  for (const node of dirtyTargets) {
    node.addEventListener("input", markDirty);
    node.addEventListener("change", markDirty);
  }

  window.addEventListener("beforeunload", (event) => {
    if (!isDirty) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });

  setTokenButtonIcon(false);
  updateArrayPreviews();
  void loadSettings();
})();
