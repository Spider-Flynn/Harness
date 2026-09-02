"""列出本仓库索引中的目标项目。"""

from __future__ import annotations

from pathlib import Path

from ..errors import HarnessError
from ..storage import load_registry, registry_recovery_hint


def run() -> None:
    try:
        registry = load_registry()
    except (HarnessError, OSError, UnicodeError) as exc:
        raise HarnessError(
            "本机绑定索引不可读，但目标项目清单不受影响；"
            f"{registry_recovery_hint()}：{exc}"
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
