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




@router.get("/api/storage-settings")

async def get_storage_settings():

    settings = load_storage_settings()

    return {

        "dirs": settings["dirs"],

        "defaults": {key: os.path.abspath(value) for key, value in DEFAULT_STORAGE_DIRS.items()},

    }



@router.patch("/api/storage-settings")

async def update_storage_settings(payload: Dict[str, str]):

    return save_storage_settings(payload or {})



@router.get("/api/storage-files")

async def list_storage_files(kind: str = "generated", offset: int = 0, limit: int = 80):

    root = storage_kind_dir(kind)

    os.makedirs(root, exist_ok=True)

    offset = max(0, int(offset or 0))

    limit = max(20, min(200, int(limit or 80)))

    items = []

    for current, dirs, files in os.walk(root):

        dirs[:] = sorted([d for d in dirs if not d.startswith(".") and not d.startswith("._")], key=str.lower)

        for name in sorted(files, key=str.lower):

            if name.startswith(".") or name.startswith("._"):

                continue

            if os.path.splitext(name)[1].lower() not in STORAGE_IMAGE_EXTS:

                continue

            item = storage_file_item(kind, root, os.path.join(current, name))

            if item:

                items.append(item)

    items.sort(key=lambda item: item.get("created_at") or 0, reverse=True)

    total = len(items)

    page_items = items[offset:offset + limit]

    return {

        "kind": kind,

        "root": root,

        "items": page_items,

        "total": total,

        "offset": offset,

        "limit": limit,

        "has_more": offset + len(page_items) < total,

    }



@router.get("/api/storage-files/{kind}/{rel_path:path}")

async def get_storage_file(kind: str, rel_path: str):

    path = storage_file_path(kind, rel_path)

    if not path or not os.path.isfile(path):

        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(path, media_type=content_type_for_path(path))



@router.post("/api/storage-files/delete")

async def delete_storage_files(payload: Dict[str, Any]):

    kind = str((payload or {}).get("kind") or "").strip()

    rels = [str(item or "").strip() for item in ((payload or {}).get("items") or []) if str(item or "").strip()]

    if not rels:

        raise HTTPException(status_code=400, detail="请选择要删除的文件")

    removed = 0

    for rel in rels:

        path = storage_file_path(kind, rel)

        if not path or not os.path.isfile(path):

            continue

        try:

            os.remove(path)

            removed += 1

        except OSError:

            pass

    return {"removed": removed}



@router.post("/api/upload")

async def upload_image(files: List[UploadFile] = File(...)):

    uploaded_files = []

    files_content = []

    for file in files:

        content = await file.read()

        files_content.append((file, content))



    for file, content in files_content:

        success_count = 0

        last_result = None

        for addr in core.COMFYUI_INSTANCES:

            try:

                files_data = {'image': (file.filename, content, file.content_type)}

                response = requests.post(f"http://{addr}/upload/image", files=files_data, timeout=5)

                if response.status_code == 200:

                    last_result = response.json()

                    success_count += 1

            except Exception as e:

                print(f"Upload error for {addr}: {e}")



        if success_count > 0 and last_result:

            uploaded_files.append({"comfy_name": last_result.get("name", file.filename)})

        else:

            raise HTTPException(status_code=500, detail="Failed to upload to any backend")



    return {"files": uploaded_files}



@router.post("/api/ai/upload")

async def upload_ai_reference(files: List[UploadFile] = File(...)):

    uploaded = []

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    video_exts = {".mp4", ".webm", ".mov", ".m4v", ".flv"}

    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

    doc_exts = {".pdf", ".txt", ".md", ".markdown", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".json", ".zip", ".yaml", ".yml", ".log"}

    max_upload_bytes = 50 * 1024 * 1024

    for file in files:

        content = await file.read()

        if not content:

            continue

        if len(content) > max_upload_bytes:

            raise HTTPException(status_code=413, detail=f"{file.filename or '文件'} 超过 50MB，无法上传")

        ext = os.path.splitext(file.filename or "")[1].lower()

        content_type = (file.content_type or "").lower()

        kind = "image"

        if ext in video_exts or content_type.startswith("video/"):

            kind = "video"

            if ext not in video_exts:

                ext = ".webm" if "webm" in content_type else ".mov" if "quicktime" in content_type else ".mp4"

        elif ext in audio_exts or content_type.startswith("audio/"):

            kind = "audio"

            if ext not in audio_exts:

                ext = ".wav" if "wav" in content_type else ".ogg" if "ogg" in content_type else ".m4a" if "mp4" in content_type else ".mp3"

        elif ext in image_exts or content_type.startswith("image/"):

            kind = "image"

            if ext not in image_exts:

                ext = ".jpg" if "jpeg" in content_type else ".webp" if "webp" in content_type else ".gif" if "gif" in content_type else ".png"

        elif ext in doc_exts or content_type.startswith(("text/", "application/")):

            kind = "file"

            if not ext:

                ext = mimetypes.guess_extension(content_type) or ".bin"

        else:

            kind = "file"

            if not ext:

                ext = ".bin"

        filename = f"ai_ref_{uuid.uuid4().hex[:12]}{ext}"

        path = output_path_for(filename, "input")

        with open(path, "wb") as f:

            f.write(content)

        uploaded.append({"url": output_url_for(filename, "input"), "name": file.filename or filename, "kind": kind, "mime": content_type})

    return {"files": uploaded}



@router.post("/api/ai/upload-base64")

async def upload_ai_base64(payload: Base64UploadRequest):

    """以 base64 JSON 方式上传字节到 assets/input，返回 /assets 地址。

    给不便用 multipart/FormData 的客户端（如 PS UXP 面板）用——UXP 的 fetch+FormData 经常发不出有效 multipart。"""

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

    if len(content) > 50 * 1024 * 1024:

        raise HTTPException(status_code=413, detail="超过 50MB")

    kind, ext = _local_upload_kind_ext(payload.name or "", ct or "image/png")

    if kind is None:

        kind, ext = "image", ".png"

    filename = f"ai_ref_{uuid.uuid4().hex[:12]}{ext}"

    path = output_path_for(filename, "input")

    with open(path, "wb") as f:

        f.write(content)

    return {"files": [{"url": output_url_for(filename, "input"), "name": payload.name or filename, "kind": kind}]}



@router.post("/api/ai/import-local-image")

async def import_local_ai_reference(payload: LocalImageImportRequest, request: Request):

    ensure_same_origin_request(request)

    requested = [payload.path] if payload.path else []

    requested.extend(payload.paths or [])

    requested = [p for p in requested if str(p or "").strip()][:20]

    if not requested:

        raise HTTPException(status_code=400, detail="没有可导入的本地图片")

    return {"files": [import_local_image_file(normalize_local_image_path(path)) for path in requested]}
