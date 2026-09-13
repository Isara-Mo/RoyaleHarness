# 首次坐标核对

`calibration.json` 是 1080×1920 的参考地面投影。历史样本覆盖两席位各 8 个静止加农炮落点，
不是新设备的自动验收证据。相同比例缩放可复用参考，不同比例会拒绝；换竞技场界面仍需核对。

1. 大号手动开好友战，AI 保持关闭，先保存一张自己的完整竞技场截图。
2. 使用项目 Python 执行：

   ```powershell
   .\.venv\Scripts\python.exe tools\coordinate_view.py --image 'C:\captures\battle.png' --output diagnostics\coordinates.html
   ```

   用浏览器打开结果；查看左右两路、河边和后场的地面中心，切换 owner 后相同模型落点应仍在同一屏幕位置。
   当前叠图工具使用 1080×1920 参考画布；其他尺寸截图需先准备对应参考尺寸图像，原始设备尺寸还须按比例验证。
3. 核对手牌槽中心与游戏实际手牌；参考点在 `config.py` 的 HAND_CARD_SLOTS 中。
   如果地面网格有偏移，先修改校准矩阵或提供独立 `calibration_path`，不要盲目改左右翻转逻辑。
4. 确认参考后，将 `settings.local.json` 的 `calibration_verified` 改为 `true`，
   进行一局有人观察的好友战，核对建筑的静止落点和两路出牌；发现偏移立即 Ctrl+C 停止并修正。
   静止建筑最适合验证落点；火球飞行起点、移动中的单位位置不能作为出生点真值。

首次建议只观察模式。设置确认标记只是允许受监督实战验证，并不能代替实战本身。

## 英雄火枪手技能

复制 `ability_calibration.example.json` 为 `ability_calibration.local.json`。
在只有一个英雄的对局中部署英雄火枪手，技能按钮出现时核对其中心坐标、屏幕尺寸；
调整 `point` 和 `size` 后才把 `verified` 改为 `true`。
当前仅支持 `controller_slot=1` 的该技能按钮；第二英雄控制器不在此标定范围。
未验证的按钮不会自动点击。不要把自己的 verified 文件打包给其他用户。
