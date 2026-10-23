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
    await check('微信启动按钮、进度与二维码中止反馈', async () => {
        const panel = page.locator('#wechat-startup');
        const button = panel.getByRole('button', { name: '启动微信', exact: true });
        await button.click();
        await panel.getByText('正在登录微信…', { exact: true }).waitFor();
        assert.equal(await button.isDisabled(), true);
        await panel.getByText('微信已就绪', { exact: true }).waitFor();
        assert.equal(calls.filter(c => c.endpoint === '/api/console/wechat-startup' && c.method === 'POST').length, 1);
        state.startupOutcome = 'qr_required';
        await button.click();
        await panel.getByText('需要扫码，自动操作已停止', { exact: true }).waitFor();
        assert.equal(await button.isEnabled(), true);
        state.startupSupported = false;
        await nav('chat');
        await nav('dashboard');
        await page.waitForFunction(() => document.querySelector('#wechat-startup button')?.disabled);
        state.startupSupported = true;
    });
    await check('持续看护恢复进度和限流停止反馈', async () => {
        state.startupState = 'restarting';
        await nav('chat');
        await nav('dashboard');
        const panel = page.locator('#wechat-startup');
        await panel.getByText('微信已退出，正在恢复…', { exact: true }).waitFor();
        assert.equal(await panel.getByRole('button', { name: '启动微信', exact: true }).isDisabled(), true);
        state.startupState = 'restart_limited';
        await panel.getByText('恢复次数已达上限，看护已停止', { exact: true }).waitFor();
        assert.equal(await panel.getByRole('button', { name: '启动微信', exact: true }).isEnabled(), true);
        state.startupState = 'ready';
        await nav('chat');
        await nav('dashboard');
        await panel.getByText('微信已就绪', { exact: true }).waitFor();
    });
    await check('微信启动卡片跨整行、桌面与手机布局不挤压', async () => {
        await page.setViewportSize({ width: 1440, height: 960 });
        const desktop = await page.evaluate(() => {
            const card = document.querySelector('#wechat-startup').getBoundingClientRect();
            const grid = document.querySelector('.dash-grid').getBoundingClientRect();
            return { card: card.width, grid: grid.width, title: document.querySelector('#wechat-startup h3').getBoundingClientRect().height };
        });
        assert.ok(Math.abs(desktop.card - desktop.grid) <= 2);
        assert.ok(desktop.title < 30);
        for (const width of [320, 390, 768]) {
            await page.setViewportSize({ width, height: 844 });
            const size = await page.evaluate(() => {
                const card = document.querySelector('#wechat-startup').getBoundingClientRect();
                const button = document.querySelector('#wechat-startup button').getBoundingClientRect();
                return { left: card.left, right: card.right, buttonRight: button.right, title: document.querySelector('#wechat-startup h3').getBoundingClientRect().height, overflow: document.documentElement.scrollWidth > innerWidth };
            });
            assert.ok(size.left >= 0 && size.right <= width && size.buttonRight <= width);
            assert.ok(size.title < 30);
            assert.equal(size.overflow, false);
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await page.screenshot({ animations: 'disabled', path: path.join(artifacts, 'startup-mobile.png') });
        await page.setViewportSize({ width: 1440, height: 960 });
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
