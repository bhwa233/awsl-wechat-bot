import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.auth import verify_token
from cortex.routes.custom_commands import create_routes as command_routes
from cortex.routes.tasks import create_routes as task_routes
from cortex.routes.webhooks import create_routes as webhook_routes
from cortex.services.custom_command import CustomCommandService
from cortex.services.scheduled_task import ScheduledTaskService
from cortex.services.webhook import WebhookService


class OrmServicesTest(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        app = FastAPI()
        app.dependency_overrides[verify_token] = lambda: None
        services = [
            (WebhookService(str(directory / "webhooks.db")), webhook_routes),
            (ScheduledTaskService(str(directory / "tasks.db")), task_routes),
            (CustomCommandService(str(directory / "commands.db"), Mock()), command_routes),
        ]
        for service, routes in services:
            self.addCleanup(service.close)
            app.include_router(routes(service))
        self.client = self.enterContext(TestClient(app))

    def test_webhook_crud_keeps_jsonpath_validation_at_request_boundary(self):
        for mode in ("none", "text", "jsonpath"):
            with self.subTest(mode=mode):
                response = self.client.post("/api/webhooks", json={
                    "name": "hook", "url": "http://localhost/unused", "enabled": False,
                    "reply_mode": mode, "reply_jsonpath": " $.data.reply ",
                })
                self.assertEqual(response.status_code, 200, response.text)
                webhook = response.json()
                self.assertEqual(webhook["reply_jsonpath"], "$.data.reply")
                path = f'/api/webhooks/{webhook["id"]}'
                response = self.client.put(path, json={"reply_mode": "jsonpath", "reply_jsonpath": " $.reply "})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["reply_jsonpath"], "$.reply")
                self.assertEqual(len(self.client.get("/api/webhooks").json()), 1)
                self.assertEqual(self.client.delete(path).status_code, 200)
                self.assertEqual(self.client.get("/api/webhooks").json(), [])

    def test_invalid_jsonpath_is_rejected_before_inserting_a_webhook(self):
        for fields in ({}, {"reply_jsonpath": " "}, {"reply_jsonpath": "$["}):
            with self.subTest(fields=fields):
                response = self.client.post("/api/webhooks", json={
                    "name": "hook", "url": "http://localhost/unused", "enabled": False,
                    "reply_mode": "jsonpath", **fields,
                })
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/webhooks").json(), [])

    def test_task_crud(self):
        response = self.client.post("/api/tasks", json={
            "name": "task", "cron_expression": "*/5 * * * *", "enabled": False,
            "message": "hello", "target_groups": ["test"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        path = f'/api/tasks/{response.json()["id"]}'
        response = self.client.put(path, json={"message": "updated", "cron_expression": "*/7 * * * *"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(path).json()["message"], "updated")
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_custom_command_crud(self):
        response = self.client.post("/api/custom-commands", json={
            "key": "test", "message": "hello", "enabled": False, "target_groups": ["test"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        path = f'/api/custom-commands/{response.json()["id"]}'
        response = self.client.put(path, json={"message": "updated"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(path).json()["message"], "updated")
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
