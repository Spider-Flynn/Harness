"""首次绑定领域系统与目标项目。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..binding import desired_manifest
from ..catalog import select_system
from ..errors import HarnessError
from ..filesystem import (
    atomic_write,
    managed_path,
    remove_empty_parents,
    replace_symlink,
)
from ..paths import MANAGED_ROOT, MANIFEST_PATH, RUNTIME_LINK, SKILLS_ROOT
from ..rules import restore_rules, rule_records, select_rule_files, write_rules
from ..storage import project_lock, register_binding


def _preflight_init(project: Path, manifest: dict[str, Any]) -> None:
    manifest_path = managed_path(project, MANIFEST_PATH)
    if manifest_path.exists() or manifest_path.is_symlink():
        raise HarnessError("项目已经存在绑定清单；请使用 doctor、relink 或 remove")
    managed_root = managed_path(project, MANAGED_ROOT)
    if managed_root.is_symlink():
        raise HarnessError(f"拒绝接管符号链接目录：{MANAGED_ROOT}")
    if managed_root.exists() and any(managed_root.iterdir()):
        raise HarnessError(f"项目已有非空 {MANAGED_ROOT}，拒绝接管")

    link_names = [manifest["runtime_link"]["path"], *manifest["skill_links"]]
    for link_name in link_names:
        relative = Path(link_name)
        target = managed_path(project, relative)
        if target.exists() or target.is_symlink():
            raise HarnessError(f"项目已有同路径内容，拒绝覆盖：{relative}")


def run(
    project: Path,
    system_id: str | None,
    rules_mode: str,
    selected_skills: list[str],
    allow_non_git: bool,
) -> None:
    if not allow_non_git and not (project / ".git").exists():
        raise HarnessError(
            "目标目录未发现 .git；如果已由 AI 或用户确认它仍是合适的代码项目，"
            "请显式增加 --allow-non-git"
        )
    system = select_system(system_id)
    rule_paths = select_rule_files(project, rules_mode)
    rules = rule_records(project, rule_paths)
    manifest = desired_manifest(project, system, selected_skills, rules)

    with project_lock(project):
        _preflight_init(project, manifest)
        registry_notice: str | None = None
        rule_snapshots = {
            path: (project / path).read_text(encoding="utf-8")
            if (project / path).exists()
            else None
            for path in rule_paths
        }
        created_links: list[Path] = []
        try:
            runtime = manifest["runtime_link"]
            runtime_link = managed_path(project, runtime["path"])
            replace_symlink(runtime_link, Path(runtime["source"]))
            created_links.append(runtime_link)
            for link_name, item in manifest["skill_links"].items():
                link = managed_path(project, link_name)
                replace_symlink(link, Path(item["source"]))
                created_links.append(link)
            write_rules(project, rules)
            atomic_write(
                managed_path(project, MANIFEST_PATH),
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            registry_notice = register_binding(project, manifest, system["name"])
        except Exception as exc:
            for link in reversed(created_links):
                if link.is_symlink():
                    link.unlink()
            restore_rules(project, rule_snapshots)
            manifest_path = managed_path(project, MANIFEST_PATH)
            if manifest_path.is_file() and not manifest_path.is_symlink():
                manifest_path.unlink()
            remove_empty_parents(project / RUNTIME_LINK.parent, project)
            remove_empty_parents(project / SKILLS_ROOT, project)
            if isinstance(exc, HarnessError):
                raise
            raise HarnessError("接入失败，已恢复目标项目原状态") from exc

    print(f"已接入 Harness：{project}")
    print(f"领域系统：{system['name']} ({system['id']})")
    print("规则入口：" + "、".join(str(path) for path in rule_paths))
    if selected_skills:
        print("可选 Skills：" + "、".join(sorted(set(selected_skills))))
    if registry_notice:
        print(f"NOTICE {registry_notice}")
    print("项目知识 build 为可选能力，不影响 Harness 处理正常需求。")
