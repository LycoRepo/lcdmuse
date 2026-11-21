# SPDX-License-Identifier: GPL-3.0-or-later
"""配置与日志：仓库内路径相对仓库根；用户级插件目录放系统配置目录。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "lcdmuse"


def default_user_dir() -> Path:
    """用户级目录：Windows 用 %APPDATA%，其它平台用 XDG 配置目录。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / APP_NAME


def default_user_drivers_dir() -> Path:
    return default_user_dir() / "drivers"


@dataclass
class Config:
    drivers_dir: Path = field(default_factory=lambda: REPO_ROOT / "drivers")
    user_drivers_dir: Path = field(default_factory=default_user_drivers_dir)
    themes_dir: Path = field(default_factory=lambda: REPO_ROOT / "res" / "themes")
    fonts_dir: Path = field(default_factory=lambda: REPO_ROOT / "res" / "fonts")
    pets_dir: Path = field(default_factory=lambda: REPO_ROOT / "pets")
    out_dir: Path = field(default_factory=lambda: REPO_ROOT / "out")
    theme: str = "3.5inchTheme2"
    pet_enabled: bool = True
    default_pet: str = "whale-girl"
    drivers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def drivers_dirs(self) -> list[Path]:
        """用户目录在前：同 id 时用户装的插件覆盖仓库自带的。"""
        return [self.user_drivers_dir, self.drivers_dir]

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        cfg = cls()
        p = Path(path) if path else REPO_ROOT / f"{APP_NAME}.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for key in ("theme", "pet_enabled", "default_pet"):
                if key in data:
                    setattr(cfg, key, data[key])
            for key in ("drivers_dir", "user_drivers_dir"):
                if data.get(key):
                    setattr(cfg, key, Path(data[key]).expanduser())
            cfg.drivers = dict(data.get("drivers") or {})
        return cfg

    def driver_config(self, plugin_id: str) -> dict[str, Any]:
        return dict(self.drivers.get(plugin_id) or {})

    def ensure_out(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir

    def ensure_user_drivers(self) -> Path:
        self.user_drivers_dir.mkdir(parents=True, exist_ok=True)
        return self.user_drivers_dir


_LOGGER_NAME = APP_NAME


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
        fh = logging.FileHandler(log_dir / f"{APP_NAME}.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    return logger


log = get_logger()
