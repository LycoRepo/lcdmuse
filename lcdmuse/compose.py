# SPDX-License-Identifier: GPL-3.0-or-later
"""把三层拼成一张图：底图（可换） → 主题蒙版与数据 → 桌宠。

顺序是有意的：
1. 底图：一张图 / GIF / 视频，用户随时换；
2. 主题：半透明蒙版 + 文字与进度条，只绑指标，不关心底图长什么样；
3. 桌宠：最后贴，永远在最上面（它是角色，不该被蒙版压暗）。

三样都可以缺：没主题就用测试卡，没桌宠就只有画面。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from PIL import Image

from lcdmuse.config import log
from lcdmuse.media import MediaSource
from lcdmuse.metrics import get_hub
from lcdmuse.pet import Pet
from lcdmuse.render.demo import render_demo
from lcdmuse.theme import Theme


def _micro_motion(sprite: Image.Image, state: str, now: float):
    """给 8 帧的姿态之间补上连续的小动作。

    素材只有 8 张图（源 GIF 就 8 帧），光靠切帧看着像 PPT。这里在每帧上叠一点
    正弦位移/缩放：待机呼吸、走路上下颠、吃饭轻点头。屏刷新多少帧，动作就变多少次，
    所以 6 fps 下也是连续的，而不是每 8 帧才跳一下。
    """
    import math

    state = (state or "idle").lower()
    if state == "sleep":
        wave = math.sin(now * 2 * math.pi * 0.25)
        return sprite, 0, int(round(wave * 1.0))
    if state == "walk":
        wave = math.sin(now * 2 * math.pi * 2.6)
        return sprite, 0, -int(round(abs(wave) * 3.0))
    if state == "eat":
        wave = math.sin(now * 2 * math.pi * 1.8)
        return sprite, 0, int(round(wave * 1.5))
    if state in ("click", "say"):
        wave = math.sin(now * 2 * math.pi * 6.0)
        return sprite, int(round(wave * 2.0)), int(round(abs(wave) * -2.0))
    wave = math.sin(now * 2 * math.pi * 0.55)          # idle：呼吸（只做整像素位移）
    # 不要每帧 resize 做“压扁呼吸”：LANCZOS 每帧重采样会让边缘像素来回变，看着就是闪。
    return sprite, 0, int(round(wave * 1.0))


@dataclass
class Composition:
    theme: Theme | None = None
    pet: Pet | None = None
    background: MediaSource | None = None
    font_dir: Path | None = None

    def render(self, size: tuple[int, int], now: float | None = None,
               metrics: Mapping[str, Any] | None = None,
               step: int | None = None) -> Image.Image:
        import time
        now = now if now is not None else time.time()
        values = dict(metrics) if metrics is not None else get_hub().sample(now, ttl=0.5)
        if self.theme is not None:
            try:
                canvas = self.theme.render(size, metrics=values, now=now,
                                           media=self.background, font_dir=self.font_dir)
            except Exception as exc:                    # 主题坏了也别让屏黑掉
                log.warning("主题渲染失败（%s），退回测试卡: %s", self.theme.id, exc)
                canvas = render_demo(size[0], size[1], str(self.font_dir or ""), now=now).image
        else:
            canvas = render_demo(size[0], size[1], str(self.font_dir or ""), now=now).image

        if self.pet is not None:
            try:
                sprite = self.pet.frame_at(now, step=step)
                x, y = self.pet.anchor_xy(canvas.width, canvas.height, sprite)
                if canvas.mode != "RGB":
                    canvas = canvas.convert("RGB")
                if self.pet.micro_motion:
                    sprite, dx, dy = _micro_motion(sprite, self.pet.state, now)
                else:
                    dx = dy = 0
                # 只贴桌宠那一小块：paste 用 sprite 自己的 alpha 当蒙版，
                # 比整幅转 RGBA 再 alpha_composite 省两次全画布拷贝
                canvas.paste(sprite, (max(0, x + dx), max(0, y + dy)), sprite)
            except Exception as exc:
                log.warning("桌宠合成失败（%s）: %s", getattr(self.pet, "name", "?"), exc)
        return canvas

    def describe(self) -> dict[str, Any]:
        return {
            "theme": self.theme.id if self.theme else None,
            "pet": self.pet.name if self.pet else None,
            "background": self.background.describe() if self.background else
            (self.theme.background if self.theme else None),
        }
