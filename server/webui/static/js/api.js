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

  const buildDetailReason = (details) => {
    if (!Array.isArray(details) || details.length === 0) return "";
    const messages = details
      .map((d) => {
        if (!d || typeof d !== "object") return "";
        const message = typeof d.message === "string" ? d.message.trim() : "";
        return message;
      })
      .filter(Boolean);
    return messages.join("；");
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

  const REQUEST_TIMEOUT_MS = 15000;

  // R2-T-4 / R2-T-5：老浏览器 fallback + AbortSignal.any 缺失时手动转发 userSignal。
  // R2-T-6：支持 per-call timeoutMs override（如 restart 路径需要 60s）。
  const buildTimeoutSignal = (userSignal, timeoutMs) => {
    const timeoutValue = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : REQUEST_TIMEOUT_MS;

    // 优先用原生 AbortSignal.timeout（Chrome 103+/Firefox 100+/Safari 16.4+）
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      const timeoutSignal = AbortSignal.timeout(timeoutValue);
      if (!userSignal) {
        return timeoutSignal;
      }
      if (typeof AbortSignal.any === "function") {
        return AbortSignal.any([userSignal, timeoutSignal]);
      }
      // AbortSignal.any 不可用：构造合并 controller 手动转发任一 abort
      if (typeof AbortController !== "undefined") {
        const merged = new AbortController();
        const onTimeout = () => merged.abort(timeoutSignal.reason);
        const onUserAbort = () => merged.abort(userSignal.reason);
        if (timeoutSignal.aborted) {
          merged.abort(timeoutSignal.reason);
        } else {
          timeoutSignal.addEventListener("abort", onTimeout, { once: true });
        }
        if (userSignal.aborted) {
          merged.abort(userSignal.reason);
        } else {
          userSignal.addEventListener("abort", onUserAbort, { once: true });
        }
        return merged.signal;
      }
      // 极端兜底：放弃 userSignal，至少保住 timeout
      return timeoutSignal;
    }

    // 老浏览器降级：用 AbortController + setTimeout 兜底
    if (typeof AbortController !== "undefined") {
      const controller = new AbortController();
      const timer = setTimeout(() => {
        try {
          const TimeoutErrorCtor = typeof DOMException !== "undefined" ? DOMException : Error;
          controller.abort(new TimeoutErrorCtor("Timeout", "TimeoutError"));
        } catch (_err) {
          controller.abort();
        }
      }, timeoutValue);
      if (userSignal) {
        if (userSignal.aborted) {
          clearTimeout(timer);
          controller.abort(userSignal.reason);
        } else {
          userSignal.addEventListener(
            "abort",
            () => {
              clearTimeout(timer);
              controller.abort(userSignal.reason);
            },
            { once: true },
          );
        }
      }
      return controller.signal;
    }

    // 极端兜底：无 AbortController（理论不应出现）
    return userSignal;
  };

  const isTimeoutError = (error) => {
    if (!error) return false;
    if (error.name === "TimeoutError") return true;
    return error.name === "AbortError" && /timeout/i.test(String(error.message || ""));
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
      signal,
      timeoutMs,
    } = {}
  ) => {
    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body,
        signal: buildTimeoutSignal(signal, timeoutMs),
      });
    } catch (error) {
      if (isTimeoutError(error)) {
        throw new ApiRequestError(buildActionFailureMessage(action, "请求超时"), {
          code: "request_timeout",
          reason: "请求超时",
        });
      }
      throw new ApiRequestError(buildNetworkErrorMessage(action, error), {
        reason: error instanceof Error ? String(error.message || "").trim() : "",
      });
    }

    const payload = await parseJsonSafe(response);
    const { code, reason, details } = readApiError(payload);

    if (!response.ok) {
      const detailReason = buildDetailReason(details);
      const finalReason = detailReason || reason || buildFallbackReason(response.status);
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
