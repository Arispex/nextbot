from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from nonebot.log import logger

from nextbot.db import User, get_session
from nextbot.time_utils import beijing_now
from server.routes import api_error
from server.routes.webui import _client_ip

router = APIRouter()


def _build_users_payload(rows: list[tuple[str, bool, str | None]]) -> list[dict[str, Any]]:
    """把 DB 行转为响应中的 user 列表。

    保留 password_hash 的原始 None；ETag 阶段才把 None 当作 "" 参与 hash，
    避免 NULL 让 ETag 在两次完全相同的快照之间漂移。
    """
    return [
        {
            "name": str(name),
            "banned": bool(banned),
            "password_hash": password_hash if password_hash else None,
        }
        for name, banned, password_hash in rows
    ]


def _compute_snapshot_etag(users_payload: list[dict[str, Any]]) -> str:
    """对 sync-relevant 字段（name / banned / password_hash）按 name 排序后稳定哈希。

    - password_hash 为 None 时在 hash 输入里用空串占位（响应体里仍输出 null）。
    - 仅 sync-relevant 字段参与；coins / sign_streak / rob_* 改动不影响 ETag。
    - ensure_ascii=False + utf-8 hash：支持 emoji / 中文用户名稳定。
    """
    canonical = json.dumps(
        sorted(
            [
                {
                    "name": u["name"],
                    "banned": bool(u["banned"]),
                    "password_hash": u["password_hash"] or "",
                }
                for u in users_payload
            ],
            key=lambda x: x["name"],
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_if_none_match(raw: str) -> str:
    """RFC 7232：客户端发的 If-None-Match 形如 `"<hex>"`（带双引号），剥掉后比对。

    宽松处理：忽略 weak validator 前缀 `W/`、空白；不处理多 ETag 列表（本端点只会
    生成 strong ETag，客户端理论上也只会回一个）。
    """
    value = (raw or "").strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    return value.strip('"')


@router.get("/webui/api/sync/snapshot")
async def webui_sync_snapshot(request: Request) -> Response:
    """C# 插件 poll 这个端点同步白名单 / 黑名单 / 账号密码 hash。

    走现有 webui auth 中间件（cookie 或 query token），无需独立认证。
    """
    session = get_session()
    try:
        rows: list[tuple[str, bool, str | None]] = [
            (row.name, row.is_banned, row.password_hash)
            for row in session.query(User.name, User.is_banned, User.password_hash).all()
        ]
    except Exception as exc:
        client_ip = _client_ip(request)
        logger.exception(
            f"加载同步快照失败：reason={exc} client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()

    users_payload = _build_users_payload(rows)
    etag = _compute_snapshot_etag(users_payload)

    incoming = _parse_if_none_match(request.headers.get("if-none-match", ""))
    response_headers = {
        "ETag": f'"{etag}"',
        "Cache-Control": "no-cache",
    }

    if incoming and incoming == etag:
        # 304 不带 body；高频 poll 路径不打 info 日志以免噪声。
        return Response(status_code=304, headers=response_headers)

    client_ip = _client_ip(request)
    logger.info(
        f"返回同步快照成功：user_count={len(users_payload)} "
        f"etag={etag[:12]} client_ip={client_ip}"
    )

    body = {
        "version": etag,
        "generated_at": beijing_now().isoformat(),
        "users": users_payload,
    }
    return JSONResponse(content=body, headers=response_headers)
