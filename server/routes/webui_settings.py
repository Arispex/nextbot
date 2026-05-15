from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from nonebot.log import logger

from server.pages.console_page import render_settings_page
from server.routes import (
    api_error,
    api_success,
    client_ip as _shared_client_ip,
    read_json_object,
    user_agent as _shared_user_agent,
)
from server.settings_service import (
    SettingsValidationError,
    get_settings_metadata,
    get_settings_snapshot,
    save_settings,
)

router = APIRouter()
_RESTART_LOCK = threading.Lock()
_RESTART_SCHEDULED = False

# CRIT-1：token mask 与 servers 模块同形，仅在 list/get 响应中外泄末 4 位。
_TOKEN_MASK_PREFIX = "****"

# H-3：CSRF 防护标识，与前端 settings.js fetch headers 显式同步。
_CSRF_HEADER_NAME = "x-requested-with"
_CSRF_HEADER_VALUE = "NextBotWebUI"

# M-2：英文字段名 → 中文 label 映射（与前端 settings.js FIELD_LABELS 对齐），
# 422 响应在 top-level message 里替换英文字段名展示，但 details[0].message 保留原文便于调试。
_FIELD_LABELS: dict[str, str] = {
    "onebot_ws_urls": "OneBot WebSocket 地址",
    "onebot_access_token": "OneBot 访问令牌",
    "owner_id": "管理员 QQ",
    "group_id": "允许群号",
    "web_server_host": "Web 服务监听地址",
    "web_server_port": "Web 服务端口",
    "web_server_public_base_url": "Web 服务对外地址",
    "command_disabled_mode": "命令关闭模式",
    "command_disabled_message": "命令关闭提示语",
    "login_notify_all_groups": "登入通知范围",
    "player_notify_mode": "上下线通知范围",
    "player_notify_group_id": "上下线通知群号",
    "player_notify_online_template": "上线消息模板",
    "player_notify_offline_template": "下线消息模板",
    "chat_sync_mode": "消息同步范围",
    "chat_sync_group_id": "消息同步群号",
    "chat_sync_template": "消息同步模板",
    "group_welcome_enabled": "入群欢迎启用",
    "group_welcome_template": "入群欢迎模板",
    "group_farewell_enabled": "退群送别启用",
    "group_farewell_template": "退群送别模板",
    "group_auto_ban_on_leave_enabled": "退群自动封禁",
    "group_auto_ban_on_leave_notify": "退群封禁通知",
}


def _mask_token(token: str) -> str:
    """CRIT-1：把 token 转为 mask 形式返回前端；保留末 4 位便于运维识别。"""
    raw = str(token or "")
    if not raw:
        return ""
    if len(raw) <= 4:
        return _TOKEN_MASK_PREFIX
    return _TOKEN_MASK_PREFIX + raw[-4:]


def _is_mask_token(token: str) -> bool:
    """CRIT-1：客户端回填的 mask 形式表示"保留原值"。"""
    return token.startswith(_TOKEN_MASK_PREFIX)


# CRIT-1 / HIGH-2：thin re-export aliases；canonical helper 在 server/routes/__init__.py。
_client_ip = _shared_client_ip
_user_agent = _shared_user_agent


def _check_csrf_header(request: Request) -> JSONResponse | None:
    """H-3：写入端点必须带 X-Requested-With 自定义头，缺失视为 CSRF 拒绝。"""
    if request.headers.get(_CSRF_HEADER_NAME, "") != _CSRF_HEADER_VALUE:
        return api_error(status_code=403, code="forbidden", message="非法请求")
    return None


def _localize_validation_message(exc: SettingsValidationError) -> str:
    """M-2：把 `str(exc)` 内的英文字段名替换为中文 label。"""
    raw = str(exc)
    if exc.field and exc.field in _FIELD_LABELS:
        return raw.replace(exc.field, _FIELD_LABELS[exc.field])
    return raw


def _restart_worker(source: str = "manual") -> None:
    """L-1：通过 source 标签区分重启触发来源（settings-save / manual）。"""
    global _RESTART_SCHEDULED
    try:
        time.sleep(0.8)
        logger.warning(f"检测到设置变更，程序即将重启：source={source}")
        # L-2：显式传 env，避免未来注入的 env 变量被默认 inheritance 丢失。
        os.execve(sys.executable, [sys.executable, *sys.argv], os.environ.copy())
    except Exception as exc:
        logger.exception(f"重启失败：source={source}，reason={exc}")
        with _RESTART_LOCK:
            _RESTART_SCHEDULED = False


def _schedule_process_restart(source: str = "manual") -> bool:
    global _RESTART_SCHEDULED
    with _RESTART_LOCK:
        if _RESTART_SCHEDULED:
            return False
        _RESTART_SCHEDULED = True
    thread = threading.Thread(
        target=_restart_worker,
        name=f"nextbot-restart-worker[{source}]",
        args=(source,),
        daemon=True,
    )
    thread.start()
    return True


@router.get("/webui/settings", response_class=HTMLResponse)
async def webui_settings_page() -> HTMLResponse:
    return HTMLResponse(content=render_settings_page())


@router.get("/webui/api/settings")
async def webui_settings_get() -> JSONResponse:
    # CRIT-1：响应中对 onebot_access_token 做 mask，明文 token 仅通过显式 reveal 端点暴露。
    data = get_settings_snapshot()
    if isinstance(data, dict) and "onebot_access_token" in data:
        data["onebot_access_token"] = _mask_token(str(data.get("onebot_access_token") or ""))
    return api_success(
        data=data,
        meta=get_settings_metadata(),
    )


@router.get("/webui/api/settings/onebot-token")
async def webui_settings_reveal_onebot_token(request: Request) -> JSONResponse:
    """CRIT-1：按需返回完整 OneBot access token；每次调用都 WARN 日志记录 IP / UA。"""
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    snapshot = get_settings_snapshot()
    token = ""
    if isinstance(snapshot, dict):
        token = str(snapshot.get("onebot_access_token") or "")
    logger.warning(
        f"展示 OneBot token 成功："
        f"client_ip={client_ip} user_agent={user_agent!r}"
    )
    return api_success(data={"token": token})


@router.put("/webui/api/settings")
async def webui_settings_put(request: Request) -> JSONResponse:
    # H-3：CSRF 头校验
    csrf_error = _check_csrf_header(request)
    if csrf_error is not None:
        return csrf_error

    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response

    # CRIT-1：客户端在 token input 为空或回填 mask 串时，保留 snapshot 内现有 token，
    # 避免明文 token 必须每次保存重新输入。
    if isinstance(payload, dict):
        incoming_token = payload.get("onebot_access_token", None)
        if isinstance(incoming_token, str):
            stripped = incoming_token.strip()
            if not stripped or _is_mask_token(stripped):
                snapshot = get_settings_snapshot()
                if isinstance(snapshot, dict):
                    payload["onebot_access_token"] = str(
                        snapshot.get("onebot_access_token") or ""
                    )

    try:
        result = save_settings(payload)
    except SettingsValidationError as exc:
        logger.warning(f"保存设置失败：field={exc.field or ''}，reason={exc}")
        details: list[dict[str, Any]] | None = None
        if exc.field:
            details = [{"field": exc.field, "message": str(exc)}]
        return api_error(
            status_code=422,
            code="validation_error",
            message=_localize_validation_message(exc),
            details=details,
        )
    except Exception:
        # H-1 / L-3：不再把 exc 字符串拼进 message，避免未来 settings_service 异常 message
        # 携带 token 等敏感字段时被日志原文落地；traceback 由 .exception 自动附加。
        logger.exception("保存设置内部错误")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    if not _schedule_process_restart(source="settings-save"):
        logger.warning("保存设置失败：reason=重启已在进行中")
        # M-3：error.message 只回有效原因，"请稍后刷新页面" 等展示文案由前端生成。
        return api_error(
            status_code=409,
            code="conflict",
            message="重启已在进行中",
            details=[{"field": "restart", "message": "重启已在进行中"}],
        )

    logger.info(f"保存设置成功：saved_fields={','.join(result.saved_fields)}")
    return api_success(
        data={
            "restart_scheduled": True,
            "saved_fields": result.saved_fields,
        }
    )


@router.post("/webui/api/restart")
async def webui_restart(request: Request) -> JSONResponse:
    # H-3：CSRF 头校验
    csrf_error = _check_csrf_header(request)
    if csrf_error is not None:
        return csrf_error

    if not _schedule_process_restart(source="manual"):
        # M-3：error.message 只回有效原因。
        return api_error(
            status_code=409,
            code="conflict",
            message="重启已在进行中",
        )
    logger.info("手动触发重启")
    return api_success(data={"restart_scheduled": True})
