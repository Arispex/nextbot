from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from nextbot.time_utils import beijing_filename_timestamp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@contextlib.asynccontextmanager
async def temp_screenshot_path(
    prefix: str, *, suffix: str = ".png"
) -> AsyncIterator[Path]:
    """生成 /tmp 下一个唯一截图路径，退出时自动清理。

    用法：
        async with temp_screenshot_path("user-info-{user_id}") as path:
            await screenshot_url(url, path, options=...)
            await bot.send(event, image_segment(path))

    退出时不论是否异常，文件都会被 unlink（missing_ok=True 不抛错）。

    PQA-3.1 / PQB-X.3：文件名追加 8 位 uuid 后缀，避免秒级时间戳在并发场景
    下同 prefix 碰撞导致 cleanup 删除并发请求的源文件 / 写入互相覆盖。
    """
    path = (
        Path("/tmp")
        / f"{prefix}-{beijing_filename_timestamp()}-{uuid.uuid4().hex[:8]}{suffix}"
    )
    try:
        yield path
    finally:
        # 清理失败不影响主流程
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
