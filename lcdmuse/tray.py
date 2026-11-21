# SPDX-License-Identifier: GPL-3.0-or-later
"""托盘图标：把 Player 挂在系统托盘上，右键菜单能暂停、开关桌宠、退出。

只依赖 pystray + Pillow（都是纯 Python 能跑的），不引入 GUI 框架。
单实例用 Windows 命名信号量 / 其它平台锁文件挡住重复启动。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from lcdmuse.config import APP_NAME, Config, log
from lcdmuse.player import Player
import contextlib

_ACCENT = (90, 210, 255)
_DARK = (12, 19, 32)


def make_icon(size: int = 64, *, active: bool = True, error: bool = False) -> Image.Image:
    """画一个"副屏"图标：圆角屏 + 中间的取景框；出错时变成红点。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(2, size // 12)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=size // 8,
                        fill=_DARK, outline=_ACCENT if active else (90, 100, 115), width=max(2, size // 24))
    d.rectangle([size // 3, size // 3, size - size // 3, size - size // 3],
                outline=_ACCENT if active else (90, 100, 115), width=max(1, size // 32))
    dot = (235, 80, 90) if error else ((110, 240, 170) if active else (150, 160, 175))
    r = max(3, size // 10)
    cx, cy = size - pad - r + 1, size - pad - r + 1
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot)
    return img


class SingleInstance:
    """同名实例已经在跑就直接退出，避免两个托盘抢同一块设备。"""

    def __init__(self, name: str = f"{APP_NAME}-tray"):
        self.name = name
        self._handle = None
        self._lockfile: Path | None = None

    def acquire(self) -> bool:
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            self._handle = kernel32.CreateMutexW(None, False, self.name)
            return kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS
        import tempfile
        path = Path(tempfile.gettempdir()) / f"{self.name}.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        self._lockfile = path
        return True

    def release(self) -> None:
        if self._handle and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._lockfile and self._lockfile.exists():
            with contextlib.suppress(OSError):
                self._lockfile.unlink()


def _open_path(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))                        # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        log.warning("打开 %s 失败: %s", path, exc)


def run_tray(cfg: Config, plugin_id: str = "", interval: float = 1.0,
             pet: str | None = "whale-girl") -> int:
    try:
        import pystray
    except Exception as exc:                               # 缺依赖时给一句人话
        print(f"托盘需要 pystray：pip install pystray（{exc}）", file=sys.stderr)
        return 2

    guard = SingleInstance()
    if not guard.acquire():
        print(f"{APP_NAME} 托盘已经在运行了（右下角找一下）", file=sys.stderr)
        return 0

    players = _build_players(cfg, plugin_id, interval, pet)
    for p in players:
        p.start()

    icon = pystray.Icon(f"{APP_NAME}", make_icon(), f"{APP_NAME} · 副屏")

    def refresh() -> None:
        running = any(p.status.running for p in players)
        if len(players) == 1:
            icon.title = f"{APP_NAME} · {players[0].status.summary()}"
        else:
            icon.title = f"{APP_NAME} · {len(players)} 块屏 · " + " / ".join(
                f"{p.status.device or p.status.plugin} {p.status.frames} 帧" for p in players)
        icon.icon = make_icon(active=running and not players[0].status.paused,
                              error=any(p.status.last_error for p in players))
        with contextlib.suppress(Exception):
            icon.update_menu()

    def on_toggle_pause(icon_: pystray.Icon, item) -> None:
        target = not players[0].status.paused
        for p in players:
            p.status.paused = target
        refresh()

    def on_toggle_pet(icon_: pystray.Icon, item) -> None:
        enable = players[0].pet_name is None
        for p in players:
            p.set_pet((cfg.default_pet or "whale-girl") if enable else None)
        refresh()

    def on_plugins(icon_: pystray.Icon, item) -> None:
        _open_path(cfg.ensure_user_drivers())

    def on_logs(icon_: pystray.Icon, item) -> None:
        _open_path(Path(cfg.out_dir).parent / "logs")

    def on_quit(icon_: pystray.Icon, item) -> None:
        for p in players:
            p.stop()
        icon_.stop()

    def _status_lines() -> list[tuple[str, str]]:
        return [(f"{p.status.device or p.status.plugin}：{p.status.summary()}", "") for p in players]

    def _status_item(item) -> str:
        return players[0].status.summary() if len(players) == 1 else f"{len(players)} 块屏在推"

    menu = pystray.Menu(
        pystray.MenuItem(_status_item, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("暂停 / 继续", on_toggle_pause,
                         checked=lambda item: players[0].status.paused),
        pystray.MenuItem("桌宠", on_toggle_pet,
                         checked=lambda item: players[0].pet_name is not None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开插件目录", on_plugins),
        pystray.MenuItem("打开日志", on_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )
    icon.menu = menu

    import threading
    stop_tick = threading.Event()

    def _ticker() -> None:
        while not stop_tick.wait(1.0):
            refresh()

    threading.Thread(target=_ticker, daemon=True).start()
    try:
        icon.run()
    finally:
        stop_tick.set()
        for p in players:
            p.stop()
        guard.release()
    return 0


def _build_players(cfg: Config, plugin_id: str, interval: float, pet: str | None) -> list[Player]:
    """`all` 表示把所有探测到的设备都推起来；否则只推指定的那一个。"""
    if plugin_id != "all":
        return [Player(cfg, plugin_id, interval=interval, pet=pet)]
    probe = Player(cfg, "", interval=interval, pet=pet)
    # "all" 的语义是"所有真屏幕"：把用户装的插件都临时启用，并排除 simulated
    options = {pid: dict(opts) for pid, opts in cfg.drivers.items()}
    for plugin in probe.registry.plugins:
        options.setdefault(plugin.id, {})["enabled"] = True
    try:
        found = [(p, info) for p, info in probe.registry.probe(options) if p.id != "simulated"]
    except Exception as exc:
        log.warning("探测失败: %s", exc)
        found = []
    if not found:
        return [Player(cfg, "", interval=interval, pet=pet)]
    return [Player(cfg, plugin.id, interval=interval, pet=pet) for plugin, _info in found]
