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

        "kind": "classic",

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

    canvas["kind"] = "classic"

    canvas["nodes"] = payload.nodes

    canvas["connections"] = payload.connections

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
