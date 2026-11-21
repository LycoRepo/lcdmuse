"""示例驱动：不碰硬件，把收到的帧存盘。

它就是"写一个驱动"的最小样板：实现 probe/open/push_frame，其余能力按需覆盖。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from lcdkit.lcd_comm import Capability, DeviceInfo, Frame, LcdComm


class SimulatedDriver(LcdComm):
    driver_id = "simulated"
    display_name = "模拟屏（离线验证）"

    @classmethod
    def probe(cls, config: Mapping[str, Any] | None = None) -> DeviceInfo | None:
        cfg = dict(config or {})
        return DeviceInfo(
            id="simulated:0",
            name="模拟屏（离线验证）",
            width=int(cfg.get("width", 960)),
            height=int(cfg.get("height", 480)),
            capabilities=Capability.PUSH_FULL | Capability.BRIGHTNESS | Capability.ROTATION,
            driver_id=cls.driver_id,
            firmware="simulated-1",
        )

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.frames = 0
        self.last_bytes = 0
        self.brightness = 100
        self.out_dir = Path(self.config.get("out_dir") or "out")
        self._opened_at = 0.0

    def open(self) -> DeviceInfo:
        info = self.probe(self.config)
        assert info is not None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._opened_at = time.time()
        return self.bind(info)

    def push_frame(self, frame: Frame) -> None:
        if frame.size != self.info.size:
            raise ValueError(f"帧尺寸 {frame.size} 与设备 {self.info.size} 不一致")
        self.frames += 1
        self.last_bytes = len(frame.data)
        frame.to_image().save(self.out_dir / "last-frame.png")

    def set_brightness(self, percent: int) -> None:
        self.brightness = max(0, min(100, int(percent)))

    def set_rotation(self, degrees: int) -> None:
        if degrees % 90:
            raise ValueError("旋转必须是 90 的整数倍")

    def status(self) -> dict[str, Any]:
        return {
            "uptime_s": round(time.time() - self._opened_at, 3),
            "frames": self.frames,
            "last_frame_bytes": self.last_bytes,
            "brightness": self.brightness,
            "out_dir": str(self.out_dir.resolve()),
        }
