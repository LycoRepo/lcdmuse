# SPDX-License-Identifier: GPL-3.0-or-later
"""阶段 1 的验收测试：契约、插件、渲染、桌宠、以及"核心零硬件"。"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lcdmuse.lcd_comm import (DeviceInfo, Frame, LcdComm, Unsupported,  # noqa: E402
                            DRIVER_API_VERSION, InvalidArgument)
from lcdmuse.plugin import load_plugins  # noqa: E402
from lcdmuse.plugin_store import PluginError, install_plugin, remove_plugin  # noqa: E402
from lcdmuse.registry import Registry  # noqa: E402
from lcdmuse.render.canvas import Canvas  # noqa: E402
from lcdmuse.pet import Pet, list_pets  # noqa: E402
from lcdmuse.player import Player  # noqa: E402


# ── 契约 ────────────────────────────────────────────────────────────────
def test_frame_rotation_swaps_size():
    cv = Canvas(8, 4, background=(0, 0, 0))
    f = cv.to_frame()
    assert f.size == (8, 4)
    assert f.rotated(90).size == (4, 8)
    assert f.rotated(0) is f


def test_canvas_diff_reports_change_and_quiet_when_same():
    cv = Canvas(16, 16)
    first = cv.to_frame()
    assert cv.diff(first) == []
    cv.rect(2, 2, 4, 4, fill=(255, 0, 0))
    changed = cv.diff(first)
    assert len(changed) == 1
    rect = changed[0]
    assert rect.x <= 2 and rect.y <= 2 and rect.width >= 4 and rect.height >= 4


def test_unsupported_by_default():
    info = DeviceInfo(id="x", name="x", width=4, height=4)

    class Minimal(LcdComm):
        @classmethod
        def probe(cls, config=None):
            return info

        def open(self):
            return self.bind(info)

        def push_frame(self, frame):
            self.last = frame

    d = Minimal()
    d.open()
    d.push_frame(Frame(4, 4, b"\x00" * 48))
    with pytest.raises(Unsupported):
        d.push_region(Frame(2, 2, b"\x00" * 12), 0, 0)
    with pytest.raises(Unsupported):
        d.set_brightness(50)


# ── 插件加载 ────────────────────────────────────────────────────────────
def _make_plugin(root: Path, pid: str, api: int = DRIVER_API_VERSION, requires: str = "[]") -> None:
    d = root / pid
    d.mkdir(parents=True)
    (d / "driver.yaml").write_text(textwrap.dedent(f"""
        id: {pid}
        name: {pid} 测试驱动
        version: 0.0.1
        api: {api}
        entry: driver:TestDriver
        requires: {requires}
        enabled_by_default: true
    """), encoding="utf-8")
    (d / "driver.py").write_text(textwrap.dedent("""
        from lcdmuse.lcd_comm import Capability, DeviceInfo, LcdComm

        class TestDriver(LcdComm):
            @classmethod
            def probe(cls, config=None):
                return DeviceInfo(id="t:0", name="t", width=8, height=8,
                                  capabilities=Capability.PUSH_FULL)
            def open(self):
                return self.bind(self.probe())
            def push_frame(self, frame):
                self.pushed = frame
    """), encoding="utf-8")


def test_plugin_api_mismatch_is_unavailable(tmp_path):
    _make_plugin(tmp_path, "good")
    _make_plugin(tmp_path, "old", api=DRIVER_API_VERSION - 1)
    plugins = {p.id: p for p in load_plugins(tmp_path)}
    assert plugins["good"].available
    assert not plugins["old"].available
    assert "契约版本" in plugins["old"].reason


def test_plugin_missing_dependency_is_unavailable(tmp_path):
    _make_plugin(tmp_path, "needs", requires="[not_a_real_module_xyz]")
    plugin = load_plugins(tmp_path)[0]
    assert not plugin.available
    assert "缺少依赖" in plugin.reason


def test_repo_drivers_all_load():
    reg = Registry(REPO / "drivers")
    rows = reg.describe()
    assert len(rows) >= 8, rows
    assert {r["id"] for r in rows} >= {"simulated", "turing_rev_a", "turing_rev_b", "turing_rev_c",
                                       "turing_rev_d", "turzx_usb", "weact_a", "weact_b"}
    for r in rows:
        assert r["api"] == DRIVER_API_VERSION, r


def test_disabled_by_default_not_probed():
    reg = Registry(REPO / "drivers")
    enabled_ids = {p.id for p, _ in reg.probe()}
    assert "simulated" in enabled_ids
    assert "turing_rev_a" not in enabled_ids       # 原厂移植默认不加载


# ── 桌宠 ────────────────────────────────────────────────────────────────
def test_whale_girl_pet_loads_and_renders():
    pet = Pet.load(REPO / "pets" / "whale-girl")
    assert {"idle", "eat", "sleep", "click", "say"} <= set(pet.state_names)
    img = pet.frame_at()
    assert img.mode == "RGBA"
    assert img.width <= 300 and img.height <= 400


def test_one_shot_state_returns_to_idle():
    pet = Pet.load(REPO / "pets" / "whale-girl")
    t0 = 1_000.0
    pet.set_state("eat", now=t0)
    assert pet.state == "eat"
    pet.frame_at(t0 + 0.1)
    pet.frame_at(t0 + pet.states["eat"].duration + 1.0)   # 播完
    assert pet.state == "idle"


def test_pet_template_is_valid():
    assert "_template" in list_pets(REPO / "pets")
    Pet.load(REPO / "pets" / "_template").frame_at()


def test_unknown_pet_state_raises():
    pet = Pet.load(REPO / "pets" / "whale-girl")
    with pytest.raises(InvalidArgument):
        pet.set_state("不存在的状态")


# ── 核心零硬件（把 §8 的 grep 固化成测试）────────────────────────────────
CORE_PATTERNS = {
    # 通用形态检查：不点名任何具体硬件，只要求核心"不认识硬件"
    "十六进制常量": r"\b0x[0-9A-Fa-f]{2,}\b",
    "设备标识": r"\bVID_|\bPID_|\bCOM\d+\b",
    "波特率常量": r"baud\w*\s*=\s*\d{4,}",
    "协议魔数": r"\\x55\\xaa|\b55\s*AA\b",
}


@pytest.mark.parametrize("name,pattern", list(CORE_PATTERNS.items()))
def test_core_has_no_hardware_knowledge(name, pattern):
    offenders = []
    for path in (REPO / "lcdmuse").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(pattern, line, re.I):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()[:80]}")
    assert offenders == [], f"{name} 泄漏到核心: {offenders[:5]}"


# ── 用户插件目录（方案 B） ───────────────────────────────────────────────
def _copy_simulated(dest_root: Path) -> Path:
    import shutil
    target = dest_root / "simulated"
    shutil.copytree(REPO / "drivers" / "simulated", target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return target


def test_user_dir_plugins_are_scanned_and_marked_as_user(tmp_path):
    user_root = tmp_path / "user-drivers"
    _copy_simulated(user_root)
    reg = Registry([user_root, REPO / "drivers"])
    plugin = reg.get("simulated")
    assert plugin.origin == "user"
    assert plugin.available


def test_user_plugin_overrides_builtin_with_same_id(tmp_path):
    user_root = tmp_path / "user-drivers"
    target = _copy_simulated(user_root)
    manifest = target / "driver.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        "name: 模拟屏（离线验证）", "name: 模拟屏（用户覆盖版）"), encoding="utf-8")

    reg = Registry([user_root, REPO / "drivers"])
    assert reg.get("simulated").name == "模拟屏（用户覆盖版）"
    # 同 id 只出现一次
    assert [p.id for p in reg.plugins].count("simulated") == 1


def test_plugin_install_and_remove_round_trip(tmp_path):
    user_root = tmp_path / "user-drivers"
    info = install_plugin(REPO / "drivers" / "simulated", user_root)
    assert info["id"] == "simulated" and info["origin"] == "user"
    assert (user_root / "simulated" / "driver.yaml").is_file()

    with pytest.raises(PluginError):
        install_plugin(REPO / "drivers" / "simulated", user_root)      # 已存在
    install_plugin(REPO / "drivers" / "simulated", user_root, force=True)

    out = remove_plugin("simulated", user_root)
    assert out["id"] == "simulated"
    assert not (user_root / "simulated").exists()
    with pytest.raises(PluginError):
        remove_plugin("simulated", user_root)                          # 已经没了


def test_plugin_install_from_zip(tmp_path):
    import zipfile
    user_root = tmp_path / "user-drivers"
    zpath = tmp_path / "simulated.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in (REPO / "drivers" / "simulated").rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(REPO / "drivers"))
    info = install_plugin(zpath, user_root)
    assert info["id"] == "simulated"
    assert (user_root / "simulated" / "driver.py").is_file()


def test_plugin_rejects_zip_slip(tmp_path):
    import zipfile
    user_root = tmp_path / "user-drivers"
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../escaped.txt", "x")
        zf.writestr("driver.yaml", "id: evil\napi: 1\nentry: driver:Driver\n")
    with pytest.raises(PluginError):
        install_plugin(zpath, user_root)
    assert not (tmp_path / "escaped.txt").exists()


def test_install_skips_git_and_cache_dirs(tmp_path):
    """从版本库目录装插件时，别把 .git / __pycache__ 拷进用户目录（只读对象会导致下次删除失败）。"""
    src = tmp_path / "src" / "simulated"
    import shutil
    shutil.copytree(REPO / "drivers" / "simulated", src)
    (src / ".git" / "objects").mkdir(parents=True, exist_ok=True)
    (src / ".git" / "objects" / "x").write_text("readonly-ish", encoding="utf-8")
    (src / "__pycache__").mkdir(exist_ok=True)
    (src / "__pycache__" / "y.pyc").write_text("x", encoding="utf-8")

    user_root = tmp_path / "user"
    install_plugin(src, user_root)
    assert not (user_root / "simulated" / ".git").exists()
    # __pycache__ 会在“装完即时校验”那一步由 import 生成，不算复制进来的
    assert not any(p.name == "y.pyc" for p in (user_root / "simulated").rglob("*.pyc"))
    # 覆盖安装（--force）应该能顺利删掉旧副本
    install_plugin(src, user_root, force=True)


def test_min_core_blocks_too_new_plugin(tmp_path):
    d = tmp_path / "future" / "driver.js"
    d.parent.mkdir(parents=True)
    (d.parent / "driver.yaml").write_text(
        "id: future\nname: 未来插件\nversion: 9.0.0\napi: 1\nentry: driver:Driver\nmin_core: 99.0.0\n",
        encoding="utf-8")
    d.write_text("from lcdmuse.lcd_comm import LcdComm\n\n\nclass Driver(LcdComm):\n    def open(self):\n"
                 "        raise NotImplementedError\n", encoding="utf-8")
    plugin = load_plugins(tmp_path, origin="user")[0]
    assert not plugin.available
    assert "核心" in plugin.reason


# ── 推帧循环 / 托盘图标 ─────────────────────────────────────────────────
def _cfg(tmp_path):
    from lcdmuse.config import Config
    cfg = Config()
    cfg.drivers_dir = REPO / "drivers"
    cfg.user_drivers_dir = tmp_path / "user-drivers"
    cfg.pets_dir = REPO / "pets"
    cfg.fonts_dir = REPO / "res" / "fonts"
    cfg.out_dir = tmp_path / "out"
    cfg.drivers = {"simulated": {"out_dir": str(tmp_path / "out")}}
    return cfg


def test_player_pushes_frames(tmp_path):
    import time
    p = Player(_cfg(tmp_path), "simulated", interval=0.1, pet=None)
    p.start()
    time.sleep(1.0)
    p.stop()
    assert p.status.frames >= 2, p.status
    assert p.status.last_error == ""
    assert p.status.size == (960, 480)


def test_player_pause_stops_frames(tmp_path):
    import time
    p = Player(_cfg(tmp_path), "simulated", interval=0.1, pet=None)
    p.start()
    time.sleep(0.5)
    p.pause()
    time.sleep(0.2)
    frames = p.status.frames
    time.sleep(0.6)
    p.stop()
    assert p.status.frames == frames, "暂停后不该继续推帧"


def test_player_pet_toggle(tmp_path):
    p = Player(_cfg(tmp_path), "simulated", interval=1, pet="whale-girl")
    assert p.pet_name == "whale-girl"
    assert p.toggle_pet() is None
    assert p.toggle_pet() == "whale-girl"


def test_tray_icon_is_valid_image():
    from lcdmuse.tray import make_icon
    img = make_icon(32)
    assert img.size == (32, 32) and img.mode == "RGBA"
    assert make_icon(32, active=False, error=True).size == (32, 32)


def test_single_instance_blocks_second_copy():
    from lcdmuse.tray import SingleInstance
    first = SingleInstance("lcdmuse-test-guard")
    assert first.acquire() is True
    second = SingleInstance("lcdmuse-test-guard")
    assert second.acquire() is False
    first.release()


def test_device_name_alias_is_persisted(tmp_path):
    from lcdmuse.config import Config
    cfg = Config()
    cfg.drivers_dir = REPO / "drivers"
    cfg.user_drivers_dir = tmp_path / "user"
    cfg_path = tmp_path / "lcdmuse.yaml"
    cfg_path.write_text("theme: x\ndrivers: {}\n", encoding="utf-8")

    assert cfg.name_for("sama_s300", "TXW818") == "TXW818"
    cfg.set_device_name("sama_s300", "我的水冷屏", cfg_path)
    assert cfg.name_for("sama_s300", "TXW818") == "我的水冷屏"

    again = Config.load(cfg_path)
    assert again.name_for("sama_s300") == "我的水冷屏"
    # 改回空 = 删掉自定义
    again.set_device_name("sama_s300", "", cfg_path)
    assert Config.load(cfg_path).name_for("sama_s300", "TXW818") == "TXW818"
