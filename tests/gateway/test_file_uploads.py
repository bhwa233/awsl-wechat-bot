import asyncio
import queue
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import anyio
import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect, Request

from common.config import config
from common.models import MessageType, OutgoingMessage
from gateway.adapters.linux import LinuxWeChatAdapter
from gateway.core.models import SendTask
from gateway.core.sender import _STOP_SENDER, MessageSender
from gateway.routes import messages


class FileUploadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch('tempfile.tempdir', str(self.directory)))
        self.enterContext(patch.multiple(messages, _MAX_FILE_BYTES=100, _MAX_TEMP_BYTES=256))
        self.enterContext(patch.object(config, 'HTTP_API_TOKEN', ''))
        self.adapter = LinuxWeChatAdapter.__new__(LinuxWeChatAdapter)
        self.adapter._send_file_path = Mock(return_value=True)
        self.sender = MessageSender(self.adapter, {})
        self.sender._running = True
        self.sender.enqueue = Mock(return_value=True)
        self.gateway = Mock(sender=self.sender)
        self.gateway.get_send_error.return_value = None
        self.gateway.send_message.side_effect = self.sender.enqueue
        self.app = FastAPI()
        with patch.object(messages, 'router', APIRouter()):
            self.app.include_router(messages.create_routes(self.gateway))

    def stored_bytes(self):
        return sum(path.stat().st_size for path in self.directory.glob('*/*') if path.is_file())

    async def finish(self, path):
        call = next(call for call in self.sender.enqueue.call_args_list if call.args[0].file_path == path)
        completion = call.args[0].completion
        if not completion.done():
            completion.set_result(False)
        async with asyncio.timeout(1):
            while path.exists():
                await asyncio.sleep(.001)
        self.assertFalse(path.exists())

    async def upload_stream(self, stream, filename='file'):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://gateway') as client:
            response = await client.post('/api/send/file', content=stream,
                params={'group_name': 'group', 'filename': filename}, headers={'content-type': 'application/octet-stream'})
        if response.status_code != 200:
            raise HTTPException(response.status_code, response.text)
        return self.sender.enqueue.call_args.args[0].file_path

    async def upload(self, *chunks):
        async def stream():
            for chunk in chunks:
                yield chunk
        return await self.upload_stream(stream())

    def test_legacy_objects_stay_unchanged_and_task_fields_are_not_exposed(self):
        sender = MessageSender(self.adapter, {})
        schema = self.app.openapi()['components']['schemas']['SendMessageRequest']['properties']
        self.assertIs(SendTask, OutgoingMessage)
        self.assertNotIn('file_path', schema)
        self.assertNotIn('completion', schema)
        payload = 'Zmls' * 65536
        for message_type, message, filename in [
            (MessageType.TEXT, 'text', None),
            (MessageType.IMAGE, payload, None),
            (MessageType.FILE, payload, 'legacy.bin'),
            (MessageType.VOICE, 'voice text', None),
            (MessageType.VOICE_FILE, payload, 'voice.mp3'),
        ]:
            original = OutgoingMessage(group_name='group', message_type=message_type, message=message, filename=filename)
            self.assertTrue(sender.enqueue(original))
            queued = sender.send_queue.get_nowait()
            self.assertIs(queued, original)
            sender.send_queue.task_done()
        completion = Future()
        task = SendTask.model_construct(group_name='group', message_type=MessageType.FILE, filename='local.bin',
                                        file_path=Path('local.bin'), completion=completion)
        self.assertEqual(task.filename, 'local.bin')
        self.assertIsNone(task.message)
        self.assertNotIn('file_path', task.model_dump())
        self.assertNotIn('completion', task.model_dump())
        self.assertTrue(sender.enqueue(task))
        queued = sender.send_queue.get_nowait()
        self.assertIs(queued, task)
        self.assertIs(completion, queued.completion)
        self.assertNotIn('completion', queued.model_dump(mode='json'))
        sender.send_queue.task_done()
        with self.assertRaises(ValueError):
            OutgoingMessage(group_name='group', message_type=MessageType.FILE, filename='local.bin')

    async def test_limits_include_files_waiting_for_cleanup(self):
        first = await self.upload(b'x' * 100)
        await self.upload(b'x' * 100)
        for chunks, status in [((b'x' * 50, b'x' * 51), 413), ((b'x' * 57,), 503), ((b'',), 400)]:
            with self.assertRaises(HTTPException) as error:
                await self.upload(*chunks)
            self.assertEqual(error.exception.status_code, status)
            self.assertEqual(self.stored_bytes(), 200)
        with patch('gateway.routes.messages.asyncio.timeout', return_value=asyncio.timeout(0)):
            with self.assertRaises(HTTPException) as error:
                await self.upload(b'file')
            self.assertEqual(error.exception.status_code, 408)
        self.assertEqual(self.stored_bytes(), 200)
        await self.upload(b'x' * 56)
        await self.finish(first)
        self.assertEqual((await self.upload(b'x' * 100)).stat().st_size, 100)

    def test_base64_keeps_its_entry_point_and_stream_reuses_platform_sending(self):
        adapter = self.sender.adapter
        sender = MessageSender(adapter, {'group': Mock()})
        sender._running = True
        sender._wait_for_rate_limit = Mock()
        async def stream():
            yield b'file'
        streamed = asyncio.run(self.upload_stream(stream(), 'stream'))
        sender.enqueue(SendTask.model_construct(group_name='group', message_type=MessageType.FILE, filename=streamed.name,
                                                file_path=streamed))
        sender.enqueue(OutgoingMessage(group_name='group', message_type=MessageType.FILE, message='ZmlsZQ==', filename='base64'))
        sender.send_queue.put_nowait(_STOP_SENDER)
        with patch.object(adapter, 'send_file_to_window', wraps=adapter.send_file_to_window) as legacy:
            sender._loop()
        legacy.assert_called_once_with(sender.group_windows['group'], 'ZmlsZQ==', 'base64')
        paths = [Path(call.args[1]) for call in adapter._send_file_path.call_args_list]
        self.assertEqual(paths[0], streamed)
        self.assertEqual([path.read_bytes() for path in paths], [b'file', b'file'])
        for path in paths:
            messages.discard_file(path)

    def test_legacy_base64_keeps_filename_and_ignores_new_stream_limits(self):
        with patch.multiple(messages, _MAX_TEMP_BYTES=0, _MAX_FILE_BYTES=0):
            self.assertTrue(self.adapter.send_file_to_window(Mock(), 'ZmlsZQ==', '../legacy.txt'))
            path = Path(self.adapter._send_file_path.call_args.args[1])
        try:
            self.assertEqual(path.name, 'legacy.txt')
            self.assertEqual(path.read_bytes(), b'file')
            self.assertEqual(path.parent.parent, self.directory)
            self.assertEqual(self.stored_bytes(), 4)
        finally:
            messages.discard_file(path)

    async def test_stream_accepts_portable_names_and_rejects_invalid_ones(self):
        async def stream():
            yield b'file'
        for filename in ['../file', 'CON.txt', '..', 'file.', 'a\\b', '\u6d4b' * 190 + '.txt']:
            with self.assertRaises(HTTPException) as error:
                await self.upload_stream(stream(), filename)
            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual(list(self.directory.iterdir()), [])
        filename = '\u6d4b' * 60 + '.txt'
        path = await self.upload_stream(stream(), filename)
        self.assertEqual(path.name, filename)
        self.assertEqual(path.read_bytes(), b'file')

    async def test_success_keeps_storage_reserved_until_cleanup_runs(self):
        path = await self.upload(b'x' * 100)
        completion = self.sender.enqueue.call_args.args[0].completion
        self.assertTrue(path.exists())
        await self.upload(b'x' * 100)
        original_sleep = asyncio.sleep
        with patch.object(messages.asyncio, 'sleep', new_callable=AsyncMock) as sleep, \
                patch.object(messages.time, 'sleep'), \
                patch.object(messages, 'discard_file', return_value=False) as discard, \
                patch.object(messages, 'run_file_io', wraps=messages.run_file_io) as file_io:
            completion.set_result(True)
            async with asyncio.timeout(1):
                while discard.call_count < 11:
                    await original_sleep(.001)
            cleanup_args = file_io.call_args.args
        sleep.assert_awaited_once_with(180)
        self.assertEqual(discard.call_count, 11)
        with self.assertRaises(HTTPException) as error:
            await self.upload(b'x' * 57)
        self.assertEqual(error.exception.status_code, 503)
        await messages.run_file_io(*cleanup_args)
        self.assertFalse(path.exists())
        self.assertEqual((await self.upload(b'x' * 100)).stat().st_size, 100)

    async def test_concurrency_and_interrupted_upload_cleanup(self):
        ready = [asyncio.Event() for _ in range(5)]
        release = asyncio.Event()

        async def stream(index):
            yield b'partial'
            ready[index].set()
            await release.wait()
            raise ClientDisconnect()

        tasks = [asyncio.create_task(self.upload_stream(stream(i), str(i))) for i in range(5)]
        try:
            await asyncio.gather(*(event.wait() for event in ready))
            with self.assertRaises(HTTPException) as error:
                await self.upload(b'sixth')
            self.assertEqual(error.exception.status_code, 429)
            tasks[0].cancel()
        finally:
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertTrue(all(isinstance(result, HTTPException) for result in results[1:]))
        self.assertEqual(self.stored_bytes(), 0)
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_budget_is_released_during_an_upload_after_original_cleanup(self):
        first = await self.upload(b'x' * 100)
        await self.upload(b'x' * 100)

        async def stream():
            yield b'x' * 50
            await self.finish(first)
            yield b'x' * 50

        self.assertEqual((await self.upload_stream(stream())).stat().st_size, 100)
        self.assertEqual(self.stored_bytes(), 200)

    async def test_api_reuses_queue_and_keeps_base64(self):
        gateway, app = self.gateway, self.app
        with TestClient(app) as client, patch.object(config, 'HTTP_API_TOKEN', 'secret'):
            params = {'group_name': 'group', 'filename': 'report.txt'}
            headers = {'Authorization': 'Bearer secret', 'Content-Type': 'application/octet-stream'}
            self.assertEqual(client.post('/api/send/file', params=params, content=b'file').status_code, 401)
            self.assertEqual(client.post('/api/send/file', params=params, content=b'file', headers=headers).status_code, 200)
            task = self.sender.enqueue.call_args.args[0]
            self.assertEqual(task.file_path.read_bytes(), b'file')
            self.assertEqual(task.file_path.parent.parent, self.directory)
            self.assertNotIn('/', task.filename)
            messages.discard_file(task.file_path)
            self.assertEqual(client.post('/api/send', headers={'Authorization': 'Bearer secret'}, json={
                'group_name': 'group', 'message_type': 'file', 'filename': 'old', 'message': 'ZmlsZQ==',
                'file_path': '/tmp/injected', 'completion': 'injected',
            }).status_code, 200)
            self.assertEqual(gateway.send_message.call_args.args[0].message, 'ZmlsZQ==')
            self.assertIsNone(gateway.send_message.call_args.args[0].file_path)
            self.assertIsNone(gateway.send_message.call_args.args[0].completion)
            self.sender.enqueue.return_value = False
            self.assertEqual(client.post('/api/send/file', params=params, content=b'file', headers=headers).status_code, 503)
            self.assertEqual(self.stored_bytes(), 0)

    async def test_full_queue_cleans_up_after_receiving_the_body(self):
        self.sender.send_queue = queue.Queue(maxsize=1)
        self.sender.send_queue.put_nowait(_STOP_SENDER)
        self.sender.enqueue.side_effect = lambda task: MessageSender.enqueue(self.sender, task)
        read = []

        async def stream():
            read.append(True)
            yield b'file'

        with self.assertRaises(HTTPException) as error, self.assertLogs('gateway.core.sender', level='WARNING'):
            await self.upload_stream(stream())
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(read, [True])
        self.sender.enqueue.assert_called_once()
        self.assertEqual(list(self.directory.iterdir()), [])
        self.sender.send_queue.get_nowait()
        self.sender.send_queue.task_done()
        self.assertEqual((await self.upload(b'ok')).read_bytes(), b'ok')

    async def test_stop_preserves_legacy_order_and_finishes_queued_files(self):
        path = await self.upload(b'file')
        uploaded = self.sender.enqueue.call_args.args[0]
        entered, release = threading.Event(), threading.Event()

        def send_text(window, text, **kwargs):
            if text == 'first':
                entered.set()
                self.assertTrue(release.wait(2))
            return True

        self.adapter.send_text_to_window = Mock(side_effect=send_text)
        self.adapter.FILE_SEND_CLEANUP_DELAY = 0
        sender = MessageSender(self.adapter, {'group': Mock()})
        sender._wait_for_rate_limit = Mock()
        sender.start()
        try:
            sender.enqueue(OutgoingMessage(group_name='group', message='first'))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            sender.enqueue(OutgoingMessage(group_name='group', message='second'))
            sender.enqueue(uploaded)
            stopped = asyncio.create_task(asyncio.to_thread(sender.stop))
            async with asyncio.timeout(1):
                while sender._running:
                    await asyncio.sleep(.001)
            release.set()
            await stopped
        finally:
            release.set()
            await asyncio.to_thread(sender.stop)
        self.assertFalse(sender._thread.is_alive())
        self.assertEqual([call.args[1] for call in self.adapter.send_text_to_window.call_args_list], ['first', 'second'])
        self.adapter._send_file_path.assert_called_once_with(sender.group_windows['group'], str(path), cleanup=False)
        self.assertTrue(uploaded.completion.result())
        async with asyncio.timeout(1):
            while path.exists():
                await asyncio.sleep(.001)
        self.assertFalse(path.exists())

    async def test_queue_rejection_cancellation_waits_for_cleanup(self):
        self.sender.enqueue.return_value = False
        started, release = threading.Event(), threading.Event()
        discard = messages.discard_file

        def slow(path):
            started.set()
            release.wait(2)
            return discard(path)

        with patch.object(messages, 'discard_file', slow):
            task = asyncio.create_task(self.upload(b'file'))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_sender_reports_completion_without_deleting_the_streamed_file(self):
        adapter = LinuxWeChatAdapter.__new__(LinuxWeChatAdapter)
        adapter._send_file_path = Mock(side_effect=RuntimeError('send failed'))
        adapter._schedule_temp_cleanup = Mock()
        sender = MessageSender(adapter, {'group': Mock()})
        sender._running = True
        sender._wait_for_rate_limit = Mock()
        async def stream():
            yield b'file'
        path = asyncio.run(self.upload_stream(stream(), 'file'))
        completion = Future()
        sender.enqueue(SendTask.model_construct(group_name='group', message_type=MessageType.FILE,
                                                filename=path.name, file_path=path, completion=completion))
        sender.send_queue.put_nowait(_STOP_SENDER)
        with self.assertLogs('gateway.core.sender', level='ERROR'):
            sender._loop()
        adapter._schedule_temp_cleanup.assert_not_called()
        self.assertFalse(completion.result())
        self.assertTrue(path.exists())
        messages.discard_file(path)
        with self.assertRaises(RuntimeError):
            adapter.send_file_to_window(Mock(), 'ZmlsZQ==', 'file')
        path = Path(adapter._schedule_temp_cleanup.call_args.args[0])
        self.assertTrue(path.exists())
        messages.discard_file(path)

    async def test_api_waits_for_completion_without_blocking_other_requests(self):
        path = await self.upload(b'file')
        completion = self.sender.enqueue.call_args.args[0].completion
        self.assertFalse(completion.done())
        self.assertTrue(path.exists())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://gateway') as client:
            response = await asyncio.wait_for(client.post('/api/send', json={
                'group_name': 'group', 'message_type': 'file', 'filename': 'base64', 'message': 'ZmlsZQ==',
            }), .5)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.sender.enqueue.call_args.args[0].completion)
        self.assertTrue(path.exists())
        self.app.add_api_route('/ordinary', lambda: {'ok': True}, methods=['GET'])
        started, release = threading.Event(), threading.Event()
        discard = messages.discard_file
        limiter = anyio.to_thread.current_default_thread_limiter()
        original_limit = limiter.total_tokens

        def slow_delete(path):
            started.set()
            release.wait(2)
            return discard(path)

        try:
            limiter.total_tokens = 1
            with patch.object(messages, 'discard_file', slow_delete):
                completion.set_result(False)
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://gateway') as client:
                    self.assertEqual((await asyncio.wait_for(client.get('/ordinary'), .5)).status_code, 200)
        finally:
            release.set()
            limiter.total_tokens = original_limit
        await self.finish(path)

    async def test_five_slow_uploads_do_not_block_other_requests_or_buffer_the_body(self):
        app = self.app
        ready = [asyncio.Event() for _ in range(5)]
        release = asyncio.Event()

        async def stream(index):
            yield b'first'
            ready[index].set()
            await release.wait()
            yield b'second'

        original_body = Request.body

        async def body(request):
            if request.url.path == '/api/send/file':
                raise AssertionError('Streaming uploads must not buffer the request body')
            return await original_body(request)

        with patch.object(config, 'HTTP_API_TOKEN', ''), patch.object(Request, 'body', body), \
                patch.object(Request, 'form', side_effect=AssertionError('Streaming uploads must not parse multipart forms')), \
                patch('tempfile.SpooledTemporaryFile', side_effect=AssertionError('Streaming uploads must not create spool files')):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as client:
                params = {'group_name': 'group', 'filename': 'file'}
                headers = {'content-type': 'application/octet-stream'}
                tasks = [
                    asyncio.create_task(client.post('/api/send/file', params=params, headers=headers, content=stream(i)))
                    for i in range(5)
                ]
                try:
                    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), 2)
                    self.assertEqual(self.sender.enqueue.call_count, 0)
                    ordinary = await asyncio.wait_for(client.post('/api/send', json={'group_name': 'group', 'message': 'text'}), 2)
                    self.assertEqual(ordinary.status_code, 200)
                    sixth = await asyncio.wait_for(client.post('/api/send/file', params=params, headers=headers, content=b'sixth'), 2)
                    self.assertEqual(sixth.status_code, 429)
                finally:
                    release.set()
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                self.assertEqual([result.status_code for result in results], [200] * 5)

    async def test_file_io_cancellation_keeps_api_responsive_and_releases_files_and_slots(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://gateway') as client:
            for method in ['open', 'write', 'close']:
                for after in [False, True]:
                    started, release = threading.Event(), threading.Event()
                    original_open, opened = Path.open, []

                    def slow(function, *args, after=after, started=started, release=release):
                        result = function(*args) if after else None
                        started.set()
                        release.wait(2)
                        return result if after else function(*args)

                    def open_file(path, *args, method=method, original_open=original_open, opened=opened, slow=slow, **kwargs):
                        file = slow(original_open, path, *args) if method == 'open' else original_open(path, *args, **kwargs)
                        opened.append(file)
                        wrapper = Mock(wraps=file)
                        if method != 'open':
                            wrapper_method = getattr(wrapper, method)
                            wrapper_method.side_effect = lambda *args: slow(getattr(file, method), *args)
                        return wrapper

                    with patch.object(Path, 'open', open_file):
                        task = asyncio.create_task(self.upload(b'file'))
                        try:
                            self.assertTrue(await asyncio.to_thread(started.wait, 1))
                            response = await asyncio.wait_for(
                                client.post('/api/send', json={'group_name': 'group', 'message': 'text'}), .5,
                            )
                            self.assertEqual(response.status_code, 200)
                            for _ in range(3):
                                task.cancel()
                                await asyncio.sleep(0)
                        finally:
                            release.set()
                            result = (await asyncio.gather(task, return_exceptions=True))[0]
                    self.assertIsInstance(result, asyncio.CancelledError)
                    self.assertTrue(all(file.closed for file in opened))
                    self.assertEqual(list(self.directory.iterdir()), [])
                    for path in await asyncio.gather(*(self.upload(b'ok') for _ in range(5))):
                        messages.discard_file(path)

    async def test_upload_burst_cannot_exhaust_threads_before_admission(self):
        app = FastAPI()

        @app.get('/ordinary')
        def ordinary():
            return {'ok': True}

        started, release = threading.Event(), threading.Event()
        original = Path.open

        def slow(path, *args, **kwargs):
            started.set()
            release.wait(2)
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', slow):
            tasks = [asyncio.create_task(self.upload(b'file')) for _ in range(40)]
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                for _ in range(20):
                    if sum(task.done() for task in tasks) == 35:
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(sum(task.done() for task in tasks), 35)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as client:
                    self.assertEqual((await asyncio.wait_for(client.get('/ordinary'), .5)).status_code, 200)
            finally:
                release.set()
                results = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(sum(isinstance(result, Path) for result in results), 5)
        self.assertEqual([result.status_code for result in results if isinstance(result, HTTPException)], [429] * 35)
