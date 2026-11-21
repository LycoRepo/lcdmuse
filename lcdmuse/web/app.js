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
       <div class="item" id="navTheme" title="点一下换主题插件">主题<span class="sub">${esc(s.theme || "测试卡")}</span></div>
       <div class="item" id="navBg" title="点一下换底图：图片 / GIF / 视频">底图<span class="sub">${s.background ? "自定义" : "主题自带"}</span></div>
     <div class="sep"></div><h6>系统</h6>
       <div class="item" id="navPlugins">插件目录</div>
       <div class="item" id="navLogs">日志</div>
       <div class="item" id="navQuit">退出</div>
     <div class="footnote">${devs.length} 台设备 · 插件从用户目录自动加载</div>`;

  $("nav").querySelectorAll(".item[data-i]").forEach(el => {
    el.onclick = async () => { selected = +el.dataset.i; await api("/api/select", {index:selected}); tick(); };
  });
  $("navPet").onclick = togglePet;
  $("navTheme").onclick = pickTheme;
  $("navBg").onclick = pickBackground;
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
    ["主题", s.theme ? s.theme : "测试卡"],
    ["底图", s.background ? s.background : "主题自带"],
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

async function pickTheme(){
  const data = await api("/api/themes");
  const list = data.themes || [];
  if (!list.length){ alert("themes/ 里还没有主题插件"); return; }
  const lines = list.map((t,i) => `${i+1}. ${t.id} — ${t.name}${t.has_mask?"（带蒙版）":""}`).join("\n");
  const answer = prompt(`换主题（输入编号，留空=测试卡）：\n${lines}`, "");
  if (answer === null) return;
  const idx = parseInt(answer, 10);
  const id = answer.trim() === "" ? "" : (list[idx-1] ? list[idx-1].id : answer.trim());
  await api("/api/theme", {id});
  tick();
}

async function pickBackground(){
  const current = (await api("/api/status")).background || "";
  const path = prompt("底图路径（图片 / GIF / 视频；留空=用主题自带的）：", current);
  if (path === null) return;
  await api("/api/background", {path});
  tick();
}
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
