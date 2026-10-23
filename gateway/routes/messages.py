"""
消息发送路由
"""

import asyncio
import contextlib
import logging
import re
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.requests import ClientDisconnect

from common.auth import verify_token
from common.models import MessageType
from gateway.core.gateway import Gateway
from gateway.core.models import SendTask
from gateway.models import SendMessageRequest, SendMessageResponse

logger = logging.getLogger(__name__)
router = APIRouter()
_MAX_FILE_BYTES = 100 * 1024 * 1024
_MAX_TEMP_BYTES = 256 * 1024 * 1024
_MAX_UPLOADS = 5


async def run_file_io(function, *args, limiter=None):
    """Finish disk I/O before propagating cancellation."""
    task = asyncio.create_task(anyio.to_thread.run_sync(function, *args, limiter=limiter))
    try:
        return await asyncio.shield(task)
    finally:
        while not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(task)


def discard_file(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Failed to remove the temporary file: %s", path)
        return False
    with contextlib.suppress(OSError):
        path.parent.rmdir()
    return True


def validate_filename(filename: str) -> str:
    if (
        len(filename.encode("utf-8")) > 200 or filename.endswith((" ", "."))
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)
        or re.match(r"(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]) *(\.|$)", filename)
    ):
        raise HTTPException(status_code=400, detail="Filename must be a portable basename of at most 200 UTF-8 bytes")
    return filename


def create_routes(gateway: Gateway):
    """创建路由"""
    slots = threading.BoundedSemaphore(_MAX_UPLOADS)
    cleanup_slots = anyio.CapacityLimiter(_MAX_UPLOADS)
    cleanup_tasks: set[asyncio.Task] = set()
    storage_lock = threading.Lock()
    used_bytes = 0

    def cleanup_file(path: Path, size: int):
        nonlocal used_bytes
        for retry in range(11):
            if retry:
                time.sleep(1)
            if discard_file(path):
                with storage_lock:
                    used_bytes -= size
                return

    async def cleanup_after_send(task: SendTask, size: int):
        try:
            sent = await asyncio.shield(asyncio.wrap_future(task.completion))
            if sent:
                await asyncio.sleep(gateway.sender.adapter.FILE_SEND_CLEANUP_DELAY)
            await run_file_io(cleanup_file, task.file_path, size, limiter=cleanup_slots)
        finally:
            cleanup_tasks.discard(asyncio.current_task())

    @router.post("/api/send", response_model=SendMessageResponse, dependencies=[Depends(verify_token)])
    async def send_message(request: SendMessageRequest):
        """向指定聊天窗口发送文本、图片、文件、TTS 语音或 MP3 语音"""
        error = gateway.get_send_error(request)
        if error:
            raise HTTPException(status_code=501, detail=error)
        if not gateway.send_message(request):
            raise HTTPException(status_code=503, detail="发送队列已满,请稍后重试")

        return SendMessageResponse(success=True, message="消息已加入发送队列")

    @router.post("/api/send/file", response_model=SendMessageResponse, dependencies=[Depends(verify_token)])
    async def send_file(
        request: Request,
        group_name: Annotated[str, Query(min_length=1, max_length=256)],
        filename: Annotated[str, Query(min_length=1, max_length=200)],
    ):
        nonlocal used_bytes
        if not slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="Too many concurrent uploads")
        received = 0
        path, file, queued = None, None, False

        def create_file():
            nonlocal path, file
            name = validate_filename(filename)
            path = Path(tempfile.mkdtemp(prefix="awsl-wechat-")) / name
            file = path.open("wb")

        try:
            async with asyncio.timeout(300):
                try:
                    await run_file_io(create_file)
                    async for data in request.stream():
                        size = len(data)
                        if received + size > _MAX_FILE_BYTES:
                            raise HTTPException(status_code=413, detail="File exceeds the 100 MiB limit")
                        with storage_lock:
                            if used_bytes + size > _MAX_TEMP_BYTES:
                                raise HTTPException(status_code=503, detail="Temporary storage limit exceeded")
                            received += size
                            used_bytes += size
                        await run_file_io(file.write, data)
                finally:
                    if file is not None:
                        await run_file_io(file.close)
                if not received:
                    raise HTTPException(status_code=400, detail="File must not be empty")
                task = SendTask.model_construct(
                    group_name=group_name, message_type=MessageType.FILE, filename=path.name,
                    file_path=path, completion=Future(),
                )
                queued = gateway.send_message(task)
                if not queued:
                    raise HTTPException(status_code=503, detail="Send queue is full")
                cleanup_tasks.add(asyncio.create_task(cleanup_after_send(task, received)))
            return SendMessageResponse(success=True, message="Message queued for sending")
        except TimeoutError as exc:
            raise HTTPException(status_code=408, detail="File upload timed out") from exc
        except ClientDisconnect as exc:
            raise HTTPException(status_code=400, detail="File upload interrupted") from exc
        except OSError as exc:
            raise HTTPException(status_code=507, detail="Failed to write the temporary file") from exc
        finally:
            try:
                if path is not None and not queued:
                    await run_file_io(cleanup_file, path, received, limiter=cleanup_slots)
            finally:
                slots.release()

    return router
