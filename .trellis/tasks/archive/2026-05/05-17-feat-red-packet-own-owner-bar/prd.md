# red_packet_own header 加 avatar + 名字 + QQ owner bar

## Goal

让 `我的红包` 图片在标题 `我的红包` 下方显示 owner bar（QQ 头像 + 玩家名 + QQ 号），与 signin / warehouse / inventory / dice 等图片风格统一。

## 现状（`server/templates/red_packet_own.html`）

L185-195 header：
```html
<header>
  <div class="header-rule"></div>
  <h1 id="header-title" class="header-title type-display-lg"></h1>
  <div class="header-meta type-body-sm">
    <span id="header-count"></span>
    <span class="header-meta-divider">·</span>
    <span id="header-page"></span>
    <span class="header-meta-divider">·</span>
    <span id="header-generated-at"></span>
  </div>
</header>
```

payload schema (`red_packet_own_page.py`): 只有 `entries / page / total_pages / generated_at`，无 owner 信息。
handler (`red_packet.py:587` `handle_list_own`): user_id 在作用域，但 user_name 没查 DB。

## 目标

```html
<header>
  <div class="header-rule"></div>
  <h1 id="header-title" class="header-title type-display-lg"></h1>
  <div class="owner-bar">
    <img id="rpo-avatar" class="avatar" alt="avatar" />
    <div class="owner-meta type-body-sm">
      <span class="owner-name" id="rpo-owner-name"></span>
      <span class="owner-id" id="rpo-owner-id"></span>
      <span class="meta-divider">·</span>
      <span id="header-count"></span>
      <span class="meta-divider">·</span>
      <span id="header-page"></span>
      <span class="meta-divider">·</span>
      <span id="header-generated-at"></span>
    </div>
  </div>
</header>
```

→ `[avatar] 玩家名 (QQ) · N 个红包 · 第 X / Y 页 · 时间戳`

## Reference

- `server/templates/signin.html` L282-301 owner-bar 全套
- `server/templates/warehouse.html` 同款（pattern 源头）

## 全链路改动

### 1. `nextbot/plugins/red_packet.py` `handle_list_own`（L587+）

在 session block 内查 user.name：
```python
user_row = session.query(User).filter(User.user_id == user_id).first()
user_name = str(user_row.name) if user_row else ""
```

调用 page builder 时传递：
```python
page_url = create_red_packet_own_page(
    page=page,
    total_pages=total_pages,
    entries=entries,
    owner_user_id=user_id,
    owner_user_name=user_name,
)
```

注意 import `User` from `nextbot.db`（如未导入）。

### 2. `server/web_server.py:206` `create_red_packet_own_page`

```python
def create_red_packet_own_page(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
    owner_user_id: str,
    owner_user_name: str,
) -> str:
    payload = red_packet_own_page.build_payload(
        page=page,
        total_pages=total_pages,
        entries=entries,
        owner_user_id=owner_user_id,
        owner_user_name=owner_user_name,
    )
    return _make_page_url("red_packet_own", payload)
```

### 3. `server/pages/red_packet_own_page.py`

`build_payload(...)`：
- 加 `owner_user_id: str` + `owner_user_name: str` 两个 keyword 参数
- payload dict 加 `"owner_user_id": str(...).strip()` + `"owner_user_name": str(...).strip()`

`render(payload)`：
- `data` 字典里加这两个字段透传

### 4. `server/templates/red_packet_own.html`

**CSS** — 添加 warehouse 风格 owner bar 规则（从 signin.html 复制 6 个：`.owner-bar` / `.avatar` / `.owner-meta` / `.owner-name` / `.owner-id` / `.meta-divider`）：

```css
.owner-bar {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  color: var(--color-muted-soft);
}
.avatar {
  width: 48px; height: 48px;
  border-radius: var(--radius-pill);
  background-color: var(--color-surface-card);
  border: 1px solid var(--color-hairline);
  object-fit: cover;
  flex-shrink: 0;
}
.owner-meta {
  display: flex; gap: var(--space-md);
  align-items: baseline;
  flex-wrap: wrap;
  min-width: 0;
}
.owner-name { color: var(--color-ink); font-weight: 500; }
.owner-id { color: var(--color-muted); font-family: var(--font-code); }
.meta-divider { color: var(--color-hairline); }
```

**删除**老的 `.header-meta` / `.header-meta-divider` CSS（如果其他地方没用）。

注意：模板里另有 `.avatar` 在 entry 卡片里用（约 L92 附近，img class="avatar" 48x48）—— 复用同款规则，**不会冲突**（同款样式）。但要确认：如果两处尺寸 / 边框不同需要 scope 化（用更具体 selector）。

实际上：entry 卡片 avatar 已是 48px round + canvas bg + hairline border —— 与 owner-bar avatar 完全一致。**直接复用**，不需要 scope。但 entry 内部还有 `.avatar` 规则，如果是同款，复用即可；如果不同，新规则用 `.owner-bar .avatar` 限定。**implement 时检查并决定**。

**DOM** — 替换 header 块（L185-195）为新版（如上"目标"块）。

**JS** — 在数据绑定段添加：
```js
const ownerUserId = String(data.owner_user_id || "").trim();
const ownerUserName = String(data.owner_user_name || "").trim();

document.getElementById("rpo-avatar").src =
  `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(ownerUserId)}&s=100`;
document.getElementById("rpo-owner-name").textContent = ownerUserName || "未知玩家";
document.getElementById("rpo-owner-id").textContent = ownerUserId ? `(${ownerUserId})` : "";
```

`#header-count` / `#header-page` / `#header-generated-at` 的写入逻辑**保留不变**（id 移到 owner-meta 内，但 JS 仍能找到）。

## Out of Scope

- 不改 entry 卡片本体（`.entry` / 红包 item 列表展示）
- 不改 `empty-state` / `footer` / fallback
- 不改业务逻辑（DB 查询、分页计算、send_red_packet_image）
- 不改 `red_packet_all`（这次 scope 仅 own）

## Acceptance Criteria

- [ ] handler 查 user_name 并传给 page builder
- [ ] URL builder + page builder 都接收 owner_user_id / owner_user_name
- [ ] template DOM 用 owner-bar 模式
- [ ] template JS 设 avatar src + owner-name + owner-id
- [ ] `grep -n "header-meta\|header-meta-divider" server/templates/red_packet_own.html` → 0 matches
- [ ] `grep -n "owner-bar\|rpo-avatar\|rpo-owner-name\|rpo-owner-id" server/templates/red_packet_own.html` → 出现
- [ ] `grep -n "owner_user_id\|owner_user_name" server/templates/red_packet_own.html server/pages/red_packet_own_page.py server/web_server.py nextbot/plugins/red_packet.py` → 全链 4 文件都出现
- [ ] HTML parse 通过
- [ ] `python3 -m py_compile` 3 个 .py 通过

## Technical Notes

- avatar URL 用 `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(qq)}&s=100`，与系列其他模板一致
- prefix `rpo` 唯一（与 dice `dice-`, signin `signin-`, warehouse `wh-`, inventory `meta-user-` 等不冲突）
- 检查 entry 卡片 `.avatar` 与 header `.avatar` 是否 scope 冲突（agent 实施时决定 selector 粒度）
