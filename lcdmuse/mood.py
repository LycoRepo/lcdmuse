# SPDX-License-Identifier: GPL-3.0-or-later
"""桌宠的「养成」状态：等级、经验、饱食度、亲密度。

搬的是 vlln/whale-girl 的思路（MIT，见 docs/THIRD-PARTY.md）：把「干活」换算成经验，
喂食/玩耍提升饱食度与亲密度，饿了自己会喊。存在
``%APPDATA%\\lcdmuse\\pet-stats.json``，重启不清零。

为什么不用数据库：这儿只有一只宠物、几条数字，JSON 够用，坏了删掉就重置。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lcdmuse.config import default_user_dir, log

STARS = (0, 20, 60, 140, 300, 600, 1200, 2400)      # 每级所需累计经验


@dataclass
class PetMood:
    level: int = 1
    exp: int = 0
    hunger: float = 70.0          # 0=饿死 100=很饱
    affinity: float = 20.0        # 亲密度，喂食/玩耍涨，长期不管掉
    tasks_done: int = 0
    fed: int = 0
    played: int = 0
    last_tick: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    # ── 文件 ────────────────────────────────────────────────────────────
    @staticmethod
    def path() -> Path:
        return default_user_dir() / "pet-stats.json"

    @classmethod
    def load(cls) -> PetMood:
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:                    # noqa: BLE001 - 坏了就当新宠物
            log.warning("养成数据读不出来，重置: %s", exc)
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        self.updated = time.time()
        try:
            self.path().parent.mkdir(parents=True, exist_ok=True)
            self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except Exception as exc:                    # noqa: BLE001
            log.warning("养成数据写不进去: %s", exc)

    # ── 结算 ────────────────────────────────────────────────────────────
    def tick(self, now: float | None = None) -> None:
        """按经过的时间扣饱食度、掉亲密度（每小时）。"""
        now = now if now is not None else time.time()
        dt = max(0.0, now - self.last_tick)
        self.last_tick = now
        if dt <= 0:
            return
        hours = dt / 3600.0
        self.hunger = max(0.0, self.hunger - 6.0 * hours)
        if self.hunger < 30:
            self.affinity = max(0.0, self.affinity - 1.5 * hours)

    def _level_up(self) -> bool:
        need = STARS[min(self.level, len(STARS) - 1)] if self.level < len(STARS) else None
        if need is None:
            return False
        if self.exp >= need:
            self.level += 1
            return True
        return False

    def add_exp(self, amount: int) -> bool:
        """干活/任务完成加经验；返回是否升级。"""
        self.exp += max(0, int(amount))
        leveled = False
        while self._level_up():
            leveled = True
        return leveled

    def feed(self) -> None:
        self.hunger = min(100.0, self.hunger + 30.0)
        self.affinity = min(100.0, self.affinity + 3.0)
        self.fed += 1
        self.add_exp(5)
        self.save()

    def play(self) -> None:
        self.hunger = max(0.0, self.hunger - 4.0)
        self.affinity = min(100.0, self.affinity + 5.0)
        self.played += 1
        self.add_exp(8)
        self.save()

    def task_done(self) -> bool:
        self.tasks_done += 1
        leveled = self.add_exp(10)
        self.save()
        return leveled

    # ── 对外 ────────────────────────────────────────────────────────────
    @property
    def hungry(self) -> bool:
        return self.hunger < 30

    def summary(self) -> dict[str, float | int | str]:
        return {"level": self.level, "exp": self.exp, "hunger": round(self.hunger, 1),
                "affinity": round(self.affinity, 1), "tasks": self.tasks_done,
                "fed": self.fed, "played": self.played,
                "title": self.title()}

    def title(self) -> str:
        return ("刚认识" if self.level <= 1 else "混熟了" if self.level <= 3
                else "老搭档" if self.level <= 5 else "离不开你")
