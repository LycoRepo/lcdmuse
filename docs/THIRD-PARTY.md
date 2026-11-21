# 第三方与借鉴清单

本项目（lcdmuse）的代码以 GPL-3.0-or-later 发布。下面这些项目**没有被整体打包进来**，
而是参考了它们的设计/状态机/文案策略，然后按副屏（3 fps、无鼠标）的约束重写。
署名与许可见下，修改点在「我们怎么改的」里写明，便于回溯。

## 桌宠行为

### PC2005-cloud/dsh-pet — MIT

- 仓库：https://github.com/PC2005-cloud/dsh-pet
- 借鉴：**工作状态六档**（思考 / 工作 / 等待 / 成功 / 出错 / 整理）的组织方式、
  「状态气泡挂在头顶」的做法、以及「睡觉是入睡 → 循环 → 醒来」的分段思路。
- 我们怎么改的：只搬状态语义和文案风格，没有搬它的 TypeScript 实现与素材链；
  气泡改由 Pillow 在画布上画（`lcdmuse/compose.py::_draw_bubble`），
  动画仍走精灵图逐帧（`lcdmuse/pet.py`），因为副屏没有 DOM。
- 落到代码：`lcdmuse/roam.py` 的 `ACTIVITY` 表、`DOZE_AFTER` 打盹阈值。

### Sutera-Diffusus/dsh-whale-musume — MIT

- 仓库：https://github.com/Sutera-Diffusus/dsh-whale-musume
- 借鉴：**按工具类型切换姿态**的思路（读文件 / 改文件 / 跑命令 → 不同动作与说法），
  以及「成就/小游戏驱动长期动力」的产品结构。
- 我们怎么改的：把「工具类型」收敛成六个 DSH 状态（thinking / working / waiting /
  review / success / error），因为副屏上没人看 45 项成就；成就系统暂未实现，
  只保留了「任务完成加经验」这一条最小闭环（`lcdmuse/mood.py::task_done`）。
- 落到代码：`lcdmuse/roam.py::set_activity`。

### vlln/whale-girl — MIT

- 仓库：https://github.com/vlln/whale-girl
- 借鉴：**养成与状态机**——拖拽/喂食/玩耍、空闲 60 秒打盹、互动唤醒、
  任务完成欢呼、等待批准时的期待姿态、以及「按优先级切换状态」的语义。
- 我们怎么改的：没有搬它的 Web GUI 与 Tauri 桌面壳；养成数据落成本地 JSON
  （`%APPDATA%\lcdmuse\pet-stats.json`），接口由本地面板与 MCP 暴露；
  「拖拽」在副屏上没有对应交互，改成多屏巡游（走过去就算它自己遛弯）。
- 落到代码：`lcdmuse/mood.py`（等级 / 经验 / 饱食度 / 亲密度）、
  `lcdmuse/roam.py`（空闲打盹、喂食/玩耍触发、换屏）。

## 桌宠素材

### LINNoot/dsh-co-pet — MIT（代码）；素材随其仓库

- 仓库：https://github.com/LINNoot/dsh-co-pet
- 使用：`pets/whale-girl-hd/spritesheet.webp`（8 列 × 11 行，单帧 192×208）
  与 `pet.json` 中的行→动作映射。
- 修改：动作行到本项目的状态名（idle / walk / eat / say / click / sleep / shy /
  stretch）重新对应；缩放改为按屏幕高度比例（`fit_height`）。

### DejavuMoe/deepseek_wale_girl — MIT（表情包素材）

- 仓库：https://github.com/DejavuMoe/deepseek_wale_girl
- 使用：`pets/whale-girl/frames/*.png`（由 `tools/petify.py` 从 GIF 抽帧、裁掉标签）。
- 说明：角色形象为网友二创，非官方；仅使用 MIT 覆盖的表情包素材。

### whale-girl-pet 的 Q 版立绘 — CC BY-NC-SA 4.0（**未入库**）

- 本机自用素材，放在 `%APPDATA%\lcdmuse\pets\whale-girl-q\`，**不进仓库**，
  因为许可证禁止商用且要求衍生同协议，和 GPL-3.0 发布版不兼容。
  仓库里缺这只宠物时会自动退回 `whale-girl-hd`。

## 屏幕驱动

### FoxPodZ/ryuo-iv-screen — GPL-3.0-or-later

- 仓库：https://github.com/FoxPodZ/ryuo-iv-screen
- 使用：爱国者（1D6B:0113，百一 / Baiyi 方案）的 HID 命令通道与图像通道格式
  作为起点；帧号、帧内序号、唤醒顺序等在本仓库实测修正（见
  `docs/AI-GUIDE.md` 与驱动仓库提交历史）。

### turing-smart-screen-python — GPL-3.0

- 仓库：https://github.com/mathoudebine/turing-smart-screen-python
- 使用：本项目的底座，保留原许可、原作者署名；驱动已全部剥成插件。

## 说明

- 上述「借鉴」均未复制对方源码文件；如果你认为某段实现应当保留原版权头，
  请在 issue 里指出，我们会补上。
- 静态资源（字体、表情包、精灵图）各自的许可见其目录内说明与本节条目。
