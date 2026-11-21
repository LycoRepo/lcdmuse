# SPDX-License-Identifier: GPL-3.0-or-later
"""lcdmuse —— 副屏管理核心：零驱动、只认契约。"""
__version__ = "0.1.0"

# numpy/OpenBLAS 默认按核数开线程，每个线程预留 8 MB（64 线程就是 512 MB）。
# 这个项目只把它当数组工具用，单线程足够，顺手把默认值压下来。
import os as _os
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
_os.environ.setdefault("OMP_NUM_THREADS", "1")
