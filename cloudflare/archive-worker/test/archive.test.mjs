import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';
import { unstable_dev } from 'wrangler';

const token = 'windows-upload-token-for-local-test';
const linuxToken = 'linux-upload-token-for-local-test';
const readToken = 'read-only-admin-token-for-local-test';
const hash = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
let worker, directory;

before(async () => {
    directory = await mkdtemp(path.join(os.tmpdir(), 'awsl-d1-'));
    execFileSync(process.execPath, ['node_modules/wrangler/bin/wrangler.js', 'd1', 'migrations', 'apply', 'DB',
        '--local', '--persist-to', directory], { env: { ...process.env, CI: 'true', WRANGLER_SEND_METRICS: 'false' } });
    execFileSync(process.execPath, ['node_modules/wrangler/bin/wrangler.js', 'd1', 'execute', 'DB', '--local',
        '--persist-to', directory, '--command',
        "CREATE TRIGGER reject_test_message BEFORE INSERT ON archive_messages WHEN NEW.content='reject-for-test' BEGIN SELECT RAISE(ABORT, 'test failure'); END;"],
    { env: { ...process.env, CI: 'true', WRANGLER_SEND_METRICS: 'false' } });
    worker = await unstable_dev('src/index.js', {
        config: 'wrangler.jsonc', local: true, ip: '127.0.0.1', port: 0, persistTo: directory,
        vars: { ARCHIVE_CLIENTS: JSON.stringify([
            { token, account_id: 'account', source_id: 'windows', groups: ['room@chatroom', 'large@chatroom'] },
            { token: linuxToken, account_id: 'account', source_id: 'linux', groups: ['room@chatroom'] },
        ]), ARCHIVE_READ_TOKEN: readToken },
        experimental: { disableExperimentalWarning: true },
    });
});
after(async () => {
    await worker?.stop();
    if (directory) await rm(directory, { recursive: true, force: true });
});
function message(localID, serverID = String(9007199254740991000n + BigInt(localID)), content = '同样的正文') {
    const row = {
        talker: 'room@chatroom', seq: 101, local_id: localID, create_time: 100, server_id: serverID,
        sender: 'member', sender_name: '测试成员', msg_type: 1, content, text: content,
        is_self: false, at_users: [], observed_at: Date.now(),
    };
    row.local_key = hash([row.seq, row.local_id, row.create_time, row.msg_type]);
    return row;
}
function request(url, { method = 'GET', credential = token, body, headers = {} } = {}) {
    return worker.fetch(url, {
        method, headers: { Authorization: `Bearer ${credential}`, ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
        body: body ? JSON.stringify(body) : undefined,
    });
}
function batch(messages, source = 'windows') {
    return { account_id: 'account', source_id: source, batch_id: hash(messages),
        groups: [...new Set(messages.map((row) => row.talker))].map((talker) => ({ talker, display_name: '测试群' })), messages };
}
const upload = (messages, source = 'windows') => request('/v1/batches', {
    method: 'POST', credential: source === 'windows' ? token : linuxToken, body: batch(messages, source),
});
const query = (extra = '') => request(`/v1/messages?account_id=account&talker=room%40chatroom${extra}`).then((res) => res.json());

test('D1 retry, cross-device identity and late server ID preserve one canonical message', async () => {
    const first = message(1, '0');
    assert.equal((await upload([first])).status, 200);
    assert.equal((await upload([first])).status, 200); // 已提交但调用方没收到确认，再传整批。
    assert.equal((await query()).messages.length, 1);
    const serverID = '9007199254740991001';
    assert.equal((await upload([message(99, serverID)], 'linux')).status, 200);
    assert.equal((await upload([{ ...first, server_id: serverID, observed_at: Date.now() }])).status, 200);
    assert.equal((await query()).messages.length, 1);
    assert.equal((await upload([{ ...first, observed_at: Date.now() }])).status, 200); // 旧的零 ID 不能降级身份。
    const rows = (await query()).messages;
    assert.equal(rows.length, 1);
    assert.equal(rows[0].server_id, serverID);
    assert.equal(rows[0].content, first.content);
    assert.equal((await upload([message(2)])).status, 200);
    assert.equal((await query()).messages.length, 2); // 正文相同、身份不同的消息不能误去重。
});

test('all rows are validated before commit; client cannot forge identity or cross its scope', async () => {
    const valid = message(3);
    const invalid = { ...message(4), local_key: 'forged' };
    assert.equal((await upload([valid, invalid])).status, 400);
    assert.equal((await query()).messages.some((row) => row.server_id === valid.server_id), false);
    assert.equal((await request('/v1/batches', { method: 'POST', credential: readToken, body: batch([valid]) })).status, 401);
    assert.equal((await request('/v1/batches', { method: 'POST', body: { ...batch([valid]), account_id: 'other' } })).status, 403);
    assert.equal((await request('/v1/messages?account_id=account&talker=private%40chatroom')).status, 403);
    assert.equal((await request('/v1/messages?account_id=other&talker=room%40chatroom')).status, 403);
    assert.equal((await request('/v1/messages?account_id=account&talker=room%40chatroom', { credential: 'wrong' })).status, 401);
    assert.equal((await upload([{ ...valid, server_id: 9007199254740991000 }])).status, 400);
    assert.equal((await request('/v1/messages?account_id=account&talker=room%40chatroom&cursor=bad')).status, 400);
    assert.equal((await upload([message(7, undefined, 'x'.repeat(800_000))])).status, 413);
    assert.equal((await upload([valid, message(8, undefined, 'reject-for-test')])).status, 503);
    assert.equal((await query()).messages.some((row) => row.server_id === valid.server_id), false);
});

test('keyset pages with equal timestamps have no gaps and literal keyword search stays within the group', async () => {
    assert.equal((await upload([message(5, undefined, '百分号 % _ 中文'), message(6, undefined, '普通正文')])).status, 200);
    let cursor = null, seen = new Set();
    do {
        const page = await query(`&limit=1${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
        for (const row of page.messages) {
            assert.equal(seen.has(row.id), false);
            seen.add(row.id);
        }
        cursor = page.next_cursor;
    } while (cursor);
    assert.equal(seen.size, (await query()).messages.length);
    const result = await query(`&q=${encodeURIComponent('% _ 中文')}`);
    assert.equal(result.messages.length, 1);
    assert.equal(result.messages[0].content, '百分号 % _ 中文');
    const groups = await request('/v1/groups?account_id=account').then((res) => res.json());
    assert.ok(groups.groups.some((group) => group.talker === 'room@chatroom' && group.count === seen.size));
});

test('UTF-8 byte bounded query pages retain full large content and return a continuation', async () => {
    const content = '聊'.repeat(180_000);
    for (const id of [20, 21]) assert.equal((await upload([{ ...message(id, undefined, content), talker: 'large@chatroom' }])).status, 200);
    const url = '/v1/messages?account_id=account&talker=large%40chatroom&limit=200';
    const first = await request(url).then((res) => res.json());
    assert.equal(first.messages.length, 1);
    assert.equal(first.messages[0].content, content);
    assert.ok(first.next_cursor);
    const second = await request(`${url}&cursor=${encodeURIComponent(first.next_cursor)}`).then((res) => res.json());
    assert.equal(second.messages.length, 1);
    assert.equal(second.messages[0].content, content);
    assert.notEqual(first.messages[0].id, second.messages[0].id);
    assert.equal(second.next_cursor, null);
});
