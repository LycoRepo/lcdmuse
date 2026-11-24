# SPDX-License-Identifier: GPL-3.0-or-later
"""用户插件目录的装卸：从目录或 zip 安装、按 id 卸载，装完立刻验证能不能加载。

安装目标固定是用户目录（Windows `%APPDATA%\\lcdmuse\\drivers`，其它平台 `~/.config/lcdmuse/drivers`），
不碰仓库自带的 `drivers/`，升级程序时不会被覆盖。
"""
from __future__ import annotations

import os
import re
import shutil
import time
import stat
import tempfile
import zipfile
from pathlib import Path

import yaml

from lcdmuse.config import default_user_drivers_dir
from lcdmuse.plugin import DriverPlugin, load_plugins

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
# 别把版本库和缓存拷进用户目录：.git 里的只读对象会让下次 --force 删不掉
_IGNORE = ("__pycache__", "*.pyc", ".git", ".pytest_cache", "*.egg-info", "out", "logs")


def _rmtree(path: Path) -> None:
    """删目录，遇到只读文件先去掉只读位再删（Windows 上 .git 常见）。"""
    def _clear_readonly(func, item, exc):
        try:
            os.chmod(item, stat.S_IWRITE)
            func(item)
        except Exception:
            raise
    try:                                    # 3.12+ 用 onexc，老版本用 onerror
        shutil.rmtree(path, onexc=_clear_readonly)
    except TypeError:
        shutil.rmtree(path, onerror=_clear_readonly)


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

        root.mkdir(parents=True, exist_ok=True)
        dest = (root / plugin_id).resolve()
        if root.resolve() not in dest.parents:       # id 里混了 .. 之类，别写到目录外
            raise PluginError(f"插件 id 解析到目录外: {dest}")
        if dest.exists() and not force:
            raise PluginError(f"{plugin_id} 已经装过，覆盖请加 --force")

        # 先在临时目录装好、验证能加载，再原子替换；失败时旧版本原样留着
        stamp = f"{os.getpid()}-{int(time.time() * 1000)}"
        staging = root / f".staging-{stamp}"
        backup = root / f".backup-{stamp}"
        try:
            staging.mkdir(parents=True)
            staged = staging / plugin_id
            shutil.copytree(src_dir, staged, ignore=shutil.ignore_patterns(*_IGNORE))

            loaded = {p.id: p for p in load_plugins(staging, origin="user")}
            plugin: DriverPlugin | None = loaded.get(plugin_id)
            if plugin is None or not plugin.available:
                reason = plugin.reason if plugin else "扫描后没找到它"
                raise PluginError(f"新版本校验没过，{plugin_id} 未改动: {reason}")

            if dest.exists():
                dest.rename(backup)
            staged.rename(dest)
            _rmtree(backup) if backup.exists() else None
            return plugin.to_dict()
        except Exception:
            if backup.exists() and not dest.exists():
                backup.rename(dest)                  # 旧版本放回去
            raise
        finally:
            for leftover in (staging, backup):
                if leftover.exists():
                    shutil.rmtree(leftover, ignore_errors=True)
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
