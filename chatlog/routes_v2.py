"""Chatlog v2 HTTP 路由 - DLL 后端"""

import logging
import os
import sys
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from chatlog.dll_loader import _DLL_PATH
from chatlog.models import ChatlogGroupResponse, ChatlogMessageResponse
from chatlog.service_v2 import ChatlogServiceV2
from common.auth import verify_token
from common.config import config

logger = logging.getLogger(__name__)


def _build_router(prefix: str, tags: list[str]) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags)
    chatlog = ChatlogServiceV2()

    def _ensure_ready() -> None:
        if sys.platform != "win32":
            raise HTTPException(
                status_code=501,
                detail=f"chatlog v2 仅支持 Windows，当前平台: {sys.platform}",
            )
        if not config.has_wechat_db_config():
            raise HTTPException(status_code=400, detail="微信数据库配置不完整")
        if not os.path.isdir(config.WECHAT_DB_INPUT_PATH):
            raise HTTPException(
                status_code=400,
                detail=f"加密目录不存在: {config.WECHAT_DB_INPUT_PATH}",
            )

    @router.post("/decrypt", dependencies=[Depends(verify_token)])
    def decrypt_database():
        return {
            "success": True,
            "message": "v2 后端无需解密，DLL 直接读取加密数据库",
            "count": 0,
        }

    @router.get(
        "/groups",
        response_model=List[ChatlogGroupResponse],
        dependencies=[Depends(verify_token)],
    )
    def list_groups(limit: int = Query(0, ge=0, le=10000, description="限制返回数量，0 表示不限制")):
        _ensure_ready()
        try:
            groups = chatlog.list_groups(
                config.WECHAT_DB_INPUT_PATH, config.WECHAT_DB_KEY, limit=limit
            )
            return [
                ChatlogGroupResponse(
                    username=g.username,
                    owner=g.owner,
                    remark=g.remark,
                    nick_name=g.nick_name,
                    display_name=g.display_name(),
                )
                for g in groups
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[ChatlogV2] 列出群聊失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败，请查看服务器日志")

    @router.get(
        "/messages",
        response_model=List[ChatlogMessageResponse],
        dependencies=[Depends(verify_token)],
    )
    def query_messages(
        group: str = Query(..., description="群聊ID 或个人微信ID"),
        start: Optional[str] = Query(None, description="开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        end: Optional[str] = Query(None, description="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        limit: int = Query(100, ge=1, le=10000, description="限制返回数量"),
        offset: int = Query(0, ge=0, le=100000, description="跳过前 N 条消息"),
        desc: bool = Query(False, description="是否倒序（最新消息优先）"),
    ):
        _ensure_ready()
        start_time = end_time = None
        try:
            if start:
                fmt = "%Y-%m-%d %H:%M:%S" if " " in start else "%Y-%m-%d"
                start_time = datetime.strptime(start, fmt)
            if end:
                fmt = "%Y-%m-%d %H:%M:%S" if " " in end else "%Y-%m-%d"
                end_time = datetime.strptime(end, fmt)
                if " " not in end:
                    end_time = end_time.replace(hour=23, minute=59, second=59)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"无效的时间格式: {e}")

        try:
            messages = chatlog.get_messages(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                group,
                start_time=start_time,
                end_time=end_time,
                text_only=False,
                limit=limit,
                offset=offset,
                desc=desc,
            )
            return [
                ChatlogMessageResponse(
                    seq=m.seq,
                    time=m.time.isoformat(),
                    talker=m.talker,
                    sender=m.sender,
                    sender_name=m.sender_name,
                    msg_type=m.msg_type,
                    content=m.content,
                    is_self=m.is_self,
                )
                for m in messages
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[ChatlogV2] 查询消息失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败，请查看服务器日志")

    @router.get("/group-display-name", dependencies=[Depends(verify_token)])
    def get_group_display_name(group: str = Query(..., description="群聊ID")):
        _ensure_ready()
        try:
            display_name = chatlog.get_group_display_name(
                config.WECHAT_DB_INPUT_PATH, config.WECHAT_DB_KEY, group
            )
            return {"group": group, "display_name": display_name or group}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[ChatlogV2] 获取群名失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败")

    _emoticon_cache: dict[str, tuple[bytes, str]] = {}

    def _detect_content_type(data: bytes) -> str:
        if data[:4] == b'GIF8':
            return "image/gif"
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return "image/webp"
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return "image/png"
        if data[:2] == b'\xff\xd8':
            return "image/jpeg"
        return "application/octet-stream"

    @router.post("/emoticon", dependencies=[Depends(verify_token)])
    def decode_emoticon(body: dict):
        _ensure_ready()
        content_xml = body.get("content", "")
        if not content_xml:
            raise HTTPException(status_code=400, detail="content is required")

        import re as _re
        md5_match = _re.search(r'md5="([0-9a-fA-F]{32})"', content_xml)
        cache_key = md5_match.group(1) if md5_match else ""

        if cache_key and cache_key in _emoticon_cache:
            data, ct = _emoticon_cache[cache_key]
            from fastapi.responses import Response as RawResponse
            return RawResponse(content=data, media_type=ct)

        try:
            data = chatlog.decode_emoticon(content_xml, config.wechat_data_dir)
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[ChatlogV2] 表情解码失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="表情解码失败")

        ct = _detect_content_type(data)
        if ct == "application/octet-stream":
            raise HTTPException(status_code=400, detail="表情解码结果不是有效图片格式")
        if cache_key:
            if len(_emoticon_cache) > 500:
                _emoticon_cache.clear()
            _emoticon_cache[cache_key] = (data, ct)

        from fastapi.responses import Response as RawResponse
        return RawResponse(content=data, media_type=ct)

    @router.post("/debug/query", dependencies=[Depends(verify_token)])
    def debug_query(body: dict):
        if not config.DEBUG:
            raise HTTPException(status_code=404, detail="debug API 未启用，设置 DEBUG=true 开启")
        _ensure_ready()
        import os as _os

        db = body.get("db", "contact")
        sql = body.get("sql", "")
        limit = min(max(body.get("limit", 50), 1), 500)
        if not sql:
            raise HTTPException(status_code=400, detail="sql is required")

        if db == "contact":
            db_path = _os.path.join(
                config.WECHAT_DB_INPUT_PATH, "db_storage", "contact", "contact.db"
            )
        else:
            db_path = _os.path.join(
                config.WECHAT_DB_INPUT_PATH, "db_storage", "message", f"{db}.db"
            )
        if not _os.path.isfile(db_path):
            raise HTTPException(status_code=404, detail=f"数据库不存在: {db_path}")
        from chatlog.dll_loader import query_raw_sql

        safe_sql = sql.strip().rstrip(";")
        if "LIMIT" not in safe_sql.upper():
            safe_sql += f" LIMIT {limit}"
        try:
            result = query_raw_sql(db_path, config.WECHAT_DB_KEY, safe_sql)
            return {
                "columns": result.get("columns", []),
                "rows": (result.get("rows", []) or [])[:limit],
                "count": len(result.get("rows", []) or []),
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/debug/emoticon-cache", dependencies=[Depends(verify_token)])
    def debug_emoticon_cache(body: dict):
        if not config.DEBUG:
            raise HTTPException(status_code=404, detail="debug API 未启用")
        _ensure_ready()
        import base64 as _b64
        import glob as _glob

        md5 = body.get("md5", "").lower().strip()
        if not md5 or len(md5) != 32:
            raise HTTPException(status_code=400, detail="md5 is required (32 hex chars)")

        pattern = os.path.join(config.wechat_data_dir, "cache", "*", "Emoticon", md5[:2], md5)
        matches = _glob.glob(pattern)
        if not matches:
            return {"found": False, "pattern": pattern}

        path = matches[0]
        data = open(path, "rb").read()
        return {
            "found": True,
            "path": path,
            "size": len(data),
            "head_hex": data[:64].hex(),
            "head_b64": _b64.b64encode(data[:256]).decode(),
            "full_b64": _b64.b64encode(data).decode() if len(data) <= 100_000 else None,
        }

    @router.get("/health")
    async def health():
        ready = True
        detail = None
        if sys.platform != "win32":
            ready = False
            detail = f"仅支持 Windows，当前: {sys.platform}"
        elif not config.has_wechat_db_config():
            ready = False
            detail = "微信数据库配置不完整"
        elif not os.path.isdir(config.WECHAT_DB_INPUT_PATH):
            ready = False
            detail = f"加密目录不存在: {config.WECHAT_DB_INPUT_PATH}"
        elif not _DLL_PATH.is_file():
            ready = False
            detail = f"DLL 不存在: {_DLL_PATH}"
        status = "ok" if ready else "unavailable"
        resp = {"status": status, "backend": "v2", "platform": sys.platform}
        if ready:
            resp["wechat_data_dir"] = config.wechat_data_dir
            resp["input_path"] = config.WECHAT_DB_INPUT_PATH
        if detail:
            resp["detail"] = detail
        return resp

    return router


def create_v2_routes() -> list[APIRouter]:
    return [
        _build_router("/api/chatlog/v2", ["chatlog-v2"]),
        _build_router("/api/chatlog", ["chatlog"]),
    ]
