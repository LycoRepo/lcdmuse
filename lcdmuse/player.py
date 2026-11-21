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
from lcdmuse.compose import Composition
from lcdmuse.lcd_comm import Frame
from lcdmuse.media import MediaSource
from lcdmuse.pet import Pet, find_pet, pet_dirs
from lcdmuse.registry import Registry
from lcdmuse.theme import find_theme, theme_dirs
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
                 pet: str | None = "whale-girl", stage=None):
        self.cfg = cfg
        self.registry = Registry([str(p) for p in cfg.drivers_dirs])
        fps = 0.0
        with contextlib.suppress(Exception):
            raw = (cfg.driver_config(plugin_id) or {}).get("target_fps")
            fps = float(raw or 0)
        self.interval = max(0.02, (1.0 / fps) if fps > 0 else float(interval))
        self.pet_name = pet
        self.stage = stage        # PetStage：多屏共享的一只桌宠；给了它就不再各屏各画一只
        self.status = PlayerStatus(plugin=plugin_id, pet=pet)
        self.plugin_id = plugin_id or self.autodetect() or ""
        self.status.plugin = self.plugin_id

        self._driver = None
        self._info: DeviceInfo | None = None
        self.last_image = None          # 最近一次渲染的画面（PIL.Image），给界面预览用
        self._pet: Pet | None = None
        self._theme_id: str = cfg.theme or ""
        self._background_path: str = cfg.background or ""
        self._comp = None
        self._comp_key: tuple | None = None
        self.status.extra["theme"] = self._theme_id
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
        self._pet = None
        self._comp = None
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
                self.status.fps = (1.0 / max(gap, 1e-3)) if self.status.frames > 1 else 1.0 / self.interval
                if time.time() - last_beat >= 30:
                    last_beat = time.time()
                    log.info("心跳 %s：%d 帧，%.1f fps", self.status.device or self.plugin_id,
                             self.status.frames, self.status.fps)
            spent = time.time() - started
            self._stop.wait(max(0.0, self.interval - spent))

    def _push_once(self) -> None:
        info = self._info
        driver = self._driver
        if info is None or driver is None:
            raise DeviceOffline("设备未打开")
        image = self._render_sized(info.width, info.height, step=self.status.frames)
        frame = Frame.from_image(image)
        self.last_image = image
        tick = getattr(driver, "tick", None)
        if callable(tick):                 # SAMA 这类要定时重发保活，别让屏自己睡过去
            with contextlib.suppress(Exception):
                tick(time.time())
        driver.push_frame(frame)

    def _safe_area(self) -> tuple[int, int, int, int]:
        """面板被外框挡住的边：驱动配置里的 safe_area = [左, 上, 右, 下]。

        实测：先马水冷头右侧那条金属边压住约 30px，内容画进去就被吃了。
        有它就把内容画在缩小后的画布上再居中贴回去，所有主题自动避让。
        """
        raw = (self.cfg.driver_config(self.plugin_id) or {}).get("safe_area")
        if isinstance(raw, dict):
            raw = [raw.get("left", 0), raw.get("top", 0), raw.get("right", 0), raw.get("bottom", 0)]
        try:
            left, top, right, bottom = (int(v) for v in (raw or [0, 0, 0, 0])[:4])
        except (TypeError, ValueError):
            return (0, 0, 0, 0)
        return (max(0, left), max(0, top), max(0, right), max(0, bottom))

    def _render_sized(self, width: int, height: int, step: int | None = None):
        left, top, right, bottom = self._safe_area()
        comp = self.composition(width, height)
        inner = (width, height)
        if any((left, top, right, bottom)):
            inner = (max(8, width - left - right), max(8, height - top - bottom))

        # 多屏巡游：桌宠由全局 RoamingPet 决定此刻在谁身上、画在哪
        pet_draw = None
        hide_pet = False
        if self.single_pet:
            hide_pet = True                          # 单宠模式：本屏的桌宠只能由 roamer 给
        if self.roamer is not None and self.roamer.pet is not None:
            pet_draw = self.roamer.draw_on(self.plugin_id or "", inner[0], inner[1],
                                           step or 0)
            hide_pet = pet_draw is None
            if pet_draw is not None:                 # 巡游时就别让 Composition 再画一只
                comp.pet = self.roamer.pet

        if not any((left, top, right, bottom)):
            return comp.render((width, height), step=step, pet_draw=pet_draw,
                               hide_pet=hide_pet)
        from PIL import Image
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        canvas.paste(comp.render(inner, step=step, pet_draw=pet_draw, hide_pet=hide_pet),
                     (left, top))
        return canvas

    # ── 三层合成：底图 → 主题蒙版与数据 → 桌宠 ──────────────────────────
    def composition(self, width: int, height: int) -> Composition:
        """尺寸变了（换屏/换驱动）就重建；平时复用同一个对象，省掉重复读图。"""
        pet_name = None if self.stage is not None else self.pet_name
        key = (width, height, self._theme_id, pet_name, self._background_path)
        if self._comp is not None and self._comp_key == key:
            return self._comp
        theme = find_theme(self._theme_id, theme_dirs(self.cfg)) if self._theme_id else None
        if theme is None and self._theme_id:
            log.warning("主题 %s 找不到，先用测试卡", self._theme_id)
        media = None
        if self._background_path:
            try:
                media = MediaSource.open(self._background_path, (width, height))
            except Exception as exc:
                log.warning("底图 %s 打不开: %s", self._background_path, exc)
        self._comp = Composition(theme=theme, pet=self._pet_obj(), background=media,
                                font_dir=Path(self.cfg.fonts_dir))
        self._comp_key = key
        return self._comp

    @property
    def theme_id(self) -> str:
        return self._theme_id

    def set_theme(self, theme_id: str | None) -> str:
        self._theme_id = theme_id or ""
        self.cfg.theme = self._theme_id
        self.status.extra["theme"] = self._theme_id
        self._comp = None
        return self._theme_id

    def set_background(self, path: str | None) -> str:
        self._background_path = path or ""
        self.cfg.background = self._background_path
        self._comp = None
        return self._background_path

    roamer: Any = None                     # RoamingPet：多屏共用一只桌宠
    single_pet: bool = False               # True = 屏上只允许这一只（不再每屏各画一只）

    def _pet_obj(self) -> Pet | None:
        if self._pet is None and self.pet_name:
            # 配置里指定的桌宠可能在用户目录（不进仓库），别的机器上就没有：
            # 依次退回仓库自带的两个，最后才放弃，别让屏上干脆没有桌宠。
            candidates = [self.pet_name, "whale-girl-hd", "whale-girl"]
            for name in candidates:
                try:
                    found = find_pet(name, pet_dirs(self.cfg)) or \
                        Pet.load(Path(self.cfg.pets_dir) / name)
                except Exception as exc:                   # noqa: BLE001 - 逐个候选都要试
                    if name == candidates[-1]:
                        log.warning("桌宠加载失败（试过 %s）: %s", "、".join(candidates), exc)
                    continue
                if found is not None:
                    if name != self.pet_name:
                        log.info("桌宠 %s 不可用，退用 %s", self.pet_name, name)
                    self._pet = found
                    break
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
