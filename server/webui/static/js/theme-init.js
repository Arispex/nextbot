(function () {
  try {
    var stored = localStorage.getItem("nextbot-webui-theme");
    var shouldUseDark =
      stored === "dark" ||
      (stored !== "light" &&
        window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (shouldUseDark) {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  } catch (error) {
    // L-8：localStorage 异常（隐私模式 / quota / ITP）时仍尝试跟随系统 prefers-color-scheme，
    // 避免一律降级 light 导致 dark 系统用户看到日间主题闪烁。
    try {
      var prefersDark =
        window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.classList.toggle("dark", !!prefersDark);
    } catch (_inner) {
      document.documentElement.classList.remove("dark");
    }
  }
})();
