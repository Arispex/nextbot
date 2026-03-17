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

  const formatNumber = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return "--";
    }
    return parsed.toLocaleString("zh-CN");
  };

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
    loading = Boolean(isLoading);
    reloadButton.disabled = loading;
    setReloadButtonText(loading ? "刷新中..." : "刷新");

    if (loading) {
      loadingNode.classList.remove("hidden");
      if (!hasLoaded) {
        statsGridNode.classList.add("hidden");
        dashboardPanelsNode.classList.add("hidden");
      }
      return;
    }

    loadingNode.classList.add("hidden");
    if (hasLoaded) {
      statsGridNode.classList.remove("hidden");
      dashboardPanelsNode.classList.remove("hidden");
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
    runningStatusNode.textContent = String(data.running_status || "--");
    serverCountNode.textContent = formatNumber(data.server_count);
    userCountNode.textContent = formatNumber(data.user_count);
    groupCountNode.textContent = formatNumber(data.group_count);
    commandTotalNode.textContent = formatNumber(data.command_total);
    commandEnabledCountNode.textContent = formatNumber(data.command_enabled_count);
    commandExecuteCountNode.textContent = formatNumber(data.command_execute_count);
    dashboardUpdatedAtNode.textContent = String(data.generated_at || "--");
    renderConnectedBotIds(data.connected_bot_ids);
  };

  const loadDashboardData = async () => {
    if (loading) {
      return;
    }

    setLoadingState(true);
    setStatus("");

    try {
      const payload = await api.apiRequest("/webui/api/dashboard", {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
        errorPrefix: "加载失败",
      });

      renderMetrics(api.unwrapData(payload));
      hasLoaded = true;
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "加载失败", "error");
    } finally {
      setLoadingState(false);
    }
  };

  reloadButton.addEventListener("click", () => {
    void loadDashboardData();
  });

  void loadDashboardData();
})();
