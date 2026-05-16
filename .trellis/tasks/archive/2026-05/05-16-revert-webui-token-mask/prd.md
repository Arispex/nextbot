# Web UI Token 启动日志去脱敏，直接显示明文 token

## Goal

把 `server/web_server.py` 启动时打印的 `Web UI Token` 日志从「脱敏」格式改回「明文」格式，方便运维直接从终端复制 token，不用再去打开 `.webui_auth.json` 文件。

## Background

H-1 audit fix 把 token 日志改成了 mask 格式：
```
Web UI Token 已写入：<path>（脱敏：KWiB...kjjp）
```

用户主动撤销该掩码（产品决策）：
- token 已经存在 `.webui_auth.json` 本地文件里，host 上能读到
- WebUI 默认 loopback-only，运维就是 host 操作员
- 终端能直接看到 token 比"开个文件 cat 一下"更顺手

## Decision (ADR-lite)

**Context**：H-1 把 token mask 后，运维体验下降（每次都要 cat auth file）。
**Decision**：撤销 token mask，恢复 commit `9a8857e` 之前的明文 + warning 级别的写法。
**Consequences**：
- ✅ 运维体验恢复
- ⚠️ Trade-off：日志文件如果被采集 / 转发，token 会暴露。运维需要确保日志文件权限、不被外发
- ✅ token 本身就在本地 `.webui_auth.json`，本机权限边界一致

## Scope

仅 `server/web_server.py`：

### 修改 1：去掉 mask，恢复明文 token 日志

**修改前**（L564-570）：
```python
if settings.auth_file_created:
    logger.info(
        f"已生成 Web UI 认证文件，请从该文件读取 token：{settings.auth_file_path}"
    )
logger.info(
    f"Web UI Token 已写入：{settings.auth_file_path}（脱敏：{_mask_token(settings.webui_token)}）"
)
```

**修改后**：
```python
if settings.auth_file_created:
    logger.info(
        f"已生成 Web UI 认证文件：{settings.auth_file_path}"
    )
logger.warning(f"Web UI Token：{settings.webui_token}")
```

理由：恢复 commit `9a8857e` 之前的写法（`logger.warning(f"Web UI Token：{settings.webui_token}")`），同时简化 `auth_file_created` 的提示文案（既然 token 在下一行直接打出来了，不需要再说"从该文件读取"）。

### 修改 2：删除 dead helper `_mask_token`

**删除** L463-469：
```python
def _mask_token(token: str) -> str:
    """H-1：日志中只暴露 token 前 4 + 后 4 字符，中间替换为 ``...``。"""
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"
```

理由：`grep -n "_mask_token" server/web_server.py` 确认仅在被删除的 L569 一处使用，删后无人调用 → dead code。

### 修改 3：更新注释 L559-561

**修改前**：
```python
# L-4 / H-1：日志合并；监听地址打 settings.host，loopback URL 单独提示。
# token 一律 mask；首次启动写入 auth 文件时单独提示用户去文件取 full token。
```

**修改后**：
```python
# L-4：日志合并；监听地址打 settings.host，loopback URL 单独提示。
# token 用 warning 级别打明文，便于运维直接从终端复制；运维需保证日志权限。
```

## Out of Scope

- 不影响 `server/routes/webui_settings.py` / `server/routes/webui_servers.py` 的 `_mask_token`（这两处用于不同的 admin / server-token 链，业务语义不同）
- 不动 `.webui_auth.json` 落盘逻辑
- 不改 `add_webui_auth_middleware` / 认证流程

## Acceptance Criteria

- [ ] `server/web_server.py` `Web UI Token` 日志输出明文 token，格式 `Web UI Token：<token>`，warning 级别
- [ ] `_mask_token` helper 已删除
- [ ] 注释 L559-561 更新，去掉 H-1 mask 描述
- [ ] `grep -n "_mask_token\|脱敏" server/web_server.py` 无输出
- [ ] `server/routes/webui_settings.py` / `server/routes/webui_servers.py` 的 `_mask_token` 函数未受影响
- [ ] 启动 web server 后日志输出预期格式（人工验证）

## Technical Notes

- prior art commit `9a8857e`（mask 之前的写法）
- H-1 audit task 归档：`.trellis/tasks/archive/2026-05/05-15-webui-servers-audit/`（注意：servers audit H-1 是 server-token 链，与本次的 admin-token 不同；helper 同名不同对象）
