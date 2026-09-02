#!/usr/bin/env python3
"""Harness Engineering 项目接入工具。"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


HARNESS_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_ROOT = HARNESS_ROOT / "systems"
MANAGED_ROOT = Path(".harness")
MANIFEST_PATH = MANAGED_ROOT / "manifest.json"
RUNTIME_LINK = MANAGED_ROOT / "runtime/HARNESS.md"
SKILLS_ROOT = Path(".agents/skills")
BINDING_SCHEMA_VERSION = 2
SYSTEM_SCHEMA_VERSION = 1
REGISTRY_SCHEMA_VERSION = 1
RULE_FILES = {
    "agents": Path("AGENTS.md"),
    "claude": Path("CLAUDE.md"),
}
RULE_BLOCK_START = "<!-- HARNESS_START -->"
RULE_BLOCK_END = "<!-- HARNESS_END -->"
RULE_BLOCK = (
    f"{RULE_BLOCK_START}\n"
    "开始处理本项目任务前，读取并遵守 `.harness/runtime/HARNESS.md`。\n"
    f"{RULE_BLOCK_END}\n"
)
RULE_BLOCK_PATTERN = re.compile(
    rf"(?m)^{re.escape(RULE_BLOCK_START)}\n.*?^{re.escape(RULE_BLOCK_END)}\n?",
    re.DOTALL,
)


class HarnessError(RuntimeError):
    """接入操作无法安全完成。"""


def _atomic_write(path: Path, content: str, *, new_mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise HarnessError(f"拒绝通过符号链接写入受管文件：{path}")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else new_mode
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), mode)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _state_root() -> Path:
    return HARNESS_ROOT / "data"


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _project_lock(project: Path) -> Iterator[None]:
    key = hashlib.sha256(str(project).encode("utf-8")).hexdigest()
    with _file_lock(_state_root() / "locks" / f"{key}.lock"):
        yield


def _project_path(raw: str) -> Path:
    project = Path(raw).expanduser().resolve()
    forbidden = {Path("/"), Path.home().resolve(), HARNESS_ROOT, HARNESS_ROOT.parent}
    if project in forbidden:
        raise HarnessError(f"拒绝把宽泛目录或 Harness 源目录作为目标项目：{project}")
    if not project.is_dir():
        raise HarnessError(f"目标项目目录不存在：{project}")
    return project


def _assert_no_symlink_parents(project: Path, relative: Path) -> None:
    current = project
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise HarnessError(f"受管路径父目录不能是符号链接：{current}")


def _managed_path(project: Path, relative: Path | str) -> Path:
    normalized = Path(relative)
    if normalized.is_absolute() or not normalized.parts or ".." in normalized.parts:
        raise HarnessError(f"受管路径必须位于目标项目内：{relative}")
    _assert_no_symlink_parents(project, normalized)
    return project / normalized


def _safe_relative(raw: Any, *, label: str) -> Path:
    path = Path(str(raw))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise HarnessError(f"{label} 必须是 Harness 仓库内相对路径：{raw}")
    return path


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"无法读取{label}：{path}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"{label}根节点必须是对象：{path}")
    return value


def _validate_name(name: Any, *, label: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        raise HarnessError(f"{label}名称无效：{name}")
    return name


def _validate_system(raw: dict[str, Any], source: Path) -> dict[str, Any]:
    if raw.get("schema_version") != SYSTEM_SCHEMA_VERSION:
        raise HarnessError(f"不支持的领域系统清单版本：{source}")
    system_id = _validate_name(raw.get("id"), label="领域系统")
    if source.stem != system_id:
        raise HarnessError(f"领域系统 id 与文件名不一致：{source}")
    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
        raise HarnessError(f"领域系统缺少名称：{source}")
    runtime = _safe_relative(raw.get("runtime"), label="Runtime")
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
        normalized = [_validate_name(name, label="Skill") for name in names]
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


def _discover_systems() -> dict[str, dict[str, Any]]:
    systems: dict[str, dict[str, Any]] = {}
    for source in sorted(SYSTEMS_ROOT.glob("*.json")):
        system = _validate_system(_read_json(source, label="领域系统清单"), source)
        if system["id"] in systems:
            raise HarnessError(f"领域系统 id 重复：{system['id']}")
        systems[system["id"]] = system
    if not systems:
        raise HarnessError(f"没有发现领域系统清单：{SYSTEMS_ROOT}")
    return systems


def _select_system(system_id: str | None) -> dict[str, Any]:
    systems = _discover_systems()
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
    name = _validate_name(metadata.get("name"), label="Skill")
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


def _project_skills(
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


def _select_rule_files(project: Path, mode: str) -> list[Path]:
    if mode != "auto":
        if mode == "both":
            return [RULE_FILES["agents"], RULE_FILES["claude"]]
        return [RULE_FILES[mode]]

    existing = [path for path in RULE_FILES.values() if (project / path).exists()]
    if existing:
        return existing
    if not sys.stdin.isatty():
        raise HarnessError(
            "项目没有 AGENTS.md 或 CLAUDE.md；非交互模式必须使用 "
            "--rules agents、--rules claude 或 --rules both"
        )

    print("项目没有可用的 Agent 规则入口：")
    print("  1. 创建 AGENTS.md（Codex 等 Agent 生态）")
    print("  2. 创建 CLAUDE.md（Claude Code 生态）")
    print("  3. 两者都创建")
    raw = input("输入编号：").strip()
    choices = {
        "1": [RULE_FILES["agents"]],
        "2": [RULE_FILES["claude"]],
        "3": [RULE_FILES["agents"], RULE_FILES["claude"]],
    }
    if raw not in choices:
        raise HarnessError("规则入口选择无效")
    return choices[raw]


def _rule_block_matches(content: str) -> bool:
    matches = RULE_BLOCK_PATTERN.findall(content)
    return len(matches) == 1 and matches[0] == RULE_BLOCK


def _upsert_rule_block(content: str) -> str:
    starts = content.count(RULE_BLOCK_START)
    ends = content.count(RULE_BLOCK_END)
    if starts != ends or starts > 1:
        raise HarnessError("规则文件中的 Harness 受管标记不完整或重复")
    if starts == 1:
        return RULE_BLOCK_PATTERN.sub(RULE_BLOCK, content, count=1)
    if not content:
        return RULE_BLOCK
    return content.rstrip() + "\n\n" + RULE_BLOCK


def _remove_rule_block(content: str) -> str:
    starts = content.count(RULE_BLOCK_START)
    ends = content.count(RULE_BLOCK_END)
    if starts != 1 or ends != 1:
        raise HarnessError("规则文件中的 Harness 受管标记缺失或重复")
    result = RULE_BLOCK_PATTERN.sub("", content, count=1).rstrip()
    return result + "\n" if result else ""


def _validate_rule_target(project: Path, relative: Path) -> None:
    target = _managed_path(project, relative)
    if target.is_symlink():
        raise HarnessError(f"拒绝通过符号链接维护规则入口：{relative}")
    if target.exists() and not target.is_file():
        raise HarnessError(f"规则入口不是普通文件：{relative}")


def _rule_records(project: Path, rules: list[Path]) -> list[dict[str, Any]]:
    records = []
    for relative in rules:
        _validate_rule_target(project, relative)
        target = _managed_path(project, relative)
        records.append({"path": str(relative), "created": not target.exists()})
    return records


def _desired_manifest(
    project: Path,
    system: dict[str, Any],
    selected_skills: list[str],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    runtime_source = (HARNESS_ROOT / system["runtime"]).resolve()
    if not runtime_source.is_file():
        raise HarnessError(f"Runtime 源文件不存在：{runtime_source}")
    skills = _project_skills(system, selected_skills)
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
        "selected_skills": sorted(set(selected_skills)),
        "rules_files": rules,
    }
    _validate_binding_manifest(manifest)
    return manifest


def _validate_binding_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != BINDING_SCHEMA_VERSION:
        raise HarnessError(
            f"不支持的项目绑定清单版本：{manifest.get('schema_version')}"
        )
    _validate_name(manifest.get("system_id"), label="领域系统")
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

    selected = manifest.get("selected_skills")
    if not isinstance(selected, list):
        raise HarnessError("项目绑定清单包含非法可选 Skill")
    normalized_selected = [
        _validate_name(name, label="可选 Skill") for name in selected
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


def _load_binding_manifest(project: Path) -> dict[str, Any]:
    path = _managed_path(project, MANIFEST_PATH)
    if path.is_symlink():
        raise HarnessError(f"项目绑定清单不能是符号链接：{path}")
    if not path.is_file():
        raise HarnessError(f"项目尚未接入 Harness：{project}")
    manifest = _read_json(path, label="项目绑定清单")
    _validate_binding_manifest(manifest)
    return manifest


def _replace_symlink(link: Path, source: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        raise HarnessError(f"受管链接路径已有普通内容，拒绝覆盖：{link}")
    temporary = link.with_name(f".{link.name}.harness-link-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise HarnessError(f"临时链接路径被占用：{temporary}")
    try:
        temporary.symlink_to(source, target_is_directory=source.is_dir())
        os.replace(temporary, link)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def _link_target(link: Path) -> str | None:
    if link.is_symlink():
        return os.readlink(link)
    if link.exists():
        raise HarnessError(f"受管链接路径已被普通内容替换：{link}")
    return None


def _resolved_link(link: Path) -> Path:
    target = Path(os.readlink(link))
    return target.resolve() if target.is_absolute() else (link.parent / target).resolve()


def _restore_link(link: Path, target: str | None) -> None:
    if link.exists() or link.is_symlink():
        if not link.is_symlink():
            raise HarnessError(f"回滚时受管链接被普通内容占用：{link}")
        link.unlink()
    if target is not None:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _registry_path() -> Path:
    return _state_root() / "projects.json"


def _load_registry_unlocked() -> dict[str, Any]:
    path = _registry_path()
    if not path.exists():
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "projects": {}}
    registry = _read_json(path, label="本机绑定索引")
    if (
        registry.get("schema_version") != REGISTRY_SCHEMA_VERSION
        or not isinstance(registry.get("projects"), dict)
    ):
        raise HarnessError(f"本机绑定索引格式无效：{path}")
    return registry


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


def _registry_recovery_hint() -> str:
    path = _registry_path()
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return f"请先将占用 {path} 的非普通文件移走，再运行 relink"
    return "请确认索引路径可写；普通文件内容损坏时，运行 relink 会隔离并重建"


def _register_binding(
    project: Path, manifest: dict[str, Any], system_name: str
) -> str | None:
    try:
        with _file_lock(_state_root() / "locks" / "registry.lock"):
            registry, notice = _registry_for_update_unlocked()
            registry["projects"][str(project)] = {
                "system_id": manifest["system_id"],
                "system_name": system_name,
                "source_root": str(HARNESS_ROOT),
            }
            _atomic_write(
                _registry_path(),
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            )
            return notice
    except (HarnessError, OSError, UnicodeError) as exc:
        return (
            f"本机绑定索引未更新，不影响目标项目绑定：{exc}；"
            f"{_registry_recovery_hint()}"
        )


def _unregister_binding(project: Path) -> str | None:
    try:
        with _file_lock(_state_root() / "locks" / "registry.lock"):
            registry, notice = _registry_for_update_unlocked()
            registry["projects"].pop(str(project), None)
            _atomic_write(
                _registry_path(),
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            )
            return notice
    except (HarnessError, OSError, UnicodeError) as exc:
        return (
            f"本机绑定索引未更新，不影响目标项目解绑：{exc}；"
            f"{_registry_recovery_hint()}"
        )


def _preflight_init(project: Path, manifest: dict[str, Any]) -> None:
    manifest_path = _managed_path(project, MANIFEST_PATH)
    if manifest_path.exists() or manifest_path.is_symlink():
        raise HarnessError("项目已经存在绑定清单；请使用 doctor、relink 或 remove")
    managed_root = _managed_path(project, MANAGED_ROOT)
    if managed_root.is_symlink():
        raise HarnessError(f"拒绝接管符号链接目录：{MANAGED_ROOT}")
    if managed_root.exists() and any(managed_root.iterdir()):
        raise HarnessError(f"项目已有非空 {MANAGED_ROOT}，拒绝接管")

    link_names = [manifest["runtime_link"]["path"], *manifest["skill_links"]]
    for link_name in link_names:
        relative = Path(link_name)
        target = _managed_path(project, relative)
        if target.exists() or target.is_symlink():
            raise HarnessError(f"项目已有同路径内容，拒绝覆盖：{relative}")


def _write_rules(project: Path, rules: list[dict[str, Any]]) -> None:
    for record in rules:
        relative = Path(record["path"])
        target = _managed_path(project, relative)
        content = target.read_text(encoding="utf-8") if target.exists() else ""
        _atomic_write(target, _upsert_rule_block(content), new_mode=0o644)


def _restore_rules(project: Path, snapshots: dict[Path, str | None]) -> None:
    for relative, content in snapshots.items():
        target = _managed_path(project, relative)
        if content is None:
            if target.exists():
                target.unlink()
        else:
            _atomic_write(target, content)


def command_init(
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
    system = _select_system(system_id)
    rule_paths = _select_rule_files(project, rules_mode)
    rules = _rule_records(project, rule_paths)
    manifest = _desired_manifest(project, system, selected_skills, rules)

    with _project_lock(project):
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
            runtime_link = _managed_path(project, runtime["path"])
            _replace_symlink(runtime_link, Path(runtime["source"]))
            created_links.append(runtime_link)
            for link_name, item in manifest["skill_links"].items():
                link = _managed_path(project, link_name)
                _replace_symlink(link, Path(item["source"]))
                created_links.append(link)
            _write_rules(project, rules)
            _atomic_write(
                _managed_path(project, MANIFEST_PATH),
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            registry_notice = _register_binding(project, manifest, system["name"])
        except Exception as exc:
            for link in reversed(created_links):
                if link.is_symlink():
                    link.unlink()
            _restore_rules(project, rule_snapshots)
            manifest_path = _managed_path(project, MANIFEST_PATH)
            if manifest_path.is_file() and not manifest_path.is_symlink():
                manifest_path.unlink()
            _remove_empty_parents(project / RUNTIME_LINK.parent, project)
            _remove_empty_parents(project / SKILLS_ROOT, project)
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


def command_relink(project: Path) -> None:
    with _project_lock(project):
        old_manifest = _load_binding_manifest(project)
        systems = _discover_systems()
        system_id = old_manifest["system_id"]
        if system_id not in systems:
            raise HarnessError(f"原领域系统在当前 Harness 源码中不存在：{system_id}")
        system = systems[system_id]
        rules = old_manifest["rules_files"]
        new_manifest = _desired_manifest(
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
            relative: _link_target(_managed_path(project, relative))
            for relative in link_relatives
        }
        rule_relatives = {Path(item["path"]) for item in rules}
        for relative in rule_relatives:
            _validate_rule_target(project, relative)
        rule_snapshots = {
            relative: _managed_path(project, relative).read_text(encoding="utf-8")
            if _managed_path(project, relative).exists()
            else None
            for relative in rule_relatives
        }
        old_manifest_text = _managed_path(project, MANIFEST_PATH).read_text(
            encoding="utf-8"
        )
        registry_notice: str | None = None

        try:
            runtime = new_manifest["runtime_link"]
            _replace_symlink(
                _managed_path(project, runtime["path"]), Path(runtime["source"])
            )
            for link_name, item in new_manifest["skill_links"].items():
                _replace_symlink(
                    _managed_path(project, link_name), Path(item["source"])
                )
            removed_links = set(old_manifest["skill_links"]) - set(
                new_manifest["skill_links"]
            )
            for link_name in sorted(removed_links):
                link = _managed_path(project, link_name)
                if link.is_symlink():
                    link.unlink()
            _write_rules(project, rules)
            _atomic_write(
                _managed_path(project, MANIFEST_PATH),
                json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n",
            )
            registry_notice = _register_binding(
                project, new_manifest, system["name"]
            )
        except Exception as exc:
            for relative, target in link_snapshots.items():
                _restore_link(_managed_path(project, relative), target)
            _restore_rules(project, rule_snapshots)
            _atomic_write(_managed_path(project, MANIFEST_PATH), old_manifest_text)
            if isinstance(exc, HarnessError):
                raise
            raise HarnessError("重新链接失败，已恢复原绑定") from exc

    print(f"已重新链接 Harness：{project}")
    print(f"领域系统：{system['name']} ({system['id']})")
    if registry_notice:
        print(f"NOTICE {registry_notice}")


def _check_binding(
    project: Path, manifest: dict[str, Any]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notices: list[str] = []
    systems = _discover_systems()
    system = systems.get(manifest["system_id"])
    if system is None:
        errors.append(f"当前源码缺少领域系统：{manifest['system_id']}")
        return errors, notices

    try:
        expected = _desired_manifest(
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
        link = _managed_path(project, link_name)
        if not link.is_symlink():
            errors.append(f"受管链接缺失或被普通内容替换：{link_name}")
            continue
        if _resolved_link(link) != Path(expected_source).resolve():
            errors.append(f"受管链接目标不正确：{link_name}")
        elif not Path(expected_source).exists():
            errors.append(f"受管链接源码不存在：{link_name}")

    for record in manifest["rules_files"]:
        relative = Path(record["path"])
        target = _managed_path(project, relative)
        if not target.is_file() or target.is_symlink():
            errors.append(f"规则入口缺失或不是普通文件：{relative}")
            continue
        if not _rule_block_matches(target.read_text(encoding="utf-8")):
            errors.append(f"规则入口中的 Harness 受管片段缺失或被修改：{relative}")

    try:
        with _file_lock(_state_root() / "locks" / "registry.lock"):
            registry = _load_registry_unlocked()
            entry = registry["projects"].get(str(project))
        if not entry:
            notices.append("本机绑定索引缺少该项目；运行 relink 可恢复")
        elif entry.get("system_id") != manifest["system_id"]:
            notices.append("本机绑定索引与项目清单不一致；运行 relink 可恢复")
    except (HarnessError, OSError, UnicodeError) as exc:
        notices.append(
            f"本机绑定索引不可读，不影响目标项目绑定：{exc}；"
            f"{_registry_recovery_hint()}"
        )
    return errors, notices


def command_doctor(project: Path) -> None:
    with _project_lock(project):
        manifest = _load_binding_manifest(project)
        errors, notices = _check_binding(project, manifest)
    for notice in notices:
        print(f"NOTICE {notice}")
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        raise HarnessError(f"doctor 发现 {len(errors)} 个阻断问题")
    print(f"OK Harness 接入完整：{project}")


def command_remove(project: Path) -> None:
    with _project_lock(project):
        manifest = _load_binding_manifest(project)
        link_records = {
            Path(manifest["runtime_link"]["path"]): manifest["runtime_link"]["source"],
            **{
                Path(name): item["source"]
                for name, item in manifest["skill_links"].items()
            },
        }
        link_snapshots: dict[Path, str | None] = {}
        for relative, source in link_records.items():
            link = _managed_path(project, relative)
            target = _link_target(link)
            if target is not None and _resolved_link(link) != Path(source).resolve():
                raise HarnessError(f"受管链接目标已变化，拒绝移除：{relative}")
            link_snapshots[relative] = target

        rule_records = manifest["rules_files"]
        rule_snapshots: dict[Path, str | None] = {}
        rule_results: dict[Path, str] = {}
        for record in rule_records:
            relative = Path(record["path"])
            _validate_rule_target(project, relative)
            target = _managed_path(project, relative)
            if not target.is_file():
                raise HarnessError(f"规则入口缺失：{relative}")
            content = target.read_text(encoding="utf-8")
            rule_snapshots[relative] = content
            rule_results[relative] = _remove_rule_block(content)
        old_manifest_text = _managed_path(project, MANIFEST_PATH).read_text(
            encoding="utf-8"
        )
        registry_notice: str | None = None

        try:
            for relative in link_records:
                link = _managed_path(project, relative)
                if link.is_symlink():
                    link.unlink()
            for record in rule_records:
                relative = Path(record["path"])
                target = _managed_path(project, relative)
                content = rule_results[relative]
                if not content and record["created"]:
                    target.unlink()
                else:
                    _atomic_write(target, content)
            _managed_path(project, MANIFEST_PATH).unlink()
            registry_notice = _unregister_binding(project)
        except Exception as exc:
            for relative, target in link_snapshots.items():
                _restore_link(_managed_path(project, relative), target)
            _restore_rules(project, rule_snapshots)
            _atomic_write(_managed_path(project, MANIFEST_PATH), old_manifest_text)
            if isinstance(exc, HarnessError):
                raise
            raise HarnessError("解除接入失败，已恢复原绑定") from exc

        _remove_empty_parents(project / RUNTIME_LINK.parent, project)
        _remove_empty_parents(project / SKILLS_ROOT, project)
        _remove_empty_parents(project / MANAGED_ROOT, project)
    print(f"已解除 Harness 接入：{project}")
    if registry_notice:
        print(f"NOTICE {registry_notice}")


def command_list() -> None:
    try:
        with _file_lock(_state_root() / "locks" / "registry.lock"):
            registry = _load_registry_unlocked()
    except (HarnessError, OSError, UnicodeError) as exc:
        raise HarnessError(
            "本机绑定索引不可读，但目标项目清单不受影响；"
            f"{_registry_recovery_hint()}：{exc}"
        ) from exc
    projects = registry["projects"]
    if not projects:
        print("当前 Harness 仓库没有项目绑定记录。")
        return
    for project, item in sorted(projects.items()):
        state = "存在" if Path(project).is_dir() else "目录缺失"
        print(
            f"{item.get('system_name', item.get('system_id', 'unknown'))}\t"
            f"{project}\t{state}"
        )


def _add_project_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=".", help="目标项目目录，默认当前目录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harness Engineering 项目接入工具")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="首次绑定领域系统与目标项目")
    _add_project_argument(init_parser)
    init_parser.add_argument("--system", help="领域系统 id；当前只有一个时自动选择")
    init_parser.add_argument(
        "--rules",
        choices=("auto", "agents", "claude", "both"),
        default="auto",
        help="规则入口；默认复用项目已有 AGENTS.md/CLAUDE.md",
    )
    init_parser.add_argument(
        "--with-skill",
        action="append",
        default=[],
        metavar="NAME",
        help="额外投影领域系统允许的条件或本机 Skill，可重复",
    )
    init_parser.add_argument(
        "--allow-non-git",
        action="store_true",
        help="显式允许绑定没有 .git 的目录",
    )

    relink_parser = commands.add_parser(
        "relink", help="复用原选择重新建立 Runtime、Skills 与规则入口"
    )
    _add_project_argument(relink_parser)

    doctor_parser = commands.add_parser("doctor", help="检查当前项目绑定")
    _add_project_argument(doctor_parser)

    remove_parser = commands.add_parser("remove", help="解除当前项目绑定")
    _add_project_argument(remove_parser)

    commands.add_parser("list", help="列出本仓库记录的目标项目绑定")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "list":
            command_list()
        else:
            project = _project_path(args.project)
            if args.command == "init":
                command_init(
                    project,
                    args.system,
                    args.rules,
                    args.with_skill,
                    args.allow_non_git,
                )
            elif args.command == "relink":
                command_relink(project)
            elif args.command == "doctor":
                command_doctor(project)
            elif args.command == "remove":
                command_remove(project)
    except (HarnessError, OSError, UnicodeError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
