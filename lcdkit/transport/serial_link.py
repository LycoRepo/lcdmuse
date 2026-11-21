"""链路层：只管「把字节送出去」，不认识任何设备。

刻意不含端口名、波特率、命令字——这些由驱动从自己的配置里传进来。
"""
from __future__ import annotations

import time
from typing import Any


class TransportError(Exception):
    pass


class SerialLink:
    """pyserial 的薄封装。依赖缺失时构造即抛，驱动应在 probe 里吞掉。"""

    def __init__(self, port: str, baud: int, *, timeout: float = 1.0, write_timeout: float = 3.0,
                 bytesize: int = 8, parity: str = "N", stopbits: int = 1):
        try:
            import serial  # noqa: F401
        except ImportError as exc:      # pragma: no cover
            raise TransportError("缺少 pyserial，本驱动不可用") from exc
        self.port, self.baud = port, int(baud)
        self._kwargs: dict[str, Any] = dict(timeout=timeout, write_timeout=write_timeout,
                                            bytesize=bytesize, parity=parity, stopbits=stopbits)
        self._ser = None

    def open(self) -> SerialLink:
        import serial
        try:
            self._ser = serial.Serial(self.port, self.baud, **self._kwargs)
        except Exception as exc:
            raise TransportError(f"打不开 {self.port}: {exc}") from exc
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        return self

    def write(self, data: bytes, *, chunk: int = 0, pace: float = 0.0) -> None:
        if self._ser is None:
            raise TransportError("链路未打开")
        if chunk <= 0:
            self._ser.write(data)
        else:
            for i in range(0, len(data), chunk):
                self._ser.write(data[i:i + chunk])
                if pace:
                    time.sleep(pace)
        self._ser.flush()

    def read(self, size: int = 1, timeout: float | None = None) -> bytes:
        if self._ser is None:
            raise TransportError("链路未打开")
        if timeout is not None:
            self._ser.timeout = timeout
        return self._ser.read(size)

    def read_exact(self, size: int, timeout: float = 1.0) -> bytes:
        deadline = time.monotonic() + timeout
        buf = b""
        while len(buf) < size and time.monotonic() < deadline:
            chunk = self.read(size - len(buf), timeout=max(0.01, deadline - time.monotonic()))
            if not chunk:
                break
            buf += chunk
        return buf

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None

    def __enter__(self) -> SerialLink:
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()


def list_serial_ports() -> list[str]:
    """枚举系统串口，供配置向导使用。"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]
