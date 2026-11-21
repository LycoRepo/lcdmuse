# SPDX-License-Identifier: GPL-3.0-or-later
"""推帧循环：托盘和 CLI 共用的一段逻辑，和界面无关（便于无头测试）。

职责边界：这里只管"按间隔把画面推给驱动"，不碰图标、不碰菜单。
设备掉了就退避重连，不把异常甩给界面。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lcdmuse.config import Config, log
from lcdmuse.lcd_comm import DeviceInfo, DeviceOffline, LcdError
from lcdmuse.pet import Pet
from lcdmuse.registry import Registry
from lcdmuse.render.demo import render_demo
import contextlib


@dataclass
class PlayerStatus:
    plugin: str = ""
    device: str = ""
    size: tuple[int, int] = (0, 0)
    running: bool = False
    paused: bool = False
    pet: str | None = None
    frames: int = 0
    fps: float = 0.0
    last_error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if self.last_error and not self.running:
            return f"未运行：{self.last_error[:60]}"
        if self.paused:
            return f"已暂停 · {self.device} 已推 {self.frames} 帧"
        return f"{self.device or '未连接'} · {self.fps:.1f} fps · 已推 {self.frames} 帧"


class Player:
    """把 render_demo (+桌宠) 按间隔推给某个驱动。"""

    def __init__(self, cfg: Config, plugin_id: str = "", interval: float = 1.0,
                 pet: str | None = "whale-girl"):
        self.cfg = cfg
        self.registry = Registry([str(p) for p in cfg.drivers_dirs])
        self.interval = max(0.1, float(interval))
        self.pet_name = pet
        self.status = PlayerStatus(plugin=plugin_id, pet=pet)
        self.plugin_id = plugin_id or self.autodetect() or ""
        self.status.plugin = self.plugin_id

        self._driver = None
        self._info: DeviceInfo | None = None
        self._pet: Pet | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ── 自动挑设备 ──────────────────────────────────────────────────────
    def autodetect(self) -> str:
        try:
            found = self.registry.probe(self.cfg.drivers)
        except Exception as exc:                       # 探测失败不该炸托盘
            log.warning("探测失败: %s", exc)
            return ""
        if not found:
            return ""
        plugin, info = found[0]
        self.status.device = info.name
        return plugin.id

    # ── 生命周期 ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lcdmuse-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._close()

    def pause(self) -> None:
        self.status.paused = True

    def resume(self) -> None:
        self.status.paused = False

    def toggle_pause(self) -> bool:
        self.status.paused = not self.status.paused
        return self.status.paused

    def set_pet(self, name: str | None) -> None:
        self.pet_name = name or None
        self.status.pet = self.pet_name
        self._pet = None                                  # 下次渲染重新加载

    def toggle_pet(self) -> str | None:
        self.set_pet(None if self.pet_name else (self.cfg.default_pet or "whale-girl"))
        return self.pet_name

    # ── 主循环 ──────────────────────────────────────────────────────────
    def _run(self) -> None:
        backoff = 3.0
        last_beat = 0.0
        last_push = 0.0
        while not self._stop.is_set():
            if not self.plugin_id:
                self.plugin_id = self.autodetect()
            if not self._info and not self._open():
                self.status.running = False
                self._stop.wait(backoff)
                continue

            self.status.running = True
            started = time.time()
            pushed = False
            try:
                if not self.status.paused:
                    self._push_once()
                    pushed = True
                self.status.last_error = ""
                backoff = 3.0
            except LcdError as exc:
                self.status.last_error = exc.message
                log.warning("推帧失败: %s", exc.message)
                self._close()
                self.status.running = False
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            if pushed:
                now = time.time()
                gap = now - last_push if last_push else max(1e-3, now - started)
                last_push = now
                self.status.frames += 1
                self.status.fps = 1.0 / max(gap, 1e-3)
                if time.time() - last_beat >= 30:
                    last_beat = time.time()
                    log.info("心跳 %s：%d 帧，%.1f fps", self.status.device or self.plugin_id,
                             self.status.frames, self.status.fps)
            self._stop.wait(self.interval)

    def _push_once(self) -> None:
        info = self._info
        driver = self._driver
        if info is None or driver is None:
            raise DeviceOffline("设备未打开")
        canvas = render_demo(info.width, info.height, str(self.cfg.fonts_dir))
        if self.pet_name:
            pet = self._pet_obj()
            if pet is not None:
                pet.draw_on(canvas)
        driver.push_frame(canvas.to_frame())

    def _pet_obj(self) -> Pet | None:
        if self._pet is None and self.pet_name:
            try:
                self._pet = Pet.load(Path(self.cfg.pets_dir) / self.pet_name)
            except Exception as exc:
                log.warning("桌宠 %s 加载失败: %s", self.pet_name, exc)
                self._pet = None
        return self._pet

    # ── 设备 ────────────────────────────────────────────────────────────
    def _open(self) -> bool:
        if not self.plugin_id:
            return False
        try:
            driver = self.registry.open(self.plugin_id, self.cfg.driver_config(self.plugin_id))
            self._info = driver.open()
            self._driver = driver
            self.status.device = self._info.name
            self.status.size = (self._info.width, self._info.height)
            self.status.plugin = self.plugin_id
            log.info("已打开 %s (%s) %dx%d", self._info.name, self.plugin_id,
                     self._info.width, self._info.height)
            return True
        except LcdError as exc:
            self.status.last_error = exc.message
            log.warning("打开 %s 失败: %s", self.plugin_id, exc.message)
            return False

    def _close(self) -> None:
        with self._lock:
            if self._driver is not None:
                with contextlib.suppress(Exception):
                    self._driver.close()
            self._driver = None
            self._info = None
