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

router = APIRouter()




@router.post("/api/comfyui/upload-base64")

async def upload_comfyui_base64(payload: Base64UploadRequest):

    """base64 方式把图片传到 ComfyUI 各后端的 input 目录，返回 comfy 用文件名（供 UXP 做 ComfyUI 图生图）。"""

    raw = (payload.data or "").strip()

    ct = (payload.content_type or "").split(";", 1)[0].strip().lower()

    if raw.startswith("data:"):

        header, _, raw = raw.partition(",")

        if not ct:

            ct = header[5:].split(";", 1)[0].strip().lower()

    try:

        content = base64.b64decode(raw, validate=False)

    except Exception:

        raise HTTPException(status_code=400, detail="数据无法解码")

    if not content:

        raise HTTPException(status_code=400, detail="内容为空")

    _, ext = _local_upload_kind_ext(payload.name or "", ct or "image/png")

    filename = f"dx_{uuid.uuid4().hex[:12]}{ext or '.png'}"

    comfy_name = None

    for addr in core.COMFYUI_INSTANCES:

        try:

            resp = requests.post(f"http://{addr}/upload/image",

                                 files={'image': (filename, content, ct or 'image/png')}, timeout=10)

            if resp.status_code == 200:

                comfy_name = resp.json().get("name", filename)

        except Exception as exc:

            print(f"ComfyUI base64 upload error for {addr}: {exc}")

    if not comfy_name:

        raise HTTPException(status_code=502, detail="上传到 ComfyUI 失败")

    return {"name": comfy_name}



@router.get("/api/comfyui/instances")

def get_comfyui_instances():

    return {"instances": core.COMFYUI_INSTANCES}



@router.put("/api/comfyui/instances")

def save_comfyui_instances(payload: ComfyInstancesPayload):

    # 宽容校验：去前后空白、去 http(s):// 前缀、去尾部斜杠；要求形如 host:port

    cleaned = []

    for item in payload.instances:

        s = str(item or "").strip()

        if not s:

            continue

        s = re.sub(r"^https?://", "", s)

        s = s.rstrip("/")

        if ":" not in s:

            raise HTTPException(status_code=400, detail=f"地址缺少端口号：{item}（应为 host:port，例如 127.0.0.1:8188）")

        host, _, port = s.rpartition(":")

        if not host or not port.isdigit():

            raise HTTPException(status_code=400, detail=f"地址不合法：{item}（应为 host:port，例如 127.0.0.1:8188）")

        if s in cleaned:

            continue

        cleaned.append(s)

    if not cleaned:

        raise HTTPException(status_code=400, detail="至少保留一个 ComfyUI 后端地址")

    # 写入 env 文件

    try:

        update_env_values({"COMFYUI_INSTANCES": ",".join(cleaned)})

    except Exception as e:

        raise HTTPException(status_code=500, detail=f"写入 env 失败：{e}")

    # 更新进程中的全局变量

    core.COMFYUI_INSTANCES = cleaned

    core.COMFYUI_ADDRESS = cleaned[0]

    new_load = {addr: 0 for addr in cleaned}

    for addr, n in (core.BACKEND_LOCAL_LOAD or {}).items():

        if addr in new_load:

            new_load[addr] = n

    core.BACKEND_LOCAL_LOAD = new_load

    return {"instances": core.COMFYUI_INSTANCES}
