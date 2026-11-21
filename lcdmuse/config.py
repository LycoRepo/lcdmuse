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
    themes_dir: Path = field(default_factory=lambda: REPO_ROOT / "themes")
    user_themes_dir: Path = field(default_factory=lambda: default_user_dir() / "themes")
    fonts_dir: Path = field(default_factory=lambda: REPO_ROOT / "res" / "fonts")
    pets_dir: Path = field(default_factory=lambda: REPO_ROOT / "pets")
    user_pets_dir: Path = field(default_factory=lambda: default_user_dir() / "pets")
    out_dir: Path = field(default_factory=lambda: REPO_ROOT / "out")
    theme: str = "bezel-mask"        # 主题插件 id（themes/<id>/theme.yaml）；空 = 用测试卡
    background: str = ""             # 覆盖主题底图：一张图 / GIF / 视频的路径
    pet_enabled: bool = True
    default_pet: str = "whale-girl"
    drivers: dict[str, dict[str, Any]] = field(default_factory=dict)
    device_names: dict[str, str] = field(default_factory=dict)   # plugin_id -> 自定义显示名
    path: Path | None = None                                     # 从哪个 yaml 读进来，回写也写回它

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
            for key in ("theme", "pet_enabled", "default_pet", "background"):
                if key in data:
                    setattr(cfg, key, data[key])
            for key in ("drivers_dir", "user_drivers_dir", "themes_dir", "user_themes_dir",
                        "pets_dir", "user_pets_dir"):
                if data.get(key):
                    setattr(cfg, key, Path(data[key]).expanduser())
            cfg.drivers = dict(data.get("drivers") or {})
            cfg.device_names = {str(k): str(v) for k, v in (data.get("device_names") or {}).items()}
            cfg.path = p
        return cfg

    def name_for(self, plugin_id: str, fallback: str = "") -> str:
        """设备显示名：用户改过的优先，其次设备自报，最后插件 id。"""
        return self.device_names.get(plugin_id) or fallback or plugin_id

    def set_device_name(self, plugin_id: str, name: str, path: Path | str | None = None) -> None:
        name = (name or "").strip()
        if name:
            self.device_names[plugin_id] = name
        else:
            self.device_names.pop(plugin_id, None)
        self.save(path)

    def save(self, path: Path | str | None = None) -> Path:
        """只回写我们管的几个键，其余保持原样。"""
        p = Path(path) if path else self.path
        if p is None:                     # 不是从配置文件起来的实例：不许写回，免得把别人的配置冲掉
            log.warning("这个 Config 没有来源文件，跳过回写（用 Config.load() 或显式传 path）")
            return Path(f"{APP_NAME}.yaml")
        data: dict[str, Any] = {}
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        data.update({"theme": self.theme, "background": self.background,
                     "pet_enabled": self.pet_enabled, "default_pet": self.default_pet,
                     "drivers": self.drivers, "device_names": self.device_names})
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self.path = p
        return p

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
