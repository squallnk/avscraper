"""路径模板渲染与落盘（默认关闭）。

模板语法（把 MDC-NG 与 Amane 里最实用的三条挑出来实现，不做全集）：

- `{name}`          必填占位符，缺值渲染成空串
- `{name?}`         可空占位符
- `{name|a=b,c=d}`  值映射，未列出的值保持原样
- `[...]`           可选组：组内所有 `{x?}` 都为空时，整组连同方括号一起省略
- `[[...]]`         双层组：有值时保留方括号，无值时整块省略

例子：
    template = "{category}/{number}[-CD{cd?}][-{sub?}]"
    data     = {"category": "有码", "number": "MIDV-123", "sub": "C"}
    -> "有码/MIDV-123-C"

落盘走 `LocalStorage`，写操作受 `allow_writes` 控制；`organize_enabled=False` 时
只会算出目标路径，不会动任何文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from server.models import MediaMetadata
from server.storage import LocalStorage

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)(\?)?((?:\|[^}]*)?)\}")
_GROUP = re.compile(r"\[([^\[\]]*)\]")


class TemplateError(Exception):
    pass


def _apply_mapping(raw: str, mapping: str) -> str:
    """`|a=b,c=d` —— 只改写列出的值。`|=缺省` 把空值映成缺省。"""
    if not mapping:
        return raw
    for pair in mapping.lstrip("|").split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        if key.strip() == raw:
            return value
    return raw


def render_segment(template: str, data: dict[str, str]) -> str:
    """渲染一段模板（不含可选组）。"""

    def replace(match: re.Match[str]) -> str:
        name, optional, mapping = match.group(1), match.group(2), match.group(3) or ""
        value = data.get(name, "")
        value = _apply_mapping(value if value else "", mapping)
        if optional and not value:
            return ""
        return value

    return _PLACEHOLDER.sub(replace, template)


def _group_is_empty(group: str) -> bool:
    """组内所有可空占位符是否都为空。没有可空占位符的组视为非空。"""
    optionals = [m for m in _PLACEHOLDER.finditer(group) if m.group(2)]
    if not optionals:
        return False
    return all(not (m.group(3) or "") for m in optionals) and not any(
        True for _ in optionals
    ) or False


def _group_has_value(group: str, data: dict[str, str]) -> bool:
    """组内所有可空占位符都为空时，整组省略。不含可空占位符的组始终保留。"""
    optionals = [m for m in _PLACEHOLDER.finditer(group) if m.group(2)]
    if not optionals:
        return True
    return any(bool(data.get(m.group(1), "")) for m in optionals)


_TOKEN = "\x00G{}\x00"


def render(template: str, data: dict[str, str]) -> str:
    """渲染完整模板。

    先按**内层到外层**把可选组替换成不可见占位符，再统一替换变量，
    最后还原占位符。这样做的原因是：如果先替换变量再处理方括号，
    `[[{def?}]]` 会被单层组的规则再吃一次，把方括号吞掉。
    """
    if template.count("[") != template.count("]"):
        raise TemplateError(f"模板方括号不配对: {template}")

    stashed: list[str] = []

    def stash(match: re.Match[str], *, keep_brackets: bool) -> str:
        inner = match.group(1)
        if _group_has_value(inner, data):
            body = render_segment(inner, data)
            value = f"[{body}]" if keep_brackets else body
        else:
            value = ""
        token = _TOKEN.format(len(stashed))
        stashed.append(value)
        return token

    text = re.sub(r"\[\[([^\[\]]*)\]\]", lambda m: stash(m, keep_brackets=True), template)
    text = re.sub(r"\[([^\[\]]*)\]", lambda m: stash(m, keep_brackets=False), text)

    if "[" in text or "]" in text:
        raise TemplateError(f"模板有未配对的方括号: {template}")

    rendered = render_segment(text, data)
    for index, value in enumerate(stashed):
        rendered = rendered.replace(_TOKEN.format(index), value)

    rendered = re.sub(r"[-_ ]{2,}", "-", rendered)
    return rendered.strip(" -_/") or "unknown"


def build_template_data(metadata: MediaMetadata, *, category: str = "") -> dict[str, str]:
    """把元数据摊平成模板变量。"""
    data: dict[str, str] = {
        "number": metadata.number or "",
        "title": metadata.title or "",
        "original_title": metadata.original_title or "",
        "actor": metadata.actors[0] if metadata.actors else "",
        "actors": ",".join(metadata.actors),
        "studio": metadata.studio or "",
        "publisher": metadata.publisher or "",
        "series": metadata.series or "",
        "year": str(metadata.year) if metadata.year else "",
        "release": metadata.release_date or "",
        "runtime": str(metadata.runtime) if metadata.runtime else "",
        "category": category,
    }
    if data["number"]:
        prefix, _, suffix = data["number"].partition("-")
        data["prefix"] = prefix
        data["suffix"] = suffix
    else:
        data["prefix"] = ""
        data["suffix"] = ""
    return data


@dataclass
class OrganizePlan:
    """一次整理的落盘计划。生成计划本身**不做任何写操作**。"""

    source: Path
    target_dir: Path
    target_name: str
    dry_run: bool

    @property
    def target(self) -> Path:
        return self.target_dir / self.target_name


class Organizer:
    def __init__(
        self,
        storage: LocalStorage,
        *,
        directory_template: str,
        filename_template: str,
    ) -> None:
        self._storage = storage
        self._directory_template = directory_template
        self._filename_template = filename_template

    def plan(
        self,
        source: Path,
        metadata: MediaMetadata,
        *,
        library_root: Path,
        category: str = "",
        dry_run: bool = True,
    ) -> OrganizePlan:
        data = build_template_data(metadata, category=category)
        relative_dir = render(self._directory_template, data)
        name = render(self._filename_template, data)
        suffix = source.suffix or ".mp4"
        return OrganizePlan(
            source=source,
            target_dir=library_root.joinpath(*relative_dir.split("/")),
            target_name=f"{name}{suffix}",
            dry_run=dry_run,
        )

    def execute(self, plan: OrganizePlan) -> Path | None:
        """执行计划。dry_run 或未开启写入时返回 None。"""
        if plan.dry_run or not self._storage.writes_enabled:
            return None
        if plan.source == plan.target:
            return plan.target
        self._storage.mkdir(plan.target_dir)
        if plan.target.exists():
            raise FileExistsError(f"目标已存在: {plan.target}")
        return self._storage.move(plan.source, plan.target)
