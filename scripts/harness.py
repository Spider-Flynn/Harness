#!/usr/bin/env python3
"""Harness Engineering 本地项目接入工具。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


HARNESS_ROOT = Path(__file__).resolve().parents[1]
MANAGED_ROOT = Path(".harness")
MANIFEST_PATH = MANAGED_ROOT / "manifest.json"
SCHEMA_VERSION = 1
MANAGED_FILES = (
    Path("runtime/HARNESS.md"),
)
PROJECT_SKILLS = {
    "intent",
    "know",
    "build",
    "router",
    "design",
    "dev",
    "debug",
    "fix",
    "it-test",
    "audit",
    "cr",
    "retro",
    "subagent",
}
OPTIONAL_PROJECT_SKILLS = {"cooper", "biz", "mock"}
CONTROL_PLANE_SKILLS = {"skill-creator", "skill-neat"}


class HarnessError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise HarnessError(f"拒绝通过符号链接写入受管文件：{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _project_path(raw: str) -> Path:
    project = Path(raw).expanduser().resolve()
    forbidden = {Path("/"), Path.home().resolve(), HARNESS_ROOT, HARNESS_ROOT.parent}
    if project in forbidden:
        raise HarnessError(f"拒绝把宽泛或源码目录作为业务项目：{project}")
    if not project.is_dir():
        raise HarnessError(f"业务项目目录不存在：{project}")
    return project


def _assert_no_symlink_parents(project: Path, relative: Path) -> None:
    current = project
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise HarnessError(f"受管路径父目录不能是符号链接：{current}")


def _skill_name(skill_dir: Path) -> str:
    skill_file = skill_dir / "SKILL.md"
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise HarnessError(f"Skill 缺少 frontmatter：{skill_file}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise HarnessError(f"Skill frontmatter 未闭合：{skill_file}") from exc
    metadata = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if not re.fullmatch(r"[a-z0-9-]{1,64}", name) or not description:
        raise HarnessError(f"Skill name 或 description 无效：{skill_file}")

    return name


def _discover_skills() -> dict[str, Path]:
    skills: dict[str, Path] = {}
    for skill_file in sorted((HARNESS_ROOT / "skills").glob("*/*/SKILL.md")):
        skill_dir = skill_file.parent
        name = _skill_name(skill_dir)
        if name != skill_dir.name:
            raise HarnessError(f"Skill 目录与 name 不一致：{skill_dir} -> {name}")
        if name in skills:
            raise HarnessError(f"Skill 名称重复：{name}")
        skills[name] = skill_dir.resolve()
    if not skills:
        raise HarnessError("没有发现可接入的 Harness Skill")
    expected = PROJECT_SKILLS | CONTROL_PLANE_SKILLS
    missing = sorted(expected - set(skills))
    if missing:
        raise HarnessError("缺少系统必需 Skill：" + "、".join(missing))
    unknown = sorted(set(skills) - expected - OPTIONAL_PROJECT_SKILLS)
    if unknown:
        raise HarnessError("发现未纳入系统设计的 Skill：" + "、".join(unknown))
    return skills


def _project_skills(skills: dict[str, Path]) -> dict[str, Path]:
    """返回需要投影到业务项目的 Skill。"""
    return {
        name: path
        for name, path in skills.items()
        if name not in CONTROL_PLANE_SKILLS
    }


def _destination(relative: Path) -> Path:
    if relative.parts[0] == "runtime":
        return MANAGED_ROOT / relative
    if relative.parts[0] == "flows":
        return MANAGED_ROOT / relative
    raise HarnessError(f"未定义的受管文件：{relative}")


def _load_manifest(project: Path) -> dict[str, Any]:
    path = project / MANIFEST_PATH
    _assert_no_symlink_parents(project, MANIFEST_PATH)
    if path.is_symlink():
        raise HarnessError(f"受管清单不能是符号链接：{path}")
    if not path.is_file():
        raise HarnessError(f"项目尚未接入 Harness：{project}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"无法读取受管清单：{path}") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise HarnessError(f"不支持的清单版本：{manifest.get('schema_version')}")
    _validate_manifest(manifest)
    for installed_name in manifest["files"]:
        installed_relative = Path(installed_name)
        _assert_no_symlink_parents(project, installed_relative)
        if (project / installed_relative).is_symlink():
            raise HarnessError(f"受管文件不能是符号链接：{installed_name}")
    for link_name in manifest["skill_links"]:
        _assert_no_symlink_parents(project, Path(link_name))
    return manifest


def _validate_manifest(manifest: dict[str, Any]) -> None:
    files = manifest.get("files")
    links = manifest.get("skill_links")
    if not isinstance(files, dict) or not isinstance(links, dict):
        raise HarnessError("受管清单缺少 files 或 skill_links")

    for installed_name, item in files.items():
        installed = Path(installed_name)
        source = Path(str(item.get("source", ""))) if isinstance(item, dict) else Path()
        if (
            installed.is_absolute()
            or ".." in installed.parts
            or source.is_absolute()
            or ".." in source.parts
            or not source.parts
            or source.parts[0] not in {"runtime", "flows"}
            or installed != _destination(source)
            or not isinstance(item, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
        ):
            raise HarnessError(f"受管清单包含非法文件记录：{installed_name}")

    for link_name, item in links.items():
        link = Path(link_name)
        if (
            len(link.parts) != 3
            or link.parts[:2] != (".agents", "skills")
            or not re.fullmatch(r"[a-z0-9-]+", link.name)
            or not isinstance(item, dict)
        ):
            raise HarnessError(f"受管清单包含非法 Skill 链接：{link_name}")
        source = Path(str(item.get("source", "")))
        if not source.is_absolute() or source.name != link.name:
            raise HarnessError(f"受管清单包含非法 Skill 来源：{link_name}")


def _manifest(skills: dict[str, Path]) -> dict[str, Any]:
    files = {}
    for source_relative in MANAGED_FILES:
        source = HARNESS_ROOT / source_relative
        if not source.is_file():
            raise HarnessError(f"Harness 源文件不存在：{source}")
        installed_relative = _destination(source_relative)
        files[str(installed_relative)] = {
            "source": str(source_relative),
            "sha256": _sha256(source),
        }
    links = {
        str(Path(".agents/skills") / name): {
            "source": str(path),
        }
        for name, path in sorted(skills.items())
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_root": str(HARNESS_ROOT),
        "files": files,
        "skill_links": links,
    }
    _validate_manifest(manifest)
    return manifest


def _check_managed_state(
    project: Path, manifest: dict[str, Any], *, compare_source: bool
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notices: list[str] = []
    for installed_name, item in manifest["files"].items():
        installed = project / installed_name
        if not installed.is_file():
            errors.append(f"受管文件缺失：{installed_name}")
            continue
        if _sha256(installed) != item["sha256"]:
            errors.append(f"受管文件已被项目修改：{installed_name}")
            continue
        if compare_source:
            source = HARNESS_ROOT / item["source"]
            if not source.is_file():
                notices.append(f"源码已移除：{item['source']}")
            elif _sha256(source) != item["sha256"]:
                notices.append(f"可更新：{installed_name}")

    for link_name, item in manifest["skill_links"].items():
        link = project / link_name
        expected = Path(item["source"])
        if not link.is_symlink():
            errors.append(f"受管 Skill 链接缺失或被替换：{link_name}")
            continue
        actual = (link.parent / os.readlink(link)).resolve()
        if actual != expected.resolve():
            errors.append(f"受管 Skill 链接目标变化：{link_name}")
        elif compare_source and not expected.is_dir():
            notices.append(f"Skill 源码已移除：{link_name}")
    return errors, notices


def _preflight_new_links(
    project: Path, skills: dict[str, Path], old_manifest: dict[str, Any] | None
) -> None:
    old_links = set((old_manifest or {}).get("skill_links", {}))
    for name in skills:
        relative = str(Path(".agents/skills") / name)
        target = project / relative
        _assert_no_symlink_parents(project, Path(relative))
        if target.exists() or target.is_symlink():
            if relative not in old_links:
                raise HarnessError(f"项目已有同名 Skill，拒绝覆盖：{relative}")


def _preflight_new_files(
    project: Path, new_manifest: dict[str, Any], old_manifest: dict[str, Any]
) -> None:
    old_files = set(old_manifest.get("files", {}))
    for installed_name in set(new_manifest["files"]) - old_files:
        target = project / installed_name
        _assert_no_symlink_parents(project, Path(installed_name))
        if target.exists() or target.is_symlink():
            raise HarnessError(f"项目已有同路径普通内容，拒绝覆盖：{installed_name}")


def _install_files(project: Path, manifest: dict[str, Any]) -> None:
    for installed_name, item in manifest["files"].items():
        source = HARNESS_ROOT / item["source"]
        destination = project / installed_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _install_links(project: Path, manifest: dict[str, Any]) -> None:
    for link_name, item in manifest["skill_links"].items():
        link = project / link_name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(Path(item["source"]), target_is_directory=True)


def _replace_skill_link(link: Path, source: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(
        f".{link.name}.harness-update-{uuid.uuid4().hex}"
    )
    try:
        temporary.symlink_to(source, target_is_directory=True)
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def command_init(project: Path) -> None:
    manifest_path = project / MANIFEST_PATH
    if manifest_path.exists():
        manifest = _load_manifest(project)
        errors, notices = _check_managed_state(project, manifest, compare_source=True)
        if errors:
            raise HarnessError("项目已接入但状态异常：" + "；".join(errors))
        print(f"Harness 已接入：{project}")
        for notice in notices:
            print(f"NOTICE {notice}")
        return
    managed_root = project / MANAGED_ROOT
    if managed_root.is_symlink():
        raise HarnessError(f"拒绝接管符号链接：{MANAGED_ROOT}")
    if managed_root.exists() and any(managed_root.iterdir()):
        raise HarnessError(f"项目已有非空 {MANAGED_ROOT}，拒绝接管")

    skills = _project_skills(_discover_skills())
    _preflight_new_links(project, skills, None)
    manifest = _manifest(skills)
    try:
        _install_files(project, manifest)
        _install_links(project, manifest)
        _atomic_write(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    except Exception:
        for link_name in manifest["skill_links"]:
            link = project / link_name
            if link.is_symlink():
                link.unlink()
        if managed_root.exists():
            shutil.rmtree(managed_root)
        raise
    print(f"已接入 Harness：{project}")
    print("项目规则仍需人工加入：软件开发任务先读取 .harness/runtime/HARNESS.md。")
    print("首次启用前必须显式运行 $build，建设当前项目知识体系。")


def command_doctor(project: Path) -> None:
    manifest = _load_manifest(project)
    errors, notices = _check_managed_state(project, manifest, compare_source=True)
    if not (project / ".git").exists():
        notices.append("业务项目未发现 .git；Git 边界无法确认")
    for notice in notices:
        print(f"NOTICE {notice}")
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        raise HarnessError(f"doctor 发现 {len(errors)} 个阻断问题")
    print(f"OK Harness 接入完整：{project}")


def command_update(project: Path) -> None:
    old_manifest = _load_manifest(project)
    errors, _ = _check_managed_state(project, old_manifest, compare_source=False)
    if errors:
        raise HarnessError("受管内容存在项目修改，拒绝更新：" + "；".join(errors))

    skills = _project_skills(_discover_skills())
    _preflight_new_links(project, skills, old_manifest)
    new_manifest = _manifest(skills)
    _preflight_new_files(project, new_manifest, old_manifest)
    new_links = set(new_manifest["skill_links"])
    old_links = set(old_manifest["skill_links"])
    new_files = set(new_manifest["files"])
    old_files = set(old_manifest["files"])

    with tempfile.TemporaryDirectory(
        prefix=".harness-update-",
        dir=project,
    ) as temporary_directory:
        stage = Path(temporary_directory)
        staged_new = stage / "new"
        staged_old = stage / "old"
        for installed_name, item in new_manifest["files"].items():
            source = HARNESS_ROOT / item["source"]
            staged = staged_new / installed_name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, staged)
            if _sha256(staged) != item["sha256"]:
                raise HarnessError(f"更新暂存内容与清单不一致：{installed_name}")
        for installed_name in old_files:
            backup = staged_old / installed_name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / installed_name, backup)

        try:
            for installed_name in sorted(new_files):
                destination = project / installed_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_new / installed_name, destination)
            for removed in sorted(old_files - new_files):
                (project / removed).unlink()

            for link_name, item in new_manifest["skill_links"].items():
                _replace_skill_link(
                    project / link_name,
                    Path(item["source"]),
                )
            for removed in sorted(old_links - new_links):
                (project / removed).unlink()

            _atomic_write(
                project / MANIFEST_PATH,
                json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n",
            )
        except Exception as update_error:
            rollback_errors = []
            for installed_name in sorted(new_files - old_files):
                target = project / installed_name
                try:
                    if target.exists() or target.is_symlink():
                        target.unlink()
                except OSError as exc:
                    rollback_errors.append(str(exc))
            for installed_name in sorted(old_files):
                target = project / installed_name
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.replace(staged_old / installed_name, target)
                except OSError as exc:
                    rollback_errors.append(str(exc))

            for link_name in sorted(new_links - old_links):
                target = project / link_name
                try:
                    if target.is_symlink():
                        target.unlink()
                except OSError as exc:
                    rollback_errors.append(str(exc))
            for link_name, item in old_manifest["skill_links"].items():
                try:
                    _replace_skill_link(
                        project / link_name,
                        Path(item["source"]),
                    )
                except OSError as exc:
                    rollback_errors.append(str(exc))
            if rollback_errors:
                raise HarnessError(
                    "更新失败且回滚不完整：" + "；".join(rollback_errors)
                ) from update_error
            raise HarnessError("更新失败，已恢复原受管内容") from update_error

    for removed in sorted(old_files - new_files):
        _remove_empty_parents((project / removed).parent, project / MANAGED_ROOT)
    for removed in sorted(old_links - new_links):
        _remove_empty_parents((project / removed).parent, project)
    print(f"已更新 Harness：{project}")


def command_remove(project: Path) -> None:
    manifest = _load_manifest(project)
    errors, _ = _check_managed_state(project, manifest, compare_source=False)
    if errors:
        raise HarnessError("受管内容存在项目修改，拒绝移除：" + "；".join(errors))

    for link_name in manifest["skill_links"]:
        link = project / link_name
        link.unlink()
        _remove_empty_parents(link.parent, project)
    for installed_name in manifest["files"]:
        installed = project / installed_name
        installed.unlink()
        _remove_empty_parents(installed.parent, project / MANAGED_ROOT)
    (project / MANIFEST_PATH).unlink()
    _remove_empty_parents(project / MANAGED_ROOT, project)
    print(f"已解除 Harness 接入：{project}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harness Engineering 项目接入工具")
    parser.add_argument(
        "command",
        choices=("init", "doctor", "update", "remove"),
        help="接入、检查、更新或解除接入",
    )
    parser.add_argument(
        "--project",
        default=".",
        help="业务项目目录，默认当前目录",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        project = _project_path(args.project)
        {
            "init": command_init,
            "doctor": command_doctor,
            "update": command_update,
            "remove": command_remove,
        }[args.command](project)
    except HarnessError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
