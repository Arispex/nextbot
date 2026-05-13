# fix: 菜单命令截图样式恢复为之前的宽版布局

## Goal

回滚 commit `e7f9ae9`（plugin audit round 的 MI-3.1）在 `nextbot/plugins/menu.py` 的 viewport_width 改动，恢复用户体验上"之前的宽版样式"。

## 根因

**Commit `e7f9ae9`**（5 月 9 日 plugin audit）的 MI-3.1：
```diff
+# MI-3.1：viewport_width 与项目其他截图统一为 920（之前 1920，OOM 风险更高）
 MENU_SCREENSHOT_OPTIONS = ScreenshotOptions(
-    viewport_width=1920,
+    viewport_width=920,
     viewport_height=1280,
     full_page=True,
     fit_content_height=True,
 )
```

- **920 太窄** → 菜单卡片 / 命令描述 / usage 字符串频繁自动换行 → 用户体感"变窄了"
- **1920 实际无 OOM 风险**：菜单是 trusted 内部模板（命令列表是项目自身静态数据），单图 PNG ~几百 KB，远低于下游 cap

## 已有的 OOM 防线（3 道，足够安全）

1. `_menu_semaphore = asyncio.Semaphore(2)` 限制并发（`menu.py:49`）
2. `screenshot_render.py:118` 编码前 `file_size * 4 // 3 > MAX_BASE64_BYTES` 预估校验
3. `screenshot_render.py:132` 编码后 `len(encoded) > MAX_BASE64_BYTES` 复检

`MAX_BASE64_BYTES = 200 * 1024 * 1024`（200MB），1920×1280 菜单图实际产物远小于此。**MI-3.1 的"OOM 风险"担忧为过度防御**。

## Requirements

1. `nextbot/plugins/menu.py:42` `viewport_width=920` 改回 `1920`
2. 更新注释，说明"MI-3.1 已被回滚：菜单是 trusted 模板，宽度限制无必要，下游 cap + semaphore 已足够"
3. 保留 viewport_height=1280 / full_page=True / fit_content_height=True 不动（这三项与样式问题无关）

## Out of Scope

- 不动其他 plugin 的截图 viewport（leaderboard / lottery / red_packet 等）：用户**只反馈**菜单，其他暂未提
- 不动 `MAX_BASE64_BYTES` / semaphore 等下游 cap：本轮只回滚单 plugin 的宽度
- 不动模板 CSS（menu.html）：宽度足够后换行问题自然解决

## Acceptance Criteria

- [ ] `menu.py:42` `viewport_width=1920`
- [ ] 注释更新解释回滚原因
- [ ] `python3 -m py_compile nextbot/plugins/menu.py` OK
- [ ] 实际"菜单 <分类>"截图视觉宽度恢复到 audit 前样貌（人工验证）

## Technical Notes

- 回滚单点：`menu.py:42` 一行
- 关联但不修改：`screenshot_render.py` / `large_image.py` / `menu.html`
