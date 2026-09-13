"""Interactive launcher. Selecting options never connects to the game."""
import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config


RUN_MODES = {
    '1': ('自动对战，每局结束后等待你再次匹配', ()),
    '2': ('自动对战，只运行一局', ('--once',)),
    '3': ('只观察和记录模型决策，不下牌、不点技能', ('--dry-run',)),
    '4': ('只观察一局，不下牌、不点技能', ('--dry-run', '--once')),
}
MODELS = {
    '1': ('速猪 specialist2（当前默认）', 'hog26'),
    '2': ('速猪 specialist1（对照模型）', 'hog26_proactive'),
    '3': ('通用模型 general', 'general'),
    '4': ('模仿学习模型 IL', 'il'),
    '5': ('模仿学习模型 active IL', 'active_il'),
}
FORMS = {
    '1': ('自动识别：英雄火枪手、觉醒小骷髅／加农炮', ()),
    '2': ('基础形态排错：禁用特殊形态和技能执行', ('--base-only',)),
}
OBSERVATION_PROFILES = {
    '1': ('原版规则基线（默认）', 'reference'),
    '2': ('精确事件扩展（待效果验证）', 'extended'),
}


def choose(title, options):
    print(f'\n{title}')
    for key, (label, _) in options.items():
        print(f'  {key}. {label}')
    print('  0. 退出')
    while True:
        answer = input('请输入数字，直接回车选 1：').strip() or '1'
        if answer == '0':
            raise SystemExit(0)
        if answer in options:
            return answer
        print('输入无效，请选择上面列出的数字。')


def build_command(mode, model, forms, observation_profile='1'):
    return [str(config.VENV_PYTHON), '-u', str(config.BASE_DIR / 'main.py'),
            '--checkpoint', MODELS[model][1], *RUN_MODES[mode][1], *FORMS[forms][1],
            '--observation-profile', OBSERVATION_PROFILES[observation_profile][1]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='show resolved options without starting the agent')
    args = parser.parse_args()
    print('皇室战争 AI — 启动设置')
    print('日常使用：连续按四次回车即可采用当前默认配置。')
    mode = choose('第一步：运行方式', RUN_MODES)
    model = choose('第二步：模型', MODELS)
    forms = choose('第三步：特殊形态', FORMS)
    observation_profile = choose('第四步：模型输入方案', OBSERVATION_PROFILES)
    print('\n本次设置：')
    print(f'  运行方式：{RUN_MODES[mode][0]}')
    print(f'  模型：{MODELS[model][0]}')
    print(f'  特殊形态：{FORMS[forms][0]}')
    print(f'  输入方案：{OBSERVATION_PROFILES[observation_profile][0]}')
    command = build_command(mode, model, forms, observation_profile)
    if args.preview:
        print('\n仅预览，未启动 AI：')
        print(subprocess.list2cmdline(command))
        return 0
    checkpoint = config.CHECKPOINTS[MODELS[model][1]]
    if not config.VENV_PYTHON.is_file() or not checkpoint.is_file():
        print('\n启动失败：Python 环境或所选模型文件不存在。')
        return 1
    print('\n保持游戏在大厅，等待 ready 后再手动匹配。Ctrl+C 停止。')
    print('切换模型不会扩大当前支持的卡牌形态范围。\n')
    # Fixed choices become separate argv items. Never evaluate menu input as shell code.
    return subprocess.call(command, cwd=config.BASE_DIR)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print('\n已退出启动设置或停止运行。')
        raise SystemExit(0)
