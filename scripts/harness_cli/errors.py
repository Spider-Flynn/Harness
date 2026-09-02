"""接入工具的可解释错误。"""

from __future__ import annotations


class HarnessError(RuntimeError):
    """接入操作无法安全完成。"""
