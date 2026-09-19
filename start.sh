#!/usr/bin/env bash
# Infinite-Canvas Linux 启动脚本
# 用法: ./start.sh            （前台启动）
#       HOST=0.0.0.0 PORT=3000 ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "错误: 未找到 $PYTHON，请安装 Python 3.10+（apt install python3 python3-venv python3-pip）" >&2
    exit 1
fi

if [ ! -d .venv ]; then
    echo ">> 创建虚拟环境 .venv ..."
    "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo ">> 安装/同步 Python 依赖 ..."
pip install -q -r requirements.txt

echo ">> 启动 Infinite-Canvas（HOST=${HOST:-0.0.0.0} PORT=${PORT:-3000}）"
exec python main.py
