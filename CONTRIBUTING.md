# 参与 lcdpilot

先读 [`docs/AI-GUIDE.md`](docs/AI-GUIDE.md) —— 里面是硬规矩、契约和验收命令，比这份更细。

## 最欢迎的几种贡献

- **加驱动**：`drivers/<id>/`，一个目录就是一个机型，核心不用改
- **加桌宠**：`pets/<name>/`，素材必须可再分发
- **主题**：`res/themes/<name>/`（主题目录不随本仓发布，可在自己仓库里发布后投稿）
- **文档与翻译**：中文措辞、英文对照、踩坑记录
- **缺陷报告**：能复现的最小步骤 + 平台版本

## 硬门槛（不满足就不合）

1. `lcdpilot/` 里不得出现设备知识：VID/PID、型号名、命令字、协议魔数、波特率常量、串口名。
2. `lcdpilot/` 不得 `import drivers.*`；只经 `lcdpilot/plugin.py` 动态加载。
3. 界面与文案默认中文。
4. 不依赖本机绝对路径、不依赖个人环境。

改完自己跑一遍：

```bash
python -m lcdpilot.cli drivers list
python -m lcdpilot.cli render --offline --pet whale-girl --out out/preview.png
python -m pytest tests/test_stage1.py -q
python -m ruff check lcdpilot drivers/*/driver.py tools/petify.py tools/pet_preview.py tests/test_stage1.py
```

四条都过再提 PR。

## 提 PR 的规矩

- 一事一 PR；大改动拆成能独立评审的几步。
- 默认保持向后兼容；要破坏兼容先开 issue 说清楚。
- 提交信息写「为什么」，PR 正文附**实际命令与输出**，不要写「应该可以」。
- 新增 `.py` 保留文件头的 `SPDX-License-Identifier: GPL-3.0-or-later`。
- 不动上游版权与许可文件（`LICENSE`、`AUTHORS`、`COPYRIGHT`）。

## 素材与第三方代码

- 图片 / GIF / 字体：必须是 MIT、CC0、CC BY 之类**允许再分发**的许可，并在 `pet.json` 或主题文档里写明作者与出处。
- **不接受** CC BY-NC（禁止商用）、未标注授权的网络图片、带水印的素材。
- 抄别人的代码要写清来源与许可；GPL 代码只能以 GPL 兼容方式引入。

## 法律声明

提交即表示：内容由你原创或你有权提交，并同意以 **GPL-3.0-or-later** 随本项目分发。
