"""画布：把「主题」画成一帧，再交给驱动。不含任何设备知识。

原语来自旧 `lcd_comm.py` 的 8 个绘制方法，但从「推给设备」改成「画到整帧」。
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFont

from lcdkit.lcd_comm import Frame
from lcdkit.render.color import RGB, parse_color

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


def resolve_font(font_dir: str, font_ref: str, size: int):
    """font_ref 形如 "SourceHanSansCN/SourceHanSansCN-Bold.otf"；找不到就回退内置位图字体。"""
    if font_ref:
        p = os.path.join(font_dir, font_ref.replace("\\", "/"))
        if os.path.exists(p):
            try:
                return load_font(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size)
    except TypeError:            # Pillow < 10
        return ImageFont.load_default()


@dataclass(frozen=True)
class DirtyRect:
    x: int
    y: int
    width: int
    height: int

    def to_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


class Canvas:
    """一张可增量绘制的画布。"""

    def __init__(self, width: int, height: int, background: RGB | str = (0, 0, 0), font_dir: str = ""):
        self.width, self.height = int(width), int(height)
        self.font_dir = font_dir
        self._img = Image.new("RGB", (self.width, self.height), parse_color(background))
        self._draw = ImageDraw.Draw(self._img)

    # ── 原语 ─────────────────────────────────────────────────────────────
    def clear(self, background: RGB | str = (0, 0, 0)) -> None:
        self._img.paste(Image.new("RGB", (self.width, self.height), parse_color(background)))
        self._draw = ImageDraw.Draw(self._img)

    def rect(self, x: int, y: int, width: int, height: int, fill=None, outline=None, outline_width: int = 1) -> None:
        box = [x, y, x + width - 1, y + height - 1]
        self._draw.rectangle(box, fill=parse_color(fill) if fill else None,
                             outline=parse_color(outline) if outline else None,
                             width=outline_width)

    def bar(self, x: int, y: int, width: int, height: int, ratio: float,
            bar_color=(0, 120, 255), background=(40, 40, 40), outline=None) -> None:
        ratio = max(0.0, min(1.0, float(ratio)))
        self.rect(x, y, width, height, fill=background)
        filled = int(round(width * ratio))
        if filled > 0:
            self.rect(x, y, filled, height, fill=bar_color)
        if outline:
            self.rect(x, y, width, height, outline=outline)

    def text(self, x: int, y: int, value: str, font_ref: str = "", font_size: int = 20,
             color=(255, 255, 255), anchor: str = "la", background=None) -> None:
        font = resolve_font(self.font_dir, font_ref, font_size)
        self._draw.text((x, y), str(value), font=font, fill=parse_color(color),
                        anchor=anchor, background=parse_color(background) if background else None)

    def image_file(self, path: str, x: int = 0, y: int = 0, size: tuple[int, int] | None = None) -> None:
        img = Image.open(path).convert("RGB")
        if size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        self._img.paste(img, (x, y))

    def image_bytes(self, data: bytes, x: int = 0, y: int = 0, size: tuple[int, int] | None = None) -> None:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        self._img.paste(img, (x, y))

    def paste_alpha(self, img: Image.Image, x: int, y: int) -> None:
        """把带透明通道的图叠到画布上（桌宠用）。"""
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        base = self._img.convert("RGBA")
        base.alpha_composite(img, (int(x), int(y)))
        self._img = base.convert("RGB")
        self._draw = ImageDraw.Draw(self._img)

    # ── 输出 ────────────────────────────────────────────────────────────
    @property
    def image(self) -> Image.Image:
        return self._img

    def to_frame(self) -> Frame:
        return Frame.from_image(self._img)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._img.save(path)
        return path

    # ── 脏矩形 ──────────────────────────────────────────────────────────
    def diff(self, previous: Frame | None, threshold: int = 0) -> list[DirtyRect]:
        """与上一帧比较，返回变化区域（合并成一个包围盒，够用且省事）。"""
        if previous is None:
            return [DirtyRect(0, 0, self.width, self.height)]
        prev = previous.to_image()
        if prev.size != self._img.size:
            return [DirtyRect(0, 0, self.width, self.height)]
        delta = ImageChops.difference(prev, self._img)
        if threshold:
            delta = delta.point(lambda v: 255 if v > threshold else 0)
        bbox = delta.getbbox()
        if not bbox:
            return []
        x0, y0, x1, y1 = bbox
        return [DirtyRect(x0, y0, x1 - x0, y1 - y0)]
