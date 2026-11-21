# SPDX-License-Identifier: GPL-3.0-or-later
"""画主题底图：深海月光（960×480）与夜港霓虹（1480×720）。

纯 Pillow 程序化绘制，不依赖外部素材，生成：
    themes/deepsea/bg.jpg + mask.png
    themes/neon-harbor/bg.jpg + mask.png

用法：python tools/make_theme_art.py
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
THEMES = ROOT / "themes"


# ---------- 通用小工具 ----------

def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _vgrad(size: tuple[int, int], stops: list[tuple[float, tuple[int, int, int]]]) -> Image.Image:
    """竖向渐变：stops 是 (位置0~1, 颜色) 的列表。"""
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                px[0, y] = _lerp(c0, c1, k)
                break
        else:
            px[0, y] = stops[-1][1]
    return img.resize((w, h), Image.Resampling.BILINEAR)


def _glow(size: tuple[int, int], center: tuple[float, float], radius: float,
          color: tuple[int, int, int], strength: float) -> Image.Image:
    """一团柔光，用来做月亮/霓虹灯晕。"""
    w, h = size
    small = (max(8, w // 4), max(8, h // 4))
    layer = Image.new("L", small, 0)
    d = ImageDraw.Draw(layer)
    cx, cy = center[0] / 4, center[1] / 4
    r = max(1, radius / 4)
    steps = 14
    for i in range(steps, 0, -1):
        k = i / steps
        d.ellipse([cx - r * k, cy - r * k, cx + r * k, cy + r * k],
                  fill=int(255 * strength * (1 - k) ** 2.2))
    layer = layer.filter(ImageFilter.GaussianBlur(r / 6))
    layer = layer.resize(size, Image.Resampling.BILINEAR)
    tint = Image.new("RGB", size, color)
    return Image.composite(tint, Image.new("RGB", size, (0, 0, 0)), layer)


def _add_glow(base: Image.Image, center: tuple[float, float], radius: float,
              color: tuple[int, int, int], strength: float) -> Image.Image:
    glow = _glow(base.size, center, radius, color, strength)
    from PIL import ImageChops
    return ImageChops.add(base, glow, scale=1.0, offset=0)


def _vignette(size: tuple[int, int], strength: float = 0.55, power: float = 2.2) -> Image.Image:
    w, h = size
    small = (w // 4, h // 4)
    mask = Image.new("L", small, 0)
    px = mask.load()
    cx, cy = small[0] / 2, small[1] / 2
    maxd = math.hypot(cx, cy)
    for y in range(small[1]):
        for x in range(small[0]):
            d = math.hypot(x - cx, y - cy) / maxd
            px[x, y] = int(255 * strength * max(0.0, d ** power))
    return mask.resize(size, Image.Resampling.BILINEAR)


def _grain(size: tuple[int, int], amount: int = 8, seed: int = 7) -> Image.Image:
    rnd = random.Random(seed)
    w, h = size
    noise = Image.new("L", (w, h))
    noise.putdata([128 + rnd.randint(-amount, amount) for _ in range(w * h)])
    return noise


def _finish(img: Image.Image, *, vig: float = 0.5, grain: int = 3, seed: int = 7) -> Image.Image:
    out = img.convert("RGB")
    dark = Image.new("RGB", out.size, (0, 0, 0))
    out = Image.composite(dark, out, _vignette(out.size, vig))
    noise = _grain(out.size, grain, seed)
    from PIL import ImageChops
    return ImageChops.add(out, Image.merge("RGB", (noise, noise, noise)), scale=1.0, offset=-128)


def _whale_mask(w: int, h: int) -> Image.Image:
    """鲸鱼剪影蒙版：几个椭圆取并集再羽化，头右尾左。"""
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([120, 78, 540, 212], fill=255)      # 身体
    d.ellipse([430, 84, 600, 206], fill=255)      # 头
    d.ellipse([96, 104, 210, 190], fill=255)      # 尾柄
    d.ellipse([20, 46, 150, 128], fill=255)       # 尾上叶
    d.ellipse([20, 156, 150, 244], fill=255)      # 尾下叶
    d.ellipse([350, 176, 470, 236], fill=255)     # 胸鳍
    d.ellipse([250, 60, 340, 110], fill=255)      # 背脊
    m = m.filter(ImageFilter.GaussianBlur(6)).point(lambda v: 255 if v > 130 else 0)
    return m.filter(ImageFilter.GaussianBlur(1.2))


def _stars(img: Image.Image, count: int, seed: int, *, max_y: float = 0.75) -> None:
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    for _ in range(count):
        x = rnd.uniform(0, w)
        y = rnd.uniform(0, h * max_y)
        r = rnd.choice((0, 0, 0, 1, 1, 2))
        a = rnd.randint(40, 190)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(210, 232, 255, a))


# ---------- 主题一：深海月光（960×480，先马水冷头） ----------

def make_deepsea() -> tuple[Image.Image, Image.Image]:
    W, H = 960, 480
    base = _vgrad((W, H), [
        (0.0, (6, 26, 44)),
        (0.35, (9, 48, 76)),
        (0.62, (7, 32, 56)),
        (1.0, (3, 12, 22)),
    ]).convert("RGB")

    # 月光光柱：从右上斜插下来的几道柔光
    beams = Image.new("RGB", (W, H), (0, 0, 0))
    bd = ImageDraw.Draw(beams)
    rnd = random.Random(11)
    for i in range(5):
        x0 = W * (0.58 + i * 0.07)
        width = rnd.uniform(26, 64)
        bd.polygon([(x0, -30), (x0 + width, -30),
                    (x0 + width * 2.6 + 120, H * 0.95), (x0 + 120, H * 0.95)],
                   fill=(18, 60, 88))
    beams = beams.filter(ImageFilter.GaussianBlur(26))
    from PIL import ImageChops
    base = ImageChops.add(base, beams)

    base = _add_glow(base, (W * 0.80, H * 0.10), 190, (120, 200, 235), 0.9)   # 水面月晕
    base = _add_glow(base, (W * 0.20, H * 0.85), 240, (10, 70, 96), 0.5)

    _stars(base, 90, seed=3, max_y=0.5)

    # 远处的鲸影：多个椭圆先并成一个蒙版再上色，边缘连续、不会出现"串珠脖子"
    base = base.convert("RGBA")
    wm = _whale_mask(700, 300).resize((588, 252), Image.Resampling.LANCZOS)
    whale_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    whale_layer.paste(Image.new("RGBA", (588, 252), (17, 54, 78, 228)), (296, 96), wm)
    base = Image.alpha_composite(base, whale_layer)

    # 气泡
    rnd = random.Random(21)
    bub = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bub)
    for _ in range(46):
        x = rnd.uniform(0, W)
        y = rnd.uniform(H * 0.25, H)
        r = rnd.uniform(0.9, 2.8)
        a = rnd.randint(22, 66)
        bd.ellipse([x - r, y - r, x + r, y + r], outline=(190, 235, 255, a), width=1)
    base = Image.alpha_composite(base.convert("RGBA"), bub).convert("RGB")

    bg = _finish(base, vig=0.55, grain=3, seed=5)

    # 蒙版：左侧压暗一块放数据、顶部一条细高光、四周暗角
    mask = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    md = ImageDraw.Draw(mask, "RGBA")
    scrim = Image.new("L", (W, H), 0)
    sdw = ImageDraw.Draw(scrim)
    for i in range(120):
        a = int(150 * (1 - i / 120) ** 1.8)
        sdw.line([(0, i), (W, i)], fill=a)
    mask.paste(Image.new("RGBA", (W, H), (2, 10, 18, 255)), (0, 0), scrim)
    md.line([(0, H - 1), (W, H - 1)], fill=(120, 200, 235, 26), width=1)
    md.rounded_rectangle([24, H - 132, 372, H - 24], radius=16,
                         fill=(4, 20, 32, 120), outline=(96, 176, 210, 60), width=1)
    return bg, mask


# ---------- 主题二：夜港霓虹（1480×720，爱国者） ----------

def make_neon_harbor() -> tuple[Image.Image, Image.Image]:
    W, H = 1480, 720
    horizon = int(H * 0.46)
    sky = _vgrad((W, horizon), [
        (0.0, (10, 8, 26)),
        (0.45, (26, 18, 52)),
        (0.78, (58, 30, 74)),
        (1.0, (96, 48, 84)),
    ])
    water = _vgrad((W, H - horizon), [
        (0.0, (46, 26, 58)),
        (0.35, (18, 16, 40)),
        (1.0, (6, 7, 18)),
    ])
    base = Image.new("RGB", (W, H))
    base.paste(sky, (0, 0))
    base.paste(water, (0, horizon))

    _stars(base, 220, seed=17, max_y=0.36)
    mx, my, mr = W * 0.79, horizon * 0.44, 34                # 月亮位置，最后再画
    base = _add_glow(base, (W * 0.79, horizon * 0.44), 130, (235, 225, 255), 1.35)  # 月晕
    base = _add_glow(base, (W * 0.78, horizon * 0.62), 200, (150, 120, 220), 0.55)
    base = _add_glow(base, (W * 0.16, horizon * 0.9), 260, (70, 60, 150), 0.35)

    # 城市轮廓
    rnd = random.Random(23)
    city = Image.new("RGB", (W, H), (0, 0, 0))
    cd = ImageDraw.Draw(city)
    x = -40
    towers: list[tuple[int, int, int]] = []
    while x < W + 40:
        bw = rnd.randint(46, 128)
        bh = rnd.randint(int(H * 0.10), int(H * 0.26))
        top = horizon - bh
        shade = rnd.randint(10, 24)
        cd.rectangle([x, top, x + bw, horizon], fill=(shade, shade + 4, shade + 14))
        towers.append((x, bw, top))
        x += bw + rnd.randint(4, 20)

    # 窗户：暖黄和冷青两种灯
    for (tx, bw, top) in towers:
        cols = max(1, bw // 14)
        rows = max(1, (horizon - top) // 16)
        for c in range(cols):
            for r in range(rows):
                if rnd.random() < 0.55:
                    continue
                wx = tx + 6 + c * 14
                wy = top + 8 + r * 16
                warm = rnd.random() < 0.7
                col = (255, 208, 130) if warm else (140, 226, 255)
                a = rnd.randint(70, 200)
                cd.rectangle([wx, wy, wx + 5, wy + 7], fill=tuple(int(v * a / 255) for v in col))

    base = Image.blend(base, Image.blend(base, city, 0.92), 0.9)

    # 水面倒影：把天际线镜像压扁、横向拉丝
    refl = base.crop((0, horizon - int(H * 0.22), W, horizon)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    refl = refl.resize((W, int(H * 0.22)), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(3))
    refl = refl.resize((W, H - horizon), Image.Resampling.BILINEAR)
    alpha = Image.linear_gradient("L").resize((W, H - horizon)).point(lambda v: int(200 * (1 - v / 255)))
    base.paste(refl, (0, horizon), alpha)

    # 水面上的一道月光影
    streak = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pk = ImageDraw.Draw(streak)
    for i in range(9):
        wdt = (mr * 2) * (0.5 + i * 0.16)
        pk.ellipse([mx - wdt / 2, horizon + i * 22, mx + wdt / 2, horizon + 26 + i * 22],
                   fill=(225, 220, 255, max(6, 46 - i * 4)))
    streak = streak.filter(ImageFilter.GaussianBlur(9))
    base = Image.alpha_composite(base.convert("RGBA"), streak).convert("RGB")

    # 霓虹灯带
    nd = ImageDraw.Draw(base, "RGBA")
    nd.line([(0, horizon + 1), (W, horizon + 1)], fill=(255, 150, 210, 60), width=2)

    bg = _finish(base, vig=0.5, grain=3, seed=9)

    # 月亮放在暗角之后画，否则边缘压暗会把它变成灰盘
    halo = _glow((W, H), (mx, my), 150, (215, 205, 255), 1.0)
    from PIL import ImageChops
    bg = ImageChops.add(bg, halo)
    md0 = ImageDraw.Draw(bg, "RGBA")
    for i in range(mr, 0, -1):                                # 由外到内渐亮
        t = i / mr
        v = int(238 + 17 * (1 - t))
        md0.ellipse([mx - i, my - i, mx + i, my + i], fill=(v, int(v * 0.99), 255, 255))

    # 蒙版：右侧压暗竖条放数据 + 四周暗角 + 底部细线
    mask = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    md = ImageDraw.Draw(mask, "RGBA")
    scrim = Image.new("L", (W, H), 0)
    sdw = ImageDraw.Draw(scrim)
    for i in range(220):
        a = int(170 * (1 - i / 220) ** 1.6)
        sdw.line([(W - i, 0), (W - i, H)], fill=a)
    mask.paste(Image.new("RGBA", (W, H), (6, 4, 16, 255)), (0, 0), scrim)
    md.line([(0, H - 1), (W, H - 1)], fill=(255, 160, 210, 28), width=1)
    md.rounded_rectangle([W - 470, H - 300, W - 60, H - 60], radius=20,
                         fill=(10, 6, 26, 130), outline=(190, 140, 235, 55), width=1)
    md.rounded_rectangle([40, 566, 1000, 672], radius=18,
                         fill=(10, 6, 26, 150), outline=(190, 140, 235, 40), width=1)
    # 底部指标条后面垫一层暗色带，压在倒影上也看得清
    band = Image.new("L", (W, H), 0)
    ImageDraw.Draw(band).rectangle([0, H - 190, W, H], fill=150)
    band = band.filter(ImageFilter.GaussianBlur(28))
    mask.paste(Image.new("RGBA", (W, H), (6, 4, 16, 255)), (0, 0), band)
    return bg, mask


def main() -> int:
    jobs = {
        "deepsea": make_deepsea,
        "neon-harbor": make_neon_harbor,
    }
    for tid, fn in jobs.items():
        out = THEMES / tid
        out.mkdir(parents=True, exist_ok=True)
        bg, mask = fn()
        bg.save(out / "bg.jpg", quality=92, subsampling=1)
        mask.save(out / "mask.png")
        print(f"{tid}: bg {bg.size} {bg.mode} / mask {mask.size} {mask.mode} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
