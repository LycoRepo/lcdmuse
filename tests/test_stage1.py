"""阶段 1 的验收测试：契约、插件、渲染、桌宠、以及"核心零硬件"。"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lcdpilot.lcd_comm import (DeviceInfo, Frame, LcdComm, Unsupported,  # noqa: E402
                            DRIVER_API_VERSION, InvalidArgument)
from lcdpilot.plugin import load_plugins  # noqa: E402
from lcdpilot.registry import Registry  # noqa: E402
from lcdpilot.render.canvas import Canvas  # noqa: E402
from lcdpilot.pet import Pet, list_pets  # noqa: E402


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
        from lcdpilot.lcd_comm import Capability, DeviceInfo, LcdComm

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
    for path in (REPO / "lcdpilot").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(pattern, line, re.I):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()[:80]}")
    assert offenders == [], f"{name} 泄漏到核心: {offenders[:5]}"
