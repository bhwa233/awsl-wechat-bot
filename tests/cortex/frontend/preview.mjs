import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mockApi } from './fixtures.mjs';

const root = fileURLToPath(new URL('../../../cortex/templates/', import.meta.url));
const port = Number(process.env.MOCK_PORT || 7997);
const base = `http://127.0.0.1:${port}`;
let handleApi;

// 将浏览器测试的 Mock 路由适配为 HTTP，复用同一份模拟数据。
await mockApi(
    {
        on() {},
        async route(_pattern, handler) {
            handleApi = handler;
        },
    },
    base
);

const server = http.createServer(async (req, res) => {
    try {
        const url = new URL(req.url, base);
        if (url.pathname.startsWith('/api/')) {
            const chunks = [];
            for await (const chunk of req) chunks.push(chunk);
            const body = Buffer.concat(chunks).toString('utf8');
            await handleApi({
                request: () => ({
                    url: () => url.href,
                    method: () => req.method,
                    headers: () => req.headers,
                    postData: () => body,
                    postDataJSON: () => JSON.parse(body),
                }),
                fulfill: async ({ status = 200, contentType, body: payload }) => {
                    res.writeHead(status, { 'Content-Type': contentType, 'Cache-Control': 'no-store' });
                    res.end(payload);
                },
            });
            return;
        }
        if (url.pathname === '/manifest.json') {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ name: 'AWSL Mock', start_url: '/', display: 'standalone' }));
            return;
        }
        if (url.pathname !== '/' && !url.pathname.startsWith('/static/')) {
            res.writeHead(404).end();
            return;
        }
        const file = path.resolve(
            root,
            url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname.slice(8))
        );
        if (!file.startsWith(root)) {
            res.writeHead(404).end();
            return;
        }
        let content = await fs.readFile(file);
        if (url.pathname === '/')
            content = content.toString().replace('<title>AWSL WeChat Bot</title>', '<title>AWSL Mock 预览</title>');
        const mime = {
            '.html': 'text/html',
            '.js': 'text/javascript',
            '.css': 'text/css',
            '.json': 'application/json',
        };
        res.writeHead(200, {
            'Content-Type': mime[path.extname(file)] || 'application/octet-stream',
            'Cache-Control': 'no-store',
        });
        res.end(content);
    } catch (error) {
        res.writeHead(error.code === 'ENOENT' ? 404 : 400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ detail: error.code === 'ENOENT' ? '文件不存在' : 'Mock 请求无效' }));
    }
});
server.listen(port, '127.0.0.1', () => console.log(`Mock 预览：${base}/（模拟 API，数据仅保存在内存中）`));
