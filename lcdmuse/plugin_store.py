# SPDX-License-Identifier: GPL-3.0-or-later
"""用户插件目录的装卸：从目录或 zip 安装、按 id 卸载，装完立刻验证能不能加载。

安装目标固定是用户目录（Windows `%APPDATA%\\lcdmuse\\drivers`，其它平台 `~/.config/lcdmuse/drivers`），
不碰仓库自带的 `drivers/`，升级程序时不会被覆盖。
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import yaml

from lcdmuse.config import default_user_drivers_dir
from lcdmuse.plugin import DriverPlugin, load_plugins

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class PluginError(RuntimeError):
    """安装/卸载失败，消息直接给用户看。"""


def _find_manifest_dir(src: Path) -> Path:
    """定位 driver.yaml 所在目录，容忍外层多套一层（zip 常见）。"""
    if src.is_dir():
        if (src / "driver.yaml").is_file():
            return src
        subs = [p for p in sorted(src.iterdir()) if p.is_dir() and (p / "driver.yaml").is_file()]
        if len(subs) == 1:
            return subs[0]
        raise PluginError(f"{src} 里没有 driver.yaml（该目录下有 {len(subs)} 个候选）")
    raise PluginError(f"{src} 不是目录")


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        if root != target and root not in target.parents:
            raise PluginError(f"zip 里有越界路径，已中止: {member}")
    zf.extractall(dest)


def install_plugin(src: Path | str, user_root: Path | str | None = None,
                   force: bool = False) -> dict:
    """把 src（目录或 .zip）装进用户插件目录，返回装上的插件信息。"""
    root = Path(user_root or default_user_drivers_dir()).expanduser()
    source = Path(src).expanduser()
    if not source.exists():
        raise PluginError(f"路径不存在: {source}")

    tmp: Path | None = None
    try:
        if source.is_file() and source.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix="lcdmuse-install-"))
            with zipfile.ZipFile(source) as zf:
                _safe_extract(zf, tmp)
            src_dir = _find_manifest_dir(tmp)
        else:
            src_dir = _find_manifest_dir(source)

        data = yaml.safe_load((src_dir / "driver.yaml").read_text(encoding="utf-8")) or {}
        plugin_id = str(data.get("id") or src_dir.name)
        if not _ID_RE.match(plugin_id):
            raise PluginError(f"插件 id 不合法: {plugin_id!r}（只允许小写字母、数字、下划线）")

        dest = root / plugin_id
        if dest.exists() and not force:
            raise PluginError(f"{plugin_id} 已经装过，覆盖请加 --force")
        if dest.exists():
            shutil.rmtree(dest)

        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

        loaded = {p.id: p for p in load_plugins(root, origin="user")}
        plugin: DriverPlugin | None = loaded.get(plugin_id)
        if plugin is None or not plugin.available:
            reason = plugin.reason if plugin else "扫描后没找到它"
            shutil.rmtree(dest, ignore_errors=True)
            raise PluginError(f"复制成功但加载失败，已回滚: {reason}")
        return plugin.to_dict()
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def remove_plugin(plugin_id: str, user_root: Path | str | None = None) -> dict:
    """从用户插件目录删掉一个插件。仓库自带的删不了（也不该删）。"""
    if not _ID_RE.match(plugin_id):
        raise PluginError(f"插件 id 不合法: {plugin_id!r}")
    root = Path(user_root or default_user_drivers_dir()).expanduser()
    dest = root / plugin_id
    if not dest.is_dir():
        raise PluginError(f"用户目录里没有 {plugin_id}（仓库自带的插件请直接改仓库）")
    if root.resolve() not in dest.resolve().parents:
        raise PluginError(f"拒绝删除目录外的路径: {dest}")
    shutil.rmtree(dest)
    return {"id": plugin_id, "removed": str(dest)}


def list_user_plugins(user_root: Path | str | None = None) -> list[DriverPlugin]:
    root = Path(user_root or default_user_drivers_dir()).expanduser()
    return load_plugins(root, origin="user")
