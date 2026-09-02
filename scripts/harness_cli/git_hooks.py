"""目标 Git 项目的受管 Hook 规划、安装、检查与恢复。"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import HarnessError
from .filesystem import atomic_write


HOOK_MARKER_PREFIX = "# HARNESS_MANAGED_GIT_HOOK_V1:"
LEGACY_HOOK_MARKERS = ("# HARNESS_MANAGED_PRE_COMMIT_V1",)
SUPPORTED_HOOKS = ("pre-commit", "prepare-commit-msg")


@dataclass(frozen=True)
class _PathSnapshot:
    kind: str
    content: bytes | None = None
    mode: int | None = None
    link_target: str | None = None


@dataclass(frozen=True)
class GitHookSnapshot:
    paths: dict[Path, _PathSnapshot]


def _run_git(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(project), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise HarnessError("无法执行 git，不能管理项目 Git Hook") from exc


def _git_path(project: Path, *arguments: str, label: str) -> Path:
    result = _run_git(project, *arguments)
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f"：{detail}" if detail else ""
        raise HarnessError(f"无法确定目标项目{label}{suffix}")
    return Path(value).resolve()


def git_hooks_directory(project: Path) -> Path | None:
    """返回当前项目独占的标准 Hook 目录；显式非 Git 项目返回 None。"""
    git_entry = project / ".git"
    if not git_entry.exists() and not git_entry.is_symlink():
        return None
    if git_entry.is_symlink():
        raise HarnessError("目标项目的 .git 是符号链接，拒绝接管其指向仓库的 Hook")

    inside = _run_git(project, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise HarnessError("目标目录存在 .git，但不是可管理的 Git 工作树")
    top_level = _git_path(
        project,
        "rev-parse",
        "--show-toplevel",
        label=" Git 工作树根目录",
    )
    if top_level != project.resolve():
        raise HarnessError("目标目录的 .git 指向其他工作树，拒绝接管其 Hook")

    configured_hooks = _run_git(project, "config", "--get", "core.hooksPath")
    if configured_hooks.returncode not in (0, 1):
        raise HarnessError("无法检查目标项目的 core.hooksPath")
    if configured_hooks.stdout.strip():
        raise HarnessError(
            "目标项目已经配置 core.hooksPath；为避免改动共享或自定义 Hook，"
            "当前版本拒绝自动接管"
        )

    git_directory = _git_path(
        project,
        "rev-parse",
        "--absolute-git-dir",
        label=" Git 目录",
    )
    common_directory = _git_path(
        project,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        label=" Git 公共目录",
    )
    if git_directory != common_directory:
        raise HarnessError(
            "目标项目是 linked worktree；其 Hook 会影响同仓库其他工作树，"
            "当前版本拒绝自动接管"
        )
    return git_directory / "hooks"


def _hook_path(hooks_directory: Path, hook_name: str) -> Path:
    if hook_name not in SUPPORTED_HOOKS:
        raise HarnessError(f"当前不支持的 Git Hook：{hook_name}")
    return hooks_directory / hook_name


def _backup_path(hooks_directory: Path, hook_name: str) -> Path:
    if hook_name not in SUPPORTED_HOOKS:
        raise HarnessError(f"当前不支持的 Git Hook：{hook_name}")
    return hooks_directory / f"{hook_name}.harness-original"


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _hook_marker(hook_name: str) -> str:
    return f"{HOOK_MARKER_PREFIX}{hook_name}"


def _managed_hook(path: Path, hook_name: str) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        content = path.read_text(encoding="utf-8")
        return _hook_marker(hook_name) in content or any(
            marker in content for marker in LEGACY_HOOK_MARKERS
        )
    except (OSError, UnicodeError):
        return False


def render_git_hook(hook_name: str, protected_branches: list[str]) -> str:
    branch_patterns = "|".join(protected_branches)
    branches_text = "、".join(protected_branches)
    backup_name = _backup_path(Path("."), hook_name).name
    return f"""#!/bin/sh
{_hook_marker(hook_name)}

branch_name="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
case "$branch_name" in
  {branch_patterns})
    printf '%s\n' "Harness：禁止在受保护分支 $branch_name 直接提交。" >&2
    printf '%s\n' "请先创建或切换到开发分支；当前未提交修改会保留。" >&2
    printf '%s\n' "受保护分支：{branches_text}" >&2
    exit 1
    ;;
esac

original_hook="$(dirname "$0")/{backup_name}"
if [ -x "$original_hook" ]; then
  "$original_hook" "$@"
  exit $?
fi
exit 0
"""


def _original_identity(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        return {"type": "symlink", "target": os.readlink(path)}
    if not path.exists():
        return None
    if not path.is_file():
        raise HarnessError(f"Git Hook 路径不是普通文件或符号链接：{path}")
    return {
        "type": "file",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mode": stat.S_IMODE(path.stat().st_mode),
    }


def _identity_matches(path: Path, expected: dict[str, Any] | None) -> bool:
    try:
        return _original_identity(path) == expected
    except (HarnessError, OSError):
        return False


def plan_git_hooks(
    project: Path,
    system: dict[str, Any],
    previous_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """根据领域策略与原绑定记录形成目标 Hook 记录，不修改文件。"""
    policies = system.get("git_hooks", {})
    previous = previous_records or {}
    if not policies:
        if previous:
            raise HarnessError("原绑定包含 Git Hook，但当前领域系统已不再声明")
        return {}

    hooks_directory = git_hooks_directory(project)
    if hooks_directory is None:
        if previous:
            raise HarnessError("目标项目已失去 Git 工作树，不能维护原 Git Hook")
        return {}

    records: dict[str, dict[str, Any]] = {}
    for hook_name, policy in policies.items():
        previous_record = previous.get(hook_name)
        hook = _hook_path(hooks_directory, hook_name)
        backup = _backup_path(hooks_directory, hook_name)
        if previous_record is None:
            if _path_exists(backup):
                raise HarnessError(f"Git Hook 备份路径已被占用，拒绝覆盖：{backup}")
            if _path_exists(hook) and hook.is_dir():
                raise HarnessError(f"Git Hook 路径被目录占用：{hook}")
            if _managed_hook(hook, hook_name):
                raise HarnessError(
                    f"发现没有绑定记录的 Harness {hook_name}；请先人工确认其来源"
                )
            original = _original_identity(hook)
        else:
            original = previous_record["original"]
        records[hook_name] = {
            "protected_branches": list(policy["protected_branches"]),
            "original": original,
        }
    return records


def _capture_path(path: Path) -> _PathSnapshot:
    if path.is_symlink():
        return _PathSnapshot(kind="symlink", link_target=os.readlink(path))
    if not path.exists():
        return _PathSnapshot(kind="missing")
    if not path.is_file():
        raise HarnessError(f"Git Hook 受管路径不是普通文件或符号链接：{path}")
    return _PathSnapshot(
        kind="file",
        content=path.read_bytes(),
        mode=stat.S_IMODE(path.stat().st_mode),
    )


def capture_git_hook_snapshot(
    project: Path, records: dict[str, dict[str, Any]]
) -> GitHookSnapshot | None:
    if not records:
        return None
    hooks_directory = git_hooks_directory(project)
    if hooks_directory is None:
        raise HarnessError("目标项目不是 Git 工作树，不能保存 Hook 原状态")
    paths: dict[Path, _PathSnapshot] = {}
    for hook_name in records:
        hook = _hook_path(hooks_directory, hook_name)
        backup = _backup_path(hooks_directory, hook_name)
        paths[hook] = _capture_path(hook)
        paths[backup] = _capture_path(backup)
    return GitHookSnapshot(paths=paths)


def _atomic_write_bytes(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            os.fchmod(target.fileno(), mode)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if _path_exists(temporary):
            temporary.unlink()


def restore_git_hook_snapshot(snapshot: GitHookSnapshot | None) -> None:
    if snapshot is None:
        return
    for path in snapshot.paths:
        if _path_exists(path):
            if path.is_dir() and not path.is_symlink():
                raise HarnessError(f"回滚时 Git Hook 路径被目录占用：{path}")
            path.unlink()
    for path, state in snapshot.paths.items():
        if state.kind == "missing":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if state.kind == "symlink":
            path.symlink_to(state.link_target)
        elif state.kind == "file":
            mode = state.mode if state.mode is not None else 0o600
            _atomic_write_bytes(path, state.content or b"", mode)


def _validate_apply_state(
    hooks_directory: Path,
    records: dict[str, dict[str, Any]],
    previous_records: dict[str, dict[str, Any]],
) -> None:
    for hook_name, record in records.items():
        hook = _hook_path(hooks_directory, hook_name)
        backup = _backup_path(hooks_directory, hook_name)
        previous_record = previous_records.get(hook_name)
        if previous_record is None:
            if _path_exists(backup):
                raise HarnessError(f"Git Hook 备份路径已被占用，拒绝覆盖：{backup}")
            if _path_exists(hook) and hook.is_dir():
                raise HarnessError(f"Git Hook 路径被目录占用：{hook}")
            if _managed_hook(hook, hook_name):
                raise HarnessError(f"发现没有绑定记录的 Harness {hook_name}")
            if _original_identity(hook) != record["original"]:
                raise HarnessError(f"Git Hook 在接入期间发生变化，拒绝覆盖：{hook}")
            continue

        if previous_record["original"] != record["original"]:
            raise HarnessError("Git Hook 原文件记录发生非法变化")
        if _path_exists(hook) and not _managed_hook(hook, hook_name):
            raise HarnessError(f"受管 Git Hook 已被其他内容替换，拒绝覆盖：{hook}")
        if record["original"] is not None:
            if not _identity_matches(backup, record["original"]):
                raise HarnessError(f"原 Git Hook 备份缺失或已变化：{backup}")
        elif _path_exists(backup):
            raise HarnessError(f"发现未记录的 Git Hook 备份，拒绝覆盖：{backup}")


def apply_git_hooks(
    project: Path,
    records: dict[str, dict[str, Any]],
    previous_records: dict[str, dict[str, Any]] | None = None,
) -> None:
    if not records:
        return
    previous = previous_records or {}
    hooks_directory = git_hooks_directory(project)
    if hooks_directory is None:
        raise HarnessError("目标项目不是 Git 工作树，不能安装 Git Hook")
    if hooks_directory.is_symlink() or (
        hooks_directory.exists() and not hooks_directory.is_dir()
    ):
        raise HarnessError(f"Git Hook 目录不可接管：{hooks_directory}")
    _validate_apply_state(hooks_directory, records, previous)
    hooks_directory.mkdir(parents=True, exist_ok=True)

    for hook_name, record in records.items():
        hook = _hook_path(hooks_directory, hook_name)
        backup = _backup_path(hooks_directory, hook_name)
        if hook_name not in previous and _path_exists(hook):
            os.replace(hook, backup)
        atomic_write(
            hook,
            render_git_hook(hook_name, record["protected_branches"]),
            new_mode=0o755,
        )
        hook.chmod(0o755)


def check_git_hooks(
    project: Path, records: dict[str, dict[str, Any]]
) -> list[str]:
    if not records:
        return []
    try:
        hooks_directory = git_hooks_directory(project)
    except HarnessError as exc:
        return [str(exc)]
    if hooks_directory is None:
        return ["目标项目不是 Git 工作树，受管 Git Hook 不可用"]

    errors: list[str] = []
    for hook_name, record in records.items():
        hook = _hook_path(hooks_directory, hook_name)
        backup = _backup_path(hooks_directory, hook_name)
        expected = render_git_hook(hook_name, record["protected_branches"])
        if not hook.is_file() or hook.is_symlink():
            errors.append(f"受管 Git Hook 缺失或不是普通文件：{hook_name}")
        else:
            try:
                content = hook.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                errors.append(f"受管 Git Hook 无法读取：{hook_name}")
            else:
                if content != expected:
                    errors.append(f"受管 Git Hook 内容已变化：{hook_name}")
            if not stat.S_IMODE(hook.stat().st_mode) & 0o111:
                errors.append(f"受管 Git Hook 不可执行：{hook_name}")
        if record["original"] is not None:
            if not _identity_matches(backup, record["original"]):
                errors.append(f"原 Git Hook 备份缺失或已变化：{backup.name}")
        elif _path_exists(backup):
            errors.append(f"出现未记录的 Git Hook 备份：{backup.name}")
    return errors


def remove_git_hooks(project: Path, records: dict[str, dict[str, Any]]) -> None:
    if not records:
        return
    hooks_directory = git_hooks_directory(project)
    if hooks_directory is None:
        raise HarnessError("目标项目不是 Git 工作树，不能解除 Git Hook")

    for hook_name, record in records.items():
        hook = _hook_path(hooks_directory, hook_name)
        backup = _backup_path(hooks_directory, hook_name)
        if not hook.is_file() or hook.is_symlink():
            raise HarnessError(f"受管 Git Hook 缺失或已被替换：{hook}")
        if hook.read_text(encoding="utf-8") != render_git_hook(
            hook_name,
            record["protected_branches"]
        ):
            raise HarnessError(f"受管 Git Hook 内容已变化，拒绝移除：{hook}")
        if record["original"] is not None:
            if not _identity_matches(backup, record["original"]):
                raise HarnessError(f"原 Git Hook 备份缺失或已变化，拒绝移除：{backup}")
        elif _path_exists(backup):
            raise HarnessError(f"发现未记录的 Git Hook 备份，拒绝移除：{backup}")

    for hook_name, record in records.items():
        hook = _hook_path(hooks_directory, hook_name)
        backup = _backup_path(hooks_directory, hook_name)
        hook.unlink()
        if record["original"] is not None:
            os.replace(backup, hook)
