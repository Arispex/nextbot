(() => {
  const parseJsonSafe = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const buildFallbackErrorMessage = (fallbackPrefix, status) => {
    const prefix = String(fallbackPrefix || "请求失败").trim() || "请求失败";
    return `${prefix}，HTTP ${status}`;
  };

  const readApiErrorMessage = (payload, fallback) => {
    if (payload && typeof payload === "object") {
      const error = payload.error;
      if (error && typeof error === "object") {
        const baseMessage = typeof error.message === "string" ? error.message.trim() : "";
        const details = Array.isArray(error.details)
          ? error.details
              .map((item) => {
                if (!item || typeof item !== "object") {
                  return "";
                }
                const message = typeof item.message === "string" ? item.message.trim() : "";
                return message;
              })
              .filter(Boolean)
          : [];
        if (baseMessage && details.length) {
          return `${baseMessage}\n${details.join("\n")}`;
        }
        if (baseMessage) {
          return baseMessage;
        }
        if (details.length) {
          return details.join("\n");
        }
      }
    }
    return fallback;
  };

  const unwrapData = (payload) => {
    if (!payload || typeof payload !== "object" || !("data" in payload)) {
      throw new Error("返回数据格式错误");
    }
    return payload.data;
  };

  const unwrapMeta = (payload) => {
    if (!payload || typeof payload !== "object") {
      return {};
    }
    const meta = payload.meta;
    return meta && typeof meta === "object" ? meta : {};
  };

  const buildNetworkErrorMessage = (fallbackPrefix, error) => {
    const prefix = String(fallbackPrefix || "请求失败").trim() || "请求失败";
    const reason = error instanceof Error ? String(error.message || "").trim() : "";
    return reason ? `${prefix}，${reason}` : prefix;
  };

  const apiRequest = async (url, { method = "GET", headers = {}, body, errorPrefix } = {}) => {
    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body,
      });
    } catch (error) {
      throw new Error(buildNetworkErrorMessage(errorPrefix, error));
    }
    const payload = await parseJsonSafe(response);
    if (!response.ok) {
      throw new Error(
        readApiErrorMessage(payload, buildFallbackErrorMessage(errorPrefix || "请求失败", response.status))
      );
    }
    return payload;
  };

  window.NextBotWebUIApi = {
    parseJsonSafe,
    readApiErrorMessage,
    unwrapData,
    unwrapMeta,
    apiRequest,
    buildFallbackErrorMessage,
    buildNetworkErrorMessage,
  };
})();
