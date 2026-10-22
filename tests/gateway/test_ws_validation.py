import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock

from gateway.routes.ws import _handle_message


class WebSocketValidationTest(unittest.TestCase):
    def test_invalid_regex_returns_error_and_next_subscribe_succeeds(self):
        socket = Mock(send_text=AsyncMock())
        gateway = Mock()
        gateway.ws_subscribe.return_value = None

        async def exchange():
            await _handle_message(socket, gateway, json.dumps({"type": "subscribe", "group_filter": "["}))
            await _handle_message(socket, gateway, json.dumps({"type": "subscribe", "group_filter": "^group$"}))

        asyncio.run(exchange())
        self.assertEqual([json.loads(call.args[0])["type"] for call in socket.send_text.call_args_list],
                         ["error", "subscribe_ok"])
        gateway.ws_subscribe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
