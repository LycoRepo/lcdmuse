# SPDX-License-Identifier: GPL-3.0-or-later
"""颜色解析：主题 yaml 里的 "255, 255, 255" / "#fff" / [r,g,b] 统一成 RGB 元组。"""
from __future__ import annotations

from typing import Any
from collections.abc import Sequence

RGB = tuple[int, int, int]


def parse_color(value: Any, default: RGB = (255, 255, 255)) -> RGB:
    if value is None:
        return default
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return tuple(max(0, min(255, int(v))) for v in value[:3])  # type: ignore[return-value]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("#"):
            s = s[1:]
            if len(s) == 3:
                s = "".join(ch * 2 for ch in s)
            if len(s) >= 6:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
            raise ValueError(f"无法解析颜色: {value!r}")
        parts = [p for p in s.replace(";", ",").split(",") if p.strip()]
        if len(parts) >= 3:
            return tuple(max(0, min(255, int(float(p)))) for p in parts[:3])  # type: ignore[return-value]
    raise ValueError(f"无法解析颜色: {value!r}")


def to_hex(rgb: Sequence[int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[int(c) for c in rgb[:3]])
