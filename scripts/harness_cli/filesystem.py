"""路径检查、原子写入、文件锁与软链接操作。"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import HarnessError
from .paths import HARNESS_ROOT


def atomic_write(path: Path, content: str, *, new_mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise HarnessError(f"拒绝通过符号链接写入受管文件：{path}")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else new_mode
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), mode)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def project_path(raw: str) -> Path:
    project = Path(raw).expanduser().resolve()
    forbidden = {Path("/"), Path.home().resolve(), HARNESS_ROOT, HARNESS_ROOT.parent}
    if project in forbidden:
        raise HarnessError(f"拒绝把宽泛目录或 Harness 源目录作为目标项目：{project}")
    if not project.is_dir():
        raise HarnessError(f"目标项目目录不存在：{project}")
    return project


def _assert_no_symlink_parents(project: Path, relative: Path) -> None:
    current = project
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise HarnessError(f"受管路径父目录不能是符号链接：{current}")


def managed_path(project: Path, relative: Path | str) -> Path:
    normalized = Path(relative)
    if normalized.is_absolute() or not normalized.parts or ".." in normalized.parts:
        raise HarnessError(f"受管路径必须位于目标项目内：{relative}")
    _assert_no_symlink_parents(project, normalized)
    return project / normalized


def safe_relative(raw: Any, *, label: str) -> Path:
    path = Path(str(raw))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise HarnessError(f"{label} 必须是 Harness 仓库内相对路径：{raw}")
    return path


def read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"无法读取{label}：{path}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"{label}根节点必须是对象：{path}")
    return value


def replace_symlink(link: Path, source: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        raise HarnessError(f"受管链接路径已有普通内容，拒绝覆盖：{link}")
    temporary = link.with_name(f".{link.name}.harness-link-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise HarnessError(f"临时链接路径被占用：{temporary}")
    try:
        temporary.symlink_to(source, target_is_directory=source.is_dir())
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def link_target(link: Path) -> str | None:
    if link.is_symlink():
        return os.readlink(link)
    if link.exists():
        raise HarnessError(f"受管链接路径已被普通内容替换：{link}")
    return None


def resolved_link(link: Path) -> Path:
    target = Path(os.readlink(link))
    return target.resolve() if target.is_absolute() else (link.parent / target).resolve()


def restore_link(link: Path, target: str | None) -> None:
    if link.exists() or link.is_symlink():
        if not link.is_symlink():
            raise HarnessError(f"回滚时受管链接被普通内容占用：{link}")
        link.unlink()
    if target is not None:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)


def remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
