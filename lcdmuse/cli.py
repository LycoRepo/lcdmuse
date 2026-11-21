# SPDX-License-Identifier: GPL-3.0-or-later
"""命令行入口：lcdmuse drivers / probe / render / pets / plugin / tray。

阶段 1 只到「契约 + 渲染 + 模拟驱动」，daemon / MCP 在阶段 5。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lcdmuse.config import Config, log
from lcdmuse.lcd_comm import Frame, LcdError
from lcdmuse.plugin_store import PluginError, install_plugin, list_user_plugins, remove_plugin
from lcdmuse.registry import Registry
from lcdmuse.compose import Composition
from lcdmuse.media import MediaSource
from lcdmuse.pet import Pet, find_pet, list_pets, pet_dirs
from lcdmuse.theme import find_theme, list_themes, theme_dirs


def _fmt_table(rows: list[dict]) -> str:
    if not rows:
        return "(空)"
    cols = ["id", "name", "version", "api", "origin", "available", "reason"]
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    head = "  ".join(c.ljust(widths[c]) for c in cols)
    line = "  ".join("-" * widths[c] for c in cols)
    body = "\n".join("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols) for r in rows)
    return f"{head}\n{line}\n{body}"


def cmd_drivers_list(args: argparse.Namespace, cfg: Config) -> int:
    reg = Registry(cfg.drivers_dirs)
    rows = reg.describe()
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print("驱动目录（前面的优先）:")
        for i, d in enumerate(cfg.drivers_dirs):
            print(f"  {i + 1}. {d}  ({'用户' if d == cfg.user_drivers_dir else '仓库自带'})")
        print(f"\n共 {len(rows)} 个插件\n")
        print(_fmt_table(rows))
    return 0


def cmd_probe(args: argparse.Namespace, cfg: Config) -> int:
    reg = Registry(cfg.drivers_dirs)
    found = reg.probe(cfg.drivers, only=args.plugin)
    out = [{"driver": p.id, "device": info.to_dict()} for p, info in found]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out else 1


def cmd_render(args: argparse.Namespace, cfg: Config) -> int:
    if args.offline:
        width = args.width or 960
        height = args.height or 480
        out_path = Path(args.out) if args.out else (cfg.ensure_out() / "offline.png")
        comp, theme_info, pet_info = _build_composition(args, cfg, width, height)
        image = comp.render((width, height))
        image.save(str(out_path))
        saved = str(out_path)
        frame = Frame.from_image(image)
        print(json.dumps({
            "ok": True, "mode": "offline", "drivers_loaded": 0,
            "frame": {"width": frame.width, "height": frame.height, "bytes": len(frame.data)},
            "png": str(Path(saved).resolve()),
            "theme": theme_info,
            "pet": pet_info,
        }, ensure_ascii=False, indent=2))
        return 0

    reg = Registry(cfg.drivers_dirs)
    plugin_id = args.plugin
    out_path = Path(args.out) if args.out else (cfg.ensure_out() / "render.png")

    driver_cfg = cfg.driver_config(plugin_id)
    if args.width:
        driver_cfg["width"] = args.width
    if args.height:
        driver_cfg["height"] = args.height
    driver_cfg["out_dir"] = str(Path(out_path).parent)

    drv = reg.open(plugin_id, driver_cfg)
    info = drv.open()
    log.info("已打开 %s (%s) %dx%d 能力=%s", info.name, info.id, info.width, info.height,
             [c.name for c in info.capabilities])

    comp, theme_info, pet_info = _build_composition(args, cfg, info.width, info.height)
    image = comp.render((info.width, info.height))
    frame = Frame.from_image(image)
    drv.push_frame(frame)
    image.save(str(out_path))
    saved = str(out_path)
    print(json.dumps({
        "ok": True,
        "driver": plugin_id,
        "device": info.to_dict(),
        "frame": {"width": frame.width, "height": frame.height, "bytes": len(frame.data)},
        "png": str(Path(saved).resolve()),
        "theme": theme_info,
        "pet": pet_info,
        "driver_state": drv.status() if hasattr(drv, "status") else {},
    }, ensure_ascii=False, indent=2))
    drv.close()
    return 0


def cmd_plugin_list(args: argparse.Namespace, cfg: Config) -> int:
    root = cfg.user_drivers_dir
    plugins = list_user_plugins(root)
    rows = [p.to_dict() for p in plugins]
    print(json.dumps({"ok": True, "dir": str(root), "count": len(rows), "plugins": rows},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_plugin_add(args: argparse.Namespace, cfg: Config) -> int:
    try:
        info = install_plugin(args.source, cfg.user_drivers_dir, force=args.force)
    except PluginError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "installed": info, "dir": str(cfg.user_drivers_dir)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_plugin_remove(args: argparse.Namespace, cfg: Config) -> int:
    try:
        out = remove_plugin(args.plugin_id, cfg.user_drivers_dir)
    except PluginError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, **out}, ensure_ascii=False, indent=2))
    return 0


def cmd_plugin_dir(args: argparse.Namespace, cfg: Config) -> int:
    root = cfg.ensure_user_drivers()
    print(json.dumps({"ok": True, "dir": str(root)}, ensure_ascii=False, indent=2))
    return 0


def cmd_tray(args: argparse.Namespace, cfg: Config) -> int:
    """托盘常驻：把玩家循环挂到右下角，右键菜单能暂停/开关桌宠/退出。"""
    from lcdmuse.tray import run_tray
    pet = None if args.pet in ("none", "off") else (args.pet or cfg.default_pet)
    return run_tray(cfg, args.plugin or "", interval=args.interval, pet=pet)


def cmd_pets_list(args: argparse.Namespace, cfg: Config) -> int:
    dirs = pet_dirs(cfg)
    detail = []
    for name in list_pets(dirs):
        try:
            detail.append(find_pet(name, dirs).describe())
        except Exception as exc:
            detail.append({"name": name, "error": str(exc)})
    print(json.dumps({"ok": True, "dirs": [str(d) for d in dirs], "pets": detail},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_themes_list(args: argparse.Namespace, cfg: Config) -> int:
    dirs = theme_dirs(cfg)
    themes = [t.describe() for t in list_themes(dirs)]
    print(json.dumps({"ok": True, "dirs": [str(d) for d in dirs],
                      "active": cfg.theme, "themes": themes}, ensure_ascii=False, indent=2))
    return 0


def cmd_metrics(args: argparse.Namespace, cfg: Config) -> int:
    from lcdmuse.metrics import CATALOG, read
    payload = {"ok": True, "values": read()}
    if args.catalog:
        payload["catalog"] = [{"name": k, "label": v[0], "unit": v[1]} for k, v in CATALOG.items()]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _build_composition(args: argparse.Namespace, cfg: Config, width: int,
                       height: int) -> tuple[Composition, dict | None, dict | None]:
    """底图 → 主题 → 桌宠。参数为空时用配置里的默认值。"""
    theme_id = args.theme if getattr(args, "theme", None) is not None else cfg.theme
    theme = find_theme(theme_id, theme_dirs(cfg)) if theme_id else None
    if theme is None and theme_id:
        log.warning("主题 %s 没找到，退回测试卡", theme_id)
    bg_path = args.background if getattr(args, "background", None) else cfg.background
    media = None
    if bg_path:
        try:
            media = MediaSource.open(bg_path, (width, height))
        except Exception as exc:
            log.warning("底图 %s 打不开: %s", bg_path, exc)

    pet = None
    name = args.pet if getattr(args, "pet", None) is not None else (cfg.default_pet if cfg.pet_enabled else None)
    if name and name not in ("none", "off"):
        try:
            pet = find_pet(name, pet_dirs(cfg)) or Pet.load(Path(cfg.pets_dir) / name)
            if getattr(args, "pet_state", None):
                pet.set_state(args.pet_state)
        except Exception as exc:
            log.warning("桌宠 %s 加载失败: %s", name, exc)

    comp = Composition(theme=theme, pet=pet, background=media, font_dir=Path(cfg.fonts_dir))
    return comp, (theme.describe() if theme else {"id": None}), (pet.describe() if pet else None)


def _maybe_draw_pet(args: argparse.Namespace, cfg: Config, canvas) -> dict | None:
    """桌宠是覆盖层：关掉它屏幕就是普通主题（SPEC 6）。"""
    name = args.pet if args.pet is not None else (cfg.default_pet if cfg.pet_enabled else None)
    if not name or name in ("none", "off"):
        return None
    pet_dir = Path(cfg.pets_dir) / name
    try:
        pet = Pet.load(pet_dir)
        if args.pet_state:
            pet.set_state(args.pet_state)
        x, y = pet.draw_on(canvas)
        info = pet.describe()
        info.update({"x": x, "y": y})
        return info
    except Exception as exc:
        log.warning("桌宠加载失败: %s", exc)
        return {"error": str(exc)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lcdmuse", description="副屏管理：零驱动核心 + 插件驱动")
    p.add_argument("--config", default=None, help="配置文件路径（默认仓库根 lcdmuse.yaml）")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("drivers", help="驱动相关")
    dsub = d.add_subparsers(dest="sub", required=True)
    dl = dsub.add_parser("list", help="列出插件")
    dl.add_argument("--json", dest="as_json", action="store_true")
    dl.set_defaults(func=cmd_drivers_list)

    pr = sub.add_parser("probe", help="探测设备")
    pr.add_argument("--plugin", default=None)
    pr.set_defaults(func=cmd_probe)

    r = sub.add_parser("render", help="渲染一帧并推给驱动")
    r.add_argument("--plugin", default="simulated")
    r.add_argument("--out", default=None)
    r.add_argument("--width", type=int, default=None)
    r.add_argument("--offline", action="store_true", help="不加载任何驱动，纯渲染出图")
    r.add_argument("--pet", default=None, help="桌宠名；传 none 表示关掉桌宠")
    r.add_argument("--pet-state", default=None, help="idle/eat/sleep/click/say")
    r.add_argument("--theme", default=None, help="主题插件 id；传空字符串=用测试卡")
    r.add_argument("--background", default=None, help="底图：图片 / GIF / 视频路径")
    r.add_argument("--height", type=int, default=None)
    r.set_defaults(func=cmd_render)

    pg = sub.add_parser("plugin", help="用户插件：安装 / 卸载 / 查看")
    pgsub = pg.add_subparsers(dest="sub", required=True)
    pgl = pgsub.add_parser("list", help="列出用户目录里装的插件")
    pgl.set_defaults(func=cmd_plugin_list)
    pga = pgsub.add_parser("add", help="装一个插件（目录或 .zip）")
    pga.add_argument("source")
    pga.add_argument("--force", action="store_true", help="已存在时覆盖")
    pga.set_defaults(func=cmd_plugin_add)
    pgr = pgsub.add_parser("remove", help="卸载用户插件")
    pgr.add_argument("plugin_id")
    pgr.set_defaults(func=cmd_plugin_remove)
    pgd = pgsub.add_parser("dir", help="打印用户插件目录（不存在则创建）")
    pgd.set_defaults(func=cmd_plugin_dir)

    tr = sub.add_parser("tray", help="托盘常驻（右键菜单：暂停/桌宠/退出）")
    tr.add_argument("--plugin", default="", help="留空自动探测设备")
    tr.add_argument("--interval", type=float, default=1.0, help="推帧间隔（秒）")
    tr.add_argument("--pet", default="", help="桌宠名；传 none 表示关掉桌宠")
    tr.set_defaults(func=cmd_tray)

    th = sub.add_parser("themes", help="主题插件（themes/<id>/theme.yaml）")
    thsub = th.add_subparsers(dest="sub", required=True)
    thl = thsub.add_parser("list", help="列出仓库与用户目录里的主题")
    thl.set_defaults(func=cmd_themes_list)

    mt = sub.add_parser("metrics", help="打印当前指标（主题绑的就是这些名字）")
    mt.add_argument("--catalog", action="store_true", help="附带指标清单与说明")
    mt.set_defaults(func=cmd_metrics)

    pe = sub.add_parser("pets", help="桌宠相关")
    pesub = pe.add_subparsers(dest="sub", required=True)
    pel = pesub.add_parser("list", help="列出桌宠")
    pel.set_defaults(func=cmd_pets_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    try:
        return int(args.func(args, cfg) or 0)
    except LcdError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
