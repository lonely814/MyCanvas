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
