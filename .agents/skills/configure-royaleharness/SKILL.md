---
name: configure-royaleharness
description: Configure and validate RoyaleHarness on a Windows PC and a Root MuMu instance running the supported Null's Royale build. Use for onboarding another emulator or account, filling local settings, or diagnosing startup and reconnect behavior. This is live gameplay setup, not offline-engine installation or model training.
---

# 配置 RoyaleHarness

目标是让指定实例完成状态读取、模型推理和已获授权的触摸执行，配置保留在使用者本地。
仓库根目录是同时包含 `config.py`、`setup.ps1`、`upstream.lock.json` 的目录；
所有命令在该目录运行，不依赖历史开发路径。安装步骤见
[docs/SETUP.md](../../../docs/SETUP.md)，按需查阅，不复制另一套安装逻辑。

## 先确认已有环境

读取 `settings.local.json`（若存在）、`settings.example.json` 和 `upstream.lock.json`。
保留现有配置和用户正在运行的实例，不用自己的机器参数覆盖模板。
优先从运行中的 MuMu 进程／安装位置发现 `MuMuManager.exe` 与对应 `adb.exe`；
找不到时只询问缺失的安装位置。

使用已定位的管理器运行 `info -v all`，按用户指定的实例名称匹配，取得真实
`index`、`adb_host_ip`、`adb_port`。不要从“CR-AI-2”等名称推算端口或假定是实例 0。
TCP 实例执行 `adb connect` 后用 `adb devices -l` 核对目标。
同一设备可能还显示 `emulator-…` 别名；不要误当成另一个实例。

用对应实例的 ADB 检查 `shell wm size`、`shell su -c id`，
通过项目 `preflight.py --device-check` 检查游戏库指纹。
原生库或 ABI 不匹配时记录实测差异并停止安装，不绕过检查，也不进入新版本逆向适配。

## 填写本地设置

从模板创建 `settings.local.json`，只填写实测或用户明确提供的值。
路径相对于该配置文件所在目录解析；JSON 中 Windows 路径可使用 `/`。
通过 `CR_AGENT_SETTINGS` 指定外置配置时，也按外置文件目录解释相对路径。

| 字段 | 获取方法 |
| --- | --- |
| `firstlight_dir` | 用户的 FirstLight 目录或相邻目录；按 `upstream.lock.json` 核对版本和冻结目录数据 |
| `adb_path` | 该 MuMu 安装附带的 ADB 绝对路径 |
| `adb_serial` | 管理器返回的真实地址或已核对的设备序列号 |
| `account_id` | 稍后从本人的对局快照中确认的原生数字 `accountId`；未确认先保留 `null` |
| `device` | 根据 PyTorch 实际可运行的设备填写 `cuda:0` 或 `cpu`；用 preflight 的实际张量计算核对 |
| `probe_port` | 默认主机端口 26888；多实例时核对 `adb forward --list` 和本机监听，为本实例选独立空闲端口 |
| `calibration_verified` | 首次保留 `false`；核对本机画面与地面投影后才设 `true` |

设备内遥测端口固定为 26888，主机端口可以不同。不要把两者一起随意修改。
`vm_name`、`vm_index` 可记录实例身份，但实际连接使用 `adb_serial`。
仅在需要时指定 `checkpoints_dir`、`calibration_path` 或 `ability_calibration_path`。
独立地面校准可放 `local/calibration.json`，保持公开参考文件不带本机确认信息。

## 安装与账号识别

复用有效的本项目 `.venv`，或运行 `setup.ps1` 创建独立环境；它不会安装离线游戏。
已有解释器可通过 `CR_AGENT_PYTHON` 提供给启动器。不要要求用户下载 IL_Replay、
运行 FirstLight 离线 APK 安装、设置离线防火墙或启动训练引擎。

先执行 `preflight.py --device-check --model hog26`（其他卡组选对应模型）。
首次未识别账号时，该项失败是预期的，其他依赖／指纹错误仍需处理。
确认需要安装且任务已授权后，运行 `probe/deploy_probe.ps1`；该步骤会保留原始 SDK 并重启游戏。
`-Check` 只校验本地稳定产物，不能作为实机安装成功的证据。
安装后确认进程、探针指纹和大厅遥测。不要再次覆盖已有 SDK 备份或复制其他设备的备份。

账号需要真实对局。请用户在 AI 未启动时手动开一局；不同卡组更易分辨双方。
运行 `tools/inspect_players.py`，用画面手牌／卡组、塔血等证据匹配属于使用者的那一行。
没有唯一证据时询问用户，不默认 owner 0，不从另一个实例复制 ID。
保存 `account_id`，不要把 `#玩家标签` 或会变化的席位 `owner` 当成账号。

## 校准与对战

按照 [docs/CALIBRATION.md](../../../docs/CALIBRATION.md) 核对完整游戏画面、手牌槽和静止建筑地面位置。
不要根据移动单位当前位置或法术飞行起点断言落点偏移。
同为 1080×1920 可以复用参考作为起点，但不能仅凭分辨率就将本机标记为已核对。
英雄火枪手的单按钮需单独核对 `ability_calibration.local.json`；其他英雄不套用该按钮配置。

保存配置后重新启动读取它的程序，再跑 preflight。先验证实际快照可进入模型，
已有授权时进行一局自动测试；没有对应授权则停在只读模式并说明需要的操作。
`--dry-run --once` 不发下牌／技能输入；常规 `--once` 会操作游戏，并在一局结束后退出。
匹配由使用者执行，除非任务也已授权程序匹配。

若网络重连，观察 `telemetry_paused` / `telemetry_resumed`，不要另启动第二个 AI 或重发旧指令。
当前 `--once` 会等待同局恢复；若换了对局则退出。旧结果页启动会等待下一局。
用户退出测试或出现持续不可恢复故障时停止该 AI，保留错误证据，不无限重启或连续匹配。

## 交付状态

报告连接与环境检查结果、实际推理、出牌／技能确认、终局以及仍缺少的验证证据。
技能成为合法候选不等于发生技能点击；普通卡部署不等于完成觉醒循环。
将账号、完整配置和原始日志保留在本地，公开文档只记录不含个人身份的验证摘要。
仅为本机配置时不修改公共源码或扩展输入；确有缺陷时做最小修复并验证，然后按用户要求同步源码。
