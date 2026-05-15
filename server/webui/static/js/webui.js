(() => {
  const root = document.documentElement;
  const sidebar = document.getElementById("webui-sidebar");
  const sidebarOverlay = document.getElementById("sidebar-overlay");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const themeToggle = document.getElementById("theme-toggle");
  const logoutButton = document.getElementById("logout-btn");
  const api = window.NextBotWebUIApi;
  const sidebarLinks = sidebar ? sidebar.querySelectorAll("a[href]") : [];
  const sidebarStateKey = "nextbot-webui-sidebar-collapsed";
  const themeKey = "nextbot-webui-theme";
  const mobileMedia = window.matchMedia("(max-width: 840px)");
  const darkSchemeMedia =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;

  // L-9：轻量统一日志入口；4 处 catch 与 logout 失败可挂上报 hook。
  const log = {
    warn(scope, message, error) {
      try {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("[webui]", scope, message, error);
        }
      } catch (_inner) {
        // ignore
      }
    },
  };

  let desktopCollapsed = false;
  let mobileOpen = false;
  let mobileMode = mobileMedia.matches;

  const setExpanded = (expanded) => {
    if (sidebarToggle) {
      sidebarToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    }
  };

  const setMobileOpen = (next) => {
    mobileOpen = next;
    applySidebarState();
  };

  const setSidebarHidden = (hidden) => {
    if (!sidebar) {
      return;
    }
    if (hidden) {
      sidebar.setAttribute("inert", "");
      sidebar.setAttribute("aria-hidden", "true");
    } else {
      sidebar.removeAttribute("inert");
      sidebar.setAttribute("aria-hidden", "false");
    }
  };

  const applySidebarState = () => {
    if (!sidebar) {
      return;
    }

    mobileMode = mobileMedia.matches;

    if (mobileMode) {
      sidebar.classList.remove("is-collapsed");
      sidebar.classList.toggle("is-mobile-open", mobileOpen);
      if (sidebarOverlay) {
        sidebarOverlay.classList.toggle("is-visible", mobileOpen);
        sidebarOverlay.setAttribute("aria-hidden", mobileOpen ? "false" : "true");
      }
      document.body.classList.toggle("sidebar-open-lock", mobileOpen);
      setExpanded(mobileOpen);
      setSidebarHidden(!mobileOpen);
      if (sidebarToggle) {
        sidebarToggle.setAttribute("aria-label", mobileOpen ? "关闭导航菜单" : "打开导航菜单");
      }
      return;
    }

    mobileOpen = false;
    sidebar.classList.remove("is-mobile-open");
    sidebar.classList.toggle("is-collapsed", desktopCollapsed);
    if (sidebarOverlay) {
      sidebarOverlay.classList.remove("is-visible");
      sidebarOverlay.setAttribute("aria-hidden", "true");
    }
    document.body.classList.remove("sidebar-open-lock");
    setExpanded(!desktopCollapsed);
    setSidebarHidden(desktopCollapsed);
    if (sidebarToggle) {
      sidebarToggle.setAttribute("aria-label", desktopCollapsed ? "展开侧边栏" : "隐藏侧边栏");
    }
  };

  const syncThemeButton = () => {
    if (!themeToggle) {
      return;
    }
    const dark = root.classList.contains("dark");
    themeToggle.setAttribute("aria-label", dark ? "切换到浅色主题" : "切换到深色主题");
  };

  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", () => {
      if (mobileMedia.matches) {
        setMobileOpen(!mobileOpen);
        return;
      }

      desktopCollapsed = !desktopCollapsed;
      applySidebarState();
      try {
        localStorage.setItem(sidebarStateKey, desktopCollapsed ? "1" : "0");
      } catch (error) {
        log.warn("sidebar-state", "保存折叠状态失败", error);
      }
    });
  }

  if (sidebarOverlay) {
    sidebarOverlay.addEventListener("click", () => {
      if (mobileMedia.matches) {
        setMobileOpen(false);
      }
    });
  }

  if (sidebarLinks.length > 0) {
    sidebarLinks.forEach((link) => {
      // L-5：如果某 link 调用 preventDefault（未来 keyboard nav / dropdown 等），保持 sidebar 状态不变。
      link.addEventListener("click", (event) => {
        if (event.defaultPrevented) {
          return;
        }
        if (mobileMedia.matches) {
          setMobileOpen(false);
        }
      });
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mobileMedia.matches && mobileOpen) {
      setMobileOpen(false);
    }
  });

  // L-7：所有目标浏览器均支持 MediaQueryList.addEventListener，移除老 API 兜底。
  mobileMedia.addEventListener("change", () => {
    applySidebarState();
  });

  // M-3：仅在用户从未手动选过主题时跟随系统切换；一旦点过 toggle（localStorage 写入）即停止跟随。
  const hasManualThemePreference = () => {
    try {
      const stored = localStorage.getItem(themeKey);
      return stored === "dark" || stored === "light";
    } catch (error) {
      log.warn("theme-state", "读取主题偏好失败", error);
      return false;
    }
  };

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const dark = root.classList.toggle("dark");
      try {
        localStorage.setItem(themeKey, dark ? "dark" : "light");
      } catch (error) {
        log.warn("theme-state", "保存主题偏好失败", error);
      }
      syncThemeButton();
    });
  }

  if (darkSchemeMedia && darkSchemeMedia.addEventListener) {
    darkSchemeMedia.addEventListener("change", (event) => {
      if (hasManualThemePreference()) {
        return;
      }
      root.classList.toggle("dark", !!event.matches);
      syncThemeButton();
    });
  }

  if (logoutButton && api) {
    const logoutModal = document.getElementById("logout-confirm-modal");
    const logoutConfirmBtn = document.getElementById("logout-confirm-confirm-btn");
    const logoutCancelBtn = document.getElementById("logout-confirm-cancel-btn");
    const logoutCloseBtn = document.getElementById("logout-confirm-close-btn");
    let logoutPreviousFocus = null;
    let logoutPreviousBodyOverflow = "";

    const performLogout = async () => {
      logoutButton.disabled = true;
      let succeeded = false;
      let failureReason = "";
      try {
        // M-2：缩短 logout 超时至 5s，避免用户卡到默认 15s。
        await api.apiRequest("/webui/api/session", {
          method: "DELETE",
          action: "退出",
          expectedStatus: 204,
          timeoutMs: 5000,
        });
        succeeded = true;
      } catch (error) {
        // M-2：失败原样透传 reason，不再静默吞错。
        failureReason =
          (error && typeof error === "object" && typeof error.reason === "string" && error.reason) ||
          (error && typeof error === "object" && typeof error.message === "string" && error.message) ||
          "";
        log.warn("logout", "退出请求失败", error);
      }

      if (succeeded) {
        // 仅在确认 logout 成功时跳转登录页；不恢复 disabled，避免页面卸载前重复点击。
        window.location.assign("/webui/login");
        return;
      }

      // M-2：失败时不再静默跳转。展示原始原因（保留 API error.message），由用户决定下一步。
      try {
        if (typeof window.alert === "function") {
          window.alert(failureReason ? `退出失败，${failureReason}` : "退出失败");
        }
      } catch (alertError) {
        log.warn("logout", "弹出失败提示异常", alertError);
      }
      logoutButton.disabled = false;
    };

    const isLogoutModalOpen = () => !!(logoutModal && !logoutModal.classList.contains("hidden"));

    const openLogoutModal = () => {
      if (!logoutModal) {
        // Shell 未注入 modal（理论不会发生）— 静默忽略点击，避免无确认直接登出。
        log.warn("logout-modal", "logout-confirm-modal 节点缺失", null);
        return;
      }
      logoutPreviousFocus =
        document.activeElement && document.activeElement !== document.body
          ? document.activeElement
          : null;
      logoutPreviousBodyOverflow = document.body.style.overflow;
      logoutModal.classList.remove("hidden");
      document.body.style.overflow = "hidden";
      if (logoutCancelBtn) {
        try {
          logoutCancelBtn.focus();
        } catch (focusError) {
          log.warn("logout-modal", "focus 取消按钮失败", focusError);
        }
      }
    };

    const closeLogoutModal = () => {
      if (!logoutModal) {
        return;
      }
      logoutModal.classList.add("hidden");
      document.body.style.overflow = logoutPreviousBodyOverflow || "";
      const fallback = logoutButton;
      const target = logoutPreviousFocus && document.contains(logoutPreviousFocus)
        ? logoutPreviousFocus
        : fallback;
      if (target && typeof target.focus === "function") {
        try {
          target.focus();
        } catch (focusError) {
          log.warn("logout-modal", "还原焦点失败", focusError);
        }
      }
      logoutPreviousFocus = null;
    };

    logoutButton.addEventListener("click", () => {
      openLogoutModal();
    });

    if (logoutCancelBtn) {
      logoutCancelBtn.addEventListener("click", () => {
        closeLogoutModal();
      });
    }

    if (logoutCloseBtn) {
      logoutCloseBtn.addEventListener("click", () => {
        closeLogoutModal();
      });
    }

    if (logoutModal) {
      logoutModal.querySelectorAll("[data-logout-confirm-close]").forEach((node) => {
        node.addEventListener("click", () => {
          closeLogoutModal();
        });
      });
    }

    if (logoutConfirmBtn) {
      logoutConfirmBtn.addEventListener("click", async () => {
        closeLogoutModal();
        await performLogout();
      });
    }

    // ESC 仅在 logout modal 打开时拦截；其它 page 的 modal-stack 不受影响。
    window.addEventListener("keydown", (event) => {
      if (event.defaultPrevented) {
        return;
      }
      if (event.key !== "Escape") {
        return;
      }
      if (!isLogoutModalOpen()) {
        return;
      }
      event.preventDefault();
      closeLogoutModal();
    });
  }

  try {
    desktopCollapsed = localStorage.getItem(sidebarStateKey) === "1";
  } catch (error) {
    desktopCollapsed = false;
    log.warn("sidebar-state", "读取折叠状态失败", error);
  }

  applySidebarState();
  syncThemeButton();
})();
