import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.auth import verify_token
from cortex.routes.tasks import create_routes
from cortex.services.scheduled_task import ScheduledTaskService


class TaskCreationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = ScheduledTaskService(str(Path(self.directory.name) / "tasks.db"))
        self.addCleanup(self.service.close)
        app = FastAPI()
        app.dependency_overrides[verify_token] = lambda: None
        app.include_router(create_routes(self.service))
        self.client = self.enterContext(TestClient(app))
        self.payload = {"name": "task", "cron_expression": "0 9 * * *", "message": "hello"}

    def test_create_disabled_task(self):
        response = self.client.post("/api/tasks", json={**self.payload, "enabled": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["enabled"])

    def test_create_task_enabled_by_default(self):
        response = self.client.post("/api/tasks", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["enabled"])
