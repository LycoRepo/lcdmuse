# SPDX-License-Identifier: GPL-3.0-or-later
"""控制窗口：顶栏 / 左栏 / 主区 / 右侧预览 / 底栏，五块对齐 mockup。

Tk 只能在自己线程里创建，所以窗口自带线程；托盘那边用队列发 show/hide/quit。
预览是驱动真实推出去的那一帧（Player.last_image），不是另画一张。
"""
from __future__ import annotations

import contextlib
import os
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lcdmuse import __version__
from lcdmuse.config import APP_NAME, Config, log

if TYPE_CHECKING:                                     # pragma: no cover
    from lcdmuse.player import Player

BG = "#070b11"
PANEL = "#0e1620"
PANEL2 = "#121c28"
BORDER = "#1c2836"
FG = "#e6edf3"
MUTED = "#8b98a9"
ACCENT = "#5ad2ff"
OK = "#38d39f"
WARN = "#d99a3a"
BAD = "#f05a5a"

UI_FONT = ("Microsoft YaHei UI", 9)
UI_FONT_S = ("Microsoft YaHei UI", 8)
MONO = ("Consolas", 9)


class ControlWindow(threading.Thread):
    def __init__(self, players: list[Player], cfg: Config, on_quit=None):
        super().__init__(daemon=True, name="lcdmuse-window")
        self.players = players
        self.cfg = cfg
        self.on_quit = on_quit
        self._q: queue.Queue[str] = queue.Queue()
        self._root: Any = None
        self._photo = None
        self._selected = 0
        self._last_frame_seen = -1
        self._preview_label: Any = None
        self._nav_items: list[tuple[Any, Any]] = []
        self._device_labels: list[dict[str, Any]] = []
        self._status_line: Any = None
        self._pills: list[Any] = []

    # ── 托盘线程调用 ────────────────────────────────────────────────────
    def show(self) -> None:
        self._q.put("show")

    def hide(self) -> None:
        self._q.put("hide")

    def close(self) -> None:
        self._q.put("quit")

    # ── 字体/组件小工具 ─────────────────────────────────────────────────
    @staticmethod
    def _panel(parent, **kw) -> Any:
        import tkinter as tk
        return tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER, **kw)

    @staticmethod
    def _label(parent, text="", fg=FG, font=UI_FONT, bg=PANEL, **kw) -> Any:
        import tkinter as tk
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=font, anchor="w", **kw)

    # ── Tk 线程 ─────────────────────────────────────────────────────────
    def run(self) -> None:                            # pragma: no cover - GUI
        try:
            import tkinter as tk
        except Exception as exc:
            log.warning("没有 tkinter/Pillow，控制窗口不可用: %s", exc)
            return

        if os.name == "nt":
            with contextlib.suppress(Exception):
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)

        root = tk.Tk()
        self._root = root
        root.title(f"{APP_NAME} · 副屏控制")
        root.configure(bg=BG)
        root.geometry("1240x700")
        root.minsize(860, 560)
        root.protocol("WM_DELETE_WINDOW", self._hide)

        # ── 顶栏 ──
        head = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        head.pack(fill="x", padx=10, pady=(10, 0), ipady=6)
        tk.Frame(head, bg=ACCENT, width=14, height=14).pack(side="left", padx=(12, 8))
        tk.Label(head, text=APP_NAME, bg=PANEL, fg=FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self._label(head, "中文", fg=MUTED, font=UI_FONT_S).pack(side="left", padx=8)
        self._label(head, f"v{__version__}", fg=MUTED, font=UI_FONT_S).pack(side="right", padx=12)
        for _ in self.players:
            pill = self._label(head, "", fg=MUTED, font=UI_FONT_S)
            pill.pack(side="right", padx=8)
            self._pills.append(pill)

        # ── 底栏（先 pack，保证始终可见）──
        foot = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        foot.pack(fill="x", padx=10, pady=(8, 10), ipady=4)
        self._status_line = self._label(foot, "…", fg=MUTED, font=UI_FONT_S)
        self._status_line.pack(side="left", padx=12)
        self._label(foot, "MCP 服务：未开工", fg=MUTED, font=UI_FONT_S).pack(side="right", padx=12)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=8)

        # ── 左栏 ──
        nav = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, width=200)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        self._label(nav, "设备", fg=MUTED, font=("Microsoft YaHei UI", 7)).pack(anchor="w", padx=12, pady=(10, 2))
        for index, player in enumerate(self.players):
            row = tk.Frame(nav, bg=PANEL)
            row.pack(fill="x", padx=6, pady=1)
            icon = self._label(row, "▤" if index == 0 else "◯", fg=ACCENT if index == 0 else MUTED, font=UI_FONT)
            icon.pack(side="left", padx=(4, 6))
            text = self._label(row, self._alias(player)[:16], fg=FG, font=UI_FONT)
            text.pack(side="left")
            for widget in (row, icon, text):
                widget.bind("<Button-1>", lambda _e, i=index: self._select(i))
                widget.bind("<Button-3>", lambda _e, p=player: self._rename(p))
            self._nav_items.append((row, text))

        self._label(nav, "显示", fg=MUTED, font=("Microsoft YaHei UI", 7)).pack(anchor="w", padx=12, pady=(12, 2))
        self._nav_button(nav, "☺  桌宠", self._toggle_pet)
        self._nav_button(nav, "▦  主题（未接）", None)
        self._nav_button(nav, "✎  图片 / 推帧", None)
        self._label(nav, "系统", fg=MUTED, font=("Microsoft YaHei UI", 7)).pack(anchor="w", padx=12, pady=(12, 2))
        self._nav_button(nav, "◈  插件目录", lambda: self._open(self.cfg.ensure_user_drivers()))
        self._nav_button(nav, "⚙  日志", lambda: self._open(Path(self.cfg.out_dir).parent / "logs"))
        self._nav_button(nav, "⏻  退出", self._quit)

        # ── 右侧预览 ──
        side = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, width=344)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        self._label(side, "屏幕预览", fg=MUTED, font=("Microsoft YaHei UI", 7)).pack(anchor="w", padx=12, pady=(10, 4))
        self._preview_label = tk.Label(side, bg="#060a10", fg=MUTED, text="等待首帧…",
                                       font=UI_FONT_S, bd=0, padx=1, pady=1)
        self._preview_label.pack(padx=10, fill="x")
        self._preview_info = self._label(side, "", fg=MUTED, font=MONO, justify="left", wraplength=310)
        self._preview_info.pack(anchor="w", padx=12, pady=(8, 0), fill="x")

        # ── 主区 ──
        main = tk.Frame(body, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=8)
        self._main_title = self._label(main, "当前设备", fg=FG, bg=BG, justify="left", wraplength=640,
                                       font=("Microsoft YaHei UI", 10, "bold"))
        self._main_title.pack(anchor="w", pady=(2, 6))
        cards = tk.Frame(main, bg=BG)
        cards.pack(fill="x")
        for player in self.players:
            card = self._panel(cards)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=2)
            name = self._label(card, self._alias(player)[:22], fg=FG,
                               font=("Microsoft YaHei UI", 9, "bold"))
            name.pack(anchor="w", padx=12, pady=(10, 0))
            meta = self._label(card, "", fg=MUTED, font=MONO)
            meta.pack(anchor="w", padx=12, pady=(2, 0))
            state = self._label(card, "…", fg=OK, font=MONO)
            state.pack(anchor="w", padx=12, pady=(6, 10))
            self._device_labels.append({"name": name, "meta": meta, "state": state, "player": player})

        info = self._panel(main)
        info.pack(fill="both", expand=True, pady=(10, 0))
        self._label(info, "运行信息", fg=MUTED, font=("Microsoft YaHei UI", 7)).pack(
            anchor="w", padx=12, pady=(10, 4))
        self._runinfo = self._label(info, "", fg=FG, font=MONO, justify="left", wraplength=560)
        self._runinfo.pack(anchor="w", padx=12, pady=(0, 10), fill="x")

        row = tk.Frame(main, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        for text, cmd in (("暂停 / 继续", self._toggle_pause), ("桌宠", self._toggle_pet),
                          ("插件目录", lambda: self._open(self.cfg.ensure_user_drivers()))):
            tk.Button(row, text=text, command=cmd, bg=PANEL2, fg=FG, activebackground=BORDER,
                      activeforeground=ACCENT, relief="flat", padx=12, pady=5,
                      font=UI_FONT).pack(side="left", padx=(0, 6))


        try:
            from lcdmuse.registry import Registry
            reg = Registry([str(p) for p in self.cfg.drivers_dirs])
            self._plugin_stats = (len(reg.available()), sum(1 for p in reg.plugins if p.enabled_by_default))
        except Exception:
            self._plugin_stats = (0, 0)

        root.withdraw()
        self._tick()
        root.mainloop()

    def _nav_button(self, parent, text: str, cmd) -> None:   # pragma: no cover - GUI
        import tkinter as tk
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=6, pady=1)
        label = tk.Label(row, text=text, bg=PANEL, fg=FG, font=UI_FONT, anchor="w", padx=6, pady=4)
        label.pack(fill="x")
        if cmd is not None:
            label.bind("<Button-1>", lambda _e: cmd())
            label.bind("<Enter>", lambda _e: label.configure(bg=PANEL2))
            label.bind("<Leave>", lambda _e: label.configure(bg=PANEL))

    # ── 轮询 ────────────────────────────────────────────────────────────
    def _tick(self) -> None:                           # pragma: no cover - GUI
        self._drain()
        self._refresh()
        self._root.after(500, self._tick)

    def _drain(self) -> None:                          # pragma: no cover - GUI
        while True:
            try:
                cmd = self._q.get_nowait()
            except queue.Empty:
                return
            if cmd == "show":
                self._root.deiconify()
                self._root.lift()
                self._root.attributes("-topmost", True)
                self._root.after(400, lambda: self._root.attributes("-topmost", False))
                self._root.focus_force()
            elif cmd == "hide":
                self._root.withdraw()
            elif cmd == "quit":
                self._quit()
                return

    def _refresh(self) -> None:                        # pragma: no cover - GUI
        from PIL import ImageTk

        for pill, player in zip(self._pills, self.players, strict=False):
            st = player.status
            dot = "●" if st.running and not st.paused else ("⏸" if st.paused else "○")
            color = OK if st.running and not st.paused else (WARN if st.paused else MUTED)
            if st.last_error:
                color = BAD
            name = self._alias(player)
            pill.configure(text=f"{dot} {name[:12]} {st.size[0]}×{st.size[1]}", fg=color)

        for entry in self._device_labels:
            st = entry["player"].status
            state_color = BAD if st.last_error else (WARN if st.paused else OK)
            entry["meta"].configure(text=f"{st.size[0]}×{st.size[1]}  {st.plugin}")
            if st.last_error:
                entry["state"].configure(text=f"⚠ {st.last_error[:26]}", fg=state_color)
            else:
                entry["state"].configure(text=f"{'⏸' if st.paused else '●'} {st.frames} 帧 · "
                                               f"{st.fps:.1f} fps", fg=state_color)

        player = self.players[self._selected] if self.players else None
        if player is not None:
            st = player.status
            self._main_title.configure(
                text=f"当前设备   {self._alias(player)}    {st.size[0]}×{st.size[1]}    {st.plugin or ''}")
            self._status_line.configure(
                text=f"{len(self.players)} 台设备 · 已推 {sum(p.status.frames for p in self.players)} 帧 · "
                     f"{st.fps:.1f} fps · 托盘图标左键可重开窗口")
            plugins = getattr(self, "_plugin_stats", (0, 0))
            self._runinfo.configure(
                text=f"插件：{plugins[0]} 个可用，已启用 {plugins[1]}   设备：{len(self.players)} 台\n"
                     f"配置：{self.cfg.__class__.__name__}  ·  用户插件目录：{self.cfg.user_drivers_dir}\n"
                     f"托盘：图标左键开会本窗口，右键看菜单")
            self._preview_info.configure(
                text=f"驱动：{st.plugin or '—'}\n设备：{self._alias(player)}\n"
                     f"帧：{st.frames}   帧率：{st.fps:.1f} fps\n"
                     f"状态：{'暂停' if st.paused else ('运行' if st.running else '未连')}")

            image = getattr(player, "last_image", None)
            if image is not None and st.frames != self._last_frame_seen:
                self._last_frame_seen = st.frames
                box_w, box_h = 300, 146          # 预览框固定，图按比例塞进去，别把横屏拉成竖条
                scale = min(box_w / max(1, image.width), box_h / max(1, image.height))
                size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                self._photo = ImageTk.PhotoImage(image.resize(size))
                self._preview_label.configure(image=self._photo, text="",
                                              width=box_w, height=box_h)

        for index, (row, text) in enumerate(self._nav_items):
            active = index == self._selected
            row.configure(bg=PANEL2 if active else PANEL)
            text.configure(bg=PANEL2 if active else PANEL, fg=ACCENT if active else FG)

    # ── 动作 ────────────────────────────────────────────────────────────
    def _alias(self, player) -> str:
        """左栏/卡片显示名：用户自定义 > 设备自报 > 插件 id。右键可改。"""
        pid = player.plugin_id or player.status.plugin
        return self.cfg.name_for(pid, player.status.device or pid or "设备")

    def _rename(self, player) -> None:                 # pragma: no cover - GUI
        from tkinter import simpledialog
        pid = player.plugin_id or player.status.plugin
        if not pid:
            return
        current = self._alias(player)
        new = simpledialog.askstring(f"{APP_NAME} · 设备名", f"{pid} 的显示名：",
                                     initialvalue=current, parent=self._root)
        if new is None:
            return
        self.cfg.set_device_name(pid, new)
        log.info("设备 %s 改名：%s", pid, self.cfg.name_for(pid, current))
        self._refresh()

    def _hide(self) -> None:                           # pragma: no cover - GUI
        with contextlib.suppress(Exception):
            self._root.withdraw()

    def _select(self, index: int) -> None:             # pragma: no cover - GUI
        self._selected = min(index, max(0, len(self.players) - 1))
        self._last_frame_seen = -1

    def _toggle_pause(self) -> None:                   # pragma: no cover - GUI
        target = not self.players[0].status.paused
        for p in self.players:
            p.status.paused = target

    def _toggle_pet(self) -> None:                     # pragma: no cover - GUI
        enable = self.players[0].pet_name is None
        for p in self.players:
            p.set_pet((self.cfg.default_pet or "whale-girl") if enable else None)

    def _open(self, path) -> None:                     # pragma: no cover - GUI
        from lcdmuse.tray import _open_path
        _open_path(Path(path))

    def _quit(self) -> None:                           # pragma: no cover - GUI
        with contextlib.suppress(Exception):
            self._root.destroy()
        if self.on_quit:
            self.on_quit()
