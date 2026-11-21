# SPDX-License-Identifier: GPL-3.0-or-later
"""TURZX / Turing USB（1CBE）（原厂驱动，移植成插件；默认不加载，用作契约回归）。

实现方式：适配层——真正的协议实现仍在 `drivers/_legacy/library/`，
本类只负责把它接到 lcdpilot 的契约上。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from collections.abc import Mapping

_LEGACY = Path(__file__).resolve().parent.parent / "_legacy"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

from lcdpilot.lcd_comm import Capability, DeviceInfo, Frame, LcdComm, DriverFailure  # noqa: E402


class TurzxUsbDriver(LcdComm):
    driver_id = "turzx_usb"
    display_name = "TURZX / Turing USB（1CBE）"

    legacy_module = "lcd_comm_turing_usb"
    legacy_class = "LcdCommTuringUSB"
    default_size = (320, 480)
    capabilities_mask = Capability.PUSH_FULL | Capability.BRIGHTNESS

    # ── 老实现加载 ────────────────────────────────────────────────────
    @classmethod
    def _legacy_cls(cls):
        import importlib
        mod = importlib.import_module(f"library.lcd.{cls.legacy_module}")
        return getattr(mod, cls.legacy_class)

    @classmethod
    def _make_info(cls, cfg: Mapping[str, Any], device_key: str) -> DeviceInfo:
        w = int(cfg.get("width") or cls.default_size[0])
        h = int(cfg.get("height") or cls.default_size[1])
        return DeviceInfo(
            id=f"{cls.driver_id}:{device_key}",
            name="TURZX / Turing USB（1CBE）",
            width=w,
            height=h,
            capabilities=cls.capabilities_mask,
            driver_id=cls.driver_id,
            firmware="legacy",
            extra={"port": device_key},
        )

    @classmethod
    def probe(cls, config: Mapping[str, Any] | None = None) -> DeviceInfo | None:
        cfg = dict(config or {})
        port = str(cfg.get("port") or "AUTO")
        if port != "AUTO":
            return cls._make_info(cfg, port)
        try:
            found = cls._legacy_cls().auto_detect_com_port()
        except Exception:
            found = None
        if not found and cfg.get("assume_present"):
            found = "AUTO"
        return cls._make_info(cfg, found) if found else None

    # ── 契约实现 ──────────────────────────────────────────────────────
    def open(self) -> DeviceInfo:
        self._lcd = None
        cfg = self.config
        port = str(cfg.get("port") or "AUTO")
        klass = self._legacy_cls()
        self._lcd = klass(com_port=port,
                          display_width=int(cfg.get("width") or self.default_size[0]),
                          display_height=int(cfg.get("height") or self.default_size[1]))
        try:
            self._lcd.InitializeComm()
        except Exception as exc:
            raise DriverFailure(f"{self.driver_id} 初始化失败: {exc}") from exc
        info = self.probe(cfg) or self._make_info(cfg, port)
        return self.bind(info)

    def close(self) -> None:
        try:
            ser = getattr(self._lcd, "lcd_serial", None)
            if ser is not None:
                ser.close()
        except Exception:
            pass

    def push_frame(self, frame: Frame) -> None:
        img = frame.to_image()
        try:
            self._lcd.DisplayPILImage(img)
        except Exception as exc:
            raise DriverFailure(f"送帧失败: {exc}") from exc

    def set_brightness(self, percent: int) -> None:
        try:
            self._lcd.SetBrightness(max(0, min(100, int(percent))))
        except Exception as exc:
            raise DriverFailure(f"设置亮度失败: {exc}") from exc
