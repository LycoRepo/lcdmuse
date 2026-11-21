# SPDX-License-Identifier: GPL-3.0-or-later
"""配置与日志：路径全部相对仓库根，方便"拷走目录就能跑"。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    drivers_dir: Path = field(default_factory=lambda: REPO_ROOT / "drivers")
    themes_dir: Path = field(default_factory=lambda: REPO_ROOT / "res" / "themes")
    fonts_dir: Path = field(default_factory=lambda: REPO_ROOT / "res" / "fonts")
    pets_dir: Path = field(default_factory=lambda: REPO_ROOT / "pets")
    out_dir: Path = field(default_factory=lambda: REPO_ROOT / "out")
    theme: str = "3.5inchTheme2"
    pet_enabled: bool = True
    default_pet: str = "whale-girl"
    drivers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        cfg = cls()
        p = Path(path) if path else REPO_ROOT / "lcdpilot.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for key in ("theme", "pet_enabled", "default_pet"):
                if key in data:
                    setattr(cfg, key, data[key])
            cfg.drivers = dict(data.get("drivers") or {})
        return cfg

    def driver_config(self, plugin_id: str) -> dict[str, Any]:
        return dict(self.drivers.get(plugin_id) or {})

    def ensure_out(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir


_LOGGER_NAME = "lcdpilot"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s", "%H:%M:%S")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    try:
        log_dir = REPO_ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        fh = logging.FileHandler(log_dir / "lcdpilot.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    return logger


log = get_logger()
