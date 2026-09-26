# Infinite-Canvas（二开版）

基于 [wuli大雄 的 Infinite-Canvas](https://github.com/hero8152/Infinite-Canvas) 二次开发。

原版最后更新于 **2026-08-28**（当时 3.1k star），之后停更；本分支在此基础上继续维护。
**不与上游同步**，也不建议把上游改动合并回来——文件结构已经分叉。

原作者：**wuli大雄**。二次开发与衍生必须保持开源并注明原作者，这是原版许可（见 `LICENSE`）的硬性要求。

---

## 一、相对原版的改进

### 1. 安全

| 项 | 说明 |
|---|---|
| **修复 SSRF 漏洞** | `/api/download-output` 等接口接受前端传入的任意 URL 并由服务端抓取，原先只校验协议、不校验目标地址。凡是能打开页面的人都能拿它探测内网、读云厂商 metadata——而本应用所在服务器通常能连到内网的 ComfyUI，所以这不是纸面风险。现在会解析目标域名取全部 IP，拒绝本机 / 内网 / 链路本地 / 组播 / 保留地址，并额外封掉 `100.64.0.0/10`（云 metadata 常见网段，Python 的 `ipaddress` 不视其为 private，是个易漏点）。重定向改为**逐跳校验**，否则用一个公网地址 302 到 `127.0.0.1` 就能绕过首次检查。 |
| **CLI 日志脱敏** | Codex / 即梦 CLI 的 stderr 常把带 key 的请求行、配置内容打进错误信息，原先原样回传前端。现在回传前抹掉明文凭据。此处有个取舍：只匹配明确的凭据标记（`key=`、`Bearer`、`sk-` 前缀），**不按「长随机串」兜底**——即梦的任务 ID 是从 stdout 解析出来的，一刀切会毁掉轮询，所以 stdout 不脱敏、只处理 stderr。 |

### 2. 画布稳定性

| 项 | 说明 |
|---|---|
| **任务状态落盘** | 画布任务原先只存在内存里，而且没有任何清理。进程重启后前端轮询一律拿到 404「任务状态已丢失」，**哪怕那次生成其实已经成功、图片也已经存到磁盘**。现在任务元数据落盘到 `data/canvas_tasks.json`，启动时读回，保留 24 小时、上限 500 条（`CANVAS_TASK_TTL` / `CANVAS_TASK_MAX` 可调）。重启时仍停在「排队 / 生成中」的任务会明确标记为「服务重启，任务已中断，请重新生成」，而不是让页面一直转圈，也不会无限增长吃内存。 |
| **输入法保护** | 中文等输入法组字期间按下的 `r`、`z`、`g` 会被当成快捷键，误触发刀模、撤销、打组。现在按 `isComposing` 与 `keyCode 229` 双重判断跳过。 |
| **文案缓存失效** | `static/js/i18n.js` 里的模块版本号是写死的字面量，改了文案但没改版本号时，缓存过的浏览器会一直用旧翻译，界面直接显示原始 key。现在启动时按 i18n 文件的真实修改时间自动重写版本号。 |

### 3. 画布交互

| 项 | 说明 |
|---|---|
| **重做（Redo）** | 原版只有撤销。现在支持 `Ctrl+Shift+Z` / `Ctrl+Y`，并新增工具栏撤销 / 重做按钮（按可用状态置灰）。 |
| **修复撤销对「新建节点」无效** | 原版 `addNode()` 从未调用 `pushUndo()`，**新建的节点根本进不了撤销历史**，`Ctrl+Z` 对它完全无效。已补上。 |
| **解散分组** | 原版只有打组（`Ctrl+G`），打完只能连着节点一起删。现在支持 `Ctrl+Shift+G` 解散：保留组内节点与它们的下游连线，只移除组壳。 |
| **浅色主题对比度** | 节点内说明文字原本用硬编码灰色，白底对比度低至 **1.47:1**（可读下限 4.5），浅色下几乎看不清——能看清全靠深色主题单独写的覆盖规则，浅色下没有。现在统一走语义色变量，两套主题同时达标（浅色 4.76、深色 6.85）。 |

### 4. 工作流字段映射（ComfyUI 自定义工作流）

原版的字段映射能力主要在 RunningHub 那一侧；ComfyUI 自定义工作流这边，媒体字段只能靠「字段在列表里的先后顺序」隐式分配，而且没连图时会把**空字符串**塞给 ComfyUI 的 `LoadImage`，换回一句含糊的加载失败。现补齐三个属性：

- **槽位顺序** `image_order` —— 显式指定第几张上游素材喂给哪个字段，不必删掉字段重建
- **必填** `required` —— 媒体字段没拿到输入时直接报错并点名是哪个字段，附带操作指引
- **跟随上游提示词** `source_from_upstream` —— 可关掉，固定使用本字段配置的值

三者都有默认值，**旧配置文件原样可读**。

### 5. 纯黑主题（新增第三套配色）

原版只有「浅色 / 深蓝」两套。新增一套中性**纯黑**主题，切换按钮按 `白天 → 深蓝 → 纯黑 → 白天` 循环。

实现说明：深蓝的配色是 `theme.css` 里逐条硬编码的 `!important`，**不是变量驱动**（实测把变量全设成红色只有约 2% 的界面变色），所以黑色主题无法靠覆盖变量实现。`static/css/theme-black.css` 由脚本 `tools/theme-generator/gen_black_theme.py` 从 `theme.css` 生成：把深色规则里的每个颜色按感知亮度中性化（`r=g=b`），在去掉蓝调的同时保留原有明暗层次。该文件**自动生成，勿手改**；改过 `theme.css` 的深色配色后重新运行该脚本即可。

### 6. 后端重构

原版后端是单文件 `main.py`，**880KB**。已拆为：

```
main.py            薄入口（FastAPI 实例、中间件、静态挂载、include_router）
app/core.py        配置常量、运行时状态、共享辅助与各渠道协议适配（约 12.7k 行）
app/routers/       按域拆分的 10 个路由模块，共 147 个路由
                   misc / storage / assets / comfyui / runninghub / cli
                   providers / generation / canvases / prompt_lib
```

### 7. 精简（删除未使用模块）

| 删除内容 | 体积 |
|---|---|
| `static/js/smart-canvas.js` + `static/js/i18n/smart-canvas.js` | 963KB + 26KB |
| `static/css/smart-canvas.css` | 206KB |
| `static/smart-canvas.html` | 41KB |
| `static/gpt-chat.html` | — |
| `python/`（原版内嵌的 Windows Python 运行时） | 34 个文件 |
| `packages/`（Windows 离线 wheel 包） | 22 个文件 |
| macOS / Windows 桌面分发脚本与物料（`MAC-使用说明.md`、`mac-*.command`、`CLI/windows/**` 等） | — |
| 原版的自更新系统、推广 / 返利内容 | — |

文件总数从原版 **208** 降到 **123**。

删除 smart-canvas 时保留了 3 处「历史数据过滤器」（判断 `kind != 'smart'`），用于忽略磁盘上遗留的旧画布，**不是死代码**。

### 8. 工程化

| 项 | 说明 |
|---|---|
| `VERSION` + `CHANGELOG.md` | 原版虽然有 `VERSION` 文件，但界面与仓库都不体现，`app/core.py` 里另有一个人工维护、长期未同步的 `APP_VERSION` 常量（已删）。二开版把 `VERSION` 作为静态资源缓存版本的唯一来源，并维护变更日志。 |
| `.env.example` | 环境变量清单（原版没有）。 |
| `deploy/infinite-canvas.service` | systemd 常驻部署单元。 |
| `start.sh` | 一键安装依赖并启动。 |
| `.gitignore` | 挡住运行时数据与 API 凭据。 |

---

## 二、功能概览

- **无限画布**：图像 / 视频素材排版、生成、编辑、分组、导出；缩略图由服务端生成并磁盘缓存
- **13 种节点类型**：图片、提示词、循环、LLM、API 生成、ModelScope、Midjourney、视频生成、MiniMax、LTX Director、RunningHub、ComfyUI、Output
- **AI 渠道**：OpenAI 兼容 / Gemini / 火山方舟 / APIMart / ModelScope / RunningHub 工作流 / 即梦 CLI / Codex CLI，统一在「API 设置」页配置
- **本地 ComfyUI**：反代调用与自定义工作流（`workflows/`），支持字段映射
- **素材库 / 提示词库 / 项目分组 / 回收站**（30 天）
- **视频能力**：LTX 时间轴导演、数字人视频、360 全景
- **配套工具**：`tools/` 下 Chrome 采集插件与 Photoshop 直连插件
- **国际化**：中英双语，772 条文案键

## 三、架构

- **后端**：Python 3.10+ / FastAPI，模块化包结构（见上文「后端重构」）
- **前端**：**无构建**原生 HTML/JS/CSS（`static/`，11 个页面、约 32k 行 JS），第三方库本地化于 `static/vendor/`，不依赖 Node 工具链
- **存储**：JSON 文件 + 文件系统（`data/`、`API/.env`、`output/`、`assets/`），**无数据库**
- **实时通知**：WebSocket `/ws/stats`（在线人数 / 画布更新广播）

## 四、Linux 服务器部署

依赖：Python 3.10+、pip、ffmpeg（视频功能需要）。

```bash
# 1. 安装系统依赖（Debian/Ubuntu）
sudo apt install -y python3 python3-venv python3-pip ffmpeg

# 2. 安装 Python 依赖并启动
./start.sh
# 或手动：
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py   # 默认 0.0.0.0:3000
```

systemd 常驻部署见 `deploy/infinite-canvas.service`；环境变量说明见 `.env.example`。

### 安全提示（重要）

应用本身**没有用户登录系统**，API Key 明文存于服务端。不要将 3000 端口直接暴露公网：

- 局域网使用：直接访问 `http://<服务器IP>:3000`
- 公网使用：**务必**置于 Nginx/Caddy 反代 + Basic Auth 之后，或设置 `API_TOKEN` 启用应用层 Token 鉴权

注意：反代鉴权只保护入口，防不住服务端自己发起的请求——这正是上面修复 SSRF 的原因。

### 外部 CLI 渠道（可选）

即梦 / Codex / Gemini 渠道依赖对应 CLI（见 `CLI/README.md`），需在服务器上以运行服务的用户身份安装并登录。

---

## 五、更新日志

### 2026-09-26

- **新增纯黑主题**（第三套配色），切换按钮改为三态循环
- **修复浅色主题对比度**：节点内说明文字最低仅 1.47:1，浅色下几乎不可见
- **画布任务状态落盘 + TTL 清理**：进程重启后不再一律「任务状态已丢失」
- **工作流字段映射**：ComfyUI 侧补齐槽位顺序 / 必填 / 上游提示词开关
- **修复 i18n 文案缓存不刷新**：`i18n.js` 版本号写死，导致新文案在缓存过的浏览器上不生效
- **新增重做、解散分组、输入法保护**；修复撤销对「新建节点」无效
- **CLI 日志脱敏**
- **修复 SSRF 漏洞**：`/api/download-output` 等服务端抓取接口增加内网地址拦截
- 补 `VERSION` / `CHANGELOG.md`，删除 `app/core.py` 中已无引用的 `APP_VERSION` 常量

### 更早（二开初期）

- 后端 `main.py`（880KB 单文件）拆分为 `app/` 包结构，147 个路由分 10 个模块
- 移除 smart-canvas 遗留模块（963KB JS + 206KB CSS + 41KB 页面）、`gpt-chat.html`、内嵌 Python 运行时、离线 wheel 包与桌面分发物料
- 画布 UI 改版：统一节点类型注册表驱动工具栏与各新建菜单，节点视觉压平 + 类型色条，状态与端口重做，空画布引导，连线悬停高亮
- 节点增量渲染（内容未变的节点复用 DOM），缩略图服务端生成与磁盘缓存

---

## 六、许可与署名

本项目沿用上游的自定义许可（见 `LICENSE`）：**禁止商用；二次开发衍生品必须保持开源并注明原作者**。

原作者：**wuli大雄**（[hero8152/Infinite-Canvas](https://github.com/hero8152/Infinite-Canvas)）。

本分支的二次开发内容同样适用上述条款。
