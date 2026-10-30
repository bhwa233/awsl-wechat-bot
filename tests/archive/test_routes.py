import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive.store import ArchiveStore
from common.config import config
from cortex.routes.archive import create_routes


class ArchiveRoutesTest(unittest.TestCase):
    def test_cortex_auth_protects_state_changes_and_status_does_not_disclose_cloud_secret(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "archive.db"
        self.enterContext(patch.multiple(config, HTTP_API_TOKEN="local-cortex-test", ARCHIVE_ENABLED=True,
                                        ARCHIVE_URL="http://127.0.0.1:8999", ARCHIVE_TOKEN="cloud-secret-not-for-browser",
                                        ARCHIVE_ACCOUNT_ID="account", ARCHIVE_SOURCE_ID="windows", ARCHIVE_STATE_PATH=str(path)))
        router, cloud = create_routes(Mock())
        self.addCleanup(cloud.close)
        store = ArchiveStore(path, "account", "windows")
        store.set_group("room@chatroom", "测试群", True)
        app = FastAPI()
        app.include_router(router)
        client = self.enterContext(TestClient(app))
        body = {"talker": "room@chatroom", "enabled": False}
        self.assertEqual(client.put("/api/archive/groups", json=body).status_code, 401)
        self.assertTrue(store.groups()[0]["enabled"])
        headers = {"Authorization": "Bearer local-cortex-test"}
        self.assertEqual(client.put("/api/archive/groups", json=body, headers=headers).status_code, 200)
        self.assertFalse(store.groups()[0]["enabled"])
        response = client.get("/api/archive/status", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cloud-secret-not-for-browser", response.text)
        self.assertFalse(response.json()["running"])


if __name__ == "__main__":
    unittest.main()
