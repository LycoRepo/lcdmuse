# SPDX-License-Identifier: GPL-3.0-or-later

import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, ".")
from lcdmuse.pet import Pet

REPO = Path(".")
FONT = REPO/"res/fonts/SourceHanSansCN/SourceHanSansCN-Bold.otf"
FONT_R = REPO/"res/fonts/SourceHanSansCN/SourceHanSansCN-Regular.otf"
pet = Pet.load(REPO/"pets/whale-girl")

states = ["idle","eat","sleep","click","say"]
names = {"idle":"待机 idle","eat":"吃饭 eat","sleep":"睡觉 sleep","click":"被戳 click","say":"说话 say"}
CELL_W, CELL_H, LABEL_H = 320, 400, 34

sheet = Image.new("RGB", (CELL_W*len(states), CELL_H+LABEL_H), (12,16,24))
d = ImageDraw.Draw(sheet)
f_big = ImageFont.truetype(str(FONT), 22)
f_sm = ImageFont.truetype(str(FONT_R), 15)

for i, st in enumerate(states):
    spec = pet.states[st]
    fr = Image.open(spec.frames[len(spec.frames)//2]).convert("RGBA")   # 直接取该状态中间帧
    x0 = i*CELL_W
    d.rectangle([x0+6, 6, x0+CELL_W-6, CELL_H+LABEL_H-6], fill=(17,23,33), outline=(40,54,72))
    px = x0 + (CELL_W - fr.width)//2
    py = LABEL_H + (CELL_H - fr.height)//2
    sheet.paste(fr, (px, py), fr)
    d.text((x0+16, 8), names[st], font=f_big, fill=(130,225,255))
    d.text((x0+16, CELL_H+8), f"{len(spec.frames)} 帧 · {spec.fps:.0f} fps · {fr.size[0]}x{fr.size[1]}", font=f_sm, fill=(150,165,182))

out = REPO / "out/pet-sheet.png"
sheet.save(out)
print(json.dumps({"out": str(out), "size": sheet.size}, ensure_ascii=False))

screen = Image.new("RGB", (960,480), (10,14,22))
dd = ImageDraw.Draw(screen)
for y in range(480):
    t = y/479
    dd.line([(0,y),(960,y)], fill=(int(10+16*t), int(14+24*t), int(24+38*t)))
dd.text((40,36), "LCD STUDIO", font=ImageFont.truetype(str(FONT), 46), fill=(130,235,255))
dd.text((40,96), "960×480 · 桌宠按 300×400 上限合成 · 左下锚点", font=ImageFont.truetype(str(FONT_R), 22), fill=(175,190,208))
spec = pet.states["idle"]
fr2 = Image.open(spec.frames[len(spec.frames)//2]).convert("RGBA")
screen.paste(fr2, (16, 480-fr2.height-16), fr2)
out2 = REPO / "out/pet-on-screen.png"
screen.save(out2)
print(json.dumps({"out2": str(out2), "pet_size": fr2.size}, ensure_ascii=False))
