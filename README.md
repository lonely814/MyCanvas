# Infinite-Canvas（二开精简版）

基于 [wuli大雄 的 Infinite-Canvas](https://github.com/hero8152/Infinite-Canvas)（2026.08.28 停更版）二次开发的个人自用分支。

二开改动：仅面向 **Linux 服务器部署**，拆除了原作者的自更新系统、推广/返利内容、Windows/macOS 桌面分发物料与 gpt-chat / smart-canvas 等未使用模块。**上游已停更，本分支不与上游同步。**

## 功能概览

- 无限画布（经典画布）：图像/视频素材排版、生成、编辑、导出
- AI 渠道：OpenAI 兼容协议 / Gemini / 火山方舟 / APIMart / ModelScope / RunningHub 工作流 / 即梦 CLI / Codex CLI，统一在「API 设置」页配置
- 本地/局域网 ComfyUI 反代与工作流调用（`workflows/`）
- 素材库、提示词库、项目分组、回收站（30 天）
- 视频能力：LTX 时间轴导演、数字人视频、360 全景
- 配套工具：`tools/` 下 Chrome 采集插件与 Photoshop 直连插件

## 架构

- 后端：Python 3.10+ / FastAPI，单文件 `main.py`（后续计划拆分为 `app/` 包）
- 前端：无构建原生 HTML/JS（`static/`），第三方库本地化于 `static/vendor/`
- 存储：纯 JSON 文件 + 文件系统（`data/`、`API/.env`、`output/`、`assets/`），无数据库
- 实时通知：WebSocket `/ws/stats`（在线人数 / 画布更新广播）

## Linux 服务器部署

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
- 公网使用：务必置于 Nginx/Caddy 反代 + Basic Auth 之后，或设置 `API_TOKEN` 启用应用层 Token 鉴权

### 外部 CLI 渠道（可选）

即梦 / Codex / Gemini 渠道依赖对应 CLI（见 `CLI/README.md`），需在服务器上以运行服务的用户身份安装并登录。

## 许可与署名

本项目沿用上游的自定义许可（见 `LICENSE`）：**禁止商用；二次开发衍生品必须保持开源并注明原作者**。原作者：wuli大雄（[hero8152/Infinite-Canvas](https://github.com/hero8152/Infinite-Canvas)）。
