"""存储访问与安全边界。

本项目**不直连 115**。115 的内容通过 CloudDrive2 暴露：
- 首选：CD2 的挂载点，对进程来说就是普通本地路径，读写走文件系统。
- 备用：CD2 虚拟路径（`/115open/...`），由 `Cd2PathMapper` 映射到挂载点。

所有路径操作先经 `RootGuard` 解析真实路径并校验落在允许根目录内，
符号链接逃逸与越界路径都会被拒绝。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path

from server.classify import is_video_file


class StorageError(Exception):
    """存储层错误基类。"""


class OutsideAllowedRoots(StorageError):
    """路径不在允许的根目录内。"""


class WritesDisabled(StorageError):
    """写入未开启（dry_run 或 organize_enabled=False）。"""


class Cd2PathError(StorageError):
    """CD2 虚拟路径与挂载路径的映射失败。"""


class RootGuard:
    """路径白名单。所有落盘与遍历都要先过这一关。"""

    def __init__(self, roots: Sequence[Path]) -> None:
        resolved: list[Path] = []
        for root in roots:
            try:
                resolved.append(Path(root).expanduser().resolve(strict=False))
            except OSError:
                continue
        self._roots = resolved

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def check(self, path: str | Path) -> Path:
        """返回解析后的真实路径，越界则抛 `OutsideAllowedRoots`。"""
        candidate = Path(path).expanduser()
        try:
            real = candidate.resolve(strict=False)
        except OSError as exc:
            raise StorageError(f"路径无法解析: {candidate}") from exc
        if not self._roots:
            raise OutsideAllowedRoots("未配置任何允许根目录，拒绝访问")
        for root in self._roots:
            if real == root or root in real.parents:
                return real
        raise OutsideAllowedRoots(f"路径 {real} 不在允许的根目录内")

    def contains(self, path: str | Path) -> bool:
        try:
            self.check(path)
        except StorageError:
            return False
        return True


class LocalStorage:
    """文件系统访问。读操作始终允许，写操作受 `allow_writes` 控制。"""

    def __init__(self, guard: RootGuard, *, allow_writes: bool = False) -> None:
        self._guard = guard
        self._allow_writes = allow_writes

    @property
    def guard(self) -> RootGuard:
        return self._guard

    @property
    def writes_enabled(self) -> bool:
        return self._allow_writes

    def set_writes_enabled(self, value: bool) -> None:
        self._allow_writes = value

    def _require_writes(self) -> None:
        if not self._allow_writes:
            raise WritesDisabled("写入未开启（dry_run 或 organize_enabled=False）")

    # ---------------- 读 ----------------

    def exists(self, path: str | Path) -> bool:
        try:
            return self._guard.check(path).exists()
        except StorageError:
            return False

    def stat(self, path: str | Path) -> os.stat_result:
        return self._guard.check(path).stat()

    def size(self, path: str | Path) -> int:
        return self.stat(path).st_size

    def list_dir(self, path: str | Path) -> list[Path]:
        target = self._guard.check(path)
        if not target.is_dir():
            return []
        try:
            return sorted(target.iterdir(), key=lambda p: p.name)
        except OSError:
            return []

    def iter_videos(self, root: str | Path, *, recursive: bool = True) -> Iterator[Path]:
        """遍历视频文件。跳过隐藏目录与回收站目录。"""
        base = self._guard.check(root)
        if not base.is_dir():
            return
        pattern = "**/*" if recursive else "*"
        for path in sorted(base.glob(pattern)):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(base).parts[:-1]):
                continue
            if is_video_file(path.name):
                yield path

    # ---------------- 写 ----------------

    def mkdir(self, path: str | Path) -> Path:
        self._require_writes()
        target = self._guard.check(path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def move(self, src: str | Path, dst: str | Path) -> Path:
        self._require_writes()
        source = self._guard.check(src)
        target = self._guard.check(dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return target

    def replace(self, src: str | Path, dst: str | Path) -> Path:
        """目标存在时覆盖。"""
        self._require_writes()
        source = self._guard.check(src)
        target = self._guard.check(dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        os.replace(str(source), str(target))
        return target

    def write_text(self, path: str | Path, content: str, *, encoding: str = "utf-8") -> Path:
        self._require_writes()
        target = self._guard.check(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        return target

    def write_bytes(self, path: str | Path, content: bytes) -> Path:
        self._require_writes()
        target = self._guard.check(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target


class Cd2PathMapper:
    """CD2 虚拟路径 <-> 本地挂载路径。

    CD2 的 webhook / gRPC 给的是虚拟路径（`/115open/云下载/a.mp4`），
    进程能读的是挂载点（`/mnt/cd2/云下载/a.mp4`）。映射靠配置的
    (虚拟前缀, 挂载点) 对做**最长前缀匹配**。虚拟路径是 POSIX 分段字符串，
    在 Windows 上同样如此，因此一律用 posix 语义处理，不交给 pathlib.Path 解析。
    """

    def __init__(self, mappings: Sequence[tuple[str, Path]]) -> None:
        pairs: list[tuple[str, Path]] = []
        for virtual, mount in mappings:
            normalized = "/" + virtual.strip("/")
            if normalized == "/":
                continue
            pairs.append((normalized, Path(mount)))
        # 长的前缀优先
        self._pairs = sorted(pairs, key=lambda item: len(item[0]), reverse=True)

    @property
    def mappings(self) -> list[tuple[str, Path]]:
        return list(self._pairs)

    def to_local(self, virtual_path: str) -> Path:
        posix = "/" + str(virtual_path).strip("/")
        for virtual, mount in self._pairs:
            if posix == virtual:
                return mount
            if posix.startswith(virtual + "/"):
                relative = posix[len(virtual) + 1 :]
                return mount.joinpath(*relative.split("/"))
        raise Cd2PathError(f"虚拟路径 {virtual_path} 没有匹配的挂载点")

    def to_virtual(self, local_path: str | Path) -> str:
        target = Path(local_path)
        for virtual, mount in self._pairs:
            try:
                relative = target.relative_to(mount)
            except ValueError:
                continue
            parts = relative.as_posix().strip("/")
            return f"{virtual}/{parts}" if parts else virtual
        raise Cd2PathError(f"本地路径 {local_path} 不在任何 CD2 挂载点内")
