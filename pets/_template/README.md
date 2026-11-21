# 做一个桌宠

1. 把 PNG 序列帧放进 `frames/`（建议 ≤300×400，透明底）
2. 改 `pet.json`：`states` 里每个状态给 `frames` 列表、`fps`、`loop`
3. 可选状态名：`idle`（循环）、`eat`/`click`/`say`（播完回 idle）、`sleep`（保持）
4. 主程序里 `lcd_pet_list` 就能看到它，`lcd_pet_set(id, pet="目录名")` 切过去

从 GIF 抽帧：`python tools/petify.py --gif x.gif --state eat --out pets/我的桌宠`
