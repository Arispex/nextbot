# red_packet_all 标题改「红包列表」+ 每条加「抢红包」引导

## Goal

修两处 `server/templates/red_packet_all.html`：
1. 大标题 `当前红包` → `红包列表`
2. 每个红包卡片的顶行（entry-top）追加引导文本 `抢红包 <红包名称>`（mirror shop_list 的 `查看商店 <id>` / lottery_list 的 `查看奖池 <id>` 模式）

## Reference

- `server/templates/shop_list.html` L217-220 — `.entry-cmd` cmdHint 模式（`查看商店 ${entry.shop_id}`）
- `server/templates/lottery_list.html` L239 — 同款（`查看奖池 ${entry.pool_id}`）
- `抢红包` 命令在 `nextbot/plugins/red_packet.py:40` `on_command("抢红包")`，接收 name 参数（L271 `parse_command_args_with_fallback(event, arg, "抢红包")`，正文 `抢红包 <name>` 即可参与，见 L254 hint）

## Changes（`server/templates/red_packet_all.html`）

### 1. 标题（L220 JS）

```js
// 修改前
document.getElementById("header-title").textContent = "当前红包";

// 修改后
document.getElementById("header-title").textContent = "红包列表";
```

### 2. 添加 .entry-cmd CSS

从 `shop_list.html` 复制 `.entry-cmd` 规则进 `<style>` block（位置放在 `.entry-name` 附近）。

### 3. 添加 cmdHint DOM + JS（在 typePill 后）

在现有 typePill `top.appendChild(typePill)` 之后追加：
```js
const cmdHint = document.createElement("span");
cmdHint.className = "entry-cmd";
cmdHint.textContent = `抢红包 ${name}`;
top.appendChild(cmdHint);
```

`name` 已是当前作用域变量（红包名称，payload 字段 `name`）。

## Out of Scope

- 不改 page 模块 / business logic / 其他模板
- 不改 entry 卡片其他段（avatar / sender / stats）
- 不动其他文案

## Acceptance Criteria

- [ ] 标题字符串变 `红包列表`
- [ ] 每个 entry 卡片顶行末尾出现 `抢红包 <红包名称>` 文本
- [ ] `.entry-cmd` CSS 存在
- [ ] 旧的 `当前红包` 字符串无残留
- [ ] HTML parse 通过
- [ ] `git diff --name-only` 仅 `server/templates/red_packet_all.html`
