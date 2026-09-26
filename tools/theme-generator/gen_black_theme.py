# -*- coding: utf-8 -*-
"""从 static/css/theme.css 生成 static/css/theme-black.css（纯黑主题覆盖层）。

思路：把深色规则里的每个颜色按感知亮度中性化（r=g=b），既去掉蓝调，
又保留原有的明暗层次与对比关系。

为什么不能靠改变量实现：深蓝的配色是 theme.css 里逐条硬编码的 !important，
不是变量驱动（实测把变量全设成红色只有约 2% 的界面变色），所以必须逐条覆盖。

用法（在仓库根目录执行）：
    python tools/theme-generator/gen_black_theme.py

改过 theme.css 的深色配色后需重新运行本脚本。生成的文件勿手改。
"""
import io
import os
import re

# 以脚本位置为基准定位仓库根，避免必须在特定目录下执行
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, 'static', 'css', 'theme.css')
DST = os.path.join(ROOT, 'static', 'css', 'theme-black.css')
HEXRE = re.compile(r'#[0-9a-fA-F]{6}\b')


def luma(h):
    h = h.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return round(0.299 * r + 0.587 * g + 0.114 * b)


def neutralize(h):
    v = luma(h)
    return '#%02x%02x%02x' % (v, v, v)


src = io.open(SRC, encoding='utf-8').read()
rules = re.findall(r'([^{}]+)\{([^{}]*)\}', src)

out_rules = []
for sel, body in rules:
    sel_c = sel.strip()
    # 只处理深色规则
    if 'studio-theme-dark' not in sel_c:
        continue
    # 选择器加 theme-black 约束：把 body.studio-theme-dark / html.studio-theme-dark 换成 .theme-black
    new_sel = sel_c
    new_sel = new_sel.replace('html.studio-theme-dark', 'html.theme-black')
    new_sel = new_sel.replace('body.studio-theme-dark', 'body.theme-black')
    if 'theme-black' not in new_sel:
        continue
    if not HEXRE.search(body):
        continue
    new_body = HEXRE.sub(lambda m: neutralize(m.group(0)), body)
    out_rules.append((new_sel, ' '.join(new_body.split())))

# 追加：变量层的黑色覆盖（含 canvas.css 的 .theme-dark 变量块）
header = """/* 纯黑主题覆盖层（自动生成，勿手改）
   生成脚本：tools/theme-generator/gen_black_theme.py
   说明：深蓝主题的配色是 theme.css 里逐条硬编码的 !important，不是变量驱动
   （实测把变量全设成红色只有约 2% 的界面变色），因此黑色主题必须逐条覆盖。
   这里把深色规则中的每个颜色按感知亮度中性化（r=g=b），去掉蓝调的同时
   保留原有明暗层次。本文件必须在 theme.css 之后加载。 */

"""

var_block = """/* 变量层：覆盖 canvas.css / theme.css 里定义在深色作用域下的冷色变量 */
html.theme-black, body.theme-black,
html.theme-black body, html.theme-black body .shell.theme-dark,
body.theme-black .shell.theme-dark {
    --page:#0a0a0b !important; --bg:#0a0a0b !important; --bg-base:#0a0a0b !important;
    --card:#141416 !important; --card-solid:#141416 !important;
    --panel:rgba(18,18,20,.92) !important;
    --soft:#101012 !important; --soft-2:#1a1a1d !important;
    --line:rgba(255,255,255,.10) !important; --line-2:rgba(255,255,255,.17) !important;
    --grid:rgba(255,255,255,.10) !important;
    --shadow:rgba(0,0,0,.5) !important;
}
"""

with io.open(DST, 'w', encoding='utf-8', newline='') as f:
    f.write(header)
    f.write(var_block)
    for sel, body in out_rules:
        f.write('%s { %s }\n' % (sel, body))

print('生成', os.path.relpath(DST, ROOT))
print('  覆盖规则数:', len(out_rules))
print('  文件大小:', os.path.getsize(DST), 'bytes')
