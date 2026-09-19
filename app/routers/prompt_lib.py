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
