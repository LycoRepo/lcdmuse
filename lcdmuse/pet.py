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


@dataclass
class PetState:
    name: str
    frames: list[Path]
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
    cache_limit: int = 8
    _cache: dict[Any, Image.Image] = field(default_factory=dict)   # 解码过的帧，别每帧读盘

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
        states: dict[str, PetState] = {}
        for name, spec in (data.get("states") or {}).items():
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
                   source=dict(data.get("source") or {}))

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

    def frame_at(self, now: float | None = None) -> Image.Image:
        now = now if now is not None else time.time()
        st = self._resolve(now)
        idx = int((now - self._started) * st.fps)
        idx = idx % len(st.frames) if st.loop else min(idx, len(st.frames) - 1)
        path = st.frames[idx]
        sprite = self._cache.get(path)
        if sprite is None:
            with Image.open(path) as im:
                sprite = im.convert("RGBA")
            if len(self._cache) >= self.cache_limit:      # 只留最近用过的，别把整套帧常驻
                self._cache.pop(next(iter(self._cache)))
            self._cache[path] = sprite
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
