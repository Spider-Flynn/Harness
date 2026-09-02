"""源码根目录与目标项目受管路径。"""

from __future__ import annotations

from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[2]
SYSTEMS_ROOT = HARNESS_ROOT / "systems"
MANAGED_ROOT = Path(".harness")
MANIFEST_PATH = MANAGED_ROOT / "manifest.json"
RUNTIME_LINK = MANAGED_ROOT / "runtime/HARNESS.md"
SKILLS_ROOT = Path(".agents/skills")
