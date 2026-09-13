# RoyaleHarness：agent 工作说明

本项目是在线游戏的感知—推理—触摸执行桥接层。默认复用 FirstLight V4 权重，
通过 Root MuMu 读取 Null’s Royale 原生状态，使用 ADB 触摸执行动作。

## 按任务读取

- 新电脑／模拟器配置、启动排错、首次对战验证：读取
  [.agents/skills/configure-royaleharness/SKILL.md](.agents/skills/configure-royaleharness/SKILL.md)。
- 手动安装步骤：[docs/SETUP.md](docs/SETUP.md)。
- 修改观测或执行逻辑：先看 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 打包公开版本：[docs/RELEASE.md](docs/RELEASE.md)。

## 保持仓库精简

机器路径、实例、账号、校准确认写入已忽略的 `settings.local.json` 和
`ability_calibration.local.json`。参考模板保持通用；不要为每台机器创建一个启动脚本，
也不要把环境探测、训练或未请求的机制适配混进一次配置任务。

优先复用 `setup.ps1`、`tools/preflight.py`、`probe/deploy_probe.ps1`、
`tools/inspect_players.py` 和 `start_agent.bat`。配置变化需重新启动进程才能生效。
同一实例一次只运行一个负责触摸的 AI 进程。

遵循用户当前任务范围及已有授权。已授权的安装、重启或自动测试可以继续；
缺少无法读取的关键信息或需要未授权操作时再询问，不要求用户逐步重复确认。

## 修改与验证

保留用户改动。普通环境配置无需修改模型、决策间隔或坐标算法。
修复实际问题时添加相应边界测试，并使用项目 Python 运行相关检查：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

区分只读检查、模型推理、实际输入与游戏确认。发送成功不能替代手牌／技能变化确认，
终局前未确认的动作保留为未确认。源码测试通过、整局运行通过和策略强度是不同结论。

分发文件由 `SHA256SUMS.json` 控制。只更新已审查文件的校验和，遵守打包工具的文本换行规则；
新增技能文件也要加入清单。不要打包账号配置、`.venv`、SDK 备份、截图或原始对局。
