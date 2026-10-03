/* 触屏 → 鼠标事件桥接
   画布交互（平移/拖节点/连线/框选）全部基于 mouse 事件实现，触屏设备上只有原生 click 可用。
   这里把单指触摸翻译成 mousedown/mousemove/mouseup(+click/dblclick)，双指捏合翻译成 wheel
   （三个画布的缩放都挂在 wheel 上），使触屏可以拖动和缩放画布内容。
   规则：
   - 可编辑控件（input/textarea/select/contenteditable）与音视频上的触摸不转换，保留原生行为；
   - 位于可滚动容器内（项目列表、工具栏横向滚动、各面板）的触摸不转换，保留原生滚动和点按；
   - 监听用冒泡阶段：局部已有的 touch 处理（如输出对比滑杆）stopPropagation 后自然跳过桥接。 */
(function(){
    if(window.__touchMouseBridgeInstalled) return;
    window.__touchMouseBridgeInstalled = true;

    const TAP_MOVE = 8;      // px，位移超过视为拖动，不再补发 click
    const TAP_TIME = 600;    // ms，按住超过视为长按拖动而非点击
    const DBL_TIME = 350;    // ms，两次点按间隔小于此发 dblclick
    const DBL_DIST = 32;     // px
    const PINCH_STEP = 0.06; // 捏合距离的 log 比例累积到该值发一次 wheel
    // 长按 → contextmenu：触屏没有右键，而节点菜单、空白处新建菜单全靠 contextmenu。
    // 手指按住不动到达该时长即弹菜单，取 500ms（与系统长按习惯一致）。
    const LONGPRESS_MS = 500;

    let drag = null;
    let pinch = null;
    let longPressTimer = null;
    let lastTap = { time: 0, x: 0, y: 0 };

    function cancelLongPress(){
        if(longPressTimer !== null){
            clearTimeout(longPressTimer);
            longPressTimer = null;
        }
    }

    function shouldSkip(target){
        if(!(target instanceof Element)) return true;
        if(target.closest('input, textarea, select, audio, video, [contenteditable=""], [contenteditable="true"]')) return true;
        let node = target;
        while(node && node instanceof Element){
            const cs = getComputedStyle(node);
            if(/(auto|scroll)/.test(cs.overflowY) && node.scrollHeight > node.clientHeight + 1) return true;
            if(/(auto|scroll)/.test(cs.overflowX) && node.scrollWidth > node.clientWidth + 1) return true;
            // 遇到 fixed 浮层（灯箱、模态）就停止上溯：它盖在文档之上，
            // 其内部的触摸不应被"下方文档可滚动"这条规则拦掉，
            // 否则生成页的图片灯箱里无法捏合缩放、无法拖动查看大图。
            if(cs.position === 'fixed') return false;
            if(node === document.documentElement) break;
            node = node.parentElement;
        }
        // 文档级滚动：生成页整页靠 html 滚动（body 是 overflow:visible），
        // 这种滚动没有可检测的祖先容器，只能看根元素的尺寸差。
        // 不判这条的话，手指在生成页上划不动页面（触摸会被 preventDefault 掉）。
        return document.documentElement.scrollHeight > document.documentElement.clientHeight + 4;
    }

    function fire(type, x, y, opts = {}){
        const target = document.elementFromPoint(x, y) || opts.fallback || document.body;
        target.dispatchEvent(new MouseEvent(type, {
            bubbles: true, cancelable: true, composed: true, view: window,
            clientX: x, clientY: y,
            button: opts.button !== undefined ? opts.button : 0,
            buttons: opts.buttons || 0,
            detail: opts.detail || 1
        }));
    }

    function fireContextMenu(x, y, fallback){
        // 右键事件习惯带 button:2；canvas.js 的处理器不读 button，但保持一致以免第三方库误判
        fire('contextmenu', x, y, { button: 2, fallback });
    }

    function fireWheel(x, y, deltaY){
        const target = document.elementFromPoint(x, y) || document.body;
        const ev = new WheelEvent('wheel', {
            bubbles: true, cancelable: true, composed: true, view: window,
            clientX: x, clientY: y, deltaY: deltaY, deltaMode: 0
        });
        // 画布端据此把捏合的缩放强度调到跟手 1:1（鼠标滚轮一格的强度对捏合来说太猛）
        ev.__touchBridgeWheel = true;
        target.dispatchEvent(ev);
    }

    function pinchState(touches){
        const a = touches[0], b = touches[1];
        return {
            d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
            x: (a.clientX + b.clientX) / 2,
            y: (a.clientY + b.clientY) / 2
        };
    }

    document.addEventListener('touchstart', e => {
        if(e.touches.length === 1){
            const t = e.touches[0];
            if(shouldSkip(e.target)) return;
            e.preventDefault(); // 同时抑制长按选择/系统菜单与双击缩放
            drag = { id: t.identifier, startX: t.clientX, startY: t.clientY, x: t.clientX, y: t.clientY, time: Date.now(), moved: false, fallback: e.target, longPressed: false };
            // 长按判定：这段时间内若手指移动超过阈值或抬起，就在 touchmove/touchend 里取消。
            // 触发后置 longPressed，阻止随后的 click/dblclick，避免刚弹出的菜单被当成点选关掉。
            cancelLongPress();
            longPressTimer = setTimeout(() => {
                longPressTimer = null;
                if(!drag || drag.moved) return;
                drag.longPressed = true;
                fireContextMenu(drag.x, drag.y, drag.fallback);
            }, LONGPRESS_MS);
            fire('mousedown', t.clientX, t.clientY, { buttons: 1, fallback: e.target });
        } else if(e.touches.length === 2){
            cancelLongPress();
            if(drag){ fire('mouseup', drag.x, drag.y, { fallback: drag.fallback }); drag = null; }
            if(shouldSkip(e.target)) return;
            e.preventDefault();
            pinch = Object.assign(pinchState(e.touches), { acc: 0 });
        } else {
            cancelLongPress();
            pinch = null;
        }
    }, { passive: false });

    document.addEventListener('touchmove', e => {
        if(pinch && e.touches.length >= 2){
            e.preventDefault();
            const now = pinchState(e.touches);
            if(pinch.d > 0 && now.d > 0){
                pinch.acc += Math.log(now.d / pinch.d);
                while(Math.abs(pinch.acc) >= PINCH_STEP){
                    fireWheel(now.x, now.y, pinch.acc > 0 ? -100 : 100);
                    pinch.acc -= Math.sign(pinch.acc) * PINCH_STEP;
                }
            }
            pinch.d = now.d; pinch.x = now.x; pinch.y = now.y;
            return;
        }
        if(!drag) return;
        const t = [...e.touches].find(t => t.identifier === drag.id);
        if(!t) return;
        e.preventDefault();
        drag.x = t.clientX; drag.y = t.clientY;
        if(Math.abs(t.clientX - drag.startX) > TAP_MOVE || Math.abs(t.clientY - drag.startY) > TAP_MOVE){
            drag.moved = true;
            cancelLongPress(); // 已判定为拖动，不再弹长按菜单
        }
        fire('mousemove', t.clientX, t.clientY, { buttons: 1, fallback: drag.fallback });
    }, { passive: false });

    function onTouchFinish(e){
        cancelLongPress();
        if(pinch && e.touches.length < 2) pinch = null;
        if(!drag) return;
        const t = [...e.changedTouches].find(t => t.identifier === drag.id);
        if(!t) return;
        const d = drag;
        drag = null;
        e.preventDefault(); // 抑制浏览器补发的兼容 mousedown/mouseup/click，避免重复
        fire('mouseup', t.clientX, t.clientY, { fallback: d.fallback });
        if(e.type === 'touchcancel') return;
        // 长按已弹出菜单：不再补发 click/dblclick。
        // 否则 click 会落进刚打开的菜单（坐标重叠）而误触发其中的项；
        // 同时清掉 lastTap，避免这次长按被当成双击的前半段。
        if(d.longPressed){
            lastTap = { time: 0, x: 0, y: 0 };
            return;
        }
        if(!d.moved && Date.now() - d.time < TAP_TIME){
            fire('click', t.clientX, t.clientY, { fallback: d.fallback });
            const now = Date.now();
            if(now - lastTap.time < DBL_TIME && Math.hypot(t.clientX - lastTap.x, t.clientY - lastTap.y) < DBL_DIST){
                fire('dblclick', t.clientX, t.clientY, { detail: 2, fallback: d.fallback });
                lastTap = { time: 0, x: 0, y: 0 };
            } else {
                lastTap = { time: now, x: t.clientX, y: t.clientY };
            }
        }
    }
    document.addEventListener('touchend', onTouchFinish, { passive: false });
    document.addEventListener('touchcancel', onTouchFinish, { passive: false });
})();
