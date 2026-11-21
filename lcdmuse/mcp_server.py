# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP 服务：给 DSH 这类外部代理用的薄封装。

设计（和 SPEC 一致）：
- 设备只允许**一个进程**持有。真正占着串口/HID 的是托盘（`lcdmuse tray`），
  所以这里不碰硬件：stdio 模式把 JSON-RPC 原样转给托盘内嵌的 `POST /mcp`。
- 托盘不在跑就直接返回 `device_offline`，并说明该怎么起，不要假装成功。
- 工具统一返回 `{ok, data, error}`；错误分 `device_offline` / `invalid_argument` / `unsupported`。
- 大图走**文件路径**，不收 base64（960×480 一帧几十 KB，反复编码不划算）。

启动方式：
- stdio（DSH 拉起子进程）：`lcdmuse mcp --stdio`
- 托盘内嵌 HTTP：`POST http://127.0.0.1:<port>/mcp`（端口写在 `%APPDATA%\\lcdmuse\\runtime.json`）
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lcdmuse import __version__
from lcdmuse.config import default_user_dir, log

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lcdmuse"


# ── 运行时信息（托盘写、MCP 读） ────────────────────────────────────────
def runtime_path() -> Path:
    return default_user_dir() / "runtime.json"


def write_runtime(port: int, pid: int | None = None) -> Path:
    path = runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid or os.getpid(), "port": port,
                                "version": __version__}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def read_runtime() -> dict[str, Any]:
    path = runtime_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def clear_runtime() -> None:
    """只删自己写的那一份：别人的托盘还在跑就别动。"""
    runtime = read_runtime()
    if runtime and runtime.get("pid") not in (None, os.getpid()):
        return
    with contextlib.suppress(OSError):
        runtime_path().unlink()


def daemon_url() -> str:
    """托盘的控制台地址；没有就返回空串。"""
    runtime = read_runtime()
    port = runtime.get("port")
    return f"http://127.0.0.1:{port}/mcp" if port else ""


# ── 错误 ────────────────────────────────────────────────────────────────
class McpError(Exception):
    def __init__(self, code: str, message: str, **data: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data: dict[str, Any] = data


def _fail(code: str, message: str, **data) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **data}}


# ── 工具实现（在托盘进程里跑，能摸到 Player） ────────────────────────────
class Toolbox:
    """把玩家（Player）包成工具。参数校验在这里做，错误码在这里定。"""

    def __init__(self, players: list[Any], cfg: Any):
        self.players = players
        self.cfg = cfg

    # 设备定位
    def _player(self, device_id: str | None) -> Any:
        if not self.players:
            raise McpError("device_offline", "没有设备在推：先跑 lcdmuse tray")
        if device_id in (None, "", "default"):
            index = 0
        else:
            ids = [p.plugin_id for p in self.players]
            if device_id in ids:
                index = ids.index(device_id)
            elif str(device_id).isdigit() and int(device_id) < len(self.players):
                index = int(device_id)
            else:
                raise McpError("invalid_argument", f"没有这个设备：{device_id}", available=ids)
        player = self.players[index]
        if player.status.last_error and not player.status.running:
            raise McpError("device_offline", player.status.last_error, device=player.plugin_id)
        return player

    def _driver(self, player: Any) -> Any:
        driver = getattr(player, "_driver", None)
        if driver is None:
            raise McpError("device_offline", "设备没打开（驱动还没连上）", device=player.plugin_id)
        return driver

    @staticmethod
    def _check_file(path: str) -> Path:
        if not path:
            raise McpError("invalid_argument", "要传文件路径（不接受 base64）")
        p = Path(path).expanduser()
        if not p.is_file():
            raise McpError("invalid_argument", f"文件不存在：{p}")
        return p

    # ── 查询 ────────────────────────────────────────────────────────────
    def list_devices(self, **_kw) -> dict[str, Any]:
        devices = []
        for index, player in enumerate(self.players):
            st = player.status
            pid = player.plugin_id or st.plugin
            devices.append({
                "index": index, "id": pid, "device": st.device,
                "name": self.cfg.name_for(pid, st.device or pid) if hasattr(self.cfg, "name_for") else st.device,
                "size": list(st.size), "running": st.running, "paused": st.paused,
                "frames": st.frames, "fps": round(st.fps, 2), "theme": getattr(player, "theme_id", ""),
                "pet": player.pet_name, "error": st.last_error,
            })
        return {"ok": True, "data": {"devices": devices}}

    def device_info(self, device_id: str | None = None, **_kw) -> dict[str, Any]:
        player = self._player(device_id)
        st = player.status
        return {"ok": True, "data": {
            "id": player.plugin_id, "device": st.device, "size": list(st.size),
            "running": st.running, "paused": st.paused, "frames": st.frames, "fps": round(st.fps, 2),
            "theme": getattr(player, "theme_id", ""), "pet": player.pet_name, "error": st.last_error,
        }}

    def status(self, device_id: str | None = None, **_kw) -> dict[str, Any]:
        player = self._player(device_id)
        data = self.device_info(device_id)["data"]
        size = tuple(player.status.size)
        data["compose"] = player.composition(*size).describe() if size and all(size) else None
        return {"ok": True, "data": data}

    def metrics(self, names: list[str] | None = None, **_kw) -> dict[str, Any]:
        from lcdmuse.metrics import CATALOG, read
        values = read()
        if names:
            values = {k: v for k, v in values.items() if k in names}
        return {"ok": True, "data": {"values": values,
                                     "catalog": [{"name": k, "label": v[0], "unit": v[1]}
                                                 for k, v in CATALOG.items()]}}

    # ── 画面 ────────────────────────────────────────────────────────────
    def show_image(self, path: str, fit: str = "cover", device_id: str | None = None, **_kw) -> dict[str, Any]:
        from lcdmuse.media import MediaSource
        file = self._check_file(path)
        player = self._player(device_id)
        size = tuple(player.status.size) or (960, 480)
        try:
            media = MediaSource.open(file, size, fit if fit in ("cover", "contain", "stretch") else "cover")
        except Exception as exc:
            raise McpError("invalid_argument", f"读不了这张图：{exc}") from exc
        return self._push_media(player, media)

    def show_background(self, path: str = "", device_id: str | None = None, **_kw) -> dict[str, Any]:
        """换底图（留空=回到主题自带），主题不动。"""
        player = self._player(device_id)
        if path:
            self._check_file(path)
        player.set_background(path or "")
        self._save()
        return {"ok": True, "data": {"background": player._background_path}}

    def show_theme(self, theme: str, device_id: str | None = None, **_kw) -> dict[str, Any]:
        from lcdmuse.theme import find_theme, theme_dirs
        player = self._player(device_id)
        if theme:
            found = find_theme(theme, theme_dirs(self.cfg))
            if found is None:
                from lcdmuse.theme import list_themes
                raise McpError("invalid_argument", f"没有主题 {theme}",
                               available=[t.id for t in list_themes(theme_dirs(self.cfg))])
        player.set_theme(theme or "")
        self._save()
        return {"ok": True, "data": {"theme": player.theme_id}}

    def push_frame(self, png_path: str, device_id: str | None = None, **_kw) -> dict[str, Any]:
        from lcdmuse.lcd_comm import Frame
        from PIL import Image
        file = self._check_file(png_path)
        player = self._player(device_id)
        driver = self._driver(player)
        try:
            with Image.open(file) as im:
                frame = Frame.from_image(im.convert("RGB"))
        except Exception as exc:
            raise McpError("invalid_argument", f"不是可用的图片：{exc}") from exc
        if tuple(player.status.size) and frame.width != player.status.size[0]:
            raise McpError("invalid_argument",
                           f"图是 {frame.width}×{frame.height}，这块屏是 "
                           f"{player.status.size[0]}×{player.status.size[1]}" + "（先用 lcd_render_preview 缩放）")
        driver.push_frame(frame)
        player.status.frames += 1
        player.last_image = frame.to_image()
        return {"ok": True, "data": {"device": player.plugin_id, "bytes": len(frame.data),
                                     "size": [frame.width, frame.height]}}

    def render_preview(self, theme: str | None = None, background: str | None = None,
                       pet: str | None = None, width: int | None = None, height: int | None = None,
                       out_png: str | None = None, device_id: str | None = None, **_kw) -> dict[str, Any]:
        """不接硬件渲染一帧：给 DSH 先看效果，再决定推不推。"""
        from lcdmuse.compose import Composition
        from lcdmuse.media import MediaSource
        from lcdmuse.pet import find_pet, pet_dirs
        from lcdmuse.theme import find_theme, theme_dirs

        size = (int(width or 960), int(height or 480))
        if device_id not in (None, ""):
            with contextlib.suppress(McpError):
                size = tuple(self._player(device_id).status.size) or size
        theme_obj = find_theme(theme or self.cfg.theme, theme_dirs(self.cfg)) if (theme or self.cfg.theme) else None
        media = None
        bg = background if background is not None else (self.cfg.background or "")
        if bg:
            media = MediaSource.open(self._check_file(bg), size)
        pet_name = pet if pet is not None else self.cfg.default_pet
        pet_obj = find_pet(pet_name, pet_dirs(self.cfg)) if pet_name not in (None, "", "none", "off") else None
        image = Composition(theme=theme_obj, pet=pet_obj, background=media,
                            font_dir=Path(self.cfg.fonts_dir)).render(size)
        target = Path(out_png).expanduser() if out_png else (Path(self.cfg.out_dir) / "mcp-preview.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return {"ok": True, "data": {"png": str(target.resolve()), "size": list(image.size),
                                     "theme": theme_obj.id if theme_obj else None,
                                     "pet": pet_obj.name if pet_obj else None}}

    # ── 硬件参数 ────────────────────────────────────────────────────────
    def set_brightness(self, percent: int, device_id: str | None = None, **_kw) -> dict[str, Any]:
        try:
            value = int(percent)
        except (TypeError, ValueError) as exc:
            raise McpError("invalid_argument", "percent 要是 0-100 的整数") from exc
        if not 0 <= value <= 100:
            raise McpError("invalid_argument", "percent 超出 0-100")
        player = self._player(device_id)
        return self._call_hw(self._driver(player).set_brightness, value, player, "brightness", value)

    def set_rotation(self, degrees: int, device_id: str | None = None, **_kw) -> dict[str, Any]:
        try:
            value = int(degrees) % 360
        except (TypeError, ValueError) as exc:
            raise McpError("invalid_argument", "degrees 要是 0/90/180/270") from exc
        if value not in (0, 90, 180, 270):
            raise McpError("invalid_argument", "degrees 只支持 0/90/180/270")
        player = self._player(device_id)
        return self._call_hw(self._driver(player).set_rotation, value, player, "rotation", value)

    # ── 桌宠 ────────────────────────────────────────────────────────────
    def pet_list(self, **_kw) -> dict[str, Any]:
        from lcdmuse.pet import list_pets, pet_dirs
        return {"ok": True, "data": {"pets": list_pets(pet_dirs(self.cfg)),
                                     "active": self.players[0].pet_name if self.players else None}}

    def pet_set(self, pet: str | None = None, enabled: bool | None = None,
                device_id: str | None = None, **_kw) -> dict[str, Any]:
        player = self._player(device_id)
        name = player.pet_name
        if enabled is False or (pet in ("", "none", "off")):
            name = None
        else:
            name = pet or name or (self.cfg.default_pet or "whale-girl")
        player.set_pet(name)
        return {"ok": True, "data": {"pet": player.pet_name, "enabled": player.pet_name is not None}}

    def pet_state(self, state: str, device_id: str | None = None, **_kw) -> dict[str, Any]:
        player = self._player(device_id)
        pet = player._pet_obj()
        if pet is None:
            raise McpError("invalid_argument", "桌宠没开（先 lcd_pet_set）")
        try:
            pet.set_state(state)
        except Exception as exc:
            raise McpError("invalid_argument", str(exc), available=pet.state_names) from exc
        return {"ok": True, "data": {"state": pet.state}}

    def pet_say(self, text: str, device_id: str | None = None, **_kw) -> dict[str, Any]:
        """气泡文字：先切到 say 状态（素材自带文字则忽略 text），返回实际状态。"""
        player = self._player(device_id)
        pet = player._pet_obj()
        if pet is None:
            raise McpError("invalid_argument", "桌宠没开（先 lcd_pet_set）")
        target = "say" if "say" in pet.states else pet.state
        pet.set_state(target)
        return {"ok": True, "data": {"state": target, "text": text or "",
                                     "note": "当前素材是预渲染帧，文字不会画上去"}}

    # ── 内部 ────────────────────────────────────────────────────────────
    def _call_hw(self, fn: Callable[[int], Any], value: int, player: Any, key: str, raw: int) -> dict[str, Any]:
        from lcdmuse.lcd_comm import LcdError, Unsupported
        try:
            fn(value)
        except Unsupported as exc:
            raise McpError("unsupported", str(exc.message if hasattr(exc, "message") else exc),
                           device=player.plugin_id) from exc
        except LcdError as exc:
            raise McpError("device_offline", getattr(exc, "message", str(exc)),
                           device=player.plugin_id) from exc
        return {"ok": True, "data": {key: raw, "device": player.plugin_id}}

    def _push_media(self, player: Any, media: Any) -> dict[str, Any]:
        from lcdmuse.lcd_comm import Frame
        driver = self._driver(player)
        image = media.frame().convert("RGB")
        frame = Frame.from_image(image)
        driver.push_frame(frame)
        player.status.frames += 1
        player.last_image = image
        return {"ok": True, "data": {"device": player.plugin_id, "size": list(image.size),
                                     "kind": media.kind, "frames_total": player.status.frames}}

    def _save(self) -> None:
        with contextlib.suppress(Exception):
            self.cfg.save()


# ── 工具清单（tools/list 用） ───────────────────────────────────────────
_S = {"type": "string"}
_I = {"type": "integer"}

TOOLS: list[dict[str, Any]] = [
    {"name": "lcd_list_devices", "description": "列出所有在推的副屏（id、分辨率、帧率、当前主题/桌宠）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "lcd_device_info", "description": "单块屏的详情",
     "inputSchema": {"type": "object", "properties": {"device_id": _S}}},
    {"name": "lcd_status", "description": "设备状态 + 当前合成（主题/桌宠/底图）",
     "inputSchema": {"type": "object", "properties": {"device_id": _S}}},
    {"name": "lcd_metrics", "description": "当前指标（cpu.percent 等，主题绑的就是这些名字）",
     "inputSchema": {"type": "object", "properties": {"names": {"type": "array", "items": _S}}}},
    {"name": "lcd_show_image", "description": "把一张图（或 GIF/视频）铺到屏上；只收文件路径，不收 base64",
     "inputSchema": {"type": "object", "required": ["path"], "properties": {
         "path": _S, "fit": {"type": "string", "enum": ["cover", "contain", "stretch"]}, "device_id": _S}}},
    {"name": "lcd_show_background", "description": "换底图（传空字符串=回到主题自带），主题与桌宠保留",
     "inputSchema": {"type": "object", "properties": {"path": _S, "device_id": _S}}},
    {"name": "lcd_show_theme", "description": "切换主题插件（传空字符串=用测试卡）",
     "inputSchema": {"type": "object", "required": ["theme"], "properties": {"theme": _S, "device_id": _S}}},
    {"name": "lcd_render_preview", "description": "不接硬件渲染一帧 PNG，先看效果再决定推不推",
     "inputSchema": {"type": "object", "properties": {
         "theme": _S, "background": _S, "pet": _S, "width": _I, "height": _I,
         "out_png": _S, "device_id": _S}}},
    {"name": "lcd_push_frame", "description": "把现成的 PNG 直接推上屏（尺寸必须和屏一致）",
     "inputSchema": {"type": "object", "required": ["png_path"], "properties": {"png_path": _S, "device_id": _S}}},
    {"name": "lcd_set_brightness", "description": "亮度 0-100（驱动不支持会返回 unsupported）",
     "inputSchema": {"type": "object", "required": ["percent"], "properties": {"percent": _I, "device_id": _S}}},
    {"name": "lcd_set_rotation", "description": "旋转 0/90/180/270",
     "inputSchema": {"type": "object", "required": ["degrees"], "properties": {"degrees": _I, "device_id": _S}}},
    {"name": "lcd_pet_list", "description": "有哪些桌宠插件，当前用哪个",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "lcd_pet_set", "description": "开/关/换桌宠：enabled=false 关掉，pet 传名字换一只",
     "inputSchema": {"type": "object", "properties": {
         "pet": _S, "enabled": {"type": "boolean"}, "device_id": _S}}},
    {"name": "lcd_pet_state", "description": "切桌宠状态（idle/eat/sleep/click/say）",
     "inputSchema": {"type": "object", "required": ["state"], "properties": {"state": _S, "device_id": _S}}},
    {"name": "lcd_pet_say", "description": "让桌宠说话（当前素材是预渲染帧，只切到 say 状态）",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": _S, "device_id": _S}}},
]

_METHODS: dict[str, str] = {
    "lcd_list_devices": "list_devices",
    "lcd_device_info": "device_info",
    "lcd_status": "status",
    "lcd_metrics": "metrics",
    "lcd_show_image": "show_image",
    "lcd_show_background": "show_background",
    "lcd_show_theme": "show_theme",
    "lcd_render_preview": "render_preview",
    "lcd_push_frame": "push_frame",
    "lcd_set_brightness": "set_brightness",
    "lcd_set_rotation": "set_rotation",
    "lcd_pet_list": "pet_list",
    "lcd_pet_set": "pet_set",
    "lcd_pet_state": "pet_state",
    "lcd_pet_say": "pet_say",
}


# ── JSON-RPC 分发（托盘里跑，也能单测） ─────────────────────────────────
def handle(request: dict[str, Any], toolbox: Toolbox) -> dict[str, Any] | None:
    """处理一条 MCP 请求，返回响应；通知（没有 id）返回 None。"""
    req_id = request.get("id")
    method = request.get("method") or ""
    params = request.get("params") or {}

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION,
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "version": __version__}})
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = dict(params.get("arguments") or {})
        target = _METHODS.get(name)
        if target is None:
            return ok({"content": [{"type": "text", "text": json.dumps(
                _fail("invalid_argument", f"没有这个工具：{name}"), ensure_ascii=False)}],
                "isError": True})
        try:
            payload = getattr(toolbox, target)(**args)
        except McpError as exc:
            payload = _fail(exc.code, exc.message, **exc.data)
        except TypeError as exc:                       # 参数名写错
            payload = _fail("invalid_argument", f"参数不对：{exc}")
        except Exception as exc:                       # 兜底：别把栈甩给代理
            log.warning("MCP 工具 %s 出错: %s", name, exc)
            payload = _fail("internal_error", str(exc))
        return ok({"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                   "isError": not payload.get("ok", False), "structuredContent": payload})
    return err(-32601, f"不支持的方法：{method}")


# ── stdio 传输：转给托盘 ────────────────────────────────────────────────
def _post(url: str, request: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    body = json.dumps(request, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def serve_stdio(url: str = "", stream_in=None, stream_out=None) -> int:
    """把 DSH 的每条 JSON-RPC 转给托盘；托盘没起就回 device_offline。"""
    stream_in = stream_in or sys.stdin
    stream_out = stream_out or sys.stdout
    target = url or daemon_url()
    log.info("MCP stdio 已连到 %s", target or "（托盘未运行）")
    for line in stream_in:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            stream_out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "不是合法 JSON"}}) + "\n")
            stream_out.flush()
            continue
        # 本地能答的（握手/清单）就不打扰托盘
        if request.get("method") in ("initialize", "ping", "tools/list") or "id" not in request:
            response = _local(request)
        elif not target:
            response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {
                "code": -32000,
                "message": json.dumps(_fail("device_offline",
                                            "托盘没在跑：先执行 lcdmuse tray，再让 MCP 调用工具"),
                                      ensure_ascii=False)}}
        else:
            try:
                response = _post(target, request)
            except urllib.error.URLError as exc:
                response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {
                    "code": -32000,
                    "message": json.dumps(_fail("device_offline", f"连不上托盘：{exc.reason}"),
                                          ensure_ascii=False)}}
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {
                    "code": -32603, "message": f"转发失败：{exc}"}}
        if response is not None:
            stream_out.write(json.dumps(response, ensure_ascii=False) + "\n")
            stream_out.flush()
    return 0


def _local(request: dict[str, Any]) -> dict[str, Any] | None:
    """stdio 本机就能答的请求（不需要设备）。"""
    req_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    return None
