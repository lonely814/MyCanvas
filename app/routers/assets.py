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



@router.post("/api/local-assets/upload")

async def upload_local_assets(files: List[UploadFile] = File(...), folder: str = Form("")):

    max_upload_bytes = 50 * 1024 * 1024

    uploaded = []

    folder_rel, folder_abs = _local_upload_safe_folder(folder)

    os.makedirs(folder_abs, exist_ok=True)

    for file in files:

        content = await file.read()

        if not content:

            continue

        if len(content) > max_upload_bytes:

            raise HTTPException(status_code=413, detail=f"{file.filename or '文件'} 超过 50MB，无法上传")

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



@router.get("/api/asset-library")

async def get_asset_library():

    return {"library": load_asset_library()}



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
