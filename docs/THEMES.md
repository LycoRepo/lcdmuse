# 主题一览

仓库自带两套主题，都用真实底图 + 蒙版 + 信息层三层结构。两块屏可以各用一套（`lcdmuse.yaml` 里的 `device_themes`）。

| 主题 id | 名称 | 设计尺寸 | 底图来源 |
|---|---|---|---|
| `deepsea` | 深海月光 | 960×480 | 通义万相生成 |
| `neon-harbor` | 夜港霓虹 | 1480×720 | 通义万相生成 |
| `bezel-mask` | 侧栏蒙版 | 960×480 | 程序化渐变（纯色底 + 蒙版） |

## 目录结构

```
themes/<id>/
  theme.yaml    # api / size / background / overlay
  bg.jpg        # 底图（可被 lcdmuse.yaml 的 background: 覆盖）
  mask.png      # RGBA 蒙版：压暗、玻璃面板、细高光
```

## 重新生成底图

底图是生图模型出的（不是程序画的风扇形渐变），提示词和尺寸记在这里，方便重画或做新配色：

**deepsea（960×480，2:1）** — 先生成 1280×720 再居中裁成 2:1
```
深海夜晚，一束月光从右上斜射入水中形成体积光，远处的鲸鱼剪影，
细小气泡与浮游微粒，深蓝色调，安静神秘，电影感，极简构图，画面右下角留出空白
```

**neon-harbor（1480×720）** — 先生成 1280×720 再缩放到 1480×720
```
夜晚的港口城市，远处高楼天际线亮着暖黄和青色窗灯，水面上是霓虹倒影，
右上方一轮明月和淡淡光晕，深紫与靛蓝色调，赛博朋克但克制安静，
宽银幕构图，无人物，画面左侧与底部留出较暗的空白
```

命令（需要 `DASHSCOPE_API_KEY`，会从 `%LOCALAPPDATA%\hermes\.env` 自动读）：

```
python tools/gen_wallpaper.py --size 1280*720 --n 2 --out out/deepsea.png --prompt "…"
python tools/make_theme_art.py --masks-only     # 换完底图只补蒙版
```

`tools/make_theme_art.py` 不带参数时会用程序化方式重画底图（不需要联网），保留作为离线兜底。

## 换底图不改排版

`theme.yaml` 的 `size` 是设计坐标，渲染到别的分辨率时 overlay 会等比缩放。只想换图不想动排版：
把新图裁成 `size` 的比例存成 `bg.jpg`，再跑一次 `--masks-only` 对齐蒙版即可。

## 授权

底图为本项目用生图模型生成的原创图（通义万相，DashScope 服务条款允许商用）。蒙版与排版代码同本项目授权。
