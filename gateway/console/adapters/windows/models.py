from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from gateway.adapters.windows import WindowsGroupDebugInfo
from gateway.models import GroupInfo


class WindowsStandaloneWindowInfo(GroupInfo):
    debug_info: WindowsGroupDebugInfo


class CachedStandaloneWindow(BaseModel):
    name: str
    conversation_id: str | None = None


class StandaloneWindowCache(BaseModel):
    version: Literal[2] = 2
    updated_at: datetime
    windows: list[CachedStandaloneWindow]
