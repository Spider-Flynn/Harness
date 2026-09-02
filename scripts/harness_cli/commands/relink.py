"""复用项目原选择，修复链接与规则入口。"""

from __future__ import annotations

import json
from pathlib import Path

from ..binding import desired_manifest, link_records, load_binding_manifest
from ..catalog import discover_systems
from ..errors import HarnessError
from ..filesystem import (
    atomic_write,
    link_target,
    managed_path,
    replace_symlink,
    restore_link,
)
from ..git_hooks import (
    apply_git_hooks,
    capture_git_hook_snapshot,
    plan_git_hooks,
    restore_git_hook_snapshot,
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
        old_git_hooks = old_manifest.get("git_hooks", {})
        git_hooks = plan_git_hooks(project, system, old_git_hooks)
        new_manifest = desired_manifest(
            project,
            system,
            old_manifest["selected_skills"],
            rules,
            git_hooks,
        )

        old_links = link_records(old_manifest)
        new_links = link_records(new_manifest)
        link_relatives = set(old_links) | set(new_links)
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
        hook_snapshot = capture_git_hook_snapshot(
            project, {**old_git_hooks, **git_hooks}
        )
        registry_notice: str | None = None

        try:
            for relative, source in new_links.items():
                replace_symlink(managed_path(project, relative), Path(source))
            for relative in sorted(set(old_links) - set(new_links)):
                link = managed_path(project, relative)
                if link.is_symlink():
                    link.unlink()
            write_rules(project, rules)
            apply_git_hooks(project, git_hooks, old_git_hooks)
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
            restore_git_hook_snapshot(hook_snapshot)
            atomic_write(managed_path(project, MANIFEST_PATH), old_manifest_text)
            if isinstance(exc, HarnessError):
                raise
            raise HarnessError("重新链接失败，已恢复原绑定") from exc

    print(f"已重新链接 Harness：{project}")
    print(f"领域系统：{system['name']} ({system['id']})")
    if new_manifest["git_hooks"]:
        print("Git 提交门禁：pre-commit、prepare-commit-msg 已同步")
    if registry_notice:
        print(f"NOTICE {registry_notice}")
