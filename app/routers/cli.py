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
