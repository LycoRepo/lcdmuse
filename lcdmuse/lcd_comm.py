# SPDX-License-Identifier: GPL-3.0-or-later
"""驱动契约（原 `library/lcd/lcd_comm.py` 的抽象层，保留文件名）。

只描述「一台设备」与「一帧画面」，不含任何具体协议、VID/PID、波特率。
绘制原语搬到 `lcdmuse/render/`，链路搬到 `lcdmuse/transport/`（见 DESIGN §9 偏差 2）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntFlag
from typing import Any, ClassVar
from collections.abc import Mapping

from PIL import Image

DRIVER_API_VERSION = 1


# ── 错误：MCP 的 error.code 直接取这里的 code，口径唯一 ────────────────────
class LcdError(Exception):
    code = "driver_error"

    def __init__(self, message: str, *, detail: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.detail = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "detail": self.detail}}


class DeviceNotFound(LcdError):
    code = "device_not_found"


class DeviceOffline(LcdError):
    code = "device_offline"


class DeviceBusy(LcdError):
    code = "device_busy"


class DriverFailure(LcdError):
    code = "driver_error"


class InvalidArgument(LcdError):
    code = "invalid_argument"


class Unsupported(LcdError):
    code = "unsupported"


# ── 能力位 ────────────────────────────────────────────────────────────────
class Capability(IntFlag):
    PUSH_FULL = 1 << 0
    PUSH_REGION = 1 << 1
    BRIGHTNESS = 1 << 2
    ROTATION = 1 << 3
    POWER = 1 << 4
    BACKLIGHT_RGB = 1 << 5
    RAW_COMMAND = 1 << 6
    KEEPALIVE = 1 << 7


@dataclass(frozen=True)
class DeviceInfo:
    """驱动 probe/open 后回报的设备信息。核心只认这些字段。"""

    id: str
    name: str
    width: int
    height: int
    shape: str = "rect"
    native_rotation: int = 0
    capabilities: Capability = Capability.PUSH_FULL
    driver_id: str = ""
    firmware: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "shape": self.shape,
            "native_rotation": self.native_rotation,
            "driver_id": self.driver_id,
            "firmware": self.firmware,
            "capabilities": [c.name for c in Capability if c & self.capabilities],
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class Frame:
    """RGB888 整帧；尺寸 = DeviceInfo.width × height（逻辑方向，未施加 native_rotation）。"""

    width: int
    height: int
    data: bytes

    @classmethod
    def from_image(cls, img: Image.Image) -> Frame:
        img = img.convert("RGB")
        return cls(width=img.width, height=img.height, data=img.tobytes())

    def to_image(self) -> Image.Image:
        return Image.frombytes("RGB", (self.width, self.height), self.data)

    def rotated(self, degrees: int) -> Frame:
        """按 90 的整数倍旋转（核心送帧前统一调用）。"""
        d = degrees % 360
        if d == 0:
            return self
        img = self.to_image().rotate(-d, expand=True)
        return Frame.from_image(img)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


class LcdComm(ABC):
    """所有驱动插件的基类。

    必须实现：open / close / push_frame。
    其余能力按 capabilities 声明，未声明的调用会拿到 Unsupported。
    """

    api_version: ClassVar[int] = DRIVER_API_VERSION
    driver_id: ClassVar[str] = ""
    display_name: ClassVar[str] = ""

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config: dict[str, Any] = dict(config or {})
        self._info: DeviceInfo | None = None

    # ── 探测 / 生命周期 ──────────────────────────────────────────────────
    @classmethod
    def probe(cls, config: Mapping[str, Any] | None = None) -> DeviceInfo | None:
        """能不能看到设备。看不到返回 None（不要抛异常）。"""
        raise NotImplementedError

    @abstractmethod
    def open(self) -> DeviceInfo:
        """打开设备并返回实际信息；失败抛 DeviceOffline / DeviceBusy。"""

    def close(self) -> None:
        """释放设备。默认空实现。"""

    @property
    def info(self) -> DeviceInfo:
        if self._info is None:
            raise DeviceOffline("设备尚未 open")
        return self._info

    def bind(self, info: DeviceInfo) -> DeviceInfo:
        self._info = info
        return info

    # ── 送帧（唯一必需能力）──────────────────────────────────────────────
    @abstractmethod
    def push_frame(self, frame: Frame) -> None:
        """整帧。frame 尺寸必须等于 info.size（核心已按 native_rotation 摆正）。"""

    def push_region(self, frame: Frame, x: int, y: int) -> None:
        raise Unsupported(f"{self.driver_id or type(self).__name__} 不支持区域绘制")

    # ── 可选能力 ────────────────────────────────────────────────────────
    def set_brightness(self, percent: int) -> None:
        raise Unsupported("不支持调亮度")

    def set_rotation(self, degrees: int) -> None:
        raise Unsupported("不支持设备级旋转")

    def power(self, on: bool) -> None:
        raise Unsupported("不支持开关屏")

    def tick(self, now: float) -> None:
        """周期心跳（保活用）。默认什么都不做。"""

    def raw_command(self, payload: bytes) -> bytes:
        raise Unsupported("不支持裸命令")

    # ── 工具 ────────────────────────────────────────────────────────────
    def require(self, cap: Capability) -> None:
        if not (self.info.capabilities & cap):
            raise Unsupported(f"{type(self).__name__} 未声明能力 {cap.name}")

    def with_info(self, info: DeviceInfo) -> LcdComm:
        self._info = info
        return self
