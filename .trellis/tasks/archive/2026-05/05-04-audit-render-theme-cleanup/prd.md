# Audit unmigrated render templates + drop RENDER_THEME

## Goal

完成 warm-canvas 重构最后两个模板（`about.html`、`tutorial.html`），然后彻底删除 `RENDER_THEME` 环境变量及其所有依赖（render_utils、bot 启动、webui 设置页面）。

## What I already know

**未重构模板**（仍有 `data-theme="dark"` 或 Tailwind CDN）：
- `about.html` — 命令 `关于`，category=`系统功能`
- `tutorial.html` — 命令 `教程`，category=`系统功能`

**RENDER_THEME 引用清单**：
- `nextbot/render_utils.py` — `resolve_render_theme()` 函数定义
- `bot.py:51` — `.env` 初始化模板里 `"RENDER_THEME=auto\n"`
- `server/settings_service.py` — 4 处：FieldSpec、白名单、validation、default
- `server/webui/static/js/settings.js` — 4 处：label、value 提交、初始化、RENDER_THEME_LABELS 映射
- 12 个 plugin 文件中 `from nextbot.render_utils import resolve_render_theme` + `theme=resolve_render_theme()`

## Requirements

### Phase 1：重构最后 2 个模板
- `about.html` → warm-canvas：text-hero（coral rule + `系统功能` eyebrow + serif `关于` h1）+ logo + project info（cream-card + 4-tier label/value 排版）+ thanks grid（avatar cream-card + name + masked QQ）
- `tutorial.html` → warm-canvas：text-hero（coral rule + `系统功能` eyebrow + serif `data.title` h1）+ steps 列表 cream-card（带步骤数字 cream pill + 标题 + 描述 + 模拟对话气泡 + 提示框 4-tier 语义颜色）
- 两者均加 `[hidden] { display: none !important; }` 守卫
- 两者 ScreenshotOptions 增加 `fit_content_height=True`

### Phase 2：删除 RENDER_THEME
- 删除 `nextbot/render_utils.py` 中 `resolve_render_theme()` 函数（保留 `beijing_now` import 及其它工具函数若有）；如该文件只剩此函数则删除整个文件
- 删除 `bot.py:51` 行 `"RENDER_THEME=auto\n"`
- 删除 `server/settings_service.py` 中所有 `render_theme` 相关字段、白名单条目、validation 分支、default 处理
- 删除 `server/webui/static/js/settings.js` 中所有 render_theme 相关 UI（label、submit value、initialization、RENDER_THEME_LABELS）；简化设置页 DOM 中的 select 元素如有
- 12 个 plugin 文件：删除 `from nextbot.render_utils import resolve_render_theme` import + 删除 `theme=resolve_render_theme()` 参数（payload schema 默认 `theme="light"` 仍可保留向后兼容，或同时移除——决定保留默认值以最小破坏 payload schema）
- 检查 webui html 模板是否有对应 select 元素需要删除

## Acceptance Criteria

- [ ] 全 17 个 templates 都不含 `data-theme="dark"` 分支
- [ ] 全代码库无 `RENDER_THEME` / `render_theme` / `resolve_render_theme` 引用（除 trellis 历史 task / journal 不动）
- [ ] webui 设置页面不再有"图片主题"选项
- [ ] `.env` 初始化模板不写入 RENDER_THEME
- [ ] payload schema 中 `theme` 字段保留默认 light（不破坏 build_payload 调用兼容）
- [ ] about / tutorial 截图效果与已重构页面视觉一致

## Out of Scope

- 删除 payload schema 中 `theme` 字段本身（保留向后兼容，仅停止从环境变量取值）
- trellis 历史 task / workspace / journal 中的引用（不应触动归档历史）
- bot.py / settings_service.py 中其它无关字段
