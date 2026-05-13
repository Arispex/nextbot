# fix: WebUI auth middleware 区分 API vs HTML (401 vs 302)

## Goal

修复 dashboard-audit Round 1 M-2 / Round 8 deferred 项：当 session 过期或未登录访问 `/webui/api/*` 时，middleware 当前统一返回 302 重定向到 HTML 登录页，导致 fetch / XHR 客户端解析失败（期望 JSON 拿到 HTML），用户看到无意义错误而非"请重新登入"。

**统一修复影响所有 WebUI 页面**，让任何 session 过期场景下的 webui 模块（dashboard / users / servers / lottery / shop / groups / warehouse / settings / commands）都能正确处理。

## 当前行为

文件：`server/routes/webui.py:117-131`

```python
if path.startswith("/webui") and not is_webui_auth_free_path:
    if not _is_authenticated(request, settings):
        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        login_url = "/webui/login?" + urlencode({"next": next_path})
        return RedirectResponse(url=login_url, status_code=302)
```

**问题**：path 白名单不区分 `/webui/api/*` JSON 端点 vs `/webui/*` HTML 页面，统一 302。

## 修复方案

### 后端 (`server/routes/webui.py:117-131`)

middleware 增加 API 路径判断：

```python
is_api_path = path.startswith("/webui/api/")

if path.startswith("/webui") and not is_webui_auth_free_path:
    if not _is_authenticated(request, settings):
        if is_api_path:
            # API 路径返回 401 JSON，让前端 fetch / XHR 能正确解析 + 主动跳转
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "unauthorized",
                        "message": "未登录",
                    }
                },
            )
        # HTML 页面继续走 302 重定向
        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        login_url = "/webui/login?" + urlencode({"next": next_path})
        return RedirectResponse(url=login_url, status_code=302)
```

`/webui/api/session` 仍在 auth-free 白名单（登录端点本身允许匿名 POST）。

### 前端 (`server/webui/static/js/api.js`)

`apiRequest` 在 catch HTTP 错误时，检测 401 + code=`unauthorized` 时主动跳转登录页：

```javascript
if (!response.ok) {
    // 统一处理 401 → 跳转登录（保留 next 参数）
    if (response.status === 401 && code === "unauthorized") {
        const currentPath = window.location.pathname + window.location.search;
        const loginUrl = "/webui/login?next=" + encodeURIComponent(currentPath);
        window.location.assign(loginUrl);
        // 跳转后仍抛错让 caller finally 链能跑（虽然实际页面已开始卸载）
        throw new ApiRequestError("登录已过期，正在跳转登录...", {
            status: 401,
            code: "unauthorized",
            reason: "登录已过期",
        });
    }
    // 其他错误保持原逻辑
    const detailReason = buildDetailReason(details);
    ...
}
```

## Scope

后端：`server/routes/webui.py`（middleware）
前端：`server/webui/static/js/api.js`（401 处理）

## Acceptance Criteria

- [ ] middleware `/webui/api/*` 未登录返回 401 JSON
- [ ] middleware `/webui/*`（非 API）保持 302 → /webui/login?next=...
- [ ] `/webui/api/session` POST 仍可匿名（auth-free 白名单）
- [ ] 前端 401 + code=unauthorized 自动跳转登录页 + 保留 next
- [ ] login.html 登录后 next 参数能正确解析跳回原页面
- [ ] 不破坏 Round 1-2 dashboard 修复 / Round 7-9 + login-audit 加固

## Out of Scope

- 不改 session cookie 机制（login-audit 已闭环）
- 不改 brute-force rate limit（已闭环）
- 不改 dashboard 业务逻辑
- 不动 `/webui/api/session` 端点本身（auth-free）

## Technical Notes

- `JSONResponse` import 已存在于 `webui.py:14`
- prior art：dashboard-audit verify-pass2.md M-2 / login-audit verify-pass2.md
- M-A4 IP/UA logging：401 路径**也应当**像 login-audit 一样补 client_ip + UA 进日志（可选小加固，建议同步做）
- 影响所有 webui 模块前端 fetch / XHR，但**不破坏现有 caller**：现在 401 错误改为自动跳转，原 ApiRequestError 仍抛但页面已开始跳转
