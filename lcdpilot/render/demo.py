# SPDX-License-Identifier: GPL-3.0-or-later
"""临时主题：给驱动/渲染链路做离线验证用（阶段 2 换成 res/themes 的真主题引擎）。"""
from __future__ import annotations

import time

from lcdpilot.render.canvas import Canvas

FONT_BOLD = "SourceHanSansCN/SourceHanSansCN-Bold.otf"
FONT_REGULAR = "SourceHanSansCN/SourceHanSansCN-Regular.otf"


def render_demo(width: int, height: int, font_dir: str, *, now: float | None = None) -> Canvas:
    """画一张能看出渲染质量、也能看出中文是否正常的测试卡。"""
    now = now if now is not None else time.time()
    cv = Canvas(width, height, background=(10, 14, 22), font_dir=font_dir)

    # 竖向渐变
    for y in range(height):
        t = y / max(1, height - 1)
        cv.rect(0, y, width, 1, fill=(int(10 + 18 * t), int(14 + 26 * t), int(24 + 40 * t)))

    cv.rect(6, 6, width - 12, height - 12, outline=(90, 210, 255), outline_width=2)
    cv.text(24, 20, "LCD STUDIO", FONT_BOLD, max(18, height // 16), (130, 235, 255))
    cv.text(24, 20 + height // 13, "零驱动核心 · 离线渲染验证", FONT_REGULAR, max(12, height // 34), (190, 205, 220))
    cv.text(width - 24, 22, time.strftime("%H:%M:%S", time.localtime(now)), FONT_BOLD,
            max(18, height // 16), (255, 196, 96), anchor="ra")

    # 三条假数据条
    y0 = int(height * 0.42)
    for i, (label, ratio) in enumerate((("CPU", 0.37), ("GPU", 0.62), ("内存", 0.48))):
        y = y0 + i * int(height * 0.13)
        cv.text(24, y, label, FONT_REGULAR, max(12, height // 30), (200, 215, 230))
        cv.bar(96, y + 4, int(width * 0.5), max(8, height // 40), ratio,
               bar_color=(80, 200, 255), background=(32, 40, 52), outline=(60, 72, 88))
        cv.text(96 + int(width * 0.5) + 12, y, f"{int(ratio * 100)}%", FONT_REGULAR,
                max(12, height // 30), (160, 240, 200))

    # 色条
    for i, v in enumerate((30, 70, 110, 150, 190, 230, 255)):
        cv.rect(24 + i * 30, height - 52, 24, 20, fill=(v, 255 - v, 140))
    cv.text(24, height - 28, f"{width}×{height}  RGB888  demo frame", FONT_REGULAR,
            max(10, height // 44), (150, 165, 180))
    return cv
