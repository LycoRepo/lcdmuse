# SPDX-License-Identifier: GPL-3.0-or-later
"""把一张图变成程序图标：assets/icon.png + icon.ico（多尺寸）+ 网页 favicon。

用法：
    python tools/make_icon.py 我的图标.png
    python tools/make_icon.py 我的图标.png --user     # 只放到用户目录，不进仓库
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--user", action="store_true", help="放到用户目录而不是仓库")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"找不到 {src}")
        return 1

    with Image.open(src) as im:
        icon = im.convert("RGBA")

    # 正方形：按短边居中裁切，避免图标被拉扁
    side = min(icon.size)
    left = (icon.width - side) // 2
    top = (icon.height - side) // 2
    icon = icon.crop((left, top, left + side, top + side))
    icon = icon.resize((256, 256), Image.Resampling.LANCZOS)

    if args.user:
        from lcdmuse.config import default_user_dir
        out = default_user_dir()
    else:
        out = Path(__file__).resolve().parent.parent / "assets"
    out.mkdir(parents=True, exist_ok=True)

    png = out / "icon.png"
    ico = out / "icon.ico"
    icon.save(png)
    icon.save(ico, sizes=[(s, s) for s in SIZES])

    # 网页 favicon：前端目录里也放一份
    web = Path(__file__).resolve().parent.parent / "lcdmuse" / "web"
    if web.exists():
        shutil.copyfile(png, web / "favicon.png")

    print(f"已写入 {png}")
    print(f"已写入 {ico}")
    if web.exists():
        print(f"已写入 {web / 'favicon.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
