(() => {
  const reloadButton = document.getElementById("reload-btn");
  const addRuleButton = document.getElementById("add-rule-btn");
  const searchInput = document.getElementById("autoreply-search");

  const statusNode = document.getElementById("status");
  const statusMessageNode = document.getElementById("status-message");
  const loadingNode = document.getElementById("loading");
  const emptyNode = document.getElementById("empty");
  const tableWrapNode = document.getElementById("table-wrap");
  const tableBodyNode = document.getElementById("autoreply-table-body");

  const modalNode = document.getElementById("autoreply-modal");
  const modalTitleNode = document.getElementById("autoreply-modal-title");
  const modalAlertNode = document.getElementById("modal-alert");
  const modalAlertMessageNode = document.getElementById("modal-alert-message");
  const modalCloseButton = document.getElementById("modal-close-btn");
  const modalCancelButton = document.getElementById("modal-cancel-btn");
  const modalSaveButton = document.getElementById("modal-save-btn");

  const fieldKeyword = document.getElementById("field-keyword");
  const fieldReply = document.getElementById("field-reply");
  const fieldAtUser = document.getElementById("field-at-user");
  const fieldQuoteReply = document.getElementById("field-quote-reply");
  const fieldEnabled = document.getElementById("field-enabled");

  const deleteModalNode = document.getElementById("delete-modal");
  const deleteModalTextNode = document.getElementById("delete-modal-text");
  const deleteModalAlertNode = document.getElementById("delete-modal-alert");
  const deleteModalAlertMessageNode = document.getElementById("delete-modal-alert-message");
  const deleteModalCloseButton = document.getElementById("delete-modal-close-btn");
  const deleteModalCancelButton = document.getElementById("delete-modal-cancel-btn");
  const deleteModalConfirmButton = document.getElementById("delete-modal-confirm-btn");

  const requiredNodesReady = Boolean(
    reloadButton &&
      addRuleButton &&
      searchInput &&
      statusNode &&
      statusMessageNode &&
      loadingNode &&
      emptyNode &&
      tableWrapNode &&
      tableBodyNode &&
      modalNode &&
      modalTitleNode &&
      modalAlertNode &&
      modalAlertMessageNode &&
      modalCloseButton &&
      modalCancelButton &&
      modalSaveButton &&
      fieldKeyword &&
      fieldReply &&
      fieldAtUser &&
      fieldQuoteReply &&
      fieldEnabled &&
      deleteModalNode &&
      deleteModalTextNode &&
      deleteModalAlertNode &&
      deleteModalAlertMessageNode &&
      deleteModalCloseButton &&
      deleteModalCancelButton &&
      deleteModalConfirmButton
  );
  if (!requiredNodesReady) {
    return;
  }

  const api = window.NextBotWebUIApi;
  const KEYWORD_MAX_LEN = 50;
  const REPLY_MAX_LEN = 500;
  const REPLY_PREVIEW_LEN = 60;
  const SEARCH_DEBOUNCE_MS = 200;

  let ruleStates = [];
  let modalMode = "create";
  let editingRuleId = null;
  let modalSaving = false;
  let deletingRule = null;
  let deleteSaving = false;
  let reloadInFlight = false;
  let searchDebounceTimer = null;

  // modal stack / focus trap（与 users.js 一致）
  const modalStack = [];
  const modalCloseRegistry = new WeakMap();
  const modalPreviousFocus = new WeakMap();
  const modalTrapHandlers = new WeakMap();
  let bodyOverflowBeforeModal = null;

  const clearChildren = (node) => {
    if (!node) return;
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  };

  const getFocusableInModal = (node) => {
    if (!node) return [];
    return Array.from(
      node.querySelectorAll(
        'a[href]:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"]):not([disabled])'
      )
    ).filter((el) => !el.classList.contains("hidden"));
  };

  const buildTrapFocusHandler = (node) => (event) => {
    if (event.key !== "Tab") return;
    const focusables = getFocusableInModal(node);
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

  const pushModalToStack = (node) => {
    if (!node) return;
    const idx = modalStack.lastIndexOf(node);
    if (idx >= 0) modalStack.splice(idx, 1);
    modalStack.push(node);
  };

  const popModalFromStack = (node) => {
    if (!node) return;
    const idx = modalStack.lastIndexOf(node);
    if (idx >= 0) modalStack.splice(idx, 1);
  };

  const registerModalCloser = (node, closer) => {
    if (!node || typeof closer !== "function") return;
    modalCloseRegistry.set(node, closer);
  };

  const lockBodyScroll = () => {
    if (modalStack.length !== 1) return;
    bodyOverflowBeforeModal = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  };

  const unlockBodyScroll = () => {
    if (modalStack.length > 0) return;
    document.body.style.overflow = bodyOverflowBeforeModal ?? "";
    bodyOverflowBeforeModal = null;
  };

  const openModalWithFocus = (node) => {
    if (!node) return;
    if (!node.classList.contains("hidden")) return;
    modalPreviousFocus.set(node, document.activeElement);
    node.classList.remove("hidden");
    const handler = buildTrapFocusHandler(node);
    modalTrapHandlers.set(node, handler);
    node.addEventListener("keydown", handler);
    pushModalToStack(node);
    lockBodyScroll();
    setTimeout(() => {
      const focusables = getFocusableInModal(node);
      const preferred =
        focusables.find((el) => !el.classList.contains("modal-close-btn")) ||
        focusables[0];
      if (preferred && typeof preferred.focus === "function") {
        preferred.focus();
      }
    }, 0);
  };

  const closeModalAndRestoreFocus = (node) => {
    if (!node) return;
    node.classList.add("hidden");
    const handler = modalTrapHandlers.get(node);
    if (handler) {
      node.removeEventListener("keydown", handler);
      modalTrapHandlers.delete(node);
    }
    const previousFocus = modalPreviousFocus.get(node);
    modalPreviousFocus.delete(node);
    popModalFromStack(node);
    unlockBodyScroll();
    if (
      previousFocus &&
      document.contains(previousFocus) &&
      typeof previousFocus.focus === "function"
    ) {
      try {
        previousFocus.focus({ preventScroll: true });
      } catch (_error) {
        previousFocus.focus();
      }
    }
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

  const normalizeRule = (item) => ({
    id: Number(item?.id || 0),
    keyword: String(item?.keyword || ""),
    reply: String(item?.reply || ""),
    enabled: Boolean(item?.enabled),
    at_user: Boolean(item?.at_user),
    quote_reply: Boolean(item?.quote_reply),
    created_at: String(item?.created_at || ""),
  });

  const truncate = (text, max) => {
    const value = String(text || "");
    if (value.length <= max) return value;
    return value.slice(0, max) + "…";
  };

  const buildInlineToggle = (rule, fieldName) => {
    const label = document.createElement("label");
    label.className = "inline-toggle";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(rule[fieldName]);
    input.dataset.ruleId = String(rule.id);
    input.dataset.field = fieldName;
    input.addEventListener("change", () => {
      void toggleField(rule, fieldName, input.checked, input);
    });
    label.appendChild(input);
    return label;
  };

  const toggleField = async (rule, fieldName, nextValue, inputEl) => {
    const originalValue = Boolean(rule[fieldName]);
    if (originalValue === Boolean(nextValue)) {
      return;
    }
    if (inputEl) inputEl.disabled = true;
    setStatus("更新中…", "info");
    try {
      const responsePayload = await api.apiRequest(
        `/webui/api/autoreply/${rule.id}`,
        {
          method: "PUT",
          headers: { Accept: "application/json" },
          body: JSON.stringify({ [fieldName]: Boolean(nextValue) }),
          action: "更新",
          expectedStatus: 200,
        }
      );
      const result = api.unwrapData(responsePayload);
      if (result && typeof result === "object") {
        const updated = normalizeRule(result);
        const idx = ruleStates.findIndex((item) => item.id === updated.id);
        if (idx >= 0) {
          ruleStates[idx] = updated;
        }
      } else {
        rule[fieldName] = Boolean(nextValue);
      }
      setStatus("更新成功", "success");
    } catch (error) {
      // 回滚 UI 状态
      if (inputEl) inputEl.checked = originalValue;
      const message = error instanceof Error ? error.message : "更新失败";
      setStatus(message, "error");
    } finally {
      if (inputEl) inputEl.disabled = false;
    }
  };

  const renderTable = () => {
    clearChildren(tableBodyNode);
    loadingNode.classList.add("hidden");

    const keyword = String(searchInput.value || "").trim().toLowerCase();
    const filtered = keyword
      ? ruleStates.filter(
          (rule) =>
            String(rule.keyword || "").toLowerCase().includes(keyword) ||
            String(rule.reply || "").toLowerCase().includes(keyword)
        )
      : ruleStates;

    if (!filtered.length) {
      emptyNode.textContent = ruleStates.length
        ? "未匹配到自动回复规则"
        : "暂无自动回复规则";
      emptyNode.classList.remove("hidden");
      tableWrapNode.classList.add("hidden");
      return;
    }

    emptyNode.classList.add("hidden");
    tableWrapNode.classList.remove("hidden");

    for (const rule of filtered) {
      const row = document.createElement("tr");
      row.dataset.ruleId = String(rule.id);

      const idCell = document.createElement("td");
      idCell.className = "id-cell";
      idCell.textContent = String(rule.id);

      const keywordCell = document.createElement("td");
      keywordCell.className = "keyword-cell";
      keywordCell.textContent = rule.keyword;

      const replyCell = document.createElement("td");
      replyCell.className = "reply-cell";
      replyCell.textContent = truncate(rule.reply, REPLY_PREVIEW_LEN);
      replyCell.title = rule.reply;

      const atCell = document.createElement("td");
      atCell.className = "toggle-cell";
      atCell.appendChild(buildInlineToggle(rule, "at_user"));

      const quoteCell = document.createElement("td");
      quoteCell.className = "toggle-cell";
      quoteCell.appendChild(buildInlineToggle(rule, "quote_reply"));

      const enabledCell = document.createElement("td");
      enabledCell.className = "toggle-cell";
      enabledCell.appendChild(buildInlineToggle(rule, "enabled"));

      const createdCell = document.createElement("td");
      createdCell.className = "created-cell";
      createdCell.textContent = rule.created_at || "-";

      const actionCell = document.createElement("td");
      actionCell.className = "actions-cell";
      const actions = document.createElement("div");
      actions.className = "row-actions";

      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.className = "btn action-btn";
      editButton.textContent = "编辑";
      editButton.addEventListener("click", () => {
        openModal("edit", rule);
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "btn action-btn action-btn-danger";
      deleteButton.textContent = "删除";
      deleteButton.addEventListener("click", () => {
        openDeleteModal(rule);
      });

      actions.appendChild(editButton);
      actions.appendChild(deleteButton);
      actionCell.appendChild(actions);

      row.appendChild(idCell);
      row.appendChild(keywordCell);
      row.appendChild(replyCell);
      row.appendChild(atCell);
      row.appendChild(quoteCell);
      row.appendChild(enabledCell);
      row.appendChild(createdCell);
      row.appendChild(actionCell);
      tableBodyNode.appendChild(row);
    }
  };

  const loadRules = async () => {
    loadingNode.classList.remove("hidden");
    tableWrapNode.classList.add("hidden");
    emptyNode.classList.add("hidden");
    try {
      const payload = await api.apiRequest("/webui/api/autoreply", {
        method: "GET",
        headers: { Accept: "application/json" },
        action: "加载",
        expectedStatus: 200,
      });
      const rules = api.unwrapData(payload);
      if (!Array.isArray(rules)) {
        throw new Error("加载失败，返回数据格式错误");
      }
      ruleStates = rules.map(normalizeRule);
      renderTable();
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "加载失败";
      setStatus(message, "error");
      loadingNode.classList.add("hidden");
      emptyNode.classList.remove("hidden");
      emptyNode.textContent = message;
      tableWrapNode.classList.add("hidden");
      return false;
    }
  };

  const openModal = (mode, rule = null) => {
    modalMode = mode;
    editingRuleId = mode === "edit" && rule ? rule.id : null;
    modalSaving = false;
    modalSaveButton.disabled = false;
    setModalAlert("");

    if (mode === "edit" && rule) {
      modalTitleNode.textContent = "编辑自动回复规则";
      modalSaveButton.textContent = "保存";
      fieldKeyword.value = rule.keyword || "";
      fieldReply.value = rule.reply || "";
      fieldAtUser.checked = Boolean(rule.at_user);
      fieldQuoteReply.checked = Boolean(rule.quote_reply);
      fieldEnabled.checked = Boolean(rule.enabled);
    } else {
      modalTitleNode.textContent = "创建自动回复规则";
      modalSaveButton.textContent = "创建";
      fieldKeyword.value = "";
      fieldReply.value = "";
      fieldAtUser.checked = true;
      fieldQuoteReply.checked = true;
      fieldEnabled.checked = true;
    }
    openModalWithFocus(modalNode);
  };

  const closeModal = () => {
    if (modalSaving) {
      return;
    }
    closeModalAndRestoreFocus(modalNode);
  };

  const buildPayloadFromModal = () => {
    const keyword = String(fieldKeyword.value || "").trim();
    const reply = String(fieldReply.value || "").trim();

    if (!keyword) {
      throw new Error("关键词不能为空");
    }
    if (keyword.length > KEYWORD_MAX_LEN) {
      throw new Error(`关键词过长，最多 ${KEYWORD_MAX_LEN} 个字符`);
    }
    if (!reply) {
      throw new Error("回复内容不能为空");
    }
    if (reply.length > REPLY_MAX_LEN) {
      throw new Error(`回复内容过长，最多 ${REPLY_MAX_LEN} 个字符`);
    }
    return {
      keyword,
      reply,
      enabled: Boolean(fieldEnabled.checked),
      at_user: Boolean(fieldAtUser.checked),
      quote_reply: Boolean(fieldQuoteReply.checked),
    };
  };

  const saveRule = async () => {
    if (modalSaving) {
      return;
    }
    const isEdit = modalMode === "edit" && typeof editingRuleId === "number";

    let payload;
    try {
      payload = buildPayloadFromModal();
    } catch (error) {
      const message = error instanceof Error ? error.message : "表单校验失败";
      setModalAlert(`${isEdit ? "更新失败" : "创建失败"}，${message}`, "error");
      return;
    }

    modalSaving = true;
    modalSaveButton.disabled = true;
    setModalAlert("保存中…", "info");

    try {
      const url = isEdit
        ? `/webui/api/autoreply/${editingRuleId}`
        : "/webui/api/autoreply";
      const method = isEdit ? "PUT" : "POST";
      await api.apiRequest(url, {
        method,
        headers: { Accept: "application/json" },
        body: JSON.stringify(payload),
        action: isEdit ? "更新" : "创建",
        expectedStatus: isEdit ? 200 : 201,
      });
      closeModalAndRestoreFocus(modalNode);
      setStatus(isEdit ? "更新成功" : "创建成功", "success");
      await loadRules();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : isEdit ? "更新失败" : "创建失败";
      setModalAlert(message, "error");
    } finally {
      modalSaving = false;
      modalSaveButton.disabled = false;
    }
  };

  const openDeleteModal = (rule) => {
    deletingRule = rule;
    deleteSaving = false;
    deleteModalConfirmButton.disabled = false;
    setDeleteModalAlert("");
    const preview = truncate(rule.keyword, 30);
    deleteModalTextNode.textContent = `确定删除关键词「${preview || "(空)"}」对应的自动回复规则吗？此操作不可恢复。`;
    openModalWithFocus(deleteModalNode);
  };

  const closeDeleteModal = (force = false) => {
    if (deleteSaving && !force) {
      return;
    }
    closeModalAndRestoreFocus(deleteModalNode);
    if (force || !deleteSaving) {
      deletingRule = null;
    }
  };

  const confirmDeleteRule = async () => {
    if (!deletingRule || deleteSaving) {
      return;
    }
    const target = deletingRule;
    deleteSaving = true;
    deleteModalConfirmButton.disabled = true;
    setDeleteModalAlert("删除中…", "warning");

    try {
      await api.apiRequest(`/webui/api/autoreply/${target.id}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
        action: "删除",
        expectedStatus: 204,
      });
      closeDeleteModal(true);
      setStatus("删除成功", "success");
      await loadRules();
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除失败";
      setDeleteModalAlert(message, "error");
      setStatus(message, "error");
    } finally {
      deleteSaving = false;
      deleteModalConfirmButton.disabled = false;
    }
  };

  reloadButton.addEventListener("click", async () => {
    if (reloadInFlight) return;
    reloadInFlight = true;
    reloadButton.disabled = true;
    try {
      await loadRules();
    } finally {
      reloadInFlight = false;
      reloadButton.disabled = false;
    }
  });

  addRuleButton.addEventListener("click", () => {
    openModal("create");
  });

  searchInput.addEventListener("input", () => {
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
    }
    searchDebounceTimer = setTimeout(() => {
      renderTable();
    }, SEARCH_DEBOUNCE_MS);
  });

  modalCloseButton.addEventListener("click", closeModal);
  modalCancelButton.addEventListener("click", closeModal);
  modalSaveButton.addEventListener("click", () => {
    void saveRule();
  });
  modalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.dataset.modalClose === "1") {
      closeModal();
    }
  });

  deleteModalCloseButton.addEventListener("click", () => closeDeleteModal());
  deleteModalCancelButton.addEventListener("click", () => closeDeleteModal());
  deleteModalConfirmButton.addEventListener("click", () => {
    void confirmDeleteRule();
  });
  deleteModalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.dataset.deleteModalClose === "1") {
      closeDeleteModal();
    }
  });

  registerModalCloser(modalNode, () => closeModal());
  registerModalCloser(deleteModalNode, () => closeDeleteModal());
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (modalStack.length === 0) return;
    const top = modalStack[modalStack.length - 1];
    if (!top || top.classList.contains("hidden")) return;
    const closer = modalCloseRegistry.get(top);
    if (typeof closer === "function") {
      closer();
    }
  });

  void loadRules();
})();
