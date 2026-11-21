# SPDX-License-Identifier: GPL-3.0-or-later
"""把三层拼成一张图：底图（可换） → 主题蒙版与数据 → 桌宠。

顺序是有意的：
1. 底图：一张图 / GIF / 视频，用户随时换；
2. 主题：半透明蒙版 + 文字与进度条，只绑指标，不关心底图长什么样；
3. 桌宠：最后贴，永远在最上面（它是角色，不该被蒙版压暗）。

三样都可以缺：没主题就用测试卡，没桌宠就只有画面。
"""
from __future__ import annotations

from dataclasses import dataclass, field
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


def _bubble_font(size: int):
    from PIL import ImageFont
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:                             # noqa: BLE001 - 换下一个字体
            continue
    return ImageFont.load_default()


def _draw_bubble(canvas: Image.Image, x: int, y: int, sprite_w: int, text: str) -> None:
    """在角色头顶画一句短话（圆角气泡 + 小尾巴），3 fps 下也看得清。"""
    from PIL import ImageDraw

    size = max(12, int(canvas.height / 34))
    font = _bubble_font(size)
    draw = ImageDraw.Draw(canvas, "RGBA")
    pad_x, pad_y = int(size * 0.6), int(size * 0.35)
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    bx = max(2, min(canvas.width - bw - 2, x + sprite_w // 2 - bw // 2))
    by = max(2, y - bh - int(size * 0.5))
    draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=int(size * 0.5),
                           fill=(12, 22, 34, 232), outline=(120, 210, 255, 200), width=2)
    tail = int(size * 0.35)
    draw.polygon([(bx + bw // 2 - tail, by + bh), (bx + bw // 2 + tail, by + bh),
                  (bx + bw // 2, by + bh + tail)], fill=(12, 22, 34, 232))
    draw.text((bx + pad_x, by + pad_y - box[1]), text, font=font, fill=(226, 244, 255))


def _fit_pet_size(pet, sprite: Image.Image, canvas_w: int, canvas_h: int,
                  cache: dict, key) -> Image.Image:
    """按画布高度比例缩放桌宠：同一只桌宠在 1480×720 和 960×480 上观感一致。

    固定像素倍数（scale）在两种屏上差一倍多，所以推荐 fit_height：
    角色高度 = fit_height × 屏幕高，同时宽度不超过画布的 55%，防止横图顶到数据面板。
    """
    ratio = getattr(pet, "fit_height", None)
    if not ratio:
        return sprite
    target_h = max(8, int(canvas_h * float(ratio)))
    target_w = max(8, int(sprite.width * target_h / max(1, sprite.height)))
    max_w = max(8, int(canvas_w * 0.55))
    if target_w > max_w:
        target_w = max_w
        target_h = max(8, int(sprite.height * target_w / max(1, sprite.width)))
    cache_key = (key, target_w, target_h)
    cached = cache.get(cache_key)
    if cached is None:
        cached = sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)
        if len(cache) > 24:
            cache.clear()
        cache[cache_key] = cached
    return cached


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
    _pet_sizes: dict[Any, Image.Image] = field(default_factory=dict, repr=False)

    def _fit_pet(self, sprite: Image.Image, canvas_w: int, canvas_h: int,
                 step: int | None) -> Image.Image:
        key = (self.pet.state if self.pet else "", step, sprite.size)
        return _fit_pet_size(self.pet, sprite, canvas_w, canvas_h, self._pet_sizes, key)

    def render(self, size: tuple[int, int], now: float | None = None,
               metrics: Mapping[str, Any] | None = None,
               step: int | None = None,
               pet_draw: tuple[Image.Image, int, int] | None = None,
               hide_pet: bool = False) -> Image.Image:
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

        if self.pet is not None and not hide_pet:
            try:
                if pet_draw is not None:                 # 由 RoamingPet 指定位置（多屏巡游）
                    px, py = pet_draw.x, pet_draw.y
                    sprite = pet_draw.sprite
                    if canvas.mode != "RGB":
                        canvas = canvas.convert("RGB")
                    canvas.paste(sprite, (max(0, px), max(0, py)), sprite)
                    if getattr(pet_draw, "bubble", ""):
                        _draw_bubble(canvas, px, py, sprite.width, pet_draw.bubble)
                    return canvas
                sprite = self.pet.frame_at(now, step=step)
                sprite = self._fit_pet(sprite, canvas.width, canvas.height, step)
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
