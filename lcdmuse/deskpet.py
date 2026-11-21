# SPDX-License-Identifier: GPL-3.0-or-later
"""桌面桌宠：Windows 桌面上的一只透明小窗，不是画在副屏上的贴图。

和「副屏上的桌宠」是两件事：
- 副屏桌宠（core 里的 Composition）只是把角色画进主题画面，屏上看到的是静态帧；
- 桌面桌宠（本模块）是一个无边框、透明、置顶、可拖拽的窗口，会自己走动、
  被戳会吓一跳、右键能换角色/调大小/开关置顶。

用法：
    lcdmuse pet                # 起一只，默认 whale-girl
    lcdmuse pet --pet 名字 --scale 1.5
"""
from __future__ import annotations

import contextlib
import random
import sys
import time
from pathlib import Path
from typing import Any

TRANSPARENT_KEY = "#010203"          # 抠色用的键色，正常画面里不会出现


class DesktopPet:
    """一只会动、能拖、有状态机的桌面桌宠。"""

    def __init__(self, pet: Any, scale: float = 1.0, topmost: bool = True):
        import tkinter as tk

        self.tk = tk
        self.pet = pet
        self.scale = max(0.3, min(3.0, float(scale)))
        self.root = tk.Tk()
        self.root.title(pet.display_name or pet.name)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(topmost))
        with contextlib.suppress(Exception):
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.configure(bg=TRANSPARENT_KEY)

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg=TRANSPARENT_KEY)
        self.canvas.pack()
        self.image_id = self.canvas.create_image(0, 0, anchor="nw")

        self._photo = None
        self._cache: dict[Any, Any] = {}
        self._state = "idle"
        self._frame_index = 0
        self._last_tick = time.time()
        self._drag: tuple[int, int] | None = None
        self._moved = False
        self._walk_dir = 1
        self._next_decision = time.time() + random.uniform(2, 5)
        self._bubble = None

        self._bind()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.x = int(sw * 0.62)
        self.y = int(sh * 0.72)
        self._apply_geometry()
        self._render_frame()
        self._loop()

    # ── 形象 ────────────────────────────────────────────────────────────
    def _frames(self, state: str):
        return self.pet.states.get(state) or self.pet.states.get("idle")

    def _sprite(self, state: str, index: int):
        from PIL import Image, ImageTk

        st = self._frames(state)
        if not st:
            raise SystemExit(f"{self.pet.name} 没有任何帧")
        path = st.frames[index % len(st.frames)]
        key = (path, self.scale)
        photo = self._cache.get(key)
        if photo is None:
            with Image.open(path) as im:
                sprite = im.convert("RGBA")
            if self.scale != 1.0:
                size = (max(1, int(sprite.width * self.scale)), max(1, int(sprite.height * self.scale)))
                sprite = sprite.resize(size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(sprite)
            self._cache[key] = photo
        return photo

    def _render_frame(self) -> None:
        photo = self._sprite(self._state, self._frame_index)
        self.canvas.itemconfigure(self.image_id, image=photo)
        self.canvas.configure(width=photo.width(), height=photo.height())
        self._photo = photo                    # 保住引用，否则图会被回收
        self.root.geometry(f"{photo.width()}x{photo.height()}+{self.x}+{self.y}")

    def _apply_geometry(self) -> None:
        st = self._frames(self._state)
        if not st:
            return
        with contextlib.suppress(Exception):
            from PIL import Image
            with Image.open(st.frames[0]) as im:
                w = int(im.width * self.scale)
                h = int(im.height * self.scale)
            self.root.geometry(f"{w}x{h}+{self.x}+{self.y}")

    # ── 交互 ────────────────────────────────────────────────────────────
    def _bind(self) -> None:
        for w in (self.root, self.canvas):
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Button-3>", self._on_menu)
            w.bind("<Double-Button-1>", lambda _e: self.say("我饿"))

    def _on_press(self, event) -> None:
        self._drag = (event.x_root - self.x, event.y_root - self.y)
        self._moved = False

    def _on_drag(self, event) -> None:
        if not self._drag:
            return
        dx, dy = self._drag
        self.x, self.y = event.x_root - dx, event.y_root - dy
        self._moved = True
        self.root.geometry(f"+{self.x}+{self.y}")
        self._bubble_follow()

    def _on_release(self, _event) -> None:
        self._drag = None
        if not self._moved:                    # 没拖动就是戳了一下
            self.set_state("click")
            time.sleep(0)

    def _on_menu(self, event) -> None:
        tk = self.tk
        menu = tk.Menu(self.root, tearoff=0)
        for label, state in (("待机", "idle"), ("干饭", "eat"), ("睡觉", "sleep"), ("说话", "say")):
            menu.add_command(label=label, command=lambda s=state: self.set_state(s))
        menu.add_separator()
        menu.add_command(label="让它说话", command=lambda: self.say("我饿"))
        size_menu = tk.Menu(menu, tearoff=0)
        for label, factor in (("小", 0.6), ("中", 1.0), ("大", 1.5)):
            size_menu.add_command(label=label, command=lambda f=factor: self.set_scale(f))
        menu.add_cascade(label="大小", menu=size_menu)
        menu.add_command(label="退出桌宠", command=self.quit)
        with contextlib.suppress(Exception):
            menu.tk_popup(event.x_root, event.y_root)

    # ── 状态 / 动画 ─────────────────────────────────────────────────────
    def set_state(self, state: str) -> None:
        if state not in self.pet.states:
            state = "idle" if "idle" in self.pet.states else next(iter(self.pet.states))
        self._state = state
        self._frame_index = 0
        self._last_tick = time.time()

    def set_scale(self, factor: float) -> None:
        self.scale = max(0.3, min(3.0, float(factor)))
        self._cache.clear()
        self._render_frame()

    def say(self, text: str) -> None:
        """头顶冒一句话，3 秒后自己消失。"""
        tk = self.tk
        if self._bubble is not None:
            with contextlib.suppress(Exception):
                self._bubble.destroy()
        bubble = tk.Toplevel(self.root)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        label = tk.Label(bubble, text=text, bg="#0d1620", fg="#dff1f8",
                         font=("Microsoft YaHei UI", 10), padx=10, pady=5,
                         relief="flat", bd=1)
        label.pack()
        self._bubble = bubble
        self._bubble_follow()
        bubble.after(3000, lambda: self._close_bubble())

    def _bubble_follow(self) -> None:
        if self._bubble is None:
            return
        with contextlib.suppress(Exception):
            w = self._photo.width() if self._photo else 100
            self._bubble.geometry(f"+{self.x + w // 2 - 30}+{max(0, self.y - 34)}")

    def _close_bubble(self) -> None:
        if self._bubble is not None:
            with contextlib.suppress(Exception):
                self._bubble.destroy()
        self._bubble = None

    def _loop(self) -> None:
        st = self._frames(self._state)
        delay = max(40, int(1000 / max(1.0, st.fps if st else 4.0)))
        now = time.time()
        if now - self._last_tick >= delay / 1000:
            self._last_tick = now
            self._frame_index += 1
            if self._state == "click" and self._frame_index >= len(st.frames):
                self.set_state("idle")         # 被戳只播一轮
            elif self._state == "say" and self._frame_index >= len(st.frames) * 2:
                self.set_state("idle")
            self._render_frame()

        self._wander(now)
        self.root.after(delay, self._loop)

    def _wander(self, now: float) -> None:
        """自己溜达：大部分时间站着，偶尔左右走走，走到屏幕边就掉头。"""
        if self._drag is not None or self._state not in ("idle", "walk"):
            return
        sw = self.root.winfo_screenwidth()
        if now >= self._next_decision:
            self._next_decision = now + random.uniform(3, 8)
            self.set_state("walk" if self._state == "idle" and random.random() < 0.55 else "idle")
        if self._state == "walk":
            self.x += 2 * self._walk_dir
            if self.x < 8 or self.x > sw - (self._photo.width() if self._photo else 80) - 8:
                self._walk_dir *= -1
            self.root.geometry(f"+{self.x}+{self.y}")
            self._bubble_follow()

    def quit(self) -> None:
        with contextlib.suppress(Exception):
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_desktop_pet(pet_name: str = "whale-girl", scale: float = 1.0,
                    pet_dirs: list[Path] | None = None) -> int:
    from lcdmuse.pet import Pet, pet_dirs as default_dirs

    dirs = pet_dirs or default_dirs(None)
    root = None
    for d in dirs:
        candidate = Path(d) / pet_name
        if (candidate / "pet.json").exists():
            root = candidate
            break
    if root is None:
        print(f"没找到桌宠 {pet_name}（找过：{', '.join(str(d) for d in dirs)}）", file=sys.stderr)
        return 1

    pet = Pet.load(root)
    if not pet.states.get("idle"):
        print(f"桌宠 {pet_name} 没有 idle 帧", file=sys.stderr)
        return 1
    DesktopPet(pet, scale=scale).run()
    return 0
