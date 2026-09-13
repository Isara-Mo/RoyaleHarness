# 安装、配置与排错

所有命令在仓库根目录执行。可先让 agent 阅读 [AGENTS.md](../AGENTS.md) 完成配置，或按本文手动操作。

支持的游戏库 `libg.so` SHA-256：
`110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783`。
同一版本号不保证原生库与内容一致，安装器会校验指纹。

## 环境与依赖

| 项目 | 环境要求 |
| --- | --- |
| 主机 | Windows；启动与构建脚本面向 PowerShell |
| 模拟器 | MuMu，启用 Root 与 ADB，连接正确的实例 |
| 游戏 | ARM64 Null’s Royale，原生库指纹与上述支持版本一致 |
| 显示 | 1080×1920 竖屏参考布局；首次使用需核对地面与手牌位置 |
| Python | 3.12 |
| 推理环境 | PyTorch 2.11.0 + CUDA 12.8；另提供 CPU 安装选项 |
| 上游依赖 | 固定提交的 FirstLight 推理代码、冻结目录数据和所选权重 |
| 编译工具 | 使用预编译探针无需 NDK；重新构建验证使用 NDK r27c |

发行包包含本项目源码与稳定探针，不包含模型权重、上游目录资源、游戏 APK 或原始 SDK。

## 快速开始

以下命令在**发布目录**中执行。目录名称可自定；压缩包内目前保留
`CR_PlayCard_Agent-v0.1.0-preview` 名称，README 使用项目名 RoyaleHarness。

推荐目录结构：

```text
workspace/
├── FirstLight_CR/             # 单独获取的上游代码、目录数据与权重
└── RoyaleHarness/             # 本项目发布目录，也可保留解压后的名称
    ├── README.md
    ├── setup.ps1
    └── settings.example.json
```

1. 从 [FirstLight 上游](https://gitlab.com/firstlight3/FirstLight_CR) 获取代码与所需模型权重，
   使用提交 `28d66cc0a5d65888515e22fdf22f11d783b65efb`。Git LFS 文件必须下载为真实文件。
   保留 `native_runner/data/competitive` 的冻结目录数据。
   本包不含上游代码、目录数据或权重；依赖指纹见 `upstream.lock.json`。
   不需要执行上游构建／安装离线 APK 的步骤。
2. 安装 Python 3.12，在本项目目录打开 PowerShell：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   脚本创建本项目 `.venv`，安装本机验证使用的 Torch 2.11.0 / CUDA 12.8 依赖组合。
   CPU 使用 `-TorchBuild cpu`，并将配置中的 `device` 改为 `cpu`。
   已有 Python 可用 `-Python 'C:\path\python.exe'`；现有推理环境也可通过
   `CR_AGENT_PYTHON` 环境变量供启动器使用。
3. 编辑生成的 `settings.local.json`：设置 `firstlight_dir`、`adb_path`、`adb_serial`。
   示例路径只是示例，ADB 路径和端口应从自己的 MuMu 实例获取。
   路径相对于配置文件所在目录解析，含空格路径受支持。
   多实例同时运行时，每个副本设置不同的 `probe_port`；设备内部端口固定 26888。
4. 打开模拟器，在 MuMu 中启用 Root 与 ADB；游戏停在大厅。
   使用配置中的 ADB 连接实例，确认所选实例为 `device`：

   ```powershell
   $settings = Get-Content .\settings.local.json -Raw | ConvertFrom-Json
   & $settings.adb_path connect $settings.adb_serial
   & $settings.adb_path devices
   ```

   上述命令适用于示例中的 TCP 实例地址；使用 `emulator-…` 序列号时只需检查 `devices`。
   运行检查（此时账号未设置会报该项失败，其他项可继续检查）：

   ```powershell
   .\.venv\Scripts\python.exe tools\preflight.py --device-check --model hog26
   ```

5. 安装稳定探针：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\probe\deploy_probe.ps1 -Check
   powershell -ExecutionPolicy Bypass -File .\probe\deploy_probe.ps1
   ```

   `-Check` 只检查本地文件。实际安装会保留原始 SDK、停止并重启游戏，不会匹配或下牌。
   原始 SDK 只从本人的设备保存，不随本项目分发。
6. 在 AI 未启动时手动开一局好友战，两边使用不同卡组，运行：

   ```powershell
   .\.venv\Scripts\python.exe tools\inspect_players.py
   ```

   对照自己的手牌和卡组 ID 找到所属行，把原生 `account_id` 填入配置。
   这是内部数字 ID，不是个人主页的 `#玩家标签`；不要固定填写 owner，席位会变化。
7. 按 [坐标核对](CALIBRATION.md) 验证自己的界面；之后将配置中的
   `calibration_verified` 设为 `true`。英雄按钮需要单独校准。
8. 双击 `start_agent.bat`。首次建议选“只观察一局”，确认进入对战后有连续决策与正确卡组。
   正式使用选自动对战，等待 `ready` 后再手动匹配。Ctrl+C 停止。

完成账号设置后，再运行一次 `preflight.py --device-check --model hog26`，
确认各项检查通过。使用其他权重时，将 `hog26` 换成对应别名，例如 `general`。

## 配置和日常使用

菜单提供运行方式、模型、特殊形态、输入方案四组选项。切换 General 不会自动扩大执行支持范围。
英雄火枪手未校准技能按钮时，技能点击会禁用。

| 菜单 | 可选内容 |
| --- | --- |
| 运行方式 | 自动对战／自动一局／只观察／只观察一局 |
| 模型 | 速猪 specialist2、速猪 specialist1、General、IL、active IL |
| 特殊形态 | 自动识别已支持形态；或禁用特殊形态执行用于排错 |
| 输入方案 | `reference` 原版规则基线；或 `extended` 精确事件对照实验 |

默认模型为速猪 specialist2。参考卡组为：**野猪骑士、火枪手、小骷髅、冰人、冰精灵、加农炮、火球、滚木**。
使用其他卡组应选择合适权重并检查当前卡牌／形态支持情况，不应继续套用速猪专用模型。

常用命令示例：

```powershell
# 只观察一局：推理并记录，不下牌
.\.venv\Scripts\python.exe main.py --checkpoint hog26 --dry-run --once

# 使用通用模型自动执行一局；等待 ready 后手动匹配
.\.venv\Scripts\python.exe main.py --checkpoint general --once

# 在已有本地采集记录上比较两套输入，不操作游戏
.\.venv\Scripts\python.exe tools\compare_observation_profiles.py --help
```

`settings.local.json` 可选项还包括 `checkpoints_dir`、`calibration_path`、
`ability_calibration_path`。`CR_AGENT_SETTINGS` 可指定另一份配置，`device` 可设 `cuda:0` 或 `cpu`。
手动命令行支持 `--account-id`、`--device`、`--dry-run` 等；详见 `main.py --help`。

只观察模式仍会建立 ADB 连接和端口转发并读取屏幕尺寸，但不下牌或点击技能。
只读采集工具：`tools/capture_native.py --seconds 60 --output diagnostics/capture.jsonl`，
运行前用 `tools/inspect_players.py` 建立端口转发。

## 恢复原始 SDK

```powershell
powershell -ExecutionPolicy Bypass -File .\probe\deploy_probe.ps1 -Restore
```

恢复仅使用本次设备和安装目录对应的 `local/backups` 备份，会重启游戏。
不要将其他账号、其他实例或旧安装目录的备份复制过去。重装／更新游戏后需重新进行兼容检查。
备份不能省略；保留至确认不再需要恢复为止。

## 常见问题

**其他人的同版本游戏可以直接用吗？**

核心桥接代码和稳定探针可以复用，但仍需配置本机路径、模拟器连接、账号身份和坐标。
游戏库指纹不匹配时需要重新适配；只看版本号不能判断兼容。

**需要每个用户重新绑定所有卡牌 ID 吗？**

同一受支持游戏数据版本通常不需要。卡牌目录与词表来自冻结的上游依赖。
但“识别 ID”与“完整支持该卡牌机制和动作”是两回事，尤其是英雄技能、觉醒状态和塔兵资源。

**启动后为什么一直等待，或者英雄不点技能？**

先检查控制台的 `waiting`／`telemetry_rejected`／`ability_input_disabled` 信息和本地 `logs`。
常见原因包括未进入对局、账号身份不匹配、遥测断开、形态不受支持或英雄按钮未校准。
模型也可以主动选择 `wait`；日志中的等待不一定是程序卡死。

游戏断线重连时，AI 会暂停动作并等待遥测恢复；“只运行一局”也会继续等待同一局，
不会重发已经提交的指令。若恢复后已进入另一局，该模式会退出，避免意外接管下一局。
在上一局的结果页启动 AI 时，会等待新的对战开始。

**当前模型接入有哪些限制？**

实时观测与训练观测在采样、完整可见性和特殊机制覆盖上仍有差异。
执行时延和重复施法是后续优化方向，详见 [架构与边界](ARCHITECTURE.md)。

**为什么 `extended` 信息更多，却不是默认选项？**

精确事件更多不保证更接近已有权重的训练观测，也不保证决策更好。
保留 `reference` 作为基线，便于对比输入变化，避免把额外字段误当作已证实的性能提升。

**现在能使用图像识别吗？**

当前发布版仍依赖原生数据。后续视觉感知需要对象跟踪、状态估计和缺失信息处理，
不是把截图直接替换到当前模型接口里即可完成。

## 开发与验证

发布整理已通过 **234 项 Python 测试、10 项离线探针产物检查**，
速猪权重完成两席位离线推理，General 权重可加载。
发布源码的 Stable 构建与包内稳定二进制逐字节一致。
详细结果见 [验证记录](VALIDATION.md) 和 [机器可读记录](../release-validation.json)。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe test_pipeline.py --model
powershell -ExecutionPolicy Bypass -File .\probe\test_artifacts.ps1
```

原生探针源码位于 `probe`；只有重新编译的开发者需要 NDK（验证版本 r27c）：

```powershell
powershell -ExecutionPolicy Bypass -File .\probe\build_probe.ps1 -NdkRoot 'C:\Android\android-ndk-r27c'
```

构建输出候选产物，不覆盖发行包内锁定的稳定探针。发布安装器只安装稳定产物。
源码中的 `Experimental` 构建开关仅供开发研究，默认关闭。

重新打包使用 `tools/package_release.py`，只包含受审查且哈希匹配的文件。
修改源码或文档后，需要审查变更并更新 `SHA256SUMS.json`；打包工具不会自动接受变更。
输出 ZIP 已存在时会拒绝覆盖，请先将旧包移至另一个归档位置。
更多细节见 [发布说明](RELEASE.md)。

