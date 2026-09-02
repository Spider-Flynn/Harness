"""本仓库 data 目录中的项目索引、锁与备份。"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import HarnessError
from .filesystem import atomic_write, file_lock, read_json
from .paths import HARNESS_ROOT


REGISTRY_SCHEMA_VERSION = 1


def _state_root() -> Path:
    return HARNESS_ROOT / "data"


@contextmanager
def project_lock(project: Path) -> Iterator[None]:
    key = hashlib.sha256(str(project).encode("utf-8")).hexdigest()
    with file_lock(_state_root() / "locks" / f"{key}.lock"):
        yield


def _registry_path() -> Path:
    return _state_root() / "projects.json"


def _load_registry_unlocked() -> dict[str, Any]:
    path = _registry_path()
    if not path.exists():
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "projects": {}}
    registry = read_json(path, label="本机绑定索引")
    if (
        registry.get("schema_version") != REGISTRY_SCHEMA_VERSION
        or not isinstance(registry.get("projects"), dict)
    ):
        raise HarnessError(f"本机绑定索引格式无效：{path}")
    return registry


def load_registry() -> dict[str, Any]:
    with file_lock(_state_root() / "locks" / "registry.lock"):
        return _load_registry_unlocked()


def _registry_for_update_unlocked() -> tuple[dict[str, Any], str | None]:
    try:
        return _load_registry_unlocked(), None
    except HarnessError as exc:
        source = _registry_path()
        if not source.exists() and not source.is_symlink():
            return {"schema_version": REGISTRY_SCHEMA_VERSION, "projects": {}}, str(exc)
        if source.is_symlink() or not source.is_file():
            raise HarnessError(f"本机绑定索引不是普通文件：{source}") from exc
        backup_root = _state_root() / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        suffix = 0
        while True:
            extra = f"-{suffix}" if suffix else ""
            backup = backup_root / f"projects.invalid-{os.getpid()}{extra}.json"
            if not backup.exists() and not backup.is_symlink():
                break
            suffix += 1
        os.replace(source, backup)
        notice = f"损坏的本机绑定索引已隔离到 {backup}；正在重建当前索引"
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "projects": {}}, notice


def registry_recovery_hint() -> str:
    path = _registry_path()
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return f"请先将占用 {path} 的非普通文件移走，再运行 relink"
    return "请确认索引路径可写；普通文件内容损坏时，运行 relink 会隔离并重建"


def register_binding(
    project: Path, manifest: dict[str, Any], system_name: str
) -> str | None:
    try:
        with file_lock(_state_root() / "locks" / "registry.lock"):
            registry, notice = _registry_for_update_unlocked()
            registry["projects"][str(project)] = {
                "system_id": manifest["system_id"],
                "system_name": system_name,
                "source_root": str(HARNESS_ROOT),
            }
            atomic_write(
                _registry_path(),
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            )
            return notice
    except (HarnessError, OSError, UnicodeError) as exc:
        return (
            f"本机绑定索引未更新，不影响目标项目绑定：{exc}；"
            f"{registry_recovery_hint()}"
        )


def unregister_binding(project: Path) -> str | None:
    try:
        with file_lock(_state_root() / "locks" / "registry.lock"):
            registry, notice = _registry_for_update_unlocked()
            registry["projects"].pop(str(project), None)
            atomic_write(
                _registry_path(),
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            )
            return notice
    except (HarnessError, OSError, UnicodeError) as exc:
        return (
            f"本机绑定索引未更新，不影响目标项目解绑：{exc}；"
            f"{registry_recovery_hint()}"
        )
