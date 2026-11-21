# AI-GUIDE.md —— 在这个仓库里干活的说明书

> 给人和 AI 代理看的工作约定。中文为主。
> 本仓是 **turing-smart-screen-python**（GPL-3.0）的二次封装：驱动全部插件化、核心不含设备知识、界面中文化、加桌宠框架。
> 许可证不变，仍是 **GPL-3.0-or-later**。
> 上游原有的 `AGENTS.md` 已并入本文件（内容见第 10 节），不再单独维护。

---

## 1. 这是什么东西

把一块小 USB 屏（串口 CDC / USB HID）当第二显示器：采系统指标 → 画进画布 → 推帧到屏。
三种用法：常驻程序（`lcdmuse`）、配置工具、主题编辑器。将来加 `lcdmuse-daemon` + `lcdmuse-mcp` 供别的程序（如 DSH）调用。

**现状：两代代码并存。**

| | 新栈（本仓主力） | 旧栈（上游遗留） |
|---|---|---|
| 包 | `lcdmuse/` | `library/` |
| 驱动 | `drivers/<id>/` 插件，运行时才加载 | `library/lcd/lcd_comm_rev_*.py`，被 `library/display.py` 硬 import |
| 入口 | `python -m lcdmuse.cli` | `main.py` / `configure.py` / `theme-editor.py` |
| 状态 | 契约、插件、渲染、桌宠、CLI 已完成 | 仍能跑，但正在被替换 |

**转换期规则**：新功能一律写进 `lcdmuse/`，不要往 `library/` 加东西；旧栈保持能用即可，等入口重写完（阶段 2）再整体删。

## 2. 四条硬规矩

1. **核心零驱动**：`lcdmuse/` 里不许有 VID/PID、型号名、命令字、协议魔数、波特率常量、串口名。
2. **核心不 import 驱动**：`lcdmuse/` 不 `import drivers.*`，只经 `lcdmuse/plugin.py` 动态加载。
3. **中文优先**：界面与文案默认中文（i18n 后英文为次要语言）。
4. **可发布**：不依赖本机绝对路径、不依赖个人环境，clone 下来就能跑。

违反第 1、2 条的功能改动一律不接受。

## 3. 驱动怎么工作（不是独立进程）

驱动是**被核心挂载的插件**，不自己启动：

```
启动 → Registry 扫 drivers/*/driver.yaml → 校验 api 版本 + requires
     → plugin.probe() 返回 DeviceInfo（没有设备就是 None）
     → plugin.open() 拿到 LcdComm 实例
     → 核心送 Frame（RGB888 逻辑方向）→ driver.push_frame() 自己编码、自己发
```

- 加载方式：`importlib` 在**同一进程内**挂载 `driver.py`，地址空间与核心共享；驱动不监听端口、不开自己的服务。
- 谁是设备的唯一主人：将来 `lcdmuse-daemon` 独占串口/HID，所有调用方（CLI、GUI、MCP）都排队走它；`lcdmuse-mcp` 只是薄封装，自己一个字节都不碰设备。
- 因此同一块屏**同时只能被一个进程打开**；厂商软件（SAMA/AIGO 那种）在跑时，必须先从它手里把设备放出来。

## 4. 目录

```
lcdmuse/                 核心（零驱动）
  lcd_comm.py           契约：错误族、Capability、DeviceInfo、Frame、LcdComm
  plugin.py             扫 drivers/*/driver.yaml，校验 api 与 requires
  registry.py           枚举 → probe → open；enabled_by_default=false 默认不 probe
  render/               画布原语、颜色解析、脏矩形 diff、demo 测试卡
  transport/            通用链路（串口封装），无设备标识
  pet.py                桌宠：pet.json + 状态机 + 锚点合成
  config.py             配置与日志
  player.py             推帧循环（托盘与 CLI 共用，无界面依赖、可无头测试）
  tray.py               托盘图标 + 右键菜单（pystray，单实例）
  cli.py                命令行入口
drivers/<id>/           driver.yaml + driver.py
  simulated/            不接硬件的样板驱动
  _legacy/              原厂驱动兼容栈（仅被适配器引用）
pets/<name>/            pet.json + frames/*.png
tools/                  GIF 抽帧、预览合成
tests/test_stage1.py    核心零硬件断言 + 契约/桌宠测试
docs/                   工作约定、阶段状态、改动清单
library/                旧栈（待删，勿加新代码）
# 用户插件目录（不在仓库里）
%APPDATA%\lcdmuse\drivers          Windows；其它平台 ~/.config/lcdmuse/drivers
```

## 5. 契约要点（改这里等于改所有驱动）

- 必须实现：`open()`、`close()`、`push_frame(Frame)`。
- 可选覆盖：`push_region`、`set_brightness`、`set_rotation`、`power`、`tick(now)`、`raw_command`。
- 未声明能力被调用要抛 `Unsupported`；错误码即对外的 `error.code`。
- `DeviceInfo.native_rotation` 由装法决定、驱动侧固定；用户偏好旋转在核心层叠加。
- `Frame` 是 RGB888 逻辑方向，核心按 `native_rotation` 统一旋转后交给驱动。

## 6. 加一个驱动

1. `drivers/<id>/driver.yaml`：`id / name / version / api: 1 / entry: driver:类名 / requires / enabled_by_default`。
2. `driver.py` 继承 `lcdmuse.lcd_comm.LcdComm`，实现 `probe/open/close/push_frame`。
3. 串口用 `lcdmuse.transport.serial_link.SerialLink`，端口与波特率从 `self.config` 取。
4. **`requires` 写 import 名**（`serial` 不是 `pyserial`，`usb` 不是 `pyusb`），写错会永远显示"缺少依赖"。
5. `python -m lcdmuse.cli drivers list` 确认 `available: true`。
6. 不想动源码就把插件装到用户目录：`python -m lcdmuse.cli plugin add <目录|zip>`；
   同 id 与仓库自带插件撞车时，**用户目录那份优先**，另一份只打 warning。
7. 跨版本兼容写在 `driver.yaml` 的 `min_core`（如 `min_core: 0.2.0`）；核心版本低于它时插件只标「不可用」。

## 7. 加一个桌宠

1. `pets/<name>/pet.json` + `frames/*.png`（透明底，建议 ≤300×400）。
2. 状态：`idle` 循环；`eat`/`click`/`say` 播完回 idle；`sleep` 保持。
3. `python tools/petify.py --gif a.gif --state eat --out pets/<name>` 抽帧。
4. 素材必须可再分发（MIT/CC0），作者与许可写进 `pet.json` 的 `source`。

## 8. 验收命令（改前改后都跑）

```bash
python -m lcdmuse.cli drivers list
python -m lcdmuse.cli probe
python -m lcdmuse.cli render --offline --pet whale-girl --out out/preview.png
python -m lcdmuse.cli plugin list          # 用户目录里装了什么
python -m lcdmuse.cli tray --plugin all    # 托盘常驻（会占设备，验收完记得退出）
python -m pytest tests/test_stage1.py -q
```

三条硬判据（已固化在测试里）：

1. `lcdmuse/` 无设备标识：十六进制常量、`VID_`/`PID_`、`COM\d+`。
2. `lcdmuse/` 无协议魔数与波特率常量。
3. 删掉整个 `drivers/` 后，核心仍可 import、仍能 `render --offline` 出图。

## 9. 沿用的上游约定（对 `library/` 旧栈仍然有效）

- `.py` 文件头带 `SPDX-License-Identifier: GPL-3.0-or-later`。
- 配置 YAML 优先：先加选项到 `config.yaml`，再到 `library/config.py` 读，别硬编码。
- 跨平台（Windows/macOS/Linux）；平台特例可以写，但要隔离。
- 硬件选择走 `REVISION` → `library/display.py` 工厂，别在工厂外新增分支。
- 周期性更新走 `library/scheduler.py`，不要自己起并发循环抢设备。
- 主题键名保持兼容（参考 `res/themes/default.yaml`）。

## 10. 旧栈速查（上游流程，仍在跑）

**数据流**

```
config.yaml
  → library/config.py        读 config.yaml，再叠 res/themes/<THEME>/theme.yaml（缺项回默认）
  → library/stats.py         按 HW_SENSORS 取数据：AUTO / PYTHON / LHM / STUB
  → library/stats.py         按 theme.yaml 画成图
  → library/display.py       按 REVISION 选驱动（A/B/C/D / TUR_USB / WEACT_A/B / SIMU）
  → LcdComm*                 编码、推给屏
  → library/scheduler.py     @schedule / @async_job + 写屏队列（串行化，别自己起并发循环）
```

**没有硬件也能开工**：`config.yaml` 里把 `REVISION` 设成 `SIMU`，会走 `library/lcd/lcd_simulated.py`，把画面写进 `screencap.png`，不碰任何设备。

**常用命令**

```bash
pip install -r requirements.txt
python main.py                # 常驻程序
python configure.py           # 配置界面（tkinter + sv-ttk）
python theme-editor.py        # 主题编辑器
python simple-program.py      # 最小示例：直接往屏上画
pyinstaller turing-system-monitor.spec   # Windows 打包（含 res/、external/、config.yaml）
```

**依赖归属（别往核心塞）**

| 用途 | 包 |
|---|---|
| 串口屏 | `pyserial` |
| Turing USB 屏 | `pyusb` |
| Windows 深度传感器 | `pythonnet` + `external/LibreHardwareMonitor/*.dll` |
| 系统指标 | `psutil`、`GPUtil`、`pyamdgpuinfo`（Linux AMD）、`pyadl`（Windows AMD） |
| 界面 | `pystray`（托盘）、`tkinter` + `sv-ttk`（配置界面）、`Pillow` / `numpy`（图像） |

**改旧栈时先看这几个文件**

`main.py`（编排/托盘/信号）· `library/config.py`（配置与主题合并）· `library/display.py`（驱动工厂与尺寸）·
`library/scheduler.py`（周期任务与写队列）· `library/stats.py`（采集与绘制）· `config.yaml`（用户配置项）·
`simple-program.py`（低层 API 示例）· `turing-system-monitor.spec`（打包清单）

## 11. 提交规范

- 一次提交只做一件事；大改动拆成能独立评审的几步。
- 默认保持向后兼容，破坏性改动要先说明。
- 提交信息写清"为什么"，不是"改了哪几行"。
- PR 落到别人手里也要能看懂：附实测命令与输出。

## 12. 资源不在本仓

`res/`（字库 + 主题，约 1 GB）不随仓库发布。缺字库时渲染退回 Pillow 默认字体（不崩，中文会缺字）。取法：

```bash
git clone --depth 1 https://github.com/mathoudebine/turing-smart-screen-python /tmp/tss
cp -r /tmp/tss/res ./res
```

因此 `simple-program.py` 缺 `res/` 时会自己画一张纯色渐变背景、缺字库时退回内置字体（只警告，不崩）。
CI 的 System monitor 测试不拉整份 `res/`：`.github/actions/fetch-theme-assets` 用稀疏检出只取主题/字库子集（约 10 MB，上游 commit 已 pin）。

## 13. 不要做

- 不把型号、序列号、VID/PID、串口号写进 `lcdmuse/` 或文档正文。
- 不在核心引 `numpy`/`pyusb` 这类重依赖，它们只属于具体驱动的 `requires`。
- 不在设备所有者不在场时操作真实屏幕，不抢厂商软件占用的串口；自动化测试一律 `simulated` / `--offline`。
- 不删上游版权与许可声明。

## 14. 已知缺口

- `library/` 未删：`main.py` / `configure.py` / `theme-editor.py` 仍依赖它。
- 原厂适配器的 `probe()` 依赖旧代码的串口枚举，未在真实硬件上验证。
- `push_region` 未实现，能否支持取决于屏的协议。
- `lcdmuse/service/`（daemon + IPC）与 `lcdmuse/mcp/` 未开工。
- 主题引擎仍是 `render/demo.py` 的测试卡，未接 `res/themes`。

## 15. 提交前自检

```bash
python -m ruff check lcdmuse drivers/*/driver.py tools/petify.py tools/pet_preview.py tests/test_stage1.py
```
