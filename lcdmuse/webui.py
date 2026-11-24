# SPDX-License-Identifier: GPL-3.0-or-later
"""本地 Web 控制台：一个 127.0.0.1 上的小服务，界面就是设计稿那套 HTML。

为什么不用 tkinter：副屏控制台是 **Operate** 界面（选设备、看状态、按按钮），
用 HTML/CSS 才能把排版、层级、数字对齐做准；Tk 的控件系统撑不起这种密度。

只用标准库 http.server，不引第三方 web 框架；界面单文件内嵌 CSS/JS，
数据走 /api/*，预览图直接回 PNG。
"""
from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lcdmuse import __version__
from lcdmuse.config import Config, log
from lcdmuse.mcp_server import Toolbox, clear_runtime, handle as mcp_handle, write_runtime

if TYPE_CHECKING:                                     # pragma: no cover
    from lcdmuse.player import Player


_THEME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_DIALOG_LOCK = threading.Lock()


def install_theme_files(src_path: str | Path, user_root: str | Path, mode: str = "dir") -> dict[str, Any]:
    """把一个主题（文件夹或 zip）拷进用户主题目录。返回 {ok, installed} 或 {ok:False, error}。"""
    import shutil
    import tempfile
    import zipfile
    from lcdmuse.theme import list_themes, theme_dirs

    user_root = Path(user_root)
    tmp = Path(tempfile.mkdtemp(prefix="lcdmuse-theme-"))
    try:
        src = Path(src_path)
        if mode == "zip" or src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src) as zf:
                for member in zf.namelist():
                    target = (tmp / member).resolve()
                    if not str(target).startswith(str(tmp.resolve())):
                        raise ValueError(f"zip 里有非法路径：{member}")
                zf.extractall(tmp)
            found = [p for p in tmp.iterdir() if (p / "theme.yaml").exists()]
            src = found[0] if found else tmp
        if not (src / "theme.yaml").exists():
            raise ValueError("这里没有 theme.yaml，不像主题插件")
        theme_id = src.name
        with contextlib.suppress(Exception):
            import yaml
            meta = yaml.safe_load((src / "theme.yaml").read_text(encoding="utf-8")) or {}
            theme_id = str(meta.get("id") or theme_id)
        # 主题 id 只能是单层目录名：不接受路径分隔符、..、绝对路径
        if not _THEME_ID_RE.match(theme_id) or theme_id in (".", ".."):
            raise ValueError(f"主题 id 不合法: {theme_id!r}（只允许字母、数字、下划线、短横线）")
        user_root.mkdir(parents=True, exist_ok=True)
        root_resolved = user_root.resolve()
        dest = (user_root / theme_id).resolve()
        if root_resolved not in dest.parents:
            raise ValueError(f"主题目录解析到根目录之外: {dest}")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return {"ok": True, "installed": theme_id,
                "themes": [t.describe() for t in list_themes(theme_dirs())]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _active_pet(players: list[Player]) -> str | None:
    """桌宠是否真的在跑只有一个判据：巡游实例开着就报它的名字。"""
    from lcdmuse import roam as roam_mod
    roamer = roam_mod.CURRENT
    if roamer is not None:
        return roamer.pet.name if roamer.enabled else None
    return players[0].pet_name if players else None


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
            "pet": _active_pet(players),
            "theme": player.theme_id,
        })
    theme = players[0].theme_id if players else ""
    background = getattr(players[0], "_background_path", "") if players else ""
    from lcdmuse.mcp_server import TOOLS
    return {"version": __version__, "devices": devices, "selected": selected,
            "theme": theme, "background": background,
            "mcp": {"ready": bool(players), "path": "/mcp", "tools": len(TOOLS)},
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


_TOKEN = secrets.token_urlsafe(24)


def control_token() -> str:
    """本次启动生成的控制令牌：写进 runtime.json，面板和 MCP 都带它来。"""
    return _TOKEN


class _Handler(BaseHTTPRequestHandler):
    players: list[Player] = []
    cfg: Config
    selected = 0
    on_quit = None
    token: str = ""

    def _guard(self) -> bool:
        """本地控制接口的入口校验：只认本机 Host、同源 Origin、带令牌。"""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._json({"ok": False, "error": "只接受本机地址访问"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin:
            from urllib.parse import urlparse as _urlparse
            src = (_urlparse(origin).hostname or "")
            if src not in ("127.0.0.1", "localhost", "::1"):
                self._json({"ok": False, "error": f"来源不在本机: {origin}"}, 403)
                return False
        sent = self.headers.get("X-LCDMUSE-Token") or ""
        if not self.token or not secrets.compare_digest(sent, self.token):
            self._json({"ok": False, "error": "控制令牌不对（从面板或 runtime.json 取）"}, 403)
            return False
        return True

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
        if path.name == "index.html" and self.token:      # 页面拿不到令牌就没法调用控制接口
            body = body.replace(b"</head>",
                                f'<meta name="lcdmuse-token" content="{self.token}"></head>'.encode())
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ── 文件选择 / 插件安装（都要原生对话框，放在服务端做）──────────────
    def _pick_media(self) -> dict[str, Any]:
        """弹系统文件对话框选底图/视频，返回 {ok, path, cancelled, error}。"""
        if not _DIALOG_LOCK.acquire(blocking=False):
            return {"ok": False, "error": "已经有一个选择框开着", "cancelled": False}
        path, error = "", ""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                title="选一张底图 / GIF / 视频",
                filetypes=[("图片与视频", "*.png *.jpg *.jpeg *.webp *.gif *.bmp *.mp4 *.webm *.avi *.mkv"),
                           ("全部文件", "*.*")])
            root.destroy()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            _DIALOG_LOCK.release()
        return {"ok": bool(path), "path": path, "error": error, "cancelled": not path and not error}

    def _install_theme(self, data: dict[str, Any]) -> dict[str, Any]:
        """把一个主题（文件夹或 zip）装进用户主题目录，返回安装结果和最新清单。"""
        mode = str(data.get("mode") or "dir")
        if not _DIALOG_LOCK.acquire(blocking=False):
            return {"ok": False, "error": "已经有一个选择框开着"}
        picked, error = "", ""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if mode == "zip":
                picked = filedialog.askopenfilename(title="选主题 zip",
                                                    filetypes=[("主题包", "*.zip"), ("全部文件", "*.*")])
            else:
                picked = filedialog.askdirectory(title="选主题文件夹（里面有 theme.yaml）")
            root.destroy()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            _DIALOG_LOCK.release()
        if not picked or error:
            return {"ok": False, "cancelled": not picked and not error, "error": error}

        result = install_theme_files(picked, self.cfg.user_themes_dir, mode)
        from lcdmuse.theme import list_themes, theme_dirs
        return {"ok": result.get("ok", False), "installed": result.get("installed"),
                "themes": [t.describe() for t in list_themes(theme_dirs(self.cfg))],
                "error": result.get("error", "")}

    def _serve_icon(self, png: bool) -> None:      # noqa: FBT001 - 内部调用，够简单
        """网页标签页上的小图标：优先用户自己的，其次仓库里的。"""
        from lcdmuse.config import default_user_dir
        root = Path(__file__).resolve().parent
        candidates = []
        name = "icon.png" if png else "icon.ico"
        for base in (default_user_dir(), root.parent / "assets", root / "web"):
            candidates += [base / name, base / "favicon.png"]
        for path in candidates:
            if path.exists():
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png" if path.suffix == ".png" else "image/x-icon")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:                          # noqa: N802
        url = urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            self._static("index.html")
            return None
        if Path(url.path).suffix in _MIME:
            self._static(Path(url.path).name)
            return None
        if url.path == "/api/status":
            self._json(_state(self.players, self.cfg, self.selected))
            return None
        if url.path == "/api/metrics":
            from lcdmuse.metrics import CATALOG, read
            self._json({"values": read(), "catalog": [
                {"name": k, "label": v[0], "unit": v[1]} for k, v in CATALOG.items()]})
            return None
        if url.path == "/api/themes":
            from lcdmuse.theme import list_themes, theme_dirs
            self._json({"themes": [t.describe() for t in list_themes(theme_dirs(self.cfg))]})
            return None
        if url.path in ("/favicon.ico", "/favicon.png"):
            return self._serve_icon(url.path.endswith(".png"))

        if url.path == "/api/pet/roam":
            from lcdmuse import roam as roam_mod
            roamer = roam_mod.CURRENT
            if roamer is None:
                return self._json({"ok": False, "error": "桌宠未启动"}, 503)
            mood = roamer.mood or roamer._mood()
            return self._json({"ok": True, "roam": roamer.snapshot(),
                               "mood": mood.summary() if mood else None})

        if url.path == "/api/pets":
            from lcdmuse.pet import list_pets, pet_dirs
            self._json({"pets": list_pets(pet_dirs(self.cfg))})
            return None
        if url.path == "/api/preview":
            query = parse_qs(url.query)
            index = int(query.get("i", [str(self.selected)])[0])
            png = _preview_png(self.players, index)
            if png is None:
                self.send_error(HTTPStatus.NOT_FOUND, "no frame yet")
                return None
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(png)
            return None
        self.send_error(HTTPStatus.NOT_FOUND)

        return None

    def do_POST(self) -> None:                         # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode() or "{}")
        except Exception:
            data = {}

        if not self._guard():                       # 有副作用的请求先验令牌/来源
            return None

        if url.path == "/mcp":                      # DSH 的 MCP 请求（JSON-RPC 2.0）
            try:
                request = json.loads(raw.decode() or "{}")
            except Exception:
                request = {}
            if isinstance(request, list):           # 批量请求
                replies = [mcp_handle(item, Toolbox(self.players, self.cfg)) for item in request]
                return self._json([r for r in replies if r is not None])
            reply = mcp_handle(request, Toolbox(self.players, self.cfg))
            if reply is None:                       # 通知没有响应体
                self.send_response(204)
                self.end_headers()
                return None
            return self._json(reply)

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
            # 全局只有一只：这里开/关的是那一只，不是给某块屏单独配一只
            from lcdmuse import roam as roam_mod
            roamer = roam_mod.CURRENT
            want = data.get("enabled")
            if roamer is None:
                # 没有巡游实例（例如命令行单推模式）：退回按设备单只的旧行为
                index = data.get("index")
                index = self.selected if index is None else int(index)
                index = max(0, min(index, len(self.players) - 1))
                player = self.players[index]
                enable = (player.pet_name is None) if want is None else bool(want)
                player.set_pet((self.cfg.default_pet or "whale-girl") if enable else None)
                return self._json({"ok": True, "index": index, "enabled": enable,
                                   "pet": player.pet_name})
            roamer.enabled = (not roamer.enabled) if want is None else bool(want)
            self.cfg.pet_enabled = roamer.enabled       # 和托盘、MCP 共用这一个开关
            self.cfg.save()
            return self._json({"ok": True, "enabled": roamer.enabled,
                               "pet": roamer.pet.name if roamer.enabled else None})

        if url.path == "/api/pet/activity":
            from lcdmuse import roam as roam_mod
            roamer = roam_mod.CURRENT
            if roamer is None:
                return self._json({"ok": False, "error": "桌宠未启动"}, 503)
            kind = str(data.get("kind") or "idle")
            roamer.set_activity(kind, text=data.get("text"),
                                seconds=float(data.get("seconds") or 12))
            return self._json({"ok": True, "kind": kind})

        if url.path in ("/api/pet/feed", "/api/pet/play", "/api/pet/mood"):
            from lcdmuse import roam as roam_mod
            roamer = roam_mod.CURRENT
            if roamer is None:
                return self._json({"ok": False, "error": "桌宠未启动"}, 503)
            mood = roamer.mood or roamer._mood()
            if mood is None:
                return self._json({"ok": False, "error": "养成数据不可用"}, 503)
            if url.path == "/api/pet/feed":
                mood.feed()
                roamer.set_activity("hungry", text="好吃！", seconds=6)
            elif url.path == "/api/pet/play":
                mood.play()
                roamer.set_activity("play", text="再来一次", seconds=6)
            return self._json({"ok": True, "mood": mood.summary()})

        if url.path == "/api/rename":
            index = int(data.get("index") or 0)
            index = max(0, min(index, len(self.players) - 1))
            player = self.players[index]
            pid = player.plugin_id or player.status.plugin
            self.cfg.set_device_name(pid, str(data.get("name") or ""))
            return self._json({"ok": True, "alias": self.cfg.name_for(pid, "")})

        if url.path == "/api/install-theme":
            # 载入主题插件：选文件夹或 zip，拷进用户主题目录（同名覆盖）
            return self._install_theme(data)

        if url.path == "/api/pick-file":
            # 系统文件对话框选底图/视频；返回路径给前端
            return self._json(self._pick_media())

        if url.path == "/api/open":
            from pathlib import Path
            target = "plugins" if data.get("target") == "plugins" else "logs"
            if target == "plugins":
                path = self.cfg.ensure_user_drivers()
            else:
                path = Path(__file__).resolve().parent.parent / "logs"
                path.mkdir(parents=True, exist_ok=True)
            error = ""
            try:
                if os.name == "nt":
                    os.startfile(str(path))            # noqa: S606
                else:
                    import subprocess
                    import sys as _sys
                    opener = "xdg-open" if _sys.platform.startswith("linux") else "open"
                    subprocess.Popen([opener, str(path)])
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            return self._json({"ok": not error, "path": str(path), "error": error})
            return self._json({"ok": True, "path": str(path)})

        if url.path == "/api/theme":
            theme_id = str(data.get("id") or "")
            idx = data.get("index")
            if idx is None:                             # 没指定就所有屏一起换
                for player in self.players:
                    player.set_theme(theme_id)
                self.cfg.theme = theme_id
                self.cfg.device_themes.clear()
            else:
                try:
                    player = self.players[int(idx)]
                except (ValueError, TypeError, IndexError):
                    return self._json({"ok": False, "error": "设备序号不对"}, 400)
                player.set_theme(theme_id)              # 只换这一块屏
                self.cfg.device_themes[player.plugin_id] = theme_id
            self.cfg.save()
            return self._json({"ok": True, "theme": theme_id,
                               "devices": {p.plugin_id: p.theme_id for p in self.players}})

        if url.path == "/api/background":
            path = str(data.get("path") or "")
            for player in self.players:
                player.set_background(path)
            self.cfg.background = path
            self.cfg.save()
            return self._json({"ok": True, "background": path})

        if url.path == "/api/quit":
            callback = type(self).__dict__.get("on_quit")   # 直接取类属性，避免被当成方法多塞 self
            self._json({"ok": True})
            if callback:
                threading.Thread(target=callback, daemon=True).start()
            return None

        self.send_error(HTTPStatus.NOT_FOUND)
        return None


def start_server(players: list[Player], cfg: Config, port: int = 0,
                 on_quit=None, publish_runtime: bool = False) -> tuple[ThreadingHTTPServer, int]:
    """起一个只监听 127.0.0.1 的小服务；port=0 由系统分配。"""
    handler = type("BoundHandler", (_Handler,),
                   {"players": players, "cfg": cfg, "on_quit": on_quit,
                    "token": _TOKEN})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, name="lcdmuse-web", daemon=True)
    thread.start()
    bound = server.server_address[1]
    if publish_runtime:           # 只有托盘有权把端口写盘，别人（测试/临时实例）不许覆盖
        write_runtime(bound, token=_TOKEN)
        atexit.register(clear_runtime)
    log.info("控制台：http://127.0.0.1:%d/   （MCP 端点 /mcp）", bound)
    return server, bound


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
