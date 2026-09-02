"""项目规则入口选择及受管片段维护。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .errors import HarnessError
from .filesystem import atomic_write, managed_path


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


def select_rule_files(project: Path, mode: str) -> list[Path]:
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


def rule_block_matches(content: str) -> bool:
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


def remove_rule_block(content: str) -> str:
    starts = content.count(RULE_BLOCK_START)
    ends = content.count(RULE_BLOCK_END)
    if starts != 1 or ends != 1:
        raise HarnessError("规则文件中的 Harness 受管标记缺失或重复")
    result = RULE_BLOCK_PATTERN.sub("", content, count=1).rstrip()
    return result + "\n" if result else ""


def validate_rule_target(project: Path, relative: Path) -> None:
    target = managed_path(project, relative)
    if target.is_symlink():
        raise HarnessError(f"拒绝通过符号链接维护规则入口：{relative}")
    if target.exists() and not target.is_file():
        raise HarnessError(f"规则入口不是普通文件：{relative}")


def rule_records(project: Path, rules: list[Path]) -> list[dict[str, Any]]:
    records = []
    for relative in rules:
        validate_rule_target(project, relative)
        target = managed_path(project, relative)
        records.append({"path": str(relative), "created": not target.exists()})
    return records


def write_rules(project: Path, rules: list[dict[str, Any]]) -> None:
    for record in rules:
        relative = Path(record["path"])
        target = managed_path(project, relative)
        content = target.read_text(encoding="utf-8") if target.exists() else ""
        atomic_write(target, _upsert_rule_block(content), new_mode=0o644)


def restore_rules(project: Path, snapshots: dict[Path, str | None]) -> None:
    for relative, content in snapshots.items():
        target = managed_path(project, relative)
        if content is None:
            if target.exists():
                target.unlink()
        else:
            atomic_write(target, content)
