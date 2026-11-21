# SPDX-License-Identifier: GPL-3.0-or-later
"""底色层：一张静态图，或者会动的 GIF/WebP/视频。

主题只负责「蒙版 + 数据」，底下垫什么由这一层决定，所以换底图不用碰主题。
视频要先抽帧（ffmpeg），抽出来的帧缓存到用户目录，第二次直接读 PNG。
"""
from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageSequence

from lcdmuse.config import default_user_dir

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
ANIM_EXT = {".gif"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
MAX_VIDEO_FRAMES = 600
DEFAULT_VIDEO_FPS = 8.0


def cache_dir() -> Path:
    path = default_user_dir() / "cache" / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fit_image(image: Image.Image, size: tuple[int, int], fit: str = "cover",
              background: Any = (0, 0, 0)) -> Image.Image:
    """把图塞进目标尺寸：cover 裁边、contain 留边、stretch 拉伸。"""
    width, height = size
    if image.size == size:
        return image
    if fit == "stretch":
        return image.resize(size, Image.Resampling.LANCZOS)
    scale = max(width / image.width, height / image.height) if fit == "cover" \
        else min(width / image.width, height / image.height)
    resized = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                           Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, background if background else (0, 0, 0))
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas


class MediaSource:
    """一个能按时间取帧的底图。`frame(now)` 永远返回目标尺寸的 RGB 图。"""

    def __init__(self, path: Path, size: tuple[int, int] | None = None, fit: str = "cover",
                 fps: float | None = None):
        self.path = Path(path)
        self.size = size
        self.fit = fit
        self.fps = fps
        self._frames: list[Image.Image] = []
        self._durations: list[float] = []
        self._loaded = False
        self.kind = "image"
        self.video_cache: Path | None = None

    # ── 构造 ────────────────────────────────────────────────────────────
    @classmethod
    def open(cls, path: Path | str, size: tuple[int, int] | None = None,
             fit: str = "cover", fps: float | None = None) -> MediaSource:
        src = cls(Path(path), size, fit, fps)
        src._prepare()
        return src

    def _prepare(self) -> None:
        suffix = self.path.suffix.lower()
        if suffix in IMAGE_EXT or suffix in ANIM_EXT:
            self._load_images()
        elif suffix in VIDEO_EXT:
            self._load_video()
        else:
            raise ValueError(f"不认识的底图类型：{self.path.name}")
        if not self._frames:
            raise ValueError(f"读不出任何一帧：{self.path}")

    def _load_images(self) -> None:
        with Image.open(self.path) as im:
            animated = bool(getattr(im, "is_animated", False))
            self.kind = "anim" if animated else "image"
            if not animated:
                self._frames = [self._fit(im.convert("RGB"))]
                self._durations = [0.0]
                return
            for frame in ImageSequence.Iterator(im):
                self._frames.append(self._fit(frame.convert("RGB")))
                self._durations.append(max(0.02, float(frame.info.get("duration", 80)) / 1000))
            self._loaded = True

    def _load_video(self) -> None:
        self.kind = "video"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError(f"{self.path.name} 是视频，需要先装 ffmpeg（或换成 GIF）")
        stat = self.path.stat()
        key = hashlib.sha1(f"{self.path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:12]
        out_dir = cache_dir() / key
        frames = sorted(out_dir.glob("frame_*.png"))
        fps = self.fps or DEFAULT_VIDEO_FPS
        if not frames:
            out_dir.mkdir(parents=True, exist_ok=True)
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(self.path),
                   "-vf", f"fps={fps},scale=iw:ih", "-frames:v", str(MAX_VIDEO_FRAMES),
                   str(out_dir / "frame_%05d.png")]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if res.returncode != 0:
                raise ValueError(f"ffmpeg 抽帧失败：{res.stderr.strip()[:160]}")
            frames = sorted(out_dir.glob("frame_*.png"))
        if not frames:
            raise ValueError(f"{self.path.name} 抽不出帧")
        self.video_cache = out_dir
        self.fps = fps
        self._frames = []
        for frame_path in frames:
            with Image.open(frame_path) as im:
                self._frames.append(self._fit(im.convert("RGB")))
        self._durations = [1.0 / fps] * len(self._frames)
        self._loaded = True

    def _fit(self, image: Image.Image) -> Image.Image:
        return fit_image(image, self.size, self.fit) if self.size else image

    # ── 取帧 ────────────────────────────────────────────────────────────
    @property
    def animated(self) -> bool:
        return len(self._frames) > 1 and self.kind != "image"

    @property
    def duration(self) -> float:
        return sum(self._durations) or 0.0

    def frame(self, now: float | None = None) -> Image.Image:
        if len(self._frames) == 1 or not self.duration:
            return self._frames[0].copy()
        now = now if now is not None else time.monotonic()
        offset = now % self.duration
        for frame, duration in zip(self._frames, self._durations, strict=False):
            if offset < duration:
                return frame.copy()
            offset -= duration
        return self._frames[-1].copy()

    def describe(self) -> dict[str, Any]:
        return {"path": str(self.path), "kind": self.kind, "frames": len(self._frames),
                "fps": round(self.fps, 2) if self.fps else None,
                "fit": self.fit, "size": list(self.size) if self.size else None}


def from_bytes(data: bytes, size: tuple[int, int], fit: str = "cover") -> Image.Image:
    """把一张上传的图片（bytes）直接铺到指定尺寸。"""
    with Image.open(io.BytesIO(data)) as im:
        return fit_image(im.convert("RGB"), size, fit)
