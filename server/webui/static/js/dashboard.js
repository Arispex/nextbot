(() => {
  const reloadButton = document.getElementById("reload-btn");
  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const loadingNode = document.getElementById("loading");
  const statsGridNode = document.getElementById("stats-grid");
  const dashboardPanelsNode = document.getElementById("dashboard-panels");

  const runningStatusNode = document.getElementById("running_status");
  const serverCountNode = document.getElementById("server_count");
  const userCountNode = document.getElementById("user_count");
  const groupCountNode = document.getElementById("group_count");
  const commandTotalNode = document.getElementById("command_total");
  const commandEnabledCountNode = document.getElementById("command_enabled_count");
  const commandExecuteCountNode = document.getElementById("command_execute_count");
  const dashboardUpdatedAtNode = document.getElementById("dashboard-updated-at");
  const connectedBotIdsNode = document.getElementById("connected_bot_ids");

  const requiredNodesReady = Boolean(
    reloadButton &&
      statusNode &&
      statusMessageNode &&
      loadingNode &&
      statsGridNode &&
      dashboardPanelsNode &&
      runningStatusNode &&
      serverCountNode &&
      userCountNode &&
      groupCountNode &&
      commandTotalNode &&
      commandEnabledCountNode &&
      commandExecuteCountNode &&
      dashboardUpdatedAtNode &&
      connectedBotIdsNode
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;

  let loading = false;
  let hasLoaded = false;
  let reloadButtonWasFocused = false;
  // R2-B-4：模块级 AbortController，新 reload 时 abort 旧请求，避免切 tab / 关页面后 fetch 资源残留。
  let currentReloadController = null;

  const formatNumber = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return "—";
    }
    return parsed.toLocaleString("zh-CN");
  };

  const setStatus = (message, type = "") => {
    const text = String(message || "").trim();
    if (!text) {
      statusNode.className = "alert hidden";
      statusNode.setAttribute("role", "status");
      statusMessageNode.textContent = "";
      return;
    }
    const normalizedType = ["success", "error", "warning", "info"].includes(type)
      ? type
      : "info";
    statusNode.className = `alert ${normalizedType}`;
    statusNode.setAttribute("role", normalizedType === "error" ? "alert" : "status");
    statusMessageNode.textContent = text;
  };

  const setReloadButtonText = (label) => {
    const text = String(label || "").trim();
    const labelNode = reloadButton.querySelector("[data-label]");
    if (labelNode) {
      labelNode.textContent = text;
      return;
    }
    reloadButton.textContent = text;
  };

  const setLoadingState = (isLoading) => {
    if (isLoading && !loading) {
      reloadButtonWasFocused = document.activeElement === reloadButton;
    }
    loading = Boolean(isLoading);
    reloadButton.disabled = loading;
    setReloadButtonText(loading ? "刷新中…" : "刷新");

    if (loading) {
      loadingNode.classList.remove("hidden");
      statsGridNode.setAttribute("aria-busy", "true");
      dashboardPanelsNode.setAttribute("aria-busy", "true");
      if (!hasLoaded) {
        statsGridNode.classList.add("hidden");
        dashboardPanelsNode.classList.add("hidden");
      }
      return;
    }

    loadingNode.classList.add("hidden");
    statsGridNode.removeAttribute("aria-busy");
    dashboardPanelsNode.removeAttribute("aria-busy");
    if (hasLoaded) {
      statsGridNode.classList.remove("hidden");
      dashboardPanelsNode.classList.remove("hidden");
    }

    if (reloadButtonWasFocused) {
      // disabled→enabled 后短延迟恢复 focus，避免被浏览器 blur
      queueMicrotask(() => {
        if (!reloadButton.disabled) {
          reloadButton.focus({ preventScroll: true });
        }
      });
      reloadButtonWasFocused = false;
    }
  };

  const renderConnectedBotIds = (ids) => {
    const list = Array.isArray(ids)
      ? ids.map((item) => String(item || "").trim()).filter((item) => item.length > 0)
      : [];

    const fragment = document.createDocumentFragment();
    if (list.length === 0) {
      const node = document.createElement("span");
      node.className = "tag-badge none";
      node.textContent = "无";
      fragment.appendChild(node);
    } else {
      list.forEach((item) => {
        const node = document.createElement("span");
        node.className = "tag-badge";
        node.textContent = item;
        fragment.appendChild(node);
      });
    }

    connectedBotIdsNode.replaceChildren(fragment);
  };

  const renderMetrics = (data) => {
    runningStatusNode.textContent = String(data.running_status || "—");
    serverCountNode.textContent = formatNumber(data.server_count);
    userCountNode.textContent = formatNumber(data.user_count);
    groupCountNode.textContent = formatNumber(data.group_count);
    commandTotalNode.textContent = formatNumber(data.command_total);
    commandEnabledCountNode.textContent = formatNumber(data.command_enabled_count);
    commandExecuteCountNode.textContent = formatNumber(data.command_execute_count);
    dashboardUpdatedAtNode.textContent = String(data.generated_at || "—");
    renderConnectedBotIds(data.connected_bot_ids);
  };

  const loadDashboardData = async () => {
    if (loading) {
      return;
    }

    // R2-B-4：abort 任何尚未完成的旧请求
    if (currentReloadController) {
      try {
        currentReloadController.abort();
      } catch (_err) {
        // ignore
      }
    }
    const localController =
      typeof AbortController !== "undefined" ? new AbortController() : null;
    currentReloadController = localController;

    setLoadingState(true);
    setStatus("");

    try {
      const payload = await api.apiRequest("/webui/api/dashboard", {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
        action: "加载",
        expectedStatus: 200,
        signal: localController ? localController.signal : undefined,
      });

      if (localController && localController.signal.aborted) {
        return;
      }

      renderMetrics(api.unwrapData(payload));
      hasLoaded = true;
      setStatus("");
    } catch (error) {
      if (localController && localController.signal.aborted) {
        return;
      }
      setStatus(error instanceof Error ? error.message : "加载失败", "error");
    } finally {
      if (currentReloadController === localController) {
        currentReloadController = null;
      }
      setLoadingState(false);
    }
  };

  reloadButton.addEventListener("click", () => {
    void loadDashboardData();
  });

  void loadDashboardData();
})();
