# SPDX-License-Identifier: GPL-3.0-or-later
"""一只桌宠，在多块屏之间自己溜达。

和「每块屏各贴一只」的区别：全局只有一个 RoamingPet 实例，同一时刻只出现在一块屏上。
它在一块屏上往右走，走出右边缘就从下一块屏的左边缘走进来；到边以后会自动掉头，
停留一会儿再换屏，所以看起来是「这只宠物在我的机子上活动」，而不是复制了两只。

谁在用：`tray` 启动时建一个 RoamingPet，交给每个 Player；Player 每帧问它
`draw_on(device_id, w, h, step)`，返回要贴的图和坐标，不属于自己的屏就返回 None。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from PIL import Image

from lcdmuse.pet import Pet


@dataclass
class RoamingPet:
    pet: Pet
    order: list[str] = field(default_factory=list)     # 屏幕顺序（插件 id）
    index: int = 0                                     # 当前在哪块屏
    x: float = 0.04                                    # 归一化水平位置（0~1）
    direction: int = 1                                 # +1 向右、-1 向左
    speed: float = 0.10                                # 每秒移动画布宽度的比例
    dwell: float = 25.0                                # 在一块屏上待多久后考虑换屏
    margin: int = 14

    _last: float = field(default=0.0, repr=False)
    _since_hop: float = field(default=0.0, repr=False)
    _pause_until: float = field(default=0.0, repr=False)

    # ── 内部时钟 ────────────────────────────────────────────────────────
    def _tick(self, now: float) -> None:
        if not self.order:
            return
        if self._last <= 0:
            self._last = now
            return
        dt = min(0.5, max(0.0, now - self._last))
        self._last = now
        self._since_hop += dt

        if now < self._pause_until:                    # 站住发呆
            return

        self.x += self.speed * dt * self.direction
        if self.x >= 1.0:                              # 走出右边 → 进下一块屏
            self.x = 0.0
            self._hop()
        elif self.x <= 0.0:                            # 走出左边 → 退到上一块屏
            self.x = 1.0
            self._hop(-1)
        elif self._since_hop > self.dwell and random.random() < dt / self.dwell:
            self._hop()                                # 待久了随机换屏

    def _hop(self, step: int = 1) -> None:
        if len(self.order) > 1:
            self.index = (self.index + step) % len(self.order)
        self._since_hop = 0.0
        self._pause_until = self._last + random.uniform(0.8, 2.5)   # 到新屏先站一下

    # ── 给 Player 用 ────────────────────────────────────────────────────
    @property
    def current(self) -> str | None:
        return self.order[self.index] if self.order else None

    def draw_on(self, device_id: str, canvas_w: int, canvas_h: int,
                step: int, now: float | None = None,
                fit_height: float | None = None) -> tuple[Image.Image, int, int] | None:
        """这块屏要不要画桌宠；要的话返回 (图, x, y)。"""
        if not self.order or device_id != self.current:
            return None
        now = now if now is not None else time.time()
        self._tick(now)

        moving = now >= self._pause_until
        state = "walk" if moving and "walk" in self.pet.states else (
            "idle" if "idle" in self.pet.states else next(iter(self.pet.states)))
        import contextlib
        with contextlib.suppress(Exception):          # 状态名不存在就维持原状
            self.pet.set_state(state, now=now)

        sprite = self.pet.frame_at(now, step=step)
        ratio = fit_height if fit_height is not None else (self.pet.fit_height or 0.7)
        target_h = max(8, int(canvas_h * float(ratio)))
        target_w = max(8, int(sprite.width * target_h / max(1, sprite.height)))
        if target_w > int(canvas_w * 0.55):
            target_w = int(canvas_w * 0.55)
            target_h = max(8, int(sprite.height * target_w / max(1, sprite.width)))
        if (sprite.width, sprite.height) != (target_w, target_h):
            sprite = sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)

        x = int(self.x * max(1, canvas_w - sprite.width))
        x = max(0, min(canvas_w - sprite.width, x))
        y = max(0, canvas_h - sprite.height - self.margin)
        return sprite, x, y
