#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# 命中即视为潜在敏感文件，立刻终止打包。
_SECRET_PATTERNS: tuple[str, ...] = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    ".env",
    ".env.*",
    "id_rsa",
    "id_rsa.*",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_ed25519.*",
    "*credentials*",
    "*secret*",
    "*token*",
    ".webui_auth.json",
    "app.db",
    "app.db-*",
)


def _format_size(num_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(num_bytes)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.2f} {units[idx]}"


def _matches_secret(rel_path: Path) -> str | None:
    parts = rel_path.parts
    name = rel_path.name
    for pattern in _SECRET_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return pattern
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return pattern
    return None


def get_file_list(repo_root: Path) -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "-co",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=True,
    )
    raw_items = [item for item in result.stdout.decode("utf-8", errors="ignore").split("\0") if item]
    files: list[Path] = []
    for item in raw_items:
        path = repo_root / item
        if path.is_file():
            files.append(path)
    return files


def scan_secrets(repo_root: Path, files: list[Path]) -> list[tuple[Path, str]]:
    hits: list[tuple[Path, str]] = []
    for file_path in files:
        rel = file_path.relative_to(repo_root)
        matched = _matches_secret(rel)
        if matched:
            hits.append((rel, matched))
    return hits


def build_zip(repo_root: Path, output_path: Path, files: list[Path]) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(files)
    written = 0
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            arcname = file_path.relative_to(repo_root)
            zf.write(file_path, arcname)
            written += 1
            if written % 100 == 0 or written == total:
                print(f"[INFO] 已压缩 {written}/{total}", flush=True)
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="打包当前项目为 release zip (排除 ignore 文件)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="输出 zip 路径，默认在项目根目录自动生成",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="仅列出将要打包的文件，不实际生成 zip",
    )
    parser.add_argument(
        "--allow-secrets",
        action="store_true",
        help="跳过敏感文件 deny-list 检查 (危险，仅在确认无误时使用)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent

    try:
        files = get_file_list(repo_root)
    except FileNotFoundError:
        print("[ERROR] 找不到 git 命令，请先安装 git", file=sys.stderr, flush=True)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"[ERROR] 获取文件列表失败，cause={exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    if not args.allow_secrets:
        hits = scan_secrets(repo_root, files)
        if hits:
            print("[ERROR] 检测到疑似敏感文件，终止打包：", file=sys.stderr, flush=True)
            for rel, pattern in hits:
                print(f"  - {rel} (匹配 {pattern})", file=sys.stderr, flush=True)
            print(
                "[INFO] 若确认无敏感信息，可加 --allow-secrets 跳过该检查",
                file=sys.stderr,
                flush=True,
            )
            return 1

    if args.list:
        for file_path in files:
            print(file_path.relative_to(repo_root))
        print(f"[INFO] 共 {len(files)} 个文件")
        return 0

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = repo_root / f"release-{timestamp}.zip"

    try:
        file_count = build_zip(repo_root, output_path, files)
    except OSError as exc:
        print(f"[ERROR] 写入 zip 失败，cause={exc}", file=sys.stderr, flush=True)
        return 1

    try:
        zip_size = output_path.stat().st_size
        size_text = _format_size(zip_size)
    except OSError:
        size_text = "未知"

    print(f"[INFO] 打包完成：{output_path}")
    print(f"[INFO] 文件数量：{file_count}")
    print(f"[INFO] 文件大小：{size_text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
