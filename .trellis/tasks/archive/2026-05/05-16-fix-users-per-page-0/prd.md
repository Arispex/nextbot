# 修复 GET /webui/api/users?per_page=0 仍被 400 拒

## 现象

```
$ curl -i 'localhost:18081/webui/api/users?token=...&per_page=0'
HTTP/1.1 400 Bad Request
{"error":{"code":"invalid_query_parameter","message":"per_page 必须大于等于 1", ...}}
```

用户明确反馈：之前的"回退 per_page=0 全表通道"未生效。

## Root Cause

回退 commit `8d9546c fix(webui): 按用户偏好回退 3 项审计限制` 只改了 `webui_users.py` 内部的 cap 判断逻辑，但请求在到达 `webui_users_list` 之前先经过 `read_pagination_query` → `_parse_positive_int`（`server/routes/__init__.py:136-187`），后者默认 `min_value=1`，直接抛 400。控制流根本进不到 `webui_users_list` 里 `fetch_all = per_page_raw == 0` 那行。

## Goal

让 `GET /webui/api/users?per_page=0` 真正返回全表（与 `webui_users.py` 已有的 `fetch_all` 分支匹配）；**其它分页端点保持原有 `per_page >= 1` 约束不变**。

## Requirements

1. 给 `read_pagination_query` 增加 `allow_zero_per_page: bool = False` 关键字参数；为 True 时把 `min_value` 调到 0，允许 `per_page=0`。其它 caller（servers / commands / groups）行为不变。
2. `webui_users.py` 调用处加 `allow_zero_per_page=True`。
3. 不动 `_parse_positive_int` 函数名（仍然支持 min_value=0；命名虽然带 "positive" 但语义上接受 `min_value=0` 是合理的；改名会牵连 4 个 caller 越界）。
4. 不动 / 不引入其它逻辑（验证器其它字段、users 端点 cap 上限、meta 形态）。

## Acceptance Criteria

- [ ] `curl 'localhost:18081/webui/api/users?token=...&per_page=0'` 返回 200，`data` 为全表数组，`meta.per_page == meta.total`、`meta.total_pages == 1`
- [ ] `curl '.../webui/api/users?per_page=10'` 仍正常分页（每页 10 条）
- [ ] `curl '.../webui/api/users?per_page=-1'` 仍返 400（min_value=0 不允许负数）
- [ ] `curl '.../webui/api/users?per_page=abc'` 仍返 400 `per_page 必须是整数`
- [ ] 其它使用 `read_pagination_query` 的端点（servers / commands / groups）对 `per_page=0` 仍返 400（行为不变）
- [ ] `python3 -m py_compile server/routes/__init__.py server/routes/webui_users.py` 通过

## Out of Scope

- 其它端点是否也需要支持 per_page=0（用户未要求；保持收紧）
- 文档 `docs/webui_api_for_plugins.md` / `webui_api_migration_guide.md` 中关于 per_page=0 的说明（任务结束后用户可能想合并提交，留作下一轮）
- 重命名 `_parse_positive_int` 以更准确反映 `min_value=0` 语义

## Files

- `server/routes/__init__.py` —— 给 `read_pagination_query` 加 `allow_zero_per_page` 参数
- `server/routes/webui_users.py` —— caller 传 `allow_zero_per_page=True`

## Technical Approach

`server/routes/__init__.py` 改动：

```python
def read_pagination_query(
    request: "Request",
    *,
    default_page: int = DEFAULT_PAGE,
    default_per_page: int = DEFAULT_PER_PAGE,
    max_per_page: int = MAX_PER_PAGE,
    allow_zero_per_page: bool = False,  # 新增
) -> tuple[dict[str, int] | None, JSONResponse | None]:
    ...
    per_page, per_page_error = _parse_positive_int(
        request.query_params.get("per_page"),
        field="per_page",
        default_value=default_per_page,
        min_value=0 if allow_zero_per_page else 1,  # 新增
        max_value=ceiling,
    )
    ...
```

`server/routes/webui_users.py:287` 改动：

```python
pagination, error_response = read_pagination_query(request, allow_zero_per_page=True)
```

## Technical Notes

- `_parse_positive_int` 已有 `min_value: int = 1` 参数，函数体内 `if value < min_value:` 自然支持 `min_value=0`；不需要改函数实现。
- `webui_users.py` 内 `fetch_all = per_page_raw == 0` 已经处理好"per_page=0 = 取全表"分支，**只需打通校验器这一关**即可。
- 4 个 caller 中，3 个（servers / commands / groups）按默认值（不传新参数）走，与现状一致。
- `allow_zero_per_page` 命名比 `min_per_page=0` 更直白，后续 caller 看一眼就懂含义。
