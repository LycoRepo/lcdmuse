# SPDX-License-Identifier: GPL-3.0-or-later
"""调通义万相（DashScope）生成主题底图：提交 → 轮询 → 下载。

用法：
    python tools/gen_wallpaper.py --prompt "..." --out xx.png [--size 1280*720] [--n 1]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

BASE = "https://dashscope.aliyuncs.com/api/v1"
SUBMIT = f"{BASE}/services/aigc/text2image/image-synthesis"
TASK = f"{BASE}/tasks/"


def _key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if key:
        return key
    env = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("DASHSCOPE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("找不到 DASHSCOPE_API_KEY")


def _post(url: str, payload: dict, key: str, async_hdr: bool = False) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    if async_hdr:
        req.add_header("X-DashScope-Async", "enable")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _get(url: str, key: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1280*720")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--model", default="wanx2.1-t2i-turbo")
    ap.add_argument("--negative", default="文字, 水印, 边框, 低清晰度, 变形")
    args = ap.parse_args()

    key = _key()
    task = _post(SUBMIT, {
        "model": args.model,
        "input": {"prompt": args.prompt, "negative_prompt": args.negative},
        "parameters": {"size": args.size, "n": args.n, "prompt_extend": True},
    }, key, async_hdr=True)
    tid = task["output"]["task_id"]
    print(f"提交 {tid}")

    for _ in range(60):
        time.sleep(5)
        info = _get(TASK + tid, key)
        st = info["output"]["task_status"]
        if st == "SUCCEEDED":
            results = info["output"].get("results") or []
            outs = []
            stem = Path(args.out)
            for i, item in enumerate(results):
                url = item.get("url")
                if not url:
                    continue
                target = stem if len(results) == 1 else stem.with_name(f"{stem.stem}-{i + 1}{stem.suffix}")
                data = urllib.request.urlopen(url, timeout=120).read()
                target.write_bytes(data)
                outs.append(target)
                print("下载", target, len(data) // 1024, "KB")
            return 0 if outs else 1
        if st in ("FAILED", "CANCELED", "UNKNOWN"):
            print("失败：", json.dumps(info, ensure_ascii=False)[:400])
            return 1
        print("  ", st)
    print("超时")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
