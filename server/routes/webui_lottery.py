from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from nonebot.log import logger
from sqlalchemy.exc import IntegrityError

from nextbot.db import LotteryPool, LotteryPrize, Server, get_session
from nextbot.progression import PROGRESSION_KEY_TO_ZH, TIER_OPTIONS
from nextbot.time_utils import beijing_now
from server.routes import api_error, api_success, read_json_object

router = APIRouter()

_VALID_KINDS = {"item", "command", "coin"}
_NAME_MAX_LEN = 50
_DESC_MAX_LEN = 200
_CMD_MAX_LEN = 500
_EXPORT_VERSION = 1
_EXPORT_KIND = "lottery_pools"
_IMPORT_MODES = {"merge", "replace_all"}

# M-1 / M-2 / M-3 / M-4：整型字段统一上下界，避免 2**31 / 2**63 类极端值落地。
_COST_MAX = 1_000_000_000  # 10**9 金币上限
_QUANTITY_MAX = 9999
_ITEM_ID_MAX = 2_147_483_647  # int32 与 DB 模型一致
_PREFIX_ID_MAX = 2_147_483_647
_SORT_ORDER_MAX = 1_000_000
_SORT_ORDER_MIN = -1_000_000
_COIN_AMOUNT_MAX = 100_000_000  # ±10**8
_ACTUAL_VALUE_MAX = 1_000_000_000

# H-2：危险命令前缀黑名单。WebUI 是 lottery 奖品命令的唯一录入端，禁止录入高权命令。
# 抽奖落地阶段（nextbot/plugins/lottery.py）通过 RCON 直发到 MC 服务器。
_COMMAND_DENYLIST_PREFIXES = (
    "op ",
    "deop ",
    "ban ",
    "ban-ip ",
    "pardon",
    "kick ",
    "stop",
    "shutdown",
    "restart",
    "whitelist ",
    "save-all",
    "save-off",
    "save-on",
)

# H-1：replace_all 模式要求前端传入此 confirm 字段（用户在 modal 中键入「全量替换」四个汉字）。
_REPLACE_ALL_CONFIRM_PHRASE = "全量替换"


def _client_ip(request: Request) -> str:
    """从 X-Forwarded-For 或 client.host 取调用方 IP（与 webui_servers._client_ip 同实现）。"""
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client is not None:
        return request.client.host or "unknown"
    return "unknown"


def _user_agent(request: Request) -> str:
    """截断 User-Agent 防超长（与 webui_servers._user_agent 同实现）。"""
    return request.headers.get("user-agent", "")[:200]


def _strict_int(raw: Any) -> tuple[int | None, bool]:
    """L-3 / M-3：拒绝 bool（int 子类），返回 (value, ok)。"""
    if isinstance(raw, bool):
        return None, False
    try:
        return int(raw), True
    except (TypeError, ValueError):
        return None, False


def _strip_control_chars(text: str) -> str:
    """L-2：去除换行 / 回车 / 制表符等控制字符，避免日志注入。"""
    return text.replace("\r", "").replace("\n", "").replace("\t", " ")


def _command_denylist_hit(stripped_cmd: str) -> str | None:
    """H-2：返回命中的危险前缀（lower-cased），若命中。

    支持 `/op ` / `op ` 两种形式（MC 命令可带或不带 `/`）。
    """
    lower = stripped_cmd.lower().lstrip("/")
    for prefix in _COMMAND_DENYLIST_PREFIXES:
        if lower == prefix.rstrip() or lower.startswith(prefix):
            return prefix
    return None


def _validation_error_response(details: list[dict[str, str]]) -> JSONResponse:
    return api_error(
        status_code=422, code="validation_error", message="参数校验失败", details=details,
    )


def _serialize_pool(pool: LotteryPool, *, prize_count: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": int(pool.id),
        "name": str(pool.name),
        "description": str(pool.description or ""),
        "sort_order": int(pool.sort_order or 0),
        "enabled": bool(pool.enabled),
        "cost_per_draw": int(pool.cost_per_draw or 0),
    }
    if prize_count is not None:
        data["prize_count"] = int(prize_count)
    return data


def _serialize_prize(prize: LotteryPrize, *, target_server_label: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": int(prize.id),
        "pool_id": int(prize.pool_id),
        "sort_order": int(prize.sort_order or 0),
        "name": str(prize.name),
        "description": str(prize.description or ""),
        "kind": str(prize.kind),
        "enabled": bool(prize.enabled),
        "weight": float(prize.weight) if getattr(prize, "weight", None) is not None else None,
        "item_id": int(prize.item_id or 0),
        "prefix_id": int(prize.prefix_id or 0),
        "quantity": int(prize.quantity or 1),
        "min_tier": str(prize.min_tier or "none"),
        "min_tier_label": PROGRESSION_KEY_TO_ZH.get(str(prize.min_tier or "none"), str(prize.min_tier or "none")),
        "actual_value": int(prize.actual_value) if getattr(prize, "actual_value", None) is not None else None,
        "is_mystery": bool(getattr(prize, "is_mystery", False)),
        "target_server_id": int(prize.target_server_id) if prize.target_server_id is not None else None,
        "command_template": str(prize.command_template or ""),
        "show_command": bool(getattr(prize, "show_command", False)),
        "require_online": bool(getattr(prize, "require_online", False)),
        "coin_amount": int(prize.coin_amount or 0),
    }
    if prize.kind == "command":
        if prize.target_server_id is None:
            data["target_server_label"] = "全部服务器"
        else:
            data["target_server_label"] = target_server_label or f"#{prize.target_server_id}"
    else:
        data["target_server_label"] = ""
    return data


def _validate_pool_payload(
    data: dict[str, Any], *, partial: bool = False,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate pool metadata. Returns (validated, []) on success, (None, details) on failure."""
    details: list[dict[str, str]] = []
    out: dict[str, Any] = {}

    if "name" in data or not partial:
        # L-2：去控制字符后再校验长度，避免日志注入。
        name = _strip_control_chars(str(data.get("name", ""))).strip()
        if not name:
            details.append({"field": "name", "message": "名称不能为空"})
        elif len(name) > _NAME_MAX_LEN:
            details.append({"field": "name", "message": f"名称长度不能超过 {_NAME_MAX_LEN}"})
        else:
            out["name"] = name

    if "description" in data:
        desc = _strip_control_chars(str(data.get("description", ""))).strip()
        if len(desc) > _DESC_MAX_LEN:
            details.append({"field": "description", "message": f"说明长度不能超过 {_DESC_MAX_LEN}"})
        else:
            out["description"] = desc

    if "sort_order" in data:
        # L-3 / M-4：拒绝 bool，钳制上下界。
        sort_value, ok = _strict_int(data["sort_order"])
        if not ok or sort_value is None:
            details.append({"field": "sort_order", "message": "排序值必须为整数"})
        elif sort_value < _SORT_ORDER_MIN or sort_value > _SORT_ORDER_MAX:
            details.append({"field": "sort_order", "message": f"排序值范围 {_SORT_ORDER_MIN}~{_SORT_ORDER_MAX}"})
        else:
            out["sort_order"] = sort_value

    if "enabled" in data:
        out["enabled"] = bool(data["enabled"])

    if "cost_per_draw" in data or not partial:
        # L-3 / M-1：拒绝 bool，钳制上界。
        cost_value, ok = _strict_int(data.get("cost_per_draw", 0))
        if not ok or cost_value is None:
            details.append({"field": "cost_per_draw", "message": "抽奖单价必须为非负整数"})
        elif cost_value < 0:
            details.append({"field": "cost_per_draw", "message": "抽奖单价必须为非负整数"})
        elif cost_value > _COST_MAX:
            details.append({"field": "cost_per_draw", "message": f"抽奖单价不能超过 {_COST_MAX}"})
        else:
            out["cost_per_draw"] = cost_value

    if details:
        return None, details
    return out, []


def _validate_prize_payload(
    data: dict[str, Any],
    *,
    valid_server_ids: set[int],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    details: list[dict[str, str]] = []

    # L-2：去控制字符。
    name = _strip_control_chars(str(data.get("name", ""))).strip()
    if not name:
        details.append({"field": "name", "message": "名称不能为空"})
    elif len(name) > _NAME_MAX_LEN:
        details.append({"field": "name", "message": f"名称长度不能超过 {_NAME_MAX_LEN}"})

    description = _strip_control_chars(str(data.get("description", ""))).strip()
    if len(description) > _DESC_MAX_LEN:
        details.append({"field": "description", "message": f"说明长度不能超过 {_DESC_MAX_LEN}"})

    kind = str(data.get("kind", "")).strip()
    if kind not in _VALID_KINDS:
        details.append({"field": "kind", "message": "类型必须为 item、command 或 coin"})

    # L-3 / M-4：拒绝 bool，钳制上下界。
    sort_value, ok = _strict_int(data.get("sort_order", 0))
    if not ok or sort_value is None:
        sort_order = 0
        details.append({"field": "sort_order", "message": "排序值必须为整数"})
    elif sort_value < _SORT_ORDER_MIN or sort_value > _SORT_ORDER_MAX:
        sort_order = 0
        details.append({"field": "sort_order", "message": f"排序值范围 {_SORT_ORDER_MIN}~{_SORT_ORDER_MAX}"})
    else:
        sort_order = sort_value

    enabled = bool(data.get("enabled", True))

    raw_weight = data.get("weight", None)
    if raw_weight is None or (isinstance(raw_weight, str) and raw_weight.strip() == ""):
        weight = None
    else:
        # H-4：拒绝 NaN/Inf。`float("nan") < 0` 与 `float("nan") > 100` 均为 False，
        # 必须显式用 math.isfinite 拦截。同时 bool 是 int 子类，避免 True/False 当数值。
        weight: float | None = None
        weight_ok = True
        if isinstance(raw_weight, bool):
            weight_ok = False
        else:
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError):
                weight_ok = False
        if not weight_ok or weight is None:
            details.append({"field": "weight", "message": "概率必须为有限数值"})
            weight = None
        elif not math.isfinite(weight):
            details.append({"field": "weight", "message": "概率必须为有限数值"})
            weight = None
        elif weight < 0.0 or weight > 100.0:
            details.append({"field": "weight", "message": "概率必须为 0-100 之间的数值"})
            weight = None
        else:
            # M-9：钳制精度到 4 位小数，覆盖前端 step=0.01。
            weight = round(weight, 4)

    item_id = 0
    prefix_id = 0
    quantity = 1
    min_tier = "none"
    actual_value: int | None = None
    is_mystery = False
    target_server_id: int | None = None
    command_template = ""
    show_command = False
    require_online = False
    coin_amount = 0

    if kind == "item":
        item_value, ok = _strict_int(data.get("item_id", 0))
        if not ok or item_value is None or item_value < 1:
            details.append({"field": "item_id", "message": "item_id 必须为正整数"})
            item_id = 0
        elif item_value > _ITEM_ID_MAX:
            details.append({"field": "item_id", "message": f"item_id 不能超过 {_ITEM_ID_MAX}"})
            item_id = 0
        else:
            item_id = item_value

        prefix_value, ok = _strict_int(data.get("prefix_id", 0))
        if not ok or prefix_value is None or prefix_value < 0:
            details.append({"field": "prefix_id", "message": "prefix_id 必须为非负整数"})
            prefix_id = 0
        elif prefix_value > _PREFIX_ID_MAX:
            details.append({"field": "prefix_id", "message": f"prefix_id 不能超过 {_PREFIX_ID_MAX}"})
            prefix_id = 0
        else:
            prefix_id = prefix_value

        quantity_value, ok = _strict_int(data.get("quantity", 1))
        if not ok or quantity_value is None or quantity_value < 1:
            details.append({"field": "quantity", "message": "数量必须为正整数"})
            quantity = 1
        elif quantity_value > _QUANTITY_MAX:
            details.append({"field": "quantity", "message": f"数量不能超过 {_QUANTITY_MAX}"})
            quantity = 1
        else:
            quantity = quantity_value

        min_tier = str(data.get("min_tier", "none")).strip() or "none"
        if min_tier not in PROGRESSION_KEY_TO_ZH:
            details.append({"field": "min_tier", "message": "进度要求不在已知列表中"})

        raw_actual = data.get("actual_value", None)
        if raw_actual is None or (isinstance(raw_actual, str) and raw_actual.strip() == ""):
            actual_value = None
        else:
            actual_value_int, ok = _strict_int(raw_actual)
            if not ok or actual_value_int is None or actual_value_int < 0:
                details.append({"field": "actual_value", "message": "实际单价必须为非负整数"})
                actual_value = None
            elif actual_value_int > _ACTUAL_VALUE_MAX:
                details.append({"field": "actual_value", "message": f"实际单价不能超过 {_ACTUAL_VALUE_MAX}"})
                actual_value = None
            else:
                actual_value = actual_value_int

        is_mystery = bool(data.get("is_mystery", False))

    if kind == "command":
        raw_target = data.get("target_server_id", None)
        if raw_target is None or (isinstance(raw_target, str) and raw_target.strip() == ""):
            target_server_id = None
        else:
            target_value, ok = _strict_int(raw_target)
            if not ok or target_value is None:
                details.append({"field": "target_server_id", "message": "目标服务器不存在"})
                target_server_id = None
            elif target_value not in valid_server_ids:
                details.append({"field": "target_server_id", "message": "目标服务器不存在"})
                target_server_id = None
            else:
                target_server_id = target_value

        # L-2：去控制字符；空白 strip。
        command_template = _strip_control_chars(str(data.get("command_template", "")))
        stripped_cmd = command_template.strip()
        if not stripped_cmd:
            details.append({"field": "command_template", "message": "命令模板不能为空"})
        elif len(command_template) > _CMD_MAX_LEN:
            details.append({"field": "command_template", "message": f"命令长度不能超过 {_CMD_MAX_LEN}"})
        else:
            # H-2：危险命令前缀黑名单。
            hit = _command_denylist_hit(stripped_cmd)
            if hit is not None:
                details.append({
                    "field": "command_template",
                    "message": f"命令前缀 {hit.strip()} 不允许作为抽奖奖品",
                })
            else:
                command_template = stripped_cmd

        show_command = bool(data.get("show_command", False))
        require_online = bool(data.get("require_online", False))

    if kind == "coin":
        raw_coin = data.get("coin_amount", 0)
        coin_value, ok = _strict_int(raw_coin)
        if not ok or coin_value is None:
            coin_amount = 0
            details.append({"field": "coin_amount", "message": "金币数量必须为整数（可正可负）"})
        elif coin_value == 0:
            coin_amount = 0
            details.append({"field": "coin_amount", "message": "金币数量不能为 0"})
        elif coin_value < -_COIN_AMOUNT_MAX or coin_value > _COIN_AMOUNT_MAX:
            coin_amount = 0
            details.append({
                "field": "coin_amount",
                "message": f"金币数量范围 -{_COIN_AMOUNT_MAX}~{_COIN_AMOUNT_MAX}",
            })
        else:
            coin_amount = coin_value

    if details:
        return None, details

    return {
        "name": name,
        "description": description,
        "kind": kind,
        "sort_order": sort_order,
        "enabled": enabled,
        "weight": weight,
        "item_id": item_id,
        "prefix_id": prefix_id,
        "quantity": quantity,
        "min_tier": min_tier,
        "actual_value": actual_value,
        "is_mystery": is_mystery,
        "target_server_id": target_server_id,
        "command_template": command_template,
        "show_command": show_command,
        "require_online": require_online,
        "coin_amount": coin_amount,
    }, []


def _load_server_id_set() -> set[int]:
    session = get_session()
    try:
        return {int(s.id) for s in session.query(Server).all()}
    finally:
        session.close()


def _existing_weight_sum(session: Any, pool_id: int, exclude_prize_id: int | None = None) -> float:
    """C-1：同 pool 内、enabled、weight 非空的其他 prize 已设置权重之和。"""
    query = session.query(LotteryPrize).filter(
        LotteryPrize.pool_id == pool_id,
        LotteryPrize.enabled == True,  # noqa: E712 — SQLAlchemy boolean compare
        LotteryPrize.weight.isnot(None),
    )
    if exclude_prize_id is not None:
        query = query.filter(LotteryPrize.id != exclude_prize_id)
    total = 0.0
    for prize in query.all():
        try:
            w = float(prize.weight)
        except (TypeError, ValueError):
            continue
        if math.isfinite(w):
            total += w
    return total


def _load_server_label_map() -> dict[int, str]:
    session = get_session()
    try:
        return {int(s.id): str(s.name) for s in session.query(Server).all()}
    finally:
        session.close()


@router.get("/webui/api/lottery/meta/tiers")
async def list_lottery_tiers(request: Request) -> JSONResponse:
    return api_success(data=[{"key": key, "label": zh} for key, zh in TIER_OPTIONS])


@router.get("/webui/api/lottery/meta/servers")
async def list_lottery_servers(request: Request) -> JSONResponse:
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
        return api_success(
            data=[{"id": int(s.id), "name": str(s.name)} for s in servers],
        )
    finally:
        session.close()


@router.get("/webui/api/lottery")
async def list_pools(request: Request) -> JSONResponse:
    session = get_session()
    try:
        pools = session.query(LotteryPool).order_by(LotteryPool.sort_order.asc(), LotteryPool.id.asc()).all()
        counts: dict[int, int] = {}
        if pools:
            for pid, in (
                session.query(LotteryPrize.pool_id)
                .filter(LotteryPrize.pool_id.in_([p.id for p in pools]))
                .all()
            ):
                counts[int(pid)] = counts.get(int(pid), 0) + 1
        data = [_serialize_pool(p, prize_count=counts.get(int(p.id), 0)) for p in pools]
        return api_success(data=data)
    finally:
        session.close()


@router.post("/webui/api/lottery")
async def create_pool(request: Request) -> JSONResponse:
    payload, error = await read_json_object(request)
    if error is not None:
        return error
    assert payload is not None

    validated, details = _validate_pool_payload(payload)
    if details:
        return _validation_error_response(details)
    assert validated is not None
    if "name" not in validated:
        return api_error(
            status_code=422, code="validation_error", message="参数校验失败",
            details=[{"field": "name", "message": "名称不能为空"}],
        )

    # H-3：录入侧关键操作补 client_ip / user_agent，与 servers / commands R1+R2 标准对齐。
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        existing = session.query(LotteryPool).filter(LotteryPool.name == validated["name"]).first()
        if existing is not None:
            # L-9：details message 与 top-level message 解耦，避免前端拼出重复内容。
            return api_error(
                status_code=409, code="duplicate_name", message="奖池名称已存在",
                details=[{"field": "name", "message": "该名称已被其他奖池占用"}],
            )
        pool = LotteryPool(
            name=validated["name"],
            description=validated.get("description", ""),
            sort_order=int(validated.get("sort_order", 0)),
            enabled=bool(validated.get("enabled", True)),
            cost_per_draw=int(validated.get("cost_per_draw", 0)),
        )
        session.add(pool)
        try:
            session.commit()
        except IntegrityError:
            # M-5：name 并发冲突由 DB unique 兜底，转 409 而非 500。
            session.rollback()
            logger.warning(
                f"创建奖池失败：reason=name 并发重复 client_ip={client_ip}"
            )
            return api_error(
                status_code=409, code="duplicate_name", message="奖池名称已存在",
                details=[{"field": "name", "message": "该名称已被其他奖池占用"}],
            )
        session.refresh(pool)
        logger.info(
            f"创建奖池成功：pool_id={pool.id} name={pool.name!r} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(
            status_code=201,
            data=_serialize_pool(pool, prize_count=0),
            headers={"Location": f"/webui/api/lottery/{pool.id}"},
        )
    finally:
        session.close()

# ---------- Export / Import ----------


def _export_prize_dict(prize: LotteryPrize) -> dict[str, Any]:
    """Return the import-friendly subset of fields for a LotteryPrize."""
    return {
        "name": str(prize.name),
        "description": str(prize.description or ""),
        "kind": str(prize.kind),
        "sort_order": int(prize.sort_order or 0),
        "enabled": bool(prize.enabled),
        "weight": float(prize.weight) if getattr(prize, "weight", None) is not None else None,
        "item_id": int(prize.item_id or 0),
        "prefix_id": int(prize.prefix_id or 0),
        "quantity": int(prize.quantity or 1),
        "min_tier": str(prize.min_tier or "none"),
        "actual_value": int(prize.actual_value) if getattr(prize, "actual_value", None) is not None else None,
        "is_mystery": bool(getattr(prize, "is_mystery", False)),
        "target_server_id": int(prize.target_server_id) if prize.target_server_id is not None else None,
        "command_template": str(prize.command_template or ""),
        "show_command": bool(getattr(prize, "show_command", False)),
        "require_online": bool(getattr(prize, "require_online", False)),
        "coin_amount": int(prize.coin_amount or 0),
    }


@router.get("/webui/api/lottery/export")
async def export_lottery(request: Request) -> JSONResponse:
    # H-3：export 暴露全量明文配置，记录调用方便审计。
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        pools = (
            session.query(LotteryPool)
            .order_by(LotteryPool.sort_order.asc(), LotteryPool.id.asc())
            .all()
        )
        pool_ids = [int(p.id) for p in pools]
        prizes_by_pool: dict[int, list[LotteryPrize]] = {}
        if pool_ids:
            prizes = (
                session.query(LotteryPrize)
                .filter(LotteryPrize.pool_id.in_(pool_ids))
                .order_by(
                    LotteryPrize.pool_id.asc(),
                    LotteryPrize.sort_order.asc(),
                    LotteryPrize.id.asc(),
                )
                .all()
            )
            for prize in prizes:
                prizes_by_pool.setdefault(int(prize.pool_id), []).append(prize)

        exported: list[dict[str, Any]] = []
        for pool in pools:
            pool_prizes = prizes_by_pool.get(int(pool.id), [])
            exported.append({
                "name": str(pool.name),
                "description": str(pool.description or ""),
                "sort_order": int(pool.sort_order or 0),
                "enabled": bool(pool.enabled),
                "cost_per_draw": int(pool.cost_per_draw or 0),
                "prizes": [_export_prize_dict(p) for p in pool_prizes],
            })

        logger.info(
            f"导出奖池配置成功：pool_count={len(exported)} "
            f"prize_count={sum(len(p['prizes']) for p in exported)} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data={
            "version": _EXPORT_VERSION,
            "kind": _EXPORT_KIND,
            "exported_at": beijing_now().isoformat(),
            "pools": exported,
        })
    finally:
        session.close()


@router.post("/webui/api/lottery/import")
async def import_lottery(request: Request) -> JSONResponse:
    payload, error = await read_json_object(request)
    if error is not None:
        return error
    assert payload is not None

    mode = (request.query_params.get("mode") or "merge").strip() or "merge"
    if mode not in _IMPORT_MODES:
        return api_error(
            status_code=400,
            code="invalid_query_parameter",
            message="mode 必须为 merge 或 replace_all",
            details=[{"field": "mode", "message": "mode 必须为 merge 或 replace_all"}],
        )

    # H-1：replace_all 高危操作，要求 confirm 字段精确匹配「全量替换」。
    # 前端 modal 校验同步约束，后端二次校验避免被劫持的 fetch / CSRF 绕过。
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    if mode == "replace_all":
        confirm_phrase = str(payload.get("confirm", "")).strip()
        if confirm_phrase != _REPLACE_ALL_CONFIRM_PHRASE:
            logger.warning(
                f"导入奖池失败：reason=replace_all 缺少二次确认 "
                f"client_ip={client_ip} user_agent={user_agent!r}"
            )
            return api_error(
                status_code=400,
                code="confirm_required",
                message=f"全量替换需在 confirm 字段输入「{_REPLACE_ALL_CONFIRM_PHRASE}」",
                details=[{
                    "field": "confirm",
                    "message": f"全量替换需在 confirm 字段输入「{_REPLACE_ALL_CONFIRM_PHRASE}」",
                }],
            )

    # ---- Top-level structural checks ----
    structural: list[dict[str, str]] = []
    raw_version = payload.get("version")
    if raw_version != _EXPORT_VERSION:
        structural.append({
            "field": "version",
            "message": f"version 必须为 {_EXPORT_VERSION}",
        })
    raw_kind = payload.get("kind")
    if raw_kind not in (None, _EXPORT_KIND):
        structural.append({
            "field": "kind",
            "message": f"kind 必须为 {_EXPORT_KIND}",
        })
    raw_pools = payload.get("pools")
    if not isinstance(raw_pools, list):
        structural.append({"field": "pools", "message": "pools 必须为数组"})
    if structural:
        return _validation_error_response(structural)

    server_ids = _load_server_id_set()

    # ---- Validate every pool + prize, aggregate errors with path prefixes ----
    aggregated: list[dict[str, str]] = []
    validated_pools: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    seen_names: set[str] = set()

    assert isinstance(raw_pools, list)
    for pool_idx, raw_pool in enumerate(raw_pools):
        pool_path = f"pools[{pool_idx}]"
        if not isinstance(raw_pool, dict):
            aggregated.append({"field": pool_path, "message": "必须为对象"})
            continue

        pool_validated, pool_details = _validate_pool_payload(raw_pool)
        if pool_details:
            aggregated.extend({
                "field": f"{pool_path}.{d.get('field', '')}",
                "message": str(d.get("message", "")),
            } for d in pool_details)
            pool_validated = None

        if pool_validated is not None:
            name = str(pool_validated.get("name", ""))
            if name in seen_names:
                aggregated.append({
                    "field": f"{pool_path}.name",
                    "message": f"奖池名称「{name}」在 JSON 中重复",
                })
            else:
                seen_names.add(name)

        raw_prizes = raw_pool.get("prizes", [])
        prizes_validated: list[dict[str, Any]] = []
        if not isinstance(raw_prizes, list):
            aggregated.append({
                "field": f"{pool_path}.prizes",
                "message": "prizes 必须为数组",
            })
            raw_prizes = []

        for prize_idx, raw_prize in enumerate(raw_prizes):
            prize_path = f"{pool_path}.prizes[{prize_idx}]"
            if not isinstance(raw_prize, dict):
                aggregated.append({"field": prize_path, "message": "必须为对象"})
                continue
            prize_validated, prize_details = _validate_prize_payload(
                raw_prize, valid_server_ids=server_ids,
            )
            if prize_details:
                aggregated.extend({
                    "field": f"{prize_path}.{d.get('field', '')}",
                    "message": str(d.get("message", "")),
                } for d in prize_details)
            elif prize_validated is not None:
                prizes_validated.append(prize_validated)

        if pool_validated is not None:
            validated_pools.append((pool_validated, prizes_validated))

    if aggregated:
        return _validation_error_response(aggregated)

    # ---- Apply changes in a single transaction ----
    session = get_session()
    try:
        created = 0
        updated = 0
        prizes_total = 0

        if mode == "replace_all":
            # H-1：先记录将要清空的规模，便于审计排障。
            prev_pool_count = session.query(LotteryPool).count()
            prev_prize_count = session.query(LotteryPrize).count()
            logger.warning(
                f"全量替换奖池配置触发：prev_pool_count={prev_pool_count} "
                f"prev_prize_count={prev_prize_count} "
                f"client_ip={client_ip} user_agent={user_agent!r}"
            )
            session.query(LotteryPrize).delete(synchronize_session=False)
            session.query(LotteryPool).delete(synchronize_session=False)
            session.flush()

        existing_by_name: dict[str, LotteryPool] = (
            {str(p.name): p for p in session.query(LotteryPool).all()}
            if mode == "merge"
            else {}
        )

        for pool_data, prizes_data in validated_pools:
            name = str(pool_data["name"])
            existing = existing_by_name.get(name)

            if existing is not None:
                if "description" in pool_data:
                    existing.description = pool_data["description"]
                if "sort_order" in pool_data:
                    existing.sort_order = int(pool_data["sort_order"])
                if "enabled" in pool_data:
                    existing.enabled = bool(pool_data["enabled"])
                if "cost_per_draw" in pool_data:
                    existing.cost_per_draw = int(pool_data["cost_per_draw"])
                # Replace all prizes belonging to this pool.
                session.query(LotteryPrize).filter(
                    LotteryPrize.pool_id == existing.id,
                ).delete(synchronize_session=False)
                pool_id = int(existing.id)
                updated += 1
            else:
                pool = LotteryPool(
                    name=name,
                    description=pool_data.get("description", ""),
                    sort_order=int(pool_data.get("sort_order", 0)),
                    enabled=bool(pool_data.get("enabled", True)),
                    cost_per_draw=int(pool_data.get("cost_per_draw", 0)),
                )
                session.add(pool)
                session.flush()
                pool_id = int(pool.id)
                created += 1

            for prize_data in prizes_data:
                session.add(LotteryPrize(
                    pool_id=pool_id,
                    sort_order=int(prize_data["sort_order"]),
                    name=str(prize_data["name"]),
                    description=str(prize_data["description"]),
                    kind=str(prize_data["kind"]),
                    enabled=bool(prize_data["enabled"]),
                    weight=prize_data["weight"],
                    item_id=int(prize_data["item_id"]),
                    prefix_id=int(prize_data["prefix_id"]),
                    quantity=int(prize_data["quantity"]),
                    min_tier=str(prize_data["min_tier"]),
                    actual_value=prize_data["actual_value"],
                    is_mystery=bool(prize_data["is_mystery"]),
                    target_server_id=prize_data["target_server_id"],
                    command_template=str(prize_data["command_template"]),
                    show_command=bool(prize_data["show_command"]),
                    require_online=bool(prize_data["require_online"]),
                    coin_amount=int(prize_data["coin_amount"]),
                ))
                prizes_total += 1

        session.commit()
        logger.info(
            f"导入奖池配置成功：mode={mode} created={created} updated={updated} "
            f"prizes_total={prizes_total} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data={
            "mode": mode,
            "created": created,
            "updated": updated,
            "prizes_total": prizes_total,
        })
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"导入奖池配置异常：mode={mode} reason={exc} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        raise
    finally:
        session.close()


@router.get("/webui/api/lottery/{pool_id}")
async def get_pool(pool_id: int) -> JSONResponse:
    label_map = _load_server_label_map()
    session = get_session()
    try:
        pool = session.query(LotteryPool).filter(LotteryPool.id == pool_id).first()
        if pool is None:
            return api_error(status_code=404, code="not_found", message="奖池不存在")
        prizes = (
            session.query(LotteryPrize)
            .filter(LotteryPrize.pool_id == pool_id)
            .order_by(LotteryPrize.sort_order.asc(), LotteryPrize.id.asc())
            .all()
        )
        data = _serialize_pool(pool, prize_count=len(prizes))
        data["prizes"] = [
            _serialize_prize(
                p,
                target_server_label=label_map.get(int(p.target_server_id)) if p.target_server_id is not None else None,
            )
            for p in prizes
        ]
        return api_success(data=data)
    finally:
        session.close()


@router.put("/webui/api/lottery/{pool_id}")
async def update_pool(pool_id: int, request: Request) -> JSONResponse:
    payload, error = await read_json_object(request)
    if error is not None:
        return error
    assert payload is not None

    validated, details = _validate_pool_payload(payload, partial=True)
    if details:
        return _validation_error_response(details)
    assert validated is not None

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        pool = session.query(LotteryPool).filter(LotteryPool.id == pool_id).first()
        if pool is None:
            return api_error(status_code=404, code="not_found", message="奖池不存在")
        if "name" in validated and validated["name"] != pool.name:
            dup = session.query(LotteryPool).filter(LotteryPool.name == validated["name"]).first()
            if dup is not None:
                # L-9：details message 与 top-level message 解耦。
                return api_error(
                    status_code=409, code="duplicate_name", message="奖池名称已存在",
                    details=[{"field": "name", "message": "该名称已被其他奖池占用"}],
                )
            pool.name = validated["name"]
        if "description" in validated:
            pool.description = validated["description"]
        if "sort_order" in validated:
            pool.sort_order = int(validated["sort_order"])
        if "enabled" in validated:
            pool.enabled = bool(validated["enabled"])
        if "cost_per_draw" in validated:
            pool.cost_per_draw = int(validated["cost_per_draw"])
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            logger.warning(
                f"更新奖池失败：pool_id={pool_id} reason=name 并发重复 client_ip={client_ip}"
            )
            return api_error(
                status_code=409, code="duplicate_name", message="奖池名称已存在",
                details=[{"field": "name", "message": "该名称已被其他奖池占用"}],
            )
        prize_count = (
            session.query(LotteryPrize).filter(LotteryPrize.pool_id == pool_id).count()
        )
        logger.info(
            f"更新奖池成功：pool_id={pool.id} name={pool.name!r} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data=_serialize_pool(pool, prize_count=prize_count))
    finally:
        session.close()


@router.delete("/webui/api/lottery/{pool_id}")
async def delete_pool(pool_id: int, request: Request) -> JSONResponse:
    # H-3：删除属于高危状态变更，WARN 级日志 + 完整 IP/UA。
    # H-5：try/except 显式 rollback，避免异常吞没导致 prize 残留。
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        pool = session.query(LotteryPool).filter(LotteryPool.id == pool_id).first()
        if pool is None:
            logger.warning(
                f"删除奖池失败：pool_id={pool_id} reason=奖池不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(status_code=404, code="not_found", message="奖池不存在")
        pool_name = str(pool.name)
        prize_count = session.query(LotteryPrize).filter(LotteryPrize.pool_id == pool_id).count()
        try:
            session.query(LotteryPrize).filter(LotteryPrize.pool_id == pool_id).delete(
                synchronize_session=False
            )
            session.delete(pool)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception(
                f"删除奖池异常：pool_id={pool_id} reason={exc} "
                f"client_ip={client_ip}"
            )
            raise
        logger.warning(
            f"删除奖池成功：pool_id={pool_id} name={pool_name!r} "
            f"prize_count={prize_count} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data={"id": pool_id})
    finally:
        session.close()


@router.post("/webui/api/lottery/{pool_id}/prizes")
async def create_prize(pool_id: int, request: Request) -> JSONResponse:
    payload, error = await read_json_object(request)
    if error is not None:
        return error
    assert payload is not None

    server_ids = _load_server_id_set()
    validated, details = _validate_prize_payload(payload, valid_server_ids=server_ids)
    if details:
        return _validation_error_response(details)
    assert validated is not None

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        pool = session.query(LotteryPool).filter(LotteryPool.id == pool_id).first()
        if pool is None:
            return api_error(status_code=404, code="not_found", message="奖池不存在")

        # C-1：录入侧强约束「同奖池 enabled prize 已设置权重之和 ≤ 100」，
        # 避免前端展示与落地实际概率不一致（业务公平性）。
        new_weight = validated["weight"]
        new_enabled = bool(validated["enabled"])
        if new_enabled and new_weight is not None:
            existing_sum = _existing_weight_sum(session, pool_id)
            projected = existing_sum + float(new_weight)
            if projected > 100.0 + 1e-9:
                logger.warning(
                    f"创建奖品失败：pool_id={pool_id} reason=权重越界 "
                    f"existing_sum={existing_sum} new={new_weight} "
                    f"client_ip={client_ip}"
                )
                return api_error(
                    status_code=422,
                    code="weight_sum_exceeded",
                    message=f"同奖池已设置权重之和会超过 100（当前 {existing_sum:g}，新加 {new_weight}）",
                    details=[{
                        "field": "weight",
                        "message": f"同奖池已设置权重之和会超过 100（当前 {existing_sum:g}）",
                    }],
                )

        prize = LotteryPrize(
            pool_id=pool_id,
            sort_order=validated["sort_order"],
            name=validated["name"],
            description=validated["description"],
            kind=validated["kind"],
            enabled=validated["enabled"],
            weight=validated["weight"],
            item_id=validated["item_id"],
            prefix_id=validated["prefix_id"],
            quantity=validated["quantity"],
            min_tier=validated["min_tier"],
            actual_value=validated["actual_value"],
            is_mystery=validated["is_mystery"],
            target_server_id=validated["target_server_id"],
            command_template=validated["command_template"],
            show_command=validated["show_command"],
            require_online=validated["require_online"],
            coin_amount=validated["coin_amount"],
        )
        session.add(prize)
        session.commit()
        session.refresh(prize)
        label_map = _load_server_label_map()
        target_label = (
            label_map.get(int(prize.target_server_id))
            if prize.target_server_id is not None else None
        )
        logger.info(
            f"创建奖品成功：pool_id={pool_id} prize_id={prize.id} "
            f"name={prize.name!r} kind={prize.kind} weight={prize.weight} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(
            status_code=201,
            data=_serialize_prize(prize, target_server_label=target_label),
            headers={"Location": f"/webui/api/lottery/{pool_id}/prizes/{prize.id}"},
        )
    finally:
        session.close()


@router.put("/webui/api/lottery/{pool_id}/prizes/{prize_id}")
async def update_prize(pool_id: int, prize_id: int, request: Request) -> JSONResponse:
    payload, error = await read_json_object(request)
    if error is not None:
        return error
    assert payload is not None

    server_ids = _load_server_id_set()
    validated, details = _validate_prize_payload(payload, valid_server_ids=server_ids)
    if details:
        return _validation_error_response(details)
    assert validated is not None

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        prize = (
            session.query(LotteryPrize)
            .filter(LotteryPrize.id == prize_id, LotteryPrize.pool_id == pool_id)
            .first()
        )
        if prize is None:
            return api_error(status_code=404, code="not_found", message="奖品不存在")

        # C-1：更新路径同步校验权重之和，排除自身。
        new_weight = validated["weight"]
        new_enabled = bool(validated["enabled"])
        if new_enabled and new_weight is not None:
            existing_sum = _existing_weight_sum(session, pool_id, exclude_prize_id=prize_id)
            projected = existing_sum + float(new_weight)
            if projected > 100.0 + 1e-9:
                logger.warning(
                    f"更新奖品失败：pool_id={pool_id} prize_id={prize_id} "
                    f"reason=权重越界 existing_sum={existing_sum} new={new_weight} "
                    f"client_ip={client_ip}"
                )
                return api_error(
                    status_code=422,
                    code="weight_sum_exceeded",
                    message=f"同奖池已设置权重之和会超过 100（当前 {existing_sum:g}，新值 {new_weight}）",
                    details=[{
                        "field": "weight",
                        "message": f"同奖池已设置权重之和会超过 100（当前 {existing_sum:g}）",
                    }],
                )

        prize.sort_order = validated["sort_order"]
        prize.name = validated["name"]
        prize.description = validated["description"]
        prize.kind = validated["kind"]
        prize.enabled = validated["enabled"]
        prize.weight = validated["weight"]
        prize.item_id = validated["item_id"]
        prize.prefix_id = validated["prefix_id"]
        prize.quantity = validated["quantity"]
        prize.min_tier = validated["min_tier"]
        prize.actual_value = validated["actual_value"]
        prize.is_mystery = validated["is_mystery"]
        prize.target_server_id = validated["target_server_id"]
        prize.command_template = validated["command_template"]
        prize.show_command = validated["show_command"]
        prize.require_online = validated["require_online"]
        prize.coin_amount = validated["coin_amount"]
        session.commit()
        label_map = _load_server_label_map()
        target_label = (
            label_map.get(int(prize.target_server_id))
            if prize.target_server_id is not None else None
        )
        logger.info(
            f"更新奖品成功：pool_id={pool_id} prize_id={prize.id} "
            f"name={prize.name!r} kind={prize.kind} weight={prize.weight} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data=_serialize_prize(prize, target_server_label=target_label))
    finally:
        session.close()


@router.delete("/webui/api/lottery/{pool_id}/prizes/{prize_id}")
async def delete_prize(pool_id: int, prize_id: int, request: Request) -> JSONResponse:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        prize = (
            session.query(LotteryPrize)
            .filter(LotteryPrize.id == prize_id, LotteryPrize.pool_id == pool_id)
            .first()
        )
        if prize is None:
            logger.warning(
                f"删除奖品失败：pool_id={pool_id} prize_id={prize_id} "
                f"reason=奖品不存在 client_ip={client_ip}"
            )
            return api_error(status_code=404, code="not_found", message="奖品不存在")
        prize_name = str(prize.name)
        prize_kind = str(prize.kind)
        session.delete(prize)
        session.commit()
        logger.info(
            f"删除奖品成功：pool_id={pool_id} prize_id={prize_id} "
            f"name={prize_name!r} kind={prize_kind} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data={"id": prize_id})
    finally:
        session.close()
