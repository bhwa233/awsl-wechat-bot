import assert from 'node:assert/strict';

export async function run({ page, nav, check, state, data }) {
    await check('自定义命令筛选和图片附件', async () => {
        await nav('custom-commands');
        await page.locator('#customCommandsList .res-item').first().waitFor();
        assert.equal(await page.locator('#customCommandsList .res-icon').count(), 0);
        assert.deepEqual(await page.locator('#customCommandsList .res-name').allTextContents(), ['menu', 'dev']);
        await page.locator('#customCommandGroupFilter').selectOption('__global__');
        assert.equal(await page.locator('#customCommandsList .res-item').count(), 1);
        await page.getByRole('button', { name: '新建命令', exact: true }).click();
        const form = page.locator('#customCommandForm');
        await form.getByLabel('命令 Key').fill('picture');
        await form.getByRole('button', { name: '图片', exact: true }).click();
        await form.locator('input[type=file]').setInputFiles({
            name: 'test.png',
            mimeType: 'image/png',
            buffer: Buffer.from(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH9kAAAAASUVORK5CYII=',
                'base64'
            ),
        });
        await form.getByAltText('附件预览').waitFor();
        await form.getByRole('button', { name: '创建', exact: true }).click();
        await form.waitFor({ state: 'detached' });
        assert.equal(data['custom-commands'].find((item) => item.key === 'picture').action_type, 'image');
    });
    await check('远程命令策略编辑与保存', async () => {
        await nav('remote-commands');
        const section = page.locator('#section-remote-commands');
        await section.getByRole('button', { name: '编辑', exact: true }).click();
        await section.getByLabel('help帮助').check();
        await section.getByLabel('开发群', { exact: true }).check();
        await section.getByRole('button', { name: '保存', exact: true }).click();
        await section.getByRole('button', { name: '编辑', exact: true }).waitFor();
        assert.deepEqual(state.policy.allowlist, ['help']);
        assert.deepEqual(state.policy.bypass_groups, ['开发群']);
    });
    await check('重新进入远程命令页读取最新策略再编辑', async () => {
        await nav('tasks');
        state.policy = { ...state.policy, allowlist: ['news'], bypass_groups: ['产品群'] };
        await nav('remote-commands');
        const section = page.locator('#section-remote-commands');
        await section
            .locator('.remote-cmd-row')
            .filter({ hasText: 'help' })
            .getByText('未白名单', { exact: true })
            .waitFor();
        await section
            .locator('.remote-cmd-row')
            .filter({ hasText: 'news' })
            .getByText('白名单', { exact: true })
            .waitFor();
        await section.getByRole('button', { name: '编辑', exact: true }).click();
        assert.equal(await section.getByLabel('news新闻').isChecked(), true);
        assert.equal(await section.getByLabel('产品群', { exact: true }).isChecked(), true);
        await section.getByLabel('help帮助').check();
        await section.getByRole('button', { name: '保存', exact: true }).click();
        await section.getByRole('button', { name: '编辑', exact: true }).waitFor();
        assert.deepEqual(state.policy.allowlist, ['news', 'help']);
        assert.deepEqual(state.policy.bypass_groups, ['产品群']);
    });
    await check('刷新策略后取消旧读取，避免过期响应覆盖', async () => {
        await nav('tasks');
        let release;
        const pending = new Promise((resolve) => {
            release = resolve;
        });
        const oldPolicy = structuredClone(state.policy);
        const delayPolicy = async (route) => {
            if (route.request().method() !== 'GET') return route.fallback();
            await pending;
            await route.fulfill({ json: oldPolicy });
        };
        await page.route('**/api/remote-commands/policy', delayPolicy);
        try {
            const request = page.waitForRequest((r) => r.url().endsWith('/api/remote-commands/policy'));
            await nav('remote-commands');
            const oldRequest = await request;
            state.policy = { ...state.policy, allowlist: ['news'] };
            const cancelled = page.waitForEvent('requestfailed', (r) => r === oldRequest);
            const section = page.locator('#section-remote-commands');
            await section.getByRole('button', { name: '刷新命令列表', exact: true }).click();
            await cancelled;
            release();
            await section
                .locator('.remote-cmd-row')
                .filter({ hasText: 'help' })
                .getByText('未白名单', { exact: true })
                .waitFor();
            assert.equal(await section.locator('.loading-hint').count(), 0);
        } finally {
            release();
            await page.unroute('**/api/remote-commands/policy', delayPolicy);
        }
    });
    await check('策略读取完成前及读取失败时禁止编辑旧数据', async () => {
        await nav('tasks');
        state.policy = { ...state.policy, allowlist: ['help'], bypass_groups: ['开发群'] };
        let release;
        let fail = false;
        const pending = new Promise((resolve) => {
            release = resolve;
        });
        const readPolicy = async (route) => {
            if (route.request().method() !== 'GET') return route.fallback();
            await pending;
            if (fail) return route.fulfill({ status: 503, json: { detail: '策略读取失败' } });
            await route.fallback();
        };
        await page.route('**/api/remote-commands/policy', readPolicy);
        const section = page.locator('#section-remote-commands');
        const edit = section.getByRole('button', { name: '编辑', exact: true });
        try {
            await nav('remote-commands');
            await section.locator('.loading-hint').waitFor();
            assert.equal(await edit.isDisabled(), true);
            release();
            await section.locator('.loading-hint').waitFor({ state: 'detached' });
            await edit.click();
            assert.equal(await section.getByLabel('help帮助').isChecked(), true);
            assert.equal(await section.getByLabel('news新闻').isChecked(), false);
            assert.equal(await section.getByLabel('开发群', { exact: true }).isChecked(), true);
            await section.getByRole('button', { name: '保存', exact: true }).click();
            await edit.waitFor();
            assert.deepEqual(state.policy.allowlist, ['help']);
            assert.deepEqual(state.policy.bypass_groups, ['开发群']);

            await nav('tasks');
            fail = true;
            state.policy = { ...state.policy, allowlist: ['news'], bypass_groups: ['产品群'] };
            await nav('remote-commands');
            await section.getByRole('alert').getByText('策略读取失败').waitFor();
            assert.equal(await edit.isDisabled(), true);
            fail = false;
            await section.getByRole('button', { name: '重试', exact: true }).click();
            await section.getByRole('alert').waitFor({ state: 'detached' });
            await edit.click();
            assert.equal(await section.getByLabel('news新闻').isChecked(), true);
            assert.equal(await section.getByLabel('产品群', { exact: true }).isChecked(), true);
            await section.getByRole('button', { name: '取消', exact: true }).click();
        } finally {
            release();
            await page.unroute('**/api/remote-commands/policy', readPolicy);
        }
    });
}
