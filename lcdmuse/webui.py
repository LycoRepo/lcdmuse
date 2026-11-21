# SPDX-License-Identifier: GPL-3.0-or-later
"""本地 Web 控制台：一个 127.0.0.1 上的小服务，界面就是设计稿那套 HTML。

为什么不用 tkinter：副屏控制台是 **Operate** 界面（选设备、看状态、按按钮），
用 HTML/CSS 才能把排版、层级、数字对齐做准；Tk 的控件系统撑不起这种密度。

只用标准库 http.server，不引第三方 web 框架；界面单文件内嵌 CSS/JS，
数据走 /api/*，预览图直接回 PNG。
"""
from __future__ import annotations

import io
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lcdmuse import __version__
from lcdmuse.config import Config, log

if TYPE_CHECKING:                                     # pragma: no cover
    from lcdmuse.player import Player


def _state(players: list[Player], cfg: Config, selected: int) -> dict[str, Any]:
    devices = []
    for index, player in enumerate(players):
        st = player.status
        pid = player.plugin_id or st.plugin
        devices.append({
            "index": index,
            "plugin": pid,
            "alias": cfg.name_for(pid, st.device or pid),
            "device": st.device,
            "size": list(st.size),
            "frames": st.frames,
            "fps": round(st.fps, 2),
            "paused": st.paused,
            "running": st.running,
            "error": st.last_error,
            "pet": player.pet_name,
        })
    return {"version": __version__, "devices": devices, "selected": selected,
            "config_path": str(cfg.drivers_dir.parent / "lcdmuse.yaml"),
            "user_drivers": str(cfg.user_drivers_dir)}


def _preview_png(players: list[Player], index: int) -> bytes | None:
    if not players:
        return None
    index = max(0, min(index, len(players) - 1))
    image = getattr(players[index], "last_image", None)
    if image is None:
        return None
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class _Handler(BaseHTTPRequestHandler):
    players: list[Player] = []
    cfg: Config
    selected = 0
    on_quit = None

    # 安静点：默认实现每请求打一行日志，控制台会刷屏
    def log_message(self, format: str, *args) -> None:   # noqa: A002
        log.debug("web %s", format % args)

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str) -> None:
        """界面文件从 web_root() 现读，改完刷新页面就生效，不用重启。"""
        root = web_root().resolve()
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, f"no {name}")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                          # noqa: N802
        url = urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            self._static("index.html")
            return
        if Path(url.path).suffix in _MIME:
            self._static(Path(url.path).name)
            return
        if url.path == "/api/status":
            self._json(_state(self.players, self.cfg, self.selected))
            return
        if url.path == "/api/preview":
            query = parse_qs(url.query)
            index = int(query.get("i", [str(self.selected)])[0])
            png = _preview_png(self.players, index)
            if png is None:
                self.send_error(HTTPStatus.NOT_FOUND, "no frame yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(png)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:                         # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode() or "{}")
        except Exception:
            data = {}

        if url.path == "/api/select":
            self.selected = int(data.get("index") or 0)
            return self._json({"ok": True, "selected": self.selected})

        if url.path == "/api/pause":
            want = data.get("paused")
            target = (not self.players[0].status.paused) if want is None else bool(want)
            for player in self.players:
                player.status.paused = target
            return self._json({"ok": True, "paused": target})

        if url.path == "/api/pet":
            want = data.get("enabled")
            enable = (self.players[0].pet_name is None) if want is None else bool(want)
            for player in self.players:
                player.set_pet((self.cfg.default_pet or "whale-girl") if enable else None)
            return self._json({"ok": True, "enabled": enable})

        if url.path == "/api/rename":
            index = int(data.get("index") or 0)
            index = max(0, min(index, len(self.players) - 1))
            player = self.players[index]
            pid = player.plugin_id or player.status.plugin
            self.cfg.set_device_name(pid, str(data.get("name") or ""))
            return self._json({"ok": True, "alias": self.cfg.name_for(pid, "")})

        if url.path == "/api/open":
            from lcdmuse.tray import _open_path
            from pathlib import Path
            target = "plugins" if data.get("target") == "plugins" else "logs"
            path = self.cfg.ensure_user_drivers() if target == "plugins" \
                else Path(self.cfg.out_dir).parent / "logs"
            _open_path(path)
            return self._json({"ok": True, "path": str(path)})

        if url.path == "/api/quit":
            self._json({"ok": True})
            if self.on_quit:
                threading.Thread(target=self.on_quit, daemon=True).start()
            return None

        self.send_error(HTTPStatus.NOT_FOUND)
        return None


def start_server(players: list[Player], cfg: Config, port: int = 0,
                 on_quit=None) -> tuple[ThreadingHTTPServer, int]:
    """起一个只监听 127.0.0.1 的小服务；port=0 由系统分配。"""
    handler = type("BoundHandler", (_Handler,), {"players": players, "cfg": cfg, "on_quit": on_quit})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, name="lcdmuse-web", daemon=True)
    thread.start()
    log.info("控制台：http://127.0.0.1:%d/", server.server_address[1])
    return server, server.server_address[1]


WEB_DIR = Path(__file__).resolve().parent / "web"
_MIME = {".html": "text/html; charset=utf-8",
         ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8",
         ".png": "image/png", ".svg": "image/svg+xml",
         ".woff2": "font/woff2"}


def web_root() -> Path:
    """界面文件目录。设 LCDMUSE_WEB_DIR 就整套换掉前端，不用动核心代码。"""
    override = os.environ.get("LCDMUSE_WEB_DIR")
    return Path(override).expanduser() if override else WEB_DIR
