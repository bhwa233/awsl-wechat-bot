const MAX_BODY_BYTES = 1_500_000;
const encoder = new TextEncoder();

class RequestError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}
const reject = (message = 'Invalid request') => { throw new RequestError(400, message); };
const json = (data, status = 200) => Response.json(data, {
    status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
});
const object = (value) => value && typeof value === 'object' && !Array.isArray(value);
function string(value, max, allowEmpty = false) {
    if (typeof value !== 'string' || value.length > max || (!allowEmpty && !value.length)) reject();
    return value;
}
function integer(value, max = Number.MAX_SAFE_INTEGER) {
    if (!Number.isSafeInteger(value) || value < 0 || value > max) reject();
    return value;
}
function groupID(value) {
    string(value, 200);
    if (!value.endsWith('@chatroom') || /[\s\x00-\x1f]/u.test(value)) reject('Invalid group ID');
    return value;
}
async function sha256(value) {
    const digest = await crypto.subtle.digest('SHA-256', encoder.encode(JSON.stringify(value)));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
async function equalToken(a, b) {
    const hashes = await Promise.all([a, b].map((token) => crypto.subtle.digest('SHA-256', encoder.encode(token))));
    return crypto.subtle.timingSafeEqual(hashes[0], hashes[1]);
}
async function authorize(request, env, write) {
    const header = request.headers.get('Authorization') || '';
    if (!header.startsWith('Bearer ') || header.length > 4096) throw new RequestError(401, 'Unauthorized');
    const token = header.slice(7);
    if (!write && env.ARCHIVE_READ_TOKEN && await equalToken(token, env.ARCHIVE_READ_TOKEN)) return { admin: true };
    let clients;
    try { clients = JSON.parse(env.ARCHIVE_CLIENTS || '[]'); } catch { throw new Error('Invalid client configuration'); }
    if (!Array.isArray(clients)) throw new Error('Invalid client configuration');
    for (const client of clients) {
        if (!object(client) || typeof client.token !== 'string' || client.token.length < 24 ||
            typeof client.account_id !== 'string' || typeof client.source_id !== 'string' ||
            !Array.isArray(client.groups) || !client.groups.length || client.groups.length > 2000 ||
            client.groups.some((group) => typeof group !== 'string' || !group.endsWith('@chatroom'))) {
            throw new Error('Invalid client configuration');
        }
        if (await equalToken(token, client.token)) return client;
    }
    throw new RequestError(401, 'Unauthorized');
}
function scope(client, account, talker) {
    string(account, 200);
    if (!client.admin && (account !== client.account_id || (talker && !client.groups.includes(talker)))) {
        throw new RequestError(403, 'Forbidden');
    }
}
async function readBody(request) {
    if (Number(request.headers.get('Content-Length')) > MAX_BODY_BYTES) throw new RequestError(413, 'Batch too large');
    if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') {
        throw new RequestError(415, 'Expected application/json');
    }
    if (!request.body) reject();
    const reader = request.body.getReader(), chunks = [];
    let size = 0;
    try {
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > MAX_BODY_BYTES) {
                await reader.cancel();
                throw new RequestError(413, 'Batch too large');
            }
            chunks.push(value);
        }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { reject('Invalid JSON'); }
}
async function ingest(request, env, client) {
    const body = await readBody(request);
    if (!object(body)) reject();
    scope(client, body.account_id);
    if (body.source_id !== client.source_id) throw new RequestError(403, 'Forbidden');
    if (!/^[a-f0-9]{64}$/u.test(body.batch_id)) reject('Invalid batch ID');
    if (!Array.isArray(body.messages) || !body.messages.length || body.messages.length > 500 ||
        !Array.isArray(body.groups) || !body.groups.length || body.groups.length > 200) reject();
    const groups = new Map();
    for (const group of body.groups) {
        if (!object(group)) reject();
        groupID(group.talker);
        scope(client, body.account_id, group.talker);
        string(group.display_name, 1000, true);
        if (groups.has(group.talker)) reject('Duplicate group');
        groups.set(group.talker, group.display_name);
    }
    const messages = [];
    for (const message of body.messages) {
        if (!object(message)) reject();
        groupID(message.talker);
        scope(client, body.account_id, message.talker);
        if (!groups.has(message.talker)) reject('Missing group');
        integer(message.seq); integer(message.local_id); integer(message.create_time, 253402300799);
        integer(message.msg_type, 0xFFFFFFFF); integer(message.observed_at);
        if (message.observed_at > Date.now() + 300_000) reject('Source clock is ahead');
        if (typeof message.server_id !== 'string' || !/^(0|[1-9][0-9]{0,19})$/u.test(message.server_id) ||
            BigInt(message.server_id) > 18446744073709551615n) reject('Invalid server ID');
        string(message.sender, 200, true); string(message.sender_name, 1000, true);
        string(message.content, MAX_BODY_BYTES, true); string(message.text, MAX_BODY_BYTES, true);
        if (typeof message.is_self !== 'boolean' || !Array.isArray(message.at_users) || message.at_users.length > 2000) reject();
        for (const user of message.at_users) string(user, 200);
        const localKey = await sha256([message.seq, message.local_id, message.create_time, message.msg_type]);
        if (localKey !== message.local_key) reject('Invalid local identity');
        const fallbackID = await sha256([body.account_id, message.talker, 'local', body.source_id, localKey]);
        const id = message.server_id === '0' ? fallbackID : await sha256([body.account_id, message.talker, 'server', message.server_id]);
        messages.push({ ...message, id, fallbackID, localKey });
    }
    const statements = [...groups].map(([talker, name]) => env.DB.prepare(
        `INSERT INTO archive_groups VALUES (?, ?, ?, ?) ON CONFLICT(account_id, talker)
         DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at`,
    ).bind(body.account_id, talker, name, Date.now()));
    for (const m of messages) {
        const identity = [body.account_id, body.source_id, m.talker, m.localKey];
        // 零 server_id 的旧观察不能把已关联的正式 ID 降级回本地 ID。
        const resolved = m.server_id === '0'
            ? `COALESCE((SELECT message_id FROM archive_observations WHERE account_id=? AND source_id=? AND talker=? AND local_key=?), ?)`
            : '?';
        const idArgs = m.server_id === '0' ? [...identity, m.id] : [m.id];
        statements.push(env.DB.prepare(
            `INSERT INTO archive_messages VALUES (${resolved}, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(id) DO UPDATE SET
               server_id=CASE WHEN excluded.server_id='0' THEN archive_messages.server_id ELSE excluded.server_id END,
               create_time=excluded.create_time, sender=excluded.sender, sender_name=excluded.sender_name,
               msg_type=excluded.msg_type, content=excluded.content, text=excluded.text,
               is_self=excluded.is_self, at_users=excluded.at_users, observed_at=excluded.observed_at
             WHERE excluded.observed_at >= archive_messages.observed_at`,
        ).bind(...idArgs, body.account_id, m.talker, m.server_id, m.create_time, m.sender, m.sender_name,
            m.msg_type, m.content, m.text, Number(m.is_self), JSON.stringify(m.at_users), m.observed_at));
        if (m.server_id !== '0') {
            statements.push(env.DB.prepare(
                `INSERT INTO archive_messages
                 SELECT ?, account_id, talker, ?, create_time, sender, sender_name, msg_type,
                        content, text, is_self, at_users, observed_at
                 FROM archive_messages WHERE id=?
                 ON CONFLICT(id) DO UPDATE SET content=excluded.content, text=excluded.text,
                   sender=excluded.sender, sender_name=excluded.sender_name, at_users=excluded.at_users,
                   observed_at=excluded.observed_at
                 WHERE excluded.observed_at > archive_messages.observed_at`,
            ).bind(m.id, m.server_id, m.fallbackID));
            statements.push(env.DB.prepare('UPDATE archive_observations SET message_id=? WHERE message_id=?').bind(m.id, m.fallbackID));
            statements.push(env.DB.prepare('DELETE FROM archive_messages WHERE id=?').bind(m.fallbackID));
        }
        statements.push(env.DB.prepare(
            `INSERT INTO archive_observations VALUES (?, ?, ?, ?, ${resolved}, ?, ?, ?)
             ON CONFLICT(account_id, source_id, talker, local_key) DO UPDATE SET
               message_id=excluded.message_id, seq=excluded.seq, local_id=excluded.local_id,
               observed_at=MAX(archive_observations.observed_at, excluded.observed_at)`,
        ).bind(...identity, ...idArgs, m.seq, m.local_id, m.observed_at));
    }
    await env.DB.batch(statements); // 全部消息和观察原子提交后才确认，允许响应丢失后的重传。
    return json({ batch_id: body.batch_id, accepted: messages.length });
}
function queryInteger(value, fallback, max = Number.MAX_SAFE_INTEGER) {
    if (value === null) return fallback;
    if (!/^[0-9]+$/u.test(value)) reject();
    return integer(Number(value), max);
}
async function queryMessages(url, env, client) {
    const p = url.searchParams, account = p.get('account_id'), talker = groupID(p.get('talker'));
    scope(client, account, talker);
    const limit = queryInteger(p.get('limit'), 100, 200);
    if (!limit) reject();
    const clauses = ['account_id=?', 'talker=?'], args = [account, talker];
    for (const [name, op] of [['start', '>='], ['end', '<=']]) {
        if (p.has(name)) { clauses.push(`create_time${op}?`); args.push(queryInteger(p.get(name), 0, 253402300799)); }
    }
    if (p.has('sender')) { clauses.push('sender=?'); args.push(string(p.get('sender'), 200)); }
    if (p.has('q')) {
        const query = string(p.get('q'), 256);
        // instr 为字面量子串查询，不将用户输入作为 SQL 或 LIKE 模式。
        clauses.push('instr(text, ?) > 0'); args.push(query);
    }
    if (p.has('cursor')) {
        let cursor;
        try { cursor = JSON.parse(atob(string(p.get('cursor'), 400))); } catch { reject('Invalid cursor'); }
        if (!Array.isArray(cursor) || cursor.length !== 2 || !/^[a-f0-9]{64}$/u.test(cursor[1])) reject('Invalid cursor');
        integer(cursor[0], 253402300799);
        clauses.push('(create_time, id) < (?, ?)'); args.push(...cursor);
    }
    const { results } = await env.DB.prepare(
        `WITH page AS (
           SELECT * FROM archive_messages WHERE ${clauses.join(' AND ')} ORDER BY create_time DESC, id DESC LIMIT ?
         ), sized AS (
           SELECT *, SUM(length(CAST(content AS BLOB)) + length(CAST(text AS BLOB)) + length(CAST(at_users AS BLOB))
                         + length(CAST(sender_name AS BLOB)) + 4096)
             OVER (ORDER BY create_time DESC, id DESC) AS page_bytes FROM page
         ) SELECT id, account_id, talker, server_id, create_time, sender, sender_name, msg_type, is_self, observed_at,
             CASE WHEN page_bytes <= 2000000 THEN at_users ELSE '[]' END AS at_users,
             CASE WHEN page_bytes <= 2000000 THEN content ELSE NULL END AS content,
             CASE WHEN page_bytes <= 2000000 THEN text ELSE NULL END AS text
           FROM sized ORDER BY create_time DESC, id DESC`,
    ).bind(...args, limit + 1).all();
    const rows = results.filter((row) => row.content !== null).slice(0, limit), last = rows.at(-1);
    const more = results.length > rows.length;
    return json({
        messages: rows.map((row) => ({ ...row, is_self: Boolean(row.is_self), at_users: JSON.parse(row.at_users) })),
        next_cursor: more ? btoa(JSON.stringify([last.create_time, last.id])) : null,
    });
}
async function listGroups(url, env, client) {
    const account = url.searchParams.get('account_id');
    scope(client, account);
    const groups = client.admin ? [] : client.groups;
    // 允许列表分批，保持每条 D1 查询最多 100 个绑定参数。
    const batches = client.admin ? [[]] : Array.from({ length: Math.ceil(groups.length / 90) }, (_, i) => groups.slice(i * 90, (i + 1) * 90));
    const results = [];
    for (const batch of batches) {
        const filter = batch.length ? ` AND g.talker IN (${batch.map(() => '?').join(',')})` : '';
        const response = await env.DB.prepare(
            `SELECT g.*, (SELECT COUNT(*) FROM archive_messages m WHERE m.account_id=g.account_id AND m.talker=g.talker) AS count,
             (SELECT MAX(create_time) FROM archive_messages m WHERE m.account_id=g.account_id AND m.talker=g.talker) AS latest_time
             FROM archive_groups g WHERE g.account_id=?${filter} ORDER BY g.talker`,
        ).bind(account, ...batch).all();
        results.push(...response.results);
    }
    return json({ groups: results });
}

export default {
    async fetch(request, env) {
        try {
            const url = new URL(request.url);
            if (request.method === 'GET' && url.pathname === '/health') return json({ ok: true });
            const write = request.method === 'POST' && url.pathname === '/v1/batches';
            const client = await authorize(request, env, write);
            if (request.method === 'GET' && url.pathname === '/v1/scope' && !client.admin) {
                return json({ account_id: client.account_id, source_id: client.source_id, groups: client.groups });
            }
            if (write) return await ingest(request, env, client);
            if (request.method === 'GET' && url.pathname === '/v1/messages') return await queryMessages(url, env, client);
            if (request.method === 'GET' && url.pathname === '/v1/groups') return await listGroups(url, env, client);
            return json({ detail: 'Not found' }, 404);
        } catch (error) {
            if (error instanceof RequestError) return json({ detail: error.message }, error.status);
            console.error('Archive request failed', error?.name || 'Error');
            return json({ detail: 'Archive service unavailable' }, 503);
        }
    },
};
