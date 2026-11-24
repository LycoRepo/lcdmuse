# SPDX-License-Identifier: GPL-3.0-or-later
"""资源目录定位：源码运行看仓库根目录，pip 安装看 wheel 里带的 `lcdmuse/data/`。

仓库里 drivers/、themes/、pets/、assets/ 都在包外面；打成 wheel 时由 setup.py
把它们复制进 `lcdmuse/data/`，这样装完也能找到内置插件、主题和桌宠。
"""
from __future__ import annotations

from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent


def asset_dir(name: str) -> Path:
    """优先仓库根目录下的同名目录；没有就退回包内的 data/ 副本。"""
    repo = REPO_ROOT / name
    if repo.is_dir():
        return repo
    packaged = PKG_DIR / "data" / name
    return packaged if packaged.is_dir() else repo


def builtin_drivers_dir() -> Path:
    """仓库自带的驱动目录（不含 _legacy 里的老实现）。"""
    return asset_dir("drivers")
