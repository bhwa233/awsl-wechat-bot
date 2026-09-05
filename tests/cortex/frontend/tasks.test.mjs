import assert from 'node:assert/strict';

export async function run({ page, nav, check, state, data }) {
    await check('任务创建、编辑、禁用和删除', async () => {
        await nav('tasks');
        await page.getByRole('button', { name: '新建任务', exact: true }).click();
        const form = page.locator('#taskForm');
        await form.getByLabel('任务名称', { exact: true }).fill('回归任务');
        await form.getByLabel('Cron 表达式').fill('0 10 * * *');
        await form.getByLabel('消息内容').fill('你好');
        await form.getByLabel('状态', { exact: true }).selectOption('false');
        await form.getByRole('button', { name: '创建', exact: true }).click();
        await form.waitFor({ state: 'detached' });
        let row = page.locator('#tasksList .res-item').filter({ hasText: '回归任务' });
        await row.waitFor();
        assert.equal(data.tasks.find((item) => item.name === '回归任务').enabled, false);
        await row.getByRole('button', { name: '编辑', exact: true }).click();
        await page.locator('#taskForm').getByLabel('任务名称', { exact: true }).fill('修改后任务');
        await page.getByRole('button', { name: '保存修改', exact: true }).click();
        await page.locator('#taskForm').waitFor({ state: 'detached' });
        row = page.locator('#tasksList .res-item').filter({ hasText: '修改后任务' });
        await row.getByRole('switch').click();
        await row.getByText('已启用', { exact: true }).waitFor();
        await row.getByRole('button', { name: '删除', exact: true }).click();
        await page.getByRole('button', { name: '确认删除', exact: true }).click();
        await row.waitFor({ state: 'detached' });
    });
    await check('任务加载失败可重试', async () => {
        state.failTasks = true;
        await page.locator('#section-tasks').getByRole('button', { name: '刷新', exact: true }).click();
        await page.locator('#section-tasks .request-error').getByText('任务读取失败').waitFor();
        state.failTasks = false;
        await page.locator('#section-tasks').getByRole('button', { name: '重试', exact: true }).click();
        await page.locator('#section-tasks .request-error').waitFor({ state: 'detached' });
    });
    await check('总结任务配置与编辑回填', async () => {
        await nav('tasks');
        await page.getByRole('button', { name: '新建任务', exact: true }).click();
        const form = page.locator('#taskForm');
        await form.getByLabel('任务名称', { exact: true }).fill('群聊总结任务');
        await form.getByLabel('Cron 表达式').fill('0 18 * * *');
        await form.getByRole('button', { name: '总结', exact: true }).click();
        await form.getByRole('button', { name: '+ 添加群聊', exact: true }).click();
        await form.getByLabel('来源会话', { exact: true }).selectOption('room0@chatroom');
        assert.equal(await form.getByLabel('发送目标', { exact: true }).inputValue(), '开发群');
        await form.getByRole('button', { name: '创建', exact: true }).click();
        await form.waitFor({ state: 'detached' });
        const item = data.tasks.find((item) => item.name === '群聊总结任务');
        assert.equal(item.task_type, 'summary');
        assert.deepEqual(JSON.parse(item.message).groups, [{ group_id: 'room0@chatroom', group_name: '开发群' }]);
        await page
            .locator('#tasksList .res-item')
            .filter({ hasText: '群聊总结任务' })
            .getByRole('button', { name: '编辑', exact: true })
            .click();
        await form.getByLabel('来源会话', { exact: true }).waitFor();
        assert.equal(await form.getByLabel('来源会话', { exact: true }).inputValue(), 'room0@chatroom');
        await page.keyboard.press('Escape');
    });
    await check('各管理页面编辑请求未完成时禁止新建覆盖表单', async () => {
        for (const [section, kind, id, endpoint, createLabel, formId] of [
            ['tasks', 'task', 1, '/api/tasks/1', '新建任务', 'taskForm'],
            ['custom-commands', 'command', 3, '/api/custom-commands/3', '新建命令', 'customCommandForm'],
            ['webhooks', 'webhook', 2, '/api/webhooks', '新建 Webhook', 'webhookForm'],
        ]) {
            await nav(section);
            const row = page.locator(`[data-${kind}-id="${id}"]`);
            await row.waitFor();
            let release;
            const pending = new Promise((resolve) => {
                release = resolve;
            });
            const readItem = async (route) => {
                await pending;
                await route.fallback();
            };
            const pattern = `**${endpoint}`;
            await page.route(pattern, readItem);
            try {
                await row.getByRole('button', { name: '编辑', exact: true }).click();
                const create = page.getByRole('button', { name: createLabel, exact: true });
                await page.waitForFunction(
                    (label) =>
                        [...document.querySelectorAll('button')].some((el) => el.textContent === label && el.disabled),
                    createLabel
                );
                await create.evaluate((el) => el.click());
                assert.equal(await page.locator(`#${formId}`).count(), 0);
                release();
                await page.locator(`#${formId}`).waitFor();
                await page.keyboard.press('Escape');
                await create.click();
                await page.locator(`#${formId}`).waitFor();
                assert.ok((await page.getByRole('dialog').getAttribute('aria-label')).startsWith('新建'));
                await page.keyboard.press('Escape');
            } finally {
                release();
                await page.unroute(pattern, readItem);
            }
        }
    });
    await check('异常总结配置可编辑修复且不影响任务列表', async () => {
        await nav('tasks');
        const original = { ...data.tasks[0] };
        const valid = { group_id: 'room0@chatroom', group_name: '开发群' };
        try {
            for (const config of [
                { groups: {} },
                { groups: 'invalid' },
                { groups: [null, 1, [], valid] },
                { groups: [{ group_id: 123, group_name: {} }] },
            ]) {
                Object.assign(data.tasks[0], { task_type: 'summary', message: JSON.stringify(config) });
                await page.locator('[data-task-id="1"]').getByRole('button', { name: '编辑', exact: true }).click();
                const form = page.locator('#taskForm');
                await form.waitFor();
                assert.ok(await page.locator('#tasksList .res-item').count());
                if (config.groups?.includes?.(valid)) {
                    assert.equal(await form.getByLabel('来源会话', { exact: true }).inputValue(), valid.group_id);
                    assert.equal(await form.getByLabel('发送目标', { exact: true }).inputValue(), valid.group_name);
                } else {
                    if (!(await form.getByLabel('来源会话', { exact: true }).count()))
                        await form.getByRole('button', { name: '+ 添加群聊', exact: true }).click();
                    await form.getByLabel('来源会话', { exact: true }).selectOption(valid.group_id);
                }
                await form.getByRole('button', { name: '保存修改', exact: true }).click();
                await form.waitFor({ state: 'detached' });
                assert.deepEqual(JSON.parse(data.tasks[0].message).groups, [valid]);
            }
        } finally {
            Object.assign(data.tasks[0], original);
        }
    });
}
