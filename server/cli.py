"""命令行入口：自检、扫描、单文件刮削、配置读写。

`selftest` 是**只读**的：只打印将要做的事，不动任何文件。
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app import build_context, register_handlers, shutdown_context
from server.classify import classify
from server.config import RuntimeConfig, Settings
from server.models import CONTENT_TYPE_LABELS
from server.organize import render
from server.pipeline import run_scan_and_scrape, scrape_one
from server.sources import all_sources
from server.tasks import Task


async def _with_ctx(fn: Callable[[Any], Any]) -> int:
    """建好上下文执行命令。fn 可以是同步也可以是异步。"""
    ctx = await build_context(Settings())
    register_handlers(ctx)
    try:
        result = fn(ctx)
        if inspect.isawaitable(result):
            result = await result
        return int(result)
    finally:
        await shutdown_context(ctx)


def cmd_selftest(ctx: Any) -> int:
    print("== avscraper 自检（只读） ==")
    print(f"数据目录        : {ctx.settings.data_dir}")
    print(f"数据库          : {ctx.db.path}")
    print(f"允许根目录      : {[str(r) for r in ctx.storage.guard.roots] or '（未配置：禁止任何写入）'}")
    print(f"dry_run         : {ctx.config.dry_run}")
    print(f"organize_enabled: {ctx.config.organize_enabled}")
    print(f"写入是否开启    : {ctx.storage.writes_enabled}")
    print(f"注册源          : {', '.join(p.id for p in all_sources())}")
    print(f"HTTP 限速       : {ctx.config.http_rate}/s，突发 {ctx.config.http_burst}")
    print(f"文件操作限速    : {ctx.config.file_op_rate}/s，突发 {ctx.config.file_op_burst}")
    threshold = ctx.config.failure_threshold
    cooldown = ctx.config.cooldown_seconds
    print(f"熔断阈值        : 连续 {threshold} 次失败，冷却 {cooldown}s")
    for root in ctx.storage.guard.roots:
        exists = "存在" if Path(root).is_dir() else "不存在"
        print(f"  - {root} {exists}")
    if not ctx.storage.guard.roots:
        print("提示：未配置 AVS_ALLOWED_ROOTS，扫描与写入都会被拒绝。")
    print("自检结束：未修改任何文件。")
    return 0


def cmd_classify(ctx: Any, args: argparse.Namespace) -> int:
    result = classify(args.path)
    print(json.dumps({
        "number": result.number,
        "content_type": result.content_type.value,
        "label": CONTENT_TYPE_LABELS.get(result.content_type, ""),
        "confidence": result.confidence,
        "evidence": result.evidence,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_render(ctx: Any, args: argparse.Namespace) -> int:
    data = json.loads(args.data) if args.data else {}
    print(render(args.template, {k: str(v) for k, v in data.items()}))
    return 0


async def cmd_scan(ctx: Any, args: argparse.Namespace) -> int:
    task = Task(kind="scan_and_scrape", payload={
        "roots": args.root or [],
        "recursive": not args.no_recursive,
        "limit": args.limit,
        "skip_known": not args.all,
        "write_metadata": args.write_metadata,
        "metadata_dir": args.metadata_dir,
    })
    if args.dry_run:
        ctx.storage.set_writes_enabled(False)
    await run_scan_and_scrape(ctx, task)
    print(json.dumps({"total": task.total, **task.result}, ensure_ascii=False, indent=2))
    return 0


async def cmd_scrape(ctx: Any, args: argparse.Namespace) -> int:
    record = await scrape_one(ctx, Path(args.path))
    print(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2)[:4000])
    return 0


async def cmd_capture(ctx: Any, args: argparse.Namespace) -> int:
    """抓一份真实页面存成固件，供离线回归使用。

    这是**开发工具**，写出路径由 `--out` 显式指定，不经过允许根目录校验，
    因为它写的是仓库里的测试固件，不是媒体库。
    """
    result = await ctx.http.get(args.url, source="capture")
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.text, encoding="utf-8")
    print(f"已保存 {len(result.text)} 字节 -> {target}")
    return 0


async def cmd_cookie(ctx: Any, args: argparse.Namespace) -> int:
    """查看/设置/清除站点 Cookie。设置时用 `--file` 或 `--value` 传。"""
    if args.action == "list":
        for source_id, value in sorted(ctx.config.source_cookies.items()):
            print(f"{source_id:12} 已设置（{len(value)} 字符）")
        if not ctx.config.source_cookies:
            print("（没有任何站点 Cookie）")
        return 0

    from server.sources import get as source_by_id

    plugin = source_by_id(args.source)
    if plugin is None:
        print(f"未知数据源: {args.source}", file=sys.stderr)
        return 2

    if args.action == "clear":
        updated = ctx.config.with_patch({"source_cookies": {args.source: ""}})
        await ctx.save_config(updated)
        print(f"已清除 {args.source} 的 Cookie")
        return 0

    value = args.value or ""
    if args.file:
        value = Path(args.file).read_text(encoding="utf-8").strip()
    if not value:
        print("需要 --value 或 --file", file=sys.stderr)
        return 2
    updated = ctx.config.with_patch({"source_cookies": {args.source: value}})
    await ctx.save_config(updated)
    print(f"已设置 {args.source} 的 Cookie（{len(value)} 字符）")
    return 0


async def cmd_netcheck(ctx: Any, args: argparse.Namespace) -> int:
    """逐个源探测连通性。这是"首次实跑"第一步，用来确认容器能出去。"""
    import socket

    from server.sources import all_sources

    print("== 出网探测 ==")
    print(f"代理设置: {ctx.config.proxy or '（未配置，走系统路由）'}")
    print()
    failures = 0
    for plugin in all_sources():
        homepage = plugin.descriptor.homepage
        if not homepage:
            continue
        host = homepage.split("//", 1)[-1].split("/", 1)[0]
        try:
            resolved = socket.gethostbyname(host)
        except OSError as exc:
            resolved = f"解析失败({exc})"
        try:
            result = await ctx.http.get(homepage, source=plugin.id)
            verdict = "OK" if result.status == 200 else f"HTTP {result.status}"
            if result.status != 200:
                failures += 1
            print(f"  {plugin.id:11} {verdict:10} {result.elapsed_ms:6}ms  "
                  f"{len(result.content):8}B  {host} -> {resolved}")
        except Exception as exc:  # noqa: BLE001 - 探测就是把失败打出来
            failures += 1
            print(f"  {plugin.id:11} {'失败':10} {'-':>6}   {'-':>8}   {host} -> {resolved}")
            print(f"      {type(exc).__name__}: {exc}")
    print()
    if failures:
        print(f"有 {failures} 个源探测失败。常见原因：")
        print("  1. 容器没有继承宿主的路由（检查 docker 网络模式）")
        print("  2. DNS 被污染（显式把容器 DNS 指到旁路由）")
        print("  3. 需要代理（在设置里填 proxy）")
    else:
        print("全部连通。")
    return 1 if failures else 0


def cmd_sources(ctx: Any, args: argparse.Namespace) -> int:
    for plugin in all_sources():
        d = plugin.descriptor
        supports = "/".join(t.value for t in d.supports) or "-"
        marks = []
        if d.needs_proxy:
            marks.append("需代理")
        if d.needs_cookie:
            marks.append("需Cookie")
        print(f"{d.id:12} {d.name:14} 支持={supports:44} {'/'.join(marks):12} {d.note}")
    return 0


async def cmd_config(ctx: Any, args: argparse.Namespace) -> int:
    if args.set:
        key, _, value = args.set.partition("=")
        data = ctx.config.model_dump()
        if key not in data:
            print(f"未知配置项: {key}", file=sys.stderr)
            return 2
        try:
            data[key] = json.loads(value)
        except json.JSONDecodeError:
            data[key] = value
        await ctx.save_config(RuntimeConfig.model_validate(data))
    print(json.dumps(ctx.config.redacted(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avscraper", description="番号 / 里番元数据刮削器")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="只读自检")
    sub.add_parser("sources", help="列出已注册的源")
    sub.add_parser("netcheck", help="探测各源连通性（首次实跑第一步）")

    cookie = sub.add_parser("cookie", help="查看/设置/清除站点 Cookie")
    cookie.add_argument("action", choices=["list", "set", "clear"])
    cookie.add_argument("source", nargs="?", default="")
    cookie.add_argument("--value", default="")
    cookie.add_argument("--file", default="", help="从文件读取 Cookie（避免明文进命令行历史）")

    cap = sub.add_parser("capture", help="抓一份页面存成测试固件（开发工具）")
    cap.add_argument("url")
    cap.add_argument("--out", required=True, help="输出文件路径")

    scan = sub.add_parser("scan", help="扫描并刮削")
    scan.add_argument("--root", action="append", help="扫描根目录，可重复")
    scan.add_argument("--no-recursive", action="store_true")
    scan.add_argument("--limit", type=int, default=0)
    scan.add_argument("--all", action="store_true", help="包含已刮削过的文件")
    scan.add_argument("--write-metadata", action="store_true", help="写出 NFO/海报（需 organize_enabled）")
    scan.add_argument("--metadata-dir", default=None)
    scan.add_argument("--dry-run", action="store_true", default=True)

    one = sub.add_parser("scrape", help="刮削单个文件")
    one.add_argument("path")

    cls = sub.add_parser("classify", help="只看分类结果，不联网")
    cls.add_argument("path")

    rend = sub.add_parser("render", help="试渲染路径模板")
    rend.add_argument("template")
    rend.add_argument("--data", default="")

    cfg = sub.add_parser("config", help="查看或修改运行期配置")
    cfg.add_argument("--set", default=None, help="key=json值")

    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    mapping: dict[str, Callable[[], Any]] = {
        "selftest": lambda: _with_ctx(cmd_selftest),
        "sources": lambda: _with_ctx(lambda ctx: cmd_sources(ctx, args)),
        "netcheck": lambda: _with_ctx(lambda ctx: cmd_netcheck(ctx, args)),
        "capture": lambda: _with_ctx(lambda ctx: cmd_capture(ctx, args)),
        "cookie": lambda: _with_ctx(lambda ctx: cmd_cookie(ctx, args)),
        "classify": lambda: _with_ctx(lambda ctx: cmd_classify(ctx, args)),
        "render": lambda: _with_ctx(lambda ctx: cmd_render(ctx, args)),
        "scan": lambda: _with_ctx(lambda ctx: cmd_scan(ctx, args)),
        "scrape": lambda: _with_ctx(lambda ctx: cmd_scrape(ctx, args)),
        "config": lambda: _with_ctx(lambda ctx: cmd_config(ctx, args)),
    }
    handler = mapping.get(args.command)
    if handler is None:
        return 2
    return await handler()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    raise SystemExit(main())
