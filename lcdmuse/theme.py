# SPDX-License-Identifier: GPL-3.0-or-later
"""主题插件：一层蒙版 + 绑在指标上的文字/进度条。

为什么要拆成「底图」和「主题」两层：
- 底图（静态图 / GIF / 视频）是内容，用户想换就换，跟版式无关；
- 主题是**蒙版 + 版式**，决定画面压暗多少、数据摆在哪儿、什么颜色。
所以同一个主题可以叠在任何底图上，换底图不需要改主题。

一个主题就是一个目录：

```
themes/neon-mask/
  theme.yaml     # api: 1 + size + background + overlay
  mask.png       # 蒙版（PNG 带 alpha）
  theme.py       # 可选：draw(img, ctx) 自定义绘制
```
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from PIL import Image, ImageDraw, ImageFont

from lcdmuse.config import default_user_dir, log
from lcdmuse.lcd_comm import InvalidArgument
from lcdmuse.media import MediaSource, fit_image
from lcdmuse.metrics import format_value, get_hub

API = 1


# ── 字体 ────────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = False, font_dir: Path | None = None):
    """找得到中文字体就用，找不到退回 Pillow 默认（不崩）。"""
    candidates: list[Path] = []
    if font_dir:
        candidates += [font_dir / "SourceHanSansCN/SourceHanSansCN-Bold.otf",
                       font_dir / "SourceHanSansCN/SourceHanSansCN-Regular.otf",
                       font_dir / "GlowSansSC-Compressed/GlowSansSC-Compressed-Bold.otf"]
    candidates += [Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc")]
    for path in candidates:
        try:
            if path.exists():
                return ImageFont.truetype(str(path), size)
        except Exception:
            continue
    return ImageFont.load_default(size=size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()


def _color(value: Any, default: str = "#ffffff") -> tuple[int, int, int, int]:
    """#rgb / #rrggbb / #rrggbbaa / [r,g,b(,a)] 都吃。"""
    if value is None:
        value = default
    if isinstance(value, (list, tuple)):
        parts = list(value) + [255] * (4 - len(value))
        return tuple(int(p) for p in parts[:4])                   # type: ignore[return-value]
    text = str(value).strip().lstrip("#")
    if len(text) in (3, 4):
        text = "".join(c * 2 for c in text)
    if len(text) == 6:
        text += "ff"
    if len(text) != 8:
        return _color(default)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4, 6))    # type: ignore[return-value]


# ── 主题 ────────────────────────────────────────────────────────────────
@dataclass
class Theme:
    id: str
    name: str
    root: Path
    size: tuple[int, int] | None = None
    background: dict[str, Any] = field(default_factory=dict)
    overlay: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    api: int = API
    source: dict[str, Any] = field(default_factory=dict)
    _media: MediaSource | None = None
    _mask: Image.Image | None = None
    _mask_cache: dict[tuple[int, int], Image.Image] = field(default_factory=dict)
    _mask_overlay_cache: dict[tuple, tuple[Image.Image, Image.Image]] = field(default_factory=dict)
    _hook: Any = None

    # ── 加载 ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, root: Path | str) -> Theme:
        root = Path(root)
        spec_path = next((root / n for n in ("theme.yaml", "theme.yml", "theme.json")
                          if (root / n).exists()), None)
        if spec_path is None:
            raise InvalidArgument(f"{root} 里没有 theme.yaml")
        data: Mapping[str, Any]
        if spec_path.suffix == ".json":
            data = json.loads(spec_path.read_text(encoding="utf-8"))
        else:
            import yaml
            data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        api = int(data.get("api", API))
        if api != API:
            raise InvalidArgument(f"主题 {root.name} 要 api {api}，本版只认 {API}")
        size = data.get("size")
        theme = cls(id=str(data.get("id") or root.name),
                    name=str(data.get("name") or data.get("id") or root.name),
                    root=root, description=str(data.get("description") or ""),
                    size=(int(size[0]), int(size[1])) if size else None,
                    background=dict(data.get("background") or {}),
                    overlay=list(data.get("overlay") or []),
                    api=api, source=dict(data))
        theme._mask = theme._load_mask()
        theme._hook = theme._load_hook()
        return theme

    def _load_mask(self) -> Image.Image | None:
        spec = self.background.get("mask") or self.source.get("mask")
        if not spec:
            return None
        path = self._resolve(spec.get("path") if isinstance(spec, dict) else spec)
        if not path or not path.exists():
            raise InvalidArgument(f"主题 {self.id} 的蒙版不存在：{path}")
        with Image.open(path) as im:
            return im.convert("RGBA")

    def _load_hook(self):
        script = self.root / "theme.py"
        if not script.exists():
            return None
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"lcdmuse_theme_{self.id}", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)                            # type: ignore[union-attr]
        draw = getattr(module, "draw", None)
        if draw is None:
            raise InvalidArgument(f"主题 {self.id} 的 theme.py 没有 draw(img, ctx)")
        return draw

    def _resolve(self, ref: Any) -> Path | None:
        if not ref:
            return None
        path = Path(str(ref)).expanduser()
        return path if path.is_absolute() else (self.root / path)

    # ── 底图 ────────────────────────────────────────────────────────────
    def media(self, size: tuple[int, int], override: MediaSource | None = None) -> MediaSource | None:
        """主题自己的底图；`override` 是用户另指的一张图/一个 GIF/视频。"""
        if override is not None:
            return override
        if self._media is not None:
            return self._media
        cfg_bg = self.background
        kind = str(cfg_bg.get("type") or ("media" if cfg_bg.get("path") else "color"))
        if kind != "media":
            return None
        path = self._resolve(cfg_bg.get("path"))
        if not path or not path.exists():
            log.warning("主题 %s 的底图不存在：%s（改用底色）", self.id, path)
            return None
        self._media = MediaSource.open(path, size, str(cfg_bg.get("fit") or "cover"))
        return self._media

    # ── 渲染 ────────────────────────────────────────────────────────────
    def render(self, size: tuple[int, int] | None = None, *, metrics: Mapping[str, Any] | None = None,
               now: float | None = None, media: MediaSource | None = None,
               font_dir: Path | None = None) -> Image.Image:
        size = size or self.size or (960, 480)
        values = dict(metrics) if metrics is not None else get_hub().sample(now)
        base = self.media(size, media)
        if base is not None:
            canvas = base.frame(now).convert("RGB")
            if canvas.size != size:
                canvas = fit_image(canvas, size, str(self.background.get("fit") or "cover"))
        else:
            canvas = Image.new("RGB", size, _color(self.background.get("color"), "#05080c")[:3])

        # 蒙版：整幅压上去，让底图退到后面去
        if self._mask is not None:
            opacity = float(self.background.get("mask_opacity",
                                               (self.background.get("mask") or {}).get("opacity", 1.0))
                            if isinstance(self.background.get("mask"), dict) else 1.0)
            overlay = self._mask_overlay(size, opacity)
            if overlay is not None:
                rgb, alpha = overlay
                canvas.paste(rgb, (0, 0), alpha)      # 只做一次 alpha 混合，不整幅来回转 RGBA

        # 设计画布 → 实际分辨率：坐标与字号按比例放大，别让 1480×720 上只用左边 2/3
        design = self.size or size
        sx, sy = size[0] / design[0], size[1] / design[1]
        for item in self.overlay:
            self._draw_item(canvas, item, values, size, font_dir, sx, sy)

        if self._hook is not None:
            try:
                self._hook(canvas, ThemeContext(theme=self, size=size, metrics=values, now=now,
                                                font_dir=font_dir, color=_color, font=_font))
            except Exception as exc:                    # 主题自己的脚本坏了别拖垮整帧
                log.warning("主题 %s 的 theme.py 出错: %s", self.id, exc)
        return canvas

    def _mask_overlay(self, size: tuple[int, int], opacity: float):
        """蒙版的 RGB 与 alpha 通道按 (尺寸, 不透明度) 缓存一次，之后每帧直接 paste。"""
        key = (size, round(float(opacity), 3))
        cached = self._mask_overlay_cache.get(key)
        if cached is not None:
            return cached
        mask = self._mask
        if mask is None:
            return None
        if mask.size != size:
            scaled = self._mask_cache.get(size)
            if scaled is None:
                scaled = fit_image(self._mask, size, "stretch")
                self._mask_cache[size] = scaled
            mask = scaled
        if mask.mode != "RGBA":
            mask = mask.convert("RGBA")
        alpha = mask.getchannel("A")
        if opacity < 1.0:
            alpha = alpha.point(lambda a: int(a * max(0.0, min(1.0, opacity))))
        elif opacity <= 0.0:
            return None
        cached = (mask.convert("RGB"), alpha)
        self._mask_overlay_cache[key] = cached
        return cached

    def _draw_item(self, canvas: Image.Image, item: Mapping[str, Any], values: Mapping[str, Any],
                   size: tuple[int, int], font_dir: Path | None,
                   sx: float = 1.0, sy: float = 1.0) -> None:
        scale = min(sx, sy)
        draw = ImageDraw.Draw(canvas, "RGBA")
        if "rect" in item:
            spec = item["rect"] or {}
            x, y = spec.get("at") or (spec.get("x", 0), spec.get("y", 0))
            x, y = float(x) * sx, float(y) * sy
            w, h = spec.get("size") or (spec.get("w", 0), spec.get("h", 0))
            w, h = float(w) * sx, float(h) * sy
            radius = int(float(spec.get("radius", 0)) * scale)
            fill = _color(spec.get("fill"), "#00000000")
            outline = _color(spec.get("outline"), "#00000000")
            box = [int(x), int(y), int(x) + int(w), int(y) + int(h)]
            if radius:
                draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline or None)
            else:
                draw.rectangle(box, fill=fill, outline=outline or None)
            return
        if "text" in item:
            spec = item["text"] or {}
            key = str(spec.get("metric") or "")
            if "text" in spec and not key:              # 纯文字标签，不绑指标
                text = str(spec.get("text"))
            else:
                raw = values.get(key, spec.get("default", ""))
                text = format_value(raw, unit=str(spec.get("unit") or ""),
                                    decimals=spec.get("decimals"),
                                    prefix=str(spec.get("prefix") or ""),
                                    suffix=str(spec.get("suffix") or ""))
            if key == "date.weekday" and spec.get("cn"):   # 1~7 → 周一~周日
                try:
                    text = "周" + "一二三四五六日"[int(float(raw)) - 1]
                except (TypeError, ValueError, IndexError):
                    text = str(raw)
            if spec.get("upper"):
                text = text.upper()
            x, y = spec.get("at") or (spec.get("x", 0), spec.get("y", 0))
            x, y = float(x) * sx, float(y) * sy
            font = _font(max(6, int(float(spec.get("size", 22)) * scale)),
                         bool(spec.get("bold")), font_dir)
            fill = _color(spec.get("color"), "#e8eef5")
            anchor = str(spec.get("anchor") or "la")
            draw.text((int(x), int(y)), text, font=font, fill=fill, anchor=anchor)
            return
        if "bar" in item:
            spec = item["bar"] or {}
            key = str(spec.get("metric") or "")
            try:
                ratio = float(values.get(key, 0) or 0) / float(spec.get("max", 100) or 100)
            except (TypeError, ValueError):
                ratio = 0.0
            ratio = max(0.0, min(1.0, ratio))
            x, y, w, h = (float(v) for v in (spec.get("box") or [0, 0, 100, 8])[:4])
            x, y, w, h = int(x * sx), int(y * sy), int(w * sx), max(1, int(h * sy))
            radius = min(h // 2, int(float(spec.get("radius", h // 2)) * scale))
            draw.rounded_rectangle([x, y, x + w, y + h], radius=radius,
                                   fill=_color(spec.get("track"), "#1b2634"))
            filled = int(w * ratio)
            if filled > 0:
                draw.rounded_rectangle([x, y, x + max(filled, radius * 2 if radius else filled), y + h],
                                       radius=radius, fill=_color(spec.get("color"), "#3fbfe0"))
            return
        if "image" in item:
            spec = item["image"] or {}
            path = self._resolve(spec.get("path"))
            if not path or not path.exists():
                return
            x, y = spec.get("at") or (0, 0)
            x, y = float(x) * sx, float(y) * sy
            target = spec.get("size")
            target = (float(target[0]) * sx, float(target[1]) * sy) if target else None
            with Image.open(path) as im:
                img = im.convert("RGBA")
            if target:
                img = img.resize((int(target[0]), int(target[1])), Image.Resampling.LANCZOS)
            canvas.alpha_composite(img, (int(x), int(y))) if canvas.mode == "RGBA" else \
                canvas.paste(img, (int(x), int(y)), img)
            return
        if "mask" in item:
            spec = item["mask"] or {}
            path = self._resolve(spec.get("path"))
            if not path or not path.exists():
                return
            with Image.open(path) as im:
                layer = im.convert("RGBA")
            layer = layer if layer.size == size else fit_image(layer, size, "stretch")
            opacity = float(spec.get("opacity", 1.0))
            if opacity < 1.0:
                alpha = layer.getchannel("A").point(lambda a: int(a * opacity))
                layer.putalpha(alpha)
            tmp = canvas.convert("RGBA")
            tmp.alpha_composite(layer)
            canvas.paste(tmp.convert("RGB"))
            return

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "root": str(self.root),
                "size": list(self.size) if self.size else None,
                "items": len(self.overlay), "has_mask": self._mask is not None,
                "has_hook": self._hook is not None,
                "background": {k: v for k, v in self.background.items() if k != "mask"}}


@dataclass
class ThemeContext:
    """给 theme.py 的上下文：只读指标 + 画图工具，不让它直接摸硬件。"""

    theme: Theme
    size: tuple[int, int]
    metrics: Mapping[str, Any]
    now: float | None
    font_dir: Path | None
    color: Any = staticmethod(_color)      # type: ignore[assignment]
    font: Any = staticmethod(_font)        # type: ignore[assignment]


# ── 目录扫描（和驱动插件同一套规则：用户目录优先） ──────────────────────
def default_user_themes_dir() -> Path:
    return default_user_dir() / "themes"


def theme_dirs(cfg: Any = None) -> list[Path]:
    """仓库自带 themes/ + 用户目录；用户目录同名主题覆盖内置的。"""
    builtin = Path(getattr(cfg, "themes_dir", None) or Path(__file__).resolve().parent.parent / "themes")
    user = Path(getattr(cfg, "user_themes_dir", None) or default_user_themes_dir())
    return [builtin, user]


def list_themes(dirs: list[Path] | None = None) -> list[Theme]:
    out: dict[str, Theme] = {}
    for root in dirs or theme_dirs():
        root = Path(root)
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not any((child / n).exists() for n in ("theme.yaml", "theme.yml", "theme.json")):
                continue
            try:
                theme = Theme.load(child)
            except Exception as exc:
                log.warning("主题 %s 加载失败: %s", child.name, exc)
                continue
            out[theme.id] = theme            # 后出现的（用户目录）覆盖前面的
    return list(out.values())


def find_theme(theme_id: str, dirs: list[Path] | None = None) -> Theme | None:
    for theme in list_themes(dirs):
        if theme.id == theme_id:
            return theme
    return None
