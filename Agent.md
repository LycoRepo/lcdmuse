# Agent.md — 给 AI 编码代理的工作说明

> 本仓库是 **lcdkit / lcd-studio**：小尺寸 USB 副屏的显示管理内核 + 插件驱动 + 桌宠。
> 派生自 [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)（GPL-3.0）。
> 相对上游的修改：驱动插件化、核心去硬件、渲染与链路分层、桌宠框架。整体仍以 **GPL-3.0** 分发。

---

## 1. 不可违反的四条

1. **核心零驱动**：`lcdkit/` 内不得出现任何具体设备知识 —— 没有 VID/PID、型号名、命令字、协议魔数、波特率常量、串口名。
   识别设备是驱动自己的事。
2. **核心不 import 驱动**：`lcdkit/` 不得 `import drivers.*`，只经由 `lcdkit/plugin.py` 动态加载。
3. **中文优先**：界面与文案默认中文（i18n 做好后英文为次要语言）。
4. **可发布**：不依赖本机绝对路径、不依赖个人环境，别人 clone 下来就能跑。

违反第 1、2 条的改动一律不接受，即使功能可用。

## 2. 目录

```
lcdkit/                 核心（零驱动）
  lcd_comm.py           契约：错误族、Capability、DeviceInfo、Frame、LcdComm
  plugin.py             扫 drivers/*/driver.yaml，校验 api 版本与 requires
  registry.py           枚举 → probe → open；enabled_by_default=false 的插件默认不 probe
  render/               画布原语、颜色解析、脏矩形 diff、demo 测试卡
  transport/            通用链路层（串口封装），无设备标识
  pet.py                桌宠：pet.json 加载 + 状态机 + 锚点合成
  config.py             配置与日志
  cli.py                命令行入口
drivers/<id>/           驱动插件：driver.yaml + driver.py
  simulated/            不接硬件的示例驱动，也是写插件的样板
  _legacy/              原厂驱动的兼容栈（仅被适配器引用）
pets/<name>/            桌宠包：pet.json + frames/*.png
tools/                  辅助脚本（GIF 抽帧、预览合成）
tests/test_stage1.py    核心零硬件断言 + 契约/桌宠测试
docs/                   设计与阶段状态
```

## 3. 契约要点（改这里等于改所有驱动）

- 必须实现：`open()`、`close()`、`push_frame(Frame)`。
- 可选覆盖：`push_region(frame, x, y)`、`set_brightness`、`set_rotation`、`power`、`tick(now)`、`raw_command`。
- 未声明的能力被调用时抛 `Unsupported`；这些错误码就是对外（MCP）的 `error.code`。
- `DeviceInfo.native_rotation` 由装法决定、驱动侧固定；用户偏好的旋转在核心层叠加，两者别混。
- `Frame` 是 RGB888、逻辑方向；核心送帧前按 `native_rotation` 统一旋转。

## 4. 加一个驱动

1. `drivers/<id>/driver.yaml`：`id / name / version / api: 1 / entry: driver:类名 / requires / enabled_by_default`。
2. `driver.py` 继承 `lcdkit.lcd_comm.LcdComm`，实现 `probe/open/close/push_frame`。
3. 需要串口时用 `lcdkit.transport.serial_link.SerialLink`，端口与波特率从 `self.config` 取。
4. **`requires` 写 import 名**（`serial` 而非 `pyserial`，`usb` 而非 `pyusb`），写错会让插件永远"缺少依赖"。
5. `python -m lcdkit.cli drivers list` 确认 `available: true`。

参考 `drivers/simulated/`：它是完整的可运行样板。

## 5. 加一个桌宠

1. `pets/<name>/pet.json` + `frames/*.png`（透明底，建议 ≤300×400）。
2. 状态：`idle`（循环）、`eat`/`click`/`say`（播完回 idle）、`sleep`（保持）。
3. 从 GIF 抽帧：`python tools/petify.py --gif a.gif --state eat --out pets/<name>`。
4. 素材必须**可再分发**（MIT/CC0 等），并把作者与许可写进 `pet.json` 的 `source`。

## 6. 验收命令（改前改后都跑）

```bash
python -m lcdkit.cli drivers list                                  # 插件表
python -m lcdkit.cli probe                                         # 默认只探启用的插件
python -m lcdkit.cli render --offline --pet whale-girl --out out/preview.png   # 零硬件出图
python -m pytest tests/test_stage1.py -q                           # 含"核心零硬件"断言
```

三条硬判据（已在测试里固化）：

1. `lcdkit/` 里没有设备标识：十六进制常量、`VID_`/`PID_`、`COM\d+`。
2. `lcdkit/` 里没有协议魔数与波特率常量。
3. 把 `drivers/` 整个删掉后，核心仍可 import、仍能 `render --offline` 出图。

## 7. 不要做

- 不要把型号、序列号、VID/PID、串口号写进 `lcdkit/` 或文档正文；它们只属于驱动目录或用户自己的配置。
- 不要在核心引入 `numpy`/`pyusb` 之类重依赖；它们只应出现在具体驱动的 `requires` 里。
- 不要在没有设备所有者在场时操作真实屏幕，也不要抢占厂商软件正在占用的串口。
  本仓库的自动化测试一律用 `simulated` 或 `--offline`。
- 不要删上游版权与许可声明：本仓库是 GPL-3.0 衍生作品。

## 8. 资源（字库 / 主题）不在本仓库

`res/`（字库 + 主题，约 1 GB）不随本仓发布，避免克隆体积过大。缺字库时渲染会退回 Pillow 默认字体（不崩，但中文会缺字）。
需要时从上游仓库取：

```bash
git clone --depth 1 https://github.com/mathoudebine/turing-smart-screen-python /tmp/tss
cp -r /tmp/tss/res ./res
```

## 9. 已知缺口

- `library/` 是上游旧实现，尚未删除；`main.py` / `configure.py` / `theme-editor.py` 仍依赖它，删除要连入口一起重写。
- 原厂适配器的 `probe()` 依赖旧代码的串口枚举，未在真实硬件上验证。
- 区域绘制（`push_region`）未实现，能否支持取决于具体屏的协议。
- `lcdkit/service/`（常驻 daemon + IPC）与 `lcdkit/mcp/`（MCP 接口）尚未开工。
- 主题引擎仍是 `render/demo.py` 的测试卡，尚未接入 `res/themes`。

## 10. 提交前自检

```bash
python -m ruff check lcdkit drivers/*/driver.py tools/petify.py tools/pet_preview.py tests/test_stage1.py
```
