let selected = 0, history = [], historyFor = -1, lastFrames = -1;

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path, body){
  const r = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body)} : undefined);
  return r.json();
}

// ── 弹层（换主题 / 换底图 都用它，不再用浏览器 prompt）────────────────
function openModal(title, bodyHTML, footHTML = ""){
  $("mtitle").textContent = title;
  $("mbody").innerHTML = bodyHTML;
  $("mfoot").innerHTML = footHTML;
  $("modal").hidden = false;
}
function closeModal(){ $("modal").hidden = true; $("mbody").innerHTML = ""; }
function toast(msg, bad){
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.hidden = true; }, 3800);
}

// ── 交互都挂在容器上：nav 每秒重建也不会丢事件 ────────────────────────
$("nav").addEventListener("click", async ev => {
  const el = ev.target.closest("[data-act]");
  if (!el) return;
  const act = el.dataset.act;
  if (act === "device"){ selected = +el.dataset.i; await api("/api/select", {index:selected}); tick(); }
  else if (act === "pet") togglePet();
  else if (act === "theme") openThemePicker();
  else if (act === "bg") openBgPicker();
  else if (act === "plugins") openPath("plugins");
  else if (act === "logs") openPath("logs");
  else if (act === "quit") quit();
});

$("mclose").onclick = closeModal;
$("modal").addEventListener("click", ev => { if (ev.target.id === "modal") closeModal(); });
document.addEventListener("keydown", ev => { if (ev.key === "Escape") closeModal(); });

async function openPath(target){
  const r = await api("/api/open", {target});
  if (r.ok) toast("已打开：" + r.path);
  else toast("打不开：" + (r.error || r.path), true);
}

function render(s){
  $("ver").textContent = "v" + s.version;
  const devs = s.devices || [];
  if (selected >= devs.length) selected = 0;
  const d = devs[selected] || {};

  $("nav").innerHTML =
    `<h6>设备</h6>` +
    devs.map((x,i) => `<div class="item ${i===selected?"on":""}" data-act="device" data-i="${i}" title="${esc(x.alias)}">
        <span class="dotrow"><i class="dot ${x.error?"bad":(x.running?"":"off")}"></i></span>
        <span class="txt">${esc(x.alias)}</span>
        <span class="sub num">${x.fps.toFixed(1)}</span></div>`).join("") +
    `<div class="sep"></div><h6>显示</h6>
       <div class="item" data-act="theme" title="选一个主题插件">主题<span class="sub">${esc(s.theme || "测试卡")}</span></div>
       <div class="item" data-act="bg" title="图片 / GIF / 视频">底图<span class="sub">${s.background ? "自定义" : "主题自带"}</span></div>
     <div class="sep"></div><h6>系统</h6>
       <div class="item" data-act="plugins">插件目录</div>
       <div class="item" data-act="logs">日志</div>
       <div class="item" data-act="quit">退出</div>
     <div class="footnote">${devs.length} 台设备 · 插件从用户目录自动加载</div>`;

  $("cur").textContent = d.alias ? d.alias : "没有探测到设备";
  $("curMeta").textContent = d.size ? `${d.size[0]}×${d.size[1]} · ${d.plugin||""}` : "接上屏或先跑 lcdmuse probe";

  $("kv").innerHTML = [
    ["驱动", d.plugin || "—"],
    ["设备自报", d.device || "—"],
    ["帧率", `${(d.fps ?? 0).toFixed(1)} fps`],
    ["桌宠", d.pet ? d.pet : "关闭"],
    ["主题", s.theme ? s.theme : "测试卡"],
    ["底图", s.background ? s.background : "主题自带"],
    ["配置", s.config_path || "—"],
    ["插件目录", s.user_drivers || "—"],
  ].map(([k,v]) => `<tr><td>${k}</td><td class="num" style="word-break:break-all">${esc(v)}</td></tr>`).join("");

  const screen = $("screen");
  if (d.frames !== undefined && d.frames !== lastFrames && d.frames > 0){
    lastFrames = d.frames;
    let img = screen.querySelector("img");
    if (!img){ screen.innerHTML = ""; img = document.createElement("img"); screen.appendChild(img); }
    img.src = `/api/preview?i=${selected}&t=${d.frames}`;
  } else if (d.frames === 0 && !screen.querySelector("img")) {
    screen.innerHTML = `<span class="empty">等待首帧…</span>`;
  }

  $("pet").textContent = d.pet ? "桌宠：开" : "桌宠：关";
  $("pet").classList.toggle("primary", !!d.pet);

  $("legend").innerHTML = [
    ["状态", d.error ? "异常：" + esc(d.error.slice(0,30)) : (d.paused ? "已暂停" : (d.running ? "运行中" : "未连接"))],
    ["分辨率", d.size ? `${d.size[0]}×${d.size[1]}` : "—"],
  ].map(([k,v]) => `<span>${k}</span><span class="num">${v}</span>`).join("");

  if (historyFor !== selected){ historyFor = selected; history = []; lastFrames = -1; }
  history.push(d.fps || 0);
  if (history.length > 60) history.shift();
  drawChart();

  $("status").innerHTML = `<b>${devs.length}</b> 台设备 · 当前 <b class="num">${(d.fps ?? 0).toFixed(1)}</b> fps`;
  const mcp = s.mcp || {};
  $("mcp").innerHTML = mcp.ready
    ? `MCP：就绪 · <b class="num">${mcp.tools ?? 0}</b> 个工具`
    : "MCP：未就绪";
}

function drawChart(){
  const c = $("chart"), g = c.getContext("2d");
  const w = c.width, h = c.height;
  g.clearRect(0,0,w,h);
  const peak = Math.max(1, ...history);
  $("chartMax").textContent = "峰值 " + peak.toFixed(1) + " fps";
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
  g.lineTo(w, h); g.lineTo(0, h); g.closePath();
  g.fillStyle = "rgba(63,191,224,.14)"; g.fill();
}

async function tick(){ try{ render(await api("/api/status")); }catch(e){ $("status").textContent = "与本地服务断开：" + e; } }

async function togglePet(){
  const r = await api("/api/pet", {index: selected});
  toast(r.ok ? (r.enabled ? `已在当前设备打开桌宠：${r.pet || ""}` : "已关闭当前设备的桌宠") : "切换失败", !r.ok);
  tick();
}

async function openThemePicker(){
  let data = {themes: []};
  try { data = await api("/api/themes"); } catch(e) {}
  const list = data.themes || [];
  const rows = [`<div class="opt" data-id=""><b>测试卡</b><span>不套主题，只画指标</span></div>`]
    .concat(list.map(t => `<div class="opt" data-id="${esc(t.id)}">
        <b>${esc(t.name || t.id)}</b>
        <span>${esc(t.id)}${t.has_mask ? " · 带蒙版" : ""} · ${t.size ? t.size.join("×") : ""}</span></div>`));
  openModal("换主题", rows.join(""),
    `<button class="primary" id="mok">关闭</button>`);
  $("mok").onclick = closeModal;
  $("mbody").querySelectorAll(".opt").forEach(el => {
    el.onclick = async () => {
      const id = el.dataset.id;
      const r = await api("/api/theme", {id});
      closeModal();
      toast(r.ok ? (id ? "已切到主题 " + id : "已切回测试卡") : "切换失败", !r.ok);
      tick();
    };
  });
}

async function openBgPicker(){
  const s = await api("/api/status");
  const cur = s.background || "";
  openModal("换底图", `
    <div class="opt" data-mode="theme"><b>主题自带</b><span>清空自定义底图，回到主题里的那张</span></div>
    <div class="opt" data-mode="file"><b>选个文件…</b><span>打开系统文件对话框，挑图片 / GIF / 视频</span></div>
    <div class="pathrow"><input id="bgpath" placeholder="或直接粘贴路径" value="${esc(cur)}">
      <button id="bgapply">应用</button></div>`,
    `<button class="primary" id="mok">关闭</button>`);
  $("mok").onclick = closeModal;
  $("mbody").querySelector('[data-mode="theme"]').onclick = async () => {
    const r = await api("/api/background", {path: ""});
    closeModal(); toast(r.ok ? "已回到主题自带的底图" : "设置失败", !r.ok); tick();
  };
  $("mbody").querySelector('[data-mode="file"]').onclick = async () => {
    toast("正在打开文件选择框…");
    const r = await api("/api/pick-file", {});
    if (!r.ok){ if (!r.cancelled) toast("打开文件框失败：" + (r.error || ""), true); return; }
    const res = await api("/api/background", {path: r.path});
    closeModal(); toast(res.ok ? "底图已换：" + r.path.split(/[\\/]/).pop() : "设置失败", !res.ok); tick();
  };
  $("bgapply").onclick = async () => {
    const path = $("bgpath").value.trim();
    const r = await api("/api/background", {path});
    closeModal(); toast(r.ok ? "底图已设置" : "设置失败", !r.ok); tick();
  };
}

async function quit(){ await api("/api/quit", {}); document.body.innerHTML =
  `<div style="padding:40px;color:#8798ab;font:13px 'Microsoft YaHei UI'">已退出，设备已释放。</div>`; }

$("pause").onclick = async () => { await api("/api/pause", {}); tick(); };
$("pet").onclick = togglePet;
$("rename").onclick = async () => {
  const s = await api("/api/status");
  const d = (s.devices||[])[selected]; if (!d) return;
  openModal("重命名设备", `
    <div class="pathrow"><input id="newname" value="${esc(d.alias)}" placeholder="留空恢复默认"></div>`,
    `<button class="primary" id="mok">保存</button><button id="mcancel">取消</button>`);
  $("mcancel").onclick = closeModal;
  $("mok").onclick = async () => {
    const name = $("newname").value.trim();
    await api("/api/rename", {index: selected, name});
    closeModal(); tick();
  };
  $("newname").focus(); $("newname").select();
};

tick(); setInterval(tick, 1000);
