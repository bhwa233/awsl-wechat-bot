import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { mockApi } from './fixtures.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../../../cortex/templates/', import.meta.url));
const artifacts = process.env.FRONTEND_TEST_ARTIFACTS || (await fs.mkdtemp(path.join(os.tmpdir(), 'awsl-frontend-')));
await fs.mkdir(artifacts, { recursive: true });
const allModules = ['app', 'tasks', 'webhooks', 'commands', 'chat', 'console'];
const modules = process.argv.slice(2).length ? process.argv.slice(2) : allModules;
for (const name of modules) assert.ok(allModules.includes(name), `未知测试模块：${name}`);
const server = http.createServer(async (req, res) => {
    try {
        const pathname = new URL(req.url, 'http://localhost').pathname;
        const file = pathname === '/' ? 'index.html' : pathname.replace(/^\/static\//, '');
        const data = await fs.readFile(path.join(root, file));
        const mime = {
            '.js': 'text/javascript',
            '.css': 'text/css',
            '.json': 'application/json',
            '.html': 'text/html',
        };
        res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' });
        res.end(data);
    } catch {
        res.writeHead(404);
        res.end();
    }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
let total = 0;
try {
    browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
    for (const name of modules) {
        const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
        const page = await context.newPage();
        page.setDefaultTimeout(10000);
        await page.addInitScript(() => localStorage.setItem('awsl_api_token', 'test-token'));
        const fixture = await mockApi(page, base);
        const nav = async (id) => {
            await page.locator(`.nav-item[data-section="${id}"]`).click();
            await page.locator(`#section-${id}.active`).waitFor();
        };
        const check = async (label, action) => {
            await action();
            total++;
            console.log(`PASS [${name}] ${label}`);
        };
        try {
            await page.goto(base);
            const module = await import(`./${name}.test.mjs`);
            await module.run({ page, nav, check, ...fixture, artifacts });
            assert.deepEqual(fixture.errors, []);
            assert.deepEqual(fixture.external, []);
        } catch (error) {
            console.error(`FAILED [${name}]`, error);
            console.error('PAGE ERRORS', fixture.errors);
            await page.screenshot({ animations: 'disabled', path: path.join(artifacts, `${name}-failure.png`) });
            process.exitCode = 1;
        } finally {
            await context.close();
        }
    }
    console.log(`${total} checks passed. Screenshots: ${artifacts}`);
} finally {
    await browser?.close();
    server.close();
}
