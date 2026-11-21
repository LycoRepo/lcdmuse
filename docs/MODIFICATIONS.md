# 改动清单（相对上游）

上游项目：[mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)，许可证 **GPL-3.0-or-later**。
本仓在其之上做了二次封装，按 GPL-3.0 §5(a) 在此说明改动范围。

## 原样保留

| 文件/目录 | 说明 |
|---|---|
| `LICENSE` | 上游 GPL-3.0 全文，未改动 |
| `AUTHORS`、`COPYRIGHT` | 上游版权声明，未改动 |
| `library/` | 上游实现（含 8 个 `LcdComm*` 驱动），未改动；新版通过 `drivers/_legacy/` 引用它 |
| `config.yaml`、`main.py`、`configure.py`、`theme-editor.py`、`simple-program.py`、`*.spec` | 上游入口，未改动 |

## 新增

| 路径 | 说明 |
|---|---|
| `lcdmuse/` | 新核心：契约（`lcd_comm.py`）、插件加载（`plugin.py`）、注册表（`registry.py`）、渲染（`render/`）、链路（`transport/`）、桌宠（`pet.py`）、配置（`config.py`）、CLI（`cli.py`） |
| `drivers/<id>/` | 驱动插件：`simulated`（新写）、`turing_rev_a~d`、`turzx_usb`、`weact_a/b`（从 `library/lcd/` 移植的适配层） |
| `drivers/_legacy/` | 上游 `library/lcd/` 的副本，仅作适配层依赖 |
| `lcdmuse/plugin_store.py` | 用户插件目录的安装/卸载（目录或 zip，装完即时校验，失败回滚） |
| `pets/` | 桌宠框架、默认桌宠 `whale-girl`、模板 `_template` |
| `tools/petify.py`、`tools/pet_preview.py` | GIF 抽帧、预览合成 |
| `tests/test_stage1.py` | 契约 / 插件 / 桌宠 / 「核心零设备知识」断言 |
| `docs/AI-GUIDE.md` | 工作约定（合并了上游 `AGENTS.md` 仍在用的部分） |
| `docs/STAGE1-STATUS.md` | 阶段状态与缺口 |
| `pyproject.toml`、`lcdmuse.yaml` | 打包元数据与运行配置 |

## 修改

| 文件 | 改动 |
|---|---|
| `README.md` | 重写为本项目说明；保留上游项目链接与许可说明 |
| `CONTRIBUTING.md` | 重写为贡献规则（原文件是上游的通用模板） |
| `CODE_OF_CONDUCT.md` | 重写为原创短版，不再引用 Contributor Covenant 全文 |
| `SECURITY.md` | 重写：报告渠道、安全模型、范围 |
| `.gitignore` | 增加 `out/`、`logs/`、`__pycache__/`、`*.pyc`、`drivers.off/` |

新增与修改的 `.py` 文件首行统一带 `SPDX-License-Identifier: GPL-3.0-or-later`。

## 删除

| 文件 | 原因 |
|---|---|
| `AGENTS.md` | 内容并入 `docs/AI-GUIDE.md`，避免两份说明互相冲突 |
| `docs/DESIGN-stage1-driver-plugin.md` | 内部设计稿，含设备识别信息，未发布 |

## 未随本仓发布

`res/`（字库 + 主题，约 1 GB）、`external/`、`tests/library/`、`tools/windows-installer/`、`tools/lsusb/`
—— 均为上游资源或二进制。需要 `res/` 时见 `docs/AI-GUIDE.md` 第 12 节。
