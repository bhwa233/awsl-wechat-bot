import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from cortex.routes.webui import create_routes


class WebUIAssetsTest(unittest.TestCase):
    def setUp(self):
        self.templates = Path(__file__).resolve().parents[2] / "cortex" / "templates"
        app = FastAPI()
        app.include_router(create_routes())
        app.mount("/static", StaticFiles(directory=self.templates))
        self.client = self.enterContext(TestClient(app))

    def test_module_entry(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('type="module"', response.text)

    def test_local_modules_have_javascript_mime_type(self):
        files = list(self.templates.glob("*.js")) + list((self.templates / "vendor" / "preact").glob("*.js"))
        for file in files:
            with self.subTest(file=file.name):
                response = self.client.get("/static/" + file.relative_to(self.templates).as_posix())
                self.assertEqual(response.status_code, 200)
                self.assertIn("javascript", response.headers["content-type"])
