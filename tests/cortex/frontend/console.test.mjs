import assert from 'node:assert/strict';

export async function run({ page, nav, check, state, data, calls }) {
    await check('控制台切换、窗口管理和消息发送', async () => {
        await nav('console');
        await page.locator('#consoleChatList .console-row').first().waitFor();
        await page.waitForFunction(() => document.querySelector('#consoleChatList .console-row')?.disabled === false);
        await page.locator('#consoleChatList [data-name="产品群"]').click();
        await page.locator('#consoleMessages').getByText('产品群独有消息').waitFor();
        const section = page.locator('#section-console');
        await section.getByRole('button', { name: '打开独立窗口', exact: true }).click();
        await page.getByRole('dialog').getByRole('button', { name: '确认', exact: true }).click();
        await section.getByRole('button', { name: '关闭独立窗口', exact: true }).waitFor();
        await page.waitForFunction(
            () =>
                ![...document.querySelectorAll('#section-console button')].find((b) => b.textContent === '关闭独立窗口')
                    .disabled
        );
        assert.equal(state.windowsOpen, true);
        await section.getByRole('button', { name: '独立窗口管理', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '独立窗口管理' });
        await dialog.getByRole('button', { name: '保存全部窗口' }).click();
        await page.waitForFunction(() =>
            [...document.querySelectorAll('[role=dialog] button')].some(
                (b) => b.textContent === '一键恢复全部' && !b.disabled
            )
        );
        assert.equal(state.cacheExists, true);
        await page.keyboard.press('Escape');
        await page.locator('#console-composer-text').fill('控制台测试');
        await page.locator('#console-composer-send').click();
        await page.waitForFunction(() => document.querySelector('#console-composer-text').value === '');
        assert.equal(calls.filter((c) => c.endpoint === '/api/console/send-message').at(-1).body.chat_name, '产品群');
    });
    await check('后台轮询不变灰、保持输入焦点与内容，发送仍串行', async () => {
        await page.waitForFunction(() => !document.querySelector('#console-composer-text').disabled);
        let release;
        const pending = new Promise((resolve) => {
            release = resolve;
        });
        const readState = async (route) => {
            await pending;
            await route.fallback();
        };
        await page.route('**/api/console/state', readState);
        const input = page.locator('#console-composer-text');
        const appearance = () =>
            page
                .locator(
                    '#consoleChatList .console-row, #section-console .chat-head-actions .btn, .console-window-manager-trigger, #console-composer-send'
                )
                .evaluateAll((elements) => elements.map((el) => getComputedStyle(el).opacity));
        try {
            const polling = page.waitForRequest((r) => r.url().endsWith('/api/console/state'));
            await input.fill('轮询前');
            await page.waitForTimeout(200);
            const idleAppearance = await appearance();
            await polling;
            await page.waitForFunction(() => document.querySelector('#console-composer-send').disabled);
            await page.waitForTimeout(200);
            assert.deepEqual(await appearance(), idleAppearance);
            assert.equal(await page.locator('#consoleChatList .console-row').first().isDisabled(), true);
            assert.equal(await input.isDisabled(), false);
            assert.equal(await input.evaluate((el) => document.activeElement === el), true);
            await page.keyboard.type('继续输入');
            const before = calls.filter((c) => c.endpoint === '/api/console/send-message').length;
            await page.keyboard.press('Enter');
            assert.equal(calls.filter((c) => c.endpoint === '/api/console/send-message').length, before);
            release();
            await page.waitForFunction(() => !document.querySelector('#console-composer-send').disabled);
            assert.equal(await input.evaluate((el) => document.activeElement === el), true);
            await page.keyboard.type('轮询后');
            assert.equal(await input.inputValue(), '轮询前继续输入轮询后');
            await page.locator('#console-composer-send').click();
            await page.waitForFunction(() => document.querySelector('#console-composer-text').value === '');
            const sends = calls.filter((c) => c.endpoint === '/api/console/send-message');
            assert.equal(sends.length, before + 1);
            assert.equal(sends.at(-1).body.message, '轮询前继续输入轮询后');
        } finally {
            release();
            await page.unroute('**/api/console/state', readState);
        }
    });
    await check('移动端控制台从隐藏列表打开会话后滚动到最新', async () => {
        await page.setViewportSize({ width: 390, height: 844 });
        state.consoleSelected = '开发群';
        await page.reload();
        await page.waitForFunction(() => document.querySelectorAll('#consoleMessages .msg').length === 100);
        await page.locator('#consoleChatList [data-name="开发群"]').click();
        await page.waitForFunction(() => {
            const el = document.querySelector('#consoleMessages');
            return el.clientHeight > 0 && el.scrollHeight - el.scrollTop - el.clientHeight < 5;
        });
        await page.setViewportSize({ width: 1440, height: 960 });
    });
    await check('未读零不附加群名，未读一单独显示且数字群名正确匹配', async () => {
        const original = { ...state.chatItems[0] };
        const renamed = '开发群01';
        const groupsRoute = async (route) =>
            route.fulfill({
                json: [
                    { username: 'room0@chatroom', display_name: ` ${renamed} ` },
                    { username: 'room1@chatroom', display_name: '产品群' },
                ],
            });
        state.chatItems[0].name = renamed;
        state.chatItems[0].unread = 1;
        state.consoleSelected = renamed;
        await page.route('**/api/chatlog/groups', groupsRoute);
        try {
            await page.reload();
            const zero = page.locator('#consoleChatList [data-name="产品群"]');
            const one = page.locator(`#consoleChatList [data-name="${renamed}"]`);
            await zero.waitFor();
            assert.equal(await zero.locator('.console-name').innerText(), '产品群');
            assert.equal(await zero.locator('.console-badge').count(), 0);
            assert.equal(await one.locator('.console-chat-name').innerText(), renamed);
            assert.equal(await one.locator('.console-badge').innerText(), '1');
            await page.locator('#consoleMessages').getByText('开发群最新消息', { exact: true }).waitFor();
            const manager = page.getByRole('button', { name: '独立窗口管理', exact: true });
            assert.equal(await manager.locator('svg rect').count(), 1);
            await page.waitForFunction(() => !document.querySelector('#console-composer-text').disabled);
            await one.click();
            await page.waitForFunction(() => !document.querySelector('#console-composer-text').disabled);
            assert.equal(calls.filter((c) => c.endpoint === '/api/console/open-chat').at(-1).body.path, original.path);
            await page.locator('#console-composer-text').fill('数字群名回归测试');
            await page.locator('#console-composer-send').click();
            await page.waitForFunction(() => document.querySelector('#console-composer-text').value === '');
            assert.equal(
                calls.filter((c) => c.endpoint === '/api/console/send-message').at(-1).body.chat_name,
                renamed
            );
        } finally {
            Object.assign(state.chatItems[0], original);
            state.consoleSelected = original.name;
            await page.unroute('**/api/chatlog/groups', groupsRoute);
            await page.reload();
        }
    });
    await check('不支持 Console 的平台明确禁用操作', async () => {
        await page.waitForFunction(() => document.querySelector('#consoleChatList .console-row')?.disabled === false);
        await nav('dashboard');
        state.consoleUnsupported = true;
        await nav('console');
        await page.locator('#section-console .request-error').getByText('Console 仅支持 Windows Gateway').waitFor();
        assert.equal(await page.locator('#console-composer-send').isDisabled(), true);
        state.consoleUnsupported = false;
    });
}
