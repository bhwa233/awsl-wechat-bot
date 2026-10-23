import { render, Component } from 'preact';
import {
    h,
    useEffect,
    useLayoutEffect,
    useState,
    useRef,
    useCallback,
    useResource,
    AppContext,
    useApp,
    Button,
    Feedback,
    Input,
    Field,
    Modal,
    PageHead,
} from './ui.js';
import { api, getToken } from './api.js';
import { Chat } from './chat.js';
import { Console } from './console.js';
import { Tasks } from './tasks.js';
import { CustomCommands, RemoteCommands } from './custom_commands.js';
import { Webhooks } from './webhooks.js';
import { WeChatStartup } from './wechat_startup.js?v=202610230002';

const pages = [
    ['dashboard', '概览', 'dashboard'],
    ['chat', '聊天记录', 'chat'],
    ['console', '控制台', 'console'],
    ['tasks', '定时任务', 'clock'],
    ['remote-commands', '远程命令', 'cloud'],
    ['custom-commands', '自定义命令', 'command'],
    ['webhooks', 'Webhook', 'link'],
];
const components = {
    dashboard: Dashboard,
    chat: Chat,
    console: Console,
    tasks: Tasks,
    'remote-commands': RemoteCommands,
    'custom-commands': CustomCommands,
    webhooks: Webhooks,
};
const paths = {
    dashboard: ['M3 3h7v8H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 15h7v6H3z'],
    chat: ['M21 11.5a8.5 8.5 0 0 1-12 7.8L3 21l1.7-6A8.5 8.5 0 1 1 21 11.5Z'],
    console: ['M3 3h18v14H3zM8 21h8M12 17v4'],
    clock: ['M12 8v5l3 2', 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
    cloud: ['M17 19H9a7 7 0 1 1 6.7-9H17a4.5 4.5 0 1 1 0 9Z'],
    command: ['m4 5 6 6-6 6M12 19h8'],
    link: ['M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l2-2'],
    sidebar: ['M3 3h18v18H3zM9 3v18'],
    key: ['M10 14 21 3M16 8l2 2M19 5l2 2', 'M11 17a4 4 0 1 1-8 0 4 4 0 0 1 8 0'],
    moon: ['M21 13a9 9 0 0 1-10-10 9 9 0 1 0 10 10Z'],
    sun: ['M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1', 'M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0'],
};
function Icon({ name }) {
    return h(
        'svg',
        {
            viewBox: '0 0 24 24',
            fill: 'none',
            stroke: 'currentColor',
            'stroke-width': 1.7,
            'stroke-linecap': 'round',
            'stroke-linejoin': 'round',
            'aria-hidden': true,
        },
        (paths[name] || paths.chat).map((d, i) => h('path', { key: i, d }))
    );
}
function route() {
    const hash = location.hash.replace(/^#\/?/, '');
    const candidate = hash || localStorage.getItem('cortex_section');
    return pages.some(([id]) => id === candidate) ? candidate : 'dashboard';
}
function Dashboard({ active }) {
    const { navigate, refreshServer } = useApp();
    const resource = useResource(
        async (signal) => {
            const routes = [
                '/api/health',
                '/api/gateway/health',
                '/api/chatlog/health',
                '/api/groups',
                '/api/chatlog/groups',
                '/api/tasks',
                '/api/webhooks',
                '/api/custom-commands',
                '/api/remote-commands/policy',
            ];
            return Promise.allSettled(routes.map((path) => api('GET', path, { signal })));
        },
        [],
        active
    );
    const data = resource.data;
    const count = (i) => (data?.[i].status === 'fulfilled' ? data[i].value : null);
    const groups = count(3);
    const stats = [
        [
            '在线群聊',
            groups ? (Array.isArray(groups) ? groups : groups.groups || []).filter((g) => g.active).length : null,
            'chat',
        ],
        ['聊天会话', count(4)?.length, 'chat'],
        ['定时任务', count(5)?.length, 'tasks'],
        ['Webhook', count(6)?.length, 'webhooks'],
        ['自定义命令', count(7)?.length, 'custom-commands'],
        ['远程命令', count(8)?.available_commands?.length, 'remote-commands'],
    ];
    return h(
        'div',
        null,
        h(
            PageHead,
            { title: '概览' },
            h(
                Button,
                {
                    busy: resource.loading,
                    onClick: () => {
                        resource.reload();
                        refreshServer();
                    },
                },
                '刷新状态'
            )
        ),
        h(Feedback, { error: resource.error, retry: resource.reload }),
        h(
            'div',
            { class: 'dash-grid' },
            h(WeChatStartup, { active, onReady: () => { resource.reload(); refreshServer(); } }),
            h(
                'div',
                { class: 'card dash-health' },
                h(
                    'div',
                    { class: 'card-head' },
                    h('h3', null, '服务状态'),
                    h('span', { class: 'card-head-sub' }, resource.loading ? '连接中…' : '实时检查')
                ),
                h(
                    'div',
                    { class: 'health-list' },
                    ['Cortex 服务', 'Gateway 服务', 'Chatlog 服务'].map((name, i) => {
                        const success = data?.[i].status === 'fulfilled';
                        return h(
                            'div',
                            { class: `health-row ${data ? (success ? 'ok' : 'bad') : ''}`, key: name },
                            h('span', { class: 'health-dot' }),
                            h('span', { class: 'health-name' }, name),
                            h(
                                'span',
                                { class: 'health-val', title: success ? '' : data?.[i].reason?.message },
                                data ? (success ? '运行中' : '不可用') : '连接中…'
                            )
                        );
                    })
                )
            ),
            h(
                'div',
                { class: 'card dash-stats' },
                h('div', { class: 'card-head' }, h('h3', null, '数据概览')),
                h(
                    'div',
                    { class: 'stat-grid' },
                    stats.map(([label, value, target]) =>
                        h(
                            'button',
                            {
                                class: 'stat-cell stat-link',
                                key: label,
                                type: 'button',
                                onClick: () => navigate(target),
                            },
                            h('span', { class: 'stat-num' }, value ?? '—'),
                            h('span', { class: 'stat-label' }, label)
                        )
                    )
                )
            ),
            h(
                'div',
                { class: 'card dash-actions' },
                h('div', { class: 'card-head' }, h('h3', null, '快捷入口')),
                h(
                    'div',
                    { class: 'quick-actions' },
                    [
                        ['chat', '查看聊天记录'],
                        ['console', '打开远程控制台'],
                        ['tasks', '管理定时任务'],
                        ['webhooks', '连接 Webhook'],
                    ].map(([target, title]) =>
                        h(
                            'button',
                            { type: 'button', key: target, class: 'quick-action', onClick: () => navigate(target) },
                            h(Icon, { name: pages.find(([id]) => id === target)[2] }),
                            h('span', null, title)
                        )
                    )
                )
            )
        )
    );
}
function TokenDialog({ onClose, onSaved }) {
    const [token, setToken] = useState(getToken);
    function save(value) {
        if (value) localStorage.setItem('awsl_api_token', value);
        else localStorage.removeItem('awsl_api_token');
        onSaved();
        onClose();
    }
    return h(
        Modal,
        { title: 'API Token', onClose, class: 'modal-sm' },
        h(
            'form',
            {
                onSubmit: (e) => {
                    e.preventDefault();
                    save(token.trim());
                },
            },
            h(
                Field,
                { label: '访问令牌' },
                h(Input, {
                    type: 'password',
                    value: token,
                    onValue: setToken,
                    autoComplete: 'off',
                    placeholder: 'Bearer Token',
                    id: 'tokenInput',
                })
            ),
            h(
                'div',
                { class: 'form-actions' },
                h(Button, { onClick: () => save('') }, '清除'),
                h(Button, { type: 'submit', variant: 'green' }, '保存')
            )
        )
    );
}
class PageBoundary extends Component {
    constructor(props) {
        super(props);
        this.state = { failed: false };
    }
    static getDerivedStateFromError() {
        return { failed: true };
    }
    componentDidCatch(error) {
        console.error('页面渲染失败', error);
    }
    render() {
        return this.state.failed
            ? h(Feedback, { error: '页面加载失败，请刷新页面', retry: () => location.reload() })
            : this.props.children;
    }
}
function App() {
    const [section, setSection] = useState(route);
    const [visited, setVisited] = useState(() => new Set([route()]));
    const [session, setSession] = useState(0);
    const [drawer, setDrawer] = useState(false);
    const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sidebar_collapsed') === '1');
    const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'light');
    const [tokenModal, setTokenModal] = useState(false);
    const [notice, setNotice] = useState(null);
    const [emoji, setEmoji] = useState({});
    const timer = useRef(null);
    const server = useResource(
        async (signal) => {
            const [health, gateway, groups] = await Promise.allSettled([
                api('GET', '/api/health', { signal }),
                api('GET', '/api/gateway/health', { signal }),
                api('GET', '/api/groups', { signal }),
            ]);
            return {
                session,
                health: health.status === 'fulfilled' ? health.value : {},
                capabilities: gateway.status === 'fulfilled' ? gateway.value.capabilities || {} : {},
                groups:
                    groups.status === 'fulfilled'
                        ? Array.isArray(groups.value)
                            ? groups.value
                            : groups.value.groups || []
                        : [],
                error: groups.status === 'rejected' ? groups.reason.message : '',
            };
        },
        [session]
    );
    const toast = useCallback((text, type = 'info') => {
        clearTimeout(timer.current);
        setNotice({ text, type });
        timer.current = setTimeout(() => setNotice(null), 4000);
    }, []);
    const navigate = useCallback((name) => {
        if (!components[name]) return;
        location.hash = `/${name}`;
        setSection(name);
        setVisited((previous) => new Set([...previous, name]));
        setDrawer(false);
        localStorage.setItem('cortex_section', name);
    }, []);
    useEffect(() => {
        const hash = () => navigate(route());
        const keydown = (event) => {
            if (event.key === 'Escape') setDrawer(false);
        };
        window.addEventListener('hashchange', hash);
        document.addEventListener('keydown', keydown);
        const media = matchMedia('(prefers-color-scheme: dark)');
        const change = (event) => {
            if (!localStorage.getItem('bot_theme')) setTheme(event.matches ? 'dark' : 'light');
        };
        media.addEventListener('change', change);
        const controller = new AbortController();
        fetch('/static/wechat_emoji.json', { signal: controller.signal })
            .then((r) => r.json())
            .then(setEmoji)
            .catch(() => {});
        return () => {
            window.removeEventListener('hashchange', hash);
            document.removeEventListener('keydown', keydown);
            media.removeEventListener('change', change);
            controller.abort();
            clearTimeout(timer.current);
        };
    }, []);
    useLayoutEffect(() => {
        document.documentElement.dataset.theme = theme;
    }, [theme]);
    const connecting = server.loading || server.data?.session !== session;
    const info = server.data?.session === session ? server.data : { groups: [], health: {}, capabilities: {} };
    const offline = !!(server.error || info.error);
    const context = { ...info, emoji, toast, navigate, session, refreshServer: server.reload };
    return h(
        AppContext.Provider,
        { value: context },
        h(
            'div',
            { class: `app ${collapsed ? 'sidebar-collapsed' : ''} ${drawer ? 'drawer-open' : ''}`, id: 'app' },
            h(
                'button',
                {
                    class: 'menu-toggle',
                    type: 'button',
                    'aria-label': '打开导航',
                    'aria-expanded': drawer,
                    onClick: () => setDrawer(!drawer),
                },
                h(Icon, { name: 'sidebar' })
            ),
            h('button', {
                class: 'drawer-backdrop',
                type: 'button',
                'aria-label': '关闭导航',
                tabIndex: drawer ? 0 : -1,
                onClick: () => setDrawer(false),
            }),
            h(
                'aside',
                { class: 'sidebar' },
                h(
                    'div',
                    { class: 'side-brand' },
                    h('div', { class: 'logo' }, h(Icon, { name: 'chat' })),
                    h(
                        'div',
                        { class: 'side-brand-text' },
                        h('span', { class: 'side-brand-title' }, 'AWSL'),
                        h('span', { class: 'side-brand-sub' }, '微信机器人')
                    ),
                    h(
                        'button',
                        {
                            class: 'sidebar-toggle',
                            type: 'button',
                            'aria-label': collapsed ? '展开侧边栏' : '收起侧边栏',
                            'aria-expanded': !collapsed,
                            onClick: () => {
                                const next = !collapsed;
                                setCollapsed(next);
                                localStorage.setItem('sidebar_collapsed', next ? '1' : '0');
                            },
                        },
                        h(Icon, { name: 'sidebar' })
                    )
                ),
                h(
                    'nav',
                    { class: 'side-nav', 'aria-label': '主导航' },
                    pages.map(([id, label, icon]) =>
                        h(
                            'button',
                            {
                                key: id,
                                type: 'button',
                                class: `nav-item ${id === section ? 'active' : ''}`,
                                'data-section': id,
                                title: label,
                                'aria-current': id === section ? 'page' : undefined,
                                onClick: () => navigate(id),
                            },
                            h(Icon, { name: icon }),
                            h('span', null, label)
                        )
                    )
                ),
                h(
                    'div',
                    { class: 'side-foot' },
                    h(
                        'div',
                        { class: 'side-status', title: info.error || '' },
                        h('span', { class: `status-dot ${offline ? 'off' : ''}` }),
                        h(
                            'span',
                            { class: 'status-text' },
                            connecting
                                ? '连接中…'
                                : offline
                                  ? '连接失败'
                                  : `运行中 · ${info.groups.filter((g) => g.active).length} 群在线`
                        )
                    ),
                    h(
                        'button',
                        {
                            id: 'tokenBtn',
                            type: 'button',
                            class: `icon-btn-ghost ${info.health?.auth_enabled && !getToken() ? 'needs-token' : ''}`,
                            title: 'API Token',
                            'aria-label': 'API Token',
                            onClick: () => setTokenModal(true),
                        },
                        h(Icon, { name: 'key' })
                    ),
                    h(
                        'button',
                        {
                            id: 'themeToggle',
                            type: 'button',
                            class: 'icon-btn-ghost',
                            title: '切换主题',
                            'aria-label': '切换主题',
                            onClick: () => {
                                const next = theme === 'dark' ? 'light' : 'dark';
                                localStorage.setItem('bot_theme', next);
                                setTheme(next);
                            },
                        },
                        h(Icon, { name: theme === 'dark' ? 'sun' : 'moon' })
                    )
                )
            ),
            h(
                'main',
                { class: 'main' },
                h(
                    'div',
                    { class: 'content', key: session },
                    pages
                        .filter(([id]) => visited.has(id))
                        .map(([id]) =>
                            h(
                                'section',
                                {
                                    id: `section-${id}`,
                                    class: `section ${section === id ? 'active' : ''}`,
                                    key: id,
                                    'aria-label': pages.find(([key]) => key === id)[1],
                                },
                                h(PageBoundary, null, h(components[id], { active: section === id }))
                            )
                        )
                )
            )
        ),
        tokenModal &&
            h(TokenDialog, {
                onClose: () => setTokenModal(false),
                onSaved: () => {
                    setSession((n) => n + 1);
                    toast('认证信息已更新', 'success');
                },
            }),
        h(
            'div',
            { class: `toast ${notice ? `${notice.type} show` : ''}`, role: 'status', 'aria-live': 'polite' },
            notice?.text
        )
    );
}
render(h(App), document.getElementById('root'));
