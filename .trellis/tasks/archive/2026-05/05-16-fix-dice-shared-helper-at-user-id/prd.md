# fix(dice): 标题去「结果」+ 发图时艾特用户

## 改动

### 1) 标题文案

`server/templates/dice.html` 内 `<h1 class="header-title type-display-lg">掷骰子结果</h1>` → `掷骰子`。

### 2) 发图时艾特用户

V11 平台当前 `render_and_send_screenshot` 只发 image segment，不带 @。用户希望像文字回复一样 `@用户 [图片]` 一条消息发出。

#### 改 `nextbot/screenshot_render.py:render_and_send_screenshot`

加可选参数 `at_user_id: str | None = None`：
- V11 路径（line 138）：原 `await bot.send(event, OBV11MessageSegment.image(file=f"base64://{encoded}"))` 改为 — 若 `at_user_id` 非空，构造 `OBV11MessageSegment.at(at_user_id) + OBV11MessageSegment.text(" ") + OBV11MessageSegment.image(...)` 一条消息；否则仍发 image 单段
- 非 V11 fallback：在 head 字符串前 prepend `"@<at_user_id> "` 占位（adapter 自决渲染）

`_render_and_send_inner` 同步加 `at_user_id` 参数透传。

### 3) 调用方

`nextbot/plugins/dice.py` 的 `render_and_send_screenshot(...)` 调用追加 `at_user_id=user_id`。

## Scope

3 个文件：
- `server/templates/dice.html`
- `nextbot/screenshot_render.py`
- `nextbot/plugins/dice.py`

## Acceptance

- 截图标题显示「掷骰子」（不带"结果"）
- V11 QQ 群里看到 `@玩家 [图片]` 一条消息
- 其它已用 `render_and_send_screenshot` 的命令行为不变（at_user_id 默认 None）
- `python3 -m py_compile` 三个文件全过

## DO NOT

- 不改 dice_page.py / web_server.py / render.py
- 不动 其它 render 命令的调用方
- 不引外部依赖
- 不 commit
