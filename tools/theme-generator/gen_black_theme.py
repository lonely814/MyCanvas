# -*- coding: utf-8 -*-
"""从各深色 CSS 生成 static/css/theme-black.css（纯黑主题覆盖层）。

思路：把深色规则里的"底色"颜色按感知亮度中性化（r=g=b），既去掉蓝调，
又保留原有的明暗层次与对比关系；语义色（危险红、运行蓝、强调蓝）保持原样。

为什么不能靠改变量实现：深蓝的配色是各 CSS 里逐条硬编码的 !important，
不是变量驱动（实测把变量全设成红色只有约 2% 的界面变色），所以必须逐条覆盖。

为什么要扫多个文件：黑色主题下 html 同时带 .theme-dark 与 .theme-black 两个类，
所以 canvas.css / canvas-list.css / api-settings.css 里 `.theme-dark .xxx` 的冷色
规则依然生效。早先只扫 theme.css，黑色主题下这些面板仍是蓝灰（实测 25 处）。

哪些颜色算"底色"：按通道极差 max-min 判断，实测深色作用域内呈双峰分布——
极差 ≤40 的全是底色与冷灰（蓝灰面板、深蓝底），>40 的全是语义色。
阈值 40 落在两峰之间的空档上，因此可以机械区分，不必逐个规则白名单。

用法（在仓库根目录执行）：
    python tools/theme-generator/gen_black_theme.py

改过任何一个深色 CSS 的配色后需重新运行本脚本。生成的文件勿手改。
"""
import io
import os
import re

# 以脚本位置为基准定位仓库根，避免必须在特定目录下执行
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DST = os.path.join(ROOT, 'static', 'css', 'theme-black.css')
SRCS = [os.path.join(ROOT, 'static', 'css', n) for n in (
    'theme.css', 'canvas.css', 'canvas-list.css', 'asset-manager.css',
    'comfyui-settings.css', 'api-settings.css')]
HEXRE = re.compile(r'#[0-9a-fA-F]{6}\b')
# 颜色也大量写成 rgb()/rgba()（theme.css 68 处、canvas.css 263 处）。
# 早先只认 6 位 hex，导致 rgba(17,23,34,.86) 这类蓝灰原样抄进覆盖层，
# 黑色主题下 .panel / .toolbar 等 50 多处仍带蓝调。这里一并中性化，alpha 保持不变。
RGBRE = re.compile(r'\brgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(,\s*[0-9.]+\s*)?\)')
SPREAD_MAX = 40


def to_rgb(h):
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luma(rgb):
    r, g, b = rgb
    return round(0.299 * r + 0.587 * g + 0.114 * b)


def is_flat(c):
    """通道极差小 = 底色/冷灰，需要中性化；极差大 = 语义色，保留。"""
    return max(c) - min(c) <= SPREAD_MAX


def neutralize_rgb(m):
    c = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not is_flat(c):
        return m.group(0)
    v = luma(c)
    return 'rgba(%d,%d,%d%s)' % (v, v, v, m.group(4) or '')


def neutralize_hex(m):
    c = to_rgb(m.group(0))
    if not is_flat(c):
        return m.group(0)
    v = luma(c)
    return '#%02x%02x%02x' % (v, v, v)


def neutralize_all(body):
    return RGBRE.sub(neutralize_rgb, HEXRE.sub(neutralize_hex, body))


def drop_backgrounds(body):
    """丢掉 background / background-color / background-image 声明，保留其余。

    只用于"基础表面"规则（html/body/.shell/.board/...）。这类规则的底色在
    theme.css 里被写成 !important 且选择器比本文件的阶梯更长（选择器权重更高，
    覆盖不掉），而且其中一条把 `.shell .board` 的网格渐变一并钉成冷色，会顶掉
    canvas.css 里基于 var(--grid) 的中性网格（实测黑色主题下 .board 仍是
    rgb(15,20,29)）。底色与网格统一交给变量阶梯和 surface_block，
    这里只留颜色/描边类声明。
    """
    keep = []
    for decl in body.split(';'):
        d = decl.strip()
        if not d:
            continue
        if re.match(r'background(-color|-image)?\s*:', d):
            continue
        keep.append(d)
    return '; '.join(keep)


out_rules = []
seen = set()
for src_path in SRCS:
    src = io.open(src_path, encoding='utf-8').read()
    for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', src):
        sel_c = sel.strip()
        # 只处理深色规则；@import / @media 之类的 at 规则没有选择器，跳过
        if '@' in sel_c:
            continue
        # 选择器里若把深色写成否定形式（`body:not(.studio-theme-dark)`），那是浅色专用规则，
        # 不能抄进来：下文会把 studio-theme-dark 换成 theme-black，否定形式就变成
        # `body:not(.theme-black)`，于是"浅色样式"在深蓝主题下也成立（api-settings.css
        # 里有 366 条这种规则，会把深蓝主题刷成白的）。先剔掉再替换。
        if re.search(r':not\([^)]*(studio-theme-dark|theme-dark)', sel_c):
            continue
        if 'studio-theme-dark' not in sel_c and '.theme-dark' not in sel_c:
            continue
        # 把主题约束换成 .theme-black：黑色主题下 html 同时带 .theme-dark 与 .theme-black，
        # 若照抄 .theme-dark 会在深蓝主题里也生效（本文件对所有页面无差别加载）。
        new_sel = sel_c.replace('html.studio-theme-dark', 'html.theme-black')
        new_sel = new_sel.replace('body.studio-theme-dark', 'body.theme-black')
        new_sel = new_sel.replace('body.theme-dark', 'body.theme-black')
        new_sel = new_sel.replace('.theme-dark', '.theme-black')
        if 'theme-black' not in new_sel:
            continue
        # "基础表面"规则：整体结构为主题根 + 若干基础容器类的那类选择器，例如
        #   `html.theme-black, html.theme-black body, body.theme-black`
        #   `html.theme-black body .shell, body.theme-black .sidebar, body.theme-black .stage`
        # 这类规则在 theme.css 里有多份（不同轮次的深色改版叠出来的），背景色会落到本文件后段，
        # 把上面 var_block 调好的 --page 顶掉（实测 body 与 .shell 最终变成 #141414，既发闷又残留深蓝）。
        # 因此这些规则里的 background 系列声明一律丢弃，底色统一交给 var_block 与 surface_block。
        # 其余声明（前景色、描边等）仍要保留：`.node` / `.panel` 这类规则不在此列，
        # 而 `.stage` / `.shell .board` 的颜色若整条丢掉会退回 canvas.css 的默认值。
        # 注意先剥掉 CSS 注释：theme.css 里这些规则前面常带一行说明，
        # 不剥会让选择器不以 html/body 开头，正则匹配不到而漏过。
        sel_nocomment = re.sub(r'/\*.*?\*/', ' ', new_sel, flags=re.S)
        sel_parts = [p.strip() for p in sel_nocomment.split(',') if p.strip()]
        base_surface = (r'(html|body)(\.theme-black)?(\s+(html|body)(\.theme-black)?)?'
                        r'(\s+\.(shell|board|app-shell|sidebar|stage|workspace|ws-main|asset-page))*')
        is_base_surface = bool(sel_parts) and all(re.fullmatch(base_surface, p) for p in sel_parts)
        # 丢掉原规则里的自定义属性赋值（--xxx: ...）。
        # 那些是深蓝的变量块，若照抄过来会出现在本文件后段，把开头那条手工调好的
        # 抬升阶梯覆盖掉（实测 --card 会被后面的 #1d1d1d 顶掉，层次又变糊）。
        # 变量层统一由下面的 var_block + accent_block 负责，这里只保留具体样式声明。
        body_wo_vars = re.sub(r'--[A-Za-z0-9-]+\s*:[^;]*(;|$)', '', body)
        if is_base_surface:
            body_wo_vars = drop_backgrounds(body_wo_vars)
        if not HEXRE.search(body_wo_vars) and not RGBRE.search(body_wo_vars):
            continue
        cleaned = ' '.join(neutralize_all(body_wo_vars).split()).strip().strip(';')
        if not cleaned:
            continue
        key = (new_sel, cleaned)
        if key in seen:  # theme.css 里同一套深色改版叠了多轮，重复规则只留第一份
            continue
        seen.add(key)
        out_rules.append(key)

# 追加：变量层的黑色覆盖（含 canvas.css 的 .theme-dark 变量块）
header = """/* 纯黑主题覆盖层（自动生成，勿手改）
   生成脚本：tools/theme-generator/gen_black_theme.py
   说明：深蓝主题的配色是 theme.css 里逐条硬编码的 !important，不是变量驱动
   （实测把变量全设成红色只有约 2% 的界面变色），因此黑色主题必须逐条覆盖。
   这里把深色规则中的每个颜色按感知亮度中性化（r=g=b），去掉蓝调的同时
   保留原有明暗层次。本文件必须在 theme.css 之后加载。 */

"""

var_block = """/* 变量层：覆盖 canvas.css / theme.css 里定义在深色作用域下的冷色变量。

   这里的取值不是随手填的，而是一条"抬升阶梯"：每一级比上一级约亮 8~9%
   （按 WCAG 相对亮度比）。纯黑最容易出的问题是所有面都挤在最暗处，
   层次糊成一片——之前的取值 page→card 只有 1.076，而深蓝主题是 1.294，
   所以看起来"没质感"。下面 page→card 提到 1.178，且每级边界都能分辨：
     page 底 → soft（内嵌区）→ card（卡片/节点）→ soft-2（卡片内抬升）→ line
   注意 --soft 必须比 --card 暗、比 --page 亮，顺序颠倒会让凹陷变凸起。 */
html.theme-black, body.theme-black,
html.theme-black body, html.theme-black body .shell.theme-dark,
body.theme-black .shell.theme-dark {
    --page:#08080a !important; --bg:#08080a !important; --bg-base:#08080a !important;
    --soft:#141416 !important; --soft-2:#26262b !important;
    --card:#1c1c20 !important; --card-solid:#1c1c20 !important;
    --panel:rgba(23,23,26,.92) !important;
    --panel-solid:#1c1c20 !important;
    --line:rgba(255,255,255,.09) !important; --line-2:rgba(255,255,255,.16) !important;
    --grid:rgba(255,255,255,.09) !important;
    --shadow:rgba(0,0,0,.55) !important;
}
"""

# 语义色单独还原：中性化会把 --accent 之类的强调色也变成灰色（实测 --accent 从
# 蓝色 #60a5fa 变成 #e8e8e8），界面因此失去重点、更显呆板。
# 纯黑去掉的是蓝灰底色，不该把"蓝色只用于主操作与选中"这条设计一起去掉。
#
# 同时覆盖文字/描边变量族：它们也全部带蓝调（--text #e5e9f0、--muted #9aa6b8、
# --strong #d8dee9 等）。不覆盖的话，用 var(--strong) 做底色的主按钮
# （.workflow-transfer-btn.primary / .error-btn.primary）在黑色主题下仍是蓝白，
# 与周围的中性灰不搭（实测黑色主题下这类按钮残留蓝调）。
accent_block = """/* 语义色保留原值；文字/描边变量按感知亮度中性化 */
html.theme-black, body.theme-black,
html.theme-black body, html.theme-black body .shell.theme-dark,
body.theme-black .shell.theme-dark {
    --text:#e9e9e9 !important; --text-main:#e9e9e9 !important;
    --muted:#a4a4a4 !important; --faint:#848484 !important;
    --strong:#dddddd !important; --strong-text:#141414 !important;
    --accent:#60a5fa !important; --accent-soft:rgba(96,165,250,.16) !important;
    --ok:#4ade80 !important; --warn:#fbbf24 !important; --danger:#f87171 !important;
    --node-image:#0ea5e9 !important; --node-prompt:#8b5cf6 !important; --node-loop:#14b8a6 !important;
    --node-llm:#6366f1 !important; --node-generator:#3b82f6 !important; --node-video:#db2777 !important;
    --node-minimax:#0891b2 !important; --node-rh:#f59e0b !important; --node-comfy:#22c55e !important;
    --node-ltx:#ef4444 !important; --node-mj:#a855f7 !important; --node-msgen:#0284c7 !important;
    --node-output:#94a3b8 !important; --node-group:#64748b !important;
    --shadow-selected:0 0 0 2px rgba(96,165,250,.55) !important;
    --shadow-card:0 1px 2px rgba(0,0,0,.5) !important;
    --shadow-pop:0 14px 36px rgba(0,0,0,.62) !important;
}
"""

# 基础表面：直接给 html/body 与主容器落背景色。
# 只写变量不够——theme.css 里有一条 `html.studio-theme-dark ... { background:#0f141d !important }`
# 直接命中 html/body，变量本身不会覆盖它（实测 body 最终仍是深蓝底 rgb(15,20,29)），
# 所以这里必须用同级别的 !important 把基础底色钉死。
surface_block = """/* 基础表面：theme.css 用 !important 直接给 html/body 上色，仅靠变量压不住 */
html.theme-black, html.theme-black body, body.theme-black {
    background: #08080a !important;
    background-color: #08080a !important;
    color: #e8e8e8 !important;
    color-scheme: dark !important;
}
html.theme-black body .shell, body.theme-black .shell,
html.theme-black body .board, body.theme-black .board {
    background-color: #08080a !important;
}
/* 画布底板与舞台：theme.css 里用 `html.studio-theme-dark body .shell .board {...!important}`
   把底色钉成 #0f141d、网格钉成冷灰，选择器权重比本文件的通用规则高，覆盖不掉
   （实测黑色主题下 .board 仍是 rgb(15,20,29)）。这里用同样的写法钉回中性底色，
   并让网格重新走变量 --grid，与 canvas.css 里的定义保持一致。 */
html.theme-black body .shell .board, body.theme-black .shell .board {
    background-color: var(--page) !important;
    background-image: radial-gradient(var(--grid) 1px, transparent 1px) !important;
    background-size: 24px 24px !important;
}
html.theme-black body .shell .stage, body.theme-black .shell .stage,
html.theme-black body .stage, body.theme-black .stage {
    background-color: transparent !important;
    background-image: none !important;
}
"""

with io.open(DST, 'w', encoding='utf-8', newline='') as f:
    f.write(header)
    f.write(var_block)
    f.write(accent_block)
    f.write(surface_block)
    for sel, body in out_rules:
        f.write('%s { %s }\n' % (sel, body))

print('生成', os.path.relpath(DST, ROOT))
print('  覆盖规则数:', len(out_rules))
print('  文件大小:', os.path.getsize(DST), 'bytes')
