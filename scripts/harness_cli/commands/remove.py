"""解除受管绑定，保留目标项目原有内容。"""

from __future__ import annotations

from pathlib import Path

from ..binding import link_records, load_binding_manifest
from ..errors import HarnessError
from ..filesystem import (
    atomic_write,
    link_target,
    managed_path,
    remove_empty_parents,
    resolved_link,
    restore_link,
)
from ..git_hooks import (
    capture_git_hook_snapshot,
    remove_git_hooks,
    restore_git_hook_snapshot,
)
from ..paths import (
    MANAGED_ROOT,
    MANIFEST_PATH,
    RESOURCES_ROOT,
    RUNTIME_LINK,
    SKILLS_ROOT,
)
from ..rules import remove_rule_block, restore_rules, validate_rule_target
from ..storage import project_lock, unregister_binding


def run(project: Path) -> None:
    with project_lock(project):
        manifest = load_binding_manifest(project)
        links = link_records(manifest)
        link_snapshots: dict[Path, str | None] = {}
        for relative, source in links.items():
            link = managed_path(project, relative)
            target = link_target(link)
            if target is not None and resolved_link(link) != Path(source).resolve():
                raise HarnessError(f"受管链接目标已变化，拒绝移除：{relative}")
            link_snapshots[relative] = target

        rule_records = manifest["rules_files"]
        rule_snapshots: dict[Path, str | None] = {}
        rule_results: dict[Path, str] = {}
        for record in rule_records:
            relative = Path(record["path"])
            validate_rule_target(project, relative)
            target = managed_path(project, relative)
            if not target.is_file():
                raise HarnessError(f"规则入口缺失：{relative}")
            content = target.read_text(encoding="utf-8")
            rule_snapshots[relative] = content
            rule_results[relative] = remove_rule_block(content)
        old_manifest_text = managed_path(project, MANIFEST_PATH).read_text(
            encoding="utf-8"
        )
        git_hooks = manifest.get("git_hooks", {})
        hook_snapshot = capture_git_hook_snapshot(project, git_hooks)
        registry_notice: str | None = None

        try:
            for relative in links:
                link = managed_path(project, relative)
                if link.is_symlink():
                    link.unlink()
            for record in rule_records:
                relative = Path(record["path"])
                target = managed_path(project, relative)
                content = rule_results[relative]
                if not content and record["created"]:
                    target.unlink()
                else:
                    atomic_write(target, content)
            remove_git_hooks(project, git_hooks)
            managed_path(project, MANIFEST_PATH).unlink()
            registry_notice = unregister_binding(project)
        except Exception as exc:
            for relative, target in link_snapshots.items():
                restore_link(managed_path(project, relative), target)
            restore_rules(project, rule_snapshots)
            restore_git_hook_snapshot(hook_snapshot)
            atomic_write(managed_path(project, MANIFEST_PATH), old_manifest_text)
            if isinstance(exc, HarnessError):
                raise
            raise HarnessError("解除接入失败，已恢复原绑定") from exc

        remove_empty_parents(project / RUNTIME_LINK.parent, project)
        remove_empty_parents(project / SKILLS_ROOT, project)
        remove_empty_parents(project / RESOURCES_ROOT, project)
        remove_empty_parents(project / MANAGED_ROOT, project)
    print(f"已解除 Harness 接入：{project}")
    if git_hooks:
        print("Git 提交门禁：已移除，并恢复接入前的 Hook 状态")
    if registry_notice:
        print(f"NOTICE {registry_notice}")
