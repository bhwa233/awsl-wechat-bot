import assert from 'node:assert/strict';
import path from 'node:path';

export async function run({ page, nav, check, state, data, calls, artifacts }) {
    await check('慢速 Gateway 下恢复会话并保留手动发送目标', async () => {
        await page.locator('.status-text').filter({ hasText: '运行中' }).waitFor();
        await page.evaluate(() => localStorage.setItem('chatlogGroup', 'room0@chatroom'));
        let release;
        const gatewayReady = new Promise((resolve) => {
            release = resolve;
        });
        const delayGateway = async (route) => {
            await gatewayReady;
            await route.fallback();
        };
        await page.route('**/api/groups', delayGateway);
        try {
            await page.goto(new URL('/#/chat', page.url()).href);
            await page.reload();
            await page.locator('.chat-group-item.active[data-username="room0@chatroom"]').waitFor();
            assert.equal(await page.locator('#sendWindowSelect').inputValue(), '');
            release();
            await page.waitForFunction(() => document.querySelector('#sendWindowSelect').value === '开发群');
        } finally {
            release();
            await page.unroute('**/api/groups', delayGateway);
        }
        await page.locator('#sendWindowSelect').selectOption('产品群');
        const refreshed = page.waitForResponse((r) => r.url().endsWith('/api/groups'));
        await page.getByRole('button', { name: '刷新会话列表', exact: true }).click();
        await refreshed;
        await page.locator('#section-chat .loading-hint').waitFor({ state: 'detached' });
        assert.equal(await page.locator('#sendWindowSelect').inputValue(), '产品群');
        await page.locator('#chat-composer-text').fill('指定发送目标');
        await page.locator('#chat-composer-send').click();
        await page.waitForFunction(() => document.querySelector('#chat-composer-text').value === '');
        assert.equal(calls.filter((call) => call.endpoint === '/api/send').at(-1).body.group_name, '产品群');
        await page.locator('#sendWindowSelect').selectOption('');
        const clearedRefresh = page.waitForResponse((r) => r.url().endsWith('/api/groups'));
        await page.getByRole('button', { name: '刷新会话列表', exact: true }).click();
        await clearedRefresh;
        await page.locator('#section-chat .loading-hint').waitFor({ state: 'detached' });
        assert.equal(await page.locator('#sendWindowSelect').inputValue(), '');
        await nav('tasks');
        await nav('chat');
        await page.locator('#messages audio').waitFor();
        assert.equal(await page.locator('#sendWindowSelect').inputValue(), '');
        await page.locator('[data-username="room1@chatroom"]').click();
        await page.waitForFunction(() => document.querySelector('#sendWindowSelect').value === '产品群');
        await page.locator('[data-username="room0@chatroom"]').click();
        await page.waitForFunction(() => document.querySelector('#sendWindowSelect').value === '开发群');
    });
    await check('右键引用、取消、发送与切换会话清理', async () => {
        const original = page.locator('#messages').getByText('开发群最新消息', { exact: true });
        await original.click({ button: 'right' });
        await page.getByRole('menuitem', { name: '引用', exact: true }).click();
        await page.locator('#chat-composer .composer-reply').waitFor();
        assert.equal(await page.locator('#chat-composer .composer-reply span').innerText(), '开发群最新消息');
        await page.screenshot({ path: path.join(artifacts, 'chat-reply.png'), animations: 'disabled' });
        await page.getByRole('button', { name: '取消引用', exact: true }).click();
        assert.equal(await page.locator('#chat-composer .composer-reply').count(), 0);
        await original.click({ button: 'right' });
        await page.getByRole('menuitem', { name: '引用', exact: true }).waitFor();
        await page.keyboard.press('Escape');
        await page.getByRole('menu').waitFor({ state: 'detached' });
        await original.click({ button: 'right' });
        await page.getByRole('menuitem', { name: '引用', exact: true }).click();
        await page.locator('#chat-composer-text').fill('明确引用回复');
        await page.locator('#chat-composer-send').click();
        await page.waitForFunction(() => document.querySelector('#chat-composer-text').value === '');
        assert.equal(calls.filter((c) => c.endpoint === '/api/send').at(-1).body.reply_to_message, '开发群最新消息');
        assert.equal(await page.locator('#chat-composer .composer-reply').count(), 0);
        await original.click({ button: 'right' });
        await page.getByRole('menuitem', { name: '引用', exact: true }).click();
        await page.locator('[data-username="room1@chatroom"]').click();
        assert.equal(await page.locator('#chat-composer .composer-reply').count(), 0);
        await page.locator('[data-username="room0@chatroom"]').click();
    });
    await check('聊天分页、搜索与切换会话', async () => {
        await nav('chat');
        await page.locator('.chat-group-item').first().waitFor();
        assert.equal(await page.locator('.chat-group-item').count(), 40);
        await page.getByRole('button', { name: /加载更多（剩余/ }).click();
        assert.equal(await page.locator('.chat-group-item').count(), 80);
        await page.locator('[data-username="room0@chatroom"]').click();
        await page.locator('#messages').getByText('开发群最新消息', { exact: true }).waitFor();
        await page.getByRole('button', { name: '↑ 更早消息', exact: true }).click();
        await page.locator('#messages').getByText('更早的消息').waitFor();
        await page.getByRole('button', { name: '回到最新', exact: true }).click();
        await page.locator('#messages').getByText('开发群最新消息', { exact: true }).waitFor();
        state.delayA = true;
        await page.locator('#section-chat .chat-head').getByRole('button', { name: '刷新', exact: true }).click();
        await page.locator('[data-username="room1@chatroom"]').click();
        await page.locator('#messages').getByText('产品群独有消息').waitFor();
        await page.waitForTimeout(700);
        assert.equal(await page.locator('#messages').getByText('开发群最新消息', { exact: true }).count(), 0);
        state.delayA = false;
        await page.locator('#chatGroupSearch').fill('开发');
        assert.equal(await page.locator('.chat-group-item').count(), 1);
        await page.locator('.chat-group-item').click();
        await page.locator('#messages audio').waitFor();
    });
    await check('消息更新保留音频节点与成员详情', async () => {
        await page.evaluate(() => {
            window.savedAudio = document.querySelector('#messages audio');
            window.savedAudio.dataset.qa = 'retained';
        });
        state.added = true;
        await page.locator('#section-chat .chat-head').getByRole('button', { name: '刷新', exact: true }).click();
        await page.locator('#messages').getByText('轮询新增消息', { exact: true }).waitFor();
        assert.equal(await page.evaluate(() => window.savedAudio === document.querySelector('#messages audio')), true);
        await page.locator('#messages .mention-link').click();
        await page.getByRole('dialog', { name: '小明', exact: true }).waitFor();
        await page.keyboard.press('Escape');
        await page.locator('#messages').getByRole('button').filter({ hasText: '转发记录' }).click();
        await page.getByRole('dialog', { name: '转发记录', exact: true }).getByText('转发正文').waitFor();
        await page.keyboard.press('Escape');
    });
    await page.screenshot({ animations: 'disabled', path: path.join(artifacts, 'chat.png') });
    await check('输入法回车保护、发送与防重复提交', async () => {
        const input = page.locator('#chat-composer-text');
        await input.fill('消息测试');
        const before = calls.filter((c) => c.endpoint === '/api/send').length;
        await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
        assert.equal(calls.filter((c) => c.endpoint === '/api/send').length, before);
        await page.evaluate(() => {
            const button = document.querySelector('#chat-composer-send');
            button.click();
            button.click();
        });
        await page.waitForFunction(() => document.querySelector('#chat-composer-text').value === '');
        assert.equal(calls.filter((c) => c.endpoint === '/api/send').length, before + 1);
    });
    await check('消息失败保留记录、后台暂停轮询', async () => {
        await nav('chat');
        await page.locator('[data-username="room0@chatroom"]').click();
        await page.locator('#messages audio').waitFor();
        state.failMessages = true;
        await page.locator('#section-chat .chat-head').getByRole('button', { name: '刷新', exact: true }).click();
        await page.locator('#section-chat .request-error').getByText('消息读取失败').waitFor();
        assert.equal(await page.locator('#messages audio').count(), 1);
        state.failMessages = false;
        await page.locator('#section-chat .request-error').getByRole('button', { name: '重试' }).click();
        await page.locator('#section-chat .request-error').waitFor({ state: 'detached' });
        await nav('tasks');
        await page.waitForTimeout(200);
        const before = calls.filter((call) => call.endpoint === '/api/chatlog/messages').length;
        await page.waitForTimeout(5300);
        assert.equal(calls.filter((call) => call.endpoint === '/api/chatlog/messages').length, before);
    });
    await check('图片预览、缩放与文件下载地址', async () => {
        await nav('chat');
        await page.locator('#messages .msg-img').click();
        const dialog = page.getByRole('dialog', { name: '图片预览', exact: true });
        await dialog.getByRole('button', { name: '放大', exact: true }).click();
        await dialog.getByText('125%', { exact: true }).waitFor();
        await page.keyboard.press('Escape');
        const link = page.locator('#messages a[download]');
        assert.equal(await link.getAttribute('download'), '报告.pdf');
        assert.ok((await link.getAttribute('href')).includes('token=test-token'));
    });
    await check('移动端恢复会话显示最新消息并保留历史滚动位置', async () => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.reload();
        await page.waitForFunction(() => document.querySelectorAll('#messages .msg').length === 100);
        await page.locator('[data-username="room0@chatroom"]').click();
        await page.waitForFunction(() => {
            const el = document.querySelector('#messages');
            return el.clientHeight > 0 && el.scrollHeight - el.scrollTop - el.clientHeight < 5;
        });
        await page.locator('#messages').evaluate((el) => {
            el.scrollTop = 200;
        });
        await page.waitForTimeout(100);
        await page.getByRole('button', { name: '返回会话列表', exact: true }).click();
        await page.locator('[data-username="room0@chatroom"]').click();
        await page.waitForFunction(() => document.querySelector('#messages').clientHeight > 0);
        await page.waitForTimeout(100);
        assert.ok(Math.abs((await page.locator('#messages').evaluate((el) => el.scrollTop)) - 200) < 5);
        await page.setViewportSize({ width: 1440, height: 960 });
    });
    await check('健康检查失败仍可读取会话，恢复后重新启用媒体', async () => {
        const failHealth = (route) => route.fulfill({ status: 502, json: { detail: '健康检查暂时失败' } });
        await page.route('**/api/chatlog/health', failHealth);
        try {
            await nav('chat');
            await page.reload();
            await page.locator('#section-chat .request-error').getByText('健康检查暂时失败').waitFor();
            assert.equal(await page.locator('.chat-sidebar-count').innerText(), '85');
            await page.locator('#messages').getByText('开发群最新消息', { exact: true }).waitFor();
            assert.equal(await page.locator('#messages audio').count(), 0);
            await nav('console');
            await page.locator('#consoleMessages').getByText('开发群最新消息', { exact: true }).waitFor();
        } finally {
            await page.unroute('**/api/chatlog/health', failHealth);
        }
        await nav('chat');
        await page.locator('#section-chat .request-error').getByRole('button', { name: '重试' }).click();
        await page.locator('#section-chat .request-error').waitFor({ state: 'detached' });
        await page.locator('#messages audio').waitFor();
    });
    await check('群列表失败保留健康状态，重试后恢复缓存会话', async () => {
        const failGroups = (route) => route.fulfill({ status: 502, json: { detail: '会话读取暂时失败' } });
        await page.route('**/api/chatlog/groups', failGroups);
        try {
            await page.reload();
            await page.locator('#section-chat .request-error').getByText('会话读取暂时失败').waitFor();
            await page.getByRole('button', { name: '解密', exact: true }).waitFor();
            assert.equal(await page.locator('.chat-group-item').count(), 0);
        } finally {
            await page.unroute('**/api/chatlog/groups', failGroups);
        }
        await page.locator('#section-chat .request-error').getByRole('button', { name: '重试' }).click();
        await page.locator('.chat-group-item.active[data-username="room0@chatroom"]').waitFor();
        await page.locator('#messages audio').waitFor();
        await page.route('**/api/chatlog/groups', failGroups);
        try {
            await page.getByRole('button', { name: '刷新会话列表', exact: true }).click();
            await page.locator('#section-chat .request-error').getByText('会话读取暂时失败').waitFor();
            assert.equal(await page.locator('.chat-sidebar-count').innerText(), '85');
            assert.equal(await page.locator('#messages audio').count(), 1);
        } finally {
            await page.unroute('**/api/chatlog/groups', failGroups);
        }
        await page.locator('#section-chat .request-error').getByRole('button', { name: '重试' }).click();
        await page.locator('#section-chat .request-error').waitFor({ state: 'detached' });
    });
    await check('聊天与控制台刷新可重试失败图片并保留正常媒体节点', async () => {
        for (const [section, list] of [
            ['chat', '#messages'],
            ['console', '#consoleMessages'],
        ]) {
            let fail = true;
            let requests = 0;
            const media = async (route) => {
                if (new URL(route.request().url()).searchParams.get('msg_type') !== '3') return route.fallback();
                requests++;
                if (fail) return route.fulfill({ status: 502, json: { detail: '媒体读取暂时失败' } });
                return route.fallback();
            };
            await page.route('**/api/chatlog/media?**', media);
            try {
                await nav(section);
                await page.reload();
                await page.locator(`${list} .media-fallback`).getByText('[图片]', { exact: true }).waitFor();
                await page.locator(`${list} audio`).evaluate((el) => {
                    window.savedAudio = el;
                });
                const before = requests;
                fail = false;
                const refresh = page
                    .locator(`#section-${section} .chat-head`)
                    .getByRole('button', { name: '刷新', exact: true });
                await refresh.click();
                await page.waitForFunction((selector) => {
                    const img = document.querySelector(`${selector} img.msg-img`);
                    return img?.complete && img.naturalWidth > 0;
                }, list);
                assert.ok(requests > before);
                assert.equal(await page.locator(`${list} .media-fallback`).count(), 0);
                assert.equal(await page.locator(`${list} audio`).evaluate((el) => el === window.savedAudio), true);
                await page.locator(`${list} img.msg-img`).evaluate((el) => {
                    window.savedImage = el;
                });
                const healthyRequests = requests;
                const refreshed = page.waitForResponse((r) => r.url().includes('/api/chatlog/messages?'));
                await refresh.click();
                await refreshed;
                await page.locator(`#section-${section} .loading-hint`).waitFor({ state: 'detached' });
                assert.equal(
                    await page.locator(`${list} img.msg-img`).evaluate((el) => el === window.savedImage),
                    true
                );
                assert.equal(await page.locator(`${list} audio`).evaluate((el) => el === window.savedAudio), true);
                assert.equal(requests, healthyRequests);
            } finally {
                await page.unroute('**/api/chatlog/media?**', media);
            }
        }
    });
}
