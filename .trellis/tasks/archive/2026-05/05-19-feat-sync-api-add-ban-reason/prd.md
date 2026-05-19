# sync API users[] 加 ban_reason 字段

## Goal

`GET /webui/api/sync/snapshot` 响应里每个 user 多输出一个 `ban_reason` 字段，让 C# 插件能拿到封禁原因（用于客户端断开提示 / TShock 黑名单 reason 同步）。

## 现状

`server/routes/webui_sync.py`:
- 查询字段：`User.name, User.is_banned, User.password_hash`
- ETag 公式：sorted([{name, banned, password_hash or ""}]) → sha256
- 响应 users[] 字段：`name / banned / password_hash`

数据库：`User.ban_reason: str = ""`（已存在，nullable=False, default=""）

## 目标

### 响应 schema 增加 `ban_reason`

```json
{
  "users": [
    {
      "name": "user1",
      "banned": false,
      "password_hash": "$2a$07$...",
      "ban_reason": ""
    },
    {
      "name": "banned1",
      "banned": true,
      "password_hash": "$2a$07$...",
      "ban_reason": "外挂"
    }
  ]
}
```

### ETag 算法更新

加入 `ban_reason` 进 canonical state：

```python
canonical = json.dumps(
    sorted([
        {
            "name": ...,
            "banned": ...,
            "password_hash": ...,
            "ban_reason": ...,  # 新增
        }
        for ...
    ], key=lambda x: x["name"]),
    ensure_ascii=False, separators=(",", ":"),
)
```

→ 所有现有 C# client 下次 poll 时 ETag 不匹配 → 拿到全量更新一次（可接受的一次性冲击）。

## Acceptance Criteria

- [ ] `webui_sync.py` SQL 查询加 `User.ban_reason`
- [ ] payload 构建加 `"ban_reason": row.ban_reason or ""`
- [ ] ETag 公式加入 ban_reason
- [ ] `python3 -m py_compile server/routes/webui_sync.py` 通过
- [ ] 与 `users[].banned` 字段 NULL 处理一致（ban_reason 是 NOT NULL default ""，但仍 `or ""` 防御）

## Out of Scope

- 不动其他 endpoint
- 不动 `/webui/api/users` 等 REST endpoint 的字段（视情况另开）
- 不通知 C# 插件 schema 变更（自然下次 ETag 不匹配会让 C# 拿到新字段，忽略未知字段是常规做法）

## Edge Cases

| 场景 | 行为 |
|---|---|
| ban_reason 为空字符串 | 输出 `"ban_reason": ""`；不省略 |
| ban_reason 含 emoji / 中文 | utf-8 编码，ETag / JSON 都稳定 |
| 旧 C# 客户端读到新字段 | 应忽略未知字段（标准 JSON 反序列化习惯）|

## Technical Notes

- 文档（`docs/webui_sync_api.md` 之前已删，无需更新）
- ETag 公式变更 = 所有 C# poll client 下次 304 不命中 → 200 全量响应一次 → 资源开销可忽略
