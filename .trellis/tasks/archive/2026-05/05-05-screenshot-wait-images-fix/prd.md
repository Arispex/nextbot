# 截图等待动态资源回归修复

## Goal

`21822f7` 把 `wait_until` 从 `networkidle` 换成 `load` + `document.fonts.ready` 之后，所有依赖 JS 动态加载图片的渲染页面（背包、商店、抽奖结果、封禁/管理员列表、进度、红包、教程等 11 个模板）都偶发 sprite 不显示的 bug：浏览器手动打开临时链接看是好的，截图里却只看到数量、看不到图标。

根因是 `load` 只覆盖 HTML 同步资源，JS `fetch` 之后 `createElement('img')` 添加进 DOM 的 sprite 不在等待范围；`document.fonts.ready` 只管字体。旧的 `networkidle` 顺手把整条链都等了，新代码把这个隐式行为弄丢了。

## Requirements

修改 `server/screenshot.py` 的 `screenshot_url` 主流程，在 `goto` 后、screenshot 前新增两道 wait：

1. **有上限的 networkidle**：`await page.wait_for_load_state("networkidle", timeout=5000)`，**用 `contextlib.suppress(PlaywrightTimeoutError)` 容忍超时**。覆盖正常路径下的所有动态网络请求。
2. **DOM 内 `<img>` 兜底**：`page.evaluate` 跑一段 JS，等当前 DOM 中所有 `<img>` 的 `complete && naturalHeight > 0`；对未完成的挂 `load` / `error` 监听器（**`error` 也要 resolve**，否则坏图会把整个 promise 卡死）。

顺序：`goto(load)` → `wait_for_load_state(networkidle, 5s, suppress)` → DOM img wait → `document.fonts.ready` → screenshot

## Acceptance Criteria

- [ ] 我的背包命令的截图 sprite 不再丢失
- [ ] 其他 10 个动态模板（商店/抽奖结果/封禁列表/管理员列表/进度/红包/教程/介绍/抽奖查看/物品栏）截图视觉无 regression
- [ ] 静态模板（如菜单 menu.html）截图速度无明显劣化（5s networkidle 应几乎立即 fire，DOM img wait 对零图页面是 no-op）
- [ ] 长尾 networkidle 不再 throw timeout（`suppress(PlaywrightTimeoutError)` 兜住）
- [ ] 时延上限：最坏情况 30s + 5s = 35s，与原来 30s 上限基本相当
- [ ] 单例 browser、retry、lifecycle hook 等其他逻辑零改动

## Definition of Done

- 现有 12 个 `screenshot_url` 调用方零改动
- 不引入新依赖
- 错误信息覆盖三段：导航 / 字体 / 采集（`wait_for_load_state` 内部超时被 suppress，不算第四阶段）

## Technical Approach

`server/screenshot.py:131-180` 区段在 `page.goto` 之后、`document.fonts.ready` 之前插入：

```python
# Plan A 的 load 只等 HTML 同步资源；JS fetch 后动态 createElement('img') 的 sprite
# 不在等待范围。下面两道补丁覆盖动态资源场景，避免回退到不稳定的 networkidle。
with contextlib.suppress(PlaywrightTimeoutError):
    await page.wait_for_load_state("networkidle", timeout=5000)
await page.evaluate("""
  () => Promise.all(
    Array.from(document.images).map(img =>
      img.complete && img.naturalHeight > 0
        ? Promise.resolve()
        : new Promise(resolve => {
            img.addEventListener('load',  resolve, { once: true });
            img.addEventListener('error', resolve, { once: true });
          })
    )
  )
""")
```

接在原 `document.fonts.ready` 之前。`contextlib` 已 import。

## Decision (ADR-lite)

**Context**：sprite 丢失需要补一道动态资源等待，但不能回退到全局 `wait_until="networkidle"`（会重新引入 15s timeout 问题）。

**Decision**：分两段——优先用 5s 上限的 `wait_for_load_state("networkidle")`，不抛错；兜底跑 `document.images` 全员 promise.all，对坏图也 resolve 防卡死。

**Consequences**：
- 正常页面：networkidle 几秒内 fire，DOM img wait 对一个 already-complete 集合执行近似 no-op
- 长尾页面：networkidle 5s 超时被吞，DOM img wait 兜住所有当前已添加进 DOM 的 img
- 边界：fetch 还没回来 → DOM 里无 img → 兜底失效。但此时 5s networkidle 大概率已经覆盖了 fetch（fetch 本身就属于 network 流量，会让 networkidle 推迟到 fetch 完成 + img 加载完成才 fire）

## Out of Scope

- 不改 `wait_until` 默认值（保持 `load`）
- 不改 `timeout_ms` 默认值（保持 30000）
- 不改 retry / singleton / lifecycle 逻辑
- 不修改任何 template 模板
- 不改调用方

## Technical Notes

- 受影响模板（11 个动态加载图片）：`server/templates/{warehouse,inventory,about,admin_list,ban_list,lottery_view,progress,lottery_result,red_packet_all,shop_view,tutorial}.html`
- 现有 screenshot.py：`server/screenshot.py:131-180`
- `contextlib` 已在文件头 import
- `PlaywrightTimeoutError` 已在文件头 import
- Reference benchmark: 原方案在 localhost 测得 5 并发 0.40s wall（已归档于 `05-04-screenshot-perf-fix`）。新增两道 wait 在静态页几乎零开销，动态页加 sprite 真实加载时间。
