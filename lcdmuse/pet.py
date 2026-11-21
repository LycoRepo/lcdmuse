# SPDX-License-Identifier: GPL-3.0-or-later
"""桌宠：一个可插拔的 PNG 帧包 + 一台小状态机。

设计要点（DESIGN §5.1）：
- 桌宠是**覆盖层**，不改变主题渲染；关掉它就是回到普通主题。
- 帧推进用**读时求值** `frame_at(now)`，不用定时器累加，长时间运行不会漂。
- 一次性状态（eat / click / say）播完自动回 idle；sleep 保持到下次指令。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from PIL import Image

from lcdmuse.config import default_user_dir
from lcdmuse.lcd_comm import InvalidArgument

ONE_SHOT = {"eat", "click", "say"}


@dataclass(frozen=True)
class SheetCell:
    """精灵图里的一个格子：第 row 行、第 col 列。"""
    row: int
    col: int


@dataclass
class PetState:
    name: str
    frames: list[Any]          # Path 或 SheetCell
    sheet_path: Path | None = None     # 该状态独占的精灵图（整张网格图太吃内存）
    sheet_columns: int = 1
    frame_size: tuple[int, int] | None = None
    fps: float = 3.0
    loop: bool = True

    @property
    def duration(self) -> float:
        return len(self.frames) / max(0.1, self.fps)


@dataclass
class Pet:
    name: str
    display_name: str
    root: Path
    states: dict[str, PetState] = field(default_factory=dict)
    anchor: str = "bottom-left"
    margin: int = 16
    source: Mapping[str, Any] = field(default_factory=dict)
    _state: str = "idle"
    _started: float = 0.0
    cache_limit: int = 64
    _cache: dict[Any, Image.Image] = field(default_factory=dict)   # 解码过的帧，别每帧读盘
    scale: float = 1.0                                            # 固定像素倍数（老写法）
    fit_height: float | None = None                               # 按画布高度比例缩放（跨屏一致，推荐）
    micro_motion: bool = True                                     # 帧少时叠加呼吸/抖动；帧密的精灵图应关掉
    facing: str = "right"                                         # 素材本身朝哪边（决定走路时是否镜像）
    sheet: Image.Image | None = None                              # 精灵图（一整张）
    _sheet_cache: dict[Any, Image.Image] = field(default_factory=dict)   # 状态图 LRU(2)
    sheet_columns: int = 1
    frame_size: tuple[int, int] | None = None

    # ── 加载 ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, root: Path | str) -> Pet:
        root = Path(root)
        manifest = root / "pet.json"
        if not manifest.exists():
            raise InvalidArgument(f"{root} 里没有 pet.json")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        api = int(data.get("api", PET_API))
        if api != PET_API:
            raise InvalidArgument(f"桌宠 {root.name} 要 api {api}，本版只认 {PET_API}")
        kind = str(data.get("kind") or "pet")
        if kind != "pet":
            raise InvalidArgument(f"{root.name} 不是桌宠插件（kind={kind}）")
        # 两种素材格式：逐帧 PNG（frames: [...]）或一整张精灵图（spritesheet: {...}）
        sheet_spec = data.get("spritesheet")   # 全局精灵图（兼容老写法）
        sheet = None
        columns = 1
        frame_size = None
        if sheet_spec:
            if isinstance(sheet_spec, str):
                sheet_spec = {"path": sheet_spec}
            sheet_path = root / str(sheet_spec.get("path") or "spritesheet.webp")
            if not sheet_path.exists():
                raise InvalidArgument(f"桌宠 {root.name} 缺精灵图 {sheet_path}")
            with Image.open(sheet_path) as im:
                sheet = im.convert("RGBA")
            columns = int(sheet_spec.get("columns") or 8)
            rows = int(sheet_spec.get("rows") or max(1, sheet.height * columns // max(1, sheet.width)))
            frame_size = (sheet.width // columns, sheet.height // rows)

        states: dict[str, PetState] = {}
        for name, spec in (data.get("states") or {}).items():
            if "sheet" in spec:                    # 每个状态一张小图（推荐：内存小得多）
                rel = str(spec["sheet"])
                path = root / rel
                if not path.exists():
                    raise InvalidArgument(f"桌宠 {root.name} 的 {name} 缺精灵图 {rel}")
                count = int(spec.get("frames") or 1)
                with Image.open(path) as im:
                    cols = int(spec.get("columns") or count)
                    fsize = (im.width // max(1, cols), im.height)
                states[name] = PetState(name=name,
                                        frames=[SheetCell(0, c) for c in range(count)],
                                        fps=float(spec.get("fps", 3.0)),
                                        loop=bool(spec.get("loop", name not in ONE_SHOT)),
                                        sheet_path=path, sheet_columns=cols,
                                        frame_size=fsize)
                continue
            if sheet is not None and "row" in spec:
                row = int(spec["row"])
                count = int(spec.get("frames") if isinstance(spec.get("frames"), int) else columns)
                frames: list[Any] = [SheetCell(row, c) for c in range(max(1, count))]
            else:
                frames = [root / f for f in spec.get("frames", [])]
                missing = [str(f) for f in frames if not f.exists()]
                if missing:
                    raise InvalidArgument(f"桌宠 {root.name} 的 {name} 缺帧: {missing[:3]}")
            states[name] = PetState(name=name, frames=frames,
                                    fps=float(spec.get("fps", 3.0)),
                                    loop=bool(spec.get("loop", name not in ONE_SHOT)))
        if not states:
            raise InvalidArgument(f"桌宠 {root.name} 没有任何状态")
        return cls(name=data.get("name") or root.name,
                   display_name=data.get("display_name") or data.get("name") or root.name,
                   root=root, states=states,
                   anchor=str(data.get("anchor") or "bottom-left"),
                   margin=int(data.get("margin", 16)),
                   source=dict(data.get("source") or {}),
                   scale=float(data.get("scale", 1.0) or 1.0),
                   fit_height=(float(data["fit_height"]) if data.get("fit_height") else None),
                   micro_motion=bool(data.get("micro_motion", sheet is None)),
                   facing=str(data.get("facing") or "right"),
                   sheet=sheet, sheet_columns=columns, frame_size=frame_size)

    def _sheet_for(self, st: PetState) -> Image.Image | None:
        """取这个状态用的整张图：优先状态自带的 sheet，否则用全局那张。"""
        if st.sheet_path is None:
            return self.sheet
        cached = self._sheet_cache.get(st.sheet_path)
        if cached is not None:
            return cached
        with Image.open(st.sheet_path) as im:
            img = im.convert("RGBA")
        if len(self._sheet_cache) >= 2:               # 只留最近两个动作的图
            self._sheet_cache.pop(next(iter(self._sheet_cache)))
        self._sheet_cache[st.sheet_path] = img
        return img

    # ── 状态机 ──────────────────────────────────────────────────────────
    @property
    def state_names(self) -> list[str]:
        return sorted(self.states)

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, now: float | None = None) -> str:
        if state not in self.states:
            raise InvalidArgument(f"没有状态 {state}，可用: {self.state_names}")
        if state != self._state:
            self._state = state
            self._started = now if now is not None else time.time()
        return self._state

    def _resolve(self, now: float) -> PetState:
        st = self.states[self._state]
        if self._state in ONE_SHOT and not st.loop and now - self._started > st.duration:
            self._state = "idle" if "idle" in self.states else self._state
            self._started = now
            st = self.states[self._state]
        return st

    def frame_at(self, now: float | None = None, step: int | None = None) -> Image.Image:
        """取一帧。

        ``step`` 给定时按「第几次推帧」取帧，而不是按墙钟时间采样——屏幕只有 3fps 时，
        8fps 的动画按时间采样会每次跳到不相邻的帧，看起来就是在闪。按步进就不会。
        """
        explicit_time = now is not None
        now = now if now is not None else time.time()
        st = self._resolve(now)
        if step is not None and st.loop and not explicit_time:
            idx = int(step) % len(st.frames)
        else:
            idx = int((now - self._started) * st.fps)
            idx = idx % len(st.frames) if st.loop else min(idx, len(st.frames) - 1)
        ref = st.frames[idx]
        sprite = self._cache.get((st.name, ref))
        if sprite is None and isinstance(ref, SheetCell):
            sheet_img = self._sheet_for(st)
            if sheet_img is not None:
                fw, fh = (st.frame_size if st.sheet_path else self.frame_size) or (0, 0)
                box = (ref.col * fw, ref.row * fh, (ref.col + 1) * fw, (ref.row + 1) * fh)
                sprite = sheet_img.crop(box)
        if sprite is None:
            with Image.open(ref) as im:
                sprite = im.convert("RGBA")
        if sprite is not None and self.scale != 1.0:
            size = (max(1, int(sprite.width * self.scale)), max(1, int(sprite.height * self.scale)))
            sprite = sprite.resize(size, Image.Resampling.LANCZOS)
        key = (st.name, ref)
        if sprite is not None and key not in self._cache:
            if len(self._cache) >= self.cache_limit:      # 只留最近用过的，别把整套帧常驻
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = sprite
        return sprite

    # ── 合成到画布 ──────────────────────────────────────────────────────
    def anchor_xy(self, canvas_w: int, canvas_h: int, sprite: Image.Image) -> tuple[int, int]:
        m = self.margin
        x = {"left": m, "center": (canvas_w - sprite.width) // 2, "right": canvas_w - sprite.width - m}
        y = {"top": m, "middle": (canvas_h - sprite.height) // 2, "bottom": canvas_h - sprite.height - m}
        v, _, h = self.anchor.partition("-")   # "bottom-left" = 垂直-水平
        return x.get(h, m), y.get(v or "bottom", m)

    def draw_on(self, canvas: Any, now: float | None = None) -> tuple[int, int]:
        sprite = self.frame_at(now)
        x, y = self.anchor_xy(canvas.width, canvas.height, sprite)
        canvas.paste_alpha(sprite, x, y)
        return x, y

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "state": self._state,
            "states": {n: {"frames": len(s.frames), "fps": s.fps, "loop": s.loop}
                       for n, s in self.states.items()},
            "anchor": self.anchor,
            "source": dict(self.source),
        }


PET_API = 1


def pet_dirs(cfg: object = None) -> list[Path]:
    """仓库 pets/ + 用户目录（%APPDATA%/lcdmuse/pets）；同名时用户目录优先。"""
    builtin = Path(getattr(cfg, "pets_dir", None) or Path(__file__).resolve().parent.parent / "pets")
    user = Path(getattr(cfg, "user_pets_dir", None) or (default_user_dir() / "pets"))
    return [builtin, user]


def list_pets(pets_dir: Path | str | list[Path]) -> list[str]:
    """给一个目录或一组目录，返回可用的桌宠 id（同名只算一次，后面的覆盖前面的）。"""
    dirs = [Path(pets_dir)] if isinstance(pets_dir, (str, Path)) else [Path(p) for p in pets_dir]
    found: dict[str, Path] = {}
    for root in dirs:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if (child / "pet.json").exists() or (child / "pet.yaml").exists():
                found[child.name] = child
    return sorted(found)


def find_pet(name: str, dirs: list[Path] | None = None) -> Pet | None:
    """按 id 找桌宠；用户目录优先。"""
    for root in reversed(dirs or pet_dirs()):
        candidate = Path(root) / name
        if (candidate / "pet.json").exists() or (candidate / "pet.yaml").exists():
            return Pet.load(candidate)
    return None
