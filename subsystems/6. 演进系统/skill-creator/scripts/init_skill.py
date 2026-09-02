#!/usr/bin/env python3
"""Create a minimal Skill scaffold."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_RESOURCES = {"scripts", "references", "assets"}


def normalize_name(raw_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", raw_name.strip().lower())
    return re.sub(r"-{2,}", "-", normalized).strip("-")


def display_title(skill_name: str) -> str:
    return " ".join(part.capitalize() for part in skill_name.split("-"))


def parse_resources(raw_resources: str) -> list[str]:
    resources = list(dict.fromkeys(
        item.strip() for item in raw_resources.split(",") if item.strip()
    ))
    invalid = sorted(set(resources) - ALLOWED_RESOURCES)
    if invalid:
        allowed = ", ".join(sorted(ALLOWED_RESOURCES))
        raise ValueError(f"未知资源目录: {', '.join(invalid)}；可选值: {allowed}")
    return resources


def skill_template(skill_name: str, description: str) -> str:
    return f"""---
name: {skill_name}
description: {description}
---

# {display_title(skill_name)}

## 定位

[说明该 Skill 解决的问题、用户结果和职责边界。]

## 执行

[用命令式表达核心决策和工作流；按真实任务组织，不保留模板说明。]

## 验证

[说明与风险匹配的验收证据和结论边界。]
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="创建包含 SKILL.md 和按需资源的最小 Skill 目录。"
    )
    parser.add_argument("skill_name", help="Skill 名称；会规范为 lowercase hyphen-case")
    parser.add_argument("--path", required=True, help="Skill 父目录")
    parser.add_argument(
        "--description",
        required=True,
        help="完整职责、触发和排他描述",
    )
    parser.add_argument(
        "--resources",
        default="",
        help="按需创建的目录，以逗号分隔: scripts,references,assets",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    skill_name = normalize_name(args.skill_name)
    if not skill_name:
        print("[ERROR] Skill 名称必须包含字母或数字。", file=sys.stderr)
        return 1
    if len(skill_name) > MAX_SKILL_NAME_LENGTH:
        print(
            f"[ERROR] Skill 名称超过 {MAX_SKILL_NAME_LENGTH} 个字符。",
            file=sys.stderr,
        )
        return 1
    if args.skill_name != skill_name:
        print(f"[INFO] 名称已规范化: {args.skill_name} -> {skill_name}")

    description = args.description.strip()
    if not description or "\n" in description:
        print("[ERROR] description 必须是非空单行文本。", file=sys.stderr)
        return 1
    try:
        resources = parse_resources(args.resources)
    except ValueError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1

    skill_dir = Path(args.path).resolve() / skill_name
    if skill_dir.exists():
        print(f"[ERROR] 目标已经存在: {skill_dir}", file=sys.stderr)
        return 1

    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        skill_template(skill_name, description), encoding="utf-8"
    )

    for resource in resources:
        (skill_dir / resource).mkdir()

    print(f"[OK] 已初始化 Skill: {skill_dir}")
    print("[NEXT] 完整替换 SKILL.md 占位内容，添加真实资源并执行校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
