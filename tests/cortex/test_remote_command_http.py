import unittest
from unittest.mock import patch

import httpx

from cortex.services.command import CommandService


class RemoteCommandHTTPTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.payload = {"type": "text", "content": "result"}

        def respond(request):
            self.requests.append(request)
            return httpx.Response(200, json=self.payload)

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("cortex.services.command.httpx.Client", return_value=client):
            self.service = CommandService()
        self.service.api_base_url = "https://commands.example/api"
        self.addCleanup(self.service.close)

    def test_get_command_query(self):
        for key, params in (("ss", ""), ("a", "中文 & + ? # =")):
            with self.subTest(key=key):
                result = self.service.execute_command(key, params, "test group")
                request = self.requests[-1]
                self.assertEqual(request.method, "GET")
                self.assertEqual(request.url.path, "/api/command")
                self.assertEqual(dict(request.url.params), {"command": f"{key} {params}" if params else key})
                self.assertEqual(request.content, b"")
                self.assertEqual(result.message, "result")
                self.assertEqual(result.group_name, "test group")

    def test_binary_response_normalization(self):
        for kind, prefix, filename in (
            ("image", "data:image/png;base64,", None),
            ("file", "data:application/octet-stream;base64,", "sample.bin"),
        ):
            with self.subTest(kind=kind):
                self.payload = {"type": kind, "content": prefix + "YWJj", "filename": filename}
                result = self.service.execute_command("example", group_name="test group")
                self.assertEqual(result.message_type, kind)
                self.assertEqual(result.message, "YWJj")
                self.assertEqual(result.filename, filename)
