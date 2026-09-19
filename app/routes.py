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





@router.get("/api/app-info")

def app_info():

    return {"version": current_app_version()}



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



@router.get("/api/asset-classification-prompt")

async def get_asset_classification_prompt():

    current = load_asset_classification_prompt()

    return {

        "prompt": current,

        "default_prompt": ASSET_CLASSIFICATION_PROMPT,

        "custom": current.strip() != ASSET_CLASSIFICATION_PROMPT.strip(),

    }



@router.patch("/api/asset-classification-prompt")

async def update_asset_classification_prompt(payload: Dict[str, str]):

    prompt = save_asset_classification_prompt((payload or {}).get("prompt") or "")

    return {"prompt": prompt, "custom": True}



@router.get("/api/media-preview")

async def media_preview(url: str, w: int = 512):

    path = output_file_from_url(url)

    if not path or not os.path.isfile(path):

        raise HTTPException(status_code=404, detail="媒体文件不存在")



    width = max(64, min(2048, int(w or 512)))

    webp_path, png_path = media_preview_cache_paths(path, width)



    if os.path.exists(webp_path):

        return FileResponse(webp_path, media_type="image/webp")

    if os.path.exists(png_path):

        return FileResponse(png_path, media_type="image/png")



    def _build_preview():

        # 同步 PIL 处理 + 落盘，放到线程里执行，避免阻塞事件循环（几十张首次生成会卡死整个 loop → 缩略图全空白）

        os.makedirs(MEDIA_PREVIEW_DIR, exist_ok=True)

        if is_video_preview_file(path):

            img = generate_video_preview_image(path, width)

        else:

            with Image.open(path) as source:

                img = ImageOps.exif_transpose(source)

                img.thumbnail((width, width), Image.LANCZOS)

                img = img.convert("RGBA" if image_has_alpha(img) else "RGB")

        try:

            img.save(webp_path, format="WEBP", quality=80, method=1)   # method=1 生成更快（缩略图不追求极致压缩）

            return webp_path, "image/webp"

        except Exception:

            img.save(png_path, format="PNG")

            return png_path, "image/png"



    try:

        out_path, media_type = await asyncio.to_thread(_build_preview)

        return FileResponse(out_path, media_type=media_type)

    except Exception as exc:

        raise HTTPException(status_code=415, detail=f"无法生成预览图：{exc}") from exc



@router.get("/api/image-jpeg")

async def image_jpeg(url: str, w: int = 0):

    """把任意图片转成 JPEG 返回（带缓存）。给不支持 WebP 等格式显示的客户端（PS UXP）用。

    w>0 时同时缩放到该宽度（缩略图）；w=0 输出原尺寸。"""

    path = output_file_from_url(url)

    if not path or not os.path.isfile(path):

        raise HTTPException(status_code=404, detail="媒体文件不存在")

    width = max(0, min(4096, int(w or 0)))

    stat = os.stat(path)

    key = hashlib.sha1(f"{os.path.abspath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{width}|jpg".encode("utf-8", "ignore")).hexdigest()

    cache_path = os.path.join(MEDIA_PREVIEW_DIR, f"{key}.jpg")

    if os.path.exists(cache_path):

        return FileResponse(cache_path, media_type="image/jpeg")



    def _build():

        os.makedirs(MEDIA_PREVIEW_DIR, exist_ok=True)

        with Image.open(path) as src:

            img = ImageOps.exif_transpose(src)

            if width:

                img.thumbnail((width, width), Image.LANCZOS)

            if img.mode in ("RGBA", "LA", "P"):

                bg = Image.new("RGB", img.size, (255, 255, 255))

                rgba = img.convert("RGBA")

                bg.paste(rgba, mask=rgba.split()[-1])

                img = bg

            else:

                img = img.convert("RGB")

            img.save(cache_path, format="JPEG", quality=86)

        return cache_path



    try:

        out_path = await asyncio.to_thread(_build)

        return FileResponse(out_path, media_type="image/jpeg")

    except Exception as exc:

        raise HTTPException(status_code=415, detail=f"无法转换图片：{exc}") from exc



# --- 路由接口 ---



@router.get("/")

async def index():

    return static_html_response("index.html")



@router.get("/api/view")

def view_image(filename: str, type: str = "input", subfolder: str = ""):

    # 先按原逻辑去各 ComfyUI 后端找

    for addr in core.COMFYUI_INSTANCES:

        try:

            url = f"http://{addr}/view"

            params = {"filename": filename, "type": type, "subfolder": subfolder}

            r = requests.get(url, params=params, timeout=1)

            if r.status_code == 200:

                return Response(content=r.content, media_type=r.headers.get('Content-Type'))

        except Exception:

            continue

    # 后端都拿不到时回退本地 assets/<input|output>/

    # 适用场景：画布通过 /api/ai/upload 把参考图直接落到本地 assets/input/，

    # 但 ComfyUI 的 input 可能因为重启/清理而丢失，导致 enhance/klein 等页面预览对比图 404

    if not subfolder and type in ("input", "output"):

        safe_name = os.path.basename(filename or "")

        if safe_name:

            local_path = output_path_for(safe_name, "input" if type == "input" else "output")

            if os.path.isfile(local_path):

                return FileResponse(local_path, media_type=content_type_for_path(local_path))

    raise HTTPException(status_code=404, detail="Image not found on any available backend")



@router.get("/api/download-output")

def download_output(request: Request, url: str, name: str = "", inline: bool = False):

    url = rewrite_runninghub_file_url(url)

    path = output_file_from_url(url)

    if not path:

        path = local_media_file_by_basename(filename_from_media_url(url, ""))

    if path:

        filename = sanitize_export_filename(os.path.basename(name) if name else os.path.basename(path), os.path.basename(path))

        return FileResponse(path, media_type=content_type_for_path(path), filename=None if inline else filename)

    # 远程文件：流式代理，绝不把整段视频/大文件读进内存（否则多个视频同时代理会撑爆内存、拖垮单进程服务）。

    parsed = urllib.parse.urlparse(str(url or "").strip())

    if parsed.scheme not in ("http", "https") or not parsed.netloc:

        raise HTTPException(status_code=400, detail="无效的下载地址")

    try:

        upstream_headers = {"User-Agent": "ComfyUI-API-Modelscope/1.0"}

        range_header = request.headers.get("range")

        if range_header:

            upstream_headers["Range"] = range_header

        upstream = requests.get(

            url, stream=True, timeout=(10, 60),

            headers=upstream_headers,

        )

        upstream.raise_for_status()

    except requests.RequestException as exc:

        raise HTTPException(status_code=502, detail=f"远程文件下载失败：{exc}")

    content_type = upstream.headers.get("content-type") or "application/octet-stream"

    fallback = filename_from_media_url(url, "download.bin")

    filename = sanitize_export_filename(os.path.basename(name) if name else fallback, fallback)

    disposition = "inline" if inline else "attachment"

    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{urllib.parse.quote(filename)}"}

    for key in ("content-range", "accept-ranges"):

        value = upstream.headers.get(key)

        if value:

            headers["-".join(part.capitalize() for part in key.split("-"))] = value



    def stream_remote():

        try:

            for chunk in upstream.iter_content(chunk_size=256 * 1024):

                if chunk:

                    yield chunk

        finally:

            upstream.close()



    return StreamingResponse(stream_remote(), media_type=content_type, headers=headers, status_code=upstream.status_code)



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



@router.post("/api/local-assets/upload")

async def upload_local_assets(files: List[UploadFile] = File(...), folder: str = Form("")):

    uploaded = []

    folder_rel, folder_abs = _local_upload_safe_folder(folder)

    os.makedirs(folder_abs, exist_ok=True)

    for file in files:

        content = await file.read()

        if not content:

            continue

        kind, ext = _local_upload_kind_ext(file.filename, file.content_type)

        if kind is None:

            continue

        base = os.path.splitext(os.path.basename(file.filename or "file"))[0]

        base = re.sub(r"[^0-9A-Za-z一-鿿._-]+", "_", base).strip("_") or "file"

        base = base[:60]

        filename = f"up_{uuid.uuid4().hex[:12]}_{base}{ext}"

        rel_name = f"{folder_rel}/{filename}".lstrip("/")

        path = os.path.join(folder_abs, filename)

        with open(path, "wb") as f:

            f.write(content)

        if kind == "image":

            classification = await classify_asset_image_best_effort(path)

            if classification:

                _write_local_upload_classification(rel_name, classification)

        uploaded.append(_local_upload_item(rel_name))

    return {"files": uploaded}



@router.post("/api/local-assets/import-urls")

async def import_local_assets_from_urls(payload: LocalAssetUrlImportRequest):

    uploaded = []

    results = []

    folder_rel, folder_abs = _local_upload_safe_folder(payload.folder)

    os.makedirs(folder_abs, exist_ok=True)

    timeout = httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "Infinite-Canvas-Asset-Importer/1.0"}) as client:

        for entry in (payload.items or [])[:200]:

            src_url = str(entry.url or "").strip()

            inline_data = str(entry.data or "").strip()

            result = {"url": src_url, "ok": False, "file": "", "error": ""}

            if not inline_data and not src_url.startswith(("http://", "https://")):

                result["error"] = "仅支持 http(s) 素材地址"

                results.append(result)

                continue

            try:

                if inline_data:

                    # 插件已在网页上下文里把字节读成 base64（dataURL 形如 data:<ct>;base64,<payload>）

                    content_type = str(entry.content_type or "").split(";", 1)[0].strip().lower()

                    b64 = inline_data

                    if inline_data.startswith("data:"):

                        header, _, b64 = inline_data.partition(",")

                        if not content_type:

                            content_type = header[5:].split(";", 1)[0].strip().lower()

                    try:

                        content = base64.b64decode(b64, validate=False)

                    except Exception:

                        raise HTTPException(status_code=400, detail="素材数据无法解码")

                    name_path = urllib.parse.urlparse(src_url).path

                else:

                    response = await client.get(src_url)

                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

                    content = response.content

                    name_path = urllib.parse.urlparse(src_url).path

                kind, ext = _local_upload_kind_ext(name_path, content_type)

                if kind == "image":

                    real = _sniff_image_ext_bytes(content[:16])   # 以真实内容为准，避免 webp 被叫成 .png 等

                    if real and not (real == ".jpg" and ext == ".jpeg"):

                        ext = real

                if kind not in ("image", "video"):

                    raise HTTPException(status_code=400, detail=f"不是图片或视频资源：{content_type or src_url}")

                if not content:

                    raise HTTPException(status_code=400, detail="素材内容为空")

                # entry.name 可能自带扩展名（采集器常传完整文件名），先 splitext 去掉，否则会和下面拼接的 ext 叠成 .png.png

                if entry.name:

                    base = os.path.splitext(entry.name)[0]

                else:

                    base = os.path.splitext(os.path.basename(urllib.parse.unquote(name_path)))[0]

                base = base or ("web-video" if kind == "video" else "web-image")

                base = re.sub(r"[^0-9A-Za-z一-鿿._-]+", "_", base).strip("_") or ("web-video" if kind == "video" else "web-image")

                base = base[:60]

                # 兜底：若 base 末尾已是同一扩展名，去掉一层再拼，杜绝重复后缀

                if ext and base.lower().endswith(ext.lower()):

                    base = base[:-len(ext)].rstrip(".") or ("web-video" if kind == "video" else "web-image")

                filename = f"up_{uuid.uuid4().hex[:12]}_{base}{ext}"

                rel_name = f"{folder_rel}/{filename}".lstrip("/")

                path = os.path.join(folder_abs, filename)

                with open(path, "wb") as f:

                    f.write(content)

                if payload.classify and kind == "image":

                    classification = await classify_asset_image_best_effort(path, payload.provider, payload.model, payload.ms_model, payload.prompt)

                    if classification:

                        _write_local_upload_classification(rel_name, classification)

                item = _local_upload_item(rel_name)

                uploaded.append(item)

                result.update({"ok": True, "file": rel_name, "item": item})

            except HTTPException as exc:

                result["error"] = str(exc.detail or "导入失败")

            except Exception as exc:

                result["error"] = str(exc) or "导入失败"

            results.append(result)

    return {"ok": True, "count": len(uploaded), "files": uploaded, "items": results}



@router.get("/api/local-assets")

async def list_local_assets():

    tree, items = _local_upload_tree_and_items()

    return {"items": items, "tree": tree}



@router.post("/api/local-assets/folders")

async def create_local_asset_folder(payload: LocalAssetFolderRequest, request: Request):

    ensure_same_origin_request(request)

    parent_rel, parent_abs = _local_upload_safe_folder(payload.parent)

    if not os.path.isdir(parent_abs):

        raise HTTPException(status_code=404, detail="父文件夹不存在")

    name = _local_upload_safe_folder_name(payload.name)

    rel = f"{parent_rel}/{name}".lstrip("/")

    _, abs_path = _local_upload_safe_folder(rel)

    if os.path.exists(abs_path):

        raise HTTPException(status_code=400, detail="同名文件夹已存在")

    os.makedirs(abs_path, exist_ok=False)

    tree, items = _local_upload_tree_and_items()

    return {"ok": True, "folder": {"path": rel, "name": name}, "tree": tree, "items": items}



@router.patch("/api/local-assets/folders")

async def rename_local_asset_folder(payload: LocalAssetFolderRequest, request: Request):

    ensure_same_origin_request(request)

    rel, abs_path = _local_upload_safe_folder(payload.path)

    if not rel:

        raise HTTPException(status_code=400, detail="根目录不能重命名")

    if not os.path.isdir(abs_path):

        raise HTTPException(status_code=404, detail="文件夹不存在")

    name = _local_upload_safe_folder_name(payload.name)

    parent = os.path.dirname(rel).replace("\\", "/")

    new_rel = f"{parent}/{name}".lstrip("/")

    _, new_abs = _local_upload_safe_folder(new_rel)

    if os.path.exists(new_abs):

        raise HTTPException(status_code=400, detail="同名文件夹已存在")

    os.rename(abs_path, new_abs)

    tree, items = _local_upload_tree_and_items()

    return {"ok": True, "folder": {"path": new_rel, "name": name}, "tree": tree, "items": items}



@router.patch("/api/local-assets/items")

async def rename_local_asset_item(payload: LocalAssetRenameRequest, request: Request):

    ensure_same_origin_request(request)

    rel, abs_path = _local_upload_safe_path(payload.path)

    if not os.path.isfile(abs_path):

        raise HTTPException(status_code=404, detail="本地素材不存在")

    kind, ext = _local_upload_kind_ext(rel, "")

    if kind is None:

        raise HTTPException(status_code=400, detail="不支持的素材类型")

    new_stem = _local_upload_safe_file_stem(payload.name)

    old_ext = os.path.splitext(rel)[1] or ext

    parent = os.path.dirname(rel).replace("\\", "/")

    new_rel = f"{parent}/{new_stem}{old_ext}".lstrip("/")

    if new_rel == rel:

        tree, items = _local_upload_tree_and_items()

        return {"ok": True, "item": _local_upload_item(rel), "tree": tree, "items": items}

    _, new_abs = _local_upload_abs(new_rel)

    if os.path.exists(new_abs):

        raise HTTPException(status_code=400, detail="同名素材已存在")

    os.rename(abs_path, new_abs)

    old_caption = _local_upload_caption_path(rel)

    new_caption = _local_upload_caption_path(new_rel)

    if os.path.isfile(old_caption) and not os.path.exists(new_caption):

        os.rename(old_caption, new_caption)

    old_classification = _local_upload_classification_path(rel)

    new_classification = _local_upload_classification_path(new_rel)

    if os.path.isfile(old_classification) and not os.path.exists(new_classification):

        os.rename(old_classification, new_classification)

    tree, items = _local_upload_tree_and_items()

    return {"ok": True, "item": _local_upload_item(new_rel), "old_path": rel, "tree": tree, "items": items}



@router.post("/api/local-assets/delete")

async def delete_local_assets(payload: dict, request: Request):

    ensure_same_origin_request(request)

    names = payload.get("names") if isinstance(payload, dict) else None

    if not isinstance(names, list):

        names = []

    deleted = []

    for name in names:

        try:

            rel, path = _local_upload_safe_path(name)

        except HTTPException:

            continue

        if os.path.isfile(path):

            try:

                os.remove(path)

                txt_path = _local_upload_caption_path(rel)

                if os.path.isfile(txt_path):

                    os.remove(txt_path)

                cls_path = _local_upload_classification_path(rel)

                if os.path.isfile(cls_path):

                    os.remove(cls_path)

                deleted.append(rel)

            except OSError:

                pass

    return {"deleted": deleted}



@router.post("/api/local-assets/move")

async def move_local_assets(payload: dict, request: Request):

    """把选中的本地素材移动到目标文件夹（folder 为空表示根目录）；连同 .txt / .classification.json 兄弟文件一起搬。"""

    ensure_same_origin_request(request)

    names = payload.get("names") if isinstance(payload, dict) else None

    if not isinstance(names, list) or not names:

        raise HTTPException(status_code=400, detail="没有选择素材")

    folder_value = str(payload.get("folder") or "").strip() if isinstance(payload, dict) else ""

    target_rel, target_abs = _local_upload_safe_folder(folder_value)

    if target_rel and not os.path.isdir(target_abs):

        raise HTTPException(status_code=404, detail="目标文件夹不存在")

    moved = 0

    for name in names:

        try:

            rel, abs_path = _local_upload_safe_path(name)

        except HTTPException:

            continue

        if not os.path.isfile(abs_path):

            continue

        base = os.path.basename(rel)

        new_rel = f"{target_rel}/{base}".lstrip("/") if target_rel else base

        if new_rel == rel:

            continue  # 已在目标文件夹，跳过

        _, new_abs = _local_upload_abs(new_rel)

        if os.path.exists(new_abs):

            # 同名冲突：加短随机后缀，避免覆盖已有文件

            stem, ext = os.path.splitext(base)

            base = f"{stem}_{uuid.uuid4().hex[:6]}{ext}"

            new_rel = f"{target_rel}/{base}".lstrip("/") if target_rel else base

            _, new_abs = _local_upload_abs(new_rel)

        try:

            os.makedirs(os.path.dirname(new_abs), exist_ok=True)

            os.rename(abs_path, new_abs)

            for src_sib, dst_sib in (

                (_local_upload_caption_path(rel), _local_upload_caption_path(new_rel)),

                (_local_upload_classification_path(rel), _local_upload_classification_path(new_rel)),

            ):

                if os.path.isfile(src_sib) and not os.path.exists(dst_sib):

                    os.rename(src_sib, dst_sib)

            moved += 1

        except OSError:

            continue

    tree, items = _local_upload_tree_and_items()

    return {"ok": True, "moved": moved, "items": items, "tree": tree}



@router.post("/api/local-assets/caption")

async def caption_local_assets(payload: LocalAssetCaptionRequest):

    prompt = (payload.prompt or "描述图片").strip() or "描述图片"

    items = []

    ok_count = 0

    for name in (payload.names or [])[:100]:

        item = {"name": name, "ok": False, "caption": "", "caption_file": "", "error": ""}

        try:

            filename, path = _local_upload_safe_path(name)

            if not os.path.isfile(path):

                raise HTTPException(status_code=404, detail="文件不存在")

            kind, _ = _local_upload_kind_ext(filename, "")

            if kind != "image":

                raise HTTPException(status_code=400, detail="仅支持图片素材反推提示词")

            caption, resolved_model = await caption_image_with_provider(

                path,

                prompt,

                payload.provider,

                payload.model,

                payload.ms_model,

            )

            txt_path = _local_upload_caption_path(filename)

            with open(txt_path, "w", encoding="utf-8", newline="") as f:

                f.write(caption)

            item.update({

                "ok": True,

                "name": filename,

                "caption": caption,

                "caption_file": os.path.basename(txt_path),

                "model": resolved_model,

            })

            ok_count += 1

        except HTTPException as exc:

            item["error"] = str(exc.detail or "反推失败")

        except Exception as exc:

            item["error"] = str(exc) or "反推失败"

        items.append(item)

    return {"ok": True, "count": ok_count, "items": items}



@router.post("/api/local-assets/classify")

async def classify_local_assets(payload: LocalAssetClassifyRequest):

    items = []

    ok_count = 0

    for name in (payload.names or [])[:80]:

        item = {"name": name, "ok": False, "classification": None, "classification_file": "", "error": ""}

        try:

            filename, path = _local_upload_safe_path(name)

            if not os.path.isfile(path):

                raise HTTPException(status_code=404, detail="文件不存在")

            kind, _ = _local_upload_kind_ext(filename, "")

            if kind != "image":

                raise HTTPException(status_code=400, detail="仅支持图片素材智能分类")

            classification = await classify_image_with_provider(

                path,

                payload.provider,

                payload.model,

                payload.ms_model,

                payload.prompt,

            )

            _write_local_upload_classification(filename, classification)

            item.update({

                "ok": True,

                "name": filename,

                "classification": classification,

                "classification_file": os.path.basename(_local_upload_classification_path(filename)),

                "model": classification.get("model") or "",

            })

            ok_count += 1

        except HTTPException as exc:

            item["error"] = str(exc.detail or "智能分类失败")

        except Exception as exc:

            item["error"] = str(exc) or "智能分类失败"

        items.append(item)

    return {"ok": True, "count": ok_count, "items": items}



@router.patch("/api/local-assets/caption")

async def save_local_asset_caption(payload: LocalAssetCaptionSaveRequest):

    filename, path = _local_upload_safe_path(payload.name)

    if not os.path.isfile(path):

        raise HTTPException(status_code=404, detail="文件不存在")

    kind, _ = _local_upload_kind_ext(filename, "")

    if kind != "image":

        raise HTTPException(status_code=400, detail="仅支持图片素材保存提示词")

    caption = str(payload.caption or "")[:100000]

    txt_path = _local_upload_caption_path(filename)

    with open(txt_path, "w", encoding="utf-8", newline="") as f:

        f.write(caption)

    return {"ok": True, "caption": caption, "caption_file": os.path.basename(txt_path)}



@router.post("/api/temp-sh/upload")

async def temp_sh_upload(payload: TempShUploadRequest, request: Request):

    ensure_same_origin_request(request)

    return await upload_local_video_to_cloud(payload.url, "auto")



@router.post("/api/cloud-video/upload")

async def cloud_video_upload(payload: CloudVideoUploadRequest, request: Request):

    ensure_same_origin_request(request)

    return await upload_local_video_to_cloud(payload.url, payload.service)



@router.post("/api/ai/import-local-image")

async def import_local_ai_reference(payload: LocalImageImportRequest, request: Request):

    ensure_same_origin_request(request)

    requested = [payload.path] if payload.path else []

    requested.extend(payload.paths or [])

    requested = [p for p in requested if str(p or "").strip()][:20]

    if not requested:

        raise HTTPException(status_code=400, detail="没有可导入的本地图片")

    return {"files": [import_local_image_file(normalize_local_image_path(path)) for path in requested]}



@router.get("/api/runninghub/app-info")

async def runninghub_app_info(webappId: str = ""):

    webapp_id = str(webappId or "").strip()

    if not webapp_id:

        raise HTTPException(status_code=400, detail="webappId 必填")

    provider = runninghub_provider()

    api_key = runninghub_api_key(provider)

    url = runninghub_endpoint_url(provider, f"/api/webapp/apiCallDemo?apiKey={urllib.parse.quote(api_key)}&webappId={urllib.parse.quote(webapp_id)}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)) as client:

        try:

            response = await client.get(url, headers=runninghub_app_headers(False))

            raw = response.json()

        except httpx.HTTPStatusError as exc:

            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text[:500]) from exc

        except Exception as exc:

            raise HTTPException(status_code=502, detail=f"请求 RunningHub 应用信息失败：{exc}") from exc

    if response.status_code >= 400:

        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:500])

    if isinstance(raw, dict) and raw.get("code") not in (0, "0", None):

        raise HTTPException(status_code=400, detail=raw.get("msg") or f"RunningHub 查询失败 code={raw.get('code')}")

    data = raw.get("data") if isinstance(raw, dict) else {}

    return {"success": True, "data": data or {}}



@router.post("/api/runninghub/submit")

async def runninghub_submit(payload: RunningHubSubmitRequest):

    webapp_id = str(payload.webappId or "").strip()

    if not webapp_id:

        raise HTTPException(status_code=400, detail="webappId 必填")

    provider = runninghub_provider()

    api_key = runninghub_api_key(provider, use_wallet=payload.useWallet)

    body = {

        "apiKey": api_key,

        "webappId": webapp_id,

        "nodeInfoList": sanitize_runninghub_node_info_list(payload.nodeInfoList or []),

    }

    instance_type = str(payload.instanceType or "").strip()

    if instance_type:

        body["instanceType"] = instance_type

    url = runninghub_endpoint_url(provider, "/task/openapi/ai-app/run")

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=180.0, write=120.0, pool=20.0)) as client:

        try:

            response = await client.post(url, headers=runninghub_app_headers(True, payload.useWallet), json=body)

            raw = response.json()

        except Exception as exc:

            raise HTTPException(status_code=502, detail=runninghub_error_detail(f"提交 RunningHub 任务失败：{exc}", endpoint=url, webappId=webapp_id)) from exc

    if response.status_code >= 400:

        log_runninghub_error("submit-http", raw, endpoint=url, webappId=webapp_id, status=response.status_code)

        raise HTTPException(status_code=response.status_code, detail=runninghub_error_detail(f"RunningHub HTTP {response.status_code}", raw, endpoint=url, webappId=webapp_id))

    if isinstance(raw, dict) and raw.get("code") in (0, "0"):

        task_id = raw.get("data", {}).get("taskId") if isinstance(raw.get("data"), dict) else ""

        if not task_id:

            raise HTTPException(status_code=502, detail=runninghub_error_detail("RunningHub 未返回 taskId", raw, endpoint=url, webappId=webapp_id))

        return {"success": True, "data": {"taskId": task_id, "raw": raw}}

    log_runninghub_error("submit-rejected", raw, endpoint=url, webappId=webapp_id)

    raise HTTPException(status_code=400, detail=runninghub_error_detail(runninghub_fail_reason(raw) or "RunningHub 提交失败", raw, endpoint=url, webappId=webapp_id))



@router.post("/api/runninghub/workflow-submit")

async def runninghub_workflow_submit(payload: RunningHubWorkflowSubmitRequest):

    workflow_id = str(payload.workflowId or "").strip()

    if not workflow_id:

        raise HTTPException(status_code=400, detail="workflowId 必填")

    provider = runninghub_provider()

    api_key = runninghub_api_key(provider, use_wallet=payload.useWallet)

    body = {

        "apiKey": api_key,

        "workflowId": workflow_id,

        "addMetadata": True,

    }

    if payload.nodeInfoList:

        body["nodeInfoList"] = sanitize_runninghub_node_info_list(payload.nodeInfoList)

    workflow_payload = payload.workflow

    if workflow_payload:

        if isinstance(workflow_payload, (dict, list)):

            body["workflow"] = json.dumps(sanitize_seed_like_workflow_values(workflow_payload), ensure_ascii=False)

        else:

            body["workflow"] = str(workflow_payload)

    url = runninghub_endpoint_url(provider, "/task/openapi/create")

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=180.0, write=120.0, pool=20.0)) as client:

        try:

            response = await client.post(url, headers=runninghub_app_headers(True, payload.useWallet), json=body)

            raw = response.json()

        except Exception as exc:

            raise HTTPException(status_code=502, detail=runninghub_error_detail(f"提交 RunningHub 工作流失败：{exc}", endpoint=url, workflowId=workflow_id)) from exc

    if response.status_code >= 400:

        log_runninghub_error("workflow-submit-http", raw, endpoint=url, workflowId=workflow_id, status=response.status_code)

        raise HTTPException(status_code=response.status_code, detail=runninghub_error_detail(f"RunningHub HTTP {response.status_code}", raw, endpoint=url, workflowId=workflow_id))

    if isinstance(raw, dict) and raw.get("code") in (0, "0"):

        task_id = raw.get("data", {}).get("taskId") if isinstance(raw.get("data"), dict) else ""

        if not task_id:

            raise HTTPException(status_code=502, detail=runninghub_error_detail("RunningHub 工作流未返回 taskId", raw, endpoint=url, workflowId=workflow_id))

        return {"success": True, "data": {"taskId": task_id, "raw": raw}}

    log_runninghub_error("workflow-submit-rejected", raw, endpoint=url, workflowId=workflow_id)

    raise HTTPException(status_code=400, detail=runninghub_error_detail(runninghub_fail_reason(raw) or "RunningHub 工作流提交失败", raw, endpoint=url, workflowId=workflow_id))



@router.get("/api/runninghub/workflow-info")

async def runninghub_workflow_info(workflowId: str = ""):

    workflow_id = str(workflowId or "").strip()

    if not workflow_id:

        raise HTTPException(status_code=400, detail="workflowId 必填")

    provider = runninghub_provider()

    api_key = runninghub_api_key(provider)

    url = runninghub_endpoint_url(provider, "/api/openapi/getJsonApiFormat")

    body = {"apiKey": api_key, "workflowId": workflow_id}

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=180.0, write=60.0, pool=20.0)) as client:

        try:

            response = await client.post(url, headers=runninghub_app_headers(True), json=body)

            raw = response.json()

        except Exception as exc:

            raise HTTPException(status_code=502, detail=f"拉取 RunningHub 工作流参数失败：{exc}") from exc

    if response.status_code >= 400:

        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])

    if not isinstance(raw, dict) or raw.get("code") not in (0, "0"):

        raise HTTPException(status_code=400, detail=(raw.get("msg") if isinstance(raw, dict) else "") or f"RunningHub 工作流参数拉取失败：{raw}")

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

    prompt = data.get("prompt")

    workflow_json = {}

    if isinstance(prompt, str) and prompt.strip():

        try:

            workflow_json = json.loads(prompt)

        except Exception as exc:

            raise HTTPException(status_code=502, detail=f"RunningHub 工作流 JSON 解析失败：{exc}") from exc

    elif isinstance(prompt, dict):

        workflow_json = prompt

    node_info_list = runninghub_workflow_node_info_list(workflow_json)

    return {"success": True, "data": {"workflowId": workflow_id, "nodeInfoList": node_info_list, "raw": raw}}



@router.get("/api/runninghub/workflows")

def list_runninghub_workflows():

    providers = load_api_providers()

    hidden_ids = runninghub_saved_hidden_workflow_ids()

    for provider in providers:

        if provider.get("id") != "runninghub":

            continue

        for entry in provider.get("rh_workflows") or []:

            workflow_id = runninghub_workflow_store_key(entry.get("workflowId") or entry.get("id"))

            if workflow_id and entry.get("hidden") is True:

                hidden_ids.add(workflow_id)

    with RUNNINGHUB_WORKFLOW_LOCK:

        store = load_runninghub_workflow_store()

    merged = {workflow_id: cfg for workflow_id, cfg in store.items() if isinstance(cfg, dict) and workflow_id not in hidden_ids}

    for provider in providers:

        if provider.get("id") != "runninghub":

            continue

        for entry in provider.get("rh_workflows") or []:

            workflow_id = runninghub_workflow_store_key(entry.get("workflowId") or entry.get("id"))

            if not workflow_id:

                continue

            if entry.get("hidden") is True:

                merged.pop(workflow_id, None)

                continue

            provider_cfg = runninghub_provider_workflow_config(workflow_id)

            if provider_cfg:

                merged[workflow_id] = runninghub_select_workflow_config(merged.get(workflow_id), provider_cfg, workflow_id)

    items = []

    for workflow_id, cfg in merged.items():

        if not isinstance(cfg, dict):

            continue

        items.append({

            "workflowId": workflow_id,

            "title": cfg.get("title") or workflow_id,

            "fieldCount": len(cfg.get("fields") or []),

            "updatedAt": cfg.get("updatedAt"),

            "description": cfg.get("description") or "",

        })

    items.sort(key=lambda item: item["title"])

    return {"workflows": items}



@router.get("/api/runninghub/workflows/{workflow_id:path}")

def get_runninghub_workflow(workflow_id: str):

    key = runninghub_workflow_store_key(workflow_id)

    if not key:

        raise HTTPException(status_code=400, detail="workflowId 必填")

    with RUNNINGHUB_WORKFLOW_LOCK:

        store = load_runninghub_workflow_store()

    cfg = store.get(key)

    provider_cfg = runninghub_provider_workflow_config(key)

    cfg = runninghub_select_workflow_config(cfg, provider_cfg, key)

    if not isinstance(cfg, dict):

        raise HTTPException(status_code=404, detail="RunningHub 工作流未找到")

    return {"workflow": cfg}



@router.post("/api/runninghub/workflows/fetch")

async def fetch_runninghub_workflow(payload: RunningHubWorkflowConfig):

    workflow_id = runninghub_workflow_store_key(payload.workflowId)

    if not workflow_id:

        raise HTTPException(status_code=400, detail="workflowId 必填")

    provider = runninghub_provider()

    api_key = runninghub_api_key(provider)

    url = runninghub_endpoint_url(provider, "/api/openapi/getJsonApiFormat")

    body = {"apiKey": api_key, "workflowId": workflow_id}

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=180.0, write=60.0, pool=20.0)) as client:

        try:

            response = await client.post(url, headers=runninghub_app_headers(True), json=body)

            raw = response.json()

        except Exception as exc:

            raise HTTPException(status_code=502, detail=f"Failed to fetch RunningHub workflow parameters: {exc}") from exc

    if response.status_code >= 400:

        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])

    if not isinstance(raw, dict) or raw.get("code") not in (0, "0"):

        raise HTTPException(status_code=400, detail=(raw.get("msg") if isinstance(raw, dict) else "") or f"RunningHub workflow fetch failed: {raw}")

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

    prompt = data.get("prompt")

    workflow_json = {}

    if isinstance(prompt, str) and prompt.strip():

        try:

            workflow_json = json.loads(prompt)

        except Exception as exc:

            raise HTTPException(status_code=502, detail=f"Failed to parse RunningHub workflow JSON: {exc}") from exc

    elif isinstance(prompt, dict):

        workflow_json = prompt

    fields = runninghub_collect_workflow_fields(workflow_json)

    return {"success": True, "data": {"workflowId": workflow_id, "title": payload.title or workflow_id, "description": payload.description or "", "fields": fields, "workflowJson": workflow_json, "raw": raw}}



@router.put("/api/runninghub/workflows/{workflow_id:path}")

def save_runninghub_workflow(workflow_id: str, payload: RunningHubWorkflowConfig):

    key = runninghub_workflow_store_key(workflow_id)

    if not key:

        raise HTTPException(status_code=400, detail="workflowId 必填")

    fields = [

        field for field in (runninghub_normalize_field(item) for item in (payload.fields or []))

        if not runninghub_is_saved_link_field(field)

    ]

    cfg = {

        "workflowId": key,

        "title": (payload.title or key).strip() or key,

        "description": payload.description or "",

        "fields": fields,

        "workflowJson": payload.workflowJson or {},

        "optionalImageMode": payload.optionalImageMode or "prune-workflow",

        "raw": payload.raw or {},

        "updatedAt": now_ms(),

    }

    with RUNNINGHUB_WORKFLOW_LOCK:

        store = load_runninghub_workflow_store()

        store[key] = cfg

        save_runninghub_workflow_store(store)

    sync_runninghub_workflow_to_provider(cfg)

    return {"success": True, "workflow": cfg}



@router.delete("/api/runninghub/workflows/{workflow_id:path}")

def delete_runninghub_workflow(workflow_id: str):

    key = runninghub_workflow_store_key(workflow_id)

    if not key:

        raise HTTPException(status_code=400, detail="workflowId 必填")

    with RUNNINGHUB_WORKFLOW_LOCK:

        store = load_runninghub_workflow_store()

        provider_cfg = runninghub_provider_workflow_config(key)

        if key not in store and not provider_cfg:

            raise HTTPException(status_code=404, detail="RunningHub 工作流未找到")

        store.pop(key, None)

        save_runninghub_workflow_store(store)

    remove_runninghub_workflow_from_provider(key)

    return {"success": True}



@router.get("/api/runninghub/query")

async def runninghub_query(taskId: str = "", useWallet: bool = False):

    task_id = str(taskId or "").strip()

    if not task_id:

        raise HTTPException(status_code=400, detail="taskId 必填")

    provider = runninghub_provider()

    api_key = runninghub_api_key(provider, use_wallet=useWallet)

    url = runninghub_endpoint_url(provider, "/task/openapi/outputs")

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=20.0)) as client:

        try:

            response = await client.post(url, headers=runninghub_app_headers(True, useWallet), json={"apiKey": api_key, "taskId": task_id})

            raw = response.json()

        except Exception as exc:

            raise HTTPException(status_code=502, detail=runninghub_error_detail(f"查询 RunningHub 任务失败：{exc}", endpoint=url, taskId=task_id)) from exc

        if response.status_code >= 400:

            log_runninghub_error("query-http", raw, endpoint=url, taskId=task_id, status=response.status_code)

            raise HTTPException(status_code=response.status_code, detail=runninghub_error_detail(f"RunningHub HTTP {response.status_code}", raw, endpoint=url, taskId=task_id))

        code = raw.get("code") if isinstance(raw, dict) else None

        status = "PENDING"

        urls = []

        image_items = []

        if code in (0, "0"):

            status = "SUCCESS"

            for remote in runninghub_extract_outputs(raw.get("data")):

                try:

                    local_url = await runninghub_store_remote_output(client, remote)

                except Exception:

                    local_url = remote

                urls.append(local_url)

                image_items.append(image_output_meta(local_url))

        elif code in (804, "804"):

            status = "RUNNING"

        elif code in (813, "813"):

            status = "QUEUED"

        elif code in (805, "805"):

            status = "FAILED"

            log_runninghub_error("query-failed", raw, endpoint=url, taskId=task_id, code=code)

        else:

            status = "UNKNOWN"

            log_runninghub_error("query-unknown", raw, endpoint=url, taskId=task_id, code=code)

        return {"success": True, "data": {"status": status, "urls": urls, "image_items": image_items, "failReason": runninghub_fail_reason(raw), "code": code, "raw": raw}}



@router.post("/api/runninghub/upload-asset")

async def runninghub_upload_asset(payload: RunningHubUploadAssetRequest):

    source_url = rewrite_runninghub_file_url(str(payload.url or "").strip())

    if not source_url:

        raise HTTPException(status_code=400, detail="url 必填")

    provider = runninghub_provider()

    api_key = runninghub_api_key(provider, use_wallet=payload.useWallet)

    filename = "asset.bin"

    content_type = "application/octet-stream"

    content = b""

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=240.0, pool=20.0), follow_redirects=True) as client:

        path = runninghub_local_asset_path(source_url)

        if path:

            filename = os.path.basename(path)

            content_type = content_type_for_path(path)

            with open(path, "rb") as f:

                content = f.read()

        elif source_url.startswith(("http://", "https://")):

            response = await client.get(source_url)

            if not response.is_success:

                raise HTTPException(status_code=400, detail=f"下载素材失败 HTTP {response.status_code}")

            content = response.content

            content_type = response.headers.get("content-type") or content_type

            filename = os.path.basename(urllib.parse.urlsplit(source_url).path) or filename

        else:

            raise HTTPException(status_code=400, detail=f"不支持的素材地址：{source_url}")

        if not content:

            raise HTTPException(status_code=400, detail="素材为空，无法上传到 RunningHub")

        upload_url = runninghub_endpoint_url(provider, "/task/openapi/upload")

        files = {"file": (filename, content, content_type)}

        data = {"apiKey": api_key, "fileType": "input"}

        try:

            response = await client.post(upload_url, headers=runninghub_app_headers(False, payload.useWallet), data=data, files=files)

            raw = response.json()

        except Exception as exc:

            raise HTTPException(status_code=502, detail=f"上传素材到 RunningHub 失败：{exc}") from exc

    if response.status_code >= 400:

        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])

    if isinstance(raw, dict) and raw.get("code") in (0, "0") and isinstance(raw.get("data"), dict) and raw["data"].get("fileName"):

        return {"success": True, "data": {"fileName": raw["data"]["fileName"], "fileType": raw["data"].get("fileType") or content_type}}

    raise HTTPException(status_code=400, detail=(raw.get("msg") if isinstance(raw, dict) else "") or f"RunningHub 上传失败：{raw}")



@router.get("/api/codex/status")

async def codex_status():

    exe = codex_cli_executable()

    image2_exe = gpt_image_2_skill_executable()

    if not exe:

        return {

            "installed": False,

            "logged_in": False,

            "image2_helper_installed": bool(image2_exe),

            "image2_helper_path": image2_exe,

            "message": "未找到 OpenAI Codex CLI，请先安装。",

        }

    try:

        proc = await asyncio.create_subprocess_exec(

            exe,

            "--version",

            cwd=BASE_DIR,

            stdout=asyncio.subprocess.PIPE,

            stderr=asyncio.subprocess.PIPE,

        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

        out_text, err_text = codex_decode_output(stdout, stderr)

        ok = proc.returncode == 0

        helper_message = "GPT Image 2 helper 已安装，OpenAI CLI 生图会使用 GPT Image 2。" if image2_exe else "未找到 GPT Image 2 helper，OpenAI CLI 生图不可用；已禁用 Codex 内置 $imagegen 回退。"

        return {

            "installed": ok,

            "logged_in": None,

            "version": out_text or err_text,

            "path": exe,

            "image2_helper_installed": bool(image2_exe),

            "image2_helper_path": image2_exe,

            "message": f"OpenAI Codex CLI 已安装。{helper_message} 登录状态会在首次执行 codex exec 时由 CLI 校验。" if ok else (err_text or out_text or "Codex CLI 检测失败"),

            "raw": {"stdout": out_text, "stderr": err_text, "returncode": proc.returncode},

        }

    except Exception as exc:

        return {

            "installed": False,

            "logged_in": False,

            "path": exe,

            "image2_helper_installed": bool(image2_exe),

            "image2_helper_path": image2_exe,

            "message": f"Codex CLI 检测失败：{exc}",

        }



@router.post("/api/codex/help")

async def codex_help(payload: CodexHelpRequest):

    exe = codex_cli_executable()

    if not exe:

        raise HTTPException(status_code=400, detail="未找到 OpenAI Codex CLI。")

    allowed = {"", "exec", "login", "logout", "doctor", "mcp", "app", "update"}

    command = str(payload.command or "").strip()

    if command not in allowed:

        raise HTTPException(status_code=400, detail="不允许的 Codex CLI 命令")

    args = [exe]

    if command:

        args.append(command)

    args.append("--help")

    proc = await asyncio.create_subprocess_exec(

        *args,

        cwd=BASE_DIR,

        stdout=asyncio.subprocess.PIPE,

        stderr=asyncio.subprocess.PIPE,

    )

    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)

    out_text, err_text = codex_decode_output(stdout, stderr)

    if proc.returncode != 0:

        raise HTTPException(status_code=502, detail=(err_text or out_text or f"exit={proc.returncode}")[:1000])

    return {"text": out_text or err_text, "raw": {"stdout": out_text, "stderr": err_text}}



@router.get("/api/gemini-cli/status")

async def gemini_cli_status():

    exe = gemini_cli_executable()

    display_name = gemini_cli_display_name(exe)

    if not exe:

        return {

            "installed": False,

            "logged_in": False,

            "provider": "antigravity",

            "message": "未找到 Antigravity CLI，请先安装。",

        }

    try:

        proc = await asyncio.create_subprocess_exec(

            exe,

            "--version",

            cwd=BASE_DIR,

            stdout=asyncio.subprocess.PIPE,

            stderr=asyncio.subprocess.PIPE,

        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

        out_text, err_text = codex_decode_output(stdout, stderr)

        ok = proc.returncode == 0

        is_agy = is_antigravity_cli(exe)

        return {

            "installed": ok,

            "logged_in": None,

            "version": out_text or err_text,

            "path": exe,

            "provider": "antigravity" if is_agy else "gemini",

            "message": f"{display_name} 已安装。登录状态会在首次执行 {'agy' if is_agy else 'gemini'} 时由 CLI 校验。" if ok else (err_text or out_text or f"{display_name} 检测失败"),

            "raw": {"stdout": out_text, "stderr": err_text, "returncode": proc.returncode},

        }

    except Exception as exc:

        return {

            "installed": False,

            "logged_in": False,

            "path": exe,

            "provider": "antigravity" if is_antigravity_cli(exe) else "gemini",

            "message": f"{display_name} 检测失败：{exc}",

        }



@router.post("/api/gemini-cli/help")

async def gemini_cli_help(payload: GeminiCliHelpRequest):

    exe = gemini_cli_executable()

    if not exe:

        raise HTTPException(status_code=400, detail="未找到 Antigravity CLI。")

    is_agy = is_antigravity_cli(exe)

    allowed = {"", "help", "install", "models", "plugin", "plugins", "update", "changelog"} if is_agy else {"", "help", "mcp", "extensions"}

    command = str(payload.command or "").strip()

    if command not in allowed:

        raise HTTPException(status_code=400, detail=f"不允许的 {gemini_cli_display_name(exe)} 命令")

    args = [exe]

    if command:

        args.append(command)

    args.append("--help")

    proc = await asyncio.create_subprocess_exec(

        *args,

        cwd=BASE_DIR,

        stdout=asyncio.subprocess.PIPE,

        stderr=asyncio.subprocess.PIPE,

    )

    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)

    out_text, err_text = codex_decode_output(stdout, stderr)

    if proc.returncode != 0:

        raise HTTPException(status_code=502, detail=(err_text or out_text or f"exit={proc.returncode}")[:1000])

    return {"text": out_text or err_text, "raw": {"stdout": out_text, "stderr": err_text}}



@router.get("/api/jimeng/status")

async def jimeng_status():

    exe = jimeng_cli_executable()

    if not exe:

        return {"installed": False, "logged_in": False, "message": "未找到 dreamina CLI"}

    version, version_text = await jimeng_cli_version()

    version_str = ".".join(str(part) for part in version) if version else None

    version_ok = version >= JIMENG_MIN_CLI_VERSION if version else None

    min_version_str = ".".join(str(part) for part in JIMENG_MIN_CLI_VERSION)

    try:

        raw = await run_jimeng_cli(["user_credit"], timeout=30)

        return {

            "installed": True,

            "logged_in": True,

            "raw": raw,

            "cli_version": version_str,

            "version_ok": version_ok,

            "min_version": min_version_str,

        }

    except HTTPException as exc:

        return {

            "installed": True,

            "logged_in": False,

            "message": str(exc.detail),

            "cli_version": version_str,

            "version_ok": version_ok,

            "min_version": min_version_str,

        }



@router.get("/api/jimeng/credit")

async def jimeng_credit():

    raw = await run_jimeng_cli(["user_credit"], timeout=30)

    return {"success": True, "raw": raw}



@router.post("/api/jimeng/logout")

async def jimeng_logout():

    raw = await run_jimeng_cli(["logout"], timeout=30)

    return {"success": True, "raw": raw}



@router.post("/api/jimeng/login/start")

async def jimeng_login_start():

    old_proc = JIMENG_LOGIN_SESSION.get("proc")

    if old_proc and getattr(old_proc, "returncode", None) is None:

        try:

            old_proc.terminate()

        except Exception:

            pass

    exe = jimeng_cli_executable()

    if not exe:

        raise HTTPException(status_code=400, detail="未找到 dreamina CLI")

    JIMENG_LOGIN_SESSION.update({"proc": None, "stdout": "", "stderr": "", "started_at": time.time()})

    args = ["login", "--headless"]

    command = jimeng_command(args, exe)

    try:

        proc = await asyncio.create_subprocess_exec(

            *command,

            cwd=BASE_DIR,

            stdout=asyncio.subprocess.PIPE,

            stderr=asyncio.subprocess.PIPE,

        )

    except FileNotFoundError as exc:

        raise HTTPException(status_code=400, detail=f"未找到即梦 CLI：{exe}") from exc

    JIMENG_LOGIN_SESSION["proc"] = proc

    asyncio.create_task(jimeng_login_reader(proc))

    await asyncio.sleep(2)

    text = jimeng_login_text()

    if proc.returncode not in (None, 0) and ("unknown" in text.lower() or "no such option" in text.lower()):

        # 旧版 CLI 可能没有 --headless，退回 debug 输出。

        JIMENG_LOGIN_SESSION.update({"proc": None, "stdout": "", "stderr": "", "started_at": time.time()})

        proc = await asyncio.create_subprocess_exec(

            *jimeng_command(["login", "--debug"], exe),

            cwd=BASE_DIR,

            stdout=asyncio.subprocess.PIPE,

            stderr=asyncio.subprocess.PIPE,

        )

        JIMENG_LOGIN_SESSION["proc"] = proc

        asyncio.create_task(jimeng_login_reader(proc))

        await asyncio.sleep(2)

        text = jimeng_login_text()

    return {

        "success": True,

        "running": JIMENG_LOGIN_SESSION.get("proc") is not None and JIMENG_LOGIN_SESSION["proc"].returncode is None,

        "text": text,

        "qr_url": jimeng_login_qr_from_text(text),

        "started_at": JIMENG_LOGIN_SESSION.get("started_at") or 0,

    }



@router.get("/api/jimeng/login/status")

async def jimeng_login_status():

    proc = JIMENG_LOGIN_SESSION.get("proc")

    text = jimeng_login_text()

    running = proc is not None and getattr(proc, "returncode", None) is None

    logged_in = False

    credit_raw = None

    if not running:

        try:

            credit_raw = await run_jimeng_cli(["user_credit"], timeout=20)

            logged_in = True

        except HTTPException:

            logged_in = False

    return {

        "success": True,

        "running": running,

        "logged_in": logged_in,

        "text": text,

        "qr_url": jimeng_login_qr_from_text(text),

        "raw": credit_raw,

    }



@router.post("/api/jimeng/help")

async def jimeng_help(payload: JimengHelpRequest):

    command = str(payload.command or "").strip()

    allowed = {"", "login", "logout", "user_credit", "text2image", "image2image", "image_upscale", "text2video", "image2video", "multimodal2video", "frames2video", "multiframe2video", "list_task", "query_result"}

    if command not in allowed:

        raise HTTPException(status_code=400, detail="不支持的帮助命令")

    args = [command, "-h"] if command else ["-h"]

    raw = await run_jimeng_cli(args, timeout=30, raw_text=True)

    text = raw.get("_stdout") or ""

    if raw.get("_stderr"):

        text = f"{text}\n{raw.get('_stderr')}".strip()

    return {"success": True, "command": command, "text": text, "raw": raw}



@router.post("/api/jimeng/query-media")

async def jimeng_query_media(payload: JimengQueryMediaRequest):

    """按 submit_id 续查即梦任务：出图返回 succeeded+urls；仍排队返回 pending+queue_info；失败返回 failed。

    供画布「排队中」卡片自动轮询与手动查询复用。"""

    submit_id = str(payload.submit_id or "").strip()

    if not submit_id:

        raise HTTPException(status_code=400, detail="缺少 submit_id")

    kind = str(payload.kind or "image").strip().lower()

    if kind not in ("image", "video", "audio"):

        kind = "image"

    queried = await jimeng_query_result(submit_id, kind)

    try:

        urls = await jimeng_store_outputs(queried, kind, allow_query=False)

        return {"status": "succeeded", "submit_id": submit_id, "kind": kind, "urls": urls}

    except JimengPendingError as exc:

        return {"status": "pending", "submit_id": submit_id, "kind": kind, "queue_info": exc.queue_info, "message": jimeng_pending_payload(exc)["message"]}

    except HTTPException as exc:

        return {"status": "failed", "submit_id": submit_id, "kind": kind, "error": str(getattr(exc, "detail", "") or exc)}



@router.get("/api/config")

async def ai_config():

    preferred_chat_model = next((m for m in core.CHAT_MODELS if m == "gpt-5.5"), core.CHAT_MODELS[0] if core.CHAT_MODELS else CHAT_MODEL)

    providers = public_api_providers()

    return {

        "base_url": core.AI_BASE_URL,

        "chat_model": preferred_chat_model,

        "image_model": IMAGE_MODEL,

        "chat_models": core.CHAT_MODELS,

        "image_models": core.IMAGE_MODELS,

        "video_models": core.VIDEO_MODELS,

        "comfy_instances": core.COMFYUI_INSTANCES,

        "api_providers": providers,

        "has_api_key": bool(core.AI_API_KEY),

        "ms_chat_models": core.MODELSCOPE_CHAT_MODELS,

        "has_ms_key": bool(modelscope_api_key()),

    }



@router.get("/api/models")

async def ai_models():

    return {"chat_models": core.CHAT_MODELS, "image_models": core.IMAGE_MODELS, "video_models": core.VIDEO_MODELS}



@router.get("/api/providers")

async def api_providers():

    return {"providers": public_api_providers()}



@router.put("/api/providers")

async def save_providers(payload: List[ApiProviderPayload]):

    providers = []

    env_updates = {}

    # 收集每个 item 的 primary 字段

    raw_primary_flags = [bool(getattr(item, "primary", False)) for item in payload]

    for item in payload:

        provider = normalize_provider(item.dict(exclude={"api_key"}))

        if provider["id"] == "runninghub":

            provider = preserve_runninghub_hidden_overrides(provider)

            prune_runninghub_workflow_store_for_provider(provider)

        if any(existing["id"] == provider["id"] for existing in providers):

            raise HTTPException(status_code=400, detail=f"API 平台 ID 重复：{provider['id']}")

        providers.append(provider)

        key_env = provider_key_env(provider["id"])

        if item.clear_key:

            env_updates[key_env] = ""

        elif item.api_key is not None and item.api_key.strip():

            env_updates[key_env] = item.api_key.strip()

        if provider["id"] == "runninghub":

            wallet_env = runninghub_wallet_key_env()

            if item.clear_wallet_key:

                env_updates[wallet_env] = ""

            elif item.wallet_api_key is not None and item.wallet_api_key.strip():

                env_updates[wallet_env] = item.wallet_api_key.strip()

        if provider["id"] == "volcengine":

            ak_env = volcengine_access_key_env()

            sk_env = volcengine_secret_key_env()

            if item.clear_volcengine_access_key_id:

                env_updates[ak_env] = ""

            elif item.volcengine_access_key_id is not None and item.volcengine_access_key_id.strip():

                env_updates[ak_env] = item.volcengine_access_key_id.strip()

            if item.clear_volcengine_secret_access_key:

                env_updates[sk_env] = ""

            elif item.volcengine_secret_access_key is not None and item.volcengine_secret_access_key.strip():

                env_updates[sk_env] = item.volcengine_secret_access_key.strip()

        if provider["id"] == "comfly":

            env_updates["COMFLY_BASE_URL"] = provider["base_url"]

            env_updates["IMAGE_MODELS"] = ",".join(provider["image_models"])

            env_updates["CHAT_MODELS"] = ",".join(provider["chat_models"])

            env_updates["VIDEO_MODELS"] = ",".join(provider.get("video_models") or [])

        if provider["id"] == "modelscope":

            env_updates["MODELSCOPE_CHAT_MODELS"] = ",".join(provider["chat_models"])

        if provider["id"] == "runninghub":

            provider["protocol"] = "runninghub"

        if provider["id"] == "volcengine":

            provider["protocol"] = "volcengine"

    if not providers:

        raise HTTPException(status_code=400, detail="至少保留一个 API 平台")

    # 强制最多一个 primary（取最后被标记的；都没标记则保持原样不强制）

    primary_indices = [i for i, flag in enumerate(raw_primary_flags) if flag]

    if primary_indices:

        winner = primary_indices[-1]

        for i, p in enumerate(providers):

            p["primary"] = (i == winner)

    save_api_providers(providers)

    runninghub_provider = next((item for item in providers if item.get("id") == "runninghub"), None)

    if runninghub_provider:

        sync_runninghub_provider_workflows_to_static_template(runninghub_provider)

    if env_updates:

        update_env_values(env_updates)

        reload_env_globals()   # 立即将最新 env 值同步回模块全局变量，无需重启

    return {"providers": [public_provider(p) for p in providers]}



# --- ModelScope Token (从 env 读取，不再支持通过 UI 修改) ---



@router.get("/api/config/token")

async def get_global_token():

    # 优先读 env，回退到 global_config.json（兼容旧数据）

    saved_token = modelscope_api_key()

    if saved_token:

        return {"token": saved_token}

    if os.path.exists(GLOBAL_CONFIG_FILE):

        try:

            with open(GLOBAL_CONFIG_FILE, 'r', encoding='utf-8') as f:

                config = json.load(f)

                return {"token": config.get("modelscope_token", "")}

        except:

            pass

    return {"token": ""}



@router.post("/api/providers/test-connection")

async def test_provider_connection(payload: TestConnectionPayload):

    """测试请求地址是否可用：调上游 /v1/models。验证通过时同时把模型清单按类别返回，避免再调一次拉取接口。"""

    protocol = protocol_from_payload(payload)

    if protocol == "codex":

        status = await codex_status()

        payload_models = codex_models_payload(raw={"status": status})

        payload_models.update({

            "ok": bool(status.get("installed")),

            "status": 200 if status.get("installed") else 0,

            "message": status.get("message") or ("OpenAI Codex CLI 可用" if status.get("installed") else "未找到 OpenAI Codex CLI"),

        })

        return payload_models

    if protocol == "gemini-cli":

        status = await gemini_cli_status()

        payload_models = gemini_cli_models_payload(raw={"status": status})

        payload_models.update({

            "ok": bool(status.get("installed")),

            "status": 200 if status.get("installed") else 0,

            "message": status.get("message") or ("Antigravity CLI 可用" if status.get("installed") else "未找到 Antigravity CLI"),

        })

        return payload_models

    if protocol == "jimeng":

        status = await jimeng_status()

        return {

            "ok": bool(status.get("installed") and status.get("logged_in")),

            "status": 200 if status.get("logged_in") else 0,

            "message": status.get("message") or "即梦 CLI 已登录",

            "model_count": len(JIMENG_DEFAULT_IMAGE_MODELS) + len(JIMENG_DEFAULT_VIDEO_MODELS),

            "image_models": JIMENG_DEFAULT_IMAGE_MODELS,

            "chat_models": [],

            "video_models": JIMENG_DEFAULT_VIDEO_MODELS,

            "all": [*JIMENG_DEFAULT_IMAGE_MODELS, *JIMENG_DEFAULT_VIDEO_MODELS],

            "raw": status.get("raw"),

        }

    if protocol == "runninghub":

        provider = {"id": "runninghub", "name": "RunningHub", "base_url": (payload.base_url or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip("/"), "protocol": "runninghub", "api_key": api_key_from_payload(payload, protocol)}

        payload_models = await runninghub_models_payload(provider)

        return {

            "ok": True,

            "status": 200,

            "message": "RunningHub OpenAPI 可用，已拉取官方直连模型注册表。",

            "model_count": payload_models["total"],

            "image_models": payload_models["image_models"],

            "chat_models": payload_models["chat_models"],

            "video_models": payload_models["video_models"],

            "all": payload_models["all"],

            "protocol": "runninghub",

            "raw": payload_models.get("raw"),

        }

    base_url = (payload.base_url or "").strip().rstrip("/")

    if not base_url:

        raise HTTPException(status_code=400, detail="请先填写请求地址")

    if not re.match(r"^https?://", base_url):

        raise HTTPException(status_code=400, detail="请求地址必须以 http:// 或 https:// 开头")

    api_key = api_key_from_payload(payload, protocol)

    if not api_key:

        key_name = "方舟 API Key" if protocol == "volcengine" else "API Key"

        raise HTTPException(status_code=400, detail=f"请先填写或保存 {key_name}")

    url = upstream_models_url(base_url, protocol)

    try:

        async with httpx.AsyncClient(timeout=15) as client:

            resp = await client.get(url, headers=upstream_model_headers(api_key, protocol))

            if resp.status_code in (301, 302, 303, 307, 308):

                location = resp.headers.get("Location") or resp.headers.get("location") or ""

                suffix = f"：{location}" if location else ""

                endpoint_label = "/v1beta/models" if protocol == "gemini" else "/api/v3/models" if protocol == "volcengine" else "/openapi/v2/models" if protocol == "runninghub" else "/v1/models"

                return {"ok": False, "status": resp.status_code, "message": f"上游 {endpoint_label} 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址"}

            if looks_like_html_response(resp.text):

                endpoint_label = "/v1beta/models" if protocol == "gemini" else "/api/v3/models" if protocol == "volcengine" else "/openapi/v2/models" if protocol == "runninghub" else "/v1/models"

                return {"ok": False, "status": resp.status_code, "message": f"上游 {endpoint_label} 返回网页 HTML，请检查请求地址是否为 API Base URL"}

            if resp.status_code >= 400:

                if protocol == "volcengine":

                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)

                    if detected:

                        message = f"{probe.get('message') or '方舟任务接口可达'}；但 /api/v3/models 不可用。请按实际方舟控制台模型名称手动填写视频模型。"

                        return volcengine_default_model_payload(status=probe.get("status") or resp.status_code, message=message, raw={"models_error": resp.text[:300], **(probe.get("raw") or {})})

                elif protocol == "openai":

                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)

                    if detected:

                        message = f"{probe.get('message') or '检测到方舟/Ark 兼容入口'}；OpenAI /v1/models 不可用，已自动切换为方舟协议。请按实际方舟控制台模型名称手动填写视频模型。"

                        return volcengine_default_model_payload(status=probe.get("status") or resp.status_code, message=message, raw={"models_error": resp.text[:300], **(probe.get("raw") or {})})

                return {"ok": False, "status": resp.status_code, "message": resp.text[:300]}

            data = resp.json() if resp.text else {}

            grouped, ids = parse_upstream_models(data, protocol)

            grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)

            grouped = apply_locked_recommended_model_rules(base_url, grouped)

            if protocol == "volcengine" and not ids:

                detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)

                if detected:

                    return volcengine_default_model_payload(status=resp.status_code, raw=data)

            return {

                "ok": True,

                "status": resp.status_code,

                "model_count": len(ids),

                "image_models": grouped["image"],

                "chat_models": grouped["chat"],

                "video_models": grouped["video"],

                "all": ids,

                "image_request_mode": detect_image_request_mode(base_url, ids) or normalize_image_request_mode(getattr(payload, "image_request_mode", "")),

            }

    except httpx.HTTPError as e:

        if protocol == "volcengine":

            try:

                async with httpx.AsyncClient(timeout=15) as client:

                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)

                    if detected:

                        message = f"{probe.get('message') or '方舟任务接口可达'}；但模型列表请求失败。请按实际方舟控制台模型名称手动填写视频模型。"

                        return volcengine_default_model_payload(status=probe.get("status") or 0, message=message, raw={"models_error": str(e)[:300], **(probe.get("raw") or {})})

            except Exception:

                pass

        return {"ok": False, "status": 0, "message": str(e)[:300]}



@router.post("/api/providers/probe-async")

async def probe_async_endpoint(payload: TestConnectionPayload):

    """验证异步协议：用假 task_id 请求 GET /v1/tasks/{fake_id}。

    收到 400 Invalid task ID = 端点存在且 Key 有效；401/403 = Key 无效；404/连接失败 = 不支持异步端点。"""

    base_url = (payload.base_url or "").strip().rstrip("/")

    protocol = protocol_from_payload(payload)

    if protocol == "codex":

        status = await codex_status()

        return {

            "ok": bool(status.get("installed")),

            "protocol": "codex",

            "status_code": 200 if status.get("installed") else 0,

            "message": status.get("message") or "OpenAI Codex CLI 本机检测完成",

            "raw": status,

        }

    if protocol == "gemini-cli":

        status = await gemini_cli_status()

        return {

            "ok": bool(status.get("installed")),

            "protocol": "gemini-cli",

            "status_code": 200 if status.get("installed") else 0,

            "message": status.get("message") or "Antigravity CLI 本机检测完成",

            "raw": status,

        }

    if not base_url:

        raise HTTPException(status_code=400, detail="请先填写请求地址")

    api_key = api_key_from_payload(payload, protocol)

    if not api_key:

        raise HTTPException(status_code=400, detail="请先填写或保存 API Key")

    is_tudou_async = is_tudou_base_url(base_url)

    if protocol == "volcengine":

        try:

            async with httpx.AsyncClient(timeout=15) as client:

                task_ok, task_probe = await probe_volcengine_task_endpoint(client, base_url, api_key)

                if task_ok:

                    return {

                        "ok": True,

                        "protocol": "volcengine",

                        "status_code": task_probe.get("status") or 200,

                        "message": "方舟/Ark 任务协议可用",

                        "raw": task_probe.get("raw"),

                    }

                compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)

                if compat_ok:

                    return {

                        "ok": True,

                        "protocol": "volcengine",

                        "status_code": compat_probe.get("status") or 200,

                        "message": "方舟/Ark Bearer 鉴权入口可用（OpenAI 兼容透传）",

                        "raw": {"task_probe": task_probe, "openai_compat_probe": compat_probe.get("raw")},

                    }

                return {

                    "ok": False,

                    "protocol": "volcengine",

                    "status_code": compat_probe.get("status") or task_probe.get("status") or 0,

                    "message": compat_probe.get("message") or task_probe.get("message") or "方舟/Ark 任务协议不可用",

                    "raw": {"task_probe": task_probe, "openai_compat_probe": compat_probe.get("raw")},

                }

        except httpx.HTTPError as e:

            raise HTTPException(status_code=502, detail=str(e)[:300])

    tasks_base = base_url if base_url.endswith("/v1") else f"{base_url}/v1"

    probe_url = f"{tasks_base}/tasks/healthcheck_probe_do_not_submit"

    try:

        async with httpx.AsyncClient(timeout=15) as client:

            resp = await client.get(probe_url, headers={"Authorization": bearer_auth_value(api_key), "Accept": "application/json"})

            try:

                body = resp.json()

            except Exception:

                body = resp.text[:500]

            sc = resp.status_code

            # 判断结果

            err_msg = ""

            if isinstance(body, dict):

                err = body.get("error") or {}

                if isinstance(err, dict):

                    err_msg = str(err.get("message") or "").lower()

                else:

                    err_msg = str(err).lower()

            # 400 + "invalid task id" → 端点存在，Key 有效

            if is_tudou_async and sc == 400:

                # 土豆对不存在的 task_id 可能使用不同的 400 错误字段；只要不是

                # 401/403，400 已证明请求命中了异步任务端点且认证通过。

                return {

                    "ok": True,

                    "protocol": "openai",

                    "image_request_mode": "tudou-async",

                    "status_code": sc,

                    "message": "土豆 GPT-Image-2 异步任务端点可用，API Key 已通过认证",

                    "raw": body,

                }

            if sc == 400 and "invalid task id" in err_msg:

                return {"ok": True, "protocol": "apimart", "status_code": sc, "message": "APIMart 异步任务端点可用，API Key 已通过认证", "raw": body}



            async_probe = {"status": sc, "message": "", "raw": body}

            if sc in (301, 302, 303, 307, 308):

                location = resp.headers.get("Location") or resp.headers.get("location") or ""

                async_probe["message"] = f"/v1/tasks/ 发生跳转{f'：{location}' if location else ''}"

            elif looks_like_html_response(resp.text):

                async_probe["message"] = "/v1/tasks/ 返回网页 HTML"

            elif sc in (401, 403):

                async_probe["message"] = "/v1/tasks/ 返回鉴权失败"

            elif sc == 404:

                async_probe["message"] = "平台不支持 /v1/tasks/ 端点，可能不是 APIMart 异步协议"

            elif 400 <= sc < 500:

                async_probe["message"] = f"/v1/tasks/ 返回 {sc}"

            elif sc < 300:

                async_probe["message"] = f"/v1/tasks/ 返回 {sc}（意外成功）"

            else:

                async_probe["message"] = f"/v1/tasks/ 服务端错误 {sc}"



            if is_tudou_async:

                return {

                    "ok": sc < 400,

                    "protocol": "openai",

                    "image_request_mode": "tudou-async" if sc < 400 else "openai",

                    "status_code": sc,

                    "message": async_probe["message"] or "土豆 GPT-Image-2 异步任务端点验证完成",

                    "raw": body,

                }



            if protocol == "apimart":

                return {"ok": False, "protocol": "apimart", "status_code": sc, "message": async_probe["message"], "raw": body}



            openai_ok, openai_probe = await probe_openai_models_endpoint(client, base_url, api_key)

            if not openai_ok and protocol == "openai":

                # /v1/models 不可用，先确认是不是“没实现 models 接口的 OpenAI 兼容站”：探一下 /v1/chat/completions。

                # 可达就判定为 OpenAI 兼容（很多网关不暴露 /v1/models），避免被下面的方舟探测（404 也算可达）误判成方舟。

                compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)

                # 仅当 /v1/chat/completions 确实存在（返回 2xx 或我们发空 messages 触发的 400 等，而非 404 路径不存在）

                # 才判为 OpenAI 兼容；404 说明该路径不存在，留给后面的方舟探测。

                if compat_ok and (compat_probe.get("status") or 0) != 404:

                    return {

                        "ok": True,

                        "protocol": "openai",

                        "status_code": compat_probe.get("status") or openai_probe.get("status") or sc,

                        "message": "OpenAI 兼容入口可达（该站未提供 /v1/models，模型请手动填写）",

                        "raw": {"async_probe": async_probe, "openai_probe": openai_probe.get("raw"), "openai_compat_probe": compat_probe.get("raw")},

                        "model_count": 0,

                        "image_models": [],

                        "chat_models": [],

                        "video_models": [],

                        "all": [],

                    }

                detected, volc_probe = await probe_volcengine_auto_detect(client, base_url, api_key)

                if detected:

                    return {

                        "ok": True,

                        "protocol": "volcengine",

                        "status_code": volc_probe.get("status") or openai_probe.get("status") or sc,

                        "message": f"{volc_probe.get('message') or '检测到方舟/Ark 兼容入口'}，已自动切换为方舟/Ark 任务协议",

                        "raw": {"async_probe": async_probe, "openai_probe": openai_probe.get("raw"), **(volc_probe.get("raw") or {})},

                    }

            return {

                "ok": openai_ok,

                "protocol": "openai",

                "status_code": openai_probe.get("status") or sc,

                "message": openai_probe.get("message") or "OpenAI 兼容验证完成",

                "raw": {"async_probe": async_probe, "openai_probe": openai_probe.get("raw")},

                "model_count": openai_probe.get("model_count") or 0,

                "image_models": openai_probe.get("image_models") or [],

                "chat_models": openai_probe.get("chat_models") or [],

                "video_models": openai_probe.get("video_models") or [],

                "all": openai_probe.get("all") or [],

                "image_request_mode": detect_image_request_mode(base_url, openai_probe.get("all") or []) or normalize_image_request_mode(getattr(payload, "image_request_mode", "")),

            }

    except httpx.HTTPError as e:

        raise HTTPException(status_code=502, detail=str(e)[:300])



@router.post("/api/providers/fetch-models")

async def fetch_upstream_models_from_payload(payload: TestConnectionPayload):

    """按页面当前表单值拉取模型，支持新增平台未保存时直接使用临时 Base URL / Key。"""

    protocol = protocol_from_payload(payload)

    api_key = api_key_from_payload(payload, protocol)

    return await fetch_models_from_upstream(payload.base_url, api_key, protocol, payload.image_request_mode)



@router.get("/api/providers/{provider_id}/fetch-models")

async def fetch_upstream_models(provider_id: str):

    """从已保存的上游 OpenAI 兼容接口拉取 /v1/models 列表，按名称智能分类为 image/chat/video。"""

    provider = get_api_provider_exact(provider_id)

    if is_codex_provider(provider):

        return await fetch_models_from_upstream("", "", "codex", provider.get("image_request_mode") or "openai")

    if is_gemini_cli_provider(provider):

        return await fetch_models_from_upstream("", "", "gemini-cli", provider.get("image_request_mode") or "openai")

    api_key = os.getenv(runninghub_wallet_key_env(), "") if provider["id"] == "runninghub" else ""

    if not api_key:

        api_key = provider_env_key_value(provider["id"])

    if not api_key:

        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider_id} 未配置 API Key")

    return await fetch_models_from_upstream(provider.get("base_url") or "", api_key, provider_protocol(provider), provider.get("image_request_mode") or "openai")



@router.post("/api/online-image")

async def online_image(payload: OnlineImageRequest):

    return await build_online_image_result(payload)



@router.post("/api/midjourney/submit")

async def submit_midjourney(payload: MidjourneySubmitRequest):

    provider = apimart_midjourney_provider(payload.provider_id)

    speed = str(payload.speed or "relax").strip().lower()

    if speed not in MIDJOURNEY_SPEEDS:

        raise HTTPException(status_code=400, detail="Midjourney 速度仅支持 relax、fast 或 turbo。")

    size = str(payload.size or "1:1").strip()

    if not re.fullmatch(r"\d{1,2}:\d{1,2}", size):

        raise HTTPException(status_code=400, detail="Midjourney 画幅应为宽:高，例如 16:9。")

    mode = str(payload.mode or "imagine").strip().lower()

    if mode not in {"imagine", "blend", "edit"}:

        raise HTTPException(status_code=400, detail="不支持的 Midjourney 节点模式。")

    image_urls = await midjourney_reference_urls([ref.dict() for ref in payload.reference_images if ref.url])

    prompt = str(payload.prompt or "").strip()

    if mode == "blend":

        if not 2 <= len(image_urls) <= 4:

            raise HTTPException(status_code=400, detail="Midjourney 多图融合需要连接 2 到 4 张图片。")

        body = {"image_urls": image_urls, "size": size, "speed": speed, "metadata": {"source": "infinite-canvas"}}

        path = "/v1/midjourney/generations/blend"

    elif mode == "edit":

        if not prompt:

            raise HTTPException(status_code=400, detail="Midjourney 图片编辑需要提示词。")

        if not image_urls:

            raise HTTPException(status_code=400, detail="Midjourney 图片编辑需要连接至少一张图片。")

        body = {"prompt": prompt, "image_urls": image_urls, "size": size, "speed": speed, "metadata": {"source": "infinite-canvas"}}

        path = "/v1/midjourney/generations/edits"

    else:

        if not prompt:

            raise HTTPException(status_code=400, detail="Midjourney 文生图需要提示词。")

        body = {"prompt": prompt, "size": size, "version": str(payload.version or "6.1").strip()[:24], "speed": speed, "metadata": {"source": "infinite-canvas"}}

        if image_urls:

            body["image_urls"] = image_urls

        path = "/v1/midjourney/generations"

    raw, task_id = await apimart_midjourney_request(provider, path, body)

    return {"task_id": task_id, "status": midjourney_task_status(raw) or "queued", "provider_id": provider["id"], "mode": mode, "raw": raw}



@router.post("/api/midjourney/actions")

async def submit_midjourney_action(payload: MidjourneyActionRequest):

    provider = apimart_midjourney_provider(payload.provider_id)

    action = str(payload.action or "").strip().lower()

    if action not in MIDJOURNEY_ACTION_PATHS:

        raise HTTPException(status_code=400, detail="不支持的 Midjourney 操作。")

    task_id = str(payload.task_id or "").strip()

    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", task_id):

        raise HTTPException(status_code=400, detail="Midjourney 任务 ID 不合法。")

    speed = str(payload.speed or "relax").strip().lower()

    if speed not in MIDJOURNEY_SPEEDS:

        raise HTTPException(status_code=400, detail="Midjourney 速度仅支持 relax、fast 或 turbo。")

    body = {"task_id": task_id, "speed": speed, "metadata": {"source": "infinite-canvas"}}

    custom_id = str(payload.custom_id or "").strip()

    if custom_id:

        body["custom_id"] = custom_id

    if action in {"upscale", "variation", "low_variation", "high_variation", "remix_subtle", "remix_strong"} and not custom_id:

        index = int(payload.index or 0)

        if index not in {1, 2, 3, 4}:

            raise HTTPException(status_code=400, detail="Midjourney 图片序号应为 1 到 4。")

        body["index"] = index

    elif action in {"zoom", "pan"} and int(payload.index or 0) in {1, 2, 3, 4}:

        body["index"] = int(payload.index)

    if action == "zoom" and payload.zoom_ratio is not None:

        zoom_ratio = float(payload.zoom_ratio)

        if zoom_ratio <= 1 or zoom_ratio > 4:

            raise HTTPException(status_code=400, detail="Midjourney 缩放比例应大于 1 且不超过 4。")

        body["zoom_ratio"] = zoom_ratio

    if action == "pan" and not custom_id:

        direction = str(payload.direction or "").strip().lower()

        if direction not in {"left", "right", "up", "down"}:

            raise HTTPException(status_code=400, detail="Midjourney 平移方向仅支持 left、right、up 或 down。")

        body["direction"] = direction

    if action in {"remix_subtle", "remix_strong"}:

        prompt = str(payload.prompt or "").strip()

        if prompt:

            body["prompt"] = prompt

    raw, new_task_id = await apimart_midjourney_request(provider, MIDJOURNEY_ACTION_PATHS[action], body)

    return {"task_id": new_task_id, "status": midjourney_task_status(raw) or "queued", "provider_id": provider["id"], "action": action, "raw": raw}



@router.post("/api/midjourney/modal")

async def submit_midjourney_modal(payload: MidjourneyModalRequest):

    provider = apimart_midjourney_provider(payload.provider_id)

    task_id = str(payload.task_id or "").strip()

    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", task_id):

        raise HTTPException(status_code=400, detail="Midjourney 任务 ID 不合法。")

    speed = str(payload.speed or "relax").strip().lower()

    if speed not in MIDJOURNEY_SPEEDS:

        raise HTTPException(status_code=400, detail="Midjourney 速度仅支持 relax、fast 或 turbo。")

    mask_url = await midjourney_modal_mask_url(payload.mask_image)

    if not mask_url:

        raise HTTPException(status_code=400, detail="Midjourney 局部重绘需要连接一个遮罩图片节点。")

    body = {

        "task_id": task_id,

        "prompt": str(payload.prompt or "").strip(),

        "mask_url": mask_url,

        "speed": speed,

        "metadata": {"source": "infinite-canvas"},

    }

    raw, submitted_task_id = await apimart_midjourney_request(provider, "/v1/midjourney/generations/modal", body)

    return {"task_id": submitted_task_id, "status": midjourney_task_status(raw) or "submitted", "provider_id": provider["id"], "raw": raw}



@router.get("/api/midjourney/tasks/{task_id}")

async def get_midjourney_task(task_id: str, provider_id: str):

    provider = apimart_midjourney_provider(provider_id)

    return await midjourney_result(provider, task_id)



@router.post("/api/image-task-query")

async def query_image_task(payload: ImageTaskQueryRequest):

    provider = get_api_provider(payload.provider_id)

    task_id = str(payload.task_id or "").strip()

    if is_runninghub_provider(provider):

        api_key = runninghub_api_key(provider)

        url = runninghub_endpoint_url(provider, "/task/openapi/outputs")

        try:

            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=20.0)) as client:

                response = await client.post(url, headers=runninghub_app_headers(True), json={"apiKey": api_key, "taskId": task_id})

                response.raise_for_status()

                raw = response.json()

                code = raw.get("code") if isinstance(raw, dict) else None

                if code in (0, "0"):

                    local_urls = []

                    local_items = []

                    for remote in runninghub_extract_outputs(raw.get("data")):

                        try:

                            local_url = await runninghub_store_remote_output(client, remote)

                        except Exception:

                            local_url = rewrite_runninghub_file_url(remote)

                        if local_url:

                            local_urls.append(local_url)

                            local_items.append(image_output_meta(local_url))

                    result = {

                        "status": "succeeded",

                        "prompt": "",

                        "images": local_urls,

                        "image_items": local_items,

                        "timestamp": time.time(),

                        "type": "online",

                        "model": "",

                        "provider_id": provider["id"],

                        "provider_name": provider.get("name") or provider["id"],

                        "task_id": task_id,

                        "request_id": "",

                        "params": {"provider_id": provider["id"]},

                        "raw": raw,

                    }

                    save_to_history(result)

                    if core.GLOBAL_LOOP:

                        asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), core.GLOBAL_LOOP)

                    return result

                if code in (805, "805"):

                    return {

                        "status": "failed",

                        "task_id": task_id,

                        "provider_id": provider["id"],

                        "provider_name": provider.get("name") or provider["id"],

                        "error": runninghub_fail_reason(raw),

                        "raw": raw,

                    }

                return {

                    "status": "running",

                    "task_id": task_id,

                    "provider_id": provider["id"],

                    "provider_name": provider.get("name") or provider["id"],

                    "message": "RunningHub 任务仍在生成中",

                    "raw": raw,

                }

        except httpx.HTTPStatusError as exc:

            text = exc.response.text or ""

            raise HTTPException(status_code=exc.response.status_code, detail=f"查询 RunningHub 任务失败：{text[:300]}") from exc

        except httpx.HTTPError as exc:

            raise HTTPException(status_code=502, detail=f"查询 RunningHub 任务失败：{exc}") from exc

    timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)

    try:

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

            raw = await fetch_image_task_payload(client, task_id, provider)

    except httpx.HTTPStatusError as exc:

        log_net_error(f"查询生图任务 HTTP状态错误 provider={provider.get('id')} task_id={task_id}", exc)

        text = exc.response.text or ""

        raise HTTPException(status_code=exc.response.status_code, detail=f"查询上游生图任务失败：{text[:300]}") from exc

    except httpx.HTTPError as exc:

        log_net_error(f"查询生图任务 网络/TLS错误 provider={provider.get('id')} task_id={task_id}", exc)

        raise HTTPException(status_code=502, detail=f"查询上游生图任务失败：{exc}") from exc



    status = image_task_status(raw)

    image_items = []

    try:

        image_items = extract_images(raw)

    except HTTPException:

        image_items = []

    if image_items:

        local_urls = []

        local_items = []

        for item in image_items:

            local_url = await save_ai_image_to_output(item, prefix="online_")

            if local_url:

                local_urls.append(local_url)

                local_items.append(image_output_meta(local_url, item))

        result = {

            "status": "succeeded",

            "prompt": "",

            "images": local_urls,

            "image_items": local_items,

            "timestamp": time.time(),

            "type": "online",

            "model": "",

            "provider_id": provider["id"],

            "provider_name": provider.get("name") or provider["id"],

            "task_id": task_id,

            "request_id": raw.get("id") if isinstance(raw, dict) else "",

            "params": {"provider_id": provider["id"]},

            "raw": raw,

        }

        save_to_history(result)

        if core.GLOBAL_LOOP:

            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), core.GLOBAL_LOOP)

        return result

    if status in IMAGE_TASK_FAILED_STATUSES:

        return {

            "status": "failed",

            "task_id": task_id,

            "provider_id": provider["id"],

            "provider_name": provider.get("name") or provider["id"],

            "error": image_task_fail_reason(raw),

            "raw": raw,

        }

    return {

        "status": "running",

        "task_id": task_id,

        "provider_id": provider["id"],

        "provider_name": provider.get("name") or provider["id"],

        "message": "任务仍在生成中",

        "raw": raw,

    }



@router.post("/api/canvas-image-tasks")

async def create_canvas_image_task(payload: OnlineImageRequest):

    task_id = f"canvas_img_{uuid.uuid4().hex}"

    with CANVAS_TASK_LOCK:

        CANVAS_TASKS[task_id] = {

            "id": task_id,

            "type": "online-image",

            "status": "queued",

            "created_at": time.time(),

            "updated_at": time.time(),

            "result": None,

            "error": "",

            "provider_id": payload.provider_id,

            "model": payload.model,

        }

    asyncio.create_task(run_canvas_image_task(task_id, payload))

    return {"task_id": task_id, "status": "queued"}



@router.get("/api/canvas-image-tasks/{task_id}")

async def get_canvas_image_task(task_id: str):

    with CANVAS_TASK_LOCK:

        task = dict(CANVAS_TASKS.get(task_id) or {})

    if not task:

        raise HTTPException(status_code=404, detail="画布任务不存在，可能服务已重启或任务已过期")

    return task



@router.post("/api/canvas-comfy-tasks")

async def create_canvas_comfy_task(payload: GenerateRequest):

    task_id = f"canvas_comfy_{uuid.uuid4().hex}"

    with CANVAS_TASK_LOCK:

        CANVAS_TASKS[task_id] = {

            "id": task_id,

            "type": "comfy",

            "status": "queued",

            "created_at": time.time(),

            "updated_at": time.time(),

            "result": None,

            "error": "",

            "workflow_json": payload.workflow_json,

        }

    asyncio.create_task(run_canvas_comfy_task(task_id, payload))

    return {"task_id": task_id, "status": "queued"}



@router.get("/api/canvas-comfy-tasks/{task_id}")

async def get_canvas_comfy_task(task_id: str):

    with CANVAS_TASK_LOCK:

        task = dict(CANVAS_TASKS.get(task_id) or {})

    if not task:

        raise HTTPException(status_code=404, detail="ComfyUI 任务不存在，可能服务已重启或任务已过期")

    return task



@router.get("/api/image-params")

async def image_params(provider_id: str = "", model: str = ""):

    providers = load_api_providers()

    provider = next((p for p in providers if p.get("id") == (provider_id or "").strip().lower()), None) or {}

    if is_runninghub_provider(provider):

        engine = "runninghub"

    elif (provider_id or "").strip().lower() == "modelscope":

        engine = "modelscope"

    elif is_volcengine_provider(provider):

        engine = "volcengine"

    else:

        engine = "api"

    return {

        "engine": engine,

        "submit": "/api/canvas-image-tasks",

        "fields": build_image_param_fields(engine, provider, model),

    }



@router.post("/api/canvas-video")

async def canvas_video(payload: CanvasVideoRequest):

    provider = get_api_provider(payload.provider_id)

    if is_jimeng_provider(provider):

        return await generate_jimeng_video(payload, provider)

    if is_runninghub_provider(provider):

        try:

            return await generate_runninghub_video(payload, provider)

        except HTTPException as exc:

            print(f"RunningHub 视频生成失败 model={payload.model}: {exc.detail}")

            raise

        except httpx.HTTPStatusError as exc:

            text = exc.response.text

            raise HTTPException(status_code=exc.response.status_code, detail=f"RunningHub 视频接口错误：{text}") from exc

        except httpx.HTTPError as exc:

            log_net_error(f"视频(RunningHub) 网络/TLS错误 model={payload.model}", exc)

            raise HTTPException(status_code=502, detail=f"请求 RunningHub 视频接口失败：{exc}") from exc

    base_url = video_api_root(provider)

    if not base_url:

        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")

    api_key = provider_env_key_value(provider["id"])

    if not api_key:

        raise HTTPException(status_code=400, detail=f"未配置 {provider.get('name') or provider['id']} 的 API Key，请在 API 设置中填写。")

    is_apimart = is_apimart_provider(provider)

    is_volcengine = is_volcengine_provider(provider)

    is_yuli = is_yuli_provider(provider)

    is_lingjing = is_lingjing_provider(provider)

    is_agnes = is_agnes_provider(provider, payload.model)

    volc_is_proxy = bool(is_volcengine and urllib.parse.urlparse(base_url).path.rstrip("/"))

    submit_urls = video_submit_url_candidates(provider, base_url)

    submit_url = submit_urls[0]

    requested_model = selected_model(payload.model, "agnes-video-v2.0" if is_agnes else "veo3-fast")

    if is_tudou_provider(provider) and is_tudou_video_model(requested_model):

        try:

            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as tudou_client:

                if is_tudou_grok_video_model(requested_model):

                    return await generate_tudou_grok_video(tudou_client, payload, provider, base_url, requested_model)

                return await generate_tudou_video(tudou_client, payload, provider, base_url, requested_model)

        except httpx.HTTPStatusError as exc:

            text = exc.response.text

            friendly = friendly_video_error_detail(text, requested_model, provider)

            raise HTTPException(status_code=exc.response.status_code, detail=friendly or f"土豆视频接口错误：{text}") from exc

        except httpx.HTTPError as exc:

            log_net_error(f"视频(土豆) 网络/TLS错误 model={requested_model}", exc)

            raise HTTPException(status_code=502, detail=f"请求土豆视频接口失败：{exc}") from exc

    is_veo31 = is_apimart and is_apimart_veo31_model(requested_model)

    if is_agnes:

        try:

            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as agnes_client:

                return await generate_agnes_video(agnes_client, payload, provider, base_url, requested_model)

        except httpx.HTTPStatusError as exc:

            text = exc.response.text

            raise HTTPException(status_code=exc.response.status_code, detail=f"Agnes 视频接口错误：{text}") from exc

        except httpx.HTTPError as exc:

            log_net_error(f"视频(Agnes) 网络/TLS错误 model={requested_model}", exc)

            raise HTTPException(status_code=502, detail=f"请求 Agnes 视频接口失败：{exc}") from exc

    if is_lingjing:

        try:

            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as lingjing_client:

                return await generate_lingjing_openai_video(lingjing_client, payload, provider, base_url, requested_model)

        except httpx.HTTPStatusError as exc:

            text = exc.response.text

            raise HTTPException(status_code=exc.response.status_code, detail=f"灵境 API 视频接口错误：{text}") from exc

        except httpx.HTTPError as exc:

            log_net_error(f"视频(灵境) 网络/TLS错误 model={requested_model}", exc)

            raise HTTPException(status_code=502, detail=f"请求灵境 API 视频接口失败：{exc}") from exc

    # 玉玉API veo3.1 走 OpenAI multipart 格式（支持 seconds 时长）；其余模型（doubao 等）

    # 沿用下方原生 /v1/video/create JSON 流程。

    if is_yuli and yuli_is_veo_openai_model(requested_model):

        try:

            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as yuli_client:

                return await generate_yuli_openai_video(yuli_client, payload, provider, base_url, requested_model)

        except httpx.HTTPStatusError as exc:

            text = exc.response.text

            raise HTTPException(status_code=exc.response.status_code, detail=f"上游视频接口错误：{text}") from exc

        except httpx.HTTPError as exc:

            log_net_error(f"视频(玉玉) 网络/TLS错误 model={requested_model}", exc)

            raise HTTPException(status_code=502, detail=f"请求上游视频接口失败：{exc}") from exc

    try:

        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:

            # --- 构造图片载荷 ---

            if is_apimart:

                # APIMart 只接受 http/https 或 asset:// URL，先上传本地图片取回网络 URL

                image_with_roles = []

                invalid_images = []  # 每项为 (原始 URL, 失败原因)

                video_payload = []

                invalid_videos = []

                for ref_url in payload.videos[:3]:

                    ref_url = str(ref_url or "").strip()

                    if not ref_url:

                        continue

                    if looks_like_image_media_url(ref_url):

                        invalid_videos.append((ref_url, "该地址是图片，不能作为参考视频。请将图片放入图片输入。"))

                        continue

                    normalized_video_url = await upload_video_for_apimart(client, provider, ref_url)

                    if valid_apimart_video_image_input(normalized_video_url):

                        video_payload.append(normalized_video_url)

                    else:

                        reason = normalized_video_url[4:] if isinstance(normalized_video_url, str) and normalized_video_url.startswith("ERR:") else apimart_video_reference_error(ref_url)

                        invalid_videos.append((ref_url, reason))

                if invalid_videos:

                    first_url, first_reason = invalid_videos[0]

                    sample = invalid_video_image_preview(first_url)

                    raise HTTPException(

                        status_code=400,

                        detail=f"输入视频无法转换为 APIMart 支持的格式：{sample}\n原因：{first_reason}"

                    )

                apimart_model = apimart_veo31_model(requested_model) if is_veo31 else ""

                if apimart_model == "veo3.1-lite" and payload.images:

                    raise HTTPException(status_code=400, detail="veo3.1-lite 不支持图片输入，请改用 veo3.1-fast 或 veo3.1-quality。")

                image_limit = 0 if apimart_model == "veo3.1-lite" else (3 if is_veo31 else 9)

                for ref in payload.images[:image_limit]:

                    if not ref.url:

                        continue

                    role = str(ref.role or "").strip()

                    if not is_veo31 and role in {"first_frame", "last_frame", "reference_image"}:

                        up_url = await upload_image_for_apimart(client, provider, ref.url)

                        if valid_apimart_video_image_input(up_url):

                            image_with_roles.append({"url": up_url, "role": role})

                        else:

                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith("ERR:") else "未知错误"

                            invalid_images.append((ref.url, reason))

                image_payload = []

                if not image_with_roles:

                    for ref in payload.images[:image_limit]:

                        if not ref.url:

                            continue

                        up_url = await upload_image_for_apimart(client, provider, ref.url)

                        if valid_apimart_video_image_input(up_url):

                            image_payload.append(up_url)

                        else:

                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith("ERR:") else "未知错误"

                            invalid_images.append((ref.url, reason))

                if payload.images and not image_with_roles and not image_payload:

                    first_url, first_reason = invalid_images[0] if invalid_images else ("", "未知错误")

                    sample = invalid_video_image_preview(first_url)

                    raise HTTPException(status_code=400, detail=f"输入图片无法转换为视频接口支持的格式：{sample}\n原因：{first_reason}\n请确认本地文件存在且不超过 10MB；VEO3.1 需要图片是 APIMart 可访问的 http/https / asset:// / data URL。")

                # --- APIMart 请求体 ---

                if is_veo31:

                    model = apimart_model

                    body = {

                        "prompt": payload.prompt,

                        "model": model,

                        "duration": apimart_veo31_duration(payload.duration),

                        "aspect_ratio": apimart_veo31_aspect(payload.aspect_ratio),

                        "resolution": apimart_veo31_resolution(payload.resolution),

                    }

                    if image_payload and model != "veo3.1-lite":

                        video_images = image_payload[:3]

                        if model == "veo3.1-quality" and len(video_images) > 2:

                            video_images = video_images[:2]

                        body["image_urls"] = video_images

                        if len(video_images) == 2:

                            body["generation_type"] = "frame"

                        elif len(video_images) >= 3 and model != "veo3.1-quality":

                            body["generation_type"] = "reference"

                    if model != "veo3.1-lite":

                        body["official_fallback"] = False

                else:

                    body = {

                        "prompt": payload.prompt,

                        "model": selected_model(payload.model, "doubao-seedance-2.0"),

                        "duration": apimart_video_duration(payload.duration),

                        "size": apimart_video_size(payload.aspect_ratio or payload.size),

                        "resolution": payload.resolution or "480p",

                    }

                    if image_with_roles and video_payload:

                        raise HTTPException(status_code=400, detail="APIMart Seedance 的 image_with_roles 不能和 video_urls 同时使用，请只保留图片首尾帧或参考视频其中一种。")

                    if image_with_roles:

                        body["image_with_roles"] = image_with_roles

                    elif image_payload:

                        body["image_urls"] = image_payload[:9]

                    if video_payload:

                        body["video_urls"] = video_payload

                    audio_payload = []

                    invalid_audios = []

                    for ref_url in (payload.audios or [])[:3]:

                        ref_url = str(ref_url or "").strip()

                        if not ref_url:

                            continue

                        normalized_audio_url = await upload_audio_for_apimart(client, provider, ref_url)

                        if valid_apimart_video_image_input(normalized_audio_url):

                            audio_payload.append(normalized_audio_url)

                        else:

                            reason = normalized_audio_url[4:] if isinstance(normalized_audio_url, str) and normalized_audio_url.startswith("ERR:") else "未知错误"

                            invalid_audios.append((ref_url, reason))

                    if invalid_audios:

                        first_url, first_reason = invalid_audios[0]

                        raise HTTPException(status_code=400, detail=f"参考音频无法转换为 APIMart 支持的地址：{invalid_video_image_preview(first_url)}\n原因：{first_reason}")

                    if audio_payload:

                        body["audio_urls"] = audio_payload

                    if payload.trusted_asset:

                        img_count = len(body.get("image_urls") or []) or len(image_with_roles)

                        body["prompt"] = apply_trusted_asset_prompt_index(

                            body["prompt"], img_count, len(video_payload), len(audio_payload)

                        )

                    if payload.seed is not None:

                        body["seed"] = payload.seed

                    if payload.return_last_frame:

                        body["return_last_frame"] = True

                    if payload.generate_audio:

                        body["generate_audio"] = True

            else:

                # 非 APIMart：data URL 方式（OpenAI / ComflyAI 接口）

                if is_volcengine and not volc_is_proxy:

                    text = str(payload.prompt or "").strip()

                    volc_model = selected_model(payload.model, "doubao-seedance-2-0-fast-260128")

                    body = {

                        "model": volc_model,

                        "content": [

                            {

                                "type": "text",

                                "text": text,

                            }

                        ],

                    }

                    # 火山方舟视频接口（含 Seedance 2.0 图生视频）均通过 body 的 duration 字段控制时长；

                    # 之前对 seedance-2.0 + 参考图的情况省略了 duration，导致接口回退到默认 5s。

                    body["duration"] = volcengine_video_duration(payload.duration)

                    if payload.aspect_ratio:

                        body["ratio"] = payload.aspect_ratio

                    resolution = volcengine_video_resolution(payload.resolution)

                    if resolution:

                        body["resolution"] = resolution

                    if payload.watermark:

                        body["watermark"] = True

                    if payload.generate_audio:

                        body["generate_audio"] = True

                    if payload.camerafixed:

                        body["camerafixed"] = True

                    image_like_urls = set()

                    frame_roles_used = {"first_frame": False, "last_frame": False}

                    volc_video_count = 0



                    def append_volcengine_image(url: str, role: str):

                        if role in {"first_frame", "last_frame"}:

                            if frame_roles_used.get(role):

                                return False

                            frame_roles_used[role] = True

                        elif role != "reference_image":

                            return False

                        body["content"].append({

                            "type": "image_url",

                            "image_url": {"url": url},

                            "role": role,

                        })

                        image_like_urls.add(url)

                        return True



                    for ref in payload.images[:9]:

                        url = volcengine_media_reference_url(ref.url, max_image_size=1536)

                        if not url:

                            continue

                        role = volcengine_content_role(ref.role, "image")

                        if role in {"first_frame", "last_frame"}:

                            append_volcengine_image(url, role)

                        elif payload.multimodal:

                            # 智能多帧/多参模式：多张图作为参考图提交，不能全部伪装成首帧。

                            append_volcengine_image(url, "reference_image")

                        elif not frame_roles_used["first_frame"]:

                            # 普通图生视频没有显式 role 时，只取第一张作为首帧。

                            append_volcengine_image(url, "first_frame")

                    for url in (payload.videos or [])[:3]:

                        text_url = str(url or "").strip()

                        if not text_url:

                            continue

                        media_url = volcengine_media_reference_url(text_url, max_image_size=1536 if looks_like_image_media_url(text_url) else None)

                        if not media_url:

                            continue

                        if media_url in image_like_urls or looks_like_image_media_url(media_url):

                            append_volcengine_image(media_url, "reference_image" if payload.multimodal else "first_frame")

                            continue

                        video_items = await volcengine_video_reference_content_items(media_url)

                        body["content"].extend(video_items)

                        volc_video_count += 1

                    for url in (payload.audios or [])[:3]:

                        duration = probe_local_audio_duration_seconds(url)

                        if duration is not None and (duration < 1.8 or duration > 15.2):

                            raise HTTPException(

                                status_code=400,

                                detail=f"参考音频时长 {duration:.2f} 秒超出范围：方舟 Seedance 参考音频要求在 1.8 ~ 15.2 秒之间，请裁剪后再插入。"

                            )

                        audio_url = volcengine_media_reference_url(url, max_image_size=None)

                        if not audio_url:

                            continue

                        body["content"].append({

                            "type": "audio_url",

                            "audio_url": {"url": audio_url},

                            "role": volcengine_content_role("", "audio"),

                        })

                    if payload.trusted_asset and body["content"] and body["content"][0].get("type") == "text":

                        body["content"][0]["text"] = apply_trusted_asset_prompt_index(

                            body["content"][0].get("text") or "", len(image_like_urls), volc_video_count, 0

                        )

                    if payload.seed is not None:

                        body["seed"] = payload.seed

                elif is_yuli:

                    # 玉玉API（yuli.host）视频走自有 veo 统一格式：POST /v1/video/create。

                    # 字段：model / prompt / images[]（http(s) URL）/ enhance_prompt /

                    # enable_upsample / aspect_ratio（仅 16:9、9:16）。无 duration 字段，

                    # 时长由模型本身决定，所以这里不传 duration/seconds。

                    yuli_images = []

                    for ref in payload.images[:3]:

                        ref_url = str(getattr(ref, "url", "") or "").strip()

                        if not ref_url:

                            continue

                        if ref_url.startswith("http://") or ref_url.startswith("https://"):

                            yuli_images.append(ref_url)

                        else:

                            # 本地/dataURL 图片转成 data URL 兜底传递

                            data_url = reference_to_data_url(ref.dict(), max_size=1536)

                            if data_url:

                                yuli_images.append(data_url)

                    prompt_text = str(payload.prompt or "")

                    # veo 只支持英文提示词：仅在含中文等非 ASCII 字符时才开启翻译增强，

                    # 纯英文原样传递（避免增强改写时引入人物等触发安全过滤的描述）。

                    needs_enhance = any(ord(ch) > 127 for ch in prompt_text)

                    body = {

                        "model": selected_model(payload.model, "veo3.1-fast"),

                        "prompt": prompt_text,

                        "enhance_prompt": needs_enhance,

                    }

                    if yuli_images:

                        body["images"] = yuli_images

                    ratio = str(payload.aspect_ratio or "").strip()

                    if ratio in {"16:9", "9:16"}:

                        body["aspect_ratio"] = ratio

                    if payload.enable_upsample:

                        body["enable_upsample"] = True

                else:

                    image_payload = []

                    for ref in payload.images[:4]:

                        if ref.url:

                            image_payload.append(reference_to_data_url(ref.dict(), max_size=1536))

                    body = {

                        "prompt": payload.prompt,

                        "model": selected_model(payload.model, "veo3-fast"),

                        "duration": payload.duration,

                        "watermark": payload.watermark,

                    }

                    if payload.aspect_ratio:

                        body["aspect_ratio"] = payload.aspect_ratio

                        body["ratio"] = payload.aspect_ratio

                    if payload.size:

                        body["size"] = payload.size

                    if payload.resolution:

                        body["resolution"] = payload.resolution

                    if image_payload:

                        body["images"] = image_payload

                    if payload.videos:

                        body["videos"] = [v for v in payload.videos if v]

                    if payload.enhance_prompt:

                        body["enhance_prompt"] = True

                    if payload.enable_upsample:

                        body["enable_upsample"] = True

                    if payload.seed is not None:

                        body["seed"] = payload.seed

                    if payload.camerafixed:

                        body["camerafixed"] = True

                    if payload.return_last_frame:

                        body["return_last_frame"] = True

                    if payload.generate_audio:

                        body["generate_audio"] = True

            # --- 发起视频生成请求 ---

            raw = None

            html_response = None

            last_response = None

            last_json_error = None

            total_candidates = len(submit_urls)

            for idx, candidate_url in enumerate(submit_urls):

                submit_url = candidate_url

                is_last = idx == total_candidates - 1

                response = await client.post(submit_url, headers=api_headers(provider=provider), json=body)

                last_response = response

                if response.status_code >= 400:

                    # 404/405（或直接返回网页 HTML）通常表示该平台不支持这个端点路径——

                    # 例如有的站点只实现了统一格式的 /v2/videos/generations，而我们先试了 /v1。

                    # 这种情况要继续尝试下一个候选端点（关键修复：以前在这里直接 raise_for_status，

                    # 第一个 /v1 报错就抛出，永远轮不到 /v2，表现为“接口错误”）。

                    # 其它错误（模型不支持/时长/额度等请求被拒）说明端点是存在的，直接抛出交给外层友好提示。

                    endpoint_missing = response.status_code in (404, 405) or looks_like_html_response(response.text)

                    if endpoint_missing and not is_last:

                        continue

                    response.raise_for_status()

                try:

                    raw = response.json()

                    break

                except Exception as exc:

                    last_json_error = exc

                    if looks_like_html_response(response.text):

                        html_response = response

                        continue

                    if not is_last:

                        continue

                    resp_text = response.text[:500]

                    raise HTTPException(status_code=502, detail=f"上游视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}")

            if raw is None:

                resp = html_response or last_response

                status_code = getattr(resp, "status_code", 200)

                resp_text = (getattr(resp, "text", "") or "")[:500]

                raise HTTPException(

                    status_code=502,

                    detail=(

                        f"上游视频接口返回了网页 HTML，而不是 JSON（状态 {status_code}）。\n\n"

                        f"这通常表示 API 设置里的 Base URL 指到了第三方聚合平台的管理后台/网页入口，"

                        f"或该平台不支持当前视频接口路径。请确认 Base URL 是接口地址，例如以 /v1 结尾的 OpenAI 兼容地址，"

                        f"并确认该平台实际支持视频生成端点。\n\n原始响应：{resp_text}"

                    )

                ) from last_json_error

            task_id = extract_task_id(raw) or raw.get("task_id") or raw.get("id")

            result = raw

            if task_id and not video_output_urls(raw):

                result = await wait_for_video_task(client, provider, task_id, submit_url)

            urls = video_output_urls(result)

            if not urls:

                raise HTTPException(status_code=502, detail=f"视频生成成功但没有返回视频：{result}")

            local_urls = [await save_remote_video_to_output(url) for url in urls]

            return {"videos": local_urls, "task_id": task_id, "raw": result}

    except httpx.HTTPStatusError as exc:

        text = exc.response.text

        try:

            requested_model = body.get("model", "") or payload.model or ""

        except NameError:

            requested_model = payload.model or ""

        provider_name = provider.get('name') or provider['id']

        # 1) 模型名不在上游支持范围 → 从错误信息里抽取合法列表展示

        valid_models_match = re.search(r"not in\s*\[([^\]]+)\]", text)

        if valid_models_match:

            valid_models = [m.strip() for m in valid_models_match.group(1).split(",") if m.strip()]

            sample = valid_models[:30]

            more = f"（共 {len(valid_models)} 个，仅显示前 {len(sample)} 个）" if len(valid_models) > len(sample) else ""

            hint = (

                f"上游「{provider_name}」不识别模型「{requested_model}」。\n\n"

                f"上游支持的视频模型清单{more}：\n  {', '.join(sample)}\n\n"

                f"请到「API 设置」里把视频模型改成上面列表中的一个。"

            )

            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc

        # 2) 模型名合法但账号没开通通道

        if "channel not found" in text or "model_not_found" in text:

            hint = (

                f"上游「{provider_name}」识别了模型「{requested_model}」，但你的 API Key 账号下**没有该模型的可用通道**。\n\n"

                f"原因：你的账号没开通这个模型的访问权限（付费/订阅相关）。\n\n"

                f"解决方法：\n"

                f"  1. 登录 {provider.get('base_url') or '上游平台'} 控制台，开通该模型 / 充值；\n"

                f"  2. 或在「API 设置」里把视频模型改成你账号已开通的型号（如 veo3-fast / veo2-fast / sora-2 等）。"

            )

            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc

        if "text.duration" in text or "specified duration is not supported" in text:

            hint = (

                f"上游「{provider_name}」模型「{requested_model}」不支持当前时长参数。\n\n"

                f"不同视频模型支持的时长不一样；如果选择了模型不支持的时长，上游可能报错，"

                f"也可能自动按平台默认时长生成，例如 5 秒。\n\n"

                f"请把视频时长切回该模型支持的值，或改用支持更长时长的视频模型。"

            )

            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc

        if "audio duration" in text.lower():

            too_long = "less than or equal" in text.lower() or "15.2" in text

            bound_hint = "太长（超过 15.2 秒）" if too_long else "太短（不足 1.8 秒）"

            hint = (

                f"上游「{provider_name}」模型「{requested_model}」拒绝了参考音频：时长{bound_hint}。\n\n"

                f"方舟 Seedance 的参考音频时长必须在 1.8 ~ 15.2 秒之间，"

                f"请把音频裁剪到这个区间后再作为参考音频输入。"

            )

            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc

        if "inputimagesensitivecontentdetected" in text.lower() or "privacyinformation" in text.lower() or "may contain real person" in text.lower():

            hint = (

                f"上游「{provider_name}」拦截了输入参考图，原因是图片里可能包含真人身份/隐私信息。\n\n"

                f"这不是代码协议错误，而是火山视频模型的内容安全策略。\n\n"

                f"建议你这样处理：\n"

                f"  1. 改用非真人参考图，例如插画、AI 头像、商品图、场景图；\n"

                f"  2. 先把真人脸做模糊、遮挡、裁掉，或转成明显的二次元/插画风；\n"

                f"  3. 如果只是想做文生视频，先去掉参考图只保留文字提示词测试。"

            )

            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc

        raise HTTPException(status_code=exc.response.status_code, detail=f"上游视频接口错误：{text}") from exc

    except httpx.HTTPError as exc:

        log_net_error(f"视频 网络/TLS错误 provider={provider.get('id')} model={payload.model}", exc)

        raise HTTPException(status_code=502, detail=f"请求上游视频接口失败：{exc}") from exc



# --- Canvas LLM ---



@router.post("/api/canvas-llm")

async def canvas_llm(payload: CanvasLLMRequest):

    _provider = get_api_provider(payload.provider)

    if is_codex_provider(_provider):

        model = selected_model(payload.model, (_provider.get("chat_models") or CODEX_DEFAULT_CHAT_MODELS)[0])

        payload.model = model

        text, raw = await codex_chat_text(payload, payload.messages)

        return {"text": text, "model": model, "raw_usage": None, "raw": raw}

    if is_gemini_cli_provider(_provider):

        model = selected_model(payload.model, (_provider.get("chat_models") or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])

        payload.model = model

        text, raw = await gemini_cli_chat_text(payload, payload.messages)

        return {"text": text, "model": model, "raw_usage": None, "raw": raw}

    chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)

    # 判断协议：APIMart 异步 vs 标准 OpenAI

    _llm_provider = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}

    _is_apimart = is_apimart_provider(_llm_provider)

    system_prompt = (payload.system_prompt or "").strip()

    upstream_messages = [{"role": "system", "content": system_prompt}] if system_prompt else []

    for item in payload.messages[-MAX_HISTORY_MESSAGES:]:

        role = item.get("role")

        content = item.get("content")

        if role in {"user", "assistant"} and content:

            upstream_messages.append({"role": role, "content": content})

    # 构造用户消息：有图片/视频时用 OpenAI/Gemini 多模态格式

    image_inputs = [img for img in (payload.images or []) if is_image_reference_value(img)]

    video_inputs = [video for video in (payload.videos or []) if is_video_reference_value(video)]

    if image_inputs or video_inputs:

        content_parts = [{"type": "text", "text": payload.message}]

        ok_imgs = 0

        for img in image_inputs[:8]:

            if not img or not isinstance(img, str):

                continue

            ref_url = media_reference_to_url(img, max_image_size=1024)

            if not ref_url:

                continue

            content_parts.append({"type": "image_url", "image_url": {"url": ref_url}})

            ok_imgs += 1

        ok_videos = 0

        for video in video_inputs[:3]:

            if not video or not isinstance(video, str):

                continue

            frame_urls = await video_reference_to_frame_data_urls(video, max_frames=6, max_size=768)

            if frame_urls:

                ok_videos += 1

                content_parts.append({"type": "text", "text": f"以下是视频 {ok_videos} 按时间顺序抽取的关键帧，请结合这些画面理解视频内容。"})

                for frame_url in frame_urls:

                    content_parts.append({"type": "image_url", "image_url": {"url": frame_url}})

            else:

                ref_url = media_reference_to_url(video)

                if not ref_url:

                    continue

                content_parts.append({"type": "video_url", "video_url": {"url": ref_url}})

                ok_videos += 1

        print(f"[canvas-llm] model={model} provider={payload.provider} text_len={len(payload.message)} images={ok_imgs}/{len(payload.images)} videos={ok_videos}/{len(payload.videos)}")

        upstream_messages.append({"role": "user", "content": content_parts})

    else:

        upstream_messages.append({"role": "user", "content": payload.message})

    raw = None

    try:

        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:

            req_body = {"model": model, "messages": upstream_messages}

            if _is_apimart:

                req_body["stream"] = False   # APIMart 默认流式，强制关闭

            response = await client.post(

                f"{chat_base}/chat/completions",

                headers=chat_hdrs,

                json=req_body,

            )

            response.raise_for_status()

            if not response.content:

                raise HTTPException(status_code=502, detail="上游接口返回了空响应")

            raw = response.json()

    except httpx.HTTPStatusError as exc:

        body = exc.response.text or ""

        friendly = friendly_chat_error_detail(body, model, _llm_provider)

        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f"上游接口错误：{body}") from exc

    except httpx.HTTPError as exc:

        raise HTTPException(status_code=502, detail=f"请求上游接口失败：{exc}") from exc

    except HTTPException:

        raise

    except Exception as exc:

        raise HTTPException(status_code=502, detail=f"解析上游响应失败：{exc}") from exc

    try:

        text = text_from_chat_response(raw).strip() if isinstance(raw, dict) else ""

        text = text or "接口返回了空回复。"

    except Exception as exc:

        raise HTTPException(status_code=502, detail=f"解析回复内容失败：{exc}") from exc

    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else {}

    return {"text": text, "model": model, "raw_usage": raw_data.get("usage")}



# --- 对话管理 ---



# --- 画布管理 ---



@router.get("/api/canvases")

async def canvases():

    return {"canvases": list_canvases()}



@router.get("/api/projects")

async def get_projects():

    return {"projects": list_projects()}



@router.post("/api/projects")

async def create_project(payload: ProjectCreateRequest):

    return {"project": project_record(new_project(payload.name))}



@router.post("/api/projects/{project_id}")

async def update_project(project_id: str, payload: ProjectUpdateRequest):

    projects = ensure_default_project()

    target = next((p for p in projects if p.get("id") == project_id), None)

    if not target:

        raise HTTPException(status_code=404, detail="项目不存在")

    if payload.name is not None:

        target["name"] = (str(payload.name).strip() or target.get("name") or "未命名项目")[:60]

    if payload.order is not None:

        target["order"] = int(payload.order)

    target["updated_at"] = now_ms()

    save_projects(projects)

    return {"project": project_record(target)}



@router.delete("/api/projects/{project_id}")

async def delete_project(project_id: str):

    """删除项目：默认项目不可删除；其余项目删除后，其下画布回归默认项目（不删画布）。"""

    if project_id == DEFAULT_PROJECT_ID:

        raise HTTPException(status_code=400, detail="默认项目不可删除")

    projects = ensure_default_project()

    if not any(p.get("id") == project_id for p in projects):

        raise HTTPException(status_code=404, detail="项目不存在")

    projects = [p for p in projects if p.get("id") != project_id]

    save_projects(projects)

    # 把该项目下的画布迁回默认项目

    moved = 0

    with CANVAS_LOCK:

        for filename in os.listdir(CANVAS_DIR):

            if not filename.endswith(".json"):

                continue

            path = os.path.join(CANVAS_DIR, filename)

            try:

                with open(path, 'r', encoding='utf-8') as f:

                    data = json.load(f)

            except Exception:

                continue

            if str(data.get("project") or "") == project_id:

                data["project"] = DEFAULT_PROJECT_ID

                with open(path, 'w', encoding='utf-8') as f:

                    json.dump(data, f, ensure_ascii=False, indent=2)

                moved += 1

    return {"ok": True, "moved": moved}



@router.get("/api/canvases/trash")

async def trashed_canvases():

    return {"canvases": list_deleted_canvases(), "retention_days": 30}



@router.post("/api/canvases")

async def create_canvas(payload: CanvasCreateRequest):

    return {"canvas": new_canvas(payload.title, payload.icon, payload.kind, payload.project, payload.board_x, payload.board_y)}



@router.get("/api/canvases/{canvas_id}/meta")

async def get_canvas_meta(canvas_id: str):

    canvas = load_canvas(canvas_id)

    return {

        "id": canvas.get("id"),

        "updated_at": canvas.get("updated_at", 0),

        "title": canvas.get("title", "未命名画布"),

        "icon": canvas.get("icon", "layers"),

        "kind": normalize_canvas_kind(canvas.get("kind")),

    }



@router.post("/api/canvases/{canvas_id}/meta")

async def update_canvas_meta(canvas_id: str, payload: CanvasMetaUpdate):

    """更新画布的轻量元数据（标题/图标/负责人/颜色/置顶）。

    刻意不走 save_canvas（它会刷新 updated_at），以免打标签/置顶把画布顶到列表最前。"""

    canvas = load_canvas(canvas_id)

    if payload.title is not None:

        canvas["title"] = (payload.title or canvas.get("title") or "未命名画布")[:80]

    if payload.icon is not None:

        canvas["icon"] = (payload.icon or "layers")[:32]

    if payload.owner is not None:

        canvas["owner"] = str(payload.owner).strip()[:40]

    if payload.color is not None:

        canvas["color"] = normalize_canvas_color(payload.color)

    if payload.pinned is not None:

        canvas["pinned"] = bool(payload.pinned)

    if payload.project is not None:

        canvas["project"] = str(payload.project).strip() or DEFAULT_PROJECT_ID

    if payload.board_x is not None:

        canvas["board_x"] = float(payload.board_x)

    if payload.board_y is not None:

        canvas["board_y"] = float(payload.board_y)

    with CANVAS_LOCK:

        with open(canvas_path(canvas["id"]), 'w', encoding='utf-8') as f:

            json.dump(canvas, f, ensure_ascii=False, indent=2)

    return {"canvas": canvas_record(canvas)}



@router.get("/api/canvases/{canvas_id}")

async def get_canvas(canvas_id: str):

    return {"canvas": load_canvas(canvas_id)}



@router.post("/api/canvases/{canvas_id}/touch")

async def touch_canvas(canvas_id: str):

    canvas = load_canvas(canvas_id)

    save_canvas(canvas)

    return {"canvas": canvas_record(canvas), "updated_at": canvas.get("updated_at", 0)}



@router.get("/api/canvas-assets")

async def list_canvas_assets():

    # canvas_assets_index 会同步遍历并解析所有画布 JSON，放进线程池避免阻塞事件循环

    # （否则画布多时一次请求就会卡住整个 asyncio loop，连 WebSocket 一起掉线）。

    return await asyncio.to_thread(canvas_assets_index)



@router.post("/api/canvas-assets/check")

async def check_canvas_assets(payload: CanvasAssetCheckRequest):

    result = {}

    for url in payload.urls[:3000]:

        text = str(url or "").strip()

        if not text:

            continue

        if text.startswith("/output/") or text.startswith("/assets/"):

            result[text] = bool(output_file_from_url(text))

        else:

            result[text] = True

    return {"exists": result}



@router.post("/api/canvas-assets/download")

async def download_canvas_assets(payload: CanvasAssetDownloadRequest):

    buffer = BytesIO()

    used_names = set()

    count = 0

    raw_items = payload.items or [{"url": url} for url in payload.urls]

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:

        for raw in raw_items[:1000]:

            if isinstance(raw, dict):

                text = str(raw.get("url") or "").strip()

                requested_name = str(raw.get("name") or "").strip()

            else:

                text = str(raw or "").strip()

                requested_name = ""

            if not text:

                continue

            path = output_file_from_url(text)

            content = None

            content_type = ""

            if path and os.path.isfile(path):

                base = sanitize_export_filename(requested_name or os.path.basename(path), os.path.basename(path) or f"image-{count + 1}.png")

            else:

                local_by_name = local_media_file_by_basename(filename_from_media_url(text, ""))

                if local_by_name and os.path.isfile(local_by_name):

                    path = local_by_name

                    base = sanitize_export_filename(requested_name or os.path.basename(path), os.path.basename(path) or f"image-{count + 1}.png")

                else:

                    try:

                        remote = fetch_remote_media_bytes(text)

                    except Exception:

                        remote = None

                    if not remote:

                        continue

                    content, content_type = remote

                    base = sanitize_export_filename(requested_name or filename_from_media_url(text, f"image-{count + 1}.bin"), f"image-{count + 1}.bin")

            name, ext = os.path.splitext(base)

            archive_name = base

            suffix = 2

            while archive_name in used_names:

                archive_name = f"{name}-{suffix}{ext}"

                suffix += 1

            used_names.add(archive_name)

            if path and os.path.isfile(path):

                zf.write(path, archive_name)

            else:

                zf.writestr(archive_name, content)

            count += 1

    if count <= 0:

        raise HTTPException(status_code=404, detail="没有可下载的本地图片")

    buffer.seek(0)

    filename = re.sub(r'[\\/:*?"<>|]+', "_", payload.filename or "canvas-output-images.zip")

    if not filename.lower().endswith(".zip"):

        filename += ".zip"

    encoded = urllib.parse.quote(filename)

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"}

    return Response(buffer.getvalue(), media_type="application/zip", headers=headers)



@router.post("/api/canvas-workflows/export")

async def export_canvas_workflow(payload: CanvasWorkflowExportRequest):

    archive, _ = build_canvas_workflow_archive(payload)

    filename = sanitize_export_filename(payload.filename or "canvas-workflow.zip", "canvas-workflow.zip")

    if not filename.lower().endswith(".zip"):

        filename += ".zip"

    encoded = urllib.parse.quote(filename)

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"}

    return Response(archive, media_type="application/zip", headers=headers)



@router.post("/api/canvas-workflows/export-to-library")

async def export_canvas_workflow_to_library(payload: CanvasWorkflowExportRequest):

    archive, meta = build_canvas_workflow_archive(payload)

    filename = sanitize_export_filename(payload.filename or "canvas-workflow.zip", "canvas-workflow.zip")

    if not filename.lower().endswith(".zip"):

        filename += ".zip"

    lib = load_asset_library()

    _, cat = asset_library_workflow_category(lib, payload.library_id, payload.category_id)

    item = make_workflow_library_item_from_bytes(archive, filename, payload.name or os.path.splitext(filename)[0])

    item["node_count"] = meta.get("node_count") or len(payload.nodes or [])

    item["connection_count"] = meta.get("connection_count") or len(payload.connections or [])

    item["resource_count"] = len(meta.get("resources") or [])

    cat.setdefault("items", []).append(item)

    save_asset_library(lib)

    return {"library": lib, "item": item}



@router.post("/api/asset-library/workflows/upload")

async def upload_asset_library_workflows(

    files: List[UploadFile] = File(...),

    library_id: str = Form(""),

    category_id: str = Form(""),

):

    lib = load_asset_library()

    _, cat = asset_library_workflow_category(lib, library_id, category_id)

    added = []

    for file in files[:100]:

        raw = await file.read()

        filename = file.filename or "canvas-workflow.zip"

        lower = filename.lower()

        if not (lower.endswith(".json") or lower.endswith(".zip") or raw[:2] == b"PK"):

            continue

        item = make_workflow_library_item_from_bytes(raw, filename, os.path.splitext(filename)[0])

        cat.setdefault("items", []).append(item)

        added.append(item)

    if not added:

        raise HTTPException(status_code=400, detail="没有可上传的工作流文件")

    save_asset_library(lib)

    return {"library": lib, "items": added}



@router.post("/api/canvas-workflows/import")

async def import_canvas_workflow(file: UploadFile = File(...)):

    raw = await file.read()

    if not raw:

        raise HTTPException(status_code=400, detail="文件为空")

    name = str(file.filename or "").lower()

    resource_mapping = {}

    workflow = None

    try:

        if name.endswith(".zip") or raw[:2] == b"PK":

            with zipfile.ZipFile(BytesIO(raw), "r") as zf:

                candidates = [n for n in zf.namelist() if n.lower().endswith("workflow.json")]

                workflow_name = "workflow.json" if "workflow.json" in zf.namelist() else (candidates[0] if candidates else "")

                if not workflow_name:

                    raise HTTPException(status_code=400, detail="压缩包中没有 workflow.json")

                workflow = json.loads(zf.read(workflow_name).decode("utf-8-sig"))

                stamp = time.strftime("%Y%m%d-%H%M%S")

                import_dir = os.path.join(core.OUTPUT_INPUT_DIR, f"workflow_import_{stamp}_{uuid.uuid4().hex[:6]}")

                os.makedirs(import_dir, exist_ok=True)

                for res in workflow.get("resources") or []:

                    archive = str(res.get("archive") or "").replace("\\", "/").lstrip("/")

                    if not archive or archive not in zf.namelist():

                        continue

                    base = sanitize_export_filename(res.get("name") or os.path.basename(archive), os.path.basename(archive) or "resource.bin")

                    target = os.path.join(import_dir, f"{uuid.uuid4().hex[:8]}_{base}")

                    with zf.open(archive) as src, open(target, "wb") as dst:

                        shutil.copyfileobj(src, dst)

                    rel = os.path.relpath(target, ASSETS_DIR).replace("\\", "/")

                    new_url = f"/assets/{rel}"

                    old_url = str(res.get("url") or "").strip()

                    if old_url:

                        resource_mapping[old_url] = new_url

                    resource_mapping[archive] = new_url

                    resource_mapping[f"./{archive}"] = new_url

                    resource_mapping[os.path.basename(archive)] = new_url

        else:

            workflow = json.loads(raw.decode("utf-8-sig"))

    except HTTPException:

        raise

    except zipfile.BadZipFile as exc:

        raise HTTPException(status_code=400, detail="无法读取压缩包") from exc

    except Exception as exc:

        raise HTTPException(status_code=400, detail=f"无法解析工作流文件：{exc}") from exc

    if isinstance(workflow, list):

        workflow = {"nodes": workflow, "connections": []}

    if not isinstance(workflow, dict):

        raise HTTPException(status_code=400, detail="工作流格式不正确")

    nodes_payload = workflow.get("nodes")

    connections_payload = workflow.get("connections")

    if nodes_payload is None and isinstance(workflow.get("workflow"), dict):

        nodes_payload = workflow["workflow"].get("nodes")

        connections_payload = workflow["workflow"].get("connections")

    if not isinstance(nodes_payload, list):

        raise HTTPException(status_code=400, detail="工作流 JSON 缺少 nodes")

    if not isinstance(connections_payload, list):

        connections_payload = []

    if resource_mapping:

        nodes_payload = canvas_workflow_replace_strings(nodes_payload, resource_mapping)

        connections_payload = canvas_workflow_replace_strings(connections_payload, resource_mapping)

    return {

        "workflow": canvas_workflow_payload(nodes_payload, connections_payload, workflow.get("resources") or []),

        "nodes": nodes_payload,

        "connections": connections_payload,

        "resource_map": resource_mapping,

    }



@router.get("/api/asset-library")

async def get_asset_library():

    return {"library": load_asset_library()}



@router.get("/api/prompt-libraries")

async def get_prompt_libraries():

    return {"library": public_prompt_libraries()}



@router.post("/api/prompt-libraries")

async def create_prompt_library(payload: PromptLibraryRequest):

    data = load_prompt_libraries()

    library = {

        "id": f"lib_{uuid.uuid4().hex[:12]}",

        "name": sanitize_asset_name(payload.name, "提示词库"),

        "type": "prompt",

        "categories": [],

        "items": [],

    }

    data.setdefault("libraries", []).append(library)

    data["active_library_id"] = library["id"]

    data = save_prompt_libraries(data)

    new_lib = next((lib for lib in data.get("libraries", []) if lib.get("id") == library["id"]), library)

    return {"library": public_prompt_libraries(data), "prompt_library": new_lib}



@router.patch("/api/prompt-libraries/{library_id}")

async def rename_prompt_library(library_id: str, payload: PromptLibraryRequest):

    data = load_prompt_libraries()

    library = find_prompt_library(data, library_id)

    if not library or library.get("id") != library_id:

        raise HTTPException(status_code=404, detail="提示词库不存在")

    library["name"] = sanitize_asset_name(payload.name, library.get("name") or "提示词库")

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data), "prompt_library": library}



@router.delete("/api/prompt-libraries/{library_id}")

async def delete_prompt_library(library_id: str):

    if library_id == "system":

        raise HTTPException(status_code=400, detail="系统提示词库不能删除，可以删除其中的提示词")

    data = load_prompt_libraries()

    libraries = data.get("libraries", []) or []

    kept = [lib for lib in libraries if lib.get("id") != library_id]

    if len(kept) == len(libraries):

        raise HTTPException(status_code=404, detail="提示词库不存在")

    data["libraries"] = kept

    if data.get("active_library_id") == library_id:

        data["active_library_id"] = "system"

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data)}



@router.post("/api/prompt-libraries/items")

async def add_prompt_library_item(payload: PromptLibraryItemRequest):

    data = load_prompt_libraries()

    library = find_prompt_library(data, payload.library_id)

    if not library:

        raise HTTPException(status_code=404, detail="提示词库不存在")

    if not str(payload.positive or "").strip():

        raise HTTPException(status_code=400, detail="提示词内容不能为空")

    item = normalize_prompt_library_item({

        "id": f"tpl_{uuid.uuid4().hex[:12]}",

        "name": payload.name,

        "category": payload.category,

        "positive": payload.positive,

        "negative": payload.negative,

        "scene": payload.scene,

        "created_at": now_ms(),

        "updated_at": now_ms(),

    })

    library.setdefault("items", []).insert(0, item)

    data["active_library_id"] = library.get("id") or data.get("active_library_id")

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data), "item": item}



@router.patch("/api/prompt-libraries/items/{item_id}")

async def update_prompt_library_item(item_id: str, payload: PromptLibraryItemRequest):

    data = load_prompt_libraries()

    for library in data.get("libraries", []) or []:

        if payload.library_id and library.get("id") != payload.library_id:

            continue

        for index, item in enumerate(library.get("items", []) or []):

            if item.get("id") == item_id:

                next_item = normalize_prompt_library_item({

                    **item,

                    "name": payload.name or item.get("name"),

                    "category": payload.category or item.get("category"),

                    "positive": payload.positive or item.get("positive"),

                    "negative": payload.negative,

                    "scene": payload.scene,

                    "updated_at": now_ms(),

                })

                library["items"][index] = next_item

                data = save_prompt_libraries(data)

                return {"library": public_prompt_libraries(data), "item": next_item}

    raise HTTPException(status_code=404, detail="提示词不存在")



@router.delete("/api/prompt-libraries/items/{item_id}")

async def delete_prompt_library_item(item_id: str):

    data = load_prompt_libraries()

    removed = None

    for library in data.get("libraries", []) or []:

        keep = []

        for item in library.get("items", []) or []:

            if item.get("id") == item_id:

                removed = item

            else:

                keep.append(item)

        library["items"] = keep

    if not removed:

        raise HTTPException(status_code=404, detail="提示词不存在")

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data), "removed": 1}



@router.post("/api/prompt-libraries/items/delete")

async def batch_delete_prompt_library_items(payload: PromptLibraryBatchDeleteRequest):

    ids = {str(item) for item in (payload.ids or []) if str(item)}

    if not ids:

        raise HTTPException(status_code=400, detail="没有选择提示词")

    data = load_prompt_libraries()

    removed = 0

    for library in data.get("libraries", []) or []:

        keep = []

        for item in library.get("items", []) or []:

            if item.get("id") in ids:

                removed += 1

            else:

                keep.append(item)

        library["items"] = keep

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data), "removed": removed}



@router.post("/api/prompt-libraries/categories")

async def add_prompt_library_category(payload: PromptLibraryCategoryRequest):

    data = load_prompt_libraries()

    library = find_prompt_library(data, payload.library_id) or find_prompt_library(data, "system")

    if not library:

        raise HTTPException(status_code=404, detail="提示词库不存在")

    name = sanitize_asset_name(payload.name, "新分组")

    existing = {str(c.get("id")) for c in (library.get("categories") or []) if isinstance(c, dict)} | PROMPT_BUILTIN_CATEGORY_IDS

    cat_id = f"pcat_{uuid.uuid4().hex[:10]}"

    while cat_id in existing:

        cat_id = f"pcat_{uuid.uuid4().hex[:10]}"

    category = {"id": cat_id, "name": name}

    library.setdefault("categories", []).append(category)

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data), "category": category}



@router.patch("/api/prompt-libraries/categories/{category_id}")

async def rename_prompt_library_category(category_id: str, payload: PromptLibraryCategoryRequest):

    # 系统库（内置）分组也允许重命名：分组的 id 不变，只改显示名，

    # 这样画布与素材库管理共用同一份分组数据，重命名两端实时同步。

    name = sanitize_asset_name(payload.name, "")

    if not name:

        raise HTTPException(status_code=400, detail="分组名称不能为空")

    data = load_prompt_libraries()

    updated = False

    for library in data.get("libraries", []) or []:

        for cat in library.get("categories") or []:

            if isinstance(cat, dict) and cat.get("id") == category_id:

                cat["name"] = name

                updated = True

    if not updated:

        raise HTTPException(status_code=404, detail="分组不存在")

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data)}



@router.delete("/api/prompt-libraries/categories/{category_id}")

async def delete_prompt_library_category(category_id: str):

    # 系统库（内置）分组也允许删除，与素材库管理/画布保持一致。

    data = load_prompt_libraries()

    found = False

    for library in data.get("libraries", []) or []:

        cats = library.get("categories") or []

        kept = [c for c in cats if not (isinstance(c, dict) and c.get("id") == category_id)]

        if len(kept) != len(cats):

            found = True

            library["categories"] = kept

            # 被删分组下的条目改挂到剩余的第一个分组；若已无分组则归到“未分类”。

            fallback = next((str(c.get("id")) for c in kept if isinstance(c, dict) and c.get("id")), "")

            for item in library.get("items", []) or []:

                if isinstance(item, dict) and item.get("category") == category_id:

                    item["category"] = fallback

    if not found:

        raise HTTPException(status_code=404, detail="分组不存在")

    data = save_prompt_libraries(data)

    return {"library": public_prompt_libraries(data)}



@router.post("/api/asset-library/libraries")

async def create_asset_library(payload: AssetLibraryRequest):

    lib = load_asset_library()

    library = {"id": f"lib_{uuid.uuid4().hex[:12]}", "name": sanitize_asset_name(payload.name, "资产库"), "type": "asset", "categories": []}

    library["categories"].append({"id": f"cat_{uuid.uuid4().hex[:12]}", "name": "默认分组", "type": "image", "items": []})

    library["categories"].append({"id": f"wf_{uuid.uuid4().hex[:12]}", "name": "工作流", "type": "workflow", "items": []})

    lib.setdefault("libraries", []).append(library)

    lib["active_library_id"] = library["id"]

    save_asset_library(lib)

    return {"library": lib, "asset_library": library}



@router.patch("/api/asset-library/libraries/{library_id}")

async def rename_asset_library(library_id: str, payload: AssetLibraryRenameRequest):

    lib = load_asset_library()

    library = find_asset_library(lib, library_id)

    if not library or library.get("id") != library_id:

        raise HTTPException(status_code=404, detail="资产库不存在")

    library["name"] = sanitize_asset_name(payload.name, library.get("name") or "资产库")

    save_asset_library(lib)

    return {"library": lib, "asset_library": library}



@router.delete("/api/asset-library/libraries/{library_id}")

async def delete_asset_library(library_id: str):

    lib = load_asset_library()

    libraries = lib.get("libraries") or []

    if len(libraries) <= 1:

        raise HTTPException(status_code=400, detail="至少保留一个资产库")

    if not any(item.get("id") == library_id for item in libraries):

        raise HTTPException(status_code=404, detail="资产库不存在")

    lib["libraries"] = [item for item in libraries if item.get("id") != library_id]

    if lib.get("active_library_id") == library_id:

        lib["active_library_id"] = lib["libraries"][0].get("id")

    save_asset_library(lib)

    return {"library": lib}



@router.post("/api/asset-library/categories")

async def create_asset_library_category(payload: AssetLibraryCategoryRequest):

    lib = load_asset_library()

    library = find_asset_library(lib, payload.library_id)

    if not library:

        raise HTTPException(status_code=404, detail="资产库不存在")

    cat_type = "workflow" if str(payload.type or "").lower() == "workflow" else "image"

    category = {"id": f"cat_{uuid.uuid4().hex[:12]}", "name": sanitize_asset_name(payload.name, "新文件夹"), "type": cat_type, "items": []}

    if cat_type == "image":

        # 图片分组在 library/ 下建一个真实文件夹，之后该分组的资产都存进这个文件夹，便于在磁盘上管理。

        category["dir"] = unique_asset_category_dir(library, payload.name)

        try:

            os.makedirs(os.path.join(ASSET_LIBRARY_DIR, category["dir"]), exist_ok=True)

        except Exception as exc:

            print(f"创建分组文件夹失败: {exc}")

    library.setdefault("categories", []).append(category)

    lib["active_library_id"] = library.get("id") or lib.get("active_library_id")

    save_asset_library(lib)

    return {"library": lib, "category": category}



@router.patch("/api/asset-library/categories/{category_id}")

async def rename_asset_library_category(category_id: str, payload: AssetLibraryRenameRequest):

    lib = load_asset_library()

    _, cat = find_asset_category_with_library(lib, category_id, payload.library_id)

    if not cat:

        raise HTTPException(status_code=404, detail="分类不存在")

    cat["name"] = sanitize_asset_name(payload.name, cat.get("name") or "新文件夹")

    save_asset_library(lib)

    return {"library": lib, "category": cat}



@router.delete("/api/asset-library/categories/{category_id}")

async def delete_asset_library_category(category_id: str, library_id: str = ""):

    lib = load_asset_library()

    library, cat = find_asset_category_with_library(lib, category_id, library_id)

    if not cat:

        raise HTTPException(status_code=404, detail="分类不存在")

    if cat.get("type") == "workflow" and category_id == "workflows" and (library.get("id") or "") == "default":

        raise HTTPException(status_code=400, detail="默认工作流分类不能删除")

    # 删除分组时一并清理该分组下的本地文件 + 分组文件夹，避免磁盘残留。

    for item in (cat.get("items") or []):

        remove_asset_library_file(item)

    cat_dir = str(cat.get("dir") or "").strip("/").strip()

    if cat_dir:

        try:

            target = os.path.join(ASSET_LIBRARY_DIR, cat_dir)

            if os.path.isdir(target) and os.path.abspath(target).startswith(os.path.abspath(ASSET_LIBRARY_DIR) + os.sep):

                shutil.rmtree(target, ignore_errors=True)

        except Exception as exc:

            print(f"删除分组文件夹失败: {exc}")

    library["categories"] = [c for c in library.get("categories", []) if c.get("id") != category_id]

    save_asset_library(lib)

    return {"library": lib}



@router.post("/api/asset-library/items")

async def add_asset_library_item(payload: AssetLibraryAddRequest):

    lib = load_asset_library()

    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)

    if not cat:

        raise HTTPException(status_code=404, detail="分类不存在")

    if cat.get("type") != "image":

        raise HTTPException(status_code=400, detail="该分类暂不支持添加媒体")

    src = output_file_from_url(payload.url)

    if not src:

        raise HTTPException(status_code=400, detail="只支持保存本地 /assets 或 /output 媒体")

    _, item = make_asset_library_item(src, payload.name or os.path.basename(src), subdir=cat.get("dir") or "")

    if item.get("kind") == "image":

        classification = await classify_asset_image_best_effort(output_file_from_url(item.get("url") or "") or src)

        if classification:

            item["classification"] = classification

    cat.setdefault("items", []).append(item)

    save_asset_library(lib)

    return {"library": lib, "item": item}



@router.post("/api/asset-library/items/batch")

async def batch_add_asset_library_items(payload: AssetLibraryBatchAddRequest):

    added = []

    lib = load_asset_library()

    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)

    if not cat:

        raise HTTPException(status_code=404, detail="分类不存在")

    if cat.get("type") != "image":

        raise HTTPException(status_code=400, detail="该分类暂不支持添加媒体")

    for entry in (payload.items or [])[:200]:

        entry.category_id = payload.category_id

        entry.library_id = payload.library_id

        src = output_file_from_url(entry.url)

        if not src:

            continue

        _, item = make_asset_library_item(src, entry.name or os.path.basename(src), subdir=cat.get("dir") or "")

        if item.get("kind") == "image":

            classification = await classify_asset_image_best_effort(output_file_from_url(item.get("url") or "") or src)

            if classification:

                item["classification"] = classification

        cat.setdefault("items", []).append(item)

        added.append(item)

    save_asset_library(lib)

    return {"library": lib, "items": added}



@router.get("/api/shared-folders")

async def list_shared_folders():

    data = shared_folders_load()

    folders = []

    for entry in data.get("folders", []):

        abs_path = shared_folder_abs(entry)

        folders.append({

            "id": entry.get("id"),

            "name": entry.get("name") or os.path.basename(abs_path) or abs_path,

            "rel": entry.get("rel") or "",

            "path": abs_path,

            "exists": os.path.isdir(abs_path),

            "created_at": entry.get("created_at"),

        })

    return {"folders": folders}



@router.post("/api/shared-folders")

async def register_shared_folder(payload: SharedFolderRegister):

    abs_path, rel = shared_resolve_register(payload.path)

    name = sanitize_asset_name(payload.name or os.path.basename(abs_path), "共享文件夹")

    with SHARED_FOLDERS_LOCK:

        data = shared_folders_load()

        for entry in data.get("folders", []):

            if os.path.normpath(shared_folder_abs(entry)) == os.path.normpath(abs_path):

                entry["name"] = name

                shared_folders_save(data)

                return {"folder": {**entry, "path": abs_path, "exists": True}}

        entry = {

            "id": f"shared_{uuid.uuid4().hex[:12]}",

            "name": name,

            "rel": rel,

            "created_at": now_ms(),

        }

        data.setdefault("folders", []).append(entry)

        shared_folders_save(data)

    return {"folder": {**entry, "path": abs_path, "exists": True}}



@router.delete("/api/shared-folders/{folder_id}")

async def unregister_shared_folder(folder_id: str):

    with SHARED_FOLDERS_LOCK:

        data = shared_folders_load()

        before = len(data.get("folders", []))

        data["folders"] = [f for f in data.get("folders", []) if f.get("id") != folder_id]

        if len(data["folders"]) == before:

            raise HTTPException(status_code=404, detail="共享文件夹不存在")

        shared_folders_save(data)

    return {"ok": True}



@router.get("/api/shared-folders/{folder_id}/tree")

async def get_shared_folder_tree(folder_id: str):

    entry = shared_folder_by_id(folder_id)

    if not entry:

        raise HTTPException(status_code=404, detail="共享文件夹不存在")

    abs_path = shared_folder_abs(entry)

    if not os.path.isdir(abs_path):

        raise HTTPException(status_code=404, detail="文件夹已不存在")

    tree = scan_shared_tree(folder_id, abs_path, "", entry.get("name") or os.path.basename(abs_path))

    return {"folder": {"id": folder_id, "name": entry.get("name"), "path": abs_path}, "tree": tree}



@router.get("/api/shared-folders/{folder_id}/file")

async def get_shared_folder_file(folder_id: str, path: str = ""):

    entry = shared_folder_by_id(folder_id)

    if not entry:

        raise HTTPException(status_code=404, detail="共享文件夹不存在")

    folder_abs = shared_folder_abs(entry)

    abs_path = shared_child_abs(folder_abs, path)

    if not os.path.isfile(abs_path):

        raise HTTPException(status_code=404, detail="文件不存在")

    ext = os.path.splitext(abs_path)[1].lower()

    if ext not in SHARED_MEDIA_EXTS:

        raise HTTPException(status_code=400, detail="不支持的文件类型")

    return FileResponse(abs_path, media_type=content_type_for_path(abs_path))



@router.post("/api/shared-folders/import")

async def import_shared_folder_files(payload: SharedFolderImport):

    entry = shared_folder_by_id(payload.folder_id)

    if not entry:

        raise HTTPException(status_code=404, detail="共享文件夹不存在")

    folder_abs = shared_folder_abs(entry)

    lib = load_asset_library()

    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)

    if not cat:

        raise HTTPException(status_code=404, detail="分类不存在")

    if cat.get("type") != "image":

        raise HTTPException(status_code=400, detail="该分类暂不支持添加媒体")

    added = []

    for rel in (payload.paths or [])[:200]:

        abs_path = shared_child_abs(folder_abs, rel)

        if not os.path.isfile(abs_path):

            continue

        ext = os.path.splitext(abs_path)[1].lower()

        if ext not in SHARED_MEDIA_EXTS:

            continue

        _, item = make_asset_library_item(abs_path, os.path.basename(abs_path), subdir=cat.get("dir") or "")

        if item.get("kind") == "image":

            classification = await classify_asset_image_best_effort(output_file_from_url(item.get("url") or "") or abs_path)

            if classification:

                item["classification"] = classification

        cat.setdefault("items", []).append(item)

        added.append(item)

    save_asset_library(lib)

    return {"library": lib, "items": added}



@router.patch("/api/asset-library/items/{item_id}")

async def rename_asset_library_item(item_id: str, payload: AssetLibraryRenameRequest):

    lib = load_asset_library()

    for library in lib.get("libraries", []):

        for cat in library.get("categories", []):

            for item in cat.get("items", []):

                if item.get("id") == item_id:

                    item["name"] = sanitize_asset_name(payload.name, item.get("name") or "asset")

                    save_asset_library(lib)

                    return {"library": lib, "item": item}

    raise HTTPException(status_code=404, detail="资产不存在")



@router.post("/api/asset-library/items/classify")

async def classify_asset_library_items(payload: AssetLibraryClassifyRequest):

    lib = load_asset_library()

    results = []

    changed = False

    for item_id in (payload.ids or [])[:80]:

        item = find_asset_item_in_library(lib, item_id, payload.library_id)

        result = {"id": item_id, "ok": False, "classification": None, "error": ""}

        if not item:

            result["error"] = "资产不存在"

            results.append(result)

            continue

        if asset_library_media_kind(item.get("url") or "") != "image" and item.get("kind") != "image":

            result["error"] = "仅支持图片素材智能分类"

            results.append(result)

            continue

        path = output_file_from_url(item.get("url") or "")

        if not path or not os.path.isfile(path):

            result["error"] = "文件不存在"

            results.append(result)

            continue

        try:

            classification = await classify_image_with_provider(path, payload.provider, payload.model, payload.ms_model, payload.prompt)

            item["classification"] = classification

            changed = True

            result.update({"ok": True, "classification": classification})

        except Exception as exc:

            result["error"] = str(getattr(exc, "detail", "") or exc)

        results.append(result)

    if changed:

        save_asset_library(lib)

    return {"library": lib, "count": sum(1 for item in results if item.get("ok")), "items": results}



@router.post("/api/asset-library/items/{item_id}/register-avatar")

async def register_asset_library_avatar(item_id: str, payload: AssetAvatarRegisterRequest):

    lib = load_asset_library()

    target_item = find_asset_item_in_library(lib, item_id, payload.library_id)

    if not target_item:

        raise HTTPException(status_code=404, detail="资产不存在")

    provider = get_api_provider(payload.provider_id)

    platform = avatar_platform_for_provider(provider)

    if platform not in AVATAR_SUPPORTED_PLATFORMS:

        name = (provider or {}).get("name") or (provider or {}).get("id") or "该平台"

        raise HTTPException(status_code=400, detail=f"「{name}」暂不支持数字人/真人认证（目前仅 APIMart 可用，火山等平台待接入官方资产 API）。")

    kind = str(target_item.get("kind") or "image").lower()

    if kind not in ("image", "video", "audio"):

        kind = "image"

    if platform == "apimart":

        project_name = str(payload.project_name or "default").strip() or "default"

        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:

            public_url = await upload_media_for_apimart(client, provider, target_item.get("url") or "", kind)

        if not valid_apimart_video_image_input(public_url):

            reason = public_url[4:] if isinstance(public_url, str) and public_url.startswith("ERR:") else "无法获取公网可访问地址"

            raise HTTPException(status_code=400, detail=f"素材无法提交到 APIMart：{reason}\n请配置 PUBLIC_BASE_URL，或确认本地文件存在。")

        task_id = await submit_apimart_avatar_asset(

            provider, public_url, target_item.get("name") or "asset", kind,

            project_name=project_name, group_name=payload.group_name,

        )

    elif platform == "volcengine":

        # 火山以 API 设置里配置的 ProjectName 为准（必须与视频生成 key 的项目一致）

        project_name = str(provider.get("volcengine_project_name") or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME

        public_url = volcengine_public_asset_url(target_item.get("url") or "")

        if public_url.startswith("ERR:"):

            raise HTTPException(status_code=400, detail=public_url[4:])

        task_id = await submit_volcengine_avatar_asset(

            public_url, target_item.get("name") or "asset", kind,

            project_name=project_name, group_name=payload.group_name or "",

        )

    else:

        raise HTTPException(status_code=400, detail="该平台的认证后端尚未接入。")

    regs = target_item.get("registrations")

    if not isinstance(regs, dict):

        regs = {}

    regs[platform] = {

        "provider_id": provider["id"],

        "project_name": project_name,

        "task_id": task_id,

        "status": "Processing",

        "detail": "已提交，审核中",

        "asset_uri": "",

        "asset_id": "",

        "registered_at": now_ms(),

    }

    target_item["registrations"] = regs

    save_asset_library(lib)

    return {"library": lib, "item": target_item}



@router.post("/api/asset-library/items/{item_id}/avatar-status")

async def check_asset_library_avatar(item_id: str, payload: AssetAvatarRegisterRequest):

    lib = load_asset_library()

    target_item = find_asset_item_in_library(lib, item_id, payload.library_id)

    if not target_item:

        raise HTTPException(status_code=404, detail="资产不存在")

    regs = target_item.get("registrations") if isinstance(target_item.get("registrations"), dict) else {}

    provider = get_api_provider(payload.provider_id or "")

    platform = avatar_platform_for_provider(provider)

    if platform not in AVATAR_SUPPORTED_PLATFORMS:

        raise HTTPException(status_code=400, detail="该平台暂不支持数字人/真人认证审核。")

    reg = regs.get(platform) if isinstance(regs.get(platform), dict) else {}

    task_id = str(reg.get("task_id") or "").strip()

    if not task_id:

        raise HTTPException(status_code=400, detail="该素材还没有提交到这个平台的认证审核。")

    if platform == "apimart":

        result = await check_apimart_avatar_task(provider, task_id)

    elif platform == "volcengine":

        result = await check_volcengine_avatar_task(

            task_id, str(reg.get("project_name") or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME,

        )

    else:

        raise HTTPException(status_code=400, detail="该平台的认证后端尚未接入。")

    reg["status"] = result["status"]

    reg["detail"] = result.get("detail") or ""

    if result["status"] == "Active" and result.get("asset_uri"):

        reg["asset_uri"] = result["asset_uri"]

        reg["asset_id"] = result["asset_uri"].replace("asset://", "")

    regs[platform] = reg

    target_item["registrations"] = regs

    save_asset_library(lib)

    return {"library": lib, "item": target_item}



@router.delete("/api/asset-library/items/{item_id}")

async def delete_asset_library_item(item_id: str):

    lib = load_asset_library()

    removed = None

    for library in lib.get("libraries", []):

        for cat in library.get("categories", []):

            keep = []

            for item in cat.get("items", []):

                if item.get("id") == item_id:

                    removed = item

                else:

                    keep.append(item)

            cat["items"] = keep

    if not removed:

        raise HTTPException(status_code=404, detail="资产不存在")

    remove_asset_library_file(removed)  # 同时删除本地文件，避免磁盘上堆积

    save_asset_library(lib)

    return {"library": lib}



@router.post("/api/asset-library/items/delete")

async def batch_delete_asset_library_items(payload: AssetLibraryBatchDeleteRequest):

    ids = {str(item) for item in (payload.ids or []) if str(item)}

    if not ids:

        raise HTTPException(status_code=400, detail="没有选择资产")

    lib = load_asset_library()

    removed = 0

    removed_items = []

    for library in lib.get("libraries", []):

        if payload.library_id and library.get("id") != payload.library_id:

            continue

        for cat in library.get("categories", []):

            keep = []

            for item in cat.get("items", []):

                if item.get("id") in ids:

                    removed += 1

                    removed_items.append(item)

                else:

                    keep.append(item)

            cat["items"] = keep

    for item in removed_items:  # 批量删除同时清理本地文件

        remove_asset_library_file(item)

    save_asset_library(lib)

    return {"library": lib, "removed": removed}



@router.post("/api/asset-library/items/move")

async def batch_move_asset_library_items(payload: AssetLibraryBatchMoveRequest):

    ids = {str(item) for item in (payload.ids or []) if str(item)}

    if not ids:

        raise HTTPException(status_code=400, detail="没有选择资产")

    lib = load_asset_library()

    target_cat = find_asset_category_in_library(lib, payload.target_category_id, payload.target_library_id)

    if not target_cat:

        raise HTTPException(status_code=404, detail="目标分组不存在")

    target_type = target_cat.get("type") or "image"

    moved = []

    for library in lib.get("libraries", []):

        if payload.library_id and library.get("id") != payload.library_id:

            continue

        for cat in library.get("categories", []):

            if (cat.get("type") or "image") != target_type:

                continue

            keep = []

            for item in cat.get("items", []):

                if item.get("id") in ids:

                    moved.append(item)

                else:

                    keep.append(item)

            cat["items"] = keep

    existing_ids = {item.get("id") for item in target_cat.get("items", [])}

    for item in moved:

        if item.get("id") not in existing_ids:

            target_cat.setdefault("items", []).append(item)

            existing_ids.add(item.get("id"))

    save_asset_library(lib)

    return {"library": lib, "moved": len(moved)}



@router.post("/api/asset-library/items/crop")

async def batch_crop_asset_library_items(payload: AssetLibraryBatchCropRequest):

    ids = {str(item) for item in (payload.ids or []) if str(item)}

    if not ids:

        raise HTTPException(status_code=400, detail="没有选择资产")

    lib = load_asset_library()

    target_cat = None

    if payload.target_category_id:

        target_cat = find_asset_category_in_library(lib, payload.target_category_id, payload.target_library_id)

        if not target_cat:

            raise HTTPException(status_code=404, detail="目标分组不存在")

        if target_cat.get("type") != "image":

            raise HTTPException(status_code=400, detail="目标分组不支持媒体")

    added = []

    for library in lib.get("libraries", []):

        if payload.library_id and library.get("id") != payload.library_id:

            continue

        for cat in library.get("categories", []):

            if cat.get("type") != "image":

                continue

            source_items = [item for item in (cat.get("items", []) or []) if item.get("id") in ids]

            for item in source_items:

                src = output_file_from_url(item.get("url") or "")

                if not src or not os.path.isfile(src):

                    continue

                try:

                    with Image.open(src) as img:

                        img = img.convert("RGBA")

                        w, h = img.size

                        side = min(w, h)

                        if side <= 0:

                            continue

                        left = max(0, (w - side) // 2)

                        top = max(0, (h - side) // 2)

                        cropped = img.crop((left, top, left + side, top + side))

                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")

                        tmp_path = tmp.name

                        tmp.close()

                        try:

                            cropped.save(tmp_path, "PNG")

                            base_name = os.path.splitext(item.get("name") or "asset")[0] + "_crop.png"

                            dest_cat = target_cat or cat

                            _, next_item = make_asset_library_item(tmp_path, base_name, subdir=dest_cat.get("dir") or "")

                            dest_cat.setdefault("items", []).append(next_item)

                            added.append(next_item)

                        finally:

                            try:

                                os.remove(tmp_path)

                            except Exception:

                                pass

                except Exception:

                    continue

    save_asset_library(lib)

    return {"library": lib, "added": len(added), "items": added}



@router.put("/api/canvases/{canvas_id}")

async def update_canvas(canvas_id: str, payload: CanvasSaveRequest):

    canvas = load_canvas(canvas_id)

    current_updated_at = int(canvas.get("updated_at") or 0)

    if payload.base_updated_at and current_updated_at and int(payload.base_updated_at) < current_updated_at:

        raise HTTPException(status_code=409, detail={

            "message": "画布已被其他页面更新，已拒绝旧版本覆盖。",

            "canvas": canvas,

            "updated_at": current_updated_at,

        })

    canvas["title"] = (payload.title or canvas.get("title") or "未命名画布")[:80]

    canvas["icon"] = (payload.icon or canvas.get("icon") or "layers")[:32]

    canvas["kind"] = normalize_canvas_kind(canvas.get("kind"))

    canvas["nodes"] = payload.nodes

    canvas["connections"] = payload.connections

    if canvas["kind"] == "smart":

        canvas["viewport"] = payload.viewport

    else:

        canvas["viewport"] = canvas.get("viewport") or {"x": 0, "y": 0, "scale": 1}

    canvas["logs"] = payload.logs[-500:]

    canvas["settings"] = payload.settings or {}

    save_canvas(canvas)

    await manager.broadcast_canvas_updated(canvas_id, int(canvas.get("updated_at") or now_ms()), payload.client_id)

    return {"canvas": canvas}



@router.delete("/api/canvases/{canvas_id}")

async def delete_canvas(canvas_id: str):

    canvas = load_canvas_any(canvas_id)

    if not canvas.get("deleted_at"):

        canvas["deleted_at"] = now_ms()

        save_canvas(canvas)

    return {"ok": True}



@router.post("/api/canvases/{canvas_id}/restore")

async def restore_canvas(canvas_id: str):

    canvas = load_canvas_any(canvas_id)

    if canvas.get("deleted_at"):

        canvas.pop("deleted_at", None)

        save_canvas(canvas)

    return {"canvas": canvas}



@router.delete("/api/canvases/{canvas_id}/purge")

async def purge_canvas(canvas_id: str):

    path = canvas_path(canvas_id)

    if os.path.exists(path):

        os.remove(path)

    return {"ok": True}



# --- 历史记录 ---



@router.get("/api/history")

async def get_history_api(type: str = None):

    if os.path.exists(HISTORY_FILE):

        try:

            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:

                data = json.load(f)

                if type:

                    data = [item for item in data if item.get("type", "zimage") == type]

                data = [item for item in data if item.get("images") and len(item["images"]) > 0]



                def sort_key(item):

                    ts = item.get("timestamp", 0)

                    if isinstance(ts, (int, float)):

                        return float(ts)

                    return 0



                data.sort(key=sort_key, reverse=True)

                return data

        except Exception as e:

            print(f"读取历史文件失败: {e}")

            return []

    return []



@router.get("/api/queue_status")

async def get_queue_status(client_id: str):

    with QUEUE_LOCK:

        total = len(QUEUE)

        positions = [i + 1 for i, t in enumerate(QUEUE) if t["client_id"] == client_id]

        position = positions[0] if positions else 0

    return {"total": total, "position": position}



@router.post("/api/history/delete")

async def delete_history(req: DeleteHistoryRequest):

    if not os.path.exists(HISTORY_FILE):

        return {"success": False, "message": "History file not found"}

    try:

        with HISTORY_LOCK:

            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:

                history = json.load(f)

            target_record = None

            new_history = []

            for item in history:

                is_match = False

                item_ts = item.get("timestamp", 0)

                if isinstance(req.timestamp, (int, float)) and isinstance(item_ts, (int, float)):

                    if abs(float(item_ts) - float(req.timestamp)) < 0.001:

                        is_match = True

                elif str(item_ts) == str(req.timestamp):

                    is_match = True

                if is_match:

                    target_record = item

                else:

                    new_history.append(item)

            if target_record:

                with open(HISTORY_FILE, 'w', encoding='utf-8') as f:

                    json.dump(new_history, f, ensure_ascii=False, indent=4)



        if target_record:

            for img_url in target_record.get("images", []):

                file_path = output_file_from_url(img_url)

                if file_path and os.path.exists(file_path):

                    try:

                        os.remove(file_path)

                    except Exception as e:

                        print(f"Failed to delete file {file_path}: {e}")

            return {"success": True}

        else:

            return {"success": False, "message": "Record not found"}

    except Exception as e:

        print(f"Delete history error: {e}")

        return {"success": False, "message": str(e)}



# --- ModelScope 角度控制 ---



@router.post("/api/angle/poll_status")

async def poll_angle_cloud(req: CloudPollRequest):

    api_root = modelscope_image_api_root()

    clean_token = modelscope_api_key(req.api_key)

    if not clean_token:

        raise HTTPException(status_code=400, detail="未提供 ModelScope API Key")



    headers = {

        "Authorization": f"Bearer {clean_token}",

        "Content-Type": "application/json",

        "X-ModelScope-Async-Mode": "true"

    }

    task_id = req.task_id

    print(f"Resuming polling for Angle Task: {task_id}")



    try:

        async with httpx.AsyncClient(timeout=30) as client:

            for i in range(300):

                await asyncio.sleep(2)

                result = await client.get(

                    f"{api_root}/tasks/{task_id}",

                    headers={**headers, "X-ModelScope-Task-Type": "image_generation"},

                )

                result.raise_for_status()

                data = result.json()

                status = str(data.get("task_status") or "").upper()



                if status == "SUCCEED":

                    img_url = data["output_images"][0]

                    local_path = ""

                    try:

                        async with httpx.AsyncClient() as dl_client:

                            img_res = await dl_client.get(img_url)

                            if img_res.status_code == 200:

                                filename = f"cloud_angle_{int(time.time())}.png"

                                file_path = output_path_for(filename, "output")

                                with open(file_path, "wb") as f:

                                    f.write(img_res.content)

                                local_path = output_url_for(filename, "output")

                            else:

                                local_path = img_url

                    except Exception:

                        local_path = img_url



                    record = {"timestamp": time.time(), "prompt": f"Resumed {task_id}", "images": [local_path], "type": "angle"}

                    save_to_history(record)

                    if req.client_id:

                        await manager.send_personal_message({"type": "cloud_status", "status": "SUCCEED", "task_id": task_id}, req.client_id)

                    return {"url": local_path}



                elif status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"}:

                    if req.client_id:

                        await manager.send_personal_message({"type": "cloud_status", "status": "FAILED", "task_id": task_id}, req.client_id)

                    raise HTTPException(status_code=502, detail=f"ModelScope task failed: {data}")



                if i % 5 == 0 and req.client_id:

                    await manager.send_personal_message({

                        "type": "cloud_status", "status": f"{status} ({i}/300)",

                        "task_id": task_id, "progress": i, "total": 300

                    }, req.client_id)



            if req.client_id:

                await manager.send_personal_message({"type": "cloud_status", "status": "TIMEOUT", "task_id": task_id}, req.client_id)

            return {"status": "timeout", "task_id": task_id, "message": "Task still pending"}



    except HTTPException:

        raise

    except Exception as e:

        print(f"Angle polling error: {e}")

        raise HTTPException(status_code=400, detail=str(e))



@router.post("/api/angle/generate")

async def generate_angle_cloud(req: CloudGenRequest):

    api_root = modelscope_image_api_root()

    clean_token = modelscope_api_key(req.api_key)

    if not clean_token:

        raise HTTPException(status_code=400, detail="未提供 ModelScope API Key")



    headers = {

        "Authorization": f"Bearer {clean_token}",

        "Content-Type": "application/json",

        "X-ModelScope-Async-Mode": "true"

    }

    model = selected_model(req.model, "Qwen/Qwen-Image-Edit-2511")

    payload = {

        "model": model,

        "prompt": req.prompt.strip(),

        "image_url": [modelscope_image_url(url, max_size=1536) for url in req.image_urls]

    }

    if req.resolution:

        payload["size"] = modelscope_size(req.resolution)

    if req.loras is not None:

        payload["loras"] = req.loras



    try:

        async with httpx.AsyncClient(timeout=30) as client:

            submit_res = await client.post(f"{api_root}/images/generations", headers=headers, json=payload)

            if submit_res.status_code != 200:

                try:

                    detail = submit_res.json()

                except:

                    detail = submit_res.text

                raise HTTPException(status_code=submit_res.status_code, detail=detail)



            task_id = submit_res.json().get("task_id")

            print(f"Angle Task submitted, ID: {task_id}")



            for i in range(300):

                await asyncio.sleep(2)

                result = await client.get(

                    f"{api_root}/tasks/{task_id}",

                    headers={**headers, "X-ModelScope-Task-Type": "image_generation"},

                )

                result.raise_for_status()

                data = result.json()

                status = str(data.get("task_status") or "").upper()



                if status == "SUCCEED":

                    img_url = data["output_images"][0]

                    local_path = ""

                    try:

                        async with httpx.AsyncClient() as dl_client:

                            img_res = await dl_client.get(img_url)

                            if img_res.status_code == 200:

                                filename = f"cloud_angle_{int(time.time())}.png"

                                file_path = output_path_for(filename, "output")

                                with open(file_path, "wb") as f:

                                    f.write(img_res.content)

                                local_path = output_url_for(filename, "output")

                            else:

                                local_path = img_url

                    except Exception:

                        local_path = img_url



                    record = {"timestamp": time.time(), "prompt": req.prompt, "images": [local_path], "type": "angle"}

                    save_to_history(record)

                    if req.client_id:

                        await manager.send_personal_message({"type": "cloud_status", "status": "SUCCEED", "task_id": task_id}, req.client_id)

                    if core.GLOBAL_LOOP:

                        asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(record), core.GLOBAL_LOOP)

                    return {"url": local_path, "task_id": task_id}



                elif status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"}:

                    if req.client_id:

                        await manager.send_personal_message({"type": "cloud_status", "status": "FAILED", "task_id": task_id}, req.client_id)

                    raise HTTPException(status_code=502, detail=f"ModelScope task failed: {data}")



                if i % 5 == 0 and req.client_id:

                    await manager.send_personal_message({

                        "type": "cloud_status", "status": f"{status} ({i}/300)",

                        "task_id": task_id, "progress": i, "total": 300

                    }, req.client_id)



            if req.client_id:

                await manager.send_personal_message({"type": "cloud_status", "status": "TIMEOUT", "task_id": task_id}, req.client_id)

            return {"status": "timeout", "task_id": task_id, "message": "Task still pending"}



    except HTTPException:

        raise

    except Exception as e:

        print(f"Angle generation error: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# --- ModelScope Z-Image 云端生图 ---



@router.post("/generate")

async def generate_cloud(req: CloudGenRequest):

    api_root = modelscope_image_api_root()

    clean_token = modelscope_api_key(req.api_key)

    if not clean_token:

        raise HTTPException(status_code=400, detail="未提供 ModelScope API Key")



    headers = {

        "Authorization": f"Bearer {clean_token}",

        "Content-Type": "application/json",

    }

    payload = {

        "model": "Tongyi-MAI/Z-Image-Turbo",

        "prompt": req.prompt.strip(),

        "size": modelscope_size(req.resolution),

        "n": 1

    }

    if req.loras is not None:

        payload["loras"] = req.loras



    try:

        async with httpx.AsyncClient(timeout=30) as client:

            submit_res = await client.post(

                f"{api_root}/images/generations",

                headers={**headers, "X-ModelScope-Async-Mode": "true"},

                json=payload

            )

            if submit_res.status_code != 200:

                try:

                    detail = submit_res.json()

                except:

                    detail = submit_res.text

                raise HTTPException(status_code=submit_res.status_code, detail=detail)



            task_id = submit_res.json().get("task_id")

            print(f"Z-Image Task submitted, ID: {task_id}")



            for i in range(200):

                await asyncio.sleep(3)

                result = await client.get(

                    f"{api_root}/tasks/{task_id}",

                    headers={**headers, "X-ModelScope-Task-Type": "image_generation"},

                )

                result.raise_for_status()

                data = result.json()

                status = str(data.get("task_status") or "").upper()



                if i % 5 == 0:

                    print(f"Task {task_id} status check {i}: {status}")



                if status == "SUCCEED":

                    img_url = data["output_images"][0]

                    local_path = ""

                    try:

                        async with httpx.AsyncClient() as dl_client:

                            img_res = await dl_client.get(img_url)

                            if img_res.status_code == 200:

                                filename = f"cloud_{int(time.time())}.png"

                                file_path = output_path_for(filename, "output")

                                with open(file_path, "wb") as f:

                                    f.write(img_res.content)

                                local_path = output_url_for(filename, "output")

                            else:

                                local_path = img_url

                    except Exception as dl_e:

                        print(f"Download error: {dl_e}")

                        local_path = img_url



                    record = {"timestamp": time.time(), "prompt": req.prompt, "images": [local_path], "type": "cloud"}

                    save_to_history(record)

                    try:

                        await manager.broadcast_new_image(record)

                    except Exception:

                        pass

                    return {"url": local_path}



                elif status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"}:

                    raise HTTPException(status_code=502, detail=f"ModelScope task failed: {data}")



            raise Exception("Cloud generation timeout")



    except HTTPException:

        raise

    except Exception as e:

        print(f"Cloud generation error: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# --- ModelScope 通用图片生成（支持图生图） ---



@router.post("/api/ms/generate")

async def ms_generate(req: MsGenerateRequest):

    api_root = modelscope_image_api_root()

    clean_token = modelscope_api_key(req.api_key)

    if not clean_token:

        raise HTTPException(status_code=400, detail="未配置 ModelScope API Key，请在 API 设置中填写，或重新保存 ModelScope Token。")



    headers = {

        "Authorization": f"Bearer {clean_token}",

        "Content-Type": "application/json",

        "X-ModelScope-Async-Mode": "true"

    }

    payload = {

        "model": req.model,

        "prompt": req.prompt.strip(),

    }

    if req.width and req.height:

        payload["width"] = req.width

        payload["height"] = req.height

        payload["size"] = modelscope_size(req.size or f"{req.width}x{req.height}")

    elif req.size:

        payload["size"] = modelscope_size(req.size)

    if req.image_urls:

        payload["image_url"] = [modelscope_image_url(url, max_size=1536) for url in req.image_urls]

    if req.loras is not None:

        payload["loras"] = req.loras



    try:

        async with httpx.AsyncClient(timeout=30) as client:

            submit_res = await client.post(

                f"{api_root}/images/generations",

                headers=headers,

                json=payload

            )

            if submit_res.status_code != 200:

                try:

                    detail = submit_res.json()

                except:

                    detail = submit_res.text

                raise HTTPException(status_code=submit_res.status_code, detail=detail)



            task_id = submit_res.json().get("task_id")

            print(f"MS Generate Task submitted ({req.model}), ID: {task_id}")



            TERMINAL_FAILED_STATUSES = {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"}



            for i in range(300):

                await asyncio.sleep(2)

                try:

                    result = await client.get(

                        f"{api_root}/tasks/{task_id}",

                        headers={**headers, "X-ModelScope-Task-Type": "image_generation"},

                    )

                    data = result.json()

                    status = data.get("task_status")

                    print(f"MS Task {task_id} poll {i}: status={status}")



                    if status == "SUCCEED":

                        img_url = data["output_images"][0]

                        local_path = ""

                        try:

                            async with httpx.AsyncClient() as dl_client:

                                img_res = await dl_client.get(img_url)

                                if img_res.status_code == 200:

                                    filename = f"ms_{req.model.replace('/', '_').replace(':', '_')}_{int(time.time())}.png"

                                    file_path = output_path_for(filename, "output")

                                    with open(file_path, "wb") as f:

                                        f.write(img_res.content)

                                    local_path = output_url_for(filename, "output")

                                else:

                                    local_path = img_url

                        except Exception:

                            local_path = img_url



                        record = {

                            "timestamp": time.time(),

                            "prompt": req.prompt,

                            "images": [local_path],

                            "type": "klein",

                            "model": req.model,

                        }

                        save_to_history(record)

                        if core.GLOBAL_LOOP:

                            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(record), core.GLOBAL_LOOP)

                        return {"url": local_path, "task_id": task_id}



                    elif status in TERMINAL_FAILED_STATUSES:

                        error_info = data.get("error_info") or data.get("message") or data.get("detail") or str(data)

                        raise HTTPException(status_code=502, detail=f"MS task {status}: {error_info}")



                except HTTPException:

                    raise

                except Exception as loop_e:

                    print(f"MS polling error: {loop_e}")

                    continue



            raise HTTPException(status_code=504, detail="MS 生图超时")



    except HTTPException:

        raise

    except Exception as e:

        print(f"MS generate error: {e}")

        raise HTTPException(status_code=400, detail=str(e))



# --- 本地 ComfyUI 生图 ---



@router.post("/api/generate")

def generate(req: GenerateRequest):

    current_task = None

    target_backend = None

    with QUEUE_LOCK:

        task_id = core.NEXT_TASK_ID

        core.NEXT_TASK_ID += 1

        current_task = {"task_id": task_id, "client_id": req.client_id}

        QUEUE.append(current_task)



    try:

        required_images = collect_required_comfy_media(req.params)



        target_backend = reserve_best_backend(required_images)



        for image_name in required_images:

            need_sync = False

            try:

                check_url = f"http://{target_backend}/view?filename={urllib.parse.quote(image_name)}&type=input"

                resp = requests.get(check_url, stream=True, timeout=0.5)

                resp.close()

                if resp.status_code != 200:

                    need_sync = True

            except:

                need_sync = True



            if need_sync:

                image_content = None

                image_type = "image/png"

                for addr in core.COMFYUI_INSTANCES:

                    if addr == target_backend: continue

                    try:

                        src_url = f"http://{addr}/view?filename={urllib.parse.quote(image_name)}&type=input"

                        r = requests.get(src_url, timeout=5)

                        if r.status_code == 200:

                            image_content = r.content

                            image_type = r.headers.get("Content-Type", "image/png")

                            break

                    except: continue



                if image_content:

                    try:

                        files = {'image': (image_name, image_content, image_type)}

                        requests.post(f"http://{target_backend}/upload/image", files=files, timeout=10)

                    except Exception as e:

                        print(f"Sync upload failed: {e}")



        workflow_path = os.path.join(WORKFLOW_DIR, req.workflow_json)

        if not os.path.exists(workflow_path) and req.workflow_json == "Z-Image.json":

            workflow_path = WORKFLOW_PATH

        if not os.path.exists(workflow_path):

            raise Exception(f"Workflow file not found: {req.workflow_json}")



        with open(workflow_path, 'r', encoding='utf-8') as f:

            workflow = json.load(f)



        seed = random.randint(1, 4294967295)



        if "23" in workflow and req.prompt:

            workflow["23"]["inputs"]["text"] = req.prompt

        if "144" in workflow:

            workflow["144"]["inputs"]["width"] = req.width

            workflow["144"]["inputs"]["height"] = req.height

        if "22" in workflow:

            workflow["22"]["inputs"]["seed"] = seed

        if "158" in workflow:

            workflow["158"]["inputs"]["noise_seed"] = seed

        for node_id in ["146", "181"]:

            if node_id in workflow and "inputs" in workflow[node_id] and "seed" in workflow[node_id]["inputs"]:

                workflow[node_id]["inputs"]["seed"] = seed

        if "184" in workflow and "inputs" in workflow["184"] and "seed" in workflow["184"]["inputs"]:

            workflow["184"]["inputs"]["seed"] = seed

        if "172" in workflow and "inputs" in workflow["172"] and "seed" in workflow["172"]["inputs"]:

            workflow["172"]["inputs"]["seed"] = seed

        if "14" in workflow and "inputs" in workflow["14"] and "seed" in workflow["14"]["inputs"]:

            workflow["14"]["inputs"]["seed"] = seed



        for node_id, node_inputs in req.params.items():

            if node_id in workflow:

                if "inputs" not in workflow[node_id]:

                    workflow[node_id]["inputs"] = {}

                for input_name, value in node_inputs.items():

                    if value is None:

                        workflow[node_id]["inputs"].pop(input_name, None)

                        continue

                    workflow[node_id]["inputs"][input_name] = value

            elif isinstance(node_inputs, dict) and node_inputs.get("class_type") and isinstance(node_inputs.get("inputs"), dict):

                workflow[str(node_id)] = {

                    "class_type": str(node_inputs.get("class_type")),

                    "inputs": node_inputs.get("inputs") or {},

                    "_meta": node_inputs.get("_meta") if isinstance(node_inputs.get("_meta"), dict) else {"title": str(node_inputs.get("class_type"))},

                }



        p = {"prompt": workflow, "client_id": CLIENT_ID}

        data = json.dumps(p).encode('utf-8')

        try:

            post_req = urllib.request.Request(f"http://{target_backend}/prompt", data=data)

            prompt_id = json.loads(urllib.request.urlopen(post_req, timeout=10).read())['prompt_id']

        except urllib.error.HTTPError as e:

            error_body = e.read().decode('utf-8')

            raise Exception(comfy_prompt_error_message(e.code, error_body))



        history_data = None

        for i in range(COMFYUI_HISTORY_TIMEOUT):

            try:

                res = get_comfy_history(target_backend, prompt_id)

                if prompt_id in res:

                    history_data = res[prompt_id]

                    break

            except Exception:

                pass

            time.sleep(1)



        if not history_data:

            raise Exception("ComfyUI 渲染超时")



        local_images = []

        local_videos = []

        local_audios = []

        local_texts = []

        local_files = []

        local_items = []

        local_urls = []

        current_timestamp = time.time()

        if 'outputs' in history_data:

            # 先把所有节点的输出收集为候选（带上 class_type），再决定下载哪些，

            # 避免把冗余的预览/对比图、调试文本一起下载进结果（后端层过滤，历史记录也更干净）。

            workflow_nodes = workflow if isinstance(workflow, dict) else {}

            def _class_type_of(nid):

                node_def = workflow_nodes.get(str(nid))

                return str(node_def.get("class_type") or "") if isinstance(node_def, dict) else ""

            file_candidates = []   # (node_id, class_type, output_key, item, kind)

            text_candidates = []   # (node_id, class_type, text, name)

            for node_id in history_data['outputs']:

                node_output = history_data['outputs'][node_id]

                class_type = _class_type_of(node_id)

                for output_key, item in collect_comfy_file_items(node_output):

                    file_candidates.append((node_id, class_type, output_key, item, comfy_output_kind(item)))

                for text, name in comfy_text_values_from_output(node_output):

                    text_candidates.append((node_id, class_type, text, name))



            # 只要存在“非预览节点”产出的图片，就把 PreviewImage/对比节点的图片视为冗余丢弃；

            # 若整个工作流只有预览图（没有 SaveImage 等），则保留预览图作为唯一结果，避免零输出。

            has_primary_image = any(

                kind == "image" and not comfy_class_is_preview(ct)

                for (_nid, ct, _ok, _it, kind) in file_candidates

            )

            prefix = f"{req.type}_{int(current_timestamp)}_"

            for node_id, class_type, output_key, item, kind in file_candidates:

                if kind == "image" and has_primary_image and comfy_class_is_preview(class_type):

                    continue  # 跳过冗余的预览/对比图

                local_path = download_comfy_output(target_backend, item, prefix=prefix)

                if kind == "image" and req.convert_to_jpg:

                    local_path = convert_output_to_jpg(local_path)

                name = os.path.basename(str(item.get("filename") or "")) or os.path.basename(str(local_path).split("?", 1)[0])

                entry = {

                    "url": local_path,

                    "kind": kind,

                    "name": name,

                    "node_id": str(node_id),

                    "output_key": str(output_key),

                    "class_type": class_type,

                }

                if kind == "image":

                    local_images.append(local_path)

                elif kind == "video":

                    local_videos.append(local_path)

                elif kind == "audio":

                    local_audios.append(local_path)

                elif kind == "text":

                    local_texts.append(local_path)

                else:

                    local_files.append(local_path)

                local_items.append(entry)

                local_urls.append(local_path)



            # 默认抑制 show/utility 类节点的调试文本，避免 .txt 噪声混入结果。

            for node_id, class_type, text, name in text_candidates:

                if comfy_class_is_debug_text(class_type):

                    continue

                local_path = save_comfy_text_output(text, prefix=prefix, name=name)

                entry = {

                    "url": local_path,

                    "kind": "text",

                    "name": os.path.basename(str(local_path).split("?", 1)[0]),

                    "node_id": str(node_id),

                    "output_key": "text",

                    "class_type": class_type,

                }

                local_texts.append(local_path)

                local_items.append(entry)

                local_urls.append(local_path)



        result = {

            "prompt": req.prompt if req.prompt else "Detail Enhance",

            "images": local_images,

            "videos": local_videos,

            "audios": local_audios,

            "texts": local_texts,

            "files": local_files,

            "items": local_items,

            "outputs": local_urls,

            "seed": seed,

            "timestamp": current_timestamp,

            "type": req.type,

            "workflow_json": req.workflow_json,

            "task_id": task_id,

            "prompt_id": prompt_id,

            "backend": target_backend,

            "params": req.params

        }

        save_to_history(result)

        if core.GLOBAL_LOOP:

            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), core.GLOBAL_LOOP)

        return result



    except Exception as e:

        return {"images": [], "error": str(e)}

    finally:

        if target_backend:

            with LOAD_LOCK:

                if core.BACKEND_LOCAL_LOAD.get(target_backend, 0) > 0:

                    core.BACKEND_LOCAL_LOAD[target_backend] -= 1

        if current_task:

            with QUEUE_LOCK:

                if current_task in QUEUE:

                    QUEUE.remove(current_task)



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



@router.get("/api/workflows")

def list_workflows():

    if not os.path.isdir(WORKFLOW_DIR):

        return {"workflows": []}

    items = []

    for root, dirs, files in os.walk(WORKFLOW_DIR):

        if os.path.abspath(root) == os.path.abspath(WORKFLOW_DIR):

            dirs[:] = [d for d in dirs if d in {CUSTOM_WORKFLOW_FOLDER, LEGACY_CUSTOM_WORKFLOW_FOLDER}]

        for fn in sorted(files):

            if not fn.endswith(".json") or fn.endswith(".config.json"):

                continue

            rel = os.path.relpath(os.path.join(root, fn), WORKFLOW_DIR).replace("\\", "/")

            if is_builtin_workflow(rel):

                continue

            cfg = {}

            cfg_path = workflow_config_path(rel)

            if os.path.exists(cfg_path):

                try:

                    with open(cfg_path, "r", encoding="utf-8") as f:

                        cfg = json.load(f) or {}

                except Exception:

                    cfg = {}

            items.append({

                "name": rel,

                "title": cfg.get("title") or fn.replace(".json", ""),

                "builtin": False,

                "field_count": len(cfg.get("fields") or []),

            })

    items.sort(key=lambda item: (0 if item["name"].startswith(f"{CUSTOM_WORKFLOW_FOLDER}/") else 1, item["title"]))

    return {"workflows": items}



@router.get("/api/workflows/{name:path}")

def get_workflow(name: str):

    if not WORKFLOW_NAME_RE.match(name):

        raise HTTPException(status_code=400, detail="Invalid workflow name")

    workflow_path = workflow_path_from_name(name)

    if not os.path.exists(workflow_path):

        raise HTTPException(status_code=404, detail="Workflow not found")

    with open(workflow_path, "r", encoding="utf-8") as f:

        workflow = json.load(f)

    cfg = {"title": name.replace(".json", ""), "fields": []}

    cfg_path = workflow_config_path(name)

    if os.path.exists(cfg_path):

        try:

            with open(cfg_path, "r", encoding="utf-8") as f:

                cfg = json.load(f) or cfg

        except Exception:

            pass

    return {"name": name, "workflow": workflow, "config": cfg, "builtin": is_builtin_workflow(name)}



@router.post("/api/workflows")

def upload_workflow(payload: WorkflowUploadRequest):

    name = os.path.basename(payload.name.strip())

    if not name.endswith(".json"):

        name = name + ".json"

    if not WORKFLOW_NAME_RE.match(name):

        raise HTTPException(status_code=400, detail="工作流名称不合法，请使用中文/英文/数字/_-.")

    if not isinstance(payload.workflow, dict) or not payload.workflow:

        raise HTTPException(status_code=400, detail="工作流 JSON 为空")

    # 简单校验：是 API 格式（节点 id 为 key，含 class_type）

    sample = next(iter(payload.workflow.values()), None)

    if not isinstance(sample, dict) or "class_type" not in sample:

        raise HTTPException(status_code=400, detail="不是有效的 ComfyUI API 工作流 JSON（需包含 class_type）")

    custom_dir = os.path.join(WORKFLOW_DIR, CUSTOM_WORKFLOW_FOLDER)

    os.makedirs(custom_dir, exist_ok=True)

    stored_name = f"{CUSTOM_WORKFLOW_FOLDER}/{name}"

    path = workflow_path_from_name(stored_name)

    with open(path, "w", encoding="utf-8") as f:

        json.dump(payload.workflow, f, ensure_ascii=False, indent=2)

    return {"name": stored_name}



@router.put("/api/workflows/{name:path}/config")

def save_workflow_config(name: str, payload: WorkflowConfig):

    if not WORKFLOW_NAME_RE.match(name):

        raise HTTPException(status_code=400, detail="Invalid workflow name")

    workflow_path = workflow_path_from_name(name)

    if not os.path.exists(workflow_path):

        raise HTTPException(status_code=404, detail="Workflow not found")

    cfg_path = workflow_config_path(name)

    with open(cfg_path, "w", encoding="utf-8") as f:

        json.dump(payload.dict(), f, ensure_ascii=False, indent=2)

    return {"config": payload.dict()}



@router.delete("/api/workflows/{name:path}")

def delete_workflow(name: str):

    if not WORKFLOW_NAME_RE.match(name):

        raise HTTPException(status_code=400, detail="Invalid workflow name")

    if is_builtin_workflow(name):

        raise HTTPException(status_code=400, detail="内置工作流不可删除")

    workflow_path = workflow_path_from_name(name)

    cfg_path = workflow_config_path(name)

    if not os.path.exists(workflow_path):

        raise HTTPException(status_code=404, detail="Workflow not found")

    os.remove(workflow_path)

    if os.path.exists(cfg_path):

        os.remove(cfg_path)

    return {"ok": True}



@router.post("/api/workflows/{name:path}/run")

def run_workflow(name: str, payload: WorkflowRunRequest):

    if not WORKFLOW_NAME_RE.match(name):

        raise HTTPException(status_code=400, detail="Invalid workflow name")

    if not os.path.exists(workflow_path_from_name(name)):

        raise HTTPException(status_code=404, detail="Workflow not found")

    # 根据 config 的字段把值映射成 params 节点覆盖

    params: Dict[str, Dict[str, Any]] = {}

    for field in payload.config.fields:

        if not field.node or not field.input:

            continue

        if field.id in payload.fields:

            value = payload.fields[field.id]

            # 类型转换

            if field.type in ("number", "slider"):

                try:

                    value = float(value) if (field.step and field.step < 1) else int(float(value))

                except Exception:

                    pass

            elif field.type == "boolean":

                value = bool(value)

            elif field.type == "dropdown":

                # 下拉值如果看起来是数字（如 "1024" / "2048" / "0.8"），自动转成 int/float

                if isinstance(value, str):

                    s = value.strip()

                    try:

                        if s and ('.' in s or 'e' in s.lower()):

                            value = float(s)

                        elif s and (s.lstrip('-').isdigit()):

                            value = int(s)

                    except (ValueError, TypeError):

                        pass

            params.setdefault(field.node, {})[field.input] = value

    req = GenerateRequest(

        prompt="",

        workflow_json=name,

        params=params,

        type="workflow-test",

        client_id=payload.client_id or str(uuid.uuid4()),

    )

    return generate(req)
