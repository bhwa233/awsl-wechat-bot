"""本地归档管理及受限云端查询,复用 Cortex 认证。"""

import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from archive.client import ArchiveClient, failure_message
from archive.models import config_error, state_path
from archive.store import ArchiveStore
from common.auth import verify_token
from common.config import config


class GroupUpdate(BaseModel):
    talker: str = Field(min_length=1, max_length=200)
    enabled: bool = True

    @field_validator("talker")
    @classmethod
    def validate_talker(cls, value):
        if not value.endswith("@chatroom") or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError("必须使用有效的群 ID")
        return value


def create_routes(chatlog_client):
    router = APIRouter(prefix="/api/archive", dependencies=[Depends(verify_token)])
    error = config_error(config)
    store = cloud = None
    if not error:
        try:
            store = ArchiveStore(state_path(config), config.ARCHIVE_ACCOUNT_ID, config.ARCHIVE_SOURCE_ID)
            cloud = ArchiveClient(config.ARCHIVE_URL, config.ARCHIVE_TOKEN)
        except ValueError as exc:
            error = str(exc)
        except (OSError, sqlite3.Error):
            error = "无法打开归档状态文件,请检查路径和读写权限"

    def ready():
        if error:
            raise HTTPException(status_code=503, detail=error)

    def cloud_scope():
        try:
            scope = cloud.scope()
            if scope.get("account_id") != config.ARCHIVE_ACCOUNT_ID or scope.get("source_id") != config.ARCHIVE_SOURCE_ID:
                raise ValueError("本地归档账号/来源与云端凭证不匹配")
            return scope["groups"]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=failure_message(exc)) from exc

    @router.get("/status")
    def status():
        if error:
            return {"enabled": False, "reason": error, "groups": []}
        result = store.status()
        heartbeat = result["heartbeat"]
        return {
            **result, "enabled": True, "account_id": config.ARCHIVE_ACCOUNT_ID, "source_id": config.ARCHIVE_SOURCE_ID,
            "running": bool(heartbeat and time.time() * 1000 - heartbeat < (config.ARCHIVE_POLL_SECONDS * 2 + 60) * 1000),
        }

    @router.get("/available-groups", dependencies=[Depends(ready)])
    def available_groups():
        allowed = set(cloud_scope())
        try:
            return [group for group in chatlog_client.list_groups() if group["username"] in allowed]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=failure_message(exc)) from exc

    @router.put("/groups", dependencies=[Depends(ready)])
    def save_group(payload: GroupUpdate):
        existing = next((group for group in store.groups() if group["talker"] == payload.talker), None)
        if existing:
            name = existing["display_name"]
        else:
            if payload.talker not in cloud_scope():
                raise HTTPException(status_code=403, detail="云端凭证未授权此群")
            try:
                group = next((group for group in chatlog_client.list_groups() if group["username"] == payload.talker), None)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=failure_message(exc)) from exc
            if not group:
                raise HTTPException(status_code=404, detail="Chatlog 未找到此群")
            name = str(group.get("display_name") or payload.talker)[:1000]
        store.set_group(payload.talker, name, payload.enabled)
        return {"success": True}

    @router.post("/replay", dependencies=[Depends(ready)])
    def replay(payload: GroupUpdate):
        if not any(group["talker"] == payload.talker for group in store.groups()):
            raise HTTPException(status_code=404, detail="未配置此群")
        store.replay(payload.talker)
        return {"success": True}

    @router.get("/messages", dependencies=[Depends(ready)])
    def messages(
        talker: str = Query(..., max_length=200), q: str = Query("", max_length=256),
        start: int | None = Query(None, ge=0, le=253402300799), end: int | None = Query(None, ge=0, le=253402300799),
        sender: str = Query("", max_length=200), cursor: str = Query("", max_length=400), limit: int = Query(100, ge=1, le=200),
    ):
        filters = {"talker": talker, "limit": limit}
        for key, value in {"q": q, "start": start, "end": end, "sender": sender, "cursor": cursor}.items():
            if value is not None and value != "":
                filters[key] = value
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=400, detail="开始时间不得晚于结束时间")
        try:
            return cloud.messages(config.ARCHIVE_ACCOUNT_ID, **filters)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=failure_message(exc)) from exc

    return router, cloud
