import assert from 'node:assert/strict';
import path from 'node:path';

export async function run({ page, nav, check, state, data, calls, external, artifacts }) {
    await check('本地 Preact 加载和概览', async () => {
        await page.getByRole('heading', { name: '概览', exact: true }).waitFor();
        await page.getByText('运行中', { exact: true }).first().waitFor();
        assert.equal(external.length, 0);
        const runtime = await page.evaluate(async () => {
            const map = JSON.parse(document.querySelector('script[type="importmap"]').textContent).imports;
            const preact = await import('preact');
            return {
                local: Object.values(map).every((url) => new URL(url, location.href).origin === location.origin),
                singleton: preact === (await import(map.preact)),
                hooks: typeof (await import('preact/hooks')).useState,
            };
        });
        assert.deepEqual(runtime, { local: true, singleton: true, hooks: 'function' });
    });
    await page.screenshot({ animations: 'disabled', path: path.join(artifacts, 'dashboard.png') });
    await check('深色主题和移动端主从布局', async () => {
        await nav('chat');
        await page.locator('[data-username="room0@chatroom"]').click();
        await page.locator('#messages audio').waitFor();
        await page.locator('#chatGroupSearch').fill('开发');
        await page.locator('#themeToggle').click();
        await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
        assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
        await page.setViewportSize({ width: 390, height: 844 });
        await page.getByRole('button', { name: '打开导航', exact: true }).click();
        await nav('chat');
        await page.getByRole('button', { name: '返回会话列表', exact: true }).click();
        await page.locator('.chat-group-item').click();
        await page.locator('#chat-composer-text').waitFor({ state: 'visible' });
        await page
            .locator('#chat-composer')
            .getByRole('combobox', { name: '消息类型', exact: true })
            .selectOption('image');
        await page.locator('#chat-composer .file-dropzone').waitFor({ state: 'visible' });
        await page
            .locator('#chat-composer')
            .getByRole('combobox', { name: '消息类型', exact: true })
            .selectOption('text');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.screenshot({ animations: 'disabled', path: path.join(artifacts, 'mobile.png') });
    });
    await check('Token 更新重新加载、401 恢复与本地依赖', async () => {
        await page.setViewportSize({ width: 1440, height: 960 });
        state.requireToken = true;
        await page.locator('#tokenBtn').click();
        await page.locator('#tokenInput').fill('test-token');
        const authenticated = page.waitForResponse(
            (response) =>
                response.url().endsWith('/api/groups') &&
                response.request().headers().authorization === 'Bearer test-token'
        );
        await page
            .getByRole('dialog', { name: 'API Token' })
            .getByRole('button', { name: '保存', exact: true })
            .click();
        await authenticated;
        await page.locator('.status-text').getByText('运行中', { exact: false }).waitFor();
        assert.ok(calls.some((c) => c.token === 'Bearer test-token'));
        assert.equal(external.length, 0);
    });
    await check('401 明确反馈与认证恢复', async () => {
        await page.locator('#tokenBtn').click();
        await page.locator('#tokenInput').fill('invalid');
        await page
            .getByRole('dialog', { name: 'API Token' })
            .getByRole('button', { name: '保存', exact: true })
            .click();
        await nav('tasks');
        await page.locator('#section-tasks .request-error').getByText('认证失败，请检查 Token').waitFor();
        await page.locator('#tokenBtn').click();
        await page.locator('#tokenInput').fill('test-token');
        await page
            .getByRole('dialog', { name: 'API Token' })
            .getByRole('button', { name: '保存', exact: true })
            .click();
        await page.locator('#tasksList .res-item').first().waitFor();
    });
}
