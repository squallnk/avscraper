"""HTML 取值助手。

刮削源最容易出 bug 的地方是"选择器一夜之间失效"。这里的做法是：
- 每个字段给**多个候选选择器**，按顺序取第一个非空值；
- 全部落空时返回 None，而不是抛异常 —— 由源自己决定缺失字段是否致命；
- 解析函数写成纯函数（html -> 模型），便于用离线固件做回归测试。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from bs4 import BeautifulSoup, Tag

Whitespace = re.compile(r"\s+")


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def first_node(root: Tag | BeautifulSoup, selectors: Iterable[str]) -> Tag | None:
    for selector in selectors:
        try:
            node = root.select_one(selector)
        except Exception:  # noqa: BLE001 - 选择器写错不应该炸掉整次刮削
            continue
        if node is not None:
            return node
    return None


def first_text(root: Tag | BeautifulSoup, selectors: Iterable[str]) -> str | None:
    node = first_node(root, selectors)
    if node is None:
        return None
    value = Whitespace.sub(" ", node.get_text(" ", strip=True))
    return value or None


def first_attr(root: Tag | BeautifulSoup, selectors: Iterable[str], attr: str) -> str | None:
    node = first_node(root, selectors)
    if node is None:
        return None
    value = node.get(attr)
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value else None


def all_texts(root: Tag | BeautifulSoup, selectors: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for selector in selectors:
        try:
            nodes = root.select(selector)
        except Exception:  # noqa: BLE001
            continue
        for node in nodes:
            value = Whitespace.sub(" ", node.get_text(" ", strip=True))
            if value and value not in seen:
                seen.append(value)
        if seen:
            break
    return seen


def labeled_value(root: Tag | BeautifulSoup, label: str) -> str | None:
    """在 javbus 风格的 `<p><span class="header">品番:</span>值</p>` 里取值。"""
    for p in root.select("p"):
        header = p.select_one("span.header")
        if header is None:
            continue
        text = Whitespace.sub("", header.get_text(strip=True)).rstrip(":：")
        if text == label:
            full = Whitespace.sub(" ", p.get_text(" ", strip=True))
            return full.replace(header.get_text(strip=True), "", 1).strip(" :：") or None
    return None


def absolute(url: str | None, base: str) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base.rstrip("/") + url
    return url
