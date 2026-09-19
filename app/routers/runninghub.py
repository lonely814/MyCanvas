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
