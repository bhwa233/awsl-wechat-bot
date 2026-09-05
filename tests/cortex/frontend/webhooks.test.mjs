import assert from 'node:assert/strict';

export async function run({ page, nav, check, state, data }) {
    await check('Webhook 表单绑定、JSON 校验和回复模式', async () => {
        await nav('webhooks');
        await page.getByRole('button', { name: '新建 Webhook', exact: true }).click();
        const form = page.locator('#webhookForm');
        await form.getByLabel('名称', { exact: true }).fill('新连接');
        await form.getByLabel('URL', { exact: true }).fill('https://example.test/new');
        await form.getByLabel('Headers（JSON）').fill('{bad');
        await form.getByRole('button', { name: '创建', exact: true }).click();
        await form.getByText('Headers 必须是有效 JSON', { exact: true }).waitFor();
        await form.getByLabel('Headers（JSON）').fill('{}');
        await form.getByLabel('回复模式').selectOption('jsonpath');
        await form.getByLabel('JSONPath 表达式').fill('$.reply');
        await form.getByRole('button', { name: '创建', exact: true }).click();
        await form.waitFor({ state: 'detached' });
        assert.equal(data.webhooks.find((item) => item.name === '新连接').reply_jsonpath, '$.reply');
    });
    await check('创建与编辑保留正则首尾空白及匹配含义', async () => {
        await page.getByRole('button', { name: '新建 Webhook', exact: true }).click();
        const form = page.locator('#webhookForm');
        await form.getByLabel('名称', { exact: true }).fill('  精确过滤  ');
        await form.getByLabel('URL', { exact: true }).fill('https://example.test/filter');
        await form.getByLabel('群聊过滤（正则）').fill(' Team ');
        await form.getByLabel('消息过滤（正则）').fill(' alert ');
        await form.getByRole('button', { name: '创建', exact: true }).click();
        await form.waitFor({ state: 'detached' });
        const item = data.webhooks.find((row) => row.name === '精确过滤');
        assert.equal(item.group_filter, ' Team ');
        assert.equal(item.message_filter, ' alert ');
        await page
            .locator(`#webhooksList [data-webhook-id="${item.id}"]`)
            .getByRole('button', { name: '编辑', exact: true })
            .click();
        await form.getByLabel('名称', { exact: true }).fill('仅改名称');
        await form.getByRole('button', { name: '保存修改', exact: true }).click();
        await form.waitFor({ state: 'detached' });
        const saved = data.webhooks.find((row) => row.id === item.id);
        assert.equal(saved.group_filter, ' Team ');
        assert.equal(saved.message_filter, ' alert ');
        assert.equal(new RegExp(saved.message_filter).test('alert'), false);
        assert.equal(new RegExp(saved.message_filter).test(' alert '), true);
    });
}
