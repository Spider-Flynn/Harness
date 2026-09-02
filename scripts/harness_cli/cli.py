"""CLI 参数解析、命令分派与错误出口。"""

from __future__ import annotations

import argparse
import sys

from .commands import doctor, init, list_projects, relink, remove
from .errors import HarnessError
from .filesystem import project_path


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
        "relink", help="复用原选择重新建立 Runtime、Skills、规则入口与 Git Hook"
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
            list_projects.run()
        else:
            project = project_path(args.project)
            if args.command == "init":
                init.run(
                    project,
                    args.system,
                    args.rules,
                    args.with_skill,
                    args.allow_non_git,
                )
            elif args.command == "relink":
                relink.run(project)
            elif args.command == "doctor":
                doctor.run(project)
            elif args.command == "remove":
                remove.run(project)
    except (HarnessError, OSError, UnicodeError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0
