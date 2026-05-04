# 新增 NEXT BOT logo SVG 资源

## Goal

项目目前只有 `server/webui/static/img/logo__white_background_with_black_text.png` 这一种位图 logo。为了在不同尺寸（侧栏、favicon、菜单截图等）下保持清晰度并便于跟随主题色变化，新增一个矢量版 SVG。

## Requirements

* 在 `server/webui/static/img/logo.svg` 落一份 NEXT BOT wordmark SVG
* 几何风格、stroke 一笔成形（与原 PNG 视觉一致）
* `viewBox 880×200`、笔画粗细 22、字符高度 140、墨色 `#0d0c0a`
* 无依赖、纯单文件 SVG，可直接 `<img src="...">` 引用
* 不替换、不删除现有 PNG（只新增）

## Acceptance Criteria

* [ ] 文件存在于 `server/webui/static/img/logo.svg`
* [ ] 浏览器直接打开 SVG 能看到「NEXT BOT」字样且无解析错误
* [ ] SVG 内仅使用 path / ellipse / g 等基础元素，无外链字体/外链资源

## Definition of Done

* SVG 单文件落地
* 视觉与 PNG 近似可识别（B 凸起为弧线、O 为椭圆，与原图存在轻微几何差异，已与用户确认接受）

## Technical Approach

复用上一轮已与用户确认的 SVG 草稿：
- N / E / X / T / 第二个 T：纯 stroke 路径
- B：主竖 + 上下两段右半圆弧
- O：单椭圆

## Out of Scope

* 不替换现有 PNG 引用点
* 不做 favicon / apple-touch-icon 等衍生资产
* 不做暗色模式版本
* 不接入任何模板或样式表

## Technical Notes

* 原始 PNG 路径：`server/webui/static/img/logo__white_background_with_black_text.png`
* SVG 内容已在会话上轮明确：`viewBox="0 0 880 200"`、stroke 黑墨色 `#0d0c0a`
