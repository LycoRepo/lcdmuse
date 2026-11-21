# SPDX-License-Identifier: GPL-3.0-or-later
"""一只桌宠，在多块屏之间自己活动。

行为设计参考 DSH 生态里两套 MIT 插件（署名见 docs/THIRD-PARTY.md）：
- dsh-co-pet：状态词汇表（idle / running / waiting / review）+ 精灵图按行取帧 + 气泡
- dsh-png-pet：把「agent 在干什么」映射成角色动作，气泡文案跟着变

落到副屏上就是三件事：
1. 自己有事干：待机、走动、左右张望、坐下、伸懒腰、打瞌睡、吃饭，按权重轮换，
   不是从左滑到右那种。
2. 跟着 DSH 干活：外部调 set_activity() 切到对应动作并冒一句话
   （思考中 / 干活中 / 等你确认 / 做完了），到点自己回到日常。
3. 多屏巡游：同一时刻只在一块屏上，走出边缘就从另一块屏进来；往左走时镜像。

坐标一律用画布宽度的比例，所以 1480×720 和 960×480 上速度观感一致。
"""
from __future__ import annotations

import contextlib
import random
import time
from dataclasses import dataclass, field

from typing import Any

from PIL import Image, ImageOps

from lcdmuse.pet import Pet

# 日常行为：名字、权重、持续秒数区间、对应的桌宠状态
AMBIENT: tuple[tuple[str, int, tuple[float, float], str], ...] = (
    ("idle", 34, (2.5, 6.0), "idle"),
    ("walk", 26, (3.0, 7.0), "walk"),
    ("look", 12, (1.2, 2.6), "idle"),
    ("sit", 10, (4.0, 9.0), "sit"),
    ("stretch", 7, (1.5, 2.5), "stretch"),
    ("eat", 5, (4.0, 8.0), "eat"),
    ("nap", 6, (8.0, 20.0), "sleep"),
)

# DSH 干活的几种状态 → 动作 + 气泡文案
ACTIVITY: dict[str, tuple[str, str]] = {
    "idle": ("idle", ""),
    # 六档跟 dsh-pet 的工作状态联动（思考/工作/等待/成功/出错），文案按事件走
    "thinking": ("think", "嗯……让我想想"),
    "working": ("work", "干活中，别催"),
    "running": ("work", "干活中，别催"),
    "waiting": ("say", "等你确认一下"),
    "review": ("say", "我看看你改的"),
    "success": ("say", "搞定啦"),
    "done": ("say", "搞定啦"),
    "error": ("click", "哎？出错了"),
    "welcome": ("say", "回来了呀"),
    "joy": ("say", "嘿嘿"),
    "play": ("say", "来玩会儿"),
    "hungry": ("eat", "我饿"),
    "sleepy": ("sleep", "困了……"),
}

# 空闲多久自己打盹（whale-girl 是 60 秒）
DOZE_AFTER = 60.0


CURRENT: RoamingPet | None = None      # 进程内当前的那一只（webui / MCP 用它接口）


@dataclass
class PetDraw:
    """一帧要贴的东西：图、坐标，外加一句头顶上的话。"""
    sprite: Image.Image
    x: int
    y: int
    bubble: str = ""


@dataclass
class RoamingPet:
    pet: Pet
    order: list[str] = field(default_factory=list)     # 屏幕顺序（插件 id）
    index: int = 0                                     # 当前在哪块屏
    x: float = 0.04                                    # 归一化水平位置（0~1）
    direction: int = 1                                 # +1 向右、-1 向左
    speed: float = 0.09                                # 每秒移动画布宽度的比例
    dwell: float = 40.0                                # 一块屏待多久后考虑换屏
    margin: int = 14

    _last: float = field(default=0.0, repr=False)
    _since_hop: float = field(default=0.0, repr=False)
    _activity: str = field(default="", repr=False)
    _activity_until: float = field(default=0.0, repr=False)
    _action: str = field(default="idle", repr=False)
    _action_until: float = field(default=0.0, repr=False)
    _bubble: str = field(default="", repr=False)
    _bubble_until: float = field(default=0.0, repr=False)
    _next_ambient: float = field(default=0.0, repr=False)
    _idle_since: float = field(default=0.0, repr=False)
    enabled: bool = True                   # 全局开关：关掉就是纯主题（仍然只有一只）
    mood: Any = None                       # PetMood：养成数据（可空，不影响基本活动）

    def _mood(self):
        if self.mood is None:
            try:
                from lcdmuse.mood import PetMood
                self.mood = PetMood.load()
            except Exception:                  # noqa: BLE001 - 养成坏了不影响桌宠
                self.mood = False
        return self.mood or None

    # ── 外部：告诉它 DSH 正在干什么 ──────────────────────────────────────
    def set_activity(self, kind: str, text: str | None = None,
                     seconds: float = 12.0, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        kind = str(kind).lower()
        mood = self._mood()
        if mood is not None and kind in ("done", "success"):
            leveled = mood.task_done()
            if leveled:
                phrase_extra = f"（升到 {mood.level} 级 · {mood.title()}）"
                text = (text or "搞定啦") + phrase_extra
        state, phrase = ACTIVITY.get(kind, ("idle", ""))
        self._activity = state
        self._activity_until = now + max(1.0, seconds)
        self._action = state
        self._action_until = self._activity_until
        self._next_ambient = self._activity_until
        self.say(text or phrase, seconds=min(6.0, seconds), now=now)

    def say(self, text: str, seconds: float = 4.0, now: float | None = None) -> None:
        if not text:
            return
        now = now if now is not None else time.time()
        self._bubble = text
        self._bubble_until = now + seconds

    # ── 内部时钟 ────────────────────────────────────────────────────────
    def _tick(self, now: float) -> None:
        if not self.order:
            return
        if self._last <= 0:
            self._last = now
            self._next_ambient = now + random.uniform(0.5, 2.0)
            return
        dt = min(0.5, max(0.0, now - self._last))
        self._last = now
        self._since_hop += dt

        mood = self._mood()
        if mood is not None:
            mood.tick(now)
            if mood.hungry and not self._activity and random.random() < dt / 45.0:
                self.say("我饿……", seconds=4.0, now=now)
                self._action, self._action_until = "eat", now + 6.0

        if self._activity and now >= self._activity_until:
            self._activity = ""

        if now >= self._next_ambient and not self._activity:
            if self._idle_since and now - self._idle_since > DOZE_AFTER:
                self._action, self._action_until = "nap", now + random.uniform(10, 25)
                self._next_ambient = self._action_until
                self._state_for("sleep")
            else:
                self._start_ambient(now)

        if self._action == "walk":
            self.x += self.speed * dt * self.direction
            if self.x >= 1.0:                            # 走出右边 → 进下一块屏
                self.x = 0.0
                self._hop(1)
            elif self.x <= 0.0:                          # 走出左边 → 退到上一块屏
                self.x = 1.0
                self._hop(-1)
        elif self._since_hop > self.dwell and random.random() < dt / self.dwell:
            self._hop(1)                                 # 待久了换个屏

    def _start_ambient(self, now: float) -> None:
        total = sum(w for _, w, _, _ in AMBIENT)
        roll = random.uniform(0, total)
        acc = 0.0
        choice = AMBIENT[0]
        for item in AMBIENT:
            acc += item[1]
            if roll <= acc:
                choice = item
                break
        name, _weight, (lo, hi), state = choice
        duration = random.uniform(lo, hi)
        self._idle_since = now if name == "idle" else 0.0

        self._action = name
        self._action_until = now + duration
        self._next_ambient = self._action_until + random.uniform(0.4, 2.0)
        if name == "walk":
            self.direction = random.choice((1, -1))

        if name in ("eat", "nap", "stretch") and random.random() < 0.45:
            self.say({"eat": "我饿", "nap": "困了……", "stretch": "伸个懒腰"}[name],
                     seconds=3.0, now=now)
        self._state_for(state)

    def _state_for(self, wanted: str) -> None:
        """把行为名映射到素材里真实存在的状态，没有就退回 idle。"""
        states = self.pet.states
        alias = {
            "sit": ("sit", "shy", "sleep"),
            "think": ("think", "say", "idle"),
            "work": ("work", "stretch", "say", "idle"),
            "say": ("say", "talk", "idle"),
        }
        for name in alias.get(wanted, (wanted,)):
            if name in states:
                with contextlib.suppress(Exception):
                    self.pet.set_state(name)
                return
        with contextlib.suppress(Exception):
            self.pet.set_state("idle" if "idle" in states else next(iter(states)))

    def _hop(self, step: int = 1) -> None:
        if len(self.order) > 1:
            self.index = (self.index + step) % len(self.order)
        self._since_hop = 0.0
        self._action = "idle"
        self._action_until = self._last + random.uniform(0.8, 2.0)
        self._next_ambient = self._action_until
        self._state_for("idle")

    # ── 给 Player 用 ────────────────────────────────────────────────────
    @property
    def current(self) -> str | None:
        return self.order[self.index] if self.order else None

    def draw_on(self, device_id: str, canvas_w: int, canvas_h: int,
                step: int, now: float | None = None,
                fit_height: float | None = None) -> PetDraw | None:
        """这块屏要不要画桌宠；要的话返回图 + 坐标 + 气泡。"""
        if not self.order or device_id != self.current or not self.enabled:
            return None
        now = now if now is not None else time.time()
        self._tick(now)

        if self._activity:
            self._state_for(self._activity)
        elif now >= self._action_until:
            self._state_for("idle")

        sprite = self.pet.frame_at(now, step=step)
        ratio = fit_height if fit_height is not None else (self.pet.fit_height or 0.7)
        target_h = max(8, int(canvas_h * float(ratio)))
        target_w = max(8, int(sprite.width * target_h / max(1, sprite.height)))
        if target_w > int(canvas_w * 0.55):
            target_w = int(canvas_w * 0.55)
            target_h = max(8, int(sprite.height * target_w / max(1, sprite.width)))
        if (sprite.width, sprite.height) != (target_w, target_h):
            sprite = sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)

        # 素材本身朝某一边（pet.json 的 facing，默认 right）：走的方向和它不一致才镜像，
        # 否则就会出现「倒着走」——之前就是这样，side.png 朝左我们还在左边镜像。
        facing = getattr(self.pet, "facing", "right") or "right"
        wants_mirror = (self.direction < 0) != (str(facing).lower() == "left")
        if self._action == "walk" and wants_mirror:
            sprite = ImageOps.mirror(sprite)

        x = int(self.x * max(1, canvas_w - sprite.width))
        x = max(0, min(canvas_w - sprite.width, x))
        y = max(0, canvas_h - sprite.height - self.margin)
        bubble = self._bubble if now < self._bubble_until else ""
        return PetDraw(sprite=sprite, x=x, y=y, bubble=bubble)
