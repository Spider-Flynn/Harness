"""领域系统与 Skill 的发现、选择及校验。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .errors import HarnessError
from .filesystem import read_json, safe_relative
from .paths import HARNESS_ROOT, SYSTEMS_ROOT


SYSTEM_SCHEMA_VERSION = 1


def validate_name(name: Any, *, label: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        raise HarnessError(f"{label}名称无效：{name}")
    return name


def _validate_system(raw: dict[str, Any], source: Path) -> dict[str, Any]:
    if raw.get("schema_version") != SYSTEM_SCHEMA_VERSION:
        raise HarnessError(f"不支持的领域系统清单版本：{source}")
    system_id = validate_name(raw.get("id"), label="领域系统")
    if source.stem != system_id:
        raise HarnessError(f"领域系统 id 与文件名不一致：{source}")
    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
        raise HarnessError(f"领域系统缺少名称：{source}")
    runtime = safe_relative(raw.get("runtime"), label="Runtime")
    if runtime != Path("runtime/HARNESS.md"):
        raise HarnessError(f"当前仅支持 runtime/HARNESS.md：{source}")

    groups = raw.get("skills")
    required_groups = {
        "default",
        "conditional",
        "local",
        "horizontal",
        "personal",
        "evolution",
    }
    if not isinstance(groups, dict) or set(groups) != required_groups:
        raise HarnessError(f"领域系统 Skills 分类不完整：{source}")
    seen: set[str] = set()
    normalized_groups: dict[str, list[str]] = {}
    for group in sorted(required_groups):
        names = groups[group]
        if not isinstance(names, list):
            raise HarnessError(f"领域系统 Skill 清单无效：{source} -> {group}")
        normalized = [validate_name(name, label="Skill") for name in names]
        if len(normalized) != len(set(normalized)):
            raise HarnessError(f"领域系统 Skill 清单重复：{source} -> {group}")
        duplicate = seen.intersection(normalized)
        if duplicate:
            raise HarnessError(
                f"领域系统 Skill 跨分类重复：{source} -> {sorted(duplicate)}"
            )
        seen.update(normalized)
        normalized_groups[group] = normalized

    return {
        "schema_version": SYSTEM_SCHEMA_VERSION,
        "id": system_id,
        "name": raw["name"].strip(),
        "description": str(raw.get("description", "")).strip(),
        "runtime": str(runtime),
        "skills": normalized_groups,
    }


def discover_systems() -> dict[str, dict[str, Any]]:
    systems: dict[str, dict[str, Any]] = {}
    for source in sorted(SYSTEMS_ROOT.glob("*.json")):
        system = _validate_system(read_json(source, label="领域系统清单"), source)
        if system["id"] in systems:
            raise HarnessError(f"领域系统 id 重复：{system['id']}")
        systems[system["id"]] = system
    if not systems:
        raise HarnessError(f"没有发现领域系统清单：{SYSTEMS_ROOT}")
    return systems


def select_system(system_id: str | None) -> dict[str, Any]:
    systems = discover_systems()
    if system_id:
        if system_id not in systems:
            raise HarnessError(
                f"领域系统不存在：{system_id}；可选：{', '.join(systems)}"
            )
        return systems[system_id]
    if len(systems) == 1:
        return next(iter(systems.values()))
    if not sys.stdin.isatty():
        raise HarnessError("存在多个领域系统，非交互模式必须使用 --system 指定")

    options = list(systems.values())
    print("请选择领域系统：")
    for index, system in enumerate(options, start=1):
        print(f"  {index}. {system['name']} ({system['id']})")
    raw = input("输入编号：").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(options):
        raise HarnessError("领域系统选择无效")
    return options[int(raw) - 1]


def _skill_name(skill_dir: Path) -> str:
    skill_file = skill_dir / "SKILL.md"
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise HarnessError(f"Skill 缺少 frontmatter：{skill_file}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise HarnessError(f"Skill frontmatter 未闭合：{skill_file}") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    name = validate_name(metadata.get("name"), label="Skill")
    if not metadata.get("description"):
        raise HarnessError(f"Skill 缺少 description：{skill_file}")
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
    return skills


def project_skills(
    system: dict[str, Any], selected_skills: list[str]
) -> dict[str, Path]:
    configured = system["skills"]
    requested = set(selected_skills)
    allowed = set(configured["conditional"]) | set(configured["local"])
    unknown = sorted(requested - allowed)
    if unknown:
        raise HarnessError(
            f"可选 Skill 不属于领域系统 {system['id']}：{', '.join(unknown)}"
        )
    discovered = _discover_skills()
    selected = (
        list(configured["default"])
        + list(configured["horizontal"])
        + sorted(requested)
    )
    missing = [name for name in selected if name not in discovered]
    if missing:
        raise HarnessError("选择的 Skill 源码不存在：" + "、".join(missing))
    return {name: discovered[name] for name in selected}
