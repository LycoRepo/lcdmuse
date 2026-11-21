# lcdpilot

把小尺寸 USB 副屏（串口 CDC / USB HID）当第二块显示器用：采集系统指标 → 画进画布 → 推帧到屏。
**核心零驱动，驱动全是插件**；界面中文；带桌宠。

![Python](https://img.shields.io/badge/Python-3.10%2B-3670A0?style=flat-square&logo=python&logoColor=ffdd54)
![Platform](https://img.shields.io/badge/Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue?style=flat-square)](./LICENSE)

> 本仓是 [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)（GPL-3.0）的二次封装：
> 驱动拆分插件化、核心剥离设备知识、界面中文化、新增桌宠框架。改动清单见 [`docs/MODIFICATIONS.md`](docs/MODIFICATIONS.md)。
> 给 AI 代理与贡献者的工作约定见 [`docs/AI-GUIDE.md`](docs/AI-GUIDE.md)。

## 现在能用的

| | 状态 |
|---|---|
| 驱动契约（`open` / `push_frame` / 能力声明 / 错误码） | 可用 |
| 插件系统（扫目录、校验 api 版本与依赖、按需加载） | 可用 |
| 画布原语（矩形、圆、文字、图片、脏矩形 diff、透明合成） | 可用 |
| 桌宠框架（`pet.json` + 状态机 + 锚点合成） | 可用 |
| 命令行（`drivers list` / `probe` / `render` / `pets list`） | 可用 |
| 离线渲染（无硬件出图，用于开发与测试） | 可用 |
| 原厂驱动适配层（7 个上游机型） | 默认不加载，未在真实硬件上验证 |
| 主题引擎接 `res/themes` | 未接（现在只有测试卡） |
| `lcdpilot-daemon` + `lcdpilot-mcp`（给别的程序调用） | 未开工 |

## 快速开始

```bash
git clone https://github.com/LycoRepo/lcdpilot.git
cd lcdpilot
pip install -e ".[dev]"
```

没接屏幕也能跑：

```bash
python -m lcdpilot.cli drivers list                       # 插件表
python -m lcdpilot.cli probe                              # 探测设备（默认只探启用的插件）
python -m lcdpilot.cli render --offline --pet whale-girl --out out/preview.png
python -m lcdpilot.cli pets list
```

`--offline` 走内置的 `simulated` 驱动，不碰任何硬件。

## 目录

```
lcdpilot/          核心：契约、插件加载、注册表、渲染、链路、桌宠、CLI
drivers/<id>/      驱动插件（driver.yaml + driver.py），simulated 是样板
pets/<name>/       桌宠（pet.json + frames/*.png）
tools/             GIF 抽帧、预览合成
tests/             契约 / 插件 / 桌宠 / 「核心零设备知识」断言
docs/              AI-GUIDE（工作约定）、阶段状态、改动清单
library/           上游旧实现，仍在过渡期，勿加新代码
```

## 加驱动

驱动就是往 `drivers/` 加一个目录，核心不用改：

1. `drivers/<id>/driver.yaml`：`api: 1`、`entry`、`requires`（写 import 名，如 `serial`）
2. `driver.py` 继承 `lcdpilot.lcd_comm.LcdComm`，实现 `probe / open / close / push_frame`
3. `python -m lcdpilot.cli drivers list` 看到 `available: true` 就成了

细节与验收标准见 [`docs/AI-GUIDE.md`](docs/AI-GUIDE.md) 第 6、8 节。

## 桌宠

默认款是 `whale-girl`（5 个状态 × 8 帧，透明底，≤300×400）。
可以关：`--pet none`、配置里 `pet_enabled: false`。
自己做一套：`python tools/petify.py --gif a.gif --state eat --out pets/my-pet`。
素材必须可再分发，并在 `pet.json` 的 `source` 里写清作者与许可。

## 与上游的关系

本仓是 GPL-3.0 衍生作品：上游的 `LICENSE`、`AUTHORS`、`COPYRIGHT` 原样保留，
被我们改动或新增的文件列在 [`docs/MODIFICATIONS.md`](docs/MODIFICATIONS.md)。
上游的驱动实现仍以 `library/`（及适配层 `drivers/_legacy/`）的形式存在，供 7 个原厂机型使用；
过渡期结束后会删除，届时只剩 `lcdpilot/` + `drivers/`。

上游文档（机型差异、主题格式、故障排查）仍然有效，见
[turing-smart-screen-python wiki](https://github.com/mathoudebine/turing-smart-screen-python/wiki)。

## 许可

**GPL-3.0-or-later**，见 [`LICENSE`](./LICENSE)。
你可以使用、修改、再分发、收费分发本软件；分发的成品必须同样以 GPL-3.0 提供完整源码，
并保留上游版权声明与许可文本。

本软件是第三方开源实现，与任何屏幕厂商（Turing / XuanFang / Kipye / WeAct 等）无关，
不代表、也不受其授权；产品与公司名称归各自所有者。硬件本身的售后问题请找卖家。

## 致谢

- [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)：契约、渲染、调度、主题格式均源自该项目
- 桌宠素材来自公开可再分发的美术资源，具体作者与许可见各自的 `pets/<name>/pet.json`
