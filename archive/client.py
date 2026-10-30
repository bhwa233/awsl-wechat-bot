"""SparkHub 归档 API 的有界 HTTP 客户端。"""

import httpx

from archive.models import encode


class ArchiveClient:
    def __init__(self, url: str, token: str):
        self.client = httpx.Client(
            base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(connect=5, read=30, write=30, pool=5), follow_redirects=False,
        )

    def close(self):
        self.client.close()

    def upload(self, body: dict):
        response = self.client.post("/v1/batches", content=encode(body).encode(), headers={"Content-Type": "application/json"})
        data = self._data(response)
        if data.get("batch_id") != body["batch_id"] or data.get("accepted") != len(body["messages"]):
            raise ValueError("云端未完整确认当前批次,保留待上传数据")

    def messages(self, account_id: str, **filters):
        response = self.client.get("/v1/messages", params={"account_id": account_id, **filters})
        return self._data(response)

    def scope(self):
        response = self.client.get("/v1/scope")
        return self._data(response)

    def heartbeat(self, body: dict):
        response = self.client.post("/v1/heartbeat", content=encode(body).encode(),
                                    headers={"Content-Type": "application/json"}, timeout=10)
        return self._data(response)

    @staticmethod
    def _data(response):
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("data"), dict):
            raise ValueError("归档服务响应不完整,保留本地进度")
        return payload["data"]


def failure_message(error: Exception) -> str:
    """不回传 URL、Token、请求体或上游错误内容。"""
    if isinstance(error, httpx.HTTPStatusError):
        return f"上游服务返回 HTTP {error.response.status_code},稍后自动重试"
    if isinstance(error, httpx.RequestError):
        return "连接失败或超时,稍后自动重试"
    if isinstance(error, ValueError) and type(error) is ValueError:
        return str(error)
    return f"归档处理失败({type(error).__name__}),进度已保留"
