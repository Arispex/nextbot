(() => {
  class ApiRequestError extends Error {
    constructor(message, { status = 0, code = "", reason = "", details = [] } = {}) {
      super(message);
      this.name = "ApiRequestError";
      this.status = status;
      this.code = code;
      this.reason = reason;
      this.details = details;
    }
  }

  const parseJsonSafe = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const buildFallbackReason = (status) => {
    return `HTTP ${status}`;
  };

  const readApiError = (payload) => {
    if (!payload || typeof payload !== "object") {
      return {
        code: "",
        reason: "",
        details: [],
      };
    }

    const error = payload.error;
    if (!error || typeof error !== "object") {
      return {
        code: "",
        reason: "",
        details: [],
      };
    }

    const reason = typeof error.message === "string" ? error.message.trim() : "";
    const code = typeof error.code === "string" ? error.code.trim() : "";
    const details = Array.isArray(error.details)
      ? error.details.filter((item) => item && typeof item === "object")
      : [];

    return {
      code,
      reason,
      details,
    };
  };

  const buildActionFailureMessage = (action, reason) => {
    const normalizedAction = String(action || "操作").trim() || "操作";
    const normalizedReason = String(reason || "").trim();
    return normalizedReason ? `${normalizedAction}失败，${normalizedReason}` : `${normalizedAction}失败`;
  };

  const buildNetworkErrorMessage = (action, error) => {
    const reason = error instanceof Error ? String(error.message || "").trim() : "";
    return buildActionFailureMessage(action || "请求", reason);
  };

  const unwrapPayload = (result) => {
    if (result && typeof result === "object" && "payload" in result) {
      return result.payload;
    }
    return result;
  };

  const unwrapData = (result) => {
    const payload = unwrapPayload(result);
    if (!payload || typeof payload !== "object" || !("data" in payload)) {
      throw new Error("返回数据格式错误");
    }
    return payload.data;
  };

  const unwrapMeta = (result) => {
    const payload = unwrapPayload(result);
    if (!payload || typeof payload !== "object") {
      return {};
    }
    const meta = payload.meta;
    return meta && typeof meta === "object" ? meta : {};
  };

  const apiRequest = async (
    url,
    {
      method = "GET",
      headers = {},
      body,
      action = "请求",
      expectedStatus,
      expectedStatuses,
    } = {}
  ) => {
    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body,
      });
    } catch (error) {
      throw new ApiRequestError(buildNetworkErrorMessage(action, error), {
        reason: error instanceof Error ? String(error.message || "").trim() : "",
      });
    }

    const payload = await parseJsonSafe(response);
    const { code, reason, details } = readApiError(payload);

    if (!response.ok) {
      const finalReason = reason || buildFallbackReason(response.status);
      throw new ApiRequestError(buildActionFailureMessage(action, finalReason), {
        status: response.status,
        code,
        reason: finalReason,
        details,
      });
    }

    const allowedStatuses = Array.isArray(expectedStatuses)
      ? expectedStatuses
      : expectedStatus === undefined
        ? []
        : [expectedStatus];
    if (allowedStatuses.length > 0 && !allowedStatuses.includes(response.status)) {
      const finalReason = `HTTP ${response.status}`;
      throw new ApiRequestError(buildActionFailureMessage(action, finalReason), {
        status: response.status,
        code: "unexpected_status",
        reason: finalReason,
        details: [],
      });
    }

    return {
      status: response.status,
      payload,
    };
  };

  window.NextBotWebUIApi = {
    ApiRequestError,
    parseJsonSafe,
    readApiError,
    unwrapData,
    unwrapMeta,
    apiRequest,
    buildActionFailureMessage,
    buildFallbackReason,
    buildNetworkErrorMessage,
  };
})();
