# SPDX-License-Identifier: GPL-3.0-or-later
"""阶段 1 的验收测试：契约、插件、渲染、桌宠、以及"核心零硬件"。"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import io

import pytest
from PIL import Image as _Img

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


class _DummyPlayer:
    """只给 Web 控制台测试用：不碰硬件，状态和帧都是假的。"""

    def __init__(self, cfg, pid="fake_dev", device="FakeDevice"):
        from lcdmuse.player import PlayerStatus
        self.cfg = cfg
        self.plugin_id = pid
        self.pet_name = "whale-girl"
        self.status = PlayerStatus(plugin=pid, device=device, size=(960, 480),
                                   running=True, frames=7, fps=1.0)
        from PIL import Image as _Img
        self.last_image = _Img.new("RGB", (960, 480), (10, 20, 30))
        self.theme_id = cfg.theme
        self._background_path = ""

    def set_pet(self, name):
        self.pet_name = name or None
        self.status.pet = self.pet_name

    def set_theme(self, theme_id):
        self.theme_id = theme_id or ""
        return self.theme_id

    def set_background(self, path):
        self._background_path = path or ""
        return self._background_path


def test_web_control_console_serves_state_and_preview(tmp_path):
    import json
    import urllib.request

    from PIL import Image as _Image

    from lcdmuse.config import Config
    from lcdmuse.webui import start_server

    yaml_path = tmp_path / "lcdmuse.yaml"
    yaml_path.write_text("theme: x\n", encoding="utf-8")

    cfg = Config()
    cfg.path = yaml_path
    cfg.user_drivers_dir = tmp_path / "drivers"
    player = _DummyPlayer(cfg)
    server, port = start_server([player], cfg)

    try:
        base = f"http://127.0.0.1:{port}"
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "lcdmuse" in page and "屏幕预览" in page

        state = json.loads(urllib.request.urlopen(base + "/api/status", timeout=5).read())
        assert state["devices"][0]["plugin"] == "fake_dev"
        assert state["devices"][0]["size"] == [960, 480]
        assert state["devices"][0]["frames"] == 7

        png = urllib.request.urlopen(base + "/api/preview?i=0", timeout=5).read()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert _Image.open(io.BytesIO(png)).size == (960, 480)

        def post(path, payload):
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=5).read())

        assert post("/api/pause", {})["paused"] is True
        assert player.status.paused is True
        assert post("/api/pause", {"paused": False})["paused"] is False
        assert post("/api/pet", {"enabled": False})["enabled"] is False
        assert player.pet_name is None

        assert post("/api/rename", {"index": 0, "name": "机箱大屏"})["alias"] == "机箱大屏"
        assert Config.load(yaml_path).name_for("fake_dev") == "机箱大屏"
    finally:
        server.shutdown()


def test_web_ui_is_plain_files_and_swappable(tmp_path, monkeypatch):
    """界面就是 web/ 下的三个文件：改文件刷新即生效，换目录即换整套前端。"""
    import urllib.request

    from lcdmuse.config import Config
    from lcdmuse.webui import start_server, web_root

    # 默认目录里确实是分开的静态文件
    root = web_root()
    assert (root / "index.html").is_file()
    assert (root / "app.css").is_file()
    assert (root / "app.js").is_file()
    assert "<style>" not in (root / "index.html").read_text(encoding="utf-8")

    # 换成本地一份皮肤：后端代码不动，页面跟着变
    skin = tmp_path / "skin"
    skin.mkdir()
    (skin / "index.html").write_text("<!doctype html><title>my skin</title>自定义", encoding="utf-8")
    (skin / "app.css").write_text("body{color:hotpink}", encoding="utf-8")
    (skin / "app.js").write_text("console.log('hi')", encoding="utf-8")
    monkeypatch.setenv("LCDMUSE_WEB_DIR", str(skin))

    cfg = Config()
    cfg.user_drivers_dir = tmp_path / "drivers"
    server, port = start_server([_DummyPlayer(cfg)], cfg)
    try:
        base = f"http://127.0.0.1:{port}"
        assert "自定义" in urllib.request.urlopen(base + "/", timeout=5).read().decode()
        css = urllib.request.urlopen(base + "/app.css", timeout=5)
        assert css.headers["Content-Type"].startswith("text/css")
        assert "hotpink" in css.read().decode()

        # 改文件 → 刷新就变（不重启进程）
        (skin / "app.css").write_text("body{color:teal}", encoding="utf-8")
        assert "teal" in urllib.request.urlopen(base + "/app.css", timeout=5).read().decode()

        # 越界路径不放行
        try:
            urllib.request.urlopen(base + "/../webui.py", timeout=5)
            raise AssertionError("路径穿越没挡住")
        except Exception as exc:
            assert "404" in str(exc) or "403" in str(exc) or "HTTP Error" in str(exc)
    finally:
        server.shutdown()


# ── 三类插件：主题 / 桌宠 / 数据接口 ─────────────────────────────────────
def _tmp_white(tmp_path):
    path = tmp_path / "white.png"
    _Img.new("RGB", (960, 480), (255, 255, 255)).save(path)
    return path


def _mean(image):
    data = list(image.getdata())
    return sum(data) / len(data)


def test_metrics_interface_has_stable_names():
    from lcdmuse.metrics import CATALOG, format_value, get_hub, read

    values = read()
    for key in ("time.now", "date.today", "cpu.percent", "mem.percent", "disk.percent",
                "net.rx_kbps", "uptime.hours"):
        assert key in values, f"缺指标 {key}"
    assert set(CATALOG) >= {"cpu.percent", "mem.percent", "gpu.percent"}

    # 第三方可以挂自己的指标
    hub = get_hub()
    hub.register("_test", lambda: {"queue": 7})
    assert hub.sample()["_test.queue"] == 7

    assert format_value(12.34, unit="%", decimals=1) == "12.3%"
    assert format_value(80, unit="%") == "80%"
    assert format_value(None, prefix="运行 ", unit=" h") == "运行 None h"


def test_media_background_image_and_gif(tmp_path):
    from lcdmuse.media import MediaSource, fit_image

    still = tmp_path / "still.png"
    _Img.new("RGB", (400, 200), (200, 30, 30)).save(still)
    src = MediaSource.open(still, (960, 480))
    assert src.kind == "image" and not src.animated
    assert src.frame().size == (960, 480)

    # cover：短边贴满，长边裁掉
    fitted = fit_image(_Img.new("RGB", (400, 200), (0, 0, 0)), (100, 100), "cover")
    assert fitted.size == (100, 100)

    gif = tmp_path / "anim.gif"
    frames = [_Img.new("RGB", (120, 60), c) for c in ((250, 0, 0), (0, 250, 0), (0, 0, 250))]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
    anim = MediaSource.open(gif, (240, 120))
    assert anim.kind == "anim" and anim.animated
    a, b = anim.frame(0.02), anim.frame(0.25)
    assert a.size == (240, 120) and a.tobytes() != b.tobytes()


def test_theme_plugin_mask_and_metrics(tmp_path):
    from lcdmuse.media import MediaSource
    from lcdmuse.metrics import read
    from lcdmuse.theme import find_theme, list_themes, theme_dirs

    themes = {t.id: t for t in list_themes(theme_dirs())}
    assert "bezel-mask" in themes
    theme = find_theme("bezel-mask")
    assert theme is not None and theme.size == (960, 480)

    values = dict(read())
    values.update({"cpu.percent": 100.0, "mem.percent": 50.0, "time.now": "12:34"})
    img = theme.render((960, 480), metrics=values)
    assert img.size == (960, 480)

    # 蒙版确实压上去了：换成纯白底图后，面板内部应该明显比面板外暗
    white = MediaSource.open(_tmp_white(tmp_path), (960, 480))
    shaded = theme.render((960, 480), metrics=values, media=white)
    inside = shaded.crop((700, 150, 860, 340)).convert("L")
    outside = shaded.crop((330, 150, 560, 340)).convert("L")
    assert _mean(inside) < _mean(outside) - 60

    # 换个分辨率也能出图（实际分辨率由驱动握手决定）
    assert theme.render((480, 240), metrics=values).size == (480, 240)


def test_pet_plugins_are_swappable_and_versioned(tmp_path):
    import json

    from lcdmuse.lcd_comm import InvalidArgument
    from lcdmuse.pet import find_pet, list_pets, pet_dirs

    dirs = pet_dirs()
    assert "whale-girl" in list_pets(dirs)
    assert find_pet("whale-girl", dirs) is not None

    # 用户目录里放一份同名桌宠 → 覆盖内置
    user = tmp_path / "pets"
    (user / "whale-girl" / "frames").mkdir(parents=True)
    frame = user / "whale-girl" / "frames" / "idle.png"
    _Img.new("RGBA", (64, 64), (0, 255, 0, 255)).save(frame)
    (user / "whale-girl" / "pet.json").write_text(json.dumps({
        "api": 1, "name": "whale-girl", "display_name": "我的鲸鱼娘",
        "anchor": "bottom-left", "margin": 8,
        "states": {"idle": {"frames": ["frames/idle.png"], "fps": 4}},
    }), encoding="utf-8")
    found = find_pet("whale-girl", [dirs[0], user])
    assert found.display_name == "我的鲸鱼娘"

    # 版本不对要明确拒绝
    (user / "bad" / "frames").mkdir(parents=True)
    (user / "bad" / "pet.json").write_text(json.dumps({
        "api": 99, "states": {"idle": {"frames": []}}}), encoding="utf-8")
    with pytest.raises(InvalidArgument):
        find_pet("bad", [user])


def test_composition_stacks_background_theme_and_pet():
    from lcdmuse.compose import Composition
    from lcdmuse.metrics import read
    from lcdmuse.pet import find_pet
    from lcdmuse.theme import find_theme

    comp = Composition(theme=find_theme("bezel-mask"), pet=find_pet("whale-girl"),
                       font_dir=REPO / "res" / "fonts")
    img = comp.render((960, 480), metrics=read())
    assert img.size == (960, 480)
    assert comp.describe()["theme"] == "bezel-mask"
    assert comp.describe()["pet"] == "whale-girl"

    # 只有主题、没有桌宠也能出图
    plain = Composition(theme=find_theme("bezel-mask")).render((960, 480), metrics=read())
    assert plain.size == (960, 480)


# ── MCP：工具清单 / 参数校验 / 错误码 / 转发端点 ─────────────────────────
def test_mcp_tools_and_error_codes(tmp_path):
    import json as _json

    from lcdmuse.config import Config
    from lcdmuse.mcp_server import TOOLS, Toolbox, handle

    cfg = Config()
    cfg.user_drivers_dir = tmp_path / "drivers"
    cfg.user_themes_dir = tmp_path / "themes"
    cfg.user_pets_dir = tmp_path / "pets"
    cfg.out_dir = tmp_path / "out"
    player = _DummyPlayer(cfg)
    box = Toolbox([player], cfg)

    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, box)
    assert init["result"]["serverInfo"]["name"] == "lcdmuse"

    listing = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, box)
    names = {t["name"] for t in listing["result"]["tools"]}
    assert {"lcd_list_devices", "lcd_show_image", "lcd_show_theme", "lcd_render_preview",
            "lcd_set_brightness", "lcd_pet_set", "lcd_metrics"} <= names
    assert len(listing["result"]["tools"]) == len(TOOLS)

    def call(name, **args):
        reply = handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                        "params": {"name": name, "arguments": args}}, box)
        return reply["result"], _json.loads(reply["result"]["content"][0]["text"])

    result, payload = call("lcd_list_devices")
    assert payload["ok"] and payload["data"]["devices"][0]["size"] == [960, 480]
    assert result["isError"] is False

    # 只收文件路径，且文件必须存在
    result, payload = call("lcd_show_image", path="")
    assert payload["ok"] is False and payload["error"]["code"] == "invalid_argument"
    result, payload = call("lcd_show_image", path=str(tmp_path / "nope.png"))
    assert payload["error"]["code"] == "invalid_argument"
    assert result["isError"] is True

    # 没有驱动 → device_offline（不是崩）
    _, payload = call("lcd_set_brightness", percent=50)
    assert payload["error"]["code"] == "device_offline"
    _, payload = call("lcd_set_brightness", percent=500)
    assert payload["error"]["code"] == "invalid_argument"

    # 桌宠：关掉/换一只
    _, payload = call("lcd_pet_set", enabled=False)
    assert payload["ok"] and payload["data"]["enabled"] is False and player.pet_name is None
    _, payload = call("lcd_pet_set", pet="whale-girl")
    assert payload["data"]["enabled"] is True and player.pet_name == "whale-girl"

    # 未知工具 / 未知方法
    _, payload = call("lcd_not_a_tool")
    assert payload["error"]["code"] == "invalid_argument"
    bad = handle({"jsonrpc": "2.0", "id": 3, "method": "no/such"}, box)
    assert bad["error"]["code"] == -32601


def test_mcp_render_preview_and_web_endpoint(tmp_path):
    import json as _json
    import urllib.request

    from lcdmuse.config import Config
    from lcdmuse.mcp_server import Toolbox, read_runtime
    from lcdmuse.webui import start_server

    cfg = Config()
    cfg.user_drivers_dir = tmp_path / "drivers"
    cfg.user_themes_dir = tmp_path / "themes"
    cfg.user_pets_dir = tmp_path / "pets"
    cfg.out_dir = tmp_path / "out"
    cfg.theme = "bezel-mask"
    cfg.pet_enabled = True
    cfg.default_pet = "whale-girl"
    player = _DummyPlayer(cfg)

    # 离线渲染：不接硬件也能出 PNG
    out = tmp_path / "preview.png"
    payload = Toolbox([player], cfg).render_preview(theme="bezel-mask", pet="whale-girl",
                                                    width=960, height=480, out_png=str(out))
    assert payload["ok"] and out.is_file()
    assert _Img.open(out).size == (960, 480)

    server, port = start_server([player], cfg, publish_runtime=True)
    try:
        base = f"http://127.0.0.1:{port}"
        runtime = read_runtime()
        assert runtime.get("port") == port

        def rpc(method, params=None, req_id=1):
            req = urllib.request.Request(base + "/mcp",
                                         data=_json.dumps({"jsonrpc": "2.0", "id": req_id,
                                                           "method": method,
                                                           "params": params or {}}).encode(),
                                         headers={"Content-Type": "application/json"})
            return _json.loads(urllib.request.urlopen(req, timeout=10).read())

        assert rpc("tools/list")["result"]["tools"]
        reply = rpc("tools/call", {"name": "lcd_show_theme", "arguments": {"theme": "bezel-mask"}})
        inner = _json.loads(reply["result"]["content"][0]["text"])
        assert inner["ok"] and inner["data"]["theme"] == "bezel-mask"

        reply = rpc("tools/call", {"name": "lcd_metrics", "arguments": {"names": ["cpu.percent"]}})
        inner = _json.loads(reply["result"]["content"][0]["text"])
        assert list(inner["data"]["values"]) == ["cpu.percent"]
    finally:
        server.shutdown()


# ── 精灵图桌宠 ────────────────────────────────────────────────────────────
def test_pet_loads_spritesheet_and_slices_rows(tmp_path):
    """精灵图格式：pet.json 里给 spritesheet + 每个状态给 row，加载器按格裁。"""
    import json
    sheet = _Img.new("RGBA", (4 * 8, 2 * 8), (0, 0, 0, 0))
    for col in range(4):                       # 第 0 行画红块，第 1 行画绿块
        sheet.paste((255, 0, 0, 255), (col * 8, 0, (col + 1) * 8, 8))
        sheet.paste((0, 255, 0, 255), (col * 8, 8, (col + 1) * 8, 16))
    root = tmp_path / "sheetpet"
    root.mkdir()
    sheet.save(root / "sheet.webp")
    (root / "pet.json").write_text(json.dumps({
        "name": "sheetpet", "display_name": "格子测试", "api": 1, "kind": "pet",
        "spritesheet": {"path": "sheet.webp", "columns": 4, "rows": 2},
        "states": {"idle": {"row": 0, "frames": 4, "fps": 8}, "eat": {"row": 1, "frames": 4, "fps": 8}},
    }), encoding="utf-8")

    pet = Pet.load(root)
    assert pet.frame_size == (8, 8)
    assert sorted(pet.state_names) == ["eat", "idle"]
    idle = pet.frame_at(0.0).convert("RGB")
    assert idle.size == (8, 8)
    assert idle.getpixel((4, 4))[0] > 200 and idle.getpixel((4, 4))[1] < 60     # 红
    pet.set_state("eat", now=0.0)
    eat = pet.frame_at(0.0).convert("RGB")
    assert eat.getpixel((4, 4))[1] > 200 and eat.getpixel((4, 4))[0] < 60       # 绿
    # 帧会随时间推进（8fps → 0.2s 后换一格）
    assert pet.frame_at(0.2) is not None


def test_pet_step_mode_advances_exactly_one_frame_per_push(tmp_path):
    """按推帧步进取帧：第 n 次推帧必须是第 n 帧，不能因为墙钟采样乱跳。"""
    import json
    sheet = _Img.new("RGBA", (4 * 8, 8), (0, 0, 0, 0))
    for col in range(4):                       # 每格颜色不同，便于判断取到第几帧
        sheet.paste((col * 60, 0, 0, 255), (col * 8, 0, (col + 1) * 8, 8))
    root = tmp_path / "steppet"
    root.mkdir()
    sheet.save(root / "sheet.webp")
    (root / "pet.json").write_text(json.dumps({
        "name": "steppet", "kind": "pet", "api": 1,
        "spritesheet": {"path": "sheet.webp", "columns": 4, "rows": 1},
        "states": {"idle": {"row": 0, "frames": 4, "fps": 8}},
    }), encoding="utf-8")

    pet = Pet.load(root)
    assert pet.micro_motion is False           # 精灵图默认不叠加抖动
    for step in range(6):                       # 不给时间 → 按推帧步进取帧
        px = pet.frame_at(step=step).convert("RGB").getpixel((4, 4))
        assert abs(px[0] - (step % 4) * 60) <= 8, f"第 {step} 次推帧取到了 {px}"   # webp 有损，留容差


def test_pet_fit_height_scales_with_canvas():
    """fit_height：同一只桌宠在 960×480 和 1480×720 上占屏比例一致（不是固定像素）。"""
    import json
    sheet = _Img.new("RGBA", (4 * 10, 20), (255, 0, 0, 255))
    root = _Canvas = None
    from pathlib import Path as _P
    import tempfile
    d = _P(tempfile.mkdtemp()) / "fitpet"
    d.mkdir()
    sheet.save(d / "sheet.webp")
    (d / "pet.json").write_text(json.dumps({
        "name": "fitpet", "kind": "pet", "api": 1, "fit_height": 0.5,
        "spritesheet": {"path": "sheet.webp", "columns": 4, "rows": 1},
        "states": {"idle": {"row": 0, "frames": 4, "fps": 4}},
    }), encoding="utf-8")
    from lcdmuse.pet import Pet
    from lcdmuse.compose import Composition
    pet = Pet.load(d)
    comp = Composition(pet=pet)
    for w, h in ((960, 480), (1480, 720)):
        fitted = comp._fit_pet(pet.frame_at(1.0, step=0), w, h, 0)
        assert abs(fitted.height - int(h * 0.5)) <= 2, f"{w}x{h} 高度 {fitted.height}"
        assert fitted.width <= int(w * 0.55) + 2


def test_roaming_pet_is_on_one_screen_at_a_time():
    """多屏巡游：同一时刻只有一块屏画桌宠，走到边缘会换到下一块屏。"""
    import json
    import tempfile
    from pathlib import Path as _P
    from lcdmuse.pet import Pet
    from lcdmuse.roam import RoamingPet

    d = _P(tempfile.mkdtemp()) / "roampet"
    d.mkdir()
    sheet = _Img.new("RGBA", (4 * 10, 20), (255, 255, 255, 255))
    sheet.save(d / "sheet.webp")
    (d / "pet.json").write_text(json.dumps({
        "name": "roampet", "kind": "pet", "api": 1, "fit_height": 0.5,
        "spritesheet": {"path": "sheet.webp", "columns": 4, "rows": 1},
        "states": {"idle": {"row": 0, "frames": 4, "fps": 4},
                    "walk": {"row": 0, "frames": 4, "fps": 4}},
    }), encoding="utf-8")

    roam = RoamingPet(pet=Pet.load(d), order=["screenA", "screenB"], speed=5.0,
                      dwell=999, min_stay=0.0)
    # 固定成「一直走路」，别让随机行为调度干扰断言
    roam._action, roam.direction = "walk", 1
    roam._action_until = roam._next_ambient = 1e18

    drawn = [roam.draw_on("screenA", 800, 400, step=0, now=10.0),
             roam.draw_on("screenB", 800, 400, step=0, now=10.0)]
    assert drawn[0] is not None and drawn[1] is None      # 只在 A 上
    assert drawn[0].sprite.size[0] <= 800 * 0.55 + 1
    # 速度 5/s：0.3 秒就走出右边缘，换到 B
    roam.draw_on("screenA", 800, 400, step=0, now=10.3)
    assert roam.current == "screenB"
    assert roam.draw_on("screenB", 800, 400, step=0, now=10.6) is not None
    assert roam.draw_on("screenA", 800, 400, step=0, now=10.6) is None
