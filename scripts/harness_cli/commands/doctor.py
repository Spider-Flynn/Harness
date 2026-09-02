"""只检查项目绑定并报告问题。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..binding import desired_manifest, load_binding_manifest
from ..catalog import discover_systems
from ..errors import HarnessError
from ..filesystem import managed_path, resolved_link
from ..paths import HARNESS_ROOT
from ..rules import rule_block_matches
from ..storage import load_registry, project_lock, registry_recovery_hint


def _check_binding(
    project: Path, manifest: dict[str, Any]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notices: list[str] = []
    systems = discover_systems()
    system = systems.get(manifest["system_id"])
    if system is None:
        errors.append(f"当前源码缺少领域系统：{manifest['system_id']}")
        return errors, notices

    try:
        expected = desired_manifest(
            project,
            system,
            manifest["selected_skills"],
            manifest["rules_files"],
        )
    except HarnessError as exc:
        errors.append(str(exc))
        return errors, notices

    if manifest["source_root"] != str(HARNESS_ROOT):
        notices.append("Harness 源目录已经移动；请运行 relink 修复全部软链接")
    if manifest["project_root"] != str(project):
        notices.append("目标项目目录已经移动；请运行 relink 更新绑定记录")

    expected_links = {
        expected["runtime_link"]["path"]: expected["runtime_link"]["source"],
        **{
            name: item["source"] for name, item in expected["skill_links"].items()
        },
    }
    recorded_links = {
        manifest["runtime_link"]["path"]: manifest["runtime_link"]["source"],
        **{
            name: item["source"] for name, item in manifest["skill_links"].items()
        },
    }
    missing_records = sorted(set(expected_links) - set(recorded_links))
    stale_records = sorted(set(recorded_links) - set(expected_links))
    if missing_records:
        errors.append(
            "项目绑定缺少当前领域能力："
            + "、".join(missing_records)
            + "；请运行 relink 同步"
        )
    if stale_records:
        errors.append(
            "项目绑定仍保留已退出领域的能力："
            + "、".join(stale_records)
            + "；请运行 relink 移除"
        )

    for link_name, expected_source in expected_links.items():
        link = managed_path(project, link_name)
        if not link.is_symlink():
            errors.append(f"受管链接缺失或被普通内容替换：{link_name}")
            continue
        if resolved_link(link) != Path(expected_source).resolve():
            errors.append(f"受管链接目标不正确：{link_name}")
        elif not Path(expected_source).exists():
            errors.append(f"受管链接源码不存在：{link_name}")

    for record in manifest["rules_files"]:
        relative = Path(record["path"])
        target = managed_path(project, relative)
        if not target.is_file() or target.is_symlink():
            errors.append(f"规则入口缺失或不是普通文件：{relative}")
            continue
        if not rule_block_matches(target.read_text(encoding="utf-8")):
            errors.append(f"规则入口中的 Harness 受管片段缺失或被修改：{relative}")

    try:
        registry = load_registry()
        entry = registry["projects"].get(str(project))
        if not entry:
            notices.append("本机绑定索引缺少该项目；运行 relink 可恢复")
        elif entry.get("system_id") != manifest["system_id"]:
            notices.append("本机绑定索引与项目清单不一致；运行 relink 可恢复")
    except (HarnessError, OSError, UnicodeError) as exc:
        notices.append(
            f"本机绑定索引不可读，不影响目标项目绑定：{exc}；"
            f"{registry_recovery_hint()}"
        )
    return errors, notices


def run(project: Path) -> None:
    with project_lock(project):
        manifest = load_binding_manifest(project)
        errors, notices = _check_binding(project, manifest)
    for notice in notices:
        print(f"NOTICE {notice}")
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        raise HarnessError(f"doctor 发现 {len(errors)} 个阻断问题")
    print(f"OK Harness 接入完整：{project}")
