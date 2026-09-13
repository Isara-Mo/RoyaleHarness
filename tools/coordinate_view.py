"""Make a standalone clickable ground-grid overlay; does not touch the game."""
import argparse
import base64
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config
from bridge.coordinates import ScreenCalibration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=ROOT / 'diagnostics/battle_before.png')
    parser.add_argument('--output', type=Path, default=ROOT / 'diagnostics/coordinates.html')
    args = parser.parse_args()
    calibration = ScreenCalibration.load(config.CALIBRATION_PATH)
    points = []
    for gy in range(32):
        for gx in range(18):
            px, py = calibration.project(gx+.5, gy+.5)
            points.append({'gx': gx, 'gy': gy, 'px': px, 'py': py,
                           'touchable': calibration.touchable(gx+.5, gy+.5)})
    source = 'data:image/png;base64,' + base64.b64encode(args.image.read_bytes()).decode('ascii')
    template = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>下牌坐标核对</title><style>
body{margin:0;background:#101827;color:#ebf1fa;font:16px system-ui;display:flex;gap:28px;padding:24px}
canvas{width:min(54vw,540px);height:auto;border-radius:12px;background:#222;cursor:crosshair}
aside{max-width:450px;position:sticky;top:24px;align-self:flex-start}h1{font-size:24px}
button,select{font:inherit;padding:9px;background:#25334b;color:white;border:1px solid #546784;border-radius:6px}
pre{line-height:1.7;background:#1c293d;padding:16px;border-radius:8px;white-space:pre-wrap}
p{line-height:1.7;color:#cbd5e1}</style>
<canvas id="board" width="1080" height="1920"></canvas><aside><h1>下牌坐标核对</h1>
<p>点击一个圆点，检查模型格子、原生世界坐标和实际像素。画面是保存的参考截图，不是直播。</p>
<label>本地席位 <select id="owner"><option value="0">Owner 0</option><option value="1">Owner 1</option></select></label>
<p><label><input type="checkbox" id="labels">显示格子编号</label></p><pre id="detail">点击地图选择落点。</pre>
<p>绿色：可点击地面。灰色：屏幕边界排除。这里不包含卡牌各自的河道、占地和敌方领土限制；实际推理会再应用完整合法掩码。</p>
<p>坐标是地面中心，建筑另有 subcell 偏移。火球飞行起点和骷髅阵形位置不能用作落点标定。</p>
<p>切换席位只改变原生坐标解释。相同的模型落点应始终留在同一屏幕位置。</p></aside>
<script>const points=__POINTS__;const img=new Image();img.src='__IMAGE__';
const c=document.getElementById('board'),ctx=c.getContext('2d');let selected=null;
function draw(){ctx.drawImage(img,0,0,1080,1920);for(const p of points){ctx.beginPath();ctx.arc(p.px,p.py,selected===p?10:4,0,Math.PI*2);ctx.fillStyle=selected===p?'#ffda66':p.touchable?'#65ffb8aa':'#a0a0a077';ctx.fill();if(labels.checked){ctx.font='13px monospace';ctx.fillStyle='#fff';ctx.fillText(p.gx+','+p.gy,p.px+5,p.py-5)}}
if(selected){const p=selected,o=Number(owner.value),nx=o===0?17-p.gx:p.gx,ny=o===0?p.gy:31-p.gy;
detail.textContent='模型格子: ('+p.gx+', '+p.gy+')\\n原生格子: ('+nx+', '+ny+')\\n原生世界: ('+((nx+.5)*1000)+', '+((ny+.5)*1000)+')\\n点击像素: ('+p.px+', '+p.py+')\\n屏幕可点击: '+p.touchable}}
img.onload=draw;labels.onchange=draw;owner.onchange=draw;c.onclick=e=>{const b=c.getBoundingClientRect(),x=(e.clientX-b.left)*1080/b.width,y=(e.clientY-b.top)*1920/b.height;selected=points.reduce((a,p)=>Math.hypot(p.px-x,p.py-y)<Math.hypot(a.px-x,a.py-y)?p:a);draw()};</script></html>'''
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(template.replace('__POINTS__', json.dumps(points)).replace('__IMAGE__', source), encoding='utf-8')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
