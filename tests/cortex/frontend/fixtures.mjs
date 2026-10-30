export async function mockApi(page, base) {
    const state = {
        startupState: 'disabled',
        startupOutcome: 'ready',
        startupSupported: true,
        failTasks: false,
        failMessages: false,
        delayA: false,
        added: false,
        consoleUnsupported: false,
        requireToken: false,
        windowsOpen: false,
        cacheExists: false,
        consoleSelected: '开发群',
        archiveGroups: [],
        archiveSaveFails: false,
        archiveQueryFails: false,
    };
    const errors = [],
        calls = [],
        external = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => {
        if (!request.url().startsWith(base) && !request.url().startsWith('data:')) external.push(request.url());
    });
    const groups = Array.from({ length: 85 }, (_, i) => ({
        username: `room${i}@chatroom`,
        display_name: i === 0 ? '开发群' : i === 1 ? '产品群' : `会话 ${i}`,
    }));
    const data = {
        tasks: [
            {
                id: 1,
                name: '晨间问候',
                enabled: true,
                task_type: 'send',
                message_type: 'text',
                message: '早上好',
                filename: '',
                target_groups: [],
                cron_expression: '0 9 * * *',
            },
        ],
        webhooks: [
            {
                id: 2,
                name: '通知服务',
                enabled: true,
                url: 'https://example.test/hook',
                headers: '{}',
                body_template: '{}',
                group_filter: '',
                message_filter: '',
                reply_mode: 'none',
                reply_jsonpath: '',
            },
        ],
        'custom-commands': [
            {
                id: 3,
                key: 'menu',
                description: '查看菜单',
                enabled: true,
                action_type: 'text',
                message: '菜单内容',
                filename: '',
                target_groups: [],
            },
            {
                id: 4,
                key: 'dev',
                description: '开发群命令',
                enabled: true,
                action_type: 'summary',
                message: '',
                target_groups: ['开发群'],
            },
        ],
    };
    state.policy = {
        allowlist: [],
        bypass_groups: [],
        available_commands: [
            { key: 'help', description: '帮助' },
            { key: 'news', description: '新闻' },
        ],
    };
    const chatItems = [
        { name: '开发群', path: '0/1', preview: '最新消息', unread: 2, pinned: true },
        { name: '产品群', path: '0/2', preview: '产品计划', time: '12:00', unread: 0 },
    ];
    state.chatItems = chatItems;
    const consoleState = () => ({
        available: true,
        chat_list: { items: chatItems },
        chat_area: { available: true, title: state.consoleSelected },
    });
    function message(group, id, content, type = 1) {
        return {
            talker: group,
            server_id_str: String(9007199254740991000n + BigInt(id)),
            local_id: id,
            create_time: 100 + id,
            time: '2026-09-05T12:00:00+08:00',
            sender: 'user1',
            sender_name: '小明',
            is_self: false,
            msg_type: type,
            content,
        };
    }
    await page.route('**/api/**', async (route) => {
        const req = route.request(),
            url = new URL(req.url()),
            endpoint = url.pathname;
        const method = req.method(),
            body = req.postData() ? req.postDataJSON() : null;
        calls.push({ endpoint, method, body, token: req.headers().authorization });
        let result = {},
            status = 200;
        if (
            state.requireToken &&
            req.headers().authorization !== 'Bearer test-token' &&
            !(endpoint === '/api/chatlog/media' && url.searchParams.get('token') === 'test-token') &&
            endpoint !== '/api/health'
        ) {
            status = 401;
            result = { detail: 'unauthorized' };
        } else if (endpoint === '/api/health')
            result = {
                auth_enabled: state.requireToken,
                timezone: 'Asia/Shanghai',
                timezone_offset: '+08:00',
                server_time: '2026-09-05T12:00:00+08:00',
            };
        else if (endpoint === '/api/gateway/health')
            result = { capabilities: { voice: true, max_binary_file_bytes: 1024 * 1024 } };
        else if (endpoint === '/api/console/wechat-startup') {
            if (method === 'POST') state.startupState = 'logging_in';
            else if (state.startupState === 'logging_in') state.startupState = state.startupOutcome;
            result = { enabled: false, supported: state.startupSupported, state: state.startupState, accepted: method === 'POST' };
        }
        else if (endpoint === '/api/groups')
            result = [
                { name: '开发群', active: true },
                { name: '产品群', active: true },
            ];
        else if (endpoint === '/api/chatlog/health')
            result = {
                backend: 'v1',
                media: { image: true, sticker: true, video_thumb: true, voice: true, file: true },
            };
        else if (endpoint === '/api/archive/status') result = {
            enabled: true, running: true, account_id: 'account', source_id: 'windows',
            heartbeat: Date.now(), pending: 0, pending_bytes: 0, groups: state.archiveGroups,
        };
        else if (endpoint === '/api/archive/available-groups') result = groups;
        else if (endpoint === '/api/archive/groups') {
            if (state.archiveSaveFails) {
                status = 503;
                result = { detail: '归档设置暂时无法保存' };
            } else {
                const existing = state.archiveGroups.find((group) => group.talker === body.talker);
                if (existing) existing.enabled = body.enabled;
                else state.archiveGroups.push({ ...body, display_name: groups.find((group) => group.username === body.talker).display_name,
                    history_done: true, pending: 0 });
                result = { success: true };
            }
        }
        else if (endpoint === '/api/archive/replay') {
            state.archiveGroups.find((group) => group.talker === body.talker).history_done = false;
            result = { success: true };
        }
        else if (endpoint === '/api/archive/messages' && state.archiveQueryFails) {
            status = 503;
            result = { detail: '云端记录暂时不可用' };
        }
        else if (endpoint === '/api/archive/messages') result = {
            messages: [{ id: url.searchParams.has('cursor') ? 'older-message' : 'current-message',
                server_id: '9007199254740991001', create_time: 100, sender: 'member', sender_name: '小明', msg_type: 1,
                content: '归档正文 <img src=x onerror=alert(1)>', text: url.searchParams.has('cursor') ? '更早的归档正文' : '归档正文 <img src=x onerror=alert(1)>',
                is_self: false }], next_cursor: url.searchParams.has('cursor') ? null : 'next-page',
        };
        else if (endpoint === '/api/chatlog/groups') result = groups;
        else if (endpoint === '/api/chatlog/decrypt') result = { success: true, message: '解密完成' };
        else if (endpoint === '/api/chatlog/contacts') result = [{ username: 'user1', nick_name: '小明' }];
        else if (endpoint === '/api/chatlog/members')
            result = [{ username: 'user1', display_name: '小明', nick_name: '小明', is_owner: true }];
        else if (endpoint === '/api/chatlog/messages') {
            const group = url.searchParams.get('group');
            if (state.delayA && group === 'room0@chatroom') await new Promise((r) => setTimeout(r, 500));
            if (state.failMessages) {
                status = 503;
                result = { detail: '消息读取失败' };
            } else if (Number(url.searchParams.get('offset'))) result = [message(group, 50, '更早的消息')];
            else if (group === 'room0@chatroom') {
                result = [
                    message(group, 1, '开发群最新消息'),
                    message(group, 2, '<voicemsg voicelength="3000"/>', 34),
                    { ...message(group, 3, '@小明 请查看'), at_users: ['user1'] },
                    message(
                        group,
                        4,
                        '<msg><appmsg><type>6</type><title>报告.pdf</title><totallen>2048</totallen></appmsg></msg>',
                        49
                    ),
                    message(group, 5, '', 3),
                    message(
                        group,
                        6,
                        '<appmsg><type>19</type><title>转发记录</title><recorditem><dataitem datatype="1"><sourcename>小明</sourcename><datadesc>转发正文</datadesc></dataitem></recorditem></appmsg>',
                        49
                    ),
                ];
                if (state.added) result.unshift(message(group, 9, '轮询新增消息'));
                while (result.length < 101)
                    result.push(message(group, 100 + result.length, '历史消息 ' + result.length));
            } else result = [message(group, 7, '产品群独有消息')];
        } else if (endpoint === '/api/chatlog/media') {
            if (url.searchParams.get('msg_type') === '34') {
                await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: Buffer.from([]) });
                return;
            }
            await route.fulfill({
                status: 200,
                contentType: 'image/png',
                body: Buffer.from(
                    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH9kAAAAASUVORK5CYII=',
                    'base64'
                ),
            });
            return;
        } else if (endpoint === '/api/remote-commands/policy' || endpoint === '/api/remote-commands/refresh') {
            if (method === 'PUT') state.policy = { ...state.policy, ...body };
            result = state.policy;
        } else if (endpoint.startsWith('/api/console')) {
            if (state.consoleUnsupported) {
                status = 501;
                result = { detail: 'Console 当前平台不可用' };
            } else if (endpoint.endsWith('/standalone-windows/status'))
                result = { available: true, open: state.windowsOpen };
            else if (endpoint.endsWith('/standalone-windows/cache')) {
                if (method === 'POST') state.cacheExists = true;
                result = {
                    available: true,
                    exists: state.cacheExists,
                    windows: state.cacheExists ? [{ name: '开发群' }] : [],
                };
            } else if (endpoint.endsWith('/standalone-windows'))
                result = { available: true, windows: state.windowsOpen ? [{ name: state.consoleSelected }] : [] };
            else if (endpoint.endsWith('/standalone-windows/open')) {
                state.windowsOpen = true;
                result = { available: true };
            } else if (endpoint.endsWith('/standalone-windows/close')) {
                state.windowsOpen = false;
                result = { available: true };
            } else if (endpoint.endsWith('/standalone-windows/open-cached')) {
                state.windowsOpen = true;
                result = { available: true };
            } else if (endpoint.endsWith('/open-chat')) {
                state.consoleSelected = chatItems.find((item) => item.path === body.path).name;
                result = consoleState();
            } else result = consoleState();
        } else if (endpoint === '/api/send' || endpoint === '/api/summary')
            result = { success: true, message: '操作已提交' };
        else {
            const match = endpoint.match(/^\/api\/(tasks|webhooks|custom-commands)(?:\/(\d+))?(?:\/test)?$/);
            if (!match) throw new Error('未模拟的 API: ' + endpoint);
            const [, kind, rawId] = match,
                id = Number(rawId);
            if (kind === 'tasks' && state.failTasks && method === 'GET') {
                status = 503;
                result = { detail: '任务读取失败' };
            } else if (endpoint.endsWith('/test')) result = { success: true };
            else if (method === 'GET') result = id ? data[kind].find((item) => item.id === id) : data[kind];
            else if (method === 'POST') {
                result = { ...body, id: Date.now() };
                data[kind].push(result);
            } else if (method === 'PUT') {
                const item = data[kind].find((item) => item.id === id);
                Object.assign(item, body);
                result = item;
            } else if (method === 'DELETE') {
                data[kind] = data[kind].filter((item) => item.id !== id);
                result = { success: true };
            }
        }
        await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(result) });
    });
    return { state, data, calls, errors, external };
}
