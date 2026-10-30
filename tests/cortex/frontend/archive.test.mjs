import assert from 'node:assert/strict';
import path from 'node:path';

export async function run({ page, nav, check, state, calls, artifacts }) {
    await nav('archive');
    const section = page.locator('#section-archive');
    await check('归档群选择、失败反馈与暂停恢复保留稳定群 ID', async () => {
        await section.getByRole('combobox', { name: '同步群聊', exact: true }).selectOption('room0@chatroom');
        await section.getByRole('button', { name: '添加并同步', exact: true }).click();
        await section.locator('#archiveGroups').getByText('room0@chatroom', { exact: true }).waitFor();
        state.archiveSaveFails = true;
        await section.getByRole('button', { name: '暂停', exact: true }).click();
        await section.getByText('归档设置暂时无法保存', { exact: true }).waitFor();
        assert.equal(state.archiveGroups[0].enabled, true);
        state.archiveSaveFails = false;
        await section.getByRole('button', { name: '暂停', exact: true }).click();
        await section.getByRole('button', { name: '继续同步', exact: true }).waitFor();
        await section.getByRole('button', { name: '继续同步', exact: true }).click();
        await section.getByRole('button', { name: '暂停', exact: true }).waitFor();
        assert.equal(state.archiveGroups[0].talker, 'room0@chatroom');
    });
    await check('重新补录确认与云端原文安全显示、查询分页', async () => {
        await section.getByRole('button', { name: '重新补录', exact: true }).click();
        await page.getByRole('dialog', { name: '重新补录历史' }).getByRole('button', { name: '确认重新补录' }).click();
        await section.getByText('历史补录中 · 待上传 0 条', { exact: true }).waitFor();
        await section.getByRole('textbox', { name: '正文关键词' }).fill('归档');
        await section.getByRole('button', { name: '查询云端', exact: true }).click();
        await section.locator('.archive-text').getByText('归档正文 <img src=x onerror=alert(1)>', { exact: true }).first().waitFor();
        assert.equal(await section.locator('.archive-results img').count(), 0);
        await section.getByRole('button', { name: '更早记录', exact: true }).click();
        await section.getByText('更早的归档正文', { exact: true }).waitFor();
        assert.ok(calls.some((call) => call.endpoint === '/api/archive/messages' && call.method === 'GET'));
        state.archiveQueryFails = true;
        await section.getByRole('textbox', { name: '正文关键词' }).fill('新的查询范围');
        await section.getByRole('button', { name: '查询云端', exact: true }).click();
        await section.getByText('云端记录暂时不可用', { exact: true }).waitFor();
        assert.equal(await section.locator('.archive-message').count(), 0);
        state.archiveQueryFails = false;
        await section.getByRole('button', { name: '重试', exact: true }).click();
        await section.locator('.archive-message').waitFor();
        await section.evaluate((element) => { element.scrollTop = 0; });
        await page.screenshot({ animations: 'disabled', path: path.join(artifacts, 'archive-desktop.png') });
        await page.setViewportSize({ width: 390, height: 844 });
        await section.evaluate((element) => { element.scrollTop = 0; });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        assert.ok((await section.getByRole('heading', { name: '云端归档', exact: true }).boundingBox()).y >= 0);
        await page.screenshot({ animations: 'disabled', path: path.join(artifacts, 'archive-mobile.png') });
    });
}
