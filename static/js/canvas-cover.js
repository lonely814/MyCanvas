// 画布封面（确定性的抽象渐变）——移植自 motif/sweetretry 的 gradient-cover 思路：
// 同一 seed 永远生成同一张图，不依赖文件、不发网络请求、不装 React。
// 原始实现是 shadcn 的 .tsx 组件（模糊色场 + 细线纹理 + 颗粒），我们项目没有构建链，
// 这里只保留能在纯 CSS 里表达的部分：种子 → 色板 + 图案 + 色场偏移。
// 取舍：不做 SVG 滤镜（blur 在节点平移时会每帧重算，是已知卡顿源），改用径向渐变叠加。
// ponytail: 色板/图案是哈希出来的固定几档，需要更丰富时按 seed 加色相旋转即可。
(function(){
    const PALETTES = [
        // 每档：三个色标 + 一个高光基色
        ['#3b82f6', '#6366f1', '#0f172a'],  // cool
        ['#f59e0b', '#ef4444', '#7c2d12'],  // warm
        ['#8b5cf6', '#d946ef', '#312e81'],  // violet
        ['#14b8a6', '#0ea5e9', '#134e4a'],  // lagoon
        ['#22c55e', '#84cc16', '#14532d'],  // green
        ['#ec4899', '#f97316', '#831843'],  // pink
    ];
    // 32 位字符串哈希（FNV-1a），稳定且与运行环境无关
    function hashSeed(seed){
        let h = 0x811c9dc5;
        const s = String(seed == null ? '' : seed);
        for(let i = 0; i < s.length; i++){
            h ^= s.charCodeAt(i);
            h = Math.imul(h, 0x01000193) >>> 0;
        }
        return h >>> 0;
    }
    function pick(seed){
        const h = hashSeed(seed);
        // 色板用高位比特（不同 id 高位差异更均匀），位置/图案用低位
        const palette = PALETTES[(h >>> 24) % PALETTES.length];
        const x1 = 10 + ((h >>> 3) % 70);
        const y1 = 6 + ((h >>> 8) % 64);
        const x2 = 24 + ((h >>> 13) % 62);
        const y2 = 30 + ((h >>> 18) % 60);
        const angle = (h >>> 26) % 360;
        const patternIdx = (h >>> 6) % 3;   // 0 无线条 / 1 斜线 / 2 点阵
        // 同色板内再用色相旋转 ±18° 拉开差异：同档两张卡不会长得一样
        const hueShift = ((h >>> 11) % 37) - 18;
        return { palette, x1, y1, x2, y2, angle, patternIdx, hueShift };
    }
    function shiftHue(hex, deg){
        if(!deg) return hex;
        const n = parseInt(hex.slice(1), 16);
        let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
        // 转 HSL→旋转→回 RGB（只用于装饰，精度够）
        const max = Math.max(r, g, b) / 255, min = Math.min(r, g, b) / 255;
        let l = (max + min) / 2, s = 0, hh = 0;
        const d = max - min;
        if(d){
            s = l > .5 ? d / (2 - max - min) : d / (max + min);
            if(max === r/255) hh = ((g - b) / 255) / d + (g < b ? 6 : 0);
            else if(max === g/255) hh = ((b - r) / 255) / d + 2;
            else hh = ((r - g) / 255) / d + 4;
            hh /= 6;
        }
        hh = (hh + deg / 360 + 1) % 1;
        const q = l < .5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
        const f = t => { t = (t + 1) % 1; return t < 1/6 ? p + (q-p)*6*t : t < 1/2 ? q : t < 2/3 ? p + (q-p)*(2/3-t)*6 : p; };
        const to = v => Math.round(Math.max(0, Math.min(255, v * 255))).toString(16).padStart(2, '0');
        return '#' + to(f(hh + 1/3)) + to(f(hh)) + to(f(hh - 1/3));
    }
    function patternLayer(p){
        // 细线沿用 motif 的「同一组白色发丝线」思路，让一排卡片看起来像同一套
        if(p.patternIdx === 0) return '';
        if(p.patternIdx === 1){
            return `repeating-linear-gradient(${p.angle}deg, rgba(255,255,255,.07) 0 1px, transparent 1px 9px)`;
        }
        return `radial-gradient(rgba(255,255,255,.10) 1px, transparent 1.2px) 0 0 / 12px 12px`;
    }
    // 返回可直接塞进 style 的 background 值（多层渐变，上层在前）
    function coverBackground(seed){
        const p = pick(seed);
        const [raw1, raw2, raw3] = p.palette;
        const c1 = shiftHue(raw1, p.hueShift);
        const c2 = shiftHue(raw2, p.hueShift);
        const c3 = shiftHue(raw3, Math.round(p.hueShift / 2));
        const layers = [];
        const pat = patternLayer(p);
        if(pat) layers.push(pat);
        layers.push(`radial-gradient(120% 100% at ${p.x1}% ${p.y1}%, ${c1} 0%, transparent 62%)`);
        layers.push(`radial-gradient(110% 90% at ${p.x2}% ${p.y2}%, ${c2} 0%, transparent 58%)`);
        layers.push(`linear-gradient(${p.angle}deg, ${c3} 0%, ${c3} 100%)`);
        // 上缘 1px 内侧高光 + 下缘暗线：与项目现有「伪毛玻璃」手法一致，无 filter
        return {
            background: layers.join(','),
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,.14), inset 0 -1px 0 rgba(0,0,0,.22)',
        };
    }
    // 直接把封面写成一段 <span> 标记（放在卡片顶部做色带）
    function coverHtml(seed, className){
        const { background, boxShadow } = coverBackground(seed);
        return `<span class="${className || 'cover-strip'}" aria-hidden="true" style="background:${background};box-shadow:${boxShadow}"></span>`;
    }
    window.canvasCover = { coverBackground, coverHtml, hashSeed, palettes: PALETTES };
})();
