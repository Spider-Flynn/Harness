"""复用项目原选择，修复链接与规则入口。"""

from __future__ import annotations

import json
from pathlib import Path

from ..binding import desired_manifest, load_binding_manifest
from ..catalog import discover_systems
from ..errors import HarnessError
from ..filesystem import (
    atomic_write,
    link_target,
    managed_path,
    replace_symlink,
    restore_link,
)
from ..paths import MANIFEST_PATH
from ..rules import restore_rules, validate_rule_target, write_rules
from ..storage import project_lock, register_binding


def run(project: Path) -> None:
    with project_lock(project):
        old_manifest = load_binding_manifest(project)
        systems = discover_systems()
        system_id = old_manifest["system_id"]
        if system_id not in systems:
            raise HarnessError(f"原领域系统在当前 Harness 源码中不存在：{system_id}")
        system = systems[system_id]
        rules = old_manifest["rules_files"]
        new_manifest = desired_manifest(
            project,
            system,
            old_manifest["selected_skills"],
            rules,
        )

        link_relatives = {
            Path(old_manifest["runtime_link"]["path"]),
            Path(new_manifest["runtime_link"]["path"]),
            *(Path(name) for name in old_manifest["skill_links"]),
            *(Path(name) for name in new_manifest["skill_links"]),
        }
        link_snapshots = {
            relative: link_target(managed_path(project, relative))
            for relative in link_relatives
        }
        rule_relatives = {Path(item["path"]) for item in rules}
        for relative in rule_relatives:
            validate_rule_target(project, relative)
        rule_snapshots = {
            relative: managed_path(project, relative).read_text(encoding="utf-8")
            if managed_path(project, relative).exists()
            else None
            for relative in rule_relatives
        }
        old_manifest_text = managed_path(project, MANIFEST_PATH).read_text(
            encoding="utf-8"
        )
        registry_notice: str | None = None

        try:
            runtime = new_manifest["runtime_link"]
            replace_symlink(
                managed_path(project, runtime["path"]), Path(runtime["source"])
            )
            for link_name, item in new_manifest["skill_links"].items():
                replace_symlink(
                    managed_path(project, link_name), Path(item["source"])
                )
            removed_links = set(old_manifest["skill_links"]) - set(
                new_manifest["skill_links"]
            )
            for link_name in sorted(removed_links):
                link = managed_path(project, link_name)
                if link.is_symlink():
                    link.unlink()
            write_rules(project, rules)
            atomic_write(
                managed_path(project, MANIFEST_PATH),
                json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n",
            )
            registry_notice = register_binding(
                project, new_manifest, system["name"]
            )
        except Exception as exc:
            for relative, target in link_snapshots.items():
                restore_link(managed_path(project, relative), target)
            restore_rules(project, rule_snapshots)
            atomic_write(managed_path(project, MANIFEST_PATH), old_manifest_text)
            if isinstance(exc, HarnessError):
                raise
            raise HarnessError("重新链接失败，已恢复原绑定") from exc

    print(f"已重新链接 Harness：{project}")
    print(f"领域系统：{system['name']} ({system['id']})")
    if registry_notice:
        print(f"NOTICE {registry_notice}")
