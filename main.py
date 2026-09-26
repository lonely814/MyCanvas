import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # 确保 app 包可导入（内嵌 Python 必需）
import json

import uuid

import base64

import hashlib

import hmac

import datetime

import urllib.request

import urllib.parse

import urllib.error

import os

import re

import random

import sys

import subprocess

import time

import traceback

import shutil

import glob

import asyncio

import logging

import requests

import zipfile

import mimetypes

import tempfile

import math

import shlex

import functools

import html

import xml.etree.ElementTree as ET

from typing import List, Dict, Any, Optional, Tuple

from threading import Lock, Thread

import httpx

from PIL import Image, ImageOps

from io import BytesIO

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Header, Request

from fastapi.exceptions import RequestValidationError

from fastapi.staticfiles import StaticFiles

from fastapi import APIRouter

from fastapi.responses import FileResponse, Response, StreamingResponse, JSONResponse, HTMLResponse

from pydantic import BaseModel, Field

from fastapi.middleware.cors import CORSMiddleware



from app.core import *

import app.core as core

from app.routers.misc import router as misc_router
from app.routers.storage import router as storage_router
from app.routers.assets import router as assets_router
from app.routers.comfyui import router as comfyui_router
from app.routers.runninghub import router as runninghub_router
from app.routers.cli import router as cli_router
from app.routers.providers import router as providers_router
from app.routers.generation import router as generation_router
from app.routers.canvases import router as canvases_router
from app.routers.prompt_lib import router as prompt_lib_router





app = FastAPI()



# --- 访问控制（可选）---

# 默认无鉴权，适合纯内网使用。设置 API_TOKEN 后：

# - 所有 /api/*、/ws 要求携带令牌（浏览器访问 / 会先进入解锁页，令牌写入 Cookie，一次输入全站生效）

# - 程序化调用可使用 Authorization: Bearer <token> 或 ?api_token=<token>

# 公网部署建议反代层（Nginx/Caddy）再加一层 Basic Auth。

API_TOKEN = os.getenv("API_TOKEN", "").strip()

if os.getenv("ALLOWED_ORIGINS", "").strip() == "*":

    _cors_origins = ["*"]

else:

    _cors_origins = ALLOWED_ORIGINS  # 默认空列表 = 不返回 CORS 头 = 仅同源可用



app.add_middleware(

    CORSMiddleware,

    allow_origins=_cors_origins,

    allow_methods=["*"],

    allow_headers=["*"],

)



def _request_api_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.query_params.get("api_token", "").strip()

def _api_token_ok(request) -> bool:

    if not API_TOKEN:

        return True

    if _request_api_token(request) == API_TOKEN:

        return True

    return request.cookies.get("ic_token", "") == API_TOKEN



_UNLOCK_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">

<meta name="viewport" content="width=device-width,initial-scale=1">

<title>Infinite-Canvas 访问验证</title>

<style>body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8e8ea;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}

.card{background:#181b22;padding:32px;border-radius:14px;width:min(360px,90vw)}

input{width:100%;box-sizing:border-box;padding:10px;margin:14px 0;border-radius:8px;border:1px solid #2c2f38;background:#0f1115;color:#e8e8ea;font-size:14px}

button{width:100%;padding:10px;border:0;border-radius:8px;background:#6366f1;color:#fff;font-size:14px;cursor:pointer}

.err{color:#f87171;font-size:12px;min-height:16px}</style></head>

<body><div class="card"><h3 style="margin:0 0 6px">访问验证</h3>

<p style="font-size:12px;color:#8f9aab;margin:0">本服务已开启令牌保护，请输入访问令牌。</p>

<form id="f"><input id="t" type="password" placeholder="访问令牌" autofocus><div class="err" id="e"></div>

<button type="submit">进入</button></form></div>

<script>document.getElementById('f').addEventListener('submit',async ev=>{ev.preventDefault();

const r=await fetch('/api/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:document.getElementById('t').value})});

if(r.ok){location.replace('/');}else{document.getElementById('e').textContent='令牌不正确，请重试';}});</script></body></html>"""



if API_TOKEN:

    @app.post("/api/unlock")

    async def api_unlock(request: Request):

        try:

            payload = await request.json()

        except Exception:

            raise HTTPException(status_code=400, detail="invalid body")

        if str(payload.get("token") or "").strip() != API_TOKEN:

            raise HTTPException(status_code=401, detail="token 不正确")

        response = JSONResponse({"ok": True})

        response.set_cookie("ic_token", API_TOKEN, max_age=30 * 24 * 3600, httponly=True, samesite="lax")

        return response



    @app.middleware("http")

    async def api_token_guard(request: Request, call_next):

        path = request.url.path

        if path in ("/api/unlock", "/healthz", "/favicon.ico"):

            return await call_next(request)

        if _api_token_ok(request):

            return await call_next(request)

        if path == "/" or (path.startswith("/static/") and path.endswith(".html")):

            return HTMLResponse(_UNLOCK_PAGE, status_code=401)

        return JSONResponse({"detail": "Unauthorized"}, status_code=401)



@app.get("/healthz")

def healthz():

    return {"ok": True}



@app.on_event("startup")

async def startup_event():

    core.GLOBAL_LOOP = asyncio.get_running_loop()

    # 先同步 i18n 加载器（会写回 i18n.js，改变其 mtime），
    # 再同步 HTML 缓存版本，否则 HTML 记下的是 i18n.js 写入前的旧 mtime。
    sync_i18n_loader_version()

    sync_static_html_versions()

    # 读回上次的画布任务状态：进程重启后前端仍能查到已完成/失败的任务，
    # 而不是一律收到 404「任务状态已丢失」。
    canvas_task_load()

    # 启动时整理资产库：给所有图片分组（含默认角色/场景）建好文件夹，并把根目录里的旧素材归整进去。

    try:

        await asyncio.to_thread(migrate_asset_library_into_dirs)

    except Exception as exc:

        print(f"资产库分组整理失败: {exc}")

    # 修复历史遗留的双重扩展名素材（foo.png.png → foo.png），否则这些卡片无法显示

    try:

        await asyncio.to_thread(migrate_double_extension_uploads)

    except Exception as exc:

        print(f"修复双重扩展名素材失败: {exc}")

    # 纠正内容与扩展名不符的图片（如 WebP 内容却叫 .png），否则严格客户端解不出来

    try:

        await asyncio.to_thread(migrate_mislabeled_image_extensions)

    except Exception as exc:

        print(f"纠正图片扩展名失败: {exc}")



@app.websocket("/ws/stats")

async def websocket_endpoint(websocket: WebSocket, client_id: str = None):

    if API_TOKEN:

        token = websocket.query_params.get("api_token", "").strip() or websocket.cookies.get("ic_token", "")

        if token != API_TOKEN:

            await websocket.close(code=4401)

            return

    await manager.connect(websocket, client_id)

    try:

        while True:

            data = await websocket.receive_text()

            if data == "ping":

                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:

        await manager.disconnect(websocket, client_id)

    except Exception as e:

        print(f"WS Error: {e}")

        await manager.disconnect(websocket, client_id)



@app.exception_handler(RequestValidationError)

async def request_validation_exception_handler(request: Request, exc: RequestValidationError):

    return JSONResponse(

        status_code=422,

        content={"detail": friendly_validation_error(exc.errors()), "errors": exc.errors()},

    )



app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")

app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")



@app.exception_handler(JimengPendingError)

async def jimeng_pending_exception_handler(request: Request, exc: JimengPendingError):

    # 轮询超时但任务还在云端排队：返回 202 + submit_id，让前端保持「排队中」卡片并续查

    return JSONResponse(status_code=202, content=jimeng_pending_payload(exc))



app.include_router(misc_router)

app.include_router(storage_router)

app.include_router(assets_router)

app.include_router(comfyui_router)

app.include_router(runninghub_router)

app.include_router(cli_router)

app.include_router(providers_router)

app.include_router(generation_router)

app.include_router(canvases_router)

app.include_router(prompt_lib_router)





if __name__ == "__main__":

    import uvicorn

    # 关闭服务端协议级 WebSocket ping：部分客户端（如 PS UXP 面板）不会自动回 pong，

    # 默认 20s ping/20s 超时会把这些连接每隔一会儿就踢掉造成"频繁断连"。

    # 客户端有自己的应用层心跳 + 断线重连兜底，这里禁用协议 ping 更稳。

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "3000")),

                ws_ping_interval=None, ws_ping_timeout=None)
