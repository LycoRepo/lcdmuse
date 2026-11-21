# SPDX-License-Identifier: GPL-3.0-or-later
"""数据接口：主题、桌宠、Web 面板都从这里取数。

设计原则：
- 只给**扁平的 `名字 → 数值`**字典，键名稳定（`cpu.percent` 这种），
  主题里绑的是键名，不是函数，所以主题是纯数据，可以换、可以传给别人。
- 取不到的指标宁可不出现，也不编一个数出来（前端/主题自己判断缺省）。
- 第三方可以加自己的指标：`get_hub().register("myapp", fn)`。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from lcdmuse.config import log

Provider = Callable[[], dict[str, Any]]

#: 指标说明：名字 → (说明, 单位)。主题/文档/接口都用这张表当权威清单。
CATALOG: dict[str, tuple[str, str]] = {
    "time.now": ("时间 HH:MM", ""),
    "time.seconds": ("当日秒数（画时钟指针用）", "s"),
    "date.today": ("日期 YYYY-MM-DD", ""),
    "date.weekday": ("星期几（1=周一）", ""),
    "uptime.hours": ("开机时长", "h"),
    "cpu.percent": ("CPU 总占用", "%"),
    "cpu.freq_mhz": ("CPU 当前频率", "MHz"),
    "cpu.temp_c": ("CPU 温度（能拿到才有）", "°C"),
    "mem.percent": ("内存占用", "%"),
    "mem.used_mb": ("内存已用", "MB"),
    "mem.total_mb": ("内存总量", "MB"),
    "swap.percent": ("交换区占用", "%"),
    "disk.percent": ("系统盘占用", "%"),
    "disk.free_gb": ("系统盘剩余", "GB"),
    "net.rx_kbps": ("下行速率", "KB/s"),
    "net.tx_kbps": ("上行速率", "KB/s"),
    "net.total_gb": ("累计流量", "GB"),
    "gpu.percent": ("NVIDIA GPU 占用（有 nvidia-smi 才有）", "%"),
    "gpu.temp_c": ("GPU 温度", "°C"),
    "gpu.mem_percent": ("显存占用", "%"),
}


@dataclass
class MetricsHub:
    """采集器。一个进程一个，主题/前端查到的是同一时刻的同一份数。"""

    providers: dict[str, Provider] = field(default_factory=dict)
    _last: dict[str, Any] = field(default_factory=dict)
    _last_net: tuple[float, int, int] | None = None

    def register(self, prefix: str, provider: Provider) -> None:
        """第三方指标：返回的字典会以 `<prefix>.<key>` 合并进来。"""
        self.providers[prefix] = provider

    # ── 采样 ────────────────────────────────────────────────────────────
    def sample(self, now: float | None = None) -> dict[str, Any]:
        now = now if now is not None else time.time()
        data: dict[str, Any] = {}
        lt = time.localtime(now)
        data["time.now"] = time.strftime("%H:%M", lt)
        data["time.seconds"] = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
        data["date.today"] = time.strftime("%Y-%m-%d", lt)
        data["date.weekday"] = lt.tm_wday + 1

        self._system(data, now)
        for prefix, provider in self.providers.items():
            try:
                for key, value in (provider() or {}).items():
                    data[f"{prefix}.{key}"] = value
            except Exception as exc:                    # 第三方指标坏了别拖垮整帧
                log.debug("指标 %s 失败: %s", prefix, exc)

        self._last = {k: v for k, v in data.items() if v is not None}
        return dict(self._last)

    def last(self) -> dict[str, Any]:
        return dict(self._last)

    # ── 系统指标 ────────────────────────────────────────────────────────
    def _system(self, data: dict[str, Any], now: float) -> None:
        try:
            import psutil
        except Exception:
            return
        with _quiet():
            data["cpu.percent"] = round(psutil.cpu_percent(interval=None), 1)
            freq = psutil.cpu_freq()
            if freq:
                data["cpu.freq_mhz"] = int(freq.current)
            mem = psutil.virtual_memory()
            data["mem.percent"] = round(mem.percent, 1)
            data["mem.used_mb"] = int(mem.used / 1048576)
            data["mem.total_mb"] = int(mem.total / 1048576)
            swap = psutil.swap_memory()
            if swap.total:
                data["swap.percent"] = round(swap.percent, 1)
            disk = psutil.disk_usage(os.path.abspath(os.sep))
            data["disk.percent"] = round(disk.percent, 1)
            data["disk.free_gb"] = round(disk.free / 1073741824, 1)
            data["uptime.hours"] = round((now - psutil.boot_time()) / 3600, 1)

            net = psutil.net_io_counters()
            data.setdefault("net.rx_kbps", 0.0)
            data.setdefault("net.tx_kbps", 0.0)
            if self._last_net is not None:
                dt = max(1e-3, now - self._last_net[0])
                data["net.rx_kbps"] = round(max(0, net.bytes_recv - self._last_net[1]) / dt / 1024, 1)
                data["net.tx_kbps"] = round(max(0, net.bytes_sent - self._last_net[2]) / dt / 1024, 1)
            self._last_net = (now, net.bytes_recv, net.bytes_sent)
            data["net.total_gb"] = round((net.bytes_recv + net.bytes_sent) / 1073741824, 2)

            temps = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
            for entries in temps.values():
                if entries and entries[0].current:
                    data["cpu.temp_c"] = round(float(entries[0].current), 1)
                    break

        gpu = _nvidia_smi()
        data.update(gpu)

    # ── 展示用 ──────────────────────────────────────────────────────────
    @staticmethod
    def describe() -> list[dict[str, str]]:
        return [{"name": k, "label": v[0], "unit": v[1]} for k, v in CATALOG.items()]


class _quiet:
    """psutil 在 Windows 上偶尔会因为某个计数器抛异常，别让整帧挂掉。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            log.debug("系统指标部分失败: %s", exc)
            return True
        return False


_SMI_ARGS = ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"]


def _nvidia_smi() -> dict[str, Any]:
    """有 NVIDIA 卡就顺手取一下；没有就返回空字典，不报错。"""
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        out = subprocess.run(_SMI_ARGS, capture_output=True, text=True, timeout=2,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (out.stdout or "").strip().splitlines()[0]
        util, temp, used, total = (part.strip() for part in line.split(","))
        data: dict[str, Any] = {"gpu.percent": float(util), "gpu.temp_c": float(temp)}
        if float(total):
            data["gpu.mem_percent"] = round(float(used) / float(total) * 100, 1)
        return data
    except Exception as exc:
        log.debug("nvidia-smi 不可用: %s", exc)
        return {}


_HUB: MetricsHub | None = None


def get_hub() -> MetricsHub:
    global _HUB
    if _HUB is None:
        _HUB = MetricsHub()
    return _HUB


def read(now: float | None = None) -> dict[str, Any]:
    """最短路径：`lcdmuse.metrics.read()` 拿一份当前数值。"""
    return get_hub().sample(now)


def format_value(value: Any, *, unit: str = "", decimals: int | None = None,
                 prefix: str = "", suffix: str = "") -> str:
    """主题里的显示格式化：数字按 decimals 取位，其余原样。"""
    if isinstance(value, bool):
        text = "开" if value else "关"
    elif isinstance(value, (int, float)):
        if decimals is None:
            decimals = 0 if abs(value) >= 100 or float(value).is_integer() else 1
        text = f"{value:.{decimals}f}"
    else:
        text = str(value)
    return f"{prefix}{text}{unit}{suffix}"
