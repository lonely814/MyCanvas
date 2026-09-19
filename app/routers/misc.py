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
