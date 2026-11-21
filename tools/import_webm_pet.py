# SPDX-License-Identifier: GPL-3.0-or-later
"""把 dsh-pet 的 webm 动作包转成本项目能直接用的精灵图宠物。

dsh-pet（https://github.com/PC2005-cloud/dsh-pet，MIT）的动作素材是 640×360 的
VP9 视频，但没有 alpha（黑底）。这里做三件事：
1. 用 ffmpeg 抽帧（默认 10 fps），按状态分组；
2. 从四边泛洪抠掉黑底（只抠连通到边界的近黑像素，角色内部的深色不会被挖洞），
   再按整组帧的统一外接框裁剪，保证同一动作播放时不抖；
3. 把所有状态拼成一张网格精灵图（每行一个动作），写 pet.json。

用法：
    python tools/import_webm_pet.py --src D:/Code/assets-src/dsh-anim --out pets/whale-girl-webm
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import deque
from pathlib import Path

from PIL import Image

FFMPEG = r"C:\Users\Chris\AppData\Local\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffmpeg.exe"

# 状态 → (webm 文件名, 是否循环)
PLAN: dict[str, tuple[str, bool]] = {
    "idle": ("待机呼吸休闲", True),
    "look": ("东张西望", True),
    "walk": ("螃蟹走路", True),
    "float": ("原地漂浮踏步", True),
    "run": ("原地左转奔跑", True),
    "stretch": ("超大伸懒腰", False),
    "yawn": ("哈欠连天", False),
    "sing": ("悠闲哼歌", True),
    "eat": ("吃白饭", True),
    "snack": ("大口吃零食", True),
    "think": ("工作状态-思考冒泡", True),
    "work": ("工作状态-忙碌点按", True),
    "tidy": ("工作状态-清点归档", True),
    "wait": ("工作状态-原地踱步张望", True),
    "success": ("工作状态-雀跃庆祝", False),
    "error": ("工作状态-垂头叹气冒汗", True),
    "sleep_in": ("睡觉-开始", False),
    "sleep": ("睡觉-循环", True),
    "wake": ("睡觉-起来", False),
    "wave": ("点击回应-元气挥手", False),
}

DARK = 26          # 近黑阈值：低于它的、且与边界连通的像素视为背景


def key_black(img: Image.Image) -> Image.Image:
    """从边界泛洪抠黑底，返回 RGBA。"""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    alpha = Image.new("L", (w, h), 255)
    ap = alpha.load()
    seen = bytearray(w * h)
    dq: deque[tuple[int, int]] = deque()

    def dark(x: int, y: int) -> bool:
        r, g, b = px[x, y]
        return max(r, g, b) < DARK

    for x in range(w):
        for y in (0, h - 1):
            if dark(x, y):
                dq.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if dark(x, y):
                dq.append((x, y))

    while dq:
        x, y = dq.popleft()
        idx = y * w + x
        if seen[idx] or not dark(x, y):
            continue
        seen[idx] = 1
        ap[x, y] = 0
        if x > 0:
            dq.append((x - 1, y))
        if x + 1 < w:
            dq.append((x + 1, y))
        if y > 0:
            dq.append((x, y - 1))
        if y + 1 < h:
            dq.append((x, y + 1))

    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def extract_frames(webm: Path, fps: float, tmp: Path) -> list[Path]:
    pattern = tmp / f"{webm.stem}-%04d.png"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(webm),
                    "-vf", f"fps={fps}", str(pattern)],
                   check=True, shell=False)
    return sorted(tmp.glob(f"{webm.stem}-*.png"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    # 屏幕只有 3–5 fps，抽帧率必须跟显示能力对齐：抽多了既塞不进带宽，也只会被丢掉。
    # 默认 5 fps，并且每个动作最多留 --max-frames 帧（够一个循环就行）。
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--max-frames", type=int, default=14)
    ap.add_argument("--height", type=int, default=320)
    ap.add_argument("--quality", type=int, default=88)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, list[Image.Image], bool]] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for state, (anim, loop) in PLAN.items():
            webm = src / f"{anim}.webm"
            if not webm.exists():
                print(f"跳过 {state}：缺 {webm.name}")
                continue
            raw_frames = extract_frames(webm, args.fps, tmp)
            if len(raw_frames) > args.max_frames:          # 只留开头一个循环
                raw_frames = raw_frames[: args.max_frames]
            frames = [key_black(Image.open(f)) for f in raw_frames]
            if not frames:
                print(f"跳过 {state}：没抽到帧")
                continue
            box = None
            for fr in frames:
                b = fr.getbbox()
                if b is None:
                    continue
                box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                             max(box[2], b[2]), max(box[3], b[3]))
            if box is None:
                continue
            pad = 4
            box = (max(0, box[0] - pad), max(0, box[1] - pad),
                   min(frames[0].width, box[2] + pad), min(frames[0].height, box[3] + pad))
            cropped = [fr.crop(box) for fr in frames]
            scale = args.height / cropped[0].height
            size = (max(1, int(cropped[0].width * scale)), args.height)
            cropped = [c.resize(size, Image.Resampling.LANCZOS) for c in cropped]
            rows.append((state, cropped, loop))
            print(f"{state:<9} {anim:<16} {len(cropped):>3} 帧  {size[0]}×{size[1]}")

    if not rows:
        print("没有任何动作被转换")
        return 1

    # 每个状态单独一张小图：整张网格图在 30MP 级别，整张解码要上百 MB，没必要。
    cell_w = max(fr[0].width for _s, fr, _l in rows)
    cell_h = max(fr[0].height for _s, fr, _l in rows)
    states = {}
    total = 0
    for state, frames, loop in rows:
        strip = Image.new("RGBA", (cell_w * len(frames), cell_h), (0, 0, 0, 0))
        for c, fr in enumerate(frames):
            strip.alpha_composite(fr, (c * cell_w, 0))
        path = out / f"{state}.webp"
        strip.save(path, quality=args.quality, method=6)
        total += path.stat().st_size
        # 帧是按显示能力抽出来的，播放走时间轴，所以 fps 就是抽帧率
        states[state] = {"sheet": path.name, "columns": len(frames), "frames": len(frames),
                         "fps": float(args.fps), "loop": loop}
        print(f"  {state:<9} {len(frames):>2} 帧 → {path.name} {path.stat().st_size // 1024} KB")

    meta = {
        "name": args.name or out.name,
        "display_name": "鲸鱼娘（dsh-pet 动画包）",
        "kind": "pet",
        "api": 1,
        "anchor": "bottom-left",
        "margin": 12,
        "fit_height": 0.62,
        "micro_motion": False,
        "facing": "left",
        "states": states,
        "source": {
            "author": "PC2005-cloud/dsh-pet",
            "repo": "https://github.com/PC2005-cloud/dsh-pet",
            "license": "MIT",
            "note": "webm 动作抽帧 + 黑底泛洪抠像 + 统一裁框；每行一个动作，见 docs/THIRD-PARTY.md",
        },
    }
    (out / "pet.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n共 {len(rows)} 个动作，合计 {total // 1024} KB → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
