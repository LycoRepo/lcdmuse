# SPDX-License-Identifier: GPL-3.0-or-later
"""打包时把仓库里的 drivers/themes/pets/assets 复制进 `lcdmuse/data/`。

这些目录在包外面，setuptools 的 package-data 抓不到；不复制的话
`pip install` 出来的 wheel 里内置驱动、主题、桌宠全是空的。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent
INCLUDE = ("drivers", "themes", "pets", "assets")
SKIP_DIRS = {"_legacy", "__pycache__"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def _ignore(_dir: str, names: list[str]) -> set[str]:
    skip = set()
    for name in names:
        if name in SKIP_DIRS or Path(name).suffix in SKIP_SUFFIX:
            skip.add(name)
    return skip


class build_py(_build_py):                     # noqa: N801 - setuptools 的命名习惯
    def run(self) -> None:
        super().run()
        target_root = Path(self.build_lib) / "lcdmuse" / "data"
        for name in INCLUDE:
            src = ROOT / name
            if not src.is_dir():
                continue
            dest = target_root / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest, ignore=_ignore)
            print(f"打包资源：{name} → {dest}")


setup(cmdclass={"build_py": build_py})
