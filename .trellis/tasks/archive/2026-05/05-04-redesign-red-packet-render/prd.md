# Redesign Red Packet Render

## Goal

按 DESIGN.md 把「红包列表」(red_packet_all) + 「我的红包」(red_packet_own) 两个截图重做成暖色编辑风：移除 dark theme 与传统红+金渐变，去掉外层 wrap card（去 Card 样式），启用 fit_content_height 自适应高度。这是第三轮"渲染页按 DESIGN.md 重构"——公共 design tokens / fonts CSS / fit_content_height 全部已就位。

## What I Already Know

**当前实现**：
- 命令：`红包列表` → `red_packet_all` 模板；`我的红包` → `red_packet_own` 模板
- 两模板都在 [server/templates/](../../../server/templates/)，各 ~290 行，单文件 + Tailwind CDN + 内联 CSS + 内联 JS
- 共享 `_RED_PACKET_SCREENSHOT_OPTIONS = ScreenshotOptions(900, 800, full_page=True)` 在 [nextbot/plugins/red_packet.py:45](../../../nextbot/plugins/red_packet.py)
- payload 都接受 `theme: str` 参数

**当前样式特征**（要替换的）：
- body 红橙渐变背景 + 大圆角白卡 page-header + list-wrap 圆角白卡套整组
- 每个 entry 左侧 4px 红→金渐变 stripe + 右侧 2.5rem 半透明大数字角标
- 红色品牌色（#dc2626）+ 金色（#eab308 / #f59e0b）渐变作为主色调
- "我的红包" 的进度条用红→橙渐变填充
- 头像外框（红包列表）用红+金渐变 ring
- "我的红包" 的红包图标 🧧 装饰

**两个页面差异**：
| | 红包列表 (all) | 我的红包 (own) |
|---|---|---|
| 主体 | 头像 + 红包名 + 类型徽章 + 发送者 + 剩余/总金额 + 剩余/总份数 | 红包图标 + 名 + 类型徽章 + 状态徽章 + 进度条 + 已抢/总额 + 份数 + 创建时间 |
| 状态 | 不显示（只有进行中的） | active/exhausted/withdrawn 三种语义色 |
| 排序 | 按 index | 按 index |

## Decisions Locked

按已成型的三轮模式直接套用，无开放问题：

- **Canvas-first 布局**：删 body 渐变、删 page-header 包卡、删 list-wrap 整组卡 — 用与菜单/用户信息一致的"canvas + 内容单元各自 cream-card"骨架
- **页面 header 用文本 hero 三件套**：珊瑚短规则线 + caption-uppercase eyebrow（"红包系统" / "红包管理"）+ Cormorant serif h1（"当前红包" / "我的红包"）— 这两页都没有视觉 hero，按之前定的"文本 hero 页保留 header-rule"原则处理
- **每个红包 entry 是独立 cream-card**：surface-card 底 + hairline 边 + rounded-lg + padding-lg + gap-md 间距
- **删除 dark theme 整段**：payload 保留兼容
- **删除装饰元素**：4px stripe 颜色条、半透明大数字角标 — 这些是装饰，不携带信息
- **删除头像 ring**（红包列表）：DESIGN.md 头像约定就是 `border: 1px solid hairline`
- **删除 🧧 emoji 装饰**（我的红包）：图标本身是装饰性，去掉后用 type-badge 已足够
- **数字字体 Inter sans + 600 + tnum**（剩余金额、剩余份数、已抢、份数）— 跟用户信息页同款理由：可读性
- **状态色映射**：
  - `active 进行中` → accent-teal `#5db8a6`（active/status 语义）
  - `exhausted 已抢完` → muted `#6c6a64`（中性/历史）
  - `withdrawn 已收回` → warning `#d4a017`（轻警示，DESIGN.md 的 warning 是琥珀色，跟"被收回"语义吻合）
- **类型徽章** 统一用 badge-pill（cream + ink + hairline）
- **进度条**（我的红包）：track 用 hairline，fill 用 accent-teal — 与签到热力图同色系
- **viewport 调到 920×600**，启用 `fit_content_height=True`（少红包不留空白，多红包完整截）
- **删除分页文字"第 X 页 / 共 X 页"独立显示** → 改成 header meta 行的一项（与菜单页 `8 个命令 · 时间` 同款风格）
- **删除底部"输入「抢红包 名称」参与"提示**（hint）：截图说明性文字属于教程内容，不是数据，移除让截图更聚焦数据本身
- **footer**：caption + muted-soft，沿用约定

## Requirements

- [ ] 整页 cream canvas，无外层 wrap card
- [ ] header 三件套（rule + eyebrow + serif title）
- [ ] header meta 显示 "N 个红包 · 第 X / Y 页 · 时间"
- [ ] 每个红包 entry 是独立 cream-card
- [ ] 数字（金额/份数）用 Inter sans 600 tnum
- [ ] 状态徽章按 active/exhausted/withdrawn 用对应语义色
- [ ] 进度条 hairline 轨 + accent-teal 填充
- [ ] 类型徽章统一 badge-pill
- [ ] 模板移除 `data-theme` 切换
- [ ] `_RED_PACKET_SCREENSHOT_OPTIONS` 启用 `fit_content_height=True`、viewport 920×600

## Acceptance Criteria

- [ ] 两个截图视觉符合 DESIGN.md 暖色编辑风
- [ ] 数字（金额、份数、进度）一眼可读
- [ ] 状态徽章颜色与语义匹配（进行中=teal / 已抢完=muted / 已收回=warning）
- [ ] 截图高度按红包数量自适应
- [ ] 命令链路 / payload schema 零改动；`theme` 字段被忽略但接受

## Out of Scope

- 其他渲染页（仓库 / 商店 / 抽奖 / 排行榜 / 教程 / 进度等）
- 红包功能本身（命令逻辑、分页算法）
- "发红包"命令的回执文本格式

## Definition of Done

- 修改限于 `server/templates/red_packet_all.html` + `red_packet_own.html` + `nextbot/plugins/red_packet.py` 的 `_RED_PACKET_SCREENSHOT_OPTIONS`
- 本地 Playwright 渲染验证：每页 0/1/N 条记录都能正确布局
- payload 兼容现有调用方
