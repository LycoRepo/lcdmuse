"""把表情包 GIF 变成桌宠帧：去标签、抠透明底、裁边、缩放到目标尺寸。

用法：
    python tools/petify.py --gif ~/stickers/eat.gif --state eat --out pets/my-pet --max 300x400

只做机械处理，不改画风；来源与许可写在 pet.json 的 source 字段里。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageSequence


def trim_label_band(img: Image.Image, keep_ratio: float = 0.86) -> Image.Image:
    """表情包底部常带整条文字标签；按比例裁掉底部，避免把文字带进桌宠。"""
    w, h = img.size
    return img.crop((0, 0, w, int(h * keep_ratio)))


def trim_alpha_bbox(img: Image.Image) -> Image.Image:
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    scale = min(max_w / img.width, max_h / img.height, 1.0)
    if scale >= 1.0:
        return img
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.Resampling.LANCZOS)


def gif_to_frames(gif_path: str, out_dir: Path, state: str, max_size: tuple[int, int]) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    with Image.open(gif_path) as im:
        for idx, frame in enumerate(ImageSequence.Iterator(im)):
            rgba = frame.convert("RGBA")
            rgba = trim_label_band(rgba)
            rgba = trim_alpha_bbox(rgba)
            rgba = fit(rgba, *max_size)
            name = f"{state}_{idx:02d}.png"
            rgba.save(out_dir / name)
            saved.append(name)
    return saved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gif", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True, help="桌宠目录（frames 会建在里面）")
    ap.add_argument("--max", default="300x400")
    args = ap.parse_args()

    max_w, max_h = (int(v) for v in args.max.lower().split("x"))
    out = Path(args.out)
    frames = gif_to_frames(args.gif, out / "frames", args.state, (max_w, max_h))
    print(json.dumps({"state": args.state, "frames": len(frames), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
