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
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
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

    def _html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                          # noqa: N802
        url = urlparse(self.path)
        if url.path == "/":
            self._html(PAGE)
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


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>lcdmuse · 副屏控制</title>
<style>
:root{
  --bg:#0b0f14; --panel:#101721; --panel-2:#131c27; --line:#1b2634;
  --ink:#e8eef5; --muted:#8798ab; --accent:#3fbfe0; --ok:#46d39a; --warn:#e2a33c; --bad:#e2665a;
  --r:8px; --r-sm:6px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:13px/1.45 "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.num{font-family:"Cascadia Mono",Consolas,monospace;font-variant-numeric:tabular-nums}
.app{height:100%;display:grid;grid-template-rows:46px 1fr 30px;
  grid-template-columns:212px 1fr 336px;gap:10px;padding:10px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r)}

/* 顶栏 */
header{grid-column:1/-1;display:flex;align-items:center;gap:12px;padding:0 14px}
.brand{display:flex;align-items:center;gap:8px;font-weight:600;letter-spacing:.2px}
.brand i{width:13px;height:13px;border-radius:3px;background:var(--accent);
  box-shadow:0 0 10px rgba(63,191,224,.45);display:block}
.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border:1px solid var(--line);
  border-radius:999px;color:var(--muted);font-size:11.5px}
.pill b{font-weight:500;color:var(--ink)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--ok)}
.dot.off{background:var(--warn)} .dot.bad{background:var(--bad)}
.grow{flex:1}

/* 左栏 */
nav{padding:8px;display:flex;flex-direction:column;gap:1px;overflow:auto}
nav h6{margin:8px 8px 4px;font-size:10px;letter-spacing:.14em;color:var(--muted);font-weight:500}
.item{display:flex;align-items:center;gap:8px;padding:6px 9px;border-radius:6px;
  cursor:pointer;color:var(--ink);user-select:none}
.item:hover{background:var(--panel-2)}
.item.on{background:rgba(63,191,224,.14);box-shadow:inset 2px 0 0 var(--accent)}
.item .dotrow{width:6px;display:flex;justify-content:center}
.item .sub{color:var(--muted);font-size:10.5px;margin-left:auto;min-width:26px;text-align:right}
.item .dim{color:var(--muted)}
.item .txt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.item.on .txt{color:var(--accent);font-weight:600}
nav h6{padding-left:4px}
.sep{height:1px;background:var(--line);margin:8px 6px}
.footnote{margin-top:auto;padding:8px;color:var(--muted);font-size:10.5px;line-height:1.6}

/* 主区 */
main{display:flex;flex-direction:column;gap:10px;padding:12px;overflow:auto}
.title{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.title h1{margin:0;font-size:15px;font-weight:600}
.tag{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:5px;padding:1px 6px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--panel-2)}
.card.on{border-color:rgba(63,191,224,.5)}
.card .name{font-weight:600;display:flex;justify-content:space-between;gap:8px}
.card .name span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .meta{color:var(--muted);font-size:11px;margin-top:2px}
.card .stat{margin-top:8px;display:flex;align-items:baseline;gap:8px}
.card .frames{font-size:19px;font-weight:600;letter-spacing:-.02em}
.card .unit{color:var(--muted);font-size:11px}
.bar{height:2px;background:var(--line);border-radius:2px;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent);width:0}
table.kv{width:100%;border-collapse:collapse;font-size:12px}
table.kv td{padding:4px 0;border-bottom:1px solid var(--line);vertical-align:top}
table.kv td:first-child{color:var(--muted);width:96px;white-space:nowrap}
table.kv tr:last-child td{border-bottom:0}
.actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}
button{font:inherit;color:var(--ink);background:var(--panel-2);border:1px solid var(--line);
  border-radius:var(--r-sm);padding:5px 11px;cursor:pointer}
button:hover{border-color:rgba(63,191,224,.55);color:#fff}
button.primary{border-color:rgba(63,191,224,.55);color:var(--accent)}

/* 右侧预览 */
aside{padding:12px;display:flex;flex-direction:column;gap:10px}
aside h2{margin:0;font-size:10px;letter-spacing:.14em;color:var(--muted);font-weight:500}
.screen{position:relative;border:1px solid var(--line);border-radius:var(--r);background:#05080c;
  aspect-ratio:2/1;width:100%;display:flex;align-items:center;justify-content:center;overflow:hidden}
.screen img{max-width:100%;max-height:100%;display:block}
.screen .empty{color:var(--muted);font-size:11.5px}
.legend{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:12px}
.legend span:nth-child(odd){color:var(--muted)}
.chartbox{margin-top:auto;border-top:1px solid var(--line);padding-top:8px}
.chartbox .cap{display:flex;justify-content:space-between;color:var(--muted);font-size:10.5px;margin-bottom:4px}
canvas{width:100%;height:56px;display:block}

/* 底栏 */
footer{grid-column:1/-1;display:flex;align-items:center;gap:14px;padding:0 12px;
  color:var(--muted);font-size:11.5px}
footer b{color:var(--ink);font-weight:500}
</style></head>
<body>
<div class="app">
  <header class="panel">
    <span class="brand"><i></i>lcdmuse</span>
    <span class="pill">中文</span>
    <div class="grow"></div>
    <div id="pills" style="display:flex;gap:8px"></div>
    <span class="pill num" id="ver">—</span>
  </header>

  <nav class="panel" id="nav"></nav>

  <main class="panel">
    <div class="title"><h1 id="cur">当前设备</h1><span class="tag" id="curMeta"></span></div>
    <h6 class="sect" style="margin:4px 0 0;font-size:10px;letter-spacing:.14em;color:var(--muted);font-weight:500">全部设备</h6>
    <div class="cards" id="cards"></div>
    <table class="kv"><tbody id="kv"></tbody></table>
    <div class="actions">
      <button class="primary" id="pause">暂停 / 继续</button>
      <button id="pet">桌宠</button>
      <button id="rename">重命名设备</button>
    </div>
  </main>

  <aside class="panel">
    <h2>屏幕预览</h2>
    <div class="screen" id="screen"><span class="empty">等待首帧…</span></div>
    <div class="legend" id="legend"></div>
    <div class="chartbox">
      <div class="cap"><span>帧率 · 最近 60 秒</span><span class="num" id="chartMax">—</span></div>
      <canvas id="chart" width="600" height="112"></canvas>
    </div>
  </aside>

  <footer class="panel">
    <span id="status">…</span><span class="grow"></span><span>MCP：未开工</span>
  </footer>
</div>
<script>
let selected = 0, timer = null, lastFrames = -1, history = [], historyFor = -1;

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path, body){
  const r = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body)} : undefined);
  return r.json();
}

function render(s){
  $("ver").textContent = "v" + s.version;
  const devs = s.devices || [];
  if (selected >= devs.length) selected = 0;

  // 顶栏胶囊
  $("pills").innerHTML = devs.map(d => {
    const cls = d.error ? "bad" : (d.running && !d.paused ? "" : "off");
    const short = d.alias.length > 14 ? d.alias.slice(0, 13) + "…" : d.alias;
    return `<span class="pill" title="${esc(d.alias)}"><i class="dot ${cls}"></i><b>${esc(short)}</b></span>`;
  }).join("");

  // 左栏
  $("nav").innerHTML =
    `<h6>设备</h6>` +
    devs.map((d,i) => `<div class="item ${i===selected?"on":""}" data-i="${i}" title="${esc(d.alias)}">
        <span class="dotrow"><i class="dot ${d.error?"bad":(d.running?"":"off")}"></i></span>
        <span class="txt">${esc(d.alias)}</span>
        <span class="sub num">${d.fps.toFixed(1)}</span></div>`).join("") +
    `<div class="sep"></div><h6>显示</h6>
       <div class="item" id="navPet">桌宠<span class="sub">${devs[0]&&devs[0].pet?"开":"关"}</span></div>
       <div class="item"><span class="dim">主题</span><span class="sub">未接</span></div>
       <div class="item"><span class="dim">图片 / 推帧</span><span class="sub">CLI</span></div>
     <div class="sep"></div><h6>系统</h6>
       <div class="item" id="navPlugins">插件目录</div>
       <div class="item" id="navLogs">日志</div>
       <div class="item" id="navQuit">退出</div>
     <div class="footnote">${devs.length} 台设备 · 插件从用户目录自动加载</div>`;

  $("nav").querySelectorAll(".item[data-i]").forEach(el => {
    el.onclick = async () => { selected = +el.dataset.i; await api("/api/select", {index:selected}); tick(); };
  });
  $("navPet").onclick = togglePet;
  $("navPlugins").onclick = () => api("/api/open", {target:"plugins"});
  $("navLogs").onclick = () => api("/api/open", {target:"logs"});
  $("navQuit").onclick = quit;

  // 主区
  const d = devs[selected] || {};
  $("cur").textContent = d.alias ? d.alias : "没有探测到设备";
  $("curMeta").textContent = d.size ? `${d.size[0]}×${d.size[1]} · ${d.plugin||""}` : "接上屏或先跑 lcdmuse probe";

  $("cards").innerHTML = devs.map((x,i) => `
    <div class="card ${i===selected?"on":""}" data-i="${i}">
      <div class="name"><span>${esc(x.alias)}</span>
        <span class="num" style="color:var(--muted);font-size:11px">${x.size[0]}×${x.size[1]}</span></div>
      <div class="meta">${esc(x.plugin||"")}${x.error ? " · "+esc(x.error.slice(0,24)) : ""}</div>
      <div class="stat"><span class="frames num">${x.frames}</span><span class="unit">帧</span>
        <span class="unit num">${x.fps.toFixed(1)} fps</span>
        <span class="unit" style="margin-left:auto;color:${x.error?"var(--bad)":(x.paused?"var(--warn)":"var(--ok)")}">
          ${x.error?"异常":(x.paused?"已暂停":"运行中")}</span></div>
      <div class="bar"><i style="width:${Math.min(100, x.fps*100/2)}%"></i></div>
    </div>`).join("");
  $("cards").querySelectorAll(".card").forEach(el => {
    el.onclick = async () => { selected = +el.dataset.i; await api("/api/select", {index:selected}); render(s); };
  });

  $("kv").innerHTML = [
    ["驱动", d.plugin || "—"],
    ["设备自报", d.device || "—"],
    ["帧 / 帧率", `${d.frames ?? 0} 帧 · ${(d.fps ?? 0).toFixed(1)} fps`],
    ["桌宠", d.pet ? d.pet : "关闭"],
    ["配置", s.config_path || "—"],
    ["插件目录", s.user_drivers || "—"],
  ].map(([k,v]) => `<tr><td>${k}</td><td class="num" style="word-break:break-all">${esc(v)}</td></tr>`).join("");

  // 预览：只有帧号变了才换图，别每秒重下
  const screen = $("screen");
  if (d.frames !== undefined && d.frames !== lastFrames && d.frames > 0){
    lastFrames = d.frames;
    let img = screen.querySelector("img");
    if (!img){ screen.innerHTML = ""; img = document.createElement("img"); screen.appendChild(img); }
    img.src = `/api/preview?i=${selected}&t=${d.frames}`;
  } else if (d.frames === 0 && !screen.querySelector("img")) {
    screen.innerHTML = `<span class="empty">等待首帧…</span>`;
  }

  $("legend").innerHTML = [
    ["状态", d.error ? "异常：" + esc(d.error.slice(0,30)) : (d.paused ? "已暂停" : (d.running ? "运行中" : "未连接"))],
    ["最近帧", d.frames ? `#${d.frames}` : "—"],
    ["分辨率", d.size ? `${d.size[0]}×${d.size[1]}` : "—"],
  ].map(([k,v]) => `<span>${k}</span><span class="num">${v}</span>`).join("");

  // 帧率曲线：只留最近 60 个采样点，切设备就重新开始
  if (historyFor !== selected){ historyFor = selected; history = []; lastFrames = -1; }
  history.push(d.fps || 0);
  if (history.length > 60) history.shift();
  drawChart();

  const total = devs.reduce((a,x) => a + (x.frames||0), 0);
  $("status").innerHTML = `<b>${devs.length}</b> 台设备 · 已推 <b class="num">${total}</b> 帧 · `
    + `当前 <b class="num">${(d.fps ?? 0).toFixed(1)}</b> fps`;
}

function drawChart(){
  const c = $("chart"), g = c.getContext("2d");
  const w = c.width, h = c.height;
  g.clearRect(0,0,w,h);
  const peak = Math.max(0.5, ...history, 1);
  $("chartMax").textContent = "峰值 " + peak.toFixed(1) + " fps";
  // 基线
  g.strokeStyle = "rgba(135,152,171,.18)"; g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, h - 1); g.lineTo(w, h - 1); g.stroke();
  g.beginPath(); g.moveTo(0, h / 2); g.lineTo(w, h / 2); g.stroke();
  if (history.length < 2) return;
  const step = w / Math.max(1, history.length - 1);
  g.beginPath();
  history.forEach((v,i) => {
    const x = i * step, y = h - 4 - (v / peak) * (h - 12);
    i ? g.lineTo(x,y) : g.moveTo(x,y);
  });
  g.strokeStyle = "#3fbfe0"; g.lineWidth = 2; g.lineJoin = "round"; g.stroke();
  const lastY = h - 4 - (history[history.length-1] / peak) * (h - 12);
  g.lineTo(w, h); g.lineTo(0, h); g.closePath();
  g.fillStyle = "rgba(63,191,224,.14)"; g.fill();
}

async function tick(){ try{ render(await api("/api/status")); }catch(e){ $("status").textContent = "与本地服务断开：" + e; } }
async function togglePet(){ await api("/api/pet", {}); tick(); }
async function quit(){ await api("/api/quit", {}); document.body.innerHTML =
  `<div style="padding:40px;color:#8798ab;font:13px 'Microsoft YaHei UI'">已退出，设备已释放。</div>`; }

$("pause").onclick = async () => { await api("/api/pause", {}); tick(); };
$("pet").onclick = togglePet;
$("rename").onclick = async () => {
  const s = await api("/api/status");
  const d = (s.devices||[])[selected]; if (!d) return;
  const name = prompt(`给「${d.alias}」起个显示名（留空恢复默认）`, d.alias);
  if (name === null) return;
  await api("/api/rename", {index:selected, name});
  tick();
};


tick(); setInterval(tick, 1000);
</script>
</body></html>"""
