"""驱动注册表：枚举插件 → probe → 绑定设备。核心在这里与"具体硬件"完全解耦。"""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from dataclasses import replace

from lcdkit.lcd_comm import DeviceInfo, LcdComm, LcdError, DeviceNotFound
from lcdkit.plugin import DriverPlugin, load_plugins

_SYS = {"windows": "windows", "darwin": "darwin"}.get(platform.system().lower(),
                                                      platform.system().lower())


class Registry:
    def __init__(self, driver_root: Path | str):
        self.driver_root = Path(driver_root)
        self.plugins: list[DriverPlugin] = load_plugins(self.driver_root)

    # ── 枚举 ────────────────────────────────────────────────────────────
    def available(self, include_unavailable: bool = False) -> list[DriverPlugin]:
        return [p for p in self.plugins if p.available or include_unavailable]

    def get(self, plugin_id: str) -> DriverPlugin:
        for p in self.plugins:
            if p.id == plugin_id:
                return p
        raise DeviceNotFound(f"没有这个驱动: {plugin_id}")

    def describe(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.plugins]

    # ── 探测 ────────────────────────────────────────────────────────────
    def _platform_ok(self, plugin: DriverPlugin) -> bool:
        return not plugin.platforms or _SYS in [p.lower() for p in plugin.platforms]

    @staticmethod
    def _enabled(plugin: DriverPlugin, options: Mapping[str, Any]) -> bool:
        """enabled_by_default=false 的插件（原厂移植、仅作回归）默认不 probe。

        要打开它，配置里写 drivers.<id>.enabled: true。
        """
        return plugin.enabled_by_default or bool(options.get("enabled"))

    def probe(self, options: Mapping[str, Mapping[str, Any]] | None = None,
              only: str | None = None) -> list[tuple[DriverPlugin, DeviceInfo]]:
        """options = {plugin_id: 该插件的配置}；only 限定只探一个。"""
        options = options or {}
        found: list[tuple[DriverPlugin, DeviceInfo]] = []
        for plugin in self.available():
            if only and plugin.id != only:
                continue
            opts = dict(options.get(plugin.id) or {})
            if not plugin.cls or not self._platform_ok(plugin) or not self._enabled(plugin, opts):
                continue
            try:
                info = plugin.cls.probe(opts)
            except Exception:
                info = None
            if info:
                if not info.driver_id:
                    info = replace(info, driver_id=plugin.id)
                found.append((plugin, info))
        return found

    # ── 打开 ────────────────────────────────────────────────────────────
    def open(self, plugin_id: str, config: Mapping[str, Any] | None = None) -> LcdComm:
        plugin = self.get(plugin_id)
        if not plugin.available or plugin.cls is None:
            raise DeviceNotFound(f"{plugin_id} 不可用: {plugin.reason}")
        try:
            return plugin.cls(config)
        except LcdError:
            raise
        except Exception as exc:
            raise DeviceNotFound(f"{plugin_id} 实例化失败: {exc}") from exc


def find_driver_root(start: Path | None = None) -> Path:
    """默认在仓库根下的 drivers/。"""
    here = start or Path(__file__).resolve().parent.parent
    return Path(here) / "drivers"
