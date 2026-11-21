# SPDX-License-Identifier: GPL-3.0-or-later
"""插件加载：扫 `drivers/<id>/driver.yaml`，动态导入，校验契约版本、核心版本与依赖。

两个来源：仓库自带的 `drivers/`（builtin），和用户目录（user，Windows 在 %APPDATA%\\lcdmuse\\drivers）。
刻意不用 entry_points / pip 安装：桌面程序"拷一个目录就多一个驱动"更好维护。
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lcdmuse import __version__ as CORE_VERSION
from lcdmuse.lcd_comm import DRIVER_API_VERSION, LcdComm


def _ver_tuple(text: str) -> tuple[int, ...]:
    """把 "0.1.0" 变成 (0,1,0)；非数字段当 0，坏版本号不会把整个扫描炸掉。"""
    parts: list[int] = []
    for chunk in str(text).replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _core_too_old(min_core: str) -> bool:
    return bool(min_core) and _ver_tuple(CORE_VERSION) < _ver_tuple(min_core)


@dataclass
class DriverPlugin:
    id: str
    name: str
    version: str
    api: int
    entry: str
    path: Path
    platforms: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    description: str = ""
    settings_schema: dict[str, Any] = field(default_factory=dict)
    enabled_by_default: bool = True
    origin: str = "builtin"          # builtin（仓库自带）/ user（用户装的）
    min_core: str = ""               # 要求的最低核心版本，空表示不限
    available: bool = True
    reason: str = ""
    cls: type[LcdComm] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "api": self.api,
            "available": self.available,
            "reason": self.reason,
            "requires": list(self.requires),
            "platforms": list(self.platforms),
            "description": self.description,
            "enabled_by_default": self.enabled_by_default,
            "origin": self.origin,
            "min_core": self.min_core,
            "path": str(self.path),
        }


def _check_requires(requires: list[str]) -> tuple[bool, str]:
    import importlib.util as iu
    missing = [m for m in requires if iu.find_spec(m) is None]
    if missing:
        return False, "缺少依赖: " + ", ".join(missing)
    return True, ""


def _load_class(driver_dir: Path, module_ref: str, class_name: str, module_name: str) -> type[LcdComm]:
    file_path = driver_dir / f"{module_ref}.py"
    if not file_path.exists():
        raise FileNotFoundError(f"入口模块不存在: {file_path}")
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {file_path} 创建 module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    obj = getattr(module, class_name, None)
    if obj is None:
        raise AttributeError(f"{file_path.name} 里没有 {class_name}")
    if not (isinstance(obj, type) and issubclass(obj, LcdComm)):
        raise TypeError(f"{class_name} 必须继承 lcdmuse.lcd_comm.LcdComm")
    return obj


def load_plugins(driver_root: Path | str, origin: str = "builtin") -> list[DriverPlugin]:
    """扫描驱动目录。任何单个插件出问题都只标 unavailable，不影响其它插件。"""
    root = Path(driver_root)
    plugins: list[DriverPlugin] = []
    if not root.is_dir():
        return plugins

    for manifest in sorted(root.glob("*/driver.yaml")):
        driver_dir = manifest.parent
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            plugins.append(DriverPlugin(id=driver_dir.name, name=driver_dir.name, version="?",
                                        api=0, entry="", path=driver_dir,
                                        available=False, reason=f"driver.yaml 解析失败: {exc}"))
            continue

        plugin = DriverPlugin(
            id=str(data.get("id") or driver_dir.name),
            name=str(data.get("name") or driver_dir.name),
            version=str(data.get("version") or "0.0.0"),
            api=int(data.get("api") or 0),
            entry=str(data.get("entry") or ""),
            path=driver_dir,
            platforms=list(data.get("platforms") or []),
            requires=list(data.get("requires") or []),
            description=str(data.get("description") or ""),
            settings_schema=dict(data.get("settings_schema") or {}),
            enabled_by_default=bool(data.get("enabled_by_default", True)),
            origin=origin,
            min_core=str(data.get("min_core") or ""),
        )

        if _core_too_old(plugin.min_core):
            plugin.available, plugin.reason = False, (
                f"需要核心 >= {plugin.min_core}，当前 {CORE_VERSION}")
            plugins.append(plugin)
            continue

        if plugin.api != DRIVER_API_VERSION:
            plugin.available, plugin.reason = False, f"契约版本不符: 插件 {plugin.api} != 核心 {DRIVER_API_VERSION}"
            plugins.append(plugin)
            continue

        ok, why = _check_requires(plugin.requires)
        if not ok:
            plugin.available, plugin.reason = False, why
            plugins.append(plugin)
            continue

        try:
            module_ref, _, class_name = plugin.entry.partition(":")
            plugin.cls = _load_class(driver_dir, module_ref or "driver", class_name or "Driver",
                                     f"lcd_drivers_{plugin.id}")
            plugin.cls.driver_id = plugin.id
            plugin.cls.display_name = plugin.name
        except Exception as exc:
            plugin.available, plugin.reason = False, f"加载失败: {exc}"
        plugins.append(plugin)

    return plugins
