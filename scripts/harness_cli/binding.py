"""目标项目绑定清单的构造、读取与校验。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .catalog import project_skills, validate_branch_name, validate_name
from .errors import HarnessError
from .filesystem import managed_path, read_json
from .paths import (
    HARNESS_ROOT,
    MANIFEST_PATH,
    RESOURCES_ROOT,
    RUNTIME_LINK,
    SKILLS_ROOT,
)
from .rules import RULE_FILES


BINDING_SCHEMA_VERSION = 3
LEGACY_BINDING_SCHEMA_VERSION = 2


def desired_manifest(
    project: Path,
    system: dict[str, Any],
    selected_skills: list[str],
    rules: list[dict[str, Any]],
    git_hooks: dict[str, dict[str, Any]] | None = None,
    git_hooks_skipped: bool = False,
) -> dict[str, Any]:
    runtime_source = (HARNESS_ROOT / system["runtime"]).resolve()
    if not runtime_source.is_file():
        raise HarnessError(f"Runtime 源文件不存在：{runtime_source}")
    skills = project_skills(system, selected_skills)
    resources: dict[str, Path] = {}
    for name, relative_source in system.get("resources", {}).items():
        source = (HARNESS_ROOT / relative_source).resolve()
        if not source.is_dir():
            raise HarnessError(f"共享资源目录不存在：{source}")
        resources[name] = source
    manifest = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "project_root": str(project),
        "source_root": str(HARNESS_ROOT),
        "system_id": system["id"],
        "runtime_link": {
            "path": str(RUNTIME_LINK),
            "source": str(runtime_source),
        },
        "skill_links": {
            str(SKILLS_ROOT / name): {"source": str(source)}
            for name, source in sorted(skills.items())
        },
        "resource_links": {
            str(RESOURCES_ROOT / name): {"source": str(source)}
            for name, source in sorted(resources.items())
        },
        "selected_skills": sorted(set(selected_skills)),
        "rules_files": rules,
        "git_hooks": git_hooks or {},
        "git_hooks_skipped": bool(git_hooks_skipped),
    }
    _validate_binding_manifest(manifest)
    return manifest


def _validate_binding_manifest(manifest: dict[str, Any]) -> None:
    schema_version = manifest.get("schema_version")
    if schema_version not in {
        LEGACY_BINDING_SCHEMA_VERSION,
        BINDING_SCHEMA_VERSION,
    }:
        raise HarnessError(
            f"不支持的项目绑定清单版本：{schema_version}"
        )
    validate_name(manifest.get("system_id"), label="领域系统")
    for field in ("project_root", "source_root"):
        if not Path(str(manifest.get(field, ""))).is_absolute():
            raise HarnessError(f"项目绑定清单字段必须是绝对路径：{field}")

    runtime = manifest.get("runtime_link")
    if (
        not isinstance(runtime, dict)
        or runtime.get("path") != str(RUNTIME_LINK)
        or not Path(str(runtime.get("source", ""))).is_absolute()
    ):
        raise HarnessError("项目绑定清单包含非法 Runtime 链接")

    links = manifest.get("skill_links")
    if not isinstance(links, dict):
        raise HarnessError("项目绑定清单缺少 skill_links")
    for link_name, item in links.items():
        link = Path(link_name)
        if (
            len(link.parts) != 3
            or link.parts[:2] != SKILLS_ROOT.parts
            or not re.fullmatch(r"[a-z0-9-]+", link.name)
            or not isinstance(item, dict)
            or not Path(str(item.get("source", ""))).is_absolute()
        ):
            raise HarnessError(f"项目绑定清单包含非法 Skill 链接：{link_name}")

    # schema_version 2 的早期绑定未记录共享资源；将其视为无资源，
    # 由 relink 依据当前领域清单补齐，而不是拒绝已有项目。
    resource_links = manifest.get("resource_links", {})
    if not isinstance(resource_links, dict):
        raise HarnessError("项目绑定清单包含非法共享资源链接")
    for link_name, item in resource_links.items():
        link = Path(link_name)
        if (
            len(link.parts) != 3
            or link.parts[:2] != RESOURCES_ROOT.parts
            or not re.fullmatch(r"[a-z0-9-]+", link.name)
            or not isinstance(item, dict)
            or not Path(str(item.get("source", ""))).is_absolute()
        ):
            raise HarnessError(f"项目绑定清单包含非法共享资源链接：{link_name}")

    selected = manifest.get("selected_skills")
    if not isinstance(selected, list):
        raise HarnessError("项目绑定清单包含非法可选 Skill")
    normalized_selected = [
        validate_name(name, label="可选 Skill") for name in selected
    ]
    if len(normalized_selected) != len(set(normalized_selected)):
        raise HarnessError("项目绑定清单包含重复可选 Skill")

    rules = manifest.get("rules_files")
    if not isinstance(rules, list) or not rules:
        raise HarnessError("项目绑定清单缺少规则入口")
    seen_rules: set[str] = set()
    allowed_rules = {str(path) for path in RULE_FILES.values()}
    for item in rules:
        if (
            not isinstance(item, dict)
            or item.get("path") not in allowed_rules
            or not isinstance(item.get("created"), bool)
            or item["path"] in seen_rules
        ):
            raise HarnessError("项目绑定清单包含非法规则入口")
        seen_rules.add(item["path"])

    if "git_hooks_skipped" in manifest and not isinstance(manifest["git_hooks_skipped"], bool):
        raise HarnessError("项目绑定清单 git_hooks_skipped 必须是布尔值")

    # schema_version 2 没有 Git Hook 合同；由 relink 升级为 version 3 并补齐。
    git_hooks = manifest.get("git_hooks", {})
    if schema_version == LEGACY_BINDING_SCHEMA_VERSION and git_hooks:
        raise HarnessError("version 2 项目绑定清单不能包含 Git Hook 记录")
    supported_hooks = {"pre-commit", "prepare-commit-msg"}
    if not isinstance(git_hooks, dict) or set(git_hooks) - supported_hooks:
        raise HarnessError("项目绑定清单包含非法 Git Hook")
    for hook_name, record in git_hooks.items():
        if not isinstance(record, dict) or set(record) != {
            "protected_branches",
            "original",
        }:
            raise HarnessError(f"项目绑定清单包含非法 {hook_name} Hook")
        branches = (
            record.get("protected_branches")
            if isinstance(record, dict)
            else None
        )
        if not isinstance(branches, list) or not branches:
            raise HarnessError(f"项目绑定清单包含非法 {hook_name} Hook")
        normalized_branches = [
            validate_branch_name(name, label="受保护分支") for name in branches
        ]
        if len(normalized_branches) != len(set(normalized_branches)):
            raise HarnessError("项目绑定清单包含重复受保护分支")
        original = record["original"]
        if original is None:
            continue
        if not isinstance(original, dict) or original.get("type") not in {
            "file",
            "symlink",
        }:
            raise HarnessError(f"项目绑定清单包含非法 {hook_name} 原 Hook 记录")
        if original["type"] == "file":
            if (
                set(original) != {"type", "sha256", "mode"}
                or not isinstance(original["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", original["sha256"])
                or not isinstance(original["mode"], int)
                or not 0 <= original["mode"] <= 0o7777
            ):
                raise HarnessError(f"项目绑定清单包含非法 {hook_name} 原文件记录")
        elif (
            set(original) != {"type", "target"}
            or not isinstance(original["target"], str)
            or not original["target"]
        ):
            raise HarnessError(f"项目绑定清单包含非法 {hook_name} 原链接记录")


def load_binding_manifest(project: Path) -> dict[str, Any]:
    path = managed_path(project, MANIFEST_PATH)
    if path.is_symlink():
        raise HarnessError(f"项目绑定清单不能是符号链接：{path}")
    if not path.is_file():
        raise HarnessError(f"项目尚未接入 Harness：{project}")
    manifest = read_json(path, label="项目绑定清单")
    _validate_binding_manifest(manifest)
    return manifest


def link_records(manifest: dict[str, Any]) -> dict[Path, str]:
    """返回该绑定声明的所有受管软链接（旧清单缺资源时为空）。"""
    return {
        Path(manifest["runtime_link"]["path"]): manifest["runtime_link"]["source"],
        **{
            Path(name): item["source"]
            for name, item in manifest["skill_links"].items()
        },
        **{
            Path(name): item["source"]
            for name, item in manifest.get("resource_links", {}).items()
        },
    }
