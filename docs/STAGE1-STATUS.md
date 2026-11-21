# 阶段 1 状态

分支：`main`　提交：见 `git log`

## 已落地

| 层 | 文件 | 说明 |
|---|---|---|
| 契约 | `lcdmuse/lcd_comm.py` | 错误码 / `Capability` / `DeviceInfo` / `Frame` / `LcdComm`（open、close、push_frame 必需，其余可选） |
| 插件 | `lcdmuse/plugin.py` | 扫 `drivers/*/driver.yaml`，校验 `api` 版本、`requires`（用 **import 名**，如 `serial` 而非 `pyserial`），失败只标 unavailable |
| 注册表 | `lcdmuse/registry.py` | 枚举 → probe → open；`enabled_by_default: false` 的插件默认不 probe |
| 渲染 | `lcdmuse/render/` | `canvas.py`（原语 + 脏矩形 diff + `paste_alpha`）、`color.py`、`demo.py`（测试卡） |
| 链路 | `lcdmuse/transport/serial_link.py` | 通用串口封装，无端口名/波特率/命令字 |
| 桌宠 | `lcdmuse/pet.py` | `pet.json` 加载、状态机（读时求值）、锚点合成；一次性状态播完回 idle |
| CLI | `lcdmuse/cli.py` | `drivers list` / `probe` / `render [--offline] [--pet …]` / `pets list` |
| 驱动 | `drivers/simulated/` | 不碰硬件的示例驱动，也是写插件的样板 |
| 驱动 | `drivers/turing_rev_{a,b,c,d}`、`turzx_usb`、`weact_{a,b}` | 原厂驱动移植成插件（适配层指向 `drivers/_legacy/`），默认不加载，作契约回归 |
| 桌宠 | `pets/whale-girl/` | 默认款（5 个状态 × 8 帧，300×400 上限，透明底） |
| 桌宠 | `pets/_template/` | 用户自建桌宠的模板（含占位帧） |
| 工具 | `tools/petify.py` | GIF → 桌宠帧（裁标签、抠透明、缩放） |
| 测试 | `tests/test_stage1.py` | 14 个用例：契约、插件、桌宠、核心零硬件 |

## 验收（全部实测）

| # | 项 | 结果 |
|---|---|---|
| ① | 核心零设备标识（十六进制常量 / `VID_` / `COM\d+`） | 0 行 |
| ② | 核心零协议魔数与波特率常量 | 0 行 |
| ③ | 核心不 import 驱动 | 0 行 |
| ④ | 运行时 import 图无驱动 | `[]` |
| ⑤ | 插件枚举 | 8 个，全部 available |
| ⑥ | 零硬件出图 | 960×480 / 1,382,400 B |
| ⑦ | 桌宠 | `whale-girl`、`_template`；`--pet-state say` 出图正常 |
| ⑧ | 删掉 `drivers/` 后 | 核心可 import、可离线出图（rc=0） |

`pytest tests/test_stage1.py` → **14 passed**

## 判据（写进项目约定）

> `lcdmuse/` 内不存在任何具体设备知识（VID/PID/型号/命令字/协议魔数/波特率/串口名），也不 import `drivers/`；
> 把 `drivers/` 整个删掉后，核心仍可 import、仍能渲染主题与桌宠。

## 已知缺口（不藏）

1. **`library/`（上游旧实现）尚未删除**：`main.py` / `configure.py` / `theme-editor.py` 仍依赖它。
   删它要连入口一起重写，单独删除会直接弄坏上游入口。
2. **原厂驱动的 `probe()` 依赖旧代码的串口枚举**，未在真实硬件上验证。
3. **区域绘制（`push_region`）未实现**：是否可行取决于具体屏的协议，需要硬件探针验证。
4. **`lcdmuse/service/`（daemon + IPC）与 `lcdmuse/mcp/` 未开工**（阶段 5）。
5. 主题引擎仍是 `render/demo.py` 的测试卡，`res/themes` 里的主题还没接。
6. 桌宠帧是逐张 PNG（40 张），后续可打包成单文件减少小文件数量。

## 开发依赖

可选依赖：`numpy`、`pyusb`、`pycryptodome`（原厂驱动）、`pytest`。
安装：`pip install -e ".[drivers,dev]"`。
